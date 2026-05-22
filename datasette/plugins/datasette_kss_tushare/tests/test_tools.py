"""Tests for datasette_kss_tushare. Mocks tushare so no network is hit."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pandas as pd
import pytest

import datasette_kss_tushare as mod


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def clear_pro_cache():
    mod._pro_api.cache_clear()
    yield
    mod._pro_api.cache_clear()


class FakeAgentTool:
    """Stand-in for datasette_agent.tools.AgentTool when the real lib isn't installed."""
    def __init__(self, *, name, description, input_schema, fn, required_permission=None):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.fn = fn
        self.required_permission = required_permission


@pytest.fixture(autouse=True)
def patch_agent_tool():
    with patch.object(mod, "AgentTool", FakeAgentTool):
        yield


@pytest.fixture
def fake_daily_df():
    # 3 rows, newest first (tushare daily convention)
    return pd.DataFrame({
        "ts_code": ["300750.SZ"] * 3,
        "trade_date": ["20260522", "20260521", "20260520"],
        "open": [180.0, 178.0, 175.0],
        "high": [185.0, 181.0, 179.0],
        "low": [179.0, 176.0, 174.0],
        "close": [184.0, 180.0, 178.0],
        "pre_close": [180.0, 178.0, 175.0],
        "change": [4.0, 2.0, 3.0],
        "pct_chg": [2.22, 1.12, 1.71],
        "vol": [1.2e6, 9.0e5, 1.1e6],
        "amount": [2.2e8, 1.6e8, 1.9e8],
    })


@pytest.fixture
def fake_basic_df():
    return pd.DataFrame({
        "ts_code": ["300750.SZ"],
        "name": ["宁德时代"],
        "area": ["福建"],
        "industry": ["电池"],
        "market": ["创业板"],
        "list_date": ["20180611"],
        "is_hs": ["S"],
    })


@pytest.fixture
def fake_hsgt_df():
    return pd.DataFrame({
        "trade_date": ["20260522", "20260521"],
        "north_money": [3.2e8, -1.5e8],
        "south_money": [1.1e8, 2.0e8],
    })


def _mock_pro(method: str, df: pd.DataFrame):
    """Return a context manager patching _pro_api() so it exposes one method."""
    class FakePro:
        pass
    fake = FakePro()
    setattr(fake, method, lambda **kw: df)
    return patch.object(mod, "_pro_api", lambda: fake)


# ---------- register_agent_tools ----------

def test_register_returns_all_tools_with_schemas():
    tools = mod.register_agent_tools(datasette=None)
    names = {t.name for t in tools}
    assert names == {
        "tushare_stock_basic", "tushare_daily", "tushare_moneyflow_hsgt",
        "spawn_kss_anomaly_scan",
        "viz_candlestick", "viz_moneyflow_stack",
    }
    for t in tools:
        assert callable(t.fn)
        assert t.input_schema["type"] == "object"
        assert "required" in t.input_schema
        assert t.description and len(t.description) > 10


def test_anomaly_scan_uses_background_permission_not_fetch():
    """Spawning a background agent goes through datasette-agent-background,
    not our local fetch permission — actors with background rights but no
    fetch rights can still launch scans (the scan only uses sql_query)."""
    tools = mod.register_agent_tools(datasette=None)
    scan_tool = next(t for t in tools if t.name == "spawn_kss_anomaly_scan")
    assert scan_tool.required_permission == "datasette-agent-background"


def test_register_empty_if_agent_missing():
    with patch.object(mod, "AgentTool", None):
        assert mod.register_agent_tools(datasette=None) == []


def test_tushare_tools_require_fetch_permission():
    """Tools that actually hit the tushare API need the fetch permission."""
    tools = mod.register_agent_tools(datasette=None)
    tushare_tools = [t for t in tools if t.name.startswith("tushare_")]
    assert len(tushare_tools) == 3
    for t in tushare_tools:
        assert t.required_permission == "kss-tushare-fetch", (
            f"{t.name} must require fetch permission"
        )


def test_tushare_tools_accept_display_param():
    tools = mod.register_agent_tools(datasette=None)
    for t in tools:
        if not t.name.startswith("tushare_"):
            continue  # spawn_kss_anomaly_scan doesn't return tabular data
        assert "display" in t.input_schema["properties"], (
            f"{t.name} missing display param"
        )
        prop = t.input_schema["properties"]["display"]
        assert prop["enum"] == ["model", "both", "user"]


def test_register_actions_exposes_fetch_permission():
    """register_actions hook must declare the permission datasette will gate against."""
    actions = mod.register_actions()
    # If datasette.permissions.Action isn't importable we get [], that's fine
    # for unit testing — the runtime presence is what matters.
    if actions:
        assert any(getattr(a, "name", None) == "kss-tushare-fetch" for a in actions)


