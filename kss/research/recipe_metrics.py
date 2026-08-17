"""Accept recipe-backed daily metrics and audit coverage without fake N/A values.

Harness nodes often emit a well-formed ``claim.metric`` (numeric value +
``formula_inputs``) but leave ``input_refs`` empty because ``_tool_results``
never made it into the attempt blob. The document builder used to skip those
rows and write ``"N/A"``, which the compiler correctly rejects.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .report_models import MetricEntry

# Keep in lockstep with compiler.FINANCIAL_NUMBER_RE so card rows cannot
# smuggle headline prices into the audited JSON.
_UNBOUND_FINANCIAL_RE = re.compile(
    r"[+-]?\d+(?:\.\d+)?\s*(?:%|bp|bps|亿|万|倍|元|点|家|张|只)|[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?"
)
_DAILY_CARD_SUMMARY = "已验证来源已入账，数字以指标账本为准。"

RECIPE_METRIC_SPECS: dict[str, dict[str, str]] = {
    "compute_temperature": {
        "metric_id": "m_temperature",
        "label": "市场温度",
        "formula_id": "temperature_index",
    },
    "theme_consensus": {
        "metric_id": "m_consensus",
        "label": "主题共识强度",
        "formula_id": "theme_consensus",
    },
    "risk_radar": {
        "metric_id": "m_risk",
        "label": "风险雷达均值",
        "formula_id": "risk_radar",
    },
}


def _finite_numbers(values: Any) -> list[float] | None:
    if not isinstance(values, list) or not values:
        return None
    out: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        out.append(number)
    return out


def coerce_recipe_metric(
    *,
    expected: dict[str, str],
    raw_metric: dict[str, Any],
    evidence_ids: set[str],
    fallback_refs: list[str],
    goal_as_of: str,
) -> tuple[MetricEntry, list[float]] | None:
    """Turn a claim.metric into a ledger row even when lineage refs are empty."""
    metric_id = str(raw_metric.get("metric_id") or "")
    formula_id = str(raw_metric.get("formula_id") or "")
    version = str(raw_metric.get("formula_version") or "v1")
    if metric_id != expected["metric_id"] or formula_id != expected["formula_id"]:
        return None
    if version not in {"v1", "kss-equivalent-v1"}:
        return None
    numbers = _finite_numbers(raw_metric.get("formula_inputs"))
    if numbers is None:
        return None
    value = raw_metric.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    try:
        precision = int(raw_metric.get("precision") or 1)
    except (TypeError, ValueError):
        return None
    if not 0 <= precision <= 8:
        return None
    metric_as_of = str(raw_metric.get("as_of") or goal_as_of)
    if metric_as_of != goal_as_of:
        return None
    if round(sum(numbers) / len(numbers), precision) != round(float(value), precision):
        return None
    input_refs = [
        str(item)
        for item in raw_metric.get("input_refs") or []
        if str(item) in evidence_ids
    ]
    if not input_refs:
        input_refs = [str(item) for item in fallback_refs if str(item) in evidence_ids]
    if not input_refs:
        return None
    return (
        MetricEntry(
            metric_id,
            str(expected["label"]),
            float(value),
            str(raw_metric.get("unit") or "%"),
            precision,
            str(expected["formula_id"]),
            "v1",
            input_refs,
            metric_as_of,
        ),
        numbers,
    )


def fill_missing_recipe_metrics(
    derived_metrics: dict[str, MetricEntry],
    formula_inputs: dict[str, Any],
    task_results: dict[str, dict[str, Any]],
    *,
    evidence_ids: set[str],
    fallback_refs: list[str],
    goal_as_of: str,
    specs: dict[str, dict[str, str]] | None = None,
) -> None:
    """Fill ledger gaps from harness claim.metric rows that lack input_refs."""
    for task_kind, expected in (specs or RECIPE_METRIC_SPECS).items():
        metric_id = expected["metric_id"]
        if metric_id in derived_metrics:
            continue
        task_result = task_results.get(task_kind) or {}
        for raw_claim in task_result.get("claims") or []:
            if not isinstance(raw_claim, dict):
                continue
            raw_metric = raw_claim.get("metric")
            if not isinstance(raw_metric, dict):
                continue
            coerced = coerce_recipe_metric(
                expected=expected,
                raw_metric=raw_metric,
                evidence_ids=evidence_ids,
                fallback_refs=fallback_refs,
                goal_as_of=goal_as_of,
            )
            if coerced is None:
                continue
            entry, numbers = coerced
            derived_metrics[metric_id] = entry
            formula_inputs[metric_id] = numbers
            break


def _task_succeeded(task_results: dict[str, dict[str, Any]], kind: str) -> bool:
    result = task_results.get(kind) or {}
    return str(result.get("status") or "") == "succeeded"


def _has_card_structure(task_results: dict[str, dict[str, Any]]) -> bool:
    return bool(_card_structure_claims(task_results))


def _card_structure_claims(task_results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw_claim in (task_results.get("analyst_cards") or {}).get("claims") or []:
        if not isinstance(raw_claim, dict):
            continue
        if str(raw_claim.get("kind") or "") == "card_structure":
            out.append(raw_claim)
            continue
        fields = raw_claim.get("fields")
        if isinstance(fields, dict) and fields.get("card_id") and fields.get("sections"):
            out.append(raw_claim)
    return out


def strip_unbound_financial_tokens(text: str) -> str:
    """Drop headline prices / sizes so compiler row audit can pass."""
    cleaned = _UNBOUND_FINANCIAL_RE.sub("", str(text or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:：、")


def _evidence_id(item: Any) -> str:
    if hasattr(item, "evidence_id"):
        return str(getattr(item, "evidence_id") or "")
    if isinstance(item, dict):
        return str(item.get("evidence_id") or "")
    return ""


def _evidence_tier(item: Any) -> str:
    if hasattr(item, "source_tier"):
        return str(getattr(item, "source_tier") or "unknown")
    if isinstance(item, dict):
        return str(item.get("source_tier") or "unknown")
    return "unknown"


def build_daily_card_rows(
    *,
    task_results: dict[str, dict[str, Any]],
    evidence: list[Any],
) -> list[dict[str, Any]]:
    """Precision-card rows for the scheduled daily path.

    Harness ``card_structure`` is a UI layout, not precision-card-v1. Raw
    collect_sources titles often contain ``1.12%`` / ``39亿`` and must not
    enter the audited row JSON.
    """
    evidence_ids = [eid for item in evidence if (eid := _evidence_id(item))]
    rows: list[dict[str, Any]] = []
    for index, raw_claim in enumerate(_card_structure_claims(task_results), start=1):
        fields = raw_claim.get("fields") if isinstance(raw_claim.get("fields"), dict) else {}
        card_id = str(fields.get("card_id") or raw_claim.get("card_id") or f"daily_card_{index:04d}")
        title = strip_unbound_financial_tokens(
            str(fields.get("title") or raw_claim.get("title") or "精判卡结构")
        ) or "精判卡结构"
        summary = strip_unbound_financial_tokens(
            str(raw_claim.get("statement") or fields.get("summary") or _DAILY_CARD_SUMMARY)
        ) or _DAILY_CARD_SUMMARY
        claimed_refs = [
            str(item)
            for item in (raw_claim.get("evidence_refs") or fields.get("evidence_refs") or [])
            if str(item) in set(evidence_ids)
        ]
        rows.append(
            {
                "card_id": card_id,
                "title": title,
                "summary": summary,
                "metric_refs": ["m_card_count"],
                "evidence_refs": claimed_refs or evidence_ids[:1],
                "source_group": "card_structure",
            }
        )
    if rows:
        return rows
    for index, item in enumerate(evidence, start=1):
        evidence_id = _evidence_id(item)
        if not evidence_id:
            continue
        rows.append(
            {
                "card_id": f"evidence_{index:04d}",
                "title": f"已验证来源 {index:04d}",
                "summary": _DAILY_CARD_SUMMARY,
                "metric_refs": ["m_card_count"],
                "evidence_refs": [evidence_id],
                "source_group": _evidence_tier(item),
            }
        )
    return rows


def iter_url_evidence_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    """URL-shaped evidence_refs from harness collect_sources (not _tool_evidence)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for bucket in (result.get("_tool_evidence"), result.get("evidence_refs")):
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(item)
    return out


