from __future__ import annotations

import asyncio
import threading
from functools import wraps

import pytest

from kss.agent import (
    AgentMessage,
    AgentRuntime,
    RunResult,
    RuntimeBusyError,
    SessionStore,
)


def async_test(function):
    """Run coroutine tests without adding pytest-asyncio to the project."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapper


@async_test
async def test_runtime_owns_state_and_emits_terminal_events_after_persistence():
    order: list[str] = []
    events = []

    async def runner(turn):
        turn.set_streaming_message(
            AgentMessage(id="draft", role="assistant", content="草稿", timestamp=2.0)
        )
        await turn.emit("message_delta", {"delta": "完成"})
        turn.append_message(
            AgentMessage(id="answer", role="assistant", content="完成", timestamp=3.0)
        )
        turn.add_usage(input_tokens=10, output_tokens=2)

    async def persist(turn, result):
        assert result.messages[-1].content == "完成"
        order.append("persisted")

    runtime = AgentRuntime(
        runner,
        model="test-model",
        tools=({"name": "lookup"},),
        persistence_barrier=persist,
        run_id_factory=lambda: "run-1",
    )
    runtime.subscribe(lambda event: events.append(event))

    result = await runtime.run_turn("session-1", "turn-1", "开始")

    assert result.status == "completed"
    assert result.usage == {"input_tokens": 10, "output_tokens": 2}
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_delta",
        "turn_end",
        "agent_end",
    ]
    assert order == ["persisted"]
    assert events[-1].sequence == 5
    assert runtime.state("run-1").streaming_message is None
    assert runtime.state("run-1").tools == ({"name": "lookup"},)


@async_test
async def test_runtime_wait_for_idle_and_abort_share_one_token():
    started = asyncio.Event()

    async def runner(turn):
        started.set()
        while not turn.abort_token.is_aborted():
            await asyncio.sleep(0)

    runtime = AgentRuntime(runner, run_id_factory=lambda: "run-abort")
    task = asyncio.create_task(runtime.run_turn("session-1", "turn-1", "开始"))
    await started.wait()

    waiter = asyncio.create_task(runtime.wait_for_idle("run-abort"))
    assert runtime.abort("run-abort", "用户停止") is True
    result = await task

    assert await waiter == result
    assert result.status == "aborted"
    assert result.termination_reason == "用户停止"
    assert runtime.abort("run-abort") is False


@async_test
async def test_runtime_rejects_concurrent_turn_for_same_session():
    release = asyncio.Event()
    started = asyncio.Event()
    run_ids = iter(("run-1", "run-2"))

    async def runner(turn):
        started.set()
        await release.wait()

    runtime = AgentRuntime(runner, run_id_factory=lambda: next(run_ids))
    first = asyncio.create_task(runtime.run_turn("session-1", "turn-1", "一"))
    await started.wait()

    with pytest.raises(RuntimeBusyError) as caught:
        await runtime.run_turn("session-1", "turn-2", "二")

    assert caught.value.existing_run_id == "run-1"
    release.set()
    await first


@async_test
async def test_runtime_allows_different_sessions_to_run_concurrently():
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()
    run_ids = iter(("run-1", "run-2"))

    async def runner(turn):
        started.add(turn.state.session_id)
        if len(started) == 2:
            both_started.set()
        await release.wait()

    runtime = AgentRuntime(runner, run_id_factory=lambda: next(run_ids))
    first = asyncio.create_task(runtime.run_turn("session-1", "turn-1", "一"))
    second = asyncio.create_task(runtime.run_turn("session-2", "turn-2", "二"))
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()

    assert {result.status for result in await asyncio.gather(first, second)} == {"completed"}


@async_test
async def test_runtime_validates_runner_result_identity():
    async def runner(turn):
        return RunResult(
            run_id="wrong",
            session_id=turn.state.session_id,
            client_turn_id=turn.state.client_turn_id,
            status="completed",
        )

    runtime = AgentRuntime(runner, run_id_factory=lambda: "run-1")
    result = await runtime.run_turn("session-1", "turn-1", "开始")

    assert result.status == "failed"
    assert result.termination_reason == "runtime_error"
    assert "another run" in (result.error or "")


@async_test
async def test_persistence_failure_fails_closed_without_agent_end():
    events = []

    async def runner(turn):
        return None

    async def persist(turn, result):
        raise OSError("disk full")

    runtime = AgentRuntime(
        runner,
        persistence_barrier=persist,
        run_id_factory=lambda: "run-1",
    )

    result = await runtime.run_turn("session-1", "turn-1", "开始", events.append)

    assert result.status == "failed"
    assert result.termination_reason == "persistence_error"
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "error",
    ]
    assert events[-1].payload["termination_reason"] == "persistence_error"


@async_test
async def test_runtime_reuses_completed_messages_without_external_loader():
    seen: list[list[str]] = []

    async def runner(turn):
        seen.append([message.content for message in turn.messages])
        turn.append_message(
            AgentMessage(
                id=f"assistant-{len(seen)}",
                role="assistant",
                content=f"回答{len(seen)}",
                timestamp=2.0,
            )
        )

    run_ids = iter(("run-1", "run-2"))
    runtime = AgentRuntime(runner, run_id_factory=lambda: next(run_ids))
    await runtime.run_turn("session-1", "turn-1", "问题1")
    await runtime.run_turn("session-1", "turn-2", "问题2")

    assert seen == [["问题1"], ["问题1", "回答1", "问题2"]]


@async_test
async def test_unsubscribe_is_idempotent_and_observer_failure_is_isolated():
    observed: list[str] = []

    async def runner(turn):
        return None

    runtime = AgentRuntime(runner, run_id_factory=lambda: "run-1")
    runtime.subscribe(lambda event: (_ for _ in ()).throw(RuntimeError("disconnected")))
    unsubscribe = runtime.subscribe(lambda event: observed.append(event.type))
    unsubscribe()
    unsubscribe()

    result = await runtime.run_turn("session-1", "turn-1", "开始")

    assert result.status == "completed"
    assert observed == []


@async_test
async def test_runtime_queue_wrappers_apply_at_runner_selected_boundary(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="session-queue")

    async def runner(turn):
        steering = turn.runtime.steer(
            turn.state.run_id,
            "steer-message",
            "先检查风险",
        )
        duplicate = turn.runtime.steer(
            turn.state.run_id,
            "steer-message",
            "重复请求不覆盖",
        )
        assert duplicate == steering
        assert turn.queued_inputs() == [steering]
        applied = turn.apply_queued_input(steering.id)
        assert applied.status == "applied"
        assert turn.messages[-1].content == "先检查风险"

    runtime = AgentRuntime(
        runner,
        queue_store=store,
        run_id_factory=lambda: "run-queue",
    )
    result = await runtime.run_turn(
        "session-queue", "turn-queue", "开始"
    )

    assert result.status == "completed"
    all_items = store.queued_inputs(
        session_id="session-queue", include_terminal=True
    )
    assert len(all_items) == 1
    assert all_items[0].mode == "steering"
    assert all_items[0].status == "applied"


@async_test
async def test_runtime_settles_pending_queue_before_persistence_and_agent_end(tmp_path):
    store = SessionStore(tmp_path)
    store.create_session(session_id="session-settle")
    events = []
    persisted_statuses: list[str] = []

    async def runner(turn):
        turn.runtime.follow_up(
            turn.state.run_id,
            "follow-message",
            "继续处理",
        )

    async def persist(turn, result):
        pending = store.queued_inputs(run_id=turn.state.run_id)
        persisted_statuses.extend(item.status for item in pending)

    runtime = AgentRuntime(
        runner,
        queue_store=store,
        persistence_barrier=persist,
        run_id_factory=lambda: "run-settle",
    )
    result = await runtime.run_turn(
        "session-settle", "turn-settle", "开始", events.append
    )

    assert result.status == "completed"
    assert persisted_statuses == ["restored"]
    assert [event.type for event in events][-2:] == ["turn_end", "agent_end"]
    all_items = store.queued_inputs(
        session_id="session-settle", include_terminal=True
    )
    assert all_items[0].status == "restored"


@async_test
async def test_queue_admission_racing_settlement_is_restored_not_stranded(tmp_path):
    add_entered = threading.Event()
    allow_add = threading.Event()

    class BlockingSessionStore(SessionStore):
        def add_queued_input(self, *args, **kwargs):
            add_entered.set()
            assert allow_add.wait(timeout=10)
            return super().add_queued_input(*args, **kwargs)

    store = BlockingSessionStore(tmp_path)
    store.create_session(session_id="session-race")
    runner_started = asyncio.Event()
    allow_runner_end = asyncio.Event()

    async def runner(turn):
        runner_started.set()
        await allow_runner_end.wait()

    runtime = AgentRuntime(
        runner,
        queue_store=store,
        run_id_factory=lambda: "run-race",
    )
    run_task = asyncio.create_task(
        runtime.run_turn("session-race", "turn-race", "开始")
    )
    await runner_started.wait()
    queue_task = asyncio.create_task(
        asyncio.to_thread(
            runtime.follow_up,
            "run-race",
            "follow-race",
            "稍后追问",
        )
    )
    for _ in range(1_000):
        if add_entered.is_set():
            break
        await asyncio.sleep(0.001)
    assert add_entered.is_set()

    allow_runner_end.set()
    await asyncio.sleep(0.05)
    assert not run_task.done()
    allow_add.set()

    queued, result = await asyncio.gather(queue_task, run_task)
    assert result.status == "completed"
    persisted = store.queued_inputs(
        session_id="session-race",
        include_terminal=True,
    )
    assert next(item for item in persisted if item.id == queued.id).status == "restored"


@async_test
async def test_runner_owned_turn_boundaries_suppress_runtime_outer_pair():
    events = []

    async def runner(turn):
        await turn.emit("turn_start", {"internal": True})
        await turn.emit("turn_end", {"internal": True})

    runtime = AgentRuntime(
        runner,
        runner_owns_turn_boundaries=True,
        run_id_factory=lambda: "run-boundaries",
    )
    await runtime.run_turn(
        "session-boundaries",
        "turn-boundaries",
        "开始",
        events.append,
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "turn_end",
        "agent_end",
    ]
    assert events[1].payload["internal"] is True
    assert events[2].payload["internal"] is True
