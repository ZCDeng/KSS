"""Best-effort article body fetch for 资讯雷达 reader (plan 2026-07-10-001 U1).

正文提取双层（plan 2026-07-22-001 U1）：trafilatura 主提取产出 markdown-lite
（## 小标题 / - 列表 / 空行分段，喂 Swift parseReadingBlocks），失败或过短回退
stdlib strip 平文本。Returns honest mode flags so UI never pretends full text
was loaded when it was not.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_DEFAULT_TIMEOUT = 14.0
_MAX_BODY_CHARS = 24_000
_MAX_RESPONSE_BYTES = 1_500_000
_MIN_USEFUL_CHARS = 80


def _strip_html(html: str) -> tuple[str, str]:
    """Return (title, text). Prefer bs4 if available; else regex fallback."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.extract()
        text = " ".join(soup.stripped_strings)
        return title, re.sub(r"\s+", " ", text).strip()
    except Exception:  # noqa: BLE001
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        text = re.sub(r"<[^>]+>", " ", html)
        return title, re.sub(r"\s+", " ", text).strip()


def _extract_markdown(html: str, *, max_chars: int = _MAX_BODY_CHARS) -> str | None:
    """trafilatura 主内容提取，输出 markdown-lite；不可用/失败/过短返回 None。"""
    if not html:
        return None
    try:
        import trafilatura  # type: ignore

        md = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_links=False,
            include_tables=True,
        )
    except Exception:  # noqa: BLE001 — 提取失败一律走 strip 回退
        return None
    md = (md or "").strip()
    if len(md) < _MIN_USEFUL_CHARS:
        return None
    if len(md) > max_chars:
        md = md[:max_chars]
    return md


def _validate_http_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("empty url")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme or 'none'}")
    if not parsed.netloc:
        raise ValueError("missing host")
    return raw


def extract_body_from_html(html: str, *, max_chars: int = _MAX_BODY_CHARS) -> dict[str, Any]:
    """Parse HTML string into body payload (no network). Used by tests + fetch.

    body_md（结构化 markdown-lite）提取成功时随 payload 返回；body 恒为平文本兼容旧调用方。
    """
    title, text = _strip_html(html or "")
    if len(text) > max_chars:
        text = text[:max_chars]
    char_count = len(text)
    if char_count < _MIN_USEFUL_CHARS:
        return {
            "body": text,
            "title": title,
            "mode": "empty",
            "error": "body too short" if char_count else "empty body",
            "char_count": char_count,
        }
    body_md = _extract_markdown(html or "", max_chars=max_chars)
    return {
        "body": text,
        "body_md": body_md,
        "extractor": "trafilatura" if body_md else "strip",
        "title": title,
        "mode": "fulltext",
        "error": None,
        "char_count": char_count,
    }


def fetch_article(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    max_chars: int = _MAX_BODY_CHARS,
) -> dict[str, Any]:
    """Fetch URL and extract body.

    Returns:
        {body, title?, mode: fulltext|summary|empty, error?, char_count}
    Mode ``summary`` is reserved for callers that supply RSS summary when
    fulltext fails; this function only returns fulltext or empty.
    """
    try:
        safe = _validate_http_url(url)
    except ValueError as e:
        return {
            "body": "",
            "title": "",
            "mode": "empty",
            "error": str(e),
            "char_count": 0,
        }

    try:
        req = urllib.request.Request(
            safe,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raw = raw[:_MAX_RESPONSE_BYTES]
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
        result = extract_body_from_html(html, max_chars=max_chars)
        result["url"] = safe
        return result
    except urllib.error.HTTPError as e:
        return {
            "body": "",
            "title": "",
            "mode": "empty",
            "error": f"http {e.code}",
            "char_count": 0,
            "url": safe,
        }
    except Exception as e:  # noqa: BLE001 — surface any network/parse failure honestly
        return {
            "body": "",
            "title": "",
            "mode": "empty",
            "error": f"{type(e).__name__}: {e}",
            "char_count": 0,
            "url": safe,
        }


def body_or_summary(
    *,
    url: str = "",
    summary: str = "",
    timeout: float = _DEFAULT_TIMEOUT,
    max_chars: int = _MAX_BODY_CHARS,
) -> dict[str, Any]:
    """Prefer fulltext fetch; fall back to RSS summary with mode=summary."""
    if url:
        got = fetch_article(url, timeout=timeout, max_chars=max_chars)
        if got.get("mode") == "fulltext" and (got.get("body") or "").strip():
            return got
        err = got.get("error")
    else:
        err = "no url"

    summary_text = re.sub(r"\s+", " ", (summary or "").strip())
    if len(summary_text) > max_chars:
        summary_text = summary_text[:max_chars]
    if summary_text:
        return {
            "body": summary_text,
            "title": "",
            "mode": "summary",
            "error": err,
            "char_count": len(summary_text),
            "url": url or None,
        }
    return {
        "body": "",
        "title": "",
        "mode": "empty",
        "error": err or "no summary",
        "char_count": 0,
        "url": url or None,
    }
