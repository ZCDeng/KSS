"""scripts/paper_trade_log_mv.py 推送通道路由的单元测试.

只覆盖 ``_send_notification`` 的 channel 路由 + 通道失败容错，不触达
真实数据加载 / Telegram HTTP 调用——后者由
``kss/tests/test_telegram_bot.py`` 负责.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "paper_trade_log_mv.py"


def _load_script_module():
    """以模块形式加载脚本（不执行 main），用来直接测内部 helper."""
    spec = importlib.util.spec_from_file_location(
        "paper_trade_log_mv_for_tests", SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def script_mod():
    return _load_script_module()


# ============================================================ #
# _build_channels 通道列表展开
# ============================================================ #


class TestBuildChannels:
    """``_build_channels`` 的纯函数行为."""

    def test_console_returns_console_only(self, script_mod) -> None:
        assert script_mod._build_channels("console") == ["console"]

    def test_telegram_returns_telegram_only(self, script_mod) -> None:
        assert script_mod._build_channels("telegram") == ["telegram"]

    def test_all_returns_both_in_order(self, script_mod) -> None:
        """``all`` 必须先 console 后 telegram，避免 cron 日志被 telegram 网络抖动堵在前."""
        assert script_mod._build_channels("all") == ["console", "telegram"]


# ============================================================ #
# _send_notification 通道分发
# ============================================================ #


class TestSendNotification:
    """``_send_notification`` 按 channel 分发，每通道独立失败不影响他通道."""

    def test_console_channel_uses_console_notifier(self, script_mod) -> None:
        """channel=console：调 ConsoleNotifier，不触发 telegram HTTP.

        ConsoleNotifier.send 内部使用 rich Panel/Text，对某些 title 字符串
        渲染会抛 MarkupError（pre-existing 行为，与本次 telegram 迁移无关）.
        本测试 mock 掉 rich 渲染，只验证 dispatch 路由.
        """
        with patch(
            "kss.notifications.telegram_bot.requests.post",
        ) as mock_post, patch(
            "kss.notifications.console.ConsoleNotifier.send",
            return_value=True,
        ) as mock_console:
            results = script_mod._send_notification(
                message="msg", channel="console", title="t",
            )
        # 不应触发任何 telegram HTTP
        mock_post.assert_not_called()
        mock_console.assert_called_once()
        assert results == {"console": True}

    def test_telegram_channel_calls_telegram_bot(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """channel=telegram：调 TelegramBot.send，使用 env 凭据."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

        with patch(
            "kss.notifications.telegram_bot.requests.post",
        ) as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}
            results = script_mod._send_notification(
                message="正文", channel="telegram", title="标题",
            )

        assert results == {"telegram": True}
        # title 必须被 prepend 到 message
        body = mock_post.call_args.kwargs["json"]
        assert "正文" in body["text"]
        assert "标题" in body["text"]

    def test_all_channel_dispatches_to_both(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """channel=all：console + telegram 都被调."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
        with patch(
            "kss.notifications.telegram_bot.requests.post",
        ) as mock_post, patch(
            "kss.notifications.console.ConsoleNotifier.send",
            return_value=True,
        ):
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"ok": True}
            results = script_mod._send_notification(
                message="msg", channel="all", title="t",
            )
        assert set(results.keys()) == {"console", "telegram"}
        assert results["console"] is True
        assert results["telegram"] is True

    def test_telegram_failure_does_not_break_console(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """telegram 抛异常 → console 仍然成功，整体不爆."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
        with patch(
            "kss.notifications.telegram_bot.requests.post",
            side_effect=RuntimeError("simulated outage"),
        ), patch(
            "kss.notifications.console.ConsoleNotifier.send",
            return_value=True,
        ):
            with caplog.at_level("WARNING"):
                results = script_mod._send_notification(
                    message="msg", channel="all", title="t",
                )
        # console 成功 + telegram 失败但不抛
        assert results["console"] is True
        assert results["telegram"] is False

    def test_telegram_without_creds_returns_false_silently(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """channel=telegram 但无凭据：返回 False 不抛."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        results = script_mod._send_notification(
            message="msg", channel="telegram", title="t",
        )
        assert results == {"telegram": False}
