"""U6 测试：`cron-edit-schedule` bridge 命令（R8 / KTD2，plan 2026-07-12-005）.

- 写命令钉死：∈ WRITE_COMMANDS。
- overlay 写入 + 渲染 + launchctl 生效链路（launchctl 打桩，不碰真系统）。
- 任一步失败即回滚 overlay（渲染失败 / launchctl 失败两分支）。
- 未知 suffix / 非法 JSON / 非法 schedule 明确拒绝。
跑：uv run pytest kss/tests/test_bridge_cron_edit_schedule.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402
from kss.config import cron_manifest as cm  # noqa: E402

_REAL_WRAPPER = "scripts/run_update_data_daily.sh"  # 真实存在，_validate_wrapper 要求


def _write_manifest(tmp_path: Path, suffix: str = "demo_daily") -> Path:
    p = tmp_path / "cron_jobs.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "category_order": ["数据更新", "其他"],
                "jobs": [{
                    "suffix": suffix,
                    "wrapper": _REAL_WRAPPER,
                    "schedule": {"hour": 8, "minute": 30, "weekdays": [1, 2, 3, 4, 5]},
                    "title": "示例",
                    "category": "数据更新",
                    "catchup": True,
                    "enabled": True,
                }],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def isolated_cron_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把清单/overlay/launchd 目录全部指向 tmp_path，绝不碰真系统状态。"""
    manifest_path = _write_manifest(tmp_path)
    overlay_path = tmp_path / "cron_overrides.yaml"
    agents_dir = tmp_path / "LaunchAgents"
    deploy_dir = tmp_path / "deploy_launchd"
    agents_dir.mkdir()
    deploy_dir.mkdir()

    monkeypatch.setattr(cm, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(cm, "OVERLAY_PATH", overlay_path)
    cm._cache.update(key=None, manifest=None)
    monkeypatch.setattr(b, "LAUNCHAGENTS_DIR", agents_dir)
    monkeypatch.setattr(b, "LAUNCHD_DIR", deploy_dir)
    return {"manifest": manifest_path, "overlay": overlay_path,
            "agents": agents_dir, "deploy": deploy_dir}


def _stub_launchctl_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "_run_launchctl", lambda args, timeout=15: (0, "", ""))


def test_is_write_command():
    assert "cron-edit-schedule" in b.WRITE_COMMANDS
    assert "cron-edit-schedule" in b.COMMANDS


class TestSuccessPath:
    def test_writes_overlay_renders_and_bootstraps(self, isolated_cron_paths, monkeypatch):
        _stub_launchctl_ok(monkeypatch)
        result = b._cron_edit_schedule(
            "demo_daily", '{"hour": 18, "minute": 30, "weekdays": [1, 2, 3, 4, 5]}'
        )
        assert result["ok"] is True
        assert result["job"]["schedule"]

        overlay_data = yaml.safe_load(isolated_cron_paths["overlay"].read_text(encoding="utf-8"))
        assert overlay_data["demo_daily"]["hour"] == 18

        agents_plist = isolated_cron_paths["agents"] / "com.zcdeng.kss.demo_daily.plist"
        deploy_plist = isolated_cron_paths["deploy"] / "com.zcdeng.kss.demo_daily.plist"
        assert agents_plist.is_file()
        assert deploy_plist.is_file()

    def test_reload_reflects_new_schedule(self, isolated_cron_paths, monkeypatch):
        """Covers AE4：编辑后重新 load_manifest 拿到新排期（无需进程重启）。"""
        _stub_launchctl_ok(monkeypatch)
        b._cron_edit_schedule("demo_daily", '{"hour": 22, "minute": 15}')
        reloaded = cm.load_manifest()
        job = reloaded.job("demo_daily")
        assert job.schedule.hour == 22
        assert job.schedule.minute == 15


