"""U6: hithink-auction / hithink-limit-up bridge + cron plist args."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402
from kss.agent.harness_pack import pack_catalog  # noqa: E402
from kss.config.cron_manifest import load_manifest

_REPO = Path(__file__).resolve().parents[2]
_RENDER_PATH = _REPO / "scripts" / "render_launchd_plists.py"
_spec = importlib.util.spec_from_file_location("render_launchd_plists", _RENDER_PATH)
render_mod = importlib.util.module_from_spec(_spec)
sys.modules["render_launchd_plists"] = render_mod
_spec.loader.exec_module(render_mod)


def test_auction_commands_registered_read_only() -> None:
    for cmd in ("hithink-auction", "hithink-limit-up"):
        assert cmd in b.COMMANDS
        assert cmd not in b.WRITE_COMMANDS


def test_auction_tools_mcp_visible() -> None:
    by_name = {e["name"]: e for e in pack_catalog()}
    for name in ("get_hithink_auction", "get_hithink_limit_up"):
        assert name in by_name
        assert by_name[name]["mcpVisible"] is True
        assert by_name[name]["write"] is False


def test_dispatch_auction_live(monkeypatch) -> None:
    monkeypatch.setattr(
        "kss.data.hithink_auction.load_auction_payload",
        lambda stage="live", from_cache="", thscodes=None: {
            "stage": stage, "data": {"data_status": "not_ready", "item": []},
        },
    )
    out = b.dispatch("hithink-auction", ["live"])
    assert out["stage"] == "live"
    assert out["data"]["data_status"] == "not_ready"


def test_dispatch_limit_up(monkeypatch) -> None:
    monkeypatch.setattr(
        "kss.data.hithink_special.limit_up_tool_payload",
        lambda date_text="": {
            "trade_date": date_text or "20260911",
            "rows": [{"thscode": "688008.SH", "reason": "算力租赁", "continue_day_cnt": 1}],
            "source": "hithink",
        },
    )
    out = b.dispatch("hithink-limit-up", ["20260911"])
    assert out["rows"][0]["thscode"] == "688008.SH"


def test_cron_jobs_render_stage_args(tmp_path) -> None:
    manifest = load_manifest()
    live = manifest.job("auction_live")
    final = manifest.job("auction_final")
    assert live is not None and final is not None
    assert live.schedule.hour == 9 and live.schedule.minute == 20
    assert final.schedule.hour == 9 and final.schedule.minute == 26
    assert live.args == ("--stage", "live")
    assert final.args == ("--stage", "final")
    project = tmp_path / "code"
    project.mkdir()
    for job in (live, final):
        out = tmp_path / f"com.zcdeng.kss.{job.suffix}.plist"
        pl = render_mod.render(str(project), job, out)
        args = pl["ProgramArguments"]
        assert "--stage" in args
        blob = out.read_text(encoding="utf-8")
        assert "HITHINK" not in blob
        assert "sk-fuyao" not in blob
        env = pl.get("EnvironmentVariables") or {}
        assert "HITHINK_FINANCE_API_KEY" not in env
