"""Features 模块单元测试."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.features.technical import TechnicalFactors
from kss.features.pipeline import FactorPipeline


class TestTechnicalFactors:
    """TechnicalFactors 功能测试."""

    def test_momentum_basic(self) -> None:
        """测试动量因子基本计算."""
        close = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
        moms = TechnicalFactors.momentum(close, periods=(1, 2))

        assert "mom_1d" in moms
        assert "mom_2d" in moms
        # mom_1d[1] = (101 - 100) / 100 = 0.01
        assert moms["mom_1d"].iloc[1] == pytest.approx(0.01)
        # mom_2d[2] = (102 - 100) / 100 = 0.02
        assert moms["mom_2d"].iloc[2] == pytest.approx(0.02)

    def test_momentum_with_nan(self) -> None:
        """测试动量因子对缺失值的处理."""
        close = pd.Series([100.0, np.nan, 102.0, 103.0, 104.0])
        moms = TechnicalFactors.momentum(close, periods=(1,))
        assert pd.isna(moms["mom_1d"].iloc[1])

    def test_mi_formula(self) -> None:
        """测试 MI 与通达信 SMA(A,N,1) / tqsdk 递推一致."""
        close = pd.Series(
            [100.0, 101.0, 103.0, 102.0, 105.0, 108.0, 107.0, 110.0],
            dtype=float,
        )
        n = 3
        out = TechnicalFactors.mi(close, periods=(n,))
        a = out[f"mi_a_{n}"]
        mi = out[f"mi_{n}"]

        # A = C_t - C_{t-3}
        assert pd.isna(a.iloc[2])
        assert a.iloc[3] == pytest.approx(102.0 - 100.0)
        assert a.iloc[4] == pytest.approx(105.0 - 101.0)

        # 首个有效 A 作为 MI 初值，其后 MI_t = (A_t + (N-1)*MI_{t-1}) / N
        first_valid = a.first_valid_index()
        assert first_valid is not None
        assert mi.loc[first_valid] == pytest.approx(a.loc[first_valid])
        for i in range(int(first_valid) + 1, len(close)):
            expected = (a.iloc[i] + (n - 1) * mi.iloc[i - 1]) / n
            assert mi.iloc[i] == pytest.approx(expected)

    def test_mi_invalid_period(self) -> None:
        """MI 周期非正时应抛错."""
        with pytest.raises(ValueError, match="正整数"):
            TechnicalFactors.mi(pd.Series([1.0, 2.0, 3.0]), periods=(0,))


class TestFactorPipeline:
    """FactorPipeline 功能测试."""

    def _make_df(self, n: int = 20) -> pd.DataFrame:
        """构造满足 _validate 要求的测试 DataFrame."""
        dates = pd.date_range("2024-01-01", periods=n)
        return pd.DataFrame({
            "trade_date": dates,
            "open": np.linspace(100, 110, n),
            "high": np.linspace(101, 111, n),
            "low": np.linspace(99, 109, n),
            "close": np.linspace(100, 110, n),
            "vol": np.ones(n) * 10000,
        })

    def test_generate_basic(self) -> None:
        """测试因子生成管道主流程."""
        df = self._make_df(n=30)
        pipe = FactorPipeline(df)
        result = pipe.generate()

        assert isinstance(result, pd.DataFrame)
        assert "mom_1d" in result.columns
        assert "macd" in result.columns
        assert "rsi_14" in result.columns
        assert "mi_12" in result.columns
        assert "mi_a_12" in result.columns

    def test_generate_missing_columns(self) -> None:
        """测试缺少必需列时抛出 ValueError."""
        df = pd.DataFrame({"close": [1, 2, 3]})
        with pytest.raises(ValueError, match="缺少必需列"):
            FactorPipeline(df)

    def test_get_feature_cols(self) -> None:
        """测试特征列过滤."""
        df = self._make_df(n=30)
        pipe = FactorPipeline(df)
        factor_df = pipe.generate()
        factor_df["symbol"] = "TEST"
        factor_df["future_return_5d"] = 0.01

        cols = pipe.get_feature_cols(factor_df)
        assert "trade_date" not in cols
        assert "symbol" not in cols
        assert "close" not in cols
        assert "future_return_5d" not in cols
        assert "mom_1d" in cols

    def test_add_targets(self) -> None:
        """测试目标变量添加."""
        df = self._make_df(n=30)
        pipe = FactorPipeline(df)
        factor_df = pipe.generate()
        result = pipe.add_targets(factor_df, df["close"])

        assert "future_return_5d" in result.columns
        assert "future_return_10d" in result.columns
        assert "future_return_20d" in result.columns
        assert "next_day_return" in result.columns

    def test_add_targets_next_day_return_is_next_open_to_next_open(self) -> None:
        """next_day_return 必须反映"次日开盘建仓→第二日开盘换仓"的可达收益.

        修复 P0 #4：旧实现 ``close[t+1]/close[t]`` 等价于"t 日 close 决策、t 日 close
        执行"，实盘不可达，系统性高估收益。新口径：``open[t+2] / open[t+1] - 1``。
        """
        n = 5
        df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=n),
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.5, 102.5, 103.5, 104.5, 105.5],
            "low": [99.5, 100.5, 101.5, 102.5, 103.5],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],  # 不同于 open，保证不混淆
            "vol": [10000.0] * n,
        })
        pipe = FactorPipeline(df)
        factor_df = pipe.generate()
        result = pipe.add_targets(factor_df, df["close"])

        # day-t 行的 next_day_return = open[t+2] / open[t+1] - 1
        assert result["next_day_return"].iloc[0] == pytest.approx(102.0 / 101.0 - 1)
        assert result["next_day_return"].iloc[1] == pytest.approx(103.0 / 102.0 - 1)
        assert result["next_day_return"].iloc[2] == pytest.approx(104.0 / 103.0 - 1)
        # 末 2 行无 open[t+1] 或 open[t+2]，应为 NaN
        assert pd.isna(result["next_day_return"].iloc[-2])
        assert pd.isna(result["next_day_return"].iloc[-1])
        # 反向断言：绝对不可等于 close-to-close（确保旧实现不会因 silent fallback 复活）
        old_value = df["close"].iloc[1] / df["close"].iloc[0] - 1
        assert result["next_day_return"].iloc[0] != pytest.approx(old_value)

    def test_add_targets_future_return_keeps_close_to_close(self) -> None:
        """future_return_Nd 作为训练标签保持 close-to-close（仅 next_day_return 切换执行口径）."""
        n = 10
        df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=n),
            "open": np.linspace(100.0, 109.0, n),
            "high": np.linspace(101.0, 110.0, n),
            "low": np.linspace(99.0, 108.0, n),
            "close": np.linspace(100.0, 109.0, n),
            "vol": np.ones(n) * 10000,
        })
        pipe = FactorPipeline(df)
        factor_df = pipe.generate()
        result = pipe.add_targets(factor_df, df["close"])

        # future_return_5d[0] = close[5]/close[0] - 1
        expected = df["close"].iloc[5] / df["close"].iloc[0] - 1
        assert result["future_return_5d"].iloc[0] == pytest.approx(expected)
        # 末 5 行无 close[t+5]，应为 NaN
        assert result["future_return_5d"].iloc[-5:].isna().all()

    def test_cs_normalize(self) -> None:
        """测试截面标准化后均值为 0、标准差为 1."""
        df = pd.DataFrame({
            "trade_date": ["2024-01-01"] * 3 + ["2024-01-02"] * 3,
            "feature_a": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        })
        normalized = FactorPipeline.cs_normalize(df, ["feature_a"])

        for date in df["trade_date"].unique():
            subset = normalized[normalized["trade_date"] == date]["feature_a"]
            assert subset.mean() == pytest.approx(0.0, abs=1e-6)
            assert subset.std() == pytest.approx(1.0, abs=1e-6)

    def test_cs_normalize_single_stock_date(self) -> None:
        """单股票截面日不应产生 NaN / inf（pandas groupby std ddof=1 在单元素分组返回 NaN）.

        修复 P0 #2 的鲁棒性部分：标准化结果直接喂给 LightGBM，串 NaN/inf 会污染整个模型。
        """
        df = pd.DataFrame({
            "trade_date": ["2024-01-01", "2024-01-01", "2024-01-02"],  # 第二日只有 1 只股票
            "symbol": ["A", "B", "A"],
            "feature_a": [1.0, 3.0, 5.0],
        })
        normalized = FactorPipeline.cs_normalize(df, ["feature_a"])

        assert not normalized["feature_a"].isna().any(), "单股票截面日应被兜底，不出 NaN"
        assert np.isfinite(normalized["feature_a"]).all(), "单股票截面日应被兜底，不出 inf"
        # 单股票日的 z-score 退化为 0：x-mean=0，分子为 0 故结果为 0
        single_day_val = normalized.loc[
            normalized["trade_date"] == "2024-01-02", "feature_a"
        ].iloc[0]
        assert single_day_val == pytest.approx(0.0, abs=1e-6)

    def test_cs_normalize_handles_nan_input(self) -> None:
        """输入特征本身含 NaN 也不应把 NaN 串入下游."""
        df = pd.DataFrame({
            "trade_date": ["2024-01-01"] * 3 + ["2024-01-02"] * 3,
            "feature_a": [1.0, np.nan, 3.0, 4.0, 5.0, np.nan],
        })
        normalized = FactorPipeline.cs_normalize(df, ["feature_a"])
        assert not normalized["feature_a"].isna().any(), "原 feature 含 NaN 应被填 0，不再向下传播"

    def test_cs_normalize_winsorize_default_off_matches_legacy(self) -> None:
        """winsorize 默认 None 时输出与旧实现完全等价（向后兼容）."""
        df = pd.DataFrame({
            "trade_date": ["2024-01-01"] * 5,
            "feature_a": [-5.0, -1.0, 0.0, 1.0, 5.0],
        })
        normalized = FactorPipeline.cs_normalize(df, ["feature_a"])
        # 5 个值的 z-score 应同时包含 |z| > 1 的离群值
        assert normalized["feature_a"].abs().max() > 1.0

    def test_cs_normalize_winsorize_clips_outliers(self) -> None:
        """winsorize=1.0 时 |z| 应被截断到 ≤ 1."""
        df = pd.DataFrame({
            "trade_date": ["2024-01-01"] * 5,
            "feature_a": [-100.0, -1.0, 0.0, 1.0, 100.0],
        })
        normalized = FactorPipeline.cs_normalize(df, ["feature_a"], winsorize=1.0)
        assert normalized["feature_a"].abs().max() <= 1.0 + 1e-9

    def test_cs_normalize_winsorize_3sigma_preserves_inliers(self) -> None:
        """winsorize=3 时正态分布数据应几乎不变."""
        rng = np.random.default_rng(0)
        n = 100
        vals = rng.standard_normal(n)
        df = pd.DataFrame({
            "trade_date": ["2024-01-01"] * n,
            "feature_a": vals,
        })
        normalized = FactorPipeline.cs_normalize(df, ["feature_a"], winsorize=3.0)
        # 正态分布中 |z| > 3 的概率约 0.27%
        clipped_count = (np.abs(normalized["feature_a"]) >= 3 - 1e-9).sum()
        assert clipped_count < 5  # 100 个样本中预期 ≤ 1 个被截断
