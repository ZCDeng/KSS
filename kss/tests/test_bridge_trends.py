# -*- coding: utf-8 -*-
"""R2-U1: 趋势观察 bridge 读路径割接到 kss.db trends_days 表。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load("kss_app_bridge", "scripts/kss_app_bridge.py")

from kss.storage.trends import write_day  # noqa: E402


@pytest.fixture
def state_root(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    return tmp_path


def _payload(date: str, **overrides) -> dict:
    base = {
        "date": date,
        "isTrading": True,
        "heat": 0.5,
        "inflowScore": 1.2,
        "inflowDir": "in",
        "sectorHeat": {"AI": 0.8},
        "recAvgFwd": 0.03,
        "north": 12.5,
        "sectorCount": 3,
        "topSector": "AI",
        "recCount": 5,
        "flags": {"reviewed": True},
    }
    base.update(overrides)
    return base


def test_dispatch_trends_month_and_day_from_db(state_root):
    db_path = state_root / "storage" / "kss.db"
    write_day(_payload("2026-07-08"), db_path=db_path)
    write_day(_payload("2026-07-09"), db_path=db_path)

    month = bridge.dispatch("trends-month", ["2026-07"])
    assert month["month"] == "2026-07"
    assert {d["date"] for d in month["days"]} == {"2026-07-08", "2026-07-09"}
    assert all(d["hasData"] for d in month["days"])

    day = bridge.dispatch("trends-day", ["2026-07-08"])
    assert day["found"] is True
    assert day["date"] == "2026-07-08"
    assert day["north"] == 12.5


def test_empty_month_returns_no_days_without_error(state_root):
    db_path = state_root / "storage" / "kss.db"
    write_day(_payload("2026-07-08"), db_path=db_path)

    month = bridge.dispatch("trends-month", ["2026-08"])
    assert month["days"] == []
    assert "error" not in month


def test_missing_day_returns_found_false(state_root):
    db_path = state_root / "storage" / "kss.db"
    write_day(_payload("2026-07-08"), db_path=db_path)

    day = bridge.dispatch("trends-day", ["2026-07-01"])
    assert day == {"date": "2026-07-01", "found": False}


def test_read_month_does_not_leak_across_month_boundary(state_root):
    db_path = state_root / "storage" / "kss.db"
    write_day(_payload("2026-06-30"), db_path=db_path)
    write_day(_payload("2026-07-01"), db_path=db_path)

    month = bridge.dispatch("trends-month", ["2026-07"])
    assert {d["date"] for d in month["days"]} == {"2026-07-01"}


def test_missing_db_returns_empty_without_creating_file(state_root):
    db_path = state_root / "storage" / "kss.db"
    assert not db_path.exists()

    month = bridge.dispatch("trends-month", ["2026-07"])
    assert month["days"] == []
    assert "error" in month
    assert not db_path.exists()

    day = bridge.dispatch("trends-day", ["2026-07-08"])
    assert day["found"] is False
    assert "error" in day
    assert not db_path.exists()
