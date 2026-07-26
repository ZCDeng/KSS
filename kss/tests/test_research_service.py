from __future__ import annotations

import json
from pathlib import Path

from kss.storage.db import connect, ensure_schema
from kss.research.profiles import list_profiles
from kss.research.service import ResearchService


def test_packaged_profiles_are_complete(project_root: Path | None = None):
    profiles = {p["profile_id"]: p for p in list_profiles(Path(__file__).resolve().parents[2])}
    weekly = profiles["investment-weekly-v3"]

    assert weekly["inputs_schema"]["required"] == ["date_range", "as_of"]
    assert len(weekly["criteria"]) >= 7
    assert len(weekly["task_graph"]) == 12
    assert weekly["audit_rules"]["all_financial_numbers_require_metric_refs"] is True
    assert weekly["tool_whitelist"]
    assert weekly["sections"][1]["anchor"] == "temperature"


def test_research_service_runs_goal_to_completed_and_publishes(tmp_path):
    service = ResearchService(state_root=tmp_path, project_root=Path(__file__).resolve().parents[2], allow_synthetic_fixture=True)
    created = service.create_goal(payload={
        "client_request_id": "req-1",
        "profile_id": "investment-weekly-v3",
        "objective": "测试周报",
        "inputs": {"date_range": "2026-07-13_to_2026-07-17", "as_of": "2026-07-17"},
    })

    goal_id = created["goal_id"]
    started = service.start_goal(goal_id=goal_id)
    opened = service.open_goal(goal_id=goal_id)
    artifacts = service.list_artifacts(goal_id=goal_id)
    events = service.list_events(goal_id=goal_id, after_sequence=0)

    assert started["ok"] is True
    assert opened["detail"]["status"] == "completed"
    assert all(t["status"] == "succeeded" for t in opened["detail"]["tasks"])
    assert any(a["kind"] == "report_html" for a in artifacts["artifacts"])
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
    assert all(event["event_id"] and event["timestamp"] for event in events)

    destination = tmp_path / "published" / "weekly.html"
    published = service.publish_artifact(goal_id=goal_id, payload={"destination": str(destination), "overwrite": True})

    assert published["ok"] is True
    assert destination.exists()
    assert published["sha256"]
    assert destination.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_publish_blocks_when_audit_has_not_passed(tmp_path):
    service = ResearchService(state_root=tmp_path, project_root=Path(__file__).resolve().parents[2])
    created = service.create_goal(payload={"client_request_id": "req-2", "objective": "未运行"})
    blocked = service.publish_artifact(goal_id=created["goal_id"], payload={"destination": str(tmp_path / "x.html")})

    assert blocked["ok"] is False
    assert blocked["error"] in {"artifact_not_found", "audit_not_passed"}


def test_create_goal_is_idempotent_by_client_request_id(tmp_path):
    service = ResearchService(state_root=tmp_path, project_root=Path(__file__).resolve().parents[2])
    a = service.create_goal(payload={"client_request_id": "same", "objective": "A"})
    b = service.create_goal(payload={"client_request_id": "same", "objective": "B"})

    assert a["goal_id"] == b["goal_id"]


def test_audit_rejects_verified_evidence_from_disallowed_source_tier(tmp_path):
    service = ResearchService(state_root=tmp_path, project_root=Path(__file__).resolve().parents[2], allow_synthetic_fixture=True)
    created = service.create_goal(payload={
        "client_request_id": "req-source-tier",
        "profile_id": "investment-weekly-v3",
        "objective": "来源等级门禁",
        "inputs": {"date_range": "2026-07-13_to_2026-07-17", "as_of": "2026-07-17"},
    })
    goal_id = created["goal_id"]
    assert service.start_goal(goal_id=goal_id)["ok"] is True

    goal = service.open_goal(goal_id=goal_id)["detail"]
    snapshot_criterion = next(item for item in goal["criteria"] if item["validator"] == "snapshot")
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE research_evidence SET source_tier='untrusted_model_text' WHERE goal_id=? AND criterion_id=?",
            (goal_id, snapshot_criterion["criterion_id"]),
        )

    audit = service.audit_goal(goal_id=goal_id)

    assert audit["status"] == "fail"
    assert any(item["code"] == "criterion_insufficient_evidence" and item["criterion_id"] == snapshot_criterion["criterion_id"] for item in audit["findings"])
    assert audit["coverage"]["criteria"][snapshot_criterion["criterion_id"]]["allowed_tier"] == 0
