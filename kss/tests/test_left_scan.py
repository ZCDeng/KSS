from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from kss.research.left_scan import (
    ScanCandidate,
    convert_to_fragment,
    html_fragment,
    pick_scan_file,
    resolve_trade_date,
    wrap_report_html,
)
from kss.research.left_scan_ingest import ingest_left_scan_report
from kss.research.service import ResearchService


def _cand(name: str, mime: str, modified: str, **extra: object) -> ScanCandidate:
    return ScanCandidate(name=name, mime=mime, modified=modified, **extra)


def test_pick_scan_file_prefers_pdf_then_newest_html() -> None:
    picked = pick_scan_file(
        [
            _cand("2026-08-17-左侧机会扫描.html", "text/html", "2026-08-17T16:13:00Z"),
            _cand("2026-08-17-左侧机会扫描.pdf", "application/pdf", "2026-08-17T15:00:00Z"),
            _cand("fin-mkt-analysis-SKILL.md", "text/markdown", "2026-08-17T16:20:00Z"),
            _cand("2026-08-16-左侧机会扫描.pdf", "application/pdf", "2026-08-16T20:00:00Z"),
        ],
        date(2026, 8, 17),
    )
    assert picked.name.endswith(".pdf")

    html_only = pick_scan_file(
        [
            _cand("2026-08-17-左侧机会扫描.html", "text/html", "2026-08-17T12:00:00Z"),
            _cand("2026-08-17-左侧机会扫描.html", "text/html", "2026-08-17T16:13:00Z", file_id="newer"),
        ],
        date(2026, 8, 17),
    )
    assert html_only.file_id == "newer"


def test_resolve_trade_date_uses_yesterday_only_before_eight() -> None:
    available = {date(2026, 8, 17)}
    assert resolve_trade_date(datetime(2026, 8, 17, 20, 0), available) == date(2026, 8, 17)
    assert resolve_trade_date(datetime(2026, 8, 18, 8, 0), available) == date(2026, 8, 17)
    assert resolve_trade_date(datetime(2026, 8, 18, 20, 5), available) == date(2026, 8, 18)


def test_html_fragment_strips_document_chrome_and_scripts() -> None:
    raw = """<!doctype html><html><head><title>x</title></head>
    <body><script>alert(1)</script><h2>左侧机会扫描 · 08-17</h2><p>正文</p></body></html>"""
    fragment = html_fragment(raw)
    assert "<h2>左侧机会扫描 · 08-17</h2>" in fragment
    assert "alert" not in fragment
    assert "<html" not in fragment.lower()


def test_wrap_report_html_uses_sector_review_body() -> None:
    html = wrap_report_html(
        "<h2>今日左侧机会扫描（2026-08-17）</h2><h3>一、盘面</h3><p>一段话</p>",
        trade_date=date(2026, 8, 17),
        source_name="2026-08-17-左侧机会扫描.html",
    )
    assert "kss-report" not in html
    assert "<style>" not in html
    assert html.startswith("<p><b>左侧机会扫描 · 2026-08-17</b></p>")
    assert "<p><b>一、盘面</b></p>" in html
    assert "<h2>" not in html
    assert "<h3>" not in html
    assert "<p>一段话</p>" in html
    assert "<p><i>来源 · 2026-08-17-左侧机会扫描.html</i></p>" in html


def test_ingest_left_scan_is_idempotent_and_listable(tmp_path: Path) -> None:
    service = ResearchService(
        state_root=tmp_path,
        project_root=Path(__file__).resolve().parents[2],
    )
    first = ingest_left_scan_report(
        service,
        fragment="<h2>扫描</h2><p>第一版</p>",
        trade_date=date(2026, 8, 17),
        source_name="2026-08-17-左侧机会扫描.html",
    )
    again = ingest_left_scan_report(
        service,
        fragment="<h2>扫描</h2><p>第一版</p>",
        trade_date=date(2026, 8, 17),
        source_name="2026-08-17-左侧机会扫描.html",
    )
    updated = ingest_left_scan_report(
        service,
        fragment="<h2>扫描</h2><p>第二版</p>",
        trade_date=date(2026, 8, 17),
        source_name="2026-08-17-左侧机会扫描.html",
    )
    listed = service.list_goals(
        origin="scheduled",
        profile_ids=["investment-daily-v1"],
        limit=10,
    )
    assert first["ok"] and first["event"] == "ingested"
    assert again["event"] == "already_ingested"
    assert again["goal_id"] == first["goal_id"]
    assert updated["event"] == "ingested"
    assert updated["goal_id"] == first["goal_id"]
    assert updated["object_hash"] != first["object_hash"]
    assert len(listed["reports"]) == 1
    row = listed["reports"][0]
    assert row["goal_id"] == first["goal_id"]
    assert row["title"] == "左侧机会扫描 · 2026-08-17"
    assert row["date_start"] == "2026-08-17"
    assert row["is_draft"] is False
    assert row["audit_status"] == "pass"
    goal = service.repo.get_goal(first["goal_id"]) or {}
    assert goal["status"] == "completed"
    assert {task["status"] for task in goal["tasks"]} == {"succeeded"}


def test_formal_review_no_longer_kicks_daily() -> None:
    wrapper = (Path(__file__).resolve().parents[2] / "scripts" / "run_formal_daily_review.sh").read_text(
        encoding="utf-8"
    )
    assert "kss_kick_next investment_analysis_daily" not in wrapper


def test_convert_html_bytes() -> None:
    fragment = convert_to_fragment(
        "2026-08-17-左侧机会扫描.html",
        "text/html",
        b"<h2>ok</h2>",
    )
    assert fragment == "<h2>ok</h2>"
