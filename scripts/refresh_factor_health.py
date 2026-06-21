#!/usr/bin/env python3
"""因子健康度刷新 driver —— 让 #8 仲裁闭环在生产可达（U8 ②）.

`kss/backtest/factor_health.py` 的 ``FactorHealthHook`` / ``arbitrate()`` 此前只有测试
驱动：生产里没人把回测先验注入真 hook caller，``backtest_ic_source=None`` → 升权路径永远
不放行（demote-only / 升权半休眠），promote / divergence 在生产不可达。

本脚本是那条缺失的 driver，**pipeline 级 rank_ic 双源**接通 #8：

  - **live 后验** = 最近窗口（``--recent-window`` 个去重交易日）的 pipeline rank_ic；
  - **回测先验** = 全历史 / 较早窗口的 pipeline rank_ic（同一 ``_daily_rank_ic`` 算出）；
  - 两者**同口径 rank_ic**（复用 ``compute_pipeline_alpha._daily_rank_ic`` 的截面 Spearman），
    经 ``FactorHealthTracker`` 落库 + ``arbitrate()``（``realized_method=backtest_method=
    rank_ic``）→ promote / divergence 在生产可达，不再 ``method_mismatch``、不再 None 先验。

回测先验经历史窗口算出后，注入真 ``FactorHealthHook`` caller（``backtest_ic_source`` =
pipeline 级 rank_ic prior callable，口径声明 ``backtest_ic_method=rank_ic``）。

衰减 / 分歧告警走 ``send_to_channels``；状态落 ``factor_health.db``。

PIT / 复用纪律：
- 同口径 rank_ic（防 sign_proxy vs rank_ic 跨口径误比，与 plan #8 守卫一致）；
- 复用 ``_daily_rank_ic`` / pipeline hit readers / ``FactorHealthHook``，非重造；
- 账本数据只读做诊断，**绝不回流回测**（本脚本不碰 factor_df / 特征工程）。

用法::

    .venv-desktop/bin/python scripts/refresh_factor_health.py --horizon 5
    .venv-desktop/bin/python scripts/refresh_factor_health.py --recent-window 60 --channel all

注：归档样本极薄时（实测 log_mv≈4 / bj50≈3 期）有效 n 远低于 ``realized_ic_min_n`` →
仲裁判 ``insufficient_n``「仅供参考不翻转」；脚本照算照记，在 ``trustworthy`` 字段显式标注，
不静默假装可信（fail loud）。这是诚实接通：闭环代码可达，是否触发取决于数据是否够厚。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.factor_health import (  # noqa: E402
    IC_METHOD_RANK_IC,
    FactorCrashRegistry,
    FactorHealthHook,
    FactorHealthTracker,
    load_thresholds,
)
from scripts.compute_pipeline_alpha import (  # noqa: E402
    PIPELINE_HIT_READERS,
    _daily_rank_ic,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# pipeline 级 rank_ic 双源：最近窗口=后验 / 较早窗口=先验（同口径 rank_ic）
# --------------------------------------------------------------------------- #

def split_ic_series(
    ic_series: pd.Series, recent_window: int
) -> tuple[pd.Series, pd.Series]:
    """把逐日 rank_ic 序列按时间切成 (较早窗口=先验, 最近窗口=后验).

    - **后验** = 最近 ``recent_window`` 个去重交易日（live，反映近期表现）；
    - **先验** = 其余较早全历史（backtest 角色，较长样本的稳态基准）。

    两段都来自同一 ``_daily_rank_ic`` 的 rank_ic 序列 → 同口径，可直接喂 #8 仲裁。
    历史不足 ``recent_window`` 时先验为空（先验缺失 → 升权保守不放行，与 #8 一致）。
    """
    s = ic_series.dropna().sort_index()
    if len(s) <= recent_window:
        # 全部归后验，先验为空（样本不足分窗）
        return pd.Series(dtype=float), s
    prior = s.iloc[:-recent_window]
    posterior = s.iloc[-recent_window:]
    return prior, posterior


def _prior_source_factory(
    priors: dict[str, tuple[float, float]],
):
    """把 pipeline 级 rank_ic 先验包成 ``BacktestICSource`` callable（factor_id → (ic,icir)|None）.

    注入真 ``FactorHealthHook`` caller：缺先验的因子返回 None（升权保守不放行）。
    """
    def _source(factor_id: str) -> tuple[float, float] | None:
        return priors.get(factor_id)

    return _source


# --------------------------------------------------------------------------- #
# 主流程：逐 pipeline 算双源 rank_ic → 落库 + #8 仲裁 + 告警
# --------------------------------------------------------------------------- #

def refresh(
    *,
    horizon: int,
    recent_window: int,
    db_path: str | Path | None = None,
    thresholds: dict[str, Any] | None = None,
    notify_channel: str = "console",
    notify: bool = False,
) -> dict[str, Any]:
    """对每条 pipeline 算 rank_ic 双源 → FactorHealthTracker + arbitrate → 状态落库.

    Returns:
        ``{pipeline_id: {verdict, reason, state, posterior_n, prior_present, ...}}`` 汇总。
    """
    th = thresholds if thresholds is not None else load_thresholds()
    tracker = FactorHealthTracker(db_path=db_path, thresholds=th)
    registry = FactorCrashRegistry(db_path=db_path)

    # 先扫一遍：算每条 pipeline 的全历史 rank_ic 序列 + 先验/后验分窗。
    posterior_by_pipeline: dict[str, pd.Series] = {}
    priors: dict[str, tuple[float, float]] = {}
    windows: dict[str, dict[str, Any]] = {}

    for pid, reader in PIPELINE_HIT_READERS.items():
        hits = reader()
        ic_series, _top_q, _all_q = _daily_rank_ic(hits, horizon)
        if ic_series.empty:
            windows[pid] = {"feasible": False, "reason": "无足够前向收益对齐样本"}
            continue
        prior_s, posterior_s = split_ic_series(ic_series, recent_window)
        factor_id = f"pipeline:{pid}"
        posterior_by_pipeline[factor_id] = posterior_s
        # 先验落库（source=backtest，method=rank_ic）+ 注入仲裁 prior
        if not prior_s.empty:
            prior_snap = tracker.record_ic_snapshot(
                factor_id, prior_s.index.max(), prior_s,
                source="backtest", method=IC_METHOD_RANK_IC, force=True,
            )
            priors[factor_id] = (prior_snap.ic_mean, prior_snap.icir)
        windows[pid] = {
            "feasible": True,
            "prior_n": int(prior_s.index.nunique()),
            "posterior_n": int(posterior_s.index.nunique()),
            "prior_present": not prior_s.empty,
        }

    # hook：注入 pipeline 级 rank_ic 先验，口径声明 rank_ic（同口径不 method_mismatch）。
    hook = FactorHealthHook(
        tracker=tracker,
        registry=registry,
        backtest_ic_source=_prior_source_factory(priors),
        backtest_ic_method=IC_METHOD_RANK_IC,  # pipeline 级先验是 rank_ic，非默认 sign_proxy
        notify_channel=notify_channel,
        notify=notify,
        thresholds=th,
    )

    summary: dict[str, Any] = {}
    for factor_id, posterior_s in posterior_by_pipeline.items():
        pid = factor_id.split(":", 1)[1]
        if posterior_s.empty:
            summary[pid] = {**windows.get(pid, {}), "verdict": None,
                            "reason": "后验窗口为空"}
            continue
        # 实盘后验走 #8 仲裁（method=rank_ic，与先验同口径）。
        out = hook.on_backtest_end(
            factor_id,
            window_start=str(posterior_s.index.min()),
            window_end=str(posterior_s.index.max()),
            ic_series=posterior_s,
            source="realized",
            method=IC_METHOD_RANK_IC,
        )
        verdict = out.get("verdict")
        summary[pid] = {
            **windows.get(pid, {}),
            "ic_mean": round(out["snapshot"].ic_mean, 6),
            "icir": round(out["snapshot"].icir, 6),
            "verdict": verdict.verdict if verdict else None,
            "reason": verdict.reason if verdict else None,
            "divergence": bool(verdict.divergence) if verdict else False,
            "state": out.get("state"),
            "n_crashes": len(out.get("crashes", [])),
        }

    return summary


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="因子健康度刷新 driver（pipeline rank_ic 双源 → #8 仲裁，生产可达）"
    )
    ap.add_argument("--horizon", type=int, default=5, help="前向收益持有天数（T+1→T+1+hold）")
    ap.add_argument("--recent-window", type=int, default=60,
                    help="后验=最近 N 去重交易日 rank_ic；其余较早窗口=先验（默认 60）")
    ap.add_argument("--channel", type=str, default=None,
                    help="衰减/分歧告警通道：console / telegram / all（不给则不推）")
    args = ap.parse_args(argv)

    notify = args.channel is not None
    summary = refresh(
        horizon=args.horizon,
        recent_window=args.recent_window,
        notify_channel=args.channel or "console",
        notify=notify,
    )
    payload = {
        "runDate": datetime.now().strftime("%Y%m%d"),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "horizon": args.horizon,
        "recentWindow": args.recent_window,
        "note": "pipeline 级 rank_ic 双源（后验=近窗 / 先验=较早窗）同口径喂 #8 仲裁；"
                "样本薄时 insufficient_n 仅供参考（fail loud，不假装可信）。",
        "pipelines": summary,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
