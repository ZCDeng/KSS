"""可投资地图桥接层测试(plan U3).

判定逻辑(区位、配额、陈旧度)全在桥接层, 这里逐条对着验收示例验.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_app_bridge as b  # noqa: E402


@pytest.fixture()
def bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把状态根指到临时目录, 两张写表随之落到临时库."""
    (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    return b


def _answer_all(bridge, code: str, values: dict[int, str]) -> None:
    for q, v in values.items():
        bridge.dispatch("investability-answer", [code, str(q), v])


# --------------------------------------------------------------------------- #
# 节点树
# --------------------------------------------------------------------------- #


def test_map_returns_full_tree(bridge) -> None:
    """节点树返回 103 个节点, 未定色节点主色为 pending(plan R1, R4)."""
    out = bridge.dispatch("investability-map", [])
    assert len(out["nodes"]) == 103
    pending = [n for n in out["nodes"] if n["primaryColor"] == "pending"]
    assert len(pending) == 17
    assert out["sourceVersion"] == "V1.1"
    assert len(out["palette"]) == 5
    assert "red" not in out["palette"]


def test_map_node_states(bridge) -> None:
    """节点三态: 有票 / 已确认无标的 / 未核(plan R9, AE1)."""
    bridge.dispatch("investability-label", ["688008.SH", "compute.05"])
    bridge.dispatch("investability-node-coverage", ["water.03", "true"])
    nodes = {n["nodeId"]: n for n in bridge.dispatch("investability-map", ["688008.SH"])["nodes"]}
    assert nodes["compute.05"]["nodeState"] == "has_stocks"
    assert nodes["compute.05"]["stocks"] == ["688008.SH"]
    assert nodes["water.03"]["nodeState"] == "confirmed_empty"
    assert nodes["water.03"]["confirmedAt"]
    assert nodes["water.01"]["nodeState"] == "unreviewed"


def test_map_not_stale_today(bridge) -> None:
    """首版录入日全表不陈旧, 页头能读到最旧复核日期(plan R23)."""
    out = bridge.dispatch("investability-map", [])
    assert out["staleCount"] == 0
    assert out["oldestReviewed"] == "2026-08-09"
    assert all(n["isStale"] is False for n in out["nodes"])


# --------------------------------------------------------------------------- #
# 区位判定(plan R14 / R15)
# --------------------------------------------------------------------------- #


def test_ae3_partial_entry_no_verdict(bridge) -> None:
    """覆盖 AE3: 只答 5 题其中 2 是 → 未尽调 · 已定 5/8, 不给核心区."""
    code = "688008.SH"
    _answer_all(bridge, code, {1: "yes", 2: "yes", 3: "no", 4: "no", 5: "no"})
    zone = bridge.dispatch("investability-stocks", [code])["stocks"][code]["zone"]
    assert zone["display"] == "未尽调 · 已定 5/8"
    assert zone["key"] == "undetermined"
    assert zone["decided"] == 5


def test_ae9_all_unknown_gives_no_low_exposure(bridge) -> None:
    """覆盖 AE9: 8 题全选未知 → 未尽调 · 已定 0/8, 绝不落进核心区."""
    code = "688008.SH"
    _answer_all(bridge, code, dict.fromkeys(range(1, 9), "unknown"))
    zone = bridge.dispatch("investability-stocks", [code])["stocks"][code]["zone"]
    assert zone["display"] == "未尽调 · 已定 0/8"
    assert zone["key"] != "core"


def test_ae10_lower_bound_locks_red_early(bridge) -> None:
    """覆盖 AE10: 已定 6 题其中 5 是 → 红区 · 已定 6/8, 剩余两题翻不回来."""
    code = "688008.SH"
    _answer_all(
        bridge, code, {1: "yes", 2: "yes", 3: "yes", 4: "yes", 5: "yes", 6: "no"}
    )
    zone = bridge.dispatch("investability-stocks", [code])["stocks"][code]["zone"]
    assert zone["display"] == "红区 · 已定 6/8"
    assert zone["key"] == "red"


def test_at_least_satellite_lower_bound(bridge) -> None:
    """已答 3 个是但未定满 → 至少卫星, 不给核心区也不冒充红区."""
    code = "688008.SH"
    _answer_all(bridge, code, {1: "yes", 2: "yes", 3: "yes"})
    zone = bridge.dispatch("investability-stocks", [code])["stocks"][code]["zone"]
    assert zone["key"] == "at_least_satellite"
    assert zone["display"] == "至少卫星 · 已定 3/8"


def test_full_entry_core_zone(bridge) -> None:
    """8 题全定且只有 1 个是 → 核心区."""
    code = "688008.SH"
    values = dict.fromkeys(range(1, 9), "no")
    values[1] = "yes"
    _answer_all(bridge, code, values)
    zone = bridge.dispatch("investability-stocks", [code])["stocks"][code]["zone"]
    assert zone["key"] == "core"
    assert zone["display"] == "核心区 · 已定 8/8"


def test_ae4_red_zone_does_not_rewrite_industry_color(bridge) -> None:
    """覆盖 AE4: 主节点浅绿的票判红区后, 色点仍是浅绿, 两个维度不互相改写."""
    code = "688008.SH"
    bridge.dispatch("investability-label", [code, "infotech.04"])
    values = dict.fromkeys(range(1, 9), "no")
    for q in (1, 2, 3, 4, 5, 6):
        values[q] = "yes"
    _answer_all(bridge, code, values)
    item = bridge.dispatch("investability-stocks", [code])["stocks"][code]
    assert item["colorKey"] == "light_green"
    assert item["colorLabel"] == "浅绿"
    assert item["zone"]["key"] == "red"
    assert item["primaryNode"]["nodeId"] == "infotech.04"


# --------------------------------------------------------------------------- #
# 个股状态与节点路径
# --------------------------------------------------------------------------- #


def test_ae2_primary_decides_color_both_nodes_in_path(bridge) -> None:
    """覆盖 AE2: 主黄副浅绿 → 色取黄, 主副两个节点都在路径里."""
    code = "688008.SH"
    bridge.dispatch("investability-label", [code, "compute.05", "telecom.03"])
    item = bridge.dispatch("investability-stocks", [code])["stocks"][code]
    assert item["colorKey"] == "yellow"
    assert item["primaryNode"]["nodeId"] == "compute.05"
    assert [n["nodeId"] for n in item["secondaryNodes"]] == ["telecom.03"]


def test_three_stock_states_are_distinct(bridge) -> None:
    """未标注 / 已上图待定色 / 已标注三态取三个不同值(plan R8)."""
    bridge.dispatch("investability-label", ["A.SH", "compute.05"])
    bridge.dispatch("investability-label", ["B.SH", "autos.07"])  # 主色 pending
    out = bridge.dispatch("investability-stocks", ["A.SH,B.SH,C.SH"])["stocks"]
    assert out["A.SH"]["state"] == "labelled"
    assert out["B.SH"]["state"] == "pending_color"
    assert out["C.SH"]["state"] == "unlabelled"
    assert len({out[k]["state"] for k in out}) == 3
    assert out["B.SH"]["stateLabel"] == "已上图 · 待定色"
    assert out["C.SH"]["stateLabel"] == "未上图"
    assert out["B.SH"]["colorKey"] == ""


def test_bulk_read_covers_many_codes(bridge) -> None:
    """批量读一次返回多只票, 推荐页与盯盘页不必逐只发桥接调用."""
    for i in range(5):
        bridge.dispatch("investability-label", [f"{i}.SH", "compute.05"])
    codes = ",".join(f"{i}.SH" for i in range(5)) + ",999.SH"
    out = bridge.dispatch("investability-stocks", [codes])["stocks"]
    assert len(out) == 6
    assert out["999.SH"]["state"] == "unlabelled"


def test_empty_codes_returns_empty(bridge) -> None:
    """空代码列表返回空字典而不是报错."""
    assert bridge.dispatch("investability-stocks", [""])["stocks"] == {}


# --------------------------------------------------------------------------- #
# 配额(plan R18 / AE6)
# --------------------------------------------------------------------------- #


def test_ae6_quota_two_tracks_and_denominator(bridge) -> None:
    """覆盖 AE6: 分母只含已标注且主色非待定的票, 五色合计 100%, 红区走副轨."""
    orange = [f"O{i}.SH" for i in range(4)]
    purple = ["P0.SH"]
    green = [f"G{i}.SH" for i in range(15)]
    for c in orange:
        bridge.dispatch("investability-label", [c, "compute.04"])
    for c in purple:
        bridge.dispatch("investability-label", [c, "materials.06"])
    for c in green:
        bridge.dispatch("investability-label", [c, "infotech.04"])
    unlabelled = [f"U{i}.SH" for i in range(5)]
    pending = ["PD.SH"]
    bridge.dispatch("investability-label", ["PD.SH", "autos.07"])

    # 其中一只浅绿票判红区
    values = dict.fromkeys(range(1, 9), "no")
    for q in (1, 2, 3, 4, 5):
        values[q] = "yes"
    _answer_all(bridge, "G0.SH", values)

    codes = ",".join(orange + purple + green + unlabelled + pending)
    out = bridge.dispatch("investability-summary", [codes])

    assert out["denominator"] == 20
    assert out["denominatorSource"] == "explicit"
    assert out["unlabelledCount"] == 5
    assert out["pendingColorCount"] == 1
    assert out["sampleInsufficient"] is False
    assert out["ratios"]["orange"] == 20.0
    assert out["ratios"]["purple"] == 5.0
    assert round(sum(out["ratios"].values()), 1) == 100.0
    assert out["redCount"] == 1
    assert out["redRatio"] == 5.0
    assert out["redSymbols"] == ["G0.SH"]


def test_pending_color_stock_kept_out_of_denominator(bridge) -> None:
    """主色待定的票不进主轨分母, 否则五色永远凑不满 100%."""
    for i in range(10):
        bridge.dispatch("investability-label", [f"G{i}.SH", "infotech.04"])
    bridge.dispatch("investability-label", ["PD.SH", "autos.07"])
    codes = ",".join([f"G{i}.SH" for i in range(10)] + ["PD.SH"])
    out = bridge.dispatch("investability-summary", [codes])
    assert out["denominator"] == 10
    assert out["pendingColorCount"] == 1
    assert round(sum(out["ratios"].values()), 1) == 100.0


def test_small_sample_suppresses_percentages(bridge) -> None:
    """已标注不足 10 只时不出百分比, 只给只数(plan R18)."""
    bridge.dispatch("investability-label", ["A.SH", "compute.05"])
    bridge.dispatch("investability-label", ["B.SH", "compute.04"])
    out = bridge.dispatch("investability-summary", ["A.SH,B.SH"])
    assert out["sampleInsufficient"] is True
    assert out["ratios"] == {}
    assert out["redRatio"] is None
    assert out["counts"]["yellow"] == 1
    assert out["counts"]["orange"] == 1


def test_cap_flags_over_limit(bridge) -> None:
    """传了橙加紫上限时返回越线标记, 留空则不判定."""
    for i in range(10):
        node = "compute.04" if i < 4 else "infotech.04"
        bridge.dispatch("investability-label", [f"S{i}.SH", node])
    codes = ",".join(f"S{i}.SH" for i in range(10))
    assert bridge.dispatch("investability-summary", [codes])["overCap"] is None
    assert bridge.dispatch("investability-summary", [codes, "25"])["overCap"] is True
    assert bridge.dispatch("investability-summary", [codes, "50"])["overCap"] is False


# --------------------------------------------------------------------------- #
# 写路径与导出
# --------------------------------------------------------------------------- #


def test_label_write_then_read_has_timestamp(bridge) -> None:
    """写标注后立即读回, 更新时间非空(plan R10)."""
    out = bridge.dispatch("investability-label", ["688008.SH", "compute.05"])
    assert out["ok"] is True
    assert out["labels"][0]["updatedAt"]
    item = bridge.dispatch("investability-stocks", ["688008.SH"])["stocks"]["688008.SH"]
    assert item["labelUpdatedAt"]


def test_label_empty_primary_clears(bridge) -> None:
    """主节点传空串清空标注, 该票回到未上图."""
    bridge.dispatch("investability-label", ["688008.SH", "compute.05"])
    bridge.dispatch("investability-label", ["688008.SH", ""])
    item = bridge.dispatch("investability-stocks", ["688008.SH"])["stocks"]["688008.SH"]
    assert item["state"] == "unlabelled"


def test_answer_rejects_bad_value(bridge) -> None:
    """答案取值只接受 yes / no / unknown."""
    with pytest.raises(ValueError):
        bridge.dispatch("investability-answer", ["688008.SH", "1", "maybe"])


def test_export_round_trips(bridge) -> None:
    """全量导出含三张表原始行, 是这份不可再生数据唯一的第二份拷贝."""
    bridge.dispatch("investability-label", ["688008.SH", "compute.05", "telecom.03"])
    bridge.dispatch("investability-answer", ["688008.SH", "2", "yes"])
    bridge.dispatch("investability-node-coverage", ["water.03", "true"])
    dump = bridge.dispatch("investability-export", [])
    assert len(dump["labels"]) == 2
    assert dump["answers"][0]["q2"] == 1
    assert len(dump["nodeCoverage"]) == 1
    assert dump["exportedAt"]


# --------------------------------------------------------------------------- #
# 命令登记(plan R25 / KTD8)
# --------------------------------------------------------------------------- #


def test_read_commands_registered_not_write(bridge) -> None:
    """四条读命令登记进 COMMANDS 但不进写命令集合."""
    for cmd in (
        "investability-map",
        "investability-stocks",
        "investability-summary",
        "investability-export",
    ):
        assert cmd in bridge.COMMANDS
        assert cmd not in bridge.WRITE_COMMANDS


def test_write_commands_are_gated(bridge) -> None:
    """三条写命令进写命令集合, 由此被 MCP 的 paper-only 闸挡住(plan R25)."""
    for cmd in (
        "investability-label",
        "investability-answer",
        "investability-node-coverage",
    ):
        assert cmd in bridge.COMMANDS
        assert cmd in bridge.WRITE_COMMANDS
