"""U3：桌面 ask 应答者与研究 pre-execute 白名单。

覆盖 AE8 / AE3 / R6 / R7 / R10 / KTD4 / KTD8。
跑：uv run pytest kss/tests/test_harness_approval_policy.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS = _ROOT / "harness" / "kss-plugins"
_NODE_PATH = _ROOT / "harness" / "kss-profile" / "node_modules"
_EVAL = _PLUGINS / "src" / "policy-eval.mjs"


def _eval(spec: dict) -> dict:
    result = subprocess.run(
        ["node", str(_EVAL), json.dumps(spec, ensure_ascii=False)],
        cwd=str(_PLUGINS),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "NODE_PATH": str(_NODE_PATH),
        },
    )
    if result.returncode != 0:
        raise AssertionError(
            f"policy-eval failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    line = result.stdout.strip().splitlines()[-1]
    return json.loads(line)


def test_ae8_never_plus_whitelist_bash_does_not_write() -> None:
    """AE8：never 不能充当研究自动放行。白名单 bash 也不得成功。"""
    out = _eval(
        {
            "surface": "research",
            "sessionPolicy": "never",
            "tool": "bash",
            "callId": "ae8-bash",
            "cwd": "/tmp/kss-research-ws",
            "allowlist": {"tools": ["bash", "write"], "cwd": "/tmp/kss-research-ws"},
            "answererMode": "none",
        }
    )
    assert out["bodyRan"] is False, out
    assert out["isError"] is True
    assert out["grants"] == []
    assert out["asked"] is False


def test_no_answerer_ask_unavailable_no_write() -> None:
    """无应答者 + ask → unavailable，不写（KTD3 / R6 fail-closed）。"""
    out = _eval(
        {
            "surface": "desktop",
            "sessionPolicy": "ask",
            "tool": "run_task",
            "callId": "no-answerer-write",
            "owned": False,
            "answererMode": "none",
        }
    )
    assert out["bodyRan"] is False, out
    assert out["isError"] is True
    assert out["grants"] == []
    assert "unavailable" in out["decisionOutcomes"] or "unavailable" in out["error"]
    assert out["asked"] is True


def test_desktop_allow_grants_call_id_without_dispatching_here() -> None:
    out = _eval(
        {
            "surface": "desktop",
            "sessionPolicy": "ask",
            "tool": "run_task",
            "callId": "desktop-allow",
            "owned": True,
            "answererMode": "allow",
        }
    )
    assert out["asked"] is True
    assert out["decisionOutcomes"] == ["allowed-once"]
    assert out["bodyRan"] is True
    assert out["grants"] == [{"callId": "desktop-allow", "command": "run"}]


def test_research_whitelist_hit_allows_without_asking() -> None:
    out = _eval(
        {
            "surface": "research",
            "sessionPolicy": "ask",
            "tool": "bash",
            "callId": "wl-hit",
            "cwd": "/tmp/kss-research-ws",
            "allowlist": {"tools": ["bash"], "cwd": "/tmp/kss-research-ws"},
            "answererMode": "none",
        }
    )
    assert out["asked"] is False, out
    assert out["bodyRan"] is True
    assert out["isError"] is False


def test_research_whitelist_miss_denies_without_prompt() -> None:
    """AE3：未入白名单的 live 写直接 deny，不问人。"""
    out = _eval(
        {
            "surface": "research",
            "sessionPolicy": "ask",
            "tool": "run_task",
            "callId": "wl-miss",
            "cwd": "/tmp/kss-research-ws",
            "allowlist": {"tools": ["bash"], "cwd": "/tmp/kss-research-ws"},
            "answererMode": "none",
        }
    )
    assert out["asked"] is False, out
    assert out["bodyRan"] is False
    assert out["isError"] is True
    assert out["grants"] == []


def test_child_cannot_call_parent_denied_live_write_or_retarget_repo_root() -> None:
    """KTD8：子 agent 继承白名单与 cwd，不得提权。"""
    workspace = "/tmp/kss-research-ws"
    denied = _eval(
        {
            "surface": "research",
            "sessionPolicy": "ask",
            "asChild": True,
            "tool": "run_task",
            "callId": "child-denied-write",
            "cwd": workspace,
            "allowlist": {"tools": ["bash"], "cwd": workspace},
            "answererMode": "none",
        }
    )
    assert denied["bodyRan"] is False, denied
    assert denied["asked"] is False
    assert denied["grants"] == []

    retarget = _eval(
        {
            "surface": "research",
            "sessionPolicy": "ask",
            "asChild": True,
            "childEscalate": {"cwd": str(_ROOT), "tools": ["bash", "write"]},
            "tool": "write",
            "callId": "child-retarget-root",
            "cwd": workspace,
            "childCwd": str(_ROOT),
            "repoRoot": str(_ROOT),
            "allowlist": {"tools": ["bash", "write"], "cwd": workspace},
            "answererMode": "none",
        }
    )
    assert retarget["bodyRan"] is False, retarget
    assert retarget["asked"] is False
    assert retarget["grants"] == []


def test_policy_does_not_encode_r7_as_auto_tasks_or_mcp_confirm() -> None:
    src = (_PLUGINS / "src" / "policy.js").read_text(encoding="utf-8")
    assert "AUTO_TASKS" not in src
    assert "confirm=True" not in src
    assert "confirm: true" not in src.lower().replace(" ", "")
