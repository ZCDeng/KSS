"""板块复盘 Markdown 格式化 —— 5 段式推送报告.

由调用方负责打分 + 切片（Top N），本模块只负责组装 markdown.

设计原则（守护 S620 表头 bug）：

- 所有表格表头用 ``"| " + " | ".join(cols) + " |"`` 列表拼接，
  禁止 ``"| col1 |" + "col2 |"`` 字符串累加 —— 后者管道分隔符易丢.
"""

from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

from kss.sector.kcb_overlay import KcbOverlay

logger = logging.getLogger(__name__)

# 缺失数据时该 section 的占位文本
_MISSING_PLACEHOLDER = "_数据暂缺_"


def _format_pct(v: float | int | None) -> str:
    """涨跌幅 / 比率统一显示：保留 2 位小数 + ``%`` 后缀，None → ``—``."""
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):+.2f}%"


def _format_amount_yi(v: float | int | None) -> str:
    """金额（元）→ 亿元，2 位小数；None → ``—``.

    Tushare ``net_amount`` 等字段单位是元；除 1e8 → 亿元更可读.
    """
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v) / 1e8:+.2f} 亿"


def _kcb_badge(count: int) -> str:
    """KCB 池子持仓数：0 → ``—``，>0 → ``⭐N``."""
    return f"⭐{count}" if count > 0 else "—"


def _markdown_table(headers: Iterable[str], rows: list[list[str]]) -> str:
    """安全拼接 Markdown 表格 —— 列表 join 而非字符串累加（S620 教训）.

    Args:
        headers: 表头列名.
        rows: 每行的字符串列表（长度必须与 headers 一致）.

    Returns:
        三行起步的 markdown 表格字符串.
    """
    header_cols = list(headers)
    n = len(header_cols)
    lines = [
        "| " + " | ".join(header_cols) + " |",
        "| " + " | ".join(["---"] * n) + " |",
    ]
    for row in rows:
        cells = [str(c) for c in row]
        if len(cells) != n:
            logger.warning("行长度 %d != 表头 %d，截断/补齐", len(cells), n)
            cells = (cells + ["—"] * n)[:n]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ====================================================================== #
# 各 section 渲染
# ====================================================================== #


def _render_industry_heat(
    df: pd.DataFrame | None,
    overlay: KcbOverlay,
) -> str:
    """🔥 行业 Top N 强势."""
    title = "🔥 *行业 Top 强势*（涨幅 + 主力净流入 + 大单买入率）"
    if df is None or df.empty:
        return f"{title}\n\n{_MISSING_PLACEHOLDER}"
    headers = ["#", "板块", "涨幅", "主力净流入率", "大单买入率", "综合分", "KCB 池"]
    rows: list[list[str]] = []
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        rows.append([
            str(idx),
            str(row.get("name", "—")),
            _format_pct(row.get("pct_change")),
            _format_pct(row.get("net_amount_rate")),
            _format_pct(row.get("buy_elg_amount_rate")),
            f"{float(row.get('heat_score', 0)):.2f}",
            _kcb_badge(overlay.count_for_industry(row.get("name", ""))),
        ])
    return f"{title}\n\n{_markdown_table(headers, rows)}"


def _render_flow_persistence(
    df: pd.DataFrame | None,
    overlay: KcbOverlay,
) -> str:
    """💰 行业资金涌入 Top N（持续性视角）."""
    title = "💰 *行业资金涌入*（N 日累计 + 连续净流入天数）"
    if df is None or df.empty:
        return f"{title}\n\n{_MISSING_PLACEHOLDER}"
    headers = ["#", "板块", "累计净流入率", "连涨天数", "KCB 池"]
    rows: list[list[str]] = []
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        rows.append([
            str(idx),
            str(row.get("name", "—")),
            _format_pct(row.get("cum_inflow")),
            f"{int(row.get('persist_days', 0))} 天",
            _kcb_badge(overlay.count_for_industry(row.get("name", ""))),
        ])
    return f"{title}\n\n{_markdown_table(headers, rows)}"


