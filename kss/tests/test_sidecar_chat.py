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


@pytest.fixture(autouse=True)
def _reset_harness_crash_domains():
    sc.reset_harness_crash_domains()
    yield
    sc.reset_harness_crash_domains()



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


def _install_desktop_session(monkeypatch, session=None, **kwargs):
    from kss.agent.desktop_host import DesktopHarnessHost, ScriptedDesktopSession

    session = session or ScriptedDesktopSession(**kwargs)
    host = DesktopHarnessHost(
        session=session,
        grant_write=sc.grant_harness_write,
        revoke_grant=sc.revoke_harness_write,
    )
    host.execute_tool = lambda **kw: sc.execute_harness_tool(**kw)
    monkeypatch.setattr(sc, "_desktop_harness_host", lambda: host)
    monkeypatch.setattr(sc, "_DESKTOP_HARNESS_HOST", host)
    return host, session


# ---------------------------------------------------------------------------

def test_execute_write_gated(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", False)
    assert sc._execute_write("run", ["x"])["error"] == "not_live"
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c, "args": a})
    out = sc._execute_write("run", ["update-cs-data"])
    assert out["ok"] and out["result"]["ran"] == "run"


def test_chat_turn_read_only_happy(monkeypatch):
    """Legacy chrome still streams, but Harness owns the turn."""
    called = []

    async def boom(*args, **kwargs):
        called.append(True)
        raise AssertionError("run_turn must not own chat")

    monkeypatch.setattr(chat_loop, "run_turn", boom)
    _install_desktop_session(monkeypatch)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "盘面"}]})
        return writer.frames()

    frames = asyncio.run(go())
    assert called == []
    chunks = [f.get("text") for f in frames if f.get("type") == "chunk"]
    assert "".join(str(t or "") for t in chunks) == "答复"
    assert any(f.get("type") == "done" for f in frames)


def test_confirm_approve_reader_executes_write(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c})
    monkeypatch.setattr(chat_loop, "run_turn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no run_turn")))
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task", "command": "run", "args": ["update-cs-data"],
        "tool_args": {"task": "update-cs-data"},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "更新"}]}))
        cr = await _wait_frame(writer, "confirm_required")
        assert cr["tool"] == "run_task" and cr["command"] == "run"
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        wr = done.get("writeResult") or {}
        assert wr.get("ok") and wr.get("result", {}).get("ran") == "run"
    asyncio.run(go())


def test_confirm_deny_no_write(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda *a: pytest.fail("拒绝不应 dispatch"))
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "cron_rerun", "command": "cron-rerun", "args": ["daily"],
        "tool_args": {"label": "daily"},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "x"}]}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": False})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert (done.get("writeResult") or {}).get("error") == "denied"
    asyncio.run(go())


def test_call_id_unmatched_discarded_then_correct(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c})
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task", "command": "run", "args": ["x"], "tool_args": {},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "x"}]}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": "BOGUS", "approved": True})
        await asyncio.sleep(0.05)
        assert not task.done()
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert (done.get("writeResult") or {}).get("ok")
    asyncio.run(go())


def test_not_live_rejects_even_approved(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", False)
    monkeypatch.setattr(bridge, "dispatch", lambda *a: pytest.fail("not live"))
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task", "command": "run", "args": ["x"], "tool_args": {},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "x"}]}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        assert (done.get("writeResult") or {}).get("error") == "not_live"
    asyncio.run(go())


def test_disconnect_rejects_pending(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task", "command": "run", "args": ["x"], "tool_args": {},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "x"}]}))
        await _wait_frame(writer, "confirm_required")
        reader.feed_eof()
        await task
        done = next(f for f in writer.frames() if f["type"] == "done")
        err = (done.get("writeResult") or {}).get("error")
        assert err in {"disconnected", "aborted", "denied"}
    asyncio.run(go())


def test_confirm_timeout(monkeypatch):
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(sc, "_CONFIRM_TIMEOUT", 0.1)
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task", "command": "run", "args": ["x"], "tool_args": {},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "x"}]})
        done = next(f for f in writer.frames() if f["type"] == "done")
        err = (done.get("writeResult") or {}).get("error")
        assert err in {"denied", "confirm_timeout", "aborted"}
    asyncio.run(go())


def test_desktop_live_write_always_asks(monkeypatch):
    """R6：桌面 AUTO 任务也不能绕过问人。"""
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(chat_loop, "AUTO_TASKS", frozenset({"refresh-market-strip"}))
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"ran": c})
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task", "command": "run", "args": ["refresh-market-strip"],
        "tool_args": {"task": "refresh-market-strip"},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_chat_turn(reader, writer, {"messages": [{"role": "user", "content": "x"}]}))
        cr = await _wait_frame(writer, "confirm_required")
        _feed(reader, {"cmd": "chat-turn-confirm", "call_id": cr["call_id"], "approved": True})
        await task
        assert any(f.get("type") == "confirm_required" for f in writer.frames())
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
    _install_desktop_session(monkeypatch)

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
    _host, session = _install_desktop_session(monkeypatch)

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
        assert session.runs == 1
        assert [frame["type"] for frame in frames] == ["agent_start", "agent_end"]
        assert frames[-1]["reason"] == "duplicate_completed"
        assert frames[-1]["existing_run_id"]

    asyncio.run(go())


