from __future__ import annotations

import importlib
import asyncio
import sys
import types
from pathlib import Path

import pytest


def test_mcp_registers_research_read_tools(monkeypatch):
    """Import smoke for research MCP tools without requiring fastmcp in this venv."""

    class FakeFastMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools: list[str] = []

        def tool(self, fn):
            self.tools.append(fn.__name__)
            return fn

        def run(self):
            raise AssertionError("test should not run MCP server")

    mod = types.ModuleType("fastmcp")
    mod.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "fastmcp", mod)
    monkeypatch.setenv("KSS_MCP_LIVE", "0")

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    sys.modules.pop("kss_mcp", None)
    kss_mcp = importlib.import_module("kss_mcp")

    assert {"research_search", "research_fetch", "research_bundle"} <= set(kss_mcp.mcp.tools)


def test_real_fastmcp_registers_research_read_tools(monkeypatch):
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("KSS_MCP_LIVE", "0")

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    sys.modules.pop("kss_mcp", None)
    kss_mcp = importlib.import_module("kss_mcp")

    tools = asyncio.run(kss_mcp.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"research_search", "research_fetch", "research_bundle"} <= names


def test_run_kss_mcp_wrapper_checks_uv_default_venv():
    root = Path(__file__).resolve().parents[2]
    wrapper = (root / "scripts" / "run_kss_mcp.sh").read_text(encoding="utf-8")
    assert '$PROJECT_ROOT/.venv/bin/python' in wrapper
