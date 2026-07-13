"""Tier A 域割接台账（plan 2026-07-12-005 / U15）.

记录逐域割接完成状态，供 U16 门控表暴露（哪些域已切到 kss.db，哪些还在读旧文件）。
纯 stdlib，键为域名，值覆写（不是追加历史——只关心「当前是否已割接」，不是审计日志）。
"""

from __future__ import annotations

import json
from pathlib import Path

from kss.config.paths import STORAGE_ROOT

LEDGER_PATH = STORAGE_ROOT / "migration_ledger.json"


def record_cutover(
    domain: str,
    *,
    completed_at: str,
    golden_result: str,
    notes: str = "",
    ledger_path: Path | None = None,
) -> dict:
    """记一域割接完成；返回写入后的完整台账 dict。"""
    path = ledger_path or LEDGER_PATH
    ledger: dict = {}
    if path.is_file():
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            ledger = {}
    ledger[domain] = {
        "completedAt": completed_at,
        "goldenResult": golden_result,
        "notes": notes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return ledger


def load_ledger(ledger_path: Path | None = None) -> dict:
    path = ledger_path or LEDGER_PATH
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
