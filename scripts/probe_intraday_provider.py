#!/usr/bin/env python3
"""U1 供应商探针：在写任何采集代码前，先验真实响应（学习 #5「先验数据源」）.

产出一份**不可变、机读**的探针报告（原子写），对 10 个代表性标的分类：分钟频率 /
最早最晚时间 / 字段映射 / 连续性·重复·空值率 / 请求延迟 / publication delay 分布
（喂 RB2 的 p95+裕度）/ schema-hash / eligibility。东财(akshare)只接受为
``forward_observed`` / ``research_only``，**绝不** ``pit_backtest_eligible``（PIT 红线）。

报告**永不**写入任何 token/凭据（admission 测试）。东财前向源无需 token；Tushare 历史
准入是阶段-6 gate（Follow-Up，决策 D1），本探针仅产出保守分类、不建历史读路径。

核心 :func:`run_probe` 接受**可注入** provider，便于单测 mock（不 live 调用）。
CLI 才构造真实 provider。

手动测试：
    .venv-desktop/bin/python scripts/probe_intraday_provider.py \
        --report storage/reports/intraday_provider_probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# 允许脚本直接运行时 import kss 包（与仓库内其它 scripts 一致）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.data.intraday_client import (  # noqa: E402
    DEFAULT_PROBE_UNIVERSE,
    EM_COLUMN_MAP,
    EastmoneyAkshareProvider,
    Eligibility,
    FetchResult,
    IntradayProvider,
    RepresentativeSymbol,
    classify_eligibility,
    schema_hash,
)

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 探针报告 schema 版本：报告结构变更时 bump，便于下游消费方校验。
REPORT_SCHEMA_VERSION = 1

# 判定空值/零成交时关注的 canonical 核心字段（经 EM_COLUMN_MAP 反查中文列）。
_CORE_CANONICAL = ("open", "high", "low", "close", "volume", "amount")
_CANONICAL_TO_RAW = {v: k for k, v in EM_COLUMN_MAP.items()}


def _analyze_symbol(
    sym: RepresentativeSymbol,
    result: FetchResult,
    interval_minutes: int,
    probe_now: datetime,
) -> dict[str, Any]:
    """把单标的取数结果析出机读指标（连续性/重复/空值/零成交/延迟）。"""
    rows = result.rows
    n_bars = len(rows)

    # 时间列原始名 "时间"；重复率 = 重复时间戳数 / 总数。
    raw_times = [str(r.get("时间")) for r in rows if r.get("时间") is not None]
    dup_count = len(raw_times) - len(set(raw_times)) if raw_times else 0
    dup_rate = (dup_count / n_bars) if n_bars else 0.0

    # 空值率：任一核心字段为空的行占比。
    null_rows = 0
    zero_vol = 0
    vol_raw = _CANONICAL_TO_RAW.get("volume")
    for r in rows:
        if any(r.get(_CANONICAL_TO_RAW.get(c)) is None for c in _CORE_CANONICAL):
            null_rows += 1
        if vol_raw is not None and _is_zero(r.get(vol_raw)):
            zero_vol += 1
    null_rate = (null_rows / n_bars) if n_bars else 0.0

    # 字段映射：响应里命中了哪些 canonical 字段。
    found_mapping = {
        raw: EM_COLUMN_MAP[raw] for raw in result.raw_columns if raw in EM_COLUMN_MAP
    }
    unmapped = [raw for raw in result.raw_columns if raw not in EM_COLUMN_MAP]

    # publication delay：probe 时刻 - 最晚 bar 时间（秒）；喂 RB2 p95+裕度。
    pub_delay = _publication_delay_seconds(result.source_asof_ts, probe_now)

    if result.error is not None and not rows:
        outcome = "error" if result.status_code != 200 else "empty"
    else:
        outcome = "ok"

    return {
        "symbol": sym.symbol,
        "asset_kind": sym.asset_kind,
        "label": sym.label,
        "liquidity_tier": sym.liquidity_tier,
        "interval_minutes": interval_minutes,
        "outcome": outcome,
        "n_bars": n_bars,
        "source_earliest_ts": _earliest_bar_iso(raw_times),
        "source_latest_ts": result.source_asof_ts,
        "dup_rate": round(dup_rate, 4),
        "null_rate": round(null_rate, 4),
        "zero_volume_bars": zero_vol,
        "status_code": result.status_code,
        "latency_ms": round(result.latency_ms, 1),
        "publication_delay_seconds": pub_delay,
        "schema_hash": schema_hash(result.raw_columns),
        "raw_columns": list(result.raw_columns),
        "field_mapping": found_mapping,
        "unmapped_columns": unmapped,
        "error": result.error,
    }


def _is_zero(val: Any) -> bool:
    try:
        return float(val) == 0.0
    except (TypeError, ValueError):
        return False


def _publication_delay_seconds(
    source_asof_ts: str | None, probe_now: datetime
) -> float | None:
    if not source_asof_ts:
        return None
    try:
        bar_end = datetime.fromisoformat(source_asof_ts)
    except ValueError:
        return None
    if bar_end.tzinfo is None:
        bar_end = bar_end.replace(tzinfo=_SHANGHAI_TZ)
    return round((probe_now - bar_end).total_seconds(), 1)


def _earliest_bar_iso(raw_times: list[str]) -> str | None:
    if not raw_times:
        return None
    from kss.data.intraday_client import _to_iso_shanghai

    return _to_iso_shanghai(min(raw_times))


def _percentile(values: list[float], pct: float) -> float | None:
    """最近邻插值百分位（无 numpy 依赖；空列表返回 None）。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 1)


