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


def test_extract_body_structured_markdown():
    """U1 plan 2026-07-22-001: 结构化提取产出 ## 小标题 / 列表 / 空行分段。"""
    import pytest

    pytest.importorskip("trafilatura")
    paras = (
        "<h2>Section One</h2>"
        + "".join(
            f"<p>Paragraph {i} carries enough real sentence content to satisfy "
            f"extraction thresholds without being boilerplate filler text.</p>"
            for i in range(1, 4)
        )
        + "<h2>Section Two</h2><ul><li>First bullet point item</li>"
        + "<li>Second bullet point item</li></ul>"
        + "<p>Closing paragraph with more meaningful content to round out the article body.</p>"
    )
    html = (
        "<html><head><title>Structured</title></head><body>"
        "<nav>Home | About | Subscribe now</nav>"
        f"<article>{paras}</article>"
        "<footer>Related articles: one two three</footer>"
        "</body></html>"
    )
    got = extract_body_from_html(html)
    assert got["mode"] == "fulltext"
    if got["extractor"] == "trafilatura":
        md = got["body_md"]
        assert md
        assert "Section One" in md
        assert "\n" in md  # 保留换行分块，不再压平
    else:
        # trafilatura 对该 fixture 判定失败时回退 strip：body_md 为空但主链路不破
        assert got["body_md"] is None


def test_extract_body_fallback_when_trafilatura_short(monkeypatch):
    """结构化产出过短 → 回退 strip，extractor 标记 fallback。"""
    from kss.news import article_fetch as af

    monkeypatch.setattr(af, "_extract_markdown", lambda html, **kw: None)
    html = (
        "<html><body><p>"
        + "plain fallback content repeated enough times to pass threshold. " * 3
        + "</p></body></html>"
    )
    got = af.extract_body_from_html(html)
    assert got["mode"] == "fulltext"
    assert got["extractor"] == "strip"
    assert got["body_md"] is None
    assert "plain fallback" in got["body"]


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


def test_fetch_article_gzip_forced_body():
    """bilibili 等站点对未声明 Accept-Encoding 的客户端也强制 gzip：按 magic 解压。"""
    import gzip as _gzip

    html = (
        "<html><head><title>G</title></head><body><p>"
        + "中文正文内容，足够长以通过有效性门槛。" * 10
        + "</p></body></html>"
    ).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = _gzip.compress(html)
    mock_resp.headers.get_content_charset.return_value = "utf-8"
    mock_resp.headers.get.return_value = "gzip"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("kss.news.article_fetch.urllib.request.urlopen", return_value=mock_resp):
        got = fetch_article("https://example.com/gz")
    assert got["mode"] == "fulltext"
    assert "中文正文内容" in got["body"]
    assert "�" not in got["body"]


def test_fetch_article_meta_charset_fallback():
    """声明 charset 错误时按 meta charset 探测（GBK 页面常见）。"""
    html_text = (
        '<html><head><meta charset="gbk"><title>G</title></head><body><p>'
        + "国产资讯页面正文，编码为 GBK，长度足够通过门槛。" * 8
        + "</p></body></html>"
    )
    raw = html_text.encode("gbk")
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.headers.get_content_charset.return_value = None  # 头未声明
    mock_resp.headers.get.return_value = None
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("kss.news.article_fetch.urllib.request.urlopen", return_value=mock_resp):
        got = fetch_article("https://example.com/gbk")
    assert got["mode"] == "fulltext"
    assert "国产资讯" in got["body"]


def test_extract_body_mojibake_gate():
    """替换符占比过高的正文按 empty 处理，不得进入 fulltext/缓存。"""
    garbage = "�" * 300 + "ok" * 20
    html = f"<html><body><p>{garbage}</p></body></html>"
    got = extract_body_from_html(html)
    assert got["mode"] == "empty"
    assert "mojibake" in (got["error"] or "")


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
