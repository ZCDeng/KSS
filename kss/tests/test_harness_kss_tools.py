"""U2：KSS Cordis 插件包以 TOOL_SPECS 为唯一登记面。

覆盖 AE7 / R12 / R3 / R6 / KTD2 / KTD10。
跑：uv run pytest kss/tests/test_harness_kss_tools.py -q
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as chat_loop  # noqa: E402
import kss_sidecar as sidecar  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_harness_crash_domains():
    sidecar.reset_harness_crash_domains()
    yield
    sidecar.reset_harness_crash_domains()


from kss.agent.harness_pack import (  # noqa: E402
    R12_WRITE_COMMANDS,
    R12_WRITE_ALIASES,
    append_pack_entry_for_test,
    dump_catalog_payload,
    freeze_pack_catalog,
    live_write_entries,
    mcp_visible_entries,
    pack_catalog,
    reset_pack_test_mutation,
)

PLUGINS_DIR = _ROOT / "harness" / "kss-plugins"
CATALOG_JSON = PLUGINS_DIR / "src" / "catalog.json"

R12_SCHEMA_NAMES = (
    "investability-label",
    "investability-answer",
    "investability-node-coverage",
    "node-coverage",
)


def _expected_live_writes() -> list[dict]:
    """TOOL_SPECS 写子集：command ∈ WRITE_COMMANDS 且不是 R12。"""
    out = []
    for spec in chat_loop.TOOL_SPECS:
        command = str(spec.get("command") or "")
        if command not in bridge.WRITE_COMMANDS:
            continue
        if command in R12_WRITE_COMMANDS or command in R12_WRITE_ALIASES:
            continue
        out.append(spec)
    return out


def _schema_blob() -> str:
    catalog = pack_catalog()
    node = CATALOG_JSON.read_text(encoding="utf-8") if CATALOG_JSON.is_file() else ""
    return json.dumps(catalog, ensure_ascii=False) + "\n" + node


# ---------------------------------------------------------------------------
# AE7 / R12
# ---------------------------------------------------------------------------


def test_ae7_r12_commands_absent_from_pack_schema() -> None:
    blob = _schema_blob()
    catalog = pack_catalog()
    names = {str(e["name"]) for e in catalog}
    commands = {str(e["command"]) for e in catalog}
    for forbidden in R12_SCHEMA_NAMES:
        assert forbidden not in blob, f"R12 不得出现在 pack schema: {forbidden}"
        assert forbidden not in names
        assert forbidden not in commands
    for spec in chat_loop.TOOL_SPECS:
        assert str(spec.get("command") or "") not in R12_WRITE_COMMANDS


def test_r12_stays_in_write_classifier_but_unregistered() -> None:
    for cmd in R12_WRITE_COMMANDS:
        assert cmd in bridge.WRITE_COMMANDS
        assert cmd not in {e["command"] for e in pack_catalog()}


# ---------------------------------------------------------------------------
# R3 / F3 surfaces
# ---------------------------------------------------------------------------


def test_live_write_tools_from_tool_specs_write_subset() -> None:
    expected = {str(s["name"]) for s in _expected_live_writes()}
    got = {str(e["name"]) for e in live_write_entries()}
    assert expected, "TOOL_SPECS 写子集不应为空"
    assert got == expected
    for spec in _expected_live_writes():
        assert spec["command"] in bridge.WRITE_COMMANDS


def test_live_writes_tagged_desktop_research_not_mcp() -> None:
    for entry in live_write_entries():
        surfaces = set(entry["surfaces"])
        assert surfaces == {"desktop", "research"}, entry["name"]
        assert entry["mcpVisible"] is False
        assert "mcp" not in surfaces
    for entry in mcp_visible_entries():
        assert entry["mcpVisible"] is True
        assert "mcp" in entry["surfaces"]
        assert entry["command"] not in bridge.WRITE_COMMANDS


def test_unregistered_write_commands_stay_off_pack() -> None:
    registered_cmds = {e["command"] for e in pack_catalog()}
    tool_spec_cmds = {str(s.get("command") or "") for s in chat_loop.TOOL_SPECS}
    for cmd in bridge.WRITE_COMMANDS:
        if cmd not in tool_spec_cmds:
            assert cmd not in registered_cmds


# ---------------------------------------------------------------------------
# U6 MCP 只读投影（AE4 / AE7 / R12 / KTD5 / KTD10）
# ---------------------------------------------------------------------------

_HOST_TOOLBOX = ("bash", "fs", "filesystem", "terminal")
_NEW_READONLY = "get_brand_new_readonly"


def _load_kss_mcp(monkeypatch: pytest.MonkeyPatch, *, live: str = "0"):
    class FakeFastMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self.tools: list[str] = []

        def tool(self, fn=None, **kwargs):
            def deco(f):
                self.tools.append(str(kwargs.get("name") or f.__name__))
                return f

            if callable(fn):
                return deco(fn)
            return deco

        def run(self) -> None:
            raise AssertionError("test should not run MCP server")

    mod = types.ModuleType("fastmcp")
    mod.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "fastmcp", mod)
    monkeypatch.setenv("KSS_MCP_LIVE", live)
    sys.modules.pop("kss_mcp", None)
    return importlib.import_module("kss_mcp")


def test_pack_says_mcp_would_not_see_live_writes() -> None:
    mcp_names = {e["name"] for e in mcp_visible_entries()}
    for entry in live_write_entries():
        assert entry["name"] not in mcp_names


def test_live_write_names_in_pack_absent_from_restrict(monkeypatch: pytest.MonkeyPatch) -> None:
    """pack 有 live 写名；restrict 投影没有它们。缺席断言，不是 confirm=True。"""
    live_names = {e["name"] for e in live_write_entries()}
    assert live_names
    kss_mcp = _load_kss_mcp(monkeypatch, live="1")
    projected = {e["name"] for e in kss_mcp.restrict_mcp_projection()}
    assert live_names.isdisjoint(projected)
    mcp_tools = set(kss_mcp.mcp.tools)
    assert live_names.isdisjoint(mcp_tools)
    assert "run_task" not in mcp_tools
    assert "cron_action" not in mcp_tools
    src = (_ROOT / "scripts" / "kss_mcp.py").read_text(encoding="utf-8")
    assert "confirm: bool = False" not in src
    assert "if _LIVE" not in src


def test_ae7_r12_names_absent_from_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    kss_mcp = _load_kss_mcp(monkeypatch, live="1")
    mcp_tools = set(kss_mcp.mcp.tools)
    projected = {e["name"] for e in kss_mcp.restrict_mcp_projection()}
    for forbidden in R12_SCHEMA_NAMES:
        assert forbidden not in mcp_tools
        assert forbidden not in projected


def test_ae4_new_readonly_plugin_visible_after_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_pack_test_mutation()
    try:
        first = _load_kss_mcp(monkeypatch)
        assert _NEW_READONLY not in set(first.mcp.tools)
        append_pack_entry_for_test(
            {
                "name": _NEW_READONLY,
                "command": "orientation",
                "desc": "U6 AE4 probe",
                "params": {},
                "order": [],
                "write": False,
                "surfaces": ["desktop", "research", "mcp"],
                "mcpVisible": True,
            }
        )
        assert _NEW_READONLY not in set(first.mcp.tools)
        for name in _HOST_TOOLBOX:
            assert name not in set(first.mcp.tools)
        for entry in live_write_entries():
            assert entry["name"] not in set(first.mcp.tools)

        second = _load_kss_mcp(monkeypatch)
        tools = set(second.mcp.tools)
        assert _NEW_READONLY in tools
        for name in _HOST_TOOLBOX:
            assert name not in tools
        for entry in live_write_entries():
            assert entry["name"] not in tools
    finally:
        reset_pack_test_mutation()


def test_mcp_read_path_fails_closed_on_write_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    kss_mcp = _load_kss_mcp(monkeypatch)
    monkeypatch.setattr(
        kss_mcp.bridge,
        "dispatch",
        lambda c, a: dispatched.append(c) or {"ok": True},
    )
    with pytest.raises(PermissionError):
        kss_mcp._call("run", ["update-cs-data"])
    with pytest.raises(PermissionError):
        kss_mcp._call("investability-label", ["688008.SH", "compute.05"])
    assert dispatched == []


# ---------------------------------------------------------------------------
# 只读 execute / 读插件不能 dispatch 写
# ---------------------------------------------------------------------------


def test_read_tool_execute_matches_bridge_read_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, list[str]]] = []

    def fake_dispatch(command: str, args: list[str]) -> dict:
        seen.append((command, list(args)))
        return {"ok": True, "command": command, "args": list(args)}

    monkeypatch.setattr(bridge, "dispatch", fake_dispatch)
    result = sidecar.execute_harness_tool(
        name="get_orientation",
        args={},
        call_id="read-1",
    )
    assert result["ok"] is True
    assert seen == [("orientation", [])]
    read_call = bridge._make_read_only_call(fake_dispatch)
    via_bridge = read_call("orientation", [])
    assert via_bridge == result["result"]


def test_read_path_raises_on_write_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(
        bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ok": True}
    )
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="read-as-write",
        force_read=True,
    )
    assert dispatched == []
    assert out.get("error") == "read_only_violation"
    read_call = bridge._make_read_only_call(lambda c, a: dispatched.append(c))
    with pytest.raises(PermissionError):
        read_call("run", ["update-cs-data"])
    assert dispatched == []


def test_read_plugin_cannot_dispatch_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(
        bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c}
    )
    out = sidecar.execute_harness_tool(
        name="get_snapshot",
        args={},
        call_id="sneak-write",
        override_command="run",
        override_positional=["update-cs-data"],
    )
    assert dispatched == []
    assert out.get("ok") is not True
    assert out.get("error") in {"write_blocked", "read_only_violation", "not_allowed"}


# ---------------------------------------------------------------------------
# KTD2 grant-gated write RPC
# ---------------------------------------------------------------------------


def test_live_write_rpc_without_harness_allow_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(
        bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c}
    )
    sidecar.clear_harness_grants()
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="ungranted-call",
    )
    assert dispatched == []
    assert out.get("error") in {"not_allowed", "grant_required"}
    assert "ok" not in out or out.get("ok") is not True


def test_live_write_rpc_with_grant_dispatches_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(
        bridge,
        "dispatch",
        lambda c, a: dispatched.append((c, list(a))) or {"ran": c, "args": a},
    )
    sidecar.clear_harness_grants()
    sidecar.grant_harness_write("granted-call", command="run")
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="granted-call",
    )
    assert out.get("ok") is True
    assert dispatched == [("run", ["update-cs-data"])]
    replay = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="granted-call",
    )
    assert dispatched == [("run", ["update-cs-data"])]
    assert replay.get("error") in {"not_allowed", "grant_required"}


def test_live_write_kill_switch_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", False)
    monkeypatch.setattr(
        bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c}
    )
    sidecar.clear_harness_grants()
    sidecar.grant_harness_write("live-off", command="run")
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="live-off",
    )
    assert dispatched == []
    assert out.get("error") == "not_live"


def test_harness_grant_is_not_chat_pending_map() -> None:
    assert sidecar.grant_harness_write is not getattr(sidecar, "_confirm_reader", None)
    src = Path(sidecar.__file__).read_text(encoding="utf-8")
    assert "execute_harness_tool" in src
    assert "_HARNESS_GRANTS" in src or "harness_grants" in src


# ---------------------------------------------------------------------------
# KTD10 catalog freeze
# ---------------------------------------------------------------------------


def test_in_flight_catalog_snapshot_unchanged_after_pack_mutation() -> None:
    frozen = freeze_pack_catalog()
    names_before = list(frozen.names)
    append_pack_entry_for_test(
        {
            "name": "get_mutated_after_freeze",
            "command": "mutated-after-freeze",
            "surfaces": ["desktop", "research", "mcp"],
            "mcpVisible": True,
        }
    )
    try:
        assert list(frozen.names) == names_before
        assert "get_mutated_after_freeze" not in frozen.names
        live_names = {e["name"] for e in pack_catalog()}
        assert "get_mutated_after_freeze" in live_names
    finally:
        reset_pack_test_mutation()


# ---------------------------------------------------------------------------
# Node pack 与 Python 目录一致
# ---------------------------------------------------------------------------


def test_node_catalog_json_matches_python_pack() -> None:
    assert CATALOG_JSON.is_file()
    dumped = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    assert dumped == dump_catalog_payload()


def test_node_plugin_exports_define_tools_meta() -> None:
    index_js = PLUGINS_DIR / "src" / "index.js"
    src = index_js.read_text(encoding="utf-8")
    assert "defineTool" in src
    assert "inject" in src
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import { packToolMeta } from "
                f"{json.dumps(str(PLUGINS_DIR / 'src' / 'catalog.js'))}; "
                "console.log(JSON.stringify(packToolMeta()));"
            ),
        ],
        cwd=str(PLUGINS_DIR),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "NODE_PATH": str(
            _ROOT / "harness" / "kss-profile" / "node_modules"
        )},
    )
    assert result.returncode == 0, result.stderr
    meta = json.loads(result.stdout)
    names = {t["name"] for t in meta["tools"]}
    assert names == {e["name"] for e in pack_catalog()}
    for tool in meta["tools"]:
        if tool["write"]:
            assert tool["surfaces"] == ["desktop", "research"]
            assert tool["mcpVisible"] is False