def test_agent_turn_same_session_reports_original_active_run(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    _install_desktop_session(monkeypatch, wait_inbox=True)

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
        start = await _wait_frame(first_writer, "agent_start")
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
        frames = second_writer.frames()
        assert frames[-1]["reason"] == "already_running"
        assert frames[-1]["existing_run_id"] == start["run_id"]
        _feed(_mk_reader(), {})  # no-op; finish first via abort on its reader
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass

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
    _install_desktop_session(monkeypatch)

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
    host, _session = _install_desktop_session(monkeypatch, wait_inbox=True)
    steered = []
    original = host.enqueue

    def wrapped(**kwargs):
        steered.append(kwargs)
        return original(**kwargs)

    host.enqueue = wrapped  # type: ignore[method-assign]

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
        assert update["item"]["mode"] == "steering"
        assert update["steering_count"] == 1
        assert steered and steered[0]["mode"] == "steer"
        frames = writer.frames()
        assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))

    asyncio.run(go())


def test_agent_turn_confirm_approve_uses_same_connection(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda command, args: {"command": command, "args": args})
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task",
        "command": "run",
        "args": ["update-cs-data"],
        "tool_args": {"task": "update-cs-data"},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "更新",
        }))
        frame = await _wait_frame(writer, "confirm_required")
        assert frame["call_id"]
        assert frame["tool"] == "run_task"
        assert frame["command"] == "run"
        assert "覆盖本地行情" in frame["effect"]
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
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task",
        "command": "run",
        "args": ["update-cs-data"],
        "tool_args": {"task": "update-cs-data"},
    })

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
    _install_desktop_session(monkeypatch, block_until_abort=True)

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
        assert writer.frames()[-1]["type"] == "agent_end"
        assert writer.frames()[-1]["termination_reason"] == "client_abort"

    asyncio.run(go())


def test_agent_turn_emits_nested_memory_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    _install_desktop_session(monkeypatch, events=[{
        "type": "memory_candidate",
        "memory_candidate": {
            "id": "m1",
            "text": "偏好简洁回答",
            "status": "proposed",
            "source": "user",
        },
    }])

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "记住",
        })
        candidate = next(frame for frame in writer.frames() if frame["type"] == "memory_candidate")
        assert candidate["memory_candidate"]["text"] == "偏好简洁回答"
        assert candidate["memory_candidate"]["status"] == "proposed"

    asyncio.run(go())


def test_agent_turn_without_host_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    called = []

    async def fake_run_turn(*args, **kwargs):
        called.append(True)
        raise AssertionError("kss_chat_loop must not own agent-turn")

    monkeypatch.setattr(chat_loop, "run_turn", fake_run_turn)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "盘面",
        })
        frames = writer.frames()
        assert any(f.get("error") == "harness_session_unavailable" for f in frames)
        assert frames[-1]["type"] == "agent_end"
        assert frames[-1]["reason"] == "harness_unavailable"
        assert called == []

    asyncio.run(go())


def test_agent_turn_late_allow_after_abort_is_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)

    def fail_dispatch(command, args):
        pytest.fail("late allow after abort must not dispatch")

    monkeypatch.setattr(bridge, "dispatch", fail_dispatch)
    host, session = _install_desktop_session(monkeypatch, confirm_intent={
        "call_id": "frozen-call",
        "name": "run_task",
        "command": "run",
        "args": ["update-cs-data"],
        "tool_args": {"task": "update-cs-data"},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "更新",
        }))
        frame = await _wait_frame(writer, "confirm_required")
        _feed(reader, {
            "cmd": "agent-control", "action": "abort", "run_id": frame["run_id"],
        })
        await task
        out = sc.execute_harness_tool(
            name="run_task",
            args={"task": "update-cs-data"},
            call_id=frame["call_id"],
        )
        assert out["error"] == "not_allowed"
        assert session.last_write is None

    asyncio.run(go())


