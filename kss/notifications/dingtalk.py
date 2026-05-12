"""钉钉 Webhook 通知器."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from kss.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class DingtalkNotifier(BaseNotifier):
    """通过钉钉群机器人 Webhook 发送文本/Markdown 消息.

    Webhook URL 读取优先级：
    1. 构造时传入的 ``webhook_url`` 参数；
    2. 环境变量 ``DINGTALK_WEBHOOK``；
    3. 配置文件 ``notifications.dingtalk.webhook_url``（若传入 ``config``）。

    若机器人开启安全设置（加签），需在配置中提供 ``secret``，
    本实现会自动计算签名并追加到 URL。
    """

    name = "dingtalk"

    def __init__(
        self,
        webhook_url: str | None = None,
        secret: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._webhook_url = webhook_url or self._resolve_url(config)
        self._secret = secret or self._resolve_secret(config)

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_url(config: dict[str, Any] | None) -> str | None:
        """按优先级解析 Webhook URL."""
        env_url = os.getenv("DINGTALK_WEBHOOK")
        if env_url:
            return env_url.strip()
        if config:
            cfg_url = config.get("notifications", {}).get("dingtalk", {}).get("webhook_url")
            if cfg_url:
                return str(cfg_url).strip()
        return None

    @staticmethod
    def _resolve_secret(config: dict[str, Any] | None) -> str | None:
        """按优先级解析加签 Secret."""
        env_secret = os.getenv("DINGTALK_SECRET")
        if env_secret:
            return env_secret.strip()
        if config:
            cfg_secret = config.get("notifications", {}).get("dingtalk", {}).get("secret")
            if cfg_secret:
                return str(cfg_secret).strip()
        return None

    def _signed_url(self) -> str | None:
        """若配置了 secret，返回带签名的 URL；否则返回原 URL."""
        if not self._webhook_url:
            return None
        if not self._secret:
            return self._webhook_url

        import hashlib
        import time
        import urllib.parse

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self._secret}"
        hmac_code = hashlib.sha256(string_to_sign.encode("utf-8")).digest()
        import base64

        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode("utf-8"))
        sep = "&" if "?" in self._webhook_url else "?"
        return f"{self._webhook_url}{sep}timestamp={timestamp}&sign={sign}"

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    def send(self, title: str, message: str, level: str = "info", **kwargs: Any) -> bool:  # noqa: ARG002
        """调用钉钉 Webhook 发送 Markdown 消息.

        Args:
            title: 消息标题.
            message: 消息正文（Markdown 格式）.
            level: 消息级别（仅影响日志）.

        Returns:
            HTTP 200 且钉钉返回 ``errcode == 0`` 时返回 ``True``。
        """
        url = self._signed_url()
        if not url:
            logger.warning("[dingtalk] Webhook URL 未配置，跳过发送")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {title}\n\n{message}",
            },
        }

        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=(5, 15),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") == 0:
                logger.debug("[dingtalk] 发送成功: %s", title)
                return True
            logger.warning("[dingtalk] 发送失败: %s", data.get("errmsg"))
            return False
        except requests.RequestException as exc:
            logger.warning("[dingtalk] 网络异常: %s", exc)
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
