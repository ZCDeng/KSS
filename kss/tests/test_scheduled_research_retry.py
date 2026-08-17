"""Credential-wait retry and helper-resolution tests for scheduled research."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from kss.research.repository import _join_warnings


_REPO = Path(__file__).resolve().parents[2]
_RUNNER_PATH = _REPO / "scripts" / "run_scheduled_research.py"
_SPEC = importlib.util.spec_from_file_location("scheduled_research_runner", _RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def test_credential_waiting_user_is_retryable_other_waits_are_not() -> None:
    assert runner._is_retryable_waiting_user(
        {"status": "waiting_user", "termination_reason": "credential_broker_timeout"}
    )
    assert runner._is_retryable_waiting_user(
        {
            "status": "waiting_user",
            "termination_reason": "credential_broker_or_provider_unavailable",
        }
    )
    assert runner._is_retryable_waiting_user(
        {"status": "waiting_user", "termination_reason": "harness_kernel_unavailable"}
    )
    assert not runner._is_retryable_waiting_user(
        {"status": "waiting_user", "termination_reason": "need_user_corpus"}
    )
    assert not runner._is_retryable_waiting_user(
        {"status": "blocked", "termination_reason": "credential_broker_timeout"}
    )
    assert not runner._is_retryable_waiting_user({"status": "draft"})


def test_credential_failure_reason_is_a_closed_invariant_set() -> None:
    class HelperError(Exception):
        def __init__(self, code: str, message: str) -> None:
            super().__init__(message)
            self.code = code

    assert (
        runner._credential_failure_reason(HelperError("helper_error", "credential socket timed out"))
        == "credential_broker_timeout"
    )
    assert (
        runner._credential_failure_reason(HelperError("helper_error", "credential nonce mismatch"))
        == "credential_broker_nonce_mismatch"
    )
    assert (
        runner._credential_failure_reason(HelperError("node_unavailable", "Node runtime not found"))
        == "credential_helper_unavailable"
    )
    generic = runner._credential_failure_reason(RuntimeError("https://api.example/v1 boom sk-secret"))
    assert generic == "credential_broker_or_provider_unavailable"
    assert "example" not in generic
    assert "sk-" not in generic


def test_scheduled_research_lib_prefers_signed_app_over_build_tree() -> None:
    lib = (_REPO / "scripts" / "lib_scheduled_research.sh").read_text(encoding="utf-8")
    assert "/Applications/KSSDesktop.app/Contents/Helpers/KSSResearchSchedulerHelper" in lib
    start = lib.index("for candidate in")
    block = lib[start:]
    assert block.index("/Applications/KSSDesktop.app") < block.index(".build")
    assert "kss_helper_is_team_signed" in lib
    weekly = (_REPO / "scripts" / "run_investment_analysis_weekly.sh").read_text(encoding="utf-8")
    assert "lib_scheduled_research.sh" in weekly
    assert "kss_find_scheduled_research_helper" in weekly
    assert "API_KEY" not in weekly
    daily = (_REPO / "scripts" / "run_investment_analysis_daily.sh").read_text(encoding="utf-8")
    assert "run_left_scan_daily.py" in daily
    assert "kss_find_scheduled_research_helper" not in daily
    assert "API_KEY" not in daily


def test_finder_honors_explicit_helper_override(tmp_path: Path) -> None:
    fake = tmp_path / "KSSResearchSchedulerHelper"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    script = (
        f"source {_REPO / 'scripts' / 'lib_scheduled_research.sh'} && "
        f"kss_find_scheduled_research_helper {_REPO}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "KSS_SCHEDULED_RESEARCH_HELPER": str(fake)},
    )
    assert result.stdout.strip() == str(fake)


def test_live_finder_prefers_installed_signed_helper() -> None:
    helper = Path("/Applications/KSSDesktop.app/Contents/Helpers/KSSResearchSchedulerHelper")
    if not helper.is_file():
        pytest.skip("signed KSSDesktop.app is not installed")
    script = (
        f"source {_REPO / 'scripts' / 'lib_scheduled_research.sh'} && "
        f"kss_find_scheduled_research_helper {_REPO}"
    )
    env = {key: value for key, value in os.environ.items() if key != "KSS_SCHEDULED_RESEARCH_HELPER"}
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert result.stdout.strip() == str(helper)


def test_scheduled_sidecar_socket_falls_back_when_af_unix_path_is_too_long(
    tmp_path: Path,
) -> None:
    short = runner._scheduled_sidecar_socket(Path("/tmp/kss"))
    assert short == Path("/tmp/kss/run/kss-scheduled-research.sock")
    long_root = tmp_path / ("x" * 80)
    fallback = runner._scheduled_sidecar_socket(long_root)
    assert fallback == Path(f"/tmp/kss-sched-research-{os.getpid()}.sock")


def test_apply_nonsecret_research_env_reads_state_root_network_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "network.env").write_text(
        "KSS_RESEARCH_PROVIDER=combosearch\nKSS_COMBOSEARCH_BIN=/opt/homebrew/bin/combosearch\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("KSS_RESEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("KSS_COMBOSEARCH_BIN", raising=False)
    runner._apply_nonsecret_research_env(tmp_path / "missing_root", tmp_path)
    assert os.environ["KSS_RESEARCH_PROVIDER"] == "combosearch"
    assert os.environ["KSS_COMBOSEARCH_BIN"] == "/opt/homebrew/bin/combosearch"
    assert "/opt/homebrew/bin" in os.environ.get("PATH", "")


def test_harness_boot_kwargs_match_sidecar_contract_without_secrets(tmp_path: Path) -> None:
    kwargs = runner._harness_boot_kwargs(tmp_path, "/tmp/kss-scheduled.sock")
    assert kwargs["driver"] == "dsh"
    assert kwargs["sidecar_socket"] == "/tmp/kss-scheduled.sock"
    assert kwargs["dsh_home"] == tmp_path / "harness" / "dsh-home"
    assert kwargs["startup_timeout"] >= 90
    assert "DEEPSEEK_API_KEY" not in kwargs
    assert "OPENAI_API_KEY" not in kwargs


def test_harness_failure_reason_is_sanitized() -> None:
    assert (
        runner._harness_failure_reason(RuntimeError("credential socket timed out"))
        == "credential_broker_timeout"
    )
    assert (
        runner._harness_failure_reason(RuntimeError("credential nonce mismatch"))
        == "credential_broker_nonce_mismatch"
    )
    generic = runner._harness_failure_reason(
        RuntimeError("https://api.example/v1 boom sk-secret")
    )
    assert generic == "harness_kernel_unavailable"
    assert "example" not in generic
    assert "sk-" not in generic


def test_harness_gap_audit_failure_is_retryable() -> None:
    assert runner._attempt_blobs_have_harness_gap(
        ['{"warnings":["harness_session_unavailable"],"harness_status":"interrupted"}']
    )
    assert runner._attempt_blobs_have_harness_gap(
        ['{"warnings":["harness kernel timed out on research.turn"]}']
    )
    assert not runner._attempt_blobs_have_harness_gap(
        ['{"warnings":["task_result_schema_invalid_after_repair"]}']
    )
    class _Service:
        db_path = Path("/nonexistent/kss.db")

    assert not runner._should_retry_scheduled_goal(
        _Service(),
        {"status": "insufficient_evidence", "termination_reason": "audit_failed", "goal_id": "g1"},
    )
    assert runner._should_retry_scheduled_goal(
        _Service(),
        {"status": "waiting_user", "termination_reason": "harness_kernel_unavailable"},
    )
    failed_mid_dag = {
        "status": "failed",
        "termination_reason": "sequence item 0: expected str instance, dict found",
        "tasks": [
            {"kind": "collect_sources", "status": "succeeded"},
            {"kind": "normalize_fields", "status": "ready"},
        ],
    }
    assert runner._has_resumable_progress(failed_mid_dag)
    assert runner._should_retry_scheduled_goal(_Service(), failed_mid_dag)
    assert not runner._should_retry_scheduled_goal(
        _Service(),
        {"status": "failed", "tasks": [{"kind": "collect_sources", "status": "incomplete"}]},
    )
    all_done_but_audit = {
        "status": "insufficient_evidence",
        "termination_reason": "audit_failed",
        "tasks": [
            {"kind": "collect_sources", "status": "succeeded"},
            {"kind": "compile_report", "status": "succeeded"},
            {"kind": "delivery_audit", "status": "succeeded"},
        ],
    }
    assert runner._collect_sources_succeeded(all_done_but_audit)
    assert runner._should_retry_scheduled_goal(_Service(), all_done_but_audit)
    compile_blocked = {
        "status": "insufficient_evidence",
        "termination_reason": "audit_failed",
        "tasks": [
            {"kind": "collect_sources", "status": "succeeded"},
            {"kind": "compile_report", "status": "incomplete"},
            {"kind": "delivery_audit", "status": "blocked"},
        ],
    }
    assert runner._has_resumable_progress(compile_blocked)
    assert runner._should_retry_scheduled_goal(_Service(), compile_blocked)
    budget_mid_dag = {
        "status": "budget_limited",
        "termination_reason": "research_budget_exhausted",
        "tasks": [
            {"kind": "collect_sources", "status": "succeeded"},
            {"kind": "compute_temperature", "status": "ready"},
        ],
    }
    assert runner._should_retry_scheduled_goal(_Service(), budget_mid_dag)


def test_boot_starts_harness_with_dsh_and_local_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _Kernel:
        alive = True

        def research_session(self) -> object:
            return object()

    def fake_ensure(**kwargs: object) -> _Kernel:
        captured.update(kwargs)
        return _Kernel()

    monkeypatch.setattr(
        "kss.agent.harness_kernel.ensure_harness_kernel",
        fake_ensure,
    )
    backend, kernel, reason = runner._boot_scheduled_research_runtime(tmp_path, tmp_path)
    try:
        assert reason == "ready"
        assert kernel is not None and kernel.alive
        assert captured["driver"] == "dsh"
        socket = Path(str(captured["sidecar_socket"]))
        assert socket.name.startswith("kss-sched")
        assert socket.suffix == ".sock"
        assert socket.is_socket()
        assert captured["dsh_home"] == tmp_path / "harness" / "dsh-home"
    finally:
        if backend is not None:
            backend.close()


def test_boot_maps_kernel_start_failure_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(**_kwargs: object) -> None:
        raise RuntimeError("https://api.example/v1 boom sk-secret")

    monkeypatch.setattr("kss.agent.harness_kernel.ensure_harness_kernel", boom)
    backend, kernel, reason = runner._boot_scheduled_research_runtime(tmp_path, tmp_path)
    assert backend is None
    assert kernel is None
    assert reason == "harness_kernel_unavailable"
    assert "example" not in reason
    assert "sk-" not in reason
    leftover = Path(f"/tmp/kss-sched-research-{os.getpid()}.sock")
    assert not leftover.exists()
    assert not (tmp_path / "run" / "kss-scheduled-research.sock").exists()


def test_harness_brokered_credentials_require_next_nonce() -> None:
    class _Kernel:
        def __init__(self, ready: dict[str, object] | None) -> None:
            self._ready = ready

    assert runner._harness_has_brokered_credentials(
        _Kernel({"credential_next_nonce": "abc123"})
    )
    assert not runner._harness_has_brokered_credentials(_Kernel({"agents": True}))
    assert not runner._harness_has_brokered_credentials(_Kernel(None))


def test_scheduled_research_session_uses_long_dsh_turn_budget() -> None:
    captured: dict[str, object] = {}

    class _Kernel:
        alive = True

        def request(self, cmd: str, payload: dict, timeout: float = 0) -> dict:
            captured["cmd"] = cmd
            captured["timeout"] = timeout
            captured["cwd"] = payload.get("cwd")
            return {"ok": True, "status": "completed", "assistant_text": "{}"}

    class _Allowlist:
        cwd = "/tmp/ws"
        tools = ("write",)

    class _Request:
        prompt = "x"
        attempt_id = "a1"
        allowlist = _Allowlist()
        applied_write_ids = ()
        task = {"payload": {"timeout_seconds": 240}}

    result = runner._ScheduledResearchSession(_Kernel()).run(_Request(), driver=None)
    assert captured["cmd"] == "research.turn"
    assert captured["timeout"] == 600.0
    assert result.harness_status == "completed"


def test_run_source_boots_harness_and_skips_python_agent_probe() -> None:
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    assert "_boot_scheduled_research_runtime" in source
    assert "_harness_has_brokered_credentials" in source
    assert "stop_harness_kernel" in source
    assert "_ScheduledResearchSession" in source
    assert "_authenticated_agent(project_root, state_root)" not in source
    assert "OPENAI_API_KEY" not in source
    assert "DEEPSEEK_API_KEY" not in source


def test_reset_scheduled_budget_clock_zeros_seconds(tmp_path: Path) -> None:
    from kss.research.service import ResearchService
    from kss.storage.db import connect

    service = ResearchService(state_root=tmp_path, project_root=_REPO)
    created = service.create_goal(
        payload={
            "client_request_id": "budget-clock",
            "objective": "测试预算时钟重置",
            "inputs": {"date_range": "2026-08-11_to_2026-08-14", "as_of": "2026-08-14"},
        }
    )
    goal_id = created["goal_id"]
    with connect(service.db_path) as conn:
        conn.execute(
            "UPDATE research_goals SET usage_json=?, started_at=? WHERE goal_id=?",
            (
                '{"nodes":11,"provider_tokens":0,"seconds":3731}',
                "2026-08-16T17:06:18+00:00",
                goal_id,
            ),
        )
    runner._reset_scheduled_budget_clock(service, goal_id)
    with connect(service.db_path) as conn:
        row = conn.execute(
            "SELECT usage_json, started_at FROM research_goals WHERE goal_id=?",
            (goal_id,),
        ).fetchone()
    usage = __import__("json").loads(row["usage_json"])
    assert usage["seconds"] == 0
    assert usage["nodes"] == 0
    assert usage["provider_tokens"] == 0
    assert row["started_at"] != "2026-08-16T17:06:18+00:00"


def test_resolve_scheduled_corpus_path_prefers_env_then_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standing = tmp_path / "storage" / "analyst-corpus-v1.jsonl"
    standing.parent.mkdir(parents=True)
    standing.write_text("{}\n", encoding="utf-8")
    monkeypatch.delenv("KSS_ANALYST_CORPUS_PATH", raising=False)
    assert runner._resolve_scheduled_corpus_path(tmp_path, tmp_path, {}) == standing
    explicit = tmp_path / "chosen.jsonl"
    explicit.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("KSS_ANALYST_CORPUS_PATH", str(explicit))
    assert runner._resolve_scheduled_corpus_path(tmp_path, tmp_path, {}) == explicit


def test_join_warnings_accepts_structured_dicts() -> None:
    assert _join_warnings(["a", "b"]) == "a; b"
    assert _join_warnings(
        [{"id": "W1", "level": "info", "message": "未经独立核验"}]
    ) == "未经独立核验"
    assert _join_warnings([]) is None
