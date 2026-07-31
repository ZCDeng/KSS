"""KSS 策略模块 —— 策略基类、横截面选股与信号生成器."""

from __future__ import annotations

from kss.strategies.base import StrategyBase
from kss.strategies.cross_sectional import CrossSectionalStrategy
from kss.strategies.risk_filters import (
    FilterResult,
    apply_all_filters,
    filter_high_leverage,
    filter_low_liquidity,
    filter_st_risk,
    summarize_removed,
)
from kss.strategies.signal_generator import SignalGenerator
from kss.strategies.style_base import FactorRankStyleStrategy, GateResult, StyleMeta
from kss.strategies.styles import (
    STYLE_ORDER,
    build_all_style_strategies,
    build_style_strategy,
    get_style_meta,
)

__all__ = [
    "CrossSectionalStrategy",
    "FactorRankStyleStrategy",
    "FilterResult",
    "GateResult",
    "STYLE_ORDER",
    "SignalGenerator",
    "StrategyBase",
    "StyleMeta",
    "apply_all_filters",
    "build_all_style_strategies",
    "build_style_strategy",
    "filter_high_leverage",
    "filter_low_liquidity",
    "filter_st_risk",
    "get_style_meta",
    "summarize_removed",
]
