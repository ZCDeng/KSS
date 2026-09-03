"""紫苏叶评分计算.

四维加权评分:

1. **layer_score** — 产业链深度. 越深 (layer 越大) 越稀缺, 越少人关注.
2. **moat_score** — 竞争护城河. 全球供应商越少, 定价权越强.
3. **lock_score** — 需求锁定度. 扩产周期长 + 下游需求确定 → 供需缺口锁定.
4. **coverage_gap** — 分析师覆盖缺口. 覆盖越少, 越可能被市场低估.

每项 0-1 归一化, 加权求和后再乘以可替代性惩罚 →
perilla_score ∈ [0, 1].
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kss.supply_chain.registry import ChainConfig, StockChainInfo


def _layer_score(chain_layer: int, max_depth: int = 5) -> float:
    """产业链深度分. layer 1=0, layer 5=1."""
    clamped = max(1, min(chain_layer, max_depth))
    return (clamped - 1) / (max_depth - 1) if max_depth > 1 else 0.0


def _moat_score(n_competitors_global: int, moat_tiers: dict[int, float],
                default: float = 0.0) -> float:
    """竞争格局分. 供应商越少分越高."""
    return moat_tiers.get(n_competitors_global, default)


def _lock_score(demand_locked: bool, expansion_cycle_years: float,
                max_cycle: float = 3.0) -> float:
    """需求锁定度. demand_locked × (扩产周期 / 3), 上限 1.0."""
    if not demand_locked:
        return 0.0
    cycle_ratio = min(expansion_cycle_years / max_cycle, 1.0) if max_cycle > 0 else 0.0
    return cycle_ratio


def _coverage_gap_score(analyst_count: int | None, full_coverage: int = 20) -> float:
    """分析师覆盖缺口. 未知不加分，0 个分析师→1.0，20+个→0.0."""
    if analyst_count is None or full_coverage <= 0:
        return 0.0
    return max(0.0, 1.0 - min(analyst_count, full_coverage) / full_coverage)


def _substitutability_score(substitutability: str) -> float:
    """可替代性惩罚.

    Serenity 原始逻辑把「不可替代性」当作 choke point 成立的前提，不是锦上添花。
    这里不用新加权重，而是把它作为总分乘数：低可替代保留满分，medium 轻惩罚，
    high/未知则保守降级，防止“深链 + 寡头”但可替代性并不强的票被误抬。
    """
    key = str(substitutability or "").strip().lower()
    return {
        "low": 1.0,
        "medium": 0.85,
        "high": 0.25,
    }.get(key, 0.0)


def compute_perilla_score(info: StockChainInfo, config: ChainConfig) -> float:
    """计算单只股票的紫苏叶综合评分.

    Args:
        info: 个股产业链元数据.
        config: 评分配置 (权重 + moat 梯度).

    Returns:
        0-1 之间的评分. 越高 → 越符合紫苏叶理论选股标准.
    """
    w = config.scoring_weights
    moat_default = getattr(config, "_moat_default", 0.0)

    layer = _layer_score(info.chain_layer)
    moat = _moat_score(info.n_competitors_global, config.moat_tiers, moat_default)
    lock = _lock_score(info.demand_locked, info.expansion_cycle_years)
    cover = _coverage_gap_score(info.analyst_count)
    sub = _substitutability_score(info.substitutability)

    score = (
        w.layer * layer
        + w.moat * moat
        + w.lock * lock
        + w.coverage_gap * cover
    )
    score *= sub
    # 安全钳位
    return max(0.0, min(score, 1.0))


def perilla_tier(info: StockChainInfo) -> str:
    """结构性分层（精不是多: 由结构字段确定性裁定, 非分数线）.

    - ``core`` — 真·紫苏叶核心: 已绑定需求链 + 深链(L≥4) + 扩产周期≥2年 +
      全球供应商≤2(垄断/双寡头) + 国内独家 + 需求锁定 + 低可替代.
    - ``main`` — 国产替代主线(Tier A): 同一瓶颈门槛 + 全球三家寡头 +
      低/中可替代.
      有定价权但非垄断, 多为半导体设备/材料的国产替代赛道.
    - ``watch`` — 其余(链层不足 / moat 靠分项补 / 需求未锁定), 不入正式列表.

    Returns:
        ``"core"`` / ``"main"`` / ``"watch"``.
    """
    sub = str(info.substitutability or "").strip().lower()
    bottleneck_gate = (
        bool(info.demand_chains)
        and info.chain_layer >= 4
        and info.demand_locked
        and info.expansion_cycle_years >= 2.0
    )
    if bottleneck_gate:
        if sub == "low" and info.n_competitors_global <= 2 and info.n_competitors_domestic <= 1:
            return "core"
        if sub in {"low", "medium"} and info.n_competitors_global == 3:
            return "main"
    return "watch"


def explain_score(info: StockChainInfo, config: ChainConfig) -> dict[str, float]:
    """返回各分项明细, 用于调试 / 报告.

    Returns:
        包含 layer / moat / lock / coverage_gap / total 五个 key.
    """
    w = config.scoring_weights
    moat_default = getattr(config, "_moat_default", 0.0)

    layer = _layer_score(info.chain_layer)
    moat = _moat_score(info.n_competitors_global, config.moat_tiers, moat_default)
    lock = _lock_score(info.demand_locked, info.expansion_cycle_years)
    cover = _coverage_gap_score(info.analyst_count)
    sub = _substitutability_score(info.substitutability)

    raw_total = w.layer * layer + w.moat * moat + w.lock * lock + w.coverage_gap * cover
    total = max(0.0, min(raw_total * sub, 1.0))

    return {
        "layer": round(layer, 4),
        "moat": round(moat, 4),
        "lock": round(lock, 4),
        "coverage_gap": round(cover, 4),
        "substitutability": round(sub, 4),
        "raw_total": round(raw_total, 4),
        "total": round(total, 4),
    }
