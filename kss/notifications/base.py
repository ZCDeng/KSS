"""通知器抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseNotifier(ABC):
    """所有通知通道的抽象基类.

    子类必须实现 :meth:`send` 与 :meth:`is_available`，
    分别负责消息投递与可用性自检。
    """

    name: str = "base"

    @abstractmethod
    def send(self, title: str, message: str, level: str = "info", **kwargs: Any) -> bool:
        """发送通知消息.

        Args:
            title: 消息标题.
            message: 消息正文（支持 Markdown 或纯文本，取决于实现）.
            level: 消息级别，可选 ``info`` / ``warning`` / ``alert``.
            **kwargs: 实现特定的额外参数.

        Returns:
            发送是否成功.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """判断当前通知通道是否可用.

        Returns:
            ``True`` 当且仅当通道配置完整且网络/服务正常.
        """
