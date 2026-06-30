"""U1: 美股对标取数 + 缓存 + 降级测试 (全 mock, 不触网)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from kss.perilla_enrich import us_peer


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "us_peer_cache"


def test_happy_fetch_and_cache(monkeypatch, cache_dir: Path) -> None:
    calls = []

    def fake_live(ticker: str) -> dict:
        calls.append(ticker)
        return {"pe": 25.0, "market_cap": 5.0e11, "price": 426.6, "currency": "USD"}

    monkeypatch.setattr(us_peer, "_fetch_live", fake_live)
    r = us_peer.fetch_us_peer("lrcx", cache_dir=cache_dir, today=date(2026, 6, 30))

    assert r["status"] == "ok"
    assert r["ticker"] == "LRCX"
    assert r["pe"] == 25.0 and r["market_cap"] == 5.0e11
    assert r["as_of"] == "2026-06-30"
    # 写了缓存文件
    assert (cache_dir / "LRCX.json").exists()
    assert calls == ["LRCX"]


def test_cache_hit_does_not_hit_network(monkeypatch, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / "LRCX.json").write_text(
        json.dumps({"status": "ok", "ticker": "LRCX", "pe": 20.0, "as_of": "2026-06-30"}),
        encoding="utf-8",
    )

    def boom(ticker: str) -> dict:  # 不应被调用
        raise AssertionError("network should not be hit on fresh cache")

    monkeypatch.setattr(us_peer, "_fetch_live", boom)
    r = us_peer.fetch_us_peer("LRCX", cache_dir=cache_dir, today=date(2026, 6, 30))
    assert r["pe"] == 20.0


def test_stale_cache_refetches(monkeypatch, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / "LRCX.json").write_text(
        json.dumps({"status": "ok", "ticker": "LRCX", "pe": 20.0, "as_of": "2026-06-01"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        us_peer, "_fetch_live",
        lambda t: {"pe": 99.0, "market_cap": 1.0, "price": 1.0, "currency": "USD"},
    )
    r = us_peer.fetch_us_peer("LRCX", cache_dir=cache_dir, today=date(2026, 6, 30), max_age_days=1)
    assert r["pe"] == 99.0  # 旧缓存过期 → 重拉


def test_none_ticker_no_peer(monkeypatch, cache_dir: Path) -> None:
    monkeypatch.setattr(us_peer, "_fetch_live", lambda t: pytest.fail("should not touch network"))
    assert us_peer.fetch_us_peer(None, cache_dir=cache_dir)["status"] == "no_peer"
    assert us_peer.fetch_us_peer("", cache_dir=cache_dir)["status"] == "no_peer"


def test_network_failure_unavailable(monkeypatch, cache_dir: Path) -> None:
    def boom(ticker: str) -> dict:
        raise ConnectionError("no route to host")

    monkeypatch.setattr(us_peer, "_fetch_live", boom)
    r = us_peer.fetch_us_peer("LRCX", cache_dir=cache_dir, today=date(2026, 6, 30))
    assert r["status"] == "unavailable"
    assert "ConnectionError" in r["reason"]


def test_network_failure_falls_back_to_stale_cache(monkeypatch, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / "LRCX.json").write_text(
        json.dumps({"status": "ok", "ticker": "LRCX", "pe": 20.0, "as_of": "2026-01-01"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(us_peer, "_fetch_live", lambda t: (_ for _ in ()).throw(TimeoutError()))
    r = us_peer.fetch_us_peer("LRCX", cache_dir=cache_dir, today=date(2026, 6, 30))
    assert r["status"] == "ok" and r["stale"] is True and r["pe"] == 20.0


def test_pe_missing_but_mcap_present(monkeypatch, cache_dir: Path) -> None:
    monkeypatch.setattr(
        us_peer, "_fetch_live",
        lambda t: {"pe": None, "market_cap": 1.0e11, "price": 100.0, "currency": "USD"},
    )
    r = us_peer.fetch_us_peer("AMAT", cache_dir=cache_dir, today=date(2026, 6, 30))
    assert r["status"] == "ok" and r["pe"] is None and r["market_cap"] == 1.0e11
