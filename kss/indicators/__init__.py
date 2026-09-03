"""指标基元库：预注册参数化技术指标（均线交叉 / RSI·动量阈值 / 布林·ATR / 支撑阻力 / 会话 VWAP）。

给 Seesaw 的指标研究写工具提供有界候选空间——参见
docs/plans/2026-07-12-004-feat-seesaw-indicator-backtest-skill-plan.md。
"""

from __future__ import annotations

from kss.indicators.primitives import FAMILIES, default_params, param_grid
from kss.indicators.rules import IndicatorSpec, compute_positions, warm_period

__all__ = [
    "FAMILIES",
    "IndicatorSpec",
    "compute_positions",
    "default_params",
    "param_grid",
    "warm_period",
]