class TestValidationFailures:
    def test_unknown_suffix_rejected(self, isolated_cron_paths, monkeypatch):
        _stub_launchctl_ok(monkeypatch)
        result = b._cron_edit_schedule("nonexistent", '{"hour": 9, "minute": 0}')
        assert result["ok"] is False
        assert result["error"] == "unknown_suffix"

    def test_bad_json_rejected(self, isolated_cron_paths, monkeypatch):
        _stub_launchctl_ok(monkeypatch)
        result = b._cron_edit_schedule("demo_daily", "{not json")
        assert result["ok"] is False
        assert result["error"] == "bad_schedule_json"

    def test_invalid_schedule_shape_rejected_and_overlay_untouched(
        self, isolated_cron_paths, monkeypatch
    ):
        _stub_launchctl_ok(monkeypatch)
        result = b._cron_edit_schedule("demo_daily", '{"hour": 99, "minute": 0}')
        assert result["ok"] is False
        assert result["error"] == "invalid_schedule"
        assert not isolated_cron_paths["overlay"].exists()


class TestFailureRollback:
    def test_launchctl_bootstrap_failure_rolls_back_overlay(
        self, isolated_cron_paths, monkeypatch
    ):
        """launchctl 失败 → overlay 回滚，面板保持原排期（AE4 的反面：不留半成品）。"""
        def fake_launchctl(args, timeout=15):
            if "bootstrap" in args:
                return 1, "", "bootstrap failed: permission denied"
            return 0, "", ""

        monkeypatch.setattr(b, "_run_launchctl", fake_launchctl)
        result = b._cron_edit_schedule("demo_daily", '{"hour": 18, "minute": 0}')
        assert result["ok"] is False
        assert result["error"] == "launchctl_failed"
        assert not isolated_cron_paths["overlay"].exists()

    def test_preserves_prior_overlay_on_rollback(self, isolated_cron_paths, monkeypatch):
        """已有其它任务的 overlay 条目在回滚时原样保留，不被清空。"""
        isolated_cron_paths["overlay"].write_text(
            yaml.safe_dump({"other_suffix_not_in_manifest": {"hour": 5, "minute": 0}}),
            encoding="utf-8",
        )

        def fake_launchctl(args, timeout=15):
            if "bootstrap" in args:
                return 1, "", "boom"
            return 0, "", ""

        monkeypatch.setattr(b, "_run_launchctl", fake_launchctl)
        b._cron_edit_schedule("demo_daily", '{"hour": 18, "minute": 0}')
        restored = yaml.safe_load(isolated_cron_paths["overlay"].read_text(encoding="utf-8"))
        assert "other_suffix_not_in_manifest" in restored
        assert "demo_daily" not in restored


def test_dispatch_wires_cron_edit_schedule(monkeypatch):
    monkeypatch.setattr(
        b, "_cron_edit_schedule", lambda suffix, schedule_json: {"ok": True, "suffix": suffix}
    )
    result = b.dispatch("cron-edit-schedule", ["demo_daily", '{"hour":9,"minute":0}'])
    assert result == {"ok": True, "suffix": "demo_daily"}


def test_dispatch_requires_two_args():
    with pytest.raises(ValueError, match="SUFFIX"):
        b.dispatch("cron-edit-schedule", ["demo_daily"])


class TestScheduleStruct:
    """_schedule_struct —— StartCalendarInterval → 编辑器初值结构（U6）。"""

    def test_weekdays_same_time(self):
        interval = [
            {"Hour": 8, "Minute": 30, "Weekday": d} for d in (1, 2, 3, 4, 5)
        ]
        result = b._schedule_struct(interval)
        assert result == {"hour": 8, "minute": 30, "weekdays": [1, 2, 3, 4, 5],
                           "weekly": False, "weekday": None}

    def test_daily_no_weekday(self):
        result = b._schedule_struct({"Hour": 9, "Minute": 0})
        assert result == {"hour": 9, "minute": 0, "weekdays": None,
                           "weekly": False, "weekday": None}

    def test_single_weekday_is_weekly(self):
        result = b._schedule_struct({"Hour": 20, "Minute": 0, "Weekday": 5})
        assert result == {"hour": 20, "minute": 0, "weekdays": None,
                           "weekly": True, "weekday": 5}

    def test_none_interval_defaults(self):
        result = b._schedule_struct(None)
        assert result["hour"] == 0
        assert result["minute"] == 0
