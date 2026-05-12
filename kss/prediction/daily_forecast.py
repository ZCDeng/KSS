"""日度预测 —— 次日预测与再平衡建议生成."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from kss.models.base import ModelBase
from kss.models.registry import ModelRegistry
from kss.notifications.base import BaseNotifier
from kss.strategies.base import StrategyBase


class DailyForecast:
    """日度预测器.

    基于已训练模型对全股票池生成次日预测，
    并支持持仓再平衡建议与 Markdown 格式化输出。
    """

    def __init__(
        self,
        model_registry: ModelRegistry,
        strategy: StrategyBase,
        notifier: BaseNotifier | None = None,
    ) -> None:
        """初始化日度预测器.

        Args:
            model_registry: 模型注册中心，用于按需加载最新模型。
            strategy: 选股策略实例（如 :class:`~kss.strategies.CrossSectionalStrategy`）。
            notifier: 可选通知器，用于异常或关键信号推送。
        """
        self.model_registry = model_registry
        self.strategy = strategy
        self.notifier = notifier

    # ------------------------------------------------------------------ #
    # 核心预测
    # ------------------------------------------------------------------ #

    def generate(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        date: str | pd.Timestamp | None = None,
        beta: float | None = None,
        clip: float | None = None,
    ) -> pd.DataFrame:
        """生成下一交易日全股票池预测.

        对 ``date`` 当天的全部股票进行模型打分，
        并转换为预测收益、预测价格、交易信号与置信度。

        Args:
            factor_df: 包含因子与行情数据的 DataFrame，需含
                ``trade_date``、``symbol``、``close`` 列。
            feature_cols: 模型输入特征列名列表。
            model: 已训练好的模型实例。
            date: 目标日期；若为 ``None`` 则使用 ``factor_df`` 中最新的交易日。

        Returns:
            预测结果 DataFrame，列为::

                - ``symbol``       : 股票代码
                - ``pred_score``   : 模型原始打分
                - ``pred_return``  : 预测收益率（基于打分与历史波动估算）
                - ``pred_price``   : 预测收盘价
                - ``current_price``: 当前收盘价
                - ``signal``       : 交易信号（buy / sell / hold / neutral）
                - ``confidence``   : 置信度（0~1，基于打分分位数）
        """
        if date is None:
            date = factor_df["trade_date"].max()
        else:
            date = pd.Timestamp(date)

        day_df = factor_df[factor_df["trade_date"] == date].copy()
        if day_df.empty:
            raise ValueError(f"日期 {date} 无可用数据，无法生成日度预测.")

        # 特征完整性检查
        missing = set(feature_cols) - set(day_df.columns)
        if missing:
            raise KeyError(f"factor_df 缺少特征列: {missing}")

        # 模型打分
        day_df["pred_score"] = model.predict(day_df[feature_cols].values)
        day_df["daily_rank"] = day_df["pred_score"].rank(pct=True)

        # 信号判定 —— 基于分位数
        day_df["signal"] = day_df["daily_rank"].apply(self._rank_to_signal)
        day_df["confidence"] = np.abs(day_df["daily_rank"] - 0.5) * 2  # 0~1

        # 预测收益 —— 截面 z-score 线性映射；可选用 calibrate_beta 校准过的 β/clip
        # 显式优于默认：beta/clip 任意一项为 None 时回退到旧默认 (0.02, 0.05)，
        # 保证既有调用方零回归.
        eff_beta = beta if beta is not None else 0.02
        eff_clip = clip if clip is not None else 0.05
        pred_return = self._score_to_return(day_df["pred_score"], beta=eff_beta, clip=eff_clip)
        day_df["pred_return"] = pred_return
        day_df["current_price"] = day_df["close"]
        day_df["pred_price"] = day_df["current_price"] * (1 + pred_return)

        result = day_df[[
            "symbol",
            "pred_score",
            "pred_return",
            "pred_price",
            "current_price",
            "signal",
            "confidence",
        ]].copy()

        return result.sort_values("pred_score", ascending=False).reset_index(drop=True)

    @staticmethod
    def _rank_to_signal(rank: float) -> str:
        """将百分位排名映射为交易信号."""
        if rank >= 0.85:
            return "buy"
        if rank <= 0.15:
            return "sell"
        if 0.40 <= rank <= 0.60:
            return "hold"
        return "neutral"

    @staticmethod
    def _score_to_return(
        score: pd.Series,
        beta: float = 0.02,
        clip: float = 0.05,
    ) -> pd.Series:
        """将模型打分映射为预测收益率（截面 z-score 线性缩放）.

        默认 ``beta=0.02``、``clip=±5%`` 与旧实现等价，保证回归测试零变化.

        旧实现 ``z * close.pct_change().rolling(20).std() * 0.5`` 在单日 day_df 输入
        下退化失败：``close`` 切片是同一天不同股票的一行一值，``pct_change`` 计算的是
        **跨股票** 价差（无业务含义），``rolling(20).std`` 在样本数 < 20 时全 NaN，
        ``fillna(vol.mean())`` 又是 ``fillna(NaN)`` ——最终 ``pred_return`` 串 NaN 到
        ``pred_price`` 与 Markdown 报告，silent 错误。

        Args:
            score: 模型打分（每行一个截面内的股票）.
            beta: 缩放系数；可由 :meth:`_score_to_return_calibrated` 注入历史校准值.
            clip: 上下界（绝对值）.

        Returns:
            预测收益率 Series；输入全 NaN 时返回全 0.
        """
        # std=NaN（单行）或 std=0（所有打分相同）都会让 z 退化；fillna(0) 兜底，
        # 保证下游 (1 + pred_return) 永远是合法浮点
        z = (score - score.mean()) / (score.std() + 1e-8)
        z = z.fillna(0.0)
        return (z * beta).clip(-clip, clip)

    @staticmethod
    def calibrate_beta(
        panel: pd.DataFrame,
        pred_col: str,
        ret_col: str,
        date_col: str = "trade_date",
        clip_quantile: float = 0.95,
    ) -> dict[str, float]:
        """从历史回测面板上估计 ``β_score``：把截面 z-score 映射到真实收益.

        逻辑：

        1. 对每个交易日做截面 z-score(pred_col).
        2. 跨日做 OLS：``ret = β × z + ε``（无截距，原点过）.
        3. 同时返回 ``ret`` 的 95% 分位作为 ``clip`` 上限的推荐值.

        Args:
            panel: 历史 panel；通常来自 ``BacktestEngine.walk_forward``
                的 ``result_df.attrs["panel"]``.
            pred_col: 预测打分列名.
            ret_col: 真实未来收益列名（如 ``next_day_return`` 或 ``future_return_5d``）.
            date_col: 交易日列.
            clip_quantile: 收益分布的分位（0~1）；建议 0.95 = ±95% 分位绝对值最大值.

        Returns:
            字典：``{"beta": ..., "clip": ..., "r_squared": ..., "n": ...}``；
            样本不足时 ``beta=0.02, clip=0.05``（即旧默认）.
        """
        clean = panel.dropna(subset=[pred_col, ret_col]).copy()
        if len(clean) < 30:
            return {"beta": 0.02, "clip": 0.05, "r_squared": 0.0, "n": len(clean)}

        # 截面 z-score
        grouped = clean.groupby(date_col)[pred_col]
        mean = grouped.transform("mean")
        std = grouped.transform("std").fillna(0.0)
        z = (clean[pred_col] - mean) / (std + 1e-8)
        z = z.replace([np.inf, -np.inf], 0.0).fillna(0.0)

        # 原点 OLS：β = sum(z * r) / sum(z^2)
        ret = clean[ret_col].values
        z_arr = z.values
        denom = float(np.sum(z_arr ** 2))
        if denom <= 0:
            return {"beta": 0.02, "clip": 0.05, "r_squared": 0.0, "n": len(clean)}
        beta = float(np.sum(z_arr * ret) / denom)
        yhat = z_arr * beta
        ss_res = float(np.sum((ret - yhat) ** 2))
        ss_tot = float(np.sum(ret ** 2))
        r_sq = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        clip_val = float(np.quantile(np.abs(ret), clip_quantile))
        # 保底，避免 clip 太窄把所有信号都截平
        clip_val = max(clip_val, 0.01)

        return {"beta": beta, "clip": clip_val, "r_squared": r_sq, "n": len(clean)}

    # ------------------------------------------------------------------ #
    # 再平衡报告
    # ------------------------------------------------------------------ #

    def rebalance_report(
        self,
        current_holdings: dict[str, float] | list[str] | set[str],
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        date: str | pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        """生成持仓再平衡建议.

        Args:
            current_holdings: 当前持仓，可为代码集合/列表或 {symbol: weight} 字典。
            factor_df: 包含因子与行情数据的 DataFrame。
            feature_cols: 模型输入特征列名列表。
            model: 已训练好的模型实例。
            date: 目标日期；若为 ``None`` 则使用最新日期。

        Returns:
            再平衡建议字典，结构为::

                - ``add_list``   : [{"symbol": ..., "pred_return": ..., "confidence": ...}]
                - ``reduce_list``: [{"symbol": ..., "reason": "掉出选股池"}]
                - ``keep_list``  : [{"symbol": ..., "pred_return": ..., "confidence": ...}]
                - ``cash_signal``: "increase" / "decrease" / "hold"
        """
        if isinstance(current_holdings, dict):
            holdings_set = set(current_holdings.keys())
        else:
            holdings_set = set(current_holdings)

        forecast_df = self.generate(factor_df, feature_cols, model, date)
        buy_symbols = set(
            forecast_df[forecast_df["signal"] == "buy"]["symbol"].tolist()
        )
        sell_symbols = set(
            forecast_df[forecast_df["signal"] == "sell"]["symbol"].tolist()
        )

        add_list: list[dict[str, Any]] = []
        reduce_list: list[dict[str, Any]] = []
        keep_list: list[dict[str, Any]] = []

        for _, row in forecast_df.iterrows():
            sym = row["symbol"]
            rec = {
                "symbol": sym,
                "pred_return": float(row["pred_return"]),
                "confidence": float(row["confidence"]),
            }
            if row["signal"] == "buy" and sym not in holdings_set:
                add_list.append(rec)
            elif row["signal"] == "sell" and sym in holdings_set:
                reduce_list.append({"symbol": sym, "reason": "模型信号转为卖出"})
            elif sym in holdings_set and row["signal"] in ("buy", "hold"):
                keep_list.append(rec)

        # 持仓中未被任何信号覆盖的股票 → reduce
        covered = buy_symbols | sell_symbols
        for sym in holdings_set:
            if sym not in covered:
                reduce_list.append({"symbol": sym, "reason": "不在今日覆盖范围内，建议减仓"})

        # 现金信号：若 add 数量远大于 reduce，提示增仓现金；反之减仓
        net_change = len(add_list) - len(reduce_list)
        if net_change > 3:
            cash_signal = "decrease"
        elif net_change < -3:
            cash_signal = "increase"
        else:
            cash_signal = "hold"

        return {
            "add_list": add_list,
            "reduce_list": reduce_list,
            "keep_list": keep_list,
            "cash_signal": cash_signal,
        }

    # ------------------------------------------------------------------ #
    # 格式化输出
    # ------------------------------------------------------------------ #

    def format_daily(self, forecast_df: pd.DataFrame) -> str:
        """将日度预测 DataFrame 格式化为 Markdown 文本.

        Args:
            forecast_df: :meth:`generate` 返回的预测结果 DataFrame。

        Returns:
            Markdown 格式的预测报告字符串。
        """
        lines: list[str] = []
        today = datetime.now().strftime("%Y-%m-%d")
        lines.append(f"# KSS 日度预测报告 ({today})")
        lines.append("")

        # 汇总
        n_buy = (forecast_df["signal"] == "buy").sum()
        n_sell = (forecast_df["signal"] == "sell").sum()
        n_hold = (forecast_df["signal"] == "hold").sum()
        lines.append("## 市场概览")
        lines.append(f"- **买入信号**: {n_buy} 只")
        lines.append(f"- **卖出信号**: {n_sell} 只")
        lines.append(f"- **持有信号**: {n_hold} 只")
        lines.append(f"- **总覆盖**: {len(forecast_df)} 只")
        lines.append("")

        # Top 买入
        buys = forecast_df[forecast_df["signal"] == "buy"].head(10)
        if not buys.empty:
            lines.append("## Top 买入推荐")
            lines.append("| 代码 | 预测收益 | 预测价 | 现价 | 置信度 |")
            lines.append("|------|----------|--------|------|--------|")
            for _, row in buys.iterrows():
                lines.append(
                    f"| {row['symbol']} | "
                    f"{row['pred_return']*100:+.2f}% | "
                    f"{row['pred_price']:.2f} | "
                    f"{row['current_price']:.2f} | "
                    f"{row['confidence']:.1%} |"
                )
            lines.append("")

        # Top 卖出 —— 按 pred_score 升序，取最低分的 10 只作为最强卖出信号
        sells = (
            forecast_df[forecast_df["signal"] == "sell"]
            .sort_values("pred_score", ascending=True)
            .head(10)
        )
        if not sells.empty:
            lines.append("## 卖出警示")
            lines.append("| 代码 | 预测收益 | 预测价 | 现价 | 置信度 |")
            lines.append("|------|----------|--------|------|--------|")
            for _, row in sells.iterrows():
                lines.append(
                    f"| {row['symbol']} | "
                    f"{row['pred_return']*100:+.2f}% | "
                    f"{row['pred_price']:.2f} | "
                    f"{row['current_price']:.2f} | "
                    f"{row['confidence']:.1%} |"
                )
            lines.append("")

        lines.append("---")
        lines.append("*Generated by KSS DailyForecast*")
        return "\n".join(lines)
