"""kss predict —— 生成日度收益预测."""

from __future__ import annotations

import logging

import click
import pandas as pd

from kss.cli._pipeline import load_and_build_factors
from kss.data.cache_manager import CacheManager
from kss.data.data_loader import DataLoader
from kss.data.tushare_client import TushareClient
from kss.models.registry import ModelRegistry
from kss.prediction.daily_forecast import DailyForecast
from kss.strategies.cross_sectional import CrossSectionalStrategy

logger = logging.getLogger(__name__)


@click.command()
@click.option("--pool", "-p", default="kcb50", help="股票池名称")
@click.option(
    "--period",
    "-t",
    type=click.Choice(["5", "10", "20"]),
    default="10",
    help="预测周期",
)
@click.option(
    "--date",
    "-d",
    default=None,
    help="预测目标日期 YYYY-MM-DD 或 YYYYMMDD，默认数据中最新一天",
)
@click.option(
    "--start",
    default="20230101",
    help="加载历史数据的起始日期",
)
@click.pass_context
def cmd(
    ctx: click.Context,
    pool: str,
    period: str,
    date: str | None,
    start: str,
) -> None:
    """生成指定日期的预测报告（Markdown 输出到 stdout）."""
    click.echo(f"🔮 预测 | 股票池: {pool} | 周期: {period}d")

    client = TushareClient()
    cache = CacheManager()
    loader = DataLoader(client, cache)
    prep = load_and_build_factors(loader, pool, start, end=None, with_targets=False)
    if prep is None:
        click.secho(f"  ❌ 数据加载失败或股票池为空: {pool}", fg="red")
        ctx.exit(1)
        return
    factor_df, feature_cols, _ = prep

    registry = ModelRegistry()
    model_name = f"lgb_{pool}_{period}d"
    try:
        model = registry.load(model_name)
    except FileNotFoundError:
        click.secho(
            f"  ❌ 模型未找到: {model_name}；请先运行 "
            f"'kss train --pool {pool} --period {period}'",
            fg="red",
        )
        ctx.exit(1)
        return

    target_date = (
        pd.Timestamp(date) if date else pd.Timestamp(factor_df["trade_date"].max())
    )
    click.echo(f"  ✅ 目标日期: {target_date.date()}")

    strategy = CrossSectionalStrategy()
    forecast = DailyForecast(model_registry=registry, strategy=strategy)
    try:
        df = forecast.generate(factor_df, feature_cols, model, date=target_date)
    except (ValueError, KeyError) as exc:
        click.secho(f"  ❌ 预测失败: {exc}", fg="red")
        ctx.exit(1)
        return

    click.echo("")
    click.echo(forecast.format_daily(df))
    logger.info(
        "Predict pool=%s date=%s n_buy=%d",
        pool,
        target_date.date(),
        int((df["signal"] == "buy").sum()),
    )
