"""KSS CLI 入口 —— Click 命令组与全局选项."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click
import yaml

from kss.cli.commands import (
    analyze,
    backtest,
    notify,
    predict,
    report,
    scan,
    train,
    update,
)


@click.group()
@click.option(
    "--config",
    "-c",
    default="./kss/config/settings.yaml",
    help="配置文件路径",
    type=click.Path(exists=False, dir_okay=False),
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="输出详细日志")
@click.option("--quiet", "-q", is_flag=True, default=False, help="仅输出错误日志")
@click.pass_context
def main(ctx: click.Context, config: str, verbose: bool, quiet: bool) -> None:
    """KSS —— 科创50成分股专业分析跟踪系统 CLI."""
    # 初始化共享上下文对象
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["config"] = _load_config(config)

    # 配置日志级别
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_config(path: str) -> dict[str, Any]:
    """安全加载 YAML 配置，失败时返回空字典."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        # 尝试相对于项目根目录定位
        alt = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if alt.exists():
            cfg_path = alt
        else:
            return {}
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("配置加载失败 (%s): %s", cfg_path, exc)
        return {}


# ------------------------------------------------------------------ #
# 注册子命令
# ------------------------------------------------------------------ #
main.add_command(update.cmd, name="update")
main.add_command(train.cmd, name="train")
main.add_command(backtest.cmd, name="backtest")
main.add_command(predict.cmd, name="predict")
main.add_command(scan.cmd, name="scan")
main.add_command(report.cmd, name="report")
main.add_command(notify.cmd, name="notify")
main.add_command(analyze.cmd, name="analyze")

if __name__ == "__main__":
    main()
