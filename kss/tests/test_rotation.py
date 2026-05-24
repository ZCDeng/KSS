"""kss/macro/rotation.py 单测.

覆盖：
- ``load_config`` 读取 + 缺失降级
- ``get_preferred_industries`` / ``get_avoid_industries`` 四阶段全有数据
- ``score_industry_fit`` 精确匹配 / 前缀匹配 / 反向前缀匹配
- 未知行业 / 未知阶段 / 空字符串均返回 0.0（不抛错）
- 热加载：临时改 YAML 立刻生效
- 自定义 ``path`` 参数注入
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kss.macro.rotation import (
    get_avoid_industries,
    get_preferred_industries,
    get_rationale,
    load_config,
    score_industry_fit,
)


# ---------------------------------------------------------------------------- #
# load_config
# ---------------------------------------------------------------------------- #


def test_load_config_default_has_four_stages() -> None:
    cfg = load_config()
    assert set(cfg["stages"].keys()) >= {"I", "II", "III", "IV"}
    assert cfg.get("country") == "CN"
    for stage in ("I", "II", "III", "IV"):
        assert "preferred" in cfg["stages"][stage]
        assert "avoid" in cfg["stages"][stage]


def test_load_config_missing_file_returns_skeleton(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg["stages"] == {}
    assert cfg["weights"]["preferred"] == 1.0
    assert cfg["weights"]["avoid"] == -1.0


# ---------------------------------------------------------------------------- #
# get_preferred / get_avoid
# ---------------------------------------------------------------------------- #


def test_get_preferred_stage_I_contains_rate_sensitive_sectors() -> None:
    prefs = get_preferred_industries("I")
    # 阶段 I = 谷底前，应含汽车 + 房地产（利率敏感）
    assert "汽车" in prefs
    assert "房地产" in prefs


def test_get_preferred_stage_II_contains_commodities() -> None:
    prefs = get_preferred_industries("II")
    assert "钢铁" in prefs or "煤炭" in prefs


def test_get_avoid_stage_III_blocks_late_cycle() -> None:
    avoid = get_avoid_industries("III")
    assert "钢铁" in avoid
    assert "煤炭" in avoid


def test_get_avoid_stage_IV_defends_against_rate_sensitive() -> None:
    avoid = get_avoid_industries("IV")
    assert "汽车" in avoid
    assert "房地产" in avoid


def test_get_preferred_unknown_stage_returns_empty() -> None:
    assert get_preferred_industries("X") == []
    assert get_avoid_industries("X") == []
    assert get_rationale("X") == ""


# ---------------------------------------------------------------------------- #
# score_industry_fit —— 匹配规则
# ---------------------------------------------------------------------------- #


def test_score_exact_match_preferred() -> None:
    # 阶段 II preferred 含"钢铁"
    assert score_industry_fit("钢铁", "II") == 1.0


def test_score_exact_match_avoid() -> None:
    # 阶段 III avoid 含"钢铁"
    assert score_industry_fit("钢铁", "III") == -1.0


def test_score_prefix_match_child_to_parent() -> None:
    """输入"医药生物-中药"应命中阶段 I 的 avoid（含"医药生物-中药"）."""
    assert score_industry_fit("医药生物-中药", "I") == -1.0


def test_score_prefix_match_parent_to_child() -> None:
    """输入"医药生物"应命中阶段 IV 的 preferred（含"医药生物"）."""
    # 阶段 IV preferred 含"医药生物"，"医药生物" 也是子项的前缀
    assert score_industry_fit("医药生物", "IV") == 1.0


def test_score_unknown_industry_neutral() -> None:
    assert score_industry_fit("根本不存在的行业XYZ", "II") == 0.0


def test_score_unknown_stage_neutral() -> None:
    assert score_industry_fit("钢铁", "X") == 0.0


def test_score_empty_inputs_neutral() -> None:
    assert score_industry_fit("", "II") == 0.0
    assert score_industry_fit("钢铁", "") == 0.0
    assert score_industry_fit(None, "II") == 0.0
    assert score_industry_fit("钢铁", None) == 0.0


def test_score_stage_flip_changes_sign() -> None:
    """同一行业（钢铁）在 II 是 +1，III 是 -1 —— 跨阶段反转."""
    s_ii = score_industry_fit("钢铁", "II")
    s_iii = score_industry_fit("钢铁", "III")
    assert s_ii > 0
    assert s_iii < 0
    assert s_ii != s_iii


# ---------------------------------------------------------------------------- #
# Hot reload + 自定义路径
# ---------------------------------------------------------------------------- #


def test_custom_path_overrides_default(tmp_path: Path) -> None:
    """传入 path 参数应覆盖默认配置；用临时 YAML 验证."""
    p = tmp_path / "test_rotation.yaml"
    p.write_text(
        """
stages:
  TEST:
    preferred: [仅测试用]
    avoid: []
weights:
  preferred: 0.5
  avoid: -0.5
  neutral: 0.0
""",
        encoding="utf-8",
    )
    assert get_preferred_industries("TEST", path=p) == ["仅测试用"]
    assert score_industry_fit("仅测试用", "TEST", path=p) == 0.5


def test_hot_reload_picks_up_yaml_changes(tmp_path: Path) -> None:
    """修改 YAML 后下次调用应读到新值（不缓存）."""
    p = tmp_path / "live.yaml"
    p.write_text(
        """
stages:
  Z:
    preferred: [A股]
    avoid: []
""",
        encoding="utf-8",
    )
    assert score_industry_fit("A股", "Z", path=p) == 1.0

    p.write_text(
        """
stages:
  Z:
    preferred: []
    avoid: [A股]
""",
        encoding="utf-8",
    )
    assert score_industry_fit("A股", "Z", path=p) == -1.0
