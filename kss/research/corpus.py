"""分析师语料 JSONL 的安全读取与 analyst-corpus-v1 契约校验."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ANALYST_CORPUS_VERSION = "analyst-corpus-v1"
MAX_CORPUS_BYTES = 64 * 1024 * 1024
REQUIRED_FIELDS: tuple[str, ...] = (
    "protocol_version",
    "source_message_id",
    "analyst_id",
    "published_at",
    "source_uri",
    "content_hash",
    "provenance",
)


class AnalystCorpusError(ValueError):
    """分析师语料不满足安全或数据契约时抛出."""


@dataclass(frozen=True)
class AnalystAttachment:
    """语料附件引用."""

    attachment_id: str
    media_type: str
    content_hash: str
    source_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalystMessage:
    """单条 analyst-corpus-v1 语料消息.

    Args:
        source_message_id: 外部消息稳定唯一 ID。
        analyst_id: 分析师、机构号或上游作者稳定 ID。
        published_at: 发布时间,由上游保持 ISO-like 字符串。
        source_uri: 原始消息或内容对象 URI。
        content_hash: ``content`` 或 ``object_ref`` 的 SHA-256 摘要。
        provenance: 来源说明 JSON object。
        content: 内联正文。与 ``object_ref`` 二选一。
        object_ref: 内容寻址对象引用。与 ``content`` 二选一。
        attachments: 可选附件。
        metadata: 兼容保留字段,不得替代正式 schema 字段。
    """

    source_message_id: str
    analyst_id: str
    published_at: str
    source_uri: str
    content_hash: str
    provenance: dict[str, Any]
    content: str | None = None
    object_ref: str | None = None
    attachments: list[AnalystAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_relation(self) -> str:
        """兼容旧调用的来源关系读取,正式字段位于 provenance."""
        value = self.provenance.get("source_relation", "direct")
        return value if isinstance(value, str) and value else "direct"

    @property
    def author(self) -> str:
        """兼容旧调用的作者读取,正式字段为 analyst_id."""
        value = self.metadata.get("author")
        return value if isinstance(value, str) and value else self.analyst_id

    @property
    def broker(self) -> str:
        """兼容旧调用的机构读取,正式字段可放入 provenance.broker."""
        value = self.provenance.get("broker")
        if isinstance(value, str) and value:
            return value
        legacy = self.metadata.get("broker")
        return legacy if isinstance(legacy, str) and legacy else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def content_sha256(content: str) -> str:
    """返回正文 UTF-8 字节的 SHA-256 十六进制摘要."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def canonical_sha256(payload: Any) -> str:
    """返回 JSON 可序列化对象的稳定 SHA-256 摘要."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_content_hash(value: Any) -> str:
    """标准化 ``content_hash`` 字段,支持裸 hex 与 ``sha256:`` 前缀."""
    if not isinstance(value, str):
        raise AnalystCorpusError("content_hash 必须是字符串")
    digest = value.removeprefix("sha256:").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise AnalystCorpusError("content_hash 必须是 SHA-256 十六进制摘要")
    return digest


def load_analyst_corpus(path: str | Path) -> list[AnalystMessage]:
    """安全读取并校验 analyst-corpus-v1 JSONL 文件."""
    raw = _read_regular_utf8_file(Path(path))
    records: list[AnalystMessage] = []
    seen_source_ids: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalystCorpusError(f"第 {line_number} 行不是合法 JSON") from exc
        record = parse_analyst_message(payload, line_number=line_number)
        if record.source_message_id in seen_source_ids:
            raise AnalystCorpusError(f"重复 source_message_id: {record.source_message_id}")
        seen_source_ids.add(record.source_message_id)
        records.append(record)
    return records


def parse_analyst_message(payload: Any, *, line_number: int | None = None) -> AnalystMessage:
    """校验单条 analyst-corpus-v1 记录."""
    prefix = f"第 {line_number} 行" if line_number is not None else "语料记录"
    if not isinstance(payload, dict):
        raise AnalystCorpusError(f"{prefix} 必须是 JSON object")
    missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in payload]
    if missing:
        raise AnalystCorpusError(f"{prefix} 缺少必填字段: {', '.join(missing)}")
    if payload["protocol_version"] != ANALYST_CORPUS_VERSION:
        raise AnalystCorpusError(f"{prefix} protocol_version 必须是 {ANALYST_CORPUS_VERSION}")

    values = _required_text_fields(payload, REQUIRED_FIELDS, prefix)
    provenance = payload["provenance"]
    if not isinstance(provenance, dict):
        raise AnalystCorpusError(f"{prefix} provenance 必须是 JSON object")

    content = payload.get("content")
    object_ref = payload.get("object_ref")
    has_content = isinstance(content, str) and bool(content)
    has_object_ref = isinstance(object_ref, str) and bool(object_ref)
    if has_content == has_object_ref:
        raise AnalystCorpusError(f"{prefix} content 与 object_ref 必须二选一")

    content_hash = normalize_content_hash(values["content_hash"])
    if has_content and content_sha256(content) != content_hash:
        raise AnalystCorpusError(f"{prefix} content_hash 与 content 不匹配")
    if has_object_ref and normalize_content_hash(object_ref) != content_hash:
        raise AnalystCorpusError(f"{prefix} object_ref 必须是同一 content_hash")

    attachments = _parse_attachments(payload.get("attachments", []), prefix)
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise AnalystCorpusError(f"{prefix} metadata 必须是 JSON object")

    return AnalystMessage(
        source_message_id=values["source_message_id"],
        analyst_id=values["analyst_id"],
        published_at=values["published_at"],
        source_uri=values["source_uri"],
        content_hash=content_hash,
        provenance=provenance,
        content=content if has_content else None,
        object_ref=object_ref if has_object_ref else None,
        attachments=attachments,
        metadata=metadata,
    )


def _required_text_fields(
    payload: dict[str, Any],
    field_names: tuple[str, ...],
    prefix: str,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for field_name in field_names:
        if field_name in {"protocol_version", "provenance"}:
            continue
        value = payload[field_name]
        if not isinstance(value, str) or not value:
            raise AnalystCorpusError(f"{prefix} {field_name} 必须是非空字符串")
        values[field_name] = value
    return values


def _parse_attachments(value: Any, prefix: str) -> list[AnalystAttachment]:
    if not isinstance(value, list):
        raise AnalystCorpusError(f"{prefix} attachments 必须是数组")
    attachments: list[AnalystAttachment] = []
    for index, item in enumerate(value):
        item_prefix = f"{prefix} attachments[{index}]"
        if not isinstance(item, dict):
            raise AnalystCorpusError(f"{item_prefix} 必须是 JSON object")
        attachment_id = item.get("attachment_id")
        media_type = item.get("media_type")
        if not isinstance(attachment_id, str) or not attachment_id:
            raise AnalystCorpusError(f"{item_prefix}.attachment_id 必须是非空字符串")
        if not isinstance(media_type, str) or not media_type:
            raise AnalystCorpusError(f"{item_prefix}.media_type 必须是非空字符串")
        content_hash = normalize_content_hash(item.get("content_hash"))
        source_uri = item.get("source_uri")
        if source_uri is not None and (not isinstance(source_uri, str) or not source_uri):
            raise AnalystCorpusError(f"{item_prefix}.source_uri 必须是非空字符串")
        attachments.append(
            AnalystAttachment(
                attachment_id=attachment_id,
                media_type=media_type,
                content_hash=content_hash,
                source_uri=source_uri,
            )
        )
    return attachments


def _read_regular_utf8_file(path: Path) -> str:
    path = path.expanduser()
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise AnalystCorpusError(f"语料文件不存在: {path}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise AnalystCorpusError("拒绝读取符号链接语料文件")
    if not stat.S_ISREG(file_stat.st_mode):
        raise AnalystCorpusError("语料路径必须是普通文件")
    if file_stat.st_size > MAX_CORPUS_BYTES:
        raise AnalystCorpusError("语料文件超过 64MB 上限")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AnalystCorpusError(f"无法打开语料文件: {path}") from exc
    try:
        opened_stat = os.fstat(fd)
        if (
            opened_stat.st_dev != file_stat.st_dev
            or opened_stat.st_ino != file_stat.st_ino
            or opened_stat.st_size != file_stat.st_size
        ):
            raise AnalystCorpusError("语料文件在打开前后发生变化")
        chunks: list[bytes] = []
        remaining = MAX_CORPUS_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        final_stat = os.fstat(fd)
        if (
            final_stat.st_size != opened_stat.st_size
            or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
        ):
            raise AnalystCorpusError("语料文件在读取过程中发生变化")
    finally:
        os.close(fd)
    if len(data) > MAX_CORPUS_BYTES:
        raise AnalystCorpusError("语料文件超过 64MB 上限")
    if b"\x00" in data:
        raise AnalystCorpusError("语料文件包含 NUL 字节")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnalystCorpusError("语料文件必须是 UTF-8 编码") from exc
