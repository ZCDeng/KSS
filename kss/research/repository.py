"""SQLite repository and durable event mirror for Deep Research."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from kss.storage.db import connect, ensure_schema

from .models import Claim, Evidence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


class ResearchRepository:
    """Persistence boundary for research goals, DAG state and events."""

    def __init__(self, *, db_path: Path, research_root: Path) -> None:
        self.db_path = Path(db_path)
        self.research_root = Path(research_root)
        self.events_root = self.research_root / "goals"
        self.research_root.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as conn:
            ensure_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(conn)
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        conn.commit()
        conn.close()

    def transaction(self) -> sqlite3.Connection:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def commit_close(self, conn: sqlite3.Connection) -> None:
        self._close(conn)

    def next_sequence(self, conn: sqlite3.Connection, goal_id: str) -> int:
        row = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS seq FROM research_events WHERE goal_id=?", (goal_id,)).fetchone()
        return int(row["seq"])

    def append_event(
        self,
        conn: sqlite3.Connection,
        *,
        goal_id: str,
        event_type: str,
        payload: dict[str, Any],
        task_id: str | None = None,
        attempt_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        # Acquire SQLite's write reservation before reading MAX(sequence).
        # This serializes event allocation across sidecar threads/processes.
        conn.execute(
            "UPDATE research_goals SET updated_at=updated_at WHERE goal_id=?",
            (goal_id,),
        )
        sequence = self.next_sequence(conn, goal_id)
        event_id = new_id("evt")
        frame = {
            "protocol_version": 1,
            "event_id": event_id,
            "goal_id": goal_id,
            "goal": goal_id,
            "sequence": sequence,
            "timestamp": now,
            "event": event_type,
            "type": event_type,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            **payload,
        }
        conn.execute(
            """
            INSERT INTO research_events (
                event_id, goal_id, sequence, event_type, task_id, attempt_id, run_id,
                payload_json, mirrored_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (event_id, goal_id, sequence, event_type, task_id, attempt_id, run_id, dumps(frame), now),
        )
        return frame

    def mirror_unmirrored(self, goal_id: str | None = None) -> None:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            if goal_id:
                rows = conn.execute(
                    "SELECT * FROM research_events WHERE goal_id=? AND mirrored_at IS NULL ORDER BY sequence",
                    (goal_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM research_events WHERE mirrored_at IS NULL ORDER BY goal_id, sequence"
                ).fetchall()
            by_goal: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                by_goal.setdefault(str(row["goal_id"]), []).append(row)
            mirrored = utc_now()
            for gid, items in by_goal.items():
                log_path = self.events_root / gid / "events.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    for row in items:
                        f.write(str(row["payload_json"]) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                    for row in items:
                        conn.execute(
                            "UPDATE research_events SET mirrored_at=? WHERE event_id=?",
                            (mirrored, row["event_id"]),
                        )

    def create_goal(
        self,
        *,
        goal_id: str,
        session_id: str | None,
        profile_id: str,
        objective: str,
        inputs: dict[str, Any],
        snapshot: dict[str, Any],
        budget: dict[str, Any],
        criteria: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        dependencies: list[tuple[str, str, bool]],
        client_request_id: str | None,
    ) -> dict[str, Any]:
        conn = self.transaction()
        try:
            if client_request_id:
                existing = conn.execute(
                    "SELECT goal_id FROM research_goals WHERE client_request_id=?",
                    (client_request_id,),
                ).fetchone()
                if existing:
                    self.commit_close(conn)
                    return self.get_goal(str(existing["goal_id"])) or {}
            now = utc_now()
            conn.execute(
                """
                INSERT INTO research_goals (
                    goal_id, session_id, profile_id, objective, status, inputs_json,
                    snapshot_json, budget_json, usage_json, termination_reason,
                    client_request_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    goal_id,
                    session_id,
                    profile_id,
                    objective,
                    dumps(inputs),
                    dumps(snapshot),
                    dumps(budget),
                    dumps({"provider_tokens": 0, "nodes": 0, "seconds": 0}),
                    client_request_id,
                    now,
                    now,
                ),
            )
            for item in criteria:
                conn.execute(
                    """
                    INSERT INTO research_criteria (
                        criterion_id, goal_id, label, required, min_verified_evidence,
                        allowed_tiers_json, freshness_days, validator, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        item["criterion_id"],
                        goal_id,
                        item["label"],
                        _bool(item.get("required", True)),
                        int(item.get("min_verified_evidence") or 1),
                        dumps(item.get("allowed_tiers") or ["official_or_primary", "reputable_secondary"]),
                        item.get("freshness_days"),
                        item.get("validator"),
                        now,
                        now,
                    ),
                )
            for task in tasks:
                conn.execute(
                    """
                    INSERT INTO research_tasks (
                        task_id, goal_id, profile_id, kind, title, status, required, sequence_index,
                        payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task["task_id"],
                        goal_id,
                        profile_id,
                        task["kind"],
                        task["title"],
                        task["status"],
                        _bool(task["required"]),
                        int(task["sequence_index"]),
                        dumps(task.get("payload") or {}),
                        now,
                        now,
                    ),
                )
            for task_id, dep_id, required in dependencies:
                conn.execute(
                    "INSERT INTO research_task_dependencies (goal_id, task_id, depends_on_task_id, required) VALUES (?, ?, ?, ?)",
                    (goal_id, task_id, dep_id, _bool(required)),
                )
            self.append_event(conn, goal_id=goal_id, event_type="goal_status", payload={"status": "draft", "profile_id": profile_id})
            self.commit_close(conn)
            self.mirror_unmirrored(goal_id)
            return self.get_goal(goal_id) or {}
        except Exception:
            conn.rollback()
            self.commit_close(conn)
            raise

    def update_goal_status(self, goal_id: str, status: str, *, termination_reason: str | None = None) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            finished_at = now if status in {"completed", "cancelled", "failed", "blocked", "budget_limited", "insufficient_evidence", "needs_refresh"} else None
            conn.execute(
                """
                UPDATE research_goals
                SET status=?, termination_reason=?, updated_at=?,
                    started_at=COALESCE(started_at, CASE WHEN ?='running' THEN ? ELSE started_at END),
                    finished_at=?
                WHERE goal_id=?
                """,
                (status, termination_reason, now, status, now, finished_at, goal_id),
            )
            event = self.append_event(
                conn,
                goal_id=goal_id,
                event_type="goal_status",
                payload={"status": status, "termination_reason": termination_reason},
            )
        self.mirror_unmirrored(goal_id)
        return event

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute("SELECT * FROM research_goals WHERE goal_id=?", (goal_id,)).fetchone()
            if not row:
                return None
            goal = dict(row)
            goal.update({
                "id": goal["goal_id"],
                "inputs": loads(goal.pop("inputs_json"), {}),
                "snapshot": loads(goal.pop("snapshot_json"), {}),
                "budget": loads(goal.pop("budget_json"), {}),
                "usage": loads(goal.pop("usage_json"), {}),
                "criteria": self._criteria(conn, goal_id),
                "tasks": self._tasks(conn, goal_id),
                "artifacts": self._artifacts(conn, goal_id),
            })
            return goal

    def list_goals(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute("SELECT goal_id FROM research_goals ORDER BY created_at DESC LIMIT 1000").fetchall()
        return [g for row in rows if (g := self.get_goal(str(row["goal_id"])))]

    def _criteria(self, conn: sqlite3.Connection, goal_id: str) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT * FROM research_criteria WHERE goal_id=? ORDER BY rowid", (goal_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["required"] = bool(item["required"])
            item["allowed_tiers"] = loads(item.pop("allowed_tiers_json"), [])
            out.append(item)
        return out

    def _tasks(self, conn: sqlite3.Connection, goal_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT t.*, a.attempt_no, a.error AS attempt_error,
                   a.result_json AS attempt_result_json
            FROM research_tasks t
            LEFT JOIN research_attempts a
              ON a.attempt_id=t.current_attempt_id
            WHERE t.goal_id=?
            ORDER BY t.sequence_index
            """,
            (goal_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["required"] = bool(item["required"])
            item["payload"] = loads(item.pop("payload_json"), {})
            attempt_result = loads(item.pop("attempt_result_json"), {})
            item["attempt"] = item.pop("attempt_no")
            item["detail"] = item.pop("attempt_error") or (
                "; ".join(attempt_result.get("warnings") or [])
                if isinstance(attempt_result, dict)
                else None
            )
            out.append(item)
        return out

    def _artifacts(self, conn: sqlite3.Connection, goal_id: str) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT * FROM research_artifacts WHERE goal_id=? ORDER BY created_at", (goal_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = loads(item.pop("metadata_json"), {})
            item["id"] = item["artifact_id"]
            out.append(item)
        return out

    def register_evidence(self, evidence: Evidence) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            conn.execute(
                """
                INSERT INTO research_evidence (
                    evidence_id, goal_id, criterion_id, task_id, attempt_id, run_id, tool_call_id,
                    source_tool, provider, uri, artifact_id, data_as_of, method, scope, hash,
                    source_tier, caveat, verified, check_count, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.goal_id,
                    evidence.criterion_id,
                    evidence.task_id,
                    evidence.attempt_id,
                    evidence.run_id,
                    evidence.tool_call_id,
                    evidence.source_tool,
                    evidence.provider,
                    evidence.uri,
                    evidence.artifact_id,
                    evidence.data_as_of,
                    evidence.method,
                    evidence.scope,
                    evidence.hash,
                    evidence.source_tier,
                    evidence.caveat,
                    0,
                    0,
                    dumps(evidence.metadata),
                    now,
                ),
            )
            self.append_event(
                conn,
                goal_id=evidence.goal_id,
                event_type="evidence_registered",
                task_id=evidence.task_id,
                attempt_id=evidence.attempt_id,
                run_id=evidence.run_id,
                payload={"evidence": evidence.to_wire()},
            )
        self.mirror_unmirrored(evidence.goal_id)
        return evidence.to_wire()

    def verify_evidence(self, evidence_id: str, *, checker: str = "manual", detail: dict[str, Any] | None = None) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            row = conn.execute("SELECT goal_id FROM research_evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            if not row:
                raise ValueError(f"unknown evidence: {evidence_id}")
            goal_id = str(row["goal_id"])
            now = utc_now()
            check_id = new_id("check")
            conn.execute(
                "INSERT INTO research_evidence_checks (check_id, evidence_id, goal_id, status, checker, detail_json, created_at) VALUES (?, ?, ?, 'passed', ?, ?, ?)",
                (check_id, evidence_id, goal_id, checker, dumps(detail or {}), now),
            )
            event = self.append_event(
                conn,
                goal_id=goal_id,
                event_type="evidence_verified",
                payload={"evidence_id": evidence_id, "check_id": check_id, "checker": checker},
            )
        self.mirror_unmirrored(goal_id)
        return event

    def register_claim(self, claim: Claim) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            now = utc_now()
            conn.execute(
                """
                INSERT INTO research_claims (
                    claim_id, goal_id, task_id, criterion_id, content, status, confidence,
                    evidence_ids_json, contradiction_ids_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim.claim_id,
                    claim.goal_id,
                    claim.task_id,
                    claim.criterion_id,
                    claim.content,
                    claim.status,
                    claim.confidence,
                    dumps(claim.evidence_ids),
                    dumps(claim.contradiction_ids),
                    now,
                    now,
                ),
            )
            self.append_event(conn, goal_id=claim.goal_id, event_type="claim_registered", task_id=claim.task_id, payload={"claim": claim.to_wire()})
        self.mirror_unmirrored(claim.goal_id)
        return claim.to_wire()

    def list_events(self, goal_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                "SELECT payload_json FROM research_events WHERE goal_id=? AND sequence>? ORDER BY sequence",
                (goal_id, after_sequence),
            ).fetchall()
        return [loads(str(row["payload_json"]), {}) for row in rows]

    def evidence_for_goal(self, goal_id: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT e.*,
                       COALESCE(SUM(CASE WHEN c.status='passed' THEN 1 ELSE 0 END), 0)
                           AS derived_check_count
                FROM research_evidence e
                LEFT JOIN research_evidence_checks c ON c.evidence_id=e.evidence_id
                WHERE e.goal_id=?
                GROUP BY e.evidence_id
                ORDER BY e.created_at
                """,
                (goal_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["check_count"] = int(item.pop("derived_check_count"))
            item["verified"] = item["check_count"] > 0
            item["metadata"] = loads(item.pop("metadata_json"), {})
            out.append(item)
        return out

    def claims_for_goal(self, goal_id: str) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute("SELECT * FROM research_claims WHERE goal_id=? ORDER BY created_at", (goal_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["evidence_ids"] = loads(item.pop("evidence_ids_json"), [])
            item["contradiction_ids"] = loads(item.pop("contradiction_ids_json"), [])
            out.append(item)
        return out

    def mark_stale_running_attempts(self, *, lease_seconds: int = 900) -> int:
        del lease_seconds  # lease_expires_at already encodes the timeout.
        cutoff = datetime.now(timezone.utc)
        cutoff_text = cutoff.isoformat(timespec="seconds")
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute(
                "SELECT attempt_id, goal_id, task_id FROM research_attempts WHERE status='running' AND COALESCE(lease_expires_at, '') < ?",
                (cutoff_text,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE research_attempts SET status='interrupted', finished_at=?, error='lease expired' WHERE attempt_id=?",
                    (utc_now(), row["attempt_id"]),
                )
                conn.execute(
                    "UPDATE research_tasks SET status='interrupted', updated_at=? WHERE task_id=? AND status='running'",
                    (utc_now(), row["task_id"]),
                )
                conn.execute(
                    """
                    UPDATE research_goals
                    SET status='paused', termination_reason='interrupted',
                        updated_at=?
                    WHERE goal_id=? AND status='running'
                    """,
                    (utc_now(), row["goal_id"]),
                )
                self.append_event(
                    conn,
                    goal_id=str(row["goal_id"]),
                    event_type="attempt_end",
                    task_id=str(row["task_id"]),
                    attempt_id=str(row["attempt_id"]),
                    payload={"status": "interrupted", "reason": "lease_expired"},
                )
        for row in rows:
            self.mirror_unmirrored(str(row["goal_id"]))
        return len(rows)
