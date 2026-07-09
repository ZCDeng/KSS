"""U1 article_fetch tests — no live network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kss.news.article_fetch import (
    body_or_summary,
    extract_body_from_html,
    fetch_article,
)


def test_extract_body_fulltext_from_html():
    html = """
    <html><head><title>Hello World News</title></head>
    <body>
      <script>evil()</script>
      <article>
        <p>Paragraph one with enough content for the usefulness threshold to pass easily.</p>
        <p>Paragraph two continues the story so we have more than eighty characters total in body.</p>
      </article>
    </body></html>
    """
    got = extract_body_from_html(html)
    assert got["mode"] == "fulltext"
    assert got["title"] == "Hello World News"
    assert "Paragraph one" in got["body"]
    assert "evil" not in got["body"]
    assert got["char_count"] >= 80
    assert got["error"] is None


def test_extract_body_empty_when_too_short():
    html = "<html><body><p>hi</p></body></html>"
    got = extract_body_from_html(html)
    assert got["mode"] == "empty"
    assert got["error"]


def test_extract_body_truncates_over_cap():
    long_p = "word " * 10_000
    html = f"<html><body><p>{long_p}</p></body></html>"
    got = extract_body_from_html(html, max_chars=500)
    assert got["mode"] == "fulltext"
    assert len(got["body"]) <= 500


def test_fetch_article_invalid_url():
    got = fetch_article("not-a-url")
    assert got["mode"] == "empty"
    assert got["error"]
    assert got["body"] == ""


def test_fetch_article_empty_url():
    got = fetch_article("")
    assert got["mode"] == "empty"


def test_fetch_article_mocked_http_ok():
    html = (
        b"<html><head><title>T</title></head><body>"
        + b"<p>" + b"content " * 30 + b"</p></body></html>"
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = html
    mock_resp.headers.get_content_charset.return_value = "utf-8"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("kss.news.article_fetch.urllib.request.urlopen", return_value=mock_resp):
        got = fetch_article("https://example.com/a")
    assert got["mode"] == "fulltext"
    assert got["url"] == "https://example.com/a"
    assert "content" in got["body"]


def test_fetch_article_mocked_timeout():
    with patch(
        "kss.news.article_fetch.urllib.request.urlopen",
        side_effect=TimeoutError("timed out"),
    ):
        got = fetch_article("https://example.com/slow")
    assert got["mode"] == "empty"
    assert "Timeout" in (got["error"] or "")


def test_body_or_summary_falls_back_to_summary():
    with patch(
        "kss.news.article_fetch.fetch_article",
        return_value={"body": "", "mode": "empty", "error": "http 403", "char_count": 0},
    ):
        got = body_or_summary(url="https://example.com/x", summary="  RSS summary text that is short  ")
    assert got["mode"] == "summary"
    assert got["body"] == "RSS summary text that is short"
    assert "403" in (got["error"] or "")


def test_body_or_summary_prefers_fulltext():
    with patch(
        "kss.news.article_fetch.fetch_article",
        return_value={
            "body": "x" * 100,
            "mode": "fulltext",
            "error": None,
            "char_count": 100,
            "title": "T",
        },
    ):
        got = body_or_summary(url="https://example.com/x", summary="ignored")
    assert got["mode"] == "fulltext"
    assert got["body"] == "x" * 100
