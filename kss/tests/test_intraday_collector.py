"""收集器单测：fail-closed 零调用（U2）+ U5 加固（trade_cal/追补/retention/部分失败）.

所有 db 落 ``tmp_path``，无 live provider 调用（FakeProvider 注入）、无 live trade_cal
（calendar 函数注入）。U5 场景见文件下半部。
"""

from __future__ import annotations

import pytest

from kss.data.intraday_client import FetchResult
from kss.data.intraday_store import IntradayStore
from scripts.collect_intraday import collect_close


def _open_cal(*open_days: str):
    """构造一个注入用日历函数：返回给定开市日中落在 [start,end] 窗内的集合。"""
    days = set(open_days)

    def _cal(start: str, end: str) -> set[str]:
        return {d for d in days if start <= d <= end}

    return _cal


def _raising_cal(_start: str, _end: str) -> set[str]:
    """模拟 trade_cal 不可达（KTD3：必须终止 calendar_unknown，绝不 fallback）。"""
    raise RuntimeError("trade_cal unreachable")


class _RecordingProvider:
    """记录 fetch_bars 调用的 fake provider，用于钉「零调用」不变式。"""

    name = "eastmoney_akshare"
    version = "akshare-fake"

    def __init__(self):
        self.calls: list[str] = []

    def supported_intervals(self):
        return (1, 5, 15, 30, 60)

    def supported_assets(self):
        return ("stock", "etf", "index")

    def fetch_bars(self, symbol, *, interval_minutes, asset_kind, start=None, end=None):
        self.calls.append(symbol)
        rows = [
            {"时间": "2026-06-19 14:59:00", "收盘": 10.1, "成交量": 1000},
            {"时间": "2026-06-19 15:00:00", "收盘": 10.2, "成交量": 1500},
        ]
        return FetchResult(
            rows=rows,
            raw_columns=("时间", "收盘", "成交量"),
            source_asof_ts="2026-06-19T15:00:00+08:00",
            status_code=200,
            latency_ms=10.0,
            error=None,
        )

    def capability(self):  # pragma: no cover — collector 不调
        raise NotImplementedError


@pytest.fixture()
def store(tmp_path) -> IntradayStore:
    return IntradayStore(tmp_path / "intraday_quotes.db")


def test_dry_run_plans_without_writing(store):
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008", active_from="2026-01-01"
    )
    provider = _RecordingProvider()
    summary = collect_close(
        store, provider, trade_date="2026-06-19", dry_run=True
    )
    assert summary["status"] == "planned"
    assert summary["planned_symbols"] == [{"symbol": "688008.SH", "asset_kind": "stock"}]
    # dry-run 零调用、零落盘。
    assert provider.calls == []
    assert store.count_observations() == 0


def test_resolved_symbol_fetched_and_appended(store):
    iid = store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008", active_from="2026-01-01"
    )
    provider = _RecordingProvider()
    summary = collect_close(
        store, provider, trade_date="2026-06-19", calendar=_open_cal("2026-06-19")
    )
    assert summary["status"] == "completed"
    assert summary["observations_written"] == 1
    assert summary["blobs_written"] == 1
    assert provider.calls == ["688008"]  # 用 provider_symbol（裸码）取数
    # lineage 落地。
    obs = store.list_observations(summary["run_id"])
    assert len(obs) == 1 and obs[0]["instrument_id"] == iid


def test_unknown_mapping_skipped_zero_call(store):
    """无活跃映射的标的：跳过且零 provider 调用。"""
    # 注册一个已过期映射 → 解析为 unknown。
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008",
        active_from="2026-01-01", active_to="2026-03-31",
    )
    provider = _RecordingProvider()
    summary = collect_close(
        store, provider, trade_date="2026-06-19", calendar=_open_cal("2026-06-19")
    )
    assert summary["status"] == "completed"
    assert "688008.SH" in summary["skipped_symbols"]
    assert provider.calls == []  # 零调用
    assert store.count_observations() == 0


def test_ambiguous_mapping_terminates_zero_call(store):
    """>1 活跃映射：终止 mapping_ambiguous，零 provider 调用，留终止 failed run。"""
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008", active_from="2026-01-01"
    )
    store.register_instrument(
        "688008.SH", "stock", "eastmoney_akshare", "688008X", active_from="2026-02-01"
    )
    provider = _RecordingProvider()
    summary = collect_close(
        store, provider, trade_date="2026-06-19", calendar=_open_cal("2026-06-19")
    )
    assert summary["status"] == "failed"
    assert summary["failure_reason"] == "mapping_ambiguous"
    assert summary["exit_code"] == 1
    assert provider.calls == []  # 零调用
    # 留一条终止 failed run，无数据行。
    run = store.get_run(summary["run_id"])
    assert run["status"] == "failed" and run["failure_reason"] == "mapping_ambiguous"
    assert store.count_observations() == 0


# =========================================================================== #
# U5 加固场景：trade_cal 终止 / 部分失败 / retention / 窗内追补 / M2 / M1 / happy
# =========================================================================== #

from scripts.collect_intraday import collect_close as _cc  # noqa: E402,F811
import scripts.collect_intraday as _collect_mod  # noqa: E402


_TOY_DAY = "2026-06-19"
# 玩具 1m profile：3 端点 09:30/09:31/09:32（含开盘竞价 bar），便于钉 complete。
_TOY_SEGMENTS = (("09:30", "09:32"),)
_TOY_ENDS = ("09:30", "09:31", "09:32")


def _toy_session_rows(day: str) -> list[dict]:
    """该日完整玩具当场 session（3 根合法 bar，含零成交开盘 bar）。"""
    return [
        {"时间": f"{day} 09:30:00", "开盘": 10.0, "收盘": 10.0,
         "最高": 10.0, "最低": 10.0, "成交量": 0, "成交额": 0.0},
        {"时间": f"{day} 09:31:00", "开盘": 10.0, "收盘": 10.1,
         "最高": 10.2, "最低": 9.9, "成交量": 1500, "成交额": 15150.0},
        {"时间": f"{day} 09:32:00", "开盘": 10.1, "收盘": 10.2,
         "最高": 10.3, "最低": 10.0, "成交量": 800, "成交额": 8200.0},
    ]


class _SessionProvider:
    """可配置 fake provider：模拟东财滚动 1m 响应（含窗内**多日**行）。

    每次 ``fetch_bars`` 返回 ``window_days`` 全部交易日的玩具 session 行（如真实滚动响应
    含近 5 日）；收集器按请求 ``trade_date`` 用 ``_rows_for_trade_date`` 过滤。

    - ``fail_symbols``：这些符号永远返回错误（用于部分失败/有界重试）。
    - ``extra_day``：额外塞入一个**窗外**日期的行（验「当场 session-only」过滤）。
    - ``window_days``：滚动响应含哪些日（默认仅 ``_TOY_DAY``）。
    - ``day_for_call``：响应 ``source_asof_ts`` 取该日 15:00（最晚 bar；M1 漂移基准）。
    """

    name = "eastmoney_akshare"
    version = "akshare-fake"

    def __init__(self, *, fail_symbols=(), extra_day=None, window_days=None):
        self.symbol_calls: list[str] = []
        self.fail_symbols = set(fail_symbols)
        self.extra_day = extra_day
        self.window_days = list(window_days) if window_days else [_TOY_DAY]
        self.day_for_call = _TOY_DAY

    def supported_intervals(self):
        return (1, 5, 15, 30, 60)

    def supported_assets(self):
        return ("stock", "etf", "index")

    def fetch_bars(self, symbol, *, interval_minutes, asset_kind, start=None, end=None):
        self.symbol_calls.append(symbol)
        if symbol in self.fail_symbols:
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None,
                status_code=None, latency_ms=5.0, error="timeout",
            )
        rows: list[dict] = []
        for d in self.window_days:
            rows.extend(_toy_session_rows(d))
        if self.extra_day:
            rows = rows + _toy_session_rows(self.extra_day)
        return FetchResult(
            rows=rows, raw_columns=("时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额"),
            source_asof_ts=f"{self.day_for_call}T15:00:00+08:00",
            status_code=200, latency_ms=10.0, error=None,
        )

    def capability(self):  # pragma: no cover
        raise NotImplementedError


def _register_toy_context(store):
    """注册玩具 session profile + contract，返回 (pv, cv)。"""
    pv = store.register_session_profile(
        version="toy-1m-v1", interval_minutes=1, segments=_TOY_SEGMENTS,
        include_segment_open=True, effective_from="2026-01-01",
    )
    cv = store.register_provider_bar_contract(
        version="toy-em-1m-end", provider="eastmoney_akshare",
        interval_minutes=1, timestamp_basis="end", publication_delay_seconds=90,
    )
    return pv, cv


def _register_three_symbols(store):
    ids = {}
    for sym, code in (("688008.SH", "688008"), ("688009.SH", "688009"),
                      ("688012.SH", "688012")):
        ids[sym] = store.register_instrument(
            sym, "stock", "eastmoney_akshare", code, active_from="2026-01-01"
        )
    return ids


# --- 集成 #1：happy path（三标的完整 session → 一完成 run + canonical + complete） --- #


def test_integration_1_happy_path_three_symbols(store):
    ids = _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    provider = _SessionProvider()
    provider.day_for_call = _TOY_DAY
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
        publication_delay_seconds=90, now_ts="2026-06-19T15:05:00+08:00",
    )
    assert summary["status"] == "completed"
    assert summary["exit_code"] == 0
    assert set(summary["succeeded_symbols"]) == {"688008.SH", "688009.SH", "688012.SH"}
    assert summary["observations_written"] == 3
    # raw payload hash（blob）+ canonical 行 + complete 评估都落地。
    assert summary["blobs_written"] >= 1
    assert store.count_observations(summary["run_id"]) == 3
    for sym, iid in ids.items():
        canon = store.list_canonical_bars(instrument_id=iid)
        assert len(canon) == 3  # 三根 canonical bar
        assert iid in summary["complete_assessed"]
        cov = store.list_coverage_assessments(instrument_id=iid)
        assert any(c["assessment_kind"] == "complete" and c["status"] == "passed"
                   for c in cov)


# --- 集成 #2：部分失败（一标的超时 → per-symbol 失败、聚合非零、无全局 complete） --- #


def test_integration_2_partial_failure_per_symbol(store):
    ids = _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    provider = _SessionProvider(fail_symbols={"688009"})  # provider_symbol 裸码
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
        now_ts="2026-06-19T15:05:00+08:00", sleep=lambda _s: None,  # 不真睡，加速重试
    )
    # 聚合 run 非零；成功标的仍 per-symbol 落地。
    assert summary["exit_code"] == 1
    assert "688009.SH" in summary["failed_symbols"]
    assert set(summary["succeeded_symbols"]) == {"688008.SH", "688012.SH"}
    # 失败标的有界重试：3 次尝试。
    assert provider.symbol_calls.count("688009") == 3
    # 失败标的无 complete；该日不全局 complete。
    assert ids["688009.SH"] not in summary["complete_assessed"]
    cov_fail = store.list_coverage_assessments(instrument_id=ids["688009.SH"])
    assert all(c["status"] != "passed"
               for c in cov_fail if c["assessment_kind"] == "complete") or not cov_fail


# --- 集成 #7：trade_cal 不可达 → calendar_unknown 终止、非零、无 fallback --- #


def test_integration_7_calendar_unreachable_terminates(store):
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    provider = _SessionProvider()
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_raising_cal,
        session_profile_version=pv, contract_version=cv,
    )
    assert summary["status"] == "failed"
    assert summary["failure_reason"] == "calendar_unknown"
    assert summary["exit_code"] == 1
    # 绝无 weekday/archive fallback：零 provider 调用、留终止 run、无数据行。
    assert provider.symbol_calls == []
    run = store.get_run(summary["run_id"])
    assert run["status"] == "failed" and run["failure_reason"] == "calendar_unknown"
    assert store.count_observations() == 0


def test_non_trade_day_skips(store):
    _register_three_symbols(store)
    provider = _SessionProvider()
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal("2026-06-18"),
    )
    assert summary["status"] == "skipped"
    assert summary["reason"] == "non_trade_day"
    assert provider.symbol_calls == []  # 非交易日零调用


# --- U10 retention：超额 → retention_limit 终止、无可查行 --- #


def test_u10_retention_db_limit_terminates(store, monkeypatch):
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    monkeypatch.setattr(_collect_mod, "INTRADAY_MAX_DB_BYTES", 0)  # 任何库都超限
    provider = _SessionProvider()
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
    )
    assert summary["status"] == "failed"
    assert summary["failure_reason"] == "retention_limit"
    assert summary["exit_code"] == 1
    assert provider.symbol_calls == []  # 采集前即终止，零调用
    assert store.count_observations() == 0  # 无可查行


def test_u10_retention_raw_per_run_terminates(store, monkeypatch):
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    monkeypatch.setattr(_collect_mod, "INTRADAY_MAX_RAW_BYTES_PER_RUN", 1)  # 1 字节即超
    provider = _SessionProvider()
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
    )
    assert summary["status"] == "failed"
    assert summary["failure_reason"] == "retention_limit"
    assert summary["exit_code"] == 1
    assert store.count_observations() == 0  # 整 run 终止，无可查行


# --- RB3 窗内追补：窗内缺失日 → 下一 run 检出并重拉、入新 observation --- #


