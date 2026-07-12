"""U7 测试：`log-list` / `log-tail` bridge 命令（R9 / KTD10，plan 2026-07-12-005）.

- 只读性钉死：两命令 ∉ WRITE_COMMANDS。
- log-list 枚举含轮转代（.log.1/.2）。
- log-tail：尾部截断、grep 过滤、路径逃逸拒绝、不存在文件明确报错。
跑：uv run pytest kss/tests/test_bridge_logs.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


@pytest.fixture
def logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    (d / "cron").mkdir()
    monkeypatch.setattr(b, "LOGS_DIR", d)
    return d


def test_commands_are_read_only():
    assert "log-list" not in b.WRITE_COMMANDS
    assert "log-tail" not in b.WRITE_COMMANDS
    assert "log-list" in b.COMMANDS
    assert "log-tail" in b.COMMANDS


class TestLogList:
    def test_empty_dir_returns_empty_list(self, logs_dir):
        result = b._log_list()
        assert result["logs"] == []

    def test_enumerates_current_and_rotated_generations(self, logs_dir):
        (logs_dir / "sidecar.log").write_text("current\n")
        (logs_dir / "sidecar.log.1").write_text("gen1\n")
        (logs_dir / "sidecar.log.2").write_text("gen2\n")
        (logs_dir / "cron" / "scanner.log").write_text("cron log\n")

        result = b._log_list()
        names = {entry["name"] for entry in result["logs"]}
        assert names == {
            "sidecar.log", "sidecar.log.1", "sidecar.log.2",
            str(Path("cron") / "scanner.log"),
        }
        for entry in result["logs"]:
            assert entry["size"] > 0
            assert entry["mtime"]

    def test_missing_dir_returns_empty_not_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "LOGS_DIR", tmp_path / "does-not-exist")
        result = b._log_list()
        assert result["logs"] == []


class TestLogTail:
    def test_tail_returns_last_n_lines(self, logs_dir):
        (logs_dir / "sidecar.log").write_text("\n".join(f"line{i}" for i in range(10)))
        result = b._log_tail("sidecar.log", lines=3)
        assert result["lines"] == ["line7", "line8", "line9"]
        assert result["totalMatched"] == 10

    def test_grep_filters_before_tail(self, logs_dir):
        content = "\n".join(
            f"ERROR: boom {i}" if i % 3 == 0 else f"INFO: ok {i}" for i in range(10)
        )
        (logs_dir / "sidecar.log").write_text(content)
        result = b._log_tail("sidecar.log", lines=100, grep="ERROR")
        assert all("ERROR" in ln for ln in result["lines"])
        assert result["totalMatched"] == 4  # i=0,3,6,9

    def test_nonexistent_file_reports_not_found(self, logs_dir):
        result = b._log_tail("nope.log")
        assert result["error"] == "not_found"
        assert result["lines"] == []

    def test_rotated_generation_is_readable(self, logs_dir):
        (logs_dir / "sidecar.log.1").write_text("old error here\n")
        result = b._log_tail("sidecar.log.1", grep="error")
        assert result["lines"] == ["old error here"]

    def test_cron_subdir_file_readable(self, logs_dir):
        (logs_dir / "cron" / "scanner.log").write_text("scan done\n")
        result = b._log_tail(str(Path("cron") / "scanner.log"))
        assert result["lines"] == ["scan done"]

    def test_path_traversal_rejected(self, logs_dir):
        with pytest.raises(SystemExit):
            b._log_tail("../../etc/passwd")

    def test_absolute_path_escape_rejected(self, logs_dir):
        with pytest.raises(SystemExit):
            b._log_tail("/etc/passwd")

    def test_lines_capped_at_2000(self, logs_dir):
        (logs_dir / "sidecar.log").write_text("\n".join(f"l{i}" for i in range(3000)))
        result = b._log_tail("sidecar.log", lines=999999)
        assert len(result["lines"]) == 2000


def test_dispatch_wires_log_list(monkeypatch):
    monkeypatch.setattr(b, "_log_list", lambda: {"logs": []})
    assert b.dispatch("log-list", []) == {"logs": []}


def test_dispatch_wires_log_tail_with_defaults(monkeypatch):
    captured = {}

    def fake_tail(name, lines, grep):
        captured["args"] = (name, lines, grep)
        return {}

    monkeypatch.setattr(b, "_log_tail", fake_tail)
    b.dispatch("log-tail", ["sidecar.log"])
    assert captured["args"] == ("sidecar.log", 500, "")


def test_dispatch_wires_log_tail_with_all_args(monkeypatch):
    captured = {}

    def fake_tail(name, lines, grep):
        captured["args"] = (name, lines, grep)
        return {}

    monkeypatch.setattr(b, "_log_tail", fake_tail)
    b.dispatch("log-tail", ["sidecar.log", "50", "ERROR"])
    assert captured["args"] == ("sidecar.log", 50, "ERROR")


def test_dispatch_requires_name():
    with pytest.raises(ValueError, match="NAME"):
        b.dispatch("log-tail", [])
