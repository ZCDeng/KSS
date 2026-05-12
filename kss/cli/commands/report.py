"""kss report —— 复盘报告（暂未实现）."""

from __future__ import annotations

import click


@click.command()
@click.option(
    "--type",
    "-t",
    "report_type",
    type=click.Choice(["daily", "weekly"]),
    default="daily",
    help="报告类型",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="输出文件路径（保留以兼容未来实现）",
    type=click.Path(),
)
@click.pass_context
def cmd(ctx: click.Context, report_type: str, output: str | None) -> None:
    """生成复盘报告.

    本命令依赖 ``scan`` / ``predict`` / ``backtest`` 输出的聚合，目前缺少持久化
    层与跨命令结果通道，暂未实现以避免输出错误的占位报告。请直接使用对应子命令
    获得当前信号与回测结果。
    """
    raise click.ClickException(
        f"`kss report --type {report_type}` 尚未实现：需先建立 scan/predict/backtest"
        " 的结果持久化通道。请直接使用对应子命令获得当前信号与回测结果。"
    )
