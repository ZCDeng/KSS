"""kss/data/macro_client.py + kss/macro/* 单元测试.

覆盖：
- ``MacroClient`` 各 fetch_* 方法的成功路径（mock Tushare pro）
- ``MacroClient`` AkShare 兜底（未安装 / 安装时）
- ``pivot_yield_curve`` 长 → 宽 + 列命名
- ``compute_rate_changes`` 窗口对齐 + 单位
- ``yield_curve_slope`` 斜率计算 + 缺列降级
- ``load_macro_snapshot`` 整体拼装 + 部分失败降级
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def mock_pro() -> MagicMock:
    """构造一个 ``pro.query`` 的 mock，可按 api_name 路由不同响应."""
    pro = MagicMock()
    pro.query = MagicMock()
    return pro


@pytest.fixture
def make_yc_long() -> object:
    """工厂：构造 yc_cb 长表样本（含两个 curve_type, 两个 trade_date）."""

    def _make(curve_type: str = "1") -> pd.DataFrame:
        rows = []
        for date in ("20240104", "20240105"):
            for term in (0.25, 1.0, 5.0, 10.0, 30.0):
                rows.append(
                    {
                        "trade_date": date,
                        "ts_code": "1001.CB",
                        "curve_name": "中债国债收益率曲线",
                        "curve_type": curve_type,
                        "curve_term": term,
                        "yield": 2.0 + term * 0.05,
                    }
                )
            # 额外一条 curve_type='0'（即期），应被过滤
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": "1001.CB",
                    "curve_name": "中债国债收益率曲线",
                    "curve_type": "0",
                    "curve_term": 10.0,
                    "yield": 1.99,
                }
            )
        return pd.DataFrame(rows)

    return _make


# ----------------------------------------------------------------------
# MacroClient
# ----------------------------------------------------------------------


def test_fetch_shibor_success(monkeypatch, mock_pro):
    from kss.data import macro_client

    mock_pro.query.return_value = pd.DataFrame(
        {
            "date": ["20240105"],
            "on": [1.75],
            "1w": [1.80],
            "1m": [2.10],
            "3m": [2.30],
        }
    )

    macro_client.MacroClient._instance = None
    client = macro_client.MacroClient()
    client._pro = mock_pro

    df = client.fetch_shibor("20240101", "20240105")
    assert df is not None and len(df) == 1
    mock_pro.query.assert_called_with("shibor", start_date="20240101", end_date="20240105")


def test_fetch_cn_yield_curve_filters_by_type(monkeypatch, mock_pro, make_yc_long):
    from kss.data import macro_client

    long_df = make_yc_long(curve_type="1")
    # 注入两类 curve_type 后看是否只留 '1'
    mock_pro.query.return_value = long_df

    macro_client.MacroClient._instance = None
    client = macro_client.MacroClient()
    client._pro = mock_pro

    out = client.fetch_cn_yield_curve("20240101", "20240105", curve_type="1")
    assert out is not None
    assert (out["curve_type"] == "1").all()
    # 原表每日 6 行（5 个 type='1' + 1 个 type='0'），过滤后每日 5 行
    assert len(out) == 10


def test_fetch_cn_yield_curve_empty_returns_none(monkeypatch, mock_pro):
    from kss.data import macro_client

    mock_pro.query.return_value = pd.DataFrame()
    macro_client.MacroClient._instance = None
    client = macro_client.MacroClient()
    client._pro = mock_pro

    assert client.fetch_cn_yield_curve("20240101", "20240105") is None


def test_fetch_cn_money_supply_passes_yyyymm(monkeypatch, mock_pro):
    from kss.data import macro_client

    mock_pro.query.return_value = pd.DataFrame({"month": ["202404"], "m2_yoy": [7.2]})
    macro_client.MacroClient._instance = None
    client = macro_client.MacroClient()
    client._pro = mock_pro

    client.fetch_cn_money_supply("202401", "202404")
    mock_pro.query.assert_called_with("cn_m", start_m="202401", end_m="202404")


def test_fetch_credit_yield_curve_no_akshare(monkeypatch):
    from kss.data import macro_client

    # 模拟 akshare 未安装：通过 sys.modules 注入 ImportError
    monkeypatch.setitem(sys.modules, "akshare", None)
    macro_client.MacroClient._instance = None
    client = macro_client.MacroClient()

    assert client.fetch_credit_yield_curve_akshare() is None


def test_fetch_credit_yield_curve_with_akshare(monkeypatch):
    from kss.data import macro_client

    fake = types.ModuleType("akshare")
    fake.bond_china_yield = lambda: pd.DataFrame(  # type: ignore[attr-defined]
        {"曲线名称": ["中债国债"], "10年": [2.50]}
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    macro_client.MacroClient._instance = None
    client = macro_client.MacroClient()

    out = client.fetch_credit_yield_curve_akshare()
    assert out is not None and len(out) == 1


# ----------------------------------------------------------------------
# pivot_yield_curve
# ----------------------------------------------------------------------


def test_pivot_yield_curve_wide_columns(make_yc_long):
    from kss.data.macro_client import pivot_yield_curve

    long_df = make_yc_long(curve_type="1")
    long_df = long_df[long_df["curve_type"] == "1"].reset_index(drop=True)

    wide = pivot_yield_curve(long_df)
    assert wide is not None
    # 期望列：trade_date + yld_3m / yld_1y / yld_5y / yld_10y / yld_30y
    assert set(wide.columns) >= {"trade_date", "yld_3m", "yld_1y", "yld_5y", "yld_10y", "yld_30y"}
    # 两个交易日
    assert len(wide) == 2


def test_pivot_yield_curve_missing_columns():
    from kss.data.macro_client import pivot_yield_curve

    bad = pd.DataFrame({"trade_date": ["20240101"], "yield": [2.5]})  # 缺 curve_term
    assert pivot_yield_curve(bad) is None


def test_pivot_yield_curve_empty():
    from kss.data.macro_client import pivot_yield_curve

    assert pivot_yield_curve(pd.DataFrame()) is None
    assert pivot_yield_curve(None) is None


# ----------------------------------------------------------------------
# Derived metrics
# ----------------------------------------------------------------------


def test_compute_rate_changes_absolute_diff():
    from kss.macro.derived import compute_rate_changes

    panel = pd.DataFrame(
        {
            "trade_date": ["20240101", "20240102", "20240103", "20240104", "20240105", "20240108"],
            "yld_10y": [2.50, 2.55, 2.60, 2.65, 2.70, 2.80],
        }
    )
    out = compute_rate_changes(panel, ["yld_10y"], windows=(2, 5))
    # d2: index 2 - index 0 = 2.60 - 2.50 = 0.10
    assert out.loc[2, "yld_10y_d2"] == pytest.approx(0.10)
    # d5: index 5 - index 0 = 2.80 - 2.50 = 0.30
    assert out.loc[5, "yld_10y_d5"] == pytest.approx(0.30)
    # 前 N 行应为 NaN
    assert pd.isna(out.loc[0, "yld_10y_d5"])


def test_compute_rate_changes_missing_col_silently_skips():
    from kss.macro.derived import compute_rate_changes

    panel = pd.DataFrame({"trade_date": ["20240101"], "yld_10y": [2.5]})
    out = compute_rate_changes(panel, ["yld_10y", "nonexistent"], windows=(1,))
    assert "yld_10y_d1" in out.columns
    assert "nonexistent_d1" not in out.columns


def test_yield_curve_slope_long_minus_short():
    from kss.data.macro_client import pivot_yield_curve
    from kss.macro.derived import yield_curve_slope

    long_df = pd.DataFrame(
        {
            "trade_date": ["20240101", "20240101", "20240102", "20240102"],
            "curve_term": [1.0, 10.0, 1.0, 10.0],
            "yield": [2.0, 2.5, 2.1, 2.4],
        }
    )
    wide = pivot_yield_curve(long_df, key_terms=(1.0, 10.0))
    slope = yield_curve_slope(wide)
    assert slope is not None
    assert slope.loc["20240101"] == pytest.approx(0.5)
    assert slope.loc["20240102"] == pytest.approx(0.3)


def test_yield_curve_slope_missing_col_returns_none():
    from kss.macro.derived import yield_curve_slope

    wide = pd.DataFrame({"trade_date": ["20240101"], "yld_5y": [2.3]})
    assert yield_curve_slope(wide) is None


# ----------------------------------------------------------------------
# Snapshot integration
# ----------------------------------------------------------------------


def test_load_macro_snapshot_all_success(monkeypatch, make_yc_long):
    """所有源都返回，missing 应为空."""
    from kss.macro.snapshot import load_macro_snapshot

    long_df = make_yc_long(curve_type="1")
    long_df = long_df[long_df["curve_type"] == "1"].reset_index(drop=True)

    client = MagicMock()
    client.fetch_shibor.return_value = pd.DataFrame({"date": ["20240105"], "on": [1.8]})
    client.fetch_cn_yield_curve.return_value = long_df
    client.fetch_cn_money_supply.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_cn_cpi.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_cn_ppi.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_credit_yield_curve_akshare.return_value = pd.DataFrame({"x": [1]})

    snap = load_macro_snapshot("20240505", client=client)
    assert snap.missing == []
    assert snap.shibor is not None
    assert snap.yield_curve_wide is not None
    assert "yld_10y" in snap.yield_curve_wide.columns


def test_load_macro_snapshot_partial_failure_no_throw(monkeypatch):
    """部分字段返回 None，整体仍然成功，missing 收集失败列表."""
    from kss.macro.snapshot import load_macro_snapshot

    client = MagicMock()
    client.fetch_shibor.return_value = None
    client.fetch_cn_yield_curve.return_value = None
    client.fetch_cn_money_supply.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_cn_cpi.return_value = None
    client.fetch_cn_ppi.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_credit_yield_curve_akshare.return_value = None

    snap = load_macro_snapshot("20240505", client=client)
    assert "shibor" in snap.missing
    assert "yield_curve" in snap.missing
    assert "cpi" in snap.missing
    assert "credit_curve" in snap.missing
    # 未失败的字段仍然填充
    assert snap.money_supply is not None
    assert snap.ppi is not None


def test_load_macro_snapshot_skip_credit(monkeypatch):
    from kss.macro.snapshot import load_macro_snapshot

    client = MagicMock()
    client.fetch_shibor.return_value = pd.DataFrame({"date": ["20240505"]})
    client.fetch_cn_yield_curve.return_value = None
    client.fetch_cn_money_supply.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_cn_cpi.return_value = pd.DataFrame({"month": ["202405"]})
    client.fetch_cn_ppi.return_value = pd.DataFrame({"month": ["202405"]})

    snap = load_macro_snapshot("20240505", include_credit=False, client=client)
    client.fetch_credit_yield_curve_akshare.assert_not_called()
    assert snap.credit_curve is None
    # 未拉取 ≠ 失败
    assert "credit_curve" not in snap.missing


def test_load_macro_snapshot_month_window_crosses_year(monkeypatch):
    """trade_date=20240105，lookback_months=6 → start_m=202307."""
    from kss.macro.snapshot import load_macro_snapshot

    client = MagicMock()
    client.fetch_shibor.return_value = None
    client.fetch_cn_yield_curve.return_value = None
    client.fetch_cn_money_supply.return_value = None
    client.fetch_cn_cpi.return_value = None
    client.fetch_cn_ppi.return_value = None
    client.fetch_credit_yield_curve_akshare.return_value = None

    load_macro_snapshot("20240105", lookback_months=6, include_credit=False, client=client)
    # 验证 cn_m 被调用时跨年窗口正确
    call_args = client.fetch_cn_money_supply.call_args
    start_m, end_m = call_args.args
    assert start_m == "202307"
    assert end_m == "202401"
