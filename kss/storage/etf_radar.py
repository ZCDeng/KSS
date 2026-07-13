"""ETF 申赎雷达 — kss.db etf_radar_snapshots / etf_radar_commentary_index /
etf_radar_morning_alert_state 三张表（plan 2026-07-12-005 / U15 割接自
storage/etf_radar/{trade_date}.json + *.commentary.md + .morning_alert_state）。

json 快照迁 Tier A（etf_radar_snapshots）；commentary.md 投顾点评仍是文件
（Tier C，不动），只用 etf_radar_commentary_index 记路径；morning_alert_state
原是纯文本单文件（存最后一次已推送的 data_date），迁 Tier A 单例表。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def write_snapshot(payload: dict[str, Any], db_path: str | Path | None = None) -> None:
    trade_date = payload["trade_date"]
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_snapshots (trade_date, payload_json, created_at) VALUES (?,?,?)",
            (trade_date, json.dumps(payload, ensure_ascii=False), None),
        )


def read_by_date(trade_date: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM etf_radar_snapshots WHERE trade_date=?", (trade_date,)
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def read_all_ascending(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全量快照，旧到新（validate_predictions.py 累积校验用）。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM etf_radar_snapshots ORDER BY trade_date ASC"
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def read_history(limit: int, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """最新 N 份快照，新到旧。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM etf_radar_snapshots ORDER BY trade_date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def record_commentary(trade_date: str, file_path: str | Path, db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_commentary_index (trade_date, file_path, created_at) VALUES (?,?,?)",
            (trade_date, str(file_path), None),
        )


def read_commentary_path(trade_date: str, db_path: str | Path | None = None) -> str | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT file_path FROM etf_radar_commentary_index WHERE trade_date=?", (trade_date,)
        ).fetchone()
    return row["file_path"] if row else None


def read_alert_state(db_path: str | Path | None = None) -> str | None:
    """最后一次已推送的 data_date；无记录返回 None。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM etf_radar_morning_alert_state WHERE singleton='default'"
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"]).get("last_alert_date")


def write_alert_state(data_date: str, db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_morning_alert_state (singleton, payload_json, updated_at) "
            "VALUES ('default', ?, ?)",
            (json.dumps({"last_alert_date": data_date}), None),
        )