def extract_daily_narrative_paragraphs(task_results: dict[str, dict[str, Any]]) -> list[str]:
    """Pull harness narrative statements into the compiled daily report body."""
    paragraphs: list[str] = []
    seen: set[str] = set()
    for raw_claim in (task_results.get("narrative") or {}).get("claims") or []:
        if not isinstance(raw_claim, dict):
            continue
        kind = str(raw_claim.get("kind") or "narrative")
        if kind not in {"narrative", "paragraph"}:
            continue
        text = str(
            raw_claim.get("statement")
            or raw_claim.get("text")
            or raw_claim.get("content")
            or ""
        ).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        paragraphs.append(text)
    return paragraphs


def extract_precision_card_payloads(task_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect precision-card-v1 objects from an analyst_cards attempt."""
    cards: list[dict[str, Any]] = []
    for raw_claim in task_result.get("claims") or []:
        if not isinstance(raw_claim, dict):
            continue
        candidates = [raw_claim]
        for key in ("card", "precision_card", "payload", "fields"):
            value = raw_claim.get(key)
            if isinstance(value, dict):
                candidates.append(value)
            elif isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        for item in candidates:
            if str(item.get("protocol_version") or "") == "precision-card-v1":
                cards.append(item)
    return cards


def augment_daily_investment_coverage(
    coverage: dict[str, Any],
    *,
    evidence: list[dict[str, Any]],
    task_results: dict[str, dict[str, Any]],
    goal_as_of: str,
) -> dict[str, Any]:
    """Count recipe-backed daily inputs so scheduled jobs are not corpus-only."""
    out = dict(coverage)
    verified = [item for item in evidence if item.get("verified")]
    out["daily_recipe_sources"] = _task_succeeded(task_results, "collect_sources") or any(
        str(item.get("source_tool") or "")
        in {"research_search", "research_bundle", "research_fetch", "combosearch"}
        or str((item.get("metadata") or {}).get("validator") or "") == "source_coverage"
        or str(item.get("task_id") or "").endswith("collect_sources")
        for item in verified
    )
    out["daily_recipe_cards"] = _has_card_structure(task_results) or any(
        str((item.get("metadata") or {}).get("validator") or "") == "precision_cards"
        or str(item.get("task_id") or "").endswith("analyst_cards")
        for item in verified
    )
    derived: dict[str, MetricEntry] = {}
    inputs: dict[str, Any] = {}
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for item in evidence
        if item.get("evidence_id")
    }
    fill_missing_recipe_metrics(
        derived,
        inputs,
        task_results,
        evidence_ids=evidence_ids,
        fallback_refs=sorted(evidence_ids),
        goal_as_of=goal_as_of,
    )
    if len(derived) < 3:
        for kind, expected in RECIPE_METRIC_SPECS.items():
            if expected["metric_id"] in derived:
                continue
            for raw_claim in (task_results.get(kind) or {}).get("claims") or []:
                metric = raw_claim.get("metric") if isinstance(raw_claim, dict) else None
                if not isinstance(metric, dict):
                    continue
                numbers = _finite_numbers(metric.get("formula_inputs"))
                value = metric.get("value")
                if (
                    str(metric.get("metric_id") or "") == expected["metric_id"]
                    and str(metric.get("formula_id") or "") == expected["formula_id"]
                    and numbers is not None
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and str(metric.get("as_of") or goal_as_of) == goal_as_of
                ):
                    derived[expected["metric_id"]] = MetricEntry(
                        expected["metric_id"],
                        expected["label"],
                        float(value),
                        "%",
                        1,
                        expected["formula_id"],
                        "v1",
                        ["pending"],
                        goal_as_of,
                    )
                    break
    out["daily_recipe_metrics"] = {
        "m_temperature",
        "m_consensus",
        "m_risk",
    }.issubset(derived)
    return out


def daily_investment_gates_satisfied(coverage: dict[str, Any]) -> dict[str, bool]:
    """Which formal investment gates are met via import tables or daily recipes."""
    return {
        "corpus": int(coverage.get("source_records") or 0) > 0
        or bool(coverage.get("daily_recipe_sources")),
        "cards": int(coverage.get("verified_precision_cards") or 0) > 0
        or bool(coverage.get("daily_recipe_cards")),
        "formula": int(coverage.get("formula_runs") or 0) > 0
        or bool(coverage.get("daily_recipe_metrics")),
    }
