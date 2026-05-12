"""策略抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from kss.models.base import ModelBase


class StrategyBase(ABC):
    """所有交易策略的抽象基类.

    子类需实现 ``generate_signals`` 与 ``backtest`` 方法，
    分别负责信号生成与回测执行。
    """

    @abstractmethod
    def generate_signals(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """生成交易信号.

        Args:
            factor_df: 包含因子、行情与目标变量的 DataFrame.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例.
            **kwargs: 策略特定参数（如 ``date`` 指定单日扫描）.

        Returns:
            信号结果 DataFrame.
        """
        pass

    @abstractmethod
    def backtest(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """执行回测.

        Args:
            factor_df: 包含因子、行情与目标变量的 DataFrame.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例.
            **kwargs: 回测参数（如 ``label_col``、``train_window`` 等）.

        Returns:
            回测结果 DataFrame.
        """
        pass
