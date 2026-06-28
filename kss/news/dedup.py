"""信息源去重 + 源真实性加权(plan U4 / R3 去重 / R14)。

两道防线,产出"独立信息源"口径供 U5 算跨源集中度:

1. **近似文本去重**(AE3):同一公告/原文被多源转发,内容近似 → 归并为 **1 个近似簇**,
   只记 1 次独立确认。防"出现在 3 个源"被误判为跨源集中。
2. **源真实性加权**(AE4):同一近似文案被多个新建/低龄账号同步发(协同刷量)→ 该簇
   标为不可信,不计入有机集中度。账号年龄/作者多样性信号在源缺失时优雅降级(无元数据
   时默认按可信处理,v1 已知局限)。

核心口径:``independent_confirmations`` = 可信近似簇数。U5 以 ≥2 上榜。纯函数模块。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PUNCT = re.compile(r"[\s　,，。.、!！?？;；:：\"'“”‘’()（）\[\]【】#@/\\\-_+~·*]+")


def normalize_text(text: str) -> str:
    """归一:去标点/空白、ASCII 转小写,保留 CJK 与字母数字。用于近似比较。"""
    t = (text or "").lower()
    return _PUNCT.sub("", t)


def shingles(text: str, k: int = 3) -> set[str]:
    """字符 k-gram 集合(对中文近似转发敏感)。短文本退化为整串。"""
    norm = normalize_text(text)
    if len(norm) <= k:
        return {norm} if norm else set()
    return {norm[i : i + k] for i in range(len(norm) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _item_text(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}".strip()


@dataclass
class Cluster:
    """一个近似簇 = 一条独立信息(可能被多源转发)。"""

    items: list[dict[str, Any]] = field(default_factory=list)
    shingle: set[str] = field(default_factory=set)
    authentic: bool = True
    reason: str = ""

    @property
    def sources(self) -> list[str]:
        seen: list[str] = []
        for it in self.items:
            s = str(it.get("source") or "")
            if s and s not in seen:
                seen.append(s)
        return seen

    @property
    def authors(self) -> list[str]:
        return [str(it.get("author")) for it in self.items if it.get("author")]

    @property
    def representative(self) -> dict[str, Any]:
        # 取 heat 最高(否则第一条)作代表
        return max(self.items, key=lambda it: it.get("heat") or 0)


def cluster_near_duplicates(items: list[dict[str, Any]], *, threshold: float = 0.6) -> list[Cluster]:
    """贪心近似聚类:与已有簇代表 shingle 的 Jaccard ≥ threshold 即归并,否则新簇。"""
    clusters: list[Cluster] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sh = shingles(_item_text(it))
        placed = False
        for c in clusters:
            if jaccard(sh, c.shingle) >= threshold:
                c.items.append(it)
                # 代表 shingle 取并集,使簇能吸附后续近似变体
                c.shingle |= sh
                placed = True
                break
        if not placed:
            clusters.append(Cluster(items=[it], shingle=sh))
    return clusters


def _assess_authenticity(
    cluster: Cluster,
    *,
    new_account_age_days: int = 30,
    new_account_ratio: float = 0.5,
    min_items_for_pump: int = 3,
) -> tuple[bool, str]:
    """协同刷量识别(AE4)。同一近似文案被 ≥min 个账号同步发,且其中过半为低龄账号
    → 不可信。账号年龄缺失(当前多数源)时无法判定,默认可信(v1 局限)。"""
    ages = [it.get("author_age_days") for it in cluster.items if it.get("author_age_days") is not None]
    distinct_authors = {a for a in cluster.authors}
    if len(cluster.items) >= min_items_for_pump and len(distinct_authors) >= min_items_for_pump and ages:
        new_ratio = sum(1 for a in ages if a < new_account_age_days) / len(ages)
        if new_ratio >= new_account_ratio:
            return False, "coordinated_pump:low_account_age"
    return True, ""


@dataclass
class DedupeResult:
    clusters: list[Cluster]

    @property
    def authentic_clusters(self) -> list[Cluster]:
        return [c for c in self.clusters if c.authentic]

    @property
    def independent_confirmations(self) -> int:
        """可信独立信息源数(去重 + 加权后)。U5 上榜口径。"""
        return len(self.authentic_clusters)

    @property
    def distinct_sources(self) -> list[str]:
        seen: list[str] = []
        for c in self.authentic_clusters:
            for s in c.sources:
                if s not in seen:
                    seen.append(s)
        return seen


def dedupe_sources(items: list[dict[str, Any]], *, threshold: float = 0.6) -> DedupeResult:
    """对一组条目做近似去重 + 真实性加权,返回带可信标记的近似簇。"""
    clusters = cluster_near_duplicates(items, threshold=threshold)
    for c in clusters:
        c.authentic, c.reason = _assess_authenticity(c)
    return DedupeResult(clusters=clusters)


def independent_confirmations(items: list[dict[str, Any]], *, threshold: float = 0.6) -> int:
    """便捷:一组条目去重 + 加权后的可信独立信息源数。"""
    return dedupe_sources(items, threshold=threshold).independent_confirmations
