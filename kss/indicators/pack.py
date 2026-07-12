"""通用 Signal Pack：读写 + 投影（AI回测/图表/复盘的唯一真源，见 mi_pack.py 母版）.

``kind=primitive`` 条目走本模块的通用 pack schema；``kind=mi_legacy`` 条目委托给
``kss.strategies.mi_pack``（MI 专属数学与存储路径原地不动，零数据迁移）。
消费方（U5/U6/U7）一律走本模块的 ``run_any_pack``/``read_any_pack``/``to_any_*``
统一入口，不需要关心底层是哪种 kind。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from kss.backtest.indicator_walk_forward import WFConfig, WFResult, reestimate
from kss.indicators.primitives import (
    FAMILY_BOLL_ATR,
    FAMILY_MA_CROSS,
    FAMILY_RSI_THRESHOLD,
)
from kss.indicators.registry import KIND_MI_LEGACY, KIND_PRIMITIVE, RegistryEntry, state_root

SCHEMA_VERSION = 1
DEFAULT_RULES: dict[str, Any] = {"defaults": {}, "symbols": {}}
_SERIES_MAX_POINTS = 400


def entry_signals_root(entry: RegistryEntry, root: Path | None = None) -> Path:
    return (root or state_root()) / entry.signals_dir


def entry_rules_path(entry: RegistryEntry, root: Path | None = None) -> Path:
    return (root or state_root()) / entry.rules_path


def load_rules(entry: RegistryEntry, root: Path | None = None) -> dict[str, Any]:
    path = entry_rules_path(entry, root)
    if not path.exists():
        return dict(DEFAULT_RULES)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = dict(DEFAULT_RULES)
    if "defaults" in data:
        out["defaults"] = {**out["defaults"], **data["defaults"]}
    if "symbols" in data and isinstance(data["symbols"], dict):
        out["symbols"] = data["symbols"]
    return out


def load_ohlcv(symbol: str, root: Path | None = None) -> pd.DataFrame | None:
    """与 mi_pack.load_ohlcv 对齐：优先根目录 cs_data_{code}.csv，其次 cs_data/."""
    root = root or state_root()
    code = symbol.split(".")[0]
    for p in (root / f"cs_data_{code}.csv", root / "cs_data" / f"cs_data_{code}.csv"):
        if p.exists():
            df = pd.read_csv(p)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            for c in ("open", "high", "low", "close"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            return (
                df.sort_values("trade_date")
                .drop_duplicates("trade_date")
                .reset_index(drop=True)
            )
    return None


def write_pack(entry: RegistryEntry, pack: dict[str, Any], *, root: Path | None = None) -> Path:
    """写入 asof 目录与 latest 拷贝."""
    signals_root = entry_signals_root(entry, root)
    asof = pack["asof"]
    d = signals_root / asof
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{pack['symbol']}.json"
    text = json.dumps(pack, ensure_ascii=False, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    ld = signals_root / "latest"
    ld.mkdir(parents=True, exist_ok=True)
    (ld / f"{pack['symbol']}.json").write_text(text, encoding="utf-8")
    return path


def read_pack(
    entry: RegistryEntry, symbol: str, *, asof: str | None = None, root: Path | None = None
) -> dict[str, Any] | None:
    """读取信号包；symbol 支持裸代码自动试 .SH/.SZ/.BJ。"""
    signals_root = entry_signals_root(entry, root)
    candidates = [symbol]
    if "." not in symbol:
        candidates.extend([f"{symbol}.SH", f"{symbol}.SZ", f"{symbol}.BJ"])
    for cand in candidates:
        p = (
            (signals_root / asof / f"{cand}.json")
            if asof
            else (signals_root / "latest" / f"{cand}.json")
        )
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


_PRIMARY_SERIES_COLS: dict[str, tuple[str, ...]] = {
    FAMILY_MA_CROSS: ("ma_fast", "ma_slow"),
    FAMILY_RSI_THRESHOLD: ("rsi",),
    FAMILY_BOLL_ATR: ("boll_upper", "boll_mid", "boll_lower"),
}


def _primary_series(feat: pd.DataFrame, family: str) -> list[dict[str, Any]]:
    """族内主线序列（供图表 overlay 消费），截断至最近 400 点。"""
    cols = _PRIMARY_SERIES_COLS.get(family)
    if cols is None:
        raise ValueError(f"未知基元族: {family!r}")
    dates = feat["trade_date"].values
    col_values = {c: feat[c].values for c in cols}
    out: list[dict[str, Any]] = []
    for i, d in enumerate(dates):
        if pd.isna(d):
            continue
        point: dict[str, Any] = {"date": str(pd.Timestamp(d).date())}
        for c in cols:
            v = col_values[c][i]
            point[c] = None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), 4)
        out.append(point)
    return out[-_SERIES_MAX_POINTS:]


def build_pack_from_wf(
    symbol: str,
    asof: str,
    entry: RegistryEntry,
    wf: WFResult,
    *,
    unpinned: bool,
    prev_action: str | None = None,
    reference_trade_date: str | None = None,
) -> dict[str, Any]:
    """WFResult → pack dict（通用 schema，MI 超集）."""
    status = wf.status
    reason = wf.reason
    if status == "ok" and reference_trade_date and asof < reference_trade_date:
        status = "stale"
        reason = f"asof {asof} < reference {reference_trade_date}"

    rep = wf.replay or {}
    action = rep.get("action") or {}
    trades = rep.get("trades") or []
    preview = rep.get("trades_preview") or trades[-10:]
    series = _primary_series(rep["feat"], entry.family) if rep.get("feat") is not None else []

    return {
        "schema_version": SCHEMA_VERSION,
        "indicator_id": entry.id,
        "indicator_name": entry.name,
        "symbol": symbol,
        "asof": asof,
        "status": status,
        "reason": reason or action.get("reason", ""),
        "family": entry.family,
        "params": wf.best_params,
        "unpinned": unpinned,
        "action": action.get("action"),
        "prev_action": prev_action,
        "pred_score": action.get("pred_score"),
        "pred_bias": action.get("pred_bias"),
        "position": action.get("position"),
        "close": action.get("close"),
        "exec_note": action.get("exec_note", ""),
        "trades": trades,
        "trades_preview": preview,
        "series": series,
        "rule_sentence": rep.get("rule_sentence", ""),
        "param_delta": wf.param_delta,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def to_signal(pack: dict[str, Any]) -> dict[str, Any]:
    """桌面/复盘结构化字段（generic 版，对齐 mi_pack.to_mi_signal 字段形状）."""
    return {
        "indicatorId": pack.get("indicator_id"),
        "asof": pack.get("asof"),
        "status": pack.get("status"),
        "reason": pack.get("reason"),
        "action": pack.get("action"),
        "prevAction": pack.get("prev_action"),
        "position": pack.get("position"),
        "predScore": pack.get("pred_score"),
        "predBias": pack.get("pred_bias"),
        "family": pack.get("family"),
        "params": pack.get("params"),
        "unpinned": pack.get("unpinned"),
        "paramDelta": pack.get("param_delta"),
        "tradesPreview": pack.get("trades_preview"),
        "close": pack.get("close"),
        "ruleSentence": pack.get("rule_sentence"),
        "execNote": pack.get("exec_note"),
    }


def to_overlay(pack: dict[str, Any], history_dates: set[str] | None = None) -> dict[str, Any]:
    """图表 overlay 载荷（generic 版，对齐 mi_pack.to_mi_overlay markers 形状）."""
    status = pack.get("status", "missing")
    badge = {
        "indicatorId": pack.get("indicator_id"),
        "family": pack.get("family"),
        "params": pack.get("params") or {},
        "asof": pack.get("asof"),
        "unpinned": bool(pack.get("unpinned")),
    }
    if status != "ok":
        return {
            "status": status,
            "reason": pack.get("reason") or status,
            "banner": None,
            "badge": badge,
            "markers": [],
            "series": [],
        }

    def _in_hist(d: str | None) -> bool:
        if not d:
            return False
        return history_dates is None or str(d) in history_dates

    trade_src = list(pack.get("trades_preview") or []) or list(pack.get("trades") or [])
    markers: list[dict[str, Any]] = []
    for t in trade_src:
        bd, sd = t.get("signal_buy_date"), t.get("signal_sell_date")
        if _in_hist(bd):
            markers.append(
                {"time": str(bd), "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "买"}
            )
        if _in_hist(sd):
            markers.append(
                {"time": str(sd), "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": "卖"}
            )

    series = [p for p in (pack.get("series") or []) if _in_hist(p.get("date"))]

    return {
        "status": "ok",
        "reason": pack.get("reason") or "",
        "banner": {
            "action": pack.get("action"),
            "reason": pack.get("reason"),
            "predScore": pack.get("pred_score"),
            "unpinned": bool(pack.get("unpinned")),
        },
        "badge": badge,
        "markers": markers,
        "series": series,
    }


def format_section(pack: dict[str, Any]) -> str:
    """研究级 Markdown 段（generic 版，对齐 mi_pack.format_mi_section 排版）."""
    name = pack.get("indicator_name") or pack.get("indicator_id") or "指标"
    lines = [f"### {name} 信号", ""]
    if pack.get("status") != "ok":
        lines.append(f"- 状态: **{pack.get('status')}** — {pack.get('reason') or '无有效信号'}")
        if pack.get("unpinned"):
            lines.append("- ⚠️ **默认参数·未钉死**（请在对应 rules 文件确认）")
        lines.append("")
        return "\n".join(lines)

    if pack.get("unpinned"):
        lines.append("- ⚠️ **默认参数·未钉死**")
    lines.append(
        f"- 动作: **{pack.get('action')}** · 仓位 `{pack.get('position')}` · "
        f"pred `{pack.get('pred_score')}` ({pack.get('pred_bias')})"
    )
    lines.append(f"- 理由: {pack.get('reason')}")
    if pack.get("prev_action"):
        lines.append(f"- 相对昨动作: `{pack.get('prev_action')}` → `{pack.get('action')}`")
    if pack.get("rule_sentence"):
        lines.append(f"- 规则: {pack.get('rule_sentence')}")
    pdlt = pack.get("param_delta") or {}
    if pdlt:
        lines.append(f"- 相对上期参数: `{pdlt}`")
    lines.append(f"- asof: `{pack.get('asof')}` · 收盘 `{pack.get('close')}`")
    if pack.get("exec_note"):
        lines.append(f"- {pack.get('exec_note')}")
    lines.append("")
    preview = pack.get("trades_preview") or []
    if preview:
        lines.append("| 信号买 | 执行买 | 信号卖 | 执行卖 | 收益 | 持有 |")
        lines.append("|--------|--------|--------|--------|------|------|")
        for t in preview[-8:]:
            tr = t.get("trade_return")
            trs = "n/a" if tr is None else f"{tr * 100:+.2f}%"
            lines.append(
                f"| {t.get('signal_buy_date')} | {t.get('exec_buy_date')} | "
                f"{t.get('signal_sell_date')} | {t.get('exec_sell_date')} | {trs} | {t.get('hold_days')} |"
            )
        lines.append("")
    return "\n".join(lines)


def run_entry_pack(
    entry: RegistryEntry,
    symbol: str,
    *,
    asof: str | None = None,
    cfg: WFConfig | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """单票端到端：按 entry.kind 分派到通用引擎或 MI 专属引擎.

    kind=mi_legacy 委托 ``kss.strategies.mi_pack.run_symbol_pack``——真数不重算、
    存储路径不改，MI 的既有行为逐字段保持不变。
    """
    if entry.kind == KIND_MI_LEGACY:
        # MI 自带的 WFConfig（kss.backtest.mi_walk_forward）与本模块的通用 WFConfig
        # 字段不同（n_grid/entry_z_grid 等 MI 专属），故不透传 cfg——MI 用自己的默认值。
        from kss.strategies import mi_pack as _mi

        return _mi.run_symbol_pack(symbol, asof=asof, root=root)
    if entry.kind != KIND_PRIMITIVE:
        raise ValueError(f"未知 registry kind: {entry.kind!r}")

    root = root or state_root()
    code = symbol if "." in symbol else f"{symbol}.SH"
    df = load_ohlcv(code, root)
    if df is None or len(df) < 80:
        pack = {
            "schema_version": SCHEMA_VERSION,
            "indicator_id": entry.id,
            "indicator_name": entry.name,
            "symbol": code,
            "asof": asof or date.today().isoformat(),
            "status": "skipped",
            "reason": "无行情或样本过短",
            "family": entry.family,
            "params": entry.params,
            "unpinned": True,
            "action": None,
            "prev_action": None,
            "pred_score": None,
            "pred_bias": None,
            "position": None,
            "close": None,
            "exec_note": "",
            "trades": [],
            "trades_preview": [],
            "series": [],
            "rule_sentence": "",
            "param_delta": {},
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        write_pack(entry, pack, root=root)
        return pack

    ref = str(pd.Timestamp(df["trade_date"].iloc[-1]).date())
    resolved_asof = asof or ref
    prev = read_pack(entry, code, root=root)
    prev_action = (prev or {}).get("action") if prev else None

    wf = reestimate(df, entry.family, cfg=cfg or WFConfig())
    pack = build_pack_from_wf(
        code, resolved_asof, entry, wf, unpinned=False, prev_action=prev_action, reference_trade_date=ref
    )
    if pack["status"] == "stale" and resolved_asof == ref and wf.status == "ok":
        pack["status"] = "ok"
        pack["reason"] = (wf.replay or {}).get("action", {}).get("reason", "")
    write_pack(entry, pack, root=root)
    return pack


def read_any_pack(
    entry: RegistryEntry, symbol: str, *, asof: str | None = None, root: Path | None = None
) -> dict[str, Any] | None:
    """统一读入口：按 kind 分派到通用 read_pack 或 mi_pack.read_pack。"""
    if entry.kind == KIND_MI_LEGACY:
        from kss.strategies import mi_pack as _mi

        return _mi.read_pack(symbol, asof=asof, root=entry_signals_root(entry, root))
    return read_pack(entry, symbol, asof=asof, root=root)


def to_any_signal(entry: RegistryEntry, pack: dict[str, Any]) -> dict[str, Any]:
    if entry.kind == KIND_MI_LEGACY:
        from kss.strategies import mi_pack as _mi

        return _mi.to_mi_signal(pack)
    return to_signal(pack)


def to_any_overlay(
    entry: RegistryEntry, pack: dict[str, Any], history_dates: set[str] | None = None
) -> dict[str, Any] | None:
    if entry.kind == KIND_MI_LEGACY:
        from kss.strategies import mi_pack as _mi

        return _mi.to_mi_overlay(pack, history_dates)
    return to_overlay(pack, history_dates)


def format_any_section(entry: RegistryEntry, pack: dict[str, Any]) -> str:
    if entry.kind == KIND_MI_LEGACY:
        from kss.strategies import mi_pack as _mi

        return _mi.format_mi_section(pack)
    return format_section(pack)
