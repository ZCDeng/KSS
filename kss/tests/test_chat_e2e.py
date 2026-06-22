"""U7 端到端:经真 unix socket 驱 chat-turn 全链路(U1+U2+U3+U6 集成)。
mock LLM 用固定 tool_call 脚本;read 轮真调 bridge;写闸:无 confirm→reader 不执行,confirm→执行。
跑:.venv-desktop/bin/python -m pytest kss/tests/test_chat_e2e.py -q
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


class _FakeChatClient:
    """注入替身:按 turn 脚本产出事件(代替真 ChatClient + openai SDK)。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)

    def stream_turn(self, messages, tools=None):
        script = self.scripts.pop(0) if self.scripts else [{"type": "finish", "reason": "stop"}]
        for ev in script:
            yield ev


def _install_fake_llm(monkeypatch, scripts):
    monkeypatch.setattr(chat_loop, "ChatClient", lambda *a, **k: _FakeChatClient(scripts))


def _tc(name, args, id="c1"):
    return {"type": "tool_call", "id": id, "name": name, "args": args}


async def _serve_one(path):
    return await asyncio.start_unix_server(sc._on_connection, path=str(path))


async def _chat_client(path, request, *, on_confirm=None, timeout=5.0):
    """连 socket,发 request,收所有帧到 done/EOF。on_confirm(frame,writer) 处理 confirm_required。"""
    reader, writer = await asyncio.open_unix_connection(path=str(path))
    writer.write((json.dumps(request) + "\n").encode("utf-8"))
    await writer.drain()
    frames = []
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            break
        if not line:
            break
        fr = json.loads(line)
        frames.append(fr)
        if fr.get("type") == "confirm_required" and on_confirm:
            on_confirm(fr, writer)
        if fr.get("type") == "done":
            break
    writer.close()
    return frames


@pytest.fixture
def sock():
    # macOS AF_UNIX 路径上限 104 字符,tmp_path 太深 → 用短 /tmp 路径
    import os
    p = Path(f"/tmp/kss_e2e_{os.getpid()}.sock")
    if p.exists():
        p.unlink()
    yield p
    if p.exists():
        p.unlink()


def test_e2e_read_turn(monkeypatch, sock):
    """chat-turn → read 工具真调 bridge → 流式真值 → done。Covers R12。"""
    _install_fake_llm(monkeypatch, [
        [_tc("get_stock", {"symbol": "688008.SH"}), {"type": "finish", "reason": "tool_calls"}],
        [{"type": "text", "text": "688008 涨 3.2%"}, {"type": "finish", "reason": "stop"}],
    ])
    monkeypatch.setattr(bridge, "dispatch",
                        lambda c, a: {"symbol": a[0], "pctChange": 3.2} if c == "stock"
                        else pytest.fail(f"意外命令 {c}"))

    async def go():
        server = await _serve_one(sock)
        frames = await _chat_client(sock, {"cmd": "chat-turn",
                                           "messages": [{"role": "user", "content": "688008 今天为什么动"}]})
        server.close()
        await server.wait_closed()
        return frames

    frames = asyncio.run(go())
    types = [f["type"] for f in frames]
    assert "tool_call" in types and "tool_done" in types and "chunk" in types
    assert frames[-1]["type"] == "done"
    assert "".join(f["text"] for f in frames if f["type"] == "chunk") == "688008 涨 3.2%"


def test_e2e_write_no_confirm_not_executed(monkeypatch, sock):
    """gated 写 → 无 confirm 消息 → reader 不执行(超时按拒收尾),loop 续到 done。"""
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(sc, "_CONFIRM_TIMEOUT", 0.2)
    _install_fake_llm(monkeypatch, [
        [_tc("run_task", {"task": "update-cs-data"}), {"type": "finish", "reason": "tool_calls"}],
        [{"type": "text", "text": "已按拒处理,继续分析"}, {"type": "finish", "reason": "stop"}],
    ])
    monkeypatch.setattr(bridge, "dispatch", lambda *a: pytest.fail("无 confirm 不得执行写"))

    async def go():
        server = await _serve_one(sock)
        frames = await _chat_client(sock, {"cmd": "chat-turn", "messages": []})  # 不回 confirm
        server.close()
        await server.wait_closed()
        return frames

    frames = asyncio.run(go())
    assert any(f["type"] == "confirm_required" for f in frames)
    assert frames[-1]["type"] == "done"   # 超时拒后仍优雅收尾


def test_e2e_write_confirm_executes(monkeypatch, sock):
    """confirm 消息后 reader 亲自执行写 → 结果回喂 → done(端到端边界)。"""
    monkeypatch.setattr(sc, "_CHAT_LOOP_LIVE", True)
    executed = {}
    _install_fake_llm(monkeypatch, [
        [_tc("run_task", {"task": "update-cs-data"}), {"type": "finish", "reason": "tool_calls"}],
        [{"type": "text", "text": "已执行更新"}, {"type": "finish", "reason": "stop"}],
    ])

    def _dispatch(c, a):
        executed["call"] = (c, a)
        return {"updated": True}
    monkeypatch.setattr(bridge, "dispatch", _dispatch)

    def on_confirm(fr, writer):
        writer.write((json.dumps({"cmd": "chat-turn-confirm",
                                  "call_id": fr["call_id"], "approved": True}) + "\n").encode())

    async def go():
        server = await _serve_one(sock)
        frames = await _chat_client(sock, {"cmd": "chat-turn", "messages": []}, on_confirm=on_confirm)
        server.close()
        await server.wait_closed()
        return frames

    frames = asyncio.run(go())
    assert executed.get("call") == ("run", ["update-cs-data"])   # reader 执行了写
    assert frames[-1]["type"] == "done"


def test_e2e_max_steps_graceful(monkeypatch, sock):
    """不收敛 tool_call 脚本 → 达步数上限优雅终止。"""
    monkeypatch.setattr(chat_loop, "_DEFAULT_MAX_STEPS", 3)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: {"x": 1})

    class _Loop:
        def stream_turn(self, messages, tools=None):
            yield _tc("get_snapshot", {})
            yield {"type": "finish", "reason": "tool_calls"}
    monkeypatch.setattr(chat_loop, "ChatClient", lambda *a, **k: _Loop())

    async def go():
        server = await _serve_one(sock)
        frames = await _chat_client(sock, {"cmd": "chat-turn", "messages": []})
        server.close()
        await server.wait_closed()
        return frames

    frames = asyncio.run(go())
    assert frames[-1]["type"] == "done" and frames[-1]["reason"] == "max_steps"
