"""资讯雷达正文缓存 — kss.db intel_article_items 表（plan 2026-07-22-001 U2）.

点开条目先读缓存，未命中才现场抓取（14s 超时是原文慢的一半原因）。文章内容
静态，不设 TTL。只缓存 fulltext 结果：summary 兜底不落库，站点恢复后仍有机会
升级为全文。抓取失败但缓存有旧记录时返回旧记录（R9 兜底）。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kss.news.article_fetch import body_or_summary
from kss.storage.db import connect, ensure_schema

BEIJING = timezone(timedelta(hours=8))


def _state_root() -> Path:
    raw = os.environ.get("KSS_STATE_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    return _state_root() / "storage" / "kss.db"


def article_key(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:16]


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        "body": row["body"] or "",
        "body_md": row["body_md"],
        "title": row["title"] or "",
        "mode": row["mode"],
        "error": None,
        "char_count": row["char_count"] or 0,
        "extractor": row["extractor"],
        "url": row["url"],
        "cached": True,
    }


def read_cached(url: str) -> dict[str, Any] | None:
    key = article_key(url)
    with connect(_db_path()) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM intel_article_items WHERE item_key=?", (key,)
        ).fetchone()
    if row is None:
        return None
    return _row_payload(row)


def _write_cached(url: str, got: dict[str, Any]) -> None:
    with connect(_db_path()) as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT OR REPLACE INTO intel_article_items
            (item_key, url, title, mode, body, body_md, char_count, extractor, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                article_key(url),
                url,
                got.get("title") or "",
                got.get("mode") or "empty",
                got.get("body") or "",
                got.get("body_md"),
                int(got.get("char_count") or 0),
                got.get("extractor"),
                datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def get_or_fetch(url: str, summary: str = "", *, force: bool = False) -> dict[str, Any]:
    """读穿缓存：命中（extractor 非空）直接返回；未命中/旧格式抓取后落库。

    抓取失败：缓存有旧记录 → 返回旧记录（R9）；否则返回 body_or_summary 的兜底结果。
    """
    if not (url or "").strip():
        return body_or_summary(url=url, summary=summary)

    cached = read_cached(url)
    if cached is not None and cached.get("extractor") and not force:
        return cached

    got = body_or_summary(url=url, summary=summary)
    if got.get("mode") == "fulltext":
        _write_cached(url, got)
        return {**got, "cached": False}
    if cached is not None:
        return cached
    return got
