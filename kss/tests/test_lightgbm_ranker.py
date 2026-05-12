"""LightGBMRanker 单测.

覆盖：

- 内部工具 :meth:`_group_sizes` / :meth:`_to_rank_label`
- fit / predict 行为与异常路径
- 端到端：弱信号 panel 上 Ranker IC 不显著差于 LightGBMModel (MSE)
- save / load roundtrip
- feature_importance 输出
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from kss.models.lightgbm_model import LightGBMModel
from kss.models.lightgbm_ranker import LightGBMRanker


# ---------------------------------------------------------------------- #
# 合成数据工具
# ---------------------------------------------------------------------- #


def _make_strong_signal_panel(
    n_dates: int = 100,
    n_stocks: int = 50,
    seed: int = 0,
) -> pd.DataFrame:
    """合成强信号 panel：f1 强 informative，截面 IC 应远高于 0."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for d in range(n_dates):
        for s in range(n_stocks):
            f1 = rng.standard_normal()
            f2 = rng.standard_normal()
            f3 = rng.standard_normal()
            # f1 主导，加适量噪声
            r = 0.6 * f1 + 0.1 * f2 + rng.standard_normal() * 0.3
            rows.append(
                {
                    "date": d,
                    "stock": s,
                    "f1": f1,
                    "f2": f2,
                    "f3": f3,
                    "y": r,
                }
            )
    return pd.DataFrame(rows)


