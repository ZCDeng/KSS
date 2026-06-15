"""A 股微调（冻结截断日 D）—— 单进程精简版（U3）.

封装上游 finetune 的**核心训练步**（``tokenizer.encode → model →
head.compute_loss`` 的 next-token 目标），剥掉 DDP / comet_ml / Qlib pickle 依赖，
改吃 KSS ``cs_data`` 并由 :mod:`kss.kronos.isolation` 守住时间隔离：

- 训练滑窗严格截止于 D（:func:`assert_train_before_cutoff`）。
- 窗口归一化只用 lookback 段（镜像上游 ``dataset.py``，防窗口内未来泄漏）。
- 产出带 D 标记的冻结 artifact，里程碑内不再训练。

CPU 可跑（小 ``max_samples`` / ``epochs``）；真实大规模微调见
``scripts/kronos_finetune.py``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from kss.kronos import load_vendor
from kss.kronos.config import ARTIFACT_ROOT, BASE_MODEL_REPO, BASE_TOKENIZER_REPO
from kss.kronos.isolation import (
    FreezeMetadata,
    assert_train_before_cutoff,
    resolve_cutoff_date,
)

logger = logging.getLogger(__name__)

# Kronos 特征顺序（与上游 tokenizer 训练口径一致：OHLC + 量 + 额）。
_FEATURE_COLS: list[str] = ["open", "high", "low", "close", "volume", "amount"]
_STAMP_COLS: list[str] = ["minute", "hour", "weekday", "day", "month"]


def _stamp_array(dates: pd.Series) -> np.ndarray:
    dt = pd.to_datetime(dates).dt
    return np.stack(
        [dt.minute, dt.hour, dt.weekday, dt.day, dt.month], axis=1
    ).astype(np.float32)


def build_training_windows(
    frames: dict[str, pd.DataFrame],
    *,
    cutoff,
    lookback_window: int,
    predict_window: int,
    clip: float = 5.0,
    max_samples: int | None = None,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """从 KSS cs_data 构造 D 之前的训练滑窗（带泄漏隔离）.

    每个滑窗长度 = ``lookback_window + predict_window + 1``（镜像上游 dataset.py）；
    归一化的 mean/std 只在前 ``lookback_window`` 行上算，避免窗口内未来泄漏。

    Args:
        frames: ``{symbol: cs_data DataFrame}``，每个含 trade_date + OHLCV.
        cutoff: 冻结截断日 D；窗口最后一根 bar 的日期必须 ≤ D.
        lookback_window / predict_window: 滑窗结构.
        clip: 归一化后的鲁棒截断.
        max_samples: 采样上限（CPU 可行性）；None = 全部.
        seed: 采样随机种子.

    Returns:
        ``(x, x_stamp)``：``x`` 形 ``[N, window, 6]``，``x_stamp`` 形 ``[N, window, 5]``.

    Raises:
        LeakageError: 任一窗口的 bar 越过 D（经 :func:`assert_train_before_cutoff`）.
    """
    from kss.kronos.adapter import normalize_ohlcv

    cutoff = pd.Timestamp(cutoff)
    window = lookback_window + predict_window + 1
    xs: list[np.ndarray] = []
    stamps: list[np.ndarray] = []

    for symbol, df in frames.items():
        norm = normalize_ohlcv(df)
        norm = norm[norm["trade_date"] <= cutoff].reset_index(drop=True)
        if len(norm) < window:
            continue
        # 隔离守卫：训练样本时间戳一律 ≤ D。
        assert_train_before_cutoff(norm["trade_date"], cutoff)

        feats = norm[_FEATURE_COLS].values.astype(np.float32)
        stamp = _stamp_array(norm["trade_date"])
        n = len(norm) - window + 1
        for i in range(n):
            w = feats[i : i + window]
            past = w[:lookback_window]
            mean = past.mean(axis=0)
            std = past.std(axis=0)
            wn = np.clip((w - mean) / (std + 1e-5), -clip, clip)
            if not np.isfinite(wn).all():
                continue
            xs.append(wn)
            stamps.append(stamp[i : i + window])

    if not xs:
        raise ValueError("build_training_windows: 没有可用训练窗口")

    x = np.stack(xs).astype(np.float32)
    x_stamp = np.stack(stamps).astype(np.float32)

    if max_samples is not None and len(x) > max_samples:
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(x), size=max_samples, replace=False)
        x, x_stamp = x[idx], x_stamp[idx]

    logger.info("训练窗口：%d 条（%d 标的，window=%d）", len(x), len(frames), window)
    return x, x_stamp


def _pick_device(device: str | None):
    import torch

    if device is not None:
        return torch.device(device)
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def finetune(
    frames: dict[str, pd.DataFrame],
    *,
    explicit_cutoff: str | None = None,
    forward_window: int | None = None,
    lookback_window: int = 90,
    predict_window: int = 10,
    epochs: int = 1,
    batch_size: int = 16,
    lr: float = 2e-5,
    max_samples: int | None = 512,
    base_model_repo: str = BASE_MODEL_REPO,
    base_tokenizer_repo: str = BASE_TOKENIZER_REPO,
    artifact_dir: str | Path | None = None,
    device: str | None = None,
    seed: int = 42,
) -> FreezeMetadata:
    """在 D 之前的 A 股数据上微调 base，产出冻结 artifact + 元数据.

    Args:
        frames: ``{symbol: cs_data DataFrame}``.
        explicit_cutoff: 显式 D（``YYYY-MM-DD``）；缺省按 forward_window 自动推导.
        forward_window: 自动推导 D 时留出的未来交易日数（缺省用 config）.
        lookback_window / predict_window: 训练滑窗结构.
        epochs / batch_size / lr / max_samples: 训练超参（CPU 默认偏小）.
        base_model_repo / base_tokenizer_repo: 微调起点.
        artifact_dir: 冻结产物目录；缺省 ``ARTIFACT_ROOT/freeze_<D>``.
        device: 训练设备；缺省自动选.
        seed: 随机种子.

    Returns:
        :class:`FreezeMetadata`（已落盘）.
    """
    import torch

    all_dates = pd.to_datetime(
        pd.concat([f["trade_date"] for f in frames.values()])
    )
    kwargs = {} if forward_window is None else {"forward_window": forward_window}
    cutoff = resolve_cutoff_date(all_dates, explicit=explicit_cutoff, **kwargs)
    logger.info("冻结截断日 D = %s", cutoff.date())

    x, x_stamp = build_training_windows(
        frames, cutoff=cutoff, lookback_window=lookback_window,
        predict_window=predict_window, max_samples=max_samples, seed=seed,
    )

    Kronos, KronosTokenizer, _ = load_vendor()
    dev = _pick_device(device)
    torch.manual_seed(seed)

    tokenizer = KronosTokenizer.from_pretrained(base_tokenizer_repo).to(dev)
    model = Kronos.from_pretrained(base_model_repo).to(dev)
    tokenizer.eval()  # 微调只训 predictor，tokenizer 冻结（同上游 train_predictor）。
    for p in tokenizer.parameters():
        p.requires_grad_(False)
    model.train()

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)

    x_t = torch.from_numpy(x)
    s_t = torch.from_numpy(x_stamp)
    n = len(x_t)
    for epoch in range(epochs):
        perm = torch.randperm(n)
        running = 0.0
        n_batches = 0
        for start in range(0, n, batch_size):
            sel = perm[start : start + batch_size]
            bx = x_t[sel].to(dev)
            bs = s_t[sel].to(dev)
            with torch.no_grad():
                tok0, tok1 = tokenizer.encode(bx, half=True)
            in0, in1 = tok0[:, :-1], tok1[:, :-1]
            out0, out1 = tok0[:, 1:], tok1[:, 1:]
            s1_logits, s2_logits = model(in0, in1, bs[:, :-1, :])
            loss, _, _ = model.head.compute_loss(s1_logits, s2_logits, out0, out1)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            running += float(loss.item())
            n_batches += 1
        logger.info("epoch %d/%d 平均 loss=%.4f", epoch + 1, epochs, running / max(1, n_batches))

    # ---- 冻结落盘 ---- #
    if artifact_dir is None:
        artifact_dir = ARTIFACT_ROOT / f"freeze_{cutoff.date()}"
    artifact_dir = Path(artifact_dir)
    model_dir = artifact_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    model.eval()
    model.save_pretrained(str(model_dir))

    meta = FreezeMetadata(
        cutoff_date=str(cutoff.date()),
        base_model_repo=base_model_repo,
        base_tokenizer_repo=base_tokenizer_repo,
        n_train_samples=int(n),
        n_symbols=len(frames),
        lookback_window=lookback_window,
        predict_window=predict_window,
        finetuned_at=datetime.now(timezone.utc).isoformat(),
        notes=f"单进程精简微调 epochs={epochs} bs={batch_size} lr={lr} device={dev}",
    )
    meta.save(artifact_dir / "freeze_meta.json")
    logger.info("冻结 artifact → %s", artifact_dir)
    return meta


def load_frozen_model(artifact_dir: str | Path, device: str | None = None):
    """加载冻结 artifact（model + tokenizer），返回 KronosPredictor.

    Args:
        artifact_dir: :func:`finetune` 产出的目录（含 ``model/`` + ``freeze_meta.json``）.
        device: 推理设备；缺省自动选.

    Returns:
        ``(predictor, FreezeMetadata)``.
    """
    Kronos, KronosTokenizer, KronosPredictor = load_vendor()
    artifact_dir = Path(artifact_dir)
    meta = FreezeMetadata.load(artifact_dir / "freeze_meta.json")
    model = Kronos.from_pretrained(str(artifact_dir / "model"))
    tokenizer = KronosTokenizer.from_pretrained(meta.base_tokenizer_repo)
    dev = _pick_device(device)
    predictor = KronosPredictor(model, tokenizer, device=str(dev))
    return predictor, meta
