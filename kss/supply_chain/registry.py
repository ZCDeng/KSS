"""产业链元数据注册与查询.

从 ``kss/config/supply_chain.yaml`` 加载个股产业链标注,
提供 ``get`` / ``score`` / ``candidates`` 等查询接口.
缺失文件或个股未标注时优雅降级（返回 None / 0）.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from kss.supply_chain.assessment import PerillaAssessment

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "supply_chain.yaml"


def _parse_analyst_count(value: Any) -> int | None:
    """解析 analyst_count.

    未知覆盖度保留为 ``None``，不伪造成“0 家覆盖”或“20 家覆盖”；评分层
    对未知值不给 coverage gap 奖励，展示层仍能明确识别数据缺口.
    """
    if value is None or value == "":
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _parse_optional_bool(value: Any) -> bool | None:
    """解析三态证据结论；缺失保持未知，非法值拒绝静默转真."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(f"证据结论必须是 bool/null，实际为 {value!r}")


def _parse_evidence_sources(value: Any) -> tuple[str, ...]:
    """解析证据标识列表，过滤空值并拒绝把字符串拆成字符."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("evidence_sources 必须是列表")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _parse_evidence_history(value: Any) -> tuple[dict[str, Any], ...]:
    """解析 point-in-time 证据历史.

    每条历史都保留为映射, 以便上层按需渲染 source / as_of / support 等字段。
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("evidence_history 必须是列表")

    history: list[dict[str, Any]] = []
    for item in value:
        if item is None:
            continue
        if not isinstance(item, dict):
            raise ValueError("evidence_history 每项必须是映射")
        cleaned = {str(key).strip(): val for key, val in item.items() if str(key).strip()}
        if cleaned:
            history.append(cleaned)
    return tuple(history)


@dataclass(frozen=True)
class StockChainInfo:
    """单只股票的产业链元数据."""

    ts_code: str
    name: str
    demand_chains: tuple[str, ...]
    chain_layer: int
    chain_role: str
    n_competitors_global: int
    n_competitors_domestic: int
    substitutability: str          # high / medium / low
    expansion_cycle_years: float
    demand_locked: bool
    analyst_count: int | None
    analyst_notes: str
    us_peer_ticker: str = ""        # 美股对标代码(如 LRCX); 空=无干净对标
    us_peer_name: str = ""          # 美股对标名(如 Lam Research)
    main_business_confirmed: bool | None = None
    import_substitution_valid: bool | None = None
    liquidity_eligible: bool | None = None
    valuation_unpriced: bool | None = None
    structural_as_of: str = ""
    evidence_as_of: str = ""
    evidence_sources: tuple[str, ...] = ()
    evidence_history: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """转字典（Telegram / banner 用）."""
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "demand_chains": list(self.demand_chains),
            "chain_layer": self.chain_layer,
            "chain_role": self.chain_role,
            "n_competitors_global": self.n_competitors_global,
            "n_competitors_domestic": self.n_competitors_domestic,
            "substitutability": self.substitutability,
            "expansion_cycle_years": self.expansion_cycle_years,
            "demand_locked": self.demand_locked,
            "analyst_count": self.analyst_count,
            "analyst_notes": self.analyst_notes,
            "us_peer_ticker": self.us_peer_ticker,
            "us_peer_name": self.us_peer_name,
            "main_business_confirmed": self.main_business_confirmed,
            "import_substitution_valid": self.import_substitution_valid,
            "liquidity_eligible": self.liquidity_eligible,
            "valuation_unpriced": self.valuation_unpriced,
            "structural_as_of": self.structural_as_of,
            "evidence_as_of": self.evidence_as_of,
            "evidence_sources": list(self.evidence_sources),
            "evidence_history": list(self.evidence_history),
        }


@dataclass
class ScoringWeights:
    """评分权重配置."""

    layer: float = 0.25
    moat: float = 0.35
    lock: float = 0.25
    coverage_gap: float = 0.15


