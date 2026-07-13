"""分时本地降级缓存（plan 2026-07-12-005 / U15 域割接自 storage/intraday_session_cache/*.json）.

主键 (symbol, interval_minutes)——每个 (标的, 粒度) 组合只留最近一次成功拉取的
bars，覆写不追加（跟旧的一 symbol+interval 一文件语义一致）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def load_session_bars(
    symbol: str, interval_minutes: int, db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """返回缓存 payload（含 bars/session_date/saved_at）；未命中 None。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM intraday_session_cache WHERE symbol=? AND interval_minutes=?",
            (symbol, interval_minutes),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def save_session_bars(
    symbol: str,
    interval_minutes: int,
    bars: list[dict[str, Any]],
    session_date: str | None,
    saved_at: str,
    db_path: str | Path | None = None,
) -> None:
    payload = {
        "symbol": symbol,
        "interval_minutes": interval_minutes,
        "session_date": session_date,
        "saved_at": saved_at,
        "bars": bars,
    }
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT OR REPLACE INTO intraday_session_cache
            (symbol, interval_minutes, session_date, payload_json, cached_at)
            VALUES (?,?,?,?,?)""",
            (symbol, interval_minutes, session_date, json.dumps(payload, ensure_ascii=False, default=str), saved_at),
        )
