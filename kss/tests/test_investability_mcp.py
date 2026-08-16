"""可投资地图 MCP 面测试(plan U8).

守两件事: 桌面端与 agent 拿到的是同一份数据(R24), 以及 agent 侧没有任何写入
路径(R25) —— 后者在实时模式下同样成立.
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import kss_app_bridge as b  # noqa: E402
import kss_chat_loop as chat  # noqa: E402

_MCP_SRC = (_SCRIPTS / "kss_mcp.py").read_text(encoding="utf-8")
_R12 = (
    "investability-label",
    "investability-answer",
    "investability-node-coverage",
)


@pytest.fixture()
def bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    return b


# --------------------------------------------------------------------------- #
# 字段对等(plan R24)
# --------------------------------------------------------------------------- #

#: R21 个股详情卡要渲染的字段. 工具返回值必须是它的超集 —— 单向包含,
#: 不要求相等: 工具多返回原始三态答案这类东西是合理的.
_DETAIL_CARD_FIELDS = {
    "colorKey",
    "colorLabel",
    "state",
    "stateLabel",
    "primaryNode",
    "secondaryNodes",
    "zone",
    "labelUpdatedAt",
}


def test_detail_card_fields_are_subset_of_tool_payload(bridge) -> None:
    """详情卡用到的字段集是工具返回字段集的子集(plan R24)."""
    bridge.dispatch("investability-label", ["688008.SH", "compute.05", "telecom.03"])
    payload = bridge.dispatch("investability-stocks", ["688008.SH"])["stocks"]["688008.SH"]
    assert _DETAIL_CARD_FIELDS <= set(payload)


def test_tool_may_return_more_than_the_card_needs(bridge) -> None:
    """工具允许比卡片多返回: 8 问原始三态供录入界面回显, 卡片不直接渲染它."""
    bridge.dispatch("investability-answer", ["688008.SH", "3", "yes"])
    payload = bridge.dispatch("investability-stocks", ["688008.SH"])["stocks"]["688008.SH"]
    assert payload["answers"]["3"] is True
    assert set(payload) - _DETAIL_CARD_FIELDS


def test_zone_string_identical_across_consumers(bridge) -> None:
    """区位串在工具返回与界面显示中逐字一致 —— 判定只在 Python 侧实现一次."""
    code = "688008.SH"
    for q, v in {1: "yes", 2: "yes", 3: "no", 4: "no", 5: "no"}.items():
        bridge.dispatch("investability-answer", [code, str(q), v])
    via_stocks = bridge.dispatch("investability-stocks", [code])["stocks"][code]["zone"]
    via_answer = bridge.dispatch("investability-answer", [code, "5", "no"])["zone"]
    assert via_stocks["display"] == via_answer["display"] == "未尽调 · 已定 5/8"


def test_three_stock_states_have_distinct_enum_values(bridge) -> None:
    """未上图 / 已上图待定色 / 已标注在 payload 里取三个不同枚举值.

    这是这套数据最危险的误读点: agent 把待定色讲成未标注, 或把未标注讲成
    「已判断为中性」, 后果比界面上混用样式更重, 因为它会被当结论复述.
    """
    bridge.dispatch("investability-label", ["A.SH", "compute.05"])
    bridge.dispatch("investability-label", ["B.SH", "autos.07"])
    out = bridge.dispatch("investability-stocks", ["A.SH,B.SH,C.SH"])["stocks"]
    assert len({out[k]["state"] for k in out}) == 3


def test_node_states_distinct_from_stock_states(bridge) -> None:
    """节点两态与个股三态是不同的枚举, 不混在一个取值域里."""
    bridge.dispatch("investability-node-coverage", ["water.03", "true"])
    nodes = {n["nodeId"]: n for n in bridge.dispatch("investability-map", [])["nodes"]}
    assert nodes["water.03"]["nodeState"] == "confirmed_empty"
    assert nodes["water.01"]["nodeState"] == "unreviewed"


def test_quota_declares_denominator_source(bridge) -> None:
    """配额返回值标明分母口径, 避免与桌面端口径分歧时无从判断."""
    for i in range(10):
        bridge.dispatch("investability-label", [f"S{i}.SH", "infotech.04"])
    codes = ",".join(f"S{i}.SH" for i in range(10))
    out = bridge.dispatch("investability-summary", [codes])
    assert out["denominatorSource"] == "explicit"
    assert out["denominator"] == 10


# --------------------------------------------------------------------------- #
# 写面缺席(plan R25 / KTD8)
# --------------------------------------------------------------------------- #


def _registered_tool_names() -> set[str]:
    """投影后的 MCP 工具名（KTD5：以 pack restrict 为准，不是手写表）。"""
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
    sys.modules["fastmcp"] = mod
    sys.modules.pop("kss_mcp", None)
    kss_mcp = importlib.import_module("kss_mcp")
    return set(kss_mcp.mcp.tools)


def test_map_read_tools_registered() -> None:
    """三个只读工具已注册."""
    names = _registered_tool_names()
    assert {
        "get_investability_map",
        "get_investability_exposure",
        "get_investability_quota",
    } <= names


def test_no_map_write_tool_anywhere() -> None:
    """源码里不存在任何地图写工具, 实时分支内也没有(plan R25).

    写确认的 confirm=True 来自 agent 不来自人, 拿它守人工色表等于把 R7 的
    手工标注偷换成模型标注.
    """
    for name in _registered_tool_names():
        assert not re.match(
            r"^(set|write|update|delete|label|answer|confirm)_investability", name
        ), f"不该存在地图写工具: {name}"
    names = _registered_tool_names()
    for cmd in _R12:
        assert cmd not in names, f"写命令不该出现在 MCP 面: {cmd}"
        assert cmd not in _MCP_SRC, f"写命令不该出现在 MCP 面: {cmd}"


def test_write_commands_stay_in_write_gate(bridge) -> None:
    """三条写命令留在写命令集合里, 由既有的 paper-only 闸兜底."""
    for cmd in _R12:
        assert cmd in bridge.WRITE_COMMANDS


def test_tool_docstrings_flag_labels_as_human_prior() -> None:
    """工具说明写明标签是人工先验不是核实事实, 避免 agent 据此下合规结论."""
    blob = "\n".join(str(spec.get("desc") or "") for spec in chat.TOOL_SPECS)
    for anchor in ("人工先验", "不得据此给买卖或合规结论"):
        assert anchor in blob


# --------------------------------------------------------------------------- #
# 应用内 AI 面板(2026-08-09 裁决: 与 MCP 对称)
# --------------------------------------------------------------------------- #


def _panel_tool_names() -> set[str]:
    """面板侧(kss_chat_loop.TOOL_SPECS)注册的工具名."""
    return {str(spec["name"]) for spec in chat.TOOL_SPECS}


def test_panel_gets_the_same_three_read_tools() -> None:
    """裁决: 面板与 MCP 对称拿这三个只读工具.

    紫苏叶富化的先例是给 MCP 不给面板(见 test_chat_system_prompt 那条断言),
    这层不照抄: 标注是用户自己维护的判断,「这只票我标了吗」天然会问面板.
    """
    assert {
        "get_investability_map",
        "get_investability_exposure",
        "get_investability_quota",
    } <= _panel_tool_names()


def test_panel_has_no_map_write_tool() -> None:
    """对称的只是读面. 写面在面板侧同样缺席(plan KTD8/R25)."""
    import kss_chat_loop as chat  # noqa: PLC0415

    write_commands = set(_R12)
    for spec in chat.TOOL_SPECS:
        assert str(spec.get("command")) not in write_commands, spec["name"]
