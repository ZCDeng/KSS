"""yupi 热点 → 资讯雷达 item 映射、词表 reconcile、缓存合并。

纯逻辑（map/redline/dedupe/merge）可单测；HTTP 经 ``YupiClient`` 注入。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

from kss.news.radar import BEIJING, DB_PATH, SOURCES_FILE, load_cache
from kss.news.track_keywords import load_keywords
from kss.news.yupi_client import YupiClient, YupiError

SOURCE_PREFIX = "热议·"


def _redline_list() -> list[str]:
    cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    return [k.lower() for k in cfg.get("redline_keywords", [])]


def hits_redline(title: str, summary: str = "", redline: list[str] | None = None) -> bool:
    rl = redline if redline is not None else _redline_list()
    blob = f"{title} {summary}".lower()
    return any(k in blob for k in rl)


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
        # drop fragment; lowercase host
        netloc = (p.netloc or "").lower()
        path = p.path or ""
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")
        return urlunparse((p.scheme.lower(), netloc, path, "", p.query, ""))
    except Exception:
        return u


def _collapse_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip()).lower()


def map_hotspot(h: dict[str, Any], redline: list[str] | None = None) -> dict[str, Any] | None:
    """yupi hotspot → radar item；红线命中返回 None。"""
    title = (h.get("title") or h.get("text") or "").strip()
    if not title:
        return None
    summary = (h.get("summary") or h.get("content") or h.get("description") or "")
    if isinstance(summary, str):
        summary = summary.strip()[:160]
    else:
        summary = ""
    if hits_redline(title, summary, redline):
        return None
    url = (h.get("url") or h.get("link") or "").strip()
    platform = (
        h.get("source")
        or h.get("platform")
        or (h.get("keyword") or {}).get("text")
        or "yupi"
    )
    if isinstance(platform, dict):
        platform = platform.get("text") or platform.get("name") or "yupi"
    platform = str(platform).strip() or "yupi"
    source = f"{SOURCE_PREFIX}{platform}"

    raw_time = h.get("publishedAt") or h.get("published_at") or h.get("createdAt") or h.get("created_at") or ""
    ts = 0
    time_s = "—"
    if raw_time:
        try:
            if isinstance(raw_time, (int, float)):
                dt = datetime.fromtimestamp(float(raw_time) / (1000 if raw_time > 1e12 else 1), tz=timezone.utc)
            else:
                s = str(raw_time).strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            time_s = dt.astimezone(BEIJING).strftime("%m-%d %H:%M")
            ts = int(dt.timestamp())
        except Exception:
            pass

    return {
        "title": title,
        "url": url,
        "time": time_s,
        "ts": ts,
        "summary": summary,
        "source": source,
    }


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """URL 优先，其次折叠 title；同键保留 summary 更长者。"""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for it in items:
        url_n = normalize_url(it.get("url") or "")
        key = url_n if url_n else f"t:{_collapse_title(it.get('title') or '')}"
        if not key or key == "t:":
            continue
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = it
            order.append(key)
            continue
        # prefer longer summary; if yupi vs rss same url keep longer summary
        if len(it.get("summary") or "") > len(prev.get("summary") or ""):
            by_key[key] = it
    return [by_key[k] for k in order]


def merge_track_items(rss_items: list[dict[str, Any]], yupi_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = dedupe_items(list(rss_items) + list(yupi_items))
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return merged


def reconcile_keywords(
    client: YupiClient,
    tracks: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """以 KSS 词表为权威，同步到 yupi（category=track_key）。

    - 缺失文本 → create
    - 同 category 多余 → isActive=false
    - 文本在 KSS 集合内但 inactive → 重新 active
    """
    desired = tracks if tracks is not None else load_keywords()
    desired_pairs: set[tuple[str, str]] = set()
    for track, words in desired.items():
        for w in words:
            desired_pairs.add((track, w))

    existing = client.list_keywords()
    by_pair: dict[tuple[str, str], dict] = {}
    for kw in existing:
        text = (kw.get("text") or "").strip()
        cat = (kw.get("category") or "").strip()
        if text and cat:
            by_pair[(cat, text)] = kw

    created = 0
    deactivated = 0
    reactivated = 0

    # create missing
    for track, text in desired_pairs:
        if (track, text) not in by_pair:
            try:
                client.create_keyword(text, category=track)
                created += 1
            except YupiError:
                # 409 race: ignore
                pass

    # refresh list after creates
    existing = client.list_keywords()
    by_pair = {}
    for kw in existing:
        text = (kw.get("text") or "").strip()
        cat = (kw.get("category") or "").strip()
        if text and cat:
            by_pair[(cat, text)] = kw

    # deactivate extras in our track categories; reactivate needed
    our_tracks = set(desired.keys())
    for kw in existing:
        kid = kw.get("id")
        if not kid:
            continue
        text = (kw.get("text") or "").strip()
        cat = (kw.get("category") or "").strip()
        if cat not in our_tracks:
            continue
        active = kw.get("isActive", True)
        if (cat, text) in desired_pairs:
            if active is False:
                try:
                    client.update_keyword(str(kid), is_active=True)
                    reactivated += 1
                except YupiError:
                    pass
        else:
            if active is not False:
                try:
                    client.update_keyword(str(kid), is_active=False)
                    deactivated += 1
                except YupiError:
                    pass

    # map track -> keyword ids for desired
    track_kw_ids: dict[str, list[str]] = {t: [] for t in desired}
    for kw in client.list_keywords():
        text = (kw.get("text") or "").strip()
        cat = (kw.get("category") or "").strip()
        if (cat, text) in desired_pairs and kw.get("id"):
            track_kw_ids.setdefault(cat, []).append(str(kw["id"]))

    return {
        "created": created,
        "deactivated": deactivated,
        "reactivated": reactivated,
        "track_keyword_ids": track_kw_ids,
    }


def fetch_yupi_items_by_track(
    client: YupiClient,
    track_keyword_ids: dict[str, list[str]],
    *,
    per_keyword: int = 20,
    redline: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    rl = redline if redline is not None else _redline_list()
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in track_keyword_ids}
    for track, kids in track_keyword_ids.items():
        acc: list[dict[str, Any]] = []
        for kid in kids:
            try:
                hotspots = client.list_hotspots(keyword_id=kid, limit=per_keyword, time_range="7d")
            except YupiError:
                continue
            for h in hotspots:
                item = map_hotspot(h, rl)
                if item:
                    acc.append(item)
        out[track] = dedupe_items(acc)
    return out


def merge_yupi_into_payload(
    data: dict[str, Any],
    by_track: dict[str, list[dict[str, Any]]],
    *,
    yupi_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 by_track 热议并入 radar payload industries[].items。"""
    industries = data.get("industries") or []
    for ind in industries:
        key = ind.get("key") or ""
        rss = ind.get("items") or []
        yitems = by_track.get(key) or []
        ind["items"] = merge_track_items(rss, yitems)
    stats = dict(data.get("stats") or {})
    if yupi_status is not None:
        stats["yupi"] = yupi_status
    data["stats"] = stats
    return data


