"""U7: 题材名匹配层。覆盖扩库加载、精确/同义词直达、AE1（不假直达）、边界安全。"""

from __future__ import annotations

from kss.news.theme_match import load_theme_library, match_theme
from kss.sector.themes import ThemeBucket, load_themes


# —— 扩库后 YAML 仍能正常加载 ——


def test_extended_yaml_loads_via_load_themes_without_error():
    themes = load_themes()
    assert isinstance(themes, dict)
    # 科技老主题 + 新增宏观/商品/消费主题都在（且 board 非空，未被跳过）.
    for name in ("半导体", "贵金属", "能源", "房地产", "消费", "船舶航运"):
        assert name in themes, f"主题缺失: {name}"
    assert "黄金" in themes["贵金属"].industries


def test_load_themes_drops_empty_macro_theme():
    # 「降息受益」industries/concepts 皆空 → load_themes 跳过.
    assert "降息受益" not in load_themes()


def test_library_keeps_empty_macro_theme():
    # 匹配层专用 loader 保留空板块宏观主题（供按主题名直达）.
    lib = load_theme_library()
    assert "降息受益" in lib
    assert lib["降息受益"].industries == []
    assert lib["降息受益"].concepts == []


# —— 精确匹配 ——


def test_exact_theme_name():
    res = match_theme("半导体")
    assert res["theme"] == "半导体"
    assert res["matched_on"] == "exact"
    assert res["direct_hit"] is True
    assert res["fallback"] == "none"


def test_exact_industry_name():
    # 「黄金」是「贵金属」的行业名 → 精确直达.
    res = match_theme("黄金")
    assert res["theme"] == "贵金属"
    assert res["direct_hit"] is True


def test_exact_concept_name_固态电池_maps_correctly():
    # AE1 正面：固态电池是「新能源储能」的概念，精确直达到正确主题，
    # 不被错对到任何「其它电池主题」.
    res = match_theme("固态电池")
    assert res["theme"] == "新能源储能"
    assert res["matched_on"] == "exact"
    assert res["direct_hit"] is True


# —— 同义词匹配 ——


def test_synonym_match():
    # 「光模块」不是任何板块名，只能经同义词表 → AI算力.
    res = match_theme("光模块")
    assert res["theme"] == "AI算力"
    assert res["matched_on"] == "synonym"
    assert res["direct_hit"] is True


def test_synonym_to_empty_macro_theme():
    # 「降息」经同义词 → 「降息受益」主题名直达，即便该主题板块为空.
    res = match_theme("降息")
    assert res["theme"] == "降息受益"
    assert res["matched_on"] == "synonym"
    assert res["direct_hit"] is True


# —— AE1 反面：不臆造直达 ——


def test_unmappable_hotword_routes_to_fallback_not_fake_direct_hit():
    # 库内确实无对应域的热词 → 必须降级，不得伪造直达.
    res = match_theme("外资流入")
    assert res["direct_hit"] is False
    assert res["theme"] is None
    assert res["matched_on"] == "none"
    assert res["fallback"] in ("board", "keyword")


def test_injected_themes_override_default_library():
    # 注入自定义题材库时，匹配只认注入内容（不串默认库）.
    custom = {"X主题": ThemeBucket("X主题", industries=["某行业"], concepts=[])}
    assert match_theme("某行业", themes=custom)["theme"] == "X主题"
    # 默认库的「黄金」在注入库里不存在 → 降级.
    assert match_theme("黄金", themes=custom)["direct_hit"] is False


# —— 边界 / 安全 ——


def test_empty_and_blank_inputs_safe():
    for bad in ("", "   ", None):
        res = match_theme(bad)  # type: ignore[arg-type]
        assert res["direct_hit"] is False
        assert res["theme"] is None
        assert res["fallback"] == "none"


def test_unknown_hotword_with_empty_library_does_not_raise():
    res = match_theme("黄金", themes={})
    assert res["direct_hit"] is False
    assert res["fallback"] == "keyword"
