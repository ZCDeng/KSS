from __future__ import annotations

import hashlib

import pytest

from kss.agent import AttachmentError, AttachmentRecord, AttachmentStore


def test_text_attachment_is_content_addressed_and_extraction_is_not_in_metadata(
    tmp_path,
):
    source = tmp_path / "notes.md"
    source.write_text("# 周报\n结论", encoding="utf-8")
    store = AttachmentStore(tmp_path / "state")

    record = store.import_file(source)

    expected = source.read_bytes()
    assert record.id.startswith("att_")
    assert record.mime_type == "text/markdown"
    assert record.kind == "document"
    assert record.sha256 == hashlib.sha256(expected).hexdigest()
    assert record.extraction_status == "extracted"
    assert store.load_bytes(record) == expected
    assert store.load_extracted_text(record) == "# 周报\n结论"
    assert "# 周报" not in str(record.to_payload())
    assert str(source) not in str(record.to_payload())

    blocks = store.content_blocks(record)
    assert blocks[0].type == "text"
    assert blocks[0].metadata["attachment_id"] == record.id
    assert blocks[1].type == "attachment_ref"


def test_image_attachment_uses_reference_block_without_embedding_bytes(tmp_path):
    source = tmp_path / "chart.png"
    data = b"\x89PNG\r\n\x1a\n" + b"payload"
    source.write_bytes(data)
    store = AttachmentStore(tmp_path / "state")

    record = store.import_file(source)
    block = store.content_blocks(record)[0]

    assert record.mime_type == "image/png"
    assert record.extraction_status == "not_applicable"
    assert block.type == "image"
    assert block.attachment_id == record.id
    assert "payload" not in str(block.to_payload())


def test_pdf_can_store_pdfkit_extraction_as_separate_object(tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7\nplaceholder")
    store = AttachmentStore(tmp_path / "state")

    record = store.import_file(source, extracted_text="PDFKit 提取内容")
    round_tripped = AttachmentRecord.from_payload(record.to_payload())

    assert round_tripped == record
    assert record.extraction_status == "extracted"
    assert store.load_extracted_text(record) == "PDFKit 提取内容"
    assert record.text_sha256 != record.sha256


def test_store_rejects_symlink_binary_text_and_unsupported_types(tmp_path):
    store = AttachmentStore(tmp_path / "state")
    target = tmp_path / "target.txt"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(AttachmentError, match="symlink") as symlink_error:
        store.import_file(link)
    assert symlink_error.value.code == "path_invalid"

    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"hello\x00world")
    with pytest.raises(AttachmentError) as binary_error:
        store.import_file(binary)
    assert binary_error.value.code == "not_text"

    unsupported = tmp_path / "archive.zip"
    unsupported.write_bytes(b"PK\x03\x04")
    with pytest.raises(AttachmentError) as unsupported_error:
        store.import_file(unsupported)
    assert unsupported_error.value.code == "unsupported_type"


def test_turn_limits_and_hash_verification_fail_closed(tmp_path):
    store = AttachmentStore(tmp_path / "state")
    records = tuple(
        AttachmentRecord(
            id=f"att_{index}",
            filename=f"{index}.txt",
            mime_type="text/plain",
            kind="document",
            size_bytes=1,
            sha256=f"{index:064x}",
        )
        for index in range(5)
    )

    with pytest.raises(AttachmentError) as count_error:
        store.validate_turn(records)
    assert count_error.value.code == "too_many_attachments"

    large = (
        AttachmentRecord(
            id="att_large",
            filename="large.pdf",
            mime_type="application/pdf",
            kind="document",
            size_bytes=store.MAX_TURN_BYTES + 1,
            sha256="a" * 64,
        ),
    )
    with pytest.raises(AttachmentError) as size_error:
        store.validate_turn(large)
    assert size_error.value.code == "attachments_too_large"

    source = tmp_path / "safe.txt"
    source.write_text("safe", encoding="utf-8")
    record = store.import_file(source)
    store.object_path(record.sha256).write_bytes(b"tampered")
    with pytest.raises(AttachmentError) as corrupt_error:
        store.load_bytes(record)
    assert corrupt_error.value.code == "object_corrupt"


def test_store_rejects_internal_object_symlink_escape(tmp_path):
    state = tmp_path / "state"
    store = AttachmentStore(state)
    source = tmp_path / "safe.txt"
    source.write_text("symlink escape", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    prefix = store.objects_dir / digest[:2]
    outside = tmp_path / "outside"
    outside.mkdir()
    prefix.symlink_to(outside)

    with pytest.raises(AttachmentError) as error:
        store.import_file(source)

    assert error.value.code == "object_path_invalid"
    assert list(outside.iterdir()) == []
