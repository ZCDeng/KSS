#!/usr/bin/env python3
"""Durable, credential-brokered runner for scheduled investment research.

This process is deliberately launched by the signed Swift scheduler helper. It
receives only a short-lived credential-broker socket and nonce, never a model
API key.  It owns the ``ResearchService`` until a goal settles so daemon worker
threads cannot disappear when a one-shot sidecar exits.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _project_imports(project_root: Path) -> None:
    value = str(project_root)
    if value not in sys.path:
        sys.path.insert(0, value)


def _latest_target_day(project_root: Path) -> str | None:
    """Return a fully covered EOD day without guessing from the weekday."""
    sentinels = ("688017", "688008", "300059", "159915")
    latest: list[str] = []
    for symbol in sentinels:
        path = project_root / f"cs_data_{symbol}.csv"
        last: list[str] | None = None
        try:
            with path.open("r", encoding="utf-8") as stream:
                for row in csv.reader(stream):
                    if row:
                        last = row
        except OSError:
            return None
        if not last or len(last) < 2:
            return None
        value = last[1].strip()
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            return None
        latest.append(value)
    return latest[0] if latest and len(set(latest)) == 1 else None


def _review_marker_exists(state_root: Path, trade_date: str) -> bool:
    path = state_root / "storage" / "pipeline_markers" / f"review_{trade_date}.json"
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(body, dict) and body.get("task") == "review"


def _calendar_dates(state_root: Path) -> list[str] | None:
    """Read an explicitly persisted trading calendar; never infer holidays.

    The regular data pipeline can write either a list or ``{"open_dates": []}``
    into this non-secret state file. Missing/invalid data intentionally blocks
    a weekly report rather than guessing a Friday holiday.
    """
    candidates = (
        state_root / "storage" / "agent" / "research" / "trading_calendar.json",
        state_root / "storage" / "trading_calendar.json",
    )
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = raw.get("open_dates") if isinstance(raw, dict) else raw
        if not isinstance(values, list):
            continue
        dates = sorted({str(value) for value in values if _valid_day(str(value))})
        if dates:
            return dates
    return None


def _valid_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _weekly_window(trade_date: str, calendar: list[str] | None) -> tuple[str, str] | None:
    if not calendar or trade_date not in calendar:
        return None
    target = date.fromisoformat(trade_date)
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)
    this_week = [value for value in calendar if monday <= date.fromisoformat(value) <= sunday]
    if not this_week or this_week[-1] != trade_date:
        return None
    return this_week[0], this_week[-1]


def _waiting_or_blocked(service: Any, goal_id: str, status: str, reason: str) -> int:
    service.repo.update_goal_status(goal_id, status, termination_reason=reason)
    service._emit(goal_id, "goal_status", {"status": status, "reason": reason})
    # Only invariant codes are logged; provider/library exceptions can contain
    # endpoint context and are intentionally not persisted by this runner.
    print(json.dumps({"goal_id": goal_id, "status": status, "reason": reason}, ensure_ascii=False))
    return 0


def _authenticated_agent(project_root: Path, state_root: Path) -> tuple[Any | None, str]:
    """Create the one agent that owns this broker nonce for the whole job.

    The broker intentionally rotates its nonce after each credential snapshot.
    A probe service followed by fresh per-task services would therefore leave
    every real task holding a stale nonce.  Keep the authenticated agent alive
    and inject it into the research runner instead; the agent still creates
    isolated runtime sessions for every durable attempt.
    """
    socket_path = os.getenv("KSS_PI_AI_CREDENTIAL_SOCKET", "").strip()
    nonce = os.getenv("KSS_PI_AI_CREDENTIAL_NONCE", "").strip()
    if not socket_path or not nonce:
        return None, "credential_broker_unavailable"
    agent: Any | None = None
    try:
        from kss.agent.service import KSSAgentService

        agent = KSSAgentService(state_root, project_root, start_provider=True)
        agent.reload_provider_credentials(socket_path=socket_path, nonce=nonce)
        catalog = agent.provider_catalog()
        primary = (catalog.get("primary") or {}).get("provider_id")
        authenticated = {
            str(provider.get("id"))
            for provider in catalog.get("providers") or []
            if provider.get("authenticated")
        }
        if not primary or primary not in authenticated:
            agent.close()
            return None, "credential_not_available_for_primary_route"
        tested = agent.test_provider_connection()
        if not tested.get("ok"):
            agent.close()
            return None, "provider_connection_unavailable"
        return agent, "ready"
    except Exception:
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass
        return None, "credential_broker_or_provider_unavailable"


def _build_payload(
    cadence: str,
    trade_date: str,
    window: tuple[str, str] | None,
    trading_calendar: list[str] | None = None,
) -> dict[str, Any]:
    if cadence == "daily":
        profile_id = "investment-daily-v1"
        inputs = {
            "trade_date": trade_date,
            "as_of": trade_date,
            "scope": "A 股盘后投资分析",
        }
        client_request_id = f"scheduled:investment-daily-v1:{trade_date}"
        objective = f"投资分析日报 {trade_date}"
    else:
        assert window is not None
        start, end = window
        profile_id = "investment-weekly-v3"
        inputs = {
            "date_range": f"{start}_to_{end}",
            "as_of": end,
            "scope": "A 股本周投资分析",
        }
        # The scheduled path has a confirmed exchange calendar. Persist the
        # exact week used to decide this is the last open day so holiday-short
        # weeks can be reproduced without guessing weekdays on recovery.
        if trading_calendar is not None:
            inputs["trading_calendar"] = [
                item for item in trading_calendar if start <= item <= end
            ]
        client_request_id = f"scheduled:investment-weekly-v3:{start}_{end}"
        objective = f"投资分析周报 {start} 至 {end}"
    return {
        "client_request_id": client_request_id,
        "profile_id": profile_id,
        "objective": objective,
        "inputs": inputs,
        "origin": "scheduled",
        "cadence": cadence,
    }


def run(*, project_root: Path, state_root: Path, cadence: str, max_seconds: float = 3600) -> int:
    _project_imports(project_root)
    from kss.research.service import ResearchService, TERMINAL_GOAL
    from kss.research.runner import AgentResearchTaskRunner

    service = ResearchService(
        state_root=state_root,
        project_root=project_root,
        allow_synthetic_fixture=False,
    )
    trade_date = _latest_target_day(project_root)
    if not trade_date:
        print(json.dumps({"status": "blocked", "reason": "eod_data_incomplete"}, ensure_ascii=False))
        return 0

    window: tuple[str, str] | None = None
    trading_calendar: list[str] | None = None
    preflight_error: str | None = None
    if cadence == "daily":
        if not _review_marker_exists(state_root, trade_date):
            preflight_error = "formal_daily_review_not_completed"
    else:
        trading_calendar = _calendar_dates(state_root)
        window = _weekly_window(trade_date, trading_calendar)
        if trading_calendar is None:
            preflight_error = "trading_calendar_unavailable"
        elif window is None:
            # A normal non-final weekday is a clean no-op, not a failed job.
            print(json.dumps({"status": "skipped", "reason": "not_week_last_open_day", "trade_date": trade_date}, ensure_ascii=False))
            return 0

    created = service.create_goal(
        payload=_build_payload(cadence, trade_date, window, trading_calendar)
    )
    if not created.get("ok"):
        print(json.dumps({"status": "blocked", "reason": "goal_create_failed"}, ensure_ascii=False))
        return 1
    goal_id = str(created.get("goal_id") or "")
    goal = service.repo.get_goal(goal_id) or {}
    if goal.get("status") in TERMINAL_GOAL or goal.get("status") == "waiting_user":
        print(json.dumps({"goal_id": goal_id, "status": goal.get("status"), "reason": "idempotent_existing_goal"}, ensure_ascii=False))
        return 0
    if preflight_error:
        return _waiting_or_blocked(service, goal_id, "blocked", preflight_error)

    agent, reason = _authenticated_agent(project_root, state_root)
    if agent is None:
        status = "waiting_user" if reason.startswith("credential_") else "blocked"
        return _waiting_or_blocked(service, goal_id, status, reason)
    # Do not construct fresh task agents after the one-shot broker reload.
    # See _authenticated_agent above for why this is a security invariant as
    # well as a correctness requirement.
    runner = AgentResearchTaskRunner(
        state_root=state_root,
        project_root=project_root,
        shared_agent=agent,
    )
    service.task_runner = runner
    try:
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            current = service.repo.get_goal(goal_id) or {}
            status = str(current.get("status") or "")
            if status in TERMINAL_GOAL or status == "waiting_user":
                print(json.dumps({"goal_id": goal_id, "status": status}, ensure_ascii=False))
                return 0
            started = service.start_goal(goal_id=goal_id)
            if not started.get("ok") and started.get("error") != "goal_terminal":
                return _waiting_or_blocked(service, goal_id, "blocked", "research_start_rejected")
            service.wait_for_idle(goal_id=goal_id, timeout=min(30.0, max(1.0, deadline - time.monotonic())))
            current = service.repo.get_goal(goal_id) or {}
            if current.get("status") == "queued":
                time.sleep(2.0)

        service.pause_goal(goal_id=goal_id)
        return _waiting_or_blocked(service, goal_id, "budget_limited", "scheduled_runner_timeout")
    finally:
        agent.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one scheduled KSS research goal")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--cadence", choices=("daily", "weekly"), required=True)
    parser.add_argument("--max-seconds", type=float, default=3600)
    args = parser.parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    if not project_root.is_dir() or not state_root.is_absolute():
        return 2
    return run(project_root=project_root, state_root=state_root, cadence=args.cadence, max_seconds=max(1, args.max_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
