"""企业微信 Webhook 通知器."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from kss.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class WechatNotifier(BaseNotifier):
    """通过企业微信群机器人 Webhook 发送文本/Markdown 消息.

    Webhook URL 读取优先级：
    1. 构造时传入的 ``webhook_url`` 参数；
    2. 环境变量 ``WECHAT_WEBHOOK``；
    3. 配置文件 ``notifications.wechat.webhook_url``（若传入 ``config``）。
    """

    name = "wechat"

    def __init__(self, webhook_url: str | None = None, config: dict[str, Any] | None = None) -> None:
        self._webhook_url = webhook_url or self._resolve_url(config)

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_url(config: dict[str, Any] | None) -> str | None:
        """按优先级解析 Webhook URL."""
        env_url = os.getenv("WECHAT_WEBHOOK")
        if env_url:
            return env_url.strip()
        if config:
            cfg_url = config.get("notifications", {}).get("wechat", {}).get("webhook_url")
            if cfg_url:
                return str(cfg_url).strip()
        return None

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    def send(self, title: str, message: str, level: str = "info", **kwargs: Any) -> bool:  # noqa: ARG002
        """调用企业微信 Webhook 发送 Markdown 消息.

        Args:
            title: 消息标题，会加粗置于消息头部.
            message: 消息正文（Markdown 格式）.
            level: 消息级别（仅影响日志，不影响微信样式）.

        Returns:
            HTTP 200 且微信返回 ``errcode == 0`` 时返回 ``True``。
        """
        if not self._webhook_url:
            logger.warning("[wechat] Webhook URL 未配置，跳过发送")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{title}**\n\n{message}",
            },
        }

        try:
            resp = requests.post(
                self._webhook_url,
                json=payload,
                timeout=(5, 15),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") == 0:
                logger.debug("[wechat] 发送成功: %s", title)
                return True
            logger.warning("[wechat] 发送失败: %s", data.get("errmsg"))
            return False
        except requests.RequestException as exc:
            logger.warning("[wechat] 网络异常: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # 可用性
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """检查 Webhook URL 是否已配置.

        Returns:
            ``True`` 当且仅当 ``webhook_url`` 非空.
        """
        return bool(self._webhook_url)
