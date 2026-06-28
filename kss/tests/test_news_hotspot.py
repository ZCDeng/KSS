"""U5: 集中热点方向计算。覆盖 AE2(单源刷屏不上榜)、热度可复现、空安全。

注入显式 lexicon,避免依赖题材库 YAML 状态。
"""

from __future__ import annotations

from kss.news import hotspot

LEX = ["半导体", "光模块", "黄金", "降息", "地产", "固态电池"]


def _item(source, title, summary="", heat=None):
    return {"source": source, "title": title, "summary": summary or title, "heat": heat}


def test_cross_two_sources_on_board_and_sorted():
    items = [
        _item("微博", "半导体大涨", heat=500000),
        _item("财联社", "半导体板块景气回升"),  # 不同文本 → 第2独立确认
        _item("雪球", "黄金创新高"),
        _item("格隆汇", "黄金延续强势,贵金属活跃"),
        _item("微博", "黄金避险升温", heat=300000),
    ]
    dirs = hotspot.build_directions(items, lexicon=LEX, min_confirmations=2)
    labels = [d.label for d in dirs]
    assert "半导体" in labels and "黄金" in labels
    # 黄金 3 确认 > 半导体 2 确认 → 排前
    assert labels.index("黄金") < labels.index("半导体")
    gold = next(d for d in dirs if d.label == "黄金")
    assert gold.independent_confirmations == 3
    assert set(gold.distinct_sources) >= {"雪球", "格隆汇", "微博"}


def test_single_source_spam_not_on_board():
    # AE2: 同一类账号在单源反复刷近似内容 → 去重后 1 确认 < 2 → 不上榜
    items = [
        _item("雪球", "XX妖股要起飞"),
        _item("雪球", "XX妖股要起飞!"),
        _item("雪球", "XX妖股要起飞 "),
    ]
    dirs = hotspot.build_directions(items, lexicon=["妖股", "半导体"], min_confirmations=2)
    assert dirs == []


def test_heat_score_reproducible():
    items = [
        _item("微博", "降息预期升温", heat=200000),
        _item("财联社", "央行或将降息,流动性宽松"),
    ]
    d1 = hotspot.build_directions(items, lexicon=LEX)
    d2 = hotspot.build_directions(items, lexicon=LEX)
    assert [(d.label, d.heat_score) for d in d1] == [(d.label, d.heat_score) for d in d2]
    assert d1[0].heat_score > 0


def test_non_financial_noise_not_bucketed():
    items = [
        _item("微博", "四川宜宾地震", heat=4000000),
        _item("微博", "成都震感", heat=700000),
    ]
    dirs = hotspot.build_directions(items, lexicon=LEX)
    assert dirs == []  # 无方向关键词命中


def test_empty_safe():
    assert hotspot.build_directions([], lexicon=LEX) == []
    assert hotspot.build_directions(None) == []


def test_default_lexicon_nonempty():
    lex = hotspot.default_lexicon()
    assert "半导体" in lex
    assert all(len(t) >= 2 for t in lex)
