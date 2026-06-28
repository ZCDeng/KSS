from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import kss_app_bridge as bridge  # noqa: E402


def test_research_commands_are_read_only_registered():
    for cmd in ("research-search", "research-fetch", "research-bundle"):
        assert cmd in bridge.COMMANDS
        assert cmd not in bridge.WRITE_COMMANDS


def test_research_dispatch_disabled_fails_soft(monkeypatch):
    monkeypatch.setenv("KSS_RESEARCH_PROVIDER", "disabled")
    out = bridge.dispatch("research-search", ["半导体 政策", "2"])
    assert out["error"] == "research_unavailable"
    assert out["partial"] is True


def test_research_dispatch_fixture_bundle(tmp_path, monkeypatch):
    fixture = tmp_path / "sources.json"
    fixture.write_text(
        json.dumps(
            {
                "generated_at": "2026-06-22T00:00:00+08:00",
                "sources": [
                    {
                        "title": "A",
                        "url": "https://example.com/a",
                        "tier": "official_or_primary",
                        "retrieved_at": "2026-06-22T00:00:00+08:00",
                        "excerpt": "A source",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KSS_RESEARCH_PROVIDER", "fixture")
    monkeypatch.setenv("KSS_RESEARCH_FIXTURE_PATH", str(fixture))
    out = bridge.dispatch("research-bundle", ["AI 政策", "1", "1000"])
    assert out["sources"][0]["url"] == "https://example.com/a"
    assert out["rules"]["localTruthPrecedence"] is True


def test_orientation_exposes_research(monkeypatch):
    monkeypatch.setenv("KSS_RESEARCH_PROVIDER", "disabled")
    monkeypatch.setattr(bridge, "_scheduled_jobs", lambda: [])
    out = bridge.dispatch("orientation", [])
    assert out["research"]["provider"] == "disabled"
    assert "research-bundle" in out["research"]["tools"]


def test_research_bridge_cli_direct_fixture_bundle():
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["KSS_RESEARCH_PROVIDER"] = "fixture"

    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "kss_app_bridge.py"),
            "research-bundle",
            "AI 政策",
            "2",
            "1000",
        ],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)["data"]
    assert payload["provider"] == "fixture"
    assert len(payload["sources"]) == 2
    assert payload["rules"]["localTruthPrecedence"] is True
