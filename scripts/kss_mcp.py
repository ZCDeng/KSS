#!/usr/bin/env python3
"""kss-mcp：把 U2 插件包投影成只读 stdio MCP server（KTD5）。

权威是 ``kss.agent.harness_pack`` 的 ``mcpVisible`` 面，不是第二份手写工具表。
不导出 Harness 自带 bash/文件系统/终端，也不登记 live 写（含已删除的 ``_LIVE`` 分支）。
新只读插件在下次 MCP 进程启动（重连）后可见，不热插入当前连接（KTD10）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any, Callable

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kss_app_bridge as bridge  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from kss.agent.harness_pack import (  # noqa: E402
    R12_WRITE_ALIASES,
    R12_WRITE_COMMANDS,
    FrozenPackCatalog,
    freeze_pack_catalog,
    pack_catalog,
)

# Harness 宿主工具箱不得出现在 MCP 面（R4）。
_HOST_TOOLBOX_NAMES = frozenset({
    "bash",
    "fs",
    "filesystem",
    "terminal",
    "read_file",
    "write_file",
    "edit",
})

# KTD10：连接（本进程 import）时冻结，运行中包变更不热插入。
_FROZEN: FrozenPackCatalog = freeze_pack_catalog()

mcp = FastMCP("kss")


def _call(command: str, args: list[str] | None = None):
    """MCP 读路径：经 ``_make_read_only_call``，碰 WRITE_COMMANDS 即失败。"""
    read_call = bridge._make_read_only_call(bridge.dispatch)
    payload = read_call(command, args or [])
    if isinstance(payload, list):
        if command == "recipe-list":
            return {"recipes": payload}
        return {"items": payload}
    return payload


def restrict_mcp_projection(
    catalog: list[dict[str, Any]] | None = None,
    frozen: FrozenPackCatalog | None = None,
) -> list[dict[str, Any]]:
    """只读 KSS 业务插件：排除宿主工具箱、live 写、R12，且须在冻结快照内。"""
    snap = frozen or _FROZEN
    frozen_names = set(snap.names)
    out: list[dict[str, Any]] = []
    for entry in catalog if catalog is not None else pack_catalog():
        name = str(entry.get("name") or "")
        command = str(entry.get("command") or "")
        if name not in frozen_names:
            continue
        if name.lower() in _HOST_TOOLBOX_NAMES or command.lower() in _HOST_TOOLBOX_NAMES:
            continue
        if entry.get("write") or not entry.get("mcpVisible"):
            continue
        if "mcp" not in list(entry.get("surfaces") or []):
            continue
        if command in R12_WRITE_COMMANDS or command in R12_WRITE_ALIASES:
            continue
        if name in R12_WRITE_COMMANDS or name in R12_WRITE_ALIASES:
            continue
        if command in bridge.WRITE_COMMANDS:
            continue
        out.append(dict(entry))
    return out


def _invoke(entry: dict[str, Any], kwargs: dict[str, Any]) -> Any:
    import kss_chat_loop as chat_loop  # 惰性，避免循环

    name = str(entry["name"])
    registry = chat_loop.ToolRegistry()
    if registry.has_tool(name):
        command, positional = registry.resolve(name, kwargs)
    else:
        command = str(entry.get("command") or "")
        positional: list[str] = []
        for key in list(entry.get("order") or []):
            val = kwargs.get(key)
            if val is not None and str(val) != "":
                positional.append(str(val))
            else:
                positional.append("")
        while positional and positional[-1] == "":
            positional.pop()
    return _call(command, positional)


def _make_tool_fn(entry: dict[str, Any]) -> Callable[..., Any]:
    params_schema = dict(entry.get("params") or {})
    order = list(entry.get("order") or [])
    keys = list(dict.fromkeys([*order, *params_schema]))
    parameters = [
        inspect.Parameter(
            key,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default="",
            annotation=str,
        )
        for key in keys
    ]
    sig = inspect.Signature(parameters, return_annotation=dict)

    def _fn(*args: Any, **kwargs: Any) -> Any:
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return _invoke(entry, dict(bound.arguments))

    _fn.__name__ = str(entry["name"])
    _fn.__doc__ = str(entry.get("desc") or "")
    _fn.__signature__ = sig  # type: ignore[attr-defined]
    _fn.__annotations__ = {key: str for key in keys} | {"return": dict}
    return _fn


def _bind_projected_tools() -> None:
    for entry in restrict_mcp_projection():
        fn = _make_tool_fn(entry)
        mcp.tool(fn, name=str(entry["name"]), description=str(entry.get("desc") or ""))
        globals()[str(entry["name"])] = fn


_bind_projected_tools()


if __name__ == "__main__":
    mcp.run()
