"""Rewrite draft pool — durable per-item JSON under STATE_ROOT/storage/intel_rewrites/.

Plan 2026-07-10-001 KTD1: body snapshot + status lifecycle generating|ready|failed.
Day key = Asia/Shanghai (BEIJING).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kss.news.rewrite_config import GENERATING_TTL_SEC

BEIJING = timezone(timedelta(hours=8))


def _state_root() -> Path:
    raw = os.environ.get("KSS_STATE_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2]


def pool_dir() -> Path:
    return _state_root() / "storage" / "intel_rewrites"


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


def draft_path(item_id: str) -> Path:
    return pool_dir() / f"{item_id}.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_draft(item_id: str) -> dict[str, Any] | None:
    path = draft_path(item_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_draft(data: dict[str, Any]) -> Path:
    iid = data.get("item_id")
    if not iid:
        raise ValueError("draft missing item_id")
    path = draft_path(str(iid))
    _atomic_write_json(path, data)
    return path


def claim_generating(
    item: dict[str, Any],
    *,
    track_key: str,
    day: str | None = None,
    ttl_sec: float = GENERATING_TTL_SEC,
) -> tuple[bool, dict[str, Any]]:
    """Atomically claim item for rewrite.

    Returns (claimed, draft). claimed=False if ready or non-stale generating exists.
    """
    day = day or beijing_day()
    iid = item_id_for(item)
    path = draft_path(iid)
    existing = read_draft(iid)
    now = time.time()
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
    # O_EXCL-style: if ready appeared under us, don't overwrite ready
    again = read_draft(iid)
    if again and again.get("status") == "ready":
        return False, again
    write_draft(draft)
    return True, draft


def list_drafts(
    *,
    track_key: str | None = None,
    day: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    root = pool_dir()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in root.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if track_key is not None and d.get("track_key") != track_key:
            continue
        if day is not None and d.get("day") != day:
            continue
        if status is not None and d.get("status") != status:
            continue
        out.append(d)
    out.sort(key=lambda x: x.get("generated_at") or x.get("started_at") or "", reverse=True)
    return out


def count_ready(track_key: str, day: str | None = None) -> int:
    day = day or beijing_day()
    return len(list_drafts(track_key=track_key, day=day, status="ready"))