def write_radar_cache(data: dict[str, Any]) -> None:
    from kss.storage.db import connect, ensure_schema

    with connect(DB_PATH) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO intel_radar_cache (singleton, payload_json, generated_at) VALUES ('default', ?, ?)",
            (json.dumps(data, ensure_ascii=False), data.get("generated_at")),
        )


def ingest_and_merge(
    *,
    client: YupiClient | None = None,
    data: dict[str, Any] | None = None,
    skip_check: bool = False,
    check_timeout: float = 180.0,
    ensure_runtime: bool = True,
) -> dict[str, Any]:
    """Health → reconcile → optional check → pull → merge → write cache.

    默认先 ``yupi_runtime.ensure(start=True)`` 拉起 KSS 托管实例，再 HTTP 旁路。
    失败时：若有 data/cache 则原样写回 stats.yupi 失败信息；不抛。
    返回最终 payload（可能仅含 yupi_status 失败说明）。
    """
    payload = data if data is not None else (load_cache() or {})
    if not payload.get("industries"):
        # empty skeleton-like
        from kss.news.radar import skeleton

        payload = skeleton()

    status: dict[str, Any] = {"ok": False, "skipped": False, "reason": ""}
    runtime_meta: dict[str, Any] = {}
    if ensure_runtime and client is None:
        # 热路径（force 刷新 / cron 灌入）禁止完整 npm install：只拉起已安装实例。
        # 首次安装走 yupi-ensure / 自检 / 设置页「安装/启动」。
        try:
            import os as _os

            from kss.news.yupi_runtime import base_url as managed_url
            from kss.news.yupi_runtime import start_background, status as yupi_status

            st = yupi_status()
            _os.environ.setdefault("YUPI_BASE_URL", managed_url())
            if st.get("health_ok"):
                runtime_meta = {"ok": True, "action": "already_healthy", "base_url": managed_url()}
            elif st.get("installed"):
                runtime_meta = start_background(allow_install=False)
            else:
                runtime_meta = {
                    "ok": False,
                    "skipped": True,
                    "reason": "yupi not installed; run yupi-ensure or Settings install",
                }
        except Exception as e:  # noqa: BLE001
            runtime_meta = {"ok": False, "error": f"ensure: {e}"}

    cli = client or YupiClient()

    try:
        cli.health()
    except YupiError as e:
        status = {
            "ok": False,
            "skipped": True,
            "reason": f"health: {e}",
            "runtime": runtime_meta,
        }
        payload = merge_yupi_into_payload(payload, {}, yupi_status=status)
        try:
            write_radar_cache(payload)
        except Exception:
            pass
        return payload

    try:
        recon = reconcile_keywords(cli)
        if not skip_check:
            cli.check_hotspots(timeout=check_timeout)
        by_track = fetch_yupi_items_by_track(cli, recon.get("track_keyword_ids") or {})
        n = sum(len(v) for v in by_track.values())
        status = {
            "ok": True,
            "skipped": False,
            "reason": "",
            "reconcile": {k: recon[k] for k in ("created", "deactivated", "reactivated") if k in recon},
            "items": n,
        }
        payload = merge_yupi_into_payload(payload, by_track, yupi_status=status)
    except YupiError as e:
        status = {"ok": False, "skipped": True, "reason": str(e)}
        payload = merge_yupi_into_payload(payload, {}, yupi_status=status)

    try:
        write_radar_cache(payload)
    except Exception as e:
        status["cache_write_error"] = str(e)
        payload.setdefault("stats", {})["yupi"] = status
    return payload


def fetch_radar_with_yupi(*, skip_check: bool = True) -> dict[str, Any]:
    """RSS fetch_radar 后 yupi merge（yupi 失败不影响 RSS 结果）。

    默认 ``skip_check=True``：UI force 热路径不跑 ``check_hotspots``（可至 180s），
    只合并 yupi DB 已有热点。盘前/盘后 cron 在 shell 里可显式 ``skip_check=False``。
    """
    from kss.news.radar import fetch_radar

    data = fetch_radar()
    return ingest_and_merge(data=data, skip_check=skip_check)
