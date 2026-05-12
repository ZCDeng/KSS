"""SignalDiagnostics 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.diagnostics import SignalDiagnostics


def _make_panel(
    n_dates: int = 30,
    n_stocks: int = 20,
    signal_strength: float = 0.5,
    seed: int = 0,
) -> pd.DataFrame:
    """构造截面 panel：``ret = signal_strength * pred + noise``."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    rows = []
    for d in dates:
        for s in range(n_stocks):
            pred = rng.standard_normal()
            ret = signal_strength * pred + rng.standard_normal() * (1 - signal_strength)
            rows.append({
                "trade_date": d,
                "symbol": f"S{s}",
                "pred_score": pred,
                "next_day_return": ret,
            })
    return pd.DataFrame(rows)


class TestIC:
    """IC 计算."""

    def test_perfect_positive_relation_ic_near_one(self) -> None:
        """完美线性关系：IC 应接近 1."""
        pred = pd.Series([1, 2, 3, 4, 5])
        ret = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        ic = SignalDiagnostics.ic(pred, ret, method="spearman")
        assert ic == pytest.approx(1.0, abs=1e-6)

    def test_perfect_inverse_relation_ic_near_neg_one(self) -> None:
        pred = pd.Series([1, 2, 3, 4, 5])
        ret = pd.Series([0.05, 0.04, 0.03, 0.02, 0.01])
        ic = SignalDiagnostics.ic(pred, ret, method="spearman")
        assert ic == pytest.approx(-1.0, abs=1e-6)

    def test_zero_std_returns_nan(self) -> None:
        """全相同预测 → IC=NaN（避免假阳性）."""
        pred = pd.Series([1.0, 1.0, 1.0, 1.0])
        ret = pd.Series([0.01, 0.02, 0.03, 0.04])
        ic = SignalDiagnostics.ic(pred, ret, method="spearman")
        assert pd.isna(ic)

    def test_too_few_samples_returns_nan(self) -> None:
        pred = pd.Series([1.0, 2.0])
        ret = pd.Series([0.01, 0.02])
        ic = SignalDiagnostics.ic(pred, ret)
        assert pd.isna(ic)


class TestICSeries:
    """IC 时间序列."""

    def test_ic_series_high_when_signal_strong(self) -> None:
        panel = _make_panel(n_dates=30, n_stocks=20, signal_strength=0.9, seed=0)
        ic_s = SignalDiagnostics.ic_series(panel, "pred_score", "next_day_return")
        assert ic_s.mean() > 0.7, f"signal_strength=0.9 时 IC 应该 > 0.7，实际 {ic_s.mean()}"
        assert (ic_s > 0).all(), "强信号下每天 IC 都应为正"

    def test_ic_series_near_zero_when_no_signal(self) -> None:
        panel = _make_panel(n_dates=30, n_stocks=20, signal_strength=0.0, seed=1)
        ic_s = SignalDiagnostics.ic_series(panel, "pred_score", "next_day_return")
        assert abs(ic_s.mean()) < 0.2

    def test_ic_summary_keys(self) -> None:
        panel = _make_panel(n_dates=30, n_stocks=20, signal_strength=0.5)
        ic_s = SignalDiagnostics.ic_series(panel, "pred_score", "next_day_return")
        summary = SignalDiagnostics.ic_summary(ic_s)
        required = {
            "ic_mean", "ic_std", "ir", "ic_t_stat",
            "ic_positive_rate", "ic_skew", "ic_max_consec_neg", "n",
        }
        assert required.issubset(set(summary.keys()))
        assert summary["n"] == 30

    def test_ic_summary_empty(self) -> None:
        summary = SignalDiagnostics.ic_summary(pd.Series(dtype=float))
        assert summary["n"] == 0
        assert pd.isna(summary["ic_mean"])


class TestQuantileReturns:
    """分位数组合."""

    def test_quantile_returns_monotonic_in_strong_signal(self) -> None:
        """signal_strength 强时 Q1..Q5 累计收益应单调上升."""
        panel = _make_panel(n_dates=40, n_stocks=20, signal_strength=0.8, seed=0)
        quant = SignalDiagnostics.quantile_returns(
            panel, "pred_score", "next_day_return", n_quantiles=5,
        )
        cum = (1 + quant.fillna(0)).prod() - 1
        # 单调上升
        assert cum["Q1"] < cum["Q2"] < cum["Q3"] < cum["Q4"] < cum["Q5"]

    def test_quantile_returns_columns(self) -> None:
        panel = _make_panel(n_dates=20, n_stocks=20)
        quant = SignalDiagnostics.quantile_returns(
            panel, "pred_score", "next_day_return", n_quantiles=4,
        )
        assert list(quant.columns) == ["Q1", "Q2", "Q3", "Q4"]

    def test_quantile_returns_handles_too_few_stocks(self) -> None:
        """单日股票数 < n_quantiles 时 NaN 行."""
        df = pd.DataFrame({
            "trade_date": [pd.Timestamp("2024-01-01")] * 3,
            "symbol": ["A", "B", "C"],
            "pred_score": [1.0, 2.0, 3.0],
            "next_day_return": [0.01, 0.02, 0.03],
        })
        quant = SignalDiagnostics.quantile_returns(
            df, "pred_score", "next_day_return", n_quantiles=5,
        )
        assert quant.isna().all().all()


