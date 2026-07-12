"""MI Signal Pack：规则加载、pack 读写与 UI 投影."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from kss.backtest.mi_walk_forward import WFConfig, WFResult, reestimate
from kss.strategies.mi_signal import RuleSpec

SCHEMA_VERSION = 1
DEFAULT_RULES: dict[str, Any] = {
    "defaults": {
        "entry": "mi_cross_up_0",
        "exit": "a_cross_dn_mi",
        "filter": "none",
    },
    "symbols": {},
}


def project_root() -> Path:
    """代码根（kss/ 所在目录）。bundle 内嵌脚本时指向 Resources，勿当 storage 根。"""
    env = os.environ.get("KSS_PROJECT_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def state_root() -> Path:
    """可变状态根（storage / cs_data）。bridge 注入 KSS_STATE_ROOT；缺省 = project_root。

    bundle 模式：代码在 .app/Resources，信号包写在 ~/…/KSS 或 breadcrumb 指向的仓库。
    读 pack / 写 pack / 行情 CSV 必须走 state_root，否则 UI 永远 miSignal=null。
    """
    env = os.environ.get("KSS_STATE_ROOT")
    if env:
        return Path(env)
    return project_root()


def _db_path(root: Path | None = None) -> Path:
    return (root or state_root()) / "storage" / "kss.db"


def load_rules(path: Path | None = None) -> dict[str, Any]:
    # 规则文件：先 state（用户钉死），再 project（bundle 内置默认）
    if path is None:
        for candidate in (
            state_root() / "storage" / "mi_rules.yaml",
            project_root() / "storage" / "mi_rules.yaml",
        ):
            if candidate.exists():
                path = candidate
                break
        else:
            path = state_root() / "storage" / "mi_rules.yaml"
    if not path.exists():
        return dict(DEFAULT_RULES)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = dict(DEFAULT_RULES)
    if "defaults" in data:
        out["defaults"] = {**out["defaults"], **data["defaults"]}
    if "symbols" in data and isinstance(data["symbols"], dict):
        out["symbols"] = data["symbols"]
    return out

def resolve_rule(
    symbol: str, rules: dict[str, Any]
) -> tuple[str, str, str, bool]:
    """返回 entry, exit, filter, unpinned."""
    code = symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    syms = rules.get("symbols") or {}
    if code in syms:
        r = syms[code]
        return (
            str(r.get("entry", rules["defaults"]["entry"])),
            str(r.get("exit", rules["defaults"]["exit"])),
            str(r.get("filter", rules["defaults"]["filter"])),
            False,
        )
    # 也试带后缀
    if symbol in syms:
        r = syms[symbol]
        return (
            str(r.get("entry", rules["defaults"]["entry"])),
            str(r.get("exit", rules["defaults"]["exit"])),
            str(r.get("filter", rules["defaults"]["filter"])),
            False,
        )
    d = rules["defaults"]
    return str(d["entry"]), str(d["exit"]), str(d["filter"]), True


def write_pack(
    pack: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> None:
    """写入 kss.db mi_signal_packs 表。db_path=None → 用 state_root() 派生默认路径."""
    from kss.storage.signal_packs import write_mi_pack

    write_mi_pack(pack, db_path or _db_path())


def read_pack(
    symbol: str,
    *,
    asof: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """读取信号包。``symbol`` 支持 ``688017.SH`` 或裸代码（自动试 .SH/.SZ/.BJ）."""
    from kss.storage.signal_packs import read_mi_pack

    resolved = db_path or _db_path()
    candidates = [symbol]
    if "." not in symbol:
        candidates.extend([f"{symbol}.SH", f"{symbol}.SZ", f"{symbol}.BJ"])
    for cand in candidates:
        pack = read_mi_pack(cand, asof, resolved)
        if pack is not None:
            return pack
    return None

def build_pack_from_wf(
    symbol: str,
    asof: str,
    wf: WFResult,
    *,
    entry: str,
    exit_: str,
    filt: str,
    unpinned: bool,
    prev_action: str | None = None,
    reference_trade_date: str | None = None,
) -> dict[str, Any]:
    """WFResult → pack dict."""
    status = wf.status
    reason = wf.reason
    if status == "ok" and reference_trade_date and asof < reference_trade_date:
        status = "stale"
        reason = f"asof {asof} < reference {reference_trade_date}"

    action = (wf.replay or {}).get("action") or {}
    trades = (wf.replay or {}).get("trades") or []
    preview = (wf.replay or {}).get("trades_preview") or trades[-10:]
    mi_series = (wf.replay or {}).get("mi_series") or []

    pack: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "asof": asof,
        "status": status,
        "reason": reason or action.get("reason", ""),
        "n": wf.best_n,
        "thr": wf.best_thr,
        "entry": entry,
        "exit": exit_,
        "filter": filt,
        "unpinned": unpinned,
        "action": action.get("action"),
        "prev_action": prev_action,
        "pred_score": action.get("pred_score"),
        "pred_bias": action.get("pred_bias"),
        "position": action.get("position"),
        "close": action.get("close"),
        "mi": action.get("mi"),
        "a": action.get("a"),
        "mi_z": action.get("mi_z"),
        "adx": action.get("adx"),
        "exec_note": action.get("exec_note"),
        "trades": trades,
        "trades_preview": preview,
        "mi_series": mi_series,
        "param_history": wf.param_history,
        "param_delta": wf.param_delta,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return pack


def to_mi_signal(pack: dict[str, Any]) -> dict[str, Any]:
    """桌面/复盘结构化字段."""
    return {
        "asof": pack.get("asof"),
        "status": pack.get("status"),
        "reason": pack.get("reason"),
        "action": pack.get("action"),
        "prev_action": pack.get("prev_action"),
        "position": pack.get("position"),
        "pred_score": pack.get("pred_score"),
        "pred_bias": pack.get("pred_bias"),
        "n": pack.get("n"),
        "thr": pack.get("thr"),
        "entry": pack.get("entry"),
        "exit": pack.get("exit"),
        "filter": pack.get("filter"),
        "unpinned": pack.get("unpinned"),
        "param_delta": pack.get("param_delta"),
        "trades_preview": pack.get("trades_preview"),
        "close": pack.get("close"),
        "mi": pack.get("mi"),
        "mi_z": pack.get("mi_z"),
        "adx": pack.get("adx"),
        "exec_note": pack.get("exec_note"),
    }


def to_mi_overlay(
    pack: dict[str, Any],
    history_dates: set[str] | None = None,
) -> dict[str, Any] | None:
    """图表 kssSetMiOverlay 载荷；非 ok 返回带 status 的空态.

    ``history_dates``：K 线可见交易日集合。有传入时只保留窗口内的 markers/MI，
    避免 LWC 因时间键对不上而整段静默不画。
    """
    status = pack.get("status", "missing")
    badge = {
        "n": pack.get("n"),
        "entry": pack.get("entry"),
        "exit": pack.get("exit"),
        "filter": pack.get("filter"),
        "asof": pack.get("asof"),
        "unpinned": bool(pack.get("unpinned")),
        "thr": pack.get("thr") or {},
    }
    if status != "ok":
        return {
            "status": status,
            "reason": pack.get("reason") or status,
            "banner": None,
            "badge": badge,
            "markers": [],
            "mi": [],
        }

    def _in_hist(d: str | None) -> bool:
        if not d:
            return False
        if history_dates is None:
            return True
        return str(d) in history_dates

    # 优先 preview，再回退全量 trades，保证窗口内有点可画
    trade_src = list(pack.get("trades_preview") or []) or list(pack.get("trades") or [])
    markers: list[dict[str, Any]] = []
    for t in trade_src:
        bd, sd = t.get("signal_buy_date"), t.get("signal_sell_date")
        if _in_hist(bd):
            markers.append(
                {
                    "time": str(bd),
                    "position": "belowBar",
                    "color": "#26a69a",
                    "shape": "arrowUp",
                    "text": "买",
                }
            )
        if _in_hist(sd):
            markers.append(
                {
                    "time": str(sd),
                    "position": "aboveBar",
                    "color": "#ef5350",
                    "shape": "arrowDown",
                    "text": "卖",
                }
            )

    mi = [
        {"time": str(p["date"]), "value": float(p["mi"])}
        for p in (pack.get("mi_series") or [])
        if p.get("mi") is not None and _in_hist(str(p.get("date")))
    ]
    # 控制体积：日线最多回传约一年半
    if len(mi) > 400:
        mi = mi[-400:]

    return {
        "status": "ok",
        "reason": pack.get("reason") or "",
        "banner": {
            "action": pack.get("action"),
            "reason": pack.get("reason"),
            "pred_score": pack.get("pred_score"),
            "unpinned": bool(pack.get("unpinned")),
        },
        "badge": badge,
        "markers": markers,
        "mi": mi,
    }


def format_mi_section(pack: dict[str, Any]) -> str:
    """研究级 Markdown 段."""
    lines = ["### MI 滚动信号", ""]
    if pack.get("status") != "ok":
        lines.append(
            f"- 状态: **{pack.get('status')}** — {pack.get('reason') or '无有效信号'}"
        )
        if pack.get("unpinned"):
            lines.append("- ⚠️ **默认形态·未钉死**（请在 mi_rules.yaml 确认）")
        lines.append("")
        return "\n".join(lines)

    if pack.get("unpinned"):
        lines.append("- ⚠️ **默认形态·未钉死**")
    lines.append(
        f"- 动作: **{pack.get('action')}** · 仓位 `{pack.get('position')}` · "
        f"pred `{pack.get('pred_score')}` ({pack.get('pred_bias')})"
    )
    lines.append(f"- 理由: {pack.get('reason')}")
    if pack.get("prev_action"):
        lines.append(
            f"- 相对昨动作: `{pack.get('prev_action')}` → `{pack.get('action')}`"
        )
    lines.append(
        f"- 参数: N=`{pack.get('n')}` · thr=`{pack.get('thr')}` · "
        f"{pack.get('entry')} / {pack.get('exit')} / {pack.get('filter')}"
    )
    pdlt = pack.get("param_delta") or {}
    if pdlt:
        lines.append(f"- 相对上期参数: `{pdlt}`")
    lines.append(f"- asof: `{pack.get('asof')}` · 收盘 `{pack.get('close')}`")
    lines.append(f"- {pack.get('exec_note') or ''}")
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
                f"{t.get('signal_sell_date')} | {t.get('exec_sell_date')} | "
                f"{trs} | {t.get('hold_days')} |"
            )
        lines.append("")
    return "\n".join(lines)


def load_ohlcv(symbol: str, root: Path | None = None) -> pd.DataFrame | None:
    """与 bridge 对齐：优先根目录 cs_data_{code}.csv，其次 cs_data/."""
    root = root or state_root()
    code = symbol.split(".")[0]
    for p in (
        root / f"cs_data_{code}.csv",
        root / "cs_data" / f"cs_data_{code}.csv",
    ):
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


def run_symbol_pack(
    symbol: str,
    *,
    asof: str | None = None,
    rules: dict[str, Any] | None = None,
    root: Path | None = None,
    cfg: WFConfig | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """单票端到端：加载 → WF → pack."""
    rules = rules or load_rules()
    root = root or state_root()
    resolved_db = db_path or _db_path(root)
    code = symbol if "." in symbol else f"{symbol}.SH"
    df = load_ohlcv(code, root)
    if df is None or len(df) < 80:
        return build_pack_from_wf(
            code,
            asof or date.today().isoformat(),
            WFResult(status="skipped", reason="无行情或样本过短"),
            entry="?",
            exit_="?",
            filt="?",
            unpinned=True,
        )

    entry, exit_, filt, unpinned = resolve_rule(code, rules)
    ref = str(pd.Timestamp(df["trade_date"].iloc[-1]).date())
    asof = asof or ref
    prev = read_pack(code, db_path=resolved_db)
    prev_action = (prev or {}).get("action") if prev else None

    wf = reestimate(df, entry, exit_, filt, cfg=cfg)
    pack = build_pack_from_wf(
        code,
        asof,
        wf,
        entry=entry,
        exit_=exit_,
        filt=filt,
        unpinned=unpinned,
        prev_action=prev_action,
        reference_trade_date=ref,
    )
    # 当日 asof 与行情末交易日一致时，ok 结果不标 stale
    if pack["status"] == "stale" and asof == ref and wf.status == "ok":
        pack["status"] = "ok"
        pack["reason"] = (wf.replay or {}).get("action", {}).get("reason", "")
    write_pack(pack, db_path=resolved_db)
    return pack
