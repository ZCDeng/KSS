"""U7 可观测性测试：结构化 run 日志(脱敏 S4) + 机读告警(degraded) + 隐私.

跑：PYTHONPATH=<worktree> .venv-desktop/bin/python -m pytest \
        kss/tests/test_intraday_observability.py -q
"""
from __future__ import annotations

import io
import json

from kss.data.intraday_observability import (
    emit_run_log,
    evaluate_alerts,
    run_log_fields,
)


# --------------------------------------------------------------------------- #
# 结构化 run 日志：字段集 + 口径
# --------------------------------------------------------------------------- #


def test_run_log_fields_emits_required_keys():
    """Observability：每 run 含 run_id/provider/requested/succeeded/rows/coverage/latency/db_bytes/status。"""
    rec = run_log_fields(
        provider="akshare",
        run_id="run_abc",
        requested_symbols=3,
        succeeded_symbols=2,
        rows=480,
        coverage_ratio=0.97,
        backlog=1,
        failure_reason=None,
        db_bytes=12345,
        status="completed",
        latency_ms=1500.0,
    )
    for key in (
        "provider", "run_id", "requested_symbols", "succeeded_symbols", "rows",
        "coverage_ratio", "backlog", "failure_reason", "db_bytes", "status",
        "latency_ms",
    ):
        assert key in rec
    assert rec["run_id"] == "run_abc"
    assert rec["requested_symbols"] == 3 and rec["succeeded_symbols"] == 2
    assert rec["coverage_ratio"] == 0.97


# --------------------------------------------------------------------------- #
# S4：failure_reason / error_summary 脱敏
# --------------------------------------------------------------------------- #


def test_failure_reason_token_redacted_s4():
    """评审 S4：provider 401 回显 token → 日志 failure_reason 无 token 子串。"""
    token = "abcdef0123456789" * 3  # 48-hex token 形态
    rec = run_log_fields(
        provider="eastmoney",
        run_id="run_x",
        requested_symbols=1,
        succeeded_symbols=0,
        rows=0,
        coverage_ratio=0.0,
        backlog=0,
        failure_reason=f"HTTP 401 Unauthorized token={token}",
        db_bytes=1,
        status="failed",
        latency_ms=None,
        error_summary=f"raw error body contains {token}",
    )
    assert token not in (rec["failure_reason"] or "")
    assert token not in (rec["error_summary"] or "")
    assert "REDACTED" in rec["failure_reason"]
    assert "REDACTED" in rec["error_summary"]


def test_known_secret_plaintext_redacted():
    """已知明文 secret（非 hex 形态）经 known_secrets 也被脱去。"""
    secret = "my-plaintext-token-XYZ"
    rec = run_log_fields(
        provider="akshare",
        run_id="r",
        requested_symbols=1,
        succeeded_symbols=0,
        rows=0,
        coverage_ratio=None,
        backlog=0,
        failure_reason=f"auth failed with {secret}",
        db_bytes=1,
        status="failed",
        latency_ms=None,
        known_secrets=(secret,),
    )
    assert secret not in (rec["failure_reason"] or "")


def test_emit_run_log_writes_redacted_json_line():
    """emit 出口纪律：即便绕过 run_log_fields 直传未脱敏 dict，敏感字段仍二次脱敏。"""
    token = "deadbeefcafe0000" * 2  # 32-hex
    buf = io.StringIO()
    line = emit_run_log(
        {"event": "intraday_run", "run_id": "r",
         "failure_reason": f"boom {token}", "status": "failed"},
        stream=buf,
    )
    parsed = json.loads(line)
    assert token not in line
    assert token not in buf.getvalue()
    assert "REDACTED" in parsed["failure_reason"]


def test_emit_run_log_is_single_line_json():
    rec = run_log_fields(
        provider="akshare", run_id="r", requested_symbols=1, succeeded_symbols=1,
        rows=240, coverage_ratio=1.0, backlog=0, failure_reason=None,
        db_bytes=10, status="completed", latency_ms=100.0,
    )
    buf = io.StringIO()
    line = emit_run_log(rec, stream=buf)
    assert "\n" not in line  # 单行
    assert json.loads(line)["status"] == "completed"


# --------------------------------------------------------------------------- #
# 告警：degraded 状态机
# --------------------------------------------------------------------------- #


def test_alert_no_complete_run_by_deadline():
    """Observability：16:00 前无 complete 收盘 run → degraded。"""
    res = evaluate_alerts(
        coverage_ratio=None,
        has_complete_close_run=False,
        now_hour=16,
    )
    assert res["state"] == "degraded"
    assert "no_complete_close_run_by_deadline" in res["reasons"]


def test_alert_coverage_below_threshold():
    """Observability：覆盖低于配置阈值 → degraded。"""
    res = evaluate_alerts(
        coverage_ratio=0.80,
        has_complete_close_run=True,
        now_hour=15,
        coverage_threshold=0.95,
    )
    assert res["state"] == "degraded"
    assert "coverage_below_threshold" in res["reasons"]


def test_alert_ok_when_complete_and_coverage_high():
    res = evaluate_alerts(
        coverage_ratio=0.99,
        has_complete_close_run=True,
        now_hour=16,
        coverage_threshold=0.95,
    )
    assert res["state"] == "ok"
    assert res["reasons"] == []


def test_alert_before_deadline_no_run_not_degraded():
    """截止钟点前无 complete run 不算 degraded（仅过点才告警）。"""
    res = evaluate_alerts(
        coverage_ratio=None,
        has_complete_close_run=False,
        now_hour=15,
        deadline_hour=16,
    )
    assert res["state"] == "ok"


def test_alert_reports_thresholds_for_audit():
    res = evaluate_alerts(
        coverage_ratio=0.9,
        has_complete_close_run=True,
        now_hour=14,
        coverage_threshold=0.95,
        deadline_hour=16,
    )
    assert res["coverage_threshold"] == 0.95
    assert res["deadline_hour"] == 16
    assert res["coverage_ratio"] == 0.9
