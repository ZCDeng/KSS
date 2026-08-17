"""Editorial HTML for compiled investment reports.

The desktop preview injects the document body into a Kami reading shell, so
daily markup must carry its own scoped CSS and must not rely on ``<body>``
classes or process-section chrome.
"""

from __future__ import annotations

import html
from typing import Any

from kss.research.report_models import ReportDocument, ReportSection

_METRIC_ANCHORS = {
    "m_temperature": "temperature",
    "m_consensus": "theme-consensus",
    "m_risk": "risk-radar",
}

DAILY_CSS = """
    .kss-report.report-daily {
      --ink: #122033;
      --ink-soft: #3d4f63;
      --ink-faint: #6b7c8d;
      --line: #d7e0ea;
      --paper: #ffffff;
      --canvas: transparent;
      --navy: #10243e;
      --blue: #1677c8;
      --blue-soft: #eef5fc;
      color: var(--ink);
      background: var(--canvas);
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif;
      font-size: 15.5px;
      line-height: 1.78;
    }
    .kss-report.report-daily * { box-sizing: border-box; }
    .kss-report.report-daily h1,
    .kss-report.report-daily h2,
    .kss-report.report-daily h3,
    .kss-report.report-daily p { margin: 0; }
    .daily-sheet {
      display: grid;
      gap: 28px;
      padding: 8px 2px 12px;
    }
    .daily-hero {
      display: grid;
      gap: 12px;
      padding: 0 0 22px;
      border-top: 4px solid var(--navy);
      border-bottom: 1px solid var(--line);
    }
    .daily-kicker,
    .daily-chip-label,
    .daily-section-kicker,
    .daily-card-kicker {
      color: var(--ink-faint);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      line-height: 1.3;
      text-transform: uppercase;
    }
    .daily-hero-title {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }
    .kss-report.report-daily h1 {
      max-width: 34rem;
      color: var(--ink);
      font-size: clamp(28px, 3vw, 34px);
      font-weight: 750;
      letter-spacing: -.03em;
      line-height: 1.18;
    }
    .daily-dek {
      max-width: 46rem;
      color: var(--ink-soft);
      font-size: 16px;
      line-height: 1.6;
    }
    .daily-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .daily-chip {
      display: grid;
      gap: 2px;
      min-width: 7.5rem;
      padding: 8px 10px;
      border: 1px solid var(--line);
      background: var(--paper);
    }
    .daily-chip-value {
      color: var(--ink);
      font-size: 13px;
      font-weight: 650;
      line-height: 1.35;
    }
    .daily-audit {
      flex: 0 0 auto;
      margin-top: 6px;
      border: 1px solid #a9cbea;
      border-radius: 999px;
      color: #075b99;
      background: var(--blue-soft);
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      padding: 7px 10px;
      white-space: nowrap;
    }
    .daily-audit.is-failed {
      border-color: #f7b4ad;
      color: #b42318;
      background: #fff1f0;
    }
    .daily-metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .daily-metric {
      display: grid;
      align-content: start;
      gap: 8px;
      min-height: 124px;
      padding: 14px 14px 12px;
      border: 1px solid var(--line);
      border-top: 3px solid #8fbee6;
      background: var(--paper);
    }
    .kss-report.report-daily .daily-metric .metric-label {
      color: var(--ink-soft);
      font-size: 13px;
      font-weight: 650;
      line-height: 1.35;
    }
    .kss-report.report-daily .daily-metric .metric-value {
      color: var(--navy);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 26px;
      font-weight: 750;
      letter-spacing: -.045em;
      line-height: 1;
    }
    .daily-metric-caption {
      color: var(--ink-faint);
      font-size: 12px;
      line-height: 1.4;
    }
    .daily-lead {
      display: grid;
      gap: 10px;
      padding: 4px 0 2px 16px;
      border-left: 3px solid var(--navy);
    }
    .kss-report.report-daily .daily-lead p {
      max-width: 68ch;
      color: var(--ink);
      font-size: 16.5px;
      font-weight: 550;
      line-height: 1.7;
    }
    .daily-body {
      display: grid;
      gap: 14px;
    }
    .kss-report.report-daily .daily-body p {
      max-width: 68ch;
      color: #243446;
      font-size: 15.5px;
      line-height: 1.82;
    }
    .daily-cards {
      display: grid;
      gap: 12px;
    }
    .daily-card-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px;
    }
    .daily-card {
      display: grid;
      gap: 8px;
      padding: 14px 15px;
      border: 1px solid var(--line);
      border-left: 3px solid #7ab8e6;
      background: var(--paper);
    }
    .kss-report.report-daily .daily-card strong {
      color: var(--ink);
      font-size: 15px;
      line-height: 1.4;
    }
    .kss-report.report-daily .daily-card p {
      color: #344054;
      font-size: 13.5px;
      line-height: 1.6;
    }
    .daily-notes {
      display: grid;
      gap: 10px;
      padding-top: 8px;
      border-top: 1px solid var(--line);
    }
    .kss-report.report-daily .daily-notes h2 {
      color: var(--ink);
      font-size: 15px;
      font-weight: 700;
      letter-spacing: -.01em;
    }
    .daily-note {
      max-width: 72ch;
      color: var(--ink-soft);
      font-size: 12.5px;
      line-height: 1.65;
    }
    .daily-note.is-meta {
      color: var(--ink-faint);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11.5px;
    }
    @media (max-width: 820px) {
      .daily-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .daily-hero-title { display: block; }
      .daily-audit { display: inline-block; margin-top: 10px; }
    }
    @media (max-width: 560px) {
      .daily-metrics { grid-template-columns: 1fr; }
    }
"""