def test_rb3_in_window_catchup_refetches_missing_day(store):
    ids = _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    # D2 缺失（仅 D1 已采过 complete），D2/D3 在窗内。
    d1, d2, d3 = "2026-06-17", "2026-06-18", "2026-06-19"
    cal = _open_cal(d1, d2, d3)

    # 先采 D1（建立「已 complete」基线）。
    p1 = _SessionProvider(window_days=[d1])
    p1.day_for_call = d1
    collect_close(store, p1, trade_date=d1, calendar=_open_cal(d1),
                  session_profile_version=pv, contract_version=cv,
                  now_ts=f"{d1}T15:05:00+08:00")
    # D2 此时无 complete。
    assert not store.has_complete_assessment(
        instrument_id=ids["688008.SH"], trade_date=d2,
        interval_minutes=1, provider="eastmoney_akshare")

    # 跑 D3 收盘：滚动响应含 D1/D2/D3；窗内追补应重拉 D2（无 complete 的窗内日）。
    p3 = _SessionProvider(window_days=[d1, d2, d3])
    p3.day_for_call = d3  # 当场（source_asof 基准）
    summary = collect_close(
        store, p3, trade_date=d3, calendar=cal,
        session_profile_version=pv, contract_version=cv,
        rolling_window_days=5, now_ts=f"{d3}T15:05:00+08:00",
    )
    assert summary["status"] == "completed"
    # D2 被列入追补日。
    assert d2 in summary["catchup_days"]
    # D2 现在有了 observation（新 revision 入库，非盲覆盖）。
    cov_d2 = store.list_coverage_assessments(trade_date=d2)
    assert any(c["assessment_kind"] == "complete" for c in cov_d2)


# --- M2 外科式：已 complete 日不重做 canonical（零新 revision），仅缺失日产新 revision --- #


def test_m2_catchup_skips_already_complete_days(store):
    ids = _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    d_prev, d_cur = "2026-06-18", "2026-06-19"
    cal = _open_cal(d_prev, d_cur)

    # 先采 d_prev → complete。
    pp = _SessionProvider(window_days=[d_prev])
    pp.day_for_call = d_prev
    collect_close(store, pp, trade_date=d_prev, calendar=_open_cal(d_prev),
                  session_profile_version=pv, contract_version=cv,
                  now_ts=f"{d_prev}T15:05:00+08:00")
    iid = ids["688008.SH"]
    canon_before = len(store.list_canonical_bars(instrument_id=iid))
    assert canon_before == 3

    # 跑 d_cur：d_prev 已 complete，追补应**外科式跳过** d_prev（零取数、零新 revision）。
    pc = _SessionProvider(window_days=[d_prev, d_cur])
    pc.day_for_call = d_cur
    summary = collect_close(
        store, pc, trade_date=d_cur, calendar=cal,
        session_profile_version=pv, contract_version=cv,
        rolling_window_days=5, now_ts=f"{d_cur}T15:05:00+08:00",
    )
    # d_prev 不在追补日（已 complete，外科跳过）。
    assert d_prev not in summary["catchup_days"]
    # d_prev 的 canonical 行数不变（无新 revision），但 d_cur 新增 3 行。
    all_canon = store.list_canonical_bars(instrument_id=iid)
    prev_rows = [c for c in all_canon if c["trade_date"] == d_prev]
    cur_rows = [c for c in all_canon if c["trade_date"] == d_cur]
    assert len(prev_rows) == 3  # 零新 revision（每根仍仅 revision 1）
    assert all(c["revision"] == 1 for c in prev_rows)
    assert len(cur_rows) == 3
    # provider 对 688008 仅取一次（当场 d_cur；追补 d_prev 已 complete 被外科跳过）。
    assert pc.symbol_calls.count("688008") == 1


def test_m2_catchup_does_not_blind_overwrite_complete(store):
    """窗内追补不盲覆盖既有 complete 日（写一次-PIT）：complete 评估 manifest 不变。"""
    ids = _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    d_prev, d_cur = "2026-06-18", "2026-06-19"
    iid = ids["688008.SH"]

    pp = _SessionProvider(window_days=[d_prev])
    pp.day_for_call = d_prev
    collect_close(store, pp, trade_date=d_prev, calendar=_open_cal(d_prev),
                  session_profile_version=pv, contract_version=cv,
                  now_ts=f"{d_prev}T15:05:00+08:00")
    cov_before = store.list_coverage_assessments(instrument_id=iid, trade_date=d_prev)
    sha_before = [c["canonical_manifest_sha256"] for c in cov_before]

    pc = _SessionProvider(window_days=[d_prev, d_cur])
    pc.day_for_call = d_cur
    collect_close(store, pc, trade_date=d_cur, calendar=_open_cal(d_prev, d_cur),
                  session_profile_version=pv, contract_version=cv,
                  rolling_window_days=5, now_ts=f"{d_cur}T15:05:00+08:00")
    cov_after = store.list_coverage_assessments(instrument_id=iid, trade_date=d_prev)
    # d_prev 的 complete 评估未被追加新的（外科跳过 → 行数与 manifest 不变）。
    assert len(cov_after) == len(cov_before)
    assert [c["canonical_manifest_sha256"] for c in cov_after] == sha_before


