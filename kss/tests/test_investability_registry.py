"""可投资地图节点树加载器测试(plan U1)."""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from kss.investability.registry import (
    PENDING,
    InvestabilityMap,
    InvestabilityMapError,
)

# --------------------------------------------------------------------------- #
# 真实配置
# --------------------------------------------------------------------------- #

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config" / "investability_map.yaml"


def test_real_config_node_counts() -> None:
    """真实配置加载后节点总数 103, 三条主轴分别 35 / 48 / 20(plan R1)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    assert len(m) == 103
    assert len(m.by_axis("six_networks")) == 35
    assert len(m.by_axis("strategic_industries")) == 48
    assert len(m.by_axis("future_industries")) == 20


def test_real_config_pending_count() -> None:
    """真实配置有 17 个未定色节点, 且未定色不等同于字段缺失(plan R4)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    pending = m.pending_nodes()
    assert len(pending) == 17
    for node in pending:
        assert node.primary_color == PENDING
        assert node.is_pending
        # 未定色是一个显式取值, 不是空串
        assert node.primary_color != ""
        # 源文依据必须写清为什么定不了
        assert node.source_ref


def test_real_config_red_never_a_color() -> None:
    """红不在色板里, 也不作任何节点的主色或次色(plan R2, R3)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    assert "red" not in m.palette
    assert len(m.palette) == 5
    for node in m.all_nodes():
        assert node.primary_color != "red"
        assert node.secondary_color != "red"


def test_real_config_tier_only_on_infotech() -> None:
    """层级是可选字段, 只有新一代信息技术的节点有值(plan R5)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    tiered = [n for n in m.all_nodes() if n.tier]
    assert {n.group for n in tiered} == {"infotech"}
    assert len(tiered) == 7


def test_real_config_semiconductor_equipment_qualifier() -> None:
    """源文的红色限定写进限定说明而不是次色(plan R3, AE7 的数据前提)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    node = m.get("infotech.04")
    assert node is not None
    assert node.primary_color == "light_green"
    assert node.secondary_color == ""
    assert "红" in node.qualifier


def test_real_config_duplicate_named_nodes_are_distinct() -> None:
    """同名的合成生物是两个独立节点, 分属不同主轴(plan Key Decisions)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    a, b = m.get("biomed.06"), m.get("biomfg.01")
    assert a is not None and b is not None
    assert a.name == b.name == "合成生物"
    assert a.axis != b.axis


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #


def test_get_hit_and_miss() -> None:
    """按 id 查命中; 查不存在的 id 返回 None 而不抛错."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    assert m.get("compute.04") is not None
    assert m.get("compute.04").name == "国产AI芯片"
    assert m.get("does.not.exist") is None
    assert m.get("") is None


def test_by_group_and_by_color() -> None:
    """按组与按色查询返回子集, 保持 YAML 原序."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    compute = m.by_group("compute")
    assert len(compute) == 7
    assert compute[0].node_id == "compute.01"
    purple = m.by_color("purple")
    assert {n.node_id for n in purple} == {
        "materials.06",
        "materials.07",
    }


# --------------------------------------------------------------------------- #
# 降级与报错
# --------------------------------------------------------------------------- #


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "map.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


_MINIMAL = """\
    palette:
      deep_green: {label: 深绿, meaning: 底座}
      light_green: {label: 浅绿, meaning: 替代}
    axes:
      six_networks:
        label: 六张网
        groups: {water: 水网}
    nodes:
      - node_id: water.01
        name: 水利工程
        axis: six_networks
        group: water
        primary_color: deep_green
        last_reviewed: '2026-08-09'
"""


def test_missing_file_degrades_to_empty(tmp_path: Path, caplog) -> None:
    """文件不存在返回空注册表并告警, 不抛异常(plan KTD7)."""
    m = InvestabilityMap.from_yaml(tmp_path / "nope.yaml")
    assert len(m) == 0
    assert m.all_nodes() == []
    assert m.get("water.01") is None
    assert any("不存在" in r.message for r in caplog.records)


def test_yaml_syntax_error_raises(tmp_path: Path) -> None:
    """YAML 语法错误抛配置异常类."""
    p = _write(tmp_path, "palette: {a: 1}\nnodes: [\n  - broken\n")
    with pytest.raises(InvestabilityMapError):
        InvestabilityMap.from_yaml(p)


def test_missing_nodes_key_raises(tmp_path: Path) -> None:
    """顶层缺 nodes 键抛配置异常类."""
    p = _write(tmp_path, "palette:\n  deep_green: {label: 深绿, meaning: 底座}\n")
    with pytest.raises(InvestabilityMapError):
        InvestabilityMap.from_yaml(p)


def test_missing_palette_key_raises(tmp_path: Path) -> None:
    """顶层缺 palette 键抛配置异常类 —— 色板是整棵树的取值域."""
    p = _write(tmp_path, "nodes: []\n")
    with pytest.raises(InvestabilityMapError):
        InvestabilityMap.from_yaml(p)


def test_nodes_not_a_list_raises(tmp_path: Path) -> None:
    """nodes 不是 list 抛配置异常类."""
    p = _write(
        tmp_path,
        "palette:\n  deep_green: {label: 深绿, meaning: 底座}\nnodes:\n  a: b\n",
    )
    with pytest.raises(InvestabilityMapError):
        InvestabilityMap.from_yaml(p)


