"""Packaged research profile loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import get_profile as _code_profile
from .graph import list_profiles as _code_profiles


def _profile_dir(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return Path(project_root) / "kss" / "config" / "research_profiles"
    return Path(__file__).resolve().parents[1] / "config" / "research_profiles"


def list_profiles(project_root: Path | None = None) -> list[dict[str, Any]]:
    profiles = {p["profile_id"]: p for p in _code_profiles()}
    directory = _profile_dir(project_root)
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            profile_id = data.get("profile_id")
            if isinstance(profile_id, str) and profile_id:
                profiles[profile_id] = {**profiles.get(profile_id, {}), **data}
    return list(profiles.values())


def get_profile(profile_id: str, project_root: Path | None = None) -> dict[str, Any]:
    for profile in list_profiles(project_root):
        if profile.get("profile_id") == profile_id:
            return profile
    return _code_profile(profile_id).to_wire()
