"""把覆盖 Markdown + R9 JSON 编成 PDF 与侧车。无 JSON 则不写评级/仓位段。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kss.equity_research.intent import r12_phrase

_PINGFANG = re.compile(r"pingfang|苹方", re.I)


def compile_report(
    markdown: str,
    *,
    r9: dict[str, Any] | None,
    output_dir: Path,
    stem: str,
    requested_format: str = "pdf",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if requested_format.lower() in {"docx", "xlsx", "xls", "workbook", "word"}:
        return {
            "ok": False,
            "r12": r12_phrase("incomplete").replace("覆盖未在预算内结束", "该格式无法完成"),
            "format": requested_format,
            "pdf": None,
            "md": None,
            "engine": None,
        }
    body = markdown or ""
    if r9:
        body = _append_r9(body, r9)
    else:
        body = _strip_r9_prose(body)
    md_path = output_dir / f"{stem}.md"
    md_path.write_text(body, encoding="utf-8")
    pdf_path = output_dir / f"{stem}.pdf"
    engine = _write_pdf(pdf_path, body, r9)
    return {
        "ok": True,
        "r12": None,
        "format": "pdf",
        "pdf": str(pdf_path),
        "md": str(md_path),
        "engine": engine,
        "relative_pdf": _rel(pdf_path),
        "relative_md": _rel(md_path),
    }


def _append_r9(markdown: str, r9: dict[str, Any]) -> str:
    block = [
        "",
        "## 脚本结论（只可引用，不可改写）",
        "",
        f"- 估值标签：{r9.get('label')}",
        f"- 投资动作：{r9.get('action')}",
        f"- Kelly-lite：{r9.get('kelly_lite')}",
        f"- 质量等级：{r9.get('quality_grade')}",
        "",
        "```json",
        json.dumps(r9, ensure_ascii=False, sort_keys=True, default=str),
        "```",
        "",
    ]
    return markdown.rstrip() + "\n" + "\n".join(block)


def _strip_r9_prose(markdown: str) -> str:
    banned = ("买入", "卖出", "建议仓位", "Kelly", "目标价")
    lines = []
    for line in (markdown or "").splitlines():
        if any(tok in line for tok in banned):
            continue
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def _write_pdf(path: Path, markdown: str, r9: dict[str, Any] | None) -> str:
    try:
        engine = _weasyprint_pdf(path, markdown)
        if engine:
            return engine
    except Exception:
        pass
    try:
        engine = _fpdf2_pdf(path, markdown, r9)
        if engine:
            return engine
    except Exception:
        pass
    _stdlib_pdf(path, markdown, r9)
    return "stdlib"


def _weasyprint_pdf(path: Path, markdown: str) -> str | None:
    from weasyprint import HTML  # type: ignore

    font = _embeddable_font()
    if font and _PINGFANG.search(str(font)):
        font = None
    family = f"font-family: '{font.stem}', sans-serif;" if font else "font-family: sans-serif;"
    face = ""
    if font:
        face = f"@font-face {{ font-family: '{font.stem}'; src: url('{font.as_uri()}'); }}"
    html = f"<html><head><style>{face} body {{ {family} }}</style></head><body><pre>{_escape(markdown)}</pre></body></html>"
    HTML(string=html, base_url=str(path.parent)).write_pdf(str(path))
    return "weasyprint"


def _fpdf2_pdf(path: Path, markdown: str, r9: dict[str, Any] | None) -> str | None:
    from fpdf import FPDF  # type: ignore

    pdf = FPDF()
    pdf.add_page()
    font = _embeddable_font()
    if font and not _PINGFANG.search(str(font)):
        pdf.add_font("kss", fname=str(font))
        pdf.set_font("kss", size=11)
    else:
        pdf.set_font("Helvetica", size=11)
    text = _ascii_summary(markdown, r9)
    pdf.multi_cell(0, 6, text)
    pdf.output(str(path))
    return "fpdf2"


def _stdlib_pdf(path: Path, markdown: str, r9: dict[str, Any] | None) -> None:
    text = _ascii_summary(markdown, r9)[:1500]
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 11 Tf 48 780 Td ({text}) Tj ET\n".encode("latin-1", "replace")
    contents = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        contents,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode("ascii")
        buf += obj
        buf += b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for off in offsets[1:]:
        buf += f"{off:010d} 00000 n \n".encode("ascii")
    buf += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(buf)


def _ascii_summary(markdown: str, r9: dict[str, Any] | None) -> str:
    bits = ["KSS equity coverage. Chinese body is in the markdown sidecar."]
    if r9:
        bits.append(
            f"label={r9.get('label')} action={r9.get('action')} kelly={r9.get('kelly_lite')}"
        )
    ascii_md = markdown.encode("ascii", "replace").decode("ascii")
    bits.append(ascii_md[:1200])
    return "\n".join(bits)


def _embeddable_font() -> Path | None:
    candidates = [
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/NotoSansCJKsc-Regular.otf"),
        Path("/System/Library/Fonts/NotoSansCJK-Regular.ttc"),
        Path("/opt/homebrew/share/fonts/NotoSansSC-Regular.otf"),
    ]
    for path in candidates:
        if path.is_file() and not _PINGFANG.search(path.name):
            return path
    return None


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _rel(path: Path) -> str:
    try:
        from os import environ
        root = Path(environ["KSS_STATE_ROOT"]) if environ.get("KSS_STATE_ROOT") else None
        if root:
            return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        pass
    return str(path.name)


def timestamped_stem(code: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", code)
    return f"{stamp}_{safe}"
