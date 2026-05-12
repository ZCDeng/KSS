"""Benchmark 单元测试."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kss.backtest.benchmark import Benchmark


@pytest.fixture
def synthetic_index(tmp_path: Path) -> Path:
    """合成指数 CSV：close 走 GBM."""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    rets = rng.normal(0.0003, 0.012, len(dates))
    close = 1000 * (1 + pd.Series(rets)).cumprod().values
    df = pd.DataFrame({
        "ts_code": "000688.SH",
        "trade_date": dates.strftime("%Y-%m-%d"),
        "close": close,
    })
    p = tmp_path / "synthetic_index.csv"
    df.to_csv(p, index=False)
    return p


class TestBenchmarkLoading:

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Benchmark(tmp_path / "nonexistent.csv")

    def test_missing_close_column_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.csv"
        pd.DataFrame({"trade_date": ["2024-01-01"], "open": [100]}).to_csv(p, index=False)
        with pytest.raises(ValueError, match="缺少必需列"):
            Benchmark(p)

    def test_load_yyyymmdd_format(self, tmp_path: Path) -> None:
        """Tushare 指数文件经常用 YYYYMMDD 整数日期."""
        p = tmp_path / "yyyymmdd.csv"
        pd.DataFrame({
            "trade_date": [20240101, 20240102, 20240103],
            "close": [100.0, 101.0, 102.0],
        }).to_csv(p, index=False)
        bm = Benchmark(p)
        assert len(bm._df) == 3


class TestAlphaBeta:

    def test_recovers_synthetic_alpha_beta(self, synthetic_index: Path) -> None:
        """构造 r_strat = α + β r_bench + ε → 回归出 α/β."""
        bm = Benchmark(synthetic_index)
        bench_close = bm._df.set_index("trade_date")["close"]
        bench_ret = bench_close.pct_change().dropna()

        true_alpha = 0.0008
        true_beta = 1.4
        rng = np.random.default_rng(1)
        strat_ret = pd.Series(
            true_alpha + true_beta * bench_ret.values + rng.normal(0, 0.003, len(bench_ret)),
            index=bench_ret.index,
        )

        result = bm.alpha_beta(strat_ret)
        assert result["alpha_daily"] == pytest.approx(true_alpha, abs=5e-4)
        assert result["beta"] == pytest.approx(true_beta, abs=0.1)
        assert result["r_squared"] > 0.7

    def test_too_few_samples_returns_nan(self, synthetic_index: Path) -> None:
        bm = Benchmark(synthetic_index)
        r = pd.Series([0.01, 0.02], index=pd.date_range("2024-01-01", periods=2))
        result = bm.alpha_beta(r)
        assert pd.isna(result["alpha_daily"])
        assert result["n"] == 2


class TestAlignedReturns:

    def test_aligned_with_strategy_index(self, synthetic_index: Path) -> None:
        bm = Benchmark(synthetic_index)
        strat_idx = pd.date_range("2024-02-01", periods=20, freq="B")
        strat = pd.Series(np.zeros(20), index=strat_idx)
        aligned = bm.aligned_returns(strat)
        assert len(aligned) == len(strat)
        assert (aligned.index == strat.index).all()


class TestExcessCurve:

    def test_zero_when_strategy_equals_benchmark(self, synthetic_index: Path) -> None:
        bm = Benchmark(synthetic_index)
        bench_ret = bm._df.set_index("trade_date")["close"].pct_change().dropna()
        excess = bm.excess_curve(bench_ret.iloc[:50])
        # 与基准一致 → 累计超额 = 0
        assert (excess.abs() < 1e-10).all()

    def test_max_dd_non_positive(self, synthetic_index: Path) -> None:
        bm = Benchmark(synthetic_index)
        rng = np.random.default_rng(2)
        dates = pd.date_range("2024-02-01", periods=100, freq="B")
        strat = pd.Series(rng.normal(0.001, 0.01, 100), index=dates)
        dd = bm.excess_max_dd(strat)
        assert dd <= 0
