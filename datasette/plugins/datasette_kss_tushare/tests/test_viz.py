"""Tests for matplotlib server-side rendering. Real PNG generation against
small DataFrames — no datasette, no SQL, no agent."""

from __future__ import annotations

import base64
import re

import pandas as pd
import pytest

from datasette_kss_tushare import _viz


# ---------- candlestick ----------

@pytest.fixture
def ohlc_df():
    return pd.DataFrame({
        "trade_date": ["20260520", "20260521", "20260522"],
        "open":  [100.0, 102.0, 105.0],
        "high":  [103.0, 106.0, 108.0],
        "low":   [99.0, 101.0, 104.0],
        "close": [102.0, 105.0, 103.0],  # up, up, down
        "vol":   [1e6, 1.5e6, 1.2e6],
    })


def _strip_data_uri(html: str) -> bytes:
    m = re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html)
    assert m, f"no data URI in {html[:200]}"
    return base64.b64decode(m.group(1))


def test_candlestick_emits_data_uri_png(ohlc_df):
    out = _viz.candlestick_html(ohlc_df, title="test")
    assert '<img src="data:image/png;base64,' in out
    png = _strip_data_uri(out)
    assert png[:8] == b'\x89PNG\r\n\x1a\n'  # PNG magic header


def test_candlestick_works_without_vol_column():
    df = pd.DataFrame({
        "trade_date": ["20260520", "20260521"],
        "open": [100.0, 101.0], "high": [102.0, 103.0],
        "low": [99.0, 100.0], "close": [101.0, 102.0],
    })
    out = _viz.candlestick_html(df)
    assert "data:image/png" in out


def test_candlestick_missing_required_column_returns_error_html():
    df = pd.DataFrame({"trade_date": ["x"], "open": [1], "close": [2]})
    out = _viz.candlestick_html(df)
    assert "missing" in out
    assert "data:image" not in out


def test_candlestick_empty_df_returns_no_rows_message():
    df = pd.DataFrame(columns=["trade_date", "open", "high", "low", "close"])
    out = _viz.candlestick_html(df)
    assert "no rows" in out


def test_candlestick_single_row_does_not_crash(ohlc_df):
    one = ohlc_df.head(1)
    out = _viz.candlestick_html(one)
    assert "data:image/png" in out


# ---------- moneyflow ----------

@pytest.fixture
def moneyflow_df():
    return pd.DataFrame({
        "trade_date": ["20260520", "20260521", "20260522"],
        "buy_sm_amount":  [100.0, 80.0, 120.0],
        "sell_sm_amount": [90.0, 100.0, 110.0],
        "buy_md_amount":  [200.0, 180.0, 220.0],
        "sell_md_amount": [190.0, 200.0, 210.0],
        "buy_lg_amount":  [500.0, 600.0, 400.0],
        "sell_lg_amount": [400.0, 700.0, 350.0],
        "buy_elg_amount":  [1000.0, 800.0, 1200.0],
        "sell_elg_amount": [900.0, 1000.0, 1100.0],
    })


def test_moneyflow_stack_emits_png(moneyflow_df):
    out = _viz.moneyflow_stack_html(moneyflow_df, title="hsgt")
    png = _strip_data_uri(out)
    assert png[:8] == b'\x89PNG\r\n\x1a\n'


def test_moneyflow_stack_tolerates_partial_tiers():
    """Only large + extra-large columns present — still renders, just two tiers."""
    df = pd.DataFrame({
        "trade_date": ["20260520", "20260521"],
        "buy_lg_amount": [500.0, 600.0], "sell_lg_amount": [400.0, 700.0],
        "buy_elg_amount": [1000.0, 800.0], "sell_elg_amount": [900.0, 1000.0],
    })
    out = _viz.moneyflow_stack_html(df)
    assert "data:image/png" in out


def test_moneyflow_empty_df_returns_no_rows():
    df = pd.DataFrame(columns=["trade_date", "buy_sm_amount", "sell_sm_amount"])
    out = _viz.moneyflow_stack_html(df)
    assert "no rows" in out


def test_moneyflow_handles_negative_net_flow_no_crash():
    """Sell-dominant days produce negative tier nets — must not crash the
    stacked bar layout (we track positive and negative bottoms separately)."""
    df = pd.DataFrame({
        "trade_date": ["20260520", "20260521"],
        "buy_sm_amount": [50.0, 200.0],
        "sell_sm_amount": [200.0, 50.0],  # day 1 net -150, day 2 net +150
    })
    out = _viz.moneyflow_stack_html(df)
    assert "data:image/png" in out
