"""Content-addressed, local-only chat attachment storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from kss.agent.types import AgentContentBlock

AttachmentKind = Literal["image", "document"]


class AttachmentError(ValueError):
    """Expected, user-facing attachment validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AttachmentRecord:
    """Durable attachment metadata.

    Absolute source paths, raw bytes, extracted text, and base64 are
    intentionally absent from this record so it is safe to persist in session
    JSONL and return during hydration.
    """

    id: str
    filename: str
    mime_type: str
    kind: AttachmentKind
    size_bytes: int
    sha256: str
    extraction_status: str = "not_applicable"
    extracted_chars: int = 0
    text_sha256: str | None = None
    provenance: str = "user_file_import"

    def to_payload(self) -> dict[str, Any]:
        """Return JSON-safe metadata without local paths or content."""
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AttachmentRecord:
        """Decode a persisted attachment record."""
        return cls(
            id=str(payload["id"]),
            filename=str(payload["filename"]),
            mime_type=str(payload["mime_type"]),
            kind=str(payload["kind"]),  # type: ignore[arg-type]
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
            extraction_status=str(
                payload.get("extraction_status") or "not_applicable"
            ),
            extracted_chars=int(payload.get("extracted_chars") or 0),
            text_sha256=(
                str(payload["text_sha256"])
                if payload.get("text_sha256") is not None
                else None
            ),
            provenance=str(payload.get("provenance") or "user_file_import"),
        )


