"""U1: seek HTTP MCP 客户端 + 探活。

不依赖本机 seek 容器:用 FakeClient 替换模块级 ``Client``,验证解析、降级与探活。
"""

from __future__ import annotations

import pytest

from kss.research import seek_client


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResult:
    def __init__(self, text: str | None = None, structured=None) -> None:
        self.content = [_FakeBlock(text)] if text is not None else []
        self.structured_content = structured


def _make_fake_client(*, result=None, raise_on_enter=None, raise_on_call=None, raise_on_ping=None):
    class _FakeClient:
        def __init__(self, endpoint, init_timeout=None):
            self.endpoint = endpoint

        async def __aenter__(self):
            if raise_on_enter is not None:
                raise raise_on_enter
            return self

        async def __aexit__(self, *exc):
            return False

        async def call_tool(self, name, args, timeout=None):
            if raise_on_call is not None:
                raise raise_on_call
            return result

        async def ping(self):
            if raise_on_ping is not None:
                raise raise_on_ping
            return True

    return _FakeClient


def test_extract_from_content_blocks():
    res = _FakeResult(text="1. 四川宜宾地震 热度:4097757")
    out = seek_client._extract(res)
    assert out["text"] == "1. 四川宜宾地震 热度:4097757"


def test_extract_falls_back_to_structured_result_key():
    res = _FakeResult(text=None, structured={"result": "热榜文本"})
    out = seek_client._extract(res)
    assert out["text"] == "热榜文本"
    assert out["structured"] == {"result": "热榜文本"}


def test_reach_success(monkeypatch):
    fake = _make_fake_client(result=_FakeResult(text="榜单"))
    monkeypatch.setattr(seek_client, "Client", fake)
    out = seek_client.reach("reach_weibo_hot")
    assert out["ok"] is True
    assert out["error"] is None
    assert out["tool"] == "reach_weibo_hot"
    assert out["text"] == "榜单"


def test_reach_degrades_on_connection_error(monkeypatch):
    fake = _make_fake_client(raise_on_enter=ConnectionRefusedError("refused"))
    monkeypatch.setattr(seek_client, "Client", fake)
    out = seek_client.reach("reach_weibo_hot")
    assert out["ok"] is False
    assert out["text"] == ""
    assert "ConnectionRefusedError" in out["error"]
    assert out["tool"] == "reach_weibo_hot"


def test_reach_degrades_on_timeout(monkeypatch):
    fake = _make_fake_client(raise_on_call=TimeoutError("slow"))
    monkeypatch.setattr(seek_client, "Client", fake)
    out = seek_client.reach("bocha_web_search", query="固态电池")
    assert out["ok"] is False
    assert "TimeoutError" in out["error"]


def test_is_alive_true(monkeypatch):
    fake = _make_fake_client(result=_FakeResult(text="ok"))
    monkeypatch.setattr(seek_client, "Client", fake)
    assert seek_client.is_alive() is True


def test_is_alive_false_when_down(monkeypatch):
    fake = _make_fake_client(raise_on_enter=ConnectionRefusedError("down"))
    monkeypatch.setattr(seek_client, "Client", fake)
    assert seek_client.is_alive() is False


def test_endpoint_env_override(monkeypatch):
    monkeypatch.setenv("KSS_SEEK_MCP_URL", "http://example:9999/mcp")
    assert seek_client._endpoint() == "http://example:9999/mcp"
    monkeypatch.delenv("KSS_SEEK_MCP_URL", raising=False)
    assert seek_client._endpoint() == seek_client.DEFAULT_ENDPOINT
