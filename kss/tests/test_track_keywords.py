"""赛道词表 load/save（默认 + 用户覆盖）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kss.news import track_keywords as tk


def test_load_defaults_has_12_tracks():
    d = tk.load_defaults()
    keys = tk.industry_keys()
    assert len(keys) == 12
    assert set(d.keys()) == set(keys)
    assert d["ai"]  # non-empty default words


def test_save_and_load_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KSS_STATE_ROOT", str(tmp_path))
    # rebind USER_FILE after env
    monkeypatch.setattr(tk, "USER_FILE", tmp_path / "storage" / "intel_track_keywords.json")
    monkeypatch.setattr(tk, "_STATE_ROOT", tmp_path)

    out = tk.set_track_keywords("ai", ["自定义词A", "自定义词B"])
    assert out["ai"] == ["自定义词A", "自定义词B"]
    # other tracks still defaults
    assert out["semi"] == tk.load_defaults()["semi"]

    raw = json.loads((tmp_path / "storage" / "intel_track_keywords.json").read_text(encoding="utf-8"))
    assert raw["tracks"]["ai"] == ["自定义词A", "自定义词B"]

    loaded = tk.load_keywords()
    assert loaded["ai"] == ["自定义词A", "自定义词B"]


def test_invalid_track_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "USER_FILE", tmp_path / "storage" / "intel_track_keywords.json")
    with pytest.raises(ValueError, match="invalid track_key"):
        tk.set_track_keywords("not_a_track", ["x"])
    with pytest.raises(ValueError, match="invalid track_key"):
        tk.save_keywords({"not_a_track": ["x"]})


def test_normalize_strips_dupes(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "USER_FILE", tmp_path / "storage" / "intel_track_keywords.json")
    out = tk.set_track_keywords("robot", ["  a ", "a", "", "b"])
    assert out["robot"] == ["a", "b"]
