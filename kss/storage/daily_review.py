"""个股复盘按日归档索引 — kss.db daily_review_index 表（plan 2026-07-12-005 /
U15 割接自对 storage/daily_review/*.md 的重复 glob+文件名正则解析）。

md 内容本身仍是文件（Tier C，不动）；本表只索引「哪天哪只票有哪份复盘」，
供按 ts_code 查历史复盘（daily_review.py 生成时的 history_recap）与列表页
（kss_app_bridge.py._reviews）替代原来各自独立的目录扫描 + 文件名正则反解。

只覆盖新格式 ``{review_date}_{ts_code}.md``；旧格式 ``{date}.md``（无 ts_code，
一天一份聚合多只票）不进索引——两种格式共存期由调用方自行决定是否兼容旧格式。
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from kss.storage.db import connect, ensure_schema


class ReviewEntry(NamedTuple):
    review_date: str
    ts_code: str
    file_path: str


def record_review(
    review_date: str, ts_code: str, file_path: str | Path, db_path: str | Path | None = None
) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO daily_review_index (review_date, ts_code, file_path, created_at) "
            "VALUES (?,?,?,?)",
            (review_date, ts_code, str(file_path), None),
        )


def list_symbol_reviews(ts_code: str, db_path: str | Path | None = None) -> list[ReviewEntry]:
    """某只票的全部复盘归档，按 review_date 升序（同 glob_symbol_reviews 既有排序约定）。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT review_date, ts_code, file_path FROM daily_review_index "
            "WHERE ts_code=? ORDER BY review_date ASC",
            (ts_code,),
        ).fetchall()
    return [ReviewEntry(r["review_date"], r["ts_code"], r["file_path"]) for r in rows]


def list_all_reviews(db_path: str | Path | None = None) -> list[ReviewEntry]:
    """全部新格式复盘归档，按 review_date 降序。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT review_date, ts_code, file_path FROM daily_review_index ORDER BY review_date DESC"
        ).fetchall()
    return [ReviewEntry(r["review_date"], r["ts_code"], r["file_path"]) for r in rows]
