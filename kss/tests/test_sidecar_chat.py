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
