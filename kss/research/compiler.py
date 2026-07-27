"""Deterministic compiler and audit gates for KSS research reports."""

from __future__ import annotations

import hashlib
import html
import json
import random
import re
import struct
import zlib
from datetime import datetime, timezone
from typing import Any

from .report_models import (
    EvidenceReference,
    MetricEntry,
    MetricLedger,
    NarrativeClaim,
    ReportBlock,
    ReportDocument,
    ReportManifest,
    ReportSection,
)

FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![\w-])(?:[+-]?\d+(?:\.\d+)?\s*(?:%|bp|bps|亿|万|倍|元|点|家|张|只)|[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?)"
)
ALLOWED_NUMBER_CONTEXT_RE = re.compile(r"(20\d{2}[-年/]|第[一二三四五六七八九十\d]+|V\d+|#)")
CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"

# The source report is intentionally not checked into KSS: it is a private
# WeChat attachment.  These values are the reviewed, source-independent V3
# presentation contract.  Keeping the contract in code makes the report
# compiler deterministic and gives tests a stable surface without copying any
# private report content into the repository.
V3_PRESENTATION_CONTRACT = {
    "layout": "investment-research-v3",
    "page_max_width": "1200px",
    "title_size": "36px",
    "section_size": "24px",
    "body_size": "15.5px",
    "body_line_height": "1.78",
    "table_size": "13.5px",
    "meta_size": "12px",
    "precision_card_min_width": "292px",
}


REPORT_CSS = """
    :root {
      color-scheme: light;
      --report-ink: #101828;
      --report-ink-soft: #475467;
      --report-ink-faint: #667085;
      --report-line: #d9e1ea;
      --report-line-strong: #b9c6d5;
      --report-paper: #ffffff;
      --report-canvas: #f3f6fa;
      --report-blue: #1677c8;
      --report-blue-soft: #eaf4ff;
      --report-navy: #10243e;
      --report-alert: #b42318;
      --report-alert-bg: #fff1f0;
    }
    * { box-sizing: border-box; }
    html { background: var(--report-canvas); }
    body {
      margin: 0;
      color: var(--report-ink);
      background: var(--report-canvas);
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif;
      font-size: 15.5px;
      font-weight: 400;
      line-height: 1.78;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }
    main {
      width: min(100%, 1200px);
      margin: 0 auto;
      padding: 42px 30px 76px;
    }
    .report-masthead {
      display: grid;
      gap: 18px;
      padding: 0 0 22px;
      border-top: 4px solid var(--report-navy);
      border-bottom: 1px solid var(--report-line-strong);
    }
    .report-eyebrow,
    .section-index,
    .card-kicker,
    .meta-label {
      color: var(--report-ink-faint);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
        "Liberation Mono", monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      line-height: 1.3;
      text-transform: uppercase;
    }
    .masthead-title-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
    }
    h1, h2, h3, p { margin: 0; }
    h1 {
      max-width: 830px;
      font-size: clamp(29px, 3vw, 36px);
      font-weight: 750;
      letter-spacing: -.036em;
      line-height: 1.18;
    }
    .audit-chip {
      flex: 0 0 auto;
      margin-top: 4px;
      border: 1px solid #a9cbea;
      border-radius: 999px;
      color: #075b99;
      background: var(--report-blue-soft);
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      padding: 7px 10px;
      white-space: nowrap;
    }
    .audit-chip.is-failed {
      border-color: #f7b4ad;
      color: var(--report-alert);
      background: var(--report-alert-bg);
    }
    .report-subtitle {
      max-width: 840px;
      color: var(--report-ink-soft);
      font-size: 16px;
      line-height: 1.65;
    }
    .report-meta {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 0;
      margin: 2px 0 0;
      border-top: 1px solid var(--report-line);
      border-bottom: 1px solid var(--report-line);
    }
    .report-meta > div {
      display: grid;
      gap: 3px;
      min-width: 0;
      padding: 9px 14px 9px 0;
    }
    .report-meta > div + div { padding-left: 14px; border-left: 1px solid var(--report-line); }
    .meta-value {
      overflow: hidden;
      color: var(--report-ink);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
        "Liberation Mono", monospace;
      font-size: 12px;
      line-height: 1.35;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .report-section {
      padding: 27px 0 0;
      scroll-margin-top: 24px;
    }
    .report-section + .report-section { margin-top: 8px; border-top: 1px solid var(--report-line); }
    .section-heading {
      display: grid;
      grid-template-columns: 38px minmax(0, 1fr);
      align-items: baseline;
      column-gap: 10px;
      margin-bottom: 14px;
    }
    .section-index { color: var(--report-blue); }
    h2 {
      font-size: 24px;
      font-weight: 720;
      letter-spacing: -.024em;
      line-height: 1.25;
    }
    .report-block + .report-block { margin-top: 14px; }
    h3 {
      margin-bottom: 7px;
      color: var(--report-ink);
      font-size: 16px;
      font-weight: 700;
      letter-spacing: -.012em;
      line-height: 1.45;
    }
    .report-prose {
      max-width: 88ch;
      color: #263446;
      font-size: 15.5px;
      line-height: 1.78;
      white-space: pre-wrap;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(164px, 1fr));
      gap: 9px;
    }
    .metric {
      min-height: 111px;
      padding: 13px 14px 12px;
      border: 1px solid var(--report-line);
      border-top: 2px solid #9ac8eb;
      background: var(--report-paper);
    }
    .metric-label { color: var(--report-ink-soft); font-size: 13px; font-weight: 650; line-height: 1.35; }
    .metric-value {
      margin: 8px 0 7px;
      color: var(--report-navy);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
        "Liberation Mono", monospace;
      font-size: 25px;
      font-weight: 750;
      letter-spacing: -.045em;
      line-height: 1;
    }
    .metric-meta,
    .card-evidence {
      color: var(--report-ink-faint);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
        "Liberation Mono", monospace;
      font-size: 11px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .table-scroll { overflow-x: auto; border: 1px solid var(--report-line); background: var(--report-paper); }
    table { width: 100%; min-width: 560px; border-collapse: collapse; font-size: 13.5px; line-height: 1.5; }
    th, td { padding: 9px 12px; border-bottom: 1px solid var(--report-line); text-align: left; vertical-align: top; }
    th {
      color: #344054;
      background: #f5f8fb;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .01em;
    }
    tbody tr:last-child td { border-bottom: 0; }
    tbody tr:nth-child(even) td { background: #fbfcfe; }
    .precision-cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(292px, 1fr));
      gap: 10px;
    }
    .precision-card {
      display: grid;
      gap: 7px;
      min-width: 0;
      padding: 13px 14px;
      border: 1px solid var(--report-line);
      border-left: 3px solid #7ab8e6;
      background: var(--report-paper);
      break-inside: avoid;
    }
    .precision-card strong { color: var(--report-ink); font-size: 15px; line-height: 1.4; }
    .precision-card p { color: #344054; font-size: 13.5px; line-height: 1.58; }
    .precision-card .card-evidence { margin-top: 1px; }
    .chart-frame { overflow: hidden; border: 1px solid var(--report-line); background: var(--report-paper); }
    .chart-frame svg { display: block; width: 100%; height: auto; min-height: 168px; }
    .watermark {
      position: fixed;
      top: 18px;
      left: 18px;
      z-index: 2;
      border: 1px solid #f7b4ad;
      color: var(--report-alert);
      background: var(--report-alert-bg);
      font-size: 12px;
      font-weight: 750;
      letter-spacing: .01em;
      padding: 8px 12px;
    }
    @media (max-width: 720px) {
      main { padding: 26px 18px 48px; }
      h1 { font-size: 30px; }
      h2 { font-size: 22px; }
      .masthead-title-row { display: block; }
      .audit-chip { display: inline-block; margin-top: 12px; }
      .report-meta { grid-template-columns: 1fr; }
      .report-meta > div + div { padding-left: 0; border-left: 0; border-top: 1px solid var(--report-line); }
      .section-heading { grid-template-columns: 31px minmax(0, 1fr); column-gap: 8px; }
      .precision-cards { grid-template-columns: 1fr; }
    }
    @media print {
      @page { margin: 13mm 12mm 15mm; }
      html, body { background: #fff; }
      main { width: 100%; max-width: none; padding: 0; }
      .report-section { break-inside: avoid; }
      .report-section + .report-section { margin-top: 6mm; }
      .watermark { position: static; display: inline-block; margin: 0 0 6mm; }
      .table-scroll { overflow: visible; }
      table { min-width: 0; }
      .precision-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .precision-card, .metric { break-inside: avoid; }
    }
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReportCompiler:
    """Compile a typed report IR into static HTML plus auditable sidecars."""

    weekly_required_anchors = [
        "overview",
        "temperature",
        "theme-consensus",
        "risk-radar",
        "analyst-sections",
        "precision-cards",
        "methodology",
        "audit",
    ]
    daily_required_anchors = [
        "overview",
        "temperature",
        "theme-consensus",
        "risk-radar",
        "precision-cards",
        "methodology",
        "audit",
    ]

    def _required_anchors(self, document: ReportDocument) -> list[str]:
        return (
            self.daily_required_anchors
            if document.profile_id == "investment-daily-v1"
            else self.weekly_required_anchors
        )

    def compile(self, document: ReportDocument, *, draft: bool = False) -> dict[str, Any]:
        audit = self.audit(document)
        effective_draft = draft or audit["status"] != "pass"
        html_text = self.render_html(document, audit=audit, draft=effective_draft)
        outputs: dict[str, bytes] = {
            "report.html": html_text.encode("utf-8"),
            "report_ir.json": stable_json(document.to_dict()).encode("utf-8"),
            "metrics.json": stable_json(document.metric_ledger.to_dict()).encode("utf-8"),
            "claims.json": stable_json({"claims": [c.to_dict() for c in document.claims]}).encode("utf-8"),
            "evidence_manifest.json": stable_json({"evidence": [e.to_dict() for e in document.evidence]}).encode("utf-8"),
            "audit.json": stable_json(audit).encode("utf-8"),
            "preview.png": self._preview_png(status=audit["status"], draft=effective_draft),
        }
        manifest = ReportManifest(
            document_id=document.document_id,
            profile_id=document.profile_id,
            audit_status=audit["status"],
            object_hashes={name: sha256_bytes(data) for name, data in sorted(outputs.items())},
            anchors=[section.anchor for section in document.sections],
            generated_at=utc_now(),
            draft=effective_draft,
        )
        outputs["manifest.json"] = stable_json(manifest.to_dict()).encode("utf-8")
        return {
            "status": audit["status"],
            "draft": effective_draft,
            "audit": audit,
            "manifest": manifest.to_dict(),
            "outputs": outputs,
        }

    def audit(self, document: ReportDocument) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        metric_ids = set(document.metric_ledger.by_id())
        evidence_ids = {e.evidence_id for e in document.evidence}
        anchors = [section.anchor for section in document.sections]
        required_anchors = self._required_anchors(document)
        formula_inputs = document.metadata.get("formula_inputs")
        formula_inputs = formula_inputs if isinstance(formula_inputs, dict) else {}
        missing_anchors = [a for a in required_anchors if a not in anchors]
        if missing_anchors:
            findings.append({"severity": "block", "code": "missing_anchor", "detail": missing_anchors})
        if len(set(anchors)) != len(anchors):
            findings.append({"severity": "block", "code": "duplicate_anchor", "detail": anchors})

        for metric in document.metric_ledger.metrics:
            metric_numeric = isinstance(metric.value, (int, float)) and not isinstance(
                metric.value, bool
            )
            if not metric_numeric:
                findings.append(
                    {
                        "severity": "block",
                        "code": "metric_value_not_numeric",
                        "metric_id": metric.metric_id,
                    }
                )
            if not metric.input_refs:
                findings.append({"severity": "block", "code": "metric_without_inputs", "metric_id": metric.metric_id})
            missing_inputs = [
                ref for ref in metric.input_refs if ref not in evidence_ids
            ]
            if missing_inputs:
                findings.append(
                    {
                        "severity": "block",
                        "code": "metric_missing_evidence_inputs",
                        "metric_id": metric.metric_id,
                        "detail": missing_inputs,
                    }
                )
            if not metric.as_of:
                findings.append({"severity": "block", "code": "metric_without_as_of", "metric_id": metric.metric_id})
            if metric.formula_version not in {"v1", "kss-equivalent-v1"}:
                findings.append({"severity": "block", "code": "unsupported_formula_version", "metric_id": metric.metric_id, "version": metric.formula_version})
            recomputed = self._recompute_metric(
                metric,
                formula_inputs=formula_inputs,
                precision_card_count=len(self._precision_card_rows(document)),
            )
            if recomputed is None:
                findings.append(
                    {
                        "severity": "block",
                        "code": "metric_formula_inputs_missing",
                        "metric_id": metric.metric_id,
                    }
                )
            elif metric_numeric and round(
                float(recomputed), metric.precision
            ) != round(float(metric.value), metric.precision):
                findings.append({"severity": "block", "code": "metric_recompute_mismatch", "metric_id": metric.metric_id})

        for evidence in document.evidence:
            if not evidence.data_as_of:
                findings.append(
                    {
                        "severity": "block",
                        "code": "evidence_without_as_of",
                        "evidence_id": evidence.evidence_id,
                    }
                )
            if not evidence.hash or not re.fullmatch(
                r"[0-9a-f]{64}", evidence.hash
            ):
                findings.append(
                    {
                        "severity": "block",
                        "code": "evidence_hash_invalid",
                        "evidence_id": evidence.evidence_id,
                    }
                )

        for claim in document.claims:
            if not claim.evidence_refs:
                findings.append({"severity": "block", "code": "claim_without_evidence", "claim_id": claim.claim_id})
            missing = [eid for eid in claim.evidence_refs if eid not in evidence_ids]
            if missing:
                findings.append({"severity": "block", "code": "claim_missing_evidence", "claim_id": claim.claim_id, "detail": missing})
            if claim.confidence is not None:
                if not claim.rubric_id or not claim.rubric_version:
                    findings.append({"severity": "block", "code": "claim_score_missing_rubric", "claim_id": claim.claim_id})
                if not claim.review_required:
                    findings.append({"severity": "block", "code": "claim_score_missing_review_flag", "claim_id": claim.claim_id})

        for section in document.sections:
            for block in section.blocks:
                unknown_metrics = [m for m in block.metric_refs if m not in metric_ids]
                unknown_evidence = [e for e in block.evidence_refs if e not in evidence_ids]
                chart_metrics = (
                    block.chart.metric_refs if block.chart is not None else []
                )
                unknown_chart_metrics = [
                    metric_id
                    for metric_id in chart_metrics
                    if metric_id not in metric_ids
                ]
                if unknown_metrics:
                    findings.append({"severity": "block", "code": "unknown_metric_ref", "block_id": block.block_id, "detail": unknown_metrics})
                if unknown_evidence:
                    findings.append({"severity": "block", "code": "unknown_evidence_ref", "block_id": block.block_id, "detail": unknown_evidence})
                if unknown_chart_metrics:
                    findings.append(
                        {
                            "severity": "block",
                            "code": "chart_unknown_metric_ref",
                            "block_id": block.block_id,
                            "detail": unknown_chart_metrics,
                        }
                    )
                for row in block.rows:
                    row_metrics = [str(m) for m in row.get("metric_refs", [])]
                    row_evidence = [str(e) for e in row.get("evidence_refs", [])]
                    bad_metrics = [m for m in row_metrics if m not in metric_ids]
                    bad_evidence = [e for e in row_evidence if e not in evidence_ids]
                    if bad_metrics:
                        findings.append({"severity": "block", "code": "row_unknown_metric_ref", "block_id": block.block_id, "detail": bad_metrics})
                    if bad_evidence:
                        findings.append({"severity": "block", "code": "row_unknown_evidence_ref", "block_id": block.block_id, "detail": bad_evidence})
                    text_blob = stable_json(row)
                    bad_numbers = self._unbound_financial_numbers(text_blob, row_metrics, document.metric_ledger.by_id())
                    if bad_numbers:
                        findings.append({"severity": "block", "code": "row_unbound_financial_number", "block_id": block.block_id, "numbers": bad_numbers[:5]})
                bad_numbers = self._unbound_financial_numbers(" ".join([block.title or "", block.text or ""]), block.metric_refs, document.metric_ledger.by_id())
                if bad_numbers:
                    findings.append({"severity": "block", "code": "unbound_financial_number", "block_id": block.block_id, "numbers": bad_numbers[:5]})

        cards = self._precision_card_rows(document)
        card_ids = [str(row.get("card_id") or "") for row in cards]
        if any(not card_id for card_id in card_ids):
            findings.append({"severity": "block", "code": "precision_card_missing_id"})
        if len(card_ids) != len(set(card_ids)):
            findings.append({"severity": "block", "code": "duplicate_precision_card_id"})
        expected_cards = document.metadata.get("card_count")
        try:
            expected_card_count = int(expected_cards)
        except (TypeError, ValueError, OverflowError):
            expected_card_count = None
            findings.append(
                {
                    "severity": "block",
                    "code": "precision_card_count_invalid",
                    "expected": expected_cards,
                }
            )
        if expected_card_count is None or expected_card_count != len(cards):
            findings.append(
                {
                    "severity": "block",
                    "code": "precision_card_count_mismatch",
                    "expected": expected_card_count,
                    "actual": len(cards),
                }
            )
        sample = self._sample_cards(document, cards)
        for row in sample:
            if not row.get("metric_refs") or not row.get("evidence_refs"):
                findings.append({"severity": "block", "code": "sampled_card_missing_refs", "card_id": row.get("card_id")})
        status = "fail" if any(f["severity"] == "block" for f in findings) else "pass"
        return {
            "status": status,
            "coverage": {
                "anchors": anchors,
                "required_anchors": required_anchors,
                "metric_count": len(metric_ids),
                "claim_count": len(document.claims),
                "evidence_count": len(evidence_ids),
                "precision_card_count": len(cards),
                "sample_size": len(sample),
                "sample_card_ids": [row.get("card_id") for row in sample],
            },
            "findings": findings,
            "generated_at": utc_now(),
        }

    def render_html(self, document: ReportDocument, *, audit: dict[str, Any], draft: bool) -> str:
        metrics = document.metric_ledger.by_id()
        sections = "\n".join(
            self._render_section(section, metrics, index=index)
            for index, section in enumerate(document.sections, start=1)
        )
        watermark = "<div class=\"watermark\">草稿 · 审计未通过 · 不得正式发布</div>" if draft else ""
        audit_passed = audit["status"] == "pass"
        audit_summary = "审计通过" if audit_passed else "审计未通过"
        report_class = "report-v3" if document.profile_id == "investment-weekly-v3" else "report-standard"
        audit_class = "audit-chip" if audit_passed else "audit-chip is-failed"
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{html.escape(CSP)}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(document.title)}</title>
  <style>{REPORT_CSS}</style>
</head>
<body class="{report_class}" data-report-layout="{V3_PRESENTATION_CONTRACT['layout'] if report_class == 'report-v3' else 'standard'}">{watermark}<main>
<header class="report-masthead">
  <div class="report-eyebrow">KSS · INVESTMENT RESEARCH</div>
  <div class="masthead-title-row"><h1>{html.escape(document.title)}</h1><span class="{audit_class}">{audit_summary}</span></div>
  <p class="report-subtitle">{html.escape(document.subtitle)}</p>
  <div class="report-meta" aria-label="报告元数据">
    <div><span class="meta-label">研究周期</span><span class="meta-value">{html.escape(document.date_range)}</span></div>
    <div><span class="meta-label">数据时点</span><span class="meta-value">{html.escape(document.as_of)}</span></div>
    <div><span class="meta-label">版本</span><span class="meta-value">{html.escape(document.profile_id)}</span></div>
  </div>
</header>
{sections}
</main></body></html>"""

    def _render_section(self, section: ReportSection, metrics: dict[str, MetricEntry], *, index: int) -> str:
        blocks = "\n".join(self._render_block(block, metrics) for block in section.blocks)
        section_class = re.sub(r"[^a-z0-9-]", "-", section.anchor.lower()).strip("-") or "section"
        return (
            f"<section class=\"report-section section-{html.escape(section_class)}\" "
            f"id=\"{html.escape(section.anchor)}\">"
            f"<div class=\"section-heading\"><span class=\"section-index\">{index:02d}</span>"
            f"<h2>{html.escape(section.title)}</h2></div>{blocks}</section>"
        )

    def _render_block(self, block: ReportBlock, metrics: dict[str, MetricEntry]) -> str:
        title = f"<h3>{html.escape(block.title)}</h3>" if block.title else ""
        if block.type == "metric_group":
            items = []
            for metric_id in block.metric_refs:
                metric = metrics[metric_id]
                value = self._format_metric(metric)
                items.append(
                    "<div class=\"metric\">"
                    f"<div class=\"metric-label\">{html.escape(metric.label)}</div>"
                    f"<div class=\"metric-value\">{html.escape(value)}</div>"
                    f"<div class=\"metric-meta\">{html.escape(metric.formula_id)} · {html.escape(metric.as_of)}</div>"
                    "</div>"
                )
            return f"<div class=\"report-block report-block-metrics\">{title}<div class=\"metric-grid\">{''.join(items)}</div></div>"
        if block.type == "precision_cards":
            cards = []
            for row in block.rows:
                cards.append(
                    "<article class=\"precision-card\">"
                    "<span class=\"card-kicker\">精判卡</span>"
                    f"<strong>{html.escape(str(row.get('title') or row.get('card_id') or '卡片'))}</strong>"
                    f"<p>{html.escape(str(row.get('summary') or ''))}</p>"
                    f"<p class=\"card-evidence\">metric: {html.escape(','.join(map(str, row.get('metric_refs', []))))} · evidence: {html.escape(','.join(map(str, row.get('evidence_refs', []))))}</p>"
                    "</article>"
                )
            return f"<div class=\"report-block report-block-cards\">{title}<div class=\"precision-cards\">{''.join(cards)}</div></div>"
        if block.type == "svg_chart" and block.chart:
            values = [
                metric
                for metric_id in block.chart.metric_refs
                if (metric := metrics.get(metric_id))
                and isinstance(metric.value, (int, float))
            ]
            maximum = max(
                (abs(float(metric.value)) for metric in values),
                default=1.0,
            )
            bars = []
            for index, metric in enumerate(values):
                height = 120 * abs(float(metric.value)) / maximum
                x = 30 + index * 76
                y = 150 - height
                bars.append(
                    f'<rect x="{x}" y="{y:.2f}" width="42" '
                    f'height="{height:.2f}" rx="6" fill="#1d9bf0">'
                    f"<title>{html.escape(metric.label)}: "
                    f"{html.escape(self._format_metric(metric))}</title></rect>"
                )
            return (
                f"<div class=\"report-block report-block-chart\">{title}<div class=\"chart-frame\"><svg role=\"img\" aria-label=\""
                f"{html.escape(block.chart.title)}\" viewBox=\"0 0 640 180\" "
                f"xmlns=\"http://www.w3.org/2000/svg\">"
                f"{''.join(bars)}</svg></div></div>"
            )
        if block.rows:
            columns = sorted({key for row in block.rows for key in row.keys() if key not in {"metric_refs", "evidence_refs"}})
            head = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
            body = "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>"
                for row in block.rows
            )
            return f"<div class=\"report-block report-block-table\">{title}<div class=\"table-scroll\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>"
        text = html.escape(block.text or "")
        return f"<div class=\"report-block report-block-prose\">{title}<p class=\"report-prose\">{text}</p></div>"

    def _format_metric(self, metric: MetricEntry) -> str:
        if isinstance(metric.value, (int, float)):
            value = f"{float(metric.value):.{metric.precision}f}"
        else:
            value = str(metric.value)
        return f"{value}{metric.unit}"

    def _unbound_financial_numbers(self, text: str, metric_refs: list[str], metrics: dict[str, MetricEntry]) -> list[str]:
        allowed = {self._format_metric(metrics[mid]).replace(",", "") for mid in metric_refs if mid in metrics}
        unbound: list[str] = []
        for match in FINANCIAL_NUMBER_RE.finditer(text):
            prefix = text[max(0, match.start() - 8): match.start()]
            if not ALLOWED_NUMBER_CONTEXT_RE.search(prefix):
                token = match.group(0).replace(" ", "").replace(",", "")
                if token not in allowed:
                    unbound.append(match.group(0))
        return unbound

    def _precision_card_rows(self, document: ReportDocument) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for section in document.sections:
            for block in section.blocks:
                if block.type == "precision_cards":
                    rows.extend(block.rows)
        return rows

    def _recompute_metric(
        self,
        metric: MetricEntry,
        *,
        formula_inputs: dict[str, Any],
        precision_card_count: int,
    ) -> float | int | None:
        if metric.formula_id == "card_count":
            return precision_card_count
        values = formula_inputs.get(metric.metric_id)
        if not isinstance(values, list) or not values:
            return None
        if metric.formula_id == "investment_temperature":
            numerator = 0.0
            denominator = 0.0
            for value in values:
                if not isinstance(value, dict):
                    return None
                try:
                    stance = float(value["stance_score"])
                    conviction = float(value["conviction_weight"])
                    analyst = float(value["analyst_weight"])
                except (KeyError, TypeError, ValueError):
                    return None
                if bool(value.get("is_sellside_forward")):
                    continue
                numerator += stance * conviction * analyst
                denominator += conviction * analyst
            return numerator / denominator if denominator else 0.0
        if metric.formula_id == "investment_theme_strength":
            numeric = [
                float(value)
                for value in values
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            if len(numeric) != len(values):
                return None
            return max(numeric, key=abs)
        if metric.formula_id == "investment_risk_severity":
            severities: list[float] = []
            for value in values:
                if not isinstance(value, dict):
                    return None
                mentions = value.get("mention_card_count")
                analysts = value.get("distinct_analyst_count")
                if (
                    isinstance(mentions, bool)
                    or not isinstance(mentions, (int, float))
                    or isinstance(analysts, bool)
                    or not isinstance(analysts, (int, float))
                ):
                    return None
                severities.append(float(mentions) + 0.5 * float(analysts))
            return max(severities) if severities else 0.0
        numeric = [
            float(value)
            for value in values
            if isinstance(value, (int, float))
        ]
        if len(numeric) != len(values):
            return None
        return sum(numeric) / len(numeric)

    def _sample_cards(self, document: ReportDocument, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not cards:
            return []
        sample_size = min(200, max(30, int(round(len(cards) * 0.15))))
        sample_size = min(sample_size, len(cards))
        seed_payload = stable_json({
            "document_id": document.document_id,
            "profile_id": document.profile_id,
            "cards": [row.get("card_id") for row in cards],
            "metrics": document.metric_ledger.to_dict(),
        })
        seed = int(hashlib.sha256(seed_payload.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in cards:
            key = str(row.get("source_group") or row.get("analyst") or row.get("risk_group") or (row.get("evidence_refs") or ["unknown"])[0])
            groups.setdefault(key, []).append(row)
        selected: list[dict[str, Any]] = []
        for key in sorted(groups):
            group = groups[key]
            quota = max(1, round(sample_size * len(group) / len(cards)))
            quota = min(quota, len(group))
            selected.extend(rng.sample(group, quota))
        if len(selected) > sample_size:
            selected = rng.sample(selected, sample_size)
        elif len(selected) < sample_size:
            seen = {id(row) for row in selected}
            remainder = [row for row in cards if id(row) not in seen]
            selected.extend(rng.sample(remainder, min(sample_size - len(selected), len(remainder))))
        return sorted(selected, key=lambda row: str(row.get("card_id")))

    def _preview_png(self, *, status: str, draft: bool, width: int = 640, height: int = 360) -> bytes:
        # A structural, content-free preview of the V3 report.  It mirrors the
        # masthead, compact metadata band, metric grid and dense report rows
        # without attempting to rasterize untrusted model text.
        bg = (243, 246, 250)
        paper = (255, 255, 255)
        navy = (16, 36, 62)
        ink = (16, 24, 40)
        line = (217, 225, 234)
        blue = (22, 119, 200)
        alert = (180, 35, 24)
        accent = alert if draft or status != "pass" else blue

        def in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
            left, top, right, bottom = rect
            return left <= x <= right and top <= y <= bottom

        paper_rect = (30, 20, width - 30, height - 20)
        accent_rect = (30, 20, width - 30, 23)
        title_rect = (56, 56, 306, 76)
        subtitle_rect = (56, 88, 420, 96)
        meta_line = (56, 111, width - 56, 112)
        meta_rects = [(56, 121, 188, 138), (202, 121, 334, 138), (348, 121, 510, 138)]
        section_rule = (56, 157, width - 56, 158)
        metric_rects = [(56, 174, 165, 228), (174, 174, 283, 228), (292, 174, 401, 228), (410, 174, 519, 228)]
        metric_values = [(68, 196, 132, 207), (186, 196, 250, 207), (304, 196, 368, 207), (422, 196, 486, 207)]
        table_head = (56, 246, width - 56, 260)
        table_rows = [(56, 268, width - 56, 269), (56, 281, width - 56, 282), (56, 294, width - 56, 295)]
        body_lines = [(56, 315, 360, 320), (56, 328, 500, 333)]

        rows = []
        for y in range(height):
            row = bytearray([0])
            for x in range(width):
                color = paper if in_rect(x, y, paper_rect) else bg
                if in_rect(x, y, accent_rect):
                    color = accent
                elif in_rect(x, y, title_rect):
                    color = navy
                elif in_rect(x, y, subtitle_rect):
                    color = (71, 84, 103)
                elif in_rect(x, y, meta_line) or in_rect(x, y, section_rule):
                    color = line
                elif any(in_rect(x, y, rect) for rect in meta_rects):
                    color = (234, 240, 246)
                elif any(in_rect(x, y, rect) for rect in metric_rects):
                    color = (249, 252, 255)
                elif any(in_rect(x, y, rect) for rect in metric_values):
                    color = blue
                elif in_rect(x, y, table_head):
                    color = (245, 248, 251)
                elif any(in_rect(x, y, rect) for rect in table_rows):
                    color = line
                elif any(in_rect(x, y, rect) for rect in body_lines):
                    color = ink
                row.extend(color)
            rows.append(bytes(row))
        raw = b"".join(rows)

        def chunk(kind: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(kind + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )


def make_investment_weekly_fixture(*, cards: int = 1143) -> ReportDocument:
    evidence = [
        EvidenceReference(
            evidence_id=f"ev_source_{i}",
            source_tier="reputable_secondary",
            title=f"分析来源 {i}",
            uri=f"https://example.invalid/source/{i}",
            data_as_of="2026-07-17",
            hash=hashlib.sha256(f"source-{i}".encode()).hexdigest(),
        )
        for i in range(1, 7)
    ]
    metrics = [
        MetricEntry("m_temperature", "市场温度", 63.4, "%", 1, "temperature_index", "v1", [e.evidence_id for e in evidence], "2026-07-17"),
        MetricEntry("m_consensus", "主题共识强度", 72.0, "%", 1, "theme_consensus", "v1", [e.evidence_id for e in evidence[:3]], "2026-07-17"),
        MetricEntry("m_risk", "风险雷达均值", 41.0, "%", 1, "risk_radar", "v1", [e.evidence_id for e in evidence[3:]], "2026-07-17"),
        MetricEntry("m_card_count", "精判卡数量", cards, "张", 0, "card_count", "v1", [e.evidence_id for e in evidence], "2026-07-17"),
    ]
    rows = [
        {
            "card_id": f"card_{i:04d}",
            "title": f"精判卡 {i:04d}",
            "summary": "结构化信号已绑定证据和指标，供审计抽样。",
            "metric_refs": ["m_temperature", "m_card_count"],
            "evidence_refs": [evidence[i % len(evidence)].evidence_id],
            "source_group": f"source_{(i % 6) + 1}",
            "risk_group": f"risk_{(i % 4) + 1}",
        }
        for i in range(1, cards + 1)
    ]
    sections = [
        ReportSection("sec_overview", "总览", "overview", [ReportBlock("b_overview", "paragraph", text="本周研究使用冻结快照和证据账本生成。", evidence_refs=["ev_source_1"])]),
        ReportSection("sec_temperature", "市场温度", "temperature", [ReportBlock("b_temp", "metric_group", metric_refs=["m_temperature", "m_card_count"], evidence_refs=["ev_source_1"])]),
        ReportSection("sec_theme", "主题共识", "theme-consensus", [ReportBlock("b_theme", "theme_table", rows=[{"theme": "AI 半导体", "score": "72.0%", "metric_refs": ["m_consensus"], "evidence_refs": ["ev_source_2"]}], metric_refs=["m_consensus"], evidence_refs=["ev_source_2"])]),
        ReportSection("sec_risk", "风险雷达", "risk-radar", [ReportBlock("b_risk", "risk_radar", rows=[{"risk": "流动性", "score": "41.0%", "metric_refs": ["m_risk"], "evidence_refs": ["ev_source_4"]}], metric_refs=["m_risk"], evidence_refs=["ev_source_4"])]),
        ReportSection("sec_analyst", "分析师分区", "analyst-sections", [ReportBlock("b_analysts", "analyst_section", rows=[{"analyst": f"来源 {i}", "focus": "市场结构", "evidence_refs": [f"ev_source_{i}"]} for i in range(1, 7)])]),
        ReportSection("sec_cards", "精判卡", "precision-cards", [ReportBlock("b_cards", "precision_cards", rows=rows, metric_refs=["m_temperature", "m_card_count"], evidence_refs=[e.evidence_id for e in evidence])]),
        ReportSection("sec_method", "方法论", "methodology", [ReportBlock("b_method", "methodology", text="所有金融数字必须绑定 Metric Ledger，模型文本不能充当证据。", evidence_refs=["ev_source_1"])]),
        ReportSection("sec_audit", "审计", "audit", [ReportBlock("b_audit", "audit", text="交付审计覆盖锚点、证据、数字和抽样卡片。", metric_refs=["m_card_count"], evidence_refs=["ev_source_1"])]),
    ]
    claims = [
        NarrativeClaim(
            "claim_temperature",
            "市场温度处于中性偏热区间。",
            ["ev_source_1", "ev_source_2"],
            confidence=0.74,
            review_required=True,
            rubric_id="market_temperature_judgment",
            rubric_version="v1",
        ),
    ]
    return ReportDocument(
        document_id="investment-weekly-v3-fixture-2026-07-13-2026-07-17",
        profile_id="investment-weekly-v3",
        title="投资分析周报 V3",
        subtitle="合成验收样本",
        date_range="2026-07-13_to_2026-07-17",
        as_of="2026-07-17",
        sections=sections,
        metric_ledger=MetricLedger(metrics),
        claims=claims,
        evidence=evidence,
        metadata={
            "fixture": True,
            "card_count": cards,
            "formula_inputs": {
                "m_temperature": [62.0, 64.8],
                "m_consensus": [70.0, 74.0],
                "m_risk": [40.0, 42.0],
            },
        },
    )
