"""KSS macro module —— 宏观分母端框架（利率 / 通胀 / 货币 / 信用利差）.

按"分子 E / 分母 r"框架（参见
``docs/plans/2026-05-25-001-feat-macro-denominator-feed-plan.md``）：

- :class:`MacroSnapshot` —— 单日宏观快照（短端 / 长端 / 信用利差）
- :func:`load_macro_snapshot` —— 拉取 + 拼装 + 容错降级
- :func:`compute_rate_changes` —— 派生 Δr 信号（5d / 20d 变化率）

后续 P1（周期阶段分类器）在此基础上构建；P0 只提供数据底座.
"""

from __future__ import annotations

from kss.macro.derived import (
    compute_e_trend,
    compute_liquidity_index,
    compute_rate_changes,
    yc_slope_change,
    yield_curve_slope,
)
from kss.macro.regime import (
    MacroRegime,
    RegimeThresholds,
    classify_day,
    classify_history,
    classify_today,
    compute_thresholds,
    load_config,
)
from kss.macro.rotation import (
    get_avoid_industries,
    get_preferred_industries,
    get_rationale,
    score_industry_fit,
)
from kss.macro.snapshot import MacroSnapshot, load_macro_snapshot
from kss.macro.valuation import (
    ValuationResult,
    compute_hs300_n_from_panel,
    compute_n_percentile,
    compute_time_premium,
    modulate_entry_count,
    stage_rule_for_n,
)

__all__ = [
    "MacroRegime",
    "MacroSnapshot",
    "RegimeThresholds",
    "ValuationResult",
    "classify_day",
    "classify_history",
    "classify_today",
    "compute_e_trend",
    "compute_hs300_n_from_panel",
    "compute_liquidity_index",
    "compute_n_percentile",
    "compute_rate_changes",
    "compute_thresholds",
    "compute_time_premium",
    "get_avoid_industries",
    "get_preferred_industries",
    "get_rationale",
    "load_config",
    "load_macro_snapshot",
    "modulate_entry_count",
    "score_industry_fit",
    "stage_rule_for_n",
    "yc_slope_change",
    "yield_curve_slope",
]
