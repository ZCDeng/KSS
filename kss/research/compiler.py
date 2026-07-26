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

from .report_models import EvidenceReference, MetricEntry, MetricLedger, NarrativeClaim, ReportBlock, ReportDocument, ReportManifest, ReportSection

FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![\w-])(?:[+-]?\d+(?:\.\d+)?\s*(?:%|bp|bps|亿|万|倍|元|点|家|张|只)|[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?)"
)
ALLOWED_NUMBER_CONTEXT_RE = re.compile(r"(20\d{2}[-年/]|第[一二三四五六七八九十\d]+|V\d+|#)")
CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReportCompiler:
    """Compile a typed report IR into static HTML plus auditable sidecars."""

    required_anchors = [
        "overview",
        "temperature",
        "theme-consensus",
        "risk-radar",
        "analyst-sections",
        "precision-cards",
        "methodology",
        "audit",
    ]

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
        missing_anchors = [a for a in self.required_anchors if a not in anchors]
        if missing_anchors:
            findings.append({"severity": "block", "code": "missing_anchor", "detail": missing_anchors})
        if len(set(anchors)) != len(anchors):
            findings.append({"severity": "block", "code": "duplicate_anchor", "detail": anchors})

        for metric in document.metric_ledger.metrics:
            if not metric.input_refs:
                findings.append({"severity": "block", "code": "metric_without_inputs", "metric_id": metric.metric_id})
            if not metric.as_of:
                findings.append({"severity": "block", "code": "metric_without_as_of", "metric_id": metric.metric_id})
            if metric.formula_version != "v1":
                findings.append({"severity": "block", "code": "unsupported_formula_version", "metric_id": metric.metric_id, "version": metric.formula_version})
            expected_values = (document.metadata.get("formula_results") or {}) if isinstance(document.metadata.get("formula_results"), dict) else {}
            if metric.metric_id in expected_values and str(expected_values[metric.metric_id]) != str(metric.value):
                findings.append({"severity": "block", "code": "metric_recompute_mismatch", "metric_id": metric.metric_id})

        for claim in document.claims:
            missing = [eid for eid in claim.evidence_refs if eid not in evidence_ids]
            if missing:
                findings.append({"severity": "block", "code": "claim_missing_evidence", "claim_id": claim.claim_id, "detail": missing})
            if claim.confidence is not None and (not claim.rubric_id or not claim.rubric_version):
                findings.append({"severity": "block", "code": "claim_score_missing_rubric", "claim_id": claim.claim_id})

        for section in document.sections:
            for block in section.blocks:
                unknown_metrics = [m for m in block.metric_refs if m not in metric_ids]
                unknown_evidence = [e for e in block.evidence_refs if e not in evidence_ids]
                if unknown_metrics:
                    findings.append({"severity": "block", "code": "unknown_metric_ref", "block_id": block.block_id, "detail": unknown_metrics})
                if unknown_evidence:
                    findings.append({"severity": "block", "code": "unknown_evidence_ref", "block_id": block.block_id, "detail": unknown_evidence})
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
        sample = self._sample_cards(document, cards)
        for row in sample:
            if not row.get("metric_refs") or not row.get("evidence_refs"):
                findings.append({"severity": "block", "code": "sampled_card_missing_refs", "card_id": row.get("card_id")})
        status = "fail" if any(f["severity"] == "block" for f in findings) else "pass"
        return {
            "status": status,
            "coverage": {
                "anchors": anchors,
                "required_anchors": self.required_anchors,
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
        sections = "\n".join(self._render_section(section, document.metric_ledger.by_id()) for section in document.sections)
        watermark = "<div class=\"watermark\">草稿 · 审计未通过 · 不得正式发布</div>" if draft else ""
        audit_summary = html.escape(audit["status"])
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{html.escape(CSP)}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(document.title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#0f172a; --muted:#64748b; --line:#dbe4ee; --blue:#1d9bf0; --bg:#f8fafc; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
    main {{ max-width:1180px; margin:0 auto; padding:40px 28px 80px; }}
    header {{ border-bottom:1px solid var(--line); margin-bottom:28px; padding-bottom:22px; }}
    h1 {{ font-size:34px; margin:0 0 8px; letter-spacing:-0.03em; }}
    h2 {{ font-size:24px; margin:30px 0 14px; letter-spacing:-0.02em; }}
    h3 {{ font-size:17px; margin:18px 0 8px; }}
    p {{ line-height:1.72; }}
    table {{ width:100%; border-collapse:collapse; background:white; border:1px solid var(--line); border-radius:14px; overflow:hidden; }}
    th,td {{ padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ background:#eef6ff; font-weight:700; }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
    .metric {{ background:white; border:1px solid var(--line); border-radius:18px; padding:16px; }}
    .metric-value {{ font-size:26px; font-weight:800; color:var(--blue); }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }}
    .card {{ background:white; border:1px solid var(--line); border-radius:18px; padding:14px; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .watermark {{ position:fixed; inset:16px auto auto 16px; z-index:2; background:#fff1f2; color:#be123c; border:1px solid #fecdd3; border-radius:999px; padding:8px 14px; font-weight:700; }}
    @media print {{ body {{ background:white; }} .watermark {{ position:static; display:inline-block; }} }}
  </style>
</head>
<body>{watermark}<main>
<header><h1>{html.escape(document.title)}</h1><p class="muted">{html.escape(document.subtitle)} · {html.escape(document.date_range)} · as of {html.escape(document.as_of)} · audit {audit_summary}</p></header>
{sections}
</main></body></html>"""

    def _render_section(self, section: ReportSection, metrics: dict[str, MetricEntry]) -> str:
        blocks = "\n".join(self._render_block(block, metrics) for block in section.blocks)
        return f"<section id=\"{html.escape(section.anchor)}\"><h2>{html.escape(section.title)}</h2>{blocks}</section>"

    def _render_block(self, block: ReportBlock, metrics: dict[str, MetricEntry]) -> str:
        title = f"<h3>{html.escape(block.title)}</h3>" if block.title else ""
        if block.type == "metric_group":
            items = []
            for metric_id in block.metric_refs:
                metric = metrics[metric_id]
                value = self._format_metric(metric)
                items.append(f"<div class=\"metric\"><div class=\"muted\">{html.escape(metric.label)}</div><div class=\"metric-value\">{html.escape(value)}</div><div class=\"muted\">{html.escape(metric.formula_id)} · {html.escape(metric.as_of)}</div></div>")
            return f"{title}<div class=\"metric-grid\">{''.join(items)}</div>"
        if block.type == "precision_cards":
            cards = []
            for row in block.rows:
                cards.append(
                    "<div class=\"card\">"
                    f"<strong>{html.escape(str(row.get('title') or row.get('card_id') or '卡片'))}</strong>"
                    f"<p>{html.escape(str(row.get('summary') or ''))}</p>"
                    f"<p class=\"muted\">metric: {html.escape(','.join(map(str, row.get('metric_refs', []))))} · evidence: {html.escape(','.join(map(str, row.get('evidence_refs', []))))}</p>"
                    "</div>"
                )
            return f"{title}<div class=\"cards\">{''.join(cards)}</div>"
        if block.rows:
            columns = sorted({key for row in block.rows for key in row.keys() if key not in {"metric_refs", "evidence_refs"}})
            head = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
            body = "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>"
                for row in block.rows
            )
            return f"{title}<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        text = html.escape(block.text or "")
        return f"{title}<p>{text}</p>"

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
        bg = (248, 250, 252)
        accent = (190, 18, 60) if draft or status != "pass" else (29, 155, 240)
        rows = []
        for y in range(height):
            row = bytearray([0])
            for x in range(width):
                if 36 <= x <= width - 36 and 44 <= y <= height - 44:
                    color = (255, 255, 255)
                else:
                    color = bg
                if 36 <= x <= width - 36 and 44 <= y <= 52:
                    color = accent
                if 70 <= x <= 300 and 95 <= y <= 122:
                    color = (15, 23, 42)
                if 70 <= x <= 560 and 155 <= y <= 172:
                    color = (219, 228, 238)
                if 70 <= x <= 520 and 198 <= y <= 215:
                    color = (219, 228, 238)
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
        NarrativeClaim("claim_temperature", "市场温度处于中性偏热区间。", ["ev_source_1", "ev_source_2"], confidence=0.74, rubric_id="market_temperature_judgment", rubric_version="v1"),
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
        metadata={"fixture": True, "card_count": cards, "formula_results": {
            "m_temperature": 63.4,
            "m_consensus": 72.0,
            "m_risk": 41.0,
            "m_card_count": cards,
        }},
    )
