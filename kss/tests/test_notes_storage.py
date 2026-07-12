"""沉淀库（storage.notes）测试（plan 2026-07-09-001；结构化部分 U15 割接自 .json 文件）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kss.storage.notes import (
    _atomic_write,
    intel_digest_exists,
    read_intel_digest_response,
    save_intel_digest,
)


@pytest.fixture
def fake_state_root(tmp_path, monkeypatch):
    """把 STATE_ROOT 指向临时目录"""
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    return tmp_path


def _read_note_payload(state_root: Path, date: str, track_key: str) -> dict:
    conn = sqlite3.connect(state_root / "storage" / "kss.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT payload_json FROM intel_digest_notes WHERE digest_date=? AND track_key=?",
        (date, track_key),
    ).fetchone()
    conn.close()
    return json.loads(row["payload_json"]) if row else None


def test_save_intel_digest_creates_md_and_db_row(fake_state_root):
    items = [{"title": "x1", "url": "u1", "time": "07-09 10:00", "source": "src"}]
    md_path = save_intel_digest(
        "ai", "AI / 大模型", "prompt text", "- bullet 1\n- bullet 2",
        "test-model", items,
    )
    assert md_path.exists()

    md_content = md_path.read_text(encoding="utf-8")
    assert "# AI / 大模型 要点 ·" in md_content
    assert "- bullet 1" in md_content

    from kss.storage.notes import _date_str

    payload = _read_note_payload(fake_state_root, _date_str(), "ai")
    assert payload is not None
    assert payload["track_key"] == "ai"
    assert payload["track_name"] == "AI / 大模型"
    assert payload["prompt"] == "prompt text"
    assert payload["model"] == "test-model"
    assert payload["item_count"] == 1
    assert "generated_at" in payload


def test_save_intel_digest_overwrites_existing(fake_state_root):
    items = [{"title": "x1"}]
    save_intel_digest("ai", "AI", "p1", "old text", "m", items)
    save_intel_digest("ai", "AI", "p2", "new text", "m", items)
    md_path = intel_digest_exists("ai")
    assert md_path is not None
    content = md_path.read_text(encoding="utf-8")
    assert "new text" in content
    assert "old text" not in content


def test_intel_digest_exists_false_when_no_file(fake_state_root):
    assert intel_digest_exists("nonexistent") is None


def test_read_intel_digest_response_strips_header(fake_state_root):
    items = [{"title": "x"}]
    save_intel_digest("ai", "AI", "p", "- first bullet\n- second bullet", "m", items)
    body = read_intel_digest_response("ai")
    assert body is not None
    assert body.startswith("- first bullet")
    assert "# AI" not in body  # header stripped


def test_atomic_write_replaces_target(fake_state_root):
    target = fake_state_root / "test.txt"
    _atomic_write(target, "first content")
    assert target.read_text() == "first content"
    _atomic_write(target, "second content")
    assert target.read_text() == "second content"


def test_save_creates_notes_directory(fake_state_root):
    notes_dir = fake_state_root / "storage" / "notes"
    assert not notes_dir.exists()
    items = [{"title": "x"}]
    save_intel_digest("ai", "AI", "p", "text", "m", items)
    assert notes_dir.exists()