from __future__ import annotations

import json

import pytest

from kss.research.corpus import AnalystCorpusError, content_sha256, load_analyst_corpus


def _record(
    source_message_id: str = "msg-1",
    content: str = "机器人订单继续放量。",
    *,
    object_ref: bool = False,
) -> dict[str, object]:
    digest = content_sha256(content)
    row: dict[str, object] = {
        "protocol_version": "analyst-corpus-v1",
        "source_message_id": source_message_id,
        "analyst_id": "analyst-zhang",
        "published_at": "2026-07-27T09:30:00+08:00",
        "source_uri": f"kss://analyst-corpus/{source_message_id}",
        "content_hash": digest,
        "provenance": {"channel": "analyst-chat", "source_relation": "direct", "broker": "测试证券"},
        "attachments": [
            {
                "attachment_id": "att-1",
                "media_type": "application/pdf",
                "content_hash": "1" * 64,
                "source_uri": "kss://object/att-1",
            }
        ],
    }
    if object_ref:
        row["object_ref"] = digest
    else:
        row["content"] = content
    return row


def _write_jsonl(path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def test_load_analyst_corpus_accepts_content_and_object_ref_jsonl(tmp_path) -> None:
    path = tmp_path / "corpus.jsonl"
    _write_jsonl(path, [_record(), _record("msg-2", object_ref=True)])

    records = load_analyst_corpus(path)

    assert len(records) == 2
    assert records[0].source_message_id == "msg-1"
    assert records[0].analyst_id == "analyst-zhang"
    assert records[0].content_hash == content_sha256("机器人订单继续放量。")
    assert records[1].object_ref == records[1].content_hash
    assert records[0].attachments[0].media_type == "application/pdf"


def test_load_analyst_corpus_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.jsonl"
    _write_jsonl(target, [_record()])
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)

    with pytest.raises(AnalystCorpusError, match="符号链接"):
        load_analyst_corpus(link)


def test_load_analyst_corpus_rejects_nul_and_non_utf8(tmp_path) -> None:
    nul_path = tmp_path / "nul.jsonl"
    nul_path.write_bytes(b'{"protocol_version":"analyst-corpus-v1"}\x00')
    bad_encoding_path = tmp_path / "gbk.jsonl"
    bad_encoding_path.write_bytes("不是 UTF8".encode("gbk"))

    with pytest.raises(AnalystCorpusError, match="NUL"):
        load_analyst_corpus(nul_path)
    with pytest.raises(AnalystCorpusError, match="UTF-8"):
        load_analyst_corpus(bad_encoding_path)


def test_load_analyst_corpus_rejects_files_over_64mb(tmp_path) -> None:
    oversized = tmp_path / "oversized.jsonl"
    with oversized.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)

    with pytest.raises(AnalystCorpusError, match="64MB"):
        load_analyst_corpus(oversized)


def test_load_analyst_corpus_rejects_required_hash_duplicates_and_content_choice(tmp_path) -> None:
    missing = _record()
    del missing["analyst_id"]
    mismatch = _record("msg-2")
    mismatch["content_hash"] = "0" * 64
    both = _record("msg-3")
    both["object_ref"] = both["content_hash"]
    duplicate_path = tmp_path / "duplicate.jsonl"
    _write_jsonl(duplicate_path, [_record(), _record()])

    missing_path = tmp_path / "missing.jsonl"
    mismatch_path = tmp_path / "mismatch.jsonl"
    both_path = tmp_path / "both.jsonl"
    _write_jsonl(missing_path, [missing])
    _write_jsonl(mismatch_path, [mismatch])
    _write_jsonl(both_path, [both])

    with pytest.raises(AnalystCorpusError, match="缺少必填字段"):
        load_analyst_corpus(missing_path)
    with pytest.raises(AnalystCorpusError, match="不匹配"):
        load_analyst_corpus(mismatch_path)
    with pytest.raises(AnalystCorpusError, match="二选一"):
        load_analyst_corpus(both_path)
    with pytest.raises(AnalystCorpusError, match="重复 source_message_id"):
        load_analyst_corpus(duplicate_path)
