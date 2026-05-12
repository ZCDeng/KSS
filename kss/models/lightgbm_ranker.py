"""LightGBM 排序模型 —— 用 lambdarank 目标学习截面 ranking.

与 :class:`~kss.models.lightgbm_model.LightGBMModel` 的关系：

- ``LightGBMModel``: ``objective="regression"`` + MSE loss，学预测**收益值**.
  在弱 IC 环境（A 股因子 IC 量级 0.02）下，MSE 优化被噪声主导
  → 训练-测试目标错配.
- ``LightGBMRanker``: ``objective="lambdarank"`` + NDCG，直接学**排序**.
  把每个 trade_date 视为一个 query group，截面排名作为 label，
  无视 future_return 的绝对值，只关心相对排序.

关键差异：

- :meth:`fit` 必须接 ``trade_dates`` 参数（每个 trade_date 一组）
- label 需要从 future_return → 截面整数 rank（lambdarank 要求 int label）
- :meth:`predict` 输出还是 float score，下游 ``.rank()`` 提取相对名次

参考：

- https://lightgbm.readthedocs.io/en/latest/Parameters.html#objective
- López de Prado (2018) "Advances in Financial ML" Ch.7 关于 ranking 任务
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from kss.models.base import ModelBase

logger = logging.getLogger(__name__)

_DEFAULT_RANKER_PARAMS: dict[str, Any] = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "ndcg_eval_at": [5, 10, 20],
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "random_state": 42,
    # 6 档 rank 的 gain 表（指数递增，强化对头部档的关注）
    "label_gain": [0, 1, 3, 7, 15, 31],
}


class LightGBMRanker(ModelBase):
    """LightGBM lambdarank 排序模型.

    :meth:`fit` 必须接收 ``trade_dates`` 参数（与 X 同长度的日期序列），
    用于构造 group. label 自动从连续值 future_return 转成
    ``0..(n_bins-1)`` 的整数 rank.
    """

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        n_rank_bins: int = 6,
        num_boost_round: int = 500,
        early_stopping_rounds: int = 50,
    ) -> None:
        """初始化.

        Args:
            params: lightgbm 参数；缺省用 :data:`_DEFAULT_RANKER_PARAMS`.
                若传入自定义 ``label_gain``，长度需 >= ``n_rank_bins``，
                否则会被自动截断 / 补全.
            n_rank_bins: 把 future_return 离散到几个 rank 档
                （与 ``label_gain`` 长度对齐）.
            num_boost_round: 训练轮数.
            early_stopping_rounds: 早停轮数（仅当传入验证集时生效）.
        """
        self.n_rank_bins = int(n_rank_bins)
        if self.n_rank_bins < 2:
            raise ValueError(
                f"n_rank_bins 必须 >= 2，当前 {self.n_rank_bins}"
            )

        merged = {**_DEFAULT_RANKER_PARAMS, **(params or {})}
        # 让 label_gain 长度与 n_rank_bins 对齐
        gains = list(merged.get("label_gain", _DEFAULT_RANKER_PARAMS["label_gain"]))
        if len(gains) < self.n_rank_bins:
            # 用指数外推补齐
            last = gains[-1] if gains else 1
            while len(gains) < self.n_rank_bins:
                last = last * 2 + 1
                gains.append(last)
        elif len(gains) > self.n_rank_bins:
            gains = gains[: self.n_rank_bins]
        merged["label_gain"] = gains

        self.params = merged
        self.num_boost_round = int(num_boost_round)
        self.early_stopping_rounds = int(early_stopping_rounds)
        self._booster: lgb.Booster | None = None
        self._feature_names: list[str] | None = None

    # ------------------------------------------------------------------ #
    # 训练
    # ------------------------------------------------------------------ #

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        *,
        trade_dates: np.ndarray | pd.Series | None = None,
        valid_X: pd.DataFrame | np.ndarray | None = None,
        valid_y: pd.Series | np.ndarray | None = None,
        valid_dates: np.ndarray | pd.Series | None = None,
        sample_weight: np.ndarray | pd.Series | None = None,
        **kwargs: Any,
    ) -> None:
        """训练排序模型.

        Args:
            X: 特征矩阵 ``(N, n_features)``.
            y: 目标变量（连续值，会被自动转成 rank int label）.
            trade_dates: 与 X 同长度的交易日序列，用于构造 group.
                **必需** —— 没有就抛 :class:`ValueError`.
            valid_X / valid_y / valid_dates: 验证集（可选，触发 early stopping）.
            sample_weight: 与 X 同长度的训练样本权重；``None`` = 等权（默认，向后兼容）.
                内部会跟随 trade_date 排序一同重排. 用于 DDG-DA 轻量版"近期样本加权".
            **kwargs: 传给 :func:`lgb.train` 的额外参数.

        Raises:
            ValueError: ``trade_dates`` 缺失、长度不匹配，``sample_weight`` 长度不匹配，
                或验证集参数不完整.
        """
        if trade_dates is None:
            raise ValueError(
                "LightGBMRanker.fit 必须传 trade_dates 用于构造 group"
            )

        X_arr, y_arr, dates_arr, feature_names = self._prepare_arrays(
            X, y, trade_dates
        )

        # 按 trade_date 排序，确保 group 在 X 中连续
        sort_idx = np.argsort(dates_arr, kind="stable")
        X_sorted = X_arr[sort_idx]
        y_sorted = y_arr[sort_idx]
        dates_sorted = dates_arr[sort_idx]

        # 校验 + 重排 sample_weight（保持与 X_sorted 对齐）
        sw_sorted: np.ndarray | None = None
        if sample_weight is not None:
            sw_arr = np.asarray(
                sample_weight.values
                if isinstance(sample_weight, pd.Series)
                else sample_weight
            ).reshape(-1).astype(float)
            if len(sw_arr) != len(X_arr):
                raise ValueError(
                    "sample_weight 长度必须与 X 一致："
                    f"sample_weight={len(sw_arr)}, X={len(X_arr)}"
                )
            sw_sorted = sw_arr[sort_idx]

        label = self._to_rank_label(y_sorted, dates_sorted)
        group = self._group_sizes(dates_sorted)

        train_data = lgb.Dataset(
            X_sorted, label=label, group=group, weight=sw_sorted,
        )
        valid_sets: list[lgb.Dataset] = [train_data]
        valid_names: list[str] = ["train"]

        callbacks: list[Any] = []

        # 处理验证集 —— 三者必须同时出现或同时缺失
        valid_provided = [
            v is not None for v in (valid_X, valid_y, valid_dates)
        ]
        if any(valid_provided):
            if not all(valid_provided):
                raise ValueError(
                    "valid_X / valid_y / valid_dates 必须同时提供"
                )
            vX_arr, vy_arr, vdates_arr, _ = self._prepare_arrays(
                valid_X, valid_y, valid_dates
            )
            v_sort = np.argsort(vdates_arr, kind="stable")
            vX_sorted = vX_arr[v_sort]
            vy_sorted = vy_arr[v_sort]
            vdates_sorted = vdates_arr[v_sort]

            v_label = self._to_rank_label(vy_sorted, vdates_sorted)
            v_group = self._group_sizes(vdates_sorted)

            valid_data = lgb.Dataset(
                vX_sorted,
                label=v_label,
                group=v_group,
                reference=train_data,
            )
            valid_sets.append(valid_data)
            valid_names.append("valid")

            callbacks.append(
                lgb.early_stopping(
                    stopping_rounds=kwargs.pop(
                        "early_stopping_rounds", self.early_stopping_rounds
                    ),
                    verbose=False,
                )
            )

        num_boost_round = kwargs.pop("num_boost_round", self.num_boost_round)

        self._booster = lgb.train(
            self.params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
            **kwargs,
        )

        self._feature_names = feature_names

    # ------------------------------------------------------------------ #
    # 预测
    # ------------------------------------------------------------------ #

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """生成排序分数.

        Args:
            X: 特征矩阵.

        Returns:
            一维 float 分数数组；下游可对每个 trade_date 内部 ``.rank()``
            取相对名次.

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
            包含 ``feature`` / ``importance`` 列的 DataFrame；
            模型未训练则返回 ``None``.
        """
        if self._booster is None:
            return None

        importance = self._booster.feature_importance(importance_type="gain")
        names = self._feature_names or [
            f"feature_{i}" for i in range(len(importance))
        ]
        df = pd.DataFrame(
            {"feature": names, "importance": importance}
        ).sort_values("importance", ascending=False)
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #

    def save(self, path: str) -> None:
        """使用 joblib 序列化整个 ranker 状态."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "booster": self._booster,
                "params": self.params,
                "feature_names": self._feature_names,
                "n_rank_bins": self.n_rank_bins,
                "num_boost_round": self.num_boost_round,
                "early_stopping_rounds": self.early_stopping_rounds,
            },
            path,
        )

    def load(self, path: str) -> None:
        """从磁盘加载 ranker."""
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(f"模型文件不存在: {path}")

        data = joblib.load(path)
        self._booster = data.get("booster")
        self.params = data.get("params", self.params)
        self._feature_names = data.get("feature_names")
        self.n_rank_bins = int(data.get("n_rank_bins", self.n_rank_bins))
        self.num_boost_round = int(
            data.get("num_boost_round", self.num_boost_round)
        )
        self.early_stopping_rounds = int(
            data.get("early_stopping_rounds", self.early_stopping_rounds)
        )

    # ------------------------------------------------------------------ #
    # 参数查看
    # ------------------------------------------------------------------ #

    def get_params(self) -> dict[str, Any]:
        """返回当前 LightGBM 参数字典副本."""
        return self.params.copy()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    @staticmethod
    def _prepare_arrays(
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        trade_dates: np.ndarray | pd.Series,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str] | None]:
        """统一把入参转成 numpy 数组并校验长度.

        Returns:
            (X_arr, y_arr, dates_arr, feature_names_or_None)
        """
        feature_names: list[str] | None = None
        if isinstance(X, pd.DataFrame):
            feature_names = list(X.columns)
            X_arr = X.to_numpy()
        else:
            X_arr = np.asarray(X)

        y_arr = np.asarray(y).reshape(-1)
        dates_arr = np.asarray(
            trade_dates.values
            if isinstance(trade_dates, pd.Series)
            else trade_dates
        ).reshape(-1)

        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(-1, 1)

        if not (len(X_arr) == len(y_arr) == len(dates_arr)):
            raise ValueError(
                "X / y / trade_dates 长度必须一致："
                f"X={len(X_arr)}, y={len(y_arr)}, dates={len(dates_arr)}"
            )

        return X_arr, y_arr, dates_arr, feature_names

    def _to_rank_label(
        self,
        y: np.ndarray,
        trade_dates: np.ndarray,
    ) -> np.ndarray:
        """把连续 future_return 转成截面 rank 整数 label.

        每个 trade_date 内做 rank（``pct=True``），然后离散到
        ``[0, n_rank_bins-1]``. 例如 ``n_rank_bins=6`` 时
        rank 最低 → 0，rank 最高 → 5.
        """
        s = pd.Series(y)
        groups = pd.Series(trade_dates)
        pct_rank = s.groupby(groups).rank(method="average", pct=True)
        # rank=1.0 (最高) → n_bins-1；rank≈0 → 0
        # 用 floor + clip，确保 1.0 也落到 n_bins-1
        bins = np.floor(pct_rank.to_numpy() * self.n_rank_bins).astype(int)
        bins = np.clip(bins, 0, self.n_rank_bins - 1)
        return bins

    @staticmethod
    def _group_sizes(trade_dates: np.ndarray) -> np.ndarray:
        """从 trade_dates 序列（已按日期排序）构造 LGBM group size 数组.

        Returns:
            形如 ``[n_day1, n_day2, ...]``，``sum == len(trade_dates)``.
        """
        if len(trade_dates) == 0:
            return np.array([], dtype=np.int64)
        # 假设 trade_dates 已经连续分组（同一日期相邻），
        # 直接找邻接边界比 value_counts 更稳健也更快.
        arr = np.asarray(trade_dates)
        # 找出相邻不相等的位置 → 段边界
        if arr.dtype.kind in ("U", "O"):
            change = arr[1:] != arr[:-1]
        else:
            change = arr[1:] != arr[:-1]
        boundaries = np.flatnonzero(change) + 1
        starts = np.concatenate(([0], boundaries))
        ends = np.concatenate((boundaries, [len(arr)]))
        return (ends - starts).astype(np.int64)
