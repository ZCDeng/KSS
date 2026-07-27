"""投资分析精判卡校验与 kss-equivalent-v1 确定性聚合."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

from kss.research.corpus import AnalystMessage, canonical_sha256, normalize_content_hash

PRECISION_CARD_VERSION = "precision-card-v1"
KSS_EQUIVALENT_VERSION = "kss-equivalent-v1"

Conviction = Literal["low", "medium", "high"]
EvidenceGrade = Literal["A", "B", "C", "D"]
CONVICTION_WEIGHTS: dict[str, float] = {"low": 0.5, "medium": 0.75, "high": 1.0}
EVIDENCE_GRADE_VALUES = {"A", "B", "C", "D"}
STANCE_LABELS = {-2, -1, 0, 1, 2}
FORMULA_VERSION = "investment-analysis-core-v1"


class PrecisionCardError(ValueError):
    """精判卡不满足版本、引用或枚举契约时抛出."""


@dataclass(frozen=True)
class QuoteSpan:
    """精判卡原文引用范围."""

    start: int
    end: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrecisionCard:
    """precision-card-v1 的正式内部表示."""

    card_id: str
    evidence: dict[str, Any]
    source: AnalystMessage
    analyst: dict[str, Any]
    trade_date: str
    instrument: str
    theme: str
    stance_label: int
    conviction: Conviction
    original_expression: str
    risk: str | None
    catalyst: str | None
    date_anchor: str | None
    evidence_grade: EvidenceGrade
    quote_span: QuoteSpan
    is_sellside_forward: bool
    extractor: dict[str, Any]
    checker: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_message_id(self) -> str:
        return self.source.source_message_id

    @property
    def content_hash(self) -> str:
        return self.source.content_hash

    @property
    def analyst_id(self) -> str:
        value = self.analyst.get("analyst_id", self.source.analyst_id)
        return value if isinstance(value, str) and value else self.source.analyst_id

    @property
    def conviction_weight(self) -> float:
        return CONVICTION_WEIGHTS[self.conviction]

    @property
    def stance_score(self) -> float:
        return self.stance_label / 2.0

    @property
    def direction(self) -> int:
        if self.stance_label > 0:
            return 1
        if self.stance_label < 0:
            return -1
        return 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["protocol_version"] = PRECISION_CARD_VERSION
        data["source"] = self.source.to_dict()
        return data


def check_precision_card(payload: dict[str, Any], corpus: list[AnalystMessage]) -> PrecisionCard:
    """校验 precision-card-v1 并绑定 analyst-corpus-v1 来源."""
    if not isinstance(payload, dict):
        raise PrecisionCardError("precision card 必须是 JSON object")
    if payload.get("protocol_version") != PRECISION_CARD_VERSION:
        raise PrecisionCardError(f"protocol_version 必须是 {PRECISION_CARD_VERSION}")

    evidence = _required_object(payload, "evidence")
    source_payload = _required_object(payload, "source")
    analyst = _required_object(payload, "analyst")
    source_id = _required_str(source_payload, "source_message_id")
    source = {message.source_message_id: message for message in corpus}.get(source_id)
    if source is None:
        raise PrecisionCardError(f"source_message_id 不在语料中: {source_id}")

    content_hash = normalize_content_hash(source_payload.get("content_hash", source.content_hash))
    if content_hash != source.content_hash:
        raise PrecisionCardError("source.content_hash 与语料 content_hash 不匹配")
    if evidence.get("source_message_id") != source_id:
        raise PrecisionCardError("evidence.source_message_id 必须匹配 source.source_message_id")
    if normalize_content_hash(evidence.get("content_hash")) != source.content_hash:
        raise PrecisionCardError("evidence.content_hash 与 source 不匹配")

    quote_span = _parse_quote_span(payload.get("quote_span"), source)
    original_expression = _required_str(payload, "original_expression")
    if quote_span.text != original_expression:
        raise PrecisionCardError("original_expression 必须等于 quote_span.text")

    stance_label = _stance_label(payload.get("stance_label"))
    conviction = _enum(payload.get("conviction"), CONVICTION_WEIGHTS.keys(), "conviction")
    evidence_grade = _enum(payload.get("evidence_grade"), EVIDENCE_GRADE_VALUES, "evidence_grade")
    is_sellside_forward = _required_bool(payload, "is_sellside_forward")
    source_is_forward = source.provenance.get("source_relation") == "sell_side_forward"
    if is_sellside_forward != source_is_forward:
        raise PrecisionCardError("is_sellside_forward 必须与语料 provenance.source_relation 对齐")

    analyst_id = _required_str(analyst, "analyst_id")
    if analyst_id != source.analyst_id:
        raise PrecisionCardError("analyst.analyst_id 必须匹配语料 analyst_id")

    risk = _optional_str(payload.get("risk"))
    catalyst = _optional_str(payload.get("catalyst"))
    date_anchor = _optional_date(payload.get("date_anchor"), "date_anchor")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise PrecisionCardError("metadata 必须是 JSON object")

    return PrecisionCard(
        card_id=_required_str(payload, "card_id"),
        evidence=evidence,
        source=source,
        analyst=analyst,
        trade_date=_required_date(payload, "trade_date"),
        instrument=_required_str(payload, "instrument"),
        theme=_required_str(payload, "theme"),
        stance_label=stance_label,
        conviction=conviction,  # type: ignore[arg-type]
        original_expression=original_expression,
        risk=risk,
        catalyst=catalyst,
        date_anchor=date_anchor,
        evidence_grade=evidence_grade,  # type: ignore[arg-type]
        quote_span=quote_span,
        is_sellside_forward=is_sellside_forward,
        extractor=_extraction_actor(payload, "extractor"),
        checker=_checker_actor(payload, "checker"),
        metadata=metadata,
    )


def check_precision_cards(payloads: list[dict[str, Any]], corpus: list[AnalystMessage]) -> list[PrecisionCard]:
    """批量校验 precision-card-v1,并拒绝重复 ``card_id``."""
    cards: list[PrecisionCard] = []
    seen_ids: set[str] = set()
    for payload in payloads:
        card = check_precision_card(payload, corpus)
        if card.card_id in seen_ids:
            raise PrecisionCardError(f"重复 card_id: {card.card_id}")
        seen_ids.add(card.card_id)
        cards.append(card)
    return cards


def aggregate_kss_equivalent(
    cards: list[PrecisionCard | dict[str, Any]],
    *,
    period_end: str,
    snapshot_hash: str | None = None,
    config: dict[str, Any] | None = None,
    sample_exact: bool = True,
) -> dict[str, Any]:
    """按批准公式确定性聚合为 kss-equivalent-v1.

    共识类指标使用非卖方转发卡片;风险和催化剂保留所有卡片,并暴露转发计数。
    """
    normalized = [_coerce_card(card) for card in cards]
    period_end_date = _parse_date(period_end, "period_end")
    effective_config, analyst_weights = _validated_config(config)
    input_snapshot = [_card_hash_input(card) for card in normalized]
    input_hash = canonical_sha256(input_snapshot)
    snapshot_hash = snapshot_hash or canonical_sha256({"period_end": period_end, "input_hash": input_hash})
    consensus_cards = [card for card in normalized if not card.is_sellside_forward]
    daily_theme_strength = _daily_theme_strength(
        consensus_cards,
        analyst_weights=analyst_weights,
    )

    risk_severity = _risk_severity(normalized)
    persistent_themes = _persistent_themes(consensus_cards)
    catalysts = _catalysts(normalized, period_end=period_end_date)
    analyst_profiles = _analyst_profiles(consensus_cards)
    kss_equivalent = {
        "protocol_version": KSS_EQUIVALENT_VERSION,
        "formula_version": FORMULA_VERSION,
        "period_end": period_end,
        "card_count": len(normalized),
        "consensus_card_count": len(consensus_cards),
        "sellside_forward_excluded_count": len(normalized) - len(consensus_cards),
        "temperature": _temperature(
            consensus_cards,
            analyst_weights=analyst_weights,
        ),
        "theme_strength": _theme_strength(
            consensus_cards,
            analyst_weights=analyst_weights,
        ),
        "risk_severity": risk_severity,
        "persistent_themes": persistent_themes,
        "catalysts": catalysts,
        "analyst_profiles": analyst_profiles,
        "trend": _trend(daily_theme_strength, snapshot_hash=snapshot_hash),
    }
    return {
        "protocol_version": KSS_EQUIVALENT_VERSION,
        "sample_exact": {
            "calibrated_from_sample": sample_exact,
            "risk_severity": risk_severity,
            "persistent_themes": persistent_themes,
            "catalysts": catalysts,
            "analyst_profiles": analyst_profiles,
        },
        "kss_equivalent": kss_equivalent,
        "formula_classification": {
            "risk_severity": "sample_exact",
            "persistent_themes": "sample_exact",
            "catalysts": "sample_exact",
            "analyst_profiles": "sample_exact",
            "temperature": "kss_equivalent",
            "theme_strength": "kss_equivalent",
            "trend": "kss_equivalent",
        },
        "hashes": {
            "formula_hash": canonical_sha256(_formula_config()),
            "config_hash": canonical_sha256(effective_config),
            "input_hash": input_hash,
            "snapshot_hash": snapshot_hash,
        },
        "audit_inputs": [
            {
                "card_id": card.card_id,
                "source_message_id": card.source_message_id,
                "analyst_id": card.analyst_id,
                "stance_score": card.stance_score,
                "conviction_weight": card.conviction_weight,
                "analyst_weight": analyst_weights.get(card.analyst_id, 1.0),
                "theme": card.theme,
                "risk": card.risk,
                "is_sellside_forward": card.is_sellside_forward,
            }
            for card in normalized
        ],
    }


def _card_hash_input(card: PrecisionCard) -> dict[str, Any]:
    return {
        "card_id": card.card_id,
        "source_message_id": card.source_message_id,
        "content_hash": card.content_hash,
        "analyst_id": card.analyst_id,
        "trade_date": card.trade_date,
        "instrument": card.instrument,
        "theme": card.theme,
        "stance_label": card.stance_label,
        "conviction": card.conviction,
        "risk": card.risk,
        "catalyst": card.catalyst,
        "date_anchor": card.date_anchor,
        "is_sellside_forward": card.is_sellside_forward,
    }


def _formula_config() -> dict[str, Any]:
    return {
        "formula_version": FORMULA_VERSION,
        "conviction_weights": CONVICTION_WEIGHTS,
        "temperature": "weighted_avg((stance_label/2)*conviction_weight)",
        "risk_severity": "mention_card_count + 0.5 * distinct_analyst_count",
        "persistent_theme": "same theme and direction >= 3 trade days and >= 2 analysts",
        "trend": "second_half_daily_theme_strength - first_half_daily_theme_strength",
        "consensus_filter": "exclude is_sellside_forward",
    }


def _validated_config(
    config: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, float]]:
    effective = dict(config or {})
    effective.setdefault("formula_version", FORMULA_VERSION)
    raw = effective.get("analyst_weights")
    if raw in (None, {}):
        effective["analyst_weights"] = {
            "approved": False,
            "version": "default-1.0",
            "weights": {},
        }
        return effective, {}
    if not isinstance(raw, dict):
        raise PrecisionCardError("analyst_weights 必须是版本化 JSON object")
    if raw.get("approved") is not True:
        raise PrecisionCardError("自定义 analyst_weights 必须经过用户批准")
    version = raw.get("version")
    weights = raw.get("weights")
    if not isinstance(version, str) or not version:
        raise PrecisionCardError("analyst_weights.version 必须是非空字符串")
    if not isinstance(weights, dict):
        raise PrecisionCardError("analyst_weights.weights 必须是 JSON object")
    normalized: dict[str, float] = {}
    for analyst_id, value in weights.items():
        if not isinstance(analyst_id, str) or not analyst_id:
            raise PrecisionCardError("analyst_weights 的分析师 ID 必须是非空字符串")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise PrecisionCardError("analyst_weights 权重必须是有限正数")
        normalized[analyst_id] = float(value)
    effective["analyst_weights"] = {
        "approved": True,
        "version": version,
        "weights": normalized,
    }
    return effective, normalized


def _coerce_card(card: PrecisionCard | dict[str, Any]) -> PrecisionCard:
    if isinstance(card, PrecisionCard):
        return card
    raise PrecisionCardError("aggregate_kss_equivalent 需要先通过 check_precision_cards 校验")


def _temperature(
    cards: list[PrecisionCard],
    *,
    analyst_weights: dict[str, float] | None = None,
) -> float:
    weights = analyst_weights or {}
    denominator = sum(
        weights.get(card.analyst_id, 1.0) * card.conviction_weight
        for card in cards
    )
    if denominator == 0:
        return 0.0
    numerator = sum(
        card.stance_score
        * weights.get(card.analyst_id, 1.0)
        * card.conviction_weight
        for card in cards
    )
    return _round(numerator / denominator)


def _theme_strength(
    cards: list[PrecisionCard],
    *,
    analyst_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    weights = analyst_weights or {}
    groups: dict[tuple[str, str], list[PrecisionCard]] = {}
    for card in cards:
        groups.setdefault((card.instrument, card.theme), []).append(card)
    rows: list[dict[str, Any]] = []
    for (instrument, theme), group in groups.items():
        contribution_sum = sum(
            card.stance_score
            * weights.get(card.analyst_id, 1.0)
            * card.conviction_weight
            for card in group
        )
        rows.append(
            {
                "instrument": instrument,
                "theme": theme,
                "contribution_sum": _round(contribution_sum),
                "cards": len(group),
                "analysts": sorted({card.analyst_id for card in group}),
            }
        )
    return sorted(rows, key=lambda row: (-abs(row["contribution_sum"]), row["instrument"], row["theme"]))


def _risk_severity(cards: list[PrecisionCard]) -> list[dict[str, Any]]:
    groups: dict[str, list[PrecisionCard]] = {}
    for card in cards:
        if card.risk:
            groups.setdefault(card.risk, []).append(card)
    rows: list[dict[str, Any]] = []
    for risk, group in groups.items():
        distinct_analysts = sorted({card.analyst_id for card in group})
        rows.append(
            {
                "risk": risk,
                "mention_card_count": len(group),
                "distinct_analyst_count": len(distinct_analysts),
                "severity": _round(len(group) + 0.5 * len(distinct_analysts)),
                "sellside_forward_count": sum(1 for card in group if card.is_sellside_forward),
            }
        )
    return sorted(rows, key=lambda row: (-row["severity"], row["risk"]))


def _persistent_themes(cards: list[PrecisionCard]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, int], list[PrecisionCard]] = {}
    for card in cards:
        if card.direction == 0:
            continue
        groups.setdefault((card.instrument, card.theme, card.direction), []).append(card)
    rows: list[dict[str, Any]] = []
    for (instrument, theme, direction), group in groups.items():
        trade_dates = sorted({card.trade_date for card in group})
        analysts = sorted({card.analyst_id for card in group})
        if len(trade_dates) >= 3 and len(analysts) >= 2:
            rows.append(
                {
                    "instrument": instrument,
                    "theme": theme,
                    "direction": direction,
                    "trade_days": trade_dates,
                    "analysts": analysts,
                    "cards": len(group),
                }
            )
    return sorted(rows, key=lambda row: (-len(row["trade_days"]), row["instrument"], row["theme"]))


def _catalysts(cards: list[PrecisionCard], *, period_end: date) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[PrecisionCard]] = {}
    for card in cards:
        if card.catalyst:
            groups.setdefault((card.instrument, card.catalyst), []).append(card)
    rows: list[dict[str, Any]] = []
    for (instrument, catalyst), group in groups.items():
        anchors = sorted({card.date_anchor for card in group if card.date_anchor})
        realized = any(_parse_date(anchor, "date_anchor") <= period_end for anchor in anchors)
        rows.append(
            {
                "instrument": instrument,
                "catalyst": catalyst,
                "status": "realized" if realized else "pending",
                "date_anchors": anchors,
                "cards": len(group),
                "analysts": sorted({card.analyst_id for card in group}),
                "sellside_forward_count": sum(1 for card in group if card.is_sellside_forward),
            }
        )
    return sorted(rows, key=lambda row: (row["status"] != "realized", row["instrument"], row["catalyst"]))


def _analyst_profiles(cards: list[PrecisionCard]) -> list[dict[str, Any]]:
    groups: dict[str, list[PrecisionCard]] = {}
    for card in cards:
        groups.setdefault(card.analyst_id, []).append(card)
    rows: list[dict[str, Any]] = []
    for analyst_id, group in groups.items():
        weighted = _temperature(group)
        rows.append(
            {
                "analyst_id": analyst_id,
                "cards": len(group),
                "temperature": weighted,
                "themes": sorted({card.theme for card in group}),
                "theme_coverage": len({card.theme for card in group}),
                "direction_distribution": {
                    "bearish": sum(1 for card in group if card.direction < 0),
                    "neutral": sum(1 for card in group if card.direction == 0),
                    "bullish": sum(1 for card in group if card.direction > 0),
                },
                "evidence_grade_distribution": {
                    grade: sum(1 for card in group if card.evidence_grade == grade)
                    for grade in sorted(EVIDENCE_GRADE_VALUES)
                },
                "sellside_forward_count": sum(1 for card in group if card.is_sellside_forward),
            }
        )
    return sorted(rows, key=lambda row: (-row["cards"], row["analyst_id"]))


def _daily_theme_strength(
    cards: list[PrecisionCard],
    *,
    analyst_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    weights = analyst_weights or {}
    groups: dict[str, list[PrecisionCard]] = {}
    for card in cards:
        groups.setdefault(card.trade_date, []).append(card)
    rows: list[dict[str, Any]] = []
    for trade_date, group in groups.items():
        contribution_sum = sum(
            card.stance_score
            * weights.get(card.analyst_id, 1.0)
            * card.conviction_weight
            for card in group
        )
        rows.append({"trade_date": trade_date, "theme_strength": _round(contribution_sum)})
    return sorted(rows, key=lambda row: row["trade_date"])


def _trend(daily_rows: list[dict[str, Any]], *, snapshot_hash: str, iterations: int = 128) -> dict[str, Any]:
    if len(daily_rows) < 2:
        return {
            "direction": "flat",
            "delta": 0.0,
            "confidence_interval": [0.0, 0.0],
            "iterations": 0,
            "daily_theme_strength": daily_rows,
        }
    values = [float(row["theme_strength"]) for row in daily_rows]
    midpoint = len(values) // 2
    first = values[:midpoint]
    second = values[midpoint:]
    delta = (sum(second) / len(second)) - (sum(first) / len(first))
    seed = int(normalize_content_hash(snapshot_hash)[:16], 16)
    rng = random.Random(seed)
    bootstrap_deltas: list[float] = []
    for _ in range(iterations):
        sample_first = [first[rng.randrange(len(first))] for _ in first]
        sample_second = [second[rng.randrange(len(second))] for _ in second]
        sample_delta = (sum(sample_second) / len(sample_second)) - (
            sum(sample_first) / len(sample_first)
        )
        bootstrap_deltas.append(sample_delta)
    bootstrap_deltas.sort()
    lower_index = max(0, int(iterations * 0.025) - 1)
    upper_index = min(iterations - 1, int(iterations * 0.975))
    lower = bootstrap_deltas[lower_index]
    upper = bootstrap_deltas[upper_index]
    if lower > 0:
        direction = "up"
    elif upper < 0:
        direction = "down"
    else:
        direction = "flat"
    return {
        "direction": direction,
        "delta": _round(delta),
        "confidence_interval": [_round(lower), _round(upper)],
        "iterations": iterations,
        "daily_theme_strength": daily_rows,
    }


def _extraction_actor(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = _required_object(payload, field_name)
    _required_str(value, "model")
    _required_str(value, "version")
    _required_str(value, "run_id")
    return value


def _checker_actor(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = _extraction_actor(payload, field_name)
    if value.get("result") != "passed":
        raise PrecisionCardError("checker.result 必须是 passed")
    extractor = _required_object(payload, "extractor")
    if extractor.get("run_id") == value.get("run_id"):
        raise PrecisionCardError("checker 必须使用独立于 extractor 的 run_id")
    return value


def _parse_quote_span(value: Any, source: AnalystMessage) -> QuoteSpan:
    if source.content is None:
        raise PrecisionCardError("object_ref 语料不能校验 quote_span,需要内联 content")
    if not isinstance(value, dict):
        raise PrecisionCardError("quote_span 必须是 JSON object")
    start = value.get("start")
    end = value.get("end")
    text = value.get("text")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str):
        raise PrecisionCardError("quote_span.start/end/text 类型不合法")
    if start < 0 or end <= start or end > len(source.content):
        raise PrecisionCardError("quote_span 区间超出正文范围")
    if source.content[start:end] != text:
        raise PrecisionCardError("quote_span.text 与正文切片不匹配")
    return QuoteSpan(start=start, end=end, text=text)


def _required_object(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, dict):
        raise PrecisionCardError(f"{field_name} 必须是 JSON object")
    return value


def _required_str(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise PrecisionCardError(f"{field_name} 必须是非空字符串")
    return value


def _required_bool(payload: dict[str, Any], field_name: str) -> bool:
    value = payload.get(field_name)
    if not isinstance(value, bool):
        raise PrecisionCardError(f"{field_name} 必须是 bool")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


def _required_date(payload: dict[str, Any], field_name: str) -> str:
    value = _required_str(payload, field_name)
    _parse_date(value, field_name)
    return value


def _optional_date(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PrecisionCardError(f"{field_name} 必须是非空日期字符串")
    _parse_date(value, field_name)
    return value


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PrecisionCardError(f"{field_name} 必须是 YYYY-MM-DD") from exc


def _stance_label(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value not in STANCE_LABELS:
        raise PrecisionCardError("stance_label 必须是 -2..2 的整数")
    return value


def _enum(value: Any, allowed: Any, field_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise PrecisionCardError(f"{field_name} 必须是枚举值: {allowed_text}")
    return value


def _round(value: float) -> float:
    return round(value, 6)
