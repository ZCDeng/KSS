"""趋势页按日归档 — kss.db trends_days 表（plan 2026-07-12-005 / U15 割接自
storage/trends/{date}.json）。

单日文件名即键，内容体量小、结构规整，整个迁 Tier A，不留文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def write_day(payload: dict[str, Any], db_path: str | Path | None = None) -> None:
    trade_date = payload["date"]
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO trends_days (trade_date, payload_json, created_at) VALUES (?,?,?)",
            (trade_date, json.dumps(payload, ensure_ascii=False), None),
        )


def read_by_date(trade_date: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM trends_days WHERE trade_date=?", (trade_date,)
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def day_exists(trade_date: str, db_path: str | Path | None = None) -> bool:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT 1 FROM trends_days WHERE trade_date=? LIMIT 1", (trade_date,)).fetchone()
    return row is not None


def read_all(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute("SELECT payload_json FROM trends_days ORDER BY trade_date ASC").fetchall()
    return [json.loads(r["payload_json"]) for r in rows]
