"""Bridge 风格对照载荷 + 影子写入 U5."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def test_style_contrasts_missing_db_returns_slots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    date, slots = b._style_contrasts({})
    assert date is None
    assert len(slots) == 4
    assert all(s["status"] in ("missing", "ok", "failed") for s in slots)
    assert slots[0]["styleId"].startswith("style_")


def test_style_contrasts_after_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    db = tmp_path / "storage" / "kss.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    from kss.storage.style_contrast import STATUS_OK, write_style_slot

    write_style_slot(
        "2026-07-30",
        "style_low_vol",
        status=STATUS_OK,
        payload={
            "picks": [
                {
                    "symbol": "600000.SH",
                    "factor_value": 0.01,
                    "rank_position": 1,
                    "planned_weight": 1.0,
                    "selection_reason": "低波",
                }
            ]
        },
        gate_label="research_blocked",
        db_path=db,
    )
    date, slots = b._style_contrasts({"600000.SH": {"name": "浦发", "industry": "银行"}})
    assert date == "2026-07-30"
    by = {s["styleId"]: s for s in slots}
    assert by["style_low_vol"]["status"] == "ok"
    assert by["style_low_vol"]["picks"][0]["name"] == "浦发"
    assert by["style_value"]["status"] == "missing"


def test_shadow_write_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    db = tmp_path / "storage" / "kss.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    from kss.storage.paper_trade_shadow import read_style_day
    from kss.storage.style_contrast import STATUS_OK, write_style_slot

    write_style_slot(
        "2026-07-30",
        "style_value",
        status=STATUS_OK,
        payload={
            "picks": [
                {
                    "symbol": "600001.SH",
                    "factor_value": 0.8,
                    "rank_position": 1,
                    "planned_weight": 1.0,
                }
            ]
        },
        db_path=db,
    )
    result = b._run_style_contrast_shadow_write(
        {"style_id": "style_value", "date": "2026-07-30"}
    )
    assert result["status"] == "success"
    got = read_style_day("2026-07-30", "style_value", db_path=db)
    assert got is not None
    assert got["picks"][0]["symbol"] == "600001.SH"


def test_run_tasks_whitelist_includes_style() -> None:
    assert "style-contrast-daily" in b.RUN_TASKS
    assert "style-contrast-shadow-write" in b.RUN_TASKS
