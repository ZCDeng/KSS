"""纸交易选股按日 — kss.db paper_trade_picks 表（plan 2026-07-12-005 / U15 割接自
storage/paper_trade/{prediction_date}.json）。

PK (prediction_date, symbol)：一行一只票的当日预测。``use_execution``/``source``
字段（两个 JSON 写入方都产出）不进 schema——U14 既有导入器已丢弃，下游无消费者
（confirmed via research: 无 reader 读取这两个字段）。读侧按 prediction_date 聚合
重建「一天一份」的 payload 形状（generated_at/strategy/top_pct/top_n/picks[]），
供 summarize_log_dir 等按天迭代的调用方使用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def day_exists(prediction_date: str, db_path: str | Path | None = None) -> bool:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM paper_trade_picks WHERE prediction_date=? LIMIT 1", (prediction_date,)
        ).fetchone()
    return row is not None


def write_day(payload: dict[str, Any], db_path: str | Path | None = None) -> None:
    prediction_date = payload["prediction_date"]
    with connect(db_path) as conn:
        ensure_schema(conn)
        for pick in payload.get("picks", []):
            conn.execute(
                """INSERT OR REPLACE INTO paper_trade_picks
                (prediction_date, symbol, generated_at, strategy, top_pct, top_n,
                 factor_value, rank_pct, rank_position, planned_weight)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    prediction_date, pick.get("symbol"), payload.get("generated_at"),
                    payload.get("strategy"), payload.get("top_pct"), payload.get("top_n"),
                    pick.get("factor_value"), pick.get("rank_pct"),
                    pick.get("rank_position"), pick.get("planned_weight"),
                ),
            )


def read_day(prediction_date: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """重建单日 payload（跨行聚合 picks[]）."""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM paper_trade_picks WHERE prediction_date=? ORDER BY rank_position",
            (prediction_date,),
        ).fetchall()
    if not rows:
        return None
    first = rows[0]
    return {
        "prediction_date": prediction_date,
        "generated_at": first["generated_at"],
        "strategy": first["strategy"],
        "top_pct": first["top_pct"],
        "top_n": first["top_n"],
        "picks": [
            {
                "symbol": r["symbol"],
                "factor_value": r["factor_value"],
                "rank_pct": r["rank_pct"],
                "rank_position": r["rank_position"],
                "planned_weight": r["planned_weight"],
            }
            for r in rows
        ],
    }


def read_all_days(
    db_path: str | Path | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """全部日期（或最近 limit 天），按 prediction_date 升序，每天一个聚合 payload。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        dates = [
            r["prediction_date"]
            for r in conn.execute(
                "SELECT DISTINCT prediction_date FROM paper_trade_picks ORDER BY prediction_date"
            )
        ]
    if limit is not None and limit > 0:
        dates = dates[-limit:]
    out: list[dict[str, Any]] = []
    for d in dates:
        day = read_day(d, db_path)
        if day is not None:
            out.append(day)
    return out


def read_latest_day(db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT prediction_date FROM paper_trade_picks ORDER BY prediction_date DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return read_day(row["prediction_date"], db_path)
