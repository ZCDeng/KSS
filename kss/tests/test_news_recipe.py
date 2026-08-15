"""U2: 舆情采集 recipe —— 多源 → 结构化证据。

用 fake reach + 临时 sources.yaml,不依赖本机 seek 容器。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from kss.news import collect

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_recipes as r  # noqa: E402


# ---- 解析器单测 ----

def test_parse_weibo_extracts_title_heat_url():
    text = (
        "1. 四川宜宾地震 [热]  热度:3878182\n"
        "   https://s.weibo.com/weibo?q=%23%E5%9B%9B%E5%B7%9D%23\n"
        "2. 神23乘组太空出差满月  热度:574440\n"
        "   https://s.weibo.com/weibo?q=%23shen23%23\n"
    )
    items = collect.parse_weibo(text)
    assert len(items) == 2
    assert items[0]["title"] == "四川宜宾地震"
    assert items[0]["heat"] == 3878182
    assert items[0]["url"].startswith("https://s.weibo.com")
    assert "[热]" not in items[0]["title"] and "热度" not in items[0]["title"]


def test_parse_search_blocks():
    text = (
        "标题：A股半导体板块早盘大涨\n"
        "摘要：板块异动 | A股半导体板块早盘大涨 个股大面积涨停\n"
        "来源：https://www.cnstock.com/commonDetail/710684\n"
        "\n---\n\n"
        "标题：A股核电板块活跃\n"
        "摘要：多家产业链企业涨停\n"
        "来源：https://www.cnstock.com/commonDetail/649779\n"
    )
    items = collect.parse_search(text)
    assert len(items) == 2
    assert items[0]["title"] == "A股半导体板块早盘大涨"
    assert "半导体" in items[0]["summary"]
    assert items[0]["url"] == "https://www.cnstock.com/commonDetail/710684"


def test_parse_twitter_empty_when_unrecognized():
    assert collect.parse_twitter("") == []
    assert collect.parse_twitter("一堆无结构推文") == []


# ---- collect_news 集成(fake reach) ----

def _write_sources(tmp_path, sources) -> Path:
    p = tmp_path / "news_sources.yaml"
    p.write_text(yaml.safe_dump({"sources": sources}, allow_unicode=True), encoding="utf-8")
    return p


def _write_tracks(tmp_path, tracks) -> Path:
    p = tmp_path / "news_sources_tracks.yaml"
    p.write_text(yaml.safe_dump({"tracks": tracks}, allow_unicode=True), encoding="utf-8")
    return p


# ---- 源清单加载:两种 yaml 结构都要认 ----

def test_load_sources_reads_track_grouped_yaml(tmp_path):
    """U1 起生产 yaml 是 tracks[].sources[];只读顶层 sources 会恒返回空清单。"""
    path = _write_tracks(tmp_path, [
        {"key": "ai", "name": "AI", "sources": [
            {"key": "a1", "name": "源一", "tool": "bocha_web_search", "args": {"query": "x"}},
            {"key": "a2", "name": "源二", "tool": "bocha_web_search", "args": {"query": "y"}},
        ]},
        {"key": "macro", "name": "宏观", "sources": [
            {"key": "m1", "name": "源三", "tool": "reach_weibo_hot", "args": {}},
        ]},
    ])
    got = collect.load_sources(path)
    assert [s["key"] for s in got] == ["a1", "a2", "m1"]


def test_load_sources_still_reads_flat_yaml(tmp_path):
    """旧扁平结构不能因为兼容新结构而失效。"""
    path = _write_sources(tmp_path, [
        {"key": "weibo", "name": "微博", "tool": "reach_weibo_hot", "args": {}},
    ])
    assert [s["key"] for s in collect.load_sources(path)] == ["weibo"]


def test_production_sources_yaml_is_not_empty():
    """冒烟:仓库里真实的源清单必须能被读出来。

    2026-07-09 yaml 改成分赛道后 load_sources 没跟上,采集空转了一个多月而测试全绿
    ——因为测试只喂扁平结构。这条直接打真实配置,堵住同类回归。
    """
    got = collect.load_sources()
    assert got, "生产 news_sources.yaml 读出空清单"
    assert any(s.get("enabled", True) for s in got)


def _fake_reach(responses):
    """responses: {tool: {"ok":..,"text":..} 或 Exception}。"""
    def reach(tool, **args):
        val = responses.get(tool)
        if val is None:
            return {"ok": False, "text": "", "structured": None, "error": "no stub", "tool": tool}
        return val
    return reach


def test_collect_merges_three_sources_with_provenance(tmp_path):
    sources = [
        {"key": "weibo", "name": "微博", "tool": "reach_weibo_hot", "args": {}, "enabled": True},
        {"key": "cls", "name": "财联社", "tool": "bocha_web_search", "args": {"query": "x"}, "enabled": True},
        {"key": "gelonghui", "name": "格隆汇", "tool": "bocha_web_search", "args": {"query": "y"}, "enabled": True},
    ]
    path = _write_sources(tmp_path, sources)
    reach = _fake_reach({
        "reach_weibo_hot": {"ok": True, "text": "1. 固态电池 热度:120000\n  https://s.weibo.com/x"},
        "bocha_web_search": {"ok": True, "text": "标题：财政发力\n摘要：政策利好基建\n来源：https://cls.cn/a"},
    })
    out = collect.collect_news("盘前", sources_path=path, reach=reach)
    assert out["scene"] == "盘前"
    assert out["evidenceRules"]["localTruthPrecedence"] is True
    assert len(out["items"]) == 3  # 微博1 + bocha×2源各1
    # 每条带完整 provenance 契约
    for it in out["items"]:
        assert set(it) >= {"source", "source_kind", "title", "summary", "url", "time", "author", "heat", "sourceTier", "warnings"}
    assert out["sources"] == {"微博": 1, "财联社": 1, "格隆汇": 1}
    assert "partial" not in out


def test_collect_single_source_failure_is_partial(tmp_path):
    sources = [
        {"key": "weibo", "name": "微博", "tool": "reach_weibo_hot", "args": {}, "enabled": True},
        {"key": "twitter", "name": "X", "tool": "reach_twitter_search", "args": {"query": "z"}, "enabled": True},
    ]
    path = _write_sources(tmp_path, sources)
    reach = _fake_reach({
        "reach_weibo_hot": {"ok": True, "text": "1. 黄金 热度:90000\n  https://s.weibo.com/g"},
        "reach_twitter_search": {"ok": False, "text": "", "error": "500", "tool": "reach_twitter_search"},
    })
    out = collect.collect_news("盘前", sources_path=path, reach=reach)
    assert out.get("partial") is True
    assert out["failedSteps"] == ["X"]
    assert len(out["items"]) == 1  # 微博正常


def test_collect_empty_config_is_safe(tmp_path):
    path = _write_sources(tmp_path, [])
    out = collect.collect_news("盘后", sources_path=path, reach=_fake_reach({}))
    assert out["items"] == []
    assert out["sources"] == {}
    assert "partial" not in out


def test_collect_missing_file_is_safe():
    out = collect.collect_news("盘前", sources_path="/nonexistent/news_sources.yaml", reach=_fake_reach({}))
    assert out["items"] == []


def test_disabled_source_skipped(tmp_path):
    sources = [
        {"key": "weibo", "name": "微博", "tool": "reach_weibo_hot", "args": {}, "enabled": True},
        {"key": "twitter", "name": "X", "tool": "reach_twitter_search", "args": {}, "enabled": False},
    ]
    path = _write_sources(tmp_path, sources)
    reach = _fake_reach({"reach_weibo_hot": {"ok": True, "text": "1. 石油 热度:50000\n  https://s.weibo.com/o"}})
    out = collect.collect_news("盘前", sources_path=path, reach=reach)
    assert list(out["sources"]) == ["微博"]
    assert "partial" not in out  # 禁用源不算失败


# ---- recipe 注册 ----

def test_news_recipe_registered_read_only():
    assert "news_collect" in r.RECIPES
    assert r.RECIPES["news_collect"]["write"] is False
    assert r.RECIPES["news_collect"]["args"] == ["scene"]
