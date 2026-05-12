"""KSS 通知模块 —— 多通道消息推送管理器与各类通知器实现."""

from __future__ import annotations

from kss.notifications.base import BaseNotifier
from kss.notifications.console import ConsoleNotifier
from kss.notifications.dingtalk import DingtalkNotifier
from kss.notifications.email import EmailNotifier
from kss.notifications.manager import NotificationManager
from kss.notifications.telegram_bot import TelegramBot

__all__ = [
    "BaseNotifier",
    "ConsoleNotifier",
    "DingtalkNotifier",
    "EmailNotifier",
    "NotificationManager",
    "TelegramBot",
]
