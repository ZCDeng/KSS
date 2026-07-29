"""U7: get_signal_cards 等工具注册与查询。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_app_bridge as bridge  # noqa: E402
import kss_chat_loop as chat  # noqa: E402

from kss.storage.signal_cards import write_cards


def test_commands_registered() -> None:
    for cmd in ("signal-cards", "etf-radar", "daily-review-archive"):
        assert cmd in bridge.COMMANDS
        assert cmd not in bridge.WRITE_COMMANDS


def test_tool_specs_in_schema() -> None:
    names = {s["name"] for s in chat.TOOL_SPECS}
    assert "get_signal_cards" in names
    assert "get_etf_radar" in names
    assert "get_daily_review_archive" in names
    schema_names = {
        item["function"]["name"] for item in chat.build_tools_schema()
    }
    assert "get_signal_cards" in schema_names


def test_query_by_date_and_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "kss.db"
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    (tmp_path / "storage").mkdir()
    write_cards(
        [
            {
                "card_id": "e1",
                "card_type": "etf_flow",
                "trade_date": "20260717",
                "subject": "芯片",
            },
            {
                "card_id": "s1",
                "card_type": "sector_move",
                "trade_date": "20260717",
                "subject": "光刻机",
            },
            {
                "card_id": "v1",
                "card_type": "volume_spike",
                "trade_date": "20260717",
                "subject": "688017.SH",
            },
        ],
        db_path=db,
    )
    # bridge helpers use STATE_ROOT / storage / kss.db
    import shutil

    shutil.move(str(db), str(tmp_path / "storage" / "kss.db"))
    out = bridge.dispatch("signal-cards", ["", "20260717", "", ""])
    assert out["count"] >= 3
    types = {c["card_type"] for c in out["cards"]}
    assert {"etf_flow", "sector_move", "volume_spike"} <= types
    # 交叉：三类同时存在且 card_id 可回查
    ids = {c["card_id"] for c in out["cards"]}
    assert "e1" in ids and "s1" in ids and "v1" in ids

    filtered = bridge.dispatch("signal-cards", ["", "20260717", "", "etf_flow"])
    assert all(c["card_type"] == "etf_flow" for c in filtered["cards"])

    empty = bridge.dispatch("signal-cards", ["", "19990101", "", ""])
    assert empty["count"] == 0
    assert empty["cards"] == []
