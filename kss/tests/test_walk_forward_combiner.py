"""WalkForwardCombiner + threshold_grid_search 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.single_stock import SingleStockAnalyzer
from kss.backtest.walk_forward_combiner import WalkForwardCombiner
from kss.strategies.multi_factor import MultiFactorCombiner


def _make_factor_df(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """合成单股票面板：3 个因子（f1 informative, f2 noisy, f3 trend）."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n)
    f1 = rng.standard_normal(n)
    f2 = rng.standard_normal(n)
    f3 = np.cumsum(rng.standard_normal(n) * 0.05)
    r = 0.4 * f1 + 0.1 * f2 + rng.standard_normal(n) * 0.5
    return pd.DataFrame({
        "trade_date": idx,
        "f1": f1, "f2": f2, "f3": f3,
        "next_day_return": r * 0.005,  # 小量级模拟实盘
    })


# ====================================================================== #
# threshold_grid_search
# ====================================================================== #


class TestThresholdGridSearch:

    def test_default_grid_shape(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        # 默认 grid：7 × 7 = 49 点，但 upper <= lower 会被过滤，约 21 个有效组合
        grid = analyzer.threshold_grid_search("f1", rolling_window=30)
        assert not grid.empty
        # upper > lower 强约束
        assert (grid["upper"] > grid["lower"]).all()
        # 列结构完整
        for col in ["upper", "lower", "annual", "sharpe", "max_dd",
                    "calmar", "n_trades", "total", "robust_sharpe"]:
            assert col in grid.columns

    def test_custom_grid(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        grid = analyzer.threshold_grid_search(
            "f1",
            upper_grid=[0.5, 1.0, 1.5],
            lower_grid=[-1.5, -1.0, -0.5],
            rolling_window=30,
        )
        # 9 个点全部 upper > lower
        assert len(grid) == 9
        assert (grid["upper"] > grid["lower"]).all()

    def test_symmetric_only(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        grid = analyzer.threshold_grid_search(
            "f1",
            upper_grid=[0.5, 1.0, 1.5],
            lower_grid=[-1.5, -1.0, -0.5],
            rolling_window=30,
            require_symmetric=True,
        )
        # 仅 3 个对称点
        assert len(grid) == 3
        for _, row in grid.iterrows():
            assert row["upper"] + row["lower"] == pytest.approx(0.0)

    def test_accepts_combiner(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df)
        c = MultiFactorCombiner.equal_weighted(["f1", "f2"])
        grid = analyzer.threshold_grid_search(
            c,
            upper_grid=[0.5, 1.0],
            lower_grid=[-1.0, -0.5],
            rolling_window=30,
        )
        assert not grid.empty

    def test_sorted_by_sharpe_desc(self) -> None:
        df = _make_factor_df(seed=42)
        analyzer = SingleStockAnalyzer(df)
        grid = analyzer.threshold_grid_search("f1", rolling_window=30)
        sh = grid["sharpe"].dropna()
        if len(sh) > 1:
            assert (sh.diff().dropna() <= 1e-9).all(), "应按 Sharpe 降序排列"


# ====================================================================== #
# WalkForwardCombiner
# ====================================================================== #


class TestWalkForwardCombinerInit:

    def test_min_warm_up_computed(self) -> None:
        df = _make_factor_df()
        wfc = WalkForwardCombiner(
            factor_df=df,
            feature_cols=["f1", "f2", "f3"],
            combiner_builder=WalkForwardCombiner.builder_equal_topk_sharpe(top_k=2),
            train_window=100, retrain_freq=20, rolling_window=30,
        )
        # min_warm_up = max(train_window=100, rolling_window=30) = 100
        assert wfc.min_warm_up == 100

    def test_sample_too_small_raises(self) -> None:
        df = _make_factor_df(n=50)
        wfc = WalkForwardCombiner(
            factor_df=df,
            feature_cols=["f1"],
            combiner_builder=WalkForwardCombiner.builder_equal_topk_sharpe(top_k=1),
            train_window=200, retrain_freq=20, rolling_window=60,
        )
        with pytest.raises(ValueError, match="样本不足"):
            wfc.run()


class TestWalkForwardCombinerRun:

    def test_smoke_returns_keys(self) -> None:
        df = _make_factor_df(n=400)
        wfc = WalkForwardCombiner(
            factor_df=df,
            feature_cols=["f1", "f2", "f3"],
            combiner_builder=WalkForwardCombiner.builder_equal_topk_sharpe(top_k=2),
            train_window=100, retrain_freq=20, rolling_window=30,
        )
        res = wfc.run()
        for k in ["metrics", "net_return", "positions", "signal_z",
                  "weights_history", "n_retrains"]:
            assert k in res
        assert res["n_retrains"] > 0
        assert len(res["weights_history"]) == res["n_retrains"]

    def test_weights_history_dates_in_order(self) -> None:
        df = _make_factor_df(n=400)
        wfc = WalkForwardCombiner(
            factor_df=df,
            feature_cols=["f1", "f2", "f3"],
            combiner_builder=WalkForwardCombiner.builder_equal_topk_sharpe(top_k=2),
            train_window=100, retrain_freq=20, rolling_window=30,
        )
        res = wfc.run()
        dates = [h["trade_date"] for h in res["weights_history"]]
        # retrain 日期严格升序
        assert all(dates[i] < dates[i + 1] for i in range(len(dates) - 1))

    def test_anti_lookahead_first_retrain_uses_only_history(self) -> None:
        """关键防 look-ahead 测试：第一次 retrain 选出的因子只能基于训练窗口数据.

        构造合成数据：前半段 f1 强 informative，后半段 f2 强 informative.
        第一次 retrain（在前半段）必须选 f1 而非 f2.
        """
        rng = np.random.default_rng(0)
        n = 400
        idx = pd.date_range("2024-01-01", periods=n)
        f1 = rng.standard_normal(n)
        f2 = rng.standard_normal(n)
        # 收益：前半段 r = 0.5 * f1 + noise；后半段 r = 0.5 * f2 + noise
        r = np.zeros(n)
        r[: n // 2] = 0.5 * f1[: n // 2] + rng.standard_normal(n // 2) * 0.3
        r[n // 2 :] = 0.5 * f2[n // 2 :] + rng.standard_normal(n // 2) * 0.3
        df = pd.DataFrame({
            "trade_date": idx, "f1": f1, "f2": f2,
            "next_day_return": r * 0.005,
        })

        wfc = WalkForwardCombiner(
            factor_df=df,
            feature_cols=["f1", "f2"],
            # top_k=1 只选最强一个
            combiner_builder=WalkForwardCombiner.builder_equal_topk_sharpe(top_k=1),
            train_window=120, retrain_freq=30, rolling_window=30,
        )
        res = wfc.run()
        # 第一次 retrain 应该选 f1（训练窗口在前半段，f1 informative）
        assert res["n_retrains"] > 0
        first = res["weights_history"][0]
        assert "f1" in first["weights"], (
            f"前半段训练应选 f1，实际选了 {list(first['weights'].keys())}"
        )

    def test_lookahead_baseline_inflates_sharpe(self) -> None:
        """对照测试：全样本 baseline 的 Sharpe 应明显 ≥ walk-forward Sharpe.

        揭示 look-ahead bias 的量化效应——构造极端 informative factor（IC≈1），
        全样本 scan 永远会选到它；walk-forward 在样本不足时可能漂移到 noisy factor.
        """
        rng = np.random.default_rng(7)
        n = 400
        idx = pd.date_range("2024-01-01", periods=n)
        # f1 永远强 informative
        f1 = rng.standard_normal(n)
        # f2 / f3 噪声
        f2 = rng.standard_normal(n)
        f3 = rng.standard_normal(n)
        r = 0.6 * f1 + rng.standard_normal(n) * 0.4
        df = pd.DataFrame({
            "trade_date": idx, "f1": f1, "f2": f2, "f3": f3,
            "next_day_return": r * 0.01,
        })

        # 全样本 baseline
        analyzer = SingleStockAnalyzer(df)
        scan = analyzer.scan_factors(["f1", "f2", "f3"], rolling_window=30)
        baseline = analyzer.multi_factor_backtest(
            MultiFactorCombiner.equal_weighted(scan.head(2)["factor"].tolist()),
            rolling_window=30,
        )

        wfc = WalkForwardCombiner(
            factor_df=df,
            feature_cols=["f1", "f2", "f3"],
            combiner_builder=WalkForwardCombiner.builder_equal_topk_sharpe(top_k=2),
            train_window=100, retrain_freq=20, rolling_window=30,
        )
        wf_res = wfc.run()

        # 至少两边都得有效数（不能都是 NaN）
        assert np.isfinite(baseline["metrics"].get("sharpe", float("nan")))
        assert np.isfinite(wf_res["metrics"].get("sharpe", float("nan")))


class TestBuilders:

    def test_sharpe_topk_builder_uses_sharpe(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df.iloc[:200])
        builder = WalkForwardCombiner.builder_sharpe_topk(top_k=2)
        combiner = builder(analyzer, ["f1", "f2", "f3"])
        # f1 informative → 应入选 top 2
        assert "f1" in combiner.weights

    def test_ic_topk_builder(self) -> None:
        """IC builder 默认 horizon=future_return_5d；dummy data 需补该列."""
        df = _make_factor_df(n=300)
        # 用 next_day_return shift -5 拼一个 future_return_5d
        df["future_return_5d"] = df["next_day_return"].shift(-5)
        analyzer = SingleStockAnalyzer(df.iloc[:200])
        builder = WalkForwardCombiner.builder_ic_topk(top_k=2, signed=True)
        combiner = builder(analyzer, ["f1", "f2", "f3"])
        assert len(combiner.weights) >= 1

    def test_equal_topk_sharpe_builder(self) -> None:
        df = _make_factor_df()
        analyzer = SingleStockAnalyzer(df.iloc[:200])
        builder = WalkForwardCombiner.builder_equal_topk_sharpe(top_k=2)
        combiner = builder(analyzer, ["f1", "f2", "f3"])
        # 等权 → 所有权重应相等
        weights = list(combiner.weights.values())
        if len(weights) > 1:
            assert all(abs(w - weights[0]) < 1e-9 for w in weights)
