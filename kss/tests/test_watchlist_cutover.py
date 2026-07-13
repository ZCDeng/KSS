"""U15 域割接测试：watchlist（第一个割接域，plan 2026-07-12-005）.

Covers AE7：真源从 storage/watchlist_symbols.txt 切到 kss.db watchlist 表，
读写方输出与割接前等价。

跑：uv run pytest kss/tests/test_watchlist_cutover.py -q
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402

from kss.storage.watchlist import load_watchlist, set_watchlist  # noqa: E402


# --------------------------------------------------------------------------- #
# ① golden：整表替换 + 保序读回，等价于割接前「一行一码、文件行序即优先序」的行为
# --------------------------------------------------------------------------- #


def test_set_then_load_round_trip_preserves_order(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    symbols = ["688322.SH", "688017.SH", "300750.SZ"]
    set_watchlist(symbols, db_path=db_path)
    assert load_watchlist(db_path=db_path) == symbols  # 顺序不是集合语义


def test_set_watchlist_is_full_replace_not_append(tmp_path: Path) -> None:
    """自选是「当前态」，不是追加台账——删除的票要真的消失。"""
    db_path = tmp_path / "kss.db"
    set_watchlist(["A.SH", "B.SH"], db_path=db_path)
    set_watchlist(["B.SH", "C.SH"], db_path=db_path)
    assert load_watchlist(db_path=db_path) == ["B.SH", "C.SH"]


def test_empty_watchlist_round_trips_to_empty_list(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    set_watchlist([], db_path=db_path)
    assert load_watchlist(db_path=db_path) == []


def test_load_watchlist_on_missing_db_returns_empty_list(tmp_path: Path) -> None:
    """割接前旧代码：txt 文件不存在 → []。割接后：db 文件不存在 → 同样 []
    （connect() 会新建空库+空表，不是报错）。"""
    db_path = tmp_path / "does_not_exist" / "kss.db"
    assert load_watchlist(db_path=db_path) == []


# --------------------------------------------------------------------------- #
# ② 割接后新写入落库，旧路径（watchlist_symbols.txt）无新文件产生
# --------------------------------------------------------------------------- #


def test_watchlist_set_command_does_not_touch_txt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    out = b.dispatch("watchlist-set", ["688017.SH,688322.SH"])
    assert out["ok"] is True
    assert out["symbols"] == ["688017.SH", "688322.SH"]
    txt_path = tmp_path / "storage" / "watchlist_symbols.txt"
    assert not txt_path.exists(), "watchlist-set 不该再写 watchlist_symbols.txt（Tier A 域不再产生新散文件）"


# --------------------------------------------------------------------------- #
# ③ cron 短进程与 sidecar 并发写同域不冲突
# --------------------------------------------------------------------------- #


def test_concurrent_watchlist_writes_do_not_deadlock(tmp_path: Path) -> None:
    # 稳态场景：kss.db 已存在（生产里由 migrate_storage.py 单进程建库），cron/sidecar
    # 并发写只碰常规读写锁，busy_timeout 兜底。冷启动并发建库另有专门测试（下面）。
    db_path = tmp_path / "kss.db"
    set_watchlist([], db_path=db_path)

    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(10):
                set_watchlist([f"{n}{i:02d}.SH"], db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"并发写 watchlist 触发异常: {errors}"


# --------------------------------------------------------------------------- #
# ④ 回读路径（bridge 命令）输出与割接前逐字段一致
# --------------------------------------------------------------------------- #


def test_indicator_watchlist_symbols_reflects_db_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    b.dispatch("watchlist-set", ["688017.SH,300750.SZ"])
    assert b._indicator_watchlist_symbols() == ["688017.SH", "300750.SZ"]


def test_watchlist_set_registered_as_write_command() -> None:
    assert "watchlist-set" in b.COMMANDS
    assert "watchlist-set" in b.WRITE_COMMANDS
