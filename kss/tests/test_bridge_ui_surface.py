"""bridge surface 命令登记与 apply/get。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.kss_app_bridge as bridge


@pytest.fixture()
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    # bridge 模块级 STATE_ROOT 可能已缓存；surface 走 kss.ui_surface 的 _config_path
    return tmp_path


def test_surface_commands_registered() -> None:
    for cmd in (
        "surface-get",
        "surface-metrics",
        "surface-propose",
        "surface-apply",
        "surface-nl-interpret",
        "surface-catalog",
    ):
        assert cmd in bridge.COMMANDS
    assert "surface-apply" in bridge.WRITE_COMMANDS
    assert "surface-get" not in bridge.WRITE_COMMANDS
    assert "surface-propose" not in bridge.WRITE_COMMANDS
    assert "surface-nl-interpret" not in bridge.WRITE_COMMANDS
    assert "surface-catalog" not in bridge.WRITE_COMMANDS


def test_surface_apply_append_and_get(state_root: Path) -> None:
    ops = [
        {
            "op": "overnight_append",
            "code": "AAPL",
            "name": "苹果",
            "kind": "yfinance",
            "kind_source": "candidate_table",
            "probe_close": 190.0,
            "resolved_at": "2026-07-28T00:00:00+00:00",
        }
    ]
    with patch.object(bridge, "probe_overnight_code", create=True):
        # apply 路径若已有 probe_close 不再探针；直接 patch resolve 内探针保险
        with patch("kss.ui_surface.resolve.probe_overnight_code") as probe:
            probe.return_value = {
                "ok": True,
                "code": "AAPL",
                "name": "苹果",
                "kind": "yfinance",
                "close": 190.0,
                "pct": 1.0,
            }
            result = bridge.dispatch("surface-apply", [json.dumps(ops)])
    assert result.get("ok") is True
    cfg_path = state_root / "storage" / "ui_surface" / "dashboard_v1.json"
    assert cfg_path.is_file()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["overnight_us"]["append"][0]["code"] == "AAPL"

    got = bridge.dispatch("surface-get", [])
    assert got.get("ok") is True
    codes = [a["code"] for a in got["config"]["overnight_us"]["append"]]
    assert "AAPL" in codes


def test_surface_apply_rejects_north_metric(state_root: Path) -> None:
    result = bridge.dispatch(
        "surface-apply",
        [json.dumps([{"op": "set_strip_metric", "metric_id": "north_money"}])],
    )
    assert result.get("ok") is False
    assert "北向" in (result.get("error") or "")


def test_surface_apply_rejects_unknown_op(state_root: Path) -> None:
    result = bridge.dispatch(
        "surface-apply",
        [json.dumps([{"op": "rm_rf"}])],
    )
    assert result.get("ok") is False


def test_surface_propose_does_not_write(state_root: Path) -> None:
    with patch("kss.ui_surface.resolve.probe_overnight_code") as probe:
        probe.return_value = {
            "ok": True,
            "code": "AMD",
            "name": "超威",
            "kind": "yfinance",
            "close": 120.0,
            "pct": 0.5,
        }
        result = bridge.dispatch(
            "surface-propose",
            [json.dumps([{"op": "overnight_append", "code": "AMD"}])],
        )
    assert result.get("ok") is True
    assert not (state_root / "storage" / "ui_surface" / "dashboard_v1.json").is_file()


def test_surface_nl_interpret_append_preview(state_root: Path) -> None:
    with patch("kss.ui_surface.resolve.probe_overnight_code") as probe:
        probe.return_value = {
            "ok": True,
            "code": "AAPL",
            "name": "苹果",
            "kind": "yfinance",
            "close": 190.0,
            "pct": 1.0,
        }
        result = bridge.dispatch("surface-nl-interpret", ["overnight_us", "加上苹果"])
    assert result.get("ok") is True
    assert result.get("ops")
    assert result["ops"][0]["code"] == "AAPL"
    assert result.get("previews")
    assert not (state_root / "storage" / "ui_surface" / "dashboard_v1.json").is_file()


def test_surface_nl_interpret_metric(state_root: Path) -> None:
    result = bridge.dispatch("surface-nl-interpret", ["strip_metric", "改成封板率"])
    assert result.get("ok") is True
    assert result.get("metric_id") == "limit_seal_rate"
    assert result["ops"][0]["op"] == "set_strip_metric"


def test_surface_nl_interpret_bad_region(state_root: Path) -> None:
    result = bridge.dispatch("surface-nl-interpret", ["nope", "加上苹果"])
    assert result.get("ok") is False
    assert result.get("error") == "bad_region"


def test_surface_catalog_search(state_root: Path) -> None:
    result = bridge.dispatch("surface-catalog", ["strip_metric", "封板"])
    assert result.get("ok") is True
    assert result.get("items")
    assert result["items"][0].get("metric_id") == "limit_seal_rate"
    assert not (state_root / "storage" / "ui_surface" / "dashboard_v1.json").is_file()
