"""yupi 旁路 map / redline / merge / fail-soft（mock HTTP，无真实 yupi）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kss.news.yupi_client import YupiClient, YupiError
from kss.news.yupi_ingest import (
    SOURCE_PREFIX,
    dedupe_items,
    hits_redline,
    ingest_and_merge,
    map_hotspot,
    merge_track_items,
    merge_yupi_into_payload,
    normalize_url,
    reconcile_keywords,
)


def test_map_hotspot_source_prefix_and_ts():
    h = {
        "title": "国产大模型发布",
        "url": "https://example.com/a",
        "source": "sogou",
        "summary": "要点摘要",
        "publishedAt": "2026-07-20T10:00:00+08:00",
    }
    item = map_hotspot(h, redline=[])
    assert item is not None
    assert item["title"] == "国产大模型发布"
    assert item["source"].startswith(SOURCE_PREFIX)
    assert "sogou" in item["source"]
    assert item["ts"] > 0
    assert item["url"] == "https://example.com/a"


def test_map_hotspot_redline_drop():
    h = {"title": "线上赌场开业", "url": "https://x.com/1", "source": "web"}
    assert map_hotspot(h, redline=["赌博", "赌场"]) is None
    assert hits_redline("线上赌场开业", "", ["赌场"]) is True


def test_merge_ordering_and_dedupe_by_url():
    rss = [
        {"title": "A", "url": "https://Ex.com/x/", "ts": 100, "summary": "s", "source": "OpenAI", "time": "—"},
        {"title": "B", "url": "https://b.com/1", "ts": 50, "summary": "", "source": "X", "time": "—"},
    ]
    yupi = [
        {
            "title": "A longer",
            "url": "https://ex.com/x",
            "ts": 200,
            "summary": "longer summary text",
            "source": f"{SOURCE_PREFIX}bing",
            "time": "—",
        },
        {
            "title": "C",
            "url": "https://c.com/1",
            "ts": 150,
            "summary": "",
            "source": f"{SOURCE_PREFIX}hn",
            "time": "—",
        },
    ]
    merged = merge_track_items(rss, yupi)
    urls = [normalize_url(m["url"]) for m in merged]
    assert urls[0].endswith("/x") or "ex.com/x" in urls[0]
    # same url collapsed to one
    assert sum(1 for u in urls if "ex.com/x" in u) == 1
    # order by ts desc — C (150) before B (50)
    titles = [m["title"] for m in merged]
    assert titles.index("C") < titles.index("B")
    # longer summary kept for A
    a = next(m for m in merged if "ex.com/x" in normalize_url(m["url"]))
    assert "longer" in (a.get("summary") or "")


def test_dedupe_by_title_when_no_url():
    items = [
        {"title": "Same  Title", "url": "", "ts": 1, "summary": "a", "source": "r", "time": "—"},
        {"title": "Same Title", "url": "", "ts": 2, "summary": "bb", "source": f"{SOURCE_PREFIX}x", "time": "—"},
    ]
    out = dedupe_items(items)
    assert len(out) == 1
    assert out[0]["summary"] == "bb"


def test_merge_payload_mixed_list():
    data = {
        "generated_at": "2026-07-21 10:00",
        "industries": [
            {
                "key": "ai",
                "name": "AI",
                "items": [{"title": "RSS", "url": "https://r.com/1", "ts": 10, "source": "OpenAI", "time": "—", "summary": ""}],
            }
        ],
        "stats": {},
    }
    by = {
        "ai": [
            {
                "title": "热议条",
                "url": "https://y.com/1",
                "ts": 20,
                "source": f"{SOURCE_PREFIX}bilibili",
                "time": "—",
                "summary": "",
            }
        ]
    }
    out = merge_yupi_into_payload(data, by, yupi_status={"ok": True})
    items = out["industries"][0]["items"]
    assert len(items) == 2
    assert items[0]["title"] == "热议条"
    assert any(SOURCE_PREFIX in (i.get("source") or "") for i in items)
    assert out["stats"]["yupi"]["ok"] is True


def test_ingest_health_fail_soft_preserves_rss(monkeypatch):
    data = {
        "generated_at": "t",
        "industries": [
            {
                "key": "ai",
                "name": "AI",
                "items": [{"title": "RSS only", "url": "https://r.com/1", "ts": 1, "source": "OpenAI", "time": "—", "summary": ""}],
            }
        ],
        "stats": {},
    }
    cli = MagicMock(spec=YupiClient)
    cli.health.side_effect = YupiError("connection refused")
    monkeypatch.setattr("kss.news.yupi_ingest.write_radar_cache", lambda d: None)
    out = ingest_and_merge(client=cli, data=data, skip_check=True)
    items = out["industries"][0]["items"]
    assert any(i["title"] == "RSS only" for i in items)
    assert out["stats"]["yupi"]["skipped"] is True
    assert out["stats"]["yupi"]["ok"] is False


def test_reconcile_creates_missing_and_deactivates_extra():
    cli = MagicMock(spec=YupiClient)
    created = []
    store = []

    def create(text, category=None):
        created.append((text, category))
        row = {"id": f"id-{text}", "text": text, "category": category, "isActive": True}
        store.append(row)
        return row

    def list_kw():
        return list(store)

    def update(kid, **kwargs):
        for row in store:
            if str(row["id"]) == str(kid):
                if "is_active" in kwargs:
                    row["isActive"] = kwargs["is_active"]
                break
        return {}

    # seed extra keyword only
    store.append({"id": "2", "text": "过时词", "category": "ai", "isActive": True})
    cli.list_keywords.side_effect = list_kw
    cli.create_keyword.side_effect = create
    cli.update_keyword.side_effect = update

    recon = reconcile_keywords(cli, tracks={"ai": ["大模型"]})
    assert any(t == "大模型" and c == "ai" for t, c in created)
    assert any(r["text"] == "过时词" and r["isActive"] is False for r in store)
    assert "1" in "".join(recon["track_keyword_ids"].get("ai", [])) or any(
        "大模型" in (r.get("text") or "") for r in store
    )


def test_ingest_check_fail_keeps_rss(monkeypatch):
    data = {
        "generated_at": "t",
        "industries": [
            {
                "key": "ai",
                "items": [{"title": "RSS", "url": "https://r.com/1", "ts": 1, "source": "O", "time": "—", "summary": ""}],
            }
        ],
        "stats": {},
    }
    cli = MagicMock(spec=YupiClient)
    cli.health.return_value = {"status": "ok"}
    cli.list_keywords.return_value = []
    cli.create_keyword.return_value = {"id": "x"}
    cli.check_hotspots.side_effect = YupiError("timeout")
    monkeypatch.setattr("kss.news.yupi_ingest.write_radar_cache", lambda d: None)
    out = ingest_and_merge(client=cli, data=data, skip_check=False)
    assert out["industries"][0]["items"][0]["title"] == "RSS"
    assert out["stats"]["yupi"]["ok"] is False
