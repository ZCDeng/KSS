"""Pick and convert a dated 左侧机会扫描 file into desktop report HTML."""

from __future__ import annotations

import html
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
SKIP_NAME_RE = re.compile(r"skill", re.IGNORECASE)
HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
PDF_MIME = "application/pdf"
HTML_MIMES = {"text/html", "application/xhtml+xml"}
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


class LeftScanError(ValueError):
    """A dated scan file could not be selected or converted."""


@dataclass(frozen=True)
class ScanCandidate:
    name: str
    mime: str
    modified: str
    size: int = 0
    path: str | None = None
    file_id: str | None = None

    @property
    def trade_date(self) -> date | None:
        return trade_date_from_name(self.name)


def trade_date_from_name(name: str) -> date | None:
    match = DATE_RE.search(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group("date"))
    except ValueError:
        return None


def _rank(candidate: ScanCandidate) -> tuple[int, str]:
    mime = (candidate.mime or "").split(";")[0].strip().lower()
    name = candidate.name.lower()
    if mime == PDF_MIME or name.endswith(".pdf"):
        return (0, candidate.modified)
    if mime in HTML_MIMES or name.endswith(".html") or name.endswith(".htm"):
        return (1, candidate.modified)
    if mime == GOOGLE_DOC_MIME:
        return (2, candidate.modified)
    return (9, candidate.modified)


def pick_scan_file(candidates: Iterable[ScanCandidate], trade_date: date) -> ScanCandidate:
    eligible: list[ScanCandidate] = []
    for item in candidates:
        if SKIP_NAME_RE.search(item.name):
            continue
        if item.trade_date != trade_date:
            continue
        if _rank(item)[0] >= 9:
            continue
        eligible.append(item)
    if not eligible:
        raise LeftScanError(f"no_scan_file_for_{trade_date.isoformat()}")
    eligible.sort(key=lambda item: _rank(item))
    # Prefer PDF/HTML class, then the newest modifiedTime within that class.
    best_class = _rank(eligible[0])[0]
    same_class = [item for item in eligible if _rank(item)[0] == best_class]
    same_class.sort(key=lambda item: item.modified, reverse=True)
    return same_class[0]


def resolve_trade_date(now: datetime, available: Iterable[date]) -> date:
    """Prefer today's file; before 20:00 a catch-up run may take yesterday."""
    present = set(available)
    today = now.date()
    if today in present:
        return today
    yesterday = today - timedelta(days=1)
    if now.hour < 20 and yesterday in present:
        return yesterday
    return today


def dated_candidates(candidates: Iterable[ScanCandidate]) -> set[date]:
    found: set[date] = set()
    for item in candidates:
        if SKIP_NAME_RE.search(item.name):
            continue
        if item.trade_date is not None and _rank(item)[0] < 9:
            found.add(item.trade_date)
    return found


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def html_fragment(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"(?is)<script\b.*?</script>", "", text)
    body = re.search(r"(?is)<body\b[^>]*>(.*)</body>", text)
    if body:
        text = body.group(1).strip()
    text = re.sub(r"(?is)</?(html|head|body)\b[^>]*>", "", text)
    return text.strip()


def pdf_bytes_to_fragment(data: bytes, *, pdftotext_bin: str = "pdftotext") -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        proc = subprocess.run(
            [pdftotext_bin, "-layout", "-enc", "UTF-8", tmp.name, "-"],
            capture_output=True,
            check=False,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()
        raise LeftScanError(detail or "pdf_text_extract_failed")
    lines = [
        html.escape(line.rstrip())
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines()
    ]
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.strip():
            buf.append(line)
            continue
        if buf:
            paragraphs.append("<p>" + "<br/>".join(buf) + "</p>")
            buf = []
    if buf:
        paragraphs.append("<p>" + "<br/>".join(buf) + "</p>")
    if not paragraphs:
        raise LeftScanError("pdf_text_empty")
    return "\n".join(paragraphs)


def convert_to_fragment(name: str, mime: str, data: bytes) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    lowered = name.lower()
    if mime == PDF_MIME or lowered.endswith(".pdf"):
        return pdf_bytes_to_fragment(data)
    if mime in HTML_MIMES or mime == GOOGLE_DOC_MIME or lowered.endswith((".html", ".htm")):
        return html_fragment(decode_text(data))
    raise LeftScanError(f"unsupported_scan_type:{mime or lowered}")


def _plain_heading(inner: str) -> str:
    return re.sub(r"<[^>]+>", "", inner).strip()


def restyle_as_review_body(fragment: str, *, trade_date: date) -> str:
    """Map scan headings onto the 板块复盘 commentary body: bold section labels."""
    title = f"左侧机会扫描 · {trade_date.isoformat()}"
    body = fragment.strip()
    first = HEADING_RE.match(body)
    if first and ("左侧机会扫描" in _plain_heading(first.group(2)) or first.group(1) in {"1", "2"}):
        body = body[first.end():].lstrip()

    def _heading(match: re.Match[str]) -> str:
        text = _plain_heading(match.group(2))
        if not text:
            return ""
        return f"<p><b>{html.escape(text)}</b></p>"

    body = HEADING_RE.sub(_heading, body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return f"<p><b>{html.escape(title)}</b></p>\n\n{body}" if body else f"<p><b>{html.escape(title)}</b></p>"


def wrap_report_html(
    fragment: str,
    *,
    trade_date: date,
    source_name: str,
) -> str:
    """Emit a Kami commentary fragment, same body contract as 板块复盘投顾点评."""
    body = restyle_as_review_body(fragment, trade_date=trade_date)
    source = f"<p><i>来源 · {html.escape(source_name)}</i></p>"
    return f"{body}\n\n{source}\n"
