"""U1: intraday-bars live→local 降级与会话 cache。"""
from __future__ import annotations

import json
from pathlib import Path

import scripts.kss_app_bridge as b


def test_aggregate_bars_to_interval():
    bars = [
        {
            "time": f"2026-07-10T09:{i:02d}:00+08:00",
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
        }
        for i in range(10)
    ]
    agg = b._aggregate_bars_to_interval(bars, 5)
    assert len(agg) == 2
    assert agg[0]["open"] == 1.0
    assert agg[0]["close"] == 1.5


def test_session_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kss.config.paths.STORAGE_ROOT",
        tmp_path,
        raising=False,
    )
    # 直接 monkeypatch 路径 helper
    def _path(symbol: str, interval_minutes: int) -> Path:
        d = tmp_path / "intraday_session_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{symbol}_{interval_minutes}m.json"

    monkeypatch.setattr(b, "_intraday_session_cache_path", _path)
    bars = [
        {
            "time": "2026-07-10T14:55:00+08:00",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
    ]
    b._save_intraday_session_cache("688017.SH", 1, bars, "2026-07-10")
    loaded, sd = b._load_local_session_bars("688017.SH", 1)
    assert len(loaded) == 1
    assert sd == "2026-07-10"
    assert loaded[0]["close"] == 10.5


def test_infer_session_date():
    assert (
        b._infer_session_date_from_bars(
            [{"time": "2026-07-09T15:00:00+08:00", "close": 1}]
        )
        == "2026-07-09"
    )


def test_expected_bars():
    assert b._intraday_expected_bars(1) == 240
    assert b._intraday_expected_bars(5) == 48


def test_prioritize_watchlist(tmp_path, monkeypatch):
    from scripts import collect_intraday as c

    wl = tmp_path / "watchlist_symbols.txt"
    wl.write_text("B.SH\nA.SH\n", encoding="utf-8")
    monkeypatch.setattr(
        c,
        "_load_watchlist_symbols",
        lambda: ["B.SH", "A.SH"],
    )
    symbols = [("X.SH", "stock"), ("A.SH", "stock"), ("B.SH", "stock"), ("Y.SH", "stock")]
    ordered = c._prioritize_watchlist(symbols)
    assert [s for s, _ in ordered[:2]] == ["B.SH", "A.SH"]
    assert {s for s, _ in ordered[2:]} == {"X.SH", "Y.SH"}
