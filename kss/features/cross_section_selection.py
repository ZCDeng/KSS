"""Walk-forward 截面 IC 选因子工厂.

与 :func:`~kss.features.selection.FeatureSelector.stable_features` 的区别：

- ``stable_features`` 在**全样本**上跑 LGB gain 一次性筛因子（一次性选，无 WF）；
- 本模块返回 ``Callable[[train_df], list[str]]``，每次 retrain 时由
  :meth:`~kss.backtest.engine.BacktestEngine.walk_forward` 调用，
  仅用**当窗训练集**算截面 IC 选因子 → 选因子环节也无 look-ahead.
"""

from __future__ import annotations

import logging
from typing import Callable, Literal

import pandas as pd

from kss.backtest.diagnostics import SignalDiagnostics

logger = logging.getLogger(__name__)


def make_ic_topk_selector(
    candidate_factors: list[str],
    horizon: str = "future_return_5d",
    top_k: int = 10,
    method: Literal["pearson", "spearman"] = "spearman",
    min_t_stat: float | None = None,
    date_col: str = "trade_date",
) -> Callable[[pd.DataFrame], list[str]]:
    """工厂：按截面 IC ``|t-stat|`` 选 Top K 因子.

    返回的 ``selector(train_df)`` 在 ``train_df`` 上调用
    :meth:`SignalDiagnostics.cross_section_ic_scan`，按 ``|t-stat|`` 降序取前 ``top_k``.

    Args:
        candidate_factors: 候选因子列名（必须是 ``train_df`` 的列）.
        horizon: IC 计算的未来收益列名.
        top_k: 选择因子数量.
        method: 相关系数类型.
        min_t_stat: 若提供，仅保留 ``|t-stat| >= min_t_stat`` 的因子（先过滤再 top_k）.
            ``None`` 不过滤；典型值 ``2.0`` 仅取 5% 显著的因子.
        date_col: 交易日列名.

    Returns:
        ``selector(train_df) -> list[str]``：每次 retrain 调用，返回当窗 Top K 因子.
    """
    def _selector(train_df: pd.DataFrame) -> list[str]:
        scan = SignalDiagnostics.cross_section_ic_scan(
            train_df, candidate_factors,
            horizons=[horizon], date_col=date_col, method=method,
        )
        if scan.empty:
            logger.warning("ic_scan 在当窗 train_df 上返回空表")
            return []
        # 按 |t-stat| 已降序排
        filtered = scan
        if min_t_stat is not None:
            filtered = scan[scan["abs_t_stat"] >= float(min_t_stat)]
            if filtered.empty:
                # 没有满足 t-stat 阈值的因子，退化到取全表 top_k 的 t-stat 最高的
                logger.debug(
                    "min_t_stat=%.2f 下无因子入选，退化到 top_k=%d", min_t_stat, top_k,
                )
                filtered = scan
        return filtered.head(top_k)["factor"].tolist()

    return _selector


def make_combined_selector(
    selectors: list[Callable[[pd.DataFrame], list[str]]],
    mode: Literal["union", "intersection"] = "union",
) -> Callable[[pd.DataFrame], list[str]]:
    """工厂：组合多个 selector（多 horizon 或多 method 互补）.

    Args:
        selectors: 多个 selector callable 列表.
        mode: ``"union"`` = 取并集；``"intersection"`` = 取交集.

    Returns:
        组合后的 selector callable.
    """
    if not selectors:
        raise ValueError("selectors 不能为空")

    def _combined(train_df: pd.DataFrame) -> list[str]:
        all_sets = [set(s(train_df)) for s in selectors]
        if mode == "union":
            result = set().union(*all_sets)
        else:
            result = set(all_sets[0]).intersection(*all_sets[1:]) if all_sets else set()
        return list(result)

    return _combined
