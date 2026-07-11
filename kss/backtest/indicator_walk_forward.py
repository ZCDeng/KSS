"""通用 walk-forward 回测：固定基元族下滚动重估参数.

与 ``kss.backtest.mi_walk_forward`` 同式（同款 ``_score_window`` holdout 夏普
打分 + 回撤惩罚），泛化为消费 ``kss.indicators`` 任意基元族与其参数网格，
而非 MI 专属的 N/thr 网格。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from kss.backtest.metrics import Metrics
from kss.indicators.primitives import param_grid
from kss.indicators.rules import IndicatorSpec, compute_positions, replay, warm_period

DEFAULT_TRAIN_WINDOW = 252
DEFAULT_RETRAIN_FREQ = 20
DEFAULT_HOLDOUT_BARS = 63
DEFAULT_MIN_TRADES = 4


@dataclass
class WFConfig:
    """滚动重估超参（与 mi_walk_forward.WFConfig 默认值一致）."""

    train_window: int = DEFAULT_TRAIN_WINDOW
    retrain_freq: int = DEFAULT_RETRAIN_FREQ
    holdout_bars: int = DEFAULT_HOLDOUT_BARS
    min_trades: int = DEFAULT_MIN_TRADES


@dataclass
class WFResult:
    """重估结果."""

    status: str  # ok | skipped | error
    reason: str = ""
    best_params: dict[str, Any] = field(default_factory=dict)
    param_history: list[dict[str, Any]] = field(default_factory=list)
    param_delta: dict[str, Any] = field(default_factory=dict)
    replay: dict[str, Any] = field(default_factory=dict)


def _score_window(
    train_df: pd.DataFrame,
    family: str,
    params: dict[str, Any],
    holdout_bars: int,
    min_trades: int,
) -> float:
    """训练窗末 holdout 段夏普；交易过少返回 -999（与 mi_walk_forward._score_window 同式）。"""
    spec = IndicatorSpec(family, params)
    warm = warm_period(spec)
    if len(train_df) < holdout_bars + warm + 20:
        return -999.0
    feat = compute_positions(train_df, spec)
    pos = feat["position"]
    start = max(warm, len(feat) - holdout_bars)
    sub_pos = pos.iloc[start:]
    sub_ret = feat["ret"].iloc[start:]
    net = (sub_pos * sub_ret).dropna()
    flips = int((sub_pos.diff().abs() > 1e-9).sum())
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


def reestimate(
    df: pd.DataFrame,
    family: str,
    cfg: WFConfig | None = None,
) -> WFResult:
    """滚动重估基元族参数，再用末窗最优参数全样本 replay."""
    cfg = cfg or WFConfig()
    if len(df) < cfg.train_window + 30:
        return WFResult(
            status="skipped",
            reason=f"样本不足: {len(df)} < train_window+30={cfg.train_window + 30}",
        )

    grid = param_grid(family)
    param_history: list[dict[str, Any]] = []
    best_params = grid[0]

    t = cfg.train_window
    while t < len(df):
        train = df.iloc[t - cfg.train_window : t].reset_index(drop=True)
        best_score = -1e18
        cand_params = best_params
        for params in grid:
            sc = _score_window(train, family, params, cfg.holdout_bars, cfg.min_trades)
            if sc > best_score:
                best_score = sc
                cand_params = params
        best_params = cand_params
        asof = str(pd.Timestamp(df["trade_date"].iloc[t - 1]).date())
        param_history.append(
            {
                "asof": asof,
                "params": dict(best_params),
                "score": None if best_score < -100 else round(float(best_score), 4),
            }
        )
        t += cfg.retrain_freq

    if not param_history:
        return WFResult(status="skipped", reason="无有效 retrain 点")

    prev = param_history[-2] if len(param_history) >= 2 else None
    param_delta: dict[str, Any] = {}
    if prev and prev["params"] != best_params:
        param_delta["params"] = {"from": prev["params"], "to": best_params}

    spec = IndicatorSpec(family, best_params)
    try:
        rep = replay(df, spec)
    except Exception as exc:  # noqa: BLE001
        return WFResult(status="error", reason=f"replay 失败: {exc}")

    return WFResult(
        status="ok",
        reason="",
        best_params=dict(best_params),
        param_history=param_history,
        param_delta=param_delta,
        replay=rep,
    )
