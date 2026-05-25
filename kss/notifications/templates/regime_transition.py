"""宏观阶段切换 Telegram 告警模板 (plan 011).

把 :class:`kss.macro.queries.StageTransition` 渲染成 Telegram HTML 消息体.
渲染时附 Bolton 四阶段含义 + 按 ``sector_rotation.yaml`` 提炼的操作建议.

不直接发送 —— 调用方拿到字符串后走 ``kss.notifications.manager.send_to_channels``
统一推送，与 ``scan_combo_signals.py`` 保持一致 (parse_mode='HTML').
"""

from __future__ import annotations

from kss.macro.queries import StageTransition


STAGE_MEANING: dict[str, str] = {
    "I": "谷底前 (利率敏感 / 被压抑需求释放)",
    "II": "扩张 (上游资源 / 商品价格高峰)",
    "III": "顶部 (财富效应 / 资本品扩张)",
    "IV": "衰退 (防御 / 必需消费)",
}


# 切换到目标阶段时的操作建议 —— 来源 kss/config/sector_rotation.yaml
STAGE_ADVICE: dict[str, str] = {
    "I": "关注利率敏感板块 (汽车/地产/家电/建材)；减仓必需消费 / 公用事业",
    "II": "关注上游资源 + 周期股 (钢铁/煤炭/有色)；减仓防御板块",
    "III": "关注财富效应板块 (白酒/美容/军工/银行)；减仓上游周期 (钢铁/煤炭/地产)",
    "IV": "关注防御板块 (公用事业/必需消费/医药/通信)；减仓汽车/地产/上游周期",
}


def _format_delta(delta: float) -> str:
    """置信度变化带符号格式化，精确到两位小数."""
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.2f}"


def render_transition_alert(transition: StageTransition) -> str:
    """渲染单次阶段切换告警为 Telegram HTML 字符串.

    Args:
        transition: 检测到的阶段切换事件.

    Returns:
        可直接送入 ``send_to_channels(..., parse_mode='HTML')`` 的字符串.
        ``as_of_date`` 入参形如 ``YYYYMMDD``，输出展开为 ``YYYY-MM-DD``.
    """
    date = transition.as_of_date
    if len(date) == 8 and date.isdigit():
        date_disp = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    else:
        date_disp = date

    meaning = STAGE_MEANING.get(transition.to_stage, "未知阶段")
    advice = STAGE_ADVICE.get(transition.to_stage, "(无操作建议映射)")
    delta_str = _format_delta(transition.confidence_delta)

    lines = [
        f"🔄 <b>宏观阶段切换</b>: {transition.from_stage} → {transition.to_stage}",
        f"日期: {date_disp}",
        f"置信度变化: {delta_str}",
        f"含义: {meaning}",
        f"建议: {advice}",
    ]
    return "\n".join(lines)


__all__ = [
    "STAGE_ADVICE",
    "STAGE_MEANING",
    "render_transition_alert",
]
