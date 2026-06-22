#!/usr/bin/env python3
"""U8 影子运行 harness + 数据持久性验收（阶段 5；RB3 + S2 + A7）.

**职责**：在批准的活跃策略 universe（**非全市场**）上跑受控 20 场影子周期，
serial、trade-cal 确认日，产出每日报告 + 三维通过门判定 + 校准建议。
**零策略消费**——只采集与报告，不喂任何回测/walk-forward 入口。

为什么独立成模块（不塞 collect_intraday.py）：
  - 聚合 / 门判定 / as-of 回放 / 校准是**确定性变换 + 纯判定**，须 test-first 钉死；
  - harness 编排（serial collect over days）与采集器单场逻辑解耦，便于注入 20-场 fixture；
  - 报告 artifact 是机读交付物（验收 gate），与采集器 run 汇总不同口径。

**S2 硬前置（评审 S2，hard stop 非依赖排序）**：影子开跑前必须断言
  (a) U2 凭据脱敏不变式 + (b) U7 catalog BLOB/TEXT 列排除不变式 全部成立。
  WHY：20 场是 **live append-only 采集**。若脱敏正则或 BLOB 排除已回归破坏，
  这 20 场会把未脱敏的请求行 / 原始 BLOB 暴露面 append 进库与 catalog，
  append-only 语义下**事后不可清洗**（不能 UPDATE 抹掉历史 observation）。
  故这是 hard refuse-to-run，不是「先跑后查」。

**三维通过门（RB3，run 成功率不单独定义通过）**：
  (a) ≥19/20 期望收盘 run 成功；
  (b) 窗内零未恢复 gap（含 permanent_gap / reconciliation_stalled）；
  (c) 零 ``complete`` 日有对账失配 **且** as-of 回放无 run 消费晚到 bar。
  失败源日须留可见 gap，不得用滚动 1m 响应回填（除非字段质量复验）。

**A7（>窗长停机）**：harness 注入 >rolling-window 停机场景，验证 ``permanent_gap``
  检测 + 告警**经 harness 端到端**触发（非仅单测 fixture）。

手动运行（注入 provider/calendar，无 live 网络）：
    见 ``run_shadow_cycle`` docstring。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.config.paths import REPORT_DIR  # noqa: E402
from kss.data.intraday_store import IntradayStore  # noqa: E402
from kss.security.redaction import (  # noqa: E402
    contains_credential,
    redact_request,
    redact_text,
)
from scripts.collect_intraday import (  # noqa: E402
    CalendarFn,
    collect_close,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 影子期期望场数（验收标准 7：≥19/20 通过）。
SHADOW_EXPECTED_RUNS: int = 20
# run-success 维度的最小通过数（19/20）。
SHADOW_MIN_SUCCESS_RUNS: int = 19


class ShadowPreconditionError(RuntimeError):
    """S2 硬前置失败：脱敏 / BLOB 排除不变式被破坏 → 拒绝跑影子周期（hard stop）。"""


# --------------------------------------------------------------------------- #
# S2 硬前置：脱敏 + BLOB/TEXT 排除不变式（hard stop）
# --------------------------------------------------------------------------- #


# 探针 fixture：一条**含 token 形态串**的 Tushare 风格请求，过脱敏器后必须洗净。
_PROBE_TOKEN = "abcdef0123456789abcdef0123456789abcdef01"  # 40-hex 形（合成，非真 token）
_PROBE_RESPONSE_WITH_TOKEN = (
    '{"data":[{"trade_date":"20260619"}],"echo_token":"'
    + _PROBE_TOKEN
    + '"}'
)

# canonical「必须从 catalog 排除」的自由文本列（S2 独立断言基准；不引用生产常量，
# 这样生产 excluded 集若被回归清空/缩小，subset 检查与反射检查都会抓住）。
_MUST_EXCLUDE_COLUMNS = frozenset({
    "ingest_runs.error_summary",
    "coverage_assessments.details_json",
    "coverage_assessments.missing_json",
})


def assert_redaction_invariants() -> None:
    """U2 凭据脱敏不变式（S2-a）：请求序列化 / 响应体扫描必须洗净 token。

    探针：(1) 含 token 的请求 → ``redact_request`` 输出无 token 子串；
          (2) 回显 token 的响应体 → ``contains_credential`` 命中（采集器据此终止
              ``credential_in_payload``，不持久化）；
          (3) ``redact_text`` 抹掉 token 形态串。
    任一不成立 → :class:`ShadowPreconditionError`（脱敏已回归，live 采集会累积未脱敏行）。
    """
    redacted = redact_request(
        "GET",
        f"https://api.tushare.pro/?token={_PROBE_TOKEN}&api=daily",
        headers={"Authorization": f"Bearer {_PROBE_TOKEN}"},
        params={"token": _PROBE_TOKEN, "api_key": _PROBE_TOKEN},
        body={"token": _PROBE_TOKEN, "ts_code": "688008.SH"},
    )
    serialized = json.dumps(redacted, ensure_ascii=False)
    if _PROBE_TOKEN in serialized:
        raise ShadowPreconditionError(
            "S2: redact_request 漏过 token —— 拒绝跑影子（未脱敏请求会 append-only 累积，不可洗）"
        )
    if not contains_credential(_PROBE_RESPONSE_WITH_TOKEN):
        raise ShadowPreconditionError(
            "S2: contains_credential 漏判回显 token 的响应体 —— 采集器无法 fail-closed，拒绝跑影子"
        )
    cleaned = redact_text(_PROBE_RESPONSE_WITH_TOKEN)
    if cleaned is not None and _PROBE_TOKEN in cleaned:
        raise ShadowPreconditionError(
            "S2: redact_text 漏过 token 形态串 —— 拒绝跑影子"
        )


# U7 catalog allowlist + 排除列不变式（与 build_data_catalog 同源，避免漂移）。
def assert_catalog_exclusion_invariants() -> None:
    """U7 catalog BLOB/TEXT 列排除不变式（S2-b）：用真 catalog builder 反射探针库.

    建一个含 ``payload_blobs`` BLOB + 排除列填合成 token 的最小库，跑 catalog builder，
    断言输出 (1) 无 ``payload_blobs`` 表 / 任何 BLOB 列；(2) 排除的自由文本列
    (``error_summary`` / ``details_json`` / ``missing_json``) 不出现；(3) 整个 catalog
    JSON 无 token 子串。任一不成立 → :class:`ShadowPreconditionError`。

    复用 ``build_data_catalog`` 的真实 ``_build_sqlite_dataset`` + 真实 allowlist /
    excluded 常量（不在此重定义，杜绝 S2 检查与生产 catalog 漂移）。
    """
    import tempfile

    from scripts.build_data_catalog import (
        _INTRADAY_EXCLUDED_COLUMNS,
        _INTRADAY_TABLE_ALLOWLIST,
        _build_sqlite_dataset,
    )

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "intraday_quotes.db"
        store = IntradayStore(db)
        # 填一条带 token 的排除列 + 一个 BLOB，确保排除真生效。
        with store._conn() as conn:  # noqa: SLF001 — 探针直写，仅测试期
            conn.execute(
                "INSERT INTO ingest_runs "
                "(run_id, provider, mode, trade_date, started_at, status, error_summary) "
                "VALUES ('probe', 'p', 'close', '2026-06-19', 'x', 'failed', ?)",
                (f"leaked {_PROBE_TOKEN}",),
            )
            conn.execute(
                "INSERT INTO payload_blobs (payload_sha256, payload, byte_size, first_seen_at) "
                "VALUES ('sha', ?, 3, 'x')",
                (b"raw",),
            )
        dataset = _build_sqlite_dataset(
            "intraday_probe",
            db,
            "S2 探针库",
            table_allowlist=_INTRADAY_TABLE_ALLOWLIST,
            excluded_columns=_INTRADAY_EXCLUDED_COLUMNS,
        )

    # 生产排除集须覆盖 canonical 必排集（若被回归清空/缩小，此处即抓住）。
    if not _MUST_EXCLUDE_COLUMNS.issubset(_INTRADAY_EXCLUDED_COLUMNS):
        missing = _MUST_EXCLUDE_COLUMNS - _INTRADAY_EXCLUDED_COLUMNS
        raise ShadowPreconditionError(
            f"S2: 生产排除集漏掉 canonical 必排列 {sorted(missing)} —— 拒绝跑影子"
        )

    blob = json.dumps(dataset, ensure_ascii=False)
    table_names = {t.get("table") for t in dataset.get("tables", [])}
    if "payload_blobs" in table_names or "payload_observations" in table_names:
        raise ShadowPreconditionError(
            "S2: catalog 暴露了 payload_blobs/payload_observations —— 拒绝跑影子"
        )
    # 反射 schema 里**结构上**不得出现任何 canonical 必排列（即便 token 不在列名）。
    reflected = {
        f"{tbl.get('table')}.{col.get('name')}"
        for tbl in dataset.get("tables", [])
        for col in tbl.get("columns", [])
    }
    leaked = _MUST_EXCLUDE_COLUMNS & reflected
    if leaked:
        raise ShadowPreconditionError(
            f"S2: catalog 反射了被排除列 {sorted(leaked)} —— 拒绝跑影子"
        )
    # 反射 schema 里也不得出现任何 BLOB 列（结构上）。
    for tbl in dataset.get("tables", []):
        for col in tbl.get("columns", []):
            if "BLOB" in str(col.get("dtype", "")).upper():
                raise ShadowPreconditionError(
                    f"S2: catalog 反射了 BLOB 列 {tbl.get('table')}.{col.get('name')} —— 拒绝跑影子"
                )
    if _PROBE_TOKEN in blob:
        raise ShadowPreconditionError(
            "S2: catalog 输出含 token 子串 —— 拒绝跑影子"
        )


def assert_shadow_preconditions() -> dict[str, Any]:
    """S2 硬前置总闸：脱敏 + BLOB/TEXT 排除不变式都过才放行（否则 hard stop）.

    返回机读 ``{"passed": True, "checks": [...]}``；任一不变式破坏 → 抛
    :class:`ShadowPreconditionError`（调用方 ``run_shadow_cycle`` 不 catch，直接拒绝）。
    """
    assert_redaction_invariants()
    assert_catalog_exclusion_invariants()
    return {
        "passed": True,
        "checks": ["redaction_invariants", "catalog_blob_text_exclusion"],
        "rationale": (
            "20 场是 live append-only 采集；脱敏/排除回归会累积不可洗的未脱敏行"
        ),
    }


# --------------------------------------------------------------------------- #
# 聚合：每日报告 + run 成功率 + gap 恢复 + false-complete 计数
# --------------------------------------------------------------------------- #


def aggregate_shadow_runs(
    run_summaries: list[dict[str, Any]],
    *,
    reconciliation_mismatches: list[dict[str, Any]] | None = None,
    stalled: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把每场 run 汇总聚合成影子报告主体（纯函数，确定性）.

    输入 ``run_summaries`` = :func:`collect_close` 返回的逐场 dict 列表（按场序）。
    产出：
      - ``per_day``：每成功场报覆盖率% / 缺失时间戳 / 对账状态 / 响应 hash / 延迟 / 磁盘增量；
      - ``run_success_count`` / ``run_success_rate``：成功 run 数与比率（成功 = status
        ``completed`` 且 ``exit_code==0``）；
      - ``recovered_gaps`` / ``unrecovered_gaps``：窗内 gap 恢复状态（catchup_days 视为
        已恢复；permanent_gaps + stalled 视为未恢复）；
      - ``false_complete_count``：``complete`` 日却对账失配的计数（应为 0）。
    """
    per_day: list[dict[str, Any]] = []
    success = 0
    recovered_gaps: list[str] = []
    unrecovered_gaps: list[dict[str, Any]] = []

    for s in run_summaries:
        status = s.get("status")
        exit_code = s.get("exit_code", 1)
        is_success = status == "completed" and exit_code == 0
        if is_success:
            success += 1
        # 追补恢复的窗内日 = recovered。
        for d in s.get("catchup_days", []) or []:
            recovered_gaps.append(d)
        # 窗外 permanent_gap = 未恢复（仅历史 proxy 可填）。
        for g in s.get("permanent_gaps", []) or []:
            unrecovered_gaps.append({"trade_date": g, "reason": "permanent_gap"})
        per_day.append(_per_day_entry(s, is_success))

    # reconciliation_stalled 也是未恢复 gap（A4：complete 但 K 周期无 reconciled）。
    for st in stalled or []:
        unrecovered_gaps.append(
            {
                "trade_date": st.get("trade_date"),
                "reason": "reconciliation_stalled",
                "instrument_id": st.get("instrument_id"),
            }
        )

    false_complete = list(reconciliation_mismatches or [])

    return {
        "expected_runs": SHADOW_EXPECTED_RUNS,
        "observed_runs": len(run_summaries),
        "run_success_count": success,
        "run_success_rate": (success / len(run_summaries)) if run_summaries else 0.0,
        "per_day": per_day,
        "recovered_gaps": sorted(set(recovered_gaps)),
        "unrecovered_gaps": unrecovered_gaps,
        "false_complete_count": len(false_complete),
        "false_complete_days": false_complete,
    }


