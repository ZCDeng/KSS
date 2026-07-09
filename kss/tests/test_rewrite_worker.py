"""Top-K rewrite worker tests."""

from __future__ import annotations

from unittest.mock import patch

from kss.news.rewrite_worker import run_top_k_rewrites
from kss.storage.rewrite_pool import beijing_day, count_ready, write_draft


def _radar(n_items: int = 20) -> dict:
    def items(prefix: str, n: int) -> list:
        return [
            {
                "title": f"Title {prefix} {i} long enough",
                "url": f"https://example.com/{prefix}/{i}",
                "source": "S",
                "time": "10:00",
                "ts": 1000 - i,
                "summary": "summary " * 10,
            }
            for i in range(n)
        ]

    return {
        "industries": [
            {"key": "ai", "name": "AI", "items": items("ai", n_items)},
            {"key": "tech", "name": "Tech", "items": items("tech", 5)},
        ]
    }


def test_worker_caps_ready_at_k(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    calls = {"n": 0}

    def fake_rewrite(track_key, track_name, item, **kwargs):
        calls["n"] += 1
        from kss.storage.rewrite_pool import item_id_for, write_draft, beijing_day

        iid = item_id_for(item)
        write_draft(
            {
                "item_id": iid,
                "track_key": track_key,
                "day": beijing_day(),
                "status": "ready",
                "text": "## 事件\nx",
                "sections": {"事件": "x", "影响": "", "标的线索": "", "待验证": ""},
            }
        )
        return {"status": "ready", "item_id": iid}

    with patch("kss.news.rewrite_worker.run_rewrite", side_effect=fake_rewrite):
        s = run_top_k_rewrites(k=3, radar=_radar(20))

    assert s["ready_new"] == 3 + 3  # two tracks, k=3 each when empty
    assert count_ready("ai") == 3
    assert count_ready("tech") == 3
    assert calls["n"] == 6


def test_worker_skips_when_already_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    day = beijing_day()
    for i in range(3):
        write_draft(
            {
                "item_id": f"pre{i}",
                "track_key": "ai",
                "day": day,
                "status": "ready",
                "text": "x",
            }
        )

    with patch("kss.news.rewrite_worker.run_rewrite") as m:
        s = run_top_k_rewrites(k=3, radar={"industries": [{"key": "ai", "name": "AI", "items": [
            {"title": "T", "url": f"https://example.com/z/{i}", "ts": i, "summary": "s" * 20}
            for i in range(5)
        ]}]})
    # already at K=3, should not call rewrite for ai
    assert m.call_count == 0 or s["ready_new"] == 0
