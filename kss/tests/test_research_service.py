from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kss.research.artifacts import ArtifactSafetyError, ArtifactStore
from kss.research.models import Claim, Evidence
from kss.research.profiles import list_profiles
from kss.research.service import ResearchService
from kss.storage.db import connect, ensure_schema


def test_packaged_profiles_are_complete(project_root: Path | None = None):
    profiles = {p["profile_id"]: p for p in list_profiles(Path(__file__).resolve().parents[2])}
    weekly = profiles["investment-weekly-v3"]

    assert weekly["inputs_schema"]["required"] == ["date_range", "as_of"]
    assert len(weekly["criteria"]) >= 7
    assert len(weekly["task_graph"]) == 12
    assert weekly["audit_rules"]["all_financial_numbers_require_metric_refs"] is True
    assert weekly["tool_whitelist"]
    assert weekly["sections"][1]["anchor"] == "temperature"


def test_research_migration_v3_is_idempotent(tmp_path):
    db_path = tmp_path / "storage" / "kss.db"
    with connect(db_path) as conn:
        first = ensure_schema(conn)
    with connect(db_path) as conn:
        second = ensure_schema(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert 3 in first
    assert second == []
    assert {
        "research_goals",
        "research_tasks",
        "research_attempts",
        "research_evidence",
        "research_artifacts",
        "research_audits",
        "research_events",
    } <= tables


def test_research_migration_v4_adds_agent_ownership(tmp_path):
    db_path = tmp_path / "storage" / "kss.db"
    with connect(db_path) as conn:
        applied = ensure_schema(conn)
        goal_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_goals)")
        }
        task_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_tasks)")
        }
        attempt_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_attempts)")
        }

    assert 4 in applied
    assert "execution_mode" in goal_columns
    assert "agent_id" in task_columns
    assert "agent_id" in attempt_columns


def test_research_migration_v5_adds_report_archive_metadata(tmp_path):
    db_path = tmp_path / "storage" / "kss.db"
    with connect(db_path) as conn:
        applied = ensure_schema(conn)
        goal_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_goals)")
        }
        indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(research_goals)")
        }

    assert 5 in applied
    assert {"origin", "cadence"} <= goal_columns
    assert "idx_research_goals_origin_cadence_created" in indexes


