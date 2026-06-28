"""U8: 关联标的映射 + provenance 审计。覆盖 AE1(题材无对应域→不臆造、走降级)。"""

from __future__ import annotations

from kss.news.hotspot import attach_related_stocks


def _direction(label, items=None):
    return {
        "label": label,
        "items": items or [{"source": "财联社", "title": f"{label}大涨", "url": "http://x/1"}],
    }


def _fake_matcher(table):
    def m(label):
        return table.get(label, {"theme": None, "direct_hit": False, "fallback": "keyword"})
    return m


# 直达 + 有 board → 挂龙头/二梯队,带板块归属 + 来源帖审计
def test_direct_hit_attaches_stocks_with_provenance():
    theme_leaders = [{
        "name": "半导体",
        "boards": [{
            "board": "半导体",
            "classification": "industry",
            "leaders": [{"symbol": "688981.SH", "name": "中芯国际"}],
            "secondTier": [{"symbol": "002049.SZ", "name": "紫光国微"}],
        }],
    }]
    matcher = _fake_matcher({"半导体": {"theme": "半导体", "direct_hit": True}})
    out = attach_related_stocks([_direction("半导体")], theme_leaders=theme_leaders, matcher=matcher)
    assert len(out) == 1
    m = out[0]
    assert m["match"] == "direct" and m["degrade"] is None
    syms = {s["symbol"]: s for s in m["stocks"]}
    assert "688981.SH" in syms and syms["688981.SH"]["tier"] == "leader"
    assert syms["002049.SZ"]["tier"] == "second"
    assert syms["688981.SH"]["board"] == "半导体"
    # provenance:每个标的带驱动来源帖
    assert syms["688981.SH"]["source_posts"][0]["source"] == "财联社"


# AE1: 题材无对应域(库无该主题 / 不直达)→ 不臆造,走降级
def test_unmappable_direction_degrades_no_fake_stock():
    matcher = _fake_matcher({})  # 全部不直达
    out = attach_related_stocks([_direction("某生造妖词")], theme_leaders=[], matcher=matcher)
    assert out[0]["stocks"] == []
    assert out[0]["degrade"] == "no_direct_match"
    assert out[0]["theme"] is None


# 宏观/商品主题 board 不在快照宇宙 → 空 board 落降级链,不空挂
def test_macro_theme_empty_board_degrades():
    theme_leaders = [{"name": "贵金属", "boards": []}]  # 黄金等无 board
    matcher = _fake_matcher({"黄金": {"theme": "贵金属", "direct_hit": True}})
    out = attach_related_stocks([_direction("黄金")], theme_leaders=theme_leaders, matcher=matcher)
    assert out[0]["theme"] == "贵金属"
    assert out[0]["stocks"] == []
    assert out[0]["degrade"] == "theme_no_board_in_universe"


# 候选与龙头去重:同一 symbol 既在 leaders 又在 secondTier → 保留龙头
def test_leader_second_tier_dedup():
    theme_leaders = [{
        "name": "AI算力",
        "boards": [{
            "board": "光模块",
            "leaders": [{"symbol": "300308.SZ", "name": "中际旭创"}],
            "secondTier": [{"symbol": "300308.SZ", "name": "中际旭创"}, {"symbol": "300502.SZ", "name": "新易盛"}],
        }],
    }]
    matcher = _fake_matcher({"AI算力": {"theme": "AI算力", "direct_hit": True}})
    out = attach_related_stocks([_direction("AI算力")], theme_leaders=theme_leaders, matcher=matcher)
    syms = {s["symbol"]: s for s in out[0]["stocks"]}
    assert syms["300308.SZ"]["tier"] == "leader"  # 去重保龙头
    assert "300502.SZ" in syms
    assert len(out[0]["stocks"]) == 2


def test_empty_directions_safe():
    assert attach_related_stocks([], theme_leaders=[], matcher=_fake_matcher({})) == []
    assert attach_related_stocks(None, theme_leaders=None, matcher=_fake_matcher({})) == []
