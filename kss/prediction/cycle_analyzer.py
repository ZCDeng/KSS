"""周期分析器 —— 多周期（5日/10日/20日）趋势预测与标签化."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from kss.models.base import ModelBase


class CycleAnalyzer:
    """多周期趋势分析器.

    同时对 5 日、10 日、20 日三个周期进行预测，
    并基于配置阈值输出人类可读的趋势标签。
    """

    # 默认阈值（与 config/settings.yaml 中的 prediction.thresholds 保持一致）
    _DEFAULT_THRESHOLDS: dict[str, float] = {
        "strong_up": 0.05,
        "mild_up": 0.02,
        "neutral": -0.02,
        "mild_down": -0.05,
    }

    def __init__(
        self,
        models_dict: dict[int, ModelBase],
        thresholds: dict[str, float] | None = None,
    ) -> None:
        """初始化周期分析器.

        Args:
            models_dict: 周期-模型映射字典，例如 ``{5: model_5d, 10: model_10d, 20: model_20d}``。
            thresholds: 趋势标签阈值字典；若为 ``None`` 则使用默认阈值。
        """
        self.models_dict = models_dict
        self.thresholds = thresholds or self._DEFAULT_THRESHOLDS.copy()

    # ------------------------------------------------------------------ #
    # 核心预测
    # ------------------------------------------------------------------ #

    def predict(
        self,
        latest_factors: pd.DataFrame,
        feature_cols: list[str],
    ) -> dict[int, pd.DataFrame]:
        """对多周期同时进行预测.

        Args:
            latest_factors: 最新因子 DataFrame（单日期或多股票）。
            feature_cols: 模型输入特征列名列表。

        Returns:
            字典，键为周期（5/10/20），值为预测结果 DataFrame，
            每行含 ``symbol``、``pred_score``、``pred_return``、``trend_label``。
        """
        results: dict[int, pd.DataFrame] = {}

        for period, model in self.models_dict.items():
            df = latest_factors.copy()
            missing = set(feature_cols) - set(df.columns)
            if missing:
                raise KeyError(f"[{period}d 模型] 缺少特征列: {missing}")

            df["pred_score"] = model.predict(df[feature_cols].values)
            # 周期越长，预测收益波动越大，用不同缩放系数
            scale = {5: 0.8, 10: 1.0, 20: 1.2}.get(period, 1.0)
            z = (df["pred_score"] - df["pred_score"].mean()) / (
                df["pred_score"].std() + 1e-8
            )
            df["pred_return"] = (z * 0.02 * scale).clip(-0.15, 0.15)
            df["trend_label"] = df["pred_return"].apply(self.trend_label)

            results[period] = df[[
                "symbol",
                "pred_score",
                "pred_return",
                "trend_label",
            ]].copy().sort_values("pred_score", ascending=False).reset_index(drop=True)

        return results

    # ------------------------------------------------------------------ #
    # 趋势标签
    # ------------------------------------------------------------------ #

    def trend_label(self, pred_return: float) -> str:
        """根据预测收益率返回趋势标签.

        阈值优先级（从高到低）::

            strong_up  > mild_up  > neutral  > mild_down  > strong_down

        Args:
            pred_return: 预测收益率（小数形式，如 0.02 表示 2%）。

        Returns:
            趋势标签字符串。
        """
        t = self.thresholds
        if pred_return >= t.get("strong_up", 0.05):
            return "强势上涨"
        if pred_return >= t.get("mild_up", 0.02):
            return "温和上涨"
        if pred_return > t.get("neutral", -0.02):
            return "震荡"
        if pred_return > t.get("mild_down", -0.05):
            return "温和下跌"
        return "强势下跌"

    # ------------------------------------------------------------------ #
    # 格式化输出
    # ------------------------------------------------------------------ #

    def format_prediction(
        self,
        symbol: str,
        current_price: float,
        predictions: dict[int, pd.DataFrame],
    ) -> str:
        """将多周期预测格式化为人类可读文本.

        输出示例::

            688008.SH 当前价: 52.30
            ├─ 5日预测: +1.85% (温和上涨) → 目标价 53.27
            ├─ 10日预测: +2.13% (温和上涨) → 目标价 53.41
            └─ 20日预测: -0.50% (震荡) → 目标价 52.04

        Args:
            symbol: 股票代码。
            current_price: 当前价格。
            predictions: :meth:`predict` 返回的多周期预测字典。

        Returns:
            格式化后的多行字符串。
        """
        lines: list[str] = []
        lines.append(f"{symbol} 当前价: {current_price:.2f}")

        periods = sorted(predictions.keys())
        for i, period in enumerate(periods):
            df = predictions[period]
            row = df[df["symbol"] == symbol]
            if row.empty:
                prefix = "└─ " if i == len(periods) - 1 else "├─ "
                lines.append(f"{prefix}{period}日预测: 数据不可用")
                continue

            pred_ret = float(row.iloc[0]["pred_return"])
            label = str(row.iloc[0]["trend_label"])
            target = current_price * (1 + pred_ret)

            prefix = "└─ " if i == len(periods) - 1 else "├─ "
            lines.append(
                f"{prefix}{period}日预测: "
                f"{pred_ret*100:+.2f}% ({label}) → 目标价 {target:.2f}"
            )

        return "\n".join(lines)

    def format_all(
        self,
        current_prices: dict[str, float],
        predictions: dict[int, pd.DataFrame],
    ) -> str:
        """格式化全股票池的多周期预测.

        Args:
            current_prices: ``{symbol: price}`` 映射字典。
            predictions: :meth:`predict` 返回的多周期预测字典。

        Returns:
            Markdown 格式的周期分析报告字符串。
        """
        lines: list[str] = []
        lines.append("# KSS 多周期趋势分析")
        lines.append("")

        for sym, price in sorted(current_prices.items()):
            lines.append("```")
            lines.append(self.format_prediction(sym, price, predictions))
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("*Generated by KSS CycleAnalyzer*")
        return "\n".join(lines)
