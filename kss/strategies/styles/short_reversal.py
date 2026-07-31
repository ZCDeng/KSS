"""短期反转 —— 近 5 日收益升序（弱者反转）.

出处：awesome short-term reversal in stocks。
"""

from __future__ import annotations

from typing import Any

from kss.backtest.cost_model import CostModel
from kss.strategies.style_base import FactorRankStyleStrategy, StyleMeta

SHORT_REVERSAL_META = StyleMeta(
    style_id="style_short_reversal",
    name="短期反转",
    factor_col="ret_5d",
    direction="asc",
    source_tags=(
        "awesome-systematic-trading:short-term-reversal-in-stocks",
        "paper:ssrn-1605049",
    ),
    reason_template="{style_name}：{factor_col}={factor_value:.2%}（第{rank_position}名，近5日弱者优先）",
    strategy_family="single_factor",
)


class ShortReversalStyleStrategy(FactorRankStyleStrategy):
    def __init__(
        self,
        *,
        top_n: int = 5,
        cost_model: CostModel | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            SHORT_REVERSAL_META, top_n=top_n, cost_model=cost_model, **kwargs
        )
