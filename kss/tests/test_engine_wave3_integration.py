"""Wave 3 集成测试 —— walk_forward 的 neutralize / ranker / execution 新参数路径.

每个新能力一个 smoke 测试 + 一个组合测试（三能力同时启用）.
覆盖目标：新参数不破现有逻辑、能产生有效输出、与对应底层模块结果一致.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cost_model import CostModel, ExecutionModel
from kss.backtest.engine import BacktestEngine


def _make_panel(
    n_dates: int = 80,
    n_stocks: int = 12,
    seed: int = 0,
    with_industry: bool = True,
    with_execution_cols: bool = True,
) -> pd.DataFrame:
    """合成横截面 panel，f1 信号强、f2 弱、log_mv 反向 informative."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range("2024-01-01", periods=n_dates):
        for s in range(n_stocks):
            f1 = rng.standard_normal()
            f2 = rng.standard_normal()
            log_mv = rng.uniform(20.0, 25.0)
            # 小市值（log_mv 小）反向预测高收益
            r5 = 0.3 * f1 - 0.4 * (log_mv - 22.5) + rng.standard_normal() * 0.5
            r_next = 0.05 * f1 - 0.1 * (log_mv - 22.5) + rng.standard_normal() * 0.4
            close = 10.0 + rng.uniform(-2, 2)
            row = {
                "trade_date": d,
                "symbol": f"688{s:03d}.SH",
                "f1": f1,
                "f2": f2,
                "log_mv": log_mv,
                "future_return_5d": r5 * 0.005,
                "next_day_return": r_next * 0.005,
            }
            if with_industry:
                row["industry"] = "TECH" if s < n_stocks // 2 else "MFG"
            if with_execution_cols:
                # 给 execution 用的列
                pre_close = close * (1 + rng.normal(0, 0.01))
                row["close"] = close
                row["pre_close"] = pre_close
                row["open"] = pre_close * (1 + rng.normal(0, 0.02))  # 普通日开盘
                row["amount"] = rng.uniform(50000, 500000)  # 千元
            rows.append(row)
    return pd.DataFrame(rows)


class TestBackwardCompat:
    """所有新参数默认 off → 行为完全等价旧版."""

    def test_baseline_runs_unchanged(self) -> None:
        df = _make_panel(n_dates=60, n_stocks=12)
        engine = BacktestEngine(CostModel())
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
        )
        assert res is not None and not res.empty
        # 旧列在
        for col in ["trade_date", "gross_return", "net_return", "turnover",
                    "cost", "n_stocks", "n_kept"]:
            assert col in res.columns
        # 新列不存在（默认未启用 execution）
        assert "n_tradable_pre" not in res.columns
        assert "n_tradable_post" not in res.columns


class TestNeutralizeIntegration:
    """neutralize 路径在 walk_forward 中正常工作."""

    def test_neutralize_runs(self) -> None:
        df = _make_panel(n_dates=60, n_stocks=12, with_industry=True)
        engine = BacktestEngine(CostModel())
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
            neutralize=True,
        )
        assert res is not None and not res.empty
        # 仍含 panel attrs
        assert "panel" in res.attrs

    def test_neutralize_without_industry_col_fails_gracefully(self) -> None:
        """缺 industry 列 + by_industry=True → 底层 neutralize 抛 ValueError."""
        df = _make_panel(n_dates=60, n_stocks=12, with_industry=False)
        engine = BacktestEngine(CostModel())
        with pytest.raises(ValueError):
            engine.walk_forward(
                df, ["f1", "f2"], "future_return_5d",
                train_window=20, retrain_freq=5, top_pct=0.3,
                min_train=15, min_test=5, min_stocks=3,
                neutralize=True, neutralize_by_industry=True,
            )

    def test_neutralize_by_log_mv_only(self) -> None:
        """关 industry，仅做 log_mv 中性化（不需要 industry 列）."""
        df = _make_panel(n_dates=60, n_stocks=12, with_industry=False)
        engine = BacktestEngine(CostModel())
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
            neutralize=True,
            neutralize_by_industry=False,
            neutralize_by_log_mv=True,
        )
        assert res is not None and not res.empty


