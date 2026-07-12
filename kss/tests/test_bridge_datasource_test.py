"""U4 测试：`datasource-test` bridge 命令（R7 / KTD6）.

- 只读性钉死：命令 ∉ WRITE_COMMANDS。
- 各源未配凭证 → not_configured，不当作探测失败混淆。
- LLM 源：主/备两套三元组分别测试，结果双条目呈现。
- 未知 source → 明确 unknown_source 错误。
跑：uv run pytest kss/tests/test_bridge_datasource_test.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def test_datasource_test_is_read_only():
    assert "datasource-test" not in b.WRITE_COMMANDS
    assert "datasource-test" in b.COMMANDS


def test_unknown_source_reports_unknown_source():
    result = b._datasource_test("nonexistent")
    assert result["ok"] is False
    assert result["error"] == "unknown_source"


class TestTushareProbe:
    def test_missing_token_reports_not_configured(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        result = b._datasource_test_tushare()
        assert result["source"] == "tushare"
        assert result["ok"] is False
        assert result["error"] == "not_configured"
        assert result["latency_ms"] is None

    def test_success_reports_ok(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
        import pandas as pd

        with patch("kss.data.tushare_client.TushareClient") as MockClient:
            instance = MockClient.return_value
            instance.get_pro.return_value.trade_cal.return_value = pd.DataFrame(
                {"cal_date": ["20260101"]}
            )
            result = b._datasource_test_tushare()
        assert result["ok"] is True
        assert result["error"] is None
        assert result["latency_ms"] is not None

    def test_exception_reports_error_type(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
        with patch("kss.data.tushare_client.TushareClient") as MockClient:
            instance = MockClient.return_value
            instance.get_pro.return_value.trade_cal.side_effect = RuntimeError("network down")
            result = b._datasource_test_tushare()
        assert result["ok"] is False
        assert result["error"] == "RuntimeError"


class TestLongbridgeProbe:
    def test_missing_credentials_reports_not_configured(self, monkeypatch):
        for k in ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        result = b._datasource_test_longbridge()
        assert result["ok"] is False
        assert result["error"] == "not_configured"

    def test_context_build_failure_reports_error(self, monkeypatch):
        monkeypatch.setenv("LONGBRIDGE_APP_KEY", "k")
        monkeypatch.setenv("LONGBRIDGE_APP_SECRET", "s")
        monkeypatch.setenv("LONGBRIDGE_ACCESS_TOKEN", "t")
        with patch("kss.data.intraday_client.LongbridgeProvider") as MockProvider:
            MockProvider.return_value._ensure_context.return_value = (None, "auth_failed")
            result = b._datasource_test_longbridge()
        assert result["ok"] is False
        assert result["error"] == "auth_failed"

    def test_context_build_success_reports_ok(self, monkeypatch):
        monkeypatch.setenv("LONGBRIDGE_APP_KEY", "k")
        monkeypatch.setenv("LONGBRIDGE_APP_SECRET", "s")
        monkeypatch.setenv("LONGBRIDGE_ACCESS_TOKEN", "t")
        with patch("kss.data.intraday_client.LongbridgeProvider") as MockProvider:
            MockProvider.return_value._ensure_context.return_value = ("ctx", None)
            result = b._datasource_test_longbridge()
        assert result["ok"] is True


class TestTelegramProbe:
    def test_missing_token_reports_not_configured(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        result = b._datasource_test_telegram()
        assert result["ok"] is False
        assert result["error"] == "not_configured"

    def test_get_me_ok_reports_ok(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"ok": True, "result": {"id": 1}}
            result = b._datasource_test_telegram()
        assert result["ok"] is True

    def test_get_me_auth_failed_reports_error(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bad-token")
        with patch("requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"ok": False, "description": "Unauthorized"}
            result = b._datasource_test_telegram()
        assert result["ok"] is False
        assert result["error"] == "auth_failed"


class TestLLMProbe:
    def test_no_credentials_reports_not_configured(self, monkeypatch):
        for key in (
            "KSS_LLM_PRIMARY_KEY", "KSS_LLM_FALLBACK_KEY",
            "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        result = b._datasource_test_llm()
        assert result["ok"] is False
        assert result["error"] == "not_configured"
        assert result["candidates"] == []

    def test_primary_and_fallback_both_tested(self, monkeypatch):
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_FALLBACK_KEY", "fallback-key")
        with patch("kss.llm.openai_client.probe_credential_candidate") as mock_probe:
            mock_probe.side_effect = [
                {"ok": True, "latency_ms": 100.0, "error": None, "hint": None},
                {"ok": False, "latency_ms": 50.0, "error": "RuntimeError", "hint": "boom"},
            ]
            result = b._datasource_test_llm()
        assert len(result["candidates"]) == 2
        assert result["candidates"][0]["role"] == "primary"
        assert result["candidates"][1]["role"] == "fallback"
        assert result["ok"] is False  # 备用失败 → 整体 ok=False


def test_dispatch_wires_datasource_test(monkeypatch):
    monkeypatch.setattr(b, "_datasource_test", lambda source: {"source": source, "ok": True})
    result = b.dispatch("datasource-test", ["llm"])
    assert result == {"source": "llm", "ok": True}


def test_dispatch_requires_source_arg():
    import pytest

    with pytest.raises(ValueError, match="SOURCE"):
        b.dispatch("datasource-test", [])
