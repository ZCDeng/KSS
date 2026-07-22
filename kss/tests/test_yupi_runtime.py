"""KSS 托管 yupi 运行时：端口/URL/key 解析（不真 clone）。"""

from __future__ import annotations

import os

from kss.news import yupi_runtime as yr


def test_default_port_and_base_url(monkeypatch):
    monkeypatch.delenv("YUPI_BASE_URL", raising=False)
    monkeypatch.delenv("KSS_YUPI_PORT", raising=False)
    assert yr.port() == 18765
    assert yr.base_url() == "http://127.0.0.1:18765"


def test_node_ok_finds_homebrew_path(monkeypatch, tmp_path):
    """GUI PATH 无 node 时，仍能从固定前缀解析。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    node = bin_dir / "node"
    npm = bin_dir / "npm"
    node.write_text("#!/bin/sh\necho v20.0.0\n")
    npm.write_text("#!/bin/sh\necho npm\n")
    node.chmod(0o755)
    npm.chmod(0o755)

    monkeypatch.setattr(yr.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(
        yr,
        "_resolve_node_binaries",
        lambda: (str(node), str(npm)),
    )
    ok, detail = yr.node_ok()
    assert ok is True
    assert "v20" in detail


def test_explicit_yupi_base_url(monkeypatch):
    monkeypatch.setenv("YUPI_BASE_URL", "http://127.0.0.1:9999/")
    assert yr.base_url() == "http://127.0.0.1:9999"


def test_resolve_openrouter_from_primary_llm(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("KSS_YUPI_OPENROUTER_KEY", raising=False)
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "sk-or-test")
    assert yr.resolve_openrouter_key() == "sk-or-test"
    key, src = yr.resolve_openrouter_key_source()
    assert key == "sk-or-test"
    assert src == "seesaw_primary"


def test_resolve_openrouter_sk_or_prefix_without_base(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("KSS_YUPI_OPENROUTER_KEY", raising=False)
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "sk-or-v1-seesaw")
    key, src = yr.resolve_openrouter_key_source()
    assert key == "sk-or-v1-seesaw"
    assert src == "seesaw_primary_sk_or"


def test_resolve_model_from_seesaw_when_openrouter(monkeypatch):
    monkeypatch.delenv("KSS_YUPI_MODEL", raising=False)
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("KSS_LLM_PRIMARY_MODEL", "anthropic/claude-sonnet-4")
    assert yr.resolve_model() == "anthropic/claude-sonnet-4"


def test_explicit_yupi_model_wins(monkeypatch):
    monkeypatch.setenv("KSS_YUPI_MODEL", "deepseek/deepseek-v3.2")
    monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("KSS_LLM_PRIMARY_MODEL", "other/model")
    assert yr.resolve_model() == "deepseek/deepseek-v3.2"


def test_yupi_client_uses_managed_port(monkeypatch):
    monkeypatch.delenv("YUPI_BASE_URL", raising=False)
    monkeypatch.setenv("KSS_YUPI_PORT", "18765")
    from kss.news.yupi_client import base_url

    assert base_url() == "http://127.0.0.1:18765"


def test_status_shape_without_install(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_YUPI_HOME", str(tmp_path / "yupi_empty"))
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    st = yr.status()
    assert "base_url" in st
    assert st["port"] == yr.port()
    assert st["installed"] is False
    assert "health_ok" in st


def test_default_git_ref_is_pinned_sha():
    assert yr._is_git_sha(yr.DEFAULT_REF)
    assert len(yr.DEFAULT_REF) >= 40


def test_start_background_prefers_launchctl(monkeypatch):
    monkeypatch.setattr(yr, "health", lambda *a, **k: {"ok": False, "error": "down"})
    monkeypatch.setattr(yr, "_server_entry", lambda: (["/usr/bin/true"], "dist"))
    monkeypatch.setattr(yr, "_launchctl_yupi_loaded", lambda: True)
    monkeypatch.setattr(
        yr,
        "_launchctl_kickstart_yupi",
        lambda: {
            "ok": True,
            "runner": "launchctl",
            "health": {"ok": True},
            "base_url": "http://127.0.0.1:18765",
        },
    )
    called = {"popen": 0}

    def boom(*a, **k):
        called["popen"] += 1
        raise AssertionError("Popen must not run when launchctl succeeds")

    monkeypatch.setattr(yr.subprocess, "Popen", boom)
    out = yr.start_background(allow_install=False)
    assert out["ok"] is True
    assert out["runner"] == "launchctl"
    assert called["popen"] == 0


def test_start_background_never_popen_when_launchd_loaded(monkeypatch):
    """launchd 已加载时 kick 失败也不走 Popen，避免抢端口。"""
    monkeypatch.setattr(yr, "health", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(yr, "_server_entry", lambda: (["/usr/bin/true"], "dist"))
    monkeypatch.setattr(yr, "_launchctl_yupi_loaded", lambda: True)
    monkeypatch.setattr(
        yr,
        "_launchctl_kickstart_yupi",
        lambda: {"ok": False, "error": "kick failed", "runner": "launchctl"},
    )
    called = {"popen": 0}

    def boom(*a, **k):
        called["popen"] += 1
        raise AssertionError("Popen forbidden while launchd loaded")

    monkeypatch.setattr(yr.subprocess, "Popen", boom)
    out = yr.start_background(allow_install=False)
    assert out["ok"] is False
    assert called["popen"] == 0
    assert out.get("runner") == "launchctl"


def test_reclaim_port_skips_self(monkeypatch):
    monkeypatch.setattr(yr, "listeners_on_port", lambda p=None: [os.getpid(), 999999])
    killed_sigs: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        if pid == 999999:
            raise OSError("no such process")
        killed_sigs.append((pid, sig))

    monkeypatch.setattr(yr.os, "kill", fake_kill)
    monkeypatch.setattr(yr, "listeners_on_port", lambda p=None: [])
    # first call returns self+fake, reclaim should not kill self
    calls = {"n": 0}

    def listeners(p=None):
        calls["n"] += 1
        return [] if calls["n"] > 1 else [os.getpid()]

    monkeypatch.setattr(yr, "listeners_on_port", listeners)
    out = yr.reclaim_port()
    assert out == []


def test_start_background_no_install_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KSS_YUPI_HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(yr, "health", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(yr, "_server_entry", lambda: None)
    out = yr.start_background(allow_install=False)
    assert out["ok"] is False
    assert "not installed" in (out.get("error") or "")
