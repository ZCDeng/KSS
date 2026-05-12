"""CLI 命令共享的数据准备流程 —— 加载股票池、生成因子、截面标准化."""

from __future__ import annotations

import logging

import pandas as pd

from kss.data.data_loader import DataLoader
from kss.features.pipeline import FactorPipeline

logger = logging.getLogger(__name__)


def load_and_build_factors(
    loader: DataLoader,
    pool: str,
    start: str,
    end: str | None,
    *,
    with_targets: bool,
) -> tuple[pd.DataFrame, list[str], FactorPipeline] | None:
    """加载股票池数据 + 因子工程 + 截面标准化的统一入口.

    将 train/scan/predict 三个 CLI 命令的重复样板代码集中到一处，保证特征列
    构造方式一致，避免训练/推理特征不一致导致的 silent 漂移。

    Args:
        loader: 已初始化的 :class:`DataLoader` 实例.
        pool: 股票池名称（用于 ``DataLoader.load_stock_pool``）.
        start: 起始日期 ``YYYYMMDD``.
        end: 截止日期 ``YYYYMMDD``；``None`` 表示今天.
        with_targets: 是否计算训练标签 ``future_return_Nd`` 与 ``next_day_return``.
            训练需要 True；预测/扫描需要 False，否则每只股票最后 N 行会因标签
            为 NaN 被下游 dropna 误丢.

    Returns:
        ``(factor_df, feature_cols, fp)`` 三元组；股票池为空或加载失败返回 ``None``.
    """
    df = loader.load_stock_pool(pool_name=pool, start=start, end=end)
    if df is None or len(df) == 0:
        logger.warning("股票池 %s 数据加载失败或为空", pool)
        return None

    symbols = list(df["symbol"].unique())
    if not symbols:
        logger.warning("股票池 %s 不含任何 symbol", pool)
        return None

    fp: FactorPipeline | None = None
    frames: list[pd.DataFrame] = []
    for sym in symbols:
        sub = (
            df[df["symbol"] == sym]
            .sort_values("trade_date")
            .reset_index(drop=True)
        )
        fp = FactorPipeline(sub)
        f = fp.generate()
        if with_targets:
            f = fp.add_targets(f, sub["close"])
        f["symbol"] = sym
        frames.append(f)
    assert fp is not None  # symbols 非空时循环必然执行至少一次

    factor_df = pd.concat(frames, ignore_index=True)
    feature_cols = fp.get_feature_cols(factor_df)
    factor_df = FactorPipeline.cs_normalize(factor_df, feature_cols)
    return factor_df, feature_cols, fp
