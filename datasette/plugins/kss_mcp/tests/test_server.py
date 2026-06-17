"""In-memory Client contract tests for kss-mcp.

Uses fastmcp's Client(transport=mcp_instance) pattern — no subprocess, no
stdio. Validates schema, dispatch, and value semantics end-to-end.

We seed a temp SQLite with the daily-table shape so tests don't depend on
the real kss.db being populated.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastmcp import Client

from kss_mcp.server import mcp


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "kss.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE daily (
                ts_code TEXT, trade_date TEXT,
                open REAL, high REAL, low REAL, close REAL, pre_close REAL,
                change REAL, pct_chg REAL,
                vol REAL, amount REAL,
                turnover_rate REAL, volume_ratio REAL,
                pe REAL, pb REAL, total_mv REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO daily(ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount, turnover_rate) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("688322.SH", "20260520", 100.0, 105.0, 99.0, 103.0, 100.0, 3.0, 1000.0, 100000.0, 1.5),
                ("688322.SH", "20260521", 103.0, 108.0, 102.0, 107.0, 103.0, 3.88, 1200.0, 130000.0, 1.8),
                ("688322.SH", "20260522", 107.0, 110.0, 105.0, 110.0, 107.0, 2.80, 1500.0, 160000.0, 2.1),
            ],
        )
    monkeypatch.setenv("KSS_DB_PATH", str(db))
    return db


async def test_tool_is_listed():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "kss_query_daily" in names

        tool = next(t for t in tools if t.name == "kss_query_daily")
        # schema sanity: required arg ts_code, optional n_days
        schema = tool.inputSchema
        assert "ts_code" in schema["properties"]
        assert "n_days" in schema["properties"]
        assert schema["required"] == ["ts_code"]


async def test_query_returns_rows(seeded_db: Path):
    async with Client(mcp) as client:
        result = await client.call_tool("kss_query_daily", {"ts_code": "688322.SH"})
        # fastmcp returns CallToolResult with content list; structured output via .data
        payload = result.data if result.data is not None else json.loads(result.content[0].text)
        assert payload["ts_code"] == "688322.SH"
        assert payload["row_count"] == 3
        assert payload["last_trade_date"] == "20260522"
        assert payload["last_close"] == 110.0
        # period = (110/100 - 1)*100 = 10.0
        assert payload["period_pct_chg"] == 10.0


async def test_query_missing_ts_code(seeded_db: Path):
    async with Client(mcp) as client:
        result = await client.call_tool("kss_query_daily", {"ts_code": "999999.SZ"})
        payload = result.data if result.data is not None else json.loads(result.content[0].text)
        assert payload["row_count"] == 0
        assert payload["last_trade_date"] is None
        assert payload["rows"] == []


async def test_n_days_validation(seeded_db: Path):
    async with Client(mcp) as client:
        with pytest.raises(Exception):  # fastmcp wraps ValueError in ToolError
            await client.call_tool("kss_query_daily", {"ts_code": "688322.SH", "n_days": 999})


async def test_n_days_limits_result(seeded_db: Path):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "kss_query_daily", {"ts_code": "688322.SH", "n_days": 2}
        )
        payload = result.data if result.data is not None else json.loads(result.content[0].text)
        assert payload["row_count"] == 2
        assert payload["last_trade_date"] == "20260522"
