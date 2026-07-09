"""资讯雷达 AI digest 测试（plan 2026-07-09-001）。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kss.news.digest_ai import (
    _MAX_ITEMS,
    build_prompt,
    parse_items_payload,
    run_digest,
)
from kss.storage.notes import intel_digest_exists, save_intel_digest


def _make_items(n: int) -> list[dict]:
    """构造 n 条 mock 资讯"""
    return [
        {
            "title": f"Test news #{i}",
            "url": f"https://example.com/{i}",
            "time": "07-09 10:00",
            "source": f"Source {i % 3}",
            "summary": f"Summary for news {i}.",
        }
        for i in range(n)
    ]


def test_build_prompt_includes_track_name_and_items():
    items = _make_items(5)
    sys_p, user_p = build_prompt("AI / 大模型", items)
    assert "AI / 大模型" in user_p
    assert "Test news #0" in user_p
    assert "Source 0" in user_p
    assert "5" in user_p  # 5 条
    assert sys_p  # 非空


def test_build_prompt_truncates_to_max_items():
    items = _make_items(50)
    sys_p, user_p = build_prompt("AI", items)
    # 超过 _MAX_ITEMS 的 item 不应出现
    assert f"Test news #{_MAX_ITEMS - 1}" in user_p  # 最后保留的
    assert f"Test news #{_MAX_ITEMS}" not in user_p
    assert f"Test news #{_MAX_ITEMS + 1}" not in user_p


def test_parse_items_payload_json_array():
    raw = json.dumps([{"title": "x"}, {"title": "y"}])
    items = parse_items_payload(raw)
    assert len(items) == 2
    assert items[0]["title"] == "x"


def test_parse_items_payload_invalid_json():
    with pytest.raises(ValueError):
        parse_items_payload("not json")


def test_parse_items_payload_not_array():
    with pytest.raises(ValueError):
        parse_items_payload(json.dumps({"a": 1}))


def test_run_digest_empty_items():
    result = run_digest("ai", "AI", [])
    assert result["skipped"] is True
    assert result["text"] == ""


def test_run_digest_cached_when_exists(tmp_path, monkeypatch):
    """已有当日沉淀 + force=False → 不调 LLM，直接返回缓存"""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    items = _make_items(3)
    # 先写一次沉淀
    save_intel_digest("ai", "AI / 大模型", "test prompt", "- cached test", "test-model", items)

    result = run_digest("ai", "AI / 大模型", items, force=False)
    assert result["from_cache"] is True
    assert "cached test" in result["text"]
    assert result["model"] == "(cached)"


def test_run_digest_force_skips_cache(tmp_path, monkeypatch):
    """force=True 时即使有缓存也重新调 LLM"""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    items = _make_items(3)
    save_intel_digest("ai", "AI / 大模型", "test prompt", "- cached", "test-model", items)

    with patch("kss.news.digest_ai.LLMClient") as MockClient:
        mock = MockClient.return_value
        mock.complete.return_value = "- fresh result"
        result = run_digest("ai", "AI / 大模型", items, force=True)
        assert result.get("from_cache") is not True
        assert "fresh result" in result["text"]
        mock.complete.assert_called_once()


def test_run_digest_llm_unavailable_returns_error():
    items = _make_items(3)
    # 不写沉淀（强制走 LLM）
    with patch("kss.news.digest_ai.LLMClient") as MockClient:
        from kss.llm.openai_client import LLMUnavailable
        mock = MockClient.return_value
        mock.complete.side_effect = LLMUnavailable("401: invalid api key")
        result = run_digest("ai", "AI", items, force=True)
        assert "401" in result["error"]
        assert result["error_type"] == "auth"
        assert result["text"] == ""


def test_run_digest_timeout_returns_timeout_type():
    items = _make_items(3)
    with patch("kss.news.digest_ai.LLMClient") as MockClient:
        from kss.llm.openai_client import LLMUnavailable
        mock = MockClient.return_value
        mock.complete.side_effect = LLMUnavailable("request timed out after 30s")
        result = run_digest("ai", "AI", items, force=True)
        assert result["error_type"] == "timeout"