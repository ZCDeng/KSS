#!/usr/bin/env python3
"""U5：常驻 Python sidecar —— 取代 subprocess-per-call。

asyncio Unix domain socket 服务，import kss_app_bridge 的 handler（零逻辑 fork），
每连接处理一条 `{"cmd","args"}` 请求 → 一条响应：
  成功 `{"code":0,"stdout":"<envelope json>"}`（stdout 与 subprocess 输出逐字一致）
  失败 `{"code":1,"stderr":"<msg>"}`
pandas 等只在 daemon 启动 import 一次；socket 0700；SIGHUP 重启自身以重载改动的 Python。
Swift 端 socket 不应答(3s)时回退 subprocess。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO = Path(__file__).resolve().parents[1]
for p in (str(_REPO), str(_REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import kss_app_bridge as bridge  # noqa: E402
from kss.agent import (  # noqa: E402
    AgentEvent,
    AgentMessage,
    EventSequencer,
    KSSAgentService,
    MemoryStore,
    RunAdmissionError,
    RuntimeBusyError,
    SessionStore,
    SkillManager,
)

logger = logging.getLogger(__name__)

SOCKET_PATH = bridge.STATE_ROOT / "run" / "kss-sidecar.sock"
PID_PATH = SOCKET_PATH.parent / "kss-sidecar.pid"
VERSION_PATH = SOCKET_PATH.parent / "kss-sidecar.version"

# Swift 端 spawn 时把 stdout/stderr 重定向进文件(见 BridgeClient.ensureSidecarRunning);
# 这里只需保证 root logger 在 INFO 级别有输出、走 stderr 即可落进那个文件。
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                     stream=sys.stderr)

# ---------------------------------------------------------------------------
# U3(plan 004)：chat-turn 长连 handler + 并发 reader 任务 = 写执行唯一点。
# 安全核心(KTD-4):写 dispatch 只在本文件的 reader/handler 路径,kss_chat_loop 不含写执行。
# ---------------------------------------------------------------------------

# 总开关(KTD-4):启动读一次,运行中不重读(防中途翻转)。独立于 kss_mcp._LIVE。
_CHAT_LOOP_LIVE = os.environ.get("KSS_APP_LIVE") == "1"
# 单个 confirm 等待人工 tap 的上限;超时即按拒(KTD-6 B3,防 loop await 挂死)。
_CONFIRM_TIMEOUT = 300.0

_AGENT_ABORTS: dict[str, Any] = {}
_AGENT_SERVICE: KSSAgentService | None = None
_AGENT_SERVICE_ROOTS: tuple[Path, Path] | None = None


def _agent_service() -> KSSAgentService:
    """Return the process-wide stateful Runtime, rebuilding only when roots change."""
    global _AGENT_SERVICE, _AGENT_SERVICE_ROOTS
    roots = (Path(bridge.STATE_ROOT), Path(bridge.PROJECT_ROOT))
    if _AGENT_SERVICE is None or _AGENT_SERVICE_ROOTS != roots:
        _AGENT_SERVICE = KSSAgentService(*roots)
        _AGENT_SERVICE_ROOTS = roots
    return _AGENT_SERVICE

def _session_store() -> SessionStore:
    return SessionStore(bridge.STATE_ROOT)


def _skill_manager() -> SkillManager:
    return SkillManager(bridge.PROJECT_ROOT, bridge.STATE_ROOT)


def _memory_store() -> MemoryStore:
    return MemoryStore(bridge.STATE_ROOT)


def _sidecar_ok(payload: Any) -> str:
    return json.dumps({"code": 0, "stdout": bridge._envelope_json(payload)}, ensure_ascii=False)


def _sidecar_err(message: str) -> str:
    return json.dumps({"code": 1, "stderr": message}, ensure_ascii=False)


def _execute_write(command: str, args: list[str]) -> dict:
    """写执行(reader/auto 路径共用)。先查总开关,关则拒;dispatch 异常隔离为业务错误。"""
    if not _CHAT_LOOP_LIVE:
        return {"error": "not_live",
                "hint": "KSS_APP_LIVE!=1,写操作全拒(_CHAT_LOOP_LIVE 总开关关闭)"}
    try:
        payload = bridge.dispatch(command, args)
        return {"ok": True, "command": command, "result": payload}
    except SystemExit as exc:
        return {"error": "rejected", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": "write_failed", "detail": f"{type(exc).__name__}: {exc}"}


def _reject_all(pending: dict, result: dict) -> None:
    """断连/SIGHUP/收尾:把所有未决 confirm 按拒收尾,防 loop 的 await 永挂(KTD-3/F3)。"""
    for entry in list(pending.values()):
        fut = entry["future"]
        if not fut.done():
            fut.set_result(dict(result))
    pending.clear()


async def _confirm_reader(reader: asyncio.StreamReader, pending: dict) -> None:
    """并发 reader 任务:收 chat-turn-confirm{call_id,approved},是 confirm 处理+写执行的唯一点。
    StreamReader/StreamWriter 是同 fd 独立两半,与 emit 写并发无 fd 争用 → 无 Gap1 死锁。"""
    while True:
        try:
            line = await reader.readline()
        except (ConnectionError, asyncio.IncompleteReadError):
            line = b""
        if not line:                       # EOF / 断连
            _reject_all(pending, {"error": "disconnected", "hint": "连接中断,写按拒收尾"})
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict) or msg.get("cmd") != "chat-turn-confirm":
            continue
        call_id = msg.get("call_id")
        approved = bool(msg.get("approved"))
        logger.info("[chat] 收到 chat-turn-confirm call_id=%s approved=%s", call_id, approved)
        entry = pending.pop(call_id, None)   # 单用途:取出即删,杜绝重放/串号(F1/B2)
        if entry is None:
            logger.warning("[chat] 丢弃不匹配/已消费 confirm call_id=%r", call_id)
            continue
        fut = entry["future"]
        if fut.done():                       # 幂等:重复 approved 忽略
            continue
        if approved:
            result = await asyncio.to_thread(_execute_write, entry["command"], entry["args"])
        else:
            result = {"error": "denied", "hint": "用户拒绝该写操作"}
        if not fut.done():
            fut.set_result(result)


async def _handle_chat_turn(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter, req: dict) -> None:
    """长连聊天一轮:spawn loop 任务 + reader 任务(解 Gap1 死锁);emit 逐帧 drain。"""
    import kss_chat_loop as chat_loop  # 惰性 import(sidecar→chat_loop 单向,KTD-2 红线)

    pending: dict[str, dict] = {}
    write_lock = asyncio.Lock()              # 串行化 writer,emit 与帧不交错

    async def emit(ev: dict) -> None:
        async with write_lock:
            writer.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()             # 每帧 drain(KTD-3 Gap2)

    async def request_write(*, command: str, args: list, tool_name: str, tool_args: dict) -> dict:
        """loop 发来的写意图。auto 任务直接执行(reader/handler 侧),否则 emit confirm 等人工 tap。
        loop 只 await 本协程结果,不持 Future、不调 dispatch(KTD-4)。"""
        if chat_loop.is_auto_task(command, args):
            return await asyncio.to_thread(_execute_write, command, args)   # AUTO 免确认
        call_id = uuid4().hex                 # handler 生成,loop 不能选值(F1)
        fut = asyncio.get_running_loop().create_future()
        pending[call_id] = {"future": fut, "command": command, "args": args}
        logger.info("[chat] confirm_required 发出 call_id=%s command=%s", call_id, command)
        await emit({"type": "confirm_required", "call_id": call_id,
                    "tool": tool_name, "command": command, "args": tool_args,
                    "argsText": json.dumps(tool_args, ensure_ascii=False),   # Swift modal 直接显
                    "effect": chat_loop.write_effect_label(command, args)})   # 人话效果(U5)
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT)
            logger.info("[chat] confirm 收到 call_id=%s 等待=%.1fs", call_id, time.monotonic() - t0)
            return result
        except asyncio.TimeoutError:          # 超时即拒(B3),删条目防后到 approved 复用
            pending.pop(call_id, None)
            logger.warning("[chat] confirm 超时 call_id=%s 等待=%.1fs", call_id, time.monotonic() - t0)
            return {"error": "confirm_timeout", "hint": "确认超时,写按拒"}

    raw = req.get("messages") if isinstance(req, dict) else None
    messages = _prepare_messages(raw)

    reader_task = asyncio.create_task(_confirm_reader(reader, pending))
    try:
        await chat_loop.run_turn(messages, emit, request_write)
    except Exception as exc:  # noqa: BLE001  loop 意外异常 → error 帧,daemon 存活
        logger.warning("[chat] run_turn 异常: %s", exc)
        try:
            await emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        reader_task.cancel()
        _reject_all(pending, {"error": "turn_ended"})


class _AgentFrameEmitter:
    """Agent v1 NDJSON frame writer with per-run monotonic sequence."""

    def __init__(self, writer: asyncio.StreamWriter, session_id: str, run_id: str) -> None:
        self.writer = writer
        self.session_id = session_id
        self.run_id = run_id
        self.sequencer = EventSequencer(session_id=session_id, run_id=run_id)
        self.lock = asyncio.Lock()

    async def emit(self, event: str, data: dict | None = None) -> None:
        async with self.lock:
            frame = self.sequencer.to_wire(self.sequencer.frame(event, data or {}))
            payload = frame.pop("payload", {})
            if isinstance(payload, dict):
                frame.update(payload)
            self.writer.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.writer.drain()


async def _write_runtime_event(writer: asyncio.StreamWriter, event: AgentEvent,
                               lock: asyncio.Lock) -> None:
    """Flatten a Runtime AgentEvent onto the existing v1 NDJSON wire."""
    async with lock:
        frame = asdict(event)
        payload = frame.pop("payload", {})
        if isinstance(payload, dict):
            frame.update(payload)
        if frame.get("type") == "error" and "error" not in frame:
            frame["error"] = frame.get("message") or "agent runtime error"
        writer.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()


def _validate_agent_turn_request(req: dict) -> tuple[str, str, str] | tuple[None, None, str]:
    allowed = {"cmd", "session_id", "client_turn_id", "input"}
    extra = set(req) - allowed
    if extra:
        return None, None, f"agent-turn unexpected fields: {sorted(extra)}"
    session_id = req.get("session_id")
    client_turn_id = req.get("client_turn_id")
    text = req.get("input")
    if not isinstance(session_id, str) or not session_id:
        return None, None, "agent-turn requires session_id"
    if not isinstance(client_turn_id, str) or not client_turn_id:
        return None, None, "agent-turn requires client_turn_id"
    if not isinstance(text, str):
        return None, None, "agent-turn requires string input"
    return session_id, client_turn_id, text


def _agent_wire_message(message: AgentMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "text": message.content,
        "content": message.content,
        "timestamp": message.timestamp,
        "tool_calls": [asdict(call) for call in message.tool_calls],
        "metadata": message.metadata,
    }


def _session_summary(state: Any, messages: list[AgentMessage] | None = None) -> dict[str, Any]:
    meta = dict(getattr(state, "metadata", {}) or {})
    status = getattr(state, "status", "running")
    return {
        "session_id": state.session_id,
        "title": meta.get("title") or state.session_id,
        "archived": status == "archived",
        "updated_at": str(meta.get("updated_at") or 0),
        "messages": [_agent_wire_message(m) for m in (messages or [])],
    }


def _session_response(store: SessionStore, *, selected_session_id: str | None = None) -> dict[str, Any]:
    """统一返回会话列表，避免动作响应与 Swift 解码形状漂移."""
    return {
        "sessions": [
            _session_summary(state, store.read_messages(state.session_id))
            for state in store.list_sessions()
        ],
        "selected_session_id": selected_session_id,
    }


def _skill_response(manager: SkillManager, session_id: str | None = None) -> dict[str, Any]:
    """统一返回技能状态、诊断和当前会话 pin 状态."""
    found, diagnostics = manager.discover()
    pinned = set(manager.pinned_skill_ids(session_id)) if session_id else set()
    return {
        "skills": [{
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "enabled": skill.enabled,
            "pinned": skill.id in pinned or skill.name in pinned,
        } for skill in found],
        "diagnostics": [{
            "code": item.code,
            "message": item.message,
            "path": str(item.path) if item.path is not None else None,
        } for item in diagnostics],
        "status": manager.status(),
    }


def _memory_wire(record: Any) -> dict[str, Any]:
    """把内部记忆记录映射为稳定的桌面协议字段."""
    source = record.source_session
    if record.source_entry:
        source = f"{source or 'session'} · {record.source_entry}"
    return {
        "id": record.id,
        "kind": record.kind,
        "text": record.content,
        "content": record.content,
        "source": source,
        "source_session": record.source_session,
        "source_entry": record.source_entry,
        "tags": list(record.tags),
        "status": record.status,
        "archived": record.status == "archived",
        "created_at": record.created_at,
        "expires_at": record.expires_at,
    }


def _memory_response(store: MemoryStore, query: str = "", *, limit: int = 100) -> dict[str, Any]:
    """统一返回已批准记忆与待确认候选."""
    records = store.search(
        query,
        include_status=("approved", "proposed", "archived"),
        limit=limit,
    )
    return {
        "memories": [_memory_wire(item) for item in records if item.status == "approved"],
        "candidates": [_memory_wire(item) for item in records if item.status == "proposed"],
        "recalls": [],
    }


def _recall_wire(items: list[str]) -> list[dict[str, Any]]:
    """把本轮实际召回文本映射为可展示来源."""
    return [{
        "id": f"recall-{index}",
        "title": "本轮召回记忆",
        "source": "长期记忆",
        "excerpt": text,
    } for index, text in enumerate(items)]


async def _agent_control_reader(reader: asyncio.StreamReader, pending: dict, abort_token: Any,
                                run_id: str) -> None:
    """agent-turn 同连接控制 reader:支持 chat-turn-confirm 和 agent-control abort。"""
    while True:
        try:
            line = await reader.readline()
        except (ConnectionError, asyncio.IncompleteReadError):
            line = b""
        if not line:
            _reject_all(pending, {"error": "disconnected", "hint": "连接中断,写按拒收尾"})
            abort_token.abort("disconnected")
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("cmd") == "agent-control":
            action = msg.get("action")
            if action == "abort" and msg.get("run_id") in (None, run_id):
                abort_token.abort(str(msg.get("reason") or "client_abort"))
                _reject_all(pending, {"error": "aborted", "hint": "用户中止本轮"})
                return
            if action != "confirm":
                continue
            call_id = msg.get("call_id")
            approved = bool(msg.get("approved"))
        elif msg.get("cmd") == "chat-turn-confirm":
            call_id = msg.get("call_id")
            approved = bool(msg.get("approved"))
        else:
            continue
        entry = pending.pop(call_id, None)
        if entry is None:
            logger.warning("[agent] 丢弃不匹配/已消费 confirm call_id=%r", call_id)
            continue
        fut = entry["future"]
        if fut.done():
            continue
        if approved:
            result = await asyncio.to_thread(_execute_write, entry["command"], entry["args"])
        else:
            result = {"error": "denied", "hint": "用户拒绝该写操作"}
        if not fut.done():
            fut.set_result(result)


async def _handle_agent_turn(reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter, req: dict) -> None:
    """Agent v1 transport: decode, stream Runtime events, and own the write gate."""
    import kss_chat_loop as chat_loop

    session_id, client_turn_id, user_text_or_error = _validate_agent_turn_request(req)
    transport_run_id = uuid4().hex
    emitter = _AgentFrameEmitter(writer, session_id or "", transport_run_id)
    if session_id is None or client_turn_id is None:
        await emitter.emit("error", {"error": user_text_or_error})
        await emitter.emit("agent_end", {"reason": "bad_request"})
        return

    service = _agent_service()
    duplicate = service.duplicate_turn(session_id, client_turn_id)
    if duplicate is not None:
        payload = {
            "existing_run_id": duplicate.existing_run_id,
            "client_turn_id": client_turn_id,
        }
        if duplicate.status == "running":
            await emitter.emit("agent_start", payload)
            await emitter.emit("agent_end", {
                **payload,
                "reason": "already_running",
                "termination_reason": "already_running",
            })
        elif duplicate.status == "completed":
            await emitter.emit("agent_start", payload)
            await emitter.emit("agent_end", {
                **payload,
                "reason": "duplicate_completed",
                "termination_reason": "duplicate_completed",
            })
        else:
            await emitter.emit("error", {
                **payload,
                "error": "interrupted or failed turns require a new client_turn_id",
                "is_error": True,
            })
            await emitter.emit("agent_end", {
                **payload,
                "reason": "retry_requires_new_client_turn_id",
                "termination_reason": "retry_requires_new_client_turn_id",
            })
        return

    pending: dict[str, dict] = {}
    writer_lock = asyncio.Lock()
    control_task: asyncio.Task | None = None
    active_run_id: str | None = None

    async def emit_runtime(event: AgentEvent) -> None:
        nonlocal control_task, active_run_id
        if active_run_id is None:
            active_run_id = event.run_id
            state = service.runtime.state(event.run_id)
            token = state.abort_token if state is not None else None
            if token is not None:
                _AGENT_ABORTS[event.run_id] = token
                control_task = asyncio.create_task(
                    _agent_control_reader(reader, pending, token, event.run_id)
                )
        await _write_runtime_event(writer, event, writer_lock)

    async def request_write(*, command: str, args: list,
                            tool_name: str, tool_args: dict,
                            emit_event: Any) -> dict:
        if chat_loop.is_auto_task(command, args):
            return await asyncio.to_thread(_execute_write, command, args)
        call_id = uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        pending[call_id] = {"future": fut, "command": command, "args": args}
        await emit_event("confirm_required", {
            "call_id": call_id,
            "tool": tool_name,
            "command": command,
            "args": tool_args,
            "argsText": json.dumps(tool_args, ensure_ascii=False),
            "effect": chat_loop.write_effect_label(command, args),
        })
        try:
            return await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:
            pending.pop(call_id, None)
            return {"error": "confirm_timeout", "hint": "确认超时,写按拒"}

    try:
        await service.run_turn(
            session_id,
            client_turn_id,
            user_text_or_error,
            emit_runtime,
            request_write,
        )
    except RunAdmissionError as exc:
        rejected = _AgentFrameEmitter(writer, session_id, transport_run_id)
        payload = {
            "existing_run_id": exc.existing_run_id,
            "client_turn_id": client_turn_id,
        }
        if exc.status == "running":
            reason = "already_running"
            await rejected.emit("agent_start", payload)
            await rejected.emit(
                "agent_end",
                {
                    **payload,
                    "reason": reason,
                    "termination_reason": reason,
                },
            )
        elif exc.status == "completed":
            reason = "duplicate_completed"
            await rejected.emit("agent_start", payload)
            await rejected.emit(
                "agent_end",
                {
                    **payload,
                    "reason": reason,
                    "termination_reason": reason,
                },
            )
        else:
            reason = "retry_requires_new_client_turn_id"
            await rejected.emit(
                "error",
                {
                    **payload,
                    "error": "interrupted or failed turns require a new client_turn_id",
                    "is_error": True,
                },
            )
            await rejected.emit(
                "agent_end",
                {
                    **payload,
                    "reason": reason,
                    "termination_reason": reason,
                },
            )
    except RuntimeBusyError as exc:
        busy = _AgentFrameEmitter(writer, session_id, transport_run_id)
        payload = {
            "existing_run_id": exc.existing_run_id,
            "client_turn_id": client_turn_id,
            "reason": "already_running",
            "termination_reason": "already_running",
        }
        await busy.emit("agent_start", payload)
        await busy.emit("agent_end", payload)
    finally:
        if control_task is not None:
            control_task.cancel()
        _reject_all(pending, {"error": "turn_ended"})
        if active_run_id is not None:
            _AGENT_ABORTS.pop(active_run_id, None)


def _prepare_messages(raw) -> list[dict]:
    """净化 user 输入(R8),其余角色原样。system prompt 由 run_turn 注入(U6)。"""
    from kss.llm.chat_client import sanitize_user_text
    out: list[dict] = []
    for m in (raw or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            out.append({"role": "user", "content": sanitize_user_text(m.get("content", ""))})
        else:
            out.append(m)
    return out


def _handle_agent_json_command(req: dict) -> str | None:
    """Agent v1 非流式 JSON 命令；返回标准 sidecar response。"""
    cmd = req.get("cmd")
    try:
        if cmd == "agent-session":
            store = _session_store()
            action = req.get("action") or "open"
            if action == "create":
                state = store.create_session(
                    session_id=req.get("session_id"),
                    metadata={"title": req.get("title") or req.get("session_id") or ""},
                )
                return _sidecar_ok(_session_response(
                    store, selected_session_id=state.session_id,
                ))
            if action == "open":
                sid = req.get("session_id")
                if not isinstance(sid, str) or not sid:
                    return _sidecar_err("agent-session open requires session_id")
                state = store.open_session(sid)
                if state is None:
                    state = store.create_session(session_id=sid, metadata={"title": sid})
                return _sidecar_ok(_session_response(store, selected_session_id=state.session_id))
            if action == "list":
                return _sidecar_ok(_session_response(store))
            if action == "rename":
                sid = req.get("session_id")
                title = req.get("title")
                if not isinstance(sid, str) or not sid:
                    return _sidecar_err("agent-session rename requires session_id")
                if not isinstance(title, str) or not title.strip():
                    return _sidecar_err("agent-session rename requires title")
                state = store.rename_session(sid, title)
                return _sidecar_ok(_session_response(
                    store, selected_session_id=state.session_id,
                ))
            if action == "archive":
                sid = req.get("session_id")
                if not isinstance(sid, str) or not sid:
                    return _sidecar_err("agent-session archive requires session_id")
                store.archive_session(sid)
                return _sidecar_ok(_session_response(store))
            return _sidecar_err(f"unknown agent-session action: {action}")
        if cmd == "agent-skills":
            manager = _skill_manager()
            action = req.get("action") or "list"
            if action in {"list", "discovery", "reload"}:
                session_id = req.get("session_id")
                return _sidecar_ok(_skill_response(
                    manager, session_id if isinstance(session_id, str) else None,
                ))
            if action == "pin":
                session_id = req.get("session_id")
                skill_id = req.get("skill_id")
                if not isinstance(session_id, str) or not isinstance(skill_id, str):
                    return _sidecar_err("agent-skills pin requires session_id and skill_id")
                if bool(req.get("pinned", True)):
                    manager.pin_skill(session_id, skill_id)
                else:
                    manager.unpin_skill(session_id, skill_id)
                return _sidecar_ok(_skill_response(manager, session_id))
            if action == "enable":
                skill_id = req.get("skill_id")
                if not isinstance(skill_id, str):
                    return _sidecar_err("agent-skills enable requires skill_id")
                enabled = bool(req.get("enabled", True))
                manager.set_enabled(skill_id, enabled)
                session_id = req.get("session_id")
                return _sidecar_ok(_skill_response(
                    manager, session_id if isinstance(session_id, str) else None,
                ))
            return _sidecar_err(f"unknown agent-skills action: {action}")
        if cmd == "agent-memories":
            store = _memory_store()
            action = req.get("action") or "search"
            if action in {"list", "search"}:
                query = req.get("query") if isinstance(req.get("query"), str) else ""
                limit = int(req.get("limit") or 10)
                return _sidecar_ok(_memory_response(store, query, limit=limit))
            if action == "propose":
                text = req.get("text")
                kind = req.get("kind") or "preference"
                if not isinstance(text, str) or not text.strip():
                    return _sidecar_err("agent-memories propose requires text")
                if kind not in {"preference", "decision", "thesis"}:
                    return _sidecar_err("agent-memories propose kind is invalid")
                store.propose(
                    kind,
                    text,
                    source_session=req.get("source_session"),
                    metadata={"source": req.get("source") or "message_action"},
                )
                return _sidecar_ok(_memory_response(store))
            if action == "approve":
                mid = req.get("memory_id") or req.get("candidate_id")
                if not isinstance(mid, str) or not mid:
                    return _sidecar_err("agent-memories approve requires memory_id or candidate_id")
                if bool(req.get("approved", True)):
                    store.approve(mid)
                else:
                    store.delete(mid)
                return _sidecar_ok(_memory_response(store))
            if action == "archive":
                mid = req.get("memory_id")
                if not isinstance(mid, str) or not mid:
                    return _sidecar_err("agent-memories archive requires memory_id")
                store.archive(mid)
                return _sidecar_ok(_memory_response(store))
            if action == "delete":
                mid = req.get("memory_id")
                if not isinstance(mid, str) or not mid:
                    return _sidecar_err("agent-memories delete requires memory_id")
                store.delete(mid)
                return _sidecar_ok(_memory_response(store))
            if action == "source-recall":
                query = req.get("query") if isinstance(req.get("query"), str) else ""
                recalled = store.recall(
                    query, now_ms=int(time.time() * 1000), limit=int(req.get("limit") or 5),
                )
                response = _memory_response(store, query)
                response["recalls"] = _recall_wire(recalled)
                return _sidecar_ok(response)
            return _sidecar_err(f"unknown agent-memories action: {action}")
        if cmd == "agent-control":
            run_id = req.get("run_id")
            action = req.get("action")
            if action != "abort":
                return _sidecar_err("agent-control one-shot supports only abort")
            if not isinstance(run_id, str) or not run_id:
                return _sidecar_err("agent-control abort requires run_id")
            token = _AGENT_ABORTS.get(run_id)
            if token is None:
                return _sidecar_ok({"ok": False, "error": "unknown_run", "run_id": run_id})
            token.abort(str(req.get("reason") or "client_abort"))
            return _sidecar_ok({"ok": True, "run_id": run_id})
    except Exception as exc:  # noqa: BLE001
        return _sidecar_err(f"{type(exc).__name__}: {exc}")
    return None


def _handle_request(line: bytes) -> str:
    try:
        req = json.loads(line)
        cmd = req["cmd"]
        args = [str(a) for a in (req.get("args") or [])]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return json.dumps({"code": 1, "stderr": f"bad request: {exc}"})
    try:
        payload = bridge.dispatch(cmd, args)
        return json.dumps({"code": 0, "stdout": bridge._envelope_json(payload)})
    except (ValueError, SystemExit) as exc:           # 参数错误 / 护栏：业务失败，非 daemon 崩
        return json.dumps({"code": 1, "stderr": str(exc)})
    except Exception as exc:                            # 意外异常：隔离，daemon 存活
        return json.dumps({"code": 1, "stderr": f"{type(exc).__name__}: {exc}"})


async def _on_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await reader.readline()
        if not line:
            return
        cmd = None
        try:
            req = json.loads(line)
            cmd = req.get("cmd") if isinstance(req, dict) else None
        except json.JSONDecodeError:
            req = None
        if cmd == "chat-turn":               # 长连聊天:不在此 close,handler 跑完为止(KTD-3)
            await _handle_chat_turn(reader, writer, req)
            return
        if cmd == "agent-turn":
            await _handle_agent_turn(reader, writer, req if isinstance(req, dict) else {})
            return
        agent_resp = _handle_agent_json_command(req) if isinstance(req, dict) else None
        if agent_resp is not None:
            writer.write((agent_resp + "\n").encode("utf-8"))
            await writer.drain()
            return
        # legacy 一次性命令:单 readline→单 write→close(保原路不回归)。
        # to_thread(而非直接同步调用):bridge.dispatch 可能做真实同步 I/O(pandas/文件),
        # 若在事件循环线程上直接跑,会连带冻结本进程正服务的其它连接——包括一个正等待
        # confirm_required 人工 tap 的长连 chat-turn(它自己的 asyncio.wait_for 超时回调
        # 也调度在同一循环上,循环被占满时超时同样打不出来)。
        t0 = time.monotonic()
        resp = await asyncio.to_thread(_handle_request, line)
        logger.info("[conn] legacy cmd=%r 耗时=%.3fs", cmd, time.monotonic() - t0)
        writer.write((resp + "\n").encode("utf-8"))
        await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _serve() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = await asyncio.start_unix_server(_on_connection, path=str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o700)
    # PID 文件供 U9 运行时更新后 SIGHUP 重载。
    PID_PATH.write_text(str(os.getpid()))
    # U10：启动时把自身代码版本指纹持久化，Swift 端可快速比对陈旧进程。
    try:
        VERSION_PATH.write_text(bridge._sidecar_version_fingerprint())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[version] 无法写版本文件: %s", exc)

    # SIGHUP → exec 自身：重载改动的 Python（保住「改 Python 不重编」DX）。
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(
        signal.SIGHUP,
        lambda: os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)]),
    )
    # SIGTERM/SIGINT → 干净退出。
    stop = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))

    async with server:
        await stop
    # 清理：socket/pid/version 文件一起移除，避免 orphan 文件。
    for p in (SOCKET_PATH, PID_PATH, VERSION_PATH):
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    asyncio.run(_serve())
