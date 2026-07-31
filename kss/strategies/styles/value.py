"""价值风格 —— PB 升序（低 PB 优先，PIT daily_basic）.

出处：awesome value book-to-market 因子。
"""

from __future__ import annotations

from typing import Any

from kss.backtest.cost_model import CostModel
from kss.strategies.style_base import FactorRankStyleStrategy, StyleMeta

VALUE_META = StyleMeta(
    style_id="style_value",
    name="价值",
    factor_col="pb",
    direction="asc",
    source_tags=(
        "awesome-systematic-trading:value-book-to-market-factor",
        "paper:ssrn-2595747",
    ),
    reason_template="{style_name}：{factor_col}={factor_value:.3f}（第{rank_position}名，低PB优先）",
    strategy_family="single_factor",
)


class ValueStyleStrategy(FactorRankStyleStrategy):
    def __init__(
        self,
        *,
        top_n: int = 5,
        cost_model: CostModel | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(VALUE_META, top_n=top_n, cost_model=cost_model, **kwargs)
