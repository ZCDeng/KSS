"""Agent Core 会话 JSONL 存储."""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from kss.agent.jsonl import read_jsonl_repair_tail, update_jsonl_locked, utc_timestamp
from kss.agent.types import (
    AgentMessage,
    AgentState,
    QueuedAgentInput,
    QueuedInputMode,
    SessionStatus,
    ToolCall,
    message_content_blocks_from_payload,
)


@dataclass(frozen=True)
class RunAdmission:
    """原子 run 准入结果."""

    admitted: bool
    status: str
    run_id: str
    client_turn_id: str | None = None


class RunAdmissionError(RuntimeError):
    """持久层拒绝 run 准入."""

    def __init__(self, admission: RunAdmission) -> None:
        super().__init__(
            f"run admission rejected: status={admission.status} "
            f"run_id={admission.run_id}"
        )
        self.admission = admission
        self.status = admission.status
        self.existing_run_id = admission.run_id
        self.client_turn_id = admission.client_turn_id


class QueuedInputLimitError(RuntimeError):
    """单个 run 的累计队列输入超过安全上限."""


class SessionStore:
    """append-only JSONL 会话存储.

    每个会话一个 JSONL 文件，记录均为 v1，并包含
    ``id``、``parent_id``、``timestamp``。
    逻辑删除、完成和中断通过追加状态事件表达，不重写历史记录。
    """

    MAX_QUEUED_INPUTS_PER_RUN = 8

    def __init__(self, state_root: str | Path) -> None:
        """初始化.

        Args:
            state_root: 状态根目录；会话存储位于 ``storage/agent/sessions``。
        """
        self.state_root = Path(state_root)
        self.sessions_dir = self.state_root / "storage" / "agent" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create_session(
        self,
        *,
        session_id: str | None = None,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentState:
        """创建会话并写入初始状态."""
        sid = session_id or uuid.uuid4().hex
        meta = dict(metadata or {})
        meta.setdefault("title", sid)
        meta["updated_at"] = utc_timestamp()
        state = AgentState(session_id=sid, metadata=meta)
        self._append(sid, event_type="session_created", parent_id=parent_id, payload=asdict(state))
        return state

    def get_session(self, session_id: str) -> AgentState | None:
        """读取会话当前状态；仅未完成 run 会恢复为 interrupted."""
        entries = self._read_entries(session_id)
        if not entries:
            return None
        state = self._state_from_entries(entries)
        if state is not None and self._unfinished_run(entries) is not None:
            return self._recover_incomplete_run_once(session_id) or state
        return state

    def open_session(self, session_id: str) -> AgentState | None:
        """打开会话；等价于 get_session."""
        return self.get_session(session_id)

    def list_sessions(self, *, include_deleted: bool = False) -> list[AgentState]:
        """列出会话状态."""
        states: list[AgentState] = []
        for path in sorted(self.sessions_dir.glob("*.jsonl")):
            state = self.get_session(path.stem)
            if state is None:
                continue
            if state.status == "deleted" and not include_deleted:
                continue
            states.append(state)
        return states

    def append_message(
        self,
        session_id: str,
        message: AgentMessage,
        *,
        source_queue_id: str | None = None,
    ) -> None:
        """向会话追加消息；可原子消费一条 restored queue."""
        if source_queue_id is None:
            self._append(
                session_id,
                event_type="message_appended",
                payload=asdict(message),
            )
            return

        def append_and_consume(
            entries: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            current = self._queue_records(entries).get(source_queue_id)
            if current is None or current.session_id != session_id:
                raise KeyError(f"source queued input 不存在: {source_queue_id}")
            if current.status != "restored":
                raise ValueError("source queued input 必须处于 restored 状态")
            discarded = QueuedAgentInput(
                id=current.id,
                client_message_id=current.client_message_id,
                session_id=current.session_id,
                run_id=current.run_id,
                mode=current.mode,
                content=current.content,
                status="discarded",
                created_at=current.created_at,
                applied_at=None,
            )
            return self._build_entries(
                session_id,
                entries,
                [
                    ("message_appended", asdict(message)),
                    (
                        "queue_discarded",
                        {
                            **asdict(discarded),
                            "consumed_by_message_id": message.id,
                        },
                    ),
                ],
            )

        update_jsonl_locked(self._path(session_id), append_and_consume)

    def add_queued_input(
        self,
        session_id: str,
        run_id: str,
        mode: QueuedInputMode,
        client_message_id: str,
        content: str,
        *,
        source_queue_id: str | None = None,
        queue_id: str | None = None,
    ) -> QueuedAgentInput:
        """幂等追加 steering / follow-up；每个 run 累计最多八条."""
        if mode not in {"steering", "follow_up"}:
            raise ValueError(f"不支持的 queue mode: {mode}")
        if not client_message_id.strip():
            raise ValueError("client_message_id 不能为空")
        if not content.strip():
            raise ValueError("queued input 不能为空")
        result: QueuedAgentInput | None = None

        def add(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal result
            records = self._queue_records(entries)
            duplicate = next(
                (
                    item
                    for item in records.values()
                    if item.run_id == run_id
                    and item.client_message_id == client_message_id
                ),
                None,
            )
            if duplicate is not None:
                result = duplicate
                return []
            if source_queue_id is not None:
                source = records.get(source_queue_id)
                if source is None or source.session_id != session_id:
                    raise KeyError(
                        f"source queued input 不存在: {source_queue_id}"
                    )
                if source.status != "restored":
                    raise ValueError(
                        "source queued input 必须处于 restored 状态"
                    )
            cumulative = sum(
                1 for item in records.values() if item.run_id == run_id
            )
            if cumulative >= self.MAX_QUEUED_INPUTS_PER_RUN:
                raise QueuedInputLimitError(
                    f"run {run_id!r} queued input limit "
                    f"{self.MAX_QUEUED_INPUTS_PER_RUN} reached"
                )
            result = QueuedAgentInput(
                id=queue_id or uuid.uuid4().hex,
                client_message_id=client_message_id,
                session_id=session_id,
                run_id=run_id,
                mode=mode,
                content=content,
                status="queued",
                created_at=utc_timestamp(),
            )
            payload = asdict(result)
            if source_queue_id is not None:
                payload["source_queue_id"] = source_queue_id
            return self._build_entries(
                session_id, entries, [("queue_added", payload)]
            )

        update_jsonl_locked(self._path(session_id), add)
        assert result is not None
        return result

    def queued_inputs(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        include_terminal: bool = False,
    ) -> list[QueuedAgentInput]:
        """列出队列状态；默认只返回 queued/restored 项."""
        if session_id is None and run_id is None:
            sessions = [path.stem for path in sorted(self.sessions_dir.glob("*.jsonl"))]
        elif session_id is not None:
            sessions = [session_id]
        else:
            sessions = [path.stem for path in sorted(self.sessions_dir.glob("*.jsonl"))]
        items: list[QueuedAgentInput] = []
        for sid in sessions:
            for item in self._queue_records(self._read_entries(sid)).values():
                if run_id is not None and item.run_id != run_id:
                    continue
                if not include_terminal and item.status not in {"queued", "restored"}:
                    continue
                items.append(item)
        return sorted(items, key=lambda item: (item.created_at, item.id))

    def apply_queued_input(
        self,
        session_id: str,
        queue_id: str,
        *,
        target_run_id: str | None = None,
    ) -> QueuedAgentInput:
        """同一文件锁内追加 user message 和 ``queue_applied``."""
        result: QueuedAgentInput | None = None

        def apply(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal result
            records = self._queue_records(entries)
            current = records.get(queue_id)
            if current is None or current.session_id != session_id:
                raise KeyError(f"queued input 不存在: {queue_id}")
            if current.status == "applied":
                result = current
                return []
            if current.status == "discarded":
                raise ValueError("discarded queued input 不能应用")
            applied_at = utc_timestamp()
            result = QueuedAgentInput(
                id=current.id,
                client_message_id=current.client_message_id,
                session_id=current.session_id,
                run_id=current.run_id,
                mode=current.mode,
                content=current.content,
                status="applied",
                created_at=current.created_at,
                applied_at=applied_at,
            )
            message = AgentMessage(
                id=current.client_message_id,
                role="user",
                content=current.content,
                timestamp=applied_at,
                metadata={
                    "run_id": target_run_id or current.run_id,
                    "source_run_id": current.run_id,
                    "client_message_id": current.client_message_id,
                    "queued_input_id": current.id,
                    "queue_mode": current.mode,
                },
            )
            additions: list[tuple[str, dict[str, Any]]] = [
                ("message_appended", asdict(message)),
                (
                    "queue_applied",
                    {
                        **asdict(result),
                        "message_id": message.id,
                    },
                ),
            ]
            source_queue_id = self._queue_source_ids(entries).get(current.id)
            if source_queue_id is not None:
                source = records.get(source_queue_id)
                if source is None or source.session_id != session_id:
                    raise KeyError(
                        f"source queued input 不存在: {source_queue_id}"
                    )
                if source.status != "restored":
                    raise ValueError(
                        "source queued input 必须处于 restored 状态"
                    )
                discarded_source = QueuedAgentInput(
                    id=source.id,
                    client_message_id=source.client_message_id,
                    session_id=source.session_id,
                    run_id=source.run_id,
                    mode=source.mode,
                    content=source.content,
                    status="discarded",
                    created_at=source.created_at,
                    applied_at=None,
                )
                additions.append(
                    (
                        "queue_discarded",
                        {
                            **asdict(discarded_source),
                            "consumed_by_queue_id": current.id,
                        },
                    )
                )
            return self._build_entries(
                session_id,
                entries,
                additions,
            )

        update_jsonl_locked(self._path(session_id), apply)
        assert result is not None
        return result

    def discard_queued_input(
        self, session_id: str, queue_id: str
    ) -> QueuedAgentInput:
        """幂等丢弃尚未应用的队列输入."""
        result: QueuedAgentInput | None = None

        def discard(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal result
            current = self._queue_records(entries).get(queue_id)
            if current is None or current.session_id != session_id:
                raise KeyError(f"queued input 不存在: {queue_id}")
            if current.status == "discarded":
                result = current
                return []
            if current.status == "applied":
                raise ValueError("applied queued input 不能丢弃")
            result = QueuedAgentInput(
                id=current.id,
                client_message_id=current.client_message_id,
                session_id=current.session_id,
                run_id=current.run_id,
                mode=current.mode,
                content=current.content,
                status="discarded",
                created_at=current.created_at,
                applied_at=None,
            )
            return self._build_entries(
                session_id,
                entries,
                [("queue_discarded", asdict(result))],
            )

        update_jsonl_locked(self._path(session_id), discard)
        assert result is not None
        return result

    def restore_queued_inputs(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> list[QueuedAgentInput]:
        """把 interrupted run 的 pending queue 一次性标为 restored."""
        restored: list[QueuedAgentInput] = []

        def restore(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal restored
            run_records = self._run_records(entries)
            target_run_ids = {
                rid
                for rid, record in run_records.items()
                if record.get("status") == "interrupted"
                and (run_id is None or rid == run_id)
            }
            additions, restored = self._queue_restore_additions(
                entries, target_run_ids
            )
            return self._build_entries(session_id, entries, additions)

        update_jsonl_locked(self._path(session_id), restore)
        return restored

    def restore_pending_inputs(
        self, session_id: str, run_id: str
    ) -> list[QueuedAgentInput]:
        """在任意 run 终态前把尚未处理的输入一次性标为 restored."""
        restored: list[QueuedAgentInput] = []

        def restore(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal restored
            additions, restored = self._queue_restore_additions(entries, {run_id})
            return self._build_entries(session_id, entries, additions)

        update_jsonl_locked(self._path(session_id), restore)
        return restored

    def update_state(self, state: AgentState) -> AgentState:
        """追加会话状态更新."""
        self._append(state.session_id, event_type="state_updated", payload=asdict(state))
        return state

    def complete_session(self, session_id: str) -> AgentState:
        """将会话标记为 completed."""
        return self._set_status(session_id, "completed")

    def rename_session(self, session_id: str, title: str) -> AgentState:
        """重命名会话标题."""
        if not title.strip():
            raise ValueError("title 不能为空")
        current = self._state_from_entries(self._read_entries(session_id))
        if current is None:
            raise KeyError(f"会话不存在: {session_id}")
        metadata = {**current.metadata, "title": title.strip(), "updated_at": utc_timestamp()}
        state = AgentState(
            session_id=current.session_id,
            status=current.status,
            cursor=current.cursor,
            active_skill_ids=current.active_skill_ids,
            pinned_skill_ids=current.pinned_skill_ids,
            metadata=metadata,
        )
        self._append(session_id, event_type="renamed", payload=asdict(state))
        return state

    def archive_session(self, session_id: str) -> AgentState:
        """归档会话."""
        return self._set_status(session_id, "archived")

    def interrupt_session(
        self,
        session_id: str,
        *,
        reason: str = "interrupted",
        run_id: str | None = None,
    ) -> AgentState:
        """将会话标记为 interrupted，并为 run 追加唯一终态."""
        return self._interrupt_session_atomic(session_id, reason=reason, run_id=run_id)

    def delete_session(self, session_id: str) -> AgentState:
        """追加逻辑删除标记."""
        return self._set_status(session_id, "deleted")

    def read_messages(self, session_id: str) -> list[AgentMessage]:
        """读取会话消息列表."""
        messages: list[AgentMessage] = []
        for entry in self._read_entries(session_id):
            if entry.get("type") != "message_appended":
                continue
            payload = entry.get("payload")
            if isinstance(payload, dict):
                messages.append(
                    AgentMessage(
                        id=payload["id"],
                        role=payload["role"],
                        content=payload["content"],
                        timestamp=payload["timestamp"],
                        tool_calls=tuple(
                            ToolCall(
                                id=call["id"],
                                name=call["name"],
                                arguments=call.get("arguments", {}),
                                result=call.get("result"),
                                error=call.get("error"),
                            )
                            for call in payload.get("tool_calls", ())
                        ),
                        content_blocks=message_content_blocks_from_payload(
                            payload.get("content_blocks")
                        ),
                        metadata={
                            **payload.get("metadata", {}),
                            "session_entry_id": entry.get("id"),
                        },
                    )
                )
        return messages

    def append_entry(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any] | None = None,
        *,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """追加底层 v1 entry."""
        return self._append(
            session_id,
            event_type=entry_type,
            payload=payload or {},
            parent_id=parent_id,
        )

    def start_run(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        client_turn_id: str | None = None,
        owner_pid: int | None = None,
    ) -> str:
        """记录 run_started；``client_turn_id`` 是持久幂等键."""
        rid = run_id or uuid.uuid4().hex
        payload: dict[str, Any] = {
            "run_id": rid,
            "started_at": utc_timestamp(),
            "owner_pid": os.getpid() if owner_pid is None else owner_pid,
        }
        if client_turn_id is not None:
            payload["client_turn_id"] = client_turn_id
        self._append(session_id, event_type="run_started", payload=payload)
        return rid

    def try_start_run(
        self,
        session_id: str,
        *,
        run_id: str,
        client_turn_id: str,
        owner_pid: int | None = None,
        orphaned_owner_pid: int | None = None,
    ) -> RunAdmission:
        """在单一文件锁事务内恢复陈旧 run、检查幂等并抢占会话.

        活着的 sidecar 通过 ``owner_pid`` 保持租约；进程已退出或旧 schema
        没有 owner 的 run 会先被一次性终结为 ``interrupted``。
        """
        owner = os.getpid() if owner_pid is None else owner_pid
        result: RunAdmission | None = None

        def admit(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal result
            additions: list[tuple[str, dict[str, Any]]] = []
            if not entries:
                metadata = {"title": session_id, "updated_at": utc_timestamp()}
                additions.append(
                    (
                        "session_created",
                        asdict(AgentState(session_id=session_id, metadata=metadata)),
                    )
                )
            preview = [
                *entries,
                *self._build_entries(session_id, entries, additions),
            ]
            # Runtime invokes this callback only after its process-local active
            # gate is clear. A still-running record owned by this PID is
            # therefore an orphan left by a failed persistence barrier.
            recovery = self._stale_recovery_additions(
                preview,
                orphaned_owner_pid=orphaned_owner_pid,
            )
            additions.extend(recovery)
            preview = [
                *entries,
                *self._build_entries(session_id, entries, additions),
            ]
            records = self._run_records(preview)
            duplicate = next(
                (
                    record
                    for record in reversed(list(records.values()))
                    if record.get("client_turn_id") == client_turn_id
                ),
                None,
            )
            if duplicate is not None:
                result = RunAdmission(
                    admitted=False,
                    status=str(duplicate.get("status") or "running"),
                    run_id=str(duplicate.get("run_id") or ""),
                    client_turn_id=client_turn_id,
                )
                return self._build_entries(session_id, entries, additions)
            active = next(
                (
                    record
                    for record in reversed(list(records.values()))
                    if record.get("status") == "running"
                ),
                None,
            )
            if active is not None:
                result = RunAdmission(
                    admitted=False,
                    status="running",
                    run_id=str(active.get("run_id") or ""),
                    client_turn_id=active.get("client_turn_id"),
                )
                return self._build_entries(session_id, entries, additions)

            current = self._state_from_entries(preview)
            if current is not None and current.status != "running":
                metadata = {
                    **current.metadata,
                    "updated_at": utc_timestamp(),
                }
                metadata.pop("reason", None)
                metadata.pop("run_id", None)
                additions.append(
                    (
                        "status_changed",
                        asdict(
                            AgentState(
                                session_id=current.session_id,
                                status="running",
                                cursor=current.cursor,
                                active_skill_ids=current.active_skill_ids,
                                pinned_skill_ids=current.pinned_skill_ids,
                                metadata=metadata,
                            )
                        ),
                    )
                )
            additions.append(
                (
                    "run_started",
                    {
                        "run_id": run_id,
                        "client_turn_id": client_turn_id,
                        "started_at": utc_timestamp(),
                        "owner_pid": owner,
                    },
                )
            )
            result = RunAdmission(
                admitted=True,
                status="running",
                run_id=run_id,
                client_turn_id=client_turn_id,
            )
            return self._build_entries(session_id, entries, additions)

        update_jsonl_locked(self._path(session_id), admit)
        assert result is not None
        return result

    def finish_run(
        self,
        session_id: str,
        run_id: str,
        *,
        status: str = "completed",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录唯一 run 终态."""
        if status not in {"completed", "failed", "aborted", "interrupted"}:
            raise ValueError(f"不支持的 run status: {status}")

        def append_terminal(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            record = self._run_record(entries, run_id)
            if record is not None and record.get("status") != "running":
                return []
            payload: dict[str, Any] = {
                "run_id": run_id,
                "status": status,
                "finished_at": utc_timestamp(),
            }
            if reason is not None:
                payload["reason"] = reason
            if metadata:
                payload["metadata"] = dict(metadata)
            return self._build_entries(
                session_id, entries, [("run_finished", payload)]
            )

        update_jsonl_locked(self._path(session_id), append_terminal)

    def find_run_by_client_turn_id(
        self, session_id: str, client_turn_id: str
    ) -> dict[str, Any] | None:
        """按幂等键查询；查询事务会先终结已失去 owner 的陈旧 run."""
        if not client_turn_id:
            return None
        result: dict[str, Any] | None = None

        def inspect(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal result
            additions = self._stale_recovery_additions(entries)
            preview = [
                *entries,
                *self._build_entries(session_id, entries, additions),
            ]
            matches = [
                record
                for record in self._run_records(preview).values()
                if record.get("client_turn_id") == client_turn_id
            ]
            result = dict(matches[-1]) if matches else None
            return self._build_entries(session_id, entries, additions)

        update_jsonl_locked(self._path(session_id), inspect)
        return result

    def append_compaction(
        self,
        session_id: str,
        compaction: Any,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """持久化正式 compaction entry，兼容 dataclass 或 mapping."""
        if hasattr(compaction, "to_payload") and callable(compaction.to_payload):
            payload = dict(compaction.to_payload())
        elif is_dataclass(compaction):
            payload = asdict(compaction)
        elif isinstance(compaction, dict):
            payload = dict(compaction)
        else:
            raise TypeError("compaction 必须是 dataclass、mapping 或提供 to_payload()")
        required = {
            "summary",
            "first_kept_entry_id",
            "tokens_before",
            "tokens_after",
            "model",
            "usage",
            "fallback_used",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"compaction 缺少字段: {', '.join(missing)}")
        summary = payload.get("summary")
        if not isinstance(summary, dict) or any(
            not str(summary.get(key, "")).strip()
            for key in (
                "目标",
                "偏好",
                "已完成",
                "关键决策",
                "未完成",
                "关键证据",
            )
        ):
            raise ValueError("compaction summary 必须包含非空六段")
        if run_id is not None:
            payload["run_id"] = run_id
        return self._append(session_id, event_type="compaction", payload=payload)

    def latest_compaction(self, session_id: str) -> dict[str, Any] | None:
        """返回最新正式 compaction payload 及其 entry 元数据."""
        for entry in reversed(self._read_entries(session_id)):
            if entry.get("type") != "compaction":
                continue
            payload = entry.get("payload")
            if isinstance(payload, dict):
                return {
                    **payload,
                    "entry_id": entry.get("id"),
                    "timestamp": entry.get("timestamp"),
                }
        return None

    def _path(self, session_id: str) -> Path:
        if "/" in session_id or "\\" in session_id or session_id in {"", ".", ".."}:
            raise ValueError("session_id 不合法")
        return self.sessions_dir / f"{session_id}.jsonl"

    def _read_entries(self, session_id: str) -> list[dict[str, Any]]:
        return read_jsonl_repair_tail(self._path(session_id))

    def _append(
        self,
        session_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        appended: list[dict[str, Any]] = []

        def add(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal appended
            appended = self._build_entries(
                session_id,
                entries,
                [(event_type, payload)],
                first_parent_id=parent_id,
            )
            return appended

        update_jsonl_locked(self._path(session_id), add)
        return appended[0]

    def _state_from_entries(self, entries: list[dict[str, Any]]) -> AgentState | None:
        state_payload: dict[str, Any] | None = None
        for entry in entries:
            if entry.get("type") in {
                "session_created",
                "state_updated",
                "status_changed",
                "renamed",
            }:
                payload = entry.get("payload")
                if isinstance(payload, dict):
                    state_payload = payload
        if state_payload is None:
            return None
        return self._state_from_payload(state_payload)

    def _set_status(
        self,
        session_id: str,
        status: SessionStatus,
        extra: dict[str, Any] | None = None,
    ) -> AgentState:
        result: AgentState | None = None

        def set_status(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal result
            current = self._state_from_entries(entries)
            if current is None:
                raise KeyError(f"会话不存在: {session_id}")
            metadata = dict(current.metadata)
            metadata["updated_at"] = utc_timestamp()
            if extra:
                metadata.update(extra)
            result = AgentState(
                session_id=current.session_id,
                status=status,
                cursor=current.cursor,
                active_skill_ids=current.active_skill_ids,
                pinned_skill_ids=current.pinned_skill_ids,
                metadata=metadata,
            )
            return self._build_entries(
                session_id, entries, [("status_changed", asdict(result))]
            )

        update_jsonl_locked(self._path(session_id), set_status)
        assert result is not None
        return result

    def _state_from_payload(self, payload: dict[str, Any]) -> AgentState:
        return AgentState(
            session_id=payload["session_id"],
            status=payload.get("status", "running"),
            cursor=payload.get("cursor", 0),
            active_skill_ids=tuple(payload.get("active_skill_ids", ())),
            pinned_skill_ids=tuple(payload.get("pinned_skill_ids", ())),
            metadata=payload.get("metadata", {}),
        )

    def _unfinished_run(self, entries: list[dict[str, Any]]) -> str | None:
        for run_id, record in reversed(list(self._run_records(entries).items())):
            if record.get("status") == "running":
                return run_id
        return None

    def _queue_records(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, QueuedAgentInput]:
        records: dict[str, QueuedAgentInput] = {}
        for entry in entries:
            event_type = entry.get("type")
            if event_type not in {
                "queue_added",
                "queue_applied",
                "queue_restored",
                "queue_discarded",
            }:
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            queue_id = payload.get("id")
            if not isinstance(queue_id, str) or not queue_id:
                continue
            if event_type == "queue_added":
                try:
                    records[queue_id] = self._queued_input_from_payload(payload)
                except (KeyError, TypeError, ValueError):
                    continue
                continue
            current = records.get(queue_id)
            if current is None:
                continue
            status = {
                "queue_applied": "applied",
                "queue_restored": "restored",
                "queue_discarded": "discarded",
            }[str(event_type)]
            records[queue_id] = QueuedAgentInput(
                id=current.id,
                client_message_id=current.client_message_id,
                session_id=current.session_id,
                run_id=current.run_id,
                mode=current.mode,
                content=current.content,
                status=status,  # type: ignore[arg-type]
                created_at=current.created_at,
                applied_at=(
                    float(payload["applied_at"])
                    if status == "applied" and payload.get("applied_at") is not None
                    else current.applied_at
                ),
            )
        return records

    def _queue_source_ids(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, str]:
        sources: dict[str, str] = {}
        for entry in entries:
            if entry.get("type") != "queue_added":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            queue_id = payload.get("id")
            source_queue_id = payload.get("source_queue_id")
            if (
                isinstance(queue_id, str)
                and queue_id
                and isinstance(source_queue_id, str)
                and source_queue_id
            ):
                sources[queue_id] = source_queue_id
        return sources

    def _queued_input_from_payload(
        self, payload: dict[str, Any]
    ) -> QueuedAgentInput:
        mode = str(payload["mode"])
        status = str(payload.get("status") or "queued")
        if mode == "steer":
            mode = "steering"
        if mode not in {"steering", "follow_up"}:
            raise ValueError(f"无效 queue mode: {mode}")
        if status not in {"queued", "restored", "applied", "discarded"}:
            raise ValueError(f"无效 queue status: {status}")
        applied_at = payload.get("applied_at")
        return QueuedAgentInput(
            id=str(payload["id"]),
            client_message_id=str(payload["client_message_id"]),
            session_id=str(payload["session_id"]),
            run_id=str(payload["run_id"]),
            mode=mode,  # type: ignore[arg-type]
            content=str(payload["content"]),
            status=status,  # type: ignore[arg-type]
            created_at=float(payload.get("created_at") or 0.0),
            applied_at=float(applied_at) if applied_at is not None else None,
        )

    def _queue_restore_additions(
        self,
        entries: list[dict[str, Any]],
        run_ids: set[str],
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[QueuedAgentInput]]:
        additions: list[tuple[str, dict[str, Any]]] = []
        restored: list[QueuedAgentInput] = []
        if not run_ids:
            return additions, restored
        for current in self._queue_records(entries).values():
            if current.run_id not in run_ids or current.status != "queued":
                continue
            item = QueuedAgentInput(
                id=current.id,
                client_message_id=current.client_message_id,
                session_id=current.session_id,
                run_id=current.run_id,
                mode=current.mode,
                content=current.content,
                status="restored",
                created_at=current.created_at,
                applied_at=None,
            )
            restored.append(item)
            additions.append(("queue_restored", asdict(item)))
        return additions, restored

    def _run_records(self, entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        runs: dict[str, dict[str, Any]] = {}
        for entry in entries:
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            legacy_run_id = (
                metadata.get("run_id") if isinstance(metadata, dict) else None
            )
            raw_run_id = payload.get("run_id", legacy_run_id)
            if raw_run_id is None:
                continue
            run_id = str(raw_run_id)
            if entry.get("type") == "run_started":
                runs[run_id] = {
                    "run_id": run_id,
                    "client_turn_id": payload.get("client_turn_id"),
                    "status": "running",
                    "started_at": payload.get("started_at", entry.get("timestamp")),
                    "started_entry_id": entry.get("id"),
                    "owner_pid": payload.get("owner_pid"),
                }
            elif entry.get("type") == "run_finished" and run_id in runs:
                runs[run_id].update(
                    {
                        "status": payload.get("status", "completed"),
                        "finished_at": payload.get("finished_at", entry.get("timestamp")),
                        "finished_entry_id": entry.get("id"),
                        "reason": payload.get("reason"),
                    }
                )
            elif (
                entry.get("type") == "status_changed"
                and payload.get("status") == "interrupted"
                and run_id in runs
                and runs[run_id].get("status") == "running"
            ):
                # 兼容旧会话：历史实现只写 session status，没有 run_finished。
                runs[run_id].update(
                    {
                        "status": "interrupted",
                        "finished_at": entry.get("timestamp"),
                        "finished_entry_id": entry.get("id"),
                        "reason": metadata.get("reason")
                        if isinstance(metadata, dict)
                        else None,
                    }
                )
        return runs

    def _run_record(
        self, entries: list[dict[str, Any]], run_id: str
    ) -> dict[str, Any] | None:
        record = self._run_records(entries).get(run_id)
        return dict(record) if record is not None else None

    def _recover_incomplete_run_once(self, session_id: str) -> AgentState | None:
        recovered: AgentState | None = None

        def recover(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal recovered
            unfinished_run = self._stale_unfinished_run(entries)
            current = self._state_from_entries(entries)
            if unfinished_run is None or current is None:
                recovered = current
                return []
            metadata = {
                **current.metadata,
                "updated_at": utc_timestamp(),
                "reason": "recovered_incomplete_run",
                "run_id": unfinished_run,
            }
            recovered = AgentState(
                session_id=current.session_id,
                status="interrupted",
                cursor=current.cursor,
                active_skill_ids=current.active_skill_ids,
                pinned_skill_ids=current.pinned_skill_ids,
                metadata=metadata,
            )
            lifecycle: list[tuple[str, dict[str, Any]]] = [
                (
                    "run_finished",
                    {
                        "run_id": unfinished_run,
                        "status": "interrupted",
                        "reason": "recovered_incomplete_run",
                        "finished_at": utc_timestamp(),
                    },
                ),
                ("status_changed", asdict(recovered)),
            ]
            queue_additions, _restored = self._queue_restore_additions(
                entries, {unfinished_run}
            )
            return self._build_entries(
                session_id,
                entries,
                [*lifecycle, *queue_additions],
            )

        update_jsonl_locked(self._path(session_id), recover)
        return recovered

    def _stale_unfinished_run(self, entries: list[dict[str, Any]]) -> str | None:
        for run_id, record in reversed(list(self._run_records(entries).items())):
            if record.get("status") == "running" and not self._owner_is_alive(record):
                return run_id
        return None

    def _stale_recovery_additions(
        self,
        entries: list[dict[str, Any]],
        *,
        orphaned_owner_pid: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        stale = [
            record
            for record in self._run_records(entries).values()
            if record.get("status") == "running"
            and (
                not self._owner_is_alive(record)
                or (
                    orphaned_owner_pid is not None
                    and record.get("owner_pid") == orphaned_owner_pid
                )
            )
        ]
        if not stale:
            return []
        now = utc_timestamp()
        additions: list[tuple[str, dict[str, Any]]] = [
            (
                "run_finished",
                {
                    "run_id": str(record["run_id"]),
                    "status": "interrupted",
                    "reason": "recovered_incomplete_run",
                    "finished_at": now,
                },
            )
            for record in stale
        ]
        current = self._state_from_entries(entries)
        if current is not None:
            last_run_id = str(stale[-1]["run_id"])
            metadata = {
                **current.metadata,
                "updated_at": now,
                "reason": "recovered_incomplete_run",
                "run_id": last_run_id,
            }
            additions.append(
                (
                    "status_changed",
                    asdict(
                        AgentState(
                            session_id=current.session_id,
                            status="interrupted",
                            cursor=current.cursor,
                            active_skill_ids=current.active_skill_ids,
                            pinned_skill_ids=current.pinned_skill_ids,
                            metadata=metadata,
                        )
                    ),
                )
            )
        queue_additions, _restored = self._queue_restore_additions(
            entries, {str(record["run_id"]) for record in stale}
        )
        additions.extend(queue_additions)
        return additions

    @staticmethod
    def _owner_is_alive(record: dict[str, Any]) -> bool:
        raw_pid = record.get("owner_pid")
        if not isinstance(raw_pid, int) or raw_pid <= 0:
            return False
        try:
            os.kill(raw_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _interrupt_session_atomic(
        self,
        session_id: str,
        *,
        reason: str,
        run_id: str | None,
    ) -> AgentState:
        interrupted: AgentState | None = None

        def interrupt(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal interrupted
            current = self._state_from_entries(entries)
            if current is None:
                raise KeyError(f"会话不存在: {session_id}")
            metadata = {
                **current.metadata,
                "updated_at": utc_timestamp(),
                "reason": reason,
            }
            if run_id is not None:
                metadata["run_id"] = run_id
            interrupted = AgentState(
                session_id=current.session_id,
                status="interrupted",
                cursor=current.cursor,
                active_skill_ids=current.active_skill_ids,
                pinned_skill_ids=current.pinned_skill_ids,
                metadata=metadata,
            )
            additions: list[tuple[str, dict[str, Any]]] = []
            record = self._run_record(entries, run_id) if run_id is not None else None
            if run_id is not None and (
                record is None or record.get("status") == "running"
            ):
                additions.append(
                    (
                        "run_finished",
                        {
                            "run_id": run_id,
                            "status": "interrupted",
                            "reason": reason,
                            "finished_at": utc_timestamp(),
                        },
                    )
                )
            already_interrupted = (
                current.status == "interrupted"
                and current.metadata.get("run_id") == run_id
                and current.metadata.get("reason") == reason
            )
            if not already_interrupted:
                additions.append(("status_changed", asdict(interrupted)))
            return self._build_entries(session_id, entries, additions)

        update_jsonl_locked(self._path(session_id), interrupt)
        assert interrupted is not None
        return interrupted

    def _build_entries(
        self,
        session_id: str,
        existing: list[dict[str, Any]],
        additions: list[tuple[str, dict[str, Any]]],
        *,
        first_parent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        parent_id = first_parent_id
        if parent_id is None and existing:
            last_id = existing[-1].get("id")
            parent_id = str(last_id) if last_id is not None else None
        built: list[dict[str, Any]] = []
        for event_type, payload in additions:
            entry = {
                "version": 1,
                "id": uuid.uuid4().hex,
                "parent_id": parent_id,
                "timestamp": utc_timestamp(),
                "session_id": session_id,
                "type": event_type,
                "payload": payload,
            }
            built.append(entry)
            parent_id = entry["id"]
        return built
