"""U2 article cache tests — no live network (plan 2026-07-22-001)."""

from __future__ import annotations

import pytest

from kss.storage import article_cache


@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    (tmp_path / "storage").mkdir()
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    return tmp_path


def _fulltext(url: str, md: str | None = "## Head\n\nPara one.") -> dict:
    return {
        "body": "Head Para one.",
        "body_md": md,
        "extractor": "trafilatura" if md else "strip",
        "title": "T",
        "mode": "fulltext",
        "error": None,
        "char_count": 14,
        "url": url,
    }


def test_first_fetch_writes_cache_second_hits(cache_env, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(*, url="", summary=""):
        calls["n"] += 1
        return _fulltext(url)

    monkeypatch.setattr(article_cache, "body_or_summary", fake_fetch)

    got1 = article_cache.get_or_fetch("https://example.com/a", "sum")
    assert got1["mode"] == "fulltext"
    assert got1["cached"] is False
    assert calls["n"] == 1

    got2 = article_cache.get_or_fetch("https://example.com/a", "sum")
    assert got2["cached"] is True
    assert got2["body_md"] == "## Head\n\nPara one."
    assert calls["n"] == 1  # 命中缓存不再抓取


def test_fetch_failure_returns_stale_cache(cache_env, monkeypatch):
    """Covers AE3: 重抓失败保持旧记录不报错。"""
    monkeypatch.setattr(
        article_cache, "body_or_summary", lambda *, url="", summary="": _fulltext(url)
    )
    article_cache.get_or_fetch("https://example.com/b")

    def failing(*, url="", summary=""):
        return {"body": "", "mode": "empty", "error": "http 503", "char_count": 0}

    monkeypatch.setattr(article_cache, "body_or_summary", failing)
    got = article_cache.get_or_fetch("https://example.com/b", force=True)
    assert got["cached"] is True
    assert got["mode"] == "fulltext"


def test_miss_and_failure_returns_fallback_without_polluting(cache_env, monkeypatch):
    def failing(*, url="", summary=""):
        return {
            "body": summary, "mode": "summary", "error": "http 403",
            "char_count": len(summary), "title": "", "url": url,
        }

    monkeypatch.setattr(article_cache, "body_or_summary", failing)
    got = article_cache.get_or_fetch("https://example.com/c", "rss summary")
    assert got["mode"] == "summary"
    assert article_cache.read_cached("https://example.com/c") is None  # 不落污染记录


def test_empty_url_passthrough(cache_env, monkeypatch):
    monkeypatch.setattr(
        article_cache,
        "body_or_summary",
        lambda *, url="", summary="": {
            "body": summary, "mode": "summary", "error": "no url",
            "char_count": len(summary),
        },
    )
    got = article_cache.get_or_fetch("", "only summary")
    assert got["mode"] == "summary"
