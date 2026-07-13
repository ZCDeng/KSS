"""U5: 富化聚合器测试 — 三块独立降级 (mock client + us_peer, 不触网)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from kss.perilla_enrich import aggregate
from kss.supply_chain.registry import ChainRegistry

TODAY = date(2026, 6, 30)


class _FakeClient:
    def __init__(self, holders=None, pe=None, raise_on=None):
        self._holders = holders
        self._pe = pe
        self._raise_on = raise_on or set()

    def fetch_top10_floatholders(self, *a, **k):
        if "holders" in self._raise_on:
            raise ConnectionError("tushare down")
        return self._holders

    def fetch_daily_basic_history(self, *a, **k):
        if "pe" in self._raise_on:
            raise ConnectionError("tushare down")
        return self._pe


def _holders_df():
    return pd.DataFrame([
        {"end_date": "20260331", "holder_name": "香港中央结算有限公司", "hold_ratio": 10.5,
         "hold_change": 1e7, "holder_type": "一般企业"},
        {"end_date": "20251231", "holder_name": "香港中央结算有限公司", "hold_ratio": 9.0,
         "hold_change": 0, "holder_type": "一般企业"},
        {"end_date": "20260331", "holder_name": "某基金", "hold_ratio": 5.0,
         "hold_change": 2e6, "holder_type": "基金"},
    ])


def _pe_df():
    return pd.DataFrame({
        "trade_date": [f"202{y}{m:02d}15" for y in (4, 5, 6) for m in range(1, 13)],
        "pe_ttm": [float(x) for x in range(36)],
        "total_mv": [3.0e6] * 36,   # 万元 = 300 亿
    })


def _reg() -> ChainRegistry:
    return ChainRegistry.from_yaml()


def _a_perilla_symbol_with_peer(reg) -> str:
    for c in reg._stocks:
        if reg.tier(c) in ("core", "main") and reg.get(c).us_peer_ticker:
            return c
    raise AssertionError("no perilla symbol with peer in registry")


def test_not_in_perilla_list_short_circuits(monkeypatch) -> None:
    # 终端票(如 300002 神州泰岳)不在 core/main → 不触任何取数
    def boom(*a, **k):
        raise AssertionError("should not fetch")

    monkeypatch.setattr(aggregate._us, "fetch_us_peer", boom)
    out = aggregate.enrich("300002.SZ", client=_FakeClient(raise_on={"holders", "pe"}))
    assert out["status"] == "not_in_perilla_list"


def test_happy_all_three_blocks(monkeypatch) -> None:
    reg = _reg()
    sym = _a_perilla_symbol_with_peer(reg)
    monkeypatch.setattr(aggregate._us, "fetch_us_peer",
                        lambda t, **k: {"status": "ok", "ticker": t, "pe": 20.0,
                                        "market_cap": 5.0e11, "price": 100.0, "currency": "USD"})
    monkeypatch.setattr(aggregate._us, "fetch_usdcny", lambda **k: 7.1)

    out = aggregate.enrich(sym, registry=reg, today=TODAY,
                           client=_FakeClient(holders=_holders_df(), pe=_pe_df()))
    assert out["status"] == "ok"
    assert out["institutional"]["top10"]["status"] == "ok"
    assert out["institutional"]["northbound"]["direction"] == "increasing"
    assert out["valuation_pe"]["status"] == "ok"
    up = out["us_peer"]
    assert up["status"] == "ok" and up["peer_pe"] == 20.0
    assert up["pe_ratio_a_over_peer"] == round(35.0 / 20.0, 2)
    assert up["peer_to_a_mcap_multiple"] is not None  # 汇率可得 → 倍数算出


def test_no_peer_degrades_only_us_block(monkeypatch) -> None:
    # 选一只无对标的 core/main 票
    reg = _reg()
    sym = next(c for c in reg._stocks
               if reg.tier(c) in ("core", "main") and not reg.get(c).us_peer_ticker)
    monkeypatch.setattr(aggregate._us, "fetch_us_peer", lambda t, **k: {"status": "no_peer"})
    out = aggregate.enrich(sym, registry=reg, today=TODAY,
                           client=_FakeClient(holders=_holders_df(), pe=_pe_df()))
    assert out["us_peer"]["status"] == "no_peer"
    assert out["institutional"]["top10"]["status"] == "ok"  # 其他块不受影响
    assert out["valuation_pe"]["status"] == "ok"


def test_tushare_failure_isolated(monkeypatch) -> None:
    # 集成场景: Tushare 机构块抛错 → 该块 unavailable, PE/美股块仍 ok
    reg = _reg()
    sym = _a_perilla_symbol_with_peer(reg)
    monkeypatch.setattr(aggregate._us, "fetch_us_peer",
                        lambda t, **k: {"status": "ok", "ticker": t, "pe": 20.0,
                                        "market_cap": 5.0e11, "price": 100.0})
    monkeypatch.setattr(aggregate._us, "fetch_usdcny", lambda **k: 7.1)
    out = aggregate.enrich(sym, registry=reg, today=TODAY,
                           client=_FakeClient(pe=_pe_df(), raise_on={"holders"}))
    assert out["institutional"]["status"] == "unavailable"   # 单源失败
    assert out["valuation_pe"]["status"] == "ok"             # 不污染
    assert out["us_peer"]["status"] == "ok"
    assert out["status"] == "ok"                              # 整体不抛


def test_cache_only_never_touches_network(monkeypatch, tmp_path) -> None:
    # cache_only=True: 缓存空 → 各块 unavailable, 且绝不调 client/yfinance
    reg = _reg()
    sym = _a_perilla_symbol_with_peer(reg)

    def boom_client(*a, **k):
        raise AssertionError("cache_only must not hit tushare")

    monkeypatch.setattr(aggregate._us, "fetch_us_peer",
                        lambda *a, **k: pytest.fail("cache_only must not hit yfinance")
                        if not k.get("cache_only") else {"status": "unavailable", "reason": "cache_miss"})
    out = aggregate.enrich(sym, registry=reg, today=TODAY, db_path=tmp_path / "kss.db",
                           client=_FakeClient(raise_on={"holders", "pe"}), cache_only=True)
    assert out["status"] == "ok"  # 整体不抛
    assert out["institutional"]["top10"]["status"] == "unavailable"
    assert out["valuation_pe"]["status"] == "unavailable"
    assert out["us_peer"]["status"] == "unavailable"


def test_fx_unavailable_keeps_pe_compare(monkeypatch) -> None:
    reg = _reg()
    sym = _a_perilla_symbol_with_peer(reg)
    monkeypatch.setattr(aggregate._us, "fetch_us_peer",
                        lambda t, **k: {"status": "ok", "ticker": t, "pe": 20.0,
                                        "market_cap": 5.0e11, "price": 100.0})
    monkeypatch.setattr(aggregate._us, "fetch_usdcny", lambda **k: None)  # 汇率不可得
    out = aggregate.enrich(sym, registry=reg, today=TODAY,
                           client=_FakeClient(holders=_holders_df(), pe=_pe_df()))
    up = out["us_peer"]
    assert up["pe_ratio_a_over_peer"] is not None          # PE 对比无需汇率
    assert up.get("mcap_multiple_status") == "fx_unavailable"
    assert "peer_to_a_mcap_multiple" not in up


def test_cached_df_returns_fresh_data_when_cache_write_lock_contended(monkeypatch, tmp_path):
    """回归：sqlite3.OperationalError（写锁竞争）不是 OSError 子类，缓存写失败的异常处理
    如果窄捕成 except OSError 会让它逃出 _cached_df，白白丢掉刚抓到的有效 df。"""
    import sqlite3

    def _boom_write(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(aggregate, "write_cache_entry", _boom_write)
    fresh = pd.DataFrame({"end_date": ["20260630"], "hold_ratio": [1.0]})
    df = aggregate._cached_df(
        "holders", "688017.SH", "20260630", lambda: fresh,
        tmp_path / "kss.db", TODAY,
    )
    assert df is not None
    assert df.equals(fresh)
