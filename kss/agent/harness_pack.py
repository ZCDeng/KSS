"""Harness 插件包目录：以 chat TOOL_SPECS 为唯一 agent 可见登记面。

MCP 投影看 ``mcpVisible`` / ``surfaces``；live 写只进 desktop+research。
``kss_mcp._LIVE`` 不是本目录的权威（U6 才改 MCP 投影器）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import kss_app_bridge as bridge
import kss_chat_loop as chat_loop

# R12 / AE7：真实 WRITE_COMMAND 名永不进包。node-coverage 是历史别名。
R12_WRITE_COMMANDS = frozenset({
    "investability-label",
    "investability-answer",
    "investability-node-coverage",
})
R12_WRITE_ALIASES = frozenset({"node-coverage"})

_SURFACES_READ = ("desktop", "research", "mcp")
_SURFACES_WRITE = ("desktop", "research")

_TEST_EXTRA: list[dict[str, Any]] = []


def _entry_from_spec(spec: dict[str, Any]) -> dict[str, Any] | None:
    command = str(spec.get("command") or "")
    if command in R12_WRITE_COMMANDS or command in R12_WRITE_ALIASES:
        return None
    write = command in bridge.WRITE_COMMANDS
    surfaces = list(_SURFACES_WRITE if write else _SURFACES_READ)
    return {
        "name": str(spec["name"]),
        "command": command,
        "desc": str(spec.get("desc") or ""),
        "params": dict(spec.get("params") or {}),
        "order": list(spec.get("order") or []),
        "execution_mode": str(spec.get("execution_mode") or "sequential"),
        "write": write,
        "surfaces": surfaces,
        "mcpVisible": (not write),
    }


def pack_catalog() -> list[dict[str, Any]]:
    """当前包目录（含测试注入）。新会话应 ``freeze_pack_catalog``，勿热插入进行中回合。"""
    entries: list[dict[str, Any]] = []
    for spec in chat_loop.TOOL_SPECS:
        entry = _entry_from_spec(spec)
        if entry is not None:
            entries.append(entry)
    entries.extend(dict(item) for item in _TEST_EXTRA)
    return entries


def live_write_entries() -> list[dict[str, Any]]:
    return [e for e in pack_catalog() if e.get("write")]


def mcp_visible_entries() -> list[dict[str, Any]]:
    return [e for e in pack_catalog() if e.get("mcpVisible")]


@dataclass(frozen=True)
class FrozenPackCatalog:
    """一次 agents.create / MCP 连接时冻结的工具名表（KTD10）。"""

    names: tuple[str, ...]


def freeze_pack_catalog() -> FrozenPackCatalog:
    return FrozenPackCatalog(names=tuple(e["name"] for e in pack_catalog()))


def append_pack_entry_for_test(entry: dict[str, Any]) -> None:
    """仅测试：模拟包变更。不得改写已 freeze 的 snapshot。"""
    _TEST_EXTRA.append(dict(entry))


def reset_pack_test_mutation() -> None:
    _TEST_EXTRA.clear()


def dump_catalog_payload() -> list[dict[str, Any]]:
    """不含测试注入的权威 dump，供 Node catalog.json。"""
    extras = list(_TEST_EXTRA)
    _TEST_EXTRA.clear()
    try:
        return pack_catalog()
    finally:
        _TEST_EXTRA.extend(extras)
