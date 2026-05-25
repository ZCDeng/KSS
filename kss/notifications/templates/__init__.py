"""通知消息模板集 —— 把领域事件渲染成各通道可发送的字符串.

每个模板模块导出一个 ``render_*`` 入口函数，调用方负责走 NotificationManager
或 ``send_to_channels`` 把返回字符串送出去.
"""

from __future__ import annotations

from kss.notifications.templates.regime_transition import (
    STAGE_MEANING,
    render_transition_alert,
)

__all__ = [
    "STAGE_MEANING",
    "render_transition_alert",
]
