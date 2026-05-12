"""kss scan —— 每日信号扫描."""

from __future__ import annotations

import logging

import click
import pandas as pd

from kss.cli._pipeline import load_and_build_factors
from kss.data.cache_manager import CacheManager
from kss.data.data_loader import DataLoader
from kss.data.tushare_client import TushareClient
from kss.models.registry import ModelRegistry
from kss.strategies.cross_sectional import CrossSectionalStrategy
from kss.strategies.signal_generator import SignalGenerator

logger = logging.getLogger(__name__)


@click.command()
@click.option("--pool", "-p", default="kcb50", help="股票池名称")
@click.option(
    "--period",
    "-t",
    type=click.Choice(["5", "10", "20"]),
    default="10",
    help="预测周期（决定加载哪个模型）",
)
@click.option(
    "--date",
    "-d",
    default=None,
    help="扫描日期 YYYY-MM-DD 或 YYYYMMDD，默认数据中最新一天",
)
@click.option(
    "--top-pct",
    default=0.2,
    type=float,
    help="选股比例，默认 0.2",
)
@click.option(
    "--start",
    default="20230101",
    help="加载历史数据的起始日期（需足够长以计算技术因子）",
)
@click.pass_context
def cmd(
    ctx: click.Context,
    pool: str,
    period: str,
    date: str | None,
    top_pct: float,
    start: str,
) -> None:
    """扫描指定日期的买入/卖出/观望信号."""
    click.echo(f"🔍 信号扫描 | 股票池: {pool} | 周期: {period}d")

    # 1. 数据 + 因子
    client = TushareClient()
    cache = CacheManager()
    loader = DataLoader(client, cache)
    prep = load_and_build_factors(loader, pool, start, end=None, with_targets=False)
    if prep is None:
        click.secho(f"  ❌ 数据加载失败或股票池为空: {pool}", fg="red")
        ctx.exit(1)
        return
    factor_df, feature_cols, _ = prep

    # 2. 模型
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

    # 3. 解析扫描日期（None → 数据中最新交易日）
    scan_date = (
        pd.Timestamp(date) if date else pd.Timestamp(factor_df["trade_date"].max())
    )
    click.echo(f"  ✅ 扫描日期: {scan_date.date()}")

    # 4. 策略 + 信号
    strategy = CrossSectionalStrategy(top_pct=top_pct)
    generator = SignalGenerator(strategy)
    try:
        signal_df = generator.daily_scan(
            factor_df, feature_cols, model, date=scan_date,
        )
    except (ValueError, KeyError) as exc:
        click.secho(f"  ❌ 信号生成失败: {exc}", fg="red")
        ctx.exit(1)
        return

    click.echo("")
    click.echo(generator.format_signal(signal_df))
    logger.info(
        "Scan pool=%s date=%s n_buy=%d",
        pool,
        scan_date.date(),
        int((signal_df["signal"] == "buy").sum()),
    )
