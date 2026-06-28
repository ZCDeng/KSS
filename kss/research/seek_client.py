"""seek HTTP MCP 客户端 —— 脚本上下文访问 reach_* / bocha / read_url。

seek 是本机 HTTP MCP server(默认 ``http://127.0.0.1:8643/mcp``,Docker 容器)。
本模块复用已有依赖 ``fastmcp`` 的 streamable-HTTP ``Client``(async),包一层
``asyncio.run()`` 同步桥,供同步 recipe(``fn(call, scene) -> dict``)直接调用,
不手搓 307 / JSON-RPC / SSE 握手。

设计要点(对齐 plan U1 / R2):
- ``reach(tool, **args)``:调一个 seek 工具,**永不因传输/超时抛异常**,容器不在
  或调用失败时返回 ``ok=False`` 的降级结构(风格参照 ``adapter._unavailable``)。
- ``is_alive(timeout)``:采集前探活,容器不在返回 ``False`` 而非抛异常,让该场
  降级/告警而非空跑。
- 端点 / 超时走环境变量(``KSS_SEEK_MCP_URL`` / ``KSS_SEEK_TIMEOUT`` /
  ``KSS_SEEK_INIT_TIMEOUT``),不硬编码。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastmcp import Client

DEFAULT_ENDPOINT = "http://127.0.0.1:8643/mcp"
DEFAULT_TIMEOUT = 20.0
DEFAULT_INIT_TIMEOUT = 8.0


def _endpoint() -> str:
    return os.environ.get("KSS_SEEK_MCP_URL", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _extract(result: Any) -> dict[str, Any]:
    """把 fastmcp ``CallToolResult`` 归一为 ``{text, structured}``。

    seek 工具(weibo_hot 等)实测把人读文本放在 ``content[0].text``,并把同一文本
    以 ``{"result": <text>}`` 暴露在 ``structured_content``。两路都取,优先 content
    拼接,缺时回落 structured 里的 ``result`` 字符串。
    """
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "data", None)

    blocks = getattr(result, "content", None) or []
    parts = [getattr(b, "text", "") for b in blocks]
    text = "\n".join(p for p in parts if p)

    if not text and isinstance(structured, dict):
        candidate = structured.get("result")
        if isinstance(candidate, str):
            text = candidate

    return {"text": text, "structured": structured}


def _degraded(tool: str, error: str) -> dict[str, Any]:
    return {"ok": False, "text": "", "structured": None, "error": error, "tool": tool}


async def _areach(tool: str, args: dict[str, Any], timeout: float, init_timeout: float) -> dict[str, Any]:
    async with Client(_endpoint(), init_timeout=init_timeout) as client:
        result = await client.call_tool(tool, args or {}, timeout=timeout)
        out = _extract(result)
        out.update({"ok": True, "error": None, "tool": tool})
        return out


def reach(
    tool: str,
    *,
    timeout: float | None = None,
    init_timeout: float | None = None,
    **args: Any,
) -> dict[str, Any]:
    """同步调一个 seek 工具。

    返回 ``{"ok": bool, "text": str, "structured": Any, "error": str | None,
    "tool": str}``。传输错误 / 超时 / 容器不在一律返回 ``ok=False`` 降级结构,不抛。
    """
    to = DEFAULT_TIMEOUT if timeout is None else timeout
    ito = DEFAULT_INIT_TIMEOUT if init_timeout is None else init_timeout
    if timeout is None:
        to = _float_env("KSS_SEEK_TIMEOUT", DEFAULT_TIMEOUT)
    if init_timeout is None:
        ito = _float_env("KSS_SEEK_INIT_TIMEOUT", DEFAULT_INIT_TIMEOUT)
    try:
        return asyncio.run(_areach(tool, args, to, ito))
    except Exception as exc:  # noqa: BLE001 - 传输层任何失败都降级,不外抛
        return _degraded(tool, f"{type(exc).__name__}: {exc}")


async def _ais_alive(init_timeout: float) -> bool:
    async with Client(_endpoint(), init_timeout=init_timeout) as client:
        await client.ping()
        return True


def is_alive(timeout: float = 5.0) -> bool:
    """采集前探活。seek 容器不在 / 连接拒绝 / 超时返回 ``False``,永不抛。"""
    try:
        return asyncio.run(_ais_alive(timeout))
    except Exception:  # noqa: BLE001 - 探活失败即视为不可用
        return False
