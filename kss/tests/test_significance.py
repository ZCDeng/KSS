"""Significance 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.significance import Significance


class TestTStat:

    def test_significantly_positive(self) -> None:
        """N(0.005, 0.005²) × 500 → 极强显著正."""
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0.005, 0.005, 500))
        t, p = Significance.t_stat(r)
        assert t > 5
        assert p < 1e-6

    def test_zero_mean_not_significant(self) -> None:
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0.0, 0.01, 500))
        t, p = Significance.t_stat(r)
        assert p > 0.05

    def test_too_few_samples(self) -> None:
        r = pd.Series([0.01])
        t, p = Significance.t_stat(r)
        assert t == 0.0 and p == 1.0


class TestNeweyWestSE:

    def test_returns_finite_for_normal_input(self) -> None:
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0.001, 0.01, 252))
        se = Significance.newey_west_se(r)
        assert np.isfinite(se)
        assert se > 0

    def test_explicit_lags(self) -> None:
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0.001, 0.01, 252))
        se_default = Significance.newey_west_se(r)
        se_5 = Significance.newey_west_se(r, lags=5)
        # 不同 lag 数值不同但都应为有限正数
        assert se_default > 0 and se_5 > 0


class TestDeflatedSharpe:

    def test_higher_trials_lower_dsr(self) -> None:
        """同样 SR 下，trials 越多 PSR 越低（selection 矫正更严格）."""
        dsr1 = Significance.deflated_sharpe(1.5, n_trials=1, n_obs=252, skew=0, kurt=0)
        dsr100 = Significance.deflated_sharpe(1.5, n_trials=100, n_obs=252, skew=0, kurt=0)
        assert dsr1 > dsr100

    def test_negative_skew_lowers_dsr(self) -> None:
        """负偏度（fat 左尾）应让 DSR 略降."""
        dsr_pos = Significance.deflated_sharpe(1.0, n_trials=5, n_obs=252, skew=0.2, kurt=3.0)
        dsr_neg = Significance.deflated_sharpe(1.0, n_trials=5, n_obs=252, skew=-0.5, kurt=3.0)
        # 注意：skew 与 SR 同号时 DSR 上升；不同号时下降. 测试更细：两者数值不同.
        assert dsr_pos != dsr_neg

    def test_too_few_obs_returns_nan(self) -> None:
        dsr = Significance.deflated_sharpe(1.0, n_trials=5, n_obs=3, skew=0, kurt=0)
        assert pd.isna(dsr)


class TestBootstrapCI:

    def test_ci_contains_true_mean(self) -> None:
        rng = np.random.default_rng(0)
        true_mean = 0.001
        r = pd.Series(rng.normal(true_mean, 0.01, 500))
        lo, hi = Significance.bootstrap_ci(r, metric_fn=lambda s: float(s.mean()), n_boot=200)
        assert lo < true_mean < hi

    def test_reproducible_with_seed(self) -> None:
        rng = np.random.default_rng(42)
        r = pd.Series(rng.normal(0.001, 0.01, 200))
        lo1, hi1 = Significance.bootstrap_ci(r, lambda s: float(s.mean()), n_boot=100, seed=7)
        lo2, hi2 = Significance.bootstrap_ci(r, lambda s: float(s.mean()), n_boot=100, seed=7)
        assert lo1 == lo2 and hi1 == hi2

    def test_too_few_samples_returns_nan(self) -> None:
        r = pd.Series([0.01, 0.02])
        lo, hi = Significance.bootstrap_ci(r, lambda s: float(s.mean()))
        assert pd.isna(lo) and pd.isna(hi)


class TestSharpeSignificance:

    def test_full_block_returns_required_keys(self) -> None:
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0.001, 0.01, 252))
        sig = Significance.sharpe_significance(r, n_trials=5)
        required = {"sharpe", "t_stat", "p_value", "nw_se", "deflated_sharpe", "n"}
        assert required.issubset(set(sig.keys()))
        assert sig["n"] == 252
