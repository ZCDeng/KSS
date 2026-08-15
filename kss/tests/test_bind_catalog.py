"""Bind Catalog 生成与搜索。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from kss.ui_surface.bind_catalog import (
    CATALOG_VERSION,
    INDEX_BOARD_NAMES,
    SLOT_INDEX_BOARD,
    SLOT_OVERNIGHT,
    SLOT_STRIP,
    build_catalog,
    load_catalog,
    search,
)
from kss.ui_surface.config import DEFAULT_INDEX_BOARD_CODES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_build_catalog_has_core_metrics_and_us() -> None:
    cat = build_catalog(include_cn=False)
    assert cat["version"] == CATALOG_VERSION
    assert "metric_hot" in cat["domains_online"]
    assert "equity_us" in cat["domains_online"]
    assert "equity_hk" in cat["domains_online"]
    ids = {i["id"] for i in cat["items"]}
    assert "metric.limit_seal_rate" in ids
    assert "metric.index_a50" in ids
    assert "metric.index_sse" in ids
    assert any(i.get("codes", {}).get("code") == "AAPL" for i in cat["items"])


def test_search_strip_seal() -> None:
    cat = build_catalog(include_cn=False)
    r = search(SLOT_STRIP, "封板", catalog=cat)
    assert r["ok"] is True
    assert r["items"]
    assert r["items"][0]["metric_id"] == "limit_seal_rate"


def test_search_overnight_apple() -> None:
    cat = build_catalog(include_cn=False)
    r = search(SLOT_OVERNIGHT, "苹果", catalog=cat)
    assert r["ok"] is True
    assert any(i.get("codes", {}).get("code") == "AAPL" for i in r["items"])


def test_north_bindable_on_strip() -> None:
    """KTD2：四槽模型下北向是可替换 strip 指标，picker 必须能搜到。

    旧断言叫 test_north_not_on_strip，锁的是 R6「allowed_slots 门闩；北向禁
    strip_metric」。该约束已被 2026-07-31 的 KTD2 明文废止（「解除北向禁 strip
    以实现四槽语义……Governs R6」），同日 43b97b72 落地时把 north_money 加进了
    HOT_METRICS，却漏改本文件——全仓其余三处相关断言当时都已翻面。
    """
    cat = build_catalog(include_cn=False)
    r = search(SLOT_STRIP, "北向", catalog=cat)
    assert r["ok"] is True
    assert r["items"], "北向是默认 strip 槽之一，picker 搜不到会导致用户无法改绑"
    assert r["items"][0]["metric_id"] == "north_money"


def test_default_strip_slots_all_findable_in_picker() -> None:
    """四个默认 strip 槽都得能在 picker 里搜到，否则盘面显示了却改不了绑。"""
    from kss.ui_surface.config import DEFAULT_STRIP_SLOTS

    cat = build_catalog(include_cn=False)
    for mid in DEFAULT_STRIP_SLOTS:
        r = search(SLOT_STRIP, mid, catalog=cat)
        assert r["items"], f"默认槽 {mid} 在 picker 中不可寻"


def test_every_slot_has_a_directory() -> None:
    """三个槽位都得有可绑项。

    回归 2026-07-31~08-15：_metric_items() 给每项写死 allowed_slots=[SLOT_STRIP]，
    没有任何一项带 index_board，槽位直方图长期是
    {'overnight_marquee': 42, 'strip_metric': 12}，index_board 恒 0。
    NL 侧的 _DEFAULT_NAMES 和 Swift 侧的 defaultChoices 两处硬编码把空目录兜住了，
    所以两周没人发现。
    """
    cat = build_catalog(include_cn=False)
    histogram: dict[str, int] = {}
    for item in cat["items"]:
        for slot in item.get("allowed_slots") or []:
            histogram[slot] = histogram.get(slot, 0) + 1
    for slot in (SLOT_OVERNIGHT, SLOT_STRIP, SLOT_INDEX_BOARD):
        assert histogram.get(slot), f"槽位 {slot} 目录为空，picker 只能吃硬编码兜底"


def test_index_board_covers_config_defaults() -> None:
    """index_board 目录 == DEFAULT_INDEX_BOARD_CODES（配置侧码集真源）。"""
    cat = build_catalog(include_cn=False)
    r = search(SLOT_INDEX_BOARD, "", catalog=cat)
    assert r["ok"] is True
    got = {(i.get("codes") or {}).get("code") for i in r["items"]}
    assert got == {c.upper() for c in DEFAULT_INDEX_BOARD_CODES}
    assert r["total"] == len(DEFAULT_INDEX_BOARD_CODES)


def test_index_board_names_cover_defaults() -> None:
    """每个默认码都得有中文名；缺名只会渲染成裸码 picker。"""
    missing = [c for c in DEFAULT_INDEX_BOARD_CODES if c.upper() not in INDEX_BOARD_NAMES]
    assert not missing, f"这些码没配展示名：{missing}"
    assert set(INDEX_BOARD_NAMES) == {c.upper() for c in DEFAULT_INDEX_BOARD_CODES}


def test_index_board_matches_refresh_fetcher() -> None:
    """picker 只提供抓取脚本真会取价的码。

    ``effective_index_board_quotes`` 找不到价就返回 close=None 的骨架行，所以
    不在 refresh_market_strip.INDEX_BOARD 里的码绑上去只会是一行空的。
    """
    path = PROJECT_ROOT / "scripts" / "refresh_market_strip.py"
    spec = importlib.util.spec_from_file_location("refresh_market_strip_ut", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["refresh_market_strip_ut"] = mod
    spec.loader.exec_module(mod)

    fetched = {code.upper() for code, _name in mod.INDEX_BOARD}
    assert {c.upper() for c in DEFAULT_INDEX_BOARD_CODES} == fetched
    for code, name in mod.INDEX_BOARD:
        assert INDEX_BOARD_NAMES[code.upper()][0] == name, f"{code} 展示名与抓取表不一致"


def test_index_board_search_by_name() -> None:
    """按名字搜得中，且短名不被同族长名压过。"""
    cat = build_catalog(include_cn=False)
    for q, expect in (
        ("中证1000", "000852.SH"),
        ("北证50", "899050.BJ"),
        ("科创综指", "000680.SH"),
        ("上证", "000001.SH"),      # 不能被「上证50」抢走
        ("科创", "000688.SH"),      # 不能被「科创100/综指」抢走
        ("中证500", "000905.SH"),   # 不能被「中证A500」抢走
        ("932000.CSI", "932000.CSI"),
    ):
        r = search(SLOT_INDEX_BOARD, q, catalog=cat)
        assert r["items"], f"index_board 搜不到「{q}」"
        assert r["items"][0]["codes"]["code"] == expect, q


def test_slots_do_not_leak_across_addressing() -> None:
    """strip 按 metric_id 绑、index_board 按 code 绑，两套寻址不能串。"""
    cat = build_catalog(include_cn=False)
    board = search(SLOT_INDEX_BOARD, "", catalog=cat)["items"]
    assert all(not i["id"].startswith("metric.") for i in board)
    assert all(i.get("index_code") for i in board)
    # index_a50 的 XIN9 不在 indexBoard 抓取表里，不该出现在指数一览
    assert all(i["codes"]["code"] != "XIN9" for i in board)
    strip = search(SLOT_STRIP, "", catalog=cat)["items"]
    assert all(i["id"].startswith("metric.") for i in strip)


def test_stale_materialized_catalog_is_rebuilt(tmp_path: Path, monkeypatch) -> None:
    """老版本物化文件不能被当成有效缓存返回，否则代码改了线上读旧 JSON。"""
    import json

    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    from kss.ui_surface.bind_catalog import catalog_path

    path = catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": CATALOG_VERSION - 1, "items": [], "domains_online": []}),
        encoding="utf-8",
    )
    cat = load_catalog(rebuild_if_missing=True)
    assert cat["version"] == CATALOG_VERSION
    assert search(SLOT_INDEX_BOARD, "", catalog=cat)["total"] == len(
        DEFAULT_INDEX_BOARD_CODES
    )


def test_degraded_catalog_still_has_index_board(tmp_path: Path, monkeypatch) -> None:
    """rebuild_if_missing=False 的降级目录也要带 index_board，且 item_count 不撒谎。"""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    cat = load_catalog(rebuild_if_missing=False)
    assert cat["item_count"] == len(cat["items"])
    assert search(SLOT_INDEX_BOARD, "", catalog=cat)["total"] == len(
        DEFAULT_INDEX_BOARD_CODES
    )


def test_bad_slot() -> None:
    r = search("nope", "苹果", catalog=build_catalog(include_cn=False))
    assert r["ok"] is False
    assert r["error"] == "bad_slot"


def test_load_catalog_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    cat = load_catalog(rebuild_if_missing=True)
    assert cat["items"]
    path = tmp_path / "storage" / "ui_surface" / f"bind_catalog_v{CATALOG_VERSION}.json"
    assert path.is_file()