def test_agent_turn_disconnect_during_confirm_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda *a: pytest.fail("disconnect must not write"))
    _install_desktop_session(monkeypatch, confirm_intent={
        "name": "run_task",
        "command": "run",
        "args": ["update-cs-data"],
        "tool_args": {"task": "update-cs-data"},
    })

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        task = asyncio.create_task(sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "更新",
        }))
        await _wait_frame(writer, "confirm_required")
        reader.feed_eof()
        await task
        assert writer.frames()[-1]["type"] == "agent_end"

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

    routed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-session",
        "action": "set_provider_route",
        "session_id": "s2",
        "provider_route": {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "thinking_level": "high",
        },
    }))
    routed_payload = json.loads(routed["stdout"])["data"]
    session = next(item for item in routed_payload["sessions"] if item["session_id"] == "s2")
    assert session["provider_route"]["model_id"] == "deepseek-v4-flash"

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


def test_agent_provider_catalog_and_route_actions(monkeypatch, tmp_path):
    class ProviderService:
        def provider_catalog(self, *, refresh=False, provider_id=None):
            assert provider_id is None
            return {
                "status": "ready",
                "models": [{
                    "provider_id": "openai",
                    "model_id": "gpt-test",
                    "name": "GPT Test",
                }],
                "primary": {
                    "provider_id": "openai",
                    "model_id": "gpt-test",
                    "thinking_level": "off",
                },
                "fallback": None,
            }

        def set_provider_routes(self, *, primary, fallback=None):
            assert primary["model_id"] == "gpt-next"
            return {
                "status": "ready",
                "models": [],
                "primary": primary,
                "fallback": fallback,
            }

        def test_provider_connection(self, *, primary=None, fallback=None):
            assert primary == {"provider_id": "openai", "model_id": "gpt-next"}
            assert fallback is None
            return {
                "source": "llm",
                "ok": True,
                "status": "ready",
                "latency_ms": 12.5,
                "hint": "stream ok",
                "candidates": [{
                    "role": "primary",
                    "model": "gpt-test",
                    "ok": True,
                    "latency_ms": 12.5,
                    "hint": "stream ok",
                }],
                "providers": [{
                    "id": "openai",
                    "name": "OpenAI",
                    "auth_kind": "api_key",
                    "models": [],
                }],
                "models": [],
                "primary": {
                    "provider_id": "openai",
                    "model_id": "gpt-test",
                    "thinking_level": "off",
                },
                "fallback": None,
            }

    monkeypatch.setattr(sc, "_AGENT_SERVICE", ProviderService())
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    listed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-providers",
        "action": "list",
    }))
    payload = json.loads(listed["stdout"])["data"]
    assert payload["providers"][0]["id"] == "openai"
    assert payload["providers"][0]["models"][0]["model_id"] == "gpt-test"

    updated = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-providers",
        "action": "set_route",
        "primary": {"provider_id": "openai", "model_id": "gpt-next"},
    }))
    updated_payload = json.loads(updated["stdout"])["data"]
    assert updated_payload["primary"]["model_id"] == "gpt-next"

    tested = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-providers",
        "action": "test",
        "primary": {"provider_id": "openai", "model_id": "gpt-next"},
    }))
    tested_payload = json.loads(tested["stdout"])["data"]
    assert tested_payload["ok"] is True
    assert tested_payload["candidates"][0]["hint"] == "stream ok"


def test_agent_provider_set_route_accepts_vision(monkeypatch, tmp_path):
    class VisionService:
        def set_provider_routes(self, *, primary, fallback=None, vision=None):
            assert primary["model_id"] == "deepseek-v4-pro"
            assert vision == {"provider_id": "openai", "model_id": "gpt-vision"}
            return {
                "status": "ready",
                "models": [],
                "providers": [],
                "primary": primary,
                "fallback": fallback,
                "vision": vision,
            }

    monkeypatch.setattr(sc, "_AGENT_SERVICE", VisionService())
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    updated = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-providers",
        "action": "set_route",
        "primary": {"provider_id": "deepseek", "model_id": "deepseek-v4-pro"},
        "vision": {"provider_id": "openai", "model_id": "gpt-vision"},
    }))
    payload = json.loads(updated["stdout"])["data"]
    assert payload["vision"]["model_id"] == "gpt-vision"


