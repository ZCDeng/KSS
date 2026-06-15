"""交付与监控接线（U8）—— 影子 / 压测结果推 Telegram，避免静默丢失.

复用既有转义与降级链：

- 任何用户数据字段经 :func:`_md_v1_escape`（与 ``cross_sectional_forecast`` 同口径）
  转义，防 ``*ST航图`` / ``5G_概念`` 这类保留字使 Telegram 400 静默丢推送
  （见 docs/solutions/telegram_markdown_v1_silent_drop.md）。
- 发送前 :func:`dry_run_probe` 校验 Markdown V1 实体平衡（chat_id=0 dry-run 语义：
  只校验不投递）；不平衡 **fail loud**（抛 :class:`NotifyProbeError`），不静默返回 False。
- 竖卡布局：V1 不渲染表格，全部信息竖排。

R16, R17。
"""

from __future__ import annotations

import logging

from kss.prediction.cross_sectional_forecast import _md_v1_escape

logger = logging.getLogger(__name__)

_V1_PAIR_CHARS = ("*", "_", "`")


class NotifyProbeError(RuntimeError):
    """dry-run 探针失败：消息会被 Telegram V1 拒绝（fail loud，不静默丢）."""


def _count_unescaped(s: str, ch: str) -> int:
    """统计未被反斜杠转义的 ``ch`` 个数."""
    n = 0
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2  # 跳过被转义的下一个字符
            continue
        if s[i] == ch:
            n += 1
        i += 1
    return n


def dry_run_probe(message: str) -> None:
    """发送前 dry-run 探针（chat_id=0 语义：只校验不投递）.

    校验 Markdown V1 成对实体（``* _ ```）平衡 + ``[`` 与 ``]`` 配对。卡片里的
    ``*bold*`` 是成对的（偶数）、用户字段已被 :func:`_md_v1_escape` 反斜杠转义
    （不计入）；若有漏转义的保留字导致不平衡 → 抛 :class:`NotifyProbeError`。

    Raises:
        NotifyProbeError: 实体不平衡（会触发 Telegram ``can't parse entities``）.
    """
    for ch in _V1_PAIR_CHARS:
        if _count_unescaped(message, ch) % 2 != 0:
            raise NotifyProbeError(f"Markdown V1 实体不平衡：'{ch}' 未配对（会被 400 拒）")
    if _count_unescaped(message, "[") != _count_unescaped(message, "]"):
        raise NotifyProbeError("Markdown V1 链接括号 '[' / ']' 不配对")


def render_shadow_card(verdict, metrics: dict, *, title: str = "Kronos 影子日报") -> str:
    """影子毕业状态 → 合规竖卡（无表格）.

    Args:
        verdict: :class:`kss.kronos.shadow.ShadowVerdict`.
        metrics: :func:`weekly_validation_metrics` 输出.
        title: 卡片标题.
    """
    if verdict.graduated:
        grad = "✅ 可毕业"
    elif not verdict.window_full:
        grad = "只读（窗口未满）"
    else:
        grad = "只读（未过 mined 闸门）"

    def _num(x, fmt):
        return fmt.format(x) if x is not None and x == x else "—"  # x==x 排除 NaN

    lines = [
        f"*{_md_v1_escape(title)}*",
        "",
        f"毕业状态: {grad}",
        f"前向窗口: {verdict.n_obs} 日（{'满' if verdict.window_full else '未满'}）",
        f"Sharpe: {_num(verdict.sharpe, '{:.2f}')} | DSR: {_num(verdict.dsr, '{:.2f}')}",
        f"方向命中: {_num(metrics.get('dir_rate'), '{:.0%}')}"
        f" | Brier: {_num(metrics.get('brier'), '{:.3f}')}",
        f"80% 带覆盖: {_num(metrics.get('coverage'), '{:.0%}')}（n={metrics.get('n', 0)}）",
        "",
        "_影子只读，达标前不影响任何决策_",
    ]
    return "\n".join(lines)


def render_stress_card(report, *, title: str = "Kronos 合成压测诊断") -> str:
    """压测假阳性率诊断 → 合规竖卡（明确非阻断闸门）."""
    if report.false_positive_rate is None:
        fpr_line = f"假阳性率: — （{_md_v1_escape(report.note)}）"
    else:
        fpr_line = (
            f"闸门假阳性率: {report.false_positive_rate:.1%}"
            f"（{report.n_deployable}/{report.n_trials}）"
        )
    lines = [
        f"*{_md_v1_escape(title)}*",
        "",
        f"策略族: {_md_v1_escape(str(report.strategy_family))}",
        f"有效 trial: {report.n_trials}",
        fpr_line,
        "",
        "_诊断, 非阻断闸门；不阻止任何策略上线_",
    ]
    return "\n".join(lines)


def send_card(
    message: str,
    *,
    channel: str = "console",
    dry_run: bool = False,
    title: str | None = None,
) -> bool:
    """探针校验后推送竖卡；探针失败 fail loud（不静默返回 False）.

    Args:
        message: 已渲染（用户字段已转义）的竖卡正文.
        channel: ``console`` / ``telegram`` / ``all``.
        dry_run: True 时只跑探针 + 打印，不投递.
        title: 可选标题（console 通道需要）.

    Returns:
        发送是否成功（``console`` / ``dry_run`` 恒 True）.

    Raises:
        NotifyProbeError: 探针发现 Markdown 不平衡（fail loud）.
    """
    dry_run_probe(message)  # 失败抛错，不静默
    if dry_run or channel == "console":
        print(message)
        return True
    from kss.notifications.manager import send_to_channels

    results = send_to_channels(
        message, channel, title=title or "Kronos", parse_mode="Markdown"
    )
    if channel in ("telegram", "all"):
        return bool(results.get("telegram", False))
    return True
