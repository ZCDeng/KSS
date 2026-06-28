"""集中热点方向计算(plan U5 / R3, R4 热度)。

把去重 + 真实性加权后的条目聚成"方向桶",热度由代码确定性计算,跨独立信息源
≥2 才上榜(单源刷屏/同公告转发不入榜,见 U4 去重)。

方向桶的关键词词表默认取自题材库(``kss.sector.themes.load_themes`` 的主题名 +
行业名 + 概念名),**不依赖 U7 的匹配器代码**(U5 属共用底座,先于题材映射)。
词表可注入,便于测试与调参。热度分纯代码、可复现;LLM 不参与本单元。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from kss.news.dedup import dedupe_sources

# 兜底种子词表(题材库不可用时用,覆盖常见 A 股方向 + 宏观/商品)。
_SEED_LEXICON: tuple[str, ...] = (
    "半导体", "光模块", "AI算力", "算力", "存储芯片", "新能源", "储能", "固态电池",
    "锂电池", "光伏", "风电", "氢能", "核电", "生物医药", "创新药", "CXO", "军工",
    "低空经济", "商业航天", "机器人", "具身智能", "工业母机", "数字经济", "数据要素",
    "券商", "保险", "银行", "地产", "白酒", "消费", "黄金", "贵金属", "石油", "油气",
    "煤炭", "有色", "稀土", "降息", "稳定币", "数字货币", "船舶", "航运", "港口",
)

_CONCEPT_SUFFIX = re.compile(r"(概念|板块|产业(链)?|指数)$")


def _clean_term(term: str) -> str:
    return _CONCEPT_SUFFIX.sub("", (term or "").strip())


def default_lexicon() -> list[str]:
    """默认方向关键词:题材库主题/行业/概念名(去尾缀)∪ 种子词。运行时读 config,
    读不到则只用种子词(优雅降级)。"""
    terms: set[str] = set(_SEED_LEXICON)
    try:
        from kss.sector.themes import load_themes

        for name, bucket in (load_themes() or {}).items():
            if name:
                terms.add(name)
            for t in list(getattr(bucket, "industries", []) or []) + list(getattr(bucket, "concepts", []) or []):
                ct = _clean_term(t)
                if len(ct) >= 2:
                    terms.add(ct)
    except Exception:  # noqa: BLE001 - 题材库不可用不应让热点计算崩
        pass
    # 长词优先匹配,避免短词吞并(如先配"AI算力"再"算力")
    return sorted((t for t in terms if len(t) >= 2), key=len, reverse=True)


@dataclass
class Direction:
    label: str
    items: list[dict[str, Any]] = field(default_factory=list)
    independent_confirmations: int = 0
    distinct_sources: list[str] = field(default_factory=list)
    mention_count: int = 0
    raw_heat_total: int = 0
    heat_score: int = 0


def _item_text(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}"


def _heat_score(independent: int, mentions: int, raw_heat_total: int) -> int:
    """确定性热度分。独立确认数主导,提及数次之,原始热度(微博等)做小幅加成。"""
    raw_bonus = min(raw_heat_total // 100_000, 50)  # 每 10 万热度 +1,封顶 50
    return independent * 1000 + mentions * 10 + raw_bonus


def build_directions(
    items: list[dict[str, Any]] | None,
    *,
    lexicon: Iterable[str] | None = None,
    min_confirmations: int = 2,
    threshold: float = 0.6,
) -> list[Direction]:
    """聚方向桶并排序。每条按关键词归入一个或多个方向;每个方向用 U4 去重 + 加权
    得可信独立确认数,≥``min_confirmations`` 上榜。按热度分降序、标签次序稳定排序。"""
    lex = list(lexicon) if lexicon is not None else default_lexicon()
    lex = sorted({t for t in lex if t}, key=len, reverse=True)

    buckets: dict[str, list[dict[str, Any]]] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        text = _item_text(it)
        for kw in lex:
            if kw and kw in text:
                buckets.setdefault(kw, []).append(it)

    directions: list[Direction] = []
    for label, bucket in buckets.items():
        res = dedupe_sources(bucket, threshold=threshold)
        ic = res.independent_confirmations
        if ic < min_confirmations:
            continue
        raw_heat = sum(int(it.get("heat") or 0) for c in res.authentic_clusters for it in c.items)
        mentions = sum(len(c.items) for c in res.authentic_clusters)
        directions.append(
            Direction(
                label=label,
                items=[it for c in res.authentic_clusters for it in c.items],
                independent_confirmations=ic,
                distinct_sources=res.distinct_sources,
                mention_count=mentions,
                raw_heat_total=raw_heat,
                heat_score=_heat_score(ic, mentions, raw_heat),
            )
        )

    directions.sort(key=lambda d: (-d.heat_score, d.label))
    return directions


# ====================================================================== #
# U8: 关联标的映射 + provenance 审计(R7, R5;经 U7 命中率 gate 放行)
# ====================================================================== #

def _direction_source_posts(direction: Any) -> list[dict[str, Any]]:
    """方向的驱动来源帖(R5 provenance,随归档持久化)。"""
    items = direction.get("items", []) if isinstance(direction, dict) else getattr(direction, "items", [])
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for it in items or []:
        key = (str(it.get("source", "")), str(it.get("url", "")) or str(it.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append({"source": str(it.get("source", "")), "title": str(it.get("title", "")), "url": str(it.get("url", ""))})
    return out


def attach_related_stocks(
    directions: list[Any] | None,
    *,
    theme_leaders: list[dict[str, Any]] | None = None,
    matcher: Any = None,
) -> list[dict[str, Any]]:
    """给每个方向挂关联标的(R7),留来源审计链(R5)。

    直达主题 → 取该主题 ``_theme_leaders`` 的板块/龙头/二梯队;主题无 board(宏观/商品
    不在 sector_rotation 快照宇宙)→ **落 R8 降级、不空挂、绝不臆造个股**(AE1)。
    每个 surfaced 标的记录驱动它的来源帖。``theme_leaders`` 为 ``_theme_leaders()`` 输出
    (调用方注入,避免 kss.news → bridge 循环依赖);``matcher`` 默认 U7 的 match_theme。
    """
    if matcher is None:
        from kss.news.theme_match import match_theme as matcher  # noqa: PLW0127

    tl_index: dict[str, dict[str, Any]] = {}
    for entry in theme_leaders or []:
        name = entry.get("name")
        if name:
            tl_index[name] = entry

    out: list[dict[str, Any]] = []
    for d in directions or []:
        label = _label_of_any(d)
        m = matcher(label) or {}
        theme = m.get("theme")
        direct = bool(m.get("direct_hit"))
        source_posts = _direction_source_posts(d)

        mapping: dict[str, Any] = {
            "label": label,
            "theme": theme,
            "match": "direct" if direct else "fallback",
            "boards": [],
            "stocks": [],
            "degrade": None,
            "source_posts": source_posts,
        }

        if not direct or theme not in tl_index:
            mapping["degrade"] = "no_direct_match" if not direct else "theme_not_in_library"
            out.append(mapping)
            continue

        entry = tl_index[theme]
        boards = entry.get("boards") or []
        if not boards:
            # 宏观/商品主题 board 不在快照宇宙 → R8 降级,不空挂(AE1)
            mapping["degrade"] = "theme_no_board_in_universe"
            out.append(mapping)
            continue

        by_symbol: dict[str, dict[str, Any]] = {}
        board_names: list[str] = []
        for bd in boards:
            bname = bd.get("board", "")
            board_names.append(bname)
            for tier_key, tier_label in (("leaders", "leader"), ("secondTier", "second")):
                for s in bd.get(tier_key) or []:
                    sym = s.get("symbol") or s.get("code") or ""
                    if not sym:
                        continue
                    # 候选与龙头去重:龙头优先
                    if sym in by_symbol and by_symbol[sym]["tier"] == "leader":
                        continue
                    by_symbol[sym] = {
                        "symbol": sym,
                        "name": s.get("name", ""),
                        "board": bname,
                        "tier": tier_label,
                        "theme": theme,
                        "source_posts": source_posts,
                    }
        mapping["boards"] = board_names
        mapping["stocks"] = list(by_symbol.values())
        if not mapping["stocks"]:
            mapping["degrade"] = "theme_no_board_in_universe"
        out.append(mapping)
    return out


def _label_of_any(direction: Any) -> str:
    return direction.get("label", "") if isinstance(direction, dict) else getattr(direction, "label", "")
