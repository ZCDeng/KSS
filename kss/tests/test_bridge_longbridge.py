"""U4 测试：Longbridge 只读 bridge 命令（R5 / KTD3 / KTD4）.

- 只读性钉死：两命令 ∉ WRITE_COMMANDS ⇒ 经 _make_read_only_call 不 raise。
- 数字纪律：命令返回**真值字段**（非拼好的自然语言）。
- 能力错配：longbridge-quote 对东财路由标的返回结构化 error。
- 路由：按 route_provider 选源（mock provider，不 live）。
跑：.venv/bin/python -m pytest kss/tests/test_bridge_longbridge.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402

from kss.data.intraday_client import FetchResult  # noqa: E402


def _ok_quote_result():
    return FetchResult(
        rows=[{
            "symbol": "688008.SH", "last_done": 253.2, "prev_close": 250.0,
            "open": 251.0, "high": 254.0, "low": 250.5, "volume": 12345,
            "turnover": 3.1e6, "timestamp": None, "trade_status": "Normal",
        }],
        raw_columns=("symbol", "last_done"),
        source_asof_ts="2026-07-08T15:00:00+08:00",
        status_code=200, latency_ms=10.0, error=None,
    )


def _ok_bar_result():
    return FetchResult(
        rows=[
            {"timestamp": None, "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1, "volume": 1000, "turnover": 1e6},
            {"timestamp": None, "open": 10.1, "high": 10.3, "low": 10.0, "close": 10.25, "volume": 1500, "turnover": 1.5e6},
        ],
        raw_columns=("timestamp", "open", "high", "low", "close", "volume", "turnover"),
        source_asof_ts="2026-07-08T15:00:00+08:00",
        status_code=200, latency_ms=12.0, error=None,
    )


class _FakeLongbridge:
    name = "longbridge"

    def __init__(self, *, quote=None, bars=None):
        self._quote = quote
        self._bars = bars

    def fetch_quote(self, symbol):
        return self._quote

    def fetch_bars(self, symbol, *, interval_minutes, asset_kind, start=None, end=None):
        return self._bars


class _FakeEastmoney:
    name = "eastmoney_akshare"

    def __init__(self, *, bars=None):
        self._bars = bars

    def fetch_bars(self, symbol, *, interval_minutes, asset_kind, start=None, end=None):
        return self._bars


# --------------------------------------------------------------------------- #
# 只读性 + 漂移守卫（KTD3）
# --------------------------------------------------------------------------- #


def test_new_commands_registered_and_not_write():
    """两命令登记进 COMMANDS 且 ∉ WRITE_COMMANDS（只读性钉死）。"""
    assert "longbridge-quote" in b.COMMANDS
    assert "intraday-snapshot" in b.COMMANDS
    assert "longbridge-quote" not in b.WRITE_COMMANDS
    assert "intraday-snapshot" not in b.WRITE_COMMANDS


def test_read_only_call_does_not_raise_on_new_commands(monkeypatch):
    """经 _make_read_only_call 调新命令不 raise PermissionError（只读路径）。"""
    monkeypatch.setattr(
        b, "_longbridge_quote", lambda s: {"symbol": s, "last_done": 1.0}
    )
    call = b._make_read_only_call(b.dispatch)
    out = call("longbridge-quote", ["688008.SH"])
    assert out["last_done"] == 1.0


# --------------------------------------------------------------------------- #
# longbridge-quote：covered 走 longbridge、东财路由标的返回 error（能力错配）
# --------------------------------------------------------------------------- #


def test_longbridge_quote_covered_returns_true_value_fields(monkeypatch):
    # 用 import 注入 fake（_longbridge_quote 内部 import LongbridgeProvider）。
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "LongbridgeProvider", lambda: _FakeLongbridge(quote=_ok_quote_result()))
    out = b.dispatch("longbridge-quote", ["688008.SH"])
    # 真值字段直接透传（number_guard 可核），非自然语言。
    assert out["last_done"] == 253.2
    assert out["symbol"] == "688008.SH"
    assert out["eligibility"] == "forward_observed"
    assert out["routed_provider"] == "longbridge"


def test_longbridge_quote_eastmoney_routed_returns_error(monkeypatch):
    """东财路由标的（北交所）→ 结构化 no_realtime_snapshot error（东财无 fetch_quote）。"""
    out = b.dispatch("longbridge-quote", ["830799.BJ"])
    assert out["error"] == "no_realtime_snapshot"
    assert out["routed_provider"] == "eastmoney_akshare"


def test_longbridge_quote_requires_symbol():
    import pytest
    with pytest.raises(ValueError):
        b.dispatch("longbridge-quote", [])


# --------------------------------------------------------------------------- #
# intraday-snapshot：按路由选源，返回真值 bar 行
# --------------------------------------------------------------------------- #


def test_intraday_snapshot_covered_uses_longbridge(monkeypatch):
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "LongbridgeProvider", lambda: _FakeLongbridge(bars=_ok_bar_result()))
    out = b.dispatch("intraday-snapshot", ["688008.SH"])
    # 末行为最新 bar，整行真值透传。
    assert out["bar"]["close"] == 10.25
    assert out["routed_provider"] == "longbridge"
    assert out["eligibility"] == "forward_observed"


def test_intraday_snapshot_beijing_routes_eastmoney_honest_error(monkeypatch):
    """北交所 → 东财源；东财本机不可达 → 诚实 error（无数据非错数据，KTD6）。"""
    err = FetchResult(
        rows=[], raw_columns=(), source_asof_ts=None,
        status_code=None, latency_ms=5.0, error="unreachable",
    )
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "EastmoneyAkshareProvider", lambda: _FakeEastmoney(bars=err))
    out = b.dispatch("intraday-snapshot", ["830799.BJ"])
    assert out["routed_provider"] == "eastmoney_akshare"
    assert "error" in out
    assert "不可达" in out["hint"]


# --------------------------------------------------------------------------- #
# U0: intraday-bars（完整日内序列，F006）+ trading-hours（F007）+ 落盘降级（F009）
# --------------------------------------------------------------------------- #


def test_u0_new_commands_registered_and_not_write():
    """U0 两命令登记 COMMANDS 且 ∉ WRITE_COMMANDS（只读性钉死）。"""
    assert "intraday-bars" in b.COMMANDS
    assert "trading-hours" in b.COMMANDS
    assert "intraday-bars" not in b.WRITE_COMMANDS
    assert "trading-hours" not in b.WRITE_COMMANDS


def test_intraday_bars_returns_full_series(monkeypatch):
    """F006：intraday-bars 返回**全序列**（非单 bar），K 线图可消费。"""
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "LongbridgeProvider", lambda: _FakeLongbridge(bars=_ok_bar_result()))
    out = b.dispatch("intraday-bars", ["688008.SH"])
    # 全序列：2 根 bar 都在（对比 intraday-snapshot 只返回末行）。
    assert "bars" in out
    assert len(out["bars"]) == 2
    assert out["bars"][0]["close"] == 10.1
    assert out["bars"][1]["close"] == 10.25
    assert out["routed_provider"] == "longbridge"
    assert out["eligibility"] == "forward_observed"


def test_intraday_bars_beijing_empty_series(monkeypatch):
    """北交所 → 东财不可达 → 空序列 + error（非覆盖诚实语义）。"""
    err = FetchResult(
        rows=[], raw_columns=(), source_asof_ts=None,
        status_code=None, latency_ms=5.0, error="unreachable",
    )
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "EastmoneyAkshareProvider", lambda: _FakeEastmoney(bars=err))
    out = b.dispatch("intraday-bars", ["830799.BJ"])
    assert out["bars"] == []
    assert "error" in out
    assert out["routed_provider"] == "eastmoney_akshare"


def test_intraday_bars_requires_symbol():
    import pytest
    with pytest.raises(ValueError):
        b.dispatch("intraday-bars", [])


def test_trading_hours_shape(monkeypatch):
    """trading-hours 返回门控三字段（is_trade_day / is_trading_session / session_end）。"""
    # mock _is_trade_day 避免打真网（Tushare）。
    monkeypatch.setattr(b, "_is_trade_day", lambda d: True)
    out = b.dispatch("trading-hours", [])
    assert "is_trade_day" in out
    assert "is_trading_session" in out
    assert out["session_end"] == "15:05"
    assert out["is_trade_day"] is True


def test_trading_hours_non_trade_day(monkeypatch):
    """非交易日 → is_trading_session 必 False（即便在时段窗内）。"""
    monkeypatch.setattr(b, "_is_trade_day", lambda d: False)
    out = b.dispatch("trading-hours", [])
    assert out["is_trade_day"] is False
    assert out["is_trading_session"] is False


def test_persist_page_pull_is_noop_degrade_path():
    """F009：R5 落盘采用 plan 预授权降级路径——no-op，不抛，不写 store。"""
    # 不应抛异常，且返回 None（降级：跳过落盘）。
    result = b._persist_page_pull("688008.SH", "longbridge", 1, "stock",
                                  [{"close": 10.0}])
    assert result is None


# --------------------------------------------------------------------------- #
# U1: Swift Codable model ↔ bridge JSON 契约一致性（防字段漂移）
# --------------------------------------------------------------------------- #


def test_longbridge_quote_json_contract_matches_swift_model(monkeypatch):
    """U1：longbridge-quote 返回 JSON 含 Swift LongbridgeQuote 期望的所有 key。"""
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "LongbridgeProvider", lambda: _FakeLongbridge(quote=_ok_quote_result()))
    out = b.dispatch("longbridge-quote", ["688008.SH"])
    # Swift LongbridgeQuote CodingKeys 期望的字段（snake_case）。
    for key in ("symbol", "last_done", "prev_close", "open", "high", "low",
                "volume", "turnover", "trade_status", "source_asof_ts",
                "eligibility", "routed_provider", "manifest_stale"):
        assert key in out, f"missing key expected by Swift model: {key}"


def test_intraday_bars_json_contract_matches_swift_model(monkeypatch):
    """U1：intraday-bars 返回 JSON 含 Swift IntradayBars 期望的 key（bars 全序列）。"""
    import kss.data.intraday_client as ic
    monkeypatch.setattr(ic, "LongbridgeProvider", lambda: _FakeLongbridge(bars=_ok_bar_result()))
    out = b.dispatch("intraday-bars", ["688008.SH"])
    for key in ("symbol", "interval_minutes", "bars", "source_asof_ts",
                "eligibility", "routed_provider", "manifest_stale"):
        assert key in out, f"missing key expected by Swift model: {key}"
    # bars 元素含 OHLCBar 期望字段。
    assert out["bars"], "bars should be non-empty"
    for key in ("open", "high", "low", "close", "volume"):
        assert key in out["bars"][0], f"OHLCBar missing: {key}"


def test_trading_hours_json_contract_matches_swift_model(monkeypatch):
    """U1：trading-hours 返回 JSON 含 Swift TradingHours 期望的 key。"""
    monkeypatch.setattr(b, "_is_trade_day", lambda d: True)
    out = b.dispatch("trading-hours", [])
    for key in ("is_trade_day", "is_trading_session", "session_end"):
        assert key in out, f"missing key expected by Swift model: {key}"
