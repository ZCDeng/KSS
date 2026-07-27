"""美股行情核心服务测试（全 fake，不触网）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
from kss.data.us_market import (
    DEFAULT_US_MARKET_UNIVERSE,
    ProviderQuote,
    USMarketQuoteService,
    is_us_regular_session,
    market_phase_for_datetime,
)

NY = ZoneInfo("America/New_York")


class FakeLongbridgeProvider:
    def __init__(self, quotes: dict[str, ProviderQuote] | None = None, *, boom: bool = False) -> None:
        self.quotes = quotes or {}
        self.boom = boom
        self.calls: list[list[str]] = []

    def fetch_quotes(self, symbols: list[str]) -> dict[str, ProviderQuote]:
        self.calls.append(symbols)
        if self.boom:
            raise TimeoutError("lb timeout")
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


class FakeYFinanceProvider:
    def __init__(self, quotes: dict[str, ProviderQuote] | None = None, *, boom: set[str] | None = None) -> None:
        self.quotes = quotes or {}
        self.boom = boom or set()
        self.calls: list[str] = []

    def fetch_quote(self, symbol: str) -> ProviderQuote | None:
        self.calls.append(symbol)
        if symbol in self.boom:
            raise ConnectionError("yf down")
        return self.quotes.get(symbol)


def q(
    symbol: str,
    last: float,
    prev_close: float,
    source_as_of: datetime | None,
    provider: str = "longbridge",
    **metadata,
) -> ProviderQuote:
    return ProviderQuote(
        symbol=symbol,
        price=last,
        prev_close=prev_close,
        asof=source_as_of,
        provider=provider,
        metadata=metadata,
    )


def quote_map(rows: list) -> dict[str, object]:
    return {row.code: row for row in rows}


def test_bridge_registers_and_dispatches_us_market_as_read_only(monkeypatch) -> None:
    assert "us-market-quotes" in bridge.COMMANDS
    assert "us-market-quotes" not in bridge.WRITE_COMMANDS
    monkeypatch.setattr(
        bridge,
        "_us_market_quotes",
        lambda symbols="": {"symbols": symbols, "quotes": []},
    )

    assert bridge.dispatch("us-market-quotes", ["NVDA,IXIC"]) == {
        "symbols": "NVDA,IXIC",
        "quotes": [],
    }


def test_default_universe_has_nine_longbridge_symbols_and_static_xin9() -> None:
    longbridge = [row for row in DEFAULT_US_MARKET_UNIVERSE if row.route == "longbridge"]
    assert len(longbridge) == 9
    assert [row.code for row in longbridge] == [
        "MCHI",
        "ROBO",
        "BOTZ",
        "NVDA",
        "SOXX",
        "SMH",
        "TSLA",
        "MU",
        "AVGO",
    ]
    assert [row.yfinance_symbol for row in DEFAULT_US_MARKET_UNIVERSE if row.code in {"IXIC", "DJI"}] == [
        "^IXIC",
        "^DJI",
    ]
    assert next(row for row in DEFAULT_US_MARKET_UNIVERSE if row.code == "XIN9").route == "static"


def test_snapshot_wire_contract_and_coverage_are_json_serializable() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider({"NVDA.US": q("NVDA.US", 120.0, 100.0, now - timedelta(seconds=30))})
    yf = FakeYFinanceProvider({"^IXIC": q("^IXIC", 25000.0, 25100.0, now - timedelta(minutes=5), "yfinance")})
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    payload = svc.fetch_snapshot(["NVDA", "IXIC", "XIN9"]).to_dict()

    json.dumps(payload, ensure_ascii=False)
    assert set(payload) == {"quotes", "count", "market_phase", "received_at", "coverage"}
    assert payload["count"] == 3
    assert payload["market_phase"] == "regular"
    assert payload["coverage"] == {"live": 1, "delayed": 1, "stale": 0, "static": 1, "unavailable": 0}
    quote = payload["quotes"][0]
    assert set(quote) == {
        "code",
        "name",
        "last",
        "prev_close",
        "pct",
        "source",
        "source_as_of",
        "received_at",
        "market_phase",
        "status",
        "error",
    }


def test_longbridge_live_yfinance_delayed_and_xin9_static() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider({"NVDA.US": q("NVDA.US", 120.0, 100.0, now - timedelta(seconds=179))})
    yf = FakeYFinanceProvider({
        "^IXIC": q("^IXIC", 25000.0, 25100.0, now - timedelta(minutes=15), "yfinance"),
    })
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["NVDA", "IXIC", "XIN9"]))

    assert out["NVDA"].status == "live"
    assert out["NVDA"].last == 120.0
    assert out["NVDA"].prev_close == 100.0
    assert out["NVDA"].pct == 20.0
    assert out["NVDA"].source == "longbridge"
    assert out["NVDA"].source_as_of == (now - timedelta(seconds=179)).isoformat()
    assert out["NVDA"].received_at == now.isoformat()
    assert out["NVDA"].market_phase == "regular"
    assert out["IXIC"].status == "delayed"
    assert out["IXIC"].source == "yfinance"
    assert out["IXIC"].pct == pytest.approx(-0.4)
    assert out["XIN9"].status == "static"
    assert out["XIN9"].last is None
    assert lb.calls == [["NVDA.US"]]
    assert yf.calls == ["^IXIC"]


def test_longbridge_missing_or_incomplete_symbol_falls_back_to_yfinance_per_symbol() -> None:
    now = datetime(2026, 7, 27, 11, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider({
        "NVDA.US": q("NVDA.US", 130.0, 100.0, now, "longbridge"),
        "MU.US": ProviderQuote("MU.US", price=90.0, prev_close=None, asof=now, provider="longbridge"),
    })
    yf = FakeYFinanceProvider({
        "TSLA": q("TSLA", 220.0, 200.0, now - timedelta(minutes=1), "yfinance"),
        "MU": q("MU", 88.0, 80.0, now - timedelta(minutes=2), "yfinance"),
    })
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["NVDA", "TSLA", "MU"]))

    assert out["NVDA"].source == "longbridge"
    assert out["NVDA"].status == "live"
    assert out["TSLA"].source == "yfinance"
    assert out["TSLA"].last == 220.0
    assert out["MU"].source == "yfinance"
    assert out["MU"].last == 88.0
    assert yf.calls == ["TSLA", "MU"]


def test_provider_failure_marks_only_failed_symbols_unavailable() -> None:
    now = datetime(2026, 7, 27, 11, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider(boom=True)
    yf = FakeYFinanceProvider({
        "NVDA": q("NVDA", 120.0, 100.0, now - timedelta(minutes=1), "yfinance"),
    }, boom={"TSLA"})
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["NVDA", "TSLA", "XIN9"]))

    assert out["NVDA"].status == "delayed"
    assert out["NVDA"].source == "yfinance"
    assert out["TSLA"].status == "unavailable"
    assert out["TSLA"].error == "no_provider_quote"
    assert out["XIN9"].status == "static"


def test_price_and_prev_close_are_not_mixed_across_provider_snapshots() -> None:
    now = datetime(2026, 7, 27, 10, 30, tzinfo=NY)
    lb = FakeLongbridgeProvider({
        "NVDA.US": q("NVDA.US", 120.0, 100.0, now, "longbridge"),
        "TSLA.US": ProviderQuote("TSLA.US", price=210.0, prev_close=None, asof=now, provider="longbridge"),
    })
    yf = FakeYFinanceProvider({
        "TSLA": ProviderQuote("TSLA", price=None, prev_close=200.0, asof=now, provider="yfinance"),
    })
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["NVDA", "TSLA"]))

    assert out["NVDA"].last == 120.0
    assert out["NVDA"].prev_close == 100.0
    assert out["TSLA"].status == "unavailable"
    assert out["TSLA"].error == "incomplete_provider_quote"
    assert out["TSLA"].last is None
    assert out["TSLA"].prev_close is None


def test_market_phase_detection_and_calendar_injection() -> None:
    assert market_phase_for_datetime(datetime(2026, 7, 27, 3, 59, tzinfo=NY)) == "closed"
    assert market_phase_for_datetime(datetime(2026, 7, 27, 4, 0, tzinfo=NY)) == "pre"
    assert is_us_regular_session(datetime(2026, 7, 27, 9, 29, tzinfo=NY)) is False
    assert is_us_regular_session(datetime(2026, 7, 27, 9, 30, tzinfo=NY)) is True
    assert is_us_regular_session(datetime(2026, 7, 27, 15, 59, tzinfo=NY)) is True
    assert market_phase_for_datetime(datetime(2026, 7, 27, 16, 0, tzinfo=NY)) == "post"
    assert market_phase_for_datetime(datetime(2026, 8, 1, 10, 0, tzinfo=NY)) == "closed"

    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    svc = USMarketQuoteService(
        longbridge_provider=FakeLongbridgeProvider({"NVDA.US": q("NVDA.US", 120.0, 100.0, now)}),
        now_provider=lambda: now,
        market_calendar=lambda _now: "closed",
    )
    out = quote_map(svc.fetch_quotes(["NVDA"]))
    assert out["NVDA"].market_phase == "closed"
    assert out["NVDA"].status == "static"


def test_provider_metadata_market_phase_takes_priority() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider({
        "NVDA.US": q("NVDA.US", 120.0, 100.0, now - timedelta(seconds=1), "longbridge", trade_status="Closed"),
    })
    svc = USMarketQuoteService(longbridge_provider=lb, now_provider=lambda: now)

    snapshot = svc.fetch_snapshot(["NVDA", "XIN9"])
    out = quote_map(snapshot.quotes)

    assert out["NVDA"].market_phase == "closed"
    assert out["NVDA"].status == "static"
    assert snapshot.market_phase == "closed"


def test_freshness_statuses_use_provider_specific_thresholds() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider({
        "NVDA.US": q("NVDA.US", 120.0, 100.0, now - timedelta(seconds=180), "longbridge"),
        "TSLA.US": q("TSLA.US", 220.0, 200.0, now - timedelta(seconds=181), "longbridge"),
        "MU.US": q("MU.US", 80.0, 100.0, now - timedelta(minutes=16), "longbridge"),
    })
    yf = FakeYFinanceProvider({
        "MU": q("MU", 81.0, 100.0, now - timedelta(minutes=16), "yfinance"),
    })
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["NVDA", "TSLA", "MU"]))

    assert out["NVDA"].status == "live"
    assert out["TSLA"].status == "stale"
    assert out["TSLA"].error == "longbridge_stale"
    assert out["MU"].status == "stale"
    assert out["MU"].error == "longbridge_stale"


def test_yfinance_missing_source_as_of_is_stale_during_regular_not_now() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    yf = FakeYFinanceProvider({
        "^DJI": q("^DJI", 39000.0, 38900.0, None, "yfinance"),
    })
    svc = USMarketQuoteService(yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["DJI"]))

    assert out["DJI"].status == "stale"
    assert out["DJI"].source_as_of is None
    assert out["DJI"].received_at == now.isoformat()
    assert out["DJI"].error == "missing_source_as_of"


def test_after_hours_valid_quote_is_static_not_live_or_delayed() -> None:
    now = datetime(2026, 7, 27, 17, 0, tzinfo=NY)
    lb = FakeLongbridgeProvider({
        "NVDA.US": q("NVDA.US", 120.0, 100.0, now - timedelta(minutes=1), "longbridge"),
    })
    yf = FakeYFinanceProvider({
        "^IXIC": q("^IXIC", 25000.0, 25100.0, now - timedelta(minutes=1), "yfinance"),
    })
    svc = USMarketQuoteService(longbridge_provider=lb, yfinance_provider=yf, now_provider=lambda: now)

    out = quote_map(svc.fetch_quotes(["NVDA", "IXIC"]))

    assert out["NVDA"].market_phase == "post"
    assert out["NVDA"].status == "static"
    assert out["IXIC"].market_phase == "post"
    assert out["IXIC"].status == "static"
