"""LightGBM 回归模型封装."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import yaml

from kss.models.base import ModelBase

logger = logging.getLogger(__name__)


# 默认配置：从项目 settings.yaml 加载
_DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "metric": "rmse",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "random_state": 42,
}

_DEFAULT_NUM_BOOST_ROUND: int = 500
_DEFAULT_EARLY_STOPPING_ROUNDS: int = 50


def _load_settings() -> dict[str, Any]:
    """尝试从项目配置读取 LightGBM 默认参数."""
    try:
        # 定位到 kss/config/settings.yaml（相对于本文件向上两级）
        cfg_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            models_cfg = cfg.get("models", {})
            lgb_cfg = models_cfg.get("lightgbm", {})
            return dict(lgb_cfg)
    except Exception as exc:
        logger.debug("加载 LightGBM settings 失败: %s", exc)
    return {}


# 初始化时尝试覆盖默认参数
_settings = _load_settings()
if _settings:
    for k, v in _settings.items():
        if k in _DEFAULT_PARAMS or k in ("num_boost_round", "early_stopping_rounds"):
            if k == "num_boost_round":
                _DEFAULT_NUM_BOOST_ROUND = int(v)
            elif k == "early_stopping_rounds":
                _DEFAULT_EARLY_STOPPING_ROUNDS = int(v)
            else:
                _DEFAULT_PARAMS[k] = v


class LightGBMModel(ModelBase):
    """基于 LightGBM 的回归模型.

    封装了训练、预测、早停、特征重要性提取与序列化能力。
    """

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        """初始化 LightGBM 模型.

        Args:
            params: LightGBM 参数字典；若为 ``None`` 则使用项目配置中的默认值。
        """
        self.params = {**_DEFAULT_PARAMS, **(params or {})}
        self._booster: lgb.Booster | None = None
        self._feature_names: list[str] | None = None
        self._num_boost_round: int = _DEFAULT_NUM_BOOST_ROUND
        self._early_stopping_rounds: int = _DEFAULT_EARLY_STOPPING_ROUNDS

    # ------------------------------------------------------------------ #
    # 训练
    # ------------------------------------------------------------------ #

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        *,
        valid_X: pd.DataFrame | np.ndarray | None = None,
        valid_y: pd.Series | np.ndarray | None = None,
        **kwargs: Any,
    ) -> None:
        """训练模型.

        若提供验证集则自动启用早停。

        Args:
            X: 训练特征.
            y: 训练目标.
            valid_X: 验证特征（可选）.
            valid_y: 验证目标（可选）.
            **kwargs: 传递给 ``lgb.train`` 的额外参数.
        """
        train_data = lgb.Dataset(X, label=y)

        valid_sets: list[lgb.Dataset] = [train_data]
        valid_names: list[str] = ["train"]

        if valid_X is not None and valid_y is not None:
            valid_data = lgb.Dataset(valid_X, label=valid_y, reference=train_data)
            valid_sets.append(valid_data)
            valid_names.append("valid")

        callbacks: list[Any] = []
        if len(valid_sets) > 1:
            callbacks.append(
                lgb.early_stopping(
                    stopping_rounds=kwargs.pop(
                        "early_stopping_rounds", self._early_stopping_rounds
                    ),
                    verbose=False,
                )
            )

        num_boost_round = kwargs.pop("num_boost_round", self._num_boost_round)

        self._booster = lgb.train(
            self.params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
            **kwargs,
        )

        if isinstance(X, pd.DataFrame):
            self._feature_names = list(X.columns)
        else:
            self._feature_names = None

    # ------------------------------------------------------------------ #
    # 预测
    # ------------------------------------------------------------------ #

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """生成预测值.

        Args:
            X: 特征矩阵或 DataFrame.

        Returns:
            预测结果数组.

        Raises:
            RuntimeError: 模型尚未训练或加载.
        """
        if self._booster is None:
            raise RuntimeError("模型尚未训练或加载，请先调用 fit() 或 load().")
        return self._booster.predict(X)

    # ------------------------------------------------------------------ #
    # 特征重要性
    # ------------------------------------------------------------------ #

    def feature_importance(self) -> pd.DataFrame | None:
        """返回特征重要性.

        Returns:
            包含 ``feature`` 与 ``importance`` 列的 DataFrame；
            若模型不可用或未训练则返回 ``None``。
        """
        if self._booster is None:
            return None

        importance = self._booster.feature_importance(importance_type="gain")
        names = self._feature_names or [
            f"feature_{i}" for i in range(len(importance))
        ]
        df = pd.DataFrame({
            "feature": names,
            "importance": importance,
        }).sort_values("importance", ascending=False)
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #

    def save(self, path: str) -> None:
        """使用 joblib 保存模型.

        Args:
            path: 目标文件路径.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "booster": self._booster,
                "params": self.params,
                "feature_names": self._feature_names,
                "num_boost_round": self._num_boost_round,
                "early_stopping_rounds": self._early_stopping_rounds,
            },
            path,
        )

    def load(self, path: str) -> None:
        """使用 joblib 加载模型.

        Args:
            path: 模型文件路径.

        Raises:
            FileNotFoundError: 文件不存在.
        """
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(f"模型文件不存在: {path}")

        data = joblib.load(path)
        self._booster = data.get("booster")
        self.params = data.get("params", self.params)
        self._feature_names = data.get("feature_names")
        self._num_boost_round = data.get("num_boost_round", _DEFAULT_NUM_BOOST_ROUND)
        self._early_stopping_rounds = data.get(
            "early_stopping_rounds", _DEFAULT_EARLY_STOPPING_ROUNDS
        )

    # ------------------------------------------------------------------ #
    # 参数查看
    # ------------------------------------------------------------------ #

    def get_params(self) -> dict[str, Any]:
        """返回当前参数字典副本.

        Returns:
            当前 LightGBM 参数字典.
        """
        return self.params.copy()
