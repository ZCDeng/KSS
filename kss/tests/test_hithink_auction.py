"""U6: auction universe, persist, cache fallback."""

from __future__ import annotations

import json
from pathlib import Path

from kss.data import hithink_auction as ha
from kss.data.hithink_auction import (
    fetch_auction_snapshot,
    is_a_share_stock,
    load_auction_payload,
)
from kss.data.hithink_client import HithinkClient


class _FakeClient(HithinkClient):
    def __init__(self, data):
        super().__init__(api_key="fake")
        self._data = data
        self.calls: list[tuple] = []

    def get_auction_snapshot(self, thscodes, *, stage="live"):
        self.calls.append((list(thscodes), stage))
        return self._data


def test_auction_live_fixture_keeps_nulls_and_status() -> None:
    path = Path(__file__).resolve().parent / "fixtures" / "hithink" / "auction_live.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "data_status" in data
    assert "item" in data
    row = data["item"][0]
    for key in ("auction_price", "auction_pct", "auction_unmatched"):
        assert key in row
        assert row[key] is None or isinstance(row[key], (int, float))
    assert is_a_share_stock("600519.SH")
    assert is_a_share_stock("300750.SZ")
    assert is_a_share_stock("830799.BJ")
    assert not is_a_share_stock("510300.SH")
    assert not is_a_share_stock("159915.SZ")
    assert not is_a_share_stock("000001.SH")


def test_fetch_auction_persists_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ha, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr(ha, "load_auction_universe", lambda limit=100: ["600519.SH", "300750.SZ"])
    data = {
        "timestamp": 1,
        "auction_phase": "live",
        "data_status": "not_ready",
        "total": 2,
        "item": [
            {"thscode": "600519.SH", "auction_price": None, "auction_pct": None},
        ],
    }
    payload = fetch_auction_snapshot(stage="live", persist=True, client=_FakeClient(data))
    assert "error" not in payload
    path = ha.auction_path(payload["trade_date"], "live")
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["data"]["data_status"] == "not_ready"
    assert saved["data"]["item"][0]["auction_price"] is None


def test_from_cache_reads_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ha, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr(ha, "shanghai_today_iso", lambda: "2026-09-12")
    path = ha.auction_path("2026-09-12", "live")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"stage": "live", "data": {"data_status": "final"}}),
        encoding="utf-8",
    )
    out = load_auction_payload(stage="live", from_cache="true")
    assert out["from_cache"] is True
    assert out["data"]["data_status"] == "final"


def test_unknown_thscode_dropped_and_retried() -> None:
    class _DropUnknown(HithinkClient):
        def __init__(self) -> None:
            super().__init__(api_key="fake")
            self.calls: list[list[str]] = []
            self.last_error = None

        def get_auction_snapshot(self, thscodes, *, stage="live"):
            codes = list(thscodes)
            self.calls.append(codes)
            if "830799.BJ" in codes:
                self.last_error = {"code": 1002, "message": "Unknown thscode `830799.BJ`"}
                return None
            self.last_error = None
            return {"data_status": "not_ready", "item": [{"thscode": c} for c in codes]}

    client = _DropUnknown()
    out = fetch_auction_snapshot(
        stage="live",
        thscodes=["600519.SH", "830799.BJ", "300750.SZ"],
        client=client,
    )
    assert "error" not in out
    assert out["dropped_thscodes"] == ["830799.BJ"]
    assert "830799.BJ" not in out["thscodes"]
    assert client.calls[0] == ["600519.SH", "830799.BJ", "300750.SZ"]
    assert client.calls[1] == ["600519.SH", "300750.SZ"]


def test_unknown_thscode_parser() -> None:
    from kss.data.hithink_auction import unknown_thscode_from_error

    assert unknown_thscode_from_error(
        {"code": 1002, "message": "Unknown thscode `830799.BJ`"}
    ) == "830799.BJ"
    assert unknown_thscode_from_error(
        {"code": 1002, "message": "Unknown A-share thscode: 830799.BJ"}
    ) == "830799.BJ"
    assert unknown_thscode_from_error({"message": "rate limited"}) is None


def test_empty_universe_does_not_call_api(monkeypatch) -> None:
    monkeypatch.setattr(ha, "load_auction_universe", lambda limit=100: [])
    client = _FakeClient({"item": []})
    out = fetch_auction_snapshot(stage="live", client=client)
    assert out["error"] == "empty_universe"
    assert client.calls == []
