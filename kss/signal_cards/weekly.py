"""周报聚合与 markdown 渲染。复用 investment_analysis 确定性 helper。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from kss.config.paths import STATE_ROOT
from kss.research.investment_analysis import (
    AnalystMessage,
    PrecisionCard,
    QuoteSpan,
    _catalysts,
    _persistent_themes,
    _risk_severity,
    _temperature,
)
from kss.storage.reports_index import record_signal_weekly
from kss.storage.signal_cards import _to_compact, read_by_card_id, read_range


def _parse_compact(d: str) -> date:
    return datetime.strptime(d, "%Y%m%d").date()


def _dashed(d: str) -> str:
    c = _to_compact(d) if "-" in d else d
    return f"{c[:4]}-{c[4:6]}-{c[6:8]}"


def _stance_from_metrics(card: dict[str, Any]) -> int:
    """把信号卡转成 PrecisionCard.stance_label ∈ {-2,-1,0,1,2}。"""
    m = card.get("metrics") or {}
    ct = card["card_type"]
    if ct == "sector_move":
        pct = m.get("pctChange")
        if pct is None:
            return 0
        return 1 if float(pct) > 0 else -1
    if ct == "volume_spike":
        pct = m.get("pct_chg")
        if pct is None:
            return 0
        return 1 if float(pct) > 0 else -1
    if ct == "theme_leader":
        return 1
    if ct == "backtest_verdict":
        ic = m.get("ic_mean")
        if ic is None:
            return 0
        return 1 if float(ic) > 0 else -1
    if ct == "etf_flow":
        d = card.get("direction")
        if d == "hist_favorable":
            return 1
        if d == "hist_unfavorable":
            return -1
        return 0
    return 0


def _to_precision(card: dict[str, Any]) -> PrecisionCard:
    """薄适配：信号卡 → PrecisionCard，供 helper 复用。"""
    subject = str(card.get("subject") or "")
    ct = card["card_type"]
    stance = _stance_from_metrics(card)
    trade_date = card["trade_date"]
    # 用 card_type 充当 analyst_id，使「≥2 来源」= ≥2 卡类型
    analyst_id = ct
    msg = AnalystMessage(
        source_message_id=card["card_id"],
        analyst_id=analyst_id,
        published_at=trade_date,
        source_uri=f"signal_card:{ct}",
        content_hash=card["card_id"],
        provenance={"layer": "signal_cards"},
        content=None,
    )
    risk = None
    catalyst = None
    m = card.get("metrics") or {}
    if card.get("coverage") == "insufficient_data":
        risk = f"{ct}:insufficient_data"
    if ct == "etf_flow" and m.get("divergence"):
        risk = "etf_divergence_top"
    if ct == "theme_leader":
        catalyst = f"theme_leader:{subject}"
    return PrecisionCard(
        card_id=card["card_id"],
        evidence={"source_message_id": card["card_id"], "content_hash": card["card_id"]},
        source=msg,
        analyst={"analyst_id": analyst_id},
        trade_date=trade_date,
        instrument=subject,
        theme=ct,
        stance_label=stance,
        conviction="medium",  # type: ignore[arg-type]
        original_expression=f"{ct}:{subject}",
        risk=risk,
        catalyst=catalyst,
        date_anchor=None,
        evidence_grade="B",  # type: ignore[arg-type]
        quote_span=QuoteSpan(start=0, end=0, text=""),
        is_sellside_forward=False,
        extractor={"name": "signal_cards"},
        checker={"name": "deterministic"},
        metadata={
            "threshold_source": card.get("threshold_source"),
            "card_type": ct,
            "metrics": m,
        },
    )


def _persistence_groups(
    cards: list[dict[str, Any]],
    *,
    min_days: int = 3,
    min_sources: int = 2,
) -> list[dict[str, Any]]:
    """连续 ≥min_days 且 ≥min_sources(卡类型) 的 subject 聚合；附 card_ids。"""
    # subject → type → dates → cards
    by_subject: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for c in cards:
        by_subject[str(c.get("subject") or "")][c["card_type"]][c["trade_date"]].append(c)

    rows: list[dict[str, Any]] = []
    for subject, type_map in by_subject.items():
        if not subject:
            continue
        # 合并所有类型的交易日
        all_dates: set[str] = set()
        all_ids: list[str] = []
        sources: set[str] = set()
        for ct, date_map in type_map.items():
            sources.add(ct)
            for d, clist in date_map.items():
                all_dates.add(d)
                all_ids.extend(c["card_id"] for c in clist)
        # 也接受单来源但多日？计划要求 ≥2 来源；也可用 helper
        if len(all_dates) >= min_days and len(sources) >= min_sources:
            rows.append(
                {
                    "subject": subject,
                    "trade_days": sorted(all_dates),
                    "sources": sorted(sources),
                    "card_ids": all_ids,
                    "n_trade_days": len(all_dates),
                    "n_cards": len(all_ids),
                }
            )
        elif len(all_dates) >= min_days and len(sources) == 1:
            # 单来源连续 ≥3 日：仍记入，但标记 single_source（观察项可用）
            rows.append(
                {
                    "subject": subject,
                    "trade_days": sorted(all_dates),
                    "sources": sorted(sources),
                    "card_ids": all_ids,
                    "n_trade_days": len(all_dates),
                    "n_cards": len(all_ids),
                    "single_source": True,
                }
            )
    # 严格 N/M：默认要 ≥2 sources；single_source 仅当 min_sources==1
    if min_sources >= 2:
        rows = [r for r in rows if not r.get("single_source")]
    return sorted(rows, key=lambda r: (-r["n_trade_days"], r["subject"]))


def aggregate_week(
    start: str,
    end: str,
    *,
    db_path: str | Path | None = None,
    expected_trade_days: int | None = 5,
) -> dict[str, Any]:
    start_c = _to_compact(start) if "-" in start else start
    end_c = _to_compact(end) if "-" in end else end
    cards = read_range(start_c, end_c, db_path=db_path)

    covered_dates = sorted({c["trade_date"] for c in cards})
    expected = expected_trade_days or len(covered_dates) or 5

    # 估值不参与持续性
    non_val = [c for c in cards if c["card_type"] != "valuation"]
    # ETF 不进持续聚合（自相关），只进演变
    non_etf = [c for c in non_val if c["card_type"] != "etf_flow"]
    # 持续信号/观察项只用 covered：empty volume_ratio 等 insufficient_data
    # 可进演变/风险，不得冒充「连续 N 日信号」
    covered = [c for c in non_etf if c.get("coverage") == "covered"]

    backtested = [c for c in covered if c.get("threshold_source") == "backtested"]
    conventionish = [
        c
        for c in covered
        if c.get("threshold_source") in ("convention", "derived", "gated")
    ]

    persistent_signals = _persistence_groups(backtested, min_days=3, min_sources=2)
    # 观察项：允许单卡类型连续 ≥3 日
    persistent_observations = _persistence_groups(
        conventionish, min_days=3, min_sources=1
    )

    # 用 helper 做温度/风险/催化（适配后）
    precision = [_to_precision(c) for c in non_val]
    temperature = _temperature(precision) if precision else 0.0
    risks = _risk_severity(precision)
    # attach card_ids to risks
    risk_with_ids = []
    for r in risks:
        ids = [c.card_id for c in precision if c.risk == r["risk"]]
        risk_with_ids.append({**r, "card_ids": ids})
    catalysts = _catalysts(precision, period_end=_parse_compact(end_c))
    cat_with_ids = []
    for cat in catalysts:
        ids = [
            c.card_id
            for c in precision
            if c.catalyst == cat["catalyst"] and c.instrument == cat["instrument"]
        ]
        cat_with_ids.append({**cat, "card_ids": ids})

    # 信号演变：按日 × 类型
    evolution: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for c in cards:
        evolution[c["trade_date"]][c["card_type"]].append(
            {
                "card_id": c["card_id"],
                "subject": c.get("subject"),
                "direction": c.get("direction"),
                "win_rate": c.get("win_rate"),
                "effective_n": c.get("effective_n"),
                "hist_forward_ret": c.get("hist_forward_ret"),
                "dose_bucket": c.get("dose_bucket"),
                "coverage": c.get("coverage"),
                "metrics": c.get("metrics"),
                "regime_mismatch": c.get("regime_mismatch"),
            }
        )

    by_type_count = defaultdict(int)
    for c in cards:
        by_type_count[c["card_type"]] += 1

    return {
        "start": start_c,
        "end": end_c,
        "covered_dates": covered_dates,
        "coverage_label": f"{len(covered_dates)}/{expected} 交易日",
        "n_cards": len(cards),
        "n_trade_days": len(covered_dates),
        "by_type_count": dict(by_type_count),
        "temperature": temperature,
        "persistent_signals": persistent_signals,
        "persistent_observations": persistent_observations,
        "risks": risk_with_ids,
        "catalysts": cat_with_ids,
        "evolution": {d: dict(v) for d, v in sorted(evolution.items())},
        "has_valuation": by_type_count.get("valuation", 0) > 0,
        "regime_mismatch_note": any(c.get("regime_mismatch") for c in cards),
    }


def render_markdown(agg: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 信号卡周报 { _dashed(agg['start']) } ~ { _dashed(agg['end']) }")
    lines.append("")
    lines.append(f"- 覆盖：{agg['coverage_label']}（去重交易日 {agg['n_trade_days']}，卡片 {agg['n_cards']} 张）")
    lines.append(f"- 温度计（加权）：{agg['temperature']}")
    if agg.get("regime_mismatch_note"):
        lines.append(
            "- 注意：当前市场处于非动量期，历史胜率来自动量期回测，本期尚未校准。"
        )
    lines.append("")

    # 信号演变
    lines.append("## 信号演变")
    if not agg["evolution"]:
        lines.append("本周无信号卡。")
    else:
        for d, types in agg["evolution"].items():
            lines.append(f"### { _dashed(d) }")
            for ct, items in types.items():
                lines.append(f"- **{ct}**（{len(items)}）")
                for it in items[:20]:
                    bits = [f"`{it['card_id']}`", str(it.get("subject") or "")]
                    if ct == "etf_flow" and it.get("direction") is not None:
                        # 方向必须与胜率、n 同框
                        bits.append(
                            f"direction={it['direction']} "
                            f"win_rate={it.get('win_rate')} "
                            f"n={it.get('effective_n')} "
                            f"fwd={it.get('hist_forward_ret')} "
                            f"bucket={it.get('dose_bucket')}"
                        )
                    elif ct != "etf_flow":
                        # 非 ETF 不渲染方向词汇
                        m = it.get("metrics") or {}
                        if "pctChange" in m:
                            bits.append(f"pct={m.get('pctChange')}")
                        if "volume_ratio" in m:
                            bits.append(f"vr={m.get('volume_ratio')}")
                    lines.append("  - " + " | ".join(bits))
    lines.append("")

    # 持续信号（backtested，非 ETF）
    lines.append("## 持续信号")
    lines.append("（threshold_source=backtested，连续 ≥3 交易日且 ≥2 来源）")
    if not agg["persistent_signals"]:
        lines.append("本周无持续信号。")
    else:
        for row in agg["persistent_signals"]:
            lines.append(
                f"- **{row['subject']}** 日数={row['n_trade_days']} "
                f"来源={','.join(row['sources'])} "
                f"card_ids={','.join(row['card_ids'])}"
            )
    lines.append("")

    # 持续观察项
    lines.append("## 持续观察项")
    lines.append(
        "（threshold_source=convention/derived/gated；阈值未经回测，"
        "连续出现不代表统计显著）"
    )
    if not agg["persistent_observations"]:
        lines.append("本周无持续观察项。")
    else:
        for row in agg["persistent_observations"]:
            lines.append(
                f"- **{row['subject']}** 日数={row['n_trade_days']} "
                f"来源={','.join(row['sources'])} "
                f"card_ids={','.join(row['card_ids'])}"
            )
    lines.append("")

    # 风险雷达
    lines.append("## 风险雷达")
    if not agg["risks"]:
        lines.append("本周无风险雷达条目。")
    else:
        for r in agg["risks"]:
            lines.append(
                f"- {r['risk']} severity={r['severity']} "
                f"card_ids={','.join(r.get('card_ids') or [])}"
            )
    lines.append("")

    # 催化
    lines.append("## 催化跟踪")
    if not agg["catalysts"]:
        lines.append("本周无催化条目。")
    else:
        for c in agg["catalysts"]:
            lines.append(
                f"- {c['instrument']}: {c['catalyst']} [{c['status']}] "
                f"card_ids={','.join(c.get('card_ids') or [])}"
            )
    lines.append("")

    # 类型覆盖（缺类显式写「本周无 X」）
    lines.append("## 类型覆盖")
    for ct in (
        "etf_flow",
        "sector_move",
        "theme_leader",
        "volume_spike",
        "valuation",
        "backtest_verdict",
    ):
        n = agg["by_type_count"].get(ct, 0)
        if n == 0:
            if ct == "valuation":
                lines.append(f"- 本周无估值快照")
            else:
                lines.append(f"- 本周无 {ct}")
        else:
            lines.append(f"- {ct}: {n} 张")
    lines.append("")
    return "\n".join(lines)


def build_weekly_report(
    start: str,
    end: str,
    *,
    db_path: str | Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    agg = aggregate_week(start, end, db_path=db_path)
    md = render_markdown(agg)
    start_c = agg["start"]
    end_c = agg["end"]
    base = Path(out_dir) if out_dir is not None else Path(STATE_ROOT) / "storage" / "reports" / "signal_weekly"
    base.mkdir(parents=True, exist_ok=True)
    out_path = base / f"signal_weekly_{start_c}_{end_c}.md"
    out_path.write_text(md, encoding="utf-8")
    record_signal_weekly(
        out_path,
        report_name=out_path.name,
        category="signal_weekly",
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        db_path=db_path,
    )
    return out_path


def resolve_card_ids(
    card_ids: list[str], *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """下钻：返回库中存在的卡。"""
    out = []
    for cid in card_ids:
        c = read_by_card_id(cid, db_path=db_path)
        if c is not None:
            out.append(c)
    return out
