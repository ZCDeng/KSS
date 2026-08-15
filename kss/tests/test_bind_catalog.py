"""Bind Catalog 生成与搜索。"""

from __future__ import annotations

from pathlib import Path

from kss.ui_surface.bind_catalog import (
    SLOT_OVERNIGHT,
    SLOT_STRIP,
    build_catalog,
    load_catalog,
    search,
)


def test_build_catalog_has_core_metrics_and_us() -> None:
    cat = build_catalog(include_cn=False)
    assert cat["version"] == 1
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


def test_bad_slot() -> None:
    r = search("nope", "苹果", catalog=build_catalog(include_cn=False))
    assert r["ok"] is False
    assert r["error"] == "bad_slot"


def test_load_catalog_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    cat = load_catalog(rebuild_if_missing=True)
    assert cat["items"]
    path = tmp_path / "storage" / "ui_surface" / "bind_catalog_v1.json"
    assert path.is_file()
