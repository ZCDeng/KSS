"""Agent Core 事件协议."""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict
from typing import Any, Callable

from kss.agent.jsonl import utc_timestamp
from kss.agent.types import AgentEvent


class AbortToken:
    """可在线程间共享的中止标记."""

    def __init__(self) -> None:
        """初始化未中止状态."""
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self.reason: str | None = None

    def abort(self, reason: str = "aborted") -> None:
        """触发中止."""
        with self._lock:
            if self._event.is_set():
                return
            self.reason = reason
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue

    def add_callback(self, callback: Callable[[], None]) -> None:
        """注册中止回调；已中止时立即调用."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def is_aborted(self) -> bool:
        """返回是否已中止."""
        return self._event.is_set()

    def raise_if_aborted(self) -> None:
        """若已中止则抛出 RuntimeError."""
        if self.is_aborted():
            raise RuntimeError(self.reason or "aborted")


class EventSequencer:
    """生成 protocol v1 单调事件帧."""

    def __init__(self, *, session_id: str, run_id: str, start: int = 0) -> None:
        """初始化序号器."""
        self.session_id = session_id
        self.run_id = run_id
        self._sequence = start
        self._last_id: str | None = None

    @property
    def sequence(self) -> int:
        """返回当前序号."""
        return self._sequence

    def frame(self, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        """生成下一帧事件."""
        self._sequence += 1
        event = AgentEvent(
            id=uuid.uuid4().hex,
            session_id=self.session_id,
            run_id=self.run_id,
            parent_id=self._last_id,
            timestamp=utc_timestamp(),
            sequence=self._sequence,
            type=event_type,
            payload=payload or {},
            protocol_version=1,
        )
        self._last_id = event.id
        return event

    def to_wire(self, event: AgentEvent) -> dict[str, Any]:
        """转换为可序列化协议对象."""
        return asdict(event)
