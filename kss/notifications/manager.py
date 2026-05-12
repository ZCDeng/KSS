"""通知管理器 —— 统一入口，维护多通道推送."""

from __future__ import annotations

import logging
from typing import Any

from kss.notifications.base import BaseNotifier
from kss.notifications.console import ConsoleNotifier
from kss.notifications.dingtalk import DingtalkNotifier
from kss.notifications.email import EmailNotifier

logger = logging.getLogger(__name__)


class NotificationManager:
    """通知通道管理器.

    根据配置自动初始化可用通道，并提供统一的发送入口。
    支持运行时动态注册/注销通知器。

    Example::

        mgr = NotificationManager(config)
        mgr.send("每日复盘", "今日信号: 3 买 1 卖", level="info")
    """

    _NOTIFIER_REGISTRY: dict[str, type[BaseNotifier]] = {
        "console": ConsoleNotifier,
        "dingtalk": DingtalkNotifier,
        "email": EmailNotifier,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化通知管理器.

        Args:
            config: 项目配置字典（通常从 ``settings.yaml`` 读取）。
        """
        self._config = config or {}
        self._notifiers: dict[str, BaseNotifier] = {}

        # 始终注册 console，作为兜底通道
        self.register(ConsoleNotifier())

        # 根据配置自动注册其他通道
        notif_cfg = self._config.get("notifications", {})
        enabled_channels = notif_cfg.get("channels", ["console"])

        for channel in enabled_channels:
            if channel == "console":
                continue  # 已注册
            notifier_cls = self._NOTIFIER_REGISTRY.get(channel)
            if notifier_cls is None:
                logger.warning("[notify] 未知通道: %s", channel)
                continue
            try:
                notifier = notifier_cls(config=self._config)  # type: ignore[arg-type]
                if notifier.is_available():
                    self.register(notifier)
                    logger.info("[notify] 已启用通道: %s", channel)
                else:
                    logger.info("[notify] 通道不可用，跳过: %s", channel)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[notify] 初始化通道 %s 失败: %s", channel, exc)

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    def send(self, title: str, message: str, level: str = "info", **kwargs: Any) -> dict[str, bool]:
        """向所有已注册且可用的通道广播消息.

        Args:
            title: 消息标题.
            message: 消息正文.
            level: 消息级别 ``info`` / ``warning`` / ``alert``.
            **kwargs: 透传给各通知器的额外参数.

        Returns:
            字典，键为通道名，值为发送结果 ``True/False``。
        """
        results: dict[str, bool] = {}
        for name, notifier in self._notifiers.items():
            try:
                ok = notifier.send(title, message, level=level, **kwargs)
                results[name] = ok
            except Exception as exc:  # noqa: BLE001
                logger.warning("[notify] 发送失败 [%s]: %s", name, exc)
                results[name] = False
        return results

    def register(self, notifier: BaseNotifier) -> None:
        """动态注册通知器.

        Args:
            notifier: 通知器实例.
        """
        self._notifiers[notifier.name] = notifier
        logger.debug("[notify] 注册通道: %s", notifier.name)

    def unregister(self, name: str) -> bool:
        """注销指定通道.

        Args:
            name: 通道名称.

        Returns:
            是否成功移除.
        """
        if name in self._notifiers:
            del self._notifiers[name]
            logger.debug("[notify] 注销通道: %s", name)
            return True
        return False

    def get_available_channels(self) -> list[str]:
        """列出当前所有可用通道名称.

        Returns:
            通道名称列表.
        """
        return [
            name for name, notifier in self._notifiers.items()
            if notifier.is_available()
        ]

    def get_notifier(self, name: str) -> BaseNotifier | None:
        """按名称获取已注册的通知器.

        Args:
            name: 通道名称.

        Returns:
            通知器实例，若未注册则返回 ``None``。
        """
        return self._notifiers.get(name)
