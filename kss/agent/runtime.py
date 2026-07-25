"""状态化、provider-neutral 的 Agent Runtime.

本模块只负责编排一次 run 的生命周期。上下文组装、会话存储、provider
stream 与工具 loop 通过 callables 注入，避免 Agent Core 反向依赖 sidecar。
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, Sequence, TypeVar

from kss.agent.events import AbortToken, EventSequencer
from kss.agent.jsonl import utc_timestamp
from kss.agent.types import (
    AgentEvent,
    AgentMessage,
    RunResult,
    RunTerminalStatus,
    RuntimeState,
    ToolCall,
)

T = TypeVar("T")
MaybeAwaitable = T | Awaitable[T]
EventCallback = Callable[[AgentEvent], MaybeAwaitable[None]]
MessageLoader = Callable[[str], MaybeAwaitable[Sequence[AgentMessage]]]
RunAdmission = Callable[[str, str, str], MaybeAwaitable[None]]


class TurnRunner(Protocol):
    """注入的单轮模型/工具执行器."""

    def __call__(self, turn: "RuntimeTurn") -> MaybeAwaitable[RunResult | None]:
        """执行一轮，并通过 ``turn`` 更新消息、工具状态和事件."""


class PersistenceBarrier(Protocol):
    """终态事件前的持久化屏障."""

    def __call__(
        self,
        turn: "RuntimeTurn",
        result: RunResult,
    ) -> MaybeAwaitable[None]:
        """持久化最终消息与 run 状态；失败时应抛出异常."""


class RuntimeBusyError(RuntimeError):
    """同一会话已有 active run."""

    def __init__(self, session_id: str, existing_run_id: str) -> None:
        super().__init__(f"session {session_id!r} already has active run {existing_run_id!r}")
        self.session_id = session_id
        self.existing_run_id = existing_run_id


@dataclass
class RuntimeTurn:
    """提供给注入组件的本轮执行上下文."""

    runtime: "AgentRuntime"
    state: RuntimeState
    input: str
    _sequencer: EventSequencer
    _external_emit: EventCallback | None

    @property
    def abort_token(self) -> AbortToken:
        """返回贯穿 provider、hook 与工具的同一个 abort token."""
        token = self.state.abort_token
        if not isinstance(token, AbortToken):  # pragma: no cover - defensive invariant
            raise RuntimeError("runtime abort token is unavailable")
        return token

    @property
    def model(self) -> str | None:
        """返回本轮模型."""
        return self.state.model

    @property
    def messages(self) -> list[AgentMessage]:
        """返回 Runtime 持有的可变消息列表."""
        return self.state.messages

    @property
    def tools(self) -> tuple[Any, ...]:
        """返回本轮工具快照."""
        return self.state.tools

    async def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        """生成并广播一个 protocol v1 事件."""
        return await self.runtime._emit(
            self._sequencer.frame(event_type, payload),
            self._external_emit,
        )

    def append_message(self, message: AgentMessage) -> None:
        """追加一条完整消息；流式碎片不应调用此方法."""
        self.state.messages.append(message)

    def set_streaming_message(self, message: AgentMessage | None) -> None:
        """设置当前尚未完成的 assistant 消息."""
        self.state.streaming_message = message

    def set_pending_tool_calls(self, calls: Sequence[ToolCall]) -> None:
        """替换当前待执行工具调用快照."""
        self.state.pending_tool_calls = list(calls)

    def add_usage(self, **usage: Any) -> None:
        """累加数值 usage，其他字段以后写覆盖."""
        for key, value in usage.items():
            current = self.state.usage.get(key)
            if isinstance(current, (int, float)) and isinstance(value, (int, float)):
                self.state.usage[key] = current + value
            else:
                self.state.usage[key] = value


class AgentRuntime:
    """状态化 Agent 生命周期管理器.

    Runtime 保证同一 session 仅有一个 active run，并把 ``agent_end`` 放在
    persistence barrier 成功或明确失败之后。它不导入 provider、context、
    session store 或 sidecar 类型。
    """

    def __init__(
        self,
        turn_runner: TurnRunner,
        *,
        model: str | None = None,
        model_resolver: Callable[[], str | None] | None = None,
        tools: Sequence[Any] = (),
        message_loader: MessageLoader | None = None,
        run_admission: RunAdmission | None = None,
        persistence_barrier: PersistenceBarrier | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.model = model
        self._model_resolver = model_resolver
        self.tools = tuple(tools)
        self._turn_runner = turn_runner
        self._message_loader = message_loader
        self._run_admission = run_admission
        self._persistence_barrier = persistence_barrier
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)
        self._subscribers: list[EventCallback] = []
        self._states: dict[str, RuntimeState] = {}
        self._active_by_session: dict[str, str] = {}
        self._idle_futures: dict[str, asyncio.Future[RunResult]] = {}
        self._results: dict[str, RunResult] = {}
        self._messages_by_session: dict[str, list[AgentMessage]] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """订阅所有 run 的事件并返回幂等取消函数."""
        self._subscribers.append(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def state(self, run_id: str) -> RuntimeState | None:
        """返回 run 的当前状态对象."""
        return self._states.get(run_id)

    def active_run_id(self, session_id: str) -> str | None:
        """返回会话的 active run ID."""
        return self._active_by_session.get(session_id)

    def abort(self, run_id: str, reason: str = "aborted") -> bool:
        """请求中止；返回是否找到尚未结束的 run."""
        state = self._states.get(run_id)
        if state is None or state.status in {"completed", "failed", "aborted", "interrupted"}:
            return False
        token = state.abort_token
        if not isinstance(token, AbortToken):
            return False
        token.abort(reason)
        return True

    async def wait_for_idle(self, run_id: str) -> RunResult:
        """等待 run 进入终态；已完成 run 立即返回."""
        result = self._results.get(run_id)
        if result is not None:
            return result
        future = self._idle_futures.get(run_id)
        if future is None:
            raise KeyError(run_id)
        return await asyncio.shield(future)

    async def run_turn(
        self,
        session_id: str,
        client_turn_id: str,
        input: str,
        emit: EventCallback | None = None,
    ) -> RunResult:
        """执行一轮并在 persistence barrier 后发送终态事件."""
        run_id = self._run_id_factory()
        loop = asyncio.get_running_loop()
        idle_future: asyncio.Future[RunResult] = loop.create_future()
        token = AbortToken()
        state = RuntimeState(
            run_id=run_id,
            session_id=session_id,
            client_turn_id=client_turn_id,
            model=self._model_resolver() if self._model_resolver else self.model,
            tools=self.tools,
            abort_token=token,
            started_at=utc_timestamp(),
        )

        async with self._lock:
            existing = self._active_by_session.get(session_id)
            if existing is not None:
                raise RuntimeBusyError(session_id, existing)
            if self._run_admission is not None:
                await _await_if_needed(
                    self._run_admission(session_id, client_turn_id, run_id)
                )
            self._active_by_session[session_id] = run_id
            self._states[run_id] = state
            self._idle_futures[run_id] = idle_future

        sequencer = EventSequencer(session_id=session_id, run_id=run_id)
        turn = RuntimeTurn(self, state, input, sequencer, emit)
        result: RunResult
        try:
            state.messages.extend(await self._load_messages(session_id))
            state.messages.append(
                AgentMessage(
                    id=uuid.uuid4().hex,
                    role="user",
                    content=input,
                    timestamp=utc_timestamp(),
                    metadata={"run_id": run_id, "client_turn_id": client_turn_id},
                )
            )
            state.status = "running"
            await turn.emit(
                "agent_start",
                {"client_turn_id": client_turn_id, "model": state.model},
            )
            await turn.emit("turn_start", {"client_turn_id": client_turn_id})
            runner_result = await _await_if_needed(self._turn_runner(turn))
            result = self._normalize_result(state, runner_result)
        except asyncio.CancelledError:
            reason = token.reason or "cancelled"
            token.abort(reason)
            result = self._terminal_result(state, "aborted", reason, reason)
        except Exception as exc:
            if token.is_aborted():
                reason = token.reason or "aborted"
                result = self._terminal_result(state, "aborted", None, reason)
            else:
                result = self._terminal_result(state, "failed", str(exc), "runtime_error")

        result, persisted = await self._cross_persistence_barrier(turn, result)
        self._apply_result(state, result)
        await self._finish_run(state, result)

        if result.error:
            await turn.emit(
                "error",
                {
                    "message": result.error,
                    "is_error": True,
                    "termination_reason": result.termination_reason,
                },
            )
        if not persisted:
            return result
        terminal_payload = {
            "status": result.status,
            "usage": dict(result.usage),
            "termination_reason": result.termination_reason,
        }
        await turn.emit("turn_end", terminal_payload)
        await turn.emit("agent_end", terminal_payload)
        return result

    async def _load_messages(self, session_id: str) -> list[AgentMessage]:
        if self._message_loader is not None:
            loaded = await _await_if_needed(self._message_loader(session_id))
            return list(loaded)
        return list(self._messages_by_session.get(session_id, ()))

    def _normalize_result(
        self,
        state: RuntimeState,
        result: RunResult | None,
    ) -> RunResult:
        if result is None:
            if isinstance(state.abort_token, AbortToken) and state.abort_token.is_aborted():
                return self._terminal_result(
                    state,
                    "aborted",
                    None,
                    state.abort_token.reason or "aborted",
                )
            return self._terminal_result(state, "completed", None, "completed")
        if result.run_id != state.run_id:
            raise ValueError("turn runner returned a result for another run")
        if result.session_id != state.session_id or result.client_turn_id != state.client_turn_id:
            raise ValueError("turn runner returned a result for another turn")
        if isinstance(state.abort_token, AbortToken) and state.abort_token.is_aborted():
            return self._terminal_result(
                state,
                "aborted",
                result.error,
                state.abort_token.reason or "aborted",
            )
        messages = result.messages or tuple(state.messages)
        usage = dict(state.usage)
        usage.update(result.usage)
        return RunResult(
            run_id=result.run_id,
            session_id=result.session_id,
            client_turn_id=result.client_turn_id,
            status=result.status,
            messages=messages,
            error=result.error,
            usage=usage,
            termination_reason=result.termination_reason,
        )

    def _terminal_result(
        self,
        state: RuntimeState,
        status: RunTerminalStatus,
        error: str | None,
        termination_reason: str | None,
    ) -> RunResult:
        return RunResult(
            run_id=state.run_id,
            session_id=state.session_id,
            client_turn_id=state.client_turn_id,
            status=status,
            messages=tuple(state.messages),
            error=error,
            usage=dict(state.usage),
            termination_reason=termination_reason,
        )

    async def _cross_persistence_barrier(
        self,
        turn: RuntimeTurn,
        result: RunResult,
    ) -> tuple[RunResult, bool]:
        if self._persistence_barrier is None:
            return result, True
        try:
            await _await_if_needed(self._persistence_barrier(turn, result))
            return result, True
        except Exception as exc:
            return (
                RunResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    client_turn_id=result.client_turn_id,
                    status="failed",
                    messages=result.messages,
                    error=f"persistence barrier failed: {exc}",
                    usage=result.usage,
                    termination_reason="persistence_error",
                ),
                False,
            )

    def _apply_result(self, state: RuntimeState, result: RunResult) -> None:
        state.status = result.status
        state.messages = list(result.messages)
        state.streaming_message = None
        state.pending_tool_calls.clear()
        state.error = result.error
        state.usage = dict(result.usage)
        state.finished_at = utc_timestamp()

    async def _finish_run(self, state: RuntimeState, result: RunResult) -> None:
        async with self._lock:
            self._messages_by_session[state.session_id] = list(result.messages)
            self._results[state.run_id] = result
            if self._active_by_session.get(state.session_id) == state.run_id:
                self._active_by_session.pop(state.session_id, None)
            future = self._idle_futures.get(state.run_id)
            if future is not None and not future.done():
                future.set_result(result)

    async def _emit(
        self,
        event: AgentEvent,
        external_emit: EventCallback | None,
    ) -> AgentEvent:
        callbacks: list[EventCallback] = []
        if external_emit is not None:
            callbacks.append(external_emit)
        callbacks.extend(tuple(self._subscribers))
        for callback in callbacks:
            try:
                await _await_if_needed(callback(event))
            except Exception:
                # Observers are deliberately isolated from the execution and
                # persistence path. A disconnected UI must not corrupt a run.
                continue
        return event


async def _await_if_needed(value: MaybeAwaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "AgentRuntime",
    "EventCallback",
    "MessageLoader",
    "PersistenceBarrier",
    "RunAdmission",
    "RuntimeBusyError",
    "RuntimeTurn",
    "TurnRunner",
]