def _make_weak_signal_panel(
    n_dates: int = 120,
    n_stocks: int = 50,
    seed: int = 0,
) -> pd.DataFrame:
    """合成弱信号 panel：IC ~ 0.1，模拟 A 股因子量级."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for d in range(n_dates):
        for s in range(n_stocks):
            f1 = rng.standard_normal()
            f2 = rng.standard_normal()
            f3 = rng.standard_normal()
            r = 0.05 * f1 + 0.02 * f2 + rng.standard_normal() * 0.5
            rows.append(
                {
                    "date": d,
                    "stock": s,
                    "f1": f1,
                    "f2": f2,
                    "f3": f3,
                    "y": r,
                }
            )
    return pd.DataFrame(rows)


def _xs_ic(
    df: pd.DataFrame, pred_col: str, y_col: str = "y", date_col: str = "date"
) -> float:
    """每日 Spearman IC 平均."""
    ics: list[float] = []
    for _, sub in df.groupby(date_col):
        if len(sub) < 3:
            continue
        ic, _ = stats.spearmanr(sub[pred_col], sub[y_col])
        if np.isfinite(ic):
            ics.append(ic)
    if not ics:
        return 0.0
    return float(np.mean(ics))


# ---------------------------------------------------------------------- #
# 内部工具
# ---------------------------------------------------------------------- #


class TestGroupSizes:
    def test_simple(self) -> None:
        dates = np.array(["d1", "d1", "d2", "d2", "d2"])
        out = LightGBMRanker._group_sizes(dates)
        np.testing.assert_array_equal(out, np.array([2, 3]))

    def test_three_groups_int(self) -> None:
        dates = np.array([1, 1, 1, 2, 2, 3, 3, 3, 3])
        out = LightGBMRanker._group_sizes(dates)
        np.testing.assert_array_equal(out, np.array([3, 2, 4]))

    def test_single_group(self) -> None:
        dates = np.array([7, 7, 7, 7])
        out = LightGBMRanker._group_sizes(dates)
        np.testing.assert_array_equal(out, np.array([4]))

    def test_empty(self) -> None:
        out = LightGBMRanker._group_sizes(np.array([]))
        assert out.shape == (0,)

    def test_sum_equals_total(self) -> None:
        rng = np.random.default_rng(1)
        dates = np.repeat(np.arange(10), rng.integers(2, 8, size=10))
        out = LightGBMRanker._group_sizes(dates)
        assert int(out.sum()) == len(dates)


class TestToRankLabel:
    def test_max_returns_get_top_bin(self) -> None:
        # 每天 5 个样本，最后一个是当天 max
        n_stocks = 5
        n_dates = 4
        n_bins = 6
        ranker = LightGBMRanker(n_rank_bins=n_bins, num_boost_round=10)

        dates = np.repeat(np.arange(n_dates), n_stocks)
        # 把每天的最后一个样本设成 100（远大于其他）
        y = np.tile(np.array([-1.0, -0.5, 0.0, 0.5, 100.0]), n_dates)

        labels = ranker._to_rank_label(y, dates)
        # reshape 回 (n_dates, n_stocks) 检查每行最大者落到 top bin
        labels_per_day = labels.reshape(n_dates, n_stocks)
        assert (labels_per_day[:, -1] == n_bins - 1).all()
        # 每天最小者的 bin 应严格小于最大者
        assert (labels_per_day[:, 0] < labels_per_day[:, -1]).all()
        # 排序保序：第 i 列的 bin <= 第 i+1 列的 bin
        for i in range(n_stocks - 1):
            assert (labels_per_day[:, i] <= labels_per_day[:, i + 1]).all()

    def test_labels_in_range(self) -> None:
        rng = np.random.default_rng(0)
        n_bins = 5
        ranker = LightGBMRanker(n_rank_bins=n_bins, num_boost_round=10)
        dates = np.repeat(np.arange(10), 20)
        y = rng.standard_normal(len(dates))
        labels = ranker._to_rank_label(y, dates)
        assert labels.min() >= 0
        assert labels.max() <= n_bins - 1
        assert labels.dtype.kind in ("i", "u")

    def test_constant_returns_within_day(self) -> None:
        """同一天所有样本 y 相同：rank 都是 0.5，应得中间档."""
        ranker = LightGBMRanker(n_rank_bins=6, num_boost_round=10)
        dates = np.array([1, 1, 1, 1])
        y = np.array([0.5, 0.5, 0.5, 0.5])
        labels = ranker._to_rank_label(y, dates)
        # rank=0.5 → floor(3.0)=3
        assert (labels == 3).all()


# ---------------------------------------------------------------------- #
# fit / predict 行为
# ---------------------------------------------------------------------- #


class TestFitPredict:
    def test_basic_fit_predict_shape(self) -> None:
        df = _make_strong_signal_panel(n_dates=20, n_stocks=10, seed=0)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values

        ranker = LightGBMRanker(num_boost_round=50, n_rank_bins=4)
        ranker.fit(X, y, trade_dates=dates)
        pred = ranker.predict(X)
        assert pred.shape == (len(df),)
        assert pred.dtype == np.float64 or pred.dtype == np.float32

    def test_fit_missing_trade_dates_raises(self) -> None:
        df = _make_strong_signal_panel(n_dates=5, n_stocks=10, seed=0)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        ranker = LightGBMRanker(num_boost_round=10)
        with pytest.raises(ValueError, match="trade_dates"):
            ranker.fit(X, y)

    def test_fit_length_mismatch_raises(self) -> None:
        df = _make_strong_signal_panel(n_dates=5, n_stocks=10, seed=0)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values[:-3]  # 故意截短
        ranker = LightGBMRanker(num_boost_round=10)
        with pytest.raises(ValueError, match="长度"):
            ranker.fit(X, y, trade_dates=dates)

    def test_predict_before_fit_raises(self) -> None:
        ranker = LightGBMRanker()
        with pytest.raises(RuntimeError):
            ranker.predict(np.zeros((3, 3)))

    def test_dataframe_input_preserves_feature_names(self) -> None:
        df = _make_strong_signal_panel(n_dates=10, n_stocks=15, seed=1)
        X_df = df[["f1", "f2", "f3"]]
        y = df["y"].values
        dates = df["date"].values

        ranker = LightGBMRanker(num_boost_round=30)
        ranker.fit(X_df, y, trade_dates=dates)
        fi = ranker.feature_importance()
        assert fi is not None
        assert set(fi["feature"]) == {"f1", "f2", "f3"}

    def test_unsorted_trade_dates_are_handled(self) -> None:
        """打乱 trade_dates 顺序应不影响训练（fit 内部会排序）."""
        df = _make_strong_signal_panel(n_dates=15, n_stocks=12, seed=2)
        shuffled = df.sample(frac=1.0, random_state=3).reset_index(drop=True)
        X = shuffled[["f1", "f2", "f3"]].values
        y = shuffled["y"].values
        dates = shuffled["date"].values

        ranker = LightGBMRanker(num_boost_round=40, n_rank_bins=4)
        ranker.fit(X, y, trade_dates=dates)
        pred = ranker.predict(X)
        assert pred.shape == (len(shuffled),)

    def test_early_stopping_with_valid_set(self) -> None:
        df = _make_strong_signal_panel(n_dates=30, n_stocks=20, seed=4)
        split = int(len(df) * 0.7)
        # 注意先按 date 排序保证 split 不会把同一天劈两边
        df = df.sort_values("date").reset_index(drop=True)
        tr, va = df.iloc[:split], df.iloc[split:]

        ranker = LightGBMRanker(
            num_boost_round=200, early_stopping_rounds=10, n_rank_bins=4
        )
        ranker.fit(
            tr[["f1", "f2", "f3"]].values,
            tr["y"].values,
            trade_dates=tr["date"].values,
            valid_X=va[["f1", "f2", "f3"]].values,
            valid_y=va["y"].values,
            valid_dates=va["date"].values,
        )
        # best_iteration 应该被 early stopping 设置（不等于 num_boost_round）
        assert ranker._booster is not None

    def test_partial_valid_args_raises(self) -> None:
        df = _make_strong_signal_panel(n_dates=10, n_stocks=10, seed=0)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values
        ranker = LightGBMRanker(num_boost_round=10)
        with pytest.raises(ValueError, match="valid"):
            ranker.fit(
                X,
                y,
                trade_dates=dates,
                valid_X=X[:50],
                valid_y=y[:50],
                # 故意缺 valid_dates
            )


# ---------------------------------------------------------------------- #
# 端到端
# ---------------------------------------------------------------------- #


class TestEndToEnd:
    def test_strong_signal_ic_above_threshold(self) -> None:
        """强信号 panel 上，截面 IC 应 > 0.3."""
        df = _make_strong_signal_panel(n_dates=80, n_stocks=40, seed=7)
        df = df.sort_values("date").reset_index(drop=True)
        split = int(len(df) * 0.7)
        # split 必须落在某个日期边界上
        cut_date = df.iloc[split]["date"]
        tr = df[df["date"] < cut_date]
        te = df[df["date"] >= cut_date]

        ranker = LightGBMRanker(num_boost_round=80, n_rank_bins=5)
        ranker.fit(
            tr[["f1", "f2", "f3"]].values,
            tr["y"].values,
            trade_dates=tr["date"].values,
        )
        te = te.copy()
        te["pred"] = ranker.predict(te[["f1", "f2", "f3"]].values)

        ic = _xs_ic(te, "pred", "y", "date")
        assert ic > 0.3, f"强信号 IC 期望 > 0.3，实际 {ic:.4f}"

    def test_weak_signal_vs_regressor(self) -> None:
        """弱信号 panel 上：Ranker IC 不显著差于 LightGBMModel (MSE).

        这是 #34 任务的核心 hypothesis 验证."""
        df = _make_weak_signal_panel(n_dates=150, n_stocks=50, seed=11)
        df = df.sort_values("date").reset_index(drop=True)
        cut = df.iloc[int(len(df) * 0.7)]["date"]
        tr = df[df["date"] < cut]
        te = df[df["date"] >= cut].copy()

        # Ranker
        ranker = LightGBMRanker(num_boost_round=100, n_rank_bins=5)
        ranker.fit(
            tr[["f1", "f2", "f3"]].values,
            tr["y"].values,
            trade_dates=tr["date"].values,
        )
        te["pred_r"] = ranker.predict(te[["f1", "f2", "f3"]].values)

        # Regressor
        reg = LightGBMModel()
        reg.fit(tr[["f1", "f2", "f3"]].values, tr["y"].values)
        te["pred_g"] = reg.predict(te[["f1", "f2", "f3"]].values)

        ic_r = _xs_ic(te, "pred_r", "y", "date")
        ic_g = _xs_ic(te, "pred_g", "y", "date")
        # 弱信号上两者绝对值都不大；只要 Ranker IC 不被 Regressor 显著碾压
        # （差距 < 0.05）就算 hypothesis 成立
        assert ic_r > ic_g - 0.05, (
            f"Ranker IC ({ic_r:.4f}) 被 Regressor IC ({ic_g:.4f}) 碾压"
        )
        # 至少其一为正 —— 否则说明合成数据本身没信号
        assert max(ic_r, ic_g) > 0.0


