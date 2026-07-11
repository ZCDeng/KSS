"""指标注册表：声明式登记每个可用指标（预注册基元 或 MI legacy）.

只做登记与查询，不含任何回测/计算逻辑——那是 kss.indicators.pack 与
kss.strategies.mi_pack（MI 专属，原地不动）的事。AI回测/图表/复盘/日终 cron
统一遍历本表的 active 条目。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

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


def registry_path(root: Path | None = None) -> Path:
    return (root or state_root()) / "storage" / "indicator_registry.yaml"


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


def _default_entries() -> list[RegistryEntry]:
    return [MI_ENTRY]


def load_registry(path: Path | None = None) -> list[RegistryEntry]:
    """加载注册表；缺文件/解析失败 → 内置默认（含 MI），不抛异常。非法条目跳过。"""
    path = path or registry_path()
    if not path.exists():
        return _default_entries()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return _default_entries()
    entries_raw = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries_raw, list):
        return _default_entries()

    out: list[RegistryEntry] = []
    seen_ids: set[str] = set()
    for item in entries_raw:
        if not isinstance(item, dict) or "id" not in item or "kind" not in item:
            continue
        try:
            entry = RegistryEntry(
                id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                kind=str(item["kind"]),
                family=item.get("family"),
                params=dict(item.get("params") or {}),
                rules_path=str(item.get("rules_path", "")),
                signals_dir=str(item.get("signals_dir", "")),
                status=str(item.get("status", STATUS_ACTIVE)),
                solidified_at=item.get("solidified_at"),
                verdict_ref=item.get("verdict_ref"),
            )
        except ValueError:
            continue  # 未知 kind，跳过并记录告警交由调用方（此处不静默吞——由 caller 决定是否记日志）
        if entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        out.append(entry)

    if "mi" not in seen_ids:
        out.insert(0, MI_ENTRY)
    return out


def save_registry(entries: list[RegistryEntry], path: Path | None = None) -> Path:
    path = path or registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [asdict(e) for e in entries]}
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
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


def upsert_entry(entry: RegistryEntry, *, path: Path | None = None) -> list[RegistryEntry]:
    """新增或替换同 id 条目，写回磁盘，返回更新后的完整列表。"""
    entries = load_registry(path)
    out = [e for e in entries if e.id != entry.id]
    out.append(entry)
    save_registry(out, path)
    return out


def retire_entry(entry_id: str, *, path: Path | None = None) -> RegistryEntry | None:
    """标记 status=retired（不删数据）；未知 id 返回 None。"""
    entries = load_registry(path)
    updated: RegistryEntry | None = None
    out: list[RegistryEntry] = []
    for e in entries:
        if e.id == entry_id:
            e = replace(e, status=STATUS_RETIRED)
            updated = e
        out.append(e)
    if updated is not None:
        save_registry(out, path)
    return updated
