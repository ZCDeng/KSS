"""Agent Core 会话 JSONL 存储."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kss.agent.jsonl import append_jsonl, read_jsonl_repair_tail, utc_timestamp
from kss.agent.types import AgentMessage, AgentState, SessionStatus, ToolCall


class SessionStore:
    """append-only JSONL 会话存储.

    每个会话一个 JSONL 文件，记录均为 v1，并包含 ``id``、``parent_id``、``timestamp``。
    逻辑删除、完成和中断通过追加状态事件表达，不重写历史记录。
    """

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
        unfinished_run = self._unfinished_run(entries)
        if state is not None and unfinished_run is not None:
            self.interrupt_session(
                session_id,
                reason="recovered_incomplete_run",
                run_id=unfinished_run,
            )
            return AgentState(
                session_id=state.session_id,
                status="interrupted",
                cursor=state.cursor,
                active_skill_ids=state.active_skill_ids,
                pinned_skill_ids=state.pinned_skill_ids,
                metadata={
                    **state.metadata,
                    "reason": "recovered_incomplete_run",
                    "run_id": unfinished_run,
                },
            )
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

    def append_message(self, session_id: str, message: AgentMessage) -> None:
        """向会话追加消息."""
        self._append(session_id, event_type="message_appended", payload=asdict(message))

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
        """将会话标记为 interrupted."""
        extra = {"reason": reason}
        if run_id is not None:
            extra["run_id"] = run_id
        return self._set_status(session_id, "interrupted", extra)

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
                        metadata=payload.get("metadata", {}),
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

    def start_run(self, session_id: str, *, run_id: str | None = None) -> str:
        """记录 run_started 并返回 run_id."""
        rid = run_id or uuid.uuid4().hex
        self._append(session_id, event_type="run_started", payload={"run_id": rid})
        return rid

    def finish_run(self, session_id: str, run_id: str) -> None:
        """记录 run_finished."""
        self._append(session_id, event_type="run_finished", payload={"run_id": run_id})

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
        if parent_id is None:
            entries = self._read_entries(session_id)
            if entries:
                last_id = entries[-1].get("id")
                parent_id = str(last_id) if last_id is not None else None
        entry = {
            "version": 1,
            "id": uuid.uuid4().hex,
            "parent_id": parent_id,
            "timestamp": utc_timestamp(),
            "session_id": session_id,
            "type": event_type,
            "payload": payload,
        }
        append_jsonl(self._path(session_id), entry)
        return entry

    def _state_from_entries(self, entries: list[dict[str, Any]]) -> AgentState | None:
        state_payload: dict[str, Any] | None = None
        for entry in entries:
            if entry.get("type") in {"session_created", "state_updated", "status_changed", "renamed"}:
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
        current = self._state_from_entries(self._read_entries(session_id))
        if current is None:
            raise KeyError(f"会话不存在: {session_id}")
        metadata = dict(current.metadata)
        metadata["updated_at"] = utc_timestamp()
        if extra:
            metadata.update(extra)
        state = AgentState(
            session_id=current.session_id,
            status=status,
            cursor=current.cursor,
            active_skill_ids=current.active_skill_ids,
            pinned_skill_ids=current.pinned_skill_ids,
            metadata=metadata,
        )
        self._append(session_id, event_type="status_changed", payload=asdict(state))
        return state

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
        runs: dict[str, bool] = {}
        for entry in entries:
            payload = entry.get("payload")
            if not isinstance(payload, dict) or "run_id" not in payload:
                continue
            run_id = str(payload["run_id"])
            if entry.get("type") == "run_started":
                runs[run_id] = False
            elif entry.get("type") == "run_finished":
                runs[run_id] = True
        for run_id, finished in reversed(list(runs.items())):
            if not finished:
                return run_id
        return None
