"""信号卡层存储 — kss.db signal_cards 表（plan 2026-07-28-002 / U1）.

索引列 + payload_json STRICT 表；无物理 latest 副本，「最新」用 ORDER BY 派生。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema

_COMPACT_RE = re.compile(r"^\d{8}$")
_DASHED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_compact(date_str: str) -> str:
    """横杠 YYYY-MM-DD → 紧凑 YYYYMMDD；已是紧凑则原样；不可识别则抛错。"""
    if not isinstance(date_str, str) or not date_str.strip():
        raise ValueError(f"date_str 不可识别: {date_str!r}")
    s = date_str.strip()
    if _COMPACT_RE.match(s):
        return s
    if _DASHED_RE.match(s):
        return s.replace("-", "")
    raise ValueError(f"date_str 不可识别: {date_str!r}")


def write_cards(cards: list[dict[str, Any]], db_path: str | Path | None = None) -> int:
    """INSERT OR REPLACE 写入卡列表；返回写入条数。"""
    if not cards:
        return 0
    with connect(db_path) as conn:
        ensure_schema(conn)
        n = 0
        for card in cards:
            card_id = card["card_id"]
            trade_date = card["trade_date"]
            card_type = card["card_type"]
            subject = card.get("subject")
            conn.execute(
                "INSERT OR REPLACE INTO signal_cards "
                "(trade_date, card_type, card_id, subject, payload_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    trade_date,
                    card_type,
                    card_id,
                    subject,
                    json.dumps(card, ensure_ascii=False, default=str),
                    None,
                ),
            )
            n += 1
        return n


def read_by_date(trade_date: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    trade_date = _to_compact(trade_date) if "-" in trade_date else trade_date
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM signal_cards WHERE trade_date=? ORDER BY card_type, subject",
            (trade_date,),
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def read_range(
    start: str, end: str, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    start_c = _to_compact(start) if "-" in start else start
    end_c = _to_compact(end) if "-" in end else end
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM signal_cards "
            "WHERE trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date, card_type, subject",
            (start_c, end_c),
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def read_by_subject(
    subject: str, limit: int = 50, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM signal_cards WHERE subject=? "
            "ORDER BY trade_date DESC LIMIT ?",
            (subject, int(limit)),
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def read_by_card_id(card_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM signal_cards WHERE card_id=?", (card_id,)
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def latest_trade_date(db_path: str | Path | None = None) -> str | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT trade_date FROM signal_cards ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    return row["trade_date"] if row else None


def query_cards(
    *,
    symbol: str | None = None,
    trade_date: str | None = None,
    days: int | None = None,
    card_type: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Agent 工具查询入口：空 date → 最新交易日；无卡 → 空列表。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        if trade_date:
            date_c = _to_compact(trade_date) if "-" in trade_date else trade_date
            if days and days > 1:
                # 回看：以 date 为终点，取 trade_date <= date 的 distinct 日再过滤
                dates = [
                    r["trade_date"]
                    for r in conn.execute(
                        "SELECT DISTINCT trade_date FROM signal_cards "
                        "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
                        (date_c, int(days)),
                    ).fetchall()
                ]
                if not dates:
                    return []
                placeholders = ",".join("?" * len(dates))
                sql = (
                    f"SELECT payload_json FROM signal_cards WHERE trade_date IN ({placeholders})"
                )
                params: list[Any] = list(dates)
            else:
                sql = "SELECT payload_json FROM signal_cards WHERE trade_date=?"
                params = [date_c]
        elif days and days > 0:
            dates = [
                r["trade_date"]
                for r in conn.execute(
                    "SELECT DISTINCT trade_date FROM signal_cards "
                    "ORDER BY trade_date DESC LIMIT ?",
                    (int(days),),
                ).fetchall()
            ]
            if not dates:
                return []
            placeholders = ",".join("?" * len(dates))
            sql = f"SELECT payload_json FROM signal_cards WHERE trade_date IN ({placeholders})"
            params = list(dates)
        else:
            latest = conn.execute(
                "SELECT trade_date FROM signal_cards ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return []
            sql = "SELECT payload_json FROM signal_cards WHERE trade_date=?"
            params = [latest["trade_date"]]

        if card_type:
            sql += " AND card_type=?"
            params.append(card_type)
        if symbol:
            sql += " AND subject=?"
            params.append(symbol)
        sql += " ORDER BY trade_date DESC, card_type, subject"
        rows = conn.execute(sql, params).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]
