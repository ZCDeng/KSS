"""factor_cross_section_backtest 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cost_model import CostModel
from kss.backtest.cross_section import factor_cross_section_backtest


def _make_panel(
    n_dates: int = 60, n_stocks: int = 20, signal_strength: float = 0.5, seed: int = 0,
) -> pd.DataFrame:
    """合成横截面面板：factor 与 ret 有正向相关 (signal_strength)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        for s in range(n_stocks):
            f = rng.standard_normal()
            r = signal_strength * f + rng.standard_normal() * 0.5
            rows.append({
                "trade_date": d,
                "symbol": f"S{s:03d}",
                "factor_a": f,
                "next_day_return": r * 0.005,
            })
    return pd.DataFrame(rows)


class TestBasic:

    def test_smoke(self) -> None:
        df = _make_panel()
        cm = CostModel()
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.2)
        assert res is not None
        for col in ["trade_date", "gross_return", "net_return",
                    "turnover", "buy_turnover", "cost", "n_stocks", "n_kept"]:
            assert col in res.columns

    def test_panel_in_attrs(self) -> None:
        df = _make_panel()
        cm = CostModel()
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.2)
        assert res is not None
        panel = res.attrs.get("panel")
        assert panel is not None
        assert {"trade_date", "symbol", "pred_score", "next_day_return"}.issubset(panel.columns)
        assert res.attrs["factor_col"] == "factor_a"
        assert res.attrs["direction"] == "long_high"

    def test_missing_columns_raises(self) -> None:
        df = pd.DataFrame({"trade_date": [], "factor_a": [], "next_day_return": []})
        with pytest.raises(ValueError, match="缺少必需列"):
            factor_cross_section_backtest(df, "factor_a", CostModel())

    def test_invalid_top_pct_raises(self) -> None:
        df = _make_panel()
        with pytest.raises(ValueError, match="top_pct"):
            factor_cross_section_backtest(df, "factor_a", CostModel(), top_pct=1.5)


class TestFirstDayNoSellCost:

    def test_first_row_no_sell(self) -> None:
        df = _make_panel()
        cm = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.2)
        assert res is not None
        # 首日：sell_turnover=0，buy_turnover=1
        assert res["turnover"].iloc[0] == pytest.approx(0.0)
        assert res["buy_turnover"].iloc[0] == pytest.approx(1.0)
        # 首日 cost = buy_cost
        assert res["cost"].iloc[0] == pytest.approx(0.001)


class TestDirection:

    def test_long_high_picks_top_factor_values(self) -> None:
        """long_high 时选高分股票 → 当 signal_strength 大，毛收益应显著为正."""
        df = _make_panel(signal_strength=0.9, seed=1)
        cm = CostModel()
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.2,
                                            direction="long_high")
        assert res is not None
        assert res["gross_return"].mean() > 0

    def test_long_low_inverts_signal(self) -> None:
        """long_low 在同样正向 signal 数据上应产生负收益（反向使用）."""
        df = _make_panel(signal_strength=0.9, seed=2)
        cm = CostModel()
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.2,
                                            direction="long_low")
        assert res is not None
        # 反向使用应产生显著负收益
        assert res["gross_return"].mean() < 0


class TestSkipsLowCoverageDays:

    def test_skips_days_below_min_stocks(self) -> None:
        df = _make_panel(n_dates=10, n_stocks=5)
        cm = CostModel()
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.2,
                                            min_stocks=20)
        # 每天 5 < 20 都被跳过
        assert res is None


class TestNetReturnInvariant:

    def test_net_equals_gross_minus_cost(self) -> None:
        df = _make_panel()
        cm = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=5)
        res = factor_cross_section_backtest(df, "factor_a", cm, top_pct=0.3)
        assert res is not None
        diff = (res["net_return"] - (res["gross_return"] - res["cost"])).abs()
        assert (diff < 1e-12).all()


class TestSlippageAppliesToCost:

    def test_slippage_increases_cost(self) -> None:
        df = _make_panel()
        cm_zero = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)
        cm_high = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=20)
        r0 = factor_cross_section_backtest(df, "factor_a", cm_zero, top_pct=0.3)
        r1 = factor_cross_section_backtest(df, "factor_a", cm_high, top_pct=0.3)
        # 滑点越高总成本越高
        assert r1["cost"].sum() > r0["cost"].sum()
