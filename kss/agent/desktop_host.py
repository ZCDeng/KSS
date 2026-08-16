"""桌面 Harness 宿主缝：SessionEvent 投影、审批 callId、inbox。

生产无 Node 会话时失败关闭，不把 ``kss_chat_loop`` 当编排主人。
测试注入 Fake 会话驱动 ``session/event`` 与 ``approval/request``。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

from kss.agent.events import AbortToken

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]
GrantFn = Callable[[str, str], None]
RevokeFn = Callable[[str], None]


@dataclass(frozen=True)
class ExecuteIntent:
    """绑定到 Harness callId 的待执行写意图。ApprovalRequest 无 args。"""

    name: str
    command: str
    args: tuple[str, ...]
    tool_args: dict[str, Any]


@dataclass
class DesktopTurnRequest:
    session_id: str
    client_turn_id: str
    input: str
    run_id: str
    attachment_ids: tuple[str, ...] = ()
    source_queue_id: str | None = None
    # 会话级 provider 路由（非密钥），None 时回落全局 primary。
    provider_route: dict[str, Any] | None = None


@dataclass
class DesktopTurnResult:
    status: str
    assistant_text: str = ""
    error: str | None = None
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class DesktopTurnSession(Protocol):
    async def run(self, request: DesktopTurnRequest, host: DesktopHarnessHost) -> DesktopTurnResult:
        ...


class UnavailableDesktopSession:
    """未注入真实 Harness 时失败关闭，避免回落到 Python loop 主人。"""

    async def run(self, request: DesktopTurnRequest, host: DesktopHarnessHost) -> DesktopTurnResult:
        return DesktopTurnResult(status="unavailable", error="harness_session_unavailable")


class DesktopHarnessHost:
    """桌面回合的 Harness 投影器：inbox / 审批 / callId 作废。"""

    def __init__(
        self,
        *,
        session: DesktopTurnSession | None = None,
        grant_write: GrantFn | None = None,
        revoke_grant: RevokeFn | None = None,
    ) -> None:
        self._session = session or UnavailableDesktopSession()
        self._grant_write = grant_write
        self._revoke_grant = revoke_grant
        self._intents: dict[str, ExecuteIntent] = {}
        self._decisions: dict[str, asyncio.Future] = {}
        self._inbox: list[dict[str, Any]] = []
        self._inbox_event: asyncio.Event | None = None
        self.abort_token = AbortToken()
        self.emit: EmitFn | None = None
        self.execute_tool: Callable[..., dict[str, Any]] | None = None
        self.last_requests: list[DesktopTurnRequest] = []

    def bind_intent(
        self,
        call_id: str,
        *,
        name: str,
        command: str,
        args: list[str] | tuple[str, ...],
        tool_args: dict[str, Any] | None = None,
    ) -> ExecuteIntent:
        intent = ExecuteIntent(
            name=str(name),
            command=str(command),
            args=tuple(str(a) for a in args),
            tool_args=dict(tool_args or {}),
        )
        self._intents[str(call_id)] = intent
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._decisions[str(call_id)] = fut
        return intent

    def intent(self, call_id: str) -> ExecuteIntent | None:
        return self._intents.get(str(call_id))

    def project_confirm(self, call_id: str) -> dict[str, Any]:
        """从 callId 绑定的 execute intent 投影 confirm_required 皮肤字段。"""
        import kss_chat_loop as chat_loop

        intent = self.intent(call_id)
        if intent is None:
            return {
                "call_id": call_id,
                "tool": "",
                "command": "",
                "args": {},
                "argsText": "{}",
                "effect": "执行写操作",
            }
        return {
            "call_id": call_id,
            "tool": intent.name,
            "command": intent.command,
            "args": intent.tool_args,
            "argsText": json.dumps(intent.tool_args, ensure_ascii=False),
            "effect": chat_loop.write_effect_label(intent.command, list(intent.args)),
        }

    def invalidate(self, call_id: str) -> None:
        """中止先作废 callId，迟到允许不得 grant/dispatch。"""
        cid = str(call_id)
        if self._revoke_grant is not None:
            self._revoke_grant(cid)
        self._intents.pop(cid, None)
        fut = self._decisions.pop(cid, None)
        if fut is not None and not fut.done():
            fut.set_result(False)

    def invalidate_all(self) -> None:
        for cid in list(self._intents):
            self.invalidate(cid)

    def resolve_approval(self, call_id: str, approved: bool) -> bool:
        cid = str(call_id)
        if cid not in self._intents:
            return False
        if approved and self._grant_write is not None:
            intent = self._intents[cid]
            self._grant_write(cid, intent.command)
        elif not approved and self._revoke_grant is not None:
            self._revoke_grant(cid)
        fut = self._decisions.get(cid)
        if fut is not None and not fut.done():
            fut.set_result(bool(approved))
        return True

    async def wait_decision(self, call_id: str) -> bool:
        fut = self._decisions.get(str(call_id))
        if fut is None:
            return False
        return bool(await fut)

    def enqueue(
        self,
        *,
        mode: str,
        client_message_id: str,
        input_text: str,
        session_id: str,
        run_id: str,
        source_queue_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Harness inbox：accept / reject / restored。"""
        normalized = "steering" if mode in {"steer", "steering"} else "follow_up"
        pending = [i for i in self._inbox if i.get("status") in {"pending", "queued"}]
        if self.abort_token.aborted:
            item = self._inbox_item(
                mode=normalized,
                client_message_id=client_message_id,
                input_text=input_text,
                session_id=session_id,
                run_id=run_id,
                status="restored",
                source_queue_id=source_queue_id,
            )
            self._inbox.append(item)
            self._notify_inbox()
            return True, {"item": item, "queued_inputs": list(self._inbox), "operation": "restored"}
        if len(pending) >= 8:
            item = self._inbox_item(
                mode=normalized,
                client_message_id=client_message_id,
                input_text=input_text,
                session_id=session_id,
                run_id=run_id,
                status="discarded",
                source_queue_id=source_queue_id,
            )
            return False, {
                "item": item,
                "queued_inputs": list(self._inbox),
                "operation": "rejected",
                "reason": "queue_limit",
            }
        for existing in self._inbox:
            if existing.get("client_message_id") == client_message_id:
                return True, {
                    "item": existing,
                    "queued_inputs": list(self._inbox),
                    "operation": "accepted",
                }
        item = self._inbox_item(
            mode=normalized,
            client_message_id=client_message_id,
            input_text=input_text,
            session_id=session_id,
            run_id=run_id,
            status="pending",
            source_queue_id=source_queue_id,
        )
        self._inbox.append(item)
        self._notify_inbox()
        return True, {"item": item, "queued_inputs": list(self._inbox), "operation": "accepted"}

    def restore_inbox(self) -> list[dict[str, Any]]:
        restored = []
        for item in self._inbox:
            if item.get("status") in {"pending", "queued"}:
                item["status"] = "restored"
                restored.append(item)
        self._notify_inbox()
        return list(self._inbox)

    async def wait_inbox(self) -> list[dict[str, Any]]:
        if self._inbox_event is None:
            self._inbox_event = asyncio.Event()
        await self._inbox_event.wait()
        self._inbox_event.clear()
        return list(self._inbox)

    def abort(self, reason: str = "client_abort") -> None:
        self.invalidate_all()
        self.restore_inbox()
        self.abort_token.abort(reason)

    async def run(self, request: DesktopTurnRequest, emit: EmitFn) -> DesktopTurnResult:
        self.last_requests.append(request)
        self.emit = emit
        return await self._session.run(request, self)

    def _notify_inbox(self) -> None:
        if self._inbox_event is not None:
            self._inbox_event.set()

    @staticmethod
    def _inbox_item(
        *,
        mode: str,
        client_message_id: str,
        input_text: str,
        session_id: str,
        run_id: str,
        status: str,
        source_queue_id: str | None,
    ) -> dict[str, Any]:
        now = time.time()
        return {
            "id": uuid4().hex,
            "client_message_id": client_message_id,
            "session_id": session_id,
            "run_id": run_id,
            "mode": mode,
            "content": input_text,
            "input": input_text,
            "status": status,
            "created_at": now,
            "source_queue_id": source_queue_id,
        }