class TestRankerIntegration:
    """model_type='ranker' 路径正常工作."""

    def test_ranker_runs(self) -> None:
        df = _make_panel(n_dates=80, n_stocks=12)
        engine = BacktestEngine(CostModel())
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=30, retrain_freq=10, top_pct=0.3,
            min_train=20, min_test=5, min_stocks=3,
            model_type="ranker",
        )
        assert res is not None and not res.empty

    def test_ranker_vs_regression_both_valid(self) -> None:
        """两种 model_type 在同一数据上都产出合法 Sharpe."""
        df = _make_panel(n_dates=100, n_stocks=15, seed=42)
        engine = BacktestEngine(CostModel())
        common = dict(
            factor_df=df, feature_cols=["f1", "f2"], label_col="future_return_5d",
            train_window=30, retrain_freq=10, top_pct=0.3,
            min_train=20, min_test=5, min_stocks=3,
        )
        res_reg = engine.walk_forward(**common)
        res_rk = engine.walk_forward(**common, model_type="ranker")
        # 两个都跑出来
        assert res_reg is not None
        assert res_rk is not None
        # 输出列对齐
        assert set(res_reg.columns) <= set(res_rk.columns)


class TestExecutionIntegration:
    """execution 路径加入 walk_forward."""

    def test_execution_adds_tradable_columns(self) -> None:
        df = _make_panel(n_dates=60, n_stocks=15, with_execution_cols=True)
        engine = BacktestEngine(CostModel())
        exec_m = ExecutionModel(
            kcb_limit_pct=0.20, max_volume_pct=0.05, open_slippage_bps=5,
        )
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=20, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
            execution=exec_m,
        )
        assert res is not None and not res.empty
        # execution 启用时新增 n_tradable_pre / n_tradable_post 列
        assert "n_tradable_pre" in res.columns
        assert "n_tradable_post" in res.columns

    def test_execution_cost_no_smaller_than_baseline(self) -> None:
        """加 execution 后 cost 应 ≥ 不加（多了 slippage_cost）."""
        df = _make_panel(n_dates=80, n_stocks=15, with_execution_cols=True, seed=7)
        engine = BacktestEngine(CostModel(buy_cost=0.001, sell_cost=0.002))
        common = dict(
            factor_df=df, feature_cols=["f1", "f2"], label_col="future_return_5d",
            train_window=25, retrain_freq=5, top_pct=0.3,
            min_train=15, min_test=5, min_stocks=3,
        )
        res_base = engine.walk_forward(**common)
        res_exec = engine.walk_forward(
            **common,
            execution=ExecutionModel(
                kcb_limit_pct=0.20, max_volume_pct=0.10, open_slippage_bps=10,
            ),
        )
        # 累计 cost：execution 加滑点 → 严格 >= base（容差防 0 turnover 日抵消）
        assert res_exec["cost"].sum() >= res_base["cost"].sum() - 1e-9


class TestCombined:
    """三能力同时启用 —— 终极集成 smoke."""

    def test_all_three_features_together(self) -> None:
        df = _make_panel(
            n_dates=100, n_stocks=15,
            with_industry=True, with_execution_cols=True,
        )
        engine = BacktestEngine(CostModel())
        exec_m = ExecutionModel(
            kcb_limit_pct=0.20, max_volume_pct=0.05, open_slippage_bps=8,
        )
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=30, retrain_freq=10, top_pct=0.3,
            min_train=25, min_test=5, min_stocks=3,
            neutralize=True,
            model_type="ranker",
            execution=exec_m,
        )
        assert res is not None and not res.empty
        # 三能力的标志列都在
        assert "n_tradable_pre" in res.columns
        # 净收益不全为 NaN
        assert res["net_return"].notna().sum() > 0


class TestSampleWeightDecayCombined:
    """Task #4.4 sample_weight_decay 与其他三能力同时启用."""

    def test_all_four_features_together(self) -> None:
        """sample_weight_decay + neutralize + ranker + execution 集成 smoke."""
        df = _make_panel(
            n_dates=100, n_stocks=15,
            with_industry=True, with_execution_cols=True,
        )
        engine = BacktestEngine(CostModel())
        exec_m = ExecutionModel(
            kcb_limit_pct=0.20, max_volume_pct=0.05, open_slippage_bps=8,
        )
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=30, retrain_freq=10, top_pct=0.3,
            min_train=25, min_test=5, min_stocks=3,
            neutralize=True,
            model_type="ranker",
            execution=exec_m,
            sample_weight_decay=0.005,
        )
        assert res is not None and not res.empty
        assert "n_tradable_pre" in res.columns
        assert res["net_return"].notna().sum() > 0
        # panel attrs 仍带出
        assert "panel" in res.attrs

    def test_sample_weight_decay_alone_runs(self) -> None:
        """仅启用 sample_weight_decay（不带 neutralize / execution）."""
        df = _make_panel(n_dates=80, n_stocks=12)
        engine = BacktestEngine(CostModel())
        res = engine.walk_forward(
            df, ["f1", "f2"], "future_return_5d",
            train_window=25, retrain_freq=5, top_pct=0.3,
            min_train=20, min_test=5, min_stocks=3,
            sample_weight_decay=0.01,
        )
        assert res is not None and not res.empty
