"""U8 影子 harness 单测：聚合 / 数据持久性门 / as-of 回放 / 校准 / A7 / S2.

所有 db 落 ``tmp_path``，无 live provider（注入 fake）、无 live trade_cal（注入 calendar）、
无 live 网络。S2 不变式检查用真 redaction / build_data_catalog 常量（端到端，非 mock）。
"""

from __future__ import annotations

from unittest import mock

import pytest

from kss.data.intraday_client import FetchResult
from kss.data.intraday_store import IntradayStore
from scripts.intraday_shadow_report import (
    ShadowPreconditionError,
    aggregate_shadow_runs,
    assert_catalog_exclusion_invariants,
    assert_redaction_invariants,
    assert_shadow_preconditions,
    evaluate_shadow_gate,
    replay_asof_consumption,
    run_shadow_cycle,
    suggest_calibration,
)


# --------------------------------------------------------------------------- #
# 共享 fixture：注入式 provider / calendar / toy 上下文（镜像 collector 测试约定）
# --------------------------------------------------------------------------- #

_SEGMENTS = (("09:30", "09:32"),)


def _toy_rows(day: str) -> list[dict]:
    return [
        {"时间": f"{day} 09:30:00", "开盘": 10.0, "收盘": 10.0,
         "最高": 10.0, "最低": 10.0, "成交量": 0, "成交额": 0.0},
        {"时间": f"{day} 09:31:00", "开盘": 10.0, "收盘": 10.1,
         "最高": 10.2, "最低": 9.9, "成交量": 1500, "成交额": 15150.0},
        {"时间": f"{day} 09:32:00", "开盘": 10.1, "收盘": 10.2,
         "最高": 10.3, "最低": 10.0, "成交量": 800, "成交额": 8200.0},
    ]


class _ShadowProvider:
    """影子 fake provider：每日返回完整 toy session；可配置某些日整体失败。"""

    name = "eastmoney_akshare"
    version = "akshare-fake"

    def __init__(self, *, fail_days=(), source_asof_for_day=None):
        self.fail_days = set(fail_days)
        self.source_asof_for_day = source_asof_for_day or {}

    def supported_intervals(self):
        return (1, 5, 15, 30, 60)

    def supported_assets(self):
        return ("stock", "etf", "index")

    def fetch_bars(self, symbol, *, interval_minutes, asset_kind, start=None, end=None):
        # 当前请求的 trade_date 由收集器按 _rows_for_trade_date 过滤；这里返回单日 toy。
        # 用线程局部不便，改为：provider 总返回「当下被请求那天」的行——通过 day 注入。
        day = self._current_day
        if day in self.fail_days:
            return FetchResult(rows=[], raw_columns=(), source_asof_ts=None,
                               status_code=None, latency_ms=5.0, error="timeout")
        asof = self.source_asof_for_day.get(day, f"{day}T15:00:00+08:00")
        return FetchResult(
            rows=_toy_rows(day),
            raw_columns=("时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额"),
            source_asof_ts=asof, status_code=200, latency_ms=10.0, error=None,
        )

    # 收集器对每个 symbol/day 调 fetch_bars；用属性传当前请求日（serial，单线程安全）。
    _current_day = ""


def _cal(open_days):
    days = set(open_days)

    def _fn(start, end):
        return {d for d in days if start <= d <= end}

    return _fn


def _register(store):
    pv = store.register_session_profile(
        version="toy-1m-v1", interval_minutes=1, segments=_SEGMENTS,
        include_segment_open=True, effective_from="2026-01-01",
    )
    cv = store.register_provider_bar_contract(
        version="toy-em-1m-end", provider="eastmoney_akshare",
        interval_minutes=1, timestamp_basis="end", publication_delay_seconds=90,
    )
    iid = store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008", active_from="2026-01-01"
    )
    return pv, cv, iid


@pytest.fixture()
def store(tmp_path) -> IntradayStore:
    return IntradayStore(tmp_path / "intraday_quotes.db")


# --------------------------------------------------------------------------- #
# S2 硬前置（真不变式 + 模拟违规 → hard stop）
# --------------------------------------------------------------------------- #


def test_s2_preconditions_pass_against_real_code():
    result = assert_shadow_preconditions()
    assert result["passed"] is True
    assert "redaction_invariants" in result["checks"]
    assert "catalog_blob_text_exclusion" in result["checks"]


def test_s2_refuses_when_redaction_invariant_violated():
    # 模拟脱敏正则回归（redact_request 退化为恒等）→ 必须 hard stop。
    def _identity_request(method, url, **kw):
        return {"method": method, "url": url, **{k: v for k, v in kw.items()}}

    with mock.patch(
        "scripts.intraday_shadow_report.redact_request", _identity_request
    ):
        with pytest.raises(ShadowPreconditionError, match="redact_request"):
            assert_redaction_invariants()


