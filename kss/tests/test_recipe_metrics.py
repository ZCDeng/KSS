"""Recipe-backed daily metrics and investment-gate coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kss.research.models import Evidence
from kss.research.recipe_metrics import (
    augment_daily_investment_coverage,
    build_daily_card_rows,
    coerce_recipe_metric,
    daily_investment_gates_satisfied,
    extract_precision_card_payloads,
    fill_missing_recipe_metrics,
    strip_unbound_financial_tokens,
)
from kss.research.report_models import MetricEntry


def test_coerce_recipe_metric_accepts_empty_input_refs() -> None:
    coerced = coerce_recipe_metric(
        expected={
            "metric_id": "m_temperature",
            "label": "市场温度",
            "formula_id": "temperature_index",
        },
        raw_metric={
            "metric_id": "m_temperature",
            "formula_id": "temperature_index",
            "formula_version": "v1",
            "formula_inputs": [0.225, 1.0],
            "value": 0.6125,
            "precision": 1,
            "as_of": "2026-08-14",
            "input_refs": [],
        },
        evidence_ids={"ev_snap"},
        fallback_refs=["ev_snap"],
        goal_as_of="2026-08-14",
    )
    assert coerced is not None
    entry, numbers = coerced
    assert entry.value == 0.6125
    assert entry.input_refs == ["ev_snap"]
    assert numbers == [0.225, 1.0]


def test_coerce_recipe_metric_rejects_mean_mismatch() -> None:
    assert (
        coerce_recipe_metric(
            expected={
                "metric_id": "m_temperature",
                "label": "市场温度",
                "formula_id": "temperature_index",
            },
            raw_metric={
                "metric_id": "m_temperature",
                "formula_id": "temperature_index",
                "formula_inputs": [0.1, 0.2],
                "value": 0.9,
                "as_of": "2026-08-14",
            },
            evidence_ids={"ev_snap"},
            fallback_refs=["ev_snap"],
            goal_as_of="2026-08-14",
        )
        is None
    )


def test_fill_missing_recipe_metrics_from_harness_claims() -> None:
    derived: dict[str, MetricEntry] = {}
    inputs: dict[str, object] = {}
    fill_missing_recipe_metrics(
        derived,
        inputs,
        {
            "compute_temperature": {
                "status": "succeeded",
                "claims": [
                    {
                        "metric": {
                            "metric_id": "m_temperature",
                            "formula_id": "temperature_index",
                            "formula_version": "v1",
                            "formula_inputs": [0.225, 1.0],
                            "value": 0.6125,
                            "as_of": "2026-08-14",
                            "input_refs": [],
                        }
                    }
                ],
            }
        },
        evidence_ids={"ev_snap"},
        fallback_refs=["ev_snap"],
        goal_as_of="2026-08-14",
    )
    assert derived["m_temperature"].value == 0.6125
    assert inputs["m_temperature"] == [0.225, 1.0]


def test_daily_gates_accept_recipe_path_without_imported_corpus() -> None:
    coverage = augment_daily_investment_coverage(
        {"source_records": 0, "verified_precision_cards": 0, "formula_runs": 0},
        evidence=[{"evidence_id": "ev_snap", "verified": True, "data_as_of": "2026-08-14"}],
        task_results={
            "collect_sources": {"status": "succeeded", "claims": []},
            "analyst_cards": {
                "status": "succeeded",
                "claims": [{"kind": "card_structure", "fields": {"card_id": "c1", "sections": []}}],
            },
            "compute_temperature": {
                "status": "succeeded",
                "claims": [
                    {
                        "metric": {
                            "metric_id": "m_temperature",
                            "formula_id": "temperature_index",
                            "formula_inputs": [0.225, 1.0],
                            "value": 0.6125,
                            "as_of": "2026-08-14",
                        }
                    }
                ],
            },
            "theme_consensus": {
                "status": "succeeded",
                "claims": [
                    {
                        "metric": {
                            "metric_id": "m_consensus",
                            "formula_id": "theme_consensus",
                            "formula_inputs": [0.225],
                            "value": 0.225,
                            "as_of": "2026-08-14",
                        }
                    }
                ],
            },
            "risk_radar": {
                "status": "succeeded",
                "claims": [
                    {
                        "metric": {
                            "metric_id": "m_risk",
                            "formula_id": "risk_radar",
                            "formula_inputs": [0.225, 1.0],
                            "value": 0.6125,
                            "as_of": "2026-08-14",
                        }
                    }
                ],
            },
        },
        goal_as_of="2026-08-14",
    )
    gates = daily_investment_gates_satisfied(coverage)
    assert gates == {"corpus": True, "cards": True, "formula": True}


def test_weekly_style_coverage_still_requires_import_tables() -> None:
    gates = daily_investment_gates_satisfied(
        {"source_records": 0, "verified_precision_cards": 0, "formula_runs": 0}
    )
    assert gates == {"corpus": False, "cards": False, "formula": False}


def test_iter_url_evidence_refs_reads_harness_evidence_refs() -> None:
    from kss.research.recipe_metrics import iter_url_evidence_refs

    rows = iter_url_evidence_refs(
        {
            "evidence_refs": [
                {
                    "id": "E1",
                    "url": "https://example.com/a",
                    "sourceTier": "reputable_secondary",
                    "title": "A",
                },
                {"id": "E2", "title": "no url"},
                {
                    "id": "E3",
                    "url": "https://example.com/a",
                    "sourceTier": "official_or_primary",
                },
            ],
            "_tool_evidence": [
                {
                    "url": "https://example.com/b",
                    "sourceTier": "official_or_primary",
                    "title": "B",
                }
            ],
        }
    )
    assert [item["url"] for item in rows] == [
        "https://example.com/b",
        "https://example.com/a",
    ]


def test_strip_unbound_financial_tokens_removes_headline_prices() -> None:
    assert "1.12%" not in strip_unbound_financial_tokens("创业板涨1.12%，CPO掀涨停潮")
    assert "39亿" not in strip_unbound_financial_tokens("募资39亿加码主线")
    assert strip_unbound_financial_tokens("精判卡结构") == "精判卡结构"


def test_build_daily_card_rows_prefers_card_structure() -> None:
    rows = build_daily_card_rows(
        task_results={
            "analyst_cards": {
                "claims": [
                    {
                        "kind": "card_structure",
                        "statement": "七区结构，含创业板涨1.12%",
                        "fields": {"card_id": "jingpan_card_v1", "sections": ["meta"]},
                    }
                ]
            }
        },
        evidence=[{"evidence_id": "ev_snap", "source_tier": "deterministic_calculation", "title": "创业板涨1.12%"}],
    )
    assert len(rows) == 1
    assert rows[0]["card_id"] == "jingpan_card_v1"
    assert rows[0]["evidence_refs"] == ["ev_snap"]
    blob = str(rows[0])
    assert "1.12%" not in blob
    assert "12%" not in blob


def test_build_daily_card_rows_fallback_omits_headline_numbers() -> None:
    rows = build_daily_card_rows(
        task_results={"analyst_cards": {"claims": []}},
        evidence=[
            {
                "evidence_id": "ev_news",
                "source_tier": "reputable_secondary",
                "title": "盘后观察:创业板涨1.12%，募资39亿",
            }
        ],
    )
    assert rows[0]["title"] == "已验证来源 0001"
    assert "1.12%" not in str(rows[0])
    assert "39亿" not in str(rows[0])


def test_extract_precision_card_payloads() -> None:
    cards = extract_precision_card_payloads(
        {
            "claims": [
                {"protocol_version": "precision-card-v1", "card_id": "card-1"},
                {"fields": {"protocol_version": "precision-card-v1", "card_id": "card-2"}},
                {"kind": "card_structure", "fields": {"card_id": "ui-only"}},
            ]
        }
    )
    assert [item["card_id"] for item in cards] == ["card-1", "card-2"]


def _insert_succeeded_task(service, goal_id: str, kind: str, result: dict) -> None:
    from kss.storage.db import connect, ensure_schema

    goal = service.repo.get_goal(goal_id) or {}
    task = next(item for item in goal["tasks"] if item["kind"] == kind)
    attempt_id = f"attempt-{kind}"
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO research_attempts (
                attempt_id, goal_id, task_id, status, attempt_no, trigger,
                result_json, usage_json, created_at, started_at, finished_at
            ) VALUES (?, ?, ?, 'succeeded', 1, 'test', ?, '{}',
                      '2026-08-14T00:00:00+00:00',
                      '2026-08-14T00:00:00+00:00',
                      '2026-08-14T00:00:01+00:00')
            """,
            (attempt_id, goal_id, task["task_id"], json.dumps(result, ensure_ascii=False)),
        )
        conn.execute(
            "UPDATE research_tasks SET status='succeeded', current_attempt_id=? WHERE task_id=?",
            (attempt_id, task["task_id"]),
        )


