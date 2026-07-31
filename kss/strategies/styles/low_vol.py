"""低波风格 —— 短窗波动升序（低波优先）.

出处：awesome low-volatility / betting-against-beta 类股票策略。
"""

from __future__ import annotations

from typing import Any

from kss.backtest.cost_model import CostModel
from kss.strategies.style_base import FactorRankStyleStrategy, StyleMeta

LOW_VOL_META = StyleMeta(
    style_id="style_low_vol",
    name="低波",
    factor_col="volatility_20d",
    direction="asc",
    source_tags=(
        "awesome-systematic-trading:low-volatility-factor-effect-in-stocks",
        "paper:ssrn-980865",
    ),
    reason_template="{style_name}：{factor_col}={factor_value:.4f}（第{rank_position}名，低波优先）",
    strategy_family="single_factor",
)


class LowVolStyleStrategy(FactorRankStyleStrategy):
    def __init__(
        self,
        *,
        top_n: int = 5,
        cost_model: CostModel | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            LOW_VOL_META, top_n=top_n, cost_model=cost_model, **kwargs
        )
