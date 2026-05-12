"""kss notify —— 测试通知通道."""

from __future__ import annotations

import logging
from typing import Any

import click

from kss.notifications.manager import NotificationManager

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--channel",
    "-c",
    default=None,
    help="指定通道测试；不指定则测试所有已启用通道",
)
@click.option(
    "--message",
    "-m",
    default="这是一条来自 KSS 的测试通知 🚀",
    help="测试消息内容",
)
@click.pass_context
def cmd(ctx: click.Context, channel: str | None, message: str) -> None:
    """测试通知通道连通性.

    发送一条测试消息到指定通道或全部已启用通道，验证配置是否正确。
    """
    config: dict[str, Any] = ctx.obj.get("config", {})
    mgr = NotificationManager(config=config)

    click.echo(f"📡 通知测试 | 通道: {channel or '全部'}")

    if channel:
        notifier = mgr.get_notifier(channel)
        if notifier is None:
            click.secho(f"  ❌ 通道未注册: {channel}", fg="red")
            return
        ok = notifier.send("KSS 通知测试", message, level="info")
        if ok:
            click.secho(f"  ✅ {channel} 发送成功", fg="green")
        else:
            click.secho(f"  ❌ {channel} 发送失败", fg="red")
    else:
        results = mgr.send("KSS 通知测试", message, level="info")
        for ch, ok in results.items():
            color = "green" if ok else "red"
            icon = "✅" if ok else "❌"
            click.secho(f"  {icon} {ch}: {'成功' if ok else '失败'}", fg=color)

    logger.info("Notify test channel=%s", channel)
