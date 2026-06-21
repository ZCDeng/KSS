# -*- coding: utf-8 -*-
"""U4: bridge daily-review-symbol run-task 的命令构造单测 (mock 子进程)。"""
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


@pytest.fixture
def captured(monkeypatch):
    """mock _full_python + _run_process_task, 回放捕获的 command/artifacts/timeout。"""
    monkeypatch.setattr(bridge, "_full_python", lambda: "/fake/python")
    cap = {}

    def _fake_run(task_id, title, command, started, artifacts=None, timeout=300):
        cap.update(task_id=task_id, title=title, command=command,
                   artifacts=artifacts, timeout=timeout)
        return {"status": "success", "exitCode": 0, "artifacts": artifacts}

    monkeypatch.setattr(bridge, "_run_process_task", _fake_run)
    return cap


def test_missing_symbols_fails(monkeypatch):
    monkeypatch.setattr(bridge, "_full_python", lambda: "/fake/python")
    r = bridge.run_task("daily-review-symbol", [])
    assert r["status"] == "failed" and r["exitCode"] == 2
    assert "--symbols" in r["summary"]


def test_builds_console_command_no_dryrun(captured):
    bridge.run_task("daily-review-symbol", ["--symbols", "688322.SH"])
    cmd = captured["command"]
    assert "scripts/daily_review.py" in cmd
    assert cmd[cmd.index("--symbols") + 1] == "688322.SH"
    assert "--channel" in cmd and cmd[cmd.index("--channel") + 1] == "console"
    assert "--dry-run" not in cmd  # 即时路径总是覆盖归档
    assert captured["timeout"] == 600  # 回填放大


def test_artifacts_per_symbol(captured):
    bridge.run_task("daily-review-symbol",
                    ["--symbols", "688322.SH,688017.SH", "--date", "20260618"])
    assert captured["artifacts"] == [
        "storage/daily_review/2026-06-18_688322.SH.md",
        "storage/daily_review/2026-06-18_688017.SH.md",
    ]
    assert "--date" in captured["command"]


def test_missing_full_python_reports_env(monkeypatch):
    monkeypatch.setattr(bridge, "_full_python", lambda: None)
    r = bridge.run_task("daily-review-symbol", ["--symbols", "688322.SH"])
    assert r["status"] == "failed"
    assert r["exitCode"] == 127  # _missing_full_env_result


def test_registered_in_dispatch(captured):
    # run_task 分发表确实路由 daily-review-symbol (非 Unknown task)
    r = bridge.run_task("daily-review-symbol", ["--symbols", "688322.SH"])
    assert r["status"] != "failed" or "Unknown task" not in r.get("summary", "")
