"""自选列表读写（plan 2026-07-12-005 / U15，第一个割接域）.

真源从 ``storage/watchlist_symbols.txt``（Swift 写、Python 逐处独立 glob 读）切到
``kss.db`` 的 ``watchlist`` 表。``position`` 列保留用户添加顺序——旧 txt 文件按行序
即用户操作序，``collect_intraday.py:_prioritize_watchlist`` 依赖这个顺序做稳定排序，
不能悄悄换成字母序。

``set_watchlist`` 是整表替换语义（不是追加台账）：自选列表代表"当前态"，用户删除
一只票，表里也该消失，不是软删除。
"""

from __future__ import annotations

from pathlib import Path

from kss.storage.db import connect, ensure_schema


def load_watchlist(db_path: str | Path | None = None) -> list[str]:
    """按用户添加顺序返回 ts_code 列表；表不存在/为空返回 []。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute("SELECT ts_code FROM watchlist ORDER BY position").fetchall()
    return [r["ts_code"] for r in rows]


def set_watchlist(symbols: list[str], db_path: str | Path | None = None) -> None:
    """整表替换：删旧、按传入顺序重写 position。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM watchlist")
        for i, ts_code in enumerate(symbols):
            conn.execute(
                "INSERT INTO watchlist (ts_code, position, added_at) VALUES (?, ?, datetime('now'))",
                (ts_code, i),
            )
