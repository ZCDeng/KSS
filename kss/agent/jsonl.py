"""Agent Core 的 JSONL 存储辅助函数."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - KSS Desktop 只运行在 Unix/macOS
    fcntl = None  # type: ignore[assignment]


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def utc_timestamp() -> float:
    """返回当前 Unix 秒级时间戳."""
    import time

    return time.time()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _exclusive_lock(path: Path):
    """同时持有进程内线程锁和跨进程文件锁."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _thread_lock(path):
        with lock_path.open("a+b") as lock_handle:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _read_jsonl_unlocked(path: Path, *, repair_tail: bool) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    valid: list[dict[str, Any]] = []
    valid_until = 0
    offset = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            offset += len(raw_line)
            if not raw_line.strip():
                valid_until = offset
                continue
            try:
                item = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                break
            if isinstance(item, dict):
                valid.append(item)
                valid_until = offset
            else:
                break
    if repair_tail:
        size = path.stat().st_size
        if valid_until < size:
            with path.open("r+b") as handle:
                handle.truncate(valid_until)
                handle.flush()
                os.fsync(handle.fileno())
    return valid


def read_jsonl_repair_tail(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 并修复损坏尾部.

    Args:
        path: JSONL 文件路径。

    Returns:
        有效 JSON 对象列表；若末尾存在半行或坏 JSON，
        会截断到最后一个有效换行位置。
    """
    with _exclusive_lock(path):
        return _read_jsonl_unlocked(path, repair_tail=True)


def update_jsonl_locked(
    path: Path,
    updater: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """在同一锁内修复尾部、读取当前记录、追加并 fsync.

    ``updater`` 接收当前有效记录的快照并返回待追加记录。调用方可据此
    安全计算 ``parent_id``、幂等状态或一次性追加多个相关终态。
    """
    with _exclusive_lock(path):
        entries = _read_jsonl_unlocked(path, repair_tail=True)
        additions = updater(list(entries))
        if not additions:
            return []
        lines: list[str] = []
        for item in additions:
            if not isinstance(item, dict):
                raise TypeError("JSONL updater 必须返回对象列表")
            lines.append(
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        return additions


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    """追加写入一个 JSONL 对象并落盘."""
    update_jsonl_locked(path, lambda _entries: [item])