def test_daily_profile_freezes_one_trade_day_and_uses_daily_anchors(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(payload={
        "client_request_id": "daily-profile",
        "profile_id": "investment-daily-v1",
        "objective": "测试投资分析日报",
        "inputs": {"trade_date": "2026-07-17", "as_of": "2026-07-17"},
        "origin": "scheduled",
        "cadence": "daily",
    })

    assert created["ok"] is True
    detail = created["detail"]
    assert detail["origin"] == "scheduled"
    assert detail["cadence"] == "daily"
    assert detail["snapshot"]["inputs"]["trading_calendar"] == ["2026-07-17"]
    profile = {item["profile_id"]: item for item in list_profiles(Path(__file__).resolve().parents[2])}["investment-daily-v1"]
    assert [section["anchor"] for section in profile["sections"]] == [
        "overview", "temperature", "theme-consensus", "risk-radar",
        "precision-cards", "methodology", "audit",
    ]


def test_report_archive_listing_is_metadata_only_and_excludes_uncompiled_goals(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
        allow_synthetic_fixture=True,
    )
    uncompiled = service.create_goal(payload={
        "client_request_id": "archive-uncompiled",
        "profile_id": "investment-daily-v1",
        "objective": "还未编译的日报",
        "inputs": {"trade_date": "2026-07-17", "as_of": "2026-07-17"},
        "origin": "scheduled",
        "cadence": "daily",
    })
    compiled = service.create_goal(payload={
        "client_request_id": "archive-compiled",
        "profile_id": "investment-weekly-v3",
        "objective": "已归档的周报",
        "inputs": {"date_range": "2026-07-13_to_2026-07-17", "as_of": "2026-07-17"},
        "origin": "scheduled",
        "cadence": "weekly",
    })
    goal_id = compiled["goal_id"]
    artifact = service.artifacts.put_bytes(
        goal_id=goal_id,
        kind="report_html",
        name="report.html",
        data=b"<!doctype html><title>test</title>",
        media_type="text/html; charset=utf-8",
        metadata={"title": "投资分析周报 V3", "draft": False},
    )
    service.repo.update_goal_status(goal_id, "completed")

    response = service.list_goals(
        origin="scheduled",
        profile_ids=["investment-daily-v1", "investment-weekly-v3"],
        limit=10,
    )

    assert response["goals"] == []
    assert len(response["reports"]) == 1
    row = response["reports"][0]
    assert row["goal_id"] == goal_id
    assert row["artifact_id"] == artifact["artifact_id"]
    assert row["object_hash"] == artifact["object_hash"]
    assert row["title"] == "投资分析周报 V3"
    assert uncompiled["goal_id"] != row["goal_id"]


def test_report_archive_cursor_keeps_same_timestamp_rows(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
        allow_synthetic_fixture=True,
    )
    goal_ids = []
    for index in range(2):
        created = service.create_goal(payload={
            "client_request_id": f"cursor-{index}",
            "profile_id": "investment-daily-v1",
            "objective": f"日报 {index}",
            "inputs": {"trade_date": "2026-07-17", "as_of": "2026-07-17"},
            "origin": "scheduled",
            "cadence": "daily",
        })
        goal_id = created["goal_id"]
        goal_ids.append(goal_id)
        service.artifacts.put_bytes(
            goal_id=goal_id,
            kind="report_html",
            name="report.html",
            data=b"<!doctype html>",
            media_type="text/html; charset=utf-8",
            metadata={"draft": False},
        )

    # SQLite timestamps are intentionally collapsed to exercise the secondary
    # goal-id key used by new cursors.
    with connect(service.db_path) as conn:
        conn.execute(
            "UPDATE research_goals SET created_at='2026-07-17T23:20:00Z' WHERE goal_id IN (?, ?)",
            goal_ids,
        )
        conn.commit()

    first = service.list_goals(origin="scheduled", limit=1)
    second = service.list_goals(origin="scheduled", limit=1, cursor=first["next_cursor"])
    assert len(first["reports"]) == len(second["reports"]) == 1
    assert first["reports"][0]["goal_id"] != second["reports"][0]["goal_id"]


def test_multi_agent_pilot_binds_roles_and_caps_concurrency(tmp_path):
    class RecordingRunner:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def run(self, **_: object) -> dict[str, object]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.04)
            with self.lock:
                self.active -= 1
            return {
                "status": "succeeded",
                "claims": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "open_questions": [],
                "warnings": [],
                "usage": {"total_tokens": 10},
                "_tool_evidence": [],
                "_tool_results": [],
            }

        def abort(self, *_: object, **__: object) -> bool:
            return True

    runner = RecordingRunner()
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
        task_runner=runner,  # type: ignore[arg-type]
    )
    created = service.create_goal(
        payload={
            "client_request_id": "pilot-roles",
            "profile_id": "investment-weekly-v3",
            "execution_mode": "multi_agent_pilot",
            "objective": "多角色并发约束",
            "inputs": {
                "date_range": "2026-07-13_to_2026-07-17",
                "as_of": "2026-07-17",
            },
        }
    )

    assert created["ok"] is True
    detail = created["detail"]
    assert detail["execution_mode"] == "multi_agent_pilot"
    assert {agent["agent_id"] for agent in detail["research_agents"]} == {
        "source_collector",
        "market_structure_analyst",
        "risk_contradiction_critic",
        "report_synthesizer",
    }
    assert next(
        task
        for task in detail["tasks"]
        if task["kind"] == "compute_temperature"
    )["agent_id"] == "market_structure_analyst"

    service.start_goal(goal_id=created["goal_id"])
    assert service.wait_for_idle(created["goal_id"], timeout=10)["ok"] is True

    assert runner.max_active == 2
    with connect(service.db_path) as conn:
        rows = conn.execute(
            """
            SELECT agent_id
            FROM research_attempts
            WHERE goal_id=? AND agent_id IS NOT NULL
            """,
            (created["goal_id"],),
        ).fetchall()
    assert {str(row["agent_id"]) for row in rows} >= {
        "source_collector",
        "market_structure_analyst",
        "risk_contradiction_critic",
        "report_synthesizer",
    }


def test_artifact_store_rejects_symlink_escape(tmp_path):
    root = tmp_path / "storage" / "agent" / "research"
    objects = root / "objects"
    outside = tmp_path / "outside"
    objects.mkdir(parents=True)
    outside.mkdir()
    (objects / "sha256").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactSafetyError, match="escapes research root"):
        ArtifactStore(root=root, db_path=tmp_path / "storage" / "kss.db")