# --- RB3 窗外 gap：窗外无覆盖交易日 → permanent_gap + 告警 --- #


def test_rb3_out_of_window_permanent_gap_alert(store):
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    # 远早于窗（rolling_window_days=2）的交易日 d_old，无任何覆盖。
    d_old, d_cur = "2026-06-10", "2026-06-19"
    cal = _open_cal(d_old, d_cur)
    provider = _SessionProvider()
    provider.day_for_call = d_cur
    summary = collect_close(
        store, provider, trade_date=d_cur, calendar=cal,
        session_profile_version=pv, contract_version=cv,
        rolling_window_days=2, now_ts=f"{d_cur}T15:05:00+08:00",
    )
    assert d_old in summary["permanent_gaps"]
    assert any(a["alert"] == "permanent_gap" and a["trade_date"] == d_old
               for a in summary["alerts"])


# --- 当场 session-only：不得用滚动响应（别日行）回填先前日 --- #


def test_current_session_only_filters_other_days(store):
    ids = _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    # 响应额外塞入别日（D_other）的行；不得落入当场日的 canonical。
    provider = _SessionProvider(extra_day="2026-06-10")
    provider.day_for_call = _TOY_DAY
    collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
        rolling_window_days=2, now_ts="2026-06-19T15:05:00+08:00",
    )
    iid = ids["688008.SH"]
    canon = store.list_canonical_bars(instrument_id=iid)
    # 仅当场日 3 根；别日行被过滤，不回填先前日。
    assert {c["trade_date"] for c in canon} == {_TOY_DAY}
    assert len(canon) == 3


# --- M1 漂移自报：retrieved_at - bar_end_ts 超 delay → publication_delay_exceeded --- #


def test_m1_publication_delay_exceeded_alert(store):
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    provider = _SessionProvider()
    provider.day_for_call = _TOY_DAY
    # bar_end=15:00；retrieved_at=15:10（600s）> 配置 delay 90s → 告警。
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
        publication_delay_seconds=90, now_ts="2026-06-19T15:10:00+08:00",
    )
    drift_alerts = [a for a in summary["alerts"]
                    if a["alert"] == "publication_delay_exceeded"]
    assert drift_alerts  # fail-loud 自报
    assert drift_alerts[0]["configured_delay_seconds"] == 90
    assert drift_alerts[0]["observed_delay_seconds"] > 90


def test_m1_no_alert_within_delay(store):
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    provider = _SessionProvider()
    provider.day_for_call = _TOY_DAY
    # retrieved_at=15:00:30（30s）< delay 90s → 无告警。
    summary = collect_close(
        store, provider, trade_date=_TOY_DAY, calendar=_open_cal(_TOY_DAY),
        session_profile_version=pv, contract_version=cv,
        publication_delay_seconds=90, now_ts="2026-06-19T15:00:30+08:00",
    )
    assert not [a for a in summary["alerts"]
                if a["alert"] == "publication_delay_exceeded"]


# --- watch 模式：shadow-only，落 observation + 喂 delay 复核，不产 complete --- #


def test_watch_mode_shadow_only(store):
    from scripts.collect_intraday import collect_watch
    _register_three_symbols(store)
    pv, cv = _register_toy_context(store)
    provider = _SessionProvider()
    provider.day_for_call = _TOY_DAY
    summary = collect_watch(
        store, provider, trade_date=_TOY_DAY,
        watchlist=["688008.SH"], interval_minutes=5,
        session_profile_version=pv, contract_version=cv,
        publication_delay_seconds=90, now_ts="2026-06-19T15:10:00+08:00",
    )
    assert summary["mode"] == "watch"
    assert summary["polled_symbols"] == ["688008.SH"]
    assert summary["run_id"] is not None
    # watch 落 observation 但不产 complete 评估（盘中未收盘）。
    assert store.count_observations(summary["run_id"]) == 1
    assert store.list_coverage_assessments() == []
    # 仍喂 publication_delay 复核（M1）。
    assert any(a["alert"] == "publication_delay_exceeded" for a in summary["alerts"])
