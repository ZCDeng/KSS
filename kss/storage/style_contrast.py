"""风格对照日更快照 —— kss.db style_contrast_snapshots 表.

按 prediction_date + style_id 存四槽位；缺失风格读时补 status=missing，
供推荐页 R7 占位。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema
from kss.strategies.styles import STYLE_ORDER, get_style_meta

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_MISSING = "missing"


def write_style_slot(
    prediction_date: str,
    style_id: str,
    *,
    status: str,
    payload: dict[str, Any] | None = None,
    gate_label: str | None = None,
    error: str | None = None,
    source_tags: list[str] | None = None,
    name: str | None = None,
    generated_at: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Upsert 单风格槽位."""

    if style_id not in STYLE_ORDER and not style_id.startswith("style_"):
        raise ValueError(f"非法 style_id: {style_id!r}")
    try:
        meta_name = get_style_meta(style_id).name if style_id in STYLE_ORDER else style_id
        meta_tags = (
            list(get_style_meta(style_id).source_tags)
            if style_id in STYLE_ORDER
            else []
        )
    except KeyError:
        meta_name = style_id
        meta_tags = []
    body = payload if payload is not None else {}
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT OR REPLACE INTO style_contrast_snapshots
            (prediction_date, style_id, status, gate_label, error,
             source_tags_json, name, payload_json, generated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                prediction_date,
                style_id,
                status,
                gate_label,
                error,
                json.dumps(source_tags if source_tags is not None else meta_tags, ensure_ascii=False),
                name or meta_name,
                json.dumps(body, ensure_ascii=False),
                generated_at or datetime.now().isoformat(),
            ),
        )


def _row_to_slot(row: Any | None, style_id: str, prediction_date: str) -> dict[str, Any]:
    if row is None:
        try:
            meta = get_style_meta(style_id)
            name = meta.name
            tags = list(meta.source_tags)
        except KeyError:
            name = style_id
            tags = []
        return {
            "prediction_date": prediction_date,
            "style_id": style_id,
            "name": name,
            "status": STATUS_MISSING,
            "gate_label": None,
            "error": None,
            "source_tags": tags,
            "picks": [],
            "payload": {},
            "generated_at": None,
        }
    payload = json.loads(row["payload_json"] or "{}")
    tags = json.loads(row["source_tags_json"] or "[]")
    picks = payload.get("picks") if isinstance(payload, dict) else []
    if not isinstance(picks, list):
        picks = []
    return {
        "prediction_date": prediction_date,
        "style_id": row["style_id"],
        "name": row["name"] or style_id,
        "status": row["status"],
        "gate_label": row["gate_label"],
        "error": row["error"],
        "source_tags": tags,
        "picks": picks,
        "payload": payload,
        "generated_at": row["generated_at"],
    }


def read_day(
    prediction_date: str,
    *,
    style_ids: tuple[str, ...] | list[str] | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """返回固定顺序四槽（或指定 style_ids）；缺槽补 missing."""

    order = tuple(style_ids) if style_ids is not None else STYLE_ORDER
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = {
            r["style_id"]: r
            for r in conn.execute(
                "SELECT * FROM style_contrast_snapshots WHERE prediction_date=?",
                (prediction_date,),
            )
        }
    return [_row_to_slot(rows.get(sid), sid, prediction_date) for sid in order]


def read_latest_day(db_path: str | Path | None = None) -> tuple[str | None, list[dict[str, Any]]]:
    """最新有数据的 prediction_date + 四槽."""

    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT prediction_date FROM style_contrast_snapshots "
            "ORDER BY prediction_date DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None, [_row_to_slot(None, sid, "") for sid in STYLE_ORDER]
    d = row["prediction_date"]
    return d, read_day(d, db_path=db_path)
