"""Alpha158 因子库单元测试.

验证：
- 158 个因子能在合成数据上跑出来；
- KBAR / 价格 / 部分 rolling 公式与手算一致；
- NaN / 常数序列等边界鲁棒.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.features.alpha158 import Alpha158


def _make_df(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """构造满足 generate() 的合成行情 DataFrame (n 个交易日)."""
    rng = np.random.default_rng(seed)
    base = 100 + np.cumsum(rng.normal(0, 1, n))
    high = base + rng.uniform(0.1, 1.5, n)
    low = base - rng.uniform(0.1, 1.5, n)
    open_ = base + rng.normal(0, 0.4, n)
    close = base + rng.normal(0, 0.4, n)
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    vol = rng.uniform(1e4, 1e5, n)
    amount = vol * close
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "vol": vol,
        "amount": amount,
    })


class TestKBAR:
    """KBAR 9 因子."""

    def test_kbar_keys(self) -> None:
        df = _make_df(20)
        out = Alpha158.kbar(df)
        expected = {"KMID", "KLEN", "KMID2", "KUP", "KUP2",
                    "KLOW", "KLOW2", "KSFT", "KSFT2"}
        assert set(out.keys()) == expected
        for v in out.values():
            assert isinstance(v, pd.Series)
            assert len(v) == 20

    def test_kbar_formula(self) -> None:
        """手算 KBAR 在已知 OHLC 上的值."""
        df = pd.DataFrame({
            "open": [10.0], "close": [11.0],
            "high": [12.0], "low": [9.0],
            "vol": [100.0], "trade_date": [pd.Timestamp("2024-01-01")],
        })
        out = Alpha158.kbar(df)
        # KMID = (11-10)/10 = 0.1
        assert out["KMID"].iloc[0] == pytest.approx(0.1)
        # KLEN = (12-9)/10 = 0.3
        assert out["KLEN"].iloc[0] == pytest.approx(0.3)
        # KMID2 = (11-10)/(12-9+1e-12) ≈ 0.333
        assert out["KMID2"].iloc[0] == pytest.approx(1 / 3, rel=1e-6)
        # KUP = (12 - max(10,11)) / 10 = (12-11)/10 = 0.1
        assert out["KUP"].iloc[0] == pytest.approx(0.1)
        # KLOW = (min(10,11) - 9)/10 = 1/10 = 0.1
        assert out["KLOW"].iloc[0] == pytest.approx(0.1)
        # KSFT = (2*11 - 12 - 9)/10 = 1/10 = 0.1
        assert out["KSFT"].iloc[0] == pytest.approx(0.1)


class TestPriceFeatures:
    """价格相对位置因子 (OPEN0/HIGH0/LOW0/VWAP0)."""

    def test_price_in_reasonable_range(self) -> None:
        df = _make_df(50)
        out = Alpha158.price_features(df)
        for name in ["OPEN0", "HIGH0", "LOW0", "VWAP0"]:
            assert name in out
            # 价格/收盘 应该在 ~0.7~1.3 之间（合成数据 noise 不会爆炸）
            vals = out[name].dropna()
            assert ((vals > 0.5) & (vals < 1.5)).all(), \
                f"{name} 超出合理区间: min={vals.min()} max={vals.max()}"

    def test_vwap_fallback_to_close(self) -> None:
        """没有 amount 也没有 vwap 时，VWAP0 退化为 close/close=1.0."""
        df = _make_df(10).drop(columns=["amount"])
        out = Alpha158.price_features(df)
        # 退化路径：VWAP0 = close/close = 1.0
        assert (out["VWAP0"] == 1.0).all()


class TestRollingPrice:
    """价格类 rolling 因子."""

    def test_ma_matches_pandas(self) -> None:
        """MA{w} 应等于 close.rolling(w).mean() / close."""
        df = _make_df(100)
        out = Alpha158.rolling_ma(df["close"], windows=(5, 20))
        expected_ma5 = df["close"].rolling(5, min_periods=5).mean() / df["close"]
        pd.testing.assert_series_equal(
            out["MA5"], expected_ma5, check_names=False,
        )
        assert "MA20" in out

    def test_std_matches_pandas(self) -> None:
        df = _make_df(100)
        out = Alpha158.rolling_std(df["close"], windows=(10,))
        expected = df["close"].rolling(10, min_periods=10).std() / df["close"]
        pd.testing.assert_series_equal(
            out["STD10"], expected, check_names=False,
        )

    def test_roc_formula(self) -> None:
        """ROC{w} = close.shift(w) / close."""
        df = _make_df(50)
        out = Alpha158.rolling_roc(df["close"], windows=(5,))
        expected = df["close"].shift(5) / df["close"]
        pd.testing.assert_series_equal(out["ROC5"], expected, check_names=False)

    def test_rank_monotone(self) -> None:
        """递增 close → RANK{w} 的最后值（用 raw）应 = 1.0；递减序列 = 0.0."""
        # 递增
        s_up = pd.Series(np.arange(1, 21, dtype=float))
        out_up = Alpha158.rolling_rank(s_up, windows=(10,))
        # 第 9 行（index 9）是第一个完整窗口；序列单调递增 → last = max → rank = 1.0
        last_vals = out_up["RANK10"].dropna()
        assert (last_vals == 1.0).all(), f"递增序列 RANK 应全是 1.0: {last_vals.tolist()}"

        # 递减
        s_dn = pd.Series(np.arange(20, 0, -1, dtype=float))
        out_dn = Alpha158.rolling_rank(s_dn, windows=(10,))
        last_vals = out_dn["RANK10"].dropna()
        assert (last_vals == 0.0).all(), f"递减序列 RANK 应全是 0.0: {last_vals.tolist()}"

    def test_rsv_manual(self) -> None:
        """RSV{w} 在 已知 high/low/close 上手算对比."""
        # close = 8, high_max(5) = 10, low_min(5) = 5  →  RSV = (8-5)/(10-5) = 0.6
        n = 5
        df = pd.DataFrame({
            "high": [10, 9, 8, 9, 10],
            "low":  [5, 6, 7, 6, 5],
            "close":[7, 8, 7, 8, 8],
        })
        out = Alpha158.rolling_rsv(df["high"], df["low"], df["close"], windows=(5,))
        rsv5_last = out["RSV5"].iloc[-1]
        assert rsv5_last == pytest.approx((8 - 5) / (10 - 5 + 1e-12), rel=1e-6)


class TestRollingMisc:
    """杂项 rolling 因子."""

    def test_imax_last_position(self) -> None:
        """high 末尾为最大 → IMAX{w} = (w-1)/w."""
        n = 10
        high = pd.Series(np.arange(1, n + 1, dtype=float))
        out = Alpha158.rolling_imax(high, windows=(n,))
        # 单一窗口 = 整个序列，最大在 index = n-1
        # IMAX = (n-1)/n
        assert out[f"IMAX{n}"].iloc[-1] == pytest.approx((n - 1) / n)

    def test_imin_first_position(self) -> None:
        """low 末尾为最小 → IMIN{w} = (w-1)/w；首尾为最大 → IMIN{w} = 0/w = 0."""
        n = 10
        low = pd.Series(np.concatenate([[1.0], np.arange(2, n + 1, dtype=float)]))
        out = Alpha158.rolling_imin(low, windows=(n,))
        # 最小值 = 1 在 index 0  →  IMIN = 0/n = 0
        assert out[f"IMIN{n}"].iloc[-1] == pytest.approx(0.0)

    def test_sumpnd_relations(self) -> None:
        """SUMP + SUMN ≈ 1 (close 无连续相等)；SUMD = SUMP - SUMN."""
        df = _make_df(60)
        out = Alpha158.rolling_sumpnd(df["close"], windows=(10,))
        sp = out["SUMP10"].dropna()
        sn = out["SUMN10"].dropna()
        sd = out["SUMD10"].dropna()
        assert ((sp + sn) > 0.99).all()  # 允许 1e-12 epsilon 抖动
        assert (((sp - sn) - sd).abs() < 1e-9).all()

    def test_vsumpnd_relations(self) -> None:
        """VSUMP + VSUMN ≈ 1；VSUMD = VSUMP - VSUMN."""
        df = _make_df(60)
        out = Alpha158.rolling_vsumpnd(df["vol"], windows=(10,))
        sp = out["VSUMP10"].dropna()
        sn = out["VSUMN10"].dropna()
        sd = out["VSUMD10"].dropna()
        assert ((sp + sn) > 0.99).all()
        assert (((sp - sn) - sd).abs() < 1e-9).all()

    def test_corr_bounded(self) -> None:
        """CORR 必须落在 [-1, 1]."""
        df = _make_df(80)
        out = Alpha158.rolling_corr(df["close"], df["vol"], windows=(20,))
        vals = out["CORR20"].dropna()
        assert (vals >= -1.0).all() and (vals <= 1.0).all()

    def test_rsqr_bounded(self) -> None:
        """RSQR ∈ [0, 1]（合成数据）."""
        df = _make_df(80)
        out = Alpha158.rolling_rsqr(df["close"], windows=(20,))
        vals = out["RSQR20"].dropna()
        assert (vals >= 0.0).all() and (vals <= 1.0 + 1e-9).all()


class TestGenerate:
    """端到端 generate() 测试."""

    def test_generate_count_default(self) -> None:
        """默认 windows=(5,10,20,30,60) → 158 列."""
        df = _make_df(100)
        out = Alpha158.generate(df)
        # 9 (KBAR) + 4 (price) + 29 (rolling op) × 5 (windows) = 158
        assert out.shape[1] == 158, f"列数不对: {out.shape[1]}"

    def test_generate_columns_qlib_names(self) -> None:
        """关键 Qlib 标准命名应出现."""
        df = _make_df(80)
        out = Alpha158.generate(df)
        expected_sample = {"KMID", "KLEN", "OPEN0", "HIGH0", "VWAP0",
                           "MA5", "STD10", "RANK60",
                           "QTLU20", "QTLD20", "RSV30",
                           "IMAX60", "IMIN5", "IMXD20",
                           "CORR10", "CORD20",
                           "CNTP5", "CNTN10", "CNTD20",
                           "SUMP5", "SUMN10", "SUMD20",
                           "WVMA20", "VMA5", "VSTD60",
                           "VSUMP5", "VSUMN10", "VSUMD60",
                           "ROC10", "BETA5", "RSQR10", "RESI20",
                           "MAX5", "MIN5"}
        missing = expected_sample - set(out.columns)
        assert not missing, f"缺少列: {missing}"

    def test_generate_with_nan_robust(self) -> None:
        """输入含 NaN 不应抛错，输出 inf 已被转 NaN."""
        df = _make_df(100)
        # 注入若干 NaN
        df.loc[10:12, "close"] = np.nan
        df.loc[20, "vol"] = 0.0   # 触发 1/0 → inf → NaN
        out = Alpha158.generate(df)
        # 应能跑通；不要求所有值非 NaN，但不应出现 inf
        assert not np.isinf(out.to_numpy(dtype=float, na_value=0.0)).any()
        assert out.shape[1] == 158

    def test_generate_missing_columns(self) -> None:
        df = _make_df(30).drop(columns=["vol"])
        with pytest.raises(ValueError, match="缺少必需列"):
            Alpha158.generate(df)

    def test_generate_custom_windows(self) -> None:
        """自定义 windows 数量影响因子总数."""
        df = _make_df(50)
        out = Alpha158.generate(df, windows=(5, 10))
        # 9 + 4 + 29 × 2 = 71
        assert out.shape[1] == 9 + 4 + 29 * 2
