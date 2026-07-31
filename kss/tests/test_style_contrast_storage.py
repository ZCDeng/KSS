"""风格对照快照存储 U2."""

from __future__ import annotations

from pathlib import Path

from kss.storage.style_contrast import (
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_OK,
    read_day,
    write_style_slot,
)
from kss.strategies.styles import STYLE_ORDER


def test_write_read_four_slots(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    date = "2026-07-30"
    for i, sid in enumerate(STYLE_ORDER):
        write_style_slot(
            date,
            sid,
            status=STATUS_OK,
            payload={
                "picks": [
                    {
                        "symbol": f"60000{i}.SH",
                        "factor_value": 1.0,
                        "rank_position": 1,
                        "planned_weight": 1.0,
                    }
                ]
            },
            gate_label="research_blocked",
            db_path=db,
        )
    slots = read_day(date, db_path=db)
    assert len(slots) == 4
    assert [s["style_id"] for s in slots] == list(STYLE_ORDER)
    assert all(s["status"] == STATUS_OK for s in slots)
    assert slots[0]["picks"][0]["symbol"] == "600000.SH"
    assert slots[0]["source_tags"]


def test_partial_write_missing_slot(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    date = "2026-07-30"
    write_style_slot(
        date,
        STYLE_ORDER[0],
        status=STATUS_OK,
        payload={"picks": [{"symbol": "1.SH", "rank_position": 1}]},
        db_path=db,
    )
    slots = read_day(date, db_path=db)
    assert slots[0]["status"] == STATUS_OK
    assert slots[1]["status"] == STATUS_MISSING
    assert slots[1]["picks"] == []


def test_failed_slot_persists_error(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    date = "2026-07-30"
    write_style_slot(
        date,
        "style_sector_rotation",
        status=STATUS_FAILED,
        error="板块映射不可用",
        payload={},
        db_path=db,
    )
    slots = read_day(date, db_path=db)
    sec = next(s for s in slots if s["style_id"] == "style_sector_rotation")
    assert sec["status"] == STATUS_FAILED
    assert "板块" in (sec["error"] or "")
