"""模型抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd


class ModelBase(ABC):
    """所有预测模型的抽象基类.

    子类必须实现 ``fit``、``predict``、``save`` 和 ``load`` 方法。
    ``feature_importance`` 为可选方法，用于输出特征重要性。
    """

    @abstractmethod
    def fit(self, X: Any, y: Any, **kwargs: Any) -> None:
        """训练模型.

        Args:
            X: 特征矩阵或 DataFrame.
            y: 目标变量.
            **kwargs: 额外训练参数（如验证集、回调等）.
        """
        pass

    @abstractmethod
    def predict(self, X: Any) -> np.ndarray:
        """生成预测值.

        Args:
            X: 特征矩阵或 DataFrame.

        Returns:
            预测结果数组，形状为 ``(n_samples,)``.
        """
        pass

    @abstractmethod
    def save(self, path: str) -> None:
        """序列化模型到磁盘.

        Args:
            path: 保存路径.
        """
        pass

    @abstractmethod
    def load(self, path: str) -> None:
        """从磁盘反序列化模型.

        Args:
            path: 模型文件路径.
        """
        pass

    def feature_importance(self) -> pd.DataFrame | None:
        """返回特征重要性（可选）.

        Returns:
            包含 ``feature`` 和 ``importance`` 列的 DataFrame；
            若模型不支持则返回 ``None``。
        """
        return None
