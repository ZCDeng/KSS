"""ConsoleNotifier 回归测试.

历史 bug：``Text.from_markup`` 把含方括号的普通文本当 rich markup 解析，
paper_trade 报告含 ``(long_low)`` / ``| 排名 |`` / ``[688114.SH]`` 等 Markdown
元素时抛 ``closing tag '[/xxx]' doesn't match any open tag``.
修复：title + message 一律走 ``Text(...)`` 不解析 markup.
"""

from __future__ import annotations

from kss.notifications.console import ConsoleNotifier


def test_send_returns_true() -> None:
    """smoke: 普通输入返回 True."""
    n = ConsoleNotifier()
    assert n.send(title="ok", message="hello") is True


def test_send_handles_brackets_in_message() -> None:
    """message 含 ``[xx]`` 字面量不应被当 rich markup 解析.

    Why: paper_trade 报告里的代码 / 排名 / 表头都可能含方括号；
    早期 ``Text.from_markup`` 会把这些当 tag 触发 closing-tag 错.
    """
    n = ConsoleNotifier()
    msg = "买入 [688114.SH] / 卖出 [688322.SH]\n排名 | 代码 | log_mv"
    assert n.send(title="t", message=msg) is True


def test_send_handles_brackets_in_title() -> None:
    """title 含 ``(long_low)`` 之类被 paper_trade 截首行带入的元素不应崩.

    Why: ConsoleNotifier 把 title 包成 ``[bold cyan]...[/cyan]`` 再渲染，
    若 title 本身含方括号，复合 markup 字符串会触发 rich 不匹配错.
    """
    n = ConsoleNotifier()
    bracket_title = "# 2026-05-11 横截面选股 (`log_mv` / 反向 (long_low))"
    assert n.send(title=bracket_title, message="body") is True


def test_send_supports_warning_alert_levels() -> None:
    """三档 level 都不抛."""
    n = ConsoleNotifier()
    assert n.send(title="t", message="m", level="info") is True
    assert n.send(title="t", message="m", level="warning") is True
    assert n.send(title="t", message="m", level="alert") is True


def test_is_available_always_true() -> None:
    """console 永可用."""
    assert ConsoleNotifier().is_available() is True
