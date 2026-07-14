"""U2 单测：Longbridge 覆盖 manifest + 确定性路由（R3 / KTD5）.

约束：``route_provider`` 是确定性纯函数，喂固定 manifest 不打网；覆盖探针核心
``scan_coverage`` 用 fake provider 注入（绝不 live）。
"""

from __future__ import annotations

import json

from kss.data.intraday_client import FetchResult
from kss.data.longbridge_coverage import (
    PROVIDER_EASTMONEY,
    PROVIDER_LONGBRIDGE,
    CoverageManifest,
    is_beijing_exchange,
    is_manifest_stale,
    load_manifest,
    normalize_symbol,
    route_provider,
)
from scripts.probe_longbridge_coverage import scan_coverage


def _manifest(covered=(), route_em=(), scanned_at="2026-07-08T00:00:00+08:00"):
    return CoverageManifest(
        covered=frozenset(normalize_symbol(s) for s in covered),
        route_to_eastmoney=frozenset(normalize_symbol(s) for s in route_em),
        scanned_at=scanned_at,
    )


# --------------------------------------------------------------------------- #
# normalize_symbol / 北交所识别
# --------------------------------------------------------------------------- #


def test_normalize_symbol_infers_exchange():
    assert normalize_symbol("688008") == "688008.SH"
    assert normalize_symbol("300750") == "300750.SZ"
    assert normalize_symbol("830799") == "830799.BJ"
    assert normalize_symbol("600519.sh") == "600519.SH"  # 大写规范


def test_is_beijing_exchange():
    assert is_beijing_exchange("830799.BJ") is True
    assert is_beijing_exchange("830799") is True  # 8 开头裸码
    assert is_beijing_exchange("688008.SH") is False


# --------------------------------------------------------------------------- #
# route_provider（确定性纯函数，喂固定 manifest）
# --------------------------------------------------------------------------- #


def test_route_beijing_always_eastmoney():
    """北交所静态规则：即便误入 covered 也恒归东财。"""
    m = _manifest(covered=("830799.BJ",))
    assert route_provider("830799.BJ", m) == PROVIDER_EASTMONEY


def test_route_covered_symbol_goes_longbridge():
    m = _manifest(covered=("688008.SH",))
    assert route_provider("688008.SH", m) == PROVIDER_LONGBRIDGE
    assert route_provider("688008", m) == PROVIDER_LONGBRIDGE  # 裸码归一后命中


def test_route_unscanned_symbol_defaults_to_longbridge():
    """manifest 未扫标的 → 默认长桥（2026-07 中旬改向：东财已实证不可达，
    manifest.covered 是已扫精确图而非全池否定；显式 route_to_eastmoney 才回东财）。"""
    m = _manifest(covered=("688008.SH",))
    assert route_provider("300999.SZ", m) == PROVIDER_LONGBRIDGE


def test_route_missing_manifest_defaults_to_longbridge(tmp_path):
    """manifest 文件缺失 → 空 manifest，非北交所标的仍默认长桥（同上改向）；
    北交所静态规则不受影响。"""
    missing = tmp_path / "nope.json"
    m = load_manifest(missing, use_cache=False)
    assert m.covered == frozenset()
    assert route_provider("688008.SH", m) == PROVIDER_LONGBRIDGE
    assert route_provider("899050.BJ", m) == PROVIDER_EASTMONEY


# --------------------------------------------------------------------------- #
# 陈旧检测（季度调整）
# --------------------------------------------------------------------------- #


def test_manifest_stale_when_never_scanned():
    m = CoverageManifest(frozenset(), frozenset(), scanned_at=None)
    assert is_manifest_stale(m) is True


def test_manifest_stale_after_rescan_interval():
    m = _manifest(covered=("688008.SH",), scanned_at="2026-01-01T00:00:00+08:00")
    # 距 2026-07-08 已 >90 天 → 陈旧。
    assert is_manifest_stale(m, now_iso="2026-07-08T00:00:00+08:00") is True


def test_manifest_fresh_within_interval():
    m = _manifest(covered=("688008.SH",), scanned_at="2026-07-01T00:00:00+08:00")
    assert is_manifest_stale(m, now_iso="2026-07-08T00:00:00+08:00") is False


# --------------------------------------------------------------------------- #
# 打包 manifest（种子集）加载
# --------------------------------------------------------------------------- #


def test_shipped_manifest_loads_and_routes_seed_symbols():
    """随代码打包的种子 manifest 能加载，且实测 covered 标的路由 longbridge。"""
    m = load_manifest(use_cache=False)
    assert m.scanned_at is not None
    # 种子集里的实测标的走 longbridge。
    assert route_provider("688008.SH", m) == PROVIDER_LONGBRIDGE
    assert route_provider("300750.SZ", m) == PROVIDER_LONGBRIDGE
    # 北交所仍回东财。
    assert route_provider("830799.BJ", m) == PROVIDER_EASTMONEY


# --------------------------------------------------------------------------- #
# scan_coverage 探针核心（fake provider 注入，绝不 live）
# --------------------------------------------------------------------------- #


class _FakeQuoteProvider:
    """按 symbol 返回预设 ok/err 的 fake（覆盖探针用）。"""

    name = "longbridge"

    def __init__(self, ok_symbols):
        self._ok = {normalize_symbol(s) for s in ok_symbols}

    def fetch_quote(self, symbol):
        norm = normalize_symbol(symbol)
        if norm in self._ok:
            return FetchResult(
                rows=[{"symbol": norm, "last_done": 10.0}],
                raw_columns=("symbol", "last_done"),
                source_asof_ts="2026-07-08T15:00:00+08:00",
                status_code=200, latency_ms=10.0, error=None,
            )
        return FetchResult(
            rows=[], raw_columns=(), source_asof_ts=None,
            status_code=200, latency_ms=5.0, error="empty response",
        )


def test_scan_coverage_splits_covered_and_routed():
    provider = _FakeQuoteProvider(ok_symbols=("688008.SH", "300750.SZ"))
    symbols = ["688008.SH", "300750.SZ", "300999.SZ", "830799.BJ"]
    manifest = scan_coverage(provider, symbols, scanned_at="2026-07-08T15:00:00+08:00")
    assert manifest["covered"] == ["300750.SZ", "688008.SH"]  # 排序
    # 非覆盖 + 北交所都进 route_to_eastmoney。
    assert "300999.SZ" in manifest["route_to_eastmoney"]
    assert "830799.BJ" in manifest["route_to_eastmoney"]
    assert manifest["scanned_at"] == "2026-07-08T15:00:00+08:00"


def test_scan_coverage_beijing_not_probed():
    """北交所标的不调 provider（静态归东财）—— fake 无 .BJ 也进 route_em。"""
    provider = _FakeQuoteProvider(ok_symbols=())
    manifest = scan_coverage(provider, ["830799.BJ"], scanned_at="2026-07-08T00:00:00+08:00")
    assert manifest["route_to_eastmoney"] == ["830799.BJ"]


def test_scan_coverage_manifest_has_no_credentials(monkeypatch):
    """manifest 产物绝不含凭据子串（即便 env 里设了）。"""
    sentinel = "LONGBRIDGE_TOKEN_deadbeef1234"
    monkeypatch.setenv("LONGBRIDGE_ACCESS_TOKEN", sentinel)
    provider = _FakeQuoteProvider(ok_symbols=("688008.SH",))
    manifest = scan_coverage(provider, ["688008.SH"], scanned_at="2026-07-08T00:00:00+08:00")
    assert sentinel not in json.dumps(manifest, ensure_ascii=False)
