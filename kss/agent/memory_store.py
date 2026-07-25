"""Agent Core 记忆存储."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kss.agent.jsonl import append_jsonl, read_jsonl_repair_tail, utc_timestamp
from kss.agent.types import MemoryKind, MemoryStatus
from kss.memory.rank import rank
from kss.memory.types import Candidate


@dataclass(frozen=True)
class MemoryRecord:
    """记忆记录."""

    id: str
    kind: MemoryKind
    content: str
    source_session: str | None
    source_entry: str | None
    tags: tuple[str, ...]
    status: MemoryStatus
    created_at: float
    updated_at: float
    expires_at: float | None = None
    metadata: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        """兼容旧调用的文本别名."""
        return self.content


class MemoryStore:
    """append-only 记忆库.

    记忆先 propose 再 approve；归档和删除均写入状态事件。
    """

    _SECRET_PATTERNS = (
        re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
        re.compile(r"\b(api[_-]?key|token|secret|password)\s*[:=]", re.IGNORECASE),
    )
    _LIVE_MARKET_PATTERNS = (
        re.compile(r"(今日|当前|实时|latest|live).{0,12}(涨跌幅|价格|净流入|成交额|rank|排名)", re.IGNORECASE),
        re.compile(r"\b\d+(?:\.\d+)?\s*(?:元|%|万|亿)\b.*(今日|当前|实时)"),
    )

    def __init__(self, state_root: str | Path) -> None:
        """初始化.

        Args:
            state_root: 状态根目录；记忆文件位于 ``storage/agent/memories.jsonl``。
        """
        self.path = Path(state_root) / "storage" / "agent" / "memories.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def propose(
        self,
        kind: MemoryKind,
        text: str,
        *,
        source_session: str | None = None,
        source_entry: str | None = None,
        tags: tuple[str, ...] = (),
        expires_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        """提议一条记忆."""
        self._validate(kind, text)
        now = utc_timestamp()
        if expires_at is None:
            expires_at = self._default_expiry(kind, now)
        meta = dict(metadata or {})
        if kind == "thesis":
            meta.setdefault("review_required", True)
        record = MemoryRecord(
            id=uuid.uuid4().hex,
            kind=kind,
            content=text.strip(),
            source_session=source_session,
            source_entry=source_entry,
            tags=tuple(tags),
            status="proposed",
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            metadata=meta,
        )
        self._append("proposed", record)
        return record

    def approve(self, memory_id: str) -> MemoryRecord:
        """批准记忆."""
        return self._set_status(memory_id, "approved")

    def archive(self, memory_id: str) -> MemoryRecord:
        """归档记忆."""
        return self._set_status(memory_id, "archived")

    def delete(self, memory_id: str) -> MemoryRecord:
        """逻辑删除记忆."""
        return self._set_status(memory_id, "deleted")

    def search(
        self,
        query: str,
        *,
        include_status: tuple[MemoryStatus, ...] = ("approved",),
        now: float | None = None,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """按文本搜索记忆."""
        now = now or utc_timestamp()
        records = [
            record
            for record in self._records().values()
            if record.status in include_status and not self._expired(record, now)
        ]
        if not query.strip():
            return sorted(records, key=lambda item: (item.updated_at, item.id), reverse=True)[:limit]
        query_lower = query.lower()
        matched = [record for record in records if query_lower in record.text.lower()]
        return sorted(matched, key=lambda item: (item.updated_at, item.id), reverse=True)[:limit]

    def recall(self, query: str, *, now_ms: int, limit: int = 5) -> list[str]:
        """召回适合注入的短记忆.

        Returns:
            最多五条、每条不超过 250 字符的记忆文本。
        """
        capped_limit = min(5, max(0, limit))
        records = [
            record
            for record in self._records().values()
            if record.status == "approved" and not self._expired(record, now_ms / 1000)
        ]
        candidates = [
            Candidate(
                id=record.id,
                text=record.text,
                timestamp_ms=int(record.updated_at * 1000),
                base_score=1.0,
            )
            for record in records
        ]
        ranked = rank(candidates, query=query, now_ms=now_ms, top_k=capped_limit)
        by_id = {record.id: record for record in records}
        return [self._cap(by_id[item.id].text, 250) for item in ranked]

    def _records(self) -> dict[str, MemoryRecord]:
        records: dict[str, MemoryRecord] = {}
        for entry in read_jsonl_repair_tail(self.path):
            payload = entry.get("payload")
            if isinstance(payload, dict):
                records[payload["id"]] = MemoryRecord(
                    id=payload["id"],
                    kind=payload["kind"],
                    content=payload.get("content", payload.get("text", "")),
                    source_session=payload.get("source_session"),
                    source_entry=payload.get("source_entry"),
                    tags=tuple(payload.get("tags", ())),
                    status=payload["status"],
                    created_at=payload["created_at"],
                    updated_at=payload["updated_at"],
                    expires_at=payload.get("expires_at"),
                    metadata=payload.get("metadata", {}),
                )
        return records

    def _set_status(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        records = self._records()
        if memory_id not in records:
            raise KeyError(f"记忆不存在: {memory_id}")
        old = records[memory_id]
        record = MemoryRecord(
            id=old.id,
            kind=old.kind,
            content=old.content,
            source_session=old.source_session,
            source_entry=old.source_entry,
            tags=old.tags,
            status=status,
            created_at=old.created_at,
            updated_at=utc_timestamp(),
            expires_at=old.expires_at,
            metadata=old.metadata or {},
        )
        self._append(f"status_{status}", record)
        return record

    def _append(self, event_type: str, record: MemoryRecord) -> None:
        append_jsonl(
            self.path,
            {
                "version": 1,
                "id": uuid.uuid4().hex,
                "parent_id": record.id,
                "timestamp": utc_timestamp(),
                "type": event_type,
                "payload": {
                    "id": record.id,
                    "kind": record.kind,
                    "content": record.content,
                    "text": record.content,
                    "source_session": record.source_session,
                    "source_entry": record.source_entry,
                    "tags": list(record.tags),
                    "status": record.status,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "expires_at": record.expires_at,
                    "metadata": record.metadata or {},
                },
            },
        )

    def _validate(self, kind: MemoryKind, text: str) -> None:
        if kind not in ("preference", "decision", "thesis"):
            raise ValueError("记忆类型不合法")
        clean = text.strip()
        if not clean:
            raise ValueError("记忆不能为空")
        if any(pattern.search(clean) for pattern in self._SECRET_PATTERNS):
            raise ValueError("记忆疑似包含密钥或凭证")
        if any(pattern.search(clean) for pattern in self._LIVE_MARKET_PATTERNS):
            raise ValueError("记忆疑似包含实时行情数字")

    def _expired(self, record: MemoryRecord, now: float) -> bool:
        return record.expires_at is not None and record.expires_at <= now

    def _cap(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _default_expiry(self, kind: MemoryKind, now: float) -> float | None:
        if kind == "preference":
            return None
        if kind == "decision":
            return now + 180 * 24 * 60 * 60
        if kind == "thesis":
            return now + 30 * 24 * 60 * 60
        return None
