"""Cache helpers tested against in-memory SQLite — no datasette required."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from datasette_kss_tushare import _cache


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def _select_all(conn, table):
    return list(conn.execute(f"SELECT * FROM {table}").fetchall())


# ---------- ensure_schema ----------

def test_ensure_schema_creates_three_tables(conn):
    _cache.ensure_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"daily", "stock_basic", "moneyflow_hsgt"} <= names


def test_ensure_schema_is_idempotent(conn):
    _cache.ensure_schema(conn)
    _cache.ensure_schema(conn)  # should not raise


# ---------- upsert_daily ----------

def test_upsert_daily_normalizes_yyyymmdd_to_iso(conn):
    df = pd.DataFrame({
        "ts_code": ["300750.SZ"],
        "trade_date": ["20260522"],
        "open": [180.0], "high": [185.0], "low": [179.0],
        "close": [184.0], "pre_close": [180.0],
        "change": [4.0], "pct_chg": [2.22],
        "vol": [1.2e6], "amount": [2.2e8],
    })
    assert _cache.upsert_daily(conn, df) == 1
    row = conn.execute("SELECT trade_date FROM daily").fetchone()
    assert row[0] == "2026-05-22"


def test_upsert_daily_preserves_iso_dates(conn):
    df = pd.DataFrame({
        "ts_code": ["300750.SZ"],
        "trade_date": ["2026-05-22"],
        "close": [184.0],
    })
    _cache.upsert_daily(conn, df)
    row = conn.execute("SELECT trade_date FROM daily").fetchone()
    assert row[0] == "2026-05-22"


def test_upsert_daily_pads_missing_columns_with_null(conn):
    """tushare's daily endpoint doesn't return turnover_rate/pe/pb/total_mv."""
    df = pd.DataFrame({
        "ts_code": ["300750.SZ"],
        "trade_date": ["20260522"],
        "open": [180.0], "high": [185.0], "low": [179.0],
        "close": [184.0], "pre_close": [180.0],
        "change": [4.0], "pct_chg": [2.22],
        "vol": [1.2e6], "amount": [2.2e8],
    })
    _cache.upsert_daily(conn, df)
    pe, pb, total_mv = conn.execute(
        "SELECT pe, pb, total_mv FROM daily"
    ).fetchone()
    assert pe is None and pb is None and total_mv is None


def test_upsert_daily_is_idempotent_on_same_key(conn):
    df = pd.DataFrame({
        "ts_code": ["300750.SZ"], "trade_date": ["20260522"], "close": [184.0],
    })
    _cache.upsert_daily(conn, df)
    _cache.upsert_daily(conn, df)
    assert len(_select_all(conn, "daily")) == 1


def test_upsert_daily_replaces_on_conflict(conn):
    df1 = pd.DataFrame({
        "ts_code": ["300750.SZ"], "trade_date": ["20260522"], "close": [184.0],
    })
    df2 = pd.DataFrame({
        "ts_code": ["300750.SZ"], "trade_date": ["20260522"], "close": [200.0],
    })
    _cache.upsert_daily(conn, df1)
    _cache.upsert_daily(conn, df2)
    rows = _select_all(conn, "daily")
    assert len(rows) == 1
    close_idx = _cache.DAILY_COLS.index("close")
    assert rows[0][close_idx] == 200.0


def test_upsert_daily_converts_nan_to_null(conn):
    df = pd.DataFrame({
        "ts_code": ["300750.SZ"], "trade_date": ["20260522"],
        "close": [184.0], "pe": [np.nan],
    })
    _cache.upsert_daily(conn, df)
    pe = conn.execute("SELECT pe FROM daily").fetchone()[0]
    assert pe is None


def test_upsert_empty_df_returns_zero(conn):
    assert _cache.upsert_daily(conn, pd.DataFrame()) == 0
    assert _cache.upsert_daily(conn, None) == 0


# ---------- upsert_stock_basic ----------

def test_upsert_stock_basic_uses_ts_code_as_primary_key(conn):
    df1 = pd.DataFrame([{
        "ts_code": "300750.SZ", "name": "宁德时代",
        "industry": "电池", "list_date": "20180611",
    }])
    df2 = pd.DataFrame([{"ts_code": "300750.SZ", "industry": "新能源电池"}])
    _cache.upsert_stock_basic(conn, df1)
    _cache.upsert_stock_basic(conn, df2)
    rows = _select_all(conn, "stock_basic")
    assert len(rows) == 1
    name_idx = _cache.STOCK_BASIC_COLS.index("name")
    industry_idx = _cache.STOCK_BASIC_COLS.index("industry")
    # df2 had no name → replaced to NULL (full-row INSERT OR REPLACE semantics)
    assert rows[0][name_idx] is None
    assert rows[0][industry_idx] == "新能源电池"


# ---------- upsert_hsgt ----------

def test_upsert_hsgt_keyed_by_trade_date(conn):
    df = pd.DataFrame({
        "trade_date": ["20260522", "20260521"],
        "north_money": [3.2e8, -1.5e8],
        "south_money": [1.1e8, 2.0e8],
    })
    _cache.upsert_hsgt(conn, df)
    rows = sorted(_select_all(conn, "moneyflow_hsgt"))
    assert len(rows) == 2
    assert rows[0][0] == "2026-05-21"  # ISO normalized


def test_upsert_hsgt_tolerates_missing_columns(conn):
    df = pd.DataFrame({"trade_date": ["20260522"], "north_money": [1.0e8]})
    _cache.upsert_hsgt(conn, df)
    row = conn.execute("SELECT ggt_ss, hgt, south_money FROM moneyflow_hsgt").fetchone()
    assert row == (None, None, None)


# ---------- date normalizer ----------

@pytest.mark.parametrize("inp,want", [
    ("20260522", "2026-05-22"),
    ("2026-05-22", "2026-05-22"),
    ("", ""),
    ("not-a-date", "not-a-date"),  # passthrough, don't crash
])
def test_iso_date_normalizer(inp, want):
    assert _cache._iso_date(inp) == want
