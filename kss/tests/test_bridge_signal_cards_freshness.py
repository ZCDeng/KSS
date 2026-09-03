"""信号卡落后上游 ETF/板块快照的自检（2026-08-14 假成功事故）。

对照物是 etf_radar_snapshots / sector_rotation_snapshots，不是 sentinel CSV——
那次事故里 STATE_ROOT 的 cs_data 是绿的，卡层仍停在 8/14。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402

from kss.storage.db import connect, ensure_schema  # noqa: E402
from kss.storage.signal_cards import write_cards  # noqa: E402

_OPEN_DAYS = {"20260716", "20260717", "20260720", "20260721", "20260722"}


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    (tmp_path / "storage").mkdir()
    monkeypatch.setattr(b, "_is_trade_day", lambda d: d in _OPEN_DAYS)
    return tmp_path


def _write_etf(root: Path, trade_date: str) -> None:
    db = root / "storage" / "kss.db"
    with connect(db) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO etf_radar_snapshots "
            "(trade_date, payload_json, created_at) VALUES (?,?,?)",
            (trade_date, "{}", None),
        )


def _write_card(root: Path, trade_date: str) -> None:
    write_cards(
        [
            {
                "card_id": f"sector_move:{trade_date}:x",
                "trade_date": trade_date,
                "card_type": "sector_move",
                "subject": "x",
            }
        ],
        db_path=root / "storage" / "kss.db",
    )


def test_skip_when_no_upstream_snapshots(state_root: Path) -> None:
    r = b._signal_cards_freshness()
    assert r["ok"] is True
    assert r["skipped"] is True


def test_fresh_when_cards_match_etf(state_root: Path) -> None:
    _write_etf(state_root, "20260722")
    _write_card(state_root, "20260722")
    r = b._signal_cards_freshness()
    assert r["ok"] is True
    assert r["skipped"] is False
    assert r["cards"] == "20260722"


def test_fresh_when_cards_on_previous_session(state_root: Path) -> None:
    """宽限恰好 1 个交易日：卡停在上一交易日不算陈旧。"""
    _write_etf(state_root, "20260722")
    _write_card(state_root, "20260721")
    r = b._signal_cards_freshness()
    assert r["ok"] is True


def test_stale_when_cards_stuck_in_the_past(state_root: Path) -> None:
    _write_etf(state_root, "20260722")
    _write_card(state_root, "20260716")
    r = b._signal_cards_freshness()
    assert r["ok"] is False
    assert r["cards"] == "20260716"
    assert r["reference"] == "20260722"


def test_stale_when_upstream_exists_but_no_cards(state_root: Path) -> None:
    _write_etf(state_root, "20260722")
    r = b._signal_cards_freshness()
    assert r["ok"] is False
    assert r["cards"] is None


def test_selfcheck_item_fails_when_cards_lag(state_root: Path) -> None:
    _write_etf(state_root, "20260722")
    _write_card(state_root, "20260716")
    item = b._check_signal_cards_freshness()
    assert item["item"] == "signal_cards"
    assert item["status"] == "fail"
    assert "20260716" in item["detail"]


def test_notify_pushes_when_cards_lag(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_etf(state_root, "20260722")
    _write_card(state_root, "20260716")
    sent: list[str] = []

    def fake_send(message: str, channel: str, title: str | None = None, **kw):  # noqa: ANN202
        sent.append(title or "")
        return {"telegram": True}

    monkeypatch.setattr("kss.notifications.manager.send_to_channels", fake_send)
    r = b._cs_freshness_cmd(notify=True)
    assert r["notified"] is True
    assert any("信号卡" in t for t in sent)


def test_self_check_includes_signal_cards_item(state_root: Path) -> None:
    result = b._self_check()
    names = [item["item"] for item in result["items"]]
    assert "signal_cards" in names
