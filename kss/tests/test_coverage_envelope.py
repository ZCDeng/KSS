"""U3: 覆盖路径心跳与 R12 收尾。非本路径仍 8 步 / 240 秒。

跑：.venv-desktop/bin/python -m pytest kss/tests/test_coverage_envelope.py -q
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as loop  # noqa: E402

from kss.equity_research.coverage_envelope import (  # noqa: E402
    R12_INCOMPLETE,
    is_coverage_intent,
)


class FakeChat:
    def __init__(self, scripts, repeat_last=False):
        self.scripts = list(scripts)
        self.repeat_last = repeat_last
        self.i = 0

    def stream_turn(self, messages, tools=None):
        if self.i < len(self.scripts):
            script = self.scripts[self.i]
            self.i += 1
        elif self.repeat_last and self.scripts:
            script = self.scripts[-1]
        else:
            script = [{"type": "text", "text": "ok"}, {"type": "finish", "reason": "stop"}]
        yield from script


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _toolcall(name: str, args: dict | None = None) -> dict:
    return {"type": "tool_call", "id": "c1", "name": name, "args": args or {}}


def test_coverage_intent_research_yes_explainer_no() -> None:
    assert is_coverage_intent("研究一下 600519.SH") is True
    assert is_coverage_intent("分析茅台估值") is True
    assert is_coverage_intent("今天为什么涨") is False
    assert is_coverage_intent("研究一下为什么跌") is False
    assert is_coverage_intent("盘面怎么了") is False


def test_non_coverage_timeout_keeps_legacy_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """表征：非覆盖路径超时仍是 done/timeout，不改写成 R12。"""
    monkeypatch.setattr(bridge, "dispatch", lambda *_a, **_k: {"x": 1})
    frames: list[dict] = []

    async def emit(ev):
        frames.append(ev)

    async def rw(**_kw):
        raise AssertionError("no write")

    chat = FakeChat([
        [_toolcall("get_snapshot"), {"type": "finish", "reason": "tool_calls"}],
        [_text("半章"), {"type": "finish", "reason": "stop"}],
    ])

    def slow_dispatch(*_a, **_k):
        time.sleep(0.05)
        return {"x": 1}

    monkeypatch.setattr(bridge, "dispatch", slow_dispatch)
    asyncio.run(loop.run_turn(
        [{"role": "user", "content": "盘面"}],
        emit,
        rw,
        chat_client=chat,
        max_steps=4,
        turn_timeout=0.02,
        coverage_path=False,
    ))
    assert frames[-1]["type"] == "done"
    assert frames[-1]["reason"] == "timeout"
    texts = "".join(str(f.get("text") or "") for f in frames if f.get("type") == "chunk")
    assert R12_INCOMPLETE not in texts


def test_coverage_timeout_replaces_partial_with_r12(monkeypatch: pytest.MonkeyPatch) -> None:
    frames: list[dict] = []

    async def emit(ev):
        frames.append(ev)

    async def rw(**_kw):
        raise AssertionError("no write")

    def slow_dispatch(*_a, **_k):
        time.sleep(0.05)
        return {"x": 1}

    monkeypatch.setattr(bridge, "dispatch", slow_dispatch)
    chat = FakeChat([
        [_text("第一章半篇备忘"), _toolcall("get_snapshot"),
         {"type": "finish", "reason": "tool_calls"}],
        [_text("不应出现"), {"type": "finish", "reason": "stop"}],
    ])
    transcript = asyncio.run(loop.run_turn(
        [{"role": "user", "content": "研究一下 600519.SH"}],
        emit,
        rw,
        chat_client=chat,
        max_steps=4,
        turn_timeout=0.02,
        coverage_path=True,
    ))
    chunk_texts = [str(f.get("text") or "") for f in frames if f.get("type") == "chunk"]
    assert chunk_texts == [R12_INCOMPLETE]
    assert frames[-1]["type"] == "done"
    assert frames[-1]["reason"] == "timeout"
    assert "半篇" not in "".join(chunk_texts)
    assistants = [m for m in transcript.messages if m.get("role") == "assistant"]
    assert assistants
    assert assistants[-1]["content"] == R12_INCOMPLETE


def test_coverage_max_steps_emits_r12(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "dispatch", lambda *_a, **_k: {"x": 1})
    frames: list[dict] = []

    async def emit(ev):
        frames.append(ev)

    async def rw(**_kw):
        raise AssertionError("no write")

    chat = FakeChat(
        [[_toolcall("get_snapshot"), {"type": "finish", "reason": "tool_calls"}]],
        repeat_last=True,
    )
    asyncio.run(loop.run_turn(
        [{"role": "user", "content": "研究茅台"}],
        emit,
        rw,
        chat_client=chat,
        max_steps=2,
        turn_timeout=30,
        coverage_path=True,
    ))
    assert frames[-1]["reason"] == "max_steps"
    assert any(f.get("type") == "chunk" and f.get("text") == R12_INCOMPLETE for f in frames)


def test_coverage_abort_emits_r12_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "dispatch", lambda *_a, **_k: {"x": 1})
    frames: list[dict] = []
    token = loop.AbortToken()

    async def emit(ev):
        frames.append(ev)

    async def rw(**_kw):
        raise AssertionError("no write")

    class SlowChat:
        def stream_turn(self, messages, tools=None):
            token.abort("client_abort")
            yield _text("半章")
            yield {"type": "finish", "reason": "stop"}

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run_turn(
            [{"role": "user", "content": "研究一下"}],
            emit,
            rw,
            chat_client=SlowChat(),
            abort_token=token,
            coverage_path=True,
            turn_timeout=30,
        ))
    assert any(f.get("type") == "chunk" and f.get("text") == R12_INCOMPLETE for f in frames)


def test_coverage_keepalive_during_slow_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "COVERAGE_KEEPALIVE_SECONDS", 0.02)

    def slow_dispatch(*_a, **_k):
        time.sleep(0.07)
        return {"ok": True}

    monkeypatch.setattr(bridge, "dispatch", slow_dispatch)
    frames: list[dict] = []

    async def emit(ev):
        frames.append(ev)

    async def rw(**_kw):
        raise AssertionError("no write")

    chat = FakeChat([
        [_toolcall("get_snapshot"), {"type": "finish", "reason": "tool_calls"}],
        [_text("完整报告"), {"type": "finish", "reason": "stop"}],
    ])
    asyncio.run(loop.run_turn(
        [{"role": "user", "content": "研究一下 600519.SH"}],
        emit,
        rw,
        chat_client=chat,
        max_steps=4,
        turn_timeout=30,
        coverage_path=True,
    ))
    assert any(f.get("type") == "keepalive" for f in frames)
    assert any(f.get("type") == "chunk" and "完整报告" in str(f.get("text")) for f in frames)
    assert not any(f.get("text") == R12_INCOMPLETE for f in frames)
