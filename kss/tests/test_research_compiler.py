from __future__ import annotations

import json

from kss.research.compiler import ReportCompiler, make_investment_weekly_fixture
from kss.research.report_models import MetricEntry, MetricLedger, ReportBlock


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
        {
            **doc.metadata,
            "formula_inputs": {
                **doc.metadata["formula_inputs"],
                "m_temperature": [1],
            },
        },
    )

    result = ReportCompiler().compile(bad_doc)

    assert result["status"] == "fail"
    assert any(f["code"] == "metric_recompute_mismatch" for f in result["audit"]["findings"])


def test_invalid_evidence_hash_and_unreviewed_model_score_block_audit():
    doc = make_investment_weekly_fixture(cards=30)
    evidence = list(doc.evidence)
    evidence[0] = type(evidence[0])(
        evidence[0].evidence_id,
        evidence[0].source_tier,
        evidence[0].title,
        evidence[0].uri,
        evidence[0].data_as_of,
        "not-a-sha256",
        evidence[0].caveat,
    )
    claims = list(doc.claims)
    claims[0] = type(claims[0])(
        claims[0].claim_id,
        claims[0].text,
        claims[0].evidence_refs,
        claims[0].confidence,
        False,
        claims[0].rubric_id,
        claims[0].rubric_version,
    )
    bad_doc = type(doc)(
        doc.document_id + "-integrity",
        doc.profile_id,
        doc.title,
        doc.subtitle,
        doc.date_range,
        doc.as_of,
        doc.sections,
        doc.metric_ledger,
        claims,
        evidence,
        doc.metadata,
    )

    result = ReportCompiler().compile(bad_doc)
    codes = {finding["code"] for finding in result["audit"]["findings"]}

    assert result["status"] == "fail"
    assert "evidence_hash_invalid" in codes
    assert "claim_score_missing_review_flag" in codes


def test_malformed_numeric_metadata_becomes_audit_finding_instead_of_crash():
    doc = make_investment_weekly_fixture(cards=30)
    metrics = list(doc.metric_ledger.metrics)
    original = metrics[0]
    metrics[0] = MetricEntry(
        original.metric_id,
        original.label,
        "not-a-number",
        original.unit,
        original.precision,
        original.formula_id,
        original.formula_version,
        original.input_refs,
        original.as_of,
    )
    bad_doc = type(doc)(
        doc.document_id + "-numeric",
        doc.profile_id,
        doc.title,
        doc.subtitle,
        doc.date_range,
        doc.as_of,
        doc.sections,
        MetricLedger(metrics),
        doc.claims,
        doc.evidence,
        {**doc.metadata, "card_count": "invalid"},
    )

    result = ReportCompiler().compile(bad_doc)
    codes = {finding["code"] for finding in result["audit"]["findings"]}

    assert result["status"] == "fail"
    assert "metric_value_not_numeric" in codes
    assert "precision_card_count_invalid" in codes
