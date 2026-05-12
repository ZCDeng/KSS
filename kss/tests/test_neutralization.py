"""FactorPipeline.neutralize 单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.features.pipeline import FactorPipeline


def _make_panel(
    n_stocks: int = 20,
    n_days: int = 3,
    n_industries: int = 3,
    seed: int = 0,
) -> pd.DataFrame:
    """构造一个干净的横截面 panel，含 ``industry`` / ``log_mv`` 列."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days)
    industries = [f"IND_{i}" for i in range(n_industries)]
    rows: list[dict] = []
    for d in dates:
        for j in range(n_stocks):
            rows.append({
                "trade_date": d,
                "symbol": f"S{j:03d}",
                "industry": industries[j % n_industries],
                "log_mv": rng.normal(20.0, 1.0),
            })
    return pd.DataFrame(rows)


class TestNeutralizeOrthogonality:
    """正交性验证：中性化后残差与解释变量解相关."""

    def test_industry_dummy_perfectly_predicts_factor(self) -> None:
        """factor = 行业固定效应 + 微噪声 → 中性化后 residual ≈ 0."""
        panel = _make_panel(n_stocks=30, n_days=4, n_industries=3, seed=1)
        ind_to_effect = {"IND_0": -1.0, "IND_1": 0.0, "IND_2": 1.0}
        rng = np.random.default_rng(7)
        panel["factor"] = (
            panel["industry"].map(ind_to_effect)
            + rng.normal(0, 1e-4, size=len(panel))
        )

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=False,
        )
        # 残差应该都接近 0（行业固定效应被完全吸收）.
        assert out["factor"].abs().max() < 1e-2

    def test_log_mv_linear_factor_residual_uncorrelated(self) -> None:
        """factor = 2 * log_mv + ε → 中性化后 residual.corr(log_mv) ≈ 0."""
        panel = _make_panel(n_stocks=50, n_days=3, n_industries=1, seed=2)
        rng = np.random.default_rng(11)
        panel["factor"] = 2.0 * panel["log_mv"] + rng.normal(0, 0.1, size=len(panel))

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=False, by_log_mv=True,
        )
        corr = out[["factor", "log_mv"]].corr().iloc[0, 1]
        assert abs(corr) < 0.05

    def test_combined_neutralization_uncorrelated_with_log_mv(self) -> None:
        """同时中性化行业 + log_mv，residual 与 log_mv 解相关."""
        panel = _make_panel(n_stocks=40, n_days=4, n_industries=3, seed=3)
        rng = np.random.default_rng(13)
        ind_eff = {"IND_0": -1.0, "IND_1": 0.5, "IND_2": 1.0}
        panel["factor"] = (
            1.5 * panel["log_mv"]
            + panel["industry"].map(ind_eff)
            + rng.normal(0, 0.2, size=len(panel))
        )

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=True,
        )
        # 与 log_mv 解相关
        corr = out[["factor", "log_mv"]].corr().iloc[0, 1]
        assert abs(corr) < 0.05


class TestNeutralizeFlags:
    """``by_industry`` / ``by_log_mv`` 开关行为."""

    def test_by_industry_false_only_neutralizes_log_mv(self) -> None:
        """关闭行业 → 行业方差应保留在残差里."""
        panel = _make_panel(n_stocks=40, n_days=3, n_industries=3, seed=4)
        ind_eff = {"IND_0": -5.0, "IND_1": 0.0, "IND_2": 5.0}
        panel["factor"] = panel["industry"].map(ind_eff).astype(float)

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=False, by_log_mv=True,
        )
        # 行业组间差异应该还在.
        grp_means = out.groupby("industry")["factor"].mean()
        assert grp_means.max() - grp_means.min() > 1.0

    def test_by_log_mv_false_only_neutralizes_industry(self) -> None:
        """关闭 log_mv → 残差仍与 log_mv 相关（行业方差被吸收）."""
        panel = _make_panel(n_stocks=50, n_days=3, n_industries=3, seed=5)
        rng = np.random.default_rng(17)
        panel["factor"] = (
            3.0 * panel["log_mv"] + rng.normal(0, 0.05, size=len(panel))
        )

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=False,
        )
        # log_mv 没有被中性化 → 相关性应该仍然显著.
        corr = out[["factor", "log_mv"]].corr().iloc[0, 1]
        assert abs(corr) > 0.5


