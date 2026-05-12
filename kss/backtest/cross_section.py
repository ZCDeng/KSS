"""单因子横截面选股回测 —— 不训练模型，直接用因子值作 score.

与 :class:`~kss.backtest.engine.BacktestEngine` 的区别：

- ``BacktestEngine.walk_forward`` 每个窗口训练 LightGBM，把多因子组合成 score；
- 本模块**直接用单个因子列作 score**，不训练任何模型——
  适合做单因子横截面 alpha 验证 / baseline / 对照实验.

无 look-ahead：

- 因子值用 ``trade_date = t`` 的 close 后可观测；
- 持仓在 ``t+1`` 开盘建仓，``t+2`` 开盘换仓——通过传入
  :func:`FactorPipeline.add_targets` 计算的 ``next_day_return`` 实现.
- 双边换手成本累加（与 ``BacktestEngine`` 同构）.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import pandas as pd

from kss.backtest.cost_model import CostModel, ExecutionModel

logger = logging.getLogger(__name__)


def factor_cross_section_backtest(
    factor_df: pd.DataFrame,
    factor_col: str,
    cost_model: CostModel,
    top_pct: float = 0.2,
    direction: Literal["long_high", "long_low"] = "long_high",
    min_stocks: int = 10,
    date_col: str = "trade_date",
    symbol_col: str = "symbol",
    ret_col: str = "next_day_return",
    *,
    execution: ExecutionModel | None = None,
) -> pd.DataFrame | None:
    """单因子横截面 Top-Pct 选股回测.

    流程：

    1. 按 ``date_col`` 分组，每日按 ``factor_col`` 截面 rank；
    2. 选出 Top ``top_pct`` 股票（``direction=long_high`` 选高分，反之选低分）；
    3. 等权持有，``next_day_return`` 算毛收益；
    4. 与上一日 Top 集合对比算双边换手率，扣 ``cost_model`` 成本.

    Args:
        factor_df: 含 ``date_col / symbol_col / factor_col / ret_col`` 的面板.
        factor_col: 作为截面 score 的因子列名.
        cost_model: 成本模型.
        top_pct: 选股比例（0~1）.
        direction: ``"long_high"`` = 高分买入（macd_hist 等趋势因子）；
            ``"long_low"`` = 低分买入（pb 等估值因子，反向使用）.
        min_stocks: 单日最小股票数，少于则跳过.
        date_col / symbol_col / ret_col: 列名（默认与 KSS 约定一致）.
        execution: 可选 :class:`ExecutionModel`，启用后做：

            1. 选 Top Pct **之前**剔除买入侧涨停股（``side="buy"``）；
            2. 持仓权重按 :meth:`ExecutionModel.max_tradable_ratio` 缩减
               （需面板含 ``amount`` 列，否则按 1.0 处理）；
            3. ``cost`` 额外叠加 :meth:`ExecutionModel.slippage_cost`.

            ``result.attrs["panel"]`` 不变；``result`` 主表追加
            ``n_tradable_pre / n_tradable_post`` 列便于诊断.

    Returns:
        DataFrame，列：``trade_date / gross_return / net_return / turnover /
        buy_turnover / cost / n_stocks / n_kept``.
        ``result.attrs["panel"]`` = 全市场每日因子打分面板（供
        :class:`SignalDiagnostics` 消费）.
        ``result.attrs["factor_col"]`` / ``["direction"]`` 元信息.
        若无有效结果则返回 ``None``.

    Raises:
        ValueError: 必需列缺失或 ``top_pct`` 越界.
    """
    required = {date_col, symbol_col, factor_col, ret_col}
    missing = required - set(factor_df.columns)
    if missing:
        raise ValueError(f"factor_df 缺少必需列: {missing}")
    if not 0 < top_pct <= 1:
        raise ValueError(f"top_pct 必须在 (0, 1] 之间，收到: {top_pct}")

    dates = sorted(factor_df[date_col].unique())
    results: list[dict[str, Any]] = []
    panel_records: list[pd.DataFrame] = []
    prev_holdings: set[str] = set()

    for d in dates:
        day_df = factor_df[factor_df[date_col] == d].copy()
        day_df = day_df.dropna(subset=[factor_col, ret_col])
        if len(day_df) < min_stocks:
            continue

        # 截面 rank：long_high → rank 升序（高分排在末尾）→ 选末尾 top_pct
        # long_low → rank 升序（低分排在前面）→ 选前 top_pct
        day_df["pred_score"] = day_df[factor_col].astype(float)
        if direction == "long_high":
            day_df["daily_rank"] = day_df["pred_score"].rank(pct=True)
            top_mask = day_df["daily_rank"] >= (1 - top_pct)
        else:
            day_df["daily_rank"] = day_df["pred_score"].rank(pct=True, ascending=False)
            top_mask = day_df["daily_rank"] >= (1 - top_pct)

        # execution: 选 Top 后剔除买入侧涨停 → 不足 min_stocks 时降级到"取过滤后
        # 集合的等价 Top Pct"（保证仍有持仓但只挑可成交者）.
        n_tradable_pre: int | None = None
        n_tradable_post: int | None = None
        if execution is not None:
            n_tradable_pre = int(top_mask.sum())
            top_df = day_df.loc[top_mask]
            tradable_top = execution.filter_tradable(
                top_df,
                side="buy",
                symbol_col=symbol_col,
                date_col=date_col,
            )
            n_tradable_post = int(len(tradable_top))
            if n_tradable_post < min_stocks:
                # 降级：扩大候选池到全日可成交股票，再取等比例 Top
                tradable_all = execution.filter_tradable(
                    day_df,
                    side="buy",
                    symbol_col=symbol_col,
                    date_col=date_col,
                )
                if len(tradable_all) >= min_stocks:
                    n_take = max(min_stocks, int(round(len(tradable_all) * top_pct)))
                    if direction == "long_high":
                        tradable_top = tradable_all.nlargest(n_take, "pred_score")
                    else:
                        tradable_top = tradable_all.nsmallest(n_take, "pred_score")
                    n_tradable_post = int(len(tradable_top))
                else:
                    # 当日实在没有可成交股票 → 跳过
                    continue
            # 用 tradable_top 重建 top_mask（按 index 对齐）
            top_mask = day_df.index.isin(tradable_top.index)
            top_mask = pd.Series(top_mask, index=day_df.index)

        top_stocks = set(day_df.loc[top_mask, symbol_col].tolist())

        # 双边换手率
        if prev_holdings:
            kept = len(prev_holdings & top_stocks)
            sell_turnover = (len(prev_holdings) - kept) / len(prev_holdings)
            buy_turnover = (
                (len(top_stocks) - kept) / len(top_stocks) if top_stocks else 0.0
            )
        else:
            kept = 0
            sell_turnover = 0.0
            buy_turnover = 1.0 if top_stocks else 0.0

        # 部分成交：execution 启用时按 max_tradable_ratio 调权重
        top_rows = day_df.loc[top_mask]
        if execution is not None and len(top_rows) > 0:
            n_top = len(top_rows)
            # 假设组合总市值归一化为 1，等权下单 → 单股下单 = 1 / n_top
            per_position = 1.0 / n_top
            ratios = top_rows.apply(
                lambda r: execution.max_tradable_ratio(r, per_position),
                axis=1,
            ).astype(float)
            # 加权毛收益（用可成交比例做权重；权重和归一化）
            weights = ratios / ratios.sum() if ratios.sum() > 0 else ratios
            portfolio_return = float((top_rows[ret_col] * weights).sum())
        else:
            portfolio_return = float(day_df.loc[top_mask, ret_col].mean())

        cost = (
            sell_turnover * cost_model.sell_total
            + buy_turnover * cost_model.buy_total
        )
        if execution is not None:
            # 开盘冲击成本按双边换手率折算（与 CostModel.lateral_cost 同口径）
            cost += execution.slippage_cost(buy_turnover + sell_turnover)
        net_return = portfolio_return - cost

        row_record: dict[str, Any] = {
            date_col: d,
            "gross_return": portfolio_return,
            "net_return": net_return,
            "turnover": sell_turnover,
            "buy_turnover": buy_turnover,
            "cost": cost,
            "n_stocks": int(top_mask.sum()),
            "n_kept": int(kept),
        }
        if execution is not None:
            row_record["n_tradable_pre"] = n_tradable_pre
            row_record["n_tradable_post"] = n_tradable_post
        results.append(row_record)

        # 全市场面板（供 diagnostics）
        panel_records.append(
            day_df[[date_col, symbol_col, "pred_score", ret_col]].copy()
        )

        prev_holdings = top_stocks

    if not results:
        return None

    result_df = pd.DataFrame(results)
    if panel_records:
        result_df.attrs["panel"] = pd.concat(panel_records, ignore_index=True)
    result_df.attrs["factor_col"] = factor_col
    result_df.attrs["direction"] = direction
    result_df.attrs["top_pct"] = top_pct
    return result_df
