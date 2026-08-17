"""Write a converted 左侧机会扫描 HTML file into the investment-analysis archive."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from kss.research.left_scan import wrap_report_html
from kss.research.repository import dumps, new_id, utc_now
from kss.research.service import ResearchService
from kss.storage.db import connect, ensure_schema

PROFILE_ID = "investment-daily-v1"
CLIENT_PREFIX = "gdrive:investment-daily-v1:"


def client_request_id(trade_date: date) -> str:
    return f"{CLIENT_PREFIX}{trade_date.isoformat()}"


def _latest_html_hash(service: ResearchService, goal_id: str) -> str | None:
    artifacts = [
        item
        for item in (service.repo.get_goal(goal_id) or {}).get("artifacts") or []
        if item.get("kind") == "report_html"
    ]
    if not artifacts:
        return None
    artifacts.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return str(artifacts[0].get("object_hash") or "") or None


def _seal_goal(service: ResearchService, *, goal_id: str, artifact_id: str) -> None:
    now = utc_now()
    with connect(service.db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE research_tasks SET status='succeeded', updated_at=? WHERE goal_id=?",
            (now, goal_id),
        )
        conn.execute(
            "UPDATE research_criteria SET status='met', updated_at=? WHERE goal_id=?",
            (now, goal_id),
        )
        conn.execute(
            """
            INSERT INTO research_audits (
                audit_id, goal_id, status, coverage_json, findings_json, artifact_id, created_at
            ) VALUES (?, ?, 'pass', ?, ?, ?, ?)
            """,
            (
                new_id("audit"),
                goal_id,
                dumps({"source": "left_scan_ingest"}),
                dumps([]),
                artifact_id,
                now,
            ),
        )
    service.repo.update_goal_status(goal_id, "completed")


def ingest_left_scan_report(
    service: ResearchService,
    *,
    fragment: str,
    trade_date: date,
    source_name: str,
) -> dict[str, Any]:
    html = wrap_report_html(fragment, trade_date=trade_date, source_name=source_name)
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    created = service.create_goal(
        payload={
            "client_request_id": client_request_id(trade_date),
            "profile_id": PROFILE_ID,
            "objective": f"左侧机会扫描 · {trade_date.isoformat()}",
            "inputs": {
                "trade_date": trade_date.isoformat(),
                "as_of": trade_date.isoformat(),
            },
            "origin": "scheduled",
            "cadence": "daily",
        }
    )
    if not created.get("ok"):
        return created
    goal_id = str(created.get("goal_id") or "")
    if _latest_html_hash(service, goal_id) == digest:
        return {
            "ok": True,
            "event": "already_ingested",
            "goal_id": goal_id,
            "object_hash": digest,
            "trade_date": trade_date.isoformat(),
        }
    artifact = service.artifacts.put_bytes(
        goal_id=goal_id,
        kind="report_html",
        name="report.html",
        data=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        metadata={
            "title": f"左侧机会扫描 · {trade_date.isoformat()}",
            "draft": False,
            "audit_status": "pass",
            "logical_name": "report.html",
            "source_name": source_name,
            "ingest": "left_scan",
        },
    )
    _seal_goal(service, goal_id=goal_id, artifact_id=str(artifact["artifact_id"]))
    return {
        "ok": True,
        "event": "ingested",
        "goal_id": goal_id,
        "artifact_id": artifact["artifact_id"],
        "object_hash": artifact["object_hash"],
        "trade_date": trade_date.isoformat(),
        "source_name": source_name,
    }