def display_period(date_range: str, as_of: str) -> str:
    text = (date_range or "").strip()
    if "_to_" in text:
        start, end = text.split("_to_", 1)
        if start == end:
            return start
        return f"{start} 至 {end}"
    return text or as_of or "未指定"


def _section_map(document: ReportDocument) -> dict[str, ReportSection]:
    return {section.anchor: section for section in document.sections}


def _paragraphs(section: ReportSection | None) -> list[str]:
    if section is None:
        return []
    out: list[str] = []
    for block in section.blocks:
        text = str(block.text or "").strip()
        if text:
            out.append(text)
    return out


def _metric_ids(document: ReportDocument) -> list[str]:
    seen: list[str] = []
    for section in document.sections:
        for block in section.blocks:
            if block.type != "metric_group":
                continue
            for metric_id in block.metric_refs:
                if metric_id not in seen:
                    seen.append(metric_id)
    return seen


def _card_rows(section: ReportSection | None) -> list[dict[str, Any]]:
    if section is None:
        return []
    rows: list[dict[str, Any]] = []
    for block in section.blocks:
        if block.type == "precision_cards" or block.rows:
            rows.extend(row for row in block.rows if isinstance(row, dict))
    return rows


def render_daily_html(
    compiler: Any,
    document: ReportDocument,
    *,
    audit: dict[str, Any],
    draft: bool,
) -> str:
    from kss.research.compiler import CSP, REPORT_CSS

    metrics = document.metric_ledger.by_id()
    sections = _section_map(document)
    paragraphs = _paragraphs(sections.get("overview"))
    lead = paragraphs[0] if paragraphs else (document.subtitle or document.title)
    body = paragraphs[1:]
    audit_passed = audit.get("status") == "pass"
    audit_label = "审计通过" if audit_passed else "审计未通过"
    audit_class = "daily-audit" if audit_passed else "daily-audit is-failed"
    watermark = (
        '<div class="watermark">草稿 · 审计未通过 · 不得正式发布</div>' if draft else ""
    )
    period = display_period(document.date_range, document.as_of)
    metric_cards = []
    for metric_id in _metric_ids(document):
        metric = metrics.get(metric_id)
        if metric is None:
            continue
        anchor = _METRIC_ANCHORS.get(metric_id, metric_id)
        metric_cards.append(
            "<article class=\"daily-metric\" "
            f"id=\"{html.escape(anchor)}\">"
            f"<div class=\"daily-card-kicker\">读数</div>"
            f"<div class=\"metric-label\">{html.escape(metric.label)}</div>"
            f"<div class=\"metric-value\">{html.escape(compiler._format_metric(metric))}</div>"
            f"<div class=\"daily-metric-caption\">截至 {html.escape(metric.as_of)}</div>"
            "</article>"
        )
    cards = []
    for row in _card_rows(sections.get("precision-cards")):
        title = str(row.get("title") or row.get("card_id") or "精判卡")
        summary = str(row.get("summary") or "").strip()
        cards.append(
            "<article class=\"daily-card\">"
            "<span class=\"daily-card-kicker\">精判卡</span>"
            f"<strong>{html.escape(title)}</strong>"
            f"<p>{html.escape(summary)}</p>"
            "</article>"
        )
    notes = []
    for anchor, fallback in (
        ("methodology", "仅工具结果和确定性计算可进入证据账本；缺失指标保持空缺。"),
        ("audit", "正式发布需通过证据、数字、矛盾、锚点和对象哈希门禁。"),
    ):
        text = " ".join(_paragraphs(sections.get(anchor))) or fallback
        notes.append(
            f"<p class=\"daily-note\" id=\"{html.escape(anchor)}\">{html.escape(text)}</p>"
        )
    formula_notes = [
        f"{html.escape(metrics[metric_id].label)} {html.escape(metrics[metric_id].formula_id)}"
        for metric_id in _metric_ids(document)
        if metric_id in metrics
    ]
    if formula_notes:
        notes.append(
            "<p class=\"daily-note is-meta\">指标口径 · "
            + " · ".join(formula_notes)
            + "</p>"
        )

    body_html = ""
    if body:
        body_html = (
            "<section class=\"daily-body\" aria-label=\"正文\">"
            "<div class=\"daily-section-kicker\">正文</div>"
            + "".join(f"<p>{html.escape(item)}</p>" for item in body)
            + "</section>"
        )
    cards_html = ""
    if cards:
        cards_html = (
            "<section class=\"daily-cards\" id=\"precision-cards\" aria-label=\"精判卡\">"
            "<div class=\"daily-section-kicker\">精判卡</div>"
            f"<div class=\"daily-card-grid\">{''.join(cards)}</div>"
            "</section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{html.escape(CSP)}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(document.title)}</title>
  <style>{REPORT_CSS}
{DAILY_CSS}</style>
</head>
<body data-report-layout="investment-daily-v1">{watermark}
<article class="kss-report report-daily">
  <style>{DAILY_CSS}</style>
  <div class="daily-sheet">
    <header class="daily-hero">
      <div class="daily-kicker">KSS · 投资分析日报</div>
      <div class="daily-hero-title">
        <h1>{html.escape(document.title)}</h1>
        <span class="{audit_class}">{audit_label}</span>
      </div>
      <p class="daily-dek">{html.escape(document.subtitle)}</p>
      <div class="daily-chips" aria-label="报告元数据">
        <div class="daily-chip"><span class="daily-chip-label">交易日</span><span class="daily-chip-value">{html.escape(period)}</span></div>
        <div class="daily-chip"><span class="daily-chip-label">数据时点</span><span class="daily-chip-value">{html.escape(document.as_of)}</span></div>
        <div class="daily-chip"><span class="daily-chip-label">版本</span><span class="daily-chip-value">日报 V1</span></div>
      </div>
    </header>
    <section class="daily-metrics" aria-label="当日读数">{''.join(metric_cards)}</section>
    <section class="daily-lead" id="overview" aria-label="摘要">
      <div class="daily-section-kicker">摘要</div>
      <p>{html.escape(lead)}</p>
    </section>
    {body_html}
    {cards_html}
    <footer class="daily-notes" id="audit" aria-label="注释">
      <div class="daily-section-kicker">注释</div>
      <h2>方法与门禁</h2>
      {''.join(notes)}
    </footer>
  </div>
</article>
</body></html>"""
