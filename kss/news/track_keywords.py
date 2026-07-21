"""12 赛道 yupi 监控词表：仓库默认 + STATE_ROOT 用户覆盖（KSS 权威）。

用户覆盖路径：``$KSS_STATE_ROOT/storage/intel_track_keywords.json``
形状：``{"tracks": {"ai": ["词1", "词2"], ...}}``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE / "track_keywords.default.json"
SOURCES_FILE = HERE / "news_sources.json"

_STATE_ROOT = Path(os.environ["KSS_STATE_ROOT"]) if os.environ.get("KSS_STATE_ROOT") else HERE.parents[1]
USER_FILE = _STATE_ROOT / "storage" / "intel_track_keywords.json"


def industry_keys() -> list[str]:
    cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    return [i["key"] for i in cfg.get("industries", [])]


def _normalize_words(words: Any) -> list[str]:
    if not isinstance(words, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if not isinstance(w, str):
            continue
        t = w.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def load_defaults() -> dict[str, list[str]]:
    data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    tracks = data.get("tracks") or {}
    keys = industry_keys()
    return {k: _normalize_words(tracks.get(k, [])) for k in keys}


def load_user_override() -> dict[str, list[str]]:
    if not USER_FILE.is_file():
        return {}
    try:
        data = json.loads(USER_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tracks = data.get("tracks") or {}
    if not isinstance(tracks, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in tracks.items():
        if isinstance(k, str):
            out[k] = _normalize_words(v)
    return out


def load_keywords() -> dict[str, list[str]]:
    """默认 ∪ 用户覆盖：用户对某 track 有键则整表替换该 track。"""
    base = load_defaults()
    user = load_user_override()
    for k, words in user.items():
        if k in base:
            base[k] = words
    return base


def save_keywords(tracks: dict[str, list[str]]) -> dict[str, list[str]]:
    """校验 track_key 后原子写用户覆盖；返回合并后的 load_keywords() 结果。

    Raises:
        ValueError: 非法 track_key 或 tracks 非 dict。
    """
    if not isinstance(tracks, dict):
        raise ValueError("tracks must be a dict of track_key -> keywords[]")
    valid = set(industry_keys())
    cleaned: dict[str, list[str]] = {}
    for k, v in tracks.items():
        if k not in valid:
            raise ValueError(f"invalid track_key: {k!r}")
        cleaned[k] = _normalize_words(v)
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tracks": cleaned}
    tmp = USER_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, USER_FILE)
    return load_keywords()


def set_track_keywords(track_key: str, words: list[str]) -> dict[str, list[str]]:
    """只改一个赛道，合并进用户覆盖后保存。"""
    valid = set(industry_keys())
    if track_key not in valid:
        raise ValueError(f"invalid track_key: {track_key!r}")
    user = load_user_override()
    user[track_key] = _normalize_words(words)
    # 保留用户文件中其它 key（仅 valid）
    return save_keywords({k: v for k, v in user.items() if k in valid})
