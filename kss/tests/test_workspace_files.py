"""workspace-files：Seesaw @file 引用的只读文件搜索."""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import kss_app_bridge as bridge  # noqa: E402


def _seed(tmp_path: Path) -> None:
    reports = tmp_path / "storage" / "reports" / "bj50_scan"
    reports.mkdir(parents=True)
    (reports / "scan_20260815.md").write_text("# scan", encoding="utf-8")
    (reports / "scan_20260814.csv").write_text("a,b", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "plan.md").write_text("# plan", encoding="utf-8")
    binary = tmp_path / "storage" / "reports" / "img.png"
    binary.write_bytes(b"\x89PNG")
    hidden = tmp_path / "storage" / "reports" / ".hidden.md"
    hidden.write_text("x", encoding="utf-8")


def test_workspace_files_search_and_whitelist(monkeypatch, tmp_path):
    _seed(tmp_path)
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)

    result = bridge.dispatch("workspace-files", ["scan_2026", "10"])
    paths = [item["path"] for item in result["files"]]
    assert "storage/reports/bj50_scan/scan_20260815.md" in paths
    assert "storage/reports/bj50_scan/scan_20260814.csv" in paths
    # 非文本与隐藏文件不进结果
    assert all("img.png" not in path and ".hidden" not in path for path in paths)

    everything = bridge.dispatch("workspace-files", [""])
    all_paths = [item["path"] for item in everything["files"]]
    assert "docs/plan.md" in all_paths

    miss = bridge.dispatch("workspace-files", ["zzzz-not-here"])
    assert miss["files"] == []


def test_workspace_files_orders_by_score_then_mtime(monkeypatch, tmp_path):
    _seed(tmp_path)
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "PROJECT_ROOT", tmp_path)
    newer = tmp_path / "storage" / "reports" / "bj50_scan" / "scan_20260815.md"
    older = tmp_path / "storage" / "reports" / "bj50_scan" / "scan_20260814.csv"
    now = time.time()
    import os

    os.utime(newer, (now, now))
    os.utime(older, (now - 3600, now - 3600))
    result = bridge.dispatch("workspace-files", ["scan_"])
    paths = [item["path"] for item in result["files"]]
    assert paths.index("storage/reports/bj50_scan/scan_20260815.md") < paths.index(
        "storage/reports/bj50_scan/scan_20260814.csv"
    )
