"""U7: PDF + Markdown 侧车；无 R9 JSON 则不输出仓位段。"""

from __future__ import annotations

from pathlib import Path

from kss.equity_research.compile import compile_report
from kss.equity_research.handler import run_equity_coverage_tool


def test_default_pdf_and_markdown_sidecar(tmp_path: Path) -> None:
    out = compile_report(
        "# 茅台\n\n正文。\n",
        r9={"label": "合理", "action": "持有", "kelly_lite": 0.05, "quality_grade": "B"},
        output_dir=tmp_path,
        stem="demo",
    )
    assert out["ok"] is True
    assert Path(out["pdf"]).is_file()
    assert Path(out["md"]).is_file()
    assert Path(out["pdf"]).read_bytes()[:4] == b"%PDF"
    md = Path(out["md"]).read_text(encoding="utf-8")
    assert "合理" in md and "0.05" in md
    assert out["engine"] in {"weasyprint", "fpdf2", "stdlib"}


def test_without_r9_strips_action_prose(tmp_path: Path) -> None:
    out = compile_report(
        "# x\n建议仓位一成\n买入\n背景。\n",
        r9=None,
        output_dir=tmp_path,
        stem="nor9",
    )
    md = Path(out["md"]).read_text(encoding="utf-8")
    assert "建议仓位" not in md
    assert "买入" not in md
    assert "背景" in md


def test_docx_without_generator_is_r12(tmp_path: Path) -> None:
    out = compile_report("# x\n", r9={"label": "合理"}, output_dir=tmp_path, stem="w", requested_format="docx")
    assert out["ok"] is False
    assert "无法完成" in (out.get("r12") or "")
    assert out["pdf"] is None


def test_tool_chat_summary_has_arabic_numbers_and_paths(tmp_path: Path) -> None:
    result = run_equity_coverage_tool({
        "query": "600519.SH",
        "assumptions": '{"price": 100, "eps": 8, "win_prob": 0.55, "lose_prob": 0.45}',
        "board": {"600519.SH": {"price": 100, "change_pct": 1.2}},
        "output_dir": str(tmp_path),
        "heartbeat_interval": 0,
        "markdown": "# 覆盖\n",
    })
    assert result["status"] == "ok"
    summary = result["chat_summary"]
    assert "Kelly-lite" in summary
    assert str(result["r9"]["kelly_lite"]) in summary
    assert result["artifacts"]["pdf"]
    assert "assumptions" not in summary.lower() or "price" not in summary
