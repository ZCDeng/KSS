"""MI walk-forward：固定形态键下滚动重估 N 与 thr."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from kss.backtest.metrics import Metrics
from kss.strategies.mi_signal import (
    RuleSpec,
    build_features,
    positions_from_rules,
    replay,
    uses_z_thresholds,
)

DEFAULT_N_GRID = (6, 9, 12, 14, 20)
DEFAULT_ENTRY_Z = (0.3, 0.5, 0.8, 1.0)
DEFAULT_EXIT_Z = (0.0, -0.3, -0.5)


@dataclass
class WFConfig:
    """滚动重估超参."""

    train_window: int = 252
    retrain_freq: int = 20
    holdout_bars: int = 63
    min_trades: int = 4
    n_grid: tuple[int, ...] = DEFAULT_N_GRID
    entry_z_grid: tuple[float, ...] = DEFAULT_ENTRY_Z
    exit_z_grid: tuple[float, ...] = DEFAULT_EXIT_Z


@dataclass
class WFResult:
    """重估结果."""

    status: str  # ok | skipped | error
    reason: str = ""
    best_n: int | None = None
    best_thr: dict[str, float] = field(default_factory=dict)
    param_history: list[dict[str, Any]] = field(default_factory=list)
    param_delta: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)


def _score_window(
    df: pd.DataFrame,
    entry: str,
    exit_: str,
    filt: str,
    n: int,
    thr: dict[str, float] | None,
    holdout_bars: int,
    min_trades: int,
) -> float:
    """训练窗末 holdout 段夏普；交易过少返回 -999."""
    if len(df) < holdout_bars + n + 20:
        return -999.0
    feat = build_features(df, n)
    warm = max(n + 5, 40)
    pos = positions_from_rules(feat, entry, exit_, filt, warm=warm, thr=thr)
    # holdout = 最后 holdout_bars
    start = max(warm, len(feat) - holdout_bars)
    sub_pos = pos.iloc[start:]
    sub_ret = feat["ret"].iloc[start:]
    net = (sub_pos * sub_ret).dropna()
    flips = int((sub_pos.diff().abs() > 1e-9).sum())
    # 训练窗内完整交易次数（避免 holdout 过短导致永远 -999）
    full_flips = int((pos.diff().abs() > 1e-9).sum())
    if full_flips < min_trades and flips < 1:
        return -999.0
    m = Metrics.calc(net)
    if not m:
        return -999.0
    sh = float(m.get("sharpe", 0.0) or 0.0)
    dd = float(m.get("max_dd", 0.0) or 0.0)
    pen = 0.0
    if dd < -0.5:
        pen += 0.5
    if dd < -0.7:
        pen += 1.0
    return sh - pen


def _thr_grid(
    entry: str, exit_: str, cfg: WFConfig
) -> list[dict[str, float] | None]:
    if not uses_z_thresholds(entry, exit_):
        return [None]
    out: list[dict[str, float] | None] = []
    for ez in cfg.entry_z_grid:
        for xz in cfg.exit_z_grid:
            out.append({"entry_z": float(ez), "exit_z": float(xz)})
    return out


def reestimate(
    df: pd.DataFrame,
    entry: str,
    exit_: str,
    filt: str = "none",
    cfg: WFConfig | None = None,
) -> WFResult:
    """滚动重估 N/thr，再用末日参数全样本 replay."""
    cfg = cfg or WFConfig()
    if len(df) < cfg.train_window + 30:
        return WFResult(
            status="skipped",
            reason=f"样本不足: {len(df)} < train_window+30={cfg.train_window + 30}",
        )

    thr_options = _thr_grid(entry, exit_, cfg)
    param_history: list[dict[str, Any]] = []
    best_n = cfg.n_grid[0]
    best_thr: dict[str, float] = thr_options[0] or {}

    t = cfg.train_window
    while t < len(df):
        train = df.iloc[t - cfg.train_window : t].reset_index(drop=True)
        best_score = -1e18
        cand_n = best_n
        cand_thr = best_thr
        for n in cfg.n_grid:
            for thr in thr_options:
                sc = _score_window(
                    train,
                    entry,
                    exit_,
                    filt,
                    n,
                    thr,
                    cfg.holdout_bars,
                    cfg.min_trades,
                )
                if sc > best_score:
                    best_score = sc
                    cand_n = n
                    cand_thr = thr or {}
        best_n = cand_n
        best_thr = cand_thr
        asof = str(pd.Timestamp(df["trade_date"].iloc[t - 1]).date())
        param_history.append(
            {
                "asof": asof,
                "n": best_n,
                "thr": dict(best_thr),
                "score": None if best_score < -100 else round(float(best_score), 4),
            }
        )
        t += cfg.retrain_freq

    if not param_history:
        return WFResult(status="skipped", reason="无有效 retrain 点")

    prev = param_history[-2] if len(param_history) >= 2 else None
    param_delta: dict[str, Any] = {}
    if prev:
        if prev["n"] != best_n:
            param_delta["n"] = {"from": prev["n"], "to": best_n}
        if prev.get("thr") != best_thr:
            param_delta["thr"] = {"from": prev.get("thr"), "to": best_thr}

    rule = RuleSpec(
        entry=entry, exit=exit_, filt=filt, n=best_n, thr=best_thr or None
    )
    try:
        rep = replay(df, rule)
    except Exception as exc:  # noqa: BLE001
        return WFResult(status="error", reason=f"replay 失败: {exc}")

    return WFResult(
        status="ok",
        reason="",
        best_n=best_n,
        best_thr=dict(best_thr),
        param_history=param_history,
        param_delta=param_delta,
        replay=rep,
    )