def run_probe(
    provider: IntradayProvider,
    *,
    universe: tuple[RepresentativeSymbol, ...] = DEFAULT_PROBE_UNIVERSE,
    interval_minutes: int = 1,
    probe_now: datetime | None = None,
) -> dict[str, Any]:
    """对 universe 跑探针，产出机读报告 dict（可注入 provider，便于单测）。

    报告**不含**任何 token/凭据。provider 级 eligibility 用**实测可达性**
    （至少一标的返回行）重新分类，而非仅靠 capability() 的 import 近似。
    """
    probe_now = probe_now or datetime.now(tz=_SHANGHAI_TZ)

    symbol_reports: list[dict[str, Any]] = []
    latencies: list[float] = []
    pub_delays: list[float] = []
    http_429 = 0
    http_5xx = 0
    any_ok = False

    for sym in universe:
        result = provider.fetch_bars(
            sym.symbol,
            interval_minutes=interval_minutes,
            asset_kind=sym.asset_kind,
        )
        analyzed = _analyze_symbol(sym, result, interval_minutes, probe_now)
        symbol_reports.append(analyzed)
        latencies.append(result.latency_ms)
        if analyzed["publication_delay_seconds"] is not None:
            pub_delays.append(analyzed["publication_delay_seconds"])
        if analyzed["outcome"] == "ok":
            any_ok = True
        http_429 += _count_status(result.error, "429")
        http_5xx += _count_5xx(result.error)

    # provider 级分级：实测可达性优先（前向源永远 forward_observed，绝不 PIT）。
    provider_eligibility = classify_eligibility(provider.name, reachable=any_ok)

    capability = provider.capability()

    report = {
        "reportSchemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": {
            "name": provider.name,
            "version": provider.version,
            "supported_intervals": list(capability.supported_intervals),
            "supported_assets": list(capability.supported_assets),
            "max_history_days": capability.max_history_days,
            "notes": list(capability.notes),
        },
        "interval_minutes": interval_minutes,
        "reachable": any_ok,
        "eligibility": provider_eligibility.value,
        "aggregate": {
            "symbols_total": len(universe),
            "symbols_ok": sum(1 for s in symbol_reports if s["outcome"] == "ok"),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "http_429_count": http_429,
            "http_5xx_count": http_5xx,
            "publication_delay_samples": pub_delays,
            "publication_delay_p95_seconds": _percentile(pub_delays, 0.95),
        },
        "symbols": symbol_reports,
        # Tushare 历史准入：仅分类、不建读路径（D1）。无冻结证据 → research_only。
        # 完整 entitlement/quota/correction + proxy 公式冻结是阶段-6 gate（Follow-Up）。
        "tushare_historical": _classify_tushare_historical_deferred(),
    }
    return report


def _classify_tushare_historical_deferred() -> dict[str, Any]:
    """Tushare 历史 PIT 的保守分类（证据未冻结 → research_only，绝非 PIT）。"""
    eligibility = classify_eligibility(
        "tushare",
        reachable=True,
        has_entitlement=None,
        requested_history_ok=None,
        correction_policy_known=None,
        has_frozen_manifest_evidence=None,
        has_availability_proxy=None,
    )
    return {
        "eligibility": eligibility.value,
        "evidence": {
            "has_entitlement": None,
            "requested_history_ok": None,
            "correction_policy_known": None,
            "has_frozen_manifest_evidence": None,
            "has_availability_proxy": None,
        },
        "notes": [
            "历史分钟 PIT 准入是阶段-6 gate（Follow-Up，D1）；本探针不建历史读路径",
            "缺冻结证据 → research_only；entitlement/quota/correction 须阶段-6 复核",
        ],
    }


def _count_status(error: str | None, code: str) -> int:
    return 1 if error and code in error else 0


def _count_5xx(error: str | None) -> int:
    if not error:
        return 0
    return 1 if any(f"5{d}" in error for d in ("0", "1", "2", "3")) else 0


def _write_atomic(report: dict[str, Any], output_path: Path) -> None:
    """原子写报告（同盘 rename，参考 build_data_catalog._write_atomic）。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, output_path)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="分时供应商探针（先验数据源）")
    p.add_argument(
        "--report",
        required=True,
        help="机读探针报告输出路径（JSON，原子写）",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=1,
        help="探测的分钟周期（默认 1）",
    )
    p.add_argument(
        "--provider",
        default="eastmoney_akshare",
        choices=["eastmoney_akshare"],
        help="provider 名（U1 仅东财前向适配）",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    if args.provider == "eastmoney_akshare":
        provider: IntradayProvider = EastmoneyAkshareProvider()
    else:  # pragma: no cover — choices 已约束
        print(f"[FATAL] 未知 provider: {args.provider}", file=sys.stderr)
        return 2

    report = run_probe(provider, interval_minutes=args.interval)
    output_path = Path(args.report)
    _write_atomic(report, output_path)

    agg = report["aggregate"]
    print(
        f"[ok] 探针报告 → {output_path}  "
        f"provider={report['provider']['name']} "
        f"eligibility={report['eligibility']} "
        f"ok={agg['symbols_ok']}/{agg['symbols_total']}"
    )
    # fail-loud：前向源完全不可达 = 环境/上游坏，非零退出（不静默成功）。
    if not report["reachable"]:
        print(
            "[FATAL] 前向源零标的成功 —— 检查 akshare/网络/系统代理",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
