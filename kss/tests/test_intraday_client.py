"""U1 单测：IntradayProvider 协议 + 东财适配 + 能力门控 + 探针报告.

约束（test-spec / plan U1）：
- **绝不 live 调用**：EM 适配器测试用 monkeypatch 打桩 akshare 函数；
  探针测试用 FakeProvider 注入。
- 覆盖 test-spec U8（能力门控）+ Provider admission（报告结构 + token 不泄露）。
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from kss.data.intraday_client import (
    DEFAULT_PROBE_UNIVERSE,
    EASTMOTNEY_1M_MAX_HISTORY_DAYS,
    EastmoneyAkshareProvider,
    Eligibility,
    FetchResult,
    RepresentativeSymbol,
    classify_eligibility,
    schema_hash,
)
from scripts.probe_intraday_provider import run_probe

_SHANGHAI = ZoneInfo("Asia/Shanghai")


# --------------------------------------------------------------------------- #
# 能力门控（test-spec U8：失败 → research_only/failed，绝不 pit_backtest_eligible）
# --------------------------------------------------------------------------- #


def test_forward_only_provider_never_pit_even_with_all_evidence():
    """前向源（东财/akshare）结构上永不 PIT——即便误传全证据为 True。"""
    elig = classify_eligibility(
        "eastmoney_akshare",
        reachable=True,
        has_entitlement=True,
        requested_history_ok=True,
        correction_policy_known=True,
        has_frozen_manifest_evidence=True,
        has_availability_proxy=True,
    )
    assert elig is Eligibility.FORWARD_OBSERVED
    assert elig is not Eligibility.PIT_BACKTEST_ELIGIBLE


def test_unreachable_provider_is_failed():
    """error/权限/覆盖 stub 失败（不可达）→ failed，绝不 pit。"""
    assert (
        classify_eligibility("eastmoney_akshare", reachable=False)
        is Eligibility.FAILED
    )
    assert (
        classify_eligibility("tushare", reachable=False) is Eligibility.FAILED
    )


def test_tushare_historical_requires_all_evidence_for_pit():
    """历史路径：全证据齐才 pit；任一未知 → research_only（影子可续）。"""
    full = classify_eligibility(
        "tushare",
        reachable=True,
        has_entitlement=True,
        requested_history_ok=True,
        correction_policy_known=True,
        has_frozen_manifest_evidence=True,
        has_availability_proxy=True,
    )
    assert full is Eligibility.PIT_BACKTEST_ELIGIBLE

    for missing in (
        "has_entitlement",
        "requested_history_ok",
        "correction_policy_known",
        "has_frozen_manifest_evidence",
        "has_availability_proxy",
    ):
        kwargs = dict(
            has_entitlement=True,
            requested_history_ok=True,
            correction_policy_known=True,
            has_frozen_manifest_evidence=True,
            has_availability_proxy=True,
        )
        kwargs[missing] = None
        assert (
            classify_eligibility("tushare", reachable=True, **kwargs)
            is Eligibility.RESEARCH_ONLY
        ), f"{missing} 未知却被放行 PIT"


def test_eastmoney_capability_is_forward_only_with_5day_limit():
    """东财 capability：forward_observed（或缺包 failed），1m 限 5 日，永不 pit。"""
    cap = EastmoneyAkshareProvider().capability()
    assert cap.eligibility in (Eligibility.FORWARD_OBSERVED, Eligibility.FAILED)
    assert cap.eligibility is not Eligibility.PIT_BACKTEST_ELIGIBLE
    assert cap.max_history_days == EASTMOTNEY_1M_MAX_HISTORY_DAYS
    assert cap.supported_intervals == (1, 5, 15, 30, 60)


# --------------------------------------------------------------------------- #
# 东财适配 fetch_bars（数据层契约：永不抛；mock akshare，不 live）
# --------------------------------------------------------------------------- #


def _fake_em_1m_df() -> pd.DataFrame:
    """模拟东财 1m 响应（period='1' 的 8 列形态）。"""
    return pd.DataFrame(
        {
            "时间": ["2026-06-19 14:58:00", "2026-06-19 14:59:00", "2026-06-19 15:00:00"],
            "开盘": [10.1, 10.2, 10.3],
            "收盘": [10.2, 10.3, 10.4],
            "最高": [10.3, 10.4, 10.5],
            "最低": [10.0, 10.1, 10.2],
            "成交量": [1000, 0, 1500],
            "成交额": [1_020_000.0, 0.0, 1_545_000.0],
            "均价": [10.15, 10.25, 10.35],
        }
    )


def test_fetch_bars_parses_rows_schema_and_source_asof(monkeypatch):
    import akshare

    monkeypatch.setattr(
        akshare, "stock_zh_a_hist_min_em", lambda **kw: _fake_em_1m_df()
    )
    res = EastmoneyAkshareProvider().fetch_bars(
        "688008", interval_minutes=1, asset_kind="stock"
    )
    assert res.ok
    assert res.status_code == 200
    assert len(res.rows) == 3
    assert "时间" in res.raw_columns and "开盘" in res.raw_columns
    # source_asof_ts = 最晚 bar，定位到 Asia/Shanghai。
    assert res.source_asof_ts == "2026-06-19T15:00:00+08:00"
    assert res.error is None


def test_fetch_bars_never_raises_on_provider_exception(monkeypatch):
    """akshare 抛异常 → 吞为 error（数据层不抛）；status None。"""
    import akshare

    def _boom(**kw):
        raise RuntimeError("HTTP 429 Too Many Requests")

    monkeypatch.setattr(akshare, "stock_zh_a_hist_min_em", _boom)
    res = EastmoneyAkshareProvider().fetch_bars(
        "688008", interval_minutes=1, asset_kind="stock"
    )
    assert res.rows == []
    assert res.error is not None and "429" in res.error
    assert res.status_code is None


def test_fetch_bars_unsupported_asset_kind_returns_error_not_raise():
    res = EastmoneyAkshareProvider().fetch_bars(
        "688008", interval_minutes=1, asset_kind="future"
    )
    assert res.rows == []
    assert "unsupported asset_kind" in (res.error or "")


def test_fetch_bars_empty_response(monkeypatch):
    import akshare

    monkeypatch.setattr(
        akshare, "stock_zh_a_hist_min_em", lambda **kw: pd.DataFrame()
    )
    res = EastmoneyAkshareProvider().fetch_bars(
        "688008", interval_minutes=1, asset_kind="stock"
    )
    assert res.rows == []
    assert res.error == "empty response"
    assert res.status_code == 200


# --------------------------------------------------------------------------- #
# schema_hash
# --------------------------------------------------------------------------- #


def test_schema_hash_order_independent_and_stable():
    a = schema_hash(("时间", "开盘", "收盘"))
    b = schema_hash(("收盘", "时间", "开盘"))
    assert a == b
    assert a != schema_hash(("时间", "开盘"))  # 列集合不同 → hash 不同


# --------------------------------------------------------------------------- #
# 探针报告（admission：结构完整 + token 不泄露 + delay 可导出）
# --------------------------------------------------------------------------- #


class _FakeProvider:
    """注入式 fake provider，按 symbol 返回预设结果（绝不 live）。"""

    name = "eastmoney_akshare"
    version = "akshare-fake"

    def __init__(self, results: dict[str, FetchResult]):
        self._results = results

    def supported_intervals(self):
        return (1, 5, 15, 30, 60)

    def supported_assets(self):
        return ("stock", "etf", "index")

    def fetch_bars(self, symbol, *, interval_minutes, asset_kind, start=None, end=None):
        return self._results[symbol]

    def capability(self):
        from kss.data.intraday_client import CapabilityResult

        return CapabilityResult(
            provider=self.name,
            version=self.version,
            supported_intervals=self.supported_intervals(),
            supported_assets=self.supported_assets(),
            max_history_days=EASTMOTNEY_1M_MAX_HISTORY_DAYS,
            eligibility=Eligibility.FORWARD_OBSERVED,
            reachable=True,
            notes=("前向-only",),
        )


def _ok_result(latest_iso: str) -> FetchResult:
    rows = [
        {
            "时间": "2026-06-19 14:59:00",
            "开盘": 10.0, "收盘": 10.1, "最高": 10.2, "最低": 9.9,
            "成交量": 1000, "成交额": 1_000_000.0, "均价": 10.05,
        },
        {
            "时间": latest_iso.replace("T", " ")[:19],
            "开盘": 10.1, "收盘": 10.2, "最高": 10.3, "最低": 10.0,
            "成交量": 0, "成交额": 0.0, "均价": 10.15,
        },
    ]
    return FetchResult(
        rows=rows,
        raw_columns=("时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "均价"),
        source_asof_ts=latest_iso,
        status_code=200,
        latency_ms=42.0,
        error=None,
    )


def _err_result() -> FetchResult:
    return FetchResult(
        rows=[],
        raw_columns=(),
        source_asof_ts=None,
        status_code=None,
        latency_ms=5.0,
        error="RuntimeError: HTTP 503 Service Unavailable",
    )


def _build_full_report(probe_now: datetime):
    latest_iso = "2026-06-19T15:00:00+08:00"
    results = {}
    for i, sym in enumerate(DEFAULT_PROBE_UNIVERSE):
        # 让一个标的失败（503），其余成功，覆盖 ok/error 两类 outcome。
        results[sym.symbol] = _err_result() if i == 0 else _ok_result(latest_iso)
    provider = _FakeProvider(results)
    return run_probe(provider, probe_now=probe_now)


def test_probe_report_contains_required_admission_fields():
    probe_now = datetime(2026, 6, 19, 15, 5, 0, tzinfo=_SHANGHAI)
    report = _build_full_report(probe_now)

    # provider identity
    assert report["provider"]["name"] == "eastmoney_akshare"
    assert report["provider"]["version"] == "akshare-fake"
    # 10 标的逐一结果
    assert len(report["symbols"]) == len(DEFAULT_PROBE_UNIVERSE) == 10
    # 源区间 / minutes / 状态码 / schema hash / eligibility 齐
    for s in report["symbols"]:
        assert s["interval_minutes"] == 1
        assert "source_latest_ts" in s and "source_earliest_ts" in s
        assert "status_code" in s
        assert isinstance(s["schema_hash"], str) and len(s["schema_hash"]) == 64
        assert s["outcome"] in ("ok", "error", "empty")
    # quota/errors 聚合（503 计入 5xx）
    assert report["aggregate"]["http_5xx_count"] >= 1
    # provider 级 eligibility：有标的成功 → forward_observed，绝不 pit
    assert report["eligibility"] == Eligibility.FORWARD_OBSERVED.value
    assert report["eligibility"] != Eligibility.PIT_BACKTEST_ELIGIBLE.value


def test_probe_report_publication_delay_recorded_and_exportable():
    """publication delay 分布被记录且可导出（喂 RB2 取 p95+裕度）。"""
    probe_now = datetime(2026, 6, 19, 15, 5, 0, tzinfo=_SHANGHAI)
    report = _build_full_report(probe_now)
    agg = report["aggregate"]
    # 成功标的 latest=15:00，probe_now=15:05 → delay=300s。
    assert 300.0 in agg["publication_delay_samples"]
    assert agg["publication_delay_p95_seconds"] == 300.0
    # 可导出：samples 是普通 list[float]，json 可序列化。
    json.dumps(agg["publication_delay_samples"])


def test_probe_report_tushare_historical_never_pit_without_evidence():
    probe_now = datetime(2026, 6, 19, 15, 5, 0, tzinfo=_SHANGHAI)
    report = _build_full_report(probe_now)
    th = report["tushare_historical"]
    assert th["eligibility"] == Eligibility.RESEARCH_ONLY.value
    assert th["eligibility"] != Eligibility.PIT_BACKTEST_ELIGIBLE.value


def test_probe_report_never_contains_token_substring(monkeypatch):
    """admission：报告机读串绝不含 token 子串（即便环境里设了 token）。"""
    sentinel = "TOKENSENTINEL_deadbeefcafe1234567890"
    monkeypatch.setenv("TUSHARE_TOKEN", sentinel)
    probe_now = datetime(2026, 6, 19, 15, 5, 0, tzinfo=_SHANGHAI)
    report = _build_full_report(probe_now)
    serialized = json.dumps(report, ensure_ascii=False)
    assert sentinel not in serialized


def test_probe_report_all_error_marks_failed():
    """所有标的失败 → provider 级 failed（reachable=False），绝不 pit。"""
    results = {s.symbol: _err_result() for s in DEFAULT_PROBE_UNIVERSE}
    report = run_probe(
        _FakeProvider(results),
        probe_now=datetime(2026, 6, 19, 15, 5, 0, tzinfo=_SHANGHAI),
    )
    assert report["reachable"] is False
    assert report["eligibility"] == Eligibility.FAILED.value


def test_default_universe_includes_thin_symbol():
    """A6：探针 universe 含至少一个稀薄标的，避免 delay/零成交模式失真。"""
    assert any(s.liquidity_tier == "thin" for s in DEFAULT_PROBE_UNIVERSE)
    # 含科创/创业个股 + ETF + 指数四类。
    kinds = {s.asset_kind for s in DEFAULT_PROBE_UNIVERSE}
    assert {"stock", "etf", "index"} <= kinds