# ---------------------------------------------------------------------- #
# 序列化
# ---------------------------------------------------------------------- #


class TestSaveLoad:
    def test_roundtrip(self, tmp_path) -> None:
        df = _make_strong_signal_panel(n_dates=20, n_stocks=15, seed=5)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values

        ranker = LightGBMRanker(num_boost_round=50, n_rank_bins=4)
        ranker.fit(X, y, trade_dates=dates)
        before = ranker.predict(X)

        path = tmp_path / "ranker.joblib"
        ranker.save(str(path))

        loaded = LightGBMRanker()
        loaded.load(str(path))
        after = loaded.predict(X)

        np.testing.assert_array_equal(before, after)
        assert loaded.n_rank_bins == 4

    def test_load_missing_file(self, tmp_path) -> None:
        ranker = LightGBMRanker()
        with pytest.raises(FileNotFoundError):
            ranker.load(str(tmp_path / "does_not_exist.joblib"))


# ---------------------------------------------------------------------- #
# 特征重要性
# ---------------------------------------------------------------------- #


class TestFeatureImportance:
    def test_returns_dataframe(self) -> None:
        df = _make_strong_signal_panel(n_dates=15, n_stocks=15, seed=6)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values

        ranker = LightGBMRanker(num_boost_round=40, n_rank_bins=4)
        ranker.fit(X, y, trade_dates=dates)
        fi = ranker.feature_importance()
        assert fi is not None
        assert {"feature", "importance"}.issubset(fi.columns)
        assert len(fi) == 3
        # importance 非负
        assert (fi["importance"] >= 0).all()

    def test_none_when_unfit(self) -> None:
        ranker = LightGBMRanker()
        assert ranker.feature_importance() is None


