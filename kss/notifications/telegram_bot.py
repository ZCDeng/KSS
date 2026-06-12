"""Telegram bot 通知器 —— 默认走云 Bot API，可切自建 server.

设计取舍（cron 友好、极简）：

- **同步、带重试退避、最终失败 log warning 后 return False**——cron 友好;
  超时 / 网络异常 / 非 JSON 网关页 / 429 / 5xx 视为瞬时错误重试（默认 3 次、
  指数退避）；4xx 等确定性 API 错误（坏 token / chat 不存在 / Markdown 非法）
  立即放弃，重试无意义。重试次数与退避可经 ``TELEGRAM_MAX_ATTEMPTS`` /
  ``TELEGRAM_RETRY_BACKOFF`` env 覆盖.
- **不引 `python-telegram-bot` / `aiogram` 等异步重依赖**，``requests`` 直发 HTTP;
- **极简 API**：``send(message)`` 一参收工；title 由调用方自行 prepend 进 message.

部署：默认指向云 ``https://api.telegram.org``，零运维. 若需自建
``telegram-bot-api`` server（更大文件 / 本地存储），把 ``TELEGRAM_API_URL``
改成 ``http://127.0.0.1:8081`` 即可，Python 端代码不动.
"""

from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://api.telegram.org"
_HTTP_TIMEOUT_SEC = 10
_DEFAULT_MAX_ATTEMPTS = 3      # 总尝试次数（含首次）
_DEFAULT_BACKOFF_SEC = 1.0     # 首次重试前退避，之后每次翻倍（1s → 2s → 4s …）


class TelegramBot:
    """Telegram bot HTTP 客户端，默认对接云 Bot API（``api.telegram.org``）."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        api_url: str | None = None,
        *,
        max_attempts: int | None = None,
        backoff_sec: float | None = None,
    ) -> None:
        """初始化 bot.

        Args:
            token: BotFather 拿到的全 token，缺省读 ``TELEGRAM_BOT_TOKEN``.
            chat_id: 目标 chat id（个人/群/频道），缺省读 ``TELEGRAM_CHAT_ID``.
            api_url: API 服务地址，缺省读 ``TELEGRAM_API_URL``，再缺省云
                ``https://api.telegram.org``. 自建 server 时填
                ``http://127.0.0.1:8081``.
            max_attempts: 瞬时失败的总尝试次数（含首次），缺省读
                ``TELEGRAM_MAX_ATTEMPTS``，再缺省 ``3``. ``1`` = 不重试.
            backoff_sec: 首次重试前退避秒数（之后指数翻倍），缺省读
                ``TELEGRAM_RETRY_BACKOFF``，再缺省 ``1.0``. ``0`` = 立即重试.
        """
        self._token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._api_url = (
            api_url or os.getenv("TELEGRAM_API_URL") or _DEFAULT_API_URL
        ).rstrip("/")
        self._max_attempts = max(
            1,
            max_attempts if max_attempts is not None
            else int(os.getenv("TELEGRAM_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS)),
        )
        self._backoff_sec = (
            backoff_sec if backoff_sec is not None
            else float(os.getenv("TELEGRAM_RETRY_BACKOFF", _DEFAULT_BACKOFF_SEC))
        )

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """发送一条文本消息.

        Args:
            message: 消息正文. ``parse_mode=Markdown`` 时支持 ``*bold*`` /
                ``_italic_`` / 反引号代码等 Markdown V1 语法.
            parse_mode: ``Markdown`` (默认) / ``MarkdownV2`` / ``HTML`` /
                空串. Telegram Bot API 同名参数.

        Returns:
            服务器返回 ``ok=true`` 时 ``True``；瞬时错误（超时 / 网络 / 非 JSON /
            429 / 5xx）重试 ``max_attempts`` 次仍失败、或确定性 API 错误（4xx），
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
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        # 只有"瞬时"失败才走到循环底部重试；成功与确定性失败都在循环内 return。
        for attempt in range(1, self._max_attempts + 1):
            reason = ""
            try:
                resp = requests.post(url, json=payload, timeout=_HTTP_TIMEOUT_SEC)
            except requests.Timeout:
                reason = f"请求超时 ({_HTTP_TIMEOUT_SEC:.1f}s)"
            except requests.RequestException as exc:
                reason = f"网络异常: {exc}"
            else:
                try:
                    data = resp.json()
                except ValueError:
                    # 非 JSON（502 反代页面等）当瞬时网关错误重试
                    reason = (
                        f"响应非 JSON: status={resp.status_code} "
                        f"body={resp.text[:120]}"
                    )
                else:
                    if data.get("ok") is True:
                        if attempt > 1:
                            logger.info("[telegram] 第 %d 次尝试发送成功", attempt)
                        return True
                    # 429 限流 / 5xx 服务端错误可重试；其余 4xx 确定性错误立即放弃
                    if resp.status_code == 429 or resp.status_code >= 500:
                        reason = f"API {resp.status_code}: {data.get('description')}"
                    else:
                        desc = str(data.get("description") or "")
                        # Markdown 实体解析失败是内容问题（如标识符里的 _ 配对
                        # 错乱），重试同一 payload 无意义——降级纯文本重发一次，
                        # 告警必达优先于排版。parse_mode 已为空时不会再递归。
                        if parse_mode and "can't parse entities" in desc.lower():
                            logger.warning(
                                "[telegram] %s 实体解析失败（%s），降级纯文本重发",
                                parse_mode, desc,
                            )
                            return self.send(message, parse_mode="")
                        logger.warning(
                            "[telegram] API 错误: status=%s description=%s",
                            resp.status_code, desc,
                        )
                        return False

            if attempt < self._max_attempts:
                backoff = self._backoff_sec * (2 ** (attempt - 1))
                logger.warning(
                    "[telegram] %s，%.1fs 后重试 (%d/%d)",
                    reason, backoff, attempt, self._max_attempts,
                )
                time.sleep(backoff)
            else:
                logger.warning(
                    "[telegram] %s，已重试 %d 次仍失败",
                    reason, self._max_attempts - 1,
                )
        return False
