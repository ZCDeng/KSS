"""rewrite_pool storage tests."""

from __future__ import annotations

from kss.storage.rewrite_pool import (
    beijing_day,
    claim_generating,
    count_ready,
    item_id_for,
    list_drafts,
    read_draft,
    write_draft,
)


def test_item_id_stable_from_url():
    a = item_id_for({"url": "https://example.com/a", "title": "x"})
    b = item_id_for({"url": "https://example.com/a", "title": "y"})
    assert a == b
    assert len(a) == 16


def test_claim_and_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    item = {"title": "T", "url": "https://example.com/1", "source": "S", "time": "10:00"}
    ok, d = claim_generating(item, track_key="ai")
    assert ok
    assert d["status"] == "generating"
    assert read_draft(d["item_id"])["status"] == "generating"

    ok2, d2 = claim_generating(item, track_key="ai")
    assert not ok2  # non-stale generating

    ready = {**d, "status": "ready", "text": "- hi", "day": beijing_day()}
    write_draft(ready)
    assert count_ready("ai") == 1
    assert list_drafts(track_key="ai", status="ready")[0]["text"] == "- hi"


def test_count_ready_filters_track_day(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    day = beijing_day()
    write_draft(
        {
            "item_id": "aaa",
            "track_key": "ai",
            "day": day,
            "status": "ready",
            "text": "a",
        }
    )
    write_draft(
        {
            "item_id": "bbb",
            "track_key": "tech",
            "day": day,
            "status": "ready",
            "text": "b",
        }
    )
    write_draft(
        {
            "item_id": "ccc",
            "track_key": "ai",
            "day": day,
            "status": "failed",
            "text": "",
        }
    )
    assert count_ready("ai", day) == 1
    assert count_ready("tech", day) == 1
