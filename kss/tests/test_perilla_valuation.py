"""U4: PE 分位计算测试 (纯函数)."""

from __future__ import annotations

import pandas as pd

from kss.perilla_enrich.valuation import pe_dynamics, pe_percentile


def test_pe_percentile_basic() -> None:
    # 100 个有效正值 1..100, 现值 75 → 1..74 低于它 = 0.74
    series = list(range(1, 101))
    assert pe_percentile(series, 75) == 0.74


def test_pe_percentile_too_few_points() -> None:
    assert pe_percentile([10, 20, 30], 25) is None  # < 30 点


def test_pe_percentile_nonpositive_now() -> None:
    assert pe_percentile(list(range(1, 60)), 0) is None
    assert pe_percentile(list(range(1, 60)), -5) is None


def test_pe_percentile_filters_invalid() -> None:
    # 含 None/负/NaN, 有效正值仍 >=30 则计算
    series = [float("nan"), -1, None] + list(range(1, 50))
    assert pe_percentile(series, 25) is not None


def test_pe_dynamics_happy() -> None:
    df = pd.DataFrame({
        "trade_date": [f"2026{m:02d}01" for m in range(1, 13)] * 3,
        "pe_ttm": [float(x) for x in range(36)],
    })
    out = pe_dynamics(df)
    assert out["status"] == "ok"
    assert out["n_points"] == 36
    assert out["percentile"] is not None
    assert out["pe_ttm"] == 35.0  # 排序后最后一个 trade_date 的 pe


def test_pe_dynamics_empty() -> None:
    assert pe_dynamics(None)["status"] == "unavailable"
    assert pe_dynamics(pd.DataFrame())["status"] == "unavailable"
