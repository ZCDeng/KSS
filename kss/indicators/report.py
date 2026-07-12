"""固化指标的 AI回测报告生成：结构化 markdown，供 AI回测模块目录扫描发现.

报告里的每个数字都来自 ``kss.indicators.gate`` 裁决结果或 ``run_entry_pack`` 产出的
pack dict——本模块只排版，不重算、不臆造。
"""

from __future__ import annotations

from typing import Any

from kss.indicators.registry import RegistryEntry


def format_report(
    entry: RegistryEntry,
    packs: list[dict[str, Any]],
    verdict_payload: dict[str, Any] | None = None,
) -> str:
    """固化报告：标题 + 绩效摘要（经济意义维度数值）+ 五维裁决表 + 标的信号概览."""
    lines = [f"# {entry.name}", ""]
    lines.append(f"- 指标 id: `{entry.id}` · 基元族: `{entry.family}` · 参数: `{entry.params}`")
    lines.append(f"- 固化时间: {entry.solidified_at or 'unknown'}")
    lines.append("")

    econ_rows: list[tuple[str, dict[str, Any]]] = []
    if verdict_payload:
        for r in verdict_payload.get("results", []):
            if r.get("status") != "judged":
                continue
            econ = next((d for d in r.get("dimensions", []) if d.get("name") == "经济意义"), None)
            if econ:
                econ_rows.append((r.get("symbol", ""), econ.get("value") or {}))

    if econ_rows:
        lines.append("## 绩效摘要")
        lines.append("")
        lines.append("| 标的 | Sharpe | 总收益 | buy&hold |")
        lines.append("|------|--------|--------|----------|")
        for symbol, v in econ_rows:
            lines.append(
                f"| {symbol} | {v.get('strategy_sharpe', '-')} | "
                f"{v.get('strategy_total', '-')} | {v.get('buy_and_hold_total', '-')} |"
            )
        lines.append("")

    if verdict_payload:
        lines.append("## GO/NO-GO 裁决")
        lines.append("")
        lines.append("| 标的 · 维度 | 结论 | 说明 |")
        lines.append("|------|------|------|")
        for r in verdict_payload.get("results", []):
            if r.get("status") != "judged":
                continue
            for d in r.get("dimensions", []):
                mark = "GO" if d.get("passed") else "NO-GO"
                lines.append(f"| {r.get('symbol')} · {d.get('name')} | {mark} | {d.get('detail', '')} |")
        lines.append("")
        lines.append(f"**总裁决：{'GO' if verdict_payload.get('go') else 'NO-GO'}**")
        lines.append("")

    lines.append("## 标的与信号")
    lines.append("")
    lines.append("| 标的 | 状态 | 动作 | 交易笔数 |")
    lines.append("|------|------|------|------|")
    for p in packs:
        lines.append(
            f"| {p.get('symbol')} | {p.get('status')} | {p.get('action') or '-'} | "
            f"{len(p.get('trades') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)
