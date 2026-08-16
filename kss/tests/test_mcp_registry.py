"""外部 MCP 注册表:清单合并/自环排除/参数收敛/结果序列化."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from kss.agent import mcp_registry  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_server_specs_merges_and_excludes_self(tmp_path):
    project = tmp_path / "repo"
    state = tmp_path / "state"
    _write(project / ".mcp.json", {"mcpServers": {
        "kss-mcp": {"type": "stdio", "command": "bash", "args": ["x.sh"]},
        "exa": {"type": "stdio", "command": "npx", "args": ["exa-mcp"]},
        "remote": {"type": "http", "url": "https://x.example"},
    }})
    _write(state / "storage" / "agent" / "mcp_servers.json", {"mcpServers": {
        "exa": {"type": "stdio", "command": "npx", "args": ["exa-mcp", "--pro"],
                "env": {"EXA_API_KEY": "ref"}},
        "tushare": {"type": "stdio", "command": "uvx", "args": ["tushare-mcp"]},
    }})
    specs = mcp_registry.load_server_specs(state, project)
    names = [spec.name for spec in specs]
    # kss-mcp 自环排除;http 非 stdio 跳过;STATE_ROOT 覆盖仓库同名条目
    assert names == ["exa", "tushare"]
    exa = specs[0]
    assert exa.args == ("exa-mcp", "--pro")
    assert exa.env == {"EXA_API_KEY": "ref"}


def test_coerce_arguments_follows_schema_types():
    params = [
        {"key": "query", "type": "string", "required": True},
        {"key": "limit", "type": "integer", "required": False},
        {"key": "ratio", "type": "number", "required": False},
        {"key": "live", "type": "boolean", "required": False},
        {"key": "tags", "type": "array", "required": False},
    ]
    coerced = mcp_registry.coerce_arguments(
        {"query": "北证50", "limit": "5", "ratio": "0.8",
         "live": "true", "tags": "[\"a\",\"b\"]"},
        params,
    )
    assert coerced == {
        "query": "北证50", "limit": 5, "ratio": 0.8,
        "live": True, "tags": ["a", "b"],
    }
    # 收敛失败时保留原字符串,不抛错
    loose = mcp_registry.coerce_arguments({"limit": "abc"}, params)
    assert loose == {"limit": "abc"}


def test_serialize_call_result_prefers_data_then_text():
    class Block:
        def __init__(self, text):
            self.text = text

    class Result:
        def __init__(self, data=None, content=(), structured=None, is_error=False):
            self.data = data
            self.content = list(content)
            self.structured_content = structured
            self.is_error = is_error

    assert mcp_registry.serialize_call_result(Result(data={"a": 1})) == {"a": 1}
    assert mcp_registry.serialize_call_result(
        Result(content=[Block('{"b": 2}')])
    ) == {"b": 2}
    assert mcp_registry.serialize_call_result(
        Result(content=[Block("plain"), Block("text")])
    ) == ["plain", "text"]
    assert mcp_registry.serialize_call_result(Result())["note"]


def test_call_mcp_tool_unknown_server(tmp_path):
    result = mcp_registry.call_mcp_tool(
        tmp_path, tmp_path, "nope", "tool", {},
    )
    assert result["error"] == "unknown_mcp_server"
