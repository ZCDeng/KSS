"""U4: 信息源去重 + 源真实性加权。覆盖 AE3(同公告多源转发)、AE4(协同刷量)。"""

from __future__ import annotations

from kss.news import dedup


def _item(source, title, summary="", author=None, author_age_days=None, heat=None):
    it = {"source": source, "title": title, "summary": summary or title, "heat": heat}
    if author is not None:
        it["author"] = author
    if author_age_days is not None:
        it["author_age_days"] = author_age_days
    return it


def test_jaccard_and_normalize():
    assert dedup.normalize_text("半导体, 板块！大涨。") == "半导体板块大涨"
    a = dedup.shingles("固态电池量产提速")
    b = dedup.shingles("固态电池量产提速!")
    assert dedup.jaccard(a, b) >= 0.9


# AE3: 同一公告原文被三处转发,内容近似 → 只记 1 次独立确认
def test_same_announcement_three_sources_counts_once():
    text = "某公司公告:固态电池产线投产,年产能10GWh"
    items = [
        _item("雪球", text),
        _item("财联社", text + " "),  # 近似
        _item("格隆汇", "某公司公告:固态电池产线投产 年产能10GWh"),  # 近似变体
    ]
    res = dedup.dedupe_sources(items)
    assert len(res.clusters) == 1
    assert res.independent_confirmations == 1  # 不因出现在 3 源而 =3
    assert set(res.clusters[0].sources) == {"雪球", "财联社", "格隆汇"}


# AE4: 多个新建/低龄账号同步发近似文案 → 加权后不计有机集中度
def test_coordinated_pump_low_age_not_authentic():
    text = "速看!XX妖股要起飞,满仓干"
    items = [
        _item("雪球", text, author="u1", author_age_days=3),
        _item("X", text + "!", author="u2", author_age_days=5),
        _item("雪球", text + " ", author="u3", author_age_days=2),
    ]
    res = dedup.dedupe_sources(items)
    assert len(res.clusters) == 1
    assert res.clusters[0].authentic is False
    assert "coordinated_pump" in res.clusters[0].reason
    assert res.independent_confirmations == 0  # 不计入有机


def test_pump_plus_one_genuine_stays_below_threshold():
    # 协同刷量(1簇,不可信) + 1条真实独立信息 → 可信确认数=1 < 2
    pump = "速看XX妖股满仓干"
    items = [
        _item("雪球", pump, author="a", author_age_days=3),
        _item("X", pump + "!", author="b", author_age_days=4),
        _item("雪球", pump + " ", author="c", author_age_days=1),
        _item("财联社", "央行宣布定向降准,银行板块受益"),  # 真实独立
    ]
    res = dedup.dedupe_sources(items)
    assert res.independent_confirmations == 1


# 分散独立讨论 → 各计 1
def test_distinct_topics_each_count():
    items = [
        _item("雪球", "半导体板块全线大涨,光模块领涨"),
        _item("财联社", "央行降息落地,地产链反弹"),
        _item("格隆汇", "黄金创历史新高,贵金属午后拉升"),
    ]
    res = dedup.dedupe_sources(items)
    assert len(res.clusters) == 3
    assert res.independent_confirmations == 3
    assert len(res.distinct_sources) == 3


def test_authentic_when_no_account_age_metadata():
    # 当前多数源无账号年龄 → 无法判定刷量,默认可信(v1 局限)
    text = "某主题热度高"
    items = [_item("雪球", text), _item("X", text + "!")]
    res = dedup.dedupe_sources(items)
    assert res.clusters[0].authentic is True


def test_empty_safe():
    res = dedup.dedupe_sources([])
    assert res.clusters == []
    assert res.independent_confirmations == 0
    assert res.distinct_sources == []
    assert dedup.independent_confirmations(None) == 0
