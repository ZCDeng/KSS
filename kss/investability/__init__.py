"""可投资地图 —— 子行业节点树与五色暴露分类.

节点树是人工季度维护的判断层, 真源为 ``kss/config/investability_map.yaml``.
个股到节点的标注与 8 问答案不在这里, 走 :mod:`kss.storage.investability`.
"""

from kss.investability.registry import (
    InvestabilityMap,
    InvestabilityMapError,
    MapNode,
    PaletteColor,
)

__all__ = [
    "InvestabilityMap",
    "InvestabilityMapError",
    "MapNode",
    "PaletteColor",
]