def test_agent_provider_set_route_rejects_non_object_vision(monkeypatch, tmp_path):
    class IdleService:
        def close(self):
            pass

    monkeypatch.setattr(sc, "_AGENT_SERVICE", IdleService())
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    result = sc._providers_action_payload({
        "action": "set_route",
        "primary": {"provider_id": "deepseek", "model_id": "deepseek-v4-pro"},
        "vision": "gpt-vision",
    })
    assert result["status"] == "error"
    assert "vision" in result["error"]


def test_agent_turn_passes_session_provider_route(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    host, _session = _install_desktop_session(monkeypatch)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn",
            "session_id": "route-s1",
            "client_turn_id": "route-c1",
            "input": "路由",
        })
        assert host.last_requests, "desktop turn should run"
        request = host.last_requests[-1]
        assert isinstance(request.provider_route, dict)
        assert request.provider_route.get("provider_id")
        assert request.provider_route.get("model_id")
        assert "thinking_level" in request.provider_route

    asyncio.run(go())


def test_agent_turn_injects_file_refs_into_harness_input(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "storage" / "reports" / "demo" / "scan.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# 扫描结论\n北证50 走强。", encoding="utf-8")
    host, _session = _install_desktop_session(monkeypatch)

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn",
            "session_id": "ref-s1",
            "client_turn_id": "ref-c1",
            "input": "看下这份报告",
            "file_refs": ["storage/reports/demo/scan.md", "../etc/passwd"],
        })
        request = host.last_requests[-1]
        assert request.input.startswith("看下这份报告")
        assert "[引用文件 storage/reports/demo/scan.md]" in request.input
        assert "北证50 走强" in request.input
        assert "(不可用：文件不存在或超出可引用范围)" in request.input
        # KSS 会话存储保留原始输入与引用元数据，不混入注入正文
        store = sc.SessionStore(tmp_path)
        user = store.read_messages("ref-s1")[0]
        assert user.content == "看下这份报告"
        assert user.metadata["file_refs"] == [
            "storage/reports/demo/scan.md", "../etc/passwd",
        ]

    asyncio.run(go())


def test_agent_turn_rejects_bad_file_refs(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    _install_desktop_session(monkeypatch)

    async def go():
        writer = FakeWriter()
        await sc._handle_agent_turn(_mk_reader(), writer, {
            "cmd": "agent-turn",
            "session_id": "ref-s2",
            "client_turn_id": "ref-c2",
            "input": "x",
            "file_refs": [1, 2],
        })
        frames = writer.frames()
        assert frames[0]["type"] == "error"
        assert "file_refs" in frames[0]["error"]

    asyncio.run(go())


def test_vision_context_requires_configured_route(monkeypatch, tmp_path):
    from kss.agent.service import KSSAgentService

    service = KSSAgentService(tmp_path, tmp_path)
    monkeypatch.setattr(sc, "_AGENT_SERVICE", service)
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    payload = sc._vision_context_payload({"path": "storage/reports/x.png"})
    assert payload["error"] == "vision_route_unconfigured"


def test_vision_context_resolves_path_route_and_env(monkeypatch, tmp_path):
    from kss.agent.service import KSSAgentService

    service = KSSAgentService(tmp_path, tmp_path)
    monkeypatch.setattr(sc, "_AGENT_SERVICE", service)
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "kss.agent.harness_kernel.get_harness_kernel", lambda: None
    )
    service.set_provider_routes(
        primary={"provider_id": "deepseek", "model_id": "deepseek-v4-pro"},
        vision={"provider_id": "acme-gateway", "model_id": "acme-vision",
                "base_url": "https://gateway.acme.example/v1",
                "supports_images": True},
    )
    image = tmp_path / "storage" / "reports" / "shot.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG fake")

    payload = sc._vision_context_payload({"path": "storage/reports/shot.png"})
    assert payload["ok"] is True
    assert payload["file_path"] == str(image.resolve())
    route = payload["route"]
    assert route["model_id"] == "acme-vision"
    assert route["base_url"] == "https://gateway.acme.example/v1"
    assert route["api_key_env"] == "KSS_PROVIDER_ACME_GATEWAY_API_KEY"

    # 非白名单路径与非图片扩展都拒绝
    assert sc._vision_context_payload({"path": "../secrets.png"})["error"] == "path_not_allowed"
    assert sc._vision_context_payload({"path": "storage/reports/a.md"})["error"] == "path_not_allowed"
    assert sc._vision_context_payload({})["error"] == "vision_target_missing"


