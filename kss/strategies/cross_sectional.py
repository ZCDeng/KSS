"""横截面选股策略 —— Walk-forward 纯多头 Top-Pct."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from kss.backtest import BacktestEngine, CostModel
from kss.models.base import ModelBase
from kss.strategies.base import StrategyBase


class CrossSectionalStrategy(StrategyBase):
    """横截面选股策略.

    每日对全股票池打分，选出前 ``top_pct`` 股票构建等权组合；
    支持 Walk-forward 滚动回测。
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        train_window: int = 120,
        retrain_freq: int = 5,
        top_pct: float = 0.2,
    ) -> None:
        """初始化策略.

        Args:
            cost_model: 交易成本模型；若为 ``None`` 则使用默认费率.
            train_window: 训练窗口长度（交易日数）.
            retrain_freq: 重训练频率（每 N 个交易日重训一次）.
            top_pct: 选股比例（仅做多前 top_pct），默认 0.2（20%）.
        """
        self.cost_model = cost_model or CostModel()
        self.train_window = train_window
        self.retrain_freq = retrain_freq
        self.top_pct = top_pct
        self._engine = BacktestEngine(cost_model=self.cost_model)

    # ------------------------------------------------------------------ #
    # 信号生成
    # ------------------------------------------------------------------ #

    def generate_signals(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        date: str | pd.Timestamp | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """为指定日期生成横截面选股信号.

        Args:
            factor_df: 包含因子与行情数据的 DataFrame，需含 ``trade_date``、``symbol`` 列.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例.
            date: 目标日期；若为 ``None`` 则使用 ``factor_df`` 中最新日期.
            **kwargs: 预留扩展参数.

        Returns:
            包含 ``symbol``、``pred_score``、``daily_rank``、``signal`` 列的 DataFrame，
            仅保留被选中（Top-Pct）的股票。
        """
        if date is None:
            date = factor_df["trade_date"].max()
        else:
            date = pd.Timestamp(date)

        day_df = factor_df[factor_df["trade_date"] == date].copy()
        if day_df.empty:
            raise ValueError(f"指定日期 {date} 无可用数据.")

        scored, top_mask = self.select_top(day_df, feature_cols, model, self.top_pct)
        signals = scored.loc[top_mask, ["symbol", "pred_score", "daily_rank"]].copy()
        signals["signal"] = "buy"
        return signals.reset_index(drop=True)

    def select_top(
        self,
        day_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        top_pct: float = 0.2,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """对单日全股票池打分并返回 Top 股票掩码.

        Args:
            day_df: 单日股票数据 DataFrame.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例.
            top_pct: 选股比例.

        Returns:
            ``(scored_df, mask)`` 元组：``scored_df`` 是原 ``day_df`` 的副本并附加
            ``pred_score``、``daily_rank`` 列；``mask`` 为布尔 ``pd.Series``，
            ``True`` 表示该股票被选中。返回带分数的副本以便上层直接读取打分。
        """
        if day_df.empty:
            return day_df.copy(), pd.Series(dtype=bool)

        # 确保特征列存在
        missing = set(feature_cols) - set(day_df.columns)
        if missing:
            raise KeyError(f"day_df 缺少特征列: {missing}")

        scored = day_df.copy()
        scored["pred_score"] = model.predict(scored[feature_cols].values)
        scored["daily_rank"] = scored["pred_score"].rank(pct=True)
        mask = scored["daily_rank"] >= (1 - top_pct)
        return scored, mask

    # ------------------------------------------------------------------ #
    # 回测
    # ------------------------------------------------------------------ #

    def backtest(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        label_col: str,
        **kwargs: Any,
    ) -> pd.DataFrame | None:
        """执行 Walk-forward 滚动回测.

        委托给 :class:`~kss.backtest.engine.BacktestEngine` 的
        :meth:`~kss.backtest.engine.BacktestEngine.walk_forward` 方法。

        Args:
            factor_df: 包含因子、目标变量、``trade_date``、``symbol`` 的 DataFrame.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例（当前 walk_forward 内部自训，保留此参数用于接口统一）.
            label_col: 训练标签列名（如 ``future_return_5d``）.
            **kwargs: 回测覆盖参数，可传入 ``train_window``、``retrain_freq``、
                ``top_pct``、``min_train``、``min_test``、``min_stocks`` 等.

        Returns:
            回测结果 DataFrame；若结果为空则返回 ``None``。
        """
        # 当前 BacktestEngine.walk_forward 内部自行训练模型，
        # 但未来可扩展为接受外部 model 参数进行纯预测回测。
        return self._engine.walk_forward(
            factor_df=factor_df,
            feature_cols=feature_cols,
            label_col=label_col,
            train_window=kwargs.pop("train_window", self.train_window),
            retrain_freq=kwargs.pop("retrain_freq", self.retrain_freq),
            top_pct=kwargs.pop("top_pct", self.top_pct),
            min_train=kwargs.pop("min_train", 300),
            min_test=kwargs.pop("min_test", 30),
            min_stocks=kwargs.pop("min_stocks", 10),
        )
