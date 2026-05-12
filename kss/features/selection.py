"""特征筛选 —— 基于时间分折 LightGBM gain 的稳定特征选择.

不在每个 walk_forward 窗口内重选（会引入 selection leak），改为在
walk_forward **之前一次性**用历史数据筛一组稳定特征，下传给所有窗口.
"""

from __future__ import annotations

import logging
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FeatureSelector:
    """特征筛选器."""

    @staticmethod
    def stable_features(
        panel: pd.DataFrame,
        candidates: list[str],
        label_col: str,
        date_col: str = "trade_date",
        n_folds: int = 4,
        top_k: int | None = None,
        gain_pct: float = 0.85,
        lgb_params: dict[str, Any] | None = None,
        random_state: int = 42,
    ) -> list[str]:
        """在时间分折上跑 LightGBM，按平均 gain 与稳定性筛特征.

        分折逻辑：按 ``date_col`` 时间序排列，均分 ``n_folds`` 段，
        每段都作为一次训练集打分；汇总每个特征的 ``mean(gain) × stability``，
        其中 ``stability = 1 - cv(gain)`` 越大越好.

        Args:
            panel: 含 ``date_col`` / 所有 ``candidates`` / ``label_col`` 的面板.
            candidates: 候选特征列名.
            label_col: 训练标签列名（如 ``future_return_5d``）.
            date_col: 时间列.
            n_folds: 时间分折数（≥ 2）.
            top_k: 直接取排名前 K 个特征；若提供，则忽略 ``gain_pct``.
            gain_pct: 累计 gain 截断比例（0~1），取累计 gain 占总 gain 比例
                达到该阈值的最小特征集；默认 0.85.
            lgb_params: 透传 LightGBM 训练参数；缺省与 engine._train_model 同源.
            random_state: 随机种子.

        Returns:
            选中的特征列名列表（按平均 gain 降序）.

        Raises:
            ValueError: 候选列缺失或 ``n_folds < 2``.
        """
        if n_folds < 2:
            raise ValueError(f"n_folds 必须 ≥ 2，收到: {n_folds}")
        missing = set(candidates + [label_col, date_col]) - set(panel.columns)
        if missing:
            raise ValueError(f"panel 缺少列: {missing}")

        # 默认参数：与 engine._train_model 同源，便于结果可移植
        params = lgb_params or {
            "objective": "regression",
            "metric": "rmse",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
            "random_state": random_state,
        }

        clean = panel.dropna(subset=[label_col] + candidates).sort_values(date_col)
        if clean.empty:
            return []

        unique_dates = sorted(clean[date_col].unique())
        if len(unique_dates) < n_folds:
            logger.warning(
                "唯一交易日 %d < n_folds=%d，无法分折，直接用全样本",
                len(unique_dates),
                n_folds,
            )
            n_folds = max(1, len(unique_dates) - 1)
            if n_folds < 2:
                n_folds = 1

        fold_size = len(unique_dates) // n_folds
        gain_records: dict[str, list[float]] = {c: [] for c in candidates}

        for fold_idx in range(n_folds):
            start = fold_idx * fold_size
            end = (fold_idx + 1) * fold_size if fold_idx < n_folds - 1 else len(unique_dates)
            fold_dates = set(unique_dates[start:end])
            sub = clean[clean[date_col].isin(fold_dates)]
            if len(sub) < 50:
                continue
            X = sub[candidates].values
            y = sub[label_col].values
            train_data = lgb.Dataset(X, y)
            booster = lgb.train(
                params,
                train_data,
                num_boost_round=200,
            )
            gain = booster.feature_importance(importance_type="gain")
            for col, g in zip(candidates, gain):
                gain_records[col].append(float(g))

        # 汇总：mean_gain × stability
        rows: list[dict[str, Any]] = []
        for col, gains in gain_records.items():
            if not gains:
                rows.append({"feature": col, "mean_gain": 0.0, "stability": 0.0, "score": 0.0})
                continue
            arr = np.array(gains, dtype=float)
            mean_gain = float(arr.mean())
            std_gain = float(arr.std())
            cv = std_gain / mean_gain if mean_gain > 0 else float("inf")
            stability = max(0.0, 1.0 - cv)  # 变异系数越低越稳
            rows.append({
                "feature": col,
                "mean_gain": mean_gain,
                "stability": stability,
                "score": mean_gain * stability,
            })

        ranked = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

        if top_k is not None and top_k > 0:
            selected = ranked.head(top_k)["feature"].tolist()
        else:
            total = ranked["mean_gain"].sum()
            if total <= 0:
                # 全 0 退化：返回原始候选
                return list(candidates)
            cumulative = (ranked["mean_gain"].cumsum() / total).values
            # 找到第一个累计 gain ≥ gain_pct 的 index
            cutoff_idx = int(np.searchsorted(cumulative, gain_pct) + 1)
            cutoff_idx = max(1, min(cutoff_idx, len(ranked)))
            selected = ranked.head(cutoff_idx)["feature"].tolist()

        logger.info(
            "特征筛选：候选 %d / 选中 %d / gain_pct=%.2f",
            len(candidates),
            len(selected),
            gain_pct,
        )
        return selected