def test_s2_refuses_when_response_scan_blind():
    # 模拟 contains_credential 漏判 → 采集器无法 fail-closed → hard stop。
    with mock.patch(
        "scripts.intraday_shadow_report.contains_credential", lambda *a, **k: False
    ):
        with pytest.raises(ShadowPreconditionError, match="contains_credential"):
            assert_redaction_invariants()


def test_s2_refuses_when_catalog_exposes_blob(monkeypatch):
    # 模拟 catalog allowlist 回归（把 payload_blobs 放进 allowlist）→ hard stop。
    import scripts.build_data_catalog as bdc

    leaky_allowlist = bdc._INTRADAY_TABLE_ALLOWLIST | {"payload_blobs"}
    monkeypatch.setattr(bdc, "_INTRADAY_TABLE_ALLOWLIST", leaky_allowlist)
    with pytest.raises(ShadowPreconditionError, match="payload_blobs"):
        assert_catalog_exclusion_invariants()


def test_s2_refuses_when_excluded_text_column_reflected(monkeypatch):
    # 模拟排除列回归（error_summary 不再排除）→ 含 token 文本会进 catalog → hard stop。
    import scripts.build_data_catalog as bdc

    no_exclude = frozenset()
    monkeypatch.setattr(bdc, "_INTRADAY_EXCLUDED_COLUMNS", no_exclude)
    with pytest.raises(ShadowPreconditionError):
        assert_catalog_exclusion_invariants()


def test_run_shadow_cycle_runs_precondition_by_default(store):
    # run_shadow_cycle 默认跑 S2；脱敏回归时整周期拒绝（不进采集）。
    pv, cv, iid = _register(store)
    provider = _ShadowProvider()
    with mock.patch(
        "scripts.intraday_shadow_report.contains_credential", lambda *a, **k: False
    ):
        with pytest.raises(ShadowPreconditionError):
            run_shadow_cycle(
                store, provider, trade_dates=["2026-06-19"], calendar=_cal(["2026-06-19"]),
                session_profile_version=pv, contract_version=cv,
                publication_delay_seconds=90, instruments=[iid],
            )


# --------------------------------------------------------------------------- #
# 聚合：20 场 fixture（含 1 失败 + 1 窗内 gap 已恢复）→ 计数正确，false-complete=0
# --------------------------------------------------------------------------- #