class ScriptedDesktopSession:
    """测试驱动：发出 chrome 词汇事件，并可在审批/inbox 上停下。"""

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        confirm_intent: dict[str, Any] | None = None,
        wait_inbox: bool = False,
        block_until_abort: bool = False,
    ) -> None:
        self.events = list(events or [])
        self.confirm_intent = confirm_intent
        self.wait_inbox = wait_inbox
        self.block_until_abort = block_until_abort
        self.runs = 0
        self.last_write: dict[str, Any] | None = None

    async def run(self, request: DesktopTurnRequest, host: DesktopHarnessHost) -> DesktopTurnResult:
        self.runs += 1
        tool_results: list[dict[str, Any]] = []
        assistant = ""
        emit = host.emit
        if emit is None:
            async def emit(_event: dict[str, Any]) -> None:
                return None

        if not self.events and not self.confirm_intent and not self.wait_inbox and not self.block_until_abort:
            await emit({"type": "turn_start"})
            await emit({"type": "context_usage", "used": 0, "limit": 0})
            await emit({"type": "message_start"})
            await emit({"type": "message_delta", "text": "答复", "delta": "答复"})
            await emit({"type": "message_end"})
            await emit({"type": "turn_end"})
            return DesktopTurnResult(status="completed", assistant_text="答复")

        if self.block_until_abort:
            await emit({"type": "turn_start"})
            while not host.abort_token.aborted:
                await asyncio.sleep(0.01)
            return DesktopTurnResult(status="aborted", error=host.abort_token.reason or "aborted")

        for event in self.events:
            text = event.get("text") or event.get("delta") or ""
            if event.get("type") in {"message_delta", "chunk"} and text:
                assistant += str(text)
            await emit(event)

        if self.confirm_intent:
            call_id = str(self.confirm_intent.get("call_id") or uuid4().hex)
            host.bind_intent(
                call_id,
                name=str(self.confirm_intent.get("name") or "run_task"),
                command=str(self.confirm_intent.get("command") or "run"),
                args=list(self.confirm_intent.get("args") or ["update-cs-data"]),
                tool_args=dict(self.confirm_intent.get("tool_args") or {"task": "update-cs-data"}),
            )
            await emit({"type": "approval_request", "call_id": call_id})
            approved = await host.wait_decision(call_id)
            if approved:
                intent = host.intent(call_id)
                if host.execute_tool is None:
                    result = {"error": "not_allowed", "hint": "no execute seam"}
                else:
                    result = host.execute_tool(
                        name=intent.name if intent else "run_task",
                        args=intent.tool_args if intent else {},
                        call_id=call_id,
                    )
                self.last_write = result
                tool_results.append(result)
            else:
                tool_results.append({
                    "error": (host.abort_token.reason or "aborted")
                    if host.abort_token.aborted
                    else "denied",
                })

        if self.wait_inbox:
            await host.wait_inbox()

        status = "aborted" if host.abort_token.aborted else "completed"
        return DesktopTurnResult(
            status=status,
            assistant_text=assistant,
            tool_results=tool_results,
            error=host.abort_token.reason if host.abort_token.aborted else None,
        )
