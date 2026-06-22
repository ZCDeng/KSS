"""U2 薄收集器单测：fail-closed 零 provider 调用 + 端到端 append.

（U5 会扩展本文件：trade_cal 终止、窗内追补、retention、部分失败。）
所有 db 落 ``tmp_path``，无 live provider 调用（FakeProvider 注入）。
"""

from __future__ import annotations

import pytest

from kss.data.intraday_client import FetchResult
from kss.data.intraday_store import IntradayStore
from scripts.collect_intraday import collect_close


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
    summary = collect_close(store, provider, trade_date="2026-06-19")
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
    summary = collect_close(store, provider, trade_date="2026-06-19")
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
    summary = collect_close(store, provider, trade_date="2026-06-19")
    assert summary["status"] == "failed"
    assert summary["failure_reason"] == "mapping_ambiguous"
    assert summary["exit_code"] == 1
    assert provider.calls == []  # 零调用
    # 留一条终止 failed run，无数据行。
    run = store.get_run(summary["run_id"])
    assert run["status"] == "failed" and run["failure_reason"] == "mapping_ambiguous"
    assert store.count_observations() == 0