@dataclass
class ChainConfig:
    """顶层配置容器."""

    scoring_weights: ScoringWeights
    moat_tiers: dict[int, float]
    ranking_multiplier: float
    demand_chains: dict[str, Any]
    structural_updated: str | None = None
    analyst_updated: str | None = None


class ChainRegistry:
    """产业链注册表 —— 加载 YAML、查询个股、计算评分."""

    def __init__(
        self,
        stocks: dict[str, StockChainInfo],
        config: ChainConfig,
    ) -> None:
        self._stocks = stocks
        self._config = config

    # ------------------------------------------------------------------ #
    # 构造
    # ------------------------------------------------------------------ #

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> ChainRegistry:
        """从 YAML 文件加载. 文件缺失返回空注册表 + warning."""
        p = Path(path) if path else _CONFIG_PATH
        if not p.exists():
            logger.warning("supply_chain.yaml 不存在 (%s), 紫苏叶评分将全部为 0", p)
            return cls(
                stocks={},
                config=ChainConfig(
                    scoring_weights=ScoringWeights(),
                    moat_tiers={1: 1.0, 2: 0.7, 3: 0.3},
                    ranking_multiplier=0.3,
                    demand_chains={},
                ),
            )

        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # 解析权重
        sw_raw = raw.get("scoring_weights", {})
        weights = ScoringWeights(
            layer=float(sw_raw.get("layer", 0.25)),
            moat=float(sw_raw.get("moat", 0.35)),
            lock=float(sw_raw.get("lock", 0.25)),
            coverage_gap=float(sw_raw.get("coverage_gap", 0.15)),
        )

        # 解析 moat 梯度
        mt_raw = raw.get("moat_tiers", {})
        moat_tiers: dict[int, float] = {}
        default_moat = 0.0
        for k, v in mt_raw.items():
            if k == "default":
                default_moat = float(v)
            else:
                moat_tiers[int(k)] = float(v)

        ranking_mult = float(raw.get("ranking_multiplier", 0.3))
        demand_chains = raw.get("demand_chains", {})
        structural_updated = raw.get("structural_updated") or raw.get("updated")
        analyst_updated = raw.get("analyst_updated")

        # 解析个股
        stocks: dict[str, StockChainInfo] = {}
        for ts_code, info in (raw.get("stocks") or {}).items():
            ts_code = str(ts_code).strip()
            up = info.get("us_peer")
            up = up if isinstance(up, dict) else {}
            try:
                stocks[ts_code] = StockChainInfo(
                    ts_code=ts_code,
                    name=str(info.get("name", "")),
                    demand_chains=tuple(info.get("demand_chains") or []),
                    chain_layer=int(info.get("chain_layer", 1)),
                    chain_role=str(info.get("chain_role", "unknown")),
                    n_competitors_global=int(info.get("n_competitors_global", 10)),
                    n_competitors_domestic=int(info.get("n_competitors_domestic", 10)),
                    substitutability=str(info.get("substitutability", "high")),
                    expansion_cycle_years=float(info.get("expansion_cycle_years", 0)),
                    demand_locked=bool(info.get("demand_locked", False)),
                    analyst_count=_parse_analyst_count(info.get("analyst_count")),
                    analyst_notes=str(info.get("analyst_notes", "")),
                    us_peer_ticker=str(up.get("ticker", "")).strip(),
                    us_peer_name=str(up.get("name", "")).strip(),
                    main_business_confirmed=_parse_optional_bool(info.get("main_business_confirmed")),
                    import_substitution_valid=_parse_optional_bool(info.get("import_substitution_valid")),
                    liquidity_eligible=_parse_optional_bool(info.get("liquidity_eligible")),
                    valuation_unpriced=_parse_optional_bool(info.get("valuation_unpriced")),
                    structural_as_of=str(info.get("structural_as_of", "")).strip(),
                    evidence_as_of=str(info.get("evidence_as_of", "")).strip(),
                    evidence_sources=_parse_evidence_sources(info.get("evidence_sources")),
                    evidence_history=_parse_evidence_history(info.get("evidence_history")),
                )
            except (ValueError, TypeError) as exc:
                logger.warning("解析 %s 失败: %s, 跳过", ts_code, exc)

        config = ChainConfig(
            scoring_weights=weights,
            moat_tiers=moat_tiers,
            ranking_multiplier=ranking_mult,
            demand_chains=demand_chains,
            structural_updated=str(structural_updated) if structural_updated else None,
            analyst_updated=str(analyst_updated) if analyst_updated else None,
        )
        # 把 default_moat 挂到 config 上, scoring 模块需要
        config._moat_default = default_moat  # type: ignore[attr-defined]

        logger.info("加载产业链标注 %d 只, 需求链 %d 条", len(stocks), len(demand_chains))
        return cls(stocks=stocks, config=config)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def get(self, ts_code: str) -> StockChainInfo | None:
        """查个股元数据; 裸码(688012) 或完整码(688012.SH) 均可."""
        ts_code = str(ts_code).strip()
        if ts_code in self._stocks:
            return self._stocks[ts_code]
        # 裸码 → 尝试加后缀
        for suffix in (".SH", ".SZ", ".BJ"):
            full = f"{ts_code}{suffix}"
            if full in self._stocks:
                return self._stocks[full]
        return None

    def score(self, ts_code: str) -> float:
        """计算紫苏叶评分 (0-1). 未标注 → 0."""
        from kss.supply_chain.scoring import compute_perilla_score

        info = self.get(ts_code)
        if info is None:
            return 0.0
        return compute_perilla_score(info, self._config)

    def tier(self, ts_code: str) -> str:
        """结构性分层 core / main / watch; 未标注 → none. 见 scoring.perilla_tier."""
        from kss.supply_chain.scoring import perilla_tier

        info = self.get(ts_code)
        if info is None:
            return "none"
        return perilla_tier(info)

    def candidates(self, min_score: float = 0.4) -> list[tuple[str, float]]:
        """返回所有 perilla_score ≥ min_score 的 (ts_code, score) 列表, 降序."""
        results = []
        for ts_code in self._stocks:
            s = self.score(ts_code)
            if s >= min_score:
                results.append((ts_code, s))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def assess(self, ts_code: str, *, as_of: date | None = None) -> PerillaAssessment | None:
        """返回紫苏叶证据审计结果; 未标注返回 ``None``."""
        from kss.supply_chain.assessment import assess_perilla

        info = self.get(ts_code)
        if info is None:
            return None
        return assess_perilla(
            info,
            structural_updated=info.structural_as_of or self._config.structural_updated,
            known_demand_chains=set(self._config.demand_chains),
            as_of=as_of,
        )

    def format_tag(self, ts_code: str) -> str:
        """生成一行产业链标签 (Telegram / banner 用).

        示例: "🍃 0.55 | L4 设备 | 全球3家国内独家 | 需求锁定"
        """
        info = self.get(ts_code)
        if info is None:
            return ""
        s = self.score(ts_code)
        if s < 0.01:
            return ""
        parts = [
            f"\U0001f33f {s:.2f}",
            f"L{info.chain_layer} {info.chain_role}",
        ]
        if info.n_competitors_domestic <= 1:
            parts.append(f"全球{info.n_competitors_global}家国内独家")
        else:
            parts.append(f"全球{info.n_competitors_global}家")
        if info.demand_locked:
            parts.append("需求锁定")
        return " | ".join(parts)

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #

    @property
    def n_stocks(self) -> int:
        return len(self._stocks)

    @property
    def config(self) -> ChainConfig:
        return self._config

    def __len__(self) -> int:
        return len(self._stocks)

    def __contains__(self, ts_code: object) -> bool:
        if not isinstance(ts_code, str):
            return False
        return self.get(ts_code) is not None

    def __repr__(self) -> str:
        return f"ChainRegistry(n_stocks={self.n_stocks})"
