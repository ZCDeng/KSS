"""控制台通知器 —— 基于 rich 的彩色终端输出."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from kss.notifications.base import BaseNotifier


class ConsoleNotifier(BaseNotifier):
    """控制台彩色输出通知器.

    使用 ``rich`` 库渲染带颜色与面板的终端消息，
    在本地调试与 CI 环境中最为常用，且**永远可用**。
    """

    name = "console"

    _LEVEL_STYLES: dict[str, str] = {
        "info": "cyan",
        "warning": "yellow",
        "alert": "red",
    }

    def __init__(self) -> None:
        self._console = Console(stderr=False)

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    def send(self, title: str, message: str, level: str = "info", **kwargs: Any) -> bool:  # noqa: ARG002
        """在终端打印彩色面板消息.

        Args:
            title: 消息标题.
            message: 消息正文.
            level: 消息级别，决定边框颜色.

        Returns:
            永远返回 ``True``.
        """
        color = self._LEVEL_STYLES.get(level, "white")
        panel = Panel(
            Text(message),
            title=Text(title, style=f"bold {color}"),
            border_style=color,
            expand=False,
        )
        self._console.print(panel)
        return True

    # ------------------------------------------------------------------ #
    # 可用性
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """控制台通知器永远可用.

        Returns:
            ``True``
        """
        return True