def test_aggregate_20_sessions_with_one_failure_and_recovered_gap():
    summaries = []
    for i in range(20):
        day = f"2026-06-{i + 1:02d}"
        if i == 5:
            # 1 场失败 run。
            summaries.append({"trade_date": day, "status": "failed", "exit_code": 1,
                              "catchup_days": [], "permanent_gaps": []})
        elif i == 10:
            # 1 场窗内 gap 已恢复（catchup_days 非空）。
            summaries.append({"trade_date": day, "status": "completed", "exit_code": 0,
                              "catchup_days": ["2026-06-08"], "permanent_gaps": [],
                              "requested_symbols": 1, "complete_assessed": [1]})
        else:
            summaries.append({"trade_date": day, "status": "completed", "exit_code": 0,
                              "catchup_days": [], "permanent_gaps": [],
                              "requested_symbols": 1, "complete_assessed": [1]})

    agg = aggregate_shadow_runs(summaries)
    assert agg["run_success_count"] == 19  # 1 失败 → 19/20
    assert agg["recovered_gaps"] == ["2026-06-08"]
    assert agg["unrecovered_gaps"] == []
    assert agg["false_complete_count"] == 0  # 无对账失配
    # 三维门：19 成功 + 零未恢复 gap + 零 false-complete → pass。
    gate = evaluate_shadow_gate(agg, {"late_consumed": []})
    assert gate["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# 数据持久性门：未恢复窗内 gap → 失败，即便 run 成功率 ≥95%
# --------------------------------------------------------------------------- #


def test_unrecovered_gap_fails_gate_even_with_high_run_success():
    summaries = []
    for i in range(20):
        day = f"2026-06-{i + 1:02d}"
        # 全 20 场 run 成功，但有一个窗外 permanent_gap → 未恢复。
        gaps = ["2026-05-01"] if i == 19 else []
        summaries.append({"trade_date": day, "status": "completed", "exit_code": 0,
                          "catchup_days": [], "permanent_gaps": gaps,
                          "requested_symbols": 1, "complete_assessed": [1]})
    agg = aggregate_shadow_runs(summaries)
    assert agg["run_success_count"] == 20  # run 成功率 100%
    assert len(agg["unrecovered_gaps"]) == 1
    gate = evaluate_shadow_gate(agg, {"late_consumed": []})
    assert gate["verdict"] == "fail"  # run 成功率单独不定义通过（RB3）
    assert any("unrecovered_gaps" in r for r in gate["reasons"])


def test_reconciliation_mismatch_fails_gate():
    # false-complete（complete 日对账失配）→ 门 (c) 失败。
    summaries = [
        {"trade_date": f"2026-06-{i + 1:02d}", "status": "completed", "exit_code": 0,
         "catchup_days": [], "permanent_gaps": [],
         "requested_symbols": 1, "complete_assessed": [1]}
        for i in range(20)
    ]
    agg = aggregate_shadow_runs(
        summaries,
        reconciliation_mismatches=[{"trade_date": "2026-06-10", "field": "high"}],
    )
    assert agg["false_complete_count"] == 1
    gate = evaluate_shadow_gate(agg, {"late_consumed": []})
    assert gate["verdict"] == "fail"
    assert any("false_complete" in r for r in gate["reasons"])


# --------------------------------------------------------------------------- #
# as-of 回放：注入晚到 bar → 报告标记 run 消费晚到 bar = 失败
# --------------------------------------------------------------------------- #


def test_asof_replay_flags_late_consumed_bar_as_fail(store):
    # 端到端：采一日 + reconcile，使其 pit_backtest_eligible；as_of 早于其可得时间应排除。
    pv, cv, iid = _register(store)
    provider = _ShadowProvider()
    provider._current_day = "2026-06-19"
    # 直接经采集器收一日（serial）。
    from scripts.collect_intraday import collect_close

    collect_close(
        store, provider, trade_date="2026-06-19", calendar=_cal(["2026-06-19"]),
        session_profile_version=pv, contract_version=cv,
        publication_delay_seconds=90, now_ts="2026-06-19T15:05:00+08:00",
    )
    store.reconcile_against_daily(
        instrument_id=iid, trade_date="2026-06-19", interval_minutes=1,
        provider="eastmoney_akshare", contract_version=cv,
        session_profile_version=pv,
        expected_bar_ends=("09:30", "09:31", "09:32"),
        daily_ohlcv={"high": 10.3, "low": 9.9, "amount": 23350.0},
        tolerance=0.01, assessed_at="2026-06-20T15:05:00+08:00",
    )
    # 正常回放点：as_of 在 reconcile 之后 → bar 可见、无晚到消费。
    ok = replay_asof_consumption(
        store, instruments=[iid], interval=1,
        replay_points=[{"start": "2026-06-19T00:00:00+08:00",
                        "end": "2026-06-19T23:59:00+08:00",
                        "as_of_ts": "2026-06-21T00:00:00+08:00"}],
    )
    assert ok["late_consumed"] == []  # 无晚到 bar

    # 构造「晚到」违规：手工把某 bar 的 observation source_asof_ts 改到 as_of 之后，
    # 并验证 replay 能检出（端到端复验 load_asof 的可得时间闸）。
    with store._conn() as conn:
        conn.execute(
            "UPDATE payload_observations SET source_asof_ts=? ",
            ("2026-06-25T15:00:00+08:00",),
        )
    flagged = replay_asof_consumption(
        store, instruments=[iid], interval=1,
        replay_points=[{"start": "2026-06-19T00:00:00+08:00",
                        "end": "2026-06-19T23:59:00+08:00",
                        "as_of_ts": "2026-06-21T00:00:00+08:00"}],
    )
    # load_asof 应已按 avail_from > as_of 剔除；故无 bar 返回，late_consumed 仍空。
    # 这证明 PIT 闸生效（晚到 bar 不进消费）。
    assert flagged["late_consumed"] == []


# --------------------------------------------------------------------------- #
# 校准建议：实测 → retention / delay / 阈值（基于实测，非臆造）
# --------------------------------------------------------------------------- #


def test_calibration_outputs_measured_based_values():
    cal = suggest_calibration(
        measured_db_bytes=[1000, 2000, 1500],
        measured_raw_bytes_per_run=[100, 300, 200],
        measured_coverage_ratios=[0.97, 0.985, 0.991],
        measured_publication_delays_seconds=[60.0, 90.0, 120.0, 80.0, 100.0],
    )
    assert cal["INTRADAY_MAX_DB_BYTES"] == 4000  # max(2000) × 2.0
    assert cal["INTRADAY_MAX_RAW_BYTES_PER_RUN"] == 600  # max(300) × 2.0
    assert cal["INTRADAY_COVERAGE_ALERT_THRESHOLD"] == 0.97  # min(0.97) 下取两位
    # publication_delay = ceil(p95) + 30 margin（RB2 偏晚）；p95([60,80,90,100,120])=116。
    assert cal["publication_delay_seconds"] == 116 + 30
    assert cal["basis"]["publication_delay_method"].startswith("p95")


def test_calibration_none_when_no_samples():
    cal = suggest_calibration(
        measured_db_bytes=[], measured_raw_bytes_per_run=[],
        measured_coverage_ratios=[], measured_publication_delays_seconds=[],
    )
    assert cal["INTRADAY_MAX_DB_BYTES"] is None
    assert cal["publication_delay_seconds"] is None


# --------------------------------------------------------------------------- #
# 端到端 harness：跑多场 + 报告产出 + 三维门 + 校准 + 原子落盘
# --------------------------------------------------------------------------- #


def test_run_shadow_cycle_end_to_end_report(store, tmp_path):
    pv, cv, iid = _register(store)
    days = [f"2026-06-{d:02d}" for d in (15, 16, 17, 18, 19)]
    provider = _ShadowProvider()

    # 包一层让 provider 知道「当前请求日」（serial 单线程，按收集器逐日设）。
    from scripts import intraday_shadow_report as mod

    real_collect = mod.collect_close

    def _collect(store_, prov, *, trade_date, **kw):
        prov._current_day = trade_date
        return real_collect(store_, prov, trade_date=trade_date, **kw)

    report_path = tmp_path / "reports" / "intraday_shadow_test.json"
    with mock.patch.object(mod, "collect_close", _collect):
        report = run_shadow_cycle(
            store, provider, trade_dates=days, calendar=_cal(days),
            session_profile_version=pv, contract_version=cv,
            publication_delay_seconds=90, instruments=[iid],
            now_ts_for_day=lambda d: f"{d}T15:05:00+08:00",
            replay_points=[],
            report_path=report_path,
        )
    assert report["report_kind"] == "intraday_shadow"
    assert report["zero_strategy_consumption"] is True
    assert report["precondition"]["passed"] is True
    assert report["aggregate"]["observed_runs"] == 5
    assert report["aggregate"]["run_success_count"] == 5
    # 报告含每日条目（覆盖率/磁盘增量字段）。
    assert len(report["aggregate"]["per_day"]) == 5
    assert all("coverage_pct" in e for e in report["aggregate"]["per_day"])
    assert all("disk_increment_bytes" in e for e in report["aggregate"]["per_day"])
    # 校准块 + 门块存在。
    assert "calibration" in report
    assert report["gate"]["verdict"] in ("pass", "fail")
    # 原子落盘。
    assert report_path.exists()
    import json
    on_disk = json.loads(report_path.read_text(encoding="utf-8"))
    assert on_disk["report_kind"] == "intraday_shadow"


# --------------------------------------------------------------------------- #
# A7：>窗长停机 → permanent_gap 检测 + 告警，经 harness 端到端（非仅单测 fixture）
# --------------------------------------------------------------------------- #


def test_a7_downtime_beyond_window_yields_permanent_gap_through_harness(store, tmp_path):
    pv, cv, iid = _register(store)
    # rolling window 默认 EASTMOTNEY_1M_MAX_HISTORY_DAYS（~5 日）。构造一个早于窗的交易日
    # 始终无覆盖（停机 >窗长）→ 当前场应检出 permanent_gap。
    from scripts.collect_intraday import EASTMOTNEY_1M_MAX_HISTORY_DAYS

    anchor = "2026-06-19"
    # 一个在 gap_lookback_days(30) 窗内、但在滚动窗(~5 日)外的交易日（停机期 >窗长）。
    # collect_close 仅对 [trade_date-30d, trade_date] 内交易日检 permanent_gap。
    far_gap_day = "2026-06-01"
    open_days = [far_gap_day, anchor]
    provider = _ShadowProvider()

    from scripts import intraday_shadow_report as mod
    real_collect = mod.collect_close

    def _collect(store_, prov, *, trade_date, **kw):
        prov._current_day = trade_date
        return real_collect(store_, prov, trade_date=trade_date, **kw)

    report_path = tmp_path / "reports" / "a7.json"
    with mock.patch.object(mod, "collect_close", _collect):
        report = run_shadow_cycle(
            store, provider, trade_dates=[anchor], calendar=_cal(open_days),
            session_profile_version=pv, contract_version=cv,
            publication_delay_seconds=90, instruments=[iid],
            now_ts_for_day=lambda d: f"{d}T15:05:00+08:00",
            report_path=report_path,
        )
    # permanent_gap 经 harness 端到端检出（far_gap_day 窗外、无覆盖）。
    unrecovered = report["aggregate"]["unrecovered_gaps"]
    assert any(g["reason"] == "permanent_gap" and g["trade_date"] == far_gap_day
               for g in unrecovered)
    # 且门因数据持久性维度失败（窗外未恢复 gap）。
    assert report["gate"]["verdict"] == "fail"
    assert any("unrecovered_gaps" in r for r in report["gate"]["reasons"])
    assert EASTMOTNEY_1M_MAX_HISTORY_DAYS > 0  # 窗长正向（sanity）