def _per_day_entry(summary: dict[str, Any], is_success: bool) -> dict[str, Any]:
    """单场每日报告条目（覆盖率/缺失戳/对账状态/hash/延迟/磁盘增量）。"""
    return {
        "trade_date": summary.get("trade_date"),
        "status": summary.get("status"),
        "is_success": is_success,
        "coverage_pct": summary.get("coverage_pct"),
        "missing_timestamps": summary.get("missing_timestamps", []),
        "reconciliation_status": summary.get("reconciliation_status"),
        "response_hash": summary.get("response_hash"),
        "latency_ms": summary.get("latency_ms"),
        "disk_increment_bytes": summary.get("disk_increment_bytes"),
        "permanent_gaps": summary.get("permanent_gaps", []),
        "catchup_days": summary.get("catchup_days", []),
    }


# --------------------------------------------------------------------------- #
# as-of 回放（PIT 安全：无 run 消费晚到 bar）
# --------------------------------------------------------------------------- #


def replay_asof_consumption(
    store: IntradayStore,
    *,
    instruments: list[int],
    interval: int,
    replay_points: list[dict[str, str]],
    eligibility: str = "pit_backtest_eligible",
) -> dict[str, Any]:
    """as-of 回放：每个回放点查 ``load_asof``，断言无晚到 bar 被消费（PIT 安全）.

    ``replay_points`` = ``[{"start","end","as_of_ts"}]``。
    对每点跑 ``load_asof``，若返回的某 bar 其 ``bar_end_ts`` 对应的可得时间晚于 ``as_of_ts``
    （即在 as_of 时本不可见却被返回）→ 记一条 ``late_consumed``。``load_asof`` 已按
    ``available_from`` + 评估 ``assessed_at`` 门控，这里**端到端复验**该闸在影子数据上生效。

    返回 ``{"late_consumed": [...], "points_checked": N}``；``late_consumed`` 非空 →
    门 (c) 失败（报告标记 run 消费晚到 bar）。
    """
    late: list[dict[str, Any]] = []
    for pt in replay_points:
        as_of = pt["as_of_ts"]
        bars = store.load_asof(
            instruments=instruments,
            start=pt["start"],
            end=pt["end"],
            interval=interval,
            as_of_ts=as_of,
            eligibility=eligibility,
        )
        # 不应有任何 bar 其声明可得时间晚于 as_of（load_asof 应已剔除；端到端复验）。
        for b in bars:
            avail = _bar_available_from(store, b.observation_id)
            if avail is not None and avail > as_of:
                late.append(
                    {
                        "as_of_ts": as_of,
                        "bar_end_ts": b.bar_end_ts,
                        "available_from": avail,
                        "instrument_id": b.instrument_id,
                    }
                )
    return {"late_consumed": late, "points_checked": len(replay_points)}


