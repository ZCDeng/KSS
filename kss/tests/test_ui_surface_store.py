"""ui_surface config store 单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kss.ui_surface import config as cfg_mod


@pytest.fixture()
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    return tmp_path


def test_load_missing_file_defaults(state_root: Path) -> None:
    c = cfg_mod.load_config()
    assert c["overnight_us"]["append"] == []
    assert c["strip_metric"]["metric_id"] == cfg_mod.DEFAULT_STRIP_METRIC
    assert c["degraded"] is False


def test_load_corrupt_json_degrades(state_root: Path) -> None:
    path = cfg_mod._config_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    c = cfg_mod.load_config()
    assert c["degraded"] is True
    assert c["error"]
    assert c["overnight_us"]["append"] == []


def test_append_max_and_duplicate(state_root: Path) -> None:
    ops = [
        {"op": "overnight_append", "code": f"T{i:03d}", "kind": "yfinance", "name": f"T{i}"}
        for i in range(8)
    ]
    r = cfg_mod.apply_patch(ops)
    assert r["ok"] is True
    assert len(r["config"]["overnight_us"]["append"]) == 8

    r2 = cfg_mod.apply_patch([{"op": "overnight_append", "code": "T999", "kind": "yfinance"}])
    assert r2["ok"] is False
    assert "max" in (r2.get("error") or "").lower() or "8" in (r2.get("error") or "")

    # 幂等：重复已有 code
    r3 = cfg_mod.apply_patch([{"op": "overnight_append", "code": "T000", "kind": "yfinance"}])
    assert r3["ok"] is True
    assert len(r3["config"]["overnight_us"]["append"]) == 8


def test_cannot_append_or_remove_default(state_root: Path) -> None:
    r = cfg_mod.apply_patch([{"op": "overnight_append", "code": "NVDA", "kind": "yfinance"}])
    assert r["ok"] is False
    assert "default" in (r.get("error") or "").lower()

    r2 = cfg_mod.apply_patch([{"op": "overnight_remove", "code": "IXIC"}])
    assert r2["ok"] is False


def test_remove_user_and_atomic_roundtrip(state_root: Path) -> None:
    r = cfg_mod.apply_patch([
        {"op": "overnight_append", "code": "AAPL", "name": "苹果", "kind": "yfinance"},
    ])
    assert r["ok"] is True
    path = cfg_mod._config_path()
    assert path.is_file()
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["overnight_us"]["append"][0]["code"] == "AAPL"

    r2 = cfg_mod.apply_patch([{"op": "overnight_remove", "code": "AAPL"}])
    assert r2["ok"] is True
    assert r2["config"]["overnight_us"]["append"] == []


def test_invalid_code_rejected_no_disk_change(state_root: Path) -> None:
    r = cfg_mod.apply_patch([{"op": "overnight_append", "code": "bad code!!", "kind": "yfinance"}])
    assert r["ok"] is False
    assert not cfg_mod._config_path().is_file()


def test_unknown_op_rejected(state_root: Path) -> None:
    r = cfg_mod.apply_patch([{"op": "drop_table"}])
    assert r["ok"] is False
    assert "unknown" in (r.get("error") or "").lower()


def test_north_metric_rejected(state_root: Path) -> None:
    r = cfg_mod.apply_patch([{"op": "set_strip_metric", "metric_id": "north_money"}])
    assert r["ok"] is False
    assert "北向" in (r.get("error") or "")


def test_set_metric_and_reset(state_root: Path) -> None:
    r = cfg_mod.apply_patch([{"op": "set_strip_metric", "metric_id": "limit_seal_rate"}])
    assert r["ok"] is True
    assert r["config"]["strip_metric"]["metric_id"] == "limit_seal_rate"
    r2 = cfg_mod.apply_patch([{"op": "reset_strip_metric"}])
    assert r2["ok"] is True
    assert r2["config"]["strip_metric"]["metric_id"] == cfg_mod.DEFAULT_STRIP_METRIC
