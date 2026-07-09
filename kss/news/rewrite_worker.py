"""Top-K auto rewrite worker (plan 2026-07-10-001 U3).

Serial per item; ready-draft budget per track/day; claim protocol; soft wall-clock.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from kss.news.radar import get_radar
from kss.news.rewrite import run_rewrite
from kss.news.rewrite_config import TOP_K, WORKER_MAX_LLM_CALLS, WORKER_WALL_SEC
from kss.storage.rewrite_pool import beijing_day, count_ready, item_id_for, read_draft

logger = logging.getLogger(__name__)


def _item_ts(item: dict[str, Any]) -> float:
    try:
        return float(item.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def run_top_k_rewrites(
    *,
    k: int | None = None,
    force: bool = False,
    wall_sec: float | None = None,
    max_llm_calls: int | None = None,
    radar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rewrite up to K ready drafts per track for today.

    Never raises for single-item failures. Returns summary counts.
    """
    k = TOP_K if k is None else k
    wall = WORKER_WALL_SEC if wall_sec is None else wall_sec
    max_calls = WORKER_MAX_LLM_CALLS if max_llm_calls is None else max_llm_calls
    day = beijing_day()
    t0 = time.time()
    data = radar if radar is not None else get_radar(force=False)

    # radar.py cache uses `industries`; bridge maps to tracks for Swift.
    tracks = data.get("industries") or data.get("tracks") or []

    summary = {
        "day": day,
        "k": k,
        "tracks": 0,
        "attempted": 0,
        "ready_new": 0,
        "failed": 0,
        "skipped": 0,
        "stopped_reason": None,
        "per_track": {},
    }

    llm_calls = 0
    for tr in tracks:
        if time.time() - t0 > wall:
            summary["stopped_reason"] = "wall_clock"
            break
        if llm_calls >= max_calls:
            summary["stopped_reason"] = "max_llm_calls"
            break

        key = tr.get("key") or tr.get("id") or ""
        name = tr.get("name") or key
        items = list(tr.get("items") or [])
        if not key or not items:
            continue
        summary["tracks"] += 1
        already = count_ready(key, day)
        need = max(0, k - already)
        track_stats = {"already_ready": already, "need": need, "new_ready": 0, "failed": 0}
        if need == 0 and not force:
            summary["skipped"] += len(items)
            summary["per_track"][key] = track_stats
            continue

        ranked = sorted(items, key=_item_ts, reverse=True)
        for item in ranked:
            if time.time() - t0 > wall:
                summary["stopped_reason"] = "wall_clock"
                break
            if llm_calls >= max_calls:
                summary["stopped_reason"] = "max_llm_calls"
                break
            if count_ready(key, day) >= k and not force:
                break

            iid = item_id_for(item)
            existing = read_draft(iid)
            if existing and existing.get("status") == "ready" and not force:
                summary["skipped"] += 1
                continue
            if existing and existing.get("status") == "generating" and not force:
                summary["skipped"] += 1
                continue

            summary["attempted"] += 1
            llm_calls += 1
            try:
                result = run_rewrite(
                    key,
                    name,
                    item,
                    force=force,
                    fetch_body=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("rewrite worker item failed: %s", e)
                summary["failed"] += 1
                track_stats["failed"] += 1
                continue

            if result.get("status") == "ready":
                if not result.get("from_cache"):
                    summary["ready_new"] += 1
                    track_stats["new_ready"] += 1
            elif result.get("status") == "failed":
                summary["failed"] += 1
                track_stats["failed"] += 1
            else:
                summary["skipped"] += 1

        summary["per_track"][key] = track_stats
        if summary["stopped_reason"]:
            break

    summary["elapsed_sec"] = round(time.time() - t0, 2)
    summary["llm_calls"] = llm_calls
    if os.environ.get("KSS_REWRITE_DEBUG"):
        logger.info("rewrite worker done: %s", summary)
    return summary
