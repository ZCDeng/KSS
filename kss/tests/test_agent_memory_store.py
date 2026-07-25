from __future__ import annotations

import time

import pytest

from kss.agent import MemoryStore


def test_memory_store_propose_approve_search_archive_delete(tmp_path):
    store = MemoryStore(tmp_path)
    proposed = store.propose(
        "decision",
        "Agent Core 采用 append-only JSONL 作为本地状态格式",
        source_session="s1",
        source_entry="e1",
        tags=("agent", "jsonl"),
    )

    assert store.search("JSONL") == []
    approved = store.approve(proposed.id)
    assert approved.status == "approved"
    assert approved.source_session == "s1"
    assert approved.source_entry == "e1"
    assert approved.tags == ("agent", "jsonl")
    assert approved.content == approved.text
    assert approved.expires_at is not None
    assert store.search("JSONL")[0].id == proposed.id

    archived = store.archive(proposed.id)
    assert archived.status == "archived"
    assert store.search("JSONL") == []

    deleted = store.delete(proposed.id)
    assert deleted.status == "deleted"


def test_memory_store_rejects_secrets_and_live_market_numbers(tmp_path):
    store = MemoryStore(tmp_path)

    with pytest.raises(ValueError, match="密钥"):
        store.propose("preference", "api_key=sk-testsecret123456789")
    with pytest.raises(ValueError, match="实时行情"):
        store.propose("thesis", "今日价格 12.3 元 当前")


def test_memory_store_recall_uses_rank_and_caps_items(tmp_path):
    store = MemoryStore(tmp_path)
    first = store.propose("preference", "用户偏好：做 KSS 变更时保持纯新增零侵入。" + "长" * 300)
    second = store.propose("decision", "Agent Core 会话恢复时标记 interrupted，不重放旧工具。")
    store.approve(first.id)
    store.approve(second.id)

    now_ms = int(time.time() * 1000)
    recalled = store.recall("纯新增 零侵入", now_ms=now_ms, limit=10)

    assert 1 <= len(recalled) <= 5
    assert "纯新增" in recalled[0]
    assert all(len(item) <= 250 for item in recalled)


def test_memory_store_default_expiry_policy(tmp_path):
    store = MemoryStore(tmp_path)
    pref = store.propose("preference", "偏好常驻")
    decision = store.propose("decision", "决策 180 天")
    thesis = store.propose("thesis", "观点 30 天")

    assert pref.expires_at is None
    assert decision.expires_at is not None
    assert thesis.expires_at is not None
    assert thesis.expires_at < decision.expires_at
    assert thesis.metadata is not None
    assert thesis.metadata["review_required"] is True


def test_memory_store_expiry_hides_old_records(tmp_path):
    store = MemoryStore(tmp_path)
    record = store.propose("thesis", "过期观点", expires_at=time.time() - 1)
    store.approve(record.id)

    assert store.search("过期") == []
