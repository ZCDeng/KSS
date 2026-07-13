"""指标注册表单测（plan 2026-07-12-005 / U15：真源从 yaml 割接到 kss.db）."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kss.indicators.registry import (
    KIND_MI_LEGACY,
    KIND_PRIMITIVE,
    RegistryEntry,
    active_entries,
    get_entry,
    load_registry,
    retire_entry,
    save_registry,
    upsert_entry,
)


def test_missing_db_falls_back_to_mi_default(tmp_path: Path) -> None:
    entries = load_registry(tmp_path / "does_not_exist.db")
    assert len(entries) == 1
    assert entries[0].id == "mi"
    assert entries[0].kind == KIND_MI_LEGACY


def test_corrupt_db_falls_back_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    path.write_bytes(b"not a real sqlite file")
    entries = load_registry(path)
    assert entries[0].id == "mi"


def test_row_with_unknown_kind_skipped_mi_still_present(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    save_registry(
        [RegistryEntry(id="a", name="a", kind=KIND_PRIMITIVE, family="ma_cross", params={})],
        path,
    )
    # 手工注入一行未知 kind——绕过 RegistryEntry.__post_init__ 的校验，模拟数据损坏。
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO indicator_registry (entry_id, name, kind, status) VALUES (?,?,?,?)",
            ("bad_kind", "bad", "not_a_real_kind", "active"),
        )
    entries = load_registry(path)
    ids = {e.id for e in entries}
    assert "bad_kind" not in ids
    assert "a" in ids
    assert "mi" in ids  # 未显式声明也自动补回


def test_upsert_and_save_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    entry = RegistryEntry(
        id="ma1",
        name="均线交叉示例",
        kind=KIND_PRIMITIVE,
        family="ma_cross",
        params={"fast": 5, "slow": 20, "kind": "sma"},
        rules_path="storage/indicator_rules/ma1.yaml",
        signals_dir="storage/indicator_signals/ma1",
    )
    upsert_entry(entry, db_path=path)
    reloaded = load_registry(path)
    got = get_entry("ma1", reloaded)
    assert got is not None
    assert got.family == "ma_cross"
    assert got.params == {"fast": 5, "slow": 20, "kind": "sma"}


def test_upsert_replaces_same_id(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    e1 = RegistryEntry(id="x", name="v1", kind=KIND_PRIMITIVE, family="ma_cross", params={"fast": 5, "slow": 20})
    e2 = RegistryEntry(id="x", name="v2", kind=KIND_PRIMITIVE, family="ma_cross", params={"fast": 10, "slow": 30})
    upsert_entry(e1, db_path=path)
    upsert_entry(e2, db_path=path)
    reloaded = load_registry(path)
    matches = [e for e in reloaded if e.id == "x"]
    assert len(matches) == 1
    assert matches[0].name == "v2"


def test_retire_sets_status_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    entry = RegistryEntry(id="rsi1", name="RSI", kind=KIND_PRIMITIVE, family="rsi_threshold", params={})
    upsert_entry(entry, db_path=path)
    updated = retire_entry("rsi1", db_path=path)
    assert updated is not None
    assert updated.status == "retired"
    reloaded = get_entry("rsi1", load_registry(path))
    assert reloaded.status == "retired"


def test_retire_unknown_id_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    save_registry([], path)
    assert retire_entry("nope", db_path=path) is None


def test_active_entries_excludes_retired(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    e1 = RegistryEntry(id="a", name="a", kind=KIND_PRIMITIVE, family="ma_cross", params={})
    e2 = RegistryEntry(id="b", name="b", kind=KIND_PRIMITIVE, family="ma_cross", params={}, status="retired")
    save_registry([e1, e2], path)
    active = active_entries(load_registry(path))
    ids = {e.id for e in active}
    # MI 未显式声明时自动补回（默认 active）——预期集合含 "a" 与自动补回的 "mi"，不含 retired 的 "b"
    assert ids == {"a", "mi"}


def test_save_registry_is_full_replace_not_append(tmp_path: Path) -> None:
    """注册表是当前态（跟 watchlist 同语义），不是追加台账。"""
    path = tmp_path / "registry.db"
    save_registry([RegistryEntry(id="a", name="a", kind=KIND_PRIMITIVE, family="ma_cross", params={})], path)
    save_registry([RegistryEntry(id="b", name="b", kind=KIND_PRIMITIVE, family="ma_cross", params={})], path)
    ids = {e.id for e in load_registry(path)}
    assert ids == {"b", "mi"}  # "a" 已被整表替换掉
