"""U7 测试：kss-mcp 暴露 Longbridge 只读实时工具（R8 / KTD4）.

- 工具注册进 mcp（FakeFastMCP + 真 fastmcp 双路）。
- 无 KSS_MCP_LIVE 下工具仍可用（读工具 ungated）。
- _call 命中 U4 bridge 命令（mock dispatch）。
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

_LB_TOOLS = {"get_longbridge_quote", "get_intraday_snapshot"}


def _load_kss_mcp(monkeypatch, *, live="0"):
    class FakeFastMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools: list[str] = []

        def tool(self, fn=None, **kwargs):
            def deco(f):
                self.tools.append(str(kwargs.get("name") or f.__name__))
                return f

            if callable(fn):
                return deco(fn)
            return deco

        def run(self):
            raise AssertionError("test should not run MCP server")

    mod = types.ModuleType("fastmcp")
    mod.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "fastmcp", mod)
    monkeypatch.setenv("KSS_MCP_LIVE", live)

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    sys.modules.pop("kss_mcp", None)
    return importlib.import_module("kss_mcp")


def test_mcp_registers_longbridge_read_tools(monkeypatch):
    """两只读实时工具注册进 mcp（ungated，KSS_MCP_LIVE=0 下仍在）。"""
    kss_mcp = _load_kss_mcp(monkeypatch, live="0")
    assert _LB_TOOLS <= set(kss_mcp.mcp.tools)


def test_longbridge_tools_funnel_through_bridge_command(monkeypatch):
    """get_longbridge_quote / get_intraday_snapshot 经 _call 命中 U4 bridge 命令。"""
    kss_mcp = _load_kss_mcp(monkeypatch, live="0")
    seen = []
    monkeypatch.setattr(
        kss_mcp.bridge, "dispatch",
        lambda cmd, args: seen.append((cmd, args)) or {"symbol": args[0], "last_done": 1.0},
    )
    out = kss_mcp.get_longbridge_quote("688008.SH")
    assert out["last_done"] == 1.0
    assert ("longbridge-quote", ["688008.SH"]) in seen

    kss_mcp.get_intraday_snapshot("688008.SH")
    assert ("intraday-snapshot", ["688008.SH"]) in seen


def test_real_fastmcp_registers_longbridge_read_tools(monkeypatch):
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("KSS_MCP_LIVE", "0")

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    sys.modules.pop("kss_mcp", None)
    kss_mcp = importlib.import_module("kss_mcp")

    tools = asyncio.run(kss_mcp.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert _LB_TOOLS <= names