class TestNeutralizeEdgeCases:
    """边界情况."""

    def test_below_min_stocks_per_day_preserves_values(self) -> None:
        """单日股票数 < min_stocks_per_day → 跳过，保留原值."""
        panel = _make_panel(n_stocks=3, n_days=2, n_industries=2, seed=6)
        panel["factor"] = np.arange(len(panel), dtype=float)
        original = panel["factor"].copy()

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=True,
            min_stocks_per_day=5,
        )
        # 全部跳过 → 残差 == 原值.
        np.testing.assert_allclose(out["factor"].to_numpy(), original.to_numpy())

    def test_feature_cols_contains_log_mv_skipped(self) -> None:
        """feature_cols 含 log_mv 自身 → 自动跳过（不报错，且原列不变）."""
        panel = _make_panel(n_stocks=20, n_days=2, n_industries=2, seed=7)
        panel["factor"] = panel["log_mv"] * 0.5
        original_log_mv = panel["log_mv"].copy()

        out = FactorPipeline.neutralize(
            panel, feature_cols=["log_mv", "factor"],
            by_industry=True, by_log_mv=True,
        )
        # log_mv 列必须原样保留（不能用 log_mv 解释 log_mv）.
        np.testing.assert_allclose(out["log_mv"].to_numpy(), original_log_mv.to_numpy())

    def test_missing_industry_col_raises(self) -> None:
        """by_industry=True 但 panel 没有 industry 列 → ValueError."""
        panel = _make_panel(n_stocks=10, n_days=2, n_industries=2, seed=8)
        panel = panel.drop(columns=["industry"])
        panel["factor"] = 0.0
        with pytest.raises(ValueError, match="industry"):
            FactorPipeline.neutralize(
                panel, feature_cols=["factor"],
                by_industry=True, by_log_mv=True,
            )

    def test_missing_log_mv_col_raises(self) -> None:
        """by_log_mv=True 但 panel 没有 log_mv → ValueError."""
        panel = _make_panel(n_stocks=10, n_days=2, n_industries=2, seed=9)
        panel = panel.drop(columns=["log_mv"])
        panel["factor"] = 0.0
        with pytest.raises(ValueError, match="log_mv"):
            FactorPipeline.neutralize(
                panel, feature_cols=["factor"],
                by_industry=False, by_log_mv=True,
            )

    def test_single_industry_day_falls_back_to_log_mv_only(self) -> None:
        """所有股票同一个行业 → industry dummy 矩阵为空，仅做 log_mv 回归，不抛错."""
        panel = _make_panel(n_stocks=20, n_days=2, n_industries=1, seed=10)
        rng = np.random.default_rng(19)
        panel["factor"] = 2.0 * panel["log_mv"] + rng.normal(0, 0.1, size=len(panel))

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=True,
        )
        # log_mv 仍应被中性化掉.
        corr = out[["factor", "log_mv"]].corr().iloc[0, 1]
        assert abs(corr) < 0.05


class TestNeutralizeNaNHandling:
    """NaN 不应传染整个截面."""

    def test_nan_in_factor_does_not_contaminate_other_rows(self) -> None:
        """单只股票当日 factor=NaN → 当日该股 NaN 保留，其余正常中性化."""
        panel = _make_panel(n_stocks=20, n_days=2, n_industries=2, seed=11)
        rng = np.random.default_rng(23)
        panel["factor"] = 1.5 * panel["log_mv"] + rng.normal(0, 0.1, size=len(panel))
        # 故意把第 0 行打成 NaN.
        panel.loc[0, "factor"] = np.nan

        out = FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=True,
        )
        # NaN 行保留 NaN.
        assert pd.isna(out.loc[0, "factor"])
        # 其他行应已中性化 → 与 log_mv 解相关.
        clean = out.dropna(subset=["factor"])
        corr = clean[["factor", "log_mv"]].corr().iloc[0, 1]
        assert abs(corr) < 0.1

    def test_returns_copy_does_not_mutate_input(self) -> None:
        """neutralize 应返回副本，不污染输入."""
        panel = _make_panel(n_stocks=20, n_days=2, n_industries=2, seed=12)
        panel["factor"] = panel["log_mv"] * 2.0
        before = panel["factor"].copy()

        FactorPipeline.neutralize(
            panel, feature_cols=["factor"],
            by_industry=True, by_log_mv=True,
        )
        np.testing.assert_allclose(panel["factor"].to_numpy(), before.to_numpy())
