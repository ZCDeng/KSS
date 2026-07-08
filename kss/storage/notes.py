"""本地 notes（沉淀库）— markdown + JSON 双格式 atomic 写。

本轮（plan 2026-07-09-001）仅支持资讯雷达 digest 的写入；阅读 UI 留待后续
Notes 页面（沉淀库只写不读由用户确认）。

文件布局::

    STATE_ROOT/storage/notes/
        intel_digest_20260709_ai.md      # 人读
        intel_digest_20260709_ai.json    # 结构化（agent/检索用）
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def _state_root() -> Path:
    """每次调用都读 env，便于测试 monkeypatch."""
    raw = os.environ.get("KSS_STATE_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2]


def _notes_dir() -> Path:
    return _state_root() / "storage" / "notes"


def _date_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _atomic_write(path: Path, content: str) -> None:
    """用 tempfile + os.replace 实现 atomic 写，防并发半写."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_intel_digest(
    track_key: str,
    track_name: str,
    prompt: str,
    response: str,
    model: str,
    items: list[dict[str, Any]],
    *,
    date: str | None = None,
) -> Path:
    """写入单赛道 AI digest 沉淀：md（人读）+ json（结构化），atomic 双写。

    Returns:
        md 文件路径。
    """
    date = date or _date_str()
    stem = f"intel_digest_{date}_{track_key}"
    md_path = _notes_dir() / f"{stem}.md"
    json_path = _notes_dir() / f"{stem}.json"

    # md：纯文本标题 + 要点
    md_content = f"# {track_name} 要点 · {date}\n\n{response.strip()}\n"
    _atomic_write(md_path, md_content)

    # json：完整结构化 payload（prompt / response / model / items / 时间戳）
    payload = {
        "track_key": track_key,
        "track_name": track_name,
        "date": date,
        "prompt": prompt,
        "response": response.strip(),
        "model": model,
        "item_count": len(items),
        "items": items,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _atomic_write(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return md_path


def intel_digest_exists(track_key: str, date: str | None = None) -> Path | None:
    """今日（指定日期）该赛道的沉淀 md 是否存在？存在则返回 md 路径，否则 None."""
    date = date or _date_str()
    md_path = _notes_dir() / f"intel_digest_{date}_{track_key}.md"
    return md_path if md_path.exists() else None


def read_intel_digest_response(track_key: str, date: str | None = None) -> str | None:
    """读已存在的沉淀 md 内容（去掉标题行），用于缓存命中回放。"""
    md_path = intel_digest_exists(track_key, date)
    if md_path is None:
        return None
    text = md_path.read_text(encoding="utf-8")
    # 去掉首行 `# 标题 · 日期\n\n`
    parts = text.split("\n\n", 1)
    return parts[1].strip() if len(parts) == 2 else text