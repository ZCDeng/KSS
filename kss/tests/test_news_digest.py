"""U9: digest 渲染 + 信号质量自陈 + 按场次归档。"""

from __future__ import annotations

from kss.llm import LLMUnavailable
from kss.news import digest as D


class _FakeClient:
    def __init__(self, reply):
        self._reply = reply

    def complete(self, system, user):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _bundle():
    return {
        "scene": "盘前",
        "items": [
            {"source": "微博", "title": "半导体大涨", "summary": "半导体板块景气", "url": "u1", "heat": 500000},
            {"source": "财联社", "title": "半导体景气回升", "summary": "国产替代加速", "url": "u2"},
            {"source": "雪球", "title": "黄金创新高", "summary": "金价大涨", "url": "u3"},
            {"source": "格隆汇", "title": "黄金避险升温", "summary": "贵金属强势", "url": "u4"},
            {"source": "财联社", "title": "发改委印发AI算力规划", "summary": "政策支持算力", "url": "u5"},
        ],
        "sources": {"微博": 1, "财联社": 2, "雪球": 1, "格隆汇": 1},
    }


LEX = ["半导体", "黄金", "AI算力", "算力"]


def test_two_section_structure_and_signal_quality(monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    client = _FakeClient("0: 偏多\n1: 分歧")
    dg = D.build_digest("盘前", date="20260629", bundle=_bundle(), client=client, with_mapping=False)
    assert dg["scene"] == "盘前"
    assert dg["evidenceRules"]["noTradeAdvice"] is True
    # 方向段非空,每条带 signal_quality
    assert len(dg["directions"]) >= 2
    for d in dg["directions"]:
        assert set(d["signal_quality"]) == {"independent_sources", "has_real_catalyst", "mapping"}
    # 催化段:政策事件
    assert any(c["type"] == "政策" for c in dg["catalysts"])


def test_numbers_from_code_not_llm(monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    client = _FakeClient("0: 偏多 涨7% 转发2万")
    dg = D.build_digest("盘前", date="20260629", bundle=_bundle(), client=client, with_mapping=False)
    md = D.render_markdown(dg)
    assert "<u>" in md  # 代码渲染的热度数字
    assert "7%" not in md and "2万" not in md  # LLM 编的数字不进产出


def test_archive_filename(tmp_path, monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    dg = D.run_news_digest("盘前", date="20260629", bundle=_bundle(),
                           client=_FakeClient("0: 偏多"), with_mapping=False, directory=tmp_path)
    assert (tmp_path / "20260629_盘前.md").exists()
    assert dg["archive_path"].endswith("20260629_盘前.md")
    # 盘后场是另一文件
    D.run_news_digest("盘后", date="20260629", bundle=_bundle(),
                      client=_FakeClient("0: 偏多"), with_mapping=False, directory=tmp_path)
    assert (tmp_path / "20260629_盘后.md").exists()


def test_llm_failure_still_renders(monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    dg = D.build_digest("盘前", date="20260629", bundle=_bundle(),
                        client=_FakeClient(LLMUnavailable("down")), with_mapping=False)
    md = D.render_markdown(dg)
    assert "舆情热点 Digest" in md
    assert dg["llm_status"] == "unavailable"
    assert all(d["sentiment"] == "分歧" for d in dg["directions"])


def test_mapping_present_when_gate_passed(monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    # 模拟 U7 gate 通过 + 注入 theme_leaders(避免 theme_match 真实表干扰,用 fake matcher)
    monkeypatch.setattr("kss.news.hotspot.match_theme",
                        lambda label: {"theme": "半导体", "direct_hit": True} if label == "半导体"
                        else {"theme": None, "direct_hit": False}, raising=False)
    import kss.news.theme_match as tm
    monkeypatch.setattr(tm, "match_theme",
                        lambda label: {"theme": "半导体", "direct_hit": True} if label == "半导体"
                        else {"theme": None, "direct_hit": False})
    theme_leaders = [{"name": "半导体", "boards": [{
        "board": "半导体", "leaders": [{"symbol": "688981.SH", "name": "中芯国际"}], "secondTier": []}]}]
    dg = D.build_digest("盘前", date="20260629", bundle=_bundle(),
                        client=_FakeClient("0: 偏多\n1: 分歧"),
                        theme_leaders=theme_leaders, with_mapping=True)
    semi = next(d for d in dg["directions"] if d["label"] == "半导体")
    assert semi["mapping"] == "direct"
    assert any(s["symbol"] == "688981.SH" for s in semi["stocks"])
    md = D.render_markdown(dg)
    assert "中芯国际" in md


def test_two_section_when_mapping_off(monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    dg = D.build_digest("盘前", date="20260629", bundle=_bundle(),
                        client=_FakeClient("0: 偏多\n1: 分歧"), with_mapping=False)
    md = D.render_markdown(dg)
    assert "## 🔥 集中热点方向" in md and "## ⚡ 重大催化事件" in md
    for d in dg["directions"]:
        assert d["stocks"] == []
        assert d["mapping"] == "off"


def test_empty_bundle_safe(monkeypatch):
    monkeypatch.setattr("kss.news.hotspot.default_lexicon", lambda: LEX)
    dg = D.build_digest("盘前", date="20260629", bundle={"items": []}, client=_FakeClient("x"), with_mapping=False)
    md = D.render_markdown(dg)
    assert "无达标集中方向" in md
    assert "无重大催化" in md