class TestLongShortAndMonotonicity:

    def test_long_short_spread_positive_when_monotonic(self) -> None:
        panel = _make_panel(n_dates=30, n_stocks=20, signal_strength=0.8)
        quant = SignalDiagnostics.quantile_returns(
            panel, "pred_score", "next_day_return", n_quantiles=5,
        )
        spread = SignalDiagnostics.long_short_spread(quant)
        assert spread.mean() > 0

    def test_monotonicity_one_for_perfect(self) -> None:
        """构造严格单调累计收益 → monotonicity ≈ 1."""
        # 直接构造 quant DataFrame
        rng = np.random.default_rng(0)
        idx = pd.date_range("2024-01-01", periods=20)
        quant = pd.DataFrame({
            "Q1": rng.normal(-0.01, 0.001, 20),
            "Q2": rng.normal(-0.005, 0.001, 20),
            "Q3": rng.normal(0.0, 0.001, 20),
            "Q4": rng.normal(0.005, 0.001, 20),
            "Q5": rng.normal(0.01, 0.001, 20),
        }, index=idx)
        mono = SignalDiagnostics.monotonicity_score(quant)
        assert mono == pytest.approx(1.0, abs=1e-6)

    def test_monotonicity_negative_for_reversed(self) -> None:
        idx = pd.date_range("2024-01-01", periods=20)
        rng = np.random.default_rng(0)
        quant = pd.DataFrame({
            "Q1": rng.normal(0.01, 0.001, 20),
            "Q2": rng.normal(0.005, 0.001, 20),
            "Q3": rng.normal(0.0, 0.001, 20),
            "Q4": rng.normal(-0.005, 0.001, 20),
            "Q5": rng.normal(-0.01, 0.001, 20),
        }, index=idx)
        mono = SignalDiagnostics.monotonicity_score(quant)
        assert mono == pytest.approx(-1.0, abs=1e-6)


class TestCrossSectionICScan:
    """批量截面 IC 扫描."""

    def test_returns_one_row_per_factor_horizon(self) -> None:
        panel = _make_panel(n_dates=30, n_stocks=20, signal_strength=0.6, seed=0)
        # 补一个第二因子（弱 informative）
        rng = np.random.default_rng(1)
        panel["pred_score_b"] = panel["pred_score"] * 0.2 + rng.standard_normal(len(panel))
        # 补一个 future_return_5d
        panel["future_return_5d"] = panel.groupby("symbol")["next_day_return"].shift(-5)
        scan = SignalDiagnostics.cross_section_ic_scan(
            panel, ["pred_score", "pred_score_b"],
            horizons=["next_day_return", "future_return_5d"],
        )
        assert len(scan) == 2 * 2  # 2 factors × 2 horizons
        assert {"factor", "horizon", "ic_mean", "ic_t_stat", "abs_t_stat", "ir", "n"}.issubset(
            set(scan.columns)
        )

    def test_sorted_by_abs_t_stat_within_horizon(self) -> None:
        panel = _make_panel(n_dates=30, n_stocks=20, signal_strength=0.8, seed=2)
        rng = np.random.default_rng(3)
        panel["weak"] = rng.standard_normal(len(panel))
        scan = SignalDiagnostics.cross_section_ic_scan(
            panel, ["pred_score", "weak"],
            horizons=["next_day_return"],
        )
        # strong (pred_score) 应在 weak 之前
        assert scan.iloc[0]["factor"] == "pred_score"
        assert scan.iloc[0]["abs_t_stat"] >= scan.iloc[1]["abs_t_stat"]

    def test_missing_horizon_raises(self) -> None:
        panel = _make_panel()
        with pytest.raises(ValueError, match="缺少 horizon 列"):
            SignalDiagnostics.cross_section_ic_scan(
                panel, ["pred_score"], horizons=["nonexistent_col"],
            )

    def test_missing_factor_silently_skipped(self) -> None:
        panel = _make_panel()
        scan = SignalDiagnostics.cross_section_ic_scan(
            panel, ["pred_score", "nonexistent_factor"],
        )
        # 只 pred_score 出现，nonexistent 被跳过
        assert "pred_score" in scan["factor"].values
        assert "nonexistent_factor" not in scan["factor"].values


class TestICDecay:

    def test_ic_decay_returns_one_row_per_horizon(self) -> None:
        rng = np.random.default_rng(0)
        dates = pd.date_range("2024-01-01", periods=30)
        rows = []
        for d in dates:
            for s in range(15):
                pred = rng.standard_normal()
                rows.append({
                    "trade_date": d, "symbol": f"S{s}", "pred_score": pred,
                    "future_return_1d": pred * 0.9 + rng.standard_normal() * 0.1,
                    "future_return_5d": pred * 0.3 + rng.standard_normal() * 0.7,
                    "future_return_20d": pred * 0.05 + rng.standard_normal() * 0.95,
                })
        panel = pd.DataFrame(rows)
        decay = SignalDiagnostics.ic_decay(
            panel, "pred_score",
            ["future_return_1d", "future_return_5d", "future_return_20d"],
        )
        assert len(decay) == 3
        # IC 应随 horizon 增长而衰减
        assert decay["ic_mean"].iloc[0] > decay["ic_mean"].iloc[1] > decay["ic_mean"].iloc[2]
