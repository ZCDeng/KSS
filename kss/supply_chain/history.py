"""紫苏叶 point-in-time 历史快照.

本模块只读取给定的 ``supply_chain.yaml`` 内容, 不联网、不刷新数据。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from kss.supply_chain.assessment import assess_perilla
from kss.supply_chain.registry import (
    ChainConfig,
    ChainRegistry,
    ScoringWeights,
    StockChainInfo,
    _parse_analyst_count,
    _parse_evidence_history,
    _parse_evidence_sources,
    _parse_optional_bool,
)
from kss.supply_chain.scoring import compute_perilla_score, explain_score, perilla_tier

_DEFAULT_OUTPUT_ROOT = Path("storage/research/perilla/point_in_time")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_EVIDENCE_FIELDS = (
    "structural_as_of",
    "main_business_confirmed",
    "import_substitution_valid",
    "liquidity_eligible",
    "valuation_unpriced",
    "evidence_as_of",
    "evidence_sources",
    "evidence_history",
)


def build_supply_chain_snapshot(
    config: str | Path | bytes | bytearray | Mapping[str, Any],
    *,
    as_of: str | date,
    observed_at: str | datetime,
    source_ref: str | None = None,
    source_observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    """构建紫苏叶 point-in-time 快照.

    Args:
        config: YAML 路径、原始 YAML bytes, 或已解析的 YAML 映射.
        as_of: 请求的审计基准日, ``YYYY-MM-DD``.
        observed_at: 快照观察时间；会规范化为 UTC ISO 字符串.
        source_ref: 可选来源引用, 例如 git ref、commit 或外部归档标识.
        source_observed_at: 可选来源自身观察时间；会规范化为 UTC ISO 字符串.

    Returns:
        可稳定序列化的快照字典.

    Raises:
        ValueError: 日期格式非法、基准日晚于观察日的上海自然日, 或 YAML 结构非法.
    """
    as_of_date = _parse_as_of(as_of)
    observed_dt = _parse_observed_at(observed_at, field_name="observed_at")
    observed_shanghai_day = observed_dt.astimezone(_SHANGHAI).date()
    if as_of_date > observed_shanghai_day:
        raise ValueError("as_of 不得晚于 observed_at 的上海自然日")

    source_observed = (
        _format_utc(_parse_observed_at(source_observed_at, field_name="source_observed_at"))
        if source_observed_at is not None
        else None
    )
    raw_config, config_sha256, config_path = _load_raw_config(config)
    registry = _registry_from_raw(raw_config)

    stocks: dict[str, Any] = {}
    tiers: dict[str, list[dict[str, Any]]] = {"core": [], "main": [], "watch": []}
    for ts_code in sorted(registry._stocks):
        info = registry._stocks[ts_code]
        score = compute_perilla_score(info, registry.config)
        tier = perilla_tier(info)
        assessment = assess_perilla(
            info,
            structural_updated=info.structural_as_of or registry.config.structural_updated,
            known_demand_chains=set(registry.config.demand_chains),
            as_of=as_of_date,
        )
        item = {
            "ts_code": ts_code,
            "name": info.name,
            "score": round(score, 6),
            "tier": tier,
            "score_breakdown": explain_score(info, registry.config),
            "assessment": assessment.as_dict(),
            "raw_stock": _jsonable((raw_config.get("stocks") or {}).get(ts_code, {})),
            "raw_evidence_fields": {
                key: _jsonable((raw_config.get("stocks") or {}).get(ts_code, {}).get(key))
                for key in _EVIDENCE_FIELDS
                if key in ((raw_config.get("stocks") or {}).get(ts_code, {}))
            },
        }
        stocks[ts_code] = item
        tiers.setdefault(tier, []).append(_candidate_summary(item))

    for candidates in tiers.values():
        candidates.sort(key=lambda value: (-float(value["score"]), str(value["ts_code"])))

    return {
        "schema_version": 1,
        "kind": "perilla_supply_chain_point_in_time",
        "observed_at": _format_utc(observed_dt),
        "as_of": as_of_date.isoformat(),
        "source": {
            "config_path": config_path,
            "config_sha256": config_sha256,
            "source_ref": source_ref,
            "source_observed_at": source_observed,
        },
        "metadata": {
            "structural_updated": registry.config.structural_updated,
            "analyst_updated": registry.config.analyst_updated,
            "stock_count": len(stocks),
            "tier_counts": {key: len(value) for key, value in tiers.items()},
        },
        "raw_config": _jsonable(raw_config),
        "tiers": tiers,
        "stocks": stocks,
    }


def write_supply_chain_snapshot(
    snapshot: Mapping[str, Any],
    *,
    output_root: str | Path = _DEFAULT_OUTPUT_ROOT,
) -> Path:
    """写入不可覆盖的快照 JSON.

    同名同内容重复调用会返回既有路径；同名不同内容会抛出异常。
    """
    observed_at = str(snapshot.get("observed_at") or "")
    observed_dt = _parse_observed_at(observed_at, field_name="snapshot.observed_at")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{_filename_timestamp(observed_dt)}.json"
    payload = _snapshot_bytes(snapshot)

    if target.exists():
        if target.read_bytes() == payload:
            return target
        raise FileExistsError(f"快照已存在且内容不同: {target}")

    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=root, prefix=".perilla_", suffix=".json") as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        try:
            os.link(tmp_name, target)
        except FileExistsError:
            if target.read_bytes() == payload:
                return target
            raise FileExistsError(f"快照已存在且内容不同: {target}") from None
        return target
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def snapshot_supply_chain_history(
    *,
    config: str | Path | bytes | bytearray | Mapping[str, Any],
    output_root: str | Path = _DEFAULT_OUTPUT_ROOT,
    as_of: str | date,
    observed_at: str | datetime,
    source_ref: str | None = None,
    source_observed_at: str | datetime | None = None,
) -> Path:
    """构建并写入紫苏叶 point-in-time 快照.

    Returns:
        写入或复用的快照路径.
    """
    snapshot = build_supply_chain_snapshot(
        config,
        as_of=as_of,
        observed_at=observed_at,
        source_ref=source_ref,
        source_observed_at=source_observed_at,
    )
    return write_supply_chain_snapshot(snapshot, output_root=output_root)


def _load_raw_config(config: str | Path | bytes | bytearray | Mapping[str, Any]) -> tuple[dict[str, Any], str, str | None]:
    if isinstance(config, Mapping):
        raw = _jsonable(dict(config))
        data = _snapshot_bytes(raw)
        return raw, hashlib.sha256(data).hexdigest(), None

    if isinstance(config, (bytes, bytearray)):
        data = bytes(config)
        raw = yaml.safe_load(data) or {}
        if not isinstance(raw, dict):
            raise ValueError("supply_chain YAML 顶层必须是映射")
        return _jsonable(raw), hashlib.sha256(data).hexdigest(), None

    path = Path(config)
    data = path.read_bytes()
    raw = yaml.safe_load(data) or {}
    if not isinstance(raw, dict):
        raise ValueError("supply_chain YAML 顶层必须是映射")
    return _jsonable(raw), hashlib.sha256(data).hexdigest(), str(path.resolve())


def _registry_from_raw(raw: Mapping[str, Any]) -> ChainRegistry:
    sw_raw = raw.get("scoring_weights") or {}
    weights = ScoringWeights(
        layer=float(sw_raw.get("layer", 0.25)),
        moat=float(sw_raw.get("moat", 0.35)),
        lock=float(sw_raw.get("lock", 0.25)),
        coverage_gap=float(sw_raw.get("coverage_gap", 0.15)),
    )
    mt_raw = raw.get("moat_tiers") or {}
    moat_tiers: dict[int, float] = {}
    default_moat = 0.0
    for key, value in mt_raw.items():
        if str(key) == "default":
            default_moat = float(value)
        else:
            moat_tiers[int(key)] = float(value)

    stocks: dict[str, StockChainInfo] = {}
    for ts_code, info_raw in (raw.get("stocks") or {}).items():
        if not isinstance(info_raw, Mapping):
            raise ValueError(f"{ts_code} 股票配置必须是映射")
        up = info_raw.get("us_peer")
        up = up if isinstance(up, Mapping) else {}
        ts_code_str = str(ts_code).strip()
        stocks[ts_code_str] = StockChainInfo(
            ts_code=ts_code_str,
            name=str(info_raw.get("name", "")),
            demand_chains=tuple(info_raw.get("demand_chains") or []),
            chain_layer=int(info_raw.get("chain_layer", 1)),
            chain_role=str(info_raw.get("chain_role", "unknown")),
            n_competitors_global=int(info_raw.get("n_competitors_global", 10)),
            n_competitors_domestic=int(info_raw.get("n_competitors_domestic", 10)),
            substitutability=str(info_raw.get("substitutability", "high")),
            expansion_cycle_years=float(info_raw.get("expansion_cycle_years", 0)),
            demand_locked=bool(info_raw.get("demand_locked", False)),
            analyst_count=_parse_analyst_count(info_raw.get("analyst_count")),
            analyst_notes=str(info_raw.get("analyst_notes", "")),
            us_peer_ticker=str(up.get("ticker", "")).strip(),
            us_peer_name=str(up.get("name", "")).strip(),
            main_business_confirmed=_parse_optional_bool(info_raw.get("main_business_confirmed")),
            import_substitution_valid=_parse_optional_bool(info_raw.get("import_substitution_valid")),
            liquidity_eligible=_parse_optional_bool(info_raw.get("liquidity_eligible")),
            valuation_unpriced=_parse_optional_bool(info_raw.get("valuation_unpriced")),
            structural_as_of=str(info_raw.get("structural_as_of", "")).strip(),
            evidence_as_of=str(info_raw.get("evidence_as_of", "")).strip(),
            evidence_sources=_parse_evidence_sources(info_raw.get("evidence_sources")),
            evidence_history=_parse_evidence_history(info_raw.get("evidence_history")),
        )

    config = ChainConfig(
        scoring_weights=weights,
        moat_tiers=moat_tiers,
        ranking_multiplier=float(raw.get("ranking_multiplier", 0.3)),
        demand_chains=dict(raw.get("demand_chains") or {}),
        structural_updated=str(raw.get("structural_updated") or raw.get("updated") or "") or None,
        analyst_updated=str(raw.get("analyst_updated") or "") or None,
    )
    config._moat_default = default_moat  # type: ignore[attr-defined]
    return ChainRegistry(stocks=stocks, config=config)


def _candidate_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    assessment = item["assessment"]
    return {
        "ts_code": item["ts_code"],
        "name": item["name"],
        "score": item["score"],
        "assessment_status": assessment["status"],
        "review_flags": assessment["reviewFlags"],
        "exclusion_reasons": assessment["exclusionReasons"],
    }


def _parse_as_of(value: str | date) -> date:
    if isinstance(value, datetime):
        raise ValueError("as_of 必须是 YYYY-MM-DD 日期, 不能是日期时间")
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError("as_of 必须是 YYYY-MM-DD") from exc


def _parse_observed_at(value: str | datetime, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是带时区的 ISO 日期时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} 必须带时区")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _filename_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _snapshot_bytes(value: Any) -> bytes:
    return (json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
