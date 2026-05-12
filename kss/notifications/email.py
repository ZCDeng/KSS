"""SMTP 邮件通知器."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from kss.notifications.base import BaseNotifier

logger = logging.getLogger(__name__)


class EmailNotifier(BaseNotifier):
    """通过 SMTP 发送邮件通知.

    配置读取优先级（从高到低）：
    1. 构造时显式传入的参数；
    2. 环境变量（``SMTP_HOST`` / ``SMTP_PORT`` / ``SMTP_USER`` / ``SMTP_PASS`` / ``SMTP_TO``）；
    3. 配置文件 ``notifications.email.*``。

    建议配合外部邮件服务（如 SendGrid、Amazon SES）或使用 Gmail / QQ 邮箱的 SMTP。
    """

    name = "email"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        to_addrs: list[str] | None = None,
        use_tls: bool = True,
        config: dict[str, Any] | None = None,
    ) -> None:
        cfg = self._resolve_config(config)
        self._host = host or cfg.get("host")
        self._port = port or cfg.get("port", 587)
        self._user = user or cfg.get("user")
        self._password = password or cfg.get("password")
        self._to_addrs = to_addrs or cfg.get("to_addrs", [])
        self._use_tls = use_tls if use_tls is not None else cfg.get("use_tls", True)

    # ------------------------------------------------------------------ #
    # 内部辅助
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
        """从环境变量与配置字典合并 SMTP 配置."""
        result: dict[str, Any] = {}
        if config:
            email_cfg = config.get("notifications", {}).get("email", {})
            result.update(email_cfg)

        env_map = {
            "host": "SMTP_HOST",
            "port": "SMTP_PORT",
            "user": "SMTP_USER",
            "password": "SMTP_PASS",
            "to_addrs": "SMTP_TO",
        }
        for key, env_name in env_map.items():
            val = os.getenv(env_name)
            if val:
                if key == "port":
                    val = int(val)
                elif key == "to_addrs":
                    val = [addr.strip() for addr in val.split(",")]
                result[key] = val
        return result

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    def send(self, title: str, message: str, level: str = "info", **kwargs: Any) -> bool:  # noqa: ARG002
        """通过 SMTP 发送 HTML 邮件.

        Args:
            title: 邮件主题.
            message: 邮件正文（会被包装为简单 HTML）.
            level: 消息级别（仅影响日志）.

        Returns:
            SMTP 投递成功返回 ``True``。
        """
        if not self._host or not self._user or not self._password:
            logger.warning("[email] SMTP 配置不完整，跳过发送")
            return False

        if not self._to_addrs:
            logger.warning("[email] 收件人地址为空，跳过发送")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = self._user
        msg["To"] = ", ".join(self._to_addrs)

        html_body = f"""\
<html>
  <body>
    <h2>{title}</h2>
    <pre style="font-family: sans-serif; white-space: pre-wrap;">{message}</pre>
  </body>
</html>
"""
        msg.attach(MIMEText(message, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self._host, self._port, timeout=15) as server:
                if self._use_tls:
                    server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, self._to_addrs, msg.as_string())
            logger.debug("[email] 发送成功: %s", title)
            return True
        except smtplib.SMTPException as exc:
            logger.warning("[email] SMTP 异常: %s", exc)
            return False
        except OSError as exc:
            logger.warning("[email] 网络异常: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # 可用性
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """检查 SMTP 必要参数是否已配置.

        Returns:
            ``True`` 当且仅当 host、user、password、to_addrs 均非空.
        """
        return bool(
            self._host and self._user and self._password and self._to_addrs
        )
