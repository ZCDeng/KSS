"""研究节点 R7 写白名单与 R11 写能力判定。

默认成员与 ``harness/kss-plugins/src/research-allowlist.json`` 对齐：仅工作区内
bash / 改文件。KSS live WRITE_COMMANDS 不在默认名单；若日后列入，仍须经
sidecar ``execute_harness_tool`` 在 pre-execute grant 之后 dispatch，不得当成
cwd 本地文件。不复用 AUTO_TASKS 或 MCP confirm。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 与 research-allowlist.json 同步的保守默认（JSON 不能写注释）。
DEFAULT_RESEARCH_WRITE_TOOLS: tuple[str, ...] = (
    "bash",
    "write",
    "edit",
    "str_replace_editor",
)

_PLUGIN_ALLOWLIST = (
    Path(__file__).resolve().parents[2]
    / "harness"
    / "kss-plugins"
    / "src"
    / "research-allowlist.json"
)


def load_research_allowlist_stub() -> dict[str, Any]:
    """读取插件包内的研究白名单 stub。"""
    raw = json.loads(_PLUGIN_ALLOWLIST.read_text(encoding="utf-8"))
    tools = [str(item) for item in (raw.get("tools") or [])]
    return {"tools": tools, "cwd": str(raw.get("cwd") or "")}


def bound_write_allowlist(task: dict[str, Any]) -> list[str]:
    """节点绑定的 R7 写白名单。空名单 = 只读节点。"""
    payload = task.get("payload") or {}
    listed = payload.get("write_allowlist")
    if listed is None:
        return []
    return [str(name) for name in listed if str(name).strip()]


def is_write_capable_research_node(task: dict[str, Any]) -> bool:
    """R11：写能力当且仅当绑定 preset 的 R7 白名单非空。"""
    payload = task.get("payload") or {}
    if payload.get("protected"):
        return False
    return bool(bound_write_allowlist(task))


def allowlist_fingerprint(tools: list[str], cwd: str) -> str:
    payload = json.dumps(
        {"tools": sorted(tools), "cwd": str(cwd)},
        ensure_ascii=False,
        sort_keys=True,
    )
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
