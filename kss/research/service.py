"""Research overlay service for KSSDesktop sidecar protocol."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema

from .artifacts import ArtifactStore
from .compiler import ReportCompiler, make_investment_weekly_fixture, stable_json
from .graph import get_profile as get_graph_profile
from .models import Claim, Evidence
from .profiles import get_profile as get_packaged_profile
from .profiles import list_profiles as list_packaged_profiles
from .report_models import (
    EvidenceReference,
    MetricEntry,
    MetricLedger,
    NarrativeClaim,
    ReportBlock,
    ReportDocument,
    ReportSection,
)
from .repository import ResearchRepository, dumps, loads, new_id, utc_now
from .runner import AgentResearchTaskRunner

TERMINAL_GOAL = {"completed", "cancelled", "failed", "blocked", "budget_limited", "insufficient_evidence", "needs_refresh"}
TERMINAL_TASK = {"succeeded", "incomplete", "failed", "interrupted", "cancelled", "blocked"}
ALLOWED_EXPORT_ROOTS = ("Downloads", "Desktop", "Documents", "projects")
DATE_RANGE_RE = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2})_to_(?P<end>\d{4}-\d{2}-\d{2})$"
)


class ResearchService:
    """Synchronous facade used by `agent-research` and `agent-artifacts`.

    The first release intentionally keeps node execution sequential and local.
    Protected compile/audit nodes are deterministic; future model-backed nodes
    can replace `_run_task` without changing protocol or ledger semantics.
    """

    def __init__(
        self,
        *,
        state_root: Path,
        project_root: Path | None = None,
        task_runner: AgentResearchTaskRunner | None = None,
        allow_synthetic_fixture: bool = False,
    ) -> None:
        self.state_root = Path(state_root).resolve()
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.db_path = self.state_root / "storage" / "kss.db"
        self.research_root = self.state_root / "storage" / "agent" / "research"
        self.repo = ResearchRepository(db_path=self.db_path, research_root=self.research_root)
        self.artifacts = ArtifactStore(root=self.research_root, db_path=self.db_path)
        self.compiler = ReportCompiler()
        self.task_runner = task_runner or AgentResearchTaskRunner(
            state_root=self.state_root,
            project_root=self.project_root,
        )
        self.allow_synthetic_fixture = allow_synthetic_fixture
        self.artifacts.clean_staging_and_isolate_orphans()
        self.repo.mark_stale_running_attempts()
        self.repo.mirror_unmirrored()

    # ------------------------------------------------------------------
    # Protocol actions
    # ------------------------------------------------------------------

    def create_goal(self, payload: dict[str, Any] | None = None, goal: str | None = None, **_: Any) -> dict[str, Any]:
        payload = payload or {}
        profile_id = str(payload.get("profile_id") or "investment-weekly-v3")
        try:
            profile = get_graph_profile(profile_id)
        except ValueError:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "profile_not_found",
                "profile_id": profile_id,
            }
        objective = str(payload.get("objective") or goal or payload.get("goal") or profile.title)
        inputs = dict(payload.get("inputs") or {})
        input_errors = self._validate_inputs(profile_id, inputs)
        if input_errors:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "invalid_research_inputs",
                "details": input_errors,
                "profile_id": profile_id,
            }
        budget = dict(profile.budget)
        budget.update(payload.get("budget_overrides") or {})
        goal_id = new_id("goal")
        task_ids = {task.kind: f"{goal_id}_{task.kind}" for task in profile.tasks}
        criteria = []
        for index, item in enumerate(profile.criteria, start=1):
            criteria.append({
                "criterion_id": f"{goal_id}_criterion_{index:02d}",
                "label": item["label"],
                "required": item.get("required", True),
                "min_verified_evidence": item.get("min_verified_evidence", 1),
                "allowed_tiers": item.get("allowed_tiers", ["official_or_primary", "reputable_secondary"]),
                "freshness_days": item.get("freshness_days"),
                "validator": item.get("validator"),
            })
        tasks = []
        dependencies: list[tuple[str, str, bool]] = []
        for index, task in enumerate(profile.tasks, start=1):
            task_id = task_ids[task.kind]
            tasks.append({
                "task_id": task_id,
                "kind": task.kind,
                "title": task.title,
                "status": "pending" if task.depends_on else "ready",
                "required": task.required,
                "sequence_index": index,
                "payload": task.payload,
            })
            for dep_kind in task.depends_on:
                dependencies.append((task_id, task_ids[dep_kind], True))
        snapshot = self._snapshot(profile_id=profile_id, inputs=inputs)
        created = self.repo.create_goal(
            goal_id=goal_id,
            session_id=payload.get("session_id"),
            profile_id=profile_id,
            objective=objective,
            inputs=inputs,
            snapshot=snapshot,
            budget=budget,
            criteria=criteria,
            tasks=tasks,
            dependencies=dependencies,
            client_request_id=payload.get("client_request_id"),
        )
        resolved_goal_id = str(created.get("goal_id") or goal_id)
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "created",
            "goal": self._wire_goal(created),
            "detail": self._wire_goal(created),
            "goal_id": resolved_goal_id,
            "profile": get_packaged_profile(profile_id, self.project_root),
        }

    def list_goals(self, **_: Any) -> dict[str, Any]:
        return {"protocol_version": 1, "ok": True, "event": "listed", "profiles": list_packaged_profiles(self.project_root), "goals": [self._summary(g) for g in self.repo.list_goals()]}

    def open_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "opened",
            "goal": self._wire_goal(goal),
            "detail": self._wire_goal(goal),
            "goal_id": goal_id,
        }

    def start_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        if goal["status"] in TERMINAL_GOAL:
            return {"protocol_version": 1, "ok": False, "error": "goal_terminal", "goal": goal_id, "goal_id": goal_id, "status": goal["status"]}
        active_goal_id, active_attempt_id = self._active_attempt()
        if active_attempt_id:
            if active_goal_id == goal_id:
                return {
                    "protocol_version": 1,
                    "ok": True,
                    "event": "already_running",
                    "goal": self._wire_goal(goal),
                    "detail": self._wire_goal(goal),
                    "goal_id": goal_id,
                    "attempt_id": active_attempt_id,
                }
            self.repo.update_goal_status(
                goal_id,
                "queued",
                termination_reason="global_research_slot_busy",
            )
            return {
                "protocol_version": 1,
                "ok": True,
                "event": "queued",
                "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                "detail": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                "goal_id": goal_id,
                "existing_goal_id": active_goal_id,
            }
        self.repo.update_goal_status(goal_id, "running")
        self._emit(goal_id, "research_start", {"status": "running"})
        try:
            exhausted = self._run_ready_loop(goal_id)
            settled = self.repo.get_goal(goal_id) or {}
            may_settle = exhausted and settled.get("status") == "running"
            audit: dict[str, Any] | None = None
            if exhausted:
                audit = self.audit_goal(
                    goal_id=goal_id,
                    complete_if_pass=may_settle,
                )
                self._emit(
                    goal_id,
                    "research_end",
                    {
                        "status": (self.repo.get_goal(goal_id) or {}).get(
                            "status"
                        ),
                        "audit_status": audit.get("status"),
                    },
                )
            return {
                "protocol_version": 1,
                "ok": True,
                "event": "started",
                "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                "detail": self._wire_goal(self.repo.get_goal(goal_id) or {}),
                "goal_id": goal_id,
            }
        except Exception as exc:
            self.repo.update_goal_status(goal_id, "failed", termination_reason=str(exc))
            self._emit(goal_id, "research_error", {"error": str(exc)})
            return {"protocol_version": 1, "ok": False, "error": "research_failed", "reason": str(exc), "goal": goal_id, "goal_id": goal_id}

    def pause_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        return self._set_goal(goal_id, "paused", "paused")

    def resume_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if goal_id:
            with connect(self.db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='ready', current_attempt_id=NULL,
                        lease_owner=NULL, lease_expires_at=NULL,
                        updated_at=?, finished_at=NULL
                    WHERE goal_id=? AND status='interrupted'
                    """,
                    (utc_now(), goal_id),
                )
            self.repo.update_goal_status(goal_id, "running")
            return self.start_goal(goal_id=goal_id)
        return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}

    def cancel_goal(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        return self._set_goal(goal_id, "cancelled", "cancelled")

    def retry_task(self, goal_id: str | None = None, task_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id or not task_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_and_task_required"}
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            task = conn.execute(
                "SELECT status FROM research_tasks WHERE goal_id=? AND task_id=?",
                (goal_id, task_id),
            ).fetchone()
            if not task:
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "task_not_found",
                    "goal_id": goal_id,
                }
            if str(task["status"]) not in {"failed", "incomplete", "interrupted"}:
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "task_not_retryable",
                    "status": str(task["status"]),
                    "goal_id": goal_id,
                }
            missing_dependencies = conn.execute(
                """
                SELECT d.depends_on_task_id, t.status
                FROM research_task_dependencies d
                JOIN research_tasks t ON t.task_id=d.depends_on_task_id
                WHERE d.goal_id=? AND d.task_id=? AND d.required=1
                  AND t.status!='succeeded'
                ORDER BY t.sequence_index
                """,
                (goal_id, task_id),
            ).fetchall()
            if missing_dependencies:
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "dependencies_not_satisfied",
                    "dependencies": [dict(row) for row in missing_dependencies],
                    "goal_id": goal_id,
                }
            now = utc_now()
            conn.execute("UPDATE research_tasks SET status='ready', current_attempt_id=NULL, updated_at=?, finished_at=NULL WHERE goal_id=? AND task_id=?", (now, goal_id, task_id))
            conn.execute(
                """
                UPDATE research_goals
                SET status='running', termination_reason=NULL, finished_at=NULL,
                    updated_at=?
                WHERE goal_id=? AND status IN (
                    'insufficient_evidence', 'blocked', 'failed',
                    'budget_limited', 'needs_refresh'
                )
                """,
                (now, goal_id),
            )
        self._emit(goal_id, "task_ready", {"task_id": task_id, "retry": True}, task_id=task_id)
        return self.start_goal(goal_id=goal_id)

    def refresh_snapshot(
        self,
        goal_id: str | None = None,
        payload: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        refreshed_inputs = dict(goal.get("inputs") or {})
        requested_inputs = (payload or {}).get("inputs")
        if isinstance(requested_inputs, dict):
            refreshed_inputs.update(requested_inputs)
        input_errors = self._validate_inputs(goal["profile_id"], refreshed_inputs)
        if input_errors:
            return {
                "protocol_version": 1,
                "ok": False,
                "error": "invalid_research_inputs",
                "details": input_errors,
                "goal_id": goal_id,
            }
        snapshot = self._snapshot(
            profile_id=goal["profile_id"],
            inputs=refreshed_inputs,
            refresh_of=goal.get("snapshot", {}).get("snapshot_id"),
        )
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            conn.execute(
                """
                UPDATE research_goals
                SET inputs_json=?, snapshot_json=?, status='draft',
                    termination_reason=NULL, finished_at=NULL, updated_at=?
                WHERE goal_id=?
                """,
                (dumps(refreshed_inputs), dumps(snapshot), now, goal_id),
            )
            conn.execute(
                """
                UPDATE research_criteria
                SET status='pending', updated_at=?
                WHERE goal_id=?
                """,
                (now, goal_id),
            )
            conn.execute(
                """
                UPDATE research_tasks
                SET status=CASE WHEN sequence_index=1 THEN 'ready' ELSE 'pending' END,
                    current_attempt_id=NULL, lease_owner=NULL,
                    lease_expires_at=NULL, started_at=NULL, finished_at=NULL,
                    updated_at=?
                WHERE goal_id=?
                """,
                (now, goal_id),
            )
            self.repo.append_event(
                conn,
                goal_id=goal_id,
                event_type="goal_status",
                payload={"status": "draft", "snapshot": snapshot, "refreshed": True},
            )
        self.repo.mirror_unmirrored(goal_id)
        refreshed = self.repo.get_goal(goal_id) or {}
        return {
            "protocol_version": 1,
            "ok": True,
            "event": "snapshot_refreshed",
            "goal": self._wire_goal(refreshed),
            "detail": self._wire_goal(refreshed),
            "goal_id": goal_id,
            "snapshot": snapshot,
        }

    def audit_goal(self, goal_id: str | None = None, complete_if_pass: bool = False, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        evidence = self.repo.evidence_for_goal(goal_id)
        claims = self.repo.claims_for_goal(goal_id)
        artifacts = self.artifacts.list_goal(goal_id)
        findings: list[dict[str, Any]] = []
        criterion_statuses: dict[str, str] = {}
        snapshot_id = str((goal.get("snapshot") or {}).get("snapshot_id") or "")
        coverage: dict[str, Any] = {"criteria": {}, "tasks": {}, "artifact_count": len(artifacts)}
        for criterion in goal.get("criteria", []):
            allowed_tiers = set(criterion.get("allowed_tiers") or [])
            verified = [
                item for item in evidence
                if item.get("criterion_id") == criterion["criterion_id"]
                and item.get("verified")
                and str((item.get("metadata") or {}).get("snapshot_id") or "")
                == snapshot_id
            ]
            allowed = [item for item in verified if not allowed_tiers or item.get("source_tier") in allowed_tiers]
            fresh = [
                item
                for item in allowed
                if self._is_fresh(
                    item,
                    criterion,
                    reference_as_of=str(
                        (goal.get("snapshot") or {}).get("as_of") or ""
                    ),
                )
            ]
            coverage["criteria"][criterion["criterion_id"]] = {
                "verified": len(verified),
                "allowed_tier": len(allowed),
                "fresh": len(fresh),
                "required": bool(criterion["required"]),
                "allowed_tiers": sorted(allowed_tiers),
            }
            criterion_statuses[criterion["criterion_id"]] = (
                "met"
                if len(fresh) >= int(criterion["min_verified_evidence"])
                else "unmet"
            )
            if (
                criterion.get("validator") != "delivery_audit"
                and criterion["required"]
                and len(fresh) < int(criterion["min_verified_evidence"])
            ):
                findings.append({"severity": "block", "code": "criterion_insufficient_evidence", "criterion_id": criterion["criterion_id"], "label": criterion["label"]})
        for task in goal.get("tasks", []):
            coverage["tasks"][task["task_id"]] = task["status"]
            if task["required"] and task["status"] != "succeeded":
                findings.append({"severity": "block", "code": "required_task_not_succeeded", "task_id": task["task_id"], "status": task["status"]})
        for claim in claims:
            if claim.get("status") == "contradicted" and claim.get(
                "contradiction_ids"
            ):
                findings.append({"severity": "block", "code": "unresolved_contradiction", "claim_id": claim["claim_id"]})
        report_artifacts = [a for a in artifacts if a.get("kind") == "report_html"]
        if not report_artifacts:
            findings.append({"severity": "block", "code": "missing_report_html"})
        compiler_audits = [a for a in artifacts if a.get("kind") == "audit_json"]
        manifests = [a for a in artifacts if a.get("kind") == "manifest"]
        if report_artifacts and not compiler_audits:
            findings.append({"severity": "block", "code": "missing_compiler_audit"})
        if report_artifacts and not manifests:
            findings.append({"severity": "block", "code": "missing_report_manifest"})
        if compiler_audits:
            try:
                compiler_audit = json.loads(
                    self.artifacts.read_bytes(
                        str(compiler_audits[-1]["object_hash"])
                    )
                )
                if compiler_audit.get("status") != "pass":
                    findings.append(
                        {"severity": "block", "code": "compiler_audit_failed"}
                    )
            except (OSError, ValueError, json.JSONDecodeError):
                findings.append(
                    {"severity": "block", "code": "compiler_audit_invalid"}
                )
        for artifact in artifacts:
            try:
                actual = hashlib.sha256(
                    self.artifacts.read_bytes(str(artifact["object_hash"]))
                ).hexdigest()
            except OSError:
                actual = ""
            if actual != artifact["object_hash"]:
                findings.append(
                    {
                        "severity": "block",
                        "code": "artifact_hash_mismatch",
                        "artifact_id": artifact["artifact_id"],
                    }
                )
        audit_criterion = next(
            (
                criterion
                for criterion in goal.get("criteria", [])
                if criterion.get("validator") == "delivery_audit"
            ),
            None,
        )
        if not findings and audit_criterion:
            existing = next(
                (
                    item
                    for item in evidence
                    if item.get("criterion_id")
                    == audit_criterion["criterion_id"]
                    and item.get("method") == "research_audit"
                    and item.get("verified")
                    and str(
                        (item.get("metadata") or {}).get("snapshot_id") or ""
                    )
                    == snapshot_id
                ),
                None,
            )
            if not existing:
                manifest_hash = (
                    str(manifests[-1]["object_hash"]) if manifests else None
                )
                audit_evidence = Evidence(
                    evidence_id=new_id("ev"),
                    goal_id=goal_id,
                    criterion_id=audit_criterion["criterion_id"],
                    source_tool="research_audit_service",
                    source_tier="deterministic_calculation",
                    artifact_id=manifests[-1]["artifact_id"]
                    if manifests
                    else None,
                    data_as_of=self._goal_as_of(goal_id),
                    method="research_audit",
                    scope="delivery_gate",
                    hash=manifest_hash,
                    metadata={"snapshot_id": snapshot_id},
                )
                self.repo.register_evidence(audit_evidence)
                self.repo.verify_evidence(
                    audit_evidence.evidence_id,
                    checker="research_audit_service",
                    detail={"manifest_hash": manifest_hash},
                )
            coverage["criteria"][audit_criterion["criterion_id"]] = {
                "verified": 1,
                "allowed_tier": 1,
                "fresh": 1,
                "required": True,
                "allowed_tiers": audit_criterion.get("allowed_tiers") or [],
            }
            criterion_statuses[audit_criterion["criterion_id"]] = "met"
        elif audit_criterion:
            criterion_statuses[audit_criterion["criterion_id"]] = "unmet"
        status = "fail" if any(f["severity"] == "block" for f in findings) else "pass"
        audit = {"status": status, "coverage": coverage, "findings": findings, "generated_at": utc_now()}
        audit_id = new_id("audit")
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            conn.execute(
                "INSERT INTO research_audits (audit_id, goal_id, status, coverage_json, findings_json, artifact_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (audit_id, goal_id, status, dumps(coverage), dumps(findings), report_artifacts[-1]["artifact_id"] if report_artifacts else None, now),
            )
            for criterion_id, criterion_status in criterion_statuses.items():
                conn.execute(
                    """
                    UPDATE research_criteria
                    SET status=?, updated_at=?
                    WHERE criterion_id=? AND goal_id=?
                    """,
                    (criterion_status, now, criterion_id, goal_id),
                )
        self._emit(goal_id, "audit_result", {"audit_id": audit_id, "status": status, "coverage": coverage, "findings": findings})
        if complete_if_pass:
            self.repo.update_goal_status(goal_id, "completed" if status == "pass" else "insufficient_evidence", termination_reason=None if status == "pass" else "audit_failed")
        return {"protocol_version": 1, "ok": True, "event": "audited", "goal": goal_id, "goal_id": goal_id, **audit}

    def list_events(self, goal_id: str | None = None, after_sequence: int = 0, **_: Any) -> list[dict[str, Any]]:
        return self.repo.list_events(str(goal_id), int(after_sequence or 0)) if goal_id else []

    events = list_events
    replay_events = list_events

    def list_artifacts(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        return {"protocol_version": 1, "ok": True, "event": "artifacts_listed", "goal": goal_id, "goal_id": goal_id, "artifacts": [self._wire_artifact(a) for a in self.artifacts.list_goal(goal_id)]}

    def export_draft(self, goal_id: str | None = None, artifact_id: str | None = None, payload: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return self._export(goal_id=goal_id, artifact_id=artifact_id, payload=payload or {}, require_pass=False, event="draft_exported")

    def publish_artifact(self, goal_id: str | None = None, artifact_id: str | None = None, payload: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return self._export(goal_id=goal_id, artifact_id=artifact_id, payload=payload or {}, require_pass=True, event="published")

    # ------------------------------------------------------------------
    # Execution internals
    # ------------------------------------------------------------------

    def _run_ready_loop(self, goal_id: str) -> bool:
        while True:
            goal = self.repo.get_goal(goal_id) or {}
            if goal.get("status") != "running":
                return False
            if self._budget_exceeded(goal):
                self.repo.update_goal_status(
                    goal_id,
                    "budget_limited",
                    termination_reason="research_budget_exhausted",
                )
                return False
            task = self._next_ready_task(goal_id)
            if not task:
                return True
            attempt_id = self._start_attempt(goal_id, task)
            if attempt_id is None:
                self.repo.update_goal_status(
                    goal_id,
                    "queued",
                    termination_reason="global_research_slot_busy",
                )
                return False
            self._run_task(goal_id, task, attempt_id)
            self._promote_ready_tasks(goal_id)

    def _run_task(
        self,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
    ) -> None:
        try:
            self._emit(goal_id, "task_start", {"task": task}, task_id=task["task_id"], attempt_id=attempt_id)
            kind = task["kind"]
            if kind == "freeze_snapshot":
                self._register_task_evidence(goal_id, task, attempt_id, "snapshot", "冻结快照证据")
            elif kind == "compile_report":
                compiled = self._compile_report(goal_id, task, attempt_id)
                compile_status = (
                    "succeeded"
                    if compiled["audit"]["status"] == "pass"
                    else "incomplete"
                )
                compile_result = {
                    "status": compile_status,
                    "claims": [],
                    "evidence_refs": [],
                    "artifact_refs": compiled["artifact_refs"],
                    "open_questions": [],
                    "warnings": [
                        str(finding.get("code") or "compiler_audit_failed")
                        for finding in compiled["audit"].get("findings") or []
                    ],
                }
                self._finish_attempt(
                    goal_id,
                    task["task_id"],
                    attempt_id,
                    compile_status,
                    compile_result,
                )
                self._record_usage(goal_id, {})
                self._emit(
                    goal_id,
                    "task_end",
                    {
                        "status": compile_status,
                        "task_id": task["task_id"],
                        "audit_status": compiled["audit"]["status"],
                    },
                    task_id=task["task_id"],
                    attempt_id=attempt_id,
                )
                return
            elif kind == "delivery_audit":
                # A task marker or assistant statement is never itself audit
                # evidence. This node only checks the compiler's durable
                # audit sidecar; ResearchAuditService creates the final
                # completion evidence after every required task has settled.
                compiler_audit = self._latest_compiler_audit(goal_id)
                if not compiler_audit or compiler_audit.get("status") != "pass":
                    result = {
                        "status": "incomplete",
                        "claims": [],
                        "evidence_refs": [],
                        "artifact_refs": [],
                        "open_questions": ["编译器审计尚未通过"],
                        "warnings": ["compiler_audit_not_passed"],
                    }
                    self._finish_attempt(
                        goal_id,
                        task["task_id"],
                        attempt_id,
                        "incomplete",
                        result,
                    )
                    self._record_usage(goal_id, {})
                    self._emit(
                        goal_id,
                        "task_end",
                        {
                            "status": "incomplete",
                            "task_id": task["task_id"],
                            "reason": "compiler_audit_not_passed",
                        },
                        task_id=task["task_id"],
                        attempt_id=attempt_id,
                    )
                    return
            elif kind == "preview_publish_gate":
                self._emit(goal_id, "artifact_ready", {"preview": True}, task_id=task["task_id"], attempt_id=attempt_id)
            elif self.allow_synthetic_fixture:
                self._run_synthetic_task(goal_id, task, attempt_id)
            else:
                result = self.task_runner.run(
                    goal=self.repo.get_goal(goal_id) or {},
                    task=task,
                    attempt_id=attempt_id,
                    dependency_summaries=self._dependency_summaries(
                        task["task_id"]
                    ),
                )
                self._capture_task_result(goal_id, task, attempt_id, result)
                status = str(result.get("status") or "incomplete")
                current_goal_status = (self.repo.get_goal(goal_id) or {}).get(
                    "status"
                )
                if current_goal_status == "paused":
                    status = "interrupted"
                elif current_goal_status == "cancelled":
                    status = "cancelled"
                self._finish_attempt(
                    goal_id,
                    task["task_id"],
                    attempt_id,
                    status,
                    {
                        key: value
                        for key, value in result.items()
                        if not key.startswith("_")
                    },
                )
                self._record_usage(goal_id, result.get("usage") or {})
                retry_scheduled = self._schedule_transient_retry(
                    goal_id,
                    task["task_id"],
                    attempt_id,
                    status,
                    result,
                )
                self._emit(
                    goal_id,
                    "task_end",
                    {
                        "status": status,
                        "task_id": task["task_id"],
                        "retry_scheduled": retry_scheduled,
                    },
                    task_id=task["task_id"],
                    attempt_id=attempt_id,
                )
                return
            succeeded = {
                "status": "succeeded",
                "claims": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "open_questions": [],
                "warnings": [],
            }
            self._finish_attempt(
                goal_id,
                task["task_id"],
                attempt_id,
                "succeeded",
                succeeded,
            )
            self._record_usage(goal_id, {})
            self._emit(
                goal_id,
                "task_end",
                {"status": "succeeded", "task_id": task["task_id"]},
                task_id=task["task_id"],
                attempt_id=attempt_id,
            )
        except Exception as exc:
            self._finish_attempt(goal_id, task["task_id"], attempt_id, "failed", {"status": "incomplete", "warnings": [str(exc)]}, error=str(exc))
            self._emit(goal_id, "task_end", {"status": "failed", "error": str(exc), "task_id": task["task_id"]}, task_id=task["task_id"], attempt_id=attempt_id)
            raise

    def _run_synthetic_task(self, goal_id: str, task: dict[str, Any], attempt_id: str) -> None:
        """Produce deterministic fixture evidence for packaged weekly-profile smoke runs.

        This is intentionally separate from model-backed task execution. It
        keeps the first desktop research workflow runnable without BYOK while
        preserving ledger, tier and audit gates.
        """

        kind = task["kind"]
        validator_by_kind = {
            "collect_sources": "source_coverage",
            "normalize_fields": "evidence",
            "compute_temperature": "metric_ledger",
            "theme_consensus": "theme_consensus",
            "risk_radar": "risk_radar",
            "analyst_cards": "precision_cards",
            "verify_data": "evidence",
            "narrative": "evidence",
        }
        validator = validator_by_kind.get(kind, "evidence")
        if kind == "collect_sources":
            for index in range(1, 7):
                self._register_task_evidence(
                    goal_id,
                    task,
                    attempt_id,
                    validator,
                    f"合成分析源 {index}",
                    uri=f"kss-fixture://investment-weekly-v3/source-{index}",
                )
            return
        self._register_task_evidence(
            goal_id,
            task,
            attempt_id,
            validator,
            f"{task['title']}证据",
        )

    def _compile_report(
        self,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self._emit(goal_id, "compile_start", {"task_id": task["task_id"]}, task_id=task["task_id"], attempt_id=attempt_id)
        document = (
            make_investment_weekly_fixture()
            if self.allow_synthetic_fixture
            else self._build_report_document(goal_id)
        )
        compiled = self.compiler.compile(document)
        artifact_refs = []
        kind_map = {
            "report.html": "report_html",
            "report_ir.json": "report_ir",
            "metrics.json": "metrics",
            "claims.json": "claims",
            "evidence_manifest.json": "evidence_manifest",
            "audit.json": "audit_json",
            "manifest.json": "manifest",
            "preview.png": "preview_png",
        }
        for name, data in compiled["outputs"].items():
            artifact = self.artifacts.put_bytes(
                goal_id=goal_id,
                task_id=task["task_id"],
                attempt_id=attempt_id,
                kind=kind_map.get(name, "report_sidecar"),
                name=name,
                data=data,
                media_type="text/html; charset=utf-8" if name.endswith(".html") else ("image/png" if name.endswith(".png") else "application/json"),
                metadata={
                    "audit_status": compiled["audit"]["status"],
                    "draft": compiled["draft"],
                    "logical_name": name,
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            artifact_refs.append(artifact["artifact_id"])
        if compiled["audit"]["status"] == "pass":
            compiled_evidence_ids: list[str] = []
            for validator in (
                "metric_ledger",
                "theme_consensus",
                "risk_radar",
                "precision_cards",
            ):
                criterion = self._criterion_for_validator(goal_id, validator)
                if not criterion:
                    continue
                evidence = Evidence(
                    evidence_id=new_id("ev"),
                    goal_id=goal_id,
                    criterion_id=criterion["criterion_id"],
                    task_id=task["task_id"],
                    attempt_id=attempt_id,
                    source_tool="delivery_compiler",
                    source_tier="deterministic_calculation",
                    artifact_id=artifact_refs[0] if artifact_refs else None,
                    data_as_of=self._goal_as_of(goal_id),
                    method="ReportCompiler.compile",
                    scope=validator,
                    hash=compiled["manifest"]["object_hashes"].get(
                        "report.html"
                    ),
                    metadata={
                        "audit_status": compiled["audit"]["status"],
                        "artifact_refs": artifact_refs,
                        "snapshot_id": self._snapshot_id(goal_id),
                    },
                )
                self.repo.register_evidence(evidence)
                self.repo.verify_evidence(
                    evidence.evidence_id, checker="delivery_compiler"
                )
                compiled_evidence_ids.append(evidence.evidence_id)
            self.repo.register_claim(
                Claim(
                    claim_id=new_id("claim"),
                    goal_id=goal_id,
                    content="投资分析周报 V3 已由结构化 IR 编译并通过确定性审计。",
                    status="supported",
                    task_id=task["task_id"],
                    evidence_ids=compiled_evidence_ids,
                )
            )
        self.repo.mirror_unmirrored(goal_id)
        self._emit(goal_id, "compile_end", {"status": compiled["audit"]["status"], "artifact_refs": artifact_refs}, task_id=task["task_id"], attempt_id=attempt_id)
        compiled["artifact_refs"] = artifact_refs
        return compiled

    def _build_report_document(self, goal_id: str) -> ReportDocument:
        """Build an honest typed draft from the current snapshot ledger.

        Missing market metrics remain explicit ``N/A`` values. The compiler
        turns those into blocking audit findings and a watermarked draft
        instead of inventing numbers or stopping before a preview exists.
        """

        goal = self.repo.get_goal(goal_id) or {}
        snapshot = goal.get("snapshot") or {}
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        raw_evidence = [
            item
            for item in self.repo.evidence_for_goal(goal_id)
            if item.get("verified")
            and str((item.get("metadata") or {}).get("snapshot_id") or "")
            == snapshot_id
            and item.get("hash")
            and item.get("data_as_of")
        ]
        evidence = [
            EvidenceReference(
                evidence_id=str(item["evidence_id"]),
                source_tier=str(item.get("source_tier") or "unknown"),
                title=str(
                    (item.get("metadata") or {}).get("title")
                    or item.get("scope")
                    or item.get("source_tool")
                    or "研究证据"
                ),
                uri=item.get("uri"),
                data_as_of=str(item.get("data_as_of") or ""),
                hash=str(item.get("hash") or ""),
                caveat=item.get("caveat"),
            )
            for item in raw_evidence
        ]
        evidence_ids = {item.evidence_id for item in evidence}
        evidence_numeric_values = {
            str(item["evidence_id"]): [
                float(value)
                for value in (item.get("metadata") or {}).get(
                    "numeric_values", []
                )
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            for item in raw_evidence
        }
        task_results = self._current_task_results(goal_id)
        claims = [
            NarrativeClaim(
                claim_id=str(item["claim_id"]),
                text=str(item.get("content") or ""),
                evidence_refs=[
                    str(ref)
                    for ref in item.get("evidence_ids") or []
                    if str(ref) in evidence_ids
                ],
                review_required=True,
            )
            for item in self.repo.claims_for_goal(goal_id)
            if item.get("status") == "supported"
            and any(str(ref) in evidence_ids for ref in item.get("evidence_ids") or [])
        ]
        card_rows = [
            {
                "card_id": f"claim_{index:04d}",
                "title": f"证据卡 {index:04d}",
                "summary": claim.text,
                "metric_refs": ["m_card_count"],
                "evidence_refs": list(claim.evidence_refs),
                "source_group": claim.evidence_refs[0],
            }
            for index, claim in enumerate(claims, start=1)
        ]
        if not card_rows:
            card_rows = [
                {
                    "card_id": f"evidence_{index:04d}",
                    "title": item.title,
                    "summary": item.caveat or "已验证证据条目，尚待形成研究主张。",
                    "metric_refs": ["m_card_count"],
                    "evidence_refs": [item.evidence_id],
                    "source_group": item.source_tier,
                }
                for index, item in enumerate(evidence, start=1)
            ]
        metric_specs = {
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
        derived_metrics: dict[str, MetricEntry] = {}
        formula_inputs: dict[str, list[float]] = {}
        for task_kind, expected in metric_specs.items():
            task_result = task_results.get(task_kind) or {}
            for raw_claim in task_result.get("claims") or []:
                if not isinstance(raw_claim, dict):
                    continue
                metric = raw_claim.get("metric")
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("metric_id") or "")
                input_refs = [
                    str(value) for value in metric.get("input_refs") or []
                ]
                values = metric.get("formula_inputs")
                if (
                    metric_id != expected["metric_id"]
                    or str(metric.get("formula_id") or "")
                    != expected["formula_id"]
                    or metric.get("formula_version") != "v1"
                    or not input_refs
                    or any(value not in evidence_ids for value in input_refs)
                    or not isinstance(values, list)
                    or not values
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        for value in values
                    )
                ):
                    continue
                source_numbers = [
                    number
                    for evidence_id in input_refs
                    for number in evidence_numeric_values.get(evidence_id, [])
                ]
                normalized_values = [float(value) for value in values]
                if not source_numbers or any(
                    not any(
                        abs(value - source) <= max(1e-9, abs(source) * 1e-9)
                        for source in source_numbers
                    )
                    for value in normalized_values
                ):
                    continue
                value = metric.get("value")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    continue
                try:
                    precision = int(metric.get("precision") or 1)
                except (TypeError, ValueError):
                    continue
                if not 0 <= precision <= 8:
                    continue
                metric_as_of = str(metric.get("as_of") or "")
                if metric_as_of != self._goal_as_of(goal_id):
                    continue
                derived_metrics[metric_id] = MetricEntry(
                    metric_id,
                    str(expected["label"]),
                    float(value),
                    str(metric.get("unit") or "%"),
                    precision,
                    str(expected["formula_id"]),
                    "v1",
                    input_refs,
                    metric_as_of,
                )
                formula_inputs[metric_id] = normalized_values
                break
        all_refs = [item.evidence_id for item in evidence]
        metrics = MetricLedger(
            [
                derived_metrics.get("m_temperature")
                or MetricEntry(
                    "m_temperature",
                    "市场温度",
                    "N/A",
                    "",
                    1,
                    "temperature_index",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
                derived_metrics.get("m_consensus")
                or MetricEntry(
                    "m_consensus",
                    "主题共识强度",
                    "N/A",
                    "",
                    1,
                    "theme_consensus",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
                derived_metrics.get("m_risk")
                or MetricEntry(
                    "m_risk",
                    "风险雷达均值",
                    "N/A",
                    "",
                    1,
                    "risk_radar",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
                MetricEntry(
                    "m_card_count",
                    "证据卡数量",
                    len(card_rows),
                    "张",
                    0,
                    "card_count",
                    "v1",
                    all_refs,
                    self._goal_as_of(goal_id),
                ),
            ]
        )
        objective = str(goal.get("objective") or "深度研究")
        sections = [
            ReportSection(
                "sec_overview",
                "总览",
                "overview",
                [
                    ReportBlock(
                        "b_overview",
                        "paragraph",
                        text=objective,
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_temperature",
                "市场温度",
                "temperature",
                [
                    ReportBlock(
                        "b_temperature",
                        "metric_group",
                        metric_refs=["m_temperature", "m_card_count"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_theme",
                "主题共识",
                "theme-consensus",
                [
                    ReportBlock(
                        "b_theme",
                        "metric_group",
                        metric_refs=["m_consensus"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_risk",
                "风险雷达",
                "risk-radar",
                [
                    ReportBlock(
                        "b_risk",
                        "metric_group",
                        metric_refs=["m_risk"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_analyst",
                "分析师分区",
                "analyst-sections",
                [
                    ReportBlock(
                        "b_analysts",
                        "table",
                        rows=[
                            {
                                "来源": item.title,
                                "等级": item.source_tier,
                                "evidence_refs": [item.evidence_id],
                            }
                            for item in evidence
                        ],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_cards",
                "精判卡",
                "precision-cards",
                [
                    ReportBlock(
                        "b_cards",
                        "precision_cards",
                        rows=card_rows,
                        metric_refs=["m_card_count"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_method",
                "方法论",
                "methodology",
                [
                    ReportBlock(
                        "b_method",
                        "methodology",
                        text="仅工具结果和确定性计算可进入证据账本；缺失指标保持空缺。",
                        evidence_refs=all_refs,
                    )
                ],
            ),
            ReportSection(
                "sec_audit",
                "审计",
                "audit",
                [
                    ReportBlock(
                        "b_audit",
                        "audit",
                        text="正式发布需通过证据、数字、矛盾、锚点和对象哈希门禁。",
                        metric_refs=["m_card_count"],
                        evidence_refs=all_refs,
                    )
                ],
            ),
        ]
        return ReportDocument(
            document_id=f"{goal_id}-{snapshot_id or 'snapshot'}",
            profile_id=str(goal.get("profile_id") or "investment-weekly-v3"),
            title="投资分析周报 V3",
            subtitle="结构化证据草稿",
            date_range=str(
                (goal.get("inputs") or {}).get("date_range") or "未指定"
            ),
            as_of=self._goal_as_of(goal_id),
            sections=sections,
            metric_ledger=metrics,
            claims=claims,
            evidence=evidence,
            metadata={
                "snapshot_id": snapshot_id,
                "card_count": len(card_rows),
                "formula_inputs": formula_inputs,
            },
        )

    def _current_task_results(self, goal_id: str) -> dict[str, dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT t.kind, a.result_json
                FROM research_tasks t
                JOIN research_attempts a ON a.attempt_id=t.current_attempt_id
                WHERE t.goal_id=? AND t.status='succeeded'
                  AND a.status='succeeded'
                """,
                (goal_id,),
            ).fetchall()
        return {
            str(row["kind"]): loads(row["result_json"], {})
            for row in rows
        }

    def _start_attempt(
        self,
        goal_id: str,
        task: dict[str, Any],
    ) -> str | None:
        attempt_id = new_id("attempt")
        now = utc_now()
        conn = self.repo.transaction()
        try:
            current = conn.execute(
                "SELECT status FROM research_tasks WHERE task_id=?",
                (task["task_id"],),
            ).fetchone()
            another = conn.execute(
                "SELECT attempt_id FROM research_attempts WHERE status='running' LIMIT 1"
            ).fetchone()
            if not current or current["status"] != "ready" or another:
                self.repo.commit_close(conn)
                return None
            row = conn.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 AS n FROM research_attempts WHERE task_id=?", (task["task_id"],)).fetchone()
            attempt_no = int(row["n"])
            conn.execute(
                "INSERT INTO research_attempts (attempt_id, goal_id, task_id, status, attempt_no, trigger, usage_json, lease_owner, lease_expires_at, created_at, started_at) VALUES (?, ?, ?, 'running', ?, 'scheduler', ?, ?, ?, ?, ?)",
                (attempt_id, goal_id, task["task_id"], attempt_no, dumps({}), os.uname().nodename, self._lease_expiry(), now, now),
            )
            conn.execute(
                "UPDATE research_tasks SET status='running', current_attempt_id=?, lease_owner=?, lease_expires_at=?, started_at=COALESCE(started_at, ?), updated_at=? WHERE task_id=?",
                (attempt_id, os.uname().nodename, self._lease_expiry(), now, now, task["task_id"]),
            )
            self.repo.commit_close(conn)
        except Exception:
            conn.rollback()
            self.repo.commit_close(conn)
            raise
        self._emit(goal_id, "attempt_start", {"attempt_id": attempt_id, "task_id": task["task_id"]}, task_id=task["task_id"], attempt_id=attempt_id)
        return attempt_id

    def _finish_attempt(self, goal_id: str, task_id: str, attempt_id: str, status: str, result: dict[str, Any], error: str | None = None) -> None:
        now = utc_now()
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                "UPDATE research_attempts SET status=?, result_json=?, error=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE attempt_id=?",
                (status, dumps(result), error, now, attempt_id),
            )
            conn.execute(
                "UPDATE research_tasks SET status=?, updated_at=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE task_id=?",
                (status, now, now, task_id),
            )
        self._emit(goal_id, "attempt_end", {"attempt_id": attempt_id, "task_id": task_id, "status": status, "error": error}, task_id=task_id, attempt_id=attempt_id)

    def _schedule_transient_retry(
        self,
        goal_id: str,
        task_id: str,
        attempt_id: str,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        if status != "incomplete" or not self._is_transient_result(result):
            return False
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            attempt = conn.execute(
                "SELECT attempt_no FROM research_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            goal = conn.execute(
                "SELECT status FROM research_goals WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if (
                not attempt
                or int(attempt["attempt_no"]) >= 3
                or not goal
                or goal["status"] != "running"
            ):
                return False
            now = utc_now()
            conn.execute(
                """
                UPDATE research_tasks
                SET status='ready', finished_at=NULL, updated_at=?
                WHERE task_id=? AND current_attempt_id=?
                """,
                (now, task_id, attempt_id),
            )
        self._emit(
            goal_id,
            "task_update",
            {
                "task_id": task_id,
                "status": "ready",
                "operation": "retry_scheduled",
                "previous_attempt_id": attempt_id,
            },
            task_id=task_id,
            attempt_id=attempt_id,
        )
        return True

    @staticmethod
    def _is_transient_result(result: dict[str, Any]) -> bool:
        text = " ".join(
            str(item) for item in (result.get("warnings") or [])
        ).lower()
        non_retryable = (
            "api key",
            "credential",
            "unauthorized",
            "forbidden",
            "401",
            "403",
            "schema",
            "path",
            "security",
        )
        if any(marker in text for marker in non_retryable):
            return False
        return any(
            marker in text
            for marker in (
                "timeout",
                "temporar",
                "connection",
                "network",
                "rate limit",
                "provider stream",
            )
        )

    def _next_ready_task(self, goal_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute("SELECT * FROM research_tasks WHERE goal_id=? AND status='ready' ORDER BY sequence_index LIMIT 1", (goal_id,)).fetchall()
        return self._row_task(rows[0]) if rows else None

    def _promote_ready_tasks(self, goal_id: str) -> None:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            pending = conn.execute("SELECT * FROM research_tasks WHERE goal_id=? AND status='pending' ORDER BY sequence_index", (goal_id,)).fetchall()
            now = utc_now()
            for row in pending:
                deps = conn.execute("SELECT d.required, t.status FROM research_task_dependencies d JOIN research_tasks t ON t.task_id=d.depends_on_task_id WHERE d.goal_id=? AND d.task_id=?", (goal_id, row["task_id"])).fetchall()
                if all(str(dep["status"]) == "succeeded" or not bool(dep["required"]) for dep in deps):
                    conn.execute("UPDATE research_tasks SET status='ready', updated_at=? WHERE task_id=?", (now, row["task_id"]))
                    self.repo.append_event(conn, goal_id=goal_id, event_type="task_ready", task_id=str(row["task_id"]), payload={"task_id": row["task_id"]})
                elif any(
                    bool(dep["required"])
                    and str(dep["status"]) in TERMINAL_TASK - {"succeeded"}
                    for dep in deps
                ):
                    conn.execute(
                        "UPDATE research_tasks SET status='blocked', updated_at=?, finished_at=? WHERE task_id=?",
                        (now, now, row["task_id"]),
                    )
                    self.repo.append_event(
                        conn,
                        goal_id=goal_id,
                        event_type="task_end",
                        task_id=str(row["task_id"]),
                        payload={
                            "task_id": row["task_id"],
                            "status": "blocked",
                            "reason": "required_dependency_not_succeeded",
                        },
                    )
        self.repo.mirror_unmirrored(goal_id)

    def _register_task_evidence(self, goal_id: str, task: dict[str, Any], attempt_id: str, validator: str, title: str, uri: str | None = None) -> None:
        criterion = self._criterion_for_validator(goal_id, validator) or self._first_criterion(goal_id)
        if not criterion:
            return
        evidence = Evidence(
            evidence_id=new_id("ev"),
            goal_id=goal_id,
            criterion_id=criterion["criterion_id"],
            task_id=task["task_id"],
            attempt_id=attempt_id,
            source_tool="research_deterministic_runner",
            source_tier="deterministic_calculation" if validator in {"snapshot", "metric_ledger", "delivery_audit"} else "reputable_secondary",
            uri=uri,
            data_as_of=self._goal_as_of(goal_id),
            method=validator,
            scope=title,
            hash=hashlib.sha256(f"{goal_id}:{task['task_id']}:{attempt_id}:{title}".encode()).hexdigest(),
            verified=True,
            check_count=1,
            metadata={
                "title": title,
                "validator": validator,
                "snapshot_id": self._snapshot_id(goal_id),
            },
        )
        self.repo.register_evidence(evidence)
        self.repo.verify_evidence(evidence.evidence_id, checker="research_deterministic_runner")

    def _capture_task_result(
        self,
        goal_id: str,
        task: dict[str, Any],
        attempt_id: str,
        result: dict[str, Any],
    ) -> None:
        """Persist structured task output and only tool-backed evidence."""
        artifact = self.artifacts.put_bytes(
            goal_id=goal_id,
            task_id=task["task_id"],
            attempt_id=attempt_id,
            kind="task_result",
            name=f"{task['kind']}-{attempt_id}.json",
            data=dumps(
                {
                    key: value
                    for key, value in result.items()
                    if not key.startswith("_")
                }
            ).encode("utf-8"),
            media_type="application/json",
            metadata={
                "task_kind": task["kind"],
                "status": result.get("status"),
                "snapshot_id": self._snapshot_id(goal_id),
            },
        )
        captured_ids: list[str] = []
        tool_artifact_ids: list[str] = []
        criterion = self._criterion_for_validator(
            goal_id,
            "source_coverage" if task["kind"] == "collect_sources" else "evidence",
        )
        for source in result.get("_tool_evidence") or []:
            if not isinstance(source, dict) or not source.get("url"):
                continue
            evidence_id = new_id("ev")
            source_payload = stable_json(source)
            evidence = Evidence(
                evidence_id=evidence_id,
                goal_id=goal_id,
                criterion_id=criterion["criterion_id"] if criterion else None,
                task_id=task["task_id"],
                attempt_id=attempt_id,
                run_id=result.get("run_id"),
                tool_call_id=source.get("tool_event_id"),
                source_tool=str(source.get("tool_name") or "research_tool"),
                provider=str(source.get("provider") or "") or None,
                uri=str(source["url"]),
                artifact_id=artifact["artifact_id"],
                data_as_of=str(
                    source.get("retrievedAt")
                    or source.get("data_as_of")
                    or self._goal_as_of(goal_id)
                ),
                method="successful_tool_result",
                scope=str(source.get("title") or task["title"]),
                hash=hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
                source_tier=str(
                    source.get("sourceTier")
                    or source.get("source_tier")
                    or "unknown"
                ),
                caveat=source.get("caveat"),
                metadata={
                    "title": source.get("title"),
                    "used_for": source.get("usedFor"),
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(
                evidence_id,
                checker="tool_result_integrity",
                detail={
                    "tool_event_id": source.get("tool_event_id"),
                    "object_hash": artifact["object_hash"],
                },
            )
            captured_ids.append(evidence_id)

        validator_by_task = {
            "compute_temperature": "metric_ledger",
            "theme_consensus": "theme_consensus",
            "risk_radar": "risk_radar",
            "analyst_cards": "precision_cards",
        }
        local_criterion = self._criterion_for_validator(
            goal_id,
            validator_by_task.get(task["kind"], "evidence"),
        )
        for tool_result in result.get("_tool_results") or []:
            if not isinstance(tool_result, dict):
                continue
            tool_name = str(tool_result.get("tool_name") or "research_tool")
            raw_payload = tool_result.get("result")
            if (
                tool_name in {
                    "research_search",
                    "research_fetch",
                    "research_bundle",
                }
                or not isinstance(raw_payload, dict)
                or raw_payload.get("error")
                or raw_payload.get("is_error")
            ):
                continue
            encoded = stable_json(raw_payload).encode("utf-8")
            tool_artifact = self.artifacts.put_bytes(
                goal_id=goal_id,
                task_id=task["task_id"],
                attempt_id=attempt_id,
                kind="tool_result",
                name=f"{tool_name}-{tool_result.get('tool_call_id') or new_id('call')}.json",
                data=encoded,
                media_type="application/json",
                metadata={
                    "tool_name": tool_name,
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            tool_artifact_ids.append(tool_artifact["artifact_id"])
            evidence_id = new_id("ev")
            numeric_values = self._numeric_values(raw_payload)
            evidence = Evidence(
                evidence_id=evidence_id,
                goal_id=goal_id,
                criterion_id=(
                    local_criterion["criterion_id"] if local_criterion else None
                ),
                task_id=task["task_id"],
                attempt_id=attempt_id,
                run_id=result.get("run_id"),
                tool_call_id=str(
                    tool_result.get("tool_call_id") or ""
                )
                or None,
                source_tool=tool_name,
                uri=(
                    f"kss-tool://{tool_name}/"
                    f"{tool_result.get('tool_call_id') or evidence_id}"
                ),
                artifact_id=tool_artifact["artifact_id"],
                data_as_of=self._goal_as_of(goal_id),
                method="successful_tool_result",
                scope=task["title"],
                hash=hashlib.sha256(encoded).hexdigest(),
                source_tier="deterministic_calculation",
                metadata={
                    "title": f"{task['title']} · {tool_name}",
                    "numeric_values": numeric_values,
                    "snapshot_id": self._snapshot_id(goal_id),
                },
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(
                evidence_id,
                checker="tool_result_integrity",
                detail={"object_hash": tool_artifact["object_hash"]},
            )
            captured_ids.append(evidence_id)

        ledger_evidence = self.repo.evidence_for_goal(goal_id)
        existing_ids = {item["evidence_id"] for item in ledger_evidence}
        reference_map: dict[str, str] = {}
        for item in ledger_evidence:
            evidence_id = str(item["evidence_id"])
            for reference in (
                evidence_id,
                item.get("uri"),
                item.get("tool_call_id"),
                item.get("source_tool"),
            ):
                if reference:
                    reference_map[str(reference)] = evidence_id

        normalized_claims: list[Any] = []
        for raw_claim in result.get("claims") or []:
            if isinstance(raw_claim, str):
                content = raw_claim
                requested_refs: list[str] = []
                confidence = None
                normalized_claim: Any = raw_claim
            elif isinstance(raw_claim, dict):
                content = str(raw_claim.get("content") or raw_claim.get("text") or "")
                requested_refs = [
                    str(value) for value in raw_claim.get("evidence_refs") or []
                ]
                confidence = raw_claim.get("confidence")
                normalized_claim = dict(raw_claim)
            else:
                continue
            resolved = list(
                dict.fromkeys(
                    reference_map.get(value, value)
                    for value in requested_refs
                    if reference_map.get(value, value) in existing_ids
                )
            )
            if isinstance(normalized_claim, dict):
                normalized_claim["evidence_refs"] = resolved
                metric = normalized_claim.get("metric")
                if isinstance(metric, dict):
                    normalized_metric = dict(metric)
                    normalized_metric["input_refs"] = list(
                        dict.fromkeys(
                            reference_map.get(str(value), str(value))
                            for value in metric.get("input_refs") or []
                            if reference_map.get(str(value), str(value))
                            in existing_ids
                        )
                    )
                    normalized_claim["metric"] = normalized_metric
            normalized_claims.append(normalized_claim)
            if not content.strip():
                continue
            self.repo.register_claim(
                Claim(
                    claim_id=new_id("claim"),
                    goal_id=goal_id,
                    content=content,
                    status="supported" if resolved else "proposed",
                    task_id=task["task_id"],
                    confidence=confidence,
                    evidence_ids=resolved,
                )
            )

        result["claims"] = normalized_claims
        result.setdefault("artifact_refs", []).extend(
            [artifact["artifact_id"], *tool_artifact_ids]
        )
        result.setdefault("evidence_refs", []).extend(captured_ids)

    @staticmethod
    def _numeric_values(value: Any, *, limit: int = 200) -> list[float]:
        values: list[float] = []

        def visit(item: Any) -> None:
            if len(values) >= limit:
                return
            if isinstance(item, bool) or item is None:
                return
            if isinstance(item, (int, float)):
                number = float(item)
                if math.isfinite(number):
                    values.append(number)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                for child in item.values():
                    visit(child)

        visit(value)
        return values

    def _dependency_summaries(self, task_id: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT t.task_id, t.kind, t.status, a.result_json
                FROM research_task_dependencies d
                JOIN research_tasks t ON t.task_id=d.depends_on_task_id
                LEFT JOIN research_attempts a ON a.attempt_id=t.current_attempt_id
                WHERE d.task_id=?
                ORDER BY t.sequence_index
                """,
                (task_id,),
            ).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "kind": row["kind"],
                "status": row["status"],
                "result": loads(row["result_json"], {}),
            }
            for row in rows
        ]

    def _record_usage(self, goal_id: str, usage: dict[str, Any]) -> None:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                "SELECT usage_json, started_at FROM research_goals WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if not row:
                return
            current = loads(row["usage_json"], {})
            tokens = int(
                usage.get("total_tokens")
                or usage.get("provider_tokens")
                or (
                    int(usage.get("input_tokens") or 0)
                    + int(usage.get("output_tokens") or 0)
                )
            )
            current["provider_tokens"] = int(
                current.get("provider_tokens") or 0
            ) + tokens
            current["nodes"] = int(current.get("nodes") or 0) + 1
            if row["started_at"]:
                try:
                    started = datetime.fromisoformat(str(row["started_at"]))
                    current["seconds"] = max(
                        0,
                        int(
                            (
                                datetime.now(timezone.utc)
                                - started.astimezone(timezone.utc)
                            ).total_seconds()
                        ),
                    )
                except ValueError:
                    pass
            conn.execute(
                "UPDATE research_goals SET usage_json=?, updated_at=? WHERE goal_id=?",
                (dumps(current), utc_now(), goal_id),
            )

    def _budget_exceeded(self, goal: dict[str, Any]) -> bool:
        budget = goal.get("budget") or {}
        usage = goal.get("usage") or {}
        checks = (
            ("max_nodes", "nodes"),
            ("max_seconds", "seconds"),
            ("max_provider_tokens", "provider_tokens"),
        )
        return any(
            int(budget.get(limit) or 0) > 0
            and int(usage.get(used) or 0) >= int(budget[limit])
            for limit, used in checks
        )

    def _active_attempt(self) -> tuple[str | None, str | None]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT goal_id, attempt_id
                FROM research_attempts
                WHERE status='running'
                ORDER BY started_at
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None, None
        return str(row["goal_id"]), str(row["attempt_id"])

    def _criterion_for_validator(self, goal_id: str, validator: str) -> dict[str, Any] | None:
        goal = self.repo.get_goal(goal_id)
        for criterion in (goal or {}).get("criteria", []):
            if criterion.get("validator") == validator:
                return criterion
        return None

    def _first_criterion(self, goal_id: str) -> dict[str, Any] | None:
        criteria = (self.repo.get_goal(goal_id) or {}).get("criteria", [])
        return criteria[0] if criteria else None

    def _goal_as_of(self, goal_id: str) -> str:
        goal = self.repo.get_goal(goal_id) or {}
        snapshot = goal.get("snapshot") or {}
        return str(snapshot.get("as_of") or date.today().isoformat())

    def _snapshot_id(self, goal_id: str) -> str:
        goal = self.repo.get_goal(goal_id) or {}
        return str((goal.get("snapshot") or {}).get("snapshot_id") or "")

    # ------------------------------------------------------------------
    # Export/publication
    # ------------------------------------------------------------------

    def _export(self, *, goal_id: str | None, artifact_id: str | None, payload: dict[str, Any], require_pass: bool, event: str) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        artifact = self._resolve_artifact(goal_id, artifact_id)
        if not artifact:
            return {"protocol_version": 1, "ok": False, "error": "artifact_not_found", "goal": goal_id, "goal_id": goal_id}
        if require_pass and not self._latest_audit_pass(goal_id):
            return {"protocol_version": 1, "ok": False, "error": "audit_not_passed", "goal": goal_id, "goal_id": goal_id}
        if require_pass:
            goal = self.repo.get_goal(goal_id) or {}
            artifact_metadata = artifact.get("metadata") or {}
            artifact_snapshot = str(
                artifact_metadata.get("snapshot_id") or ""
            )
            current_snapshot = str(
                (goal.get("snapshot") or {}).get("snapshot_id") or ""
            )
            if (
                goal.get("status") != "completed"
                or not artifact_snapshot
                or artifact_snapshot != current_snapshot
                or bool(artifact_metadata.get("draft"))
                or artifact_metadata.get("audit_status") != "pass"
            ):
                return {
                    "protocol_version": 1,
                    "ok": False,
                    "error": "artifact_not_current_completed_snapshot",
                    "goal": goal_id,
                    "goal_id": goal_id,
                }
        destination = payload.get("destination")
        if not destination:
            destination = str(self.state_root / "storage" / "agent" / "research" / "exports" / goal_id / artifact["name"])
        dest = self._safe_destination(destination)
        result = self.artifacts.export_object(
            object_hash=artifact["object_hash"],
            destination=dest,
            allow_overwrite=bool(payload.get("overwrite", False)),
        )
        if require_pass:
            pub_id = new_id("pub")
            with connect(self.db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    "INSERT INTO research_publications (publication_id, goal_id, artifact_id, destination, object_hash, status, created_at) VALUES (?, ?, ?, ?, ?, 'published', ?)",
                    (pub_id, goal_id, artifact["artifact_id"], result["destination"], result["sha256"], utc_now()),
                )
            self._emit(goal_id, "artifact_ready", {"publication_id": pub_id, "destination": result["destination"], "sha256": result["sha256"]})
        return {"protocol_version": 1, "ok": True, "event": event, "goal": goal_id, "goal_id": goal_id, "artifact": self._wire_artifact(artifact), **result}

    def _resolve_artifact(self, goal_id: str, artifact_id: str | None) -> dict[str, Any] | None:
        artifacts = self.artifacts.list_goal(goal_id)
        if artifact_id:
            return next((a for a in artifacts if a["artifact_id"] == artifact_id or a.get("id") == artifact_id), None)
        html_artifacts = [a for a in artifacts if a.get("kind") == "report_html"]
        return html_artifacts[-1] if html_artifacts else (artifacts[-1] if artifacts else None)

    def _safe_destination(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.state_root / path
        resolved = path.resolve()
        allowed = [self.state_root.resolve()]
        home = Path.home().resolve()
        allowed.extend(home / name for name in ALLOWED_EXPORT_ROOTS)
        if not any(resolved == root or root in resolved.parents for root in allowed):
            raise ValueError("destination_outside_allowed_roots")
        if resolved.exists() and resolved.is_dir():
            raise IsADirectoryError(str(resolved))
        return resolved

    def _latest_audit_pass(self, goal_id: str) -> bool:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute(
                """
                SELECT status
                FROM research_audits
                WHERE goal_id=?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (goal_id,),
            ).fetchone()
        return bool(row and row["status"] == "pass")

    def _latest_compiler_audit(self, goal_id: str) -> dict[str, Any] | None:
        current_snapshot = self._snapshot_id(goal_id)
        artifacts = [
            artifact
            for artifact in self.artifacts.list_goal(goal_id)
            if artifact.get("kind") == "audit_json"
            and str((artifact.get("metadata") or {}).get("snapshot_id") or "")
            == current_snapshot
        ]
        if not artifacts:
            return None
        try:
            value = json.loads(
                self.artifacts.read_bytes(str(artifacts[-1]["object_hash"]))
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    # ------------------------------------------------------------------
    # Wire helpers
    # ------------------------------------------------------------------

    def _emit(self, goal_id: str, event_type: str, payload: dict[str, Any], task_id: str | None = None, attempt_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        conn = self.repo.transaction()
        try:
            frame = self.repo.append_event(conn, goal_id=goal_id, event_type=event_type, task_id=task_id, attempt_id=attempt_id, run_id=run_id, payload=payload)
            self.repo.commit_close(conn)
            self.repo.mirror_unmirrored(goal_id)
            return frame
        except Exception:
            conn.rollback()
            self.repo.commit_close(conn)
            raise

    def _set_goal(self, goal_id: str | None, status: str, event: str) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        if status in {"paused", "cancelled"}:
            abort = getattr(self.task_runner, "abort", None)
            if callable(abort):
                abort(
                    goal_id,
                    "research_cancelled"
                    if status == "cancelled"
                    else "research_paused",
                )
        self.repo.update_goal_status(goal_id, status)
        return {
            "protocol_version": 1,
            "ok": True,
            "event": event,
            "goal": self._wire_goal(self.repo.get_goal(goal_id) or {}),
            "goal_id": goal_id,
        }

    def _summary(self, goal: dict[str, Any]) -> dict[str, Any]:
        tasks = goal.get("tasks") or []
        done = sum(1 for task in tasks if task.get("status") == "succeeded")
        return {
            "goal_id": goal["goal_id"],
            "id": goal["goal_id"],
            "session_id": goal.get("session_id"),
            "profile_id": goal["profile_id"],
            "objective": goal["objective"],
            "status": goal["status"],
            "progress": (done / len(tasks)) if tasks else 0.0,
            "terminal_reason": goal.get("termination_reason"),
            "created_at": goal.get("created_at"),
            "updated_at": goal.get("updated_at"),
        }

    def _wire_goal(self, goal: dict[str, Any]) -> dict[str, Any]:
        if not goal:
            return {}
        evidence = self.repo.evidence_for_goal(goal["goal_id"])
        claims = self.repo.claims_for_goal(goal["goal_id"])
        return {
            **self._summary(goal),
            "inputs": goal.get("inputs") or {},
            "snapshot": goal.get("snapshot") or {},
            "budget": goal.get("budget") or {},
            "usage": goal.get("usage") or {},
            "criteria": goal.get("criteria") or [],
            "tasks": goal.get("tasks") or [],
            "artifacts": [self._wire_artifact(a) for a in goal.get("artifacts") or []],
            "evidence": [self._wire_evidence(item) for item in evidence],
            "claims": claims,
            "audit": self._wire_audits(goal["goal_id"]),
            "events": self.repo.list_events(goal["goal_id"], 0)[-200:],
        }

    def _wire_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        metadata = evidence.get("metadata") or {}
        return {
            "evidence_id": evidence["evidence_id"],
            "id": evidence["evidence_id"],
            "criterion_id": evidence.get("criterion_id"),
            "title": metadata.get("title")
            or evidence.get("scope")
            or evidence.get("source_tool")
            or "研究证据",
            "source": evidence.get("source_tool"),
            "source_tier": evidence.get("source_tier"),
            "url": evidence.get("uri"),
            "uri": evidence.get("uri"),
            "status": "verified" if evidence.get("verified") else "unverified",
            "verified": bool(evidence.get("verified")),
            "data_as_of": evidence.get("data_as_of"),
            "method": evidence.get("method"),
            "hash": evidence.get("hash"),
            "caveat": evidence.get("caveat"),
            "created_at": evidence.get("created_at"),
        }

    def _wire_audits(self, goal_id: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT audit_id, status, coverage_json, findings_json, created_at
                FROM research_audits
                WHERE goal_id=?
                ORDER BY created_at
                """,
                (goal_id,),
            ).fetchall()
        return [
            {
                "event_id": row["audit_id"],
                "id": row["audit_id"],
                "type": "research_audit",
                "status": row["status"],
                "timestamp": row["created_at"],
                "message": (
                    "审计通过"
                    if row["status"] == "pass"
                    else f"审计阻断：{len(loads(row['findings_json'], []))} 项"
                ),
                "coverage": loads(row["coverage_json"], {}),
                "findings": loads(row["findings_json"], []),
            }
            for row in rows
        ]

    def _wire_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        metadata = artifact.get("metadata") or {}
        wire = {
            "artifact_id": artifact["artifact_id"],
            "id": artifact["artifact_id"],
            "kind": artifact["kind"],
            "logical_name": metadata.get("logical_name") or artifact.get("name"),
            "name": artifact.get("name"),
            "media_type": artifact.get("media_type"),
            "size_bytes": artifact.get("size_bytes"),
            "sha256": artifact.get("object_hash"),
            "object_hash": artifact.get("object_hash"),
            "created_at": artifact.get("created_at"),
            "audit_status": metadata.get("audit_status"),
            "draft": metadata.get("draft"),
        }
        if artifact.get("media_type") == "text/html; charset=utf-8" and int(artifact.get("size_bytes") or 0) <= 2_000_000:
            try:
                wire["content"] = self.artifacts.read_bytes(str(artifact["object_hash"])).decode("utf-8")
            except Exception:
                pass
        return wire

    def _row_task(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["required"] = bool(item["required"])
        item["payload"] = loads(item.pop("payload_json"), {})
        return item

    def _snapshot(self, *, profile_id: str, inputs: dict[str, Any], refresh_of: str | None = None) -> dict[str, Any]:
        as_of = str(inputs.get("as_of") or date.today().isoformat())
        frozen_inputs = dict(inputs)
        if profile_id == "investment-weekly-v3":
            match = DATE_RANGE_RE.fullmatch(str(inputs.get("date_range") or ""))
            if match and not frozen_inputs.get("trading_calendar"):
                start = date.fromisoformat(match.group("start"))
                end = date.fromisoformat(match.group("end"))
                frozen_inputs["trading_calendar"] = [
                    (start + timedelta(days=offset)).isoformat()
                    for offset in range((end - start).days + 1)
                    if (start + timedelta(days=offset)).weekday() < 5
                ]
        raw = stable_json({"profile_id": profile_id, "inputs": frozen_inputs, "as_of": as_of, "refresh_of": refresh_of})
        return {
            "snapshot_id": "snap_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
            "profile_id": profile_id,
            "inputs": frozen_inputs,
            "as_of": as_of,
            "created_at": utc_now(),
            "refresh_of": refresh_of,
        }

    def _is_fresh(
        self,
        evidence: dict[str, Any],
        criterion: dict[str, Any],
        *,
        reference_as_of: str | None = None,
    ) -> bool:
        freshness_days = criterion.get("freshness_days")
        if not freshness_days:
            return True
        raw = evidence.get("data_as_of")
        if not raw:
            return False
        try:
            data_date = datetime.fromisoformat(
                str(raw).replace("Z", "+00:00")
            ).date()
            reference_date = date.fromisoformat(
                str(reference_as_of or date.today().isoformat())[:10]
            )
        except ValueError:
            return False
        age = (reference_date - data_date).days
        return 0 <= age <= int(freshness_days)

    def _validate_inputs(
        self,
        profile_id: str,
        inputs: dict[str, Any],
    ) -> list[dict[str, str]]:
        if profile_id != "investment-weekly-v3":
            return []
        findings: list[dict[str, str]] = []
        date_range = str(inputs.get("date_range") or "")
        match = DATE_RANGE_RE.fullmatch(date_range)
        if not match:
            findings.append(
                {
                    "field": "date_range",
                    "reason": "expected_YYYY-MM-DD_to_YYYY-MM-DD",
                }
            )
        else:
            try:
                start = date.fromisoformat(match.group("start"))
                end = date.fromisoformat(match.group("end"))
                if start > end:
                    findings.append(
                        {"field": "date_range", "reason": "start_after_end"}
                    )
            except ValueError:
                findings.append(
                    {"field": "date_range", "reason": "invalid_calendar_date"}
                )
        raw_as_of = str(inputs.get("as_of") or "")
        try:
            as_of = date.fromisoformat(raw_as_of)
        except ValueError:
            findings.append({"field": "as_of", "reason": "invalid_iso_date"})
        else:
            if match:
                try:
                    end = date.fromisoformat(match.group("end"))
                    if as_of < end:
                        findings.append(
                            {
                                "field": "as_of",
                                "reason": "before_date_range_end",
                            }
                        )
                except ValueError:
                    pass
        return findings

    def _lease_expiry(self) -> str:
        return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 900, timezone.utc).isoformat(timespec="seconds")
