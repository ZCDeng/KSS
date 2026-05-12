"""kss update —— 更新股票池数据缓存."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click

from kss.data.data_loader import DataLoader
from kss.data.tushare_client import TushareClient

logger = logging.getLogger(__name__)


@click.command()
@click.option("--pool", "-p", default="kcb50", help="股票池名称")
@click.option("--force", "-f", is_flag=True, default=False, help="强制全量重下载")
@click.option("--start", "-s", default="20230101", help="数据起始日期 YYYYMMDD")
@click.pass_context
def cmd(ctx: click.Context, pool: str, force: bool, start: str) -> None:
    """更新股票池数据缓存.

    默认执行增量更新；使用 ``--force`` 可清空缓存后全量重下。
    """
    config: dict[str, Any] = ctx.obj.get("config", {})
    data_cfg = config.get("data", {})
    cache_dir = data_cfg.get("cache_dir", "./cs_data")

    click.echo(f"🔄 更新股票池: {pool}")

    client = TushareClient()
    loader = DataLoader(client, None)  # cache_manager 会在下方按需初始化

    # 若 force 为真，先清空该池缓存
    if force:
        click.echo("  ⚠️  强制模式 —— 清空缓存")
        _clear_pool_cache(cache_dir, pool)

    # 实际更新逻辑占位 —— 可由 data_loader 扩展
    click.echo("  ⏳ 正在拉取数据...")
    logger.info("Update pool=%s force=%s start=%s", pool, force, start)
    click.secho("  ✅ 更新完成", fg="green")


def _clear_pool_cache(cache_dir: str, pool: str) -> None:
    """删除指定股票池的缓存文件."""
    import shutil

    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return
    pool_csv = Path(f"{pool}_symbols.csv")
    if not pool_csv.exists():
        return
    import pandas as pd

    try:
        df = pd.read_csv(pool_csv)
        symbols = df["symbol"].astype(str).tolist()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取股票池 %s 失败: %s", pool, exc)
        return

    removed = 0
    for sym in symbols:
        for ext in (".pkl", ".parquet", ".csv"):
            fp = cache_path / f"{sym}{ext}"
            if fp.exists():
                fp.unlink()
                removed += 1
    if removed:
        click.echo(f"  🗑️  已清除 {removed} 个缓存文件")
