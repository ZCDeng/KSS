"""外部 MCP server 注册表:Seesaw slash 直连调用.

服务器清单(标准 ``mcpServers`` 格式,仅 stdio):
- ``STATE_ROOT/storage/agent/mcp_servers.json``(KSS 自有,用户可编辑)
- ``PROJECT_ROOT/.mcp.json``(排除 ``kss-mcp`` 自身,避免自环)

安全边界:
- 只由 slash(用户显式动作)触发,不注册给模型侧 agent;
- 输出一律按不可信外部输入处理,展示与落库均标 ``external_mcp_untrusted``;
- 每次调用带超时,子进程随调用生命周期结束(不常驻)。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# kss-mcp 是 KSS 自己的 MCP 投影,slash 已直连同源工具,排除以免自环。
_SELF_SERVER_NAMES = {"kss-mcp"}
_CATALOG_TTL_SECONDS = 300.0
_LIST_TIMEOUT_SECONDS = 20.0
_CALL_TIMEOUT_SECONDS = 45.0

_catalog_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@dataclass(frozen=True)
class MCPServerSpec:
    """一个 stdio MCP server 的启动描述(非密钥)."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None


def _load_mcp_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    servers = loaded.get("mcpServers") if isinstance(loaded, Mapping) else None
    return dict(servers) if isinstance(servers, Mapping) else {}


def load_server_specs(
    state_root: str | Path,
    project_root: str | Path,
) -> list[MCPServerSpec]:
    """合并两份清单;STATE_ROOT 覆盖同名仓库条目;排除 kss-mcp 自身."""
    merged: dict[str, Any] = {}
    merged.update(_load_mcp_document(Path(project_root) / ".mcp.json"))
    merged.update(
        _load_mcp_document(
            Path(state_root) / "storage" / "agent" / "mcp_servers.json"
        )
    )
    specs: list[MCPServerSpec] = []
    for name, raw in sorted(merged.items()):
        if not isinstance(raw, Mapping):
            continue
        server_name = str(name).strip()
        if not server_name or server_name in _SELF_SERVER_NAMES:
            continue
        transport = str(raw.get("type") or "stdio")
        if transport != "stdio":
            continue
        command = str(raw.get("command") or "").strip()
        if not command:
            continue
        args = tuple(str(item) for item in (raw.get("args") or []))
        env_raw = raw.get("env")
        env = (
            {str(key): str(value) for key, value in env_raw.items()}
            if isinstance(env_raw, Mapping)
            else {}
        )
        specs.append(MCPServerSpec(
            name=server_name,
            command=command,
            args=args,
            env=env,
            cwd=str(project_root),
        ))
    return specs


def _run_sync(coro: Any, timeout: float) -> Any:
    """在独立线程的全新事件循环里跑协程——sidecar 调用点本身在事件循环线程上."""

    def runner() -> Any:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(runner).result(timeout=timeout + 10)


def _client_for(spec: MCPServerSpec) -> Any:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(
        spec.command,
        list(spec.args),
        env=dict(spec.env) or None,
        cwd=spec.cwd,
    )
    return Client(transport)


def _schema_params(schema: Any) -> list[dict[str, Any]]:
    properties = (
        schema.get("properties") if isinstance(schema, Mapping) else None
    ) or {}
    required = set(
        str(item)
        for item in ((schema.get("required") if isinstance(schema, Mapping) else None) or [])
    )
    params: list[dict[str, Any]] = []
    for key, raw in properties.items():
        raw_map = raw if isinstance(raw, Mapping) else {}
        params.append({
            "key": str(key),
            "description": str(raw_map.get("description") or ""),
            "type": str(raw_map.get("type") or "string"),
            "required": str(key) in required,
        })
    params.sort(key=lambda item: (not item["required"], item["key"]))
    return params