def test_red_secondary_color_skips_node(tmp_path: Path, caplog) -> None:
    """次色取红的节点被跳过, 其余节点仍可用(plan R3)."""
    p = _write(
        tmp_path,
        _MINIMAL
        + """\
      - node_id: water.02
        name: 泵阀管材
        axis: six_networks
        group: water
        primary_color: deep_green
        secondary_color: red
""",
    )
    m = InvestabilityMap.from_yaml(p)
    assert len(m) == 1
    assert m.get("water.01") is not None
    assert m.get("water.02") is None
    assert any("次色" in r.message for r in caplog.records)


def test_unknown_primary_color_skips_node(tmp_path: Path, caplog) -> None:
    """主色不在色板内的节点被跳过, 其余节点仍可用."""
    p = _write(
        tmp_path,
        _MINIMAL
        + """\
      - node_id: water.03
        name: 水务运营
        axis: six_networks
        group: water
        primary_color: chartreuse
""",
    )
    m = InvestabilityMap.from_yaml(p)
    assert len(m) == 1
    assert m.get("water.03") is None
    assert any("主色" in r.message for r in caplog.records)


def test_missing_node_id_skips_node(tmp_path: Path, caplog) -> None:
    """缺 node_id 的节点被跳过, 其余节点仍可用."""
    p = _write(
        tmp_path,
        _MINIMAL
        + """\
      - name: 无名节点
        axis: six_networks
        group: water
        primary_color: deep_green
""",
    )
    m = InvestabilityMap.from_yaml(p)
    assert len(m) == 1
    assert any("node_id" in r.message for r in caplog.records)


def test_group_not_in_axis_skips_node(tmp_path: Path, caplog) -> None:
    """组不属于所声明主轴的节点被跳过."""
    p = _write(
        tmp_path,
        _MINIMAL
        + """\
      - node_id: water.04
        name: 错组节点
        axis: six_networks
        group: compute
        primary_color: deep_green
""",
    )
    m = InvestabilityMap.from_yaml(p)
    assert len(m) == 1
    assert m.get("water.04") is None
    assert any("不属于主轴" in r.message for r in caplog.records)


def test_duplicate_node_id_keeps_first(tmp_path: Path, caplog) -> None:
    """node_id 重复时保留先出现的一条并告警."""
    p = _write(
        tmp_path,
        _MINIMAL
        + """\
      - node_id: water.01
        name: 冒名顶替
        axis: six_networks
        group: water
        primary_color: light_green
""",
    )
    m = InvestabilityMap.from_yaml(p)
    assert len(m) == 1
    assert m.get("water.01").name == "水利工程"
    assert any("重复" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# 陈旧度(plan R23, 判定在 Python 侧 —— KTD2)
# --------------------------------------------------------------------------- #


def test_stale_nodes_by_age(tmp_path: Path) -> None:
    """复核日期超过 120 天算陈旧, 未超过不算."""
    p = _write(
        tmp_path,
        """\
    palette:
      deep_green: {label: 深绿, meaning: 底座}
    axes:
      six_networks: {label: 六张网, groups: {water: 水网}}
    nodes:
      - node_id: fresh
        name: 新
        axis: six_networks
        group: water
        primary_color: deep_green
        last_reviewed: '2026-07-01'
      - node_id: old
        name: 旧
        axis: six_networks
        group: water
        primary_color: deep_green
        last_reviewed: '2026-01-01'
""",
    )
    m = InvestabilityMap.from_yaml(p)
    stale = m.stale_node_ids(as_of=date(2026, 8, 9))
    assert stale == {"old"}


def test_unparsable_review_date_counts_as_stale(tmp_path: Path) -> None:
    """复核日期缺失或不可解析一律算陈旧 —— 证明不了新就不显示成新."""
    p = _write(
        tmp_path,
        """\
    palette:
      deep_green: {label: 深绿, meaning: 底座}
    axes:
      six_networks: {label: 六张网, groups: {water: 水网}}
    nodes:
      - node_id: blank
        name: 无日期
        axis: six_networks
        group: water
        primary_color: deep_green
      - node_id: junk
        name: 坏日期
        axis: six_networks
        group: water
        primary_color: deep_green
        last_reviewed: '前天'
""",
    )
    m = InvestabilityMap.from_yaml(p)
    assert m.stale_node_ids(as_of=date(2026, 8, 9)) == {"blank", "junk"}


def test_real_config_not_stale_at_authoring_date() -> None:
    """首版录入日当天全表都不陈旧, 且最旧复核日期可读(plan R23 页头)."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    assert m.stale_node_ids(as_of=date(2026, 8, 9)) == set()
    assert m.oldest_reviewed() == "2026-08-09"
    assert m.source_version == "V1.1"


def test_as_dict_shape() -> None:
    """节点序列化用 camelCase 键, 与桥接返回值约定一致."""
    m = InvestabilityMap.from_yaml(REAL_CONFIG)
    d = m.get("compute.04").as_dict()
    assert d["nodeId"] == "compute.04"
    assert d["primaryColor"] == "orange"
    assert d["isPending"] is False
    assert set(d) >= {
        "nodeId",
        "name",
        "axis",
        "group",
        "primaryColor",
        "secondaryColor",
        "tier",
        "reading",
        "qualifier",
        "sourceRef",
        "lastReviewed",
        "isPending",
    }
