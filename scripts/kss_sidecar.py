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
from datetime import UTC, datetime
from pathlib import Path
import inspect
from typing import Any, Callable
from uuid import uuid4

_REPO = Path(__file__).resolve().parents[1]
for p in (str(_REPO), str(_REPO / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import kss_app_bridge as bridge  # noqa: E402
from kss.agent import (  # noqa: E402
    AgentEvent,
    AgentMessage,
    AttachmentError,
    EventSequencer,
    KSSAgentService,
    MemoryStore,
    RunAdmissionError,
    RuntimeBusyError,
    SessionStore,
    SkillManager,
    ToolCall,
)
from kss.agent.desktop_host import (  # noqa: E402
    DesktopHarnessHost,
    DesktopTurnRequest,
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
_RESEARCH_SERVICE: Any | None = None
_RESEARCH_SERVICE_ROOTS: tuple[Path, Path] | None = None
_DESKTOP_HARNESS_HOST: DesktopHarnessHost | None = None


def _agent_service() -> KSSAgentService:
    """Return the process-wide stateful Runtime, rebuilding only when roots change."""
    global _AGENT_SERVICE, _AGENT_SERVICE_ROOTS
    roots = (Path(bridge.STATE_ROOT), Path(bridge.PROJECT_ROOT))
    if _AGENT_SERVICE is None or _AGENT_SERVICE_ROOTS != roots:
        if _AGENT_SERVICE is not None:
            _AGENT_SERVICE.close()
        _AGENT_SERVICE = KSSAgentService(*roots, start_provider=True)
        _AGENT_SERVICE_ROOTS = roots
    return _AGENT_SERVICE


def _research_service() -> Any | None:
    """Return optional ResearchService via lazy import; tests may monkeypatch."""
    global _RESEARCH_SERVICE, _RESEARCH_SERVICE_ROOTS
    roots = (Path(bridge.STATE_ROOT), Path(bridge.PROJECT_ROOT))
    if _RESEARCH_SERVICE is not None and _RESEARCH_SERVICE_ROOTS == roots:
        return _RESEARCH_SERVICE
    try:
        from kss.research.service import ResearchService  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - protocol must remain available before core lands.
        logger.info("[research] ResearchService unavailable: %s", exc)
        _RESEARCH_SERVICE = None
        _RESEARCH_SERVICE_ROOTS = roots
        return None
    _RESEARCH_SERVICE = ResearchService(
        state_root=bridge.STATE_ROOT,
        project_root=bridge.PROJECT_ROOT,
    )
    _RESEARCH_SERVICE_ROOTS = roots
    return _RESEARCH_SERVICE

def _session_store() -> SessionStore:
    return SessionStore(bridge.STATE_ROOT)


def _skill_manager() -> SkillManager:
    import kss_chat_loop as chat_loop

    return SkillManager(
        bridge.PROJECT_ROOT,
        bridge.STATE_ROOT,
        available_tools=[
            str(spec.get("name") or "") for spec in chat_loop.TOOL_SPECS
        ],
    )


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


# Harness 工具 execute 的 callId 授权表。不是 chat-turn pending，不构成第二写主人（KTD2）。
_HARNESS_GRANTS: dict[str, str] = {}
_HARNESS_GRANT_SURFACES: dict[str, str] = {}


def clear_harness_grants() -> None:
    _HARNESS_GRANTS.clear()
    _HARNESS_GRANT_SURFACES.clear()


def _drop_grants(*, surface: str | None = None) -> None:
    """作废授权。surface=None 清全部；桌面断连不得误伤研究 grant。"""
    if surface is None:
        clear_harness_grants()
        return
    for call_id, tagged in list(_HARNESS_GRANT_SURFACES.items()):
        if tagged == surface:
            _HARNESS_GRANTS.pop(call_id, None)
            _HARNESS_GRANT_SURFACES.pop(call_id, None)


def _normalize_grant_surface(surface: str | None) -> str:
    return "research" if surface == "research" else "desktop"


# 崩溃域：Node / Python / Swift 各自可不可用，但不各自拥有写权限。
# Live 写 = Node grant 然后 Python dispatch。任一段死亡 ⇒ 失败关闭（KTD2 / R6 / U7）。
_HARNESS_KERNEL_ALIVE = True
_HARNESS_ANSWERER_ALIVE = True


def harness_kernel_alive() -> bool:
    return bool(_HARNESS_KERNEL_ALIVE)


def harness_answerer_alive() -> bool:
    return bool(_HARNESS_ANSWERER_ALIVE)


def mark_harness_kernel_dead() -> None:
    """Node 内核死亡：作废全部 pending grant，不得再 dispatch。"""
    global _HARNESS_KERNEL_ALIVE
    _HARNESS_KERNEL_ALIVE = False
    clear_harness_grants()
    host = globals().get("_DESKTOP_HARNESS_HOST")
    if host is not None:
        host.invalidate_all()


def mark_harness_kernel_alive() -> None:
    global _HARNESS_KERNEL_ALIVE
    _HARNESS_KERNEL_ALIVE = True


def mark_harness_answerer_dead() -> None:
    """Swift/应答者断开：桌面待批写失败关闭。研究 pre-execute grant 不受影响。"""
    global _HARNESS_ANSWERER_ALIVE
    _HARNESS_ANSWERER_ALIVE = False
    _drop_grants(surface="desktop")
    host = globals().get("_DESKTOP_HARNESS_HOST")
    if host is not None:
        host.invalidate_all()


def mark_harness_answerer_alive() -> None:
    global _HARNESS_ANSWERER_ALIVE
    _HARNESS_ANSWERER_ALIVE = True


def reset_harness_crash_domains(*, kernel_alive: bool = True, answerer_alive: bool = True) -> None:
    """测试/启动复位。授权只存在于进程内存，永不落盘。"""
    global _HARNESS_KERNEL_ALIVE, _HARNESS_ANSWERER_ALIVE
    _HARNESS_KERNEL_ALIVE = bool(kernel_alive)
    _HARNESS_ANSWERER_ALIVE = bool(answerer_alive)
    clear_harness_grants()
    host = globals().get("_DESKTOP_HARNESS_HOST")
    if host is not None and (not kernel_alive or not answerer_alive):
        host.invalidate_all()


def grant_harness_write(call_id: str, command: str, *, surface: str = "desktop") -> None:
    """记录 Harness 已允许的一次 live 写 callId。无授权不得 dispatch。"""
    if not harness_kernel_alive():
        raise ValueError("harness kernel is not available")
    tagged = _normalize_grant_surface(surface)
    if tagged == "desktop" and not harness_answerer_alive():
        raise ValueError("harness answerer is not available")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("harness grant requires call_id")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("harness grant requires command")
    _HARNESS_GRANTS[call_id] = command
    _HARNESS_GRANT_SURFACES[call_id] = tagged


def revoke_harness_write(call_id: str) -> None:
    """作废一个 callId 的授权。中止/断开后迟到允许不得 dispatch。"""
    if isinstance(call_id, str) and call_id:
        _HARNESS_GRANTS.pop(call_id, None)
        _HARNESS_GRANT_SURFACES.pop(call_id, None)


def execute_harness_tool(
    *,
    name: str,
    args: dict[str, Any] | None,
    call_id: str,
    force_read: bool = False,
    override_command: str | None = None,
    override_positional: list[str] | None = None,
) -> dict:
    """Harness 插件 execute 的 RPC 意图入口。

    读：``_make_read_only_call``（碰 WRITE_COMMANDS 即失败）。
    写：仅当该 callId 当前已被 Harness allow，才 ``_execute_write``。
    """
    import kss_chat_loop as chat_loop  # 惰性：sidecar→chat_loop 单向

    tool_args = args if isinstance(args, dict) else {}
    registry = chat_loop.ToolRegistry()
    if name not in {str(spec["name"]) for spec in chat_loop.TOOL_SPECS}:
        return {"error": "unknown_tool", "name": name}

    spec = registry.spec(name) or {}
    spec_command = str(spec.get("command") or "")
    try:
        command, positional = registry.resolve(name, tool_args)
    except Exception as exc:  # noqa: BLE001
        return {"error": "resolve_failed", "detail": f"{type(exc).__name__}: {exc}"}

    if override_command is not None:
        command = override_command
        positional = list(override_positional or [])
        if command in bridge.WRITE_COMMANDS and spec_command not in bridge.WRITE_COMMANDS:
            return {
                "error": "read_only_violation",
                "hint": "read plugin cannot dispatch writes",
            }

    if force_read or command not in bridge.WRITE_COMMANDS:
        try:
            read_call = bridge._make_read_only_call(bridge.dispatch)
            payload = read_call(command, positional)
            return {"ok": True, "command": command, "result": payload}
        except (PermissionError, ValueError, SystemExit) as exc:
            return {
                "error": "read_only_violation",
                "detail": str(exc),
                "command": command,
            }

    if not harness_kernel_alive():
        clear_harness_grants()
        return {
            "error": "harness_unavailable",
            "hint": "Node kernel is not available; live dispatch is fail-closed",
        }
    surface = _HARNESS_GRANT_SURFACES.get(call_id, "desktop")
    if surface == "desktop" and not harness_answerer_alive():
        _drop_grants(surface="desktop")
        return {
            "error": "harness_unavailable",
            "hint": "desktop answerer is gone; live dispatch is fail-closed",
        }

    allowed = _HARNESS_GRANTS.get(call_id)
    if allowed != command:
        return {"error": "not_allowed", "hint": "Harness callId is not currently allowed"}
    # 一次性窗口：消费 grant 后再 dispatch。进程在此崩溃 ⇒ 无静默成功，
    # 同一 callId 不得在没有新的 Harness allow 时重试。
    _HARNESS_GRANTS.pop(call_id, None)
    _HARNESS_GRANT_SURFACES.pop(call_id, None)
    return _execute_write(command, positional)


def _reject_all(pending: dict, result: dict) -> None:
    """断连/SIGHUP/收尾:把所有未决 confirm 按拒收尾,防 loop 的 await 永挂(KTD-3/F3)。"""
    if result.get("error") == "disconnected":
        mark_harness_answerer_dead()
    for call_id, entry in list(pending.items()):
        revoke_harness_write(str(call_id))
        fut = entry.get("future")
        if fut is not None and not fut.done():
            fut.set_result(dict(result))
    pending.clear()


def _parse_confirm_message(msg: dict) -> tuple[Any, bool] | None:
    """chat-turn-confirm 与 agent-control confirm 投影到同一 grant 路径。"""
    if msg.get("cmd") == "chat-turn-confirm":
        return msg.get("call_id"), bool(msg.get("approved"))
    if msg.get("cmd") == "agent-control" and msg.get("action") == "confirm":
        return msg.get("call_id"), bool(msg.get("approved"))
    return None


async def _settle_granted_write(entry: dict, *, approved: bool) -> dict:
    """Chrome pending 只投影意图；dispatch 必须再过 execute_harness_tool 的 grant 检查。"""
    call_id = str(entry.get("call_id") or "")
    command = str(entry.get("command") or "")
    if not approved:
        revoke_harness_write(call_id)
        return {"error": "denied", "hint": "用户拒绝该写操作"}
    grant_harness_write(call_id, command)
    return await asyncio.to_thread(
        execute_harness_tool,
        name=str(entry.get("tool_name") or entry.get("tool") or ""),
        args=entry.get("tool_args") if isinstance(entry.get("tool_args"), dict) else {},
        call_id=call_id,
    )


def _desktop_harness_host() -> DesktopHarnessHost:
    """桌面 Harness 宿主。未注入会话时失败关闭，不把 Python loop 当主人。"""
    global _DESKTOP_HARNESS_HOST
    if _DESKTOP_HARNESS_HOST is None:
        _DESKTOP_HARNESS_HOST = DesktopHarnessHost(
            grant_write=grant_harness_write,
            revoke_grant=revoke_harness_write,
        )
        _DESKTOP_HARNESS_HOST.execute_tool = lambda **kwargs: execute_harness_tool(**kwargs)
    return _DESKTOP_HARNESS_HOST


def _project_session_event(event: dict[str, Any], host: DesktopHarnessHost) -> dict[str, Any] | None:
    """Harness SessionEvent → 既有皮肤帧词汇。不发明第二份 transcript。"""
    if not isinstance(event, dict):
        return None
    etype = str(event.get("type") or "")
    if etype in {"approval_request", "approval/request"}:
        call_id = str(event.get("call_id") or "")
        projected = host.project_confirm(call_id)
        projected["type"] = "confirm_required"
        return projected
    if etype in {"inbox_accepted", "inbox_rejected", "inbox_restored"}:
        items = event.get("queued_inputs") or ([event["item"]] if event.get("item") else [])
        operation = {
            "inbox_accepted": "accepted",
            "inbox_rejected": "rejected",
            "inbox_restored": "restored",
        }[etype]
        payload = {
            "type": "queue_update",
            "operation": event.get("operation") or operation,
            "queued_inputs": items,
            **_queue_counts([_queue_item_wire(i) for i in items]),
        }
        if event.get("item") is not None:
            payload["item"] = _queue_item_wire(event["item"])
        if event.get("reason"):
            payload["reason"] = event["reason"]
        return payload
    if etype == "chunk":
        return {**event, "type": "message_delta", "delta": event.get("delta") or event.get("text")}
    return event


async def _confirm_reader(reader: asyncio.StreamReader, pending: dict) -> None:
    """并发 reader：legacy chat-turn-confirm 适配到同一 Harness grant 路径。"""
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
        if not isinstance(msg, dict):
            continue
        parsed = _parse_confirm_message(msg)
        if parsed is None:
            continue
        call_id, approved = parsed
        logger.info("[chat] 收到 confirm call_id=%s approved=%s", call_id, approved)
        entry = pending.pop(call_id, None)
        if entry is None:
            logger.warning("[chat] 丢弃不匹配/已消费 confirm call_id=%r", call_id)
            continue
        fut = entry["future"]
        if fut.done():
            continue
        result = await _settle_granted_write(entry, approved=approved)
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
            call_id = uuid4().hex
            grant_harness_write(call_id, command)
            return await asyncio.to_thread(
                execute_harness_tool,
                name=tool_name,
                args=tool_args if isinstance(tool_args, dict) else {},
                call_id=call_id,
            )
        call_id = uuid4().hex                 # handler 生成,loop 不能选值(F1)
        fut = asyncio.get_running_loop().create_future()
        pending[call_id] = {
            "future": fut,
            "call_id": call_id,
            "command": command,
            "args": args,
            "tool_name": tool_name,
            "tool_args": tool_args,
        }
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
            revoke_harness_write(call_id)
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


def _validate_agent_turn_request(
    req: dict,
) -> tuple[str, str, str, list[str]] | tuple[None, None, str, list[str]]:
    allowed = {
        "cmd",
        "session_id",
        "client_turn_id",
        "input",
        "source_queue_id",
        "attachment_ids",
        "live_context_scope",
    }
    extra = set(req) - allowed
    if extra:
        return None, None, f"agent-turn unexpected fields: {sorted(extra)}", []
    session_id = req.get("session_id")
    client_turn_id = req.get("client_turn_id")
    text = req.get("input")
    if not isinstance(session_id, str) or not session_id:
        return None, None, "agent-turn requires session_id", []
    if not isinstance(client_turn_id, str) or not client_turn_id:
        return None, None, "agent-turn requires client_turn_id", []
    if not isinstance(text, str):
        return None, None, "agent-turn requires string input", []
    raw_attachment_ids = req.get("attachment_ids") or []
    if not isinstance(raw_attachment_ids, list) or any(
        not isinstance(item, str) for item in raw_attachment_ids
    ):
        return None, None, "agent-turn attachment_ids must be a string array", []
    return session_id, client_turn_id, text, raw_attachment_ids


def _agent_wire_message(message: AgentMessage) -> dict[str, Any]:
    attachment_ids = message.metadata.get("attachment_ids")
    attachments: list[dict[str, Any]] = []
    if isinstance(attachment_ids, list):
        store = _agent_service().attachments
        for attachment_id in attachment_ids:
            if not isinstance(attachment_id, str):
                continue
            try:
                attachments.append(store.load_record(attachment_id).to_payload())
            except Exception:
                continue
    return {
        "id": message.id,
        "role": message.role,
        "text": message.content,
        "content": message.content,
        "timestamp": message.timestamp,
        "tool_calls": [asdict(call) for call in message.tool_calls],
        "content_blocks": [
            block.to_payload() for block in message.content_blocks
        ],
        "attachments": attachments,
        "metadata": message.metadata,
    }


def _session_summary(state: Any, messages: list[AgentMessage] | None = None) -> dict[str, Any]:
    meta = dict(getattr(state, "metadata", {}) or {})
    status = getattr(state, "status", "running")
    route = meta.get("provider_route")
    return {
        "session_id": state.session_id,
        "title": meta.get("title") or state.session_id,
        "archived": status == "archived",
        "updated_at": str(meta.get("updated_at") or 0),
        "messages": [_agent_wire_message(m) for m in (messages or [])],
        "queued_inputs": _queued_inputs_wire(session_id=state.session_id),
        "provider_route": route if isinstance(route, dict) else None,
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
        "skills": [
            {
                **skill.as_dict(),
                "pinned": skill.id in pinned or skill.name in pinned,
            }
            for skill in found
        ],
        "diagnostics": [{
            "code": item.code,
            "message": item.message,
            "path": str(item.path) if item.path is not None else None,
        } for item in diagnostics],
        "status": manager.status(),
    }


def _attachment_wire(record: Any, *, status: str = "ready") -> dict[str, Any]:
    payload = record.to_payload() if hasattr(record, "to_payload") else dict(record)
    payload["name"] = payload.get("name") or payload.get("filename") or "附件"
    payload.setdefault("status", status)
    return payload


def _attachments_response(service: KSSAgentService) -> dict[str, Any]:
    return {
        "attachments": [
            _attachment_wire(record) for record in service.attachments.list_records()
        ]
    }


def _providers_action_payload(req: dict[str, Any]) -> dict[str, Any]:
    service = _agent_service()
    action = str(req.get("action") or "list")
    if action in {"list", "catalog"}:
        payload = service.provider_catalog()
    elif action == "refresh":
        provider_id = req.get("provider_id")
        payload = service.provider_catalog(
            refresh=True,
            provider_id=provider_id if isinstance(provider_id, str) else None,
        )
    elif action == "set_route":
        primary = req.get("primary")
        fallback = req.get("fallback")
        if not isinstance(primary, dict):
            return {"status": "error", "error": "agent-providers set_route requires primary"}
        if fallback is not None and not isinstance(fallback, dict):
            return {"status": "error", "error": "agent-providers fallback must be an object"}
        payload = service.set_provider_routes(primary=primary, fallback=fallback)
    elif action == "reload_credentials":
        socket_path = req.get("socket_path")
        nonce = req.get("nonce")
        payload = service.reload_provider_credentials(
            socket_path=socket_path if isinstance(socket_path, str) else None,
            nonce=nonce if isinstance(nonce, str) else None,
        )
    elif action == "test":
        primary = req.get("primary")
        fallback = req.get("fallback")
        if primary is not None and not isinstance(primary, dict):
            return {"status": "error", "error": "agent-providers test primary must be an object"}
        if fallback is not None and not isinstance(fallback, dict):
            return {"status": "error", "error": "agent-providers test fallback must be an object"}
        payload = service.test_provider_connection(primary=primary, fallback=fallback)
    else:
        return {"status": "error", "error": f"unknown agent-providers action: {action}"}
    if not isinstance(payload.get("providers"), list):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for model in payload.get("models") or []:
            if not isinstance(model, dict):
                continue
            provider_id = str(model.get("provider_id") or "unknown")
            grouped.setdefault(provider_id, []).append(model)
        payload["providers"] = [
            {
                "id": provider_id,
                "name": provider_id,
                "auth_kind": "api_key",
                "models": models,
            }
            for provider_id, models in sorted(grouped.items())
        ]
    return payload


def _attachments_action_payload(req: dict[str, Any]) -> dict[str, Any]:
    service = _agent_service()
    action = str(req.get("action") or "list")
    if action == "list":
        return _attachments_response(service)
    if action == "import":
        session_id = req.get("session_id")
        path = req.get("path")
        extracted_text = req.get("extracted_text")
        if not isinstance(session_id, str) or not session_id:
            return {"error": "agent-attachments import requires session_id"}
        if not isinstance(path, str) or not path:
            return {"error": "agent-attachments import requires path"}
        try:
            record = service.import_attachment(
                path,
                extracted_text=extracted_text if isinstance(extracted_text, str) else None,
            )
        except AttachmentError as exc:
            return {
                "error": exc.code,
                "attachment": {
                    "id": "",
                    "name": Path(path).name or "附件",
                    "status": "error",
                    "error": str(exc),
                },
                **_attachments_response(service),
            }
        return {
            "attachment": _attachment_wire(record),
            **_attachments_response(service),
        }
    if action == "remove":
        attachment_id = req.get("attachment_id")
        if not isinstance(attachment_id, str) or not attachment_id:
            return {"error": "agent-attachments remove requires attachment_id"}
        service.attachments.remove_record(attachment_id)
        return _attachments_response(service)
    return {"error": f"unknown agent-attachments action: {action}"}


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


def _recall_wire(items: list[Any]) -> list[dict[str, Any]]:
    """把本轮实际召回映射为可展示来源；优先保留真实 memory ID/source."""
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if hasattr(item, "as_dict") and callable(item.as_dict):
            payload = dict(item.as_dict())
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            payload = {"id": f"recall-{index}", "excerpt": str(item), "injection_text": str(item)}
        payload.setdefault("id", f"recall-{index}")
        payload.setdefault("title", _memory_recall_title(payload))
        payload.setdefault("source", _memory_recall_source(payload))
        payload.setdefault("excerpt", payload.get("injection_text") or payload.get("content") or payload.get("text") or "")
        out.append(payload)
    return out


def _memory_recall_title(payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "memory")
    if payload.get("review_required"):
        return f"{kind} · 待复核"
    return kind


def _memory_recall_source(payload: dict[str, Any]) -> str | None:
    source = payload.get("source")
    if source:
        return str(source)
    source_session = payload.get("source_session")
    source_entry = payload.get("source_entry")
    if source_entry:
        return f"{source_session or 'session'} · {source_entry}"
    return str(source_session) if source_session else None


async def _maybe_call(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _call_with_supported_kwargs(function: Callable[..., Any], **kwargs: Any) -> Any:
    """Call a service compatibility hook with only the kwargs it declares."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(**kwargs)
    parameters = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return function(**kwargs)
    accepted = {key: value for key, value in kwargs.items() if key in parameters}
    return function(**accepted)


def _queue_item_wire(item: Any) -> dict[str, Any]:
    if hasattr(item, "as_dict") and callable(item.as_dict):
        payload = dict(item.as_dict())
    elif hasattr(item, "__dataclass_fields__"):
        payload = asdict(item)
    elif isinstance(item, dict):
        payload = dict(item)
    else:
        payload = {"id": str(item)}
    if "input" not in payload and "content" in payload:
        payload["input"] = payload.get("content")
    if "content" not in payload and "input" in payload:
        payload["content"] = payload.get("input")
    return payload


def _queue_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "steering_count": sum(1 for item in items if item.get("mode") == "steering"),
        "follow_up_count": sum(1 for item in items if item.get("mode") == "follow_up"),
    }


def _queued_inputs_wire(
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    service = _agent_service()
    method = getattr(service, "queued_inputs", None) or getattr(service, "list_queued", None)
    if method is None:
        return []
    result = _call_with_supported_kwargs(
        method,
        session_id=session_id,
        run_id=run_id,
    )
    if inspect.isawaitable(result):
        if hasattr(result, "close"):
            result.close()
        raise RuntimeError("queue hydration requires a synchronous service method")
    if isinstance(result, dict):
        raw_items = result.get("queued_inputs") or result.get("items") or []
    else:
        raw_items = result or []
    return [_queue_item_wire(item) for item in raw_items]


async def _emit_queue_update(
    emit: Callable[[str, dict[str, Any]], Any],
    *,
    operation: str,
    item: Any | None = None,
    queued_inputs: list[dict[str, Any]] | None = None,
    reason: str | None = None,
) -> None:
    items = list(queued_inputs or [])
    if item is not None and not items:
        items = [_queue_item_wire(item)]
    payload: dict[str, Any] = {
        "operation": operation,
        "queued_inputs": items,
        **_queue_counts(items),
    }
    if item is not None:
        payload["item"] = _queue_item_wire(item)
    if reason is not None:
        payload["reason"] = reason
    await _maybe_call(emit("queue_update", payload))


async def _service_queue_control(
    service: KSSAgentService,
    *,
    action: str,
    run_id: str,
    client_message_id: str,
    input_text: str,
    source_queue_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    method_name = "steer" if action == "steer" else "follow_up"
    method = getattr(service, method_name, None)
    if method is None:
        return False, {"error": "queue_unavailable", "reason": f"service.{method_name} is not implemented"}
    result = await _maybe_call(
        _call_with_supported_kwargs(
            method,
            run_id=run_id,
            client_message_id=client_message_id,
            input=input_text,
            input_text=input_text,
            source_queue_id=source_queue_id,
        )
    )
    if isinstance(result, dict):
        return not bool(result.get("error") or result.get("rejected")), result
    return True, {"item": _queue_item_wire(result)}


def _service_queue_control_sync(
    service: KSSAgentService,
    *,
    action: str,
    run_id: str,
    client_message_id: str,
    input_text: str,
    source_queue_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    method_name = "steer" if action == "steer" else "follow_up"
    method = getattr(service, method_name, None)
    if method is None:
        return False, {"error": "queue_unavailable", "reason": f"service.{method_name} is not implemented"}
    result = _call_with_supported_kwargs(
        method,
        run_id=run_id,
        client_message_id=client_message_id,
        input=input_text,
        input_text=input_text,
        source_queue_id=source_queue_id,
    )
    if inspect.isawaitable(result):
        return False, {"error": "queue_async_unavailable", "reason": "one-shot queue control requires a synchronous service method"}
    if isinstance(result, dict):
        return not bool(result.get("error") or result.get("rejected")), result
    return True, {"item": _queue_item_wire(result)}


async def _service_queue_discard(
    service: KSSAgentService,
    *,
    session_id: str,
    queue_id: str,
) -> tuple[bool, dict[str, Any]]:
    method = getattr(service, "discard_queued_input", None) or getattr(service, "discard_queued", None)
    if method is None:
        return False, {"error": "queue_unavailable", "reason": "service.discard_queued_input is not implemented"}
    result = await _maybe_call(
        _call_with_supported_kwargs(method, session_id=session_id, queue_id=queue_id)
    )
    if isinstance(result, dict):
        return not bool(result.get("error") or result.get("rejected")), result
    return True, {"item": _queue_item_wire(result)}


def _service_queue_discard_sync(
    service: KSSAgentService,
    *,
    session_id: str,
    queue_id: str,
) -> tuple[bool, dict[str, Any]]:
    method = getattr(service, "discard_queued_input", None) or getattr(service, "discard_queued", None)
    if method is None:
        return False, {"error": "queue_unavailable", "reason": "service.discard_queued_input is not implemented"}
    result = _call_with_supported_kwargs(method, session_id=session_id, queue_id=queue_id)
    if inspect.isawaitable(result):
        return False, {"error": "queue_async_unavailable", "reason": "one-shot queue discard requires a synchronous service method"}
    if isinstance(result, dict):
        return not bool(result.get("error") or result.get("rejected")), result
    return True, {"item": _queue_item_wire(result)}


_RESEARCH_ACTION_METHODS: dict[str, tuple[str, ...]] = {
    "create": ("create", "create_goal", "create_research"),
    "list": ("list", "list_goals", "list_research"),
    "open": ("open", "open_goal", "get", "get_goal"),
    "start": ("start", "start_goal", "start_research"),
    "pause": ("pause", "pause_goal"),
    "resume": ("resume", "resume_goal"),
    "cancel": ("cancel", "cancel_goal"),
    "retry_task": ("retry_task", "retry", "retry_research_task"),
    "refresh_snapshot": ("refresh_snapshot", "snapshot", "get_snapshot"),
    "import_corpus": ("import_corpus", "import_analyst_corpus"),
    "audit": ("audit", "audit_goal"),
}

_ARTIFACT_ACTION_METHODS: dict[str, tuple[str, ...]] = {
    "list": ("list_artifacts", "artifacts"),
    "export_draft": ("export_draft", "export_artifact_draft"),
    "publish": ("publish", "publish_artifact"),
}


def _research_unavailable_payload() -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "ok": False,
        "error": "research_unavailable",
        "hint": "kss.research.service.ResearchService is not available",
    }


def _find_method(service: Any, candidates: tuple[str, ...]) -> Callable[..., Any] | None:
    for name in candidates:
        method = getattr(service, name, None)
        if callable(method):
            return method
    return None


def _research_action_payload(req: dict[str, Any]) -> dict[str, Any]:
    service = _research_service()
    if service is None:
        return _research_unavailable_payload()
    action = str(req.get("action") or "list")
    candidates = _RESEARCH_ACTION_METHODS.get(action)
    if candidates is None:
        return {"protocol_version": 1, "ok": False, "error": f"unknown agent-research action: {action}"}
    method = _find_method(service, candidates)
    if method is None:
        return {
            "protocol_version": 1,
            "ok": False,
            "error": "research_action_unavailable",
            "action": action,
        }
    result = _call_with_supported_kwargs(
        method,
        action=action,
        goal=req.get("goal"),
        goal_id=req.get("goal_id") or req.get("id"),
        task_id=req.get("task_id"),
        artifact_id=req.get("artifact_id"),
        input=req.get("input"),
        query=req.get("query"),
        origin=req.get("origin"),
        cadence=req.get("cadence"),
        profile_ids=req.get("profile_ids"),
        limit=req.get("limit"),
        cursor=req.get("cursor"),
        payload=req,
    )
    if inspect.isawaitable(result):
        if hasattr(result, "close"):
            result.close()
        return {
            "protocol_version": 1,
            "ok": False,
            "error": "research_async_unavailable",
            "action": action,
        }
    payload = result if isinstance(result, dict) else {"result": result}
    payload.setdefault("ok", not bool(payload.get("error")))
    payload.setdefault("protocol_version", 1)
    payload.setdefault("event", action)
    if "goal" not in payload and (req.get("goal") or req.get("goal_id")):
        payload["goal"] = req.get("goal") or req.get("goal_id")
    return payload


def _artifact_action_payload(req: dict[str, Any]) -> dict[str, Any]:
    service = _research_service()
    if service is None:
        return _research_unavailable_payload()
    action = str(req.get("action") or "list")
    candidates = _ARTIFACT_ACTION_METHODS.get(action)
    if candidates is None:
        return {"protocol_version": 1, "ok": False, "error": f"unknown agent-artifacts action: {action}"}
    method = _find_method(service, candidates)
    if method is None:
        return {
            "protocol_version": 1,
            "ok": False,
            "error": "artifact_action_unavailable",
            "action": action,
        }
    result = _call_with_supported_kwargs(
        method,
        action=action,
        goal_id=req.get("goal_id") or req.get("id"),
        artifact_id=req.get("artifact_id"),
        format=req.get("format"),
        payload=req,
    )
    if inspect.isawaitable(result):
        if hasattr(result, "close"):
            result.close()
        return {
            "protocol_version": 1,
            "ok": False,
            "error": "research_async_unavailable",
            "action": action,
        }
    payload = result if isinstance(result, dict) else {"result": result}
    payload.setdefault("ok", not bool(payload.get("error")))
    payload.setdefault("protocol_version", 1)
    payload.setdefault("event", action)
    if "goal" not in payload and (req.get("goal_id") or req.get("id")):
        payload["goal"] = req.get("goal_id") or req.get("id")
    return payload


def _normalize_research_frame(raw: Any, *, goal_id: str, sequence: int) -> dict[str, Any]:
    payload = dict(raw) if isinstance(raw, dict) else {"data": raw}
    resolved_sequence = int(payload.get("sequence") or sequence)
    frame = {
        "protocol_version": 1,
        "goal": payload.get("goal") or payload.get("goal_id") or goal_id,
        "goal_id": payload.get("goal_id") or payload.get("goal") or goal_id,
        "event_id": payload.get("event_id") or f"{goal_id}:{resolved_sequence}",
        "event": payload.get("event") or payload.get("type") or "research_event",
        "type": payload.get("type") or payload.get("event") or "research_event",
        "sequence": resolved_sequence,
        "timestamp": payload.get("timestamp") or payload.get("created_at")
        or datetime.now(UTC).isoformat(),
    }
    frame.update(payload)
    frame["protocol_version"] = 1
    frame.setdefault("goal", goal_id)
    frame.setdefault("goal_id", goal_id)
    frame.setdefault("event_id", f"{goal_id}:{resolved_sequence}")
    frame.setdefault("event", frame.get("type") or "research_event")
    frame.setdefault("type", frame.get("event") or "research_event")
    frame["sequence"] = int(frame.get("sequence") or sequence)
    frame.setdefault("timestamp", datetime.now(UTC).isoformat())
    return frame


def _research_events_iter(service: Any, *, goal_id: str, after_sequence: int) -> Any:
    method = _find_method(
        service,
        ("events", "replay_events", "list_events", "event_stream", "subscribe_events"),
    )
    if method is None:
        return []
    return _call_with_supported_kwargs(
        method,
        goal_id=goal_id,
        goal=goal_id,
        after_sequence=after_sequence,
    )


def _research_goal_snapshot(service: Any, *, goal_id: str) -> dict[str, Any] | None:
    method = _find_method(service, ("open_goal", "get_goal"))
    if method is None:
        return None
    result = _call_with_supported_kwargs(
        method,
        goal_id=goal_id,
        goal=goal_id,
    )
    if inspect.isawaitable(result):
        return None
    if not isinstance(result, dict):
        return None
    detail = result.get("detail") or result.get("goal")
    return detail if isinstance(detail, dict) else None


async def _handle_agent_research_events(
    writer: asyncio.StreamWriter,
    req: dict[str, Any],
) -> None:
    goal_id = req.get("goal_id") or req.get("goal")
    if not isinstance(goal_id, str) or not goal_id:
        writer.write((json.dumps({
            "protocol_version": 1,
            "goal": "",
            "event": "error",
            "type": "error",
            "sequence": 1,
            "error": "agent-research-events requires goal_id",
        }, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        return
    after_sequence = int(req.get("after_sequence") or 0)
    service = _research_service()
    if service is None:
        writer.write((json.dumps({
            **_research_unavailable_payload(),
            "goal": goal_id,
            "goal_id": goal_id,
            "event": "error",
            "type": "error",
            "sequence": after_sequence + 1,
        }, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        return
    snapshot = _research_goal_snapshot(service, goal_id=goal_id)
    if snapshot is not None:
        snapshot_frame = {
            "protocol_version": 1,
            "goal": goal_id,
            "goal_id": goal_id,
            "event": "research_snapshot",
            "type": "research_snapshot",
            "event_id": f"snapshot:{goal_id}:{after_sequence}",
            "sequence": after_sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            "snapshot": snapshot,
        }
        writer.write(
            (json.dumps(snapshot_frame, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await writer.drain()
    stream = _research_events_iter(service, goal_id=goal_id, after_sequence=after_sequence)
    if inspect.isawaitable(stream):
        stream = await stream
    sequence = after_sequence
    if hasattr(stream, "__aiter__"):
        async for item in stream:
            sequence += 1
            frame = _normalize_research_frame(item, goal_id=goal_id, sequence=sequence)
            sequence = int(frame["sequence"])
            writer.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
        return
    for item in list(stream or []):
        sequence += 1
        frame = _normalize_research_frame(item, goal_id=goal_id, sequence=sequence)
        sequence = int(frame["sequence"])
        if sequence <= after_sequence:
            continue
        writer.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()


async def _agent_control_reader(
    reader: asyncio.StreamReader,
    pending: dict,
    abort_token: Any,
    run_id: str,
    *,
    host: DesktopHarnessHost,
    session_id: str,
    emit_control: Callable[[str, dict[str, Any]], Any],
) -> None:
    """agent-turn 同连接控制：confirm/abort/steer 映射 Harness grant、cancel、inbox。"""
    while True:
        try:
            line = await reader.readline()
        except (ConnectionError, asyncio.IncompleteReadError):
            line = b""
        if not line:
            host.abort("disconnected")
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
            if action == "abort" and msg.get("run_id") in (None, "", run_id):
                reason = str(msg.get("reason") or "client_abort")
                host.abort(reason)
                _reject_all(pending, {"error": "aborted", "hint": "用户中止本轮"})
                abort_token.abort(reason)
                return
            if action in {"steer", "follow_up"}:
                if msg.get("run_id") not in (None, "", run_id):
                    await _emit_queue_update(
                        emit_control,
                        operation="rejected",
                        reason="run_id_mismatch",
                    )
                    continue
                client_message_id = msg.get("client_message_id")
                input_text = msg.get("input")
                if not isinstance(client_message_id, str) or not client_message_id:
                    await _emit_queue_update(
                        emit_control,
                        operation="rejected",
                        reason="missing_client_message_id",
                    )
                    continue
                if not isinstance(input_text, str) or not input_text.strip():
                    await _emit_queue_update(
                        emit_control,
                        operation="rejected",
                        reason="missing_input",
                    )
                    continue
                ok, payload = host.enqueue(
                    mode=action,
                    client_message_id=client_message_id,
                    input_text=input_text,
                    session_id=session_id,
                    run_id=run_id,
                    source_queue_id=msg.get("source_queue_id")
                    if isinstance(msg.get("source_queue_id"), str)
                    else None,
                )
                item = payload.get("item") if isinstance(payload, dict) else None
                queued_inputs = (
                    payload.get("queued_inputs") if isinstance(payload, dict) else None
                )
                reason = (
                    payload.get("reason") or payload.get("error")
                    if isinstance(payload, dict)
                    else None
                )
                operation = payload.get("operation") if isinstance(payload, dict) else None
                await _emit_queue_update(
                    emit_control,
                    operation=operation or ("accepted" if ok else "rejected"),
                    item=item,
                    queued_inputs=queued_inputs,
                    reason=reason if not ok else None,
                )
                continue
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
        resolved = host.resolve_approval(str(call_id), approved)
        if not resolved:
            logger.warning("[agent] 迟到允许无效 call_id=%r", call_id)
            continue
        fut = entry.get("future")
        if fut is not None and not fut.done():
            fut.set_result({"ok": bool(approved)} if approved else {"error": "denied"})


async def _handle_agent_turn(reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter, req: dict) -> None:
    """Agent v1 传输：Harness SessionEvent 投影到既有皮肤帧；不把 Python loop 当主人。"""
    session_id, client_turn_id, user_text_or_error, attachment_ids = (
        _validate_agent_turn_request(req)
    )
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
    active_run_id: str | None = transport_run_id
    wire_sequence = 0
    host = _desktop_harness_host()
    host.execute_tool = lambda **kwargs: execute_harness_tool(**kwargs)
    abort_token = host.abort_token
    store = _session_store()

    async def write_agent_frame(
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        event: AgentEvent | None = None,
    ) -> None:
        nonlocal wire_sequence
        async with writer_lock:
            wire_sequence += 1
            if event is not None:
                frame = asdict(event)
                nested = frame.pop("payload", {})
                if isinstance(nested, dict):
                    frame.update(nested)
            else:
                frame = {
                    "protocol_version": 1,
                    "id": uuid4().hex,
                    "session_id": session_id,
                    "run_id": active_run_id or transport_run_id,
                    "parent_id": None,
                    "timestamp": time.time(),
                    "type": event_type,
                }
                if payload:
                    frame.update(payload)
            frame["sequence"] = wire_sequence
            if frame.get("type") == "error" and "error" not in frame:
                frame["error"] = frame.get("message") or "agent runtime error"
            writer.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()

    async def emit_projected(raw: dict[str, Any]) -> None:
        projected = _project_session_event(raw, host)
        if not projected:
            return
        event_type = str(projected.get("type") or "message_delta")
        payload = {k: v for k, v in projected.items() if k != "type"}
        if event_type == "confirm_required":
            call_id = str(payload.get("call_id") or "")
            if call_id and call_id not in pending:
                pending[call_id] = {"call_id": call_id, "chrome": True}
        await write_agent_frame(event_type, payload)

    _AGENT_ABORTS[transport_run_id] = abort_token
    control_task = asyncio.create_task(
        _agent_control_reader(
            reader,
            pending,
            abort_token,
            transport_run_id,
            host=host,
            session_id=session_id,
            emit_control=write_agent_frame,
        )
    )

    try:
        admission = store.try_start_run(
            session_id,
            run_id=transport_run_id,
            client_turn_id=client_turn_id,
        )
        if not admission.admitted:
            payload = {
                "existing_run_id": admission.run_id,
                "client_turn_id": client_turn_id,
            }
            if admission.status == "running":
                await write_agent_frame("agent_start", payload)
                await write_agent_frame("agent_end", {
                    **payload,
                    "reason": "already_running",
                    "termination_reason": "already_running",
                })
            elif admission.status == "completed":
                await write_agent_frame("agent_start", payload)
                await write_agent_frame("agent_end", {
                    **payload,
                    "reason": "duplicate_completed",
                    "termination_reason": "duplicate_completed",
                })
            else:
                await write_agent_frame("error", {
                    **payload,
                    "error": "interrupted or failed turns require a new client_turn_id",
                    "is_error": True,
                })
                await write_agent_frame("agent_end", {
                    **payload,
                    "reason": "retry_requires_new_client_turn_id",
                    "termination_reason": "retry_requires_new_client_turn_id",
                })
            return
        source_queue_id = req.get("source_queue_id")
        if not isinstance(source_queue_id, str) or not source_queue_id:
            source_queue_id = None
        user_message = AgentMessage(
            id=uuid4().hex,
            role="user",
            content=str(user_text_or_error or ""),
            timestamp=time.time(),
        )
        store.append_message(
            session_id,
            user_message,
            source_queue_id=source_queue_id,
        )
        await write_agent_frame("agent_start", {"client_turn_id": client_turn_id})
        request = DesktopTurnRequest(
            session_id=session_id,
            client_turn_id=client_turn_id,
            input=str(user_text_or_error or ""),
            run_id=transport_run_id,
            attachment_ids=tuple(attachment_ids or ()),
            source_queue_id=source_queue_id,
        )
        mark_harness_kernel_alive()
        mark_harness_answerer_alive()
        result = await host.run(request, emit_projected)
        if result.status == "unavailable":
            # 本回合失败关闭。不要把「未注入会话」锁成内核永久死亡，
            # 否则会误伤同进程里的研究 grant。Node 子进程退出由 U8 监督器标死。
            await write_agent_frame("error", {
                "error": result.error or "harness_session_unavailable",
                "is_error": True,
            })
            store.finish_run(
                session_id,
                transport_run_id,
                status="interrupted",
                reason="harness_unavailable",
            )
            await write_agent_frame("agent_end", {
                "reason": "harness_unavailable",
                "termination_reason": "harness_unavailable",
            })
            return
        assistant_text = result.assistant_text or ""
        if assistant_text:
            store.append_message(
                session_id,
                AgentMessage(
                    id=uuid4().hex,
                    role="assistant",
                    content=assistant_text,
                    timestamp=time.time(),
                ),
            )
        for tool_result in result.tool_results:
            store.append_message(
                session_id,
                AgentMessage(
                    id=uuid4().hex,
                    role="tool",
                    content=json.dumps(tool_result, ensure_ascii=False),
                    timestamp=time.time(),
                    tool_calls=(
                        ToolCall(
                            id=uuid4().hex,
                            name="run_task",
                            arguments={},
                            result=tool_result,
                        ),
                    ),
                ),
            )
        aborted = abort_token.aborted or result.status == "aborted"
        reason = abort_token.reason or result.error or ("client_abort" if aborted else "stop")
        store.finish_run(
            session_id,
            transport_run_id,
            status="aborted" if aborted else "completed",
            reason=reason,
        )
        await write_agent_frame("agent_end", {
            "reason": reason,
            "termination_reason": reason,
            "status": "aborted" if aborted else "completed",
        })
    except (KeyError, ValueError) as exc:
        store.finish_run(
            session_id,
            transport_run_id,
            status="failed",
            reason="bad_request",
        )
        await write_agent_frame("error", {"error": str(exc), "is_error": True})
        await write_agent_frame("agent_end", {
            "reason": "bad_request",
            "termination_reason": "bad_request",
        })
    except RunAdmissionError as exc:
        rejected = _AgentFrameEmitter(writer, session_id, transport_run_id)
        payload = {
            "existing_run_id": exc.existing_run_id,
            "client_turn_id": client_turn_id,
        }
        if exc.status == "running":
            reason = "already_running"
            await rejected.emit("agent_start", payload)
            await rejected.emit("agent_end", {**payload, "reason": reason, "termination_reason": reason})
        elif exc.status == "completed":
            reason = "duplicate_completed"
            await rejected.emit("agent_start", payload)
            await rejected.emit("agent_end", {**payload, "reason": reason, "termination_reason": reason})
        else:
            reason = "retry_requires_new_client_turn_id"
            await rejected.emit("error", {
                **payload,
                "error": "interrupted or failed turns require a new client_turn_id",
                "is_error": True,
            })
            await rejected.emit("agent_end", {**payload, "reason": reason, "termination_reason": reason})
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
        host.invalidate_all()
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
        if cmd == "harness-tool-grant":
            grant_harness_write(
                str(req.get("call_id") or ""),
                str(req.get("command") or ""),
                surface=str(req.get("surface") or "research"),
            )
            return _sidecar_ok({"ok": True, "call_id": req.get("call_id")})
        if cmd == "harness-tool-execute":
            raw_args = req.get("args")
            return _sidecar_ok(execute_harness_tool(
                name=str(req.get("name") or ""),
                args=raw_args if isinstance(raw_args, dict) else {},
                call_id=str(req.get("call_id") or ""),
                force_read=bool(req.get("force_read")),
            ))
        if cmd == "agent-research":
            return _sidecar_ok(_research_action_payload(req))
        if cmd == "agent-artifacts":
            return _sidecar_ok(_artifact_action_payload(req))
        if cmd == "agent-providers":
            return _sidecar_ok(_providers_action_payload(req))
        if cmd == "agent-attachments":
            return _sidecar_ok(_attachments_action_payload(req))
        if cmd == "agent-session":
            store = _session_store()
            action = req.get("action") or "open"
            if action == "create":
                route = _agent_service().route_store.load().primary.as_dict()
                state = store.create_session(
                    session_id=req.get("session_id"),
                    metadata={
                        "title": req.get("title") or req.get("session_id") or "",
                        "provider_route": route,
                    },
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
            if action == "set_provider_route":
                sid = req.get("session_id")
                raw_route = req.get("provider_route")
                if not isinstance(sid, str) or not sid:
                    return _sidecar_err("agent-session set_provider_route requires session_id")
                if not isinstance(raw_route, dict):
                    return _sidecar_err("agent-session set_provider_route requires provider_route")
                from kss.agent.provider_route import ProviderRoute  # noqa: PLC0415

                route = ProviderRoute.from_dict(raw_route)
                state = store.set_provider_route(sid, route.as_dict())
                return _sidecar_ok(_session_response(
                    store, selected_session_id=state.session_id,
                ))
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
        if cmd == "agent-queue":
            service = _agent_service()
            action = req.get("action") or "list"
            session_id = req.get("session_id")
            queue_id = req.get("queue_id")
            if action == "list":
                if not isinstance(session_id, str) or not session_id:
                    return _sidecar_err("agent-queue list requires session_id")
                items = _queued_inputs_wire(session_id=session_id)
                return _sidecar_ok({"queued_inputs": items, **_queue_counts(items)})
            if action == "discard":
                if not isinstance(session_id, str) or not session_id:
                    return _sidecar_err("agent-queue discard requires session_id")
                if not isinstance(queue_id, str) or not queue_id:
                    return _sidecar_err("agent-queue discard requires queue_id")
                ok, payload = _service_queue_discard_sync(
                    service, session_id=session_id, queue_id=queue_id
                )
                items = _queued_inputs_wire(session_id=session_id)
                return _sidecar_ok({
                    "ok": ok,
                    "operation": "discarded" if ok else "rejected",
                    "queued_inputs": items,
                    **_queue_counts(items),
                    **(payload if isinstance(payload, dict) else {}),
                })
            return _sidecar_err(f"unknown agent-queue action: {action}")
        if cmd == "agent-control":
            run_id = req.get("run_id")
            action = req.get("action")
            if not isinstance(run_id, str) or not run_id:
                return _sidecar_err("agent-control requires run_id")
            if action == "abort":
                token = _AGENT_ABORTS.get(run_id)
                if token is None:
                    return _sidecar_ok({"ok": False, "error": "unknown_run", "run_id": run_id})
                token.abort(str(req.get("reason") or "client_abort"))
                return _sidecar_ok({"ok": True, "run_id": run_id})
            if action in {"steer", "follow_up"}:
                client_message_id = req.get("client_message_id")
                input_text = req.get("input")
                if not isinstance(client_message_id, str) or not client_message_id:
                    return _sidecar_err("agent-control queue requires client_message_id")
                if not isinstance(input_text, str) or not input_text.strip():
                    return _sidecar_err("agent-control queue requires input")
                service = _agent_service()
                ok, payload = _service_queue_control_sync(
                    service,
                    action=action,
                    run_id=run_id,
                    client_message_id=client_message_id,
                    input_text=input_text,
                    source_queue_id=req.get("source_queue_id")
                    if isinstance(req.get("source_queue_id"), str)
                    else None,
                )
                item = payload.get("item") if isinstance(payload, dict) else None
                items = payload.get("queued_inputs") if isinstance(payload, dict) else None
                if not isinstance(items, list):
                    items = [item] if item is not None else []
                return _sidecar_ok({
                    "ok": ok,
                    "operation": "accepted" if ok else "rejected",
                    "item": item,
                    "queued_inputs": [_queue_item_wire(item) for item in items],
                    **_queue_counts([_queue_item_wire(item) for item in items]),
                    **({"reason": payload.get("reason") or payload.get("error")}
                       if isinstance(payload, dict) and not ok else {}),
                })
            return _sidecar_err("agent-control one-shot supports abort, steer and follow_up")
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
        if cmd == "agent-research-events":
            await _handle_agent_research_events(writer, req if isinstance(req, dict) else {})
            return
        if cmd in {"agent-research", "agent-artifacts"}:
            # Research compilation and local audits can be CPU/disk heavy. Keep
            # them off the asyncio accept loop so event replay and pause/cancel
            # control connections remain responsive.
            agent_resp = await asyncio.to_thread(
                _handle_agent_json_command,
                req if isinstance(req, dict) else {},
            )
            if agent_resp is not None:
                writer.write((agent_resp + "\n").encode("utf-8"))
                await writer.drain()
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
    if _AGENT_SERVICE is not None:
        _AGENT_SERVICE.close()
    # 清理：socket/pid/version 文件一起移除，避免 orphan 文件。
    for p in (SOCKET_PATH, PID_PATH, VERSION_PATH):
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    # Leave the GUI app's session so quitting KSSDesktop does not SIGHUP this
    # daemon into a half-dead re-exec. Intentional reloads still use kill(pid,
    # SIGHUP), which is delivered by pid, not by session.
    try:
        os.setsid()
    except OSError:
        pass
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    except (AttributeError, ValueError, OSError):
        pass
    asyncio.run(_serve())
