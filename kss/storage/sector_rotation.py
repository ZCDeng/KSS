"""板块热点轮动归档 — kss.db sector_rotation_snapshots 表（plan 2026-07-12-005 /
U15 割接自 storage/sector_rotation/{trade_date}.json）。

PK trade_date：一天一份快照，无 latest/ 副本概念——「最新」用
``ORDER BY trade_date DESC LIMIT 1`` 派生。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def write_snapshot(payload: dict[str, Any], db_path: str | Path | None = None) -> None:
    trade_date = payload["tradeDate"]
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO sector_rotation_snapshots (trade_date, payload_json, created_at) VALUES (?,?,?)",
            (trade_date, json.dumps(payload, ensure_ascii=False), None),
        )


def read_by_date(trade_date: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM sector_rotation_snapshots WHERE trade_date=?", (trade_date,)
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def read_latest(db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM sector_rotation_snapshots ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def read_history(limit: int, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """最新 N 份快照，新到旧。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM sector_rotation_snapshots ORDER BY trade_date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def read_all_ascending(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全量快照，旧到新（离线分析脚本用，如 compute_pipeline_alpha.py）。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM sector_rotation_snapshots ORDER BY trade_date ASC"
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]
