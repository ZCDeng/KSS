"""U7 可观测性：分时采集的结构化 run 日志 + 机读告警（KTD5/S4）.

为什么单独成模块（不塞进 collect_intraday.py）：
  - 日志字段构建是**确定性变换**，须 test-first 钉死（脱敏、字段集、覆盖比口径）；
  - 告警判定是纯函数（coverage 阈值 / 16:00 无 complete run），不带 IO 便于单测；
  - 与采集器解耦，bridge/影子 harness（U8）都能复用同一口径。

**S4 日志脱敏（硬约束）**：``failure_reason`` / ``error_summary`` 写 stdout/stderr 前
必过 :func:`kss.security.redaction.redact_text`——provider 401 回显 token 不得落进
``storage/logs/launchd/*.log``。本模块是这些字段进日志的**唯一**出口。

**隐私**：日志只含 state/coverage/latest-timestamp/计数，**绝不**含原始 payload 字节。

阈值为保守起值，env 可覆盖；U8 影子期用实测校准。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from kss.security.redaction import redact_text

# 覆盖率告警阈值（活跃 session 终覆盖低于此 → degraded）。保守起值，U8 校准。
INTRADAY_COVERAGE_ALERT_THRESHOLD: float = float(
    os.environ.get("INTRADAY_COVERAGE_ALERT_THRESHOLD", "0.95")
)
# 收盘 run 应完成的截止钟点（小时，本地 Asia/Shanghai）。过此仍无 complete run → degraded。
INTRADAY_CLOSE_DEADLINE_HOUR: int = int(
    os.environ.get("INTRADAY_CLOSE_DEADLINE_HOUR", "16")
)

# 这些字段写日志前必过脱敏器（S4）：可能回显 provider 错误体里的 token。
_SENSITIVE_LOG_FIELDS = ("failure_reason", "error_summary")


def run_log_fields(
    *,
    provider: str,
    run_id: str,
    requested_symbols: int,
    succeeded_symbols: int,
    rows: int,
    coverage_ratio: float | None,
    backlog: int,
    failure_reason: str | None,
    db_bytes: int,
    status: str,
    latency_ms: float | None,
    error_summary: str | None = None,
    known_secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """构建一条结构化 run 日志记录（已脱敏，可安全落盘/打印）.

    ``failure_reason`` / ``error_summary`` 过 :func:`redact_text`（S4）：token 形态串与
    任意 ``known_secrets`` 明文替为 ``***REDACTED***``。其余字段是计数/比率/状态，
    本就不含凭据；**绝不**含原始 payload 字节（隐私）。
    """
    return {
        "event": "intraday_run",
        "provider": provider,
        "run_id": run_id,
        "requested_symbols": int(requested_symbols),
        "succeeded_symbols": int(succeeded_symbols),
        "rows": int(rows),
        "coverage_ratio": coverage_ratio,
        "backlog": int(backlog),
        "failure_reason": redact_text(failure_reason, known_secrets=known_secrets),
        "error_summary": redact_text(error_summary, known_secrets=known_secrets),
        "db_bytes": int(db_bytes),
        "status": status,
        "latency_ms": latency_ms,
    }


def emit_run_log(
    fields: dict[str, Any],
    *,
    stream: TextIO | None = None,
) -> str:
    """把结构化字段以单行 JSON 写 stdout（launchd 收 stdout 进 *.log）.

    防御性二次脱敏：即便调用方绕过 :func:`run_log_fields` 直接传 dict，敏感字段
    仍在此过一道 :func:`redact_text`（S4 出口纪律），杜绝未脱敏文本落盘。
    返回写出的 JSON 串（便于测试断言）。
    """
    safe = dict(fields)
    for key in _SENSITIVE_LOG_FIELDS:
        if key in safe and isinstance(safe[key], str):
            safe[key] = redact_text(safe[key])
    line = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    out = stream if stream is not None else sys.stdout
    print(line, file=out)
    return line


def evaluate_alerts(
    *,
    coverage_ratio: float | None,
    has_complete_close_run: bool,
    now_hour: int,
    coverage_threshold: float | None = None,
    deadline_hour: int | None = None,
) -> dict[str, Any]:
    """纯函数告警判定 → 机读 ``degraded`` 字典.

    两条 degraded 触发（任一即 degraded）：
      1. 活跃 session 终覆盖 ``coverage_ratio`` 低于阈值；
      2. 已过截止钟点（``now_hour >= deadline_hour``）仍无 ``complete`` 收盘 run。

    返回 ``{"state": "ok"|"degraded", "reasons": [...], ...}``；阈值随返回便于审计。
    """
    threshold = (
        INTRADAY_COVERAGE_ALERT_THRESHOLD
        if coverage_threshold is None
        else coverage_threshold
    )
    deadline = (
        INTRADAY_CLOSE_DEADLINE_HOUR if deadline_hour is None else deadline_hour
    )
    reasons: list[str] = []
    if not has_complete_close_run and now_hour >= deadline:
        reasons.append("no_complete_close_run_by_deadline")
    if coverage_ratio is not None and coverage_ratio < threshold:
        reasons.append("coverage_below_threshold")
    return {
        "state": "degraded" if reasons else "ok",
        "reasons": reasons,
        "coverage_ratio": coverage_ratio,
        "coverage_threshold": threshold,
        "deadline_hour": deadline,
        "has_complete_close_run": has_complete_close_run,
        "now_hour": now_hour,
    }


__all__ = [
    "INTRADAY_CLOSE_DEADLINE_HOUR",
    "INTRADAY_COVERAGE_ALERT_THRESHOLD",
    "emit_run_log",
    "evaluate_alerts",
    "run_log_fields",
]
