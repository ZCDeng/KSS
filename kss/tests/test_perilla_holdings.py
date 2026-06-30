"""U4: 机构持仓动态计算测试 (纯函数, 无 IO)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.perilla_enrich.holdings import northbound_trend, top10_dynamics


def _df(rows):
    return pd.DataFrame(rows)


def test_top10_classifies_movers() -> None:
    # 最新季 20260331: 一增一减一新进(NaN)
    df = _df([
        {"end_date": "20260331", "holder_name": "A基金", "hold_ratio": 5.0,
         "hold_change": 1.0e6, "holder_type": "基金"},
        {"end_date": "20260331", "holder_name": "B风投", "hold_ratio": 8.0,
         "hold_change": -2.0e6, "holder_type": "风险投资公司"},
        {"end_date": "20260331", "holder_name": "C企业", "hold_ratio": 3.0,
         "hold_change": np.nan, "holder_type": "一般企业"},
        {"end_date": "20251231", "holder_name": "A基金", "hold_ratio": 4.0,
         "hold_change": 5.0e5, "holder_type": "基金"},  # 上一季, 不计入
    ])
    out = top10_dynamics(df)
    assert out["status"] == "ok"
    assert out["latest_period"] == "20260331"
    assert out["n_holders"] == 3
    assert out["n_increasing"] == 1 and out["n_decreasing"] == 1
    assert out["net_direction"] == "flat"
    # 按持股比例降序: B(8) 在最前
    assert out["movers"][0]["name"] == "B风投"


def test_top10_net_increasing() -> None:
    df = _df([
        {"end_date": "20260331", "holder_name": "A", "hold_ratio": 5.0, "hold_change": 1.0, "holder_type": "基金"},
        {"end_date": "20260331", "holder_name": "B", "hold_ratio": 4.0, "hold_change": 2.0, "holder_type": "基金"},
        {"end_date": "20260331", "holder_name": "C", "hold_ratio": 3.0, "hold_change": -1.0, "holder_type": "基金"},
    ])
    assert top10_dynamics(df)["net_direction"] == "increasing"


def test_top10_empty_unavailable() -> None:
    assert top10_dynamics(None)["status"] == "unavailable"
    assert top10_dynamics(pd.DataFrame())["status"] == "unavailable"


def test_northbound_extracted_and_trend() -> None:
    df = _df([
        {"end_date": "20251231", "holder_name": "香港中央结算有限公司", "hold_ratio": 9.0, "hold_change": 0, "holder_type": "一般企业"},
        {"end_date": "20260331", "holder_name": "香港中央结算有限公司", "hold_ratio": 10.5, "hold_change": 1e7, "holder_type": "一般企业"},
        {"end_date": "20260331", "holder_name": "某基金", "hold_ratio": 5.0, "hold_change": 0, "holder_type": "基金"},
    ])
    nb = northbound_trend(df)
    assert nb["status"] == "ok"
    assert nb["hold_ratio"] == 10.5
    assert nb["qoq_change"] == 1.5 and nb["direction"] == "increasing"


def test_northbound_absent() -> None:
    df = _df([{"end_date": "20260331", "holder_name": "某基金", "hold_ratio": 5.0, "hold_change": 0, "holder_type": "基金"}])
    out = northbound_trend(df)
    assert out["status"] == "unavailable" and out["reason"] == "no_northbound_holder"


def test_northbound_single_quarter_no_qoq() -> None:
    df = _df([{"end_date": "20260331", "holder_name": "香港中央结算有限公司", "hold_ratio": 10.5, "hold_change": 0, "holder_type": "一般企业"}])
    nb = northbound_trend(df)
    assert nb["status"] == "ok" and nb["qoq_change"] is None and nb["direction"] == "unknown"
