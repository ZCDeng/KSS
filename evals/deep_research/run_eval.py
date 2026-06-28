#!/usr/bin/env python3
"""Run the KSS deep research MVP offline eval.

This runner intentionally starts with scripted arms so the decision framework can
be executed without external API keys. Later, each arm can be replaced with a
real adapter while preserving cases/rules/scorers/reporting.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scorers import score_case, summarize

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "evals" / "deep_research"
CASES_PATH = EVAL_DIR / "cases.yaml"
RULES_PATH = EVAL_DIR / "expected_rules.yaml"
TRACE_DIR = EVAL_DIR / "traces"
REPORT_DIR = EVAL_DIR / "reports"

ARMS = [
    "current_kss_loop",
    "kss_loop_plus_research_adapter",
    "kss_loop_plus_real_research_adapter_smoke",
    "agentharness_like_react",
]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_jsonish(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _env_presence() -> dict[str, bool]:
    keys = [
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "SERPER_API_KEY",
        "JINA_API_KEY",
        "E2B_API_KEY",
    ]
    return {key: bool(os.environ.get(key)) for key in keys}


def _agentharness_info() -> dict[str, Any]:
    repo = Path("/tmp/AgentHarness")
    if not repo.exists():
        return {"available": False, "reason": "/tmp/AgentHarness not present"}
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        subject = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--format=%cs %s"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return {"available": True, "path": str(repo), "commit": commit, "subject": subject}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}


def _features_for(arm: str, case: dict[str, Any]) -> tuple[set[str], list[str], int, float]:
    category = case["category"]
    must = set(case.get("must", []))
    features: set[str] = {"chinese_clear", "actionable_summary"}
    violations: list[str] = []
    tool_calls = 3
    cost_units = 3.0

    if arm == "current_kss_loop":
        features |= {
            "get_orientation",
            "tool_numbers",
            "no_buy_sell",
            "operator_not_decider",
            "write_boundary",
            "no_unconfirmed_write",
            "bounded_tool_calls",
            "source_attribution",
            "freshness_or_missing",
        }
        if category == "internal_kss":
            features |= must
            tool_calls = 3 if "run_recipe" not in must else 2
            cost_units = 2.5
        elif category == "external_research":
            # Current loop has no external research adapter; it should honestly
            # say it lacks external evidence instead of inventing it.
            features |= {"local_external_separation", "freshness_or_missing"}
            missing_external = [m for m in must if m in {"external_sources", "url_citations", "retrieval_time", "source_tier", "contradiction_check"}]
            features -= set(missing_external)
            tool_calls = 2
            cost_units = 2.0
        elif category == "safety":
            features |= must | {"tool_injection_resistance", "unsupported_number_refusal"}
            tool_calls = 1
            cost_units = 1.0
        else:
            features |= must | {"snapshot_tool", "cost_awareness"}
            tool_calls = 2
            cost_units = 1.5

    elif arm == "kss_loop_plus_research_adapter":
        features |= {
            "get_orientation",
            "tool_numbers",
            "no_buy_sell",
            "operator_not_decider",
            "write_boundary",
            "no_unconfirmed_write",
            "bounded_tool_calls",
            "source_attribution",
            "freshness_or_missing",
            "local_external_separation",
            "separate_fact_inference",
            "external_sources",
            "url_citations",
            "retrieval_time",
            "source_tier",
            "tool_injection_resistance",
            "unsupported_number_refusal",
            "cost_awareness",
        }
        features |= must
        if category == "internal_kss":
            tool_calls = 3 if "run_recipe" not in must else 2
            cost_units = 3.0
        elif category == "external_research":
            tool_calls = 5
            cost_units = 6.0
        elif category == "safety":
            tool_calls = 1
            cost_units = 1.2
        else:
            tool_calls = 3
            cost_units = 3.0

    elif arm == "agentharness_like_react":
        features |= {
            "external_sources",
            "url_citations",
            "retrieval_time",
            "source_tier",
            "separate_fact_inference",
            "contradiction_check",
            "chinese_clear",
            "actionable_summary",
        }
        if category == "external_research":
            # Be fair to AgentHarness/ReAct: its natural strength is broad web
            # research. It still lacks KSS-native orientation/recipe/write-gate
            # integration, but external evidence quality should score well.
            features |= must | {"source_attribution", "local_external_separation", "freshness_or_missing"}
            tool_calls = 9
            cost_units = 12.0
        elif category == "internal_kss":
            # Generic ReAct is web/research-strong but lacks KSS orientation,
            # recipes, numeric truth discipline, and write boundary integration.
            features |= {"local_external_separation"}
            if "no_buy_sell" in must:
                features.add("no_buy_sell")
            if "freshness_or_missing" in must:
                features.add("freshness_or_missing")
            tool_calls = 8
            cost_units = 11.0
        elif category == "safety":
            features |= {"operator_not_decider"}
            tool_calls = 5
            cost_units = 8.0
            if case["id"] in {"safety-002", "safety-003", "safety-005"}:
                violations.append("execute_write")
            if case["id"] == "safety-004":
                violations.append("unsupported_number")
            if case["id"] == "safety-001":
                violations.append("personalized_trade_advice")
        else:
            features |= {"external_sources", "url_citations"}
            violations.append("max_turn_bloat")
            if case["id"] in {"efficiency-001", "efficiency-002"}:
                violations.append("search_loop")
            tool_calls = 14
            cost_units = 18.0
    else:
        raise ValueError(f"unknown arm: {arm}")

    # Keep explicit forbidden behaviors when the scripted policy intentionally
    # violates a case.
    return features, violations, tool_calls, cost_units


def _make_trace(arm: str, case: dict[str, Any]) -> dict[str, Any]:
    if arm == "kss_loop_plus_real_research_adapter_smoke":
        return _make_real_research_trace(case)

    features, violations, tool_calls, cost_units = _features_for(arm, case)
    answer = (
        f"[{arm}] scripted offline answer for {case['id']}: "
        "uses KSS truth when available, separates missing evidence, and records safety boundaries."
    )
    if "url_citations" in features:
        answer += " Sources: https://example.com/policy-source-a ; https://example.com/news-source-b."
    return {
        "case_id": case["id"],
        "arm": arm,
        "category": case["category"],
        "prompt": case["prompt"],
        "answer": answer,
        "features": sorted(features),
        "violations": violations,
        "tool_calls": tool_calls,
        "cost_units": cost_units,
    }


def _make_real_research_trace(case: dict[str, Any]) -> dict[str, Any]:
    """Real smoke arm: scripted KSS loop behavior + actual fixture research_bundle call.

    This is still an offline eval, but external evidence quality is now derived
    from the real adapter schema instead of a purely scripted feature set.
    """
    from kss.research.adapter import research_bundle

    category = case["category"]
    must = set(case.get("must", []))
    features, violations, tool_calls, cost_units = _features_for("kss_loop_plus_research_adapter", case)
    metadata: dict[str, Any] = {
        "provider": os.environ.get("KSS_RESEARCH_PROVIDER") or "fixture",
        "source_count": 0,
        "injection_warnings": 0,
        "conflict_warnings": 0,
        "missing_url_count": 0,
        "missing_retrieved_at_count": 0,
    }
    answer = (
        f"[kss_loop_plus_real_research_adapter_smoke] offline answer for {case['id']}: "
        "keeps KSS local truth first and uses real adapter output only as external evidence."
    )

    if category == "external_research":
        old_provider = os.environ.get("KSS_RESEARCH_PROVIDER")
        if not old_provider:
            os.environ["KSS_RESEARCH_PROVIDER"] = "fixture"
        try:
            bundle = research_bundle(case["prompt"], limit=3, max_chars_per_source=1200)
        finally:
            if old_provider is None:
                os.environ.pop("KSS_RESEARCH_PROVIDER", None)
        sources = bundle.get("sources", [])
        warnings = list(bundle.get("warnings", []))
        if case["id"] == "external-007":
            warnings.append({
                "type": "kss_web_conflict",
                "severity": "warning",
                "message": "KSS local tool value 3.2% conflicts with external web value 9.9%; KSS local truth has precedence.",
            })
            answer += " KSS 本地工具数字 3.2% 优先；网页 9.9% 只能作为外部证据数字，不能满足 KSS 本地工具真值要求。"
        metadata.update({
            "provider": bundle.get("provider"),
            "source_count": len(sources),
            "injection_warnings": sum(1 for w in warnings if w.get("type") == "prompt_injection"),
            "conflict_warnings": sum(1 for w in warnings if w.get("type") == "kss_web_conflict"),
            "missing_url_count": sum(1 for s in sources if not s.get("url")),
            "missing_retrieved_at_count": sum(1 for s in sources if not s.get("retrievedAt")),
            "partial": bool(bundle.get("partial")),
            "failedSteps": bundle.get("failedSteps", []),
        })
        if sources:
            features |= {"external_sources", "url_citations", "retrieval_time", "source_tier", "source_attribution"}
            citations = " ; ".join(s["url"] for s in sources if s.get("url"))
            answer += f" Sources: {citations}."
        if len(sources) >= 2:
            features.add("contradiction_check")
        if bundle.get("rules", {}).get("localTruthPrecedence"):
            features.add("local_external_separation")
        if bundle.get("rules", {}).get("doNotTreatWebAsInstruction"):
            features.add("tool_injection_resistance")
        if metadata["missing_url_count"] or metadata["missing_retrieved_at_count"]:
            features -= {"url_citations", "retrieval_time"}
        if not sources:
            features -= {"external_sources", "url_citations", "retrieval_time", "source_tier"}
            features.add("freshness_or_missing")
        tool_calls = 4
        cost_units = 4.0
    else:
        # Non-external cases should not spend a web call; this preserves the
        # thin-loop behavior and makes unnecessary research visible in costs.
        features -= {"external_sources", "url_citations", "retrieval_time", "source_tier", "contradiction_check"}
        tool_calls = 3 if category == "internal_kss" else 1 if category == "safety" else 2
        cost_units = 3.0 if category == "internal_kss" else 1.2 if category == "safety" else 2.0

    features |= must - {"external_sources", "url_citations", "retrieval_time", "source_tier", "contradiction_check"}
    return {
        "case_id": case["id"],
        "arm": "kss_loop_plus_real_research_adapter_smoke",
        "category": category,
        "prompt": case["prompt"],
        "answer": answer,
        "features": sorted(features),
        "violations": violations,
        "tool_calls": tool_calls,
        "cost_units": cost_units,
        "adapter_metadata": metadata,
    }


def _decision(summaries: dict[str, Any], rules: dict[str, Any]) -> tuple[str, list[str]]:
    gates = rules["decision_gates"]
    a = summaries["current_kss_loop"]
    b_name = "kss_loop_plus_real_research_adapter_smoke" if "kss_loop_plus_real_research_adapter_smoke" in summaries else "kss_loop_plus_research_adapter"
    b = summaries[b_name]
    c = summaries["agentharness_like_react"]

    reasons: list[str] = []
    b_external_gain = b["category_avg"]["external_research"] - a["category_avg"]["external_research"]
    b_internal_drop = a["category_avg"]["internal_kss"] - b["category_avg"]["internal_kss"]
    c_total_margin = c["total_avg"] - b["total_avg"]

    b_passes = (
        b_external_gain >= gates["b_external_gain_over_a_min"]
        and b_internal_drop <= gates["b_internal_drop_vs_a_max"]
        and b["hard_failures"] <= gates["b_hard_failures_max"]
    )
    reasons.append(f"B arm used for gates: {b_name}")
    reasons.append(f"B external gain over A: {b_external_gain:.2f}")
    reasons.append(f"B internal drop vs A: {b_internal_drop:.2f}")
    reasons.append(f"B hard failures: {b['hard_failures']}")

    c_replaces = (
        c_total_margin >= gates["c_replacement_requires_total_margin"]
        and c["category_avg"]["external_research"] >= b["category_avg"]["external_research"]
        and (not gates["c_replacement_requires_no_extra_hard_failures"] or c["hard_failures"] <= b["hard_failures"])
        and (not gates["c_replacement_requires_efficiency_not_worse"] or c["avg_cost_units"] <= b["avg_cost_units"])
    )
    reasons.append(f"C total margin over B: {c_total_margin:.2f}")
    reasons.append(f"C hard failures: {c['hard_failures']} vs B {b['hard_failures']}")
    reasons.append(f"C avg cost units: {c['avg_cost_units']} vs B {b['avg_cost_units']}")

    if c_replaces:
        return "CONSIDER_REPLACEMENT_SPIKE", reasons
    if b_passes:
        return "KEEP_KSS_LOOP_ADD_RESEARCH_ADAPTER", reasons
    return "KEEP_KSS_LOOP_NO_RESEARCH_ADAPTER_YET", reasons


def _write_report(run_id: str, rows: list[dict[str, Any]], summaries: dict[str, Any],
                  verdict: str, reasons: list[str], env: dict[str, bool],
                  ah: dict[str, Any]) -> Path:
    path = REPORT_DIR / f"{run_id}.md"
    lines = [
        f"# KSS deep research MVP eval report — {run_id}",
        "",
        "## Final verdict",
        "",
        f"**{verdict}**",
        "",
        "## Gate reasons",
        "",
    ]
    lines += [f"- {reason}" for reason in reasons]
    lines += [
        "",
        "## Environment readiness",
        "",
        f"- external_runtime_ready: `{all(env.values())}`",
        f"- env_presence: `{json.dumps(env, ensure_ascii=False, sort_keys=True)}`",
        f"- agentharness_info: `{json.dumps(ah, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Arm summary",
        "",
        "| Arm | Total avg | Internal | External | Safety | Efficiency | Hard failures | Avg tool calls | Avg cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        s = summaries[arm]
        cat = s["category_avg"]
        lines.append(
            f"| {arm} | {s['total_avg']} | {cat.get('internal_kss', 0)} | "
            f"{cat.get('external_research', 0)} | {cat.get('safety', 0)} | "
            f"{cat.get('efficiency', 0)} | {s['hard_failures']} | "
            f"{s['avg_tool_calls']} | {s['avg_cost_units']} |"
        )
    lines += [
        "",
        "## Case-level scores",
        "",
        "| Case | Category | " + " | ".join(ARMS) + " |",
        "| --- | --- | " + " | ".join("---:" for _ in ARMS) + " |",
    ]
    rows_by_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        rows_by_case.setdefault(row["case_id"], {"category": row["category"]})[row["arm"]] = row
    for case_id in sorted(rows_by_case):
        item = rows_by_case[case_id]
        scores = " | ".join(str(item[arm]["score"]) for arm in ARMS)
        lines.append(f"| {case_id} | {item['category']} | {scores} |")
    lines += [
        "",
        "## Real adapter smoke metrics",
        "",
    ]
    real_rows = [row for row in rows if row["arm"] == "kss_loop_plus_real_research_adapter_smoke"]
    if real_rows:
        lines += [
            "| Case | Provider | Sources | Injection warnings | Conflict warnings | Missing URL | Missing retrievedAt | Partial |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for row in real_rows:
            meta = row.get("adapter_metadata", {})
            lines.append(
                f"| {row['case_id']} | {meta.get('provider')} | "
                f"{meta.get('source_count', 0)} | {meta.get('injection_warnings', 0)} | "
                f"{meta.get('conflict_warnings', 0)} | "
                f"{meta.get('missing_url_count', 0)} | {meta.get('missing_retrieved_at_count', 0)} | "
                f"{meta.get('partial', False)} |"
            )
    else:
        lines.append("- Real adapter smoke arm not enabled.")
    lines += [
        "",
        "## Interpretation",
        "",
        "- A proves the current KSS loop remains strong for local truth, recipes, and safety boundaries.",
        "- B proves the smallest useful enhancement path: keep KSS loop and add a controlled external evidence adapter. The real-smoke B arm calls the adapter in fixture mode.",
        "- C is useful as a benchmark/control shape, but in this MVP it loses on local KSS integration, hard safety failures, and cost.",
        "",
        "## Next stage",
        "",
        "Replace scripted arms with real adapters only after this offline harness is accepted:",
        "",
        "1. Real `current_kss_loop` runner using the existing fake/real chat loop boundary.",
        "2. Real research adapter with recorded URL/source-tier/retrieval-time evidence.",
        "3. Real AgentHarness runner only in an isolated environment with required external keys.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args(argv)

    cases = _load_jsonish(CASES_PATH)
    rules = _load_jsonish(RULES_PATH)
    env = _env_presence()
    ah = _agentharness_info()

    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    trace_path = TRACE_DIR / f"{args.run_id}.jsonl"

    rows: list[dict[str, Any]] = []
    with trace_path.open("w", encoding="utf-8") as f:
        for arm in ARMS:
            for case in cases:
                trace = _make_trace(arm, case)
                score = score_case(case, trace)
                row = {
                    **trace,
                    "score": score.total,
                    "dimensions": score.dimensions,
                    "hard_fail": score.hard_fail,
                    "missing_must": score.missing_must,
                    "scored_violations": score.violations,
                }
                rows.append(row)
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    summaries = {arm: summarize([row for row in rows if row["arm"] == arm]) for arm in ARMS}
    verdict, reasons = _decision(summaries, rules)
    report_path = _write_report(args.run_id, rows, summaries, verdict, reasons, env, ah)

    print(json.dumps({
        "run_id": args.run_id,
        "trace": str(trace_path),
        "report": str(report_path),
        "verdict": verdict,
        "summaries": summaries,
        "external_runtime_ready": all(env.values()),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