# ---------- _pro_api token handling ----------

def test_pro_api_without_token_fails_loud(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        mod._pro_api()


# ---------- tushare_daily ----------

@pytest.mark.asyncio
async def test_daily_returns_summary_and_html(fake_daily_df):
    with _mock_pro("daily", fake_daily_df):
        out = await mod._daily(None, None, "300750.SZ", "20260520", "20260522")
    payload = json.loads(out)
    assert payload["ts_code"] == "300750.SZ"
    assert len(payload["rows"]) == 3  # default display=both exposes rows to LLM
    assert payload["last_close"] == 184.0
    # period: newest close 184 / oldest pre_close 175 - 1
    assert payload["period_pct_chg"] == pytest.approx(5.14, abs=0.01)
    assert "<table" in payload["_html"]
    assert "300750.SZ" in payload["_html"]


@pytest.mark.asyncio
async def test_daily_handles_empty_result():
    empty = pd.DataFrame(columns=["ts_code", "trade_date", "close", "pre_close"])
    with _mock_pro("daily", empty):
        out = await mod._daily(None, None, "999999.SZ", "20260101", "20260102")
    payload = json.loads(out)
    assert "last_close" not in payload  # only set when len(df) > 0
    assert "无数据" in payload["_html"]


@pytest.mark.asyncio
async def test_daily_display_model_hides_html_from_user(fake_daily_df):
    with _mock_pro("daily", fake_daily_df):
        out = await mod._daily(None, None, "300750.SZ", "20260520", "20260522",
                               display="model")
    payload = json.loads(out)
    assert "_html" not in payload  # model mode: no UI render
    assert payload["rows"]  # rows visible to LLM


@pytest.mark.asyncio
async def test_daily_display_user_hides_rows_from_llm(fake_daily_df):
    """In 'user' mode the LLM only sees row_count, not the raw rows.
    Raw rows go under _rows (underscore → stripped before LLM sees it)."""
    with _mock_pro("daily", fake_daily_df):
        out = await mod._daily(None, None, "300750.SZ", "20260520", "20260522",
                               display="user")
    payload = json.loads(out)
    assert payload["row_count"] == 3
    assert "_html" in payload
    assert "_rows" in payload  # underscore-prefixed = LLM-hidden
    assert "rows" not in payload


@pytest.mark.asyncio
async def test_daily_display_both_default_gives_rows_and_html(fake_daily_df):
    with _mock_pro("daily", fake_daily_df):
        out = await mod._daily(None, None, "300750.SZ", "20260520", "20260522")
    payload = json.loads(out)
    assert "_html" in payload
    assert payload["rows"]


@pytest.mark.asyncio
async def test_daily_invalid_display_falls_back_to_both(fake_daily_df):
    with _mock_pro("daily", fake_daily_df):
        out = await mod._daily(None, None, "300750.SZ", "20260520", "20260522",
                               display="bogus")
    payload = json.loads(out)
    assert "_html" in payload and "rows" in payload


# ---------- tushare_stock_basic ----------

@pytest.mark.asyncio
async def test_stock_basic_returns_rows(fake_basic_df):
    with _mock_pro("stock_basic", fake_basic_df):
        out = await mod._stock_basic(None, None, "300750.SZ")
    payload = json.loads(out)
    assert payload["rows"][0]["name"] == "宁德时代"
    assert "<table" in payload["_html"]


# ---------- tushare_moneyflow_hsgt ----------

@pytest.mark.asyncio
async def test_hsgt_sums_north_money(fake_hsgt_df):
    with _mock_pro("moneyflow_hsgt", fake_hsgt_df):
        out = await mod._moneyflow_hsgt(None, None, "20260521", "20260522")
    payload = json.loads(out)
    assert len(payload["rows"]) == 2
    assert payload["net_amount_sum"] == pytest.approx(1.7e8)
    assert "北向资金" in payload["_html"]


@pytest.mark.asyncio
async def test_hsgt_handles_missing_north_money_column():
    df = pd.DataFrame({"trade_date": ["20260522"], "south_money": [1.0e8]})
    with _mock_pro("moneyflow_hsgt", df):
        out = await mod._moneyflow_hsgt(None, None, "20260522", "20260522")
    payload = json.loads(out)
    assert len(payload["rows"]) == 1
    assert "net_amount_sum" not in payload  # set only when north_money column present


# ---------- _html safety ----------

def test_df_to_html_escapes_caption():
    df = pd.DataFrame({"x": [1]})
    html_out = mod._df_to_html(df, "<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out
