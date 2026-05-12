"""Robustness 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kss.backtest.cost_model import CostModel
from kss.backtest.robustness import Robustness


def _make_factor_df(n_dates: int = 60, n_stocks: int = 10, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        for s in range(n_stocks):
            rows.append({
                "trade_date": d, "symbol": f"S{s}",
                "feat_1": rng.standard_normal(), "feat_2": rng.standard_normal(),
                "future_return_5d": rng.standard_normal() * 0.02,
                "next_day_return": rng.standard_normal() * 0.01,
            })
    return pd.DataFrame(rows)


class TestCostSensitivity:

    def test_grid_shape(self) -> None:
        df = _make_factor_df(n_dates=50, n_stocks=10)
        grid = {"buy_cost": [0.001, 0.002], "sell_cost": [0.002], "slippage_bps": [0, 10]}
        out = Robustness.cost_sensitivity(
            df, ["feat_1", "feat_2"], "future_return_5d",
            grid=grid,
            engine_kwargs={"train_window": 20, "retrain_freq": 5, "top_pct": 0.3,
                           "min_train": 15, "min_test": 5, "min_stocks": 3},
        )
        assert len(out) == 2 * 1 * 2
        assert set(out.columns) >= {
            "buy_cost", "sell_cost", "slippage_bps",
            "annual_net", "sharpe_net", "max_dd",
        }

    def test_higher_cost_lower_net_return(self) -> None:
        df = _make_factor_df(n_dates=50, n_stocks=10)
        grid = {"buy_cost": [0.0001, 0.01], "sell_cost": [0.0001, 0.01], "slippage_bps": [0]}
        out = Robustness.cost_sensitivity(
            df, ["feat_1", "feat_2"], "future_return_5d",
            grid=grid,
            engine_kwargs={"train_window": 20, "retrain_freq": 5, "top_pct": 0.3,
                           "min_train": 15, "min_test": 5, "min_stocks": 3},
        )
        low = out[(out["buy_cost"] == 0.0001) & (out["sell_cost"] == 0.0001)].iloc[0]
        high = out[(out["buy_cost"] == 0.01) & (out["sell_cost"] == 0.01)].iloc[0]
        # 高成本必然 ≤ 低成本（在同一信号集上）
        assert low["annual_net"] >= high["annual_net"] - 1e-9


class TestRegimeSplit:

    def test_two_regime_groups(self) -> None:
        rng = np.random.default_rng(0)
        n = 60
        dates = pd.date_range("2024-01-01", periods=n)
        result_df = pd.DataFrame({
            "trade_date": dates,
            "gross_return": rng.normal(0.001, 0.01, n),
            "net_return": rng.normal(0.0005, 0.012, n),
        })
        bench = pd.Series(rng.normal(0, 0.01, n), index=dates)
        out = Robustness.regime_split(result_df, bench, n_regimes=2)
        assert len(out) == 2
        assert set(out["regime"]) == {"弱", "强"}
        # 切完后总日数 = 输入日数
        assert int(out["n_days"].sum()) == n

    def test_three_regime_groups(self) -> None:
        rng = np.random.default_rng(0)
        n = 60
        dates = pd.date_range("2024-01-01", periods=n)
        result_df = pd.DataFrame({
            "trade_date": dates,
            "gross_return": rng.normal(0.001, 0.01, n),
            "net_return": rng.normal(0.0005, 0.012, n),
        })
        bench = pd.Series(rng.normal(0, 0.01, n), index=dates)
        out = Robustness.regime_split(result_df, bench, n_regimes=3)
        assert set(out["regime"]) == {"熊", "震荡", "牛"}
        assert int(out["n_days"].sum()) == n

    def test_invalid_n_regimes(self) -> None:
        import pytest
        rng = np.random.default_rng(0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n)
        result_df = pd.DataFrame({
            "trade_date": dates,
            "gross_return": rng.normal(0.001, 0.01, n),
            "net_return": rng.normal(0.0005, 0.012, n),
        })
        bench = pd.Series(rng.normal(0, 0.01, n), index=dates)
        with pytest.raises(ValueError, match="n_regimes"):
            Robustness.regime_split(result_df, bench, n_regimes=4)


class TestCostModelSlippage:
    """CostModel 滑点扩展."""

    def test_slippage_adds_to_lateral_cost(self) -> None:
        cm = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=5)
        # 5 bps = 0.0005 单边；双边 = 0.001
        expected = 0.001 + 0.002 + 0.001
        assert abs(cm.lateral_cost() - expected) < 1e-12

    def test_zero_slippage_backward_compat(self) -> None:
        cm = CostModel(buy_cost=0.001, sell_cost=0.002)  # 默认 slippage_bps=0
        assert cm.lateral_cost() == 0.003
        assert cm.buy_total == 0.001
        assert cm.sell_total == 0.002
