"""Rewrite draft pool — kss.db intel_rewrite_items 表（plan 2026-07-12-005 / U15 割接自
STATE_ROOT/storage/intel_rewrites/*.json）.

Plan 2026-07-10-001 KTD1: body snapshot + status lifecycle generating|ready|failed.
Day key = Asia/Shanghai (BEIJING).

主键 (item_id, kind)：同一条资讯可以同时有 investment（默认）与 chinese 两个改写变体。
payload_json 原样存整份 draft dict（除 item_id/kind/track_key/day/status 已拆成索引列
外，text/sections/model/generated_at/prompt_system 等字段随写入方迭代变化，不强行拆列）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kss.news.rewrite_config import GENERATING_TTL_SEC
from kss.storage.db import connect, ensure_schema

BEIJING = timezone(timedelta(hours=8))


def _state_root() -> Path:
    raw = os.environ.get("KSS_STATE_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    return _state_root() / "storage" / "kss.db"


def beijing_day(now: datetime | None = None) -> str:
    dt = now or datetime.now(BEIJING)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING).strftime("%Y%m%d")


def item_id_for(item: dict[str, Any]) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    key = f"{item.get('title','')}|{item.get('source','')}|{item.get('time','')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# kind: investment (投研改写, legacy bare file) | chinese (qmreader 风中文改写, 已无 UI 入口)
#       | translation (忠实译文, 保留 markdown 结构; plan 2026-07-22-001 U3)
VALID_KINDS = frozenset({"investment", "chinese", "translation"})


def _normalize_kind(kind: str | None) -> str:
    kind = (kind or "investment").strip().lower()
    return kind if kind in VALID_KINDS else "investment"


def _row_payload(row: Any) -> dict[str, Any]:
    d = json.loads(row["payload_json"])
    d.setdefault("kind", row["kind"])
    return d


def read_draft(item_id: str, kind: str = "investment") -> dict[str, Any] | None:
    kind = _normalize_kind(kind)
    with connect(_db_path()) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT kind, payload_json FROM intel_rewrite_items WHERE item_id=? AND kind=?",
            (item_id, kind),
        ).fetchone()
    if row is None:
        return None
    try:
        return _row_payload(row)
    except Exception:  # noqa: BLE001
        return None


def _write_draft_in(conn, data: dict[str, Any]) -> None:
    """在已打开的连接/事务里写一条（claim_generating 用，跟读一起原子提交）。"""
    iid = data.get("item_id")
    if not iid:
        raise ValueError("draft missing item_id")
    kind = _normalize_kind(str(data.get("kind") or "investment"))
    conn.execute(
        """INSERT OR REPLACE INTO intel_rewrite_items
        (item_id, kind, track_key, day, status, payload_json, created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (
            str(iid), kind, data.get("track_key"), data.get("day"),
            str(data.get("status") or "unknown"),
            json.dumps(data, ensure_ascii=False),
            data.get("generated_at") or data.get("started_at"),
        ),
    )


def write_draft(data: dict[str, Any]) -> None:
    with connect(_db_path()) as conn:
        ensure_schema(conn)
        _write_draft_in(conn, data)


def claim_generating(
    item: dict[str, Any],
    *,
    track_key: str,
    day: str | None = None,
    kind: str = "investment",
    ttl_sec: float = GENERATING_TTL_SEC,
) -> tuple[bool, dict[str, Any]]:
    """Atomically claim item for rewrite（读-判-写全在一个 SQLite 事务里，比旧版文件
    「先查再写、写前再查一次」的 O_EXCL 模拟更严格——真并发下不会有 TOCTOU 窗口）.

    Returns (claimed, draft). claimed=False if ready or non-stale generating exists.
    """
    kind = _normalize_kind(kind)
    day = day or beijing_day()
    iid = item_id_for(item)
    now = time.time()

    with connect(_db_path()) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT kind, payload_json FROM intel_rewrite_items WHERE item_id=? AND kind=?",
            (iid, kind),
        ).fetchone()
        existing = _row_payload(row) if row is not None else None
        if existing:
            st = existing.get("status")
            if st == "ready" and not existing.get("force_pending"):
                return False, existing
            if st == "generating":
                started = float(existing.get("started_at_ts") or 0)
                if now - started < ttl_sec:
                    return False, existing

        draft = {
            "item_id": iid,
            "kind": kind,
            "track_key": track_key,
            "day": day,
            "status": "generating",
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "source": item.get("source") or "",
            "time": item.get("time") or "",
            "started_at_ts": now,
            "started_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        }
        _write_draft_in(conn, draft)
        return True, draft


def list_drafts(
    *,
    track_key: str | None = None,
    day: str | None = None,
    status: str | None = None,
    kind: str | None = "investment",
) -> list[dict[str, Any]]:
    """List drafts. Default kind=investment so digest pool ignores chinese rewrites."""
    where = []
    params: list[Any] = []
    if kind is not None:
        where.append("kind=?")
        params.append(_normalize_kind(kind))
    if track_key is not None:
        where.append("track_key=?")
        params.append(track_key)
    if day is not None:
        where.append("day=?")
        params.append(day)
    if status is not None:
        where.append("status=?")
        params.append(status)
    sql = "SELECT kind, payload_json FROM intel_rewrite_items"
    if where:
        sql += " WHERE " + " AND ".join(where)

    with connect(_db_path()) as conn:
        ensure_schema(conn)
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(_row_payload(row))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda x: x.get("generated_at") or x.get("started_at") or "", reverse=True)
    return out


def count_ready(track_key: str, day: str | None = None, kind: str = "investment") -> int:
    day = day or beijing_day()
    return len(list_drafts(track_key=track_key, day=day, status="ready", kind=kind))