class AttachmentStore:
    """Store explicitly selected files under the Agent state root.

    The store does not enumerate arbitrary directories and refuses symlinks.
    Objects are immutable and keyed by SHA-256. Removing an attachment from a
    draft is therefore a UI/session operation; shared objects are not eagerly
    deleted.
    """

    MAX_FILE_BYTES = 10 * 1024 * 1024
    MAX_TURN_BYTES = 20 * 1024 * 1024
    MAX_ATTACHMENTS_PER_TURN = 4
    MAX_EXTRACTED_CHARS = 64 * 1024
    _SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
    _TEXT_MIME_BY_SUFFIX = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".csv": "text/csv",
    }

    def __init__(self, state_root: str | Path) -> None:
        self.state_root = Path(state_root)
        self.objects_dir = (
            self.state_root
            / "storage"
            / "agent"
            / "attachments"
            / "objects"
            / "sha256"
        )
        self.staging_dir = self.objects_dir.parent / ".staging"
        self.records_dir = self.objects_dir.parent / "records"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self._assert_internal_path(self.objects_dir)
        self._assert_internal_path(self.staging_dir)
        self._assert_internal_path(self.records_dir)

    def import_file(
        self,
        source: str | Path,
        *,
        extracted_text: str | None = None,
        provenance: str = "user_file_import",
    ) -> AttachmentRecord:
        """Validate and atomically import one explicitly selected file.

        PDF text is expected to be extracted by the macOS client using PDFKit
        and may be supplied through ``extracted_text``. UTF-8 text formats are
        extracted deterministically here.
        """
        path = Path(source)
        data = self._read_selected_file(path)
        mime_type, kind = self._detect_type(path, data)
        digest = hashlib.sha256(data).hexdigest()
        self._store_object(digest, data)

        extraction_status = "not_applicable"
        text_sha256: str | None = None
        extracted_chars = 0
        if mime_type.startswith("text/"):
            text = self._decode_text(data)
            extraction_status = "extracted"
        elif mime_type == "application/pdf" and extracted_text is not None:
            text = self._validate_extracted_text(extracted_text)
            extraction_status = "extracted"
        elif mime_type == "application/pdf":
            text = None
            extraction_status = "pending"
        else:
            text = None

        if text is not None:
            text_bytes = text.encode("utf-8")
            text_sha256 = hashlib.sha256(text_bytes).hexdigest()
            self._store_object(text_sha256, text_bytes)
            extracted_chars = len(text)

        record = AttachmentRecord(
            id=f"att_{digest[:24]}",
            filename=self._safe_filename(path.name),
            mime_type=mime_type,
            kind=kind,
            size_bytes=len(data),
            sha256=digest,
            extraction_status=extraction_status,
            extracted_chars=extracted_chars,
            text_sha256=text_sha256,
            provenance=provenance,
        )
        self.save_record(record)
        return record

    def save_record(self, record: AttachmentRecord) -> AttachmentRecord:
        """Atomically persist safe attachment metadata, never source paths."""

        destination = self._record_path(record.id)
        encoded = json.dumps(
            record.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fd, temporary = tempfile.mkstemp(
            prefix=".attachment-record-",
            dir=str(self.records_dir),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            directory_fd = os.open(self.records_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return record

    def load_record(self, attachment_id: str) -> AttachmentRecord:
        """Load metadata for a previously imported attachment."""

        path = self._record_path(attachment_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise AttachmentError(
                "attachment_not_found",
                "attachment metadata does not exist",
            ) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AttachmentError(
                "attachment_metadata_invalid",
                "attachment metadata is invalid",
            ) from error
        if not isinstance(payload, dict):
            raise AttachmentError(
                "attachment_metadata_invalid",
                "attachment metadata must be an object",
            )
        try:
            record = AttachmentRecord.from_payload(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise AttachmentError(
                "attachment_metadata_invalid",
                "attachment metadata is invalid",
            ) from error
        if record.id != attachment_id:
            raise AttachmentError(
                "attachment_metadata_invalid",
                "attachment metadata id mismatch",
            )
        return record

    def list_records(self) -> tuple[AttachmentRecord, ...]:
        """List safe metadata records without reading attachment payloads."""

        records: list[AttachmentRecord] = []
        for path in sorted(self.records_dir.glob("att_*.json")):
            try:
                records.append(self.load_record(path.stem))
            except AttachmentError:
                continue
        return tuple(records)

    def remove_record(self, attachment_id: str) -> bool:
        """Remove one metadata reference while retaining shared CAS objects."""

        path = self._record_path(attachment_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def validate_turn(
        self,
        records: Iterable[AttachmentRecord],
    ) -> tuple[AttachmentRecord, ...]:
        """Enforce per-turn count and aggregate byte limits."""
        values = tuple(records)
        if len(values) > self.MAX_ATTACHMENTS_PER_TURN:
            raise AttachmentError(
                "too_many_attachments",
                f"each turn supports at most {self.MAX_ATTACHMENTS_PER_TURN} attachments",
            )
        total = sum(record.size_bytes for record in values)
        if total > self.MAX_TURN_BYTES:
            raise AttachmentError(
                "attachments_too_large",
                f"attachments total exceeds {self.MAX_TURN_BYTES} bytes",
            )
        return values

    def load_bytes(self, record: AttachmentRecord) -> bytes:
        """Read and re-verify an immutable attachment object."""
        return self._load_object(record.sha256, expected_size=record.size_bytes)

    def load_extracted_text(self, record: AttachmentRecord) -> str | None:
        """Read extracted UTF-8 text without placing it in session JSONL."""
        if record.text_sha256 is None:
            return None
        return self._load_object(record.text_sha256).decode("utf-8")

    def content_blocks(
        self,
        record: AttachmentRecord,
        *,
        content_index: int | None = None,
        include_extracted_text: bool = True,
    ) -> tuple[AgentContentBlock, ...]:
        """Build provider-neutral blocks for an imported attachment."""
        blocks: list[AgentContentBlock] = []
        if include_extracted_text:
            text = self.load_extracted_text(record)
            if text:
                blocks.append(
                    AgentContentBlock(
                        type="text",
                        text=(
                            f"\n\n[附件 {record.filename} 提取文本]\n{text}\n"
                            "[附件文本结束]\n"
                        ),
                        content_index=content_index,
                        metadata={
                            "attachment_id": record.id,
                            "provenance": "attachment_extraction",
                        },
                    )
                )
        blocks.append(
            AgentContentBlock(
                type="image" if record.kind == "image" else "attachment_ref",
                attachment_id=record.id,
                mime_type=record.mime_type,
                content_index=content_index,
                metadata={
                    "filename": record.filename,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "provenance": record.provenance,
                },
            )
        )
        return tuple(blocks)

    def object_path(self, digest: str) -> Path:
        """Resolve a validated digest inside the content-addressed root."""
        if not self._SHA256_RE.fullmatch(digest):
            raise AttachmentError("invalid_object_id", "invalid SHA-256 object id")
        return self.objects_dir / digest[:2] / digest

    def _record_path(self, attachment_id: str) -> Path:
        if not re.fullmatch(r"att_[0-9a-f]{24}", attachment_id):
            raise AttachmentError("attachment_id_invalid", "invalid attachment id")
        path = self.records_dir / f"{attachment_id}.json"
        self._assert_internal_path(path)
        return path

    def _read_selected_file(self, path: Path) -> bytes:
        try:
            info = path.lstat()
        except FileNotFoundError as error:
            raise AttachmentError("not_found", "attachment does not exist") from error
        if path.is_symlink():
            raise AttachmentError("path_invalid", "symlink attachments are not allowed")
        if not path.is_file():
            raise AttachmentError("path_invalid", "attachment must be a regular file")
        if info.st_size > self.MAX_FILE_BYTES:
            raise AttachmentError(
                "file_too_large",
                f"attachment exceeds {self.MAX_FILE_BYTES} bytes",
            )

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except OSError as error:
            raise AttachmentError("path_invalid", "attachment could not be opened") from error
        try:
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != info.st_dev
                or opened.st_ino != info.st_ino
                or opened.st_size != info.st_size
            ):
                raise AttachmentError("file_changed", "attachment changed during import")
            chunks: list[bytes] = []
            remaining = self.MAX_FILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            final = os.fstat(fd)
        finally:
            os.close(fd)
        data = b"".join(chunks)
        if len(data) > self.MAX_FILE_BYTES:
            raise AttachmentError(
                "file_too_large",
                f"attachment exceeds {self.MAX_FILE_BYTES} bytes",
            )
        if (
            final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or final.st_size != opened.st_size
            or len(data) != final.st_size
        ):
            raise AttachmentError("file_changed", "attachment changed during import")
        return data

    def _detect_type(
        self,
        path: Path,
        data: bytes,
    ) -> tuple[str, AttachmentKind]:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "image"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "image"
        if (
            len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        ):
            return "image/webp", "image"
        if data.startswith(b"%PDF-"):
            return "application/pdf", "document"
        mime_type = self._TEXT_MIME_BY_SUFFIX.get(path.suffix.lower())
        if mime_type is not None:
            self._decode_text(data)
            return mime_type, "document"
        raise AttachmentError(
            "unsupported_type",
            "supported attachment types are PNG, JPEG, WebP, PDF, TXT, Markdown, and CSV",
        )

    def _decode_text(self, data: bytes) -> str:
        if b"\x00" in data:
            raise AttachmentError("not_text", "text attachment contains NUL bytes")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AttachmentError(
                "not_text", "text attachment must be valid UTF-8"
            ) from error
        return self._validate_extracted_text(text)

    def _validate_extracted_text(self, text: str) -> str:
        if "\x00" in text:
            raise AttachmentError("not_text", "extracted text contains NUL bytes")
        if len(text) > self.MAX_EXTRACTED_CHARS:
            return text[: self.MAX_EXTRACTED_CHARS]
        return text

    def _store_object(self, digest: str, data: bytes) -> None:
        target = self.object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._assert_internal_path(target.parent)
        if target.is_symlink():
            raise AttachmentError(
                "object_path_invalid", "attachment object path must not be a symlink"
            )
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise AttachmentError(
                    "object_corrupt", "existing attachment object failed hash verification"
                )
            return
        fd, staging_name = tempfile.mkstemp(dir=self.staging_dir)
        staging = Path(staging_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, target)
            self._fsync_directory(target.parent)
        finally:
            staging.unlink(missing_ok=True)

    def _load_object(
        self,
        digest: str,
        *,
        expected_size: int | None = None,
    ) -> bytes:
        path = self.object_path(digest)
        self._assert_internal_path(path.parent)
        if path.is_symlink():
            raise AttachmentError(
                "object_path_invalid", "attachment object path must not be a symlink"
            )
        try:
            data = path.read_bytes()
        except FileNotFoundError as error:
            raise AttachmentError("object_missing", "attachment object is missing") from error
        if expected_size is not None and len(data) != expected_size:
            raise AttachmentError("object_corrupt", "attachment size verification failed")
        if hashlib.sha256(data).hexdigest() != digest:
            raise AttachmentError("object_corrupt", "attachment hash verification failed")
        return data

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename).name.replace("\x00", "").strip()
        return name[:255] or "attachment"

    def _assert_internal_path(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.state_root.resolve())
        except ValueError as error:
            raise AttachmentError(
                "object_path_invalid",
                "attachment storage path escapes the state root",
            ) from error

    def _fsync_directory(self, directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
