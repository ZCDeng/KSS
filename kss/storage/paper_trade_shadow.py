"""影子纸交易轨 —— paper_trade_shadow_picks（与 formal paper_trade_picks 分轨）.

PK (prediction_date, strategy_id, symbol)。默认 formal 汇总不得读此表。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema


def write_style_day(
    payload: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> None:
    """写入某风格整池影子日志.

    payload 需含 prediction_date, strategy_id, picks[]；可选 generated_at, top_n.
    """
    prediction_date = payload["prediction_date"]
    strategy_id = payload["strategy_id"]
    generated_at = payload.get("generated_at") or datetime.now().isoformat()
    top_n = payload.get("top_n")
    picks = payload.get("picks") or []
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "DELETE FROM paper_trade_shadow_picks "
            "WHERE prediction_date=? AND strategy_id=?",
            (prediction_date, strategy_id),
        )
        for pick in picks:
            conn.execute(
                """INSERT INTO paper_trade_shadow_picks
                (prediction_date, strategy_id, symbol, generated_at, top_n,
                 factor_value, rank_pct, rank_position, planned_weight,
                 selection_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    prediction_date,
                    strategy_id,
                    pick.get("symbol"),
                    generated_at,
                    top_n if top_n is not None else pick.get("top_n"),
                    pick.get("factor_value"),
                    pick.get("rank_pct"),
                    pick.get("rank_position"),
                    pick.get("planned_weight"),
                    pick.get("selection_reason") or pick.get("reason"),
                ),
            )


def read_style_day(
    prediction_date: str,
    strategy_id: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM paper_trade_shadow_picks "
            "WHERE prediction_date=? AND strategy_id=? "
            "ORDER BY rank_position",
            (prediction_date, strategy_id),
        ).fetchall()
    if not rows:
        return None
    first = rows[0]
    return {
        "prediction_date": prediction_date,
        "strategy_id": strategy_id,
        "generated_at": first["generated_at"],
        "top_n": first["top_n"],
        "picks": [
            {
                "symbol": r["symbol"],
                "factor_value": r["factor_value"],
                "rank_pct": r["rank_pct"],
                "rank_position": r["rank_position"],
                "planned_weight": r["planned_weight"],
                "selection_reason": r["selection_reason"],
            }
            for r in rows
        ],
    }


def list_strategy_days(
    strategy_id: str,
    *,
    limit: int | None = None,
    db_path: str | Path | None = None,
) -> list[str]:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT DISTINCT prediction_date FROM paper_trade_shadow_picks "
            "WHERE strategy_id=? ORDER BY prediction_date",
            (strategy_id,),
        ).fetchall()
    dates = [r["prediction_date"] for r in rows]
    if limit is not None and limit > 0:
        dates = dates[-limit:]
    return dates


def day_exists(
    prediction_date: str,
    strategy_id: str,
    *,
    db_path: str | Path | None = None,
) -> bool:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM paper_trade_shadow_picks "
            "WHERE prediction_date=? AND strategy_id=? LIMIT 1",
            (prediction_date, strategy_id),
        ).fetchone()
    return row is not None
