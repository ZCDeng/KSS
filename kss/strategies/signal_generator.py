"""信号生成器 —— 每日扫描与周度再平衡."""

from __future__ import annotations

from typing import Any

import pandas as pd

from kss.models.base import ModelBase
from kss.strategies.base import StrategyBase


class SignalGenerator:
    """交易信号生成器.

    基于策略实例提供每日扫描、周度再平衡与人可读信号格式化。
    """

    def __init__(self, strategy: StrategyBase) -> None:
        """初始化信号生成器.

        Args:
            strategy: 策略实例（如 :class:`CrossSectionalStrategy`）。
        """
        self.strategy = strategy

    # ------------------------------------------------------------------ #
    # 每日扫描
    # ------------------------------------------------------------------ #

    def daily_scan(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        date: str | pd.Timestamp,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """对指定日期生成 buy/sell/hold 信号.

        逻辑：
        1. 调用策略 ``generate_signals`` 获取当日推荐买入列表；
        2. 若传入 ``current_holdings``（持仓集合），将不在 Top 列表中的持仓标记为 sell；
        3. 其余标记为 hold。

        Args:
            factor_df: 包含因子与行情数据的 DataFrame.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例.
            date: 目标日期.
            **kwargs: 额外参数，可传入 ``current_holdings``（当前持仓代码集合/set）。

        Returns:
            包含 ``symbol``、``pred_score``、``daily_rank``、``signal`` 列的 DataFrame。
        """
        current_holdings: set[str] = set(kwargs.pop("current_holdings", set()))

        buy_signals = self.strategy.generate_signals(
            factor_df, feature_cols, model, date=date, **kwargs
        )
        buy_symbols = set(buy_signals["symbol"].tolist())

        # 构建全市场信号
        day_df = factor_df[factor_df["trade_date"] == pd.Timestamp(date)].copy()
        if day_df.empty:
            raise ValueError(f"日期 {date} 无数据，无法生成信号.")

        all_symbols = set(day_df["symbol"].unique())
        sell_symbols = current_holdings - buy_symbols
        hold_symbols = current_holdings & buy_symbols
        neutral_symbols = all_symbols - buy_symbols - current_holdings

        records: list[dict[str, Any]] = []
        for _, row in buy_signals.iterrows():
            records.append({
                "symbol": row["symbol"],
                "pred_score": row.get("pred_score"),
                "daily_rank": row.get("daily_rank"),
                "signal": "buy",
            })

        for sym in sell_symbols:
            records.append({"symbol": sym, "pred_score": None, "daily_rank": None, "signal": "sell"})
        for sym in hold_symbols:
            # hold 股票也在 buy_signals 中，但需要确保标记为 hold 而非重复的 buy
            # 这里简化处理：如果已经在 buy_signals 中，则保持 buy（因为再平衡时 hold 的也视为保留）
            if sym not in buy_symbols:
                records.append({"symbol": sym, "pred_score": None, "daily_rank": None, "signal": "hold"})
        for sym in neutral_symbols:
            records.append({"symbol": sym, "pred_score": None, "daily_rank": None, "signal": "neutral"})

        signal_df = pd.DataFrame(records)
        # 去重：若某持仓同时出现在 buy 中，保留 buy
        signal_df = signal_df.sort_values(
            "signal", key=lambda col: col.map({"buy": 0, "hold": 1, "sell": 2, "neutral": 3})
        ).drop_duplicates(subset=["symbol"], keep="first")
        return signal_df.sort_values("signal").reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 周度再平衡
    # ------------------------------------------------------------------ #

    def weekly_rebalance(
        self,
        factor_df: pd.DataFrame,
        feature_cols: list[str],
        model: ModelBase,
        current_holdings: dict[str, float] | list[str] | set[str],
        date: str | pd.Timestamp,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """生成周度再平衡建议.

        Args:
            factor_df: 包含因子与行情数据的 DataFrame.
            feature_cols: 模型输入特征列名列表.
            model: 已训练好的模型实例.
            current_holdings: 当前持仓，可为代码集合、列表或字典（权重字典）.
            date: 再平衡日期.
            **kwargs: 额外参数，传入 ``target_positions`` 可指定目标仓位数。

        Returns:
            再平衡建议 DataFrame，含 ``symbol``、``action``、``reason`` 列。
        """
        if isinstance(current_holdings, dict):
            holdings_set = set(current_holdings.keys())
        else:
            holdings_set = set(current_holdings)

        signal_df = self.daily_scan(
            factor_df, feature_cols, model, date, current_holdings=holdings_set, **kwargs
        )

        rebalance_records: list[dict[str, Any]] = []
        for _, row in signal_df.iterrows():
            sym = row["symbol"]
            sig = row["signal"]
            score = row.get("pred_score")
            rank = row.get("daily_rank")

            if sig == "buy":
                if sym in holdings_set:
                    action = "keep"
                    reason = f"模型打分 {score:.4f}，排名 {rank:.1%}，继续持有"
                else:
                    action = "add"
                    reason = f"模型打分 {score:.4f}，排名 {rank:.1%}，新增买入"
            elif sig == "sell":
                action = "reduce"
                reason = "掉出 Top 选股池，建议卖出"
            elif sig == "hold":
                action = "keep"
                reason = "仍在 Top 选股池内，继续持有"
            else:
                action = "ignore"
                reason = "不在选股范围内"

            rebalance_records.append({
                "symbol": sym,
                "action": action,
                "reason": reason,
                "pred_score": score,
                "daily_rank": rank,
            })

        rebalance_df = pd.DataFrame(rebalance_records)
        # 优先展示 actionable 项目
        priority = {"add": 0, "reduce": 1, "keep": 2, "ignore": 3}
        rebalance_df["_priority"] = rebalance_df["action"].map(priority)
        rebalance_df = rebalance_df.sort_values("_priority").drop(columns=["_priority"])
        return rebalance_df.reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 格式化输出
    # ------------------------------------------------------------------ #

    def format_signal(self, signal_df: pd.DataFrame) -> str:
        """将信号 DataFrame 格式化为人类可读文本.

        Args:
            signal_df: 由 ``daily_scan`` 或 ``weekly_rebalance`` 生成的 DataFrame。

        Returns:
            格式化后的多行字符串.
        """
        lines: list[str] = []
        lines.append("=" * 50)
        lines.append("  KSS 交易信号")
        lines.append("=" * 50)

        for signal_type in ["buy", "sell", "add", "reduce", "keep", "hold", "neutral", "ignore"]:
            subset = signal_df[signal_df.get("signal", signal_df.get("action")) == signal_type]
            if subset.empty:
                continue

            lines.append(f"\n【{self._label(signal_type)}】")
            for _, row in subset.iterrows():
                sym = row["symbol"]
                score = row.get("pred_score")
                rank = row.get("daily_rank")
                reason = row.get("reason", "")

                parts = [f"  {sym}"]
                if pd.notna(score):
                    parts.append(f"score={score:.4f}")
                if pd.notna(rank):
                    parts.append(f"rank={rank:.1%}")
                if reason:
                    parts.append(f"| {reason}")
                lines.append("  ".join(parts))

        lines.append("\n" + "=" * 50)
        return "\n".join(lines)

    @staticmethod
    def _label(signal_type: str) -> str:
        """信号类型中文标签."""
        mapping = {
            "buy": "买入",
            "sell": "卖出",
            "add": "加仓",
            "reduce": "减仓",
            "keep": "持有",
            "hold": "持有",
            "neutral": "观望",
            "ignore": "忽略",
        }
        return mapping.get(signal_type, signal_type.upper())
