"""kss train —— 训练 LightGBM 预测模型并注册到 ModelRegistry."""

from __future__ import annotations

import logging

import click

from kss.cli._pipeline import load_and_build_factors
from kss.data.cache_manager import CacheManager
from kss.data.data_loader import DataLoader
from kss.data.tushare_client import TushareClient
from kss.models.lightgbm_model import LightGBMModel
from kss.models.registry import ModelRegistry

logger = logging.getLogger(__name__)

_DEFAULT_PERIODS = [5, 10, 20]


@click.command()
@click.option("--pool", "-p", default="kcb50", help="股票池名称")
@click.option(
    "--period",
    "-t",
    type=click.Choice(["5", "10", "20"]),
    default=None,
    help="预测周期（日）；不指定则训练全部 5/10/20d",
)
@click.option("--start", default="20230101", help="训练数据起始日期 YYYYMMDD")
@click.option(
    "--end",
    default=None,
    help="训练数据截止日期 YYYYMMDD（默认今天）",
)
@click.pass_context
def cmd(
    ctx: click.Context,
    pool: str,
    period: str | None,
    start: str,
    end: str | None,
) -> None:
    """训练 LightGBM 模型并注册到 ModelRegistry.

    模型注册名为 ``lgb_{pool}_{N}d``，后续 ``kss scan/predict`` 自动加载最新版本。
    """
    periods = [int(period)] if period else _DEFAULT_PERIODS
    click.echo(f"🧠 训练模型 | 股票池: {pool} | 周期: {periods}")

    # 1. 数据 + 因子
    client = TushareClient()
    cache = CacheManager()
    loader = DataLoader(client, cache)
    prep = load_and_build_factors(loader, pool, start, end, with_targets=True)
    if prep is None:
        click.secho(f"  ❌ 数据加载失败或股票池为空: {pool}", fg="red")
        ctx.exit(1)
        return
    factor_df, feature_cols, _ = prep
    click.echo(f"  ✅ 因子: {len(feature_cols)} 个 | 样本: {len(factor_df)}")

    # 2. 每个周期独立训练并注册
    registry = ModelRegistry()
    for p in periods:
        label_col = f"future_return_{p}d"
        click.echo(f"  ⏳ 训练 {p}d 模型...")

        # 仅 dropna 必要列，避免被其它 future_return_*d 列的 NaN 误丢样本
        sub = factor_df.dropna(subset=feature_cols + [label_col])
        if sub.empty:
            click.secho(f"    ⚠️  {p}d label 全为 NaN，跳过", fg="yellow")
            continue

        model = LightGBMModel()
        model.fit(sub[feature_cols], sub[label_col])
        model_id = registry.save(
            model,
            name=f"lgb_{pool}_{p}d",
            metadata={
                "pool": pool,
                "period": p,
                "start": start,
                "end": end,
                "n_samples": int(len(sub)),
                "n_features": len(feature_cols),
            },
        )
        click.secho(f"    ✅ 已注册: {model_id}", fg="green")
        logger.info("Trained model_id=%s n_samples=%d", model_id, len(sub))

    click.secho("🎉 全部模型训练完成", fg="bright_green")
