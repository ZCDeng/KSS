"""舆情热点 digest — kss.db news_digest_entries 表（plan 2026-07-12-005 /
U15 割接自 storage/news_digest/{date}_{scene}.json）。

md（人读）仍是文件（Tier C，不动）；json 结构化部分迁 Tier A。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

from kss.storage.db import connect, ensure_schema


class DigestKey(NamedTuple):
    digest_date: str
    scene: str


def write_entry(digest: dict[str, Any], db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO news_digest_entries (digest_date, scene, payload_json, generated_at) "
            "VALUES (?,?,?,?)",
            (digest["date"], digest["scene"], json.dumps(digest, ensure_ascii=False), digest.get("generatedAt")),
        )


def read_entry(digest_date: str, scene: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT payload_json FROM news_digest_entries WHERE digest_date=? AND scene=?",
            (digest_date, scene),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_index(db_path: str | Path | None = None) -> list[DigestKey]:
    """全部 (digest_date, scene)，新到旧。"""
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT digest_date, scene FROM news_digest_entries ORDER BY digest_date DESC"
        ).fetchall()
    return [DigestKey(r["digest_date"], r["scene"]) for r in rows]
