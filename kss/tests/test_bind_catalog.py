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


def test_north_not_on_strip() -> None:
    cat = build_catalog(include_cn=False)
    r = search(SLOT_STRIP, "北向", catalog=cat)
    assert r["ok"] is True
    assert r["items"] == []


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
