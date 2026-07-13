"""U1: 美股对标取数 + 缓存 + 降级测试 (全 mock, 不触网)。

U15 割接：缓存从 storage/us_peer_cache/*.json 切到 kss.db perilla_enrich_cache
表（kind="us_peer"），db_path 直接注入（同 aggregate.py 的 DI 风格）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from kss.perilla_enrich import us_peer
from kss.storage.db import connect, ensure_schema


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "kss.db"


def _seed(db_path: Path, ticker: str, payload: dict, cached_at: str | None = None) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO perilla_enrich_cache (ts_code, kind, payload, cached_at) VALUES (?,?,?,?)",
            (ticker, "us_peer", json.dumps(payload), cached_at or payload.get("as_of")),
        )


def _read_row(db_path: Path, ticker: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT payload FROM perilla_enrich_cache WHERE ts_code=? AND kind='us_peer'",
        (ticker,),
    ).fetchone()
    conn.close()
    return json.loads(row["payload"]) if row else None


def test_happy_fetch_and_cache(monkeypatch, db_path: Path) -> None:
    calls = []

    def fake_live(ticker: str) -> dict:
        calls.append(ticker)
        return {"pe": 25.0, "market_cap": 5.0e11, "price": 426.6, "currency": "USD"}

    monkeypatch.setattr(us_peer, "_fetch_live", fake_live)
    r = us_peer.fetch_us_peer("lrcx", db_path=db_path, today=date(2026, 6, 30))

    assert r["status"] == "ok"
    assert r["ticker"] == "LRCX"
    assert r["pe"] == 25.0 and r["market_cap"] == 5.0e11
    assert r["as_of"] == "2026-06-30"
    # 写了缓存行
    row = _read_row(db_path, "LRCX")
    assert row is not None and row["pe"] == 25.0
    assert calls == ["LRCX"]


def test_cache_hit_does_not_hit_network(monkeypatch, db_path: Path) -> None:
    _seed(db_path, "LRCX", {"status": "ok", "ticker": "LRCX", "pe": 20.0, "as_of": "2026-06-30"})

    def boom(ticker: str) -> dict:  # 不应被调用
        raise AssertionError("network should not be hit on fresh cache")

    monkeypatch.setattr(us_peer, "_fetch_live", boom)
    r = us_peer.fetch_us_peer("LRCX", db_path=db_path, today=date(2026, 6, 30))
    assert r["pe"] == 20.0


def test_stale_cache_refetches(monkeypatch, db_path: Path) -> None:
    _seed(db_path, "LRCX", {"status": "ok", "ticker": "LRCX", "pe": 20.0, "as_of": "2026-06-01"})
    monkeypatch.setattr(
        us_peer, "_fetch_live",
        lambda t: {"pe": 99.0, "market_cap": 1.0, "price": 1.0, "currency": "USD"},
    )
    r = us_peer.fetch_us_peer("LRCX", db_path=db_path, today=date(2026, 6, 30), max_age_days=1)
    assert r["pe"] == 99.0  # 旧缓存过期 → 重拉


def test_none_ticker_no_peer(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr(us_peer, "_fetch_live", lambda t: pytest.fail("should not touch network"))
    assert us_peer.fetch_us_peer(None, db_path=db_path)["status"] == "no_peer"
    assert us_peer.fetch_us_peer("", db_path=db_path)["status"] == "no_peer"


def test_network_failure_unavailable(monkeypatch, db_path: Path) -> None:
    def boom(ticker: str) -> dict:
        raise ConnectionError("no route to host")

    monkeypatch.setattr(us_peer, "_fetch_live", boom)
    r = us_peer.fetch_us_peer("LRCX", db_path=db_path, today=date(2026, 6, 30))
    assert r["status"] == "unavailable"
    assert "ConnectionError" in r["reason"]


def test_network_failure_falls_back_to_stale_cache(monkeypatch, db_path: Path) -> None:
    _seed(db_path, "LRCX", {"status": "ok", "ticker": "LRCX", "pe": 20.0, "as_of": "2026-01-01"})
    monkeypatch.setattr(us_peer, "_fetch_live", lambda t: (_ for _ in ()).throw(TimeoutError()))
    r = us_peer.fetch_us_peer("LRCX", db_path=db_path, today=date(2026, 6, 30))
    assert r["status"] == "ok" and r["stale"] is True and r["pe"] == 20.0


def test_pe_missing_but_mcap_present(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr(
        us_peer, "_fetch_live",
        lambda t: {"pe": None, "market_cap": 1.0e11, "price": 100.0, "currency": "USD"},
    )
    r = us_peer.fetch_us_peer("AMAT", db_path=db_path, today=date(2026, 6, 30))
    assert r["status"] == "ok" and r["pe"] is None and r["market_cap"] == 1.0e11


def test_db_path_none_skips_caching(monkeypatch) -> None:
    calls = []

    def fake_live(ticker: str) -> dict:
        calls.append(ticker)
        return {"pe": 1.0, "market_cap": 1.0, "price": 1.0, "currency": "USD"}

    monkeypatch.setattr(us_peer, "_fetch_live", fake_live)
    r1 = us_peer.fetch_us_peer("LRCX", db_path=None, today=date(2026, 6, 30))
    r2 = us_peer.fetch_us_peer("LRCX", db_path=None, today=date(2026, 6, 30))
    assert r1["status"] == "ok" and r2["status"] == "ok"
    assert calls == ["LRCX", "LRCX"]  # 无缓存 → 每次都触网


def test_fetch_returns_result_when_cache_write_lock_contended(monkeypatch, db_path: Path) -> None:
    """回归：sqlite3.OperationalError（写锁竞争）不是 OSError 子类，_write_cache 窄捕获
    会让它逃出 fetch_us_peer，紧跟的 return result 就执行不到，白白丢掉刚抓到的有效数据。"""
    monkeypatch.setattr(us_peer, "_fetch_live",
                         lambda t: {"pe": 25.0, "market_cap": 5.0e11, "price": 426.6, "currency": "USD"})
    monkeypatch.setattr(us_peer, "write_cache_entry",
                         lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    r = us_peer.fetch_us_peer("lrcx", db_path=db_path, today=date(2026, 6, 30))
    assert r["status"] == "ok"
    assert r["pe"] == 25.0