def test_artifact_events_allocate_unique_sequences_under_concurrency(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(payload={
        "client_request_id": "artifact-event-concurrency",
        "profile_id": "investment-weekly-v3",
        "objective": "并发产物事件序列",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    goal_id = created["goal_id"]

    def write_artifact(index: int) -> str:
        artifact = service.artifacts.put_bytes(
            goal_id=goal_id,
            kind="test_sidecar",
            name=f"artifact-{index}.json",
            data=json.dumps({"index": index}).encode("utf-8"),
            media_type="application/json",
        )
        return artifact["artifact_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        artifact_ids = list(pool.map(write_artifact, range(12)))

    service.repo.mirror_unmirrored(goal_id)
    events = [
        event for event in service.list_events(goal_id=goal_id)
        if event["type"] == "artifact_ready"
    ]
    sequences = [event["sequence"] for event in events]

    assert len(set(artifact_ids)) == 12
    assert len(events) == 12
    assert len(sequences) == len(set(sequences))
    assert sequences == sorted(sequences)


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
    settled = service.wait_for_idle(goal_id, timeout=10)
    opened = service.open_goal(goal_id=goal_id)
    artifacts = service.list_artifacts(goal_id=goal_id)
    events = service.list_events(goal_id=goal_id, after_sequence=0)

    assert started["ok"] is True
    assert settled["ok"] is True
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


def test_transient_incomplete_attempt_is_requeued_without_reusing_attempt(
    tmp_path,
):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(
        payload={
            "client_request_id": "retry-1",
            "objective": "测试瞬时失败重试",
            "inputs": {
                "date_range": "2026-07-13_to_2026-07-17",
                "as_of": "2026-07-17",
            },
        }
    )
    goal_id = created["goal_id"]
    service.repo.update_goal_status(goal_id, "running")
    task = service._next_ready_task(goal_id)
    assert task is not None
    attempt_id = service._start_attempt(goal_id, task)
    assert attempt_id is not None
    result = {
        "status": "incomplete",
        "warnings": ["agent_runtime_timeout"],
    }
    service._finish_attempt(
        goal_id,
        task["task_id"],
        attempt_id,
        "incomplete",
        result,
    )

    assert service._schedule_transient_retry(
        goal_id,
        task["task_id"],
        attempt_id,
        "incomplete",
        result,
    )
    reopened = service.open_goal(goal_id=goal_id)["detail"]
    refreshed = next(
        item for item in reopened["tasks"] if item["task_id"] == task["task_id"]
    )
    assert refreshed["status"] == "ready"
    assert refreshed["attempt"] == 1
    assert service._is_transient_result(
        {"warnings": ["task_result_schema_invalid"]}
    ) is False


def test_non_synthetic_ledger_compiles_to_watermarked_real_ir(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(
        payload={
            "client_request_id": "real-ir",
            "objective": "只使用当前快照的真实证据生成草稿",
            "inputs": {
                "date_range": "2026-07-13_to_2026-07-17",
                "as_of": "2026-07-17",
            },
        }
    )
    goal_id = created["goal_id"]
    goal = service.repo.get_goal(goal_id) or {}
    snapshot_id = goal["snapshot"]["snapshot_id"]
    source_criterion = next(
        item for item in goal["criteria"] if item["validator"] == "source_coverage"
    )
    evidence_ids = []
    for index in range(1, 7):
        evidence_id = f"real-evidence-{index}"
        evidence_ids.append(evidence_id)
        service.repo.register_evidence(
            Evidence(
                evidence_id=evidence_id,
                goal_id=goal_id,
                criterion_id=source_criterion["criterion_id"],
                source_tool="research_bundle",
                source_tier="reputable_secondary",
                uri=f"https://example.invalid/real/{index}",
                data_as_of="2026-07-17",
                method="successful_tool_result",
                scope=f"真实来源 {index}",
                hash=hashlib.sha256(evidence_id.encode()).hexdigest(),
                metadata={
                    "title": f"真实来源 {index}",
                    "snapshot_id": snapshot_id,
                },
            )
        )
        service.repo.verify_evidence(
            evidence_id, checker="test_tool_result_integrity"
        )
    service.repo.register_claim(
        Claim(
            claim_id="real-claim",
            goal_id=goal_id,
            content="当前证据支持继续跟踪，但缺少可发布的市场温度数字。",
            status="supported",
            evidence_ids=evidence_ids[:2],
        )
    )
    compile_task = next(
        item for item in goal["tasks"] if item["kind"] == "compile_report"
    )
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE research_tasks SET status='ready' WHERE task_id=?",
            (compile_task["task_id"],),
        )
    attempt_id = service._start_attempt(goal_id, compile_task)
    assert attempt_id is not None

    service._run_task(goal_id, compile_task, attempt_id)

    artifacts = service.artifacts.list_goal(goal_id)
    report = next(item for item in artifacts if item["kind"] == "report_html")
    report_ir = next(item for item in artifacts if item["kind"] == "report_ir")
    html = service.artifacts.read_bytes(report["object_hash"]).decode("utf-8")
    ir = service.artifacts.read_bytes(report_ir["object_hash"]).decode("utf-8")
    assert "草稿 · 审计未通过" in html
    assert goal_id in ir
    assert report["metadata"]["draft"] is True
    assert report["metadata"]["audit_status"] == "fail"
    reopened = service.open_goal(goal_id=goal_id)["detail"]
    recorded_task = next(
        item
        for item in reopened["tasks"]
        if item["task_id"] == compile_task["task_id"]
    )
    assert recorded_task["status"] == "incomplete"


def test_real_structured_metric_lineage_can_pass_compiler(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(payload={
        "client_request_id": "real-metric-lineage",
        "objective": "结构化周报",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    goal_id = created["goal_id"]
    goal = service.repo.get_goal(goal_id) or {}
    snapshot_id = goal["snapshot"]["snapshot_id"]
    metric_cases = [
        ("compute_temperature", "m_temperature", "temperature_index", [62.0, 64.8], 63.4),
        ("theme_consensus", "m_consensus", "theme_consensus", [70.0, 74.0], 72.0),
        ("risk_radar", "m_risk", "risk_radar", [40.0, 42.0], 41.0),
    ]
    for index, (kind, metric_id, formula_id, inputs, value) in enumerate(
        metric_cases,
        start=1,
    ):
        task = next(item for item in goal["tasks"] if item["kind"] == kind)
        criterion = next(
            item
            for item in goal["criteria"]
            if item["validator"]
            == {
                "compute_temperature": "metric_ledger",
                "theme_consensus": "theme_consensus",
                "risk_radar": "risk_radar",
            }[kind]
        )
        evidence_id = f"metric-source-{index}"
        service.repo.register_evidence(
            Evidence(
                evidence_id=evidence_id,
                goal_id=goal_id,
                criterion_id=criterion["criterion_id"],
                task_id=task["task_id"],
                source_tool="run_recipe",
                source_tier="deterministic_calculation",
                uri=f"kss-tool://run_recipe/{index}",
                data_as_of="2026-07-17",
                method="successful_tool_result",
                scope=kind,
                hash=hashlib.sha256(evidence_id.encode()).hexdigest(),
                metadata={
                    "snapshot_id": snapshot_id,
                    "numeric_values": inputs,
                },
            )
        )
        service.repo.verify_evidence(evidence_id, checker="tool_result_integrity")
        attempt_id = f"metric-attempt-{index}"
        task_result = {
            "status": "succeeded",
            "claims": [{
                "content": kind,
                "evidence_refs": [evidence_id],
                "metric": {
                    "metric_id": metric_id,
                    "value": value,
                    "unit": "%",
                    "precision": 1,
                    "formula_id": formula_id,
                    "formula_version": "v1",
                    "formula_inputs": inputs,
                    "input_refs": [evidence_id],
                    "as_of": "2026-07-17",
                },
            }],
            "evidence_refs": [evidence_id],
            "artifact_refs": [],
            "open_questions": [],
            "warnings": [],
        }
        with connect(service.db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO research_attempts (
                    attempt_id, goal_id, task_id, status, attempt_no, trigger,
                    result_json, usage_json, created_at, started_at, finished_at
                ) VALUES (?, ?, ?, 'succeeded', 1, 'test', ?, '{}',
                          '2026-07-17T00:00:00+00:00',
                          '2026-07-17T00:00:00+00:00',
                          '2026-07-17T00:00:01+00:00')
                """,
                (attempt_id, goal_id, task["task_id"], json.dumps(task_result)),
            )
            conn.execute(
                """
                UPDATE research_tasks
                SET status='succeeded', current_attempt_id=?
                WHERE task_id=?
                """,
                (attempt_id, task["task_id"]),
            )

    document = service._build_report_document(goal_id)
    compiled = service.compiler.compile(document)

    assert compiled["audit"]["status"] == "pass"
    metrics = document.metric_ledger.by_id()
    assert metrics["m_temperature"].value == 63.4
    assert document.metadata["formula_inputs"]["m_risk"] == [40.0, 42.0]


def test_tool_result_capture_normalizes_metric_refs_to_evidence_ids(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(payload={
        "client_request_id": "tool-lineage-normalization",
        "objective": "工具数字血缘",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    goal_id = created["goal_id"]
    task = next(
        item
        for item in service.open_goal(goal_id=goal_id)["detail"]["tasks"]
        if item["kind"] == "compute_temperature"
    )
    attempt_id = "attempt-tool-lineage"
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO research_attempts (
                attempt_id, goal_id, task_id, status, attempt_no, trigger,
                usage_json, created_at, started_at
            ) VALUES (?, ?, ?, 'running', 1, 'test', '{}',
                      '2026-07-17T00:00:00+00:00',
                      '2026-07-17T00:00:00+00:00')
            """,
            (attempt_id, goal_id, task["task_id"]),
        )
    result = {
        "status": "succeeded",
        "claims": [{
            "content": "市场温度来自确定性配方。",
            "evidence_refs": ["run_recipe"],
            "metric": {
                "metric_id": "m_temperature",
                "value": 63.4,
                "formula_id": "temperature_index",
                "formula_version": "v1",
                "formula_inputs": [62.0, 64.8],
                "input_refs": ["run_recipe"],
            },
        }],
        "evidence_refs": [],
        "artifact_refs": [],
        "open_questions": [],
        "warnings": [],
        "run_id": "run-tool-lineage",
        "_tool_evidence": [],
        "_tool_results": [{
            "tool_name": "run_recipe",
            "tool_call_id": "call-temperature",
            "result": {"scores": [62.0, 64.8]},
        }],
    }

    service._capture_task_result(goal_id, task, attempt_id, result)

    evidence = next(
        item
        for item in service.repo.evidence_for_goal(goal_id)
        if item["tool_call_id"] == "call-temperature"
    )
    assert evidence["metadata"]["numeric_values"] == [62.0, 64.8]
    assert result["claims"][0]["metric"]["input_refs"] == [
        evidence["evidence_id"]
    ]
    assert result["claims"][0]["evidence_refs"] == [evidence["evidence_id"]]


def test_publish_blocks_when_audit_has_not_passed(tmp_path):
    service = ResearchService(state_root=tmp_path, project_root=Path(__file__).resolve().parents[2])
    created = service.create_goal(payload={
        "client_request_id": "req-2",
        "objective": "未运行",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    blocked = service.publish_artifact(goal_id=created["goal_id"], payload={"destination": str(tmp_path / "x.html")})

    assert blocked["ok"] is False
    assert blocked["error"] in {"artifact_not_found", "audit_not_passed"}


def test_publish_rejects_draft_even_if_goal_has_passed_audit(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
        allow_synthetic_fixture=True,
    )
    created = service.create_goal(payload={
        "client_request_id": "draft-publish-gate",
        "objective": "正式发布门",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    goal_id = created["goal_id"]
    assert service.start_goal(goal_id=goal_id)["ok"] is True
    assert service.wait_for_idle(goal_id, timeout=10)["ok"] is True
    report = next(
        item
        for item in service.artifacts.list_goal(goal_id)
        if item["kind"] == "report_html"
    )
    metadata = dict(report["metadata"])
    metadata["draft"] = True
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE research_artifacts SET metadata_json=? WHERE artifact_id=?",
            (json.dumps(metadata), report["artifact_id"]),
        )

    blocked = service.publish_artifact(
        goal_id=goal_id,
        artifact_id=report["artifact_id"],
        payload={"destination": str(tmp_path / "draft.html")},
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "artifact_not_current_completed_snapshot"


def test_retry_requires_retryable_state_and_succeeded_dependencies(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(payload={
        "client_request_id": "retry-guard",
        "objective": "重试依赖保护",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    goal_id = created["goal_id"]
    tasks = service.open_goal(goal_id=goal_id)["detail"]["tasks"]

    not_terminal = service.retry_task(
        goal_id=goal_id,
        task_id=tasks[0]["task_id"],
    )
    assert not_terminal["error"] == "task_not_retryable"

    dependent = tasks[1]
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE research_tasks SET status='incomplete' WHERE task_id=?",
            (dependent["task_id"],),
        )
    blocked = service.retry_task(goal_id=goal_id, task_id=dependent["task_id"])
    assert blocked["error"] == "dependencies_not_satisfied"
    assert blocked["dependencies"][0]["status"] != "succeeded"


def test_delivery_audit_ignores_compiler_audit_from_old_snapshot(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    created = service.create_goal(payload={
        "client_request_id": "old-compiler-audit",
        "objective": "旧快照不能越过审计门",
        "inputs": {
            "date_range": "2026-07-13_to_2026-07-17",
            "as_of": "2026-07-17",
        },
    })
    goal_id = created["goal_id"]
    service.artifacts.put_bytes(
        goal_id=goal_id,
        kind="audit_json",
        name="audit.json",
        data=b'{"status":"pass","findings":[]}',
        media_type="application/json",
        metadata={"snapshot_id": "snapshot-from-old-refresh"},
    )

    assert service._latest_compiler_audit(goal_id) is None


def test_create_goal_is_idempotent_by_client_request_id(tmp_path):
    service = ResearchService(state_root=tmp_path, project_root=Path(__file__).resolve().parents[2])
    inputs = {
        "date_range": "2026-07-13_to_2026-07-17",
        "as_of": "2026-07-17",
    }
    a = service.create_goal(payload={
        "client_request_id": "same",
        "objective": "A",
        "inputs": inputs,
    })
    b = service.create_goal(payload={
        "client_request_id": "same",
        "objective": "B",
        "inputs": inputs,
    })

    assert a["goal_id"] == b["goal_id"]


def test_weekly_profile_rejects_missing_or_invalid_frozen_inputs(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )

    missing = service.create_goal(payload={"objective": "缺少时点"})
    invalid = service.create_goal(payload={
        "objective": "错误区间",
        "inputs": {
            "date_range": "2026-07-20_to_2026-07-13",
            "as_of": "2026-07-12",
        },
    })

    assert missing["error"] == "invalid_research_inputs"
    assert {item["field"] for item in missing["details"]} == {
        "date_range",
        "as_of",
    }
    assert invalid["error"] == "invalid_research_inputs"
    assert {item["reason"] for item in invalid["details"]} >= {
        "start_after_end",
        "before_date_range_end",
    }


def test_evidence_freshness_is_relative_to_frozen_snapshot(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    criterion = {"freshness_days": 14}

    assert service._is_fresh(
        {"data_as_of": "2020-01-17"},
        criterion,
        reference_as_of="2020-01-17",
    )
    assert not service._is_fresh(
        {"data_as_of": "2020-01-01"},
        criterion,
        reference_as_of="2020-01-17",
    )
    assert not service._is_fresh(
        {"data_as_of": "2020-01-18"},
        criterion,
        reference_as_of="2020-01-17",
    )


def test_second_goal_stays_queued_while_global_research_slot_is_busy(tmp_path):
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    inputs = {
        "date_range": "2026-07-13_to_2026-07-17",
        "as_of": "2026-07-17",
    }
    first = service.create_goal(payload={
        "client_request_id": "slot-1",
        "objective": "正在运行",
        "inputs": inputs,
    })
    second = service.create_goal(payload={
        "client_request_id": "slot-2",
        "objective": "等待运行",
        "inputs": inputs,
    })
    first_goal = service.open_goal(goal_id=first["goal_id"])["detail"]
    first_task = first_goal["tasks"][0]
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO research_attempts (
                attempt_id, goal_id, task_id, status, attempt_no, trigger,
                usage_json, lease_owner, lease_expires_at, created_at, started_at
            ) VALUES (
                'attempt_busy', ?, ?, 'running', 1, 'scheduler',
                '{}', 'test', '2099-01-01T00:00:00+00:00',
                '2026-07-17T00:00:00+00:00',
                '2026-07-17T00:00:00+00:00'
            )
            """,
            (first["goal_id"], first_task["task_id"]),
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET status='running', current_attempt_id='attempt_busy'
            WHERE task_id=?
            """,
            (first_task["task_id"],),
        )

    queued = service.start_goal(goal_id=second["goal_id"])

    assert queued["event"] == "queued"
    assert queued["existing_goal_id"] == first["goal_id"]
    opened = service.open_goal(goal_id=second["goal_id"])["detail"]
    assert opened["status"] == "queued"
    assert not opened["events"][-1]["type"] == "research_end"


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