def test_vision_context_resolves_attachment(monkeypatch, tmp_path):
    from kss.agent.service import KSSAgentService

    service = KSSAgentService(tmp_path, tmp_path)
    monkeypatch.setattr(sc, "_AGENT_SERVICE", service)
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "kss.agent.harness_kernel.get_harness_kernel", lambda: None
    )
    service.set_provider_routes(
        primary={"provider_id": "deepseek", "model_id": "deepseek-v4-pro"},
        vision={"provider_id": "openai", "model_id": "gpt-vision",
                "supports_images": True},
    )
    # 1x1 PNG（最小合法头），attachments._detect_type 需要真实图片魔数
    png = (
        b"\x89PNG\r\n\x1a\n" + bytes.fromhex(
            "0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c636000000200015d0a2db40000000049454e44ae426082"
        )
    )
    source = tmp_path / "shot.png"
    source.write_bytes(png)
    record = service.import_attachment(str(source))

    payload = sc._vision_context_payload({"attachment_id": record.id})
    assert payload["ok"] is True, payload
    assert payload["media_type"] == "image/png"
    assert Path(payload["file_path"]).is_file()
    assert payload["route"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["route"]["base_url"] == "https://api.openai.com/v1"


def test_agent_attachment_import_list_remove(monkeypatch, tmp_path):
    from kss.agent.attachments import AttachmentStore

    class AttachmentService:
        attachments = AttachmentStore(tmp_path)

        def import_attachment(self, source, *, extracted_text=None):
            return self.attachments.import_file(
                source,
                extracted_text=extracted_text,
            )

    service = AttachmentService()
    monkeypatch.setattr(sc, "_AGENT_SERVICE", service)
    monkeypatch.setattr(sc, "_AGENT_SERVICE_ROOTS", (tmp_path, tmp_path))
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    selected = tmp_path / "selected.txt"
    selected.write_text("附件正文", encoding="utf-8")

    imported = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-attachments",
        "action": "import",
        "session_id": "s1",
        "path": str(selected),
    }))
    imported_payload = json.loads(imported["stdout"])["data"]
    attachment_id = imported_payload["attachment"]["id"]
    assert imported_payload["attachment"]["extraction_status"] == "extracted"

    listed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-attachments",
        "action": "list",
        "session_id": "s1",
    }))
    assert json.loads(listed["stdout"])["data"]["attachments"][0]["id"] == attachment_id

    removed = json.loads(sc._handle_agent_json_command({
        "cmd": "agent-attachments",
        "action": "remove",
        "session_id": "s1",
        "attachment_id": attachment_id,
    }))
    assert json.loads(removed["stdout"])["data"]["attachments"] == []



def test_agent_turn_timeout_revives_kernel(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    revived: list[bool] = []
    monkeypatch.setattr(sc, "revive_harness_kernel_after_timeout", lambda: revived.append(True))

    class TimeoutSession:
        async def run(self, request, host):
            from kss.agent.desktop_host import DesktopTurnResult
            return DesktopTurnResult(
                status="unavailable",
                error="harness kernel timed out on desktop.turn",
            )

    _install_desktop_session(monkeypatch, session=TimeoutSession())

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c1", "input": "上手",
        })
        frames = writer.frames()
        assert any("timed out" in str(f.get("error") or "") for f in frames)
        assert revived == [True]

    asyncio.run(go())


def test_agent_turn_empty_completion_emits_error_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)

    class EmptySession:
        async def run(self, request, host):
            from kss.agent.desktop_host import DesktopTurnResult
            return DesktopTurnResult(status="completed", assistant_text="")

    _install_desktop_session(monkeypatch, session=EmptySession())

    async def go():
        reader, writer = _mk_reader(), FakeWriter()
        await sc._handle_agent_turn(reader, writer, {
            "cmd": "agent-turn", "session_id": "s1", "client_turn_id": "c-empty", "input": "上手",
        })
        frames = writer.frames()
        assert any(f.get("error") == "empty_completion" for f in frames)
        assert frames[-1]["reason"] == "harness_unavailable"
        texts = [m.content for m in sc._agent_service().sessions.read_messages("s1")]
        assert any("empty_completion" in str(t) for t in texts)

    asyncio.run(go())
