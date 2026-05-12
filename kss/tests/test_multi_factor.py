"""MultiFactorCombiner + SingleStockAnalyzer.multi_factor_backtest 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.single_stock import SingleStockAnalyzer
from kss.strategies.multi_factor import MultiFactorCombiner


# ====================================================================== #
# MultiFactorCombiner
# ====================================================================== #


class TestEqualWeighted:

    def test_equal_weights_sum_to_one(self) -> None:
        c = MultiFactorCombiner.equal_weighted(["a", "b", "c", "d"])
        assert sum(c.weights.values()) == pytest.approx(1.0)
        assert all(v == pytest.approx(0.25) for v in c.weights.values())

    def test_empty_factors_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            MultiFactorCombiner.equal_weighted([])


class TestFromScanTable:

    def test_top_k_by_sharpe(self) -> None:
        st = pd.DataFrame({
            "factor": ["x", "y", "z", "w"],
            "sharpe": [1.5, 1.0, 0.5, 2.0],
        })
        c = MultiFactorCombiner.from_scan_table(st, top_k=2)
        # 前 2 行（按表序，不按 sharpe 排序）—— 与 scan_factors 输出已排序的约定一致
        assert set(c.factors) == {"x", "y"}
        assert c.weights["x"] == pytest.approx(1.5 / 2.5)
        assert c.weights["y"] == pytest.approx(1.0 / 2.5)

    def test_clip_negative_drops_losers(self) -> None:
        st = pd.DataFrame({
            "factor": ["good", "bad"],
            "sharpe": [0.8, -0.3],
        })
        c = MultiFactorCombiner.from_scan_table(st, top_k=2, clip_negative=True)
        assert "bad" not in c.weights
        assert c.weights["good"] == pytest.approx(1.0)

    def test_all_negative_raises(self) -> None:
        st = pd.DataFrame({
            "factor": ["a", "b"],
            "sharpe": [-0.1, -0.5],
        })
        with pytest.raises(ValueError, match="无 > 0 的有效权重"):
            MultiFactorCombiner.from_scan_table(st, clip_negative=True)

    def test_missing_columns_raises(self) -> None:
        with pytest.raises(ValueError, match="缺少必需列"):
            MultiFactorCombiner.from_scan_table(pd.DataFrame({"foo": [1]}))


class TestFromIcTable:

    def test_signed_preserves_ic_direction(self) -> None:
        ic = pd.DataFrame({
            "factor": ["p", "q"],
            "horizon": ["future_return_5d", "future_return_5d"],
            "ic": [-0.2, 0.1],
        })
        c = MultiFactorCombiner.from_ic_table(ic, top_k=2, signed=True)
        # |IC|/Σ|IC| = 0.2/0.3 vs 0.1/0.3，signed 保留符号
        assert c.weights["p"] < 0
        assert c.weights["q"] > 0
        assert abs(c.weights["p"]) == pytest.approx(0.2 / 0.3)
        assert abs(c.weights["q"]) == pytest.approx(0.1 / 0.3)

    def test_unsigned_takes_abs(self) -> None:
        ic = pd.DataFrame({
            "factor": ["p"],
            "horizon": ["future_return_5d"],
            "ic": [-0.2],
        })
        c = MultiFactorCombiner.from_ic_table(ic, top_k=1, signed=False)
        assert c.weights["p"] > 0

    def test_filters_by_horizon(self) -> None:
        ic = pd.DataFrame({
            "factor": ["a", "b"],
            "horizon": ["future_return_5d", "future_return_10d"],
            "ic": [0.1, 0.3],
        })
        c = MultiFactorCombiner.from_ic_table(ic, horizon="future_return_5d", top_k=2)
        assert set(c.factors) == {"a"}

    def test_no_matching_horizon_raises(self) -> None:
        ic = pd.DataFrame({
            "factor": ["a"], "horizon": ["1d"], "ic": [0.1],
        })
        with pytest.raises(ValueError, match="无 horizon="):
            MultiFactorCombiner.from_ic_table(ic, horizon="future_return_5d")


class TestCombineZscores:

    def test_equal_weight_recovers_average(self) -> None:
        idx = pd.date_range("2024-01-01", periods=5)
        z_a = pd.Series([1, 2, 3, 4, 5], index=idx, dtype=float)
        z_b = pd.Series([5, 4, 3, 2, 1], index=idx, dtype=float)
        c = MultiFactorCombiner.equal_weighted(["a", "b"])
        combined = c.combine_zscores({"a": z_a, "b": z_b})
        # 等权 → (z_a + z_b)/2 = 3
        assert (combined == 3.0).all()

    def test_negative_weight_flips_sign(self) -> None:
        idx = pd.date_range("2024-01-01", periods=4)
        z = pd.Series([1, 2, 3, 4], index=idx, dtype=float)
        c = MultiFactorCombiner({"a": -1.0}, normalize=True)
        # 单因子 -1 归一化 → -1
        combined = c.combine_zscores({"a": z})
        assert (combined == -z).all()

    def test_missing_factor_filled_zero(self) -> None:
        """combiner 包含 b，但 z_dict 只给 a → b 被丢弃但 a 仍生效（不抛错）."""
        idx = pd.date_range("2024-01-01", periods=3)
        z_a = pd.Series([1.0, 2.0, 3.0], index=idx)
        c = MultiFactorCombiner({"a": 0.5, "b": 0.5}, normalize=False)
        combined = c.combine_zscores({"a": z_a})
        # 只有 a 在交集中，combined = 0.5 * z_a
        assert combined.iloc[0] == pytest.approx(0.5)
        assert combined.iloc[2] == pytest.approx(1.5)

    def test_no_intersection_raises(self) -> None:
        c = MultiFactorCombiner({"a": 1.0})
        with pytest.raises(ValueError, match="无交集"):
            c.combine_zscores({"b": pd.Series([1, 2, 3])})

    def test_normalize_keeps_unit_scale(self) -> None:
        c = MultiFactorCombiner({"a": 2.0, "b": 3.0, "c": -5.0}, normalize=True)
        assert sum(abs(v) for v in c.weights.values()) == pytest.approx(1.0)

    def test_zero_and_nan_weights_dropped(self) -> None:
        c = MultiFactorCombiner({"a": 1.0, "b": 0.0, "c": float("nan")})
        assert c.factors == ["a"]


class TestRestrictTo:

    def test_restrict_keeps_intersection(self) -> None:
        c = MultiFactorCombiner({"a": 1.0, "b": 2.0, "c": 3.0})
        c2 = c.restrict_to(["a", "c", "z"])  # z 不存在
        assert set(c2.factors) == {"a", "c"}
        # 重新归一：a=1/(1+3), c=3/(1+3) （都用归一前的原始值，因为 c.weights 已归一）
        # 实际：归一前 a=1/6, c=3/6 → restrict 后 a / (a+c) = (1/6) / (4/6) = 0.25
        assert c2.weights["a"] == pytest.approx(0.25)
        assert c2.weights["c"] == pytest.approx(0.75)

    def test_restrict_to_empty_raises(self) -> None:
        c = MultiFactorCombiner({"a": 1.0})
        with pytest.raises(ValueError, match="restrict_to 后无任何因子"):
            c.restrict_to(["nonexistent"])


# ====================================================================== #
# multi_factor_backtest 集成测试
# ====================================================================== #


def _make_factor_df(n: int = 200, seed: int = 0) -> pd.DataFrame:
    """合成 panel：3 个因子 + next_day_return."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n)
    # 因子和未来收益弱相关
    f1 = rng.standard_normal(n)
    f2 = rng.standard_normal(n)
    f3 = rng.standard_normal(n)
    # next_day_return 受 f1 + f2 驱动
    r = 0.3 * f1 + 0.3 * f2 + rng.standard_normal(n) * 0.5
    # 模拟实盘收益小量级
    r = r * 0.01
    return pd.DataFrame({
        "trade_date": idx,
        "f1": f1,
        "f2": f2,
        "f3": f3,
        "next_day_return": r,
    })


