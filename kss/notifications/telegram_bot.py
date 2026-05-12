"""Telegram bot 通知器 —— 默认走云 Bot API，可切自建 server.

设计取舍（cron 友好、极简）：

- **同步、无 retry、失败 log warning 后 return False**——cron 友好;
- **不引 `python-telegram-bot` / `aiogram` 等异步重依赖**，``requests`` 直发 HTTP;
- **极简 API**：``send(message)`` 一参收工；title 由调用方自行 prepend 进 message.

部署：默认指向云 ``https://api.telegram.org``，零运维. 若需自建
``telegram-bot-api`` server（更大文件 / 本地存储），把 ``TELEGRAM_API_URL``
改成 ``http://127.0.0.1:8081`` 即可，Python 端代码不动.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://api.telegram.org"
_HTTP_TIMEOUT_SEC = 10


class TelegramBot:
    """Telegram bot HTTP 客户端，默认对接云 Bot API（``api.telegram.org``）."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        api_url: str | None = None,
    ) -> None:
        """初始化 bot.

        Args:
            token: BotFather 拿到的全 token，缺省读 ``TELEGRAM_BOT_TOKEN``.
            chat_id: 目标 chat id（个人/群/频道），缺省读 ``TELEGRAM_CHAT_ID``.
            api_url: API 服务地址，缺省读 ``TELEGRAM_API_URL``，再缺省云
                ``https://api.telegram.org``. 自建 server 时填
                ``http://127.0.0.1:8081``.
        """
        self._token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._api_url = (
            api_url or os.getenv("TELEGRAM_API_URL") or _DEFAULT_API_URL
        ).rstrip("/")

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """发送一条文本消息.

        Args:
            message: 消息正文. ``parse_mode=Markdown`` 时支持 ``*bold*`` /
                ``_italic_`` / 反引号代码等 Markdown V1 语法.
            parse_mode: ``Markdown`` (默认) / ``MarkdownV2`` / ``HTML`` /
                空串. Telegram Bot API 同名参数.

        Returns:
            服务器返回 ``ok=true`` 时 ``True``；其余（API 错、网络错、超时）
            一律 log warning + 返回 ``False``. **不抛异常**.
        """
        if not self._token or not self._chat_id:
            logger.warning(
                "[telegram] 缺少 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳过发送"
            )
            return False

        url = f"{self._api_url}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        try:
            resp = requests.post(url, json=payload, timeout=_HTTP_TIMEOUT_SEC)
        except requests.Timeout:
            logger.warning("[telegram] 请求超时 (%.1fs)", _HTTP_TIMEOUT_SEC)
            return False
        except requests.RequestException as exc:
            logger.warning("[telegram] 网络异常: %s", exc)
            return False

        try:
            data = resp.json()
        except ValueError:
            logger.warning(
                "[telegram] 响应非 JSON: status=%s body=%s",
                resp.status_code, resp.text[:200],
            )
            return False

        if data.get("ok") is True:
            return True

        logger.warning(
            "[telegram] API 错误: status=%s description=%s",
            resp.status_code, data.get("description"),
        )
        return False