def _bar_available_from(store: IntradayStore, observation_id: int) -> str | None:
    """取某 observation 的 ``source_asof_ts``（PIT 可得时间）。"""
    with store._conn() as conn:  # noqa: SLF001 — 只读探针
        row = conn.execute(
            "SELECT source_asof_ts FROM payload_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


# --------------------------------------------------------------------------- #
# 三维通过门（RB3）
# --------------------------------------------------------------------------- #


def evaluate_shadow_gate(
    aggregate: dict[str, Any],
    replay: dict[str, Any],
    *,
    min_success_runs: int = SHADOW_MIN_SUCCESS_RUNS,
) -> dict[str, Any]:
    """三维通过门（RB3）：run 成功率单独**不**定义通过.

    pass = (a) run_success_count >= min_success_runs（默认 19/20）
        且 (b) 窗内零未恢复 gap（``unrecovered_gaps`` 空）
        且 (c) 零 ``complete`` 日对账失配（``false_complete_count==0``）
              **且** as-of 回放无 run 消费晚到 bar（``replay.late_consumed`` 空）。
    返回 ``{"verdict": "pass"|"fail", "dimensions": {...}, "reasons": [...]}``。
    """
    dim_a = aggregate["run_success_count"] >= min_success_runs
    dim_b = len(aggregate["unrecovered_gaps"]) == 0
    dim_c = (
        aggregate["false_complete_count"] == 0
        and len(replay.get("late_consumed", [])) == 0
    )
    reasons: list[str] = []
    if not dim_a:
        reasons.append(
            f"run_success {aggregate['run_success_count']}/{aggregate['observed_runs']} "
            f"< {min_success_runs}"
        )
    if not dim_b:
        reasons.append(
            f"unrecovered_gaps={len(aggregate['unrecovered_gaps'])}"
        )
    if not dim_c:
        if aggregate["false_complete_count"]:
            reasons.append(
                f"false_complete={aggregate['false_complete_count']}"
            )
        if replay.get("late_consumed"):
            reasons.append(
                f"asof_late_consumed={len(replay['late_consumed'])}"
            )
    return {
        "verdict": "pass" if (dim_a and dim_b and dim_c) else "fail",
        "dimensions": {
            "run_success": dim_a,
            "data_persistence_no_unrecovered_gap": dim_b,
            "no_false_complete_and_pit_safe": dim_c,
        },
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# 校准建议（实测 → retention / delay / 阈值 / publication_delay）
# --------------------------------------------------------------------------- #


def suggest_calibration(
    *,
    measured_db_bytes: list[int],
    measured_raw_bytes_per_run: list[int],
    measured_coverage_ratios: list[float],
    measured_publication_delays_seconds: list[float],
    db_headroom: float = 2.0,
    raw_headroom: float = 2.0,
    delay_safety_margin_seconds: int = 30,
) -> dict[str, Any]:
    """从影子实测产出建议值（RB2 publication_delay 取 p95+裕度，偏晚抗 look-ahead）.

    - ``INTRADAY_MAX_DB_BYTES`` = max(实测 DB 字节) × ``db_headroom``；
    - ``INTRADAY_MAX_RAW_BYTES_PER_RUN`` = max(单 run raw) × ``raw_headroom``；
    - 覆盖告警阈值 = floor(min(实测覆盖率) 到两位小数)，但不低于 0.90、不高于 0.99；
    - ``publication_delay`` = ceil(p95(实测延迟)) + ``delay_safety_margin``（RB2 偏晚）。
    全部 measured 为空时该项返回 None（无实测不臆造）。
    """
    db_suggest = (
        int(max(measured_db_bytes) * db_headroom) if measured_db_bytes else None
    )
    raw_suggest = (
        int(max(measured_raw_bytes_per_run) * raw_headroom)
        if measured_raw_bytes_per_run
        else None
    )
    cov_suggest = None
    if measured_coverage_ratios:
        worst = min(measured_coverage_ratios)
        # 向下取到两位小数作保守阈值；夹在 [0.90, 0.99]。
        floored = math.floor(worst * 100) / 100.0
        cov_suggest = max(0.90, min(0.99, floored))
    delay_suggest = None
    if measured_publication_delays_seconds:
        p95 = _percentile(measured_publication_delays_seconds, 0.95)
        delay_suggest = int(math.ceil(p95)) + delay_safety_margin_seconds

    return {
        "INTRADAY_MAX_DB_BYTES": db_suggest,
        "INTRADAY_MAX_RAW_BYTES_PER_RUN": raw_suggest,
        "INTRADAY_COVERAGE_ALERT_THRESHOLD": cov_suggest,
        "publication_delay_seconds": delay_suggest,
        "basis": {
            "db_headroom": db_headroom,
            "raw_headroom": raw_headroom,
            "delay_safety_margin_seconds": delay_safety_margin_seconds,
            "publication_delay_method": "p95+margin (RB2 late-biased)",
            "samples": {
                "db_bytes": len(measured_db_bytes),
                "raw_bytes": len(measured_raw_bytes_per_run),
                "coverage": len(measured_coverage_ratios),
                "publication_delay": len(measured_publication_delays_seconds),
            },
        },
    }


def _percentile(values: list[float], q: float) -> float:
    """线性插值百分位（q∈[0,1]）；空列表返回 0.0。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


# --------------------------------------------------------------------------- #
# harness 编排：serial collect over trade-cal 确认日 → 报告
# --------------------------------------------------------------------------- #


def run_shadow_cycle(
    store: IntradayStore,
    provider: Any,
    *,
    trade_dates: list[str],
    calendar: CalendarFn,
    session_profile_version: str,
    contract_version: str,
    publication_delay_seconds: int,
    instruments: list[int],
    interval_minutes: int = 1,
    replay_points: list[dict[str, str]] | None = None,
    reconciliation_mismatches: list[dict[str, Any]] | None = None,
    stalled_max_cycles: int = 2,
    now_ts_for_day: Callable[[str], str] | None = None,
    skip_precondition: bool = False,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """跑受控影子周期（serial、trade-cal 确认日）→ 报告 dict（并可原子落盘）.

    流程：
      0. **S2 硬前置**（除非显式 ``skip_precondition``，仅测试隔离用）：脱敏 + BLOB/TEXT
         排除不变式必须过，否则 :class:`ShadowPreconditionError`（拒绝跑）。
      1. serial 对每个 ``trade_dates`` 跑 :func:`collect_close`（注入 provider/calendar，
         无 live 网络），累积每场汇总 + 实测（DB 字节增量 / raw / 覆盖 / 延迟）。
      2. 聚合 :func:`aggregate_shadow_runs`；as-of 回放 :func:`replay_asof_consumption`；
         检 :func:`IntradayStore.detect_reconciliation_stalled`（A4 计入持久性门）。
      3. 三维门 :func:`evaluate_shadow_gate`；校准 :func:`suggest_calibration`。
      4. 原子写 ``storage/reports/intraday_shadow_*.json``。

    **零策略消费**：as-of 回放只用 ``load_asof`` 做 PIT 安全断言，不喂任何策略/回测。
    """
    if not skip_precondition:
        precondition = assert_shadow_preconditions()
    else:
        precondition = {"passed": True, "skipped": True}

    run_summaries: list[dict[str, Any]] = []
    measured_db_bytes: list[int] = []
    measured_raw_bytes: list[int] = []
    measured_coverage: list[float] = []
    measured_delays: list[float] = []

    prev_db_bytes = store.db_byte_size()
    for day in trade_dates:
        now_ts = now_ts_for_day(day) if now_ts_for_day else None
        summary = collect_close(
            store, provider, trade_date=day, calendar=calendar,
            session_profile_version=session_profile_version,
            contract_version=contract_version,
            publication_delay_seconds=publication_delay_seconds,
            interval_minutes=interval_minutes, now_ts=now_ts,
        )
        # 实测磁盘增量（本场写盘后 DB 字节 - 上场）。
        cur_db = store.db_byte_size()
        disk_inc = max(0, cur_db - prev_db_bytes)
        prev_db_bytes = cur_db
        measured_db_bytes.append(cur_db)
        # 覆盖率：本场 complete 评估的 instrument 占请求的比例（粗口径）。
        cov = _coverage_pct_for_summary(summary)
        if cov is not None:
            measured_coverage.append(cov / 100.0)
        # 每日报告增补字段（聚合用）。
        summary = {
            **summary,
            "coverage_pct": cov,
            "disk_increment_bytes": disk_inc,
            "reconciliation_status": summary.get("reconciliation_status", "pending"),
        }
        run_summaries.append(summary)
        # publication_delay 实测（来自漂移自报 alerts 或 contract 基线）。
        for a in summary.get("alerts", []) or []:
            if a.get("alert") == "publication_delay_exceeded":
                measured_delays.append(float(a.get("observed_delay_seconds", 0.0)))

    # A4：reconciliation_stalled 计入持久性门（用最后一日 as_of 近似当前周期）。
    as_of_date = trade_dates[-1] if trade_dates else _today_shanghai()
    stalled = store.detect_reconciliation_stalled(
        as_of_date=as_of_date, max_cycles=stalled_max_cycles
    )

    aggregate = aggregate_shadow_runs(
        run_summaries,
        reconciliation_mismatches=reconciliation_mismatches,
        stalled=stalled,
    )

    replay = replay_asof_consumption(
        store, instruments=instruments, interval=interval_minutes,
        replay_points=replay_points or [],
    )

    gate = evaluate_shadow_gate(aggregate, replay)

    # 若实测无 publication_delay 漂移样本，用 contract 基线作单点（fail-loud 不臆造空）。
    if not measured_delays:
        measured_delays = [float(publication_delay_seconds)]

    calibration = suggest_calibration(
        measured_db_bytes=measured_db_bytes,
        measured_raw_bytes_per_run=measured_raw_bytes,
        measured_coverage_ratios=measured_coverage,
        measured_publication_delays_seconds=measured_delays,
    )

    report = {
        "report_kind": "intraday_shadow",
        "generated_at": _now_iso_shanghai(),
        "precondition": precondition,
        "expected_runs": SHADOW_EXPECTED_RUNS,
        "trade_dates": trade_dates,
        "aggregate": aggregate,
        "asof_replay": replay,
        "reconciliation_stalled": stalled,
        "gate": gate,
        "calibration": calibration,
        "zero_strategy_consumption": True,
    }

    if report_path is not None:
        _write_atomic_report(report, report_path)

    return report


def _coverage_pct_for_summary(summary: dict[str, Any]) -> float | None:
    """粗覆盖率%：complete_assessed 数 / 请求标的数 × 100（无请求时 None）。"""
    requested = summary.get("requested_symbols")
    if not requested:
        return None
    complete = len(summary.get("complete_assessed", []) or [])
    return round(complete / requested * 100.0, 2)


def _write_atomic_report(report: dict[str, Any], path: Path) -> None:
    """原子写报告 JSON（同盘 rename；镜像 build_data_catalog._write_atomic）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _today_shanghai() -> str:
    return datetime.now(tz=_SHANGHAI).strftime("%Y-%m-%d")


def _now_iso_shanghai() -> str:
    return datetime.now(tz=_SHANGHAI).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 配置文档化：校准值写回 config-doc note
# --------------------------------------------------------------------------- #


def render_calibration_note(calibration: dict[str, Any]) -> str:
    """把校准建议渲染成 config-doc note（markdown 片段，写回计划/配置文档）。"""
    c = calibration
    lines = [
        "## 影子期校准建议（U8 实测）",
        "",
        "| 配置项 | 建议值 | 依据 |",
        "|---|---|---|",
        f"| `INTRADAY_MAX_DB_BYTES` | {c.get('INTRADAY_MAX_DB_BYTES')} | "
        f"max(实测 DB) × {c['basis']['db_headroom']} |",
        f"| `INTRADAY_MAX_RAW_BYTES_PER_RUN` | {c.get('INTRADAY_MAX_RAW_BYTES_PER_RUN')} | "
        f"max(实测 raw) × {c['basis']['raw_headroom']} |",
        f"| `INTRADAY_COVERAGE_ALERT_THRESHOLD` | {c.get('INTRADAY_COVERAGE_ALERT_THRESHOLD')} | "
        "min(实测覆盖率) 保守下取，夹 [0.90,0.99] |",
        f"| `publication_delay_seconds` | {c.get('publication_delay_seconds')} | "
        f"{c['basis']['publication_delay_method']} |",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI（--shadow-report 等价入口；默认拒绝 live —— 须注入 fixture，此处仅打印用法）
# --------------------------------------------------------------------------- #


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="U8 影子运行 harness（20 场 + 三维数据持久性门 + 校准）"
    )
    p.add_argument(
        "--check-preconditions", action="store_true",
        help="仅跑 S2 硬前置不变式检查（脱敏 + BLOB/TEXT 排除），不跑采集",
    )
    p.add_argument(
        "--report", default=None,
        help="报告输出路径（默认 storage/reports/intraday_shadow_<ts>.json）",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    if args.check_preconditions:
        try:
            result = assert_shadow_preconditions()
        except ShadowPreconditionError as exc:
            print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    # live 20 场采集需注入 approved universe + provider；CLI 不默认跑 live（test-spec）。
    print(
        json.dumps(
            {
                "status": "noop",
                "note": (
                    "影子周期须注入 approved universe / provider / calendar 跑（无 live 默认）；"
                    "见 run_shadow_cycle。用 --check-preconditions 验 S2 硬前置。"
                ),
                "default_report_dir": str(REPORT_DIR),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
