#!/usr/bin/env python3
"""Durable, credential-brokered runner for scheduled investment research.

This process is deliberately launched by the signed Swift scheduler helper. It
receives only a short-lived credential-broker socket and nonce, never a model
API key.  It owns the ``ResearchService`` until a goal settles so daemon worker
threads cannot disappear when a one-shot sidecar exits.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any


_HARNESS_STARTUP_TIMEOUT = 120.0
_HARNESS_UNAVAILABLE = "harness_kernel_unavailable"
_HARNESS_GAP_MARKERS = (
    "harness_session_unavailable",
    "harness kernel timed out",
)


def _project_imports(project_root: Path) -> None:
    for value in (str(project_root), str(project_root / "scripts")):
        if value not in sys.path:
            sys.path.insert(0, value)


def _apply_nonsecret_research_env(project_root: Path, state_root: Path) -> None:
    """Load research provider from .env / network.env into this process.

    The signed helper never forwards ``KSS_RESEARCH_PROVIDER``. Desktop injects
    it from Keychain; scheduled jobs must read the non-secret state-root file.
    """
    extras = ["/opt/homebrew/bin", "/usr/local/bin"]
    path_parts = [part for part in os.environ.get("PATH", "").split(":") if part]
    for extra in extras:
        if extra not in path_parts:
            path_parts.append(extra)
    if path_parts:
        os.environ["PATH"] = ":".join(path_parts)
    allowed = {
        "KSS_RESEARCH_PROVIDER",
        "KSS_RESEARCH_FETCH_PROVIDER",
        "KSS_RESEARCH_FIXTURE_PATH",
        "KSS_COMBOSEARCH_BIN",
        "KSS_COMBOSEARCH_TIMEOUT",
        "KSS_COMBOSEARCH_LIMIT",
        "KSS_ANALYST_CORPUS_PATH",
    }
    for env_path in (project_root / ".env", Path(state_root) / "network.env"):
        try:
            lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key not in allowed:
                continue
            value = value.strip().strip("\"'")
            if value:
                os.environ.setdefault(key, value)
    try:
        from kss.research import adapter as research_adapter
        from kss.research.combosearch_provider import install

        install(research_adapter)
    except Exception:
        pass


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


def _is_retryable_waiting_user(goal: dict[str, Any]) -> bool:
    """Credential waits are machine-fixable; other waiting_user states are not.

    A scheduled daily/weekly goal that stopped on ``credential_*`` should be
    retried the next time launchd fires (after the signed helper or Keychain
    ACL is repaired). Mid-research human gates must stay parked.
    """
    if str(goal.get("status") or "") != "waiting_user":
        return False
    reason = str(goal.get("termination_reason") or "")
    return reason.startswith("credential_") or reason == _HARNESS_UNAVAILABLE


def _credential_failure_reason(exc: BaseException) -> str:
    """Map helper/broker failures to a closed set of invariant reason codes."""
    code = str(getattr(exc, "code", "") or "")
    text = str(exc).lower()
    if "credential socket timed out" in text:
        return "credential_broker_timeout"
    if "nonce" in text:
        return "credential_broker_nonce_mismatch"
    if code in {
        "node_unavailable",
        "helper_unavailable",
        "helper_start_failed",
        "helper_not_started",
        "helper_exited",
        "helper_disconnected",
        "version_mismatch",
        "protocol_mismatch",
    }:
        return "credential_helper_unavailable"
    return "credential_broker_or_provider_unavailable"


def _harness_has_brokered_credentials(kernel: Any) -> bool:
    """True only when Harness consumed the broker nonce and returned next_nonce."""
    ready = getattr(kernel, "_ready", None) or {}
    nonce = ready.get("credential_next_nonce")
    return isinstance(nonce, str) and bool(nonce.strip())


def _harness_failure_reason(exc: BaseException) -> str:
    """Map kernel boot failures to a closed set of invariant reason codes."""
    text = str(exc).lower()
    if (
        "credential socket timed out" in text
        or "nonce" in text
        or "credential" in text
    ):
        return _credential_failure_reason(exc)
    return _HARNESS_UNAVAILABLE


def _harness_boot_kwargs(state_root: Path, sidecar_socket: str) -> dict[str, Any]:
    """Match the desktop sidecar boot contract without taking over its socket."""
    return {
        "driver": os.environ.get("KSS_HARNESS_DRIVER", "dsh"),
        "sidecar_socket": sidecar_socket,
        "dsh_home": Path(state_root) / "harness" / "dsh-home",
        "startup_timeout": _HARNESS_STARTUP_TIMEOUT,
    }


_UNIX_SOCK_MAX = 100


def _scheduled_sidecar_socket(state_root: Path) -> Path:
    """Prefer a state-root socket; fall back to /tmp if the AF_UNIX path is too long."""
    preferred = Path(state_root) / "run" / "kss-scheduled-research.sock"
    if len(os.fsencode(str(preferred))) < _UNIX_SOCK_MAX:
        return preferred
    return Path(f"/tmp/kss-sched-research-{os.getpid()}.sock")


def _dispatch_scheduled_tool(req: dict[str, Any]) -> str:
    """Serve Harness KSS-tool RPC without binding the desktop sidecar socket."""
    try:
        from kss.research import adapter as research_adapter
        from kss.research.combosearch_provider import install

        install(research_adapter)
    except Exception:
        pass
    try:
        import kss_sidecar as sidecar
    except Exception:
        return json.dumps(
            {"code": 1, "stderr": "scheduled_tool_backend_unavailable"},
            ensure_ascii=False,
        )
    cmd = str(req.get("cmd") or "")
    try:
        if cmd == "harness-tool-grant":
            sidecar.grant_harness_write(
                str(req.get("call_id") or ""),
                str(req.get("command") or ""),
                surface=str(req.get("surface") or "research"),
            )
            return sidecar._sidecar_ok({"ok": True, "call_id": req.get("call_id")})
        if cmd == "harness-tool-execute":
            raw_args = req.get("args")
            return sidecar._sidecar_ok(
                sidecar.execute_harness_tool(
                    name=str(req.get("name") or ""),
                    args=raw_args if isinstance(raw_args, dict) else {},
                    call_id=str(req.get("call_id") or ""),
                    force_read=bool(req.get("force_read")),
                )
            )
        if cmd == "harness-vision-context":
            return sidecar._sidecar_ok(sidecar._vision_context_payload(req))
        return sidecar._sidecar_err(f"unknown_cmd:{cmd}")
    except Exception as exc:
        return sidecar._sidecar_err(type(exc).__name__)


class _ScheduledToolBackend:
    """Process-local Unix RPC for research tools. Isolated from the desktop sidecar."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = Path(socket_path)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> str:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._thread = threading.Thread(
            target=self._run, name="kss-scheduled-tools", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("scheduled tool backend failed to start")
        if self._error is not None:
            raise self._error
        return str(self.socket_path)

    def close(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

    def _run(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._bind())
            self._ready.set()
            loop.run_forever()
        except Exception as exc:
            self._error = exc
            self._ready.set()
        finally:
            server = self._server
            if loop is not None:
                if server is not None:
                    server.close()
                loop.close()

    async def _bind(self) -> None:
        self._server = await asyncio.start_unix_server(
            self._on_conn, path=str(self.socket_path)
        )
        os.chmod(self.socket_path, 0o700)

    async def _on_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                req = {}
            if not isinstance(req, dict):
                req = {}
            body = await asyncio.to_thread(_dispatch_scheduled_tool, req)
            writer.write(body.encode("utf-8") + b"\n")
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


_RESEARCH_TURN_TIMEOUT = 600.0


class _ScheduledResearchSession:
    """Research session with a turn budget that can finish live tool loops.

    The shared Node session defaults to 180s. A scheduled collect_sources turn
    already had two industry-chain tool calls in the first minute and was still
    working when that budget expired.
    """

    def __init__(self, kernel: Any, timeout: float = _RESEARCH_TURN_TIMEOUT) -> None:
        self.kernel = kernel
        self.timeout = timeout

    def run(self, request: Any, driver: Any) -> Any:
        from kss.agent.harness_kernel import _agent_options_payload
        from kss.research.harness_driver import ResearchTurnResult

        del driver
        if not getattr(self.kernel, "alive", False):
            return ResearchTurnResult(
                harness_status="interrupted",
                error="harness_session_unavailable",
                applied_write_ids=request.applied_write_ids,
            )
        try:
            payload: dict[str, Any] = {
                "prompt": request.prompt,
                "cwd": str(request.allowlist.cwd),
                "attempt_id": request.attempt_id,
                "allowlist": list(request.allowlist.tools),
                "session_id": request.attempt_id,
            }
            payload.update(_agent_options_payload())
            timeout = self.timeout
            raw = (request.task.get("payload") or {}).get("timeout_seconds")
            try:
                if raw is not None:
                    timeout = max(timeout, float(raw))
            except (TypeError, ValueError):
                pass
            body = self.kernel.request("research.turn", payload, timeout=timeout)
        except Exception as exc:
            return ResearchTurnResult(
                harness_status="interrupted",
                error=str(exc) or "harness_session_unavailable",
                applied_write_ids=request.applied_write_ids,
            )
        ids = list(request.applied_write_ids)
        for item in body.get("applied_write_ids") or []:
            ids.append(str(item))
        status = str(body.get("status") or "interrupted")
        return ResearchTurnResult(
            harness_status=status,
            assistant_text=str(body.get("assistant_text") or ""),
            applied_write_ids=tuple(ids),
            error=None if body.get("ok") is not False else str(body.get("error") or ""),
        )


def _boot_scheduled_research_runtime(
    project_root: Path, state_root: Path
) -> tuple[Any | None, Any | None, str]:
    """Start the tool backend and Node Harness before any other broker consumer.

    Harness injects Keychain credentials and writes ``credential_next_nonce``
    back to the environment. A later pi-ai probe must see that rotated nonce.
    """
    _project_imports(project_root)
    backend = _ScheduledToolBackend(_scheduled_sidecar_socket(state_root))
    try:
        socket = backend.start()
    except Exception:
        return None, None, "scheduled_tool_backend_unavailable"
    try:
        from kss.agent.harness_kernel import ensure_harness_kernel

        kernel = ensure_harness_kernel(**_harness_boot_kwargs(state_root, socket))
        if kernel is None or not getattr(kernel, "alive", False):
            backend.close()
            return None, None, _HARNESS_UNAVAILABLE
        try:
            import kss_sidecar as sidecar

            sidecar.mark_harness_kernel_alive()
        except Exception:
            pass
        return backend, kernel, "ready"
    except Exception as exc:
        backend.close()
        return None, None, _harness_failure_reason(exc)


def _attempt_blobs_have_harness_gap(blobs: list[str]) -> bool:
    return any(
        any(marker in blob for marker in _HARNESS_GAP_MARKERS) for blob in blobs
    )


def _resolve_scheduled_corpus_path(project_root: Path, state_root: Path, goal: dict[str, Any]) -> Path | None:
    """Prefer an explicit user/env path, then the standing state-root file."""
    candidates: list[Path] = []
    inputs = goal.get("inputs") or {}
    for raw in (
        inputs.get("analyst_corpus_path"),
        os.environ.get("KSS_ANALYST_CORPUS_PATH"),
    ):
        if isinstance(raw, str) and raw.strip():
            candidates.append(Path(raw).expanduser())
    candidates.extend(
        [
            Path(state_root) / "storage" / "analyst-corpus-v1.jsonl",
            Path(project_root) / "storage" / "analyst-corpus-v1.jsonl",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _maybe_import_scheduled_corpus(
    service: Any,
    goal_id: str,
    project_root: Path,
    state_root: Path,
) -> None:
    goal = service.repo.get_goal(goal_id) or {}
    if str(goal.get("profile_id") or "") not in {
        "investment-daily-v1",
        "investment-weekly-v3",
    }:
        return
    path = _resolve_scheduled_corpus_path(project_root, state_root, goal)
    if path is None:
        return
    imported = service.import_analyst_corpus(
        goal_id=goal_id,
        payload={"path": str(path)},
    )
    if not imported.get("ok") and imported.get("error") not in {
        "analyst_corpus_already_imported",
    }:
        print(
            json.dumps(
                {
                    "status": "corpus_import_skipped",
                    "reason": imported.get("error"),
                },
                ensure_ascii=False,
            )
        )


def _reopen_compile_chain(service: Any, goal_id: str) -> None:
    """Re-open compile/audit nodes after a compiler口径 fix without dropping evidence."""
    from kss.research.repository import utc_now
    from kss.storage.db import connect

    now = utc_now()
    with connect(service.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET status='ready', current_attempt_id=NULL, updated_at=?
            WHERE goal_id=? AND kind='compile_report'
              AND status IN ('incomplete', 'failed', 'blocked')
            """,
            (now, goal_id),
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET status='pending', current_attempt_id=NULL, updated_at=?
            WHERE goal_id=? AND kind IN ('delivery_audit', 'preview_publish_gate')
              AND status IN ('blocked', 'failed', 'incomplete')
            """,
            (now, goal_id),
        )


def _reset_scheduled_budget_clock(service: Any, goal_id: str) -> None:
    """Drop wall-clock and node usage so a resumed DAG is not billed for debug retries.

    ``usage.seconds`` is elapsed since ``research_goals.started_at``. That stamp
    survives credential/harness retries, so a verify loop can trip
    ``max_seconds`` before the remaining nodes even start. Compile retries also
    increment ``usage.nodes`` up to ``max_nodes``, which would otherwise block
    delivery_audit after a successful compile.
    """
    from kss.research.repository import dumps, loads, utc_now
    from kss.storage.db import connect

    now = utc_now()
    with connect(service.db_path) as conn:
        row = conn.execute(
            "SELECT usage_json FROM research_goals WHERE goal_id=?",
            (goal_id,),
        ).fetchone()
        usage = loads(row["usage_json"] if row else None, {}) or {}
        usage["seconds"] = 0
        usage["nodes"] = 0
        conn.execute(
            """
            UPDATE research_goals
            SET usage_json=?, started_at=?, finished_at=NULL, updated_at=?
            WHERE goal_id=?
            """,
            (dumps(usage), now, now, goal_id),
        )


def _collect_sources_succeeded(goal: dict[str, Any]) -> bool:
    return any(
        str(item.get("kind") or "") == "collect_sources"
        and str(item.get("status") or "") == "succeeded"
        for item in goal.get("tasks") or []
        if isinstance(item, dict)
    )


def _has_resumable_progress(goal: dict[str, Any]) -> bool:
    """True when collect_sources landed and later DAG nodes are still open."""
    tasks = goal.get("tasks") or []
    if not isinstance(tasks, list) or not tasks:
        return False
    remaining = any(
        str(item.get("status") or "") in {"ready", "pending", "incomplete", "blocked"}
        for item in tasks
        if isinstance(item, dict)
    )
    return _collect_sources_succeeded(goal) and remaining


def _should_retry_scheduled_goal(service: Any, goal: dict[str, Any]) -> bool:
    """Credential waits, harness-boot gaps, and mid-DAG serialization crashes."""
    if _is_retryable_waiting_user(goal):
        return True
    if str(goal.get("status") or "") in {"failed", "budget_limited"} and _has_resumable_progress(goal):
        return True
    if str(goal.get("status") or "") != "insufficient_evidence":
        return False
    if _collect_sources_succeeded(goal):
        return True
    if str(goal.get("termination_reason") or "") != "audit_failed":
        return False
    goal_id = str(goal.get("goal_id") or "")
    if not goal_id:
        return False
    try:
        from kss.storage.db import connect

        with connect(service.db_path) as conn:
            rows = conn.execute(
                """
                SELECT a.result_json, a.error
                FROM research_attempts a
                JOIN research_tasks t ON t.current_attempt_id=a.attempt_id
                WHERE t.goal_id=? AND t.kind='collect_sources'
                """,
                (goal_id,),
            ).fetchall()
    except Exception:
        return False
    blobs = [f"{row['error'] or ''} {row['result_json'] or ''}" for row in rows]
    return _attempt_blobs_have_harness_gap(blobs)


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
    except Exception as exc:
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass
        return None, _credential_failure_reason(exc)


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
        profile_id = "investment-weekly-v3"
        if window is None:
            # 交易日历取不到时 window 必为 None(_weekly_window 对空日历直接返 None),
            # 而 run() 这时仍要先建 goal 才能把它标成 blocked——与 daily 的 preflight
            # 同一条路径,为的是留记录并走幂等检查。原先这里 assert,既与本函数签名的
            # `window: tuple[str, str] | None` 自相矛盾,又让整个 job 崩在建 goal 之前,
            # 连一条 blocked 记录都留不下(实测 investment_analysis_weekly 每周 exit 1)。
            # 退化成当日单点窗口:它生成的 client_request_id 与真实周窗口的不同,日历恢复
            # 后重跑会正常建新 goal,不会撞上本次的幂等键。
            start = end = trade_date
            unresolved = "(周窗口未定:交易日历不可用)"
        else:
            start, end = window
            unresolved = ""
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
        objective = f"投资分析周报 {start} 至 {end}{unresolved}"
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
    _apply_nonsecret_research_env(project_root, state_root)
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
    if _should_retry_scheduled_goal(service, goal):
        prior = str(goal.get("status") or "")
        if prior == "insufficient_evidence" and not _collect_sources_succeeded(goal):
            service.refresh_snapshot(goal_id)
        else:
            service.repo.update_goal_status(goal_id, "draft", termination_reason=None)
            if prior == "budget_limited":
                _reset_scheduled_budget_clock(service, goal_id)
            if prior == "insufficient_evidence":
                _reopen_compile_chain(service, goal_id)
        goal = service.repo.get_goal(goal_id) or {}
    if goal.get("status") in TERMINAL_GOAL or goal.get("status") == "waiting_user":
        print(json.dumps({"goal_id": goal_id, "status": goal.get("status"), "reason": "idempotent_existing_goal"}, ensure_ascii=False))
        return 0
    if preflight_error:
        return _waiting_or_blocked(service, goal_id, "blocked", preflight_error)
    _maybe_import_scheduled_corpus(service, goal_id, project_root, state_root)

    backend = None
    try:
        # Harness consumes the broker nonce first and writes next_nonce back.
        backend, kernel, reason = _boot_scheduled_research_runtime(
            project_root, state_root
        )
        if kernel is None:
            status = (
                "waiting_user"
                if reason.startswith("credential_") or reason == _HARNESS_UNAVAILABLE
                else "blocked"
            )
            return _waiting_or_blocked(service, goal_id, status, reason)
        # Research turns are Harness-owned. A Python pi-ai probe after boot
        # would consume the rotated nonce and then read the dsh catalog, whose
        # provider ids (deepseek-official) do not match the stored primary
        # (deepseek). That false-negative parked the 08-14 verify as
        # credential_not_available_for_primary_route in ~2s.
        if not _harness_has_brokered_credentials(kernel):
            return _waiting_or_blocked(
                service, goal_id, "waiting_user", "credential_broker_or_provider_unavailable"
            )
        from kss.research.harness_driver import ResearchHarnessDriver

        session = (
            _ScheduledResearchSession(kernel)
            if getattr(kernel, "alive", False)
            else None
        )
        runner = AgentResearchTaskRunner(
            state_root=state_root,
            project_root=project_root,
            driver=ResearchHarnessDriver(
                state_root=state_root,
                project_root=project_root,
                session=session,
            ),
        )
        service.task_runner = runner
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
        try:
            from kss.agent.harness_kernel import stop_harness_kernel

            stop_harness_kernel()
        except Exception:
            pass
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass


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