def coerce_arguments(
    args: Mapping[str, str],
    params: list[dict[str, Any]],
) -> dict[str, Any]:
    """slash 输入的字符串参数按 inputSchema 类型收敛(int/number/bool/json)."""
    types = {str(item.get("key")): str(item.get("type") or "string") for item in params}
    coerced: dict[str, Any] = {}
    for key, raw in args.items():
        value: Any = raw
        kind = types.get(key, "string")
        text = str(raw).strip()
        if kind == "integer":
            try:
                value = int(text)
            except ValueError:
                value = raw
        elif kind == "number":
            try:
                value = float(text)
            except ValueError:
                value = raw
        elif kind == "boolean":
            if text.lower() in {"true", "1", "yes"}:
                value = True
            elif text.lower() in {"false", "0", "no"}:
                value = False
        elif kind in {"array", "object"}:
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                value = raw
        coerced[key] = value
    return coerced


async def _list_tools_async(spec: MCPServerSpec) -> list[dict[str, Any]]:
    client = _client_for(spec)
    async with client:
        tools = await client.list_tools()
    catalog: list[dict[str, Any]] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        if not name:
            continue
        catalog.append({
            "server": spec.name,
            "name": name,
            "description": str(getattr(tool, "description", "") or ""),
            "params": _schema_params(getattr(tool, "inputSchema", None)),
        })
    return catalog


def list_mcp_tools(
    state_root: str | Path,
    project_root: str | Path,
    *,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """列出全部外部 MCP 工具;单个 server 失败不拖垮整表.

    Returns:
        (tools, errors) —— errors 为 "server: 原因" 文案,供 UI 呈现。
    """
    cache_key = f"{state_root}|{project_root}"
    now = time.monotonic()
    cached = _catalog_cache.get(cache_key)
    if not refresh and cached is not None and now - cached[0] < _CATALOG_TTL_SECONDS:
        return cached[1], []
    tools: list[dict[str, Any]] = []
    errors: list[str] = []
    for spec in load_server_specs(state_root, project_root):
        try:
            tools.extend(_run_sync(_list_tools_async(spec), _LIST_TIMEOUT_SECONDS))
        except Exception as exc:  # noqa: BLE001 - 单 server 失败降级为提示
            errors.append(f"{spec.name}: {type(exc).__name__}: {exc}")
    _catalog_cache[cache_key] = (now, tools)
    return tools, errors


def serialize_call_result(result: Any) -> Any:
    """把 fastmcp CallToolResult 收敛为 JSON 可编码对象."""
    data = getattr(result, "data", None)
    if data is not None:
        try:
            json.dumps(data)
            return data
        except (TypeError, ValueError):
            pass
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, (dict, list)):
        return structured
    contents = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in contents:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except (json.JSONDecodeError, ValueError):
            return texts[0]
    if texts:
        return texts
    return {"note": "MCP 工具无文本输出"}


async def _call_tool_async(
    spec: MCPServerSpec,
    tool: str,
    arguments: dict[str, Any],
) -> Any:
    client = _client_for(spec)
    async with client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    payload = serialize_call_result(result)
    if getattr(result, "is_error", False):
        return {"error": "mcp_tool_error", "detail": payload}
    return payload


def call_mcp_tool(
    state_root: str | Path,
    project_root: str | Path,
    server: str,
    tool: str,
    args: Mapping[str, str],
) -> dict[str, Any]:
    """直连调用一个外部 MCP 工具(用户显式动作;输出不可信)."""
    spec = next(
        (item for item in load_server_specs(state_root, project_root)
         if item.name == server),
        None,
    )
    if spec is None:
        return {"error": "unknown_mcp_server", "server": server}
    params: list[dict[str, Any]] = []
    tools, _errors = list_mcp_tools(state_root, project_root)
    for entry in tools:
        if entry["server"] == server and entry["name"] == tool:
            params = list(entry.get("params") or [])
            break
    arguments = coerce_arguments(dict(args), params)
    try:
        payload = _run_sync(
            _call_tool_async(spec, tool, arguments), _CALL_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - 传输/超时收敛为结构化错误
        return {"error": "mcp_call_failed", "detail": f"{type(exc).__name__}: {exc}"}
    if isinstance(payload, dict) and payload.get("error"):
        return payload
    return {"ok": True, "result": payload}


__all__ = [
    "MCPServerSpec",
    "call_mcp_tool",
    "coerce_arguments",
    "list_mcp_tools",
    "load_server_specs",
    "serialize_call_result",
]
