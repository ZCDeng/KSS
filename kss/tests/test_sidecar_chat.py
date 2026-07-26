"""U3 测试:sidecar chat-turn 长连 handler + 并发 reader 任务(写执行点)。
覆盖:happy 流式 / reader 执行写 / 默认拒 / call_id 完整性 / _LIVE 总开关 / 断连 / 超时 / legacy 不回归。
跑:.venv-desktop/bin/python -m pytest kss/tests/test_sidecar_chat.py -q
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as chat_loop  # noqa: E402
import kss_sidecar as sc  # noqa: E402


class FakeWriter:
    def __init__(self):
        self.buf = []
        self.closed = False

    def write(self, b):
        self.buf.append(b)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def frames(self):
        out = []
        for b in self.buf:
            for ln in b.decode("utf-8").splitlines():
                if ln.strip():
                    out.append(json.loads(ln))
        return out


def _mk_reader():
    return asyncio.StreamReader()


def _feed(reader, obj):
    reader.feed_data((json.dumps(obj) + "\n").encode("utf-8"))


async def _wait_frame(writer, ftype, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        for fr in writer.frames():
            if fr.get("type") == ftype:
                return fr
        await asyncio.sleep(0.01)
    raise AssertionError(f"未等到 {ftype} 帧;现有={writer.frames()}")


# ---------------------------------------------------------------------------

def test_execute_write_gated(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", False)
    assert sc._execute_write("run", ["x"])["error"] == "not_live"
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c, "args": a})
    out = sc._execute_write("run", ["update-cs-data"])
    assert out["ok"] and out["result"]["ran"] == "run"


def test_chat_turn_read_only_happy(monkeypatch):
    async def fake_run_turn(messages, emit, request_write):
        await emit({"type": "chunk", "text": "今天"})
        await emit({"type": "chunk", "text": "平稳"})
        await emit({"type": "done", "reason": "stop"})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "盘面"}]})
        frames = writer.frames()
        chunks = [f["text"] for f in frames if f["type"] == "chunk"]
        assert "".join(chunks) == "今天平稳"
        assert frames[-1]["type"] == "done"
    asyncio.run(go())


def test_confirm_approve_reader_executes_write(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c})

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="run", args=["update-cs-data"],
                                  tool_name="run_task", tool_args={"task": "update-cs-data"})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": []}))
        cr = await _wait_frame(writer, "confirm_required")
        assert cr["tool"] == "run_task" and cr["command"] == "run"
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert done["writeResult"]["ok"] and done["writeResult"]["result"]["ran"] == "run"
    asyncio.run(go())


def test_confirm_deny_no_write(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda *a: pytest.fail("拒绝不应 dispatch"))

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="cron-rerun", args=["daily"],
                                  tool_name="cron_rerun", tool_args={"label": "daily"})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": []}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": False})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert done["writeResult"]["error"] == "denied"
    asyncio.run(go())


def test_call_id_unmatched_discarded_then_correct(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c})

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="run", args=["x"], tool_name="run_task", tool_args={})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": []}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": "BOGUS", "approved": True})
        await asyncio.sleep(0.05)
        assert not task.done()           # 不匹配 → 丢弃,future 仍未决
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert done["writeResult"]["ok"]
    asyncio.run(go())


def test_not_live_rejects_even_approved(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", False)

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="run", args=["x"], tool_name="run_task", tool_args={})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": []}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert done["writeResult"]["error"] == "not_live"
    asyncio.run(go())


def test_disconnect_rejects_pending(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="run", args=["x"], tool_name="run_task", tool_args={})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": []}))
        await _wait_frame(writer, "confirm_required")
        reader.feed_eof()                # 断连 → reader 拒所有 pending
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert done["writeResult"]["error"] == "disconnected"
    asyncio.run(go())


def test_confirm_timeout(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(sc, "_CONFIRM_TIMEOUT", 0.1)

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="run", args=["x"], tool_name="run_task", tool_args={})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_chat_turn(reader, writer, {"messages": []})   # 不喂 confirm
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert done["writeResult"]["error"] == "confirm_timeout"
    asyncio.run(go())


def test_auto_task_skips_confirm(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(chat_loop, "AUTO_TASKS", frozenset({"refresh-market-strip"}))
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c})

    async def fake_run_turn(messages, emit, request_write):
        res = await request_write(command="run", args=["refresh-market-strip"],
                                  tool_name="run_task", tool_args={"task": "refresh-market-strip"})
        await emit({"type": "done", "writeResult": res})

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_chat_turn(reader, writer, {"messages": []})
        frames = writer.frames()
        assert not any(f["type"] == "confirm_required" for f in frames)   # AUTO 免确认
        done = next(f for f in frames if f["type"] == "done")
        assert done["writeResult"]["ok"]
    asyncio.run(go())


def test_legacy_command_not_regressed(monkeypatch):
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"cmd": c})

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        _feed(reader, {"cmd": "snapshot", "args": []})
        reader.feed_eof()
        await sc._on_connection(reader, writer)
        frames = writer.frames()
        assert len(frames) == 1 and frames[0]["code"] == 0
        assert writer.closed
    asyncio.run(go())


def test_agent_turn_emits_protocol_v1_frames_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        assert messages[-1]["role"] == "user"
        assert "abort_token" in kwargs and "tool_registry" in kwargs
        effective = kwargs["transform_context"](messages)
        await emit({"type": "chunk", "text": "答复"})
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=[
                *effective,
                {"role": "assistant", "content": "答复"},
            ],
            assistant_messages=[{"role": "assistant", "content": "答复"}],
            tool_results=[],
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn",
            "session_id": "s1",
            "client_turn_id": "c1",
            "input": "你好",
        })
        frames = writer.frames()
        assert frames[0]["protocol_version"] == 1
        assert frames[0]["session_id"] == "s1"
        assert frames[0]["run_id"]
        assert frames[4]["text"] == "答复"
        assert [f["sequence"] for f in frames] == list(range(1, len(frames) + 1))
        assert [f["type"] for f in frames] == [
            "agent_start", "turn_start", "context_usage",
            "message_start", "message_delta",
            "message_end", "turn_end", "agent_end",
        ]
        store = sc.SessionStore(tmp_path)
        messages = store.read_messages("s1")
        assert [m.role for m in messages] == ["user", "assistant"]
        assert (tmp_path / "storage" / "agent" / "sessions" / "s1.jsonl").is_file()
    asyncio.run(go())


def test_agent_turn_completed_client_id_hydrates_without_reexecution(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    calls = 0

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        nonlocal calls
        calls += 1
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=[
                *kwargs["transform_context"](messages),
                {"role": "assistant", "content": "只执行一次"},
            ],
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        request = {
            "cmd": "agent-turn",
            "session_id": "dedupe",
            "client_turn_id": "same-client-turn",
            "input": "不要重复",
        }
        await sc._handle_agent_turn(_mk_reader(), FakeWriter(), request)
        duplicate_writer = FakeWriter()
        await sc._handle_agent_turn(_mk_reader(), duplicate_writer, request)
        frames = duplicate_writer.frames()
        assert calls == 1
        assert [frame["type"] for frame in frames] == ["agent_start", "agent_end"]
        assert frames[-1]["reason"] == "duplicate_completed"
        assert frames[-1]["existing_run_id"]

    asyncio.run(go())


def test_agent_turn_same_session_reports_original_active_run(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    release = asyncio.Event()

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        await release.wait()
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=list(kwargs["transform_context"](messages)),
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        first_writer = FakeWriter()
        first = asyncio.create_task(sc._handle_agent_turn(
            _mk_reader(),
            first_writer,
            {
                "cmd": "agent-turn",
                "session_id": "serialized",
                "client_turn_id": "turn-1",
                "input": "一",
            },
        ))
        context_frame = await _wait_frame(first_writer, "context_usage")
        second_writer = FakeWriter()
        await sc._handle_agent_turn(
            _mk_reader(),
            second_writer,
            {
                "cmd": "agent-turn",
                "session_id": "serialized",
                "client_turn_id": "turn-2",
                "input": "二",
            },
        )
        assert second_writer.frames()[-1]["reason"] == "already_running"
        assert second_writer.frames()[-1]["existing_run_id"] == context_frame["run_id"]
        release.set()
        await first

    asyncio.run(go())


def test_agent_turn_rejects_extra_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn",
            "session_id": "s1",
            "client_turn_id": "c1",
            "input": "x",
            "messages": [],
        })
        frames = writer.frames()
        assert frames[0]["type"] == "error"
        assert "unexpected fields" in frames[0]["error"]
        assert frames[1]["type"] == "agent_end"
    asyncio.run(go())


def test_agent_turn_accepts_source_queue_id_field(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=list(kwargs["transform_context"](messages)),
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        service = sc._agent_service()
        service.sessions.create_session(session_id="s1")
        restored = service.sessions.add_queued_input(
            "s1",
            "old-run",
            "follow_up",
            "old-client-message",
            "x",
        )
        service.sessions.restore_pending_inputs("s1", "old-run")
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn",
            "session_id": "s1",
            "client_turn_id": "c1",
            "input": "x",
            "source_queue_id": restored.id,
        })
        assert not any(frame["type"] == "error" for frame in writer.frames())
        assert writer.frames()[-1]["type"] == "agent_end"
        queue_items = service.sessions.queued_inputs(
            session_id="s1",
            include_terminal=True,
        )
        assert next(item for item in queue_items if item.id == restored.id).status == "discarded"

    asyncio.run(go())


def test_agent_turn_rejects_invalid_source_queue_id_without_appending_message(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(
            reader,
            writer,
            {
                "cmd": "agent-turn",
                "session_id": "s-invalid-source",
                "client_turn_id": "c1",
                "input": "must not persist",
                "source_queue_id": "missing-restored-item",
            },
        )
        frames = writer.frames()
        assert any(frame["type"] == "error" for frame in frames)
        assert frames[-1]["type"] == "agent_end"
        service = sc._agent_service()
        assert all(
            message.content != "must not persist"
            for message in service.sessions.read_messages("s-invalid-source")
        )

    asyncio.run(go())


def test_agent_turn_same_connection_accepts_steering_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    class Token:
        def __init__(self):
            self.reason = None

        def abort(self, reason):
            self.reason = reason

    class Runtime:
        def __init__(self, token):
            self.token = token

        def state(self, run_id):
            return type("State", (), {"abort_token": self.token})()

    class Service:
        def __init__(self):
            self.token = Token()
            self.runtime = Runtime(self.token)
            self.accepted = asyncio.Event()

        def duplicate_turn(self, session_id, client_turn_id):
            return None

        async def run_turn(self, session_id, client_turn_id, input, emit, request_write):
            await emit(sc.AgentEvent(
                id="e1", session_id=session_id, run_id="run-q", parent_id=None,
                timestamp=1.0, sequence=1, type="agent_start",
                payload={"client_turn_id": client_turn_id},
            ))
            await asyncio.wait_for(self.accepted.wait(), timeout=1)
            await emit(sc.AgentEvent(
                id="e2", session_id=session_id, run_id="run-q", parent_id=None,
                timestamp=2.0, sequence=2, type="agent_end",
                payload={"status": "completed"},
            ))

        def steer(self, run_id, client_message_id, input, source_queue_id=None):
            item = {
                "id": "q1",
                "client_message_id": client_message_id,
                "session_id": "s1",
                "run_id": run_id,
                "mode": "steering",
                "content": input,
                "status": "pending",
                "created_at": 1.5,
            }
            self.accepted.set()
            return {"item": item, "queued_inputs": [item]}

    service = Service()
    monkeypatch.setattr(sc, "_AGENT_SERVICE", service)
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "开始",
        }))
        start = await _wait_frame(writer, "agent_start")
        _feed(reader, {
            "cmd": "agent-control",
            "action": "steer",
            "run_id": start["run_id"],
            "client_message_id": "m1",
            "input": "补充条件",
        })
        update = await _wait_frame(writer, "queue_update")
        await task
        assert update["operation"] == "accepted"
        assert update["item"]["id"] == "q1"
        assert update["steering_count"] == 1
        frames = writer.frames()
        assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))

    asyncio.run(go())


def test_agent_turn_confirm_approve_uses_same_connection(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda command, args: {"command": command, "args": args})

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        result = await request_write(
            command="run", args=["update-cs-data"],
            tool_name="run_task", tool_args={"task": "update-cs-data"},
        )
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=[*kwargs["transform_context"](messages), {
                "role": "tool", "name": "run_task", "tool_call_id": "write-1",
                "content": json.dumps(result),
            }],
            tool_results=[result],
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "更新",
        }))
        frame = await _wait_frame(writer, "confirm_required")
        _feed(reader, {
            "cmd": "agent-control", "action": "confirm", "run_id": frame["run_id"],
            "call_id": frame["call_id"], "approved": True,
        })
        await task
        assert writer.frames()[-1]["type"] == "agent_end"
        tool_messages = [
            message for message in sc.SessionStore(tmp_path).read_messages("s1")
            if message.role == "tool"
        ]
        assert tool_messages[0].tool_calls[0].result["ok"] is True

    asyncio.run(go())


def test_agent_turn_abort_while_confirm_pending_rejects_without_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)

    def fail_dispatch(command, args):
        pytest.fail("abort during confirmation must not dispatch a write command")

    monkeypatch.setattr(bridge, "dispatch", fail_dispatch)

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        result = await request_write(
            command="run",
            args=["update-cs-data"],
            tool_name="run_task",
            tool_args={"task": "update-cs-data"},
        )
        assert result["error"] == "aborted"
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=[*kwargs["transform_context"](messages)],
            tool_results=[result],
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn",
            "session_id": "abort-confirm",
            "client_turn_id": "c1",
            "input": "更新但马上停止",
        }))
        frame = await _wait_frame(writer, "confirm_required")
        _feed(reader, {
            "cmd": "agent-control",
            "action": "abort",
            "run_id": frame["run_id"],
            "reason": "client_abort",
        })
        await task
        frames = writer.frames()
        assert frames[-1]["type"] == "agent_end"
        assert frames[-1]["termination_reason"] == "client_abort"
        assert sc.SessionStore(tmp_path).find_run_by_client_turn_id(
            "abort-confirm", "c1"
        )["status"] == "aborted"

    asyncio.run(go())


def test_agent_turn_abort_stops_run_before_more_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        token = kwargs["abort_token"]
        while not token.aborted:
            await asyncio.sleep(0.01)
        raise asyncio.CancelledError(token.reason)

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "停止",
        }))
        start = await _wait_frame(writer, "turn_start")
        _feed(reader, {
            "cmd": "agent-control", "action": "abort", "run_id": start["run_id"],
        })
        await task
        # Runner-owned turn boundaries close before Runtime reports the
        # terminal abort and crosses the agent settlement barrier.
        assert [frame["type"] for frame in writer.frames()][-3:] == [
            "turn_end", "error", "agent_end",
        ]

    asyncio.run(go())


def test_agent_turn_emits_nested_memory_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)

    async def fake_run_turn(messages, emit, request_write, **kwargs):
        handler = kwargs["tool_registry"].handler("propose_memory")
        assert handler is not None
        result = await handler({"text": "偏好简洁回答", "source": "user"})
        await emit({"type": "done", "reason": "stop"})
        return chat_loop.TurnTranscript(
            messages=list(kwargs["transform_context"](messages)),
            tool_results=[result],
            run_state={"status": "done", "reason": "stop"},
        )

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "记住",
        })
        candidate = next(frame for frame in writer.frames() if frame["type"] == "memory_candidate")
        assert candidate["memory_candidate"]["text"] == "偏好简洁回答"
        assert candidate["memory_candidate"]["status"] == "proposed"

    asyncio.run(go())


def test_agent_json_commands_standard_response(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    resp = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-session", "action": "create", "session_id": "s2", "title": "T"
    }))
    assert resp["code"] == 0
    payload = json.loads(resp["stdout"])["data"]
    assert payload["selected_session_id"] == "s2"
    assert payload["sessions"][0]["session_id"] == "s2"
    assert payload["sessions"][0]["updated_at"]

    mem_store = sc.MemoryStore(tmp_path)
    mem = mem_store.propose("preference", "记住 A", metadata={"source": "test"})
    assert mem.status == "proposed"
    approved = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-memories", "action": "approve", "memory_id": mem.id
    }))
    memory_payload = json.loads(approved["stdout"])["data"]
    assert memory_payload["memories"][0]["text"] == "记住 A"
    assert memory_payload["memories"][0]["archived"] is False
    assert memory_payload["candidates"] == []
    assert (tmp_path / "storage" / "agent" / "memories.jsonl").is_file()

    proposed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-memories", "action": "propose",
        "text": "偏好短回答", "kind": "preference", "source_session": "s2",
    }))
    proposed_payload = json.loads(proposed["stdout"])["data"]
    assert proposed_payload["candidates"][0]["text"] == "偏好短回答"


def test_agent_queue_json_list_and_discard(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    class Service:
        def queued_inputs(self, session_id=None, run_id=None):
            return [{
                "id": "q1",
                "client_message_id": "m1",
                "session_id": session_id,
                "run_id": "run-1",
                "mode": "follow_up",
                "content": "追问",
                "status": "restored",
                "created_at": 1.0,
            }]

        def discard_queued_input(self, session_id, queue_id):
            return {"item": {"id": queue_id, "session_id": session_id, "status": "discarded"}}

    monkeypatch.setattr(sc, "_AGENT_SERVICE", Service())
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))

    listed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-queue", "action": "list", "session_id": "s1",
    }))
    listed_payload = json.loads(listed["stdout"])["data"]
    assert listed_payload["queued_inputs"][0]["id"] == "q1"
    assert listed_payload["follow_up_count"] == 1

    discarded = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-queue", "action": "discard", "session_id": "s1", "queue_id": "q1",
    }))
    discarded_payload = json.loads(discarded["stdout"])["data"]
    assert discarded_payload["ok"] is True
    assert discarded_payload["operation"] == "discarded"


def test_agent_queue_list_preserves_store_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    class Service:
        def queued_inputs(self, session_id=None, run_id=None):
            raise OSError("queue file unreadable")

    monkeypatch.setattr(sc, "_AGENT_SERVICE", Service())
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))

    response = json.loads(
        sc._handle_agent_json_command(
            {
                "cmd": "agent-queue",
                "action": "list",
                "session_id": "s1",
            }
        )
    )
    assert response["code"] == 1
    assert "queue file unreadable" in response["stderr"]


def test_agent_memory_source_recall_uses_structured_memory_id(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    store = sc.MemoryStore(tmp_path)
    record = store.propose(
        "thesis",
        "RSI 阈值历史判断需要复核",
        source_session="s1",
        source_entry="entry-1",
    )
    store.approve(record.id)

    recalled = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-memories",
        "action": "source-recall",
        "query": "RSI 阈值",
    }))
    payload = json.loads(recalled["stdout"])["data"]
    item = payload["recalls"][0]
    assert item["id"] == record.id
    assert item["source_session"] == "s1"
    assert item["source_entry"] == "entry-1"
    assert item["review_required"] is True
    assert "待复核" in item["excerpt"]


def test_agent_skill_actions_return_uniform_shape(monkeypatch, tmp_path):
    skill = tmp_path / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\ndescription: demo skill\n---\nbody", encoding="utf-8")
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)

    listed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-skills", "action": "list", "session_id": "s1",
    }))
    listed_payload = json.loads(listed["stdout"])["data"]
    assert listed_payload["skills"][0]["enabled"] is True
    assert listed_payload["skills"][0]["pinned"] is False

    pinned = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-skills", "action": "pin", "session_id": "s1",
        "skill_id": listed_payload["skills"][0]["id"], "pinned": True,
    }))
    assert json.loads(pinned["stdout"])["data"]["skills"][0]["pinned"] is True

    unpinned = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-skills", "action": "pin", "session_id": "s1",
        "skill_id": listed_payload["skills"][0]["id"], "pinned": False,
    }))
    assert json.loads(unpinned["stdout"])["data"]["skills"][0]["pinned"] is False
