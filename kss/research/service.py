"""Research overlay service for KSSDesktop sidecar protocol."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema

from .artifacts import ArtifactStore
from .compiler import ReportCompiler, make_investment_weekly_fixture, stable_json
from .graph import get_profile as get_graph_profile
from .models import Claim, Evidence
from .profiles import get_profile as get_packaged_profile
from .profiles import list_profiles as list_packaged_profiles
from .repository import ResearchRepository, dumps, loads, new_id, utc_now
from .runner import AgentResearchTaskRunner


TERMINAL_GOAL = {"completed", "cancelled", "failed", "blocked", "budget_limited", "insufficient_evidence", "needs_refresh"}
TERMINAL_TASK = {"succeeded", "incomplete", "failed", "interrupted", "cancelled", "blocked"}
ALLOWED_EXPORT_ROOTS = ("Downloads", "Desktop", "Documents", "projects")


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
        profile = get_graph_profile(profile_id)
        objective = str(payload.get("objective") or goal or payload.get("goal") or profile.title)
        inputs = dict(payload.get("inputs") or {})
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
        self.repo.update_goal_status(goal_id, "running")
        self._emit(goal_id, "research_start", {"status": "running"})
        try:
            self._run_ready_loop(goal_id)
            settled = self.repo.get_goal(goal_id) or {}
            may_settle = settled.get("status") == "running"
            audit = self.audit_goal(
                goal_id=goal_id,
                complete_if_pass=may_settle,
            )
            self._emit(goal_id, "research_end", {"status": (self.repo.get_goal(goal_id) or {}).get("status"), "audit_status": audit.get("status")})
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

    def refresh_snapshot(self, goal_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not goal_id:
            return {"protocol_version": 1, "ok": False, "error": "goal_id_required"}
        goal = self.repo.get_goal(goal_id)
        if not goal:
            return {"protocol_version": 1, "ok": False, "error": "goal_not_found", "goal": goal_id, "goal_id": goal_id}
        snapshot = self._snapshot(profile_id=goal["profile_id"], inputs=goal.get("inputs") or {}, refresh_of=goal.get("snapshot", {}).get("snapshot_id"))
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            conn.execute("UPDATE research_goals SET snapshot_json=?, status='needs_refresh', updated_at=? WHERE goal_id=?", (dumps(snapshot), utc_now(), goal_id))
        self._emit(goal_id, "goal_status", {"status": "needs_refresh", "snapshot": snapshot})
        return {"protocol_version": 1, "ok": True, "event": "snapshot_refreshed", "goal": goal_id, "goal_id": goal_id, "snapshot": snapshot}

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
        coverage: dict[str, Any] = {"criteria": {}, "tasks": {}, "artifact_count": len(artifacts)}
        for criterion in goal.get("criteria", []):
            allowed_tiers = set(criterion.get("allowed_tiers") or [])
            verified = [
                item for item in evidence
                if item.get("criterion_id") == criterion["criterion_id"] and item.get("verified")
            ]
            allowed = [item for item in verified if not allowed_tiers or item.get("source_tier") in allowed_tiers]
            fresh = [item for item in allowed if self._is_fresh(item, criterion)]
            coverage["criteria"][criterion["criterion_id"]] = {
                "verified": len(verified),
                "allowed_tier": len(allowed),
                "fresh": len(fresh),
                "required": bool(criterion["required"]),
                "allowed_tiers": sorted(allowed_tiers),
            }
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
        status = "fail" if any(f["severity"] == "block" for f in findings) else "pass"
        audit = {"status": status, "coverage": coverage, "findings": findings, "generated_at": utc_now()}
        audit_id = new_id("audit")
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO research_audits (audit_id, goal_id, status, coverage_json, findings_json, artifact_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (audit_id, goal_id, status, dumps(coverage), dumps(findings), report_artifacts[-1]["artifact_id"] if report_artifacts else None, utc_now()),
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

    def _run_ready_loop(self, goal_id: str) -> None:
        while True:
            goal = self.repo.get_goal(goal_id) or {}
            if goal.get("status") != "running":
                return
            if self._budget_exceeded(goal):
                self.repo.update_goal_status(
                    goal_id,
                    "budget_limited",
                    termination_reason="research_budget_exhausted",
                )
                return
            task = self._next_ready_task(goal_id)
            if not task:
                return
            attempt_id = self._start_attempt(goal_id, task)
            if attempt_id is None:
                return
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
                if not self.allow_synthetic_fixture:
                    result = {
                        "status": "incomplete",
                        "claims": [],
                        "evidence_refs": [],
                        "artifact_refs": [],
                        "open_questions": ["缺少可编译的结构化 ReportDocument"],
                        "warnings": ["report_ir_missing"],
                    }
                    self._finish_attempt(
                        goal_id,
                        task["task_id"],
                        attempt_id,
                        "incomplete",
                        result,
                    )
                    self._emit(
                        goal_id,
                        "task_end",
                        {
                            "status": "incomplete",
                            "task_id": task["task_id"],
                            "reason": "report_ir_missing",
                        },
                        task_id=task["task_id"],
                        attempt_id=attempt_id,
                    )
                    return
                self._compile_report(goal_id, task, attempt_id)
            elif kind == "delivery_audit":
                # Only audit_goal may certify the delivery; a task marker or
                # assistant statement is never itself audit evidence.
                self._register_task_evidence(
                    goal_id,
                    task,
                    attempt_id,
                    "delivery_audit",
                    "交付审计证据",
                )
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
                self._emit(
                    goal_id,
                    "task_end",
                    {"status": status, "task_id": task["task_id"]},
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

    def _compile_report(self, goal_id: str, task: dict[str, Any], attempt_id: str) -> None:
        self._emit(goal_id, "compile_start", {"task_id": task["task_id"]}, task_id=task["task_id"], attempt_id=attempt_id)
        document = make_investment_weekly_fixture()
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
                metadata={"audit_status": compiled["audit"]["status"], "draft": compiled["draft"], "logical_name": name},
            )
            artifact_refs.append(artifact["artifact_id"])
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
                hash=compiled["manifest"]["object_hashes"].get("report.html"),
                metadata={"audit_status": compiled["audit"]["status"], "artifact_refs": artifact_refs},
            )
            self.repo.register_evidence(evidence)
            self.repo.verify_evidence(evidence.evidence_id, checker="delivery_compiler")
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
            metadata={"title": title, "validator": validator},
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
            metadata={"task_kind": task["kind"], "status": result.get("status")},
        )
        captured_ids: list[str] = []
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

        existing_ids = {
            item["evidence_id"] for item in self.repo.evidence_for_goal(goal_id)
        }
        for raw_claim in result.get("claims") or []:
            if isinstance(raw_claim, str):
                content = raw_claim
                requested_refs: list[str] = []
                confidence = None
            elif isinstance(raw_claim, dict):
                content = str(raw_claim.get("content") or raw_claim.get("text") or "")
                requested_refs = [
                    str(value) for value in raw_claim.get("evidence_refs") or []
                ]
                confidence = raw_claim.get("confidence")
            else:
                continue
            if not content.strip():
                continue
            resolved = [
                value for value in requested_refs if value in existing_ids
            ]
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

        result.setdefault("artifact_refs", []).append(artifact["artifact_id"])
        result.setdefault("evidence_refs", []).extend(captured_ids)

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
        return str(snapshot.get("as_of") or "2026-07-17")

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
        destination = payload.get("destination")
        if not destination:
            destination = str(self.state_root / "storage" / "agent" / "research" / "exports" / goal_id / artifact["name"])
        dest = self._safe_destination(destination)
        result = self.artifacts.export_object(object_hash=artifact["object_hash"], destination=dest, allow_overwrite=bool(payload.get("overwrite", True)))
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
            row = conn.execute("SELECT status FROM research_audits WHERE goal_id=? ORDER BY created_at DESC LIMIT 1", (goal_id,)).fetchone()
        return bool(row and row["status"] == "pass")

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
        return {
            **self._summary(goal),
            "inputs": goal.get("inputs") or {},
            "snapshot": goal.get("snapshot") or {},
            "budget": goal.get("budget") or {},
            "usage": goal.get("usage") or {},
            "criteria": goal.get("criteria") or [],
            "tasks": goal.get("tasks") or [],
            "artifacts": [self._wire_artifact(a) for a in goal.get("artifacts") or []],
            "events": self.repo.list_events(goal["goal_id"], 0)[-200:],
        }

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
        as_of = str(inputs.get("as_of") or "2026-07-17")
        raw = stable_json({"profile_id": profile_id, "inputs": inputs, "as_of": as_of, "refresh_of": refresh_of})
        return {
            "snapshot_id": "snap_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
            "profile_id": profile_id,
            "inputs": inputs,
            "as_of": as_of,
            "created_at": utc_now(),
            "refresh_of": refresh_of,
        }

    def _is_fresh(self, evidence: dict[str, Any], criterion: dict[str, Any]) -> bool:
        freshness_days = criterion.get("freshness_days")
        if not freshness_days:
            return True
        raw = evidence.get("data_as_of")
        if not raw:
            return False
        try:
            data_date = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - data_date.astimezone(timezone.utc)).days <= int(freshness_days)

    def _lease_expiry(self) -> str:
        return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 900, timezone.utc).isoformat(timespec="seconds")
