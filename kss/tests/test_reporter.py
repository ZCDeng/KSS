"""BacktestReporter 单元测试."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kss.backtest.reporter import BacktestReporter


def _make_result_df(n: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n),
        "gross_return": rng.normal(0.001, 0.01, n),
        "net_return": rng.normal(0.0005, 0.012, n),
        "turnover": rng.uniform(0, 1, n),
        "cost": rng.uniform(0, 0.003, n),
        "n_stocks": rng.integers(5, 15, n),
        "n_kept": rng.integers(0, 5, n),
        "buy_turnover": rng.uniform(0, 1, n),
    })


class TestReporter:

    def test_minimal_report(self, tmp_path: Path) -> None:
        rep = BacktestReporter(result_df=_make_result_df())
        out = tmp_path / "min.md"
        content = rep.to_markdown(out)
        assert out.exists()
        assert "回测诊断报告" in content
        assert "收益摘要" in content

    def test_full_report_sections(self, tmp_path: Path) -> None:
        result = _make_result_df()
        rep = BacktestReporter(
            result_df=result,
            diagnostics={
                "ic_summary": {
                    "ic_mean": 0.05, "ic_std": 0.1, "ir": 7.94,
                    "ic_t_stat": 2.5, "ic_positive_rate": 0.65,
                    "ic_skew": 0.1, "ic_max_consec_neg": 3, "n": 30,
                },
                "quantile_df": pd.DataFrame({
                    "Q1": np.full(30, -0.001),
                    "Q2": np.full(30, 0.0),
                    "Q3": np.full(30, 0.001),
                }, index=result["trade_date"]),
                "long_short": pd.Series(np.full(30, 0.002), index=result["trade_date"]),
                "monotonicity": 0.95,
                "ic_decay": pd.DataFrame({
                    "horizon": ["future_return_1d", "future_return_5d"],
                    "ic_mean": [0.1, 0.03],
                    "ir": [10, 3],
                    "ic_positive_rate": [0.7, 0.55],
                }),
            },
            significance={
                "sharpe": 1.2, "t_stat": 2.5, "p_value": 0.013,
                "nw_se": 0.001, "deflated_sharpe": 0.85, "n": 30,
                "annual_return_ci": (-0.05, 0.25),
            },
            benchmark={
                "alpha_beta": {
                    "alpha_annual": 0.08, "beta": 1.1,
                    "alpha_t_stat": 2.1, "alpha_p_value": 0.04,
                    "r_squared": 0.6, "info_ratio": 0.9,
                },
                "excess_max_dd": -0.05,
            },
            robustness={
                "cost_sensitivity": pd.DataFrame({
                    "buy_cost": [0.001, 0.002],
                    "sell_cost": [0.002, 0.003],
                    "slippage_bps": [0, 10],
                    "annual_net": [0.15, 0.08],
                    "sharpe_net": [1.2, 0.6],
                    "max_dd": [-0.1, -0.15],
                    "calmar": [1.5, 0.5],
                    "turnover_avg": [0.4, 0.4],
                    "n": [30, 30],
                }),
                "regime_split": pd.DataFrame({
                    "regime": ["熊", "牛"],
                    "n_days": [15, 15],
                    "strat_annual": [0.05, 0.20],
                    "strat_sharpe": [0.5, 2.0],
                    "bench_annual": [-0.10, 0.30],
                    "alpha": [0.15, -0.10],
                }),
            },
            meta={"pool": "kcb50", "period": "10d"},
        )

        out = tmp_path / "full.md"
        content = rep.to_markdown(out)

        # 各章节锚点都存在
        assert "1. 收益摘要" in content
        assert "2. 信号质量诊断" in content
        assert "Information Coefficient" in content
        assert "分位数累计收益" in content
        assert "单调性得分" in content
        assert "3. 统计显著性" in content
        assert "Deflated Sharpe" in content
        assert "4. 基准对比与归因" in content
        assert "Alpha" in content
        assert "5. 稳健性扫描" in content
        assert "成本敏感度" in content
        assert "市场状态分段" in content
        # meta 字段已注入
        assert "kcb50" in content

    def test_empty_result_df(self, tmp_path: Path) -> None:
        rep = BacktestReporter(result_df=pd.DataFrame())
        out = tmp_path / "empty.md"
        content = rep.to_markdown(out)
        assert "无有效回测结果" in content
