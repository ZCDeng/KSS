"""状态化、provider-neutral 的 Agent Runtime.

本模块只负责编排一次 run 的生命周期。上下文组装、会话存储、provider
stream 与工具 loop 通过 callables 注入，避免 Agent Core 反向依赖 sidecar。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, Sequence, TypeVar

from kss.agent.events import AbortToken, EventSequencer
from kss.agent.jsonl import utc_timestamp
from kss.agent.types import (
    AgentEvent,
    AgentMessage,
    QueuedAgentInput,
    QueuedInputMode,
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


class QueueStore(Protocol):
    """Runtime 队列的同步 append-only 存储边界."""

    def add_queued_input(
        self,
        session_id: str,
        run_id: str,
        mode: QueuedInputMode,
        client_message_id: str,
        content: str,
        *,
        source_queue_id: str | None = None,
    ) -> QueuedAgentInput:
        """幂等追加一条队列输入."""

    def queued_inputs(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        include_terminal: bool = False,
    ) -> list[QueuedAgentInput]:
        """列出持久队列."""

    def apply_queued_input(
        self,
        session_id: str,
        queue_id: str,
        *,
        target_run_id: str | None = None,
    ) -> QueuedAgentInput:
        """原子追加 user message 与 applied 终态."""

    def discard_queued_input(
        self, session_id: str, queue_id: str
    ) -> QueuedAgentInput:
        """丢弃队列输入."""

    def restore_queued_inputs(
        self, session_id: str, *, run_id: str | None = None
    ) -> list[QueuedAgentInput]:
        """恢复 interrupted run 的待处理输入."""

    def restore_pending_inputs(
        self, session_id: str, run_id: str
    ) -> list[QueuedAgentInput]:
        """在 run 终态前恢复所有 pending 输入."""


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

    def queued_inputs(self) -> list[QueuedAgentInput]:
        """返回本 run 尚待应用的 steering / follow-up."""
        return self.runtime.queued_inputs(run_id=self.state.run_id)

    def apply_queued_input(self, queue_id: str) -> QueuedAgentInput:
        """在 runner 选择的安全点原子应用一条 queued input."""
        return self.runtime.apply_queued_input(self.state.session_id, queue_id)


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
        queue_store: QueueStore | None = None,
        runner_owns_turn_boundaries: bool = False,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.model = model
        self._model_resolver = model_resolver
        self.tools = tuple(tools)
        self._turn_runner = turn_runner
        self._message_loader = message_loader
        self._run_admission = run_admission
        self._persistence_barrier = persistence_barrier
        self._queue_store = queue_store
        self._runner_owns_turn_boundaries = runner_owns_turn_boundaries
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)
        self._subscribers: list[EventCallback] = []
        self._states: dict[str, RuntimeState] = {}
        self._active_by_session: dict[str, str] = {}
        self._idle_futures: dict[str, asyncio.Future[RunResult]] = {}
        self._results: dict[str, RunResult] = {}
        self._messages_by_session: dict[str, list[AgentMessage]] = {}
        self._lock = asyncio.Lock()
        self._queue_lock = threading.RLock()
        self._queued_by_id: dict[str, QueuedAgentInput] = {}
        self._queue_sources: dict[str, str] = {}

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

    def steer(
        self,
        run_id: str,
        client_message_id: str,
        input: str,
        source_queue_id: str | None = None,
    ) -> QueuedAgentInput:
        """为 active run 排入 steering 输入."""
        return self._add_queued_input(
            run_id,
            "steering",
            client_message_id,
            input,
            source_queue_id=source_queue_id,
        )

    def follow_up(
        self,
        run_id: str,
        client_message_id: str,
        input: str,
        source_queue_id: str | None = None,
    ) -> QueuedAgentInput:
        """为 active run 排入完成后处理的 follow-up."""
        return self._add_queued_input(
            run_id,
            "follow_up",
            client_message_id,
            input,
            source_queue_id=source_queue_id,
        )

    def queued_inputs(
        self,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> list[QueuedAgentInput]:
        """列出尚未应用或丢弃的队列输入."""
        if self._queue_store is not None:
            return self._queue_store.queued_inputs(
                session_id=session_id,
                run_id=run_id,
            )
        with self._queue_lock:
            return [
                item
                for item in self._queued_by_id.values()
                if item.status in {"queued", "restored"}
                and (session_id is None or item.session_id == session_id)
                and (run_id is None or item.run_id == run_id)
            ]

    def apply_queued_input(
        self, session_id: str, queue_id: str
    ) -> QueuedAgentInput:
        """应用队列输入，并同步更新当前 RuntimeState."""
        target_run_id = self._active_by_session.get(session_id)
        if self._queue_store is not None:
            item = self._queue_store.apply_queued_input(
                session_id,
                queue_id,
                target_run_id=target_run_id,
            )
        else:
            item = self._apply_in_memory_queued_input(session_id, queue_id)
        target_state = (
            self._states.get(target_run_id) if target_run_id is not None else None
        )
        if target_state is not None and not any(
            message.metadata.get("queued_input_id") == item.id
            for message in target_state.messages
        ):
            target_state.messages.append(
                self._queued_input_message(item, target_run_id=target_run_id)
            )
        return item

    def discard_queued_input(
        self, session_id: str, queue_id: str
    ) -> QueuedAgentInput:
        """幂等丢弃队列输入."""
        if self._queue_store is not None:
            return self._queue_store.discard_queued_input(session_id, queue_id)
        with self._queue_lock:
            current = self._queued_by_id.get(queue_id)
            if current is None or current.session_id != session_id:
                raise KeyError(queue_id)
            if current.status == "applied":
                raise ValueError("applied queued input 不能丢弃")
            if current.status == "discarded":
                return current
            discarded = self._queued_copy(current, status="discarded")
            self._queued_by_id[queue_id] = discarded
            return discarded

    def restore_queued_inputs(
        self, session_id: str, *, run_id: str | None = None
    ) -> list[QueuedAgentInput]:
        """恢复 interrupted run 的 pending queue."""
        if self._queue_store is not None:
            return self._queue_store.restore_queued_inputs(
                session_id, run_id=run_id
            )
        with self._queue_lock:
            restored: list[QueuedAgentInput] = []
            for queue_id, current in list(self._queued_by_id.items()):
                if (
                    current.session_id == session_id
                    and current.status == "queued"
                    and (run_id is None or current.run_id == run_id)
                ):
                    item = self._queued_copy(current, status="restored")
                    self._queued_by_id[queue_id] = item
                    restored.append(item)
            return restored

    def restore_pending_inputs(
        self, session_id: str, run_id: str
    ) -> list[QueuedAgentInput]:
        """在 persistence barrier 前 settle 仍 pending 的输入."""
        if self._queue_store is not None:
            return self._queue_store.restore_pending_inputs(session_id, run_id)
        return self.restore_queued_inputs(session_id, run_id=run_id)

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
            if not self._runner_owns_turn_boundaries:
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

        # Close the queue admission gate before settlement so an input racing
        # natural completion cannot appear after restore_pending_inputs.
        # Queue admission is synchronous and may briefly hold the gate while a
        # JSONL append/fsync completes. Wait for that gate off the event-loop
        # thread so a slow disk cannot deadlock abort/control processing.
        await asyncio.to_thread(
            self._close_queue_admission,
            state,
            result.status,
        )
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
        if result.termination_reason == "unable_to_complete":
            terminal_payload["coverage_path"] = True
            terminal_payload["disable_legacy_fallback"] = True
        if not self._runner_owns_turn_boundaries:
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
            try:
                self.restore_pending_inputs(result.session_id, result.run_id)
            except Exception as exc:
                return self._persistence_failure(result, exc), False
            return result, True
        try:
            self.restore_pending_inputs(result.session_id, result.run_id)
            await _await_if_needed(self._persistence_barrier(turn, result))
            return result, True
        except Exception as exc:
            return self._persistence_failure(result, exc), False

    def _persistence_failure(
        self, result: RunResult, exc: Exception
    ) -> RunResult:
        return RunResult(
            run_id=result.run_id,
            session_id=result.session_id,
            client_turn_id=result.client_turn_id,
            status="failed",
            messages=result.messages,
            error=f"persistence barrier failed: {exc}",
            usage=result.usage,
            termination_reason="persistence_error",
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

    def _close_queue_admission(
        self,
        state: RuntimeState,
        status: RunTerminalStatus,
    ) -> None:
        with self._queue_lock:
            state.status = status

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

    def _add_queued_input(
        self,
        run_id: str,
        mode: QueuedInputMode,
        client_message_id: str,
        content: str,
        *,
        source_queue_id: str | None,
    ) -> QueuedAgentInput:
        with self._queue_lock:
            state = self._states.get(run_id)
            if state is None:
                raise KeyError(run_id)
            existing = next(
                (
                    item
                    for item in self._all_queued_inputs(run_id=run_id)
                    if item.client_message_id == client_message_id
                ),
                None,
            )
            if existing is not None:
                return existing
            if state.status in {"completed", "failed", "aborted", "interrupted"}:
                raise RuntimeError(f"run {run_id!r} is already terminal")
            if self._queue_store is not None:
                # Settlement flips state.status under this same gate before it
                # restores pending entries. Therefore an admission either lands
                # before settlement and is restored, or observes the terminal
                # state and is rejected; it can never be stranded as queued.
                return self._queue_store.add_queued_input(
                    state.session_id,
                    run_id,
                    mode,
                    client_message_id,
                    content,
                    source_queue_id=source_queue_id,
                )
            if not client_message_id.strip() or not content.strip():
                raise ValueError("client_message_id 和 queued input 不能为空")
            cumulative = sum(
                1 for item in self._queued_by_id.values() if item.run_id == run_id
            )
            if cumulative >= 8:
                raise RuntimeError(f"run {run_id!r} queued input limit 8 reached")
            item = QueuedAgentInput(
                id=uuid.uuid4().hex,
                client_message_id=client_message_id,
                session_id=state.session_id,
                run_id=run_id,
                mode=mode,
                content=content,
                status="queued",
                created_at=utc_timestamp(),
            )
            self._queued_by_id[item.id] = item
            if source_queue_id is not None:
                self._queue_sources[item.id] = source_queue_id
            return item

    def _all_queued_inputs(
        self, *, run_id: str | None = None
    ) -> list[QueuedAgentInput]:
        if self._queue_store is not None:
            return self._queue_store.queued_inputs(
                run_id=run_id,
                include_terminal=True,
            )
        with self._queue_lock:
            return [
                item
                for item in self._queued_by_id.values()
                if run_id is None or item.run_id == run_id
            ]

    def _apply_in_memory_queued_input(
        self, session_id: str, queue_id: str
    ) -> QueuedAgentInput:
        with self._queue_lock:
            current = self._queued_by_id.get(queue_id)
            if current is None or current.session_id != session_id:
                raise KeyError(queue_id)
            if current.status == "applied":
                return current
            if current.status == "discarded":
                raise ValueError("discarded queued input 不能应用")
            source_queue_id = self._queue_sources.get(queue_id)
            source = (
                self._queued_by_id.get(source_queue_id)
                if source_queue_id is not None
                else None
            )
            if source_queue_id is not None and (
                source is None or source.status != "restored"
            ):
                raise ValueError(
                    "source queued input 必须处于 restored 状态"
                )
            item = self._queued_copy(
                current,
                status="applied",
                applied_at=utc_timestamp(),
            )
            self._queued_by_id[queue_id] = item
            if source_queue_id is not None and source is not None:
                self._queued_by_id[source_queue_id] = self._queued_copy(
                    source, status="discarded"
                )
            return item

    def _queued_copy(
        self,
        item: QueuedAgentInput,
        *,
        status: str,
        applied_at: float | None = None,
    ) -> QueuedAgentInput:
        return QueuedAgentInput(
            id=item.id,
            client_message_id=item.client_message_id,
            session_id=item.session_id,
            run_id=item.run_id,
            mode=item.mode,
            content=item.content,
            status=status,  # type: ignore[arg-type]
            created_at=item.created_at,
            applied_at=applied_at,
        )

    def _queued_input_message(
        self,
        item: QueuedAgentInput,
        *,
        target_run_id: str | None,
    ) -> AgentMessage:
        return AgentMessage(
            id=item.client_message_id,
            role="user",
            content=item.content,
            timestamp=item.applied_at or utc_timestamp(),
            metadata={
                "run_id": target_run_id or item.run_id,
                "source_run_id": item.run_id,
                "client_message_id": item.client_message_id,
                "queued_input_id": item.id,
                "queue_mode": item.mode,
            },
        )


async def _await_if_needed(value: MaybeAwaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "AgentRuntime",
    "EventCallback",
    "MessageLoader",
    "PersistenceBarrier",
    "QueueStore",
    "RunAdmission",
    "RuntimeBusyError",
    "RuntimeTurn",
    "TurnRunner",
]