class TestMultiFactorBacktest:

    def test_smoke_runs_without_error(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        combiner = MultiFactorCombiner.equal_weighted(["f1", "f2", "f3"])
        out = analyzer.multi_factor_backtest(
            combiner, rolling_window=30,
        )
        # 关键字段都齐全
        for key in [
            "metrics", "net_return", "gross_return", "positions",
            "signal_z", "turnover_total", "n_trades", "weights", "factors",
        ]:
            assert key in out

    def test_single_factor_equivalent_to_signal_backtest(self) -> None:
        """combiner 只有 1 个因子且权重=1 时，结果应与 signal_backtest 一致."""
        df = _make_factor_df(n=300, seed=42)
        analyzer = SingleStockAnalyzer(df)
        ref = analyzer.signal_backtest("f1", rolling_window=30)
        solo = MultiFactorCombiner({"f1": 1.0}, normalize=False)
        out = analyzer.multi_factor_backtest(solo, rolling_window=30)
        # net_return / positions / n_trades 严格等价
        pd.testing.assert_series_equal(
            ref["net_return"], out["net_return"],
            check_names=False,
        )
        assert ref["n_trades"] == out["n_trades"]
        assert ref["metrics"]["sharpe"] == pytest.approx(
            out["metrics"]["sharpe"], abs=1e-10,
        )

    def test_returns_aligned_with_input(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        c = MultiFactorCombiner.equal_weighted(["f1", "f2"])
        out = analyzer.multi_factor_backtest(c, rolling_window=30)
        assert len(out["net_return"]) == len(df)
        assert len(out["positions"]) == len(df)
        assert len(out["signal_z"]) == len(df)

    def test_all_factors_missing_raises(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        c = MultiFactorCombiner({"nonexistent_factor": 1.0}, normalize=False)
        with pytest.raises(ValueError, match="均不在 factor_df"):
            analyzer.multi_factor_backtest(c, rolling_window=30)

    def test_partial_missing_factors_logged_but_succeeds(self) -> None:
        """combiner 含 1 个不存在的因子时跳过它，剩余因子正常组合."""
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        c = MultiFactorCombiner({"f1": 0.5, "nonexistent": 0.5}, normalize=False)
        out = analyzer.multi_factor_backtest(c, rolling_window=30)
        # 应该跑通且至少有些非零仓位
        assert out["metrics"]["n"] > 0
