"""指标注册表单测."""

from __future__ import annotations

from pathlib import Path

import yaml

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


def test_missing_registry_falls_back_to_mi_default(tmp_path: Path) -> None:
    entries = load_registry(tmp_path / "does_not_exist.yaml")
    assert len(entries) == 1
    assert entries[0].id == "mi"
    assert entries[0].kind == KIND_MI_LEGACY


def test_malformed_yaml_falls_back_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("not: [valid: yaml: structure", encoding="utf-8")
    entries = load_registry(path)
    assert entries[0].id == "mi"


def test_invalid_entries_skipped_mi_still_present(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    payload = {
        "entries": [
            {"id": "bad_kind", "kind": "not_a_real_kind"},
            "not-a-dict",
            {"kind": "primitive"},  # 缺 id
            {
                "id": "rsi1",
                "name": "RSI 示例",
                "kind": KIND_PRIMITIVE,
                "family": "rsi_threshold",
                "params": {"period": 14, "entry_level": 30.0, "exit_level": 70.0},
                "rules_path": "storage/indicator_rules/rsi1.yaml",
                "signals_dir": "storage/indicator_signals/rsi1",
            },
        ]
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    entries = load_registry(path)
    ids = {e.id for e in entries}
    assert "rsi1" in ids
    assert "bad_kind" not in ids
    assert "mi" in ids  # 未显式声明也自动补回


def test_upsert_and_save_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    entry = RegistryEntry(
        id="ma1",
        name="均线交叉示例",
        kind=KIND_PRIMITIVE,
        family="ma_cross",
        params={"fast": 5, "slow": 20, "kind": "sma"},
        rules_path="storage/indicator_rules/ma1.yaml",
        signals_dir="storage/indicator_signals/ma1",
    )
    upsert_entry(entry, path=path)
    reloaded = load_registry(path)
    got = get_entry("ma1", reloaded)
    assert got is not None
    assert got.family == "ma_cross"
    assert got.params == {"fast": 5, "slow": 20, "kind": "sma"}


def test_upsert_replaces_same_id(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    e1 = RegistryEntry(id="x", name="v1", kind=KIND_PRIMITIVE, family="ma_cross", params={"fast": 5, "slow": 20})
    e2 = RegistryEntry(id="x", name="v2", kind=KIND_PRIMITIVE, family="ma_cross", params={"fast": 10, "slow": 30})
    upsert_entry(e1, path=path)
    upsert_entry(e2, path=path)
    reloaded = load_registry(path)
    matches = [e for e in reloaded if e.id == "x"]
    assert len(matches) == 1
    assert matches[0].name == "v2"


def test_retire_sets_status_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    entry = RegistryEntry(id="rsi1", name="RSI", kind=KIND_PRIMITIVE, family="rsi_threshold", params={})
    upsert_entry(entry, path=path)
    updated = retire_entry("rsi1", path=path)
    assert updated is not None
    assert updated.status == "retired"
    reloaded = get_entry("rsi1", load_registry(path))
    assert reloaded.status == "retired"


def test_retire_unknown_id_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    save_registry([], path=path)
    assert retire_entry("nope", path=path) is None


def test_active_entries_excludes_retired(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    e1 = RegistryEntry(id="a", name="a", kind=KIND_PRIMITIVE, family="ma_cross", params={})
    e2 = RegistryEntry(id="b", name="b", kind=KIND_PRIMITIVE, family="ma_cross", params={}, status="retired")
    save_registry([e1, e2], path=path)
    active = active_entries(load_registry(path))
    ids = {e.id for e in active}
    # MI 未显式声明时自动补回（默认 active）——预期集合含 "a" 与自动补回的 "mi"，不含 retired 的 "b"
    assert ids == {"a", "mi"}
