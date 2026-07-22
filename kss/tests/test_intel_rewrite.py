"""Investment rewrite + aggregate tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kss.news.rewrite import aggregate_track_digest, build_rewrite_prompt, run_rewrite
from kss.storage.rewrite_pool import beijing_day, write_draft


def _item(i: int = 0) -> dict:
    return {
        "title": f"News title {i} with enough chars",
        "url": f"https://example.com/{i}",
        "source": "Src",
        "time": "10:00",
        "summary": "Summary text that is long enough for thin threshold checks easily.",
    }


def test_build_rewrite_prompt_has_sections_instruction():
    sys_p, user_p = build_rewrite_prompt(
        "ai", "AI", _item(), "body text here", "fulltext"
    )
    assert "事件" in sys_p
    assert "标的线索" in sys_p
    assert "News title 0" in user_p
    assert "body text" in user_p


def test_build_chinese_rewrite_prompt():
    sys_p, user_p = build_rewrite_prompt(
        "ai", "AI", _item(), "body text here", "fulltext", kind="chinese"
    )
    assert "中文" in sys_p
    assert "破折号" in sys_p
    assert "Markdown" in sys_p
    assert "News title 0" in user_p
    assert "事件" not in sys_p  # not investment schema


def test_run_rewrite_missing_title(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    r = run_rewrite("ai", "AI", {"title": "", "url": "https://x.com"}, fetch_body=False)
    assert r.get("error")
    assert r.get("status") == "failed"


def test_run_rewrite_happy_mock_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    fake_text = """## 事件
某公司发布新品

## 影响
行业关注度上升

## 标的线索
相关硬件链（待核实）

