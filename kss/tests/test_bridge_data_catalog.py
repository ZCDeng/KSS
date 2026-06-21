"""U4 测试：dispatch data-catalog 处理器 + mtime 缓存。
跑：.venv-desktop/bin/python -m pytest kss/tests/test_bridge_data_catalog.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def _reset_cache():
    b._catalog_cache["mtime"] = None
    b._catalog_cache["data"] = None


def test_happy_returns_catalog(tmp_path, monkeypatch):
    p = tmp_path / "data_catalog.json"
    p.write_text(json.dumps({"generatedAt": "x", "datasets": [{"name": "margin_daily"}]}),
                 encoding="utf-8")
    monkeypatch.setattr(b, "DATA_CATALOG_PATH", p)
    _reset_cache()
    out = b.dispatch("data-catalog", [])
    assert out["datasets"][0]["name"] == "margin_daily"


def test_missing_file_fail_loud_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "DATA_CATALOG_PATH", tmp_path / "nope.json")
    _reset_cache()
    out = b.dispatch("data-catalog", [])
    assert out["error"] == "catalog_not_built" and "hint" in out   # 不抛、不崩 sidecar


def test_mtime_cache(tmp_path, monkeypatch):
    p = tmp_path / "data_catalog.json"
    p.write_text(json.dumps({"v": 1}), encoding="utf-8")
    monkeypatch.setattr(b, "DATA_CATALOG_PATH", p)
    _reset_cache()
    first = b._data_catalog()
    second = b._data_catalog()
    assert first is second                      # 同 mtime 复用缓存对象，不重解析
    import os, time
    time.sleep(0.01)
    p.write_text(json.dumps({"v": 2}), encoding="utf-8")
    os.utime(p, None)
    third = b._data_catalog()
    assert third["v"] == 2 and third is not first  # mtime 变 → 重读
