"""稳健性扫描 —— 成本敏感度、窗口扫描、市场状态分段.

回测一次出"过得去的 Sharpe"很容易；真正能上实盘的是**对参数扰动稳定**的策略.
本模块提供三类扫描：

- :meth:`Robustness.cost_sensitivity`：buy/sell 费率与滑点 grid，看 Sharpe 衰减斜率.
- :meth:`Robustness.rolling_window_sweep`：训练窗口 × 重训频率 × top_pct grid.
- :meth:`Robustness.regime_split`：按基准收益分位切牛/震荡/熊三段，
  分别统计策略表现.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

import numpy as np
import pandas as pd

from kss.backtest.cost_model import CostModel
from kss.backtest.engine import BacktestEngine
from kss.backtest.metrics import Metrics

logger = logging.getLogger(__name__)


class Robustness:
    """稳健性扫描指标集合."""

    # ------------------------------------------------------------------ #
    # 成本敏感度
    # ------------------------------------------------------------------ #

    @staticmethod
    def cost_sensitivity(
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str,
        grid: dict[str, list[float]],
        engine_kwargs: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """在 ``(buy_cost, sell_cost, slippage_bps)`` grid 上跑 walk-forward.

        Args:
            factor_df: 因子面板.
            feature_cols: 特征列名.
            label_col: 训练标签列名.
            grid: 形如 ``{"buy_cost": [...], "sell_cost": [...], "slippage_bps": [...]}``；
                未提供的键缺省单点（取默认值）.
            engine_kwargs: 透传给 ``BacktestEngine.walk_forward`` 的关键字参数.

        Returns:
            每个 grid 点一行的 DataFrame，列：
            ``buy_cost / sell_cost / slippage_bps / annual_net / sharpe_net /
            max_dd / calmar / turnover_avg / n``.
        """
        buy_list = grid.get("buy_cost", [0.001])
        sell_list = grid.get("sell_cost", [0.002])
        slip_list = grid.get("slippage_bps", [0.0])
        engine_kwargs = engine_kwargs or {}

        rows: list[dict[str, Any]] = []
        total = len(buy_list) * len(sell_list) * len(slip_list)
        logger.info("成本敏感度扫描：共 %d 个 grid 点", total)

        for bc, sc, slip in itertools.product(buy_list, sell_list, slip_list):
            cost = CostModel(buy_cost=bc, sell_cost=sc, slippage_bps=slip)
            engine = BacktestEngine(cost_model=cost)
            res = engine.walk_forward(
                factor_df=factor_df,
                feature_cols=feature_cols,
                label_col=label_col,
                **engine_kwargs,
            )
            if res is None or res.empty:
                rows.append({
                    "buy_cost": bc, "sell_cost": sc, "slippage_bps": slip,
                    "annual_net": float("nan"), "sharpe_net": float("nan"),
                    "max_dd": float("nan"), "calmar": float("nan"),
                    "turnover_avg": float("nan"), "n": 0,
                })
                continue
            m = Metrics.calc(res["net_return"])
            rows.append({
                "buy_cost": bc,
                "sell_cost": sc,
                "slippage_bps": slip,
                "annual_net": m.get("annual", float("nan")),
                "sharpe_net": m.get("sharpe", float("nan")),
                "max_dd": m.get("max_dd", float("nan")),
                "calmar": m.get("calmar", float("nan")),
                "turnover_avg": float(res["turnover"].mean()),
                "n": m.get("n", 0),
            })

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # 窗口扫描
    # ------------------------------------------------------------------ #

    @staticmethod
    def rolling_window_sweep(
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str,
        train_windows: list[int],
        retrain_freqs: list[int],
        top_pcts: list[float],
        cost_model: CostModel | None = None,
        engine_kwargs: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """在 ``(train_window, retrain_freq, top_pct)`` grid 上跑 walk-forward.

        Args:
            factor_df: 因子面板.
            feature_cols: 特征列名.
            label_col: 训练标签列名.
            train_windows: 训练窗口长度候选.
            retrain_freqs: 重训频率候选.
            top_pcts: 选股比例候选.
            cost_model: 成本模型；缺省用默认.
            engine_kwargs: 透传给 ``walk_forward`` 的其他参数（如 ``min_train``）.

        Returns:
            grid 点一行的 DataFrame，列：``train_window / retrain_freq / top_pct /
            annual_net / sharpe_net / max_dd / calmar / n``.
        """
        cost = cost_model or CostModel()
        engine = BacktestEngine(cost_model=cost)
        engine_kwargs = engine_kwargs or {}

        rows: list[dict[str, Any]] = []
        total = len(train_windows) * len(retrain_freqs) * len(top_pcts)
        logger.info("窗口扫描：共 %d 个 grid 点", total)

        for tw, rf, tp in itertools.product(train_windows, retrain_freqs, top_pcts):
            res = engine.walk_forward(
                factor_df=factor_df,
                feature_cols=feature_cols,
                label_col=label_col,
                train_window=tw,
                retrain_freq=rf,
                top_pct=tp,
                **engine_kwargs,
            )
            if res is None or res.empty:
                rows.append({
                    "train_window": tw, "retrain_freq": rf, "top_pct": tp,
                    "annual_net": float("nan"), "sharpe_net": float("nan"),
                    "max_dd": float("nan"), "calmar": float("nan"), "n": 0,
                })
                continue
            m = Metrics.calc(res["net_return"])
            rows.append({
                "train_window": tw,
                "retrain_freq": rf,
                "top_pct": tp,
                "annual_net": m.get("annual", float("nan")),
                "sharpe_net": m.get("sharpe", float("nan")),
                "max_dd": m.get("max_dd", float("nan")),
                "calmar": m.get("calmar", float("nan")),
                "n": m.get("n", 0),
            })

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # 市场状态分段
    # ------------------------------------------------------------------ #

    @staticmethod
    def regime_split(
        result_df: pd.DataFrame,
        benchmark_returns: pd.Series,
        n_regimes: int = 3,
    ) -> pd.DataFrame:
        """按基准日收益分位切分市场状态，分段统计策略表现.

        - ``n_regimes=2``：弱（基准日收益 ≤ 中位数）/ 强（> 中位数）
        - ``n_regimes=3``：熊（下 1/3）/ 震荡（中 1/3）/ 牛（上 1/3）

        Args:
            result_df: ``walk_forward`` 返回的回测结果，需含
                ``trade_date / gross_return / net_return``.
            benchmark_returns: 对齐到策略日历的基准日收益（``Benchmark.aligned_returns``）.
            n_regimes: 分段数，2 或 3.

        Returns:
            按 regime 分组的统计 DataFrame，列：
            ``regime / n_days / strat_annual / strat_sharpe / bench_annual / alpha``.
        """
        if result_df.empty or benchmark_returns.empty:
            return pd.DataFrame()
        if n_regimes not in (2, 3):
            raise ValueError(f"n_regimes 仅支持 2 或 3，收到: {n_regimes}")

        merged = result_df.copy()
        merged["trade_date"] = pd.to_datetime(merged["trade_date"])
        bench = benchmark_returns.copy()
        bench.index = pd.to_datetime(bench.index)
        merged = merged.merge(
            bench.rename("bench_return"),
            left_on="trade_date",
            right_index=True,
            how="left",
        )
        merged = merged.dropna(subset=["bench_return", "net_return"])
        if merged.empty:
            return pd.DataFrame()

        if n_regimes == 2:
            labels = ["弱", "强"]
        else:
            labels = ["熊", "震荡", "牛"]
        merged["regime"] = pd.qcut(
            merged["bench_return"].rank(method="first"),
            q=n_regimes,
            labels=labels,
        )

        rows: list[dict[str, Any]] = []
        for label, sub in merged.groupby("regime", observed=True):
            r_strat = sub["net_return"]
            r_bench = sub["bench_return"]
            m_strat = Metrics.calc(r_strat)
            m_bench = Metrics.calc(r_bench)
            rows.append({
                "regime": str(label),
                "n_days": len(sub),
                "strat_annual": m_strat.get("annual", float("nan")),
                "strat_sharpe": m_strat.get("sharpe", float("nan")),
                "bench_annual": m_bench.get("annual", float("nan")),
                "alpha": m_strat.get("annual", 0.0) - m_bench.get("annual", 0.0),
            })

        return pd.DataFrame(rows)