## 待验证
出货数据
"""
    mock_client = MagicMock()
    mock_client.complete.return_value = fake_text

    with patch("kss.news.rewrite.LLMClient", return_value=mock_client):
        with patch(
            "kss.news.rewrite.body_or_summary",
            return_value={
                "body": "x" * 100,
                "mode": "fulltext",
                "char_count": 100,
                "error": None,
            },
        ):
            r = run_rewrite("ai", "AI", _item(1), force=True)

    assert r["status"] == "ready"
    assert r["sections"]["事件"]
    assert r["body_mode"] == "fulltext"
    assert r["body_text"]
    mock_client.complete.assert_called_once()

    # cache hit
    with patch("kss.news.rewrite.LLMClient", return_value=mock_client) as m2:
        r2 = run_rewrite("ai", "AI", _item(1), force=False)
    assert r2.get("from_cache") is True
    m2.assert_not_called()


def test_run_rewrite_on_demand_when_pool_full(tmp_path, monkeypatch):
    """On-demand ignores K; always allowed."""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    day = beijing_day()
    for i in range(10):
        write_draft(
            {
                "item_id": f"ready{i}",
                "track_key": "ai",
                "day": day,
                "status": "ready",
                "text": f"## 事件\nevent {i}",
                "sections": {"事件": f"event {i}", "影响": "", "标的线索": "", "待验证": ""},
            }
        )
    mock_client = MagicMock()
    mock_client.complete.return_value = "## 事件\nnew\n## 影响\n\n## 标的线索\n\n## 待验证\n"
    with patch("kss.news.rewrite.LLMClient", return_value=mock_client):
        with patch(
            "kss.news.rewrite.body_or_summary",
            return_value={"body": "y" * 80, "mode": "summary", "char_count": 80, "error": None},
        ):
            r = run_rewrite("ai", "AI", _item(99), force=True)
    assert r["status"] == "ready"


def test_aggregate_insufficient(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    day = beijing_day()
    write_draft(
        {
            "item_id": "one",
            "track_key": "ai",
            "day": day,
            "status": "ready",
            "text": "## 事件\nonly one",
            "sections": {"事件": "only one", "影响": "imp", "标的线索": "", "待验证": ""},
        }
    )
    got = aggregate_track_digest("ai", day, threshold=3)
    assert got["mode"] == "insufficient"
    assert got["count"] == 1


def test_run_chinese_rewrite_separate_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    mock_client = MagicMock()
    mock_client.complete.return_value = "这是一篇**流畅**的中文改写稿。\n\n第二段继续。"
    with patch("kss.news.rewrite.LLMClient", return_value=mock_client):
        with patch(
            "kss.news.rewrite.body_or_summary",
            return_value={"body": "x" * 100, "mode": "fulltext", "char_count": 100, "error": None},
        ):
            r = run_rewrite("ai", "AI", _item(7), force=True, kind="chinese")
    assert r["status"] == "ready"
    assert r["kind"] == "chinese"
    assert "流畅" in r["text"]
    # 独立于 investment 变体存在（同 item_id 不同 kind，主键 (item_id, kind) 不冲突）
    from kss.storage.rewrite_pool import item_id_for, read_draft

    iid = item_id_for(_item(7))
    assert read_draft(iid, "chinese") is not None
    assert read_draft(iid, "chinese")["kind"] == "chinese"
    assert read_draft(iid, "investment") is None


def test_build_translation_prompt():
    """U3 plan 2026-07-22-001: 忠实译文 prompt，保结构不演绎。"""
    sys_p, user_p = build_rewrite_prompt(
        "ai", "AI", _item(), "## Head\n\nBody para", "fulltext", kind="translation"
    )
    assert "忠实" in sys_p
    assert "不增删" in sys_p
    assert "Markdown" in sys_p
    assert "事件" not in sys_p  # not investment schema
    assert "## Head" in user_p


def test_run_translation_rewrite(tmp_path, monkeypatch):
    """译文走 article_cache 结构化正文，独立稿种落库，二次命中缓存。"""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    (tmp_path / "storage").mkdir(exist_ok=True)
    mock_client = MagicMock()
    mock_client.complete.return_value = "## 标题\n\n第一段译文。\n\n- 列表项一"
    with patch("kss.news.rewrite.LLMClient", return_value=mock_client):
        with patch(
            "kss.storage.article_cache.get_or_fetch",
            return_value={
                "body": "flat text " * 20,
                "body_md": "## Head\n\nPara one.\n\n- item",
                "mode": "fulltext",
                "char_count": 200,
                "error": None,
            },
        ):
            r = run_rewrite("ai", "AI", _item(8), force=True, kind="translation")
    assert r["status"] == "ready"
    assert r["kind"] == "translation"
    assert r["text"].startswith("## ")
    assert r["sections"] == {}
    # 译文输入吃的是 body_md（结构化）
    assert "## Head" in r["body_text"]

    # 二次请求命中缓存不再调 LLM
    with patch("kss.news.rewrite.LLMClient") as m2:
        r2 = run_rewrite("ai", "AI", _item(8), force=False, kind="translation")
    assert r2.get("from_cache") is True
    m2.assert_not_called()


def test_translation_kind_normalization_and_pool_isolation(tmp_path, monkeypatch):
    """kind 归一化不误伤 translation；digest 池默认 kind=investment 不含译文。"""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    from kss.news.rewrite import _normalize_kind
    from kss.storage.rewrite_pool import list_drafts, write_draft as _wd

    assert _normalize_kind("translation") == "translation"
    assert _normalize_kind("译文") == "translation"
    assert _normalize_kind("bogus") == "investment"

    day = beijing_day()
    _wd({"item_id": "t1", "kind": "translation", "track_key": "ai", "day": day,
         "status": "ready", "text": "译文"})
    _wd({"item_id": "t1", "kind": "investment", "track_key": "ai", "day": day,
         "status": "ready", "text": "## 事件\nx"})
    default_pool = list_drafts(track_key="ai", day=day, status="ready")
    assert all(d.get("kind") == "investment" for d in default_pool)


def test_aggregate_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    day = beijing_day()
    for i in range(3):
        write_draft(
            {
                "item_id": f"p{i}",
                "track_key": "ai",
                "day": day,
                "status": "ready",
                "text": f"## 事件\nevent {i}",
                "sections": {
                    "事件": f"event {i} happened today with enough detail",
                    "影响": f"impact {i}",
                    "标的线索": "",
                    "待验证": "",
                },
                "generated_at": f"2026-07-10 10:0{i}:00",
            }
        )
    got = aggregate_track_digest("ai", day, threshold=3)
    assert got["mode"] == "pool"
    assert got["text"].startswith("- ")
    assert got["count"] == 3
    assert "event 0" in got["text"] or "event 2" in got["text"]
