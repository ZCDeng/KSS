"""U8 测试：`self-check` bridge 命令（R3/R4，plan 2026-07-12-005）.

- 只读命令：不进 WRITE_COMMANDS。
- 全绿环境 → 全 ok。
- 移走某凭据 → 该项 warn，fixHint 指向设置页（不是 fail——R12 合法终态）。
- venv 探针失败 → fail + fixAction=reinit_runtime。
跑：uv run pytest kss/tests/test_bridge_selfcheck.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402

_ALL_CREDENTIAL_ENV = (
    "TUSHARE_TOKEN", "LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN",
    "TELEGRAM_BOT_TOKEN", "KSS_LLM_PRIMARY_KEY", "KSS_LLM_FALLBACK_KEY",
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
)


@pytest.fixture
def all_credentials_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ALL_CREDENTIAL_ENV:
        monkeypatch.setenv(key, "fake-value")


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ALL_CREDENTIAL_ENV:
        monkeypatch.delenv(key, raising=False)


def test_is_read_only():
    assert "self-check" not in b.WRITE_COMMANDS
    assert "self-check" in b.COMMANDS


class TestFullyConfigured:
    def test_all_items_ok(self, all_credentials_set, monkeypatch, tmp_path):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._self_check()
        statuses = {item["item"]: item["status"] for item in result["items"]}
        assert statuses["storage"] == "ok"
        assert statuses["tushare"] == "ok"
        assert statuses["longbridge"] == "ok"
        assert statuses["telegram"] == "ok"
        assert statuses["llm"] == "ok"
        assert "generatedAt" in result


class TestMissingCredentials:
    def test_missing_tushare_is_warn_not_fail(self, no_credentials, monkeypatch, tmp_path):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "present")
        result = b._self_check()
        by_item = {item["item"]: item for item in result["items"]}
        assert by_item["tushare"]["status"] == "ok"
        assert by_item["longbridge"]["status"] == "warn"
        assert by_item["longbridge"]["fixHint"] == "去设置页数据源分区填写"
        assert by_item["longbridge"]["fixAction"] == "open_settings"

    def test_no_credentials_all_warn(self, no_credentials, monkeypatch, tmp_path):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._self_check()
        by_item = {item["item"]: item for item in result["items"]}
        for item in ("tushare", "longbridge", "telegram", "llm"):
            assert by_item[item]["status"] == "warn"
        # venv/storage 与凭据无关，不受影响。
        assert by_item["storage"]["status"] == "ok"


class TestVenvProbe:
    def test_import_success_is_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with patch.object(b.subprocess, "run", return_value=fake_proc):
            result = b._check_venv()
        assert result["status"] == "ok"

    def test_import_failure_is_fail_with_reinit_action(self, tmp_path):
        fake_proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"ModuleNotFoundError: No module named 'pandas'"
        )
        with patch.object(b.subprocess, "run", return_value=fake_proc):
            result = b._check_venv()
        assert result["status"] == "fail"
        assert result["fixAction"] == "reinit_runtime"
        assert "pandas" in result["detail"]

    def test_timeout_is_fail(self):
        with patch.object(b.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5)):
            result = b._check_venv()
        assert result["status"] == "fail"
        assert "超时" in result["detail"]


class TestStorageProbe:
    def test_writable_dir_is_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._check_storage_writable()
        assert result["status"] == "ok"

    def test_unwritable_dir_is_fail(self, tmp_path, monkeypatch):
        readonly_root = tmp_path / "readonly"
        readonly_root.mkdir()
        readonly_root.chmod(0o400)
        monkeypatch.setattr(b, "STATE_ROOT", readonly_root)
        try:
            result = b._check_storage_writable()
            assert result["status"] == "fail"
        finally:
            readonly_root.chmod(0o700)  # 恢复权限，避免 tmp_path 清理失败


def test_dispatch_wires_self_check(monkeypatch):
    monkeypatch.setattr(b, "_self_check", lambda: {"items": []})
    assert b.dispatch("self-check", []) == {"items": []}


class TestKssDbProbe:
    """U17 test scenario ③：自检含 kss.db 可开一项。"""

    def test_missing_db_is_fail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._check_kss_db()
        assert result["status"] == "fail"
        assert "不存在" in result["detail"]

    def test_openable_db_is_ok(self, tmp_path, monkeypatch):
        import sqlite3

        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        (tmp_path / "storage").mkdir(parents=True)
        con = sqlite3.connect(tmp_path / "storage" / "kss.db")
        con.execute("CREATE TABLE t (a INTEGER)")
        con.commit(); con.close()
        result = b._check_kss_db()
        assert result["status"] == "ok"

    def test_corrupt_db_is_fail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        (tmp_path / "storage").mkdir(parents=True)
        (tmp_path / "storage" / "kss.db").write_bytes(b"not a sqlite file at all, deliberately corrupt")
        result = b._check_kss_db()
        assert result["status"] == "fail"


class TestDuckdbExtensionProbe:
    """U17 test scenario ③：自检含 duckdb 扩展可加载一项（迁移后为 ok）。"""

    def test_real_duckdb_loads_sqlite_extension(self):
        """真机(已装 duckdb==1.5.4)冒烟：会话可开且 sqlite 扩展可加载。"""
        result = b._check_duckdb_extension()
        assert result["status"] == "ok"

    def test_import_failure_is_warn_not_fail(self, monkeypatch):
        """扩展/包不可用是 warn（sql-query 工具暂不可用），不阻断应用其余功能。"""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name == "duckdb":
                raise ImportError("no duckdb")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _boom)
        result = b._check_duckdb_extension()
        assert result["status"] == "warn"


class TestIntradaySecretsProbe:
    """R3-U3（plan 2026-07-14-001 / KTD5）：secrets/tushare_token 探针。"""

    def test_present_nonempty_is_ok(self, tmp_path, monkeypatch):
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "tushare_token").write_text("tok123", encoding="utf-8")
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._check_intraday_secrets()
        assert result["status"] == "ok"

    def test_missing_is_warn_with_fix_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._check_intraday_secrets()
        assert result["status"] == "warn"
        assert result["fixHint"]

    def test_empty_file_is_warn(self, tmp_path, monkeypatch):
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "tushare_token").write_text("  \n", encoding="utf-8")
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        result = b._check_intraday_secrets()
        assert result["status"] == "warn"

    def test_included_in_self_check_items(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
        items = {i["item"] for i in b._self_check()["items"]}
        assert "intraday_secrets" in items
