"""KSS local-cache MCP server (fastmcp pilot).

Exposes one tool, ``kss_query_daily``, that reads recent A-share daily bars
from the project's local SQLite cache (kss.db). No tushare API calls, no
network — pure local read. This is the smallest viable surface to validate
fastmcp 3.3.1 + Claude Code stdio integration before broader adoption.

DB path is resolved in this order:
  1. ``KSS_DB_PATH`` env var
  2. ``../../kss.db`` relative to this file (datasette workbench default)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("kss-local-cache")

_DEFAULT_DB = Path(__file__).resolve().parents[3] / "kss.db"


def _db_path() -> Path:
    override = os.environ.get("KSS_DB_PATH", "").strip()
    return Path(override) if override else _DEFAULT_DB


@mcp.tool
def kss_query_daily(ts_code: str, n_days: int = 30) -> dict[str, Any]:
    """读 KSS 本地缓存 kss.db 里某只 A 股的最近 N 日日线。

    不调用 tushare API，纯本地 SQLite 查询。库里没有该 ts_code 或日期落后时
    返回 row_count=0 + last_trade_date=None，由调用方决定是否要触发拉取。

    Args:
        ts_code: A 股代码，含交易所后缀，如 "300750.SZ" / "688322.SH"。
        n_days: 取最近多少个交易日，默认 30，最大 250（约一年）。
    """
    if n_days < 1 or n_days > 250:
        raise ValueError(f"n_days must be in [1, 250], got {n_days}")

    db = _db_path()
    if not db.exists():
        raise FileNotFoundError(f"kss.db not found at {db} (set KSS_DB_PATH to override)")

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT trade_date, open, high, low, close, pre_close,
                   pct_chg, vol, amount, turnover_rate
            FROM daily
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (ts_code, n_days),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return {
            "ts_code": ts_code,
            "row_count": 0,
            "last_trade_date": None,
            "rows": [],
        }

    last_close = rows[0]["close"]
    first_pre_close = rows[-1]["pre_close"]
    period_pct = (
        round((last_close / first_pre_close - 1) * 100, 2)
        if first_pre_close
        else None
    )

    return {
        "ts_code": ts_code,
        "row_count": len(rows),
        "last_trade_date": rows[0]["trade_date"],
        "first_trade_date": rows[-1]["trade_date"],
        "last_close": last_close,
        "period_pct_chg": period_pct,
        "rows": rows,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
