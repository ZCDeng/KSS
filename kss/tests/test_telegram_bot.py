"""TelegramBot 单元测试 —— 全 mock requests.post，不连真 Telegram.

覆盖：
- 成功路径（``ok=True`` → 返回 True）
- API 错误（``ok=False`` → 返回 False）
- 网络异常 / Timeout / 非 JSON 响应（全部返回 False，不抛）
- 缺凭据短路
- 默认 parse_mode=Markdown
- POST payload shape: ``chat_id`` / ``text`` / ``parse_mode``
- ``TELEGRAM_API_URL`` 缺省回退到 ``http://127.0.0.1:8081``
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from kss.notifications.telegram_bot import TelegramBot


# --------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------- #


def _ok_response() -> MagicMock:
    """构造一个 mock Response：HTTP 200 + ``{"ok": true}``."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    return resp


def _api_error_response(description: str = "Bad Request") -> MagicMock:
    """构造 telegram API 错误响应：HTTP 400 + ``{"ok": false, "description": ...}``."""
    resp = MagicMock()
    resp.status_code = 400
    resp.json.return_value = {"ok": False, "description": description}
    return resp


# --------------------------------------------------------------- #
# 成功路径
# --------------------------------------------------------------- #


def test_send_success_returns_true() -> None:
    """API ok=true → send 返回 True."""
    bot = TelegramBot(token="t", chat_id="c")
    with patch("kss.notifications.telegram_bot.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        assert bot.send("hello") is True


# --------------------------------------------------------------- #
# 失败 / 异常路径（一律 return False，不抛）
# --------------------------------------------------------------- #


def test_send_api_returns_not_ok_returns_false(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ok=false → 返回 False 并 log warning."""
    bot = TelegramBot(token="t", chat_id="c")
    with patch("kss.notifications.telegram_bot.requests.post") as mock_post:
        mock_post.return_value = _api_error_response("chat not found")
        with caplog.at_level("WARNING"):
            assert bot.send("hello") is False
    assert any("chat not found" in r.message for r in caplog.records)


def test_send_network_error_retries_then_returns_false() -> None:
    """``requests.RequestException`` 瞬时错误 → 重试耗尽后返回 False，不抛."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=3, backoff_sec=0)
    with patch(
        "kss.notifications.telegram_bot.requests.post",
        side_effect=requests.ConnectionError("boom"),
    ) as mock_post:
        assert bot.send("hello") is False
        assert mock_post.call_count == 3


def test_send_timeout_retries_then_returns_false() -> None:
    """``requests.Timeout`` → 重试耗尽后返回 False."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=3, backoff_sec=0)
    with patch(
        "kss.notifications.telegram_bot.requests.post",
        side_effect=requests.Timeout(),
    ) as mock_post:
        assert bot.send("hello") is False
        assert mock_post.call_count == 3


def test_send_non_json_response_retries_then_returns_false() -> None:
    """非 JSON（比如 502 反代页面）当瞬时网关错误重试，耗尽后 False 不抛."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=3, backoff_sec=0)
    bad = MagicMock()
    bad.status_code = 502
    bad.json.side_effect = ValueError("not json")
    bad.text = "<html>Bad Gateway</html>"
    with patch(
        "kss.notifications.telegram_bot.requests.post", return_value=bad,
    ) as mock_post:
        assert bot.send("hello") is False
        assert mock_post.call_count == 3


# --------------------------------------------------------------- #
# 重试 / 退避
# --------------------------------------------------------------- #


def test_send_retries_then_succeeds() -> None:
    """首次超时、第二次成功 → 返回 True，共调用 2 次（瞬时抖动自愈）."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=3, backoff_sec=0)
    with patch(
        "kss.notifications.telegram_bot.requests.post",
        side_effect=[requests.Timeout(), _ok_response()],
    ) as mock_post:
        assert bot.send("hello") is True
        assert mock_post.call_count == 2


def test_send_api_4xx_not_retried() -> None:
    """确定性 4xx API 错误（chat not found）不重试，立即 False，仅 1 次调用."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=3, backoff_sec=0)
    with patch(
        "kss.notifications.telegram_bot.requests.post",
        return_value=_api_error_response("chat not found"),
    ) as mock_post:
        assert bot.send("hello") is False
        assert mock_post.call_count == 1


def test_send_5xx_retried_then_false() -> None:
    """5xx 服务端错误可重试，耗尽后 False."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=2, backoff_sec=0)
    resp = MagicMock()
    resp.status_code = 503
    resp.json.return_value = {"ok": False, "description": "service unavailable"}
    with patch(
        "kss.notifications.telegram_bot.requests.post", return_value=resp,
    ) as mock_post:
        assert bot.send("hello") is False
        assert mock_post.call_count == 2


def test_send_max_attempts_one_disables_retry() -> None:
    """max_attempts=1 → 超时仅尝试 1 次，等价旧的"无重试"行为."""
    bot = TelegramBot(token="t", chat_id="c", max_attempts=1, backoff_sec=0)
    with patch(
        "kss.notifications.telegram_bot.requests.post",
        side_effect=requests.Timeout(),
    ) as mock_post:
        assert bot.send("hello") is False
        assert mock_post.call_count == 1


def test_send_without_token_short_circuits() -> None:
    """缺 token → 不发 HTTP 请求直接返回 False."""
    bot = TelegramBot(token="", chat_id="c")
    with patch("kss.notifications.telegram_bot.requests.post") as mock_post:
        assert bot.send("hello") is False
        mock_post.assert_not_called()


# --------------------------------------------------------------- #
# 默认值 / env 兜底
# --------------------------------------------------------------- #


def test_init_reads_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造时无显式参数：从 env 读 token/chat_id；TELEGRAM_API_URL 缺省云 API."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env_chat")
    monkeypatch.delenv("TELEGRAM_API_URL", raising=False)

    bot = TelegramBot()
    assert bot._token == "env_token"
    assert bot._chat_id == "env_chat"
    assert bot._api_url == "https://api.telegram.org"


def test_send_uses_markdown_parse_mode_by_default() -> None:
    """默认 parse_mode 应为 ``Markdown``."""
    bot = TelegramBot(token="t", chat_id="c")
    with patch("kss.notifications.telegram_bot.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        bot.send("hello")

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["parse_mode"] == "Markdown"


# --------------------------------------------------------------- #
# Payload shape
# --------------------------------------------------------------- #


def test_send_payload_shape() -> None:
    """POST 出去的 json body 形状必须含 chat_id / text / parse_mode，URL 走自建 API."""
    bot = TelegramBot(
        token="bot_token_xyz",
        chat_id="123456",
        api_url="http://127.0.0.1:8081",
    )
    with patch("kss.notifications.telegram_bot.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        bot.send("hello world", parse_mode="HTML")

    args, kwargs = mock_post.call_args
    # URL 拼接：{api_url}/bot{token}/sendMessage
    assert args[0] == "http://127.0.0.1:8081/botbot_token_xyz/sendMessage"
    body = kwargs["json"]
    assert body["chat_id"] == "123456"
    assert body["text"] == "hello world"
    assert body["parse_mode"] == "HTML"
    # 超时必须设置（cron 友好）
    assert kwargs["timeout"] == 10
