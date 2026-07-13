"""资讯雷达数据层 —— 移植自 Vibe-Research (https://github.com/simonlin1212/Vibe-Research).

抓 12 赛道 108 个公开 RSS 源 → 合规过滤（赌/预测市场/加密/色情）+ 最近 N 天
+ 按赛道分组、时间倒序。纯标准库 + 线程池，零 key、零个股字段。

AI「今日要点」不在此模块——复用 KSS 的 seeksaw chat 链路。
本模块只出客观资讯，供 SwiftUI IntelView 消费。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES_FILE = HERE / "news_sources.json"

# 可变状态根（bundle 双根：dev 下 ≈ 仓库根，bundle 下 ≈ ~/Library/Application Support/KSS）。
_STATE_ROOT = Path(os.environ["KSS_STATE_ROOT"]) if os.environ.get("KSS_STATE_ROOT") else HERE.parents[1]
DB_PATH = _STATE_ROOT / "storage" / "kss.db"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BEIJING = timezone(timedelta(hours=8))


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_dt(s: str):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fetch_source(src: dict, per: int, cutoff, redline: list[str]):
    """抓单个 RSS 源；返回 items 列表，出错返回 None。"""
    try:
        req = urllib.request.Request(src["url"], headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
        })
        with urllib.request.urlopen(req, timeout=14) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        out = []
        for n in [e for e in root.iter() if _local(e.tag) in ("item", "entry")]:
            if len(out) >= per:
                break
            d = {"title": "", "url": "", "time": "", "ts": 0, "summary": "", "source": src["name"]}
            rawtime = ""
            for c in n:
                t = _local(c.tag)
                if t == "title" and not d["title"]:
                    d["title"] = (c.text or "").strip()
                elif t == "link" and not d["url"]:
                    d["url"] = c.get("href") or (c.text or "").strip()
                elif t in ("pubDate", "published", "updated", "date") and not rawtime:
                    rawtime = (c.text or "").strip()
                elif t in ("description", "summary", "content") and not d["summary"]:
                    d["summary"] = _strip_html(c.text or "")[:160]
            if not d["title"]:
                continue
            blob = (d["title"] + " " + d["summary"]).lower()
            if any(k in blob for k in redline):  # 合规红线过滤
                continue
            dt = _parse_dt(rawtime)
            if dt is not None:
                if cutoff and dt < cutoff:
                    continue
                d["time"] = dt.astimezone(BEIJING).strftime("%m-%d %H:%M")
                d["ts"] = int(dt.timestamp())
            else:
                d["time"] = "—"
            out.append(d)
        return out
    except Exception:
        return None


def fetch_radar() -> dict:
    """抓全部源，返回 12 赛道数据并落盘缓存。"""
    cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    days = cfg.get("fetch", {}).get("recent_days", 7)
    per = cfg.get("fetch", {}).get("per_source", 6)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    redline = [k.lower() for k in cfg.get("redline_keywords", [])]

    byhint: dict[str, list] = {}
    for s in cfg["sources"]:
        byhint.setdefault(s["hint"], []).append(s)

    industries, tasks = [], []
    for i, ind in enumerate(cfg["industries"]):
        pool = byhint.get(ind["key"], [])
        industries.append({
            "key": ind["key"], "name": ind["name"], "accent": ind["accent"],
            "total": len(pool), "items": [],
        })
        for s in pool:
            tasks.append((i, s))

    with ThreadPoolExecutor(max_workers=40) as ex:
        results = list(ex.map(lambda t: (t[0], _fetch_source(t[1], per, cutoff, redline)), tasks))

    failed = 0
    for idx, items in results:
        if items is None:
            failed += 1
            continue
        industries[idx]["items"].extend(items)
    for ind in industries:
        ind["items"].sort(key=lambda x: x.get("ts", 0), reverse=True)

    data = {
        "generated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        "recent_days": days,
        "industries": industries,
        "stats": {
            "industries": len(cfg["industries"]),
            "total_sources": len(cfg["sources"]),
            "failed_sources": failed,
        },
    }
    from kss.storage.db import connect, ensure_schema

    with connect(DB_PATH) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO intel_radar_cache (singleton, payload_json, generated_at) VALUES ('default', ?, ?)",
            (json.dumps(data, ensure_ascii=False), data.get("generated_at")),
        )
    return data


def load_cache() -> dict | None:
    """读缓存；kss.db 无行或损坏返回 None。"""
    from kss.storage.db import connect, ensure_schema

    try:
        with connect(DB_PATH) as conn:
            ensure_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM intel_radar_cache WHERE singleton='default'"
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None
    except json.JSONDecodeError:
        return None


def skeleton() -> dict:
    """无缓存时返回赛道骨架（空 items），前端提示点刷新。"""
    cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    byhint: dict[str, int] = {}
    for s in cfg["sources"]:
        byhint[s["hint"]] = byhint.get(s["hint"], 0) + 1
    return {
        "generated_at": None,
        "recent_days": cfg.get("fetch", {}).get("recent_days", 7),
        "industries": [
            {
                "key": i["key"], "name": i["name"], "accent": i["accent"],
                "total": byhint.get(i["key"], 0), "items": [],
            }
            for i in cfg["industries"]
        ],
        "stats": {"industries": len(cfg["industries"]), "total_sources": len(cfg["sources"])},
    }


def get_radar(force: bool = False) -> dict:
    """返回雷达数据。``force`` 为 True 时重新抓取；否则优先读缓存。"""
    if force:
        return fetch_radar()
    return load_cache() or skeleton()
