from __future__ import annotations

import json

from kss.research.compiler import ReportCompiler, make_investment_weekly_fixture
from kss.research.report_models import ReportBlock


def _json(data: bytes):
    return json.loads(data.decode("utf-8"))


def test_weekly_fixture_compiles_all_outputs_and_required_anchors():
    doc = make_investment_weekly_fixture(cards=1143)
    result = ReportCompiler().compile(doc)

    assert result["status"] == "pass"
    assert result["draft"] is False
    assert set(result["outputs"]) == {
        "report.html",
        "report_ir.json",
        "metrics.json",
        "claims.json",
        "evidence_manifest.json",
        "audit.json",
        "manifest.json",
        "preview.png",
    }
    html = result["outputs"]["report.html"].decode("utf-8")
    assert '<section id="temperature">' in html
    assert '<meta http-equiv="Content-Security-Policy"' in html
    assert "<script" not in html.lower()
    assert len(result["outputs"]["preview.png"]) > 1000
    audit = _json(result["outputs"]["audit.json"])
    assert audit["coverage"]["precision_card_count"] == 1143
    assert 30 <= audit["coverage"]["sample_size"] <= 200
    assert audit["coverage"]["sample_size"] == 171


def test_compiler_blocks_unbound_financial_numbers_even_when_section_has_other_metrics():
    doc = make_investment_weekly_fixture(cards=30)
    sections = list(doc.sections)
    bad_blocks = list(sections[0].blocks)
    bad_blocks.append(ReportBlock("bad_number", "paragraph", text="这里出现 123.4% 但没有对应指标。", metric_refs=["m_temperature"]))
    sections[0] = type(sections[0])(sections[0].section_id, sections[0].title, sections[0].anchor, bad_blocks)
    bad_doc = type(doc)(
        doc.document_id + "-bad",
        doc.profile_id,
        doc.title,
        doc.subtitle,
        doc.date_range,
        doc.as_of,
        sections,
        doc.metric_ledger,
        doc.claims,
        doc.evidence,
        doc.metadata,
    )

    result = ReportCompiler().compile(bad_doc)

    assert result["status"] == "fail"
    assert result["draft"] is True
    codes = {finding["code"] for finding in result["audit"]["findings"]}
    assert "unbound_financial_number" in codes
    assert "草稿" in result["outputs"]["report.html"].decode("utf-8")


def test_compiler_escapes_model_text_and_manifest_hashes_match():
    doc = make_investment_weekly_fixture(cards=30)
    sections = list(doc.sections)
    blocks = list(sections[0].blocks)
    blocks.append(ReportBlock("xss", "paragraph", text="<img src=x onerror=alert(1)>", evidence_refs=["ev_source_1"]))
    sections[0] = type(sections[0])(sections[0].section_id, sections[0].title, sections[0].anchor, blocks)
    xss_doc = type(doc)(
        doc.document_id + "-xss",
        doc.profile_id,
        doc.title,
        doc.subtitle,
        doc.date_range,
        doc.as_of,
        sections,
        doc.metric_ledger,
        doc.claims,
        doc.evidence,
        doc.metadata,
    )

    result = ReportCompiler().compile(xss_doc)
    html = result["outputs"]["report.html"].decode("utf-8")
    manifest = _json(result["outputs"]["manifest.json"])

    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x" not in html
    assert manifest["object_hashes"]["report.html"]
    assert manifest["object_hashes"]["audit.json"]


def test_metric_recompute_mismatch_blocks_audit():
    doc = make_investment_weekly_fixture(cards=30)
    bad_doc = type(doc)(
        doc.document_id + "-formula",
        doc.profile_id,
        doc.title,
        doc.subtitle,
        doc.date_range,
        doc.as_of,
        doc.sections,
        doc.metric_ledger,
        doc.claims,
        doc.evidence,
        {**doc.metadata, "formula_results": {"m_temperature": 1}},
    )

    result = ReportCompiler().compile(bad_doc)

    assert result["status"] == "fail"
    assert any(f["code"] == "metric_recompute_mismatch" for f in result["audit"]["findings"])