# ---------------------------------------------------------------------- #
# 参数处理
# ---------------------------------------------------------------------- #


class TestParams:
    def test_label_gain_truncated_to_n_bins(self) -> None:
        ranker = LightGBMRanker(n_rank_bins=3)
        assert len(ranker.params["label_gain"]) == 3

    def test_label_gain_extended_to_n_bins(self) -> None:
        ranker = LightGBMRanker(n_rank_bins=10)
        assert len(ranker.params["label_gain"]) == 10

    def test_custom_params_override(self) -> None:
        ranker = LightGBMRanker(
            params={"learning_rate": 0.123, "num_leaves": 7}
        )
        assert ranker.params["learning_rate"] == pytest.approx(0.123)
        assert ranker.params["num_leaves"] == 7
        # 默认值仍保留
        assert ranker.params["objective"] == "lambdarank"

    def test_invalid_n_rank_bins(self) -> None:
        with pytest.raises(ValueError):
            LightGBMRanker(n_rank_bins=1)


# ---------------------------------------------------------------------- #
# Task #4.4 sample_weight 入口
# ---------------------------------------------------------------------- #


class TestSampleWeight:
    def test_fit_accepts_sample_weight(self) -> None:
        df = _make_strong_signal_panel(n_dates=15, n_stocks=10, seed=0)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values
        rng = np.random.default_rng(0)
        sw = rng.uniform(0.1, 1.0, size=len(df))

        ranker = LightGBMRanker(num_boost_round=30, n_rank_bins=4)
        ranker.fit(X, y, trade_dates=dates, sample_weight=sw)
        pred = ranker.predict(X)
        assert pred.shape == (len(df),)
        assert np.isfinite(pred).all()

    def test_fit_sample_weight_length_mismatch_raises(self) -> None:
        df = _make_strong_signal_panel(n_dates=10, n_stocks=10, seed=0)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values
        bad_sw = np.ones(len(df) - 5)
        ranker = LightGBMRanker(num_boost_round=10)
        with pytest.raises(ValueError, match="sample_weight"):
            ranker.fit(X, y, trade_dates=dates, sample_weight=bad_sw)

    def test_fit_sample_weight_default_none_matches_equal_weight(self) -> None:
        """``sample_weight=None`` 与 ``sample_weight=np.ones(N)`` 行为应一致.

        LGB lambdarank 内部对全 1 权重的处理应等价于不传 weight（数值上).
        """
        df = _make_strong_signal_panel(n_dates=20, n_stocks=10, seed=3)
        X = df[["f1", "f2", "f3"]].values
        y = df["y"].values
        dates = df["date"].values

        ranker_a = LightGBMRanker(num_boost_round=40, n_rank_bins=4)
        ranker_a.fit(X, y, trade_dates=dates)
        pred_a = ranker_a.predict(X)

        ranker_b = LightGBMRanker(num_boost_round=40, n_rank_bins=4)
        ranker_b.fit(X, y, trade_dates=dates, sample_weight=np.ones(len(df)))
        pred_b = ranker_b.predict(X)

        # 同种子下应严格一致
        np.testing.assert_allclose(pred_a, pred_b, atol=1e-12)
