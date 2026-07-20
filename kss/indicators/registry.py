"""指标注册表：声明式登记每个可用指标（预注册基元 或 MI legacy）.

只做登记与查询，不含任何回测/计算逻辑——那是 kss.indicators.pack 与
kss.strategies.mi_pack（MI 专属，原地不动）的事。AI回测/图表/复盘/日终 cron
统一遍历本表的 active 条目。

存储（plan 2026-07-12-005 / U15 域割接）：真源是 kss.db 的 indicator_registry 表，
不再是 storage/indicator_registry.yaml——旧 yaml 文件不再被写入。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from kss.indicators.primitives import FAMILY_SR_LEVEL, default_params
from kss.storage.db import connect, ensure_schema

KIND_PRIMITIVE = "primitive"
KIND_MI_LEGACY = "mi_legacy"
KINDS = (KIND_PRIMITIVE, KIND_MI_LEGACY)

STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"


def project_root() -> Path:
    """代码根（kss/ 所在目录）；bundle 模式由 KSS_PROJECT_ROOT 指定。"""
    env = os.environ.get("KSS_PROJECT_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def state_root() -> Path:
    """可变状态根；bundle 模式由 KSS_STATE_ROOT 指定，缺省回落 project_root。"""
    env = os.environ.get("KSS_STATE_ROOT")
    if env:
        return Path(env)
    return project_root()


def registry_db_path(root: Path | None = None) -> Path:
    return (root or state_root()) / "storage" / "kss.db"


@dataclass
class RegistryEntry:
    """一条注册指标：MI legacy 或基元库 primitive。"""

    id: str
    name: str
    kind: str
    family: str | None = None  # kind=primitive 时必填：ma_cross/rsi_threshold/boll_atr
    params: dict[str, Any] = field(default_factory=dict)
    rules_path: str = ""  # 相对 state_root
    signals_dir: str = ""  # 相对 state_root
    status: str = STATUS_ACTIVE
    solidified_at: str | None = None
    verdict_ref: str | None = None  # 相对 state_root，指向 GO 裁决快照
    symbols: list[str] = field(default_factory=list)  # 固化时通过 GO 门禁的标的；日终 cron 只刷这些

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"未知 registry kind: {self.kind!r}；允许 {KINDS}")

    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE


# MI 零迁移登记：指向现有 storage/mi_signals + storage/mi_rules.yaml，
# 不依赖磁盘文件是否存在——内置默认值，保证注册表永远至少含 MI 一条。
MI_ENTRY = RegistryEntry(
    id="mi",
    name="MI 动量",
    kind=KIND_MI_LEGACY,
    family=None,
    params={},
    rules_path="storage/mi_rules.yaml",
    signals_dir="storage/mi_signals",
    status=STATUS_ACTIVE,
)


# sr 零固化登记（plan 2026-07-20-001 KTD2）：装好即 active，symbols 留空——
# 日终批跑对 symbols 为空且从未固化（solidified_at 为空）的 primitive 条目回退自选股池，
# 首次批跑后信号即上图；GO 门禁/固化/退役保留为后续调参手段，不再是上图前置闸。
SR_ENTRY = RegistryEntry(
    id="sr",
    name="支撑阻力",
    kind=KIND_PRIMITIVE,
    family=FAMILY_SR_LEVEL,
    params=default_params(FAMILY_SR_LEVEL),
    rules_path="storage/indicator_rules/sr.yaml",
    signals_dir="storage/indicator_signals/sr",
    status=STATUS_ACTIVE,
)


def _default_entries() -> list[RegistryEntry]:
    return [MI_ENTRY, SR_ENTRY]


def _row_to_entry(row: Any) -> RegistryEntry:
    return RegistryEntry(
        id=row["entry_id"],
        name=row["name"],
        kind=row["kind"],
        family=row["family"],
        params=json.loads(row["params_json"]) if row["params_json"] else {},
        rules_path=row["rules_path"] or "",
        signals_dir=row["signals_dir"] or "",
        status=row["status"],
        solidified_at=row["solidified_at"],
        verdict_ref=row["verdict_ref"],
        symbols=json.loads(row["symbols_json"]) if row["symbols_json"] else [],
    )


def load_registry(db_path: Path | None = None) -> list[RegistryEntry]:
    """加载注册表；库/表不存在或整体损坏 → 内置默认（含 MI），不抛异常。非法行跳过。"""
    path = registry_db_path(db_path) if db_path is None else db_path
    try:
        with connect(path) as conn:
            ensure_schema(conn)
            rows = conn.execute("SELECT * FROM indicator_registry").fetchall()
    except Exception:  # noqa: BLE001
        return _default_entries()

    out: list[RegistryEntry] = []
    seen_ids: set[str] = set()
    for row in rows:
        try:
            entry = _row_to_entry(row)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue  # 未知 kind 或损坏的 JSON 列，跳过该行，不拖垮整表
        if entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        out.append(entry)

    if "mi" not in seen_ids:
        out.insert(0, MI_ENTRY)
    if "sr" not in seen_ids:
        out.append(SR_ENTRY)
    return out


def save_registry(entries: list[RegistryEntry], db_path: Path | None = None) -> Path:
    """整表替换（注册表是当前态，不是追加台账；跟 watchlist 同语义）。"""
    path = registry_db_path(db_path) if db_path is None else db_path
    with connect(path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM indicator_registry")
        for e in entries:
            conn.execute(
                """INSERT INTO indicator_registry
                (entry_id, name, kind, family, params_json, rules_path, signals_dir,
                 status, solidified_at, verdict_ref, symbols_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    e.id, e.name, e.kind, e.family,
                    json.dumps(e.params, ensure_ascii=False),
                    e.rules_path, e.signals_dir, e.status, e.solidified_at, e.verdict_ref,
                    json.dumps(e.symbols, ensure_ascii=False),
                ),
            )
    return path


def get_entry(entry_id: str, entries: list[RegistryEntry] | None = None) -> RegistryEntry | None:
    entries = entries if entries is not None else load_registry()
    for e in entries:
        if e.id == entry_id:
            return e
    return None


def active_entries(entries: list[RegistryEntry] | None = None) -> list[RegistryEntry]:
    entries = entries if entries is not None else load_registry()
    return [e for e in entries if e.is_active()]


def upsert_entry(entry: RegistryEntry, *, db_path: Path | None = None) -> list[RegistryEntry]:
    """新增或替换同 id 条目，写回 kss.db，返回更新后的完整列表。"""
    entries = load_registry(db_path)
    out = [e for e in entries if e.id != entry.id]
    out.append(entry)
    save_registry(out, db_path)
    return out


def retire_entry(entry_id: str, *, db_path: Path | None = None) -> RegistryEntry | None:
    """标记 status=retired（不删数据）；未知 id 返回 None。"""
    entries = load_registry(db_path)
    updated: RegistryEntry | None = None
    out: list[RegistryEntry] = []
    for e in entries:
        if e.id == entry_id:
            e = replace(e, status=STATUS_RETIRED)
            updated = e
        out.append(e)
    if updated is not None:
        save_registry(out, db_path)
    return updated
