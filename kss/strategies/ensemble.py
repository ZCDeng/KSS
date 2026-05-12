"""多周期/多模型集成 —— 对截面打分做秩归一加权融合.

设计理念：直接平均不同模型的 ``pred_score`` 会被 scale 主导（LightGBM 量纲与
LSTM logits 量纲不一致）；先在每个截面内对每个模型的打分做 rank-pct，再加权
平均，保留信号方向，抹平 scale 差异.
"""

from __future__ import annotations

import logging
from typing import Mapping

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MultiPeriodEnsemble:
    """多周期模型集成器.

    用例：5d / 10d / 20d 三个 LightGBM 模型在同一截面打分，按 ``period_weights``
    加权融合为单一 ``combined_score``，下游 ``select_top`` 直接消费.
    """

    def __init__(self, period_weights: Mapping[int, float]) -> None:
        """初始化集成器.

        Args:
            period_weights: 周期 → 权重映射；权重和会自动归一化到 1.

        Raises:
            ValueError: 权重为空或全为 0.
        """
        if not period_weights:
            raise ValueError("period_weights 不能为空")
        total = sum(period_weights.values())
        if total <= 0:
            raise ValueError(f"period_weights 总和必须 > 0，收到: {dict(period_weights)}")
        self.weights = {int(k): float(v) / total for k, v in period_weights.items()}

    def combine_scores(self, scores: Mapping[int, pd.Series]) -> pd.Series:
        """融合多周期截面打分.

        Args:
            scores: 周期 → 打分序列（Index 为 symbol 或 row id）；
                必须共享同一 Index，缺周期的会被跳过.

        Returns:
            按权重加权的融合打分；保留输入 Index 顺序.

        Raises:
            ValueError: 所有提供的周期都未在 ``period_weights`` 中.
        """
        valid = {p: s for p, s in scores.items() if p in self.weights and not s.empty}
        if not valid:
            raise ValueError(
                f"提供的周期 {list(scores.keys())} 与权重周期 {list(self.weights.keys())} 不交集"
            )

        # 截面 rank-pct，抹平不同模型的 scale 差异
        ranks: list[pd.Series] = []
        used_weights: list[float] = []
        for period, s in valid.items():
            ranks.append(s.rank(pct=True))
            used_weights.append(self.weights[period])

        # 归一化所选权重（防止用户只提供部分周期时权重和 < 1）
        w_total = sum(used_weights)
        used_weights = [w / w_total for w in used_weights]

        combined = sum(r * w for r, w in zip(ranks, used_weights))
        return combined.rename("combined_score")


class BackendEnsemble:
    """跨后端（LightGBM × DL）模型集成器.

    用于在 daily_forecast 中把 LGB 与 LSTM/Transformer 的打分用秩归一加权融合.
    """

    def __init__(self, lgb_weight: float = 0.6, dl_weight: float = 0.4) -> None:
        """初始化.

        Args:
            lgb_weight: LightGBM 权重.
            dl_weight: DL 权重；DL 缺失时自动把全部权重给 LGB.
        """
        total = lgb_weight + dl_weight
        if total <= 0:
            raise ValueError("lgb_weight + dl_weight 必须 > 0")
        self.lgb_weight = lgb_weight / total
        self.dl_weight = dl_weight / total

    def combine(
        self,
        lgb_scores: pd.Series,
        dl_scores: pd.Series | None,
    ) -> pd.Series:
        """融合 LGB / DL 打分.

        Args:
            lgb_scores: LightGBM 打分（Index 为 symbol）.
            dl_scores: DL 打分；``None`` 时退化为 LGB 单跑.

        Returns:
            融合打分（``combined_score`` 命名）.
        """
        if dl_scores is None or dl_scores.empty:
            return lgb_scores.rank(pct=True).rename("combined_score")
        # 对齐 Index，DL 漏掉的股票按 0.5（中性）填充
        lgb_rank = lgb_scores.rank(pct=True)
        dl_rank = dl_scores.rank(pct=True).reindex(lgb_rank.index).fillna(0.5)
        combined = lgb_rank * self.lgb_weight + dl_rank * self.dl_weight
        return combined.rename("combined_score")
