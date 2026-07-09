"""Longbridge 覆盖边界 manifest + 确定性路由（U2 / R3 / KTD5）.

Longbridge 权限口径 = **ChinaConnect LV1**（陆股通池）：实测科创/创业/沪深主板/
ETF/指数覆盖，**北交所（``.BJ``）不覆盖**。本模块把「哪些标的走 Longbridge、哪些
路由回东财」固化为一份**机读 manifest** + 一个**确定性纯函数** :func:`route_provider`，
供 U3 采集路由与 U4 bridge 命令逐字复用，不靠临场猜。

三条设计约束：

1. **fail-safe 保守**：manifest 缺失 / 标的未扫 → 回退 ``eastmoney_akshare``（宁可
   误判「无实时」也不误判「有实时」——落不可达东财 = 无数据，非错数据，KTD6）。
2. **北交所静态规则**：``.BJ`` 后缀无需逐个探针，直接归东财（实测无返回）。
3. **陈旧检测**（adversarial P2）：ChinaConnect 资格季度调整。manifest 记
   ``scanned_at`` + 再扫周期；U3 把「covered 标的连续空响应」当**陈旧信号**。

manifest 产物**绝不含任何凭据**（security-lens P2）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# provider 令牌——**逐字**对齐 ``intraday_client.FORWARD_ONLY_PROVIDERS`` 键与
# ``provider.name``，避免 eligibility 漏判 / 路由令牌漂移。
PROVIDER_LONGBRIDGE = "longbridge"
PROVIDER_EASTMONEY = "eastmoney_akshare"

# 覆盖 manifest 默认落点：随代码一起 checkin 的静态 JSON（确定性真值表）。
DEFAULT_MANIFEST_PATH: Path = Path(__file__).resolve().parent / "longbridge_coverage.json"

# ChinaConnect 资格季度调整——manifest 超过此天数即视为「可能陈旧」，提示再扫。
RESCAN_INTERVAL_DAYS: int = 90


@dataclass(frozen=True)
class CoverageManifest:
    """Longbridge 覆盖 manifest（covered 标的集 + 扫描元数据）.

    Attributes:
        covered: 实测经 Longbridge 有实时返回的标的集（规范化大写 ``NNNNNN.SH`` 形态）。
        route_to_eastmoney: 实测无返回、显式路由回东财的标的集（含北交所 + 非陆股通）。
        scanned_at: 上次全池扫描时点（ISO-8601）；``None`` 表示未扫（空 manifest）。
        rescan_interval_days: 建议再扫周期（对齐陆股通季度调整）。
        notes: 人读备注。
    """

    covered: frozenset[str]
    route_to_eastmoney: frozenset[str]
    scanned_at: str | None
    rescan_interval_days: int = RESCAN_INTERVAL_DAYS
    notes: tuple[str, ...] = ()


def normalize_symbol(symbol: str) -> str:
    """归一为 ``NNNNNN.SH`` 规范形态（大写 + 推断交易所后缀）.

    - 已带 ``.SH/.SZ/.BJ`` → 大写。
    - 裸 6 位码 → 按前缀推交易所（6/9→SH，0/2/3→SZ，4/8→BJ）。
    与 :func:`intraday_client._resolve_longbridge_symbol` 同规则（路由前后一致）。
    """
    s = symbol.strip().upper()
    if "." in s:
        return s
    if len(s) == 6 and s.isdigit():
        head = s[0]
        if head in ("6", "9"):
            return f"{s}.SH"
        if head in ("0", "2", "3"):
            return f"{s}.SZ"
        if head in ("4", "8"):
            return f"{s}.BJ"
    return s


def is_beijing_exchange(symbol: str) -> bool:
    """北交所标的（``.BJ`` 后缀 or 8/4 开头裸码）——静态归东财，无需探针。"""
    s = normalize_symbol(symbol)
    return s.endswith(".BJ")


def route_provider(symbol: str, manifest: CoverageManifest | None = None) -> str:
    """确定性路由：标的 → ``"longbridge"`` | ``"eastmoney_akshare"``（纯函数）.

    判定顺序（保守优先）：

    1. **北交所**（``.BJ``）→ 恒 ``eastmoney_akshare``（ChinaConnect 不覆盖，静态规则）。
    2. **manifest 命中 covered** → ``longbridge``。
    3. **其余**（未扫 / 显式 route_to_eastmoney / manifest 缺失）→ ``eastmoney_akshare``
       （fail-safe：不因漏扫误判有实时）。

    Args:
        symbol: 标的码（裸码或带后缀均可）。
        manifest: 覆盖 manifest；``None`` 时惰性加载默认 manifest。

    Returns:
        provider 令牌（逐字对齐 ``FORWARD_ONLY_PROVIDERS`` 键）。
    """
    if is_beijing_exchange(symbol):
        return PROVIDER_EASTMONEY
    if manifest is None:
        manifest = load_manifest()
    norm = normalize_symbol(symbol)
    if norm in manifest.route_to_eastmoney:
        return PROVIDER_EASTMONEY  # 显式路由回东财（北交所已验证不可达）
    # KTD6 保守 → 激进：非北交所、非显式 route_to_eastmoney → 全部走 longbridge
    # manifest.covered 仅是对已 scan 的精确图，而非全池否定 —— 未 scan 的标的同样
    # 可能被 ChinaConnect 覆盖。东财端点已证实不稳定（本机直连/代理均不通），
    # 故取消 2026-07 初版的 fail-safe 回退（"其余 → eastmoney"）。
    return PROVIDER_LONGBRIDGE


_MANIFEST_CACHE: CoverageManifest | None = None


def load_manifest(path: Path | None = None, *, use_cache: bool = True) -> CoverageManifest:
    """加载覆盖 manifest（缺失 → 空 manifest，全体 fail-safe 归东财）。"""
    global _MANIFEST_CACHE
    if path is None and use_cache and _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE
    target = path or DEFAULT_MANIFEST_PATH
    manifest = _parse_manifest(target)
    if path is None and use_cache:
        _MANIFEST_CACHE = manifest
    return manifest


def _parse_manifest(target: Path) -> CoverageManifest:
    if not target.exists():
        return CoverageManifest(
            covered=frozenset(),
            route_to_eastmoney=frozenset(),
            scanned_at=None,
            notes=("manifest 缺失 —— 全体 fail-safe 路由东财；跑覆盖探针生成",),
        )
    try:
        data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CoverageManifest(
            covered=frozenset(),
            route_to_eastmoney=frozenset(),
            scanned_at=None,
            notes=("manifest 解析失败 —— 全体 fail-safe 路由东财",),
        )
    covered = frozenset(normalize_symbol(s) for s in data.get("covered", []))
    route_em = frozenset(normalize_symbol(s) for s in data.get("route_to_eastmoney", []))
    return CoverageManifest(
        covered=covered,
        route_to_eastmoney=route_em,
        scanned_at=data.get("scanned_at"),
        rescan_interval_days=int(data.get("rescan_interval_days", RESCAN_INTERVAL_DAYS)),
        notes=tuple(data.get("notes", ())),
    )


def is_manifest_stale(manifest: CoverageManifest, *, now_iso: str | None = None) -> bool:
    """manifest 是否可能陈旧（超再扫周期或从未扫）。now_iso 便于测试注入。"""
    if manifest.scanned_at is None:
        return True
    from datetime import datetime, timezone

    try:
        scanned = datetime.fromisoformat(manifest.scanned_at)
    except ValueError:
        return True
    if scanned.tzinfo is None:
        scanned = scanned.replace(tzinfo=timezone.utc)
    if now_iso is not None:
        now = datetime.fromisoformat(now_iso)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(tz=timezone.utc)
    age_days = (now - scanned).total_seconds() / 86400.0
    return age_days > manifest.rescan_interval_days


__all__ = [
    "PROVIDER_EASTMONEY",
    "PROVIDER_LONGBRIDGE",
    "RESCAN_INTERVAL_DAYS",
    "CoverageManifest",
    "DEFAULT_MANIFEST_PATH",
    "is_beijing_exchange",
    "is_manifest_stale",
    "load_manifest",
    "normalize_symbol",
    "route_provider",
]
