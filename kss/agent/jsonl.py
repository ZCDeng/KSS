"""Agent Core 的 JSONL 存储辅助函数."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def utc_timestamp() -> float:
    """返回当前 Unix 秒级时间戳."""
    import time

    return time.time()


def read_jsonl_repair_tail(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 并修复损坏尾部.

    Args:
        path: JSONL 文件路径。

    Returns:
        有效 JSON 对象列表；若末尾存在半行或坏 JSON，会截断到最后一个有效换行位置。
    """
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
    size = path.stat().st_size
    if valid_until < size:
        with path.open("ab") as handle:
            handle.truncate(valid_until)
    return valid


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    """追加写入一个 JSONL 对象并落盘."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())