def _render_concept_heat(
    df: pd.DataFrame | None,
    overlay: KcbOverlay,
) -> str:
    """🎯 概念 Top N 强势（同花顺）."""
    title = "🎯 *概念板块 Top 强势*（同花顺）"
    if df is None or df.empty:
        return f"{title}\n\n{_MISSING_PLACEHOLDER}"
    headers = ["#", "概念", "涨幅", "净流入", "综合分", "KCB 池"]
    rows: list[list[str]] = []
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        rows.append([
            str(idx),
            str(row.get("name", "—")),
            _format_pct(row.get("pct_change")),
            _format_amount_yi(row.get("net_amount")),
            f"{float(row.get('heat_score', 0)):.2f}",
            _kcb_badge(overlay.count_for_concept(row.get("name", ""))),
        ])
    return f"{title}\n\n{_markdown_table(headers, rows)}"


def _render_northbound(nb: dict[str, float] | None) -> str:
    """🌍 北向资金单行汇总."""
    title = "🌍 *北向资金*"
    if nb is None:
        return f"{title}: {_MISSING_PLACEHOLDER}"
    north = nb.get("north_money", 0.0)
    # Tushare 单位是百万元（万元 × 100），换算到亿元：万元 / 10000 = 亿元
    north_yi = north / 1e4
    direction = "净流入" if north_yi > 0 else ("净流出" if north_yi < 0 else "持平")
    return f"{title}: {direction} `{north_yi:+.2f}` 亿元（单日累计）"


def _render_rotation_signal(df: pd.DataFrame | None, overlay: KcbOverlay) -> str:
    """🔄 轮动信号（排名跃升 + 今日净流入）—— 可选 section.

    Returns:
        若无信号则返回空字符串（不渲染）.
    """
    if df is None or df.empty:
        return ""
    title = "🔄 *轮动信号*（排名跃升 + 今日净流入）"
    headers = ["板块", "今日排名", "→  N 日前", "排名跃升", "今日资金流", "KCB 池"]
    rows: list[list[str]] = []
    for _, row in df.iterrows():
        rows.append([
            str(row.get("name", "—")),
            str(int(row.get("today_rank", 0))),
            str(int(row.get("past_rank", 0))),
            f"+{int(row.get('rank_jump', 0))}",
            _format_pct(row.get("today_flow")),
            _kcb_badge(overlay.count_for_industry(row.get("name", ""))),
        ])
    return f"{title}\n\n{_markdown_table(headers, rows)}"


def _render_missing_footer(missing: list[str] | None) -> str:
    if not missing:
        return ""
    return f"⚠️ 缺失数据源: `{', '.join(missing)}`"


# ====================================================================== #
# 主入口
# ====================================================================== #


def format_review_markdown(
    trade_date: str,
    industry_heat: pd.DataFrame | None,
    flow_persistence: pd.DataFrame | None,
    concept_heat: pd.DataFrame | None,
    northbound: dict[str, float] | None,
    overlay: KcbOverlay,
    rotation_signal: pd.DataFrame | None = None,
    missing: list[str] | None = None,
) -> str:
    """组装板块复盘 Markdown 推送报告（5 段固定 + 可选轮动信号）.

    Args:
        trade_date: 报告日期，可读字符串（例如 ``"2026-05-12"`` 或 ``"20260512"``）.
        industry_heat: 行业热度 Top N DataFrame（含 ``name`` / ``pct_change`` /
            ``net_amount_rate`` / ``buy_elg_amount_rate`` / ``heat_score``）.
        flow_persistence: 资金持续性 Top N DataFrame（含 ``name`` /
            ``cum_inflow`` / ``persist_days``）.
        concept_heat: 概念热度 Top N DataFrame（含 ``name`` / ``pct_change`` /
            ``net_amount`` / ``heat_score``）.
        northbound: 北向资金单日汇总 dict（含 ``north_money`` 等字段，万元）.
        overlay: :class:`KcbOverlay` 实例，用于 KCB 池子标注.
        rotation_signal: 可选，轮动信号 DataFrame；空 / None 时此 section 不渲染.
        missing: 数据源缺失列表，用于报告底部告警.

    Returns:
        组装后的 Markdown 字符串，可直接送 console / Telegram.
    """
    sections: list[str] = []
    sections.append(f"📊 *板块复盘* `{trade_date}`")
    sections.append(_render_industry_heat(industry_heat, overlay))
    sections.append(_render_flow_persistence(flow_persistence, overlay))
    sections.append(_render_concept_heat(concept_heat, overlay))

    rot = _render_rotation_signal(rotation_signal, overlay)
    if rot:
        sections.append(rot)

    sections.append(_render_northbound(northbound))

    footer = _render_missing_footer(missing)
    if footer:
        sections.append(footer)

    return "\n\n".join(sections)
