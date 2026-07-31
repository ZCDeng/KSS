"""推荐风格对照：四套因子横截面策略.

style_id 固定顺序供 UI 四槽位使用。
"""

from __future__ import annotations

from kss.strategies.style_base import FactorRankStyleStrategy, StyleMeta
from kss.strategies.styles.low_vol import LowVolStyleStrategy, LOW_VOL_META
from kss.strategies.styles.sector_rotation import (
    SectorRotationStyleStrategy,
    SECTOR_ROTATION_META,
)
from kss.strategies.styles.short_reversal import (
    ShortReversalStyleStrategy,
    SHORT_REVERSAL_META,
)
from kss.strategies.styles.value import ValueStyleStrategy, VALUE_META

# 推荐页固定四槽顺序（R2 / R4）
STYLE_ORDER: tuple[str, ...] = (
    "style_low_vol",
    "style_value",
    "style_short_reversal",
    "style_sector_rotation",
)

_STYLE_CTORS: dict[str, type[FactorRankStyleStrategy]] = {
    LOW_VOL_META.style_id: LowVolStyleStrategy,
    VALUE_META.style_id: ValueStyleStrategy,
    SHORT_REVERSAL_META.style_id: ShortReversalStyleStrategy,
    SECTOR_ROTATION_META.style_id: SectorRotationStyleStrategy,
}

_STYLE_METAS: dict[str, StyleMeta] = {
    m.style_id: m
    for m in (LOW_VOL_META, VALUE_META, SHORT_REVERSAL_META, SECTOR_ROTATION_META)
}


def get_style_meta(style_id: str) -> StyleMeta:
    if style_id not in _STYLE_METAS:
        raise KeyError(f"未知 style_id: {style_id!r}；允许 {list(STYLE_ORDER)}")
    return _STYLE_METAS[style_id]


def build_style_strategy(
    style_id: str, *, top_n: int = 5, **kwargs
) -> FactorRankStyleStrategy:
    """按 style_id 构造策略实例."""

    if style_id not in _STYLE_CTORS:
        raise KeyError(f"未知 style_id: {style_id!r}；允许 {list(STYLE_ORDER)}")
    return _STYLE_CTORS[style_id](top_n=top_n, **kwargs)


def build_all_style_strategies(*, top_n: int = 5) -> list[FactorRankStyleStrategy]:
    return [build_style_strategy(sid, top_n=top_n) for sid in STYLE_ORDER]


__all__ = [
    "STYLE_ORDER",
    "LOW_VOL_META",
    "VALUE_META",
    "SHORT_REVERSAL_META",
    "SECTOR_ROTATION_META",
    "LowVolStyleStrategy",
    "ValueStyleStrategy",
    "ShortReversalStyleStrategy",
    "SectorRotationStyleStrategy",
    "build_style_strategy",
    "build_all_style_strategies",
    "get_style_meta",
]