def test_daily_document_compiles_recipe_metrics_without_input_refs(tmp_path: Path) -> None:
    from kss.research.service import ResearchService

    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
        allow_synthetic_fixture=False,
    )
    created = service.create_goal(
        payload={
            "client_request_id": "daily-recipe-metrics",
            "profile_id": "investment-daily-v1",
            "objective": "测试日报指标口径",
            "inputs": {"trade_date": "2026-08-14", "as_of": "2026-08-14"},
        }
    )
    goal_id = created["goal_id"]
    goal = service.repo.get_goal(goal_id) or {}
    snapshot_id = goal["snapshot"]["snapshot_id"]
    evidence_id = "ev-daily-snap"
    service.repo.register_evidence(
        Evidence(
            evidence_id=evidence_id,
            goal_id=goal_id,
            source_tool="research_deterministic_runner",
            source_tier="deterministic_calculation",
            uri="kss-tool://snapshot/1",
            data_as_of="2026-08-14",
            method="snapshot",
            hash=hashlib.sha256(evidence_id.encode()).hexdigest(),
            metadata={"snapshot_id": snapshot_id},
        )
    )
    service.repo.verify_evidence(evidence_id, checker="research_deterministic_runner")
    headline_id = "ev-daily-headline"
    service.repo.register_evidence(
        Evidence(
            evidence_id=headline_id,
            goal_id=goal_id,
            source_tool="research_tool",
            source_tier="reputable_secondary",
            uri="https://example.com/headline",
            data_as_of="2026-08-14",
            method="successful_tool_result",
            hash=hashlib.sha256(headline_id.encode()).hexdigest(),
            metadata={
                "snapshot_id": snapshot_id,
                "title": "盘后观察:创业板涨1.12%，募资39亿",
            },
        )
    )
    service.repo.verify_evidence(headline_id, checker="tool_result_integrity")
    _insert_succeeded_task(
        service,
        goal_id,
        "collect_sources",
        {"status": "succeeded", "claims": [], "warnings": []},
    )
    _insert_succeeded_task(
        service,
        goal_id,
        "compute_temperature",
        {
            "status": "succeeded",
            "claims": [
                {
                    "metric": {
                        "metric_id": "m_temperature",
                        "formula_id": "temperature_index",
                        "formula_version": "v1",
                        "formula_inputs": [0.225, 1.0],
                        "value": 0.6125,
                        "precision": 1,
                        "as_of": "2026-08-14",
                        "input_refs": [],
                    }
                }
            ],
        },
    )
    _insert_succeeded_task(
        service,
        goal_id,
        "theme_consensus",
        {
            "status": "succeeded",
            "claims": [
                {
                    "metric": {
                        "metric_id": "m_consensus",
                        "formula_id": "theme_consensus",
                        "formula_version": "v1",
                        "formula_inputs": [0.225],
                        "value": 0.225,
                        "as_of": "2026-08-14",
                        "input_refs": [],
                    }
                }
            ],
        },
    )
    _insert_succeeded_task(
        service,
        goal_id,
        "risk_radar",
        {
            "status": "succeeded",
            "claims": [
                {
                    "metric": {
                        "metric_id": "m_risk",
                        "formula_id": "risk_radar",
                        "formula_version": "v1",
                        "formula_inputs": [0.225, 1.0],
                        "value": 0.6125,
                        "as_of": "2026-08-14",
                        "input_refs": [],
                    }
                }
            ],
        },
    )
    _insert_succeeded_task(
        service,
        goal_id,
        "analyst_cards",
        {
            "status": "succeeded",
            "claims": [{"kind": "card_structure", "fields": {"card_id": "c1", "sections": []}}],
        },
    )

    document = service._build_report_document(goal_id)
    compiled = service.compiler.compile(document)
    codes = {finding["code"] for finding in compiled["audit"]["findings"]}
    assert "metric_value_not_numeric" not in codes
    assert "metric_formula_inputs_missing" not in codes
    assert "row_unbound_financial_number" not in codes
    assert document.metric_ledger.by_id()["m_temperature"].value == 0.6125
    card_rows = [
        row
        for section in document.sections
        for block in section.blocks
        if block.block_id == "b_cards"
        for row in block.rows
    ]
    assert card_rows
    assert all("1.12%" not in str(row) and "39亿" not in str(row) for row in card_rows)

    audited = service.audit_goal(goal_id=goal_id)
    audit_codes = {finding["code"] for finding in audited.get("findings") or []}
    assert "missing_analyst_corpus" not in audit_codes
    assert "missing_verified_precision_cards" not in audit_codes
    assert "missing_investment_formula_run" not in audit_codes
