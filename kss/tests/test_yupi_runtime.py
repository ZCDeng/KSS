"""KSS 托管 yupi 运行时：端口/URL/key 解析（不真 clone）。"""

from __future__ import annotations

import os

from kss.news import yupi_runtime as yr


def test_default_port_and_base_url(monkeypatch):
    monkeypatch.delenv("YUPI_BASE_URL", raising=False)
    monkeypatch.delenv("KSS_YUPI_PORT", raising=False)
    assert yr.port() == 18765
    assert yr.base_url() == "http://127.0.0.1:18765"


def test_explicit_yupi_base_url(monkeypatch):
    monkeypatch.setenv("YUPI_BASE_URL", "http://127.0.0.1:9999/")
    assert yr.base_url() == "http://127.0.0.1:9999"


def test_resolve_openrouter_from_primary_llm(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "sk-or-test")
    assert yr.resolve_openrouter_key() == "sk-or-test"


def test_yupi_client_uses_managed_port(monkeypatch):
    monkeypatch.delenv("YUPI_BASE_URL", raising=False)
    monkeypatch.setenv("KSS_YUPI_PORT", "18765")
    from kss.news.yupi_client import base_url

    assert base_url() == "http://127.0.0.1:18765"


def test_status_shape_without_install(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    # re-bind module roots used at call time
    monkeypatch.setattr(yr, "_STATE_ROOT", tmp_path)
    st = yr.status()
    assert "base_url" in st
    assert st["port"] == yr.port()
    assert st["installed"] is False
    assert "health_ok" in st
