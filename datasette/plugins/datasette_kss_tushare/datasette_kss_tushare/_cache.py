"""Write-back cache helpers.

Each upsert_* takes a raw sqlite3 Connection plus a pandas DataFrame freshly
returned from a tushare endpoint. It normalizes (date format, column padding),
ensures the target table + unique index exist, then INSERT OR REPLACE.

Pure sqlite3 so tests can run against :memory:. The datasette runtime hands
us a Connection via Database.execute_write_fn().
"""

from __future__ import annotations

import sqlite3
from typing import Sequence

import pandas as pd


DAILY_COLS: Sequence[str] = (
    "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
    "change", "pct_chg", "vol", "amount",
    "turnover_rate", "volume_ratio", "pe", "pb", "total_mv",
)

STOCK_BASIC_COLS: Sequence[str] = (
    "ts_code", "name", "area", "industry", "market", "list_date", "is_hs",
)

HSGT_COLS: Sequence[str] = (
    "trade_date", "ggt_ss", "ggt_sz", "hgt", "sgt", "north_money", "south_money",
)


def _iso_date(s: str) -> str:
    """Normalize trade_date to 'YYYY-MM-DD' to match the existing `daily` table."""
    if not s:
        return s
    s = str(s)
    if "-" in s:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create write-back targets if missing. `daily` already exists from build_db.py."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS daily (
            {', '.join(c + ' TEXT' if c in ('ts_code', 'trade_date') else c + ' REAL'
                       for c in DAILY_COLS)},
            UNIQUE (ts_code, trade_date)
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_unique
            ON daily (ts_code, trade_date)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_basic (
            ts_code TEXT PRIMARY KEY,
            name TEXT, area TEXT, industry TEXT,
            market TEXT, list_date TEXT, is_hs TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS moneyflow_hsgt (
            trade_date TEXT PRIMARY KEY,
            ggt_ss REAL, ggt_sz REAL, hgt REAL, sgt REAL,
            north_money REAL, south_money REAL
        )
    """)


def _upsert(conn: sqlite3.Connection, table: str, cols: Sequence[str],
            df: pd.DataFrame) -> int:
    """Pad missing columns with None, run INSERT OR REPLACE in one executemany."""
    if df is None or len(df) == 0:
        return 0
    aligned = df.reindex(columns=cols)
    if "trade_date" in aligned.columns:
        aligned["trade_date"] = aligned["trade_date"].astype(str).map(_iso_date)
    rows = [tuple(None if pd.isna(v) else v for v in row)
            for row in aligned.itertuples(index=False, name=None)]
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def upsert_daily(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    ensure_schema(conn)
    return _upsert(conn, "daily", DAILY_COLS, df)


def upsert_stock_basic(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    ensure_schema(conn)
    return _upsert(conn, "stock_basic", STOCK_BASIC_COLS, df)


def upsert_hsgt(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    ensure_schema(conn)
    return _upsert(conn, "moneyflow_hsgt", HSGT_COLS, df)
