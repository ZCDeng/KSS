"""板块动量轮动 —— 按板块/热点动量分映射到个股后取 Top-N.

期望面板已含 ``sector_momentum_score``（由日更 runner 从 hotspot_rotation
龙头/板块动量预计算写入）。数据不足时由上层捕获 KeyError/ValueError 做 R7 占位。

出处：awesome sector momentum rotational system。
"""

from __future__ import annotations

from typing import Any

from kss.backtest.cost_model import CostModel
from kss.strategies.style_base import FactorRankStyleStrategy, StyleMeta

SECTOR_ROTATION_META = StyleMeta(
    style_id="style_sector_rotation",
    name="板块动量轮动",
    factor_col="sector_momentum_score",
    direction="desc",
    source_tags=(
        "awesome-systematic-trading:sector-momentum-rotational-system",
        "paper:ssrn-1585517",
        "kss:hotspot_rotation",
    ),
    reason_template="{style_name}：板块动量分={factor_value:.3f}（第{rank_position}名）",
    strategy_family="single_factor",
)


class SectorRotationStyleStrategy(FactorRankStyleStrategy):
    def __init__(
        self,
        *,
        top_n: int = 5,
        cost_model: CostModel | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            SECTOR_ROTATION_META, top_n=top_n, cost_model=cost_model, **kwargs
        )
