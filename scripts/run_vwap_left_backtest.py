#!/usr/bin/env python3
"""会话 VWAP 左侧策略：60 分钟真源 + 120 分钟由 60 分钟聚合，研究回测.

数据：Tushare ``stk_mins`` freq=60min（非 PIT canonical；eligibility=research_only）。
执行：信号 bar 收盘判定，次 bar 开盘成交；默认 ``t1_exit=True``（A 股股票 T+1）。
120 分钟不是上游原生周期，只从 60 分钟按上下午会话聚合。
"""

from __future__ import annotations

import json
import urllib.request
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kss.backtest.metrics import Metrics
from kss.data.minute_resample import normalize_stk_mins, resample_session_halves
from kss.data.tushare_client import TushareClient
from kss.indicators.gate import SLIPPAGE_BPS, judge
from kss.indicators.primitives import FAMILY_VWAP, param_grid
from kss.indicators.registry import state_root
from kss.indicators.rules import IndicatorSpec, compute_positions, extract_trades, warm_period

CACHE_DIR = ROOT / "storage" / "research" / "vwap_intraday"
REPORT_DIR = ROOT / "storage" / "reports" / "indicator_lab"
START = "2025-01-02 09:30:00"
END = "2026-09-02 15:00:00"
WATCHLIST = ["688017.SH", "688322.SH"]
EXTRA = ["688008.SH", "688012.SH", "688111.SH", "688036.SH"]
SYMBOLS = WATCHLIST + EXTRA
RATE_LIMIT_SECONDS = 0.8
ROUND_TRIP_SLIP = (SLIPPAGE_BPS / 10000.0) * 2


def _ts_code(symbol: str) -> str:
    s = symbol.strip().upper()
    if "." in s:
        return s
    return f"{s}.SH"


def _cache_path(ts_code: str) -> Path:
    return CACHE_DIR / f"{ts_code.replace('.', '_')}_60min.csv"


def load_daily_ohlcv(ts_code: str) -> pd.DataFrame | None:
    """日线 OHLCV。优先 Application Support / ``KSS_STATE_ROOT``，成交额千元→元、成交量手→股。"""
    code = ts_code.split(".")[0]
    roots = []
    env_root = state_root()
    roots.append(env_root)
    if env_root != ROOT:
        roots.append(ROOT)
    apps = Path.home() / "Library" / "Application Support" / "KSS"
    if apps not in roots:
        roots.append(apps)
    path = None
    for root in roots:
        for cand in (root / f"cs_data_{code}.csv", root / "cs_data" / f"cs_data_{code}.csv"):
            if cand.exists():
                path = cand
                break
        if path is not None:
            break
    if path is None:
        return None
    df = pd.read_csv(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    vol = pd.to_numeric(df["vol"] if "vol" in df.columns else df.get("volume"), errors="coerce")
    amt = pd.to_numeric(df["amount"], errors="coerce") if "amount" in df.columns else None
    # Tushare daily: vol=手, amount=千元 → 股 / 元，使 amount/volume 落在价格量纲。
    df["volume"] = vol * 100.0
    if amt is not None:
        df["amount"] = amt * 1000.0
    return (
        df.sort_values("trade_date")
        .drop_duplicates("trade_date")
        .reset_index(drop=True)
    )



def _sina_symbol(ts_code: str) -> str:
    code, _, suf = ts_code.partition(".")
    suf = suf.upper() or "SH"
    prefix = "sh" if suf in {"SH", "SS"} else "sz"
    return f"{prefix}{code}"


def fetch_sina_60m(ts_code: str, *, datalen: int = 1023) -> pd.DataFrame | None:
    """新浪 60 分钟 K（研究源）。``datalen`` 上限约 1023 根（约 1 年）。无成交额，VWAP 用 HLC3。"""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData"
        f"?symbol={_sina_symbol(ts_code)}&scale=60&ma=no&datalen={datalen}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"{ts_code} sina http error: {exc}", flush=True)
        return None
    if not isinstance(payload, list) or not payload:
        return None
    rows: list[dict[str, Any]] = []
    for item in payload:
        rows.append(
            {
                "ts_code": ts_code,
                "trade_time": str(item.get("day") or ""),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "vol": float(item.get("volume") or 0.0),
            }
        )
    return pd.DataFrame(rows)


def fetch_eastmoney_60m(ts_code: str, *, lmt: int = 4000) -> pd.DataFrame | None:
    """东财 push2his 60 分钟 K（研究源，非 PIT）.

    kline: 时间,开,收,高,低,成交量(手),成交额(元),...
    成交量改成股，与 Tushare stk_mins / 本仓库 VWAP=amount/volume 对齐。
    """
    code = ts_code.split(".")[0]
    suffix = ts_code.split(".")[-1].upper() if "." in ts_code else "SH"
    market = "1" if suffix in {"SH", "SS"} else "0"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=60&fqt=0&end=20500101&lmt={lmt}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"{ts_code} eastmoney http error: {exc}", flush=True)
        return None
    klines = ((payload or {}).get("data") or {}).get("klines") or []
    rows: list[dict[str, Any]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        ts = parts[0]
        if len(ts) == 16:
            ts = ts + ":00"
        vol_lot = float(parts[5])
        rows.append(
            {
                "ts_code": ts_code,
                "trade_time": ts,
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "vol": vol_lot * 100.0,  # 手 → 股
                "amount": float(parts[6]),
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows)


def load_or_fetch_60m(ts_code: str, client: TushareClient) -> pd.DataFrame | None:
    path = _cache_path(ts_code)
    if path.exists():
        df = pd.read_csv(path)
        if not df.empty:
            return df
    raw = fetch_sina_60m(ts_code)
    if raw is None or raw.empty:
        raw = fetch_eastmoney_60m(ts_code)
    if raw is None or raw.empty:
        # Tushare stk_mins 本账号约 1 次/小时，只作东财/新浪失败时的后备。
        raw = client.fetch_stk_mins(ts_code, freq="60min", start=START, end=END)
    if raw is None or raw.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(path, index=False)
    return raw


def _trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "win_rate": None,
            "avg_ret": None,
            "avg_ret_net": None,
            "profit_factor": None,
            "avg_hold_bars": None,
        }
    rets = np.array([float(t["trade_return"]) for t in trades if t.get("trade_return") is not None])
    if rets.size == 0:
        return {
            "n": len(trades),
            "win_rate": None,
            "avg_ret": None,
            "avg_ret_net": None,
            "profit_factor": None,
            "avg_hold_bars": None,
        }
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    holds = [int(t.get("hold_days") or 0) for t in trades]
    return {
        "n": int(rets.size),
        "win_rate": float((rets > 0).mean()),
        "avg_ret": float(rets.mean()),
        "avg_ret_net": float(rets.mean() - ROUND_TRIP_SLIP),
        "profit_factor": None if not np.isfinite(pf) else float(pf),
        "avg_hold_bars": float(np.mean(holds)) if holds else None,
    }


def _bar_metrics(feat: pd.DataFrame, warm: int) -> dict[str, float]:
    pos = feat["position"].iloc[warm:]
    ret = feat["ret"].iloc[warm:]
    strat = (pos * ret).dropna()
    m = Metrics.calc(strat) or {}
    return {
        "total": float(m.get("total", 0.0) or 0.0),
        "sharpe": float(m.get("sharpe", 0.0) or 0.0),
        "max_dd": float(m.get("max_dd", 0.0) or 0.0),
        "n_bars": int(len(strat)),
    }


def _params_key(params: dict[str, Any]) -> str:
    return (
        f"{params['rule_variant']}|entry={params['entry_dev_bps']}|"
        f"stop={params['stop_dev_bps']}|hold={params['max_hold_bars']}|"
        f"t1={int(bool(params['t1_exit']))}"
    )


def evaluate_symbol(
    df: pd.DataFrame, spec: IndicatorSpec
) -> dict[str, Any]:
    feat = compute_positions(df, spec)
    warm = warm_period(spec)
    trades = extract_trades(feat, feat["position"])
    stats = _trade_stats(trades)
    bar = _bar_metrics(feat, warm)
    n_days = int(pd.to_datetime(df["trade_date"]).dt.normalize().nunique())
    return {
        "trades": trades,
        "stats": stats,
        "bar": bar,
        "n_days": n_days,
        "n_bars": int(len(df)),
    }


def run_grid(frames: dict[str, pd.DataFrame], grid: list[dict[str, Any]], tf: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for params in grid:
        spec = IndicatorSpec(FAMILY_VWAP, params)
        pooled: list[dict[str, Any]] = []
        per_symbol: list[dict[str, Any]] = []
        for sym, df in frames.items():
            ev = evaluate_symbol(df, spec)
            pooled.extend(ev["trades"])
            per_symbol.append(
                {
                    "symbol": sym,
                    **ev["stats"],
                    **{f"bar_{k}": v for k, v in ev["bar"].items()},
                    "n_days": ev["n_days"],
                }
            )
        pooled_stats = _trade_stats(pooled)
        n_pos_symbols = sum(1 for r in per_symbol if (r.get("avg_ret") or 0) > 0)
        mean_sharpe = float(np.nanmean([r.get("bar_sharpe") for r in per_symbol]))
        rows.append(
            {
                "tf": tf,
                "key": _params_key(params),
                **params,
                **{f"pooled_{k}": v for k, v in pooled_stats.items()},
                "symbols_positive": n_pos_symbols,
                "n_symbols": len(per_symbol),
                "mean_bar_sharpe": mean_sharpe,
                "per_symbol": per_symbol,
            }
        )
    return pd.DataFrame(rows)


def _rank_left(df: pd.DataFrame) -> pd.DataFrame:
    """高胜率优先，但要求样本与滑点后均笔仍为正。"""
    out = df.copy()
    n = out["pooled_n"].fillna(0)
    wr = out["pooled_win_rate"]
    net = out["pooled_avg_ret_net"]
    # 分数：胜率 × sqrt(n) ，n<20 降权
    score = wr.fillna(0) * np.sqrt(n.clip(lower=0))
    score = np.where(n < 20, score * 0.25, score)
    score = np.where(net.fillna(-1) <= 0, score * 0.1, score)
    out["rank_score"] = score
    return out.sort_values("rank_score", ascending=False)


def _gate_row(frames: dict[str, pd.DataFrame], params: dict[str, Any]) -> dict[str, Any]:
    """在成交量最大的标的上跑五维门禁（分钟 bar 的年份用交易日计）。"""
    # 选 bar 数最多的票，避免空样本。
    sym = max(frames, key=lambda s: len(frames[s]))
    spec = IndicatorSpec(FAMILY_VWAP, params)
    verdict = judge(frames[sym], spec, grid=param_grid(FAMILY_VWAP))
    return {
        "symbol": sym,
        "go": verdict.go,
        "dims": [
            {"name": d.name, "passed": d.passed, "detail": d.detail, "value": d.value}
            for d in verdict.dimensions
        ],
    }


def write_report(
    *,
    coverage: list[dict[str, Any]],
    grid_60: pd.DataFrame,
    grid_120: pd.DataFrame,
    best_60: dict[str, Any],
    best_120: dict[str, Any],
    gate_60: dict[str, Any],
    gate_120: dict[str, Any],
    path: Path,
) -> None:
    def _fmt_grid(g: pd.DataFrame, n: int = 8) -> str:
        cols = [
            "rule_variant",
            "entry_dev_bps",
            "max_hold_bars",
            "t1_exit",
            "pooled_n",
            "pooled_win_rate",
            "pooled_avg_ret",
            "pooled_avg_ret_net",
            "pooled_profit_factor",
            "mean_bar_sharpe",
            "symbols_positive",
        ]
        show = g[cols].head(n).copy()
        show["pooled_win_rate"] = show["pooled_win_rate"].map(lambda x: None if pd.isna(x) else f"{100*x:.1f}%")
        show["pooled_avg_ret"] = show["pooled_avg_ret"].map(lambda x: None if pd.isna(x) else f"{100*x:.2f}%")
        show["pooled_avg_ret_net"] = show["pooled_avg_ret_net"].map(lambda x: None if pd.isna(x) else f"{100*x:.2f}%")
        show["pooled_profit_factor"] = show["pooled_profit_factor"].map(
            lambda x: None if pd.isna(x) else f"{x:.2f}"
        )
        show["mean_bar_sharpe"] = show["mean_bar_sharpe"].map(lambda x: None if pd.isna(x) else f"{x:.2f}")
        return show.to_markdown(index=False)

    def _gate_md(g: dict[str, Any]) -> str:
        lines = [f"- 裁决标的：`{g['symbol']}`  **{'GO' if g['go'] else 'NO-GO'}**"]
        for d in g["dims"]:
            mark = "PASS" if d["passed"] else "FAIL"
            lines.append(f"- {d['name']}: {mark} — {d['detail']}")
        return "\n".join(lines)

    cov_lines = [
        f"- `{c['symbol']}`: {c['n_60']} 根 60m / {c['n_120']} 根 120m，"
        f"{c['n_days']} 个交易日，{c['min_date']} ~ {c['max_date']}"
        for c in coverage
    ]
    md = f"""# 会话 VWAP 左侧 · 60m / 120m 研究回测

- 日期：2026-09-02
- 数据源：新浪 60min K（约 1023 根，`research_only`）；东财/Tushare 为后备。无成交额时会话 VWAP 用 HLC3×成交量
- 120 分钟：由 60 分钟按 A 股上午/下午会话聚合，**不是**上游原生周期
- 标的：自选 688017 / 688322 + 科创 688008 / 688012 / 688111 / 688036
- 窗口：{START[:10]} ~ {END[:10]}
- 执行：信号 bar 收盘判定 → 次 bar 开盘成交；默认 T+1（当日不可平）
- 成本：往返 {SLIPPAGE_BPS * 2:.0f} bp（与指标门禁 `SLIPPAGE_BPS=30` 一致）
- 左侧定义：
  - `dev_reclaim`：上一根收盘低于会话 VWAP 达阈值，本根收阳但仍未站上 VWAP
  - `close_dip`：15:00 收盘仍低于会话 VWAP 达阈值（更接近收盘左侧，次日开盘买）

## 覆盖

{chr(10).join(cov_lines)}

本地 `intraday_quotes.db` 的 `canonical_bars` 为空，东财/长桥本机不可达，故本报告不用分钟 canonical 层。

## 60 分钟网格（按高胜率分数排序，前 8）

{_fmt_grid(_rank_left(grid_60))}

首选：`{best_60.get('key')}`  
汇总胜率 **{(best_60.get('pooled_win_rate') or 0)*100:.1f}%**，n={best_60.get('pooled_n')}，
滑点后均笔 {(best_60.get('pooled_avg_ret_net') or 0)*100:.2f}% ，
利润因子 {best_60.get('pooled_profit_factor')}，
有正均笔收益的标的 {best_60.get('symbols_positive')}/{best_60.get('n_symbols')}。

### 60m 五维门禁（默认网格，裁决票=样本最长）

{_gate_md(gate_60)}

## 120 分钟网格（按高胜率分数排序，前 8）

{_fmt_grid(_rank_left(grid_120))}

首选：`{best_120.get('key')}`  
汇总胜率 **{(best_120.get('pooled_win_rate') or 0)*100:.1f}%**，n={best_120.get('pooled_n')}，
滑点后均笔 {(best_120.get('pooled_avg_ret_net') or 0)*100:.2f}% ，
利润因子 {best_120.get('pooled_profit_factor')}，
有正均笔收益的标的 {best_120.get('symbols_positive')}/{best_120.get('n_symbols')}。

### 120m 五维门禁

{_gate_md(gate_120)}

## 怎么读「高胜率左侧」

- 胜率单独看会偏向交易稀疏的格子；本表用 `win_rate × sqrt(n)`，n<20 或滑点后均笔≤0 降权。
- A 股股票 T+1：同一交易日 VWAP 回归**拿不到**。`t1_exit=True` 才是可交易口径。
- 未写入指标注册表、未固化到 App。P2 若 NO-GO，停在研究层。

## 网格全表 JSON

见同目录 `vwap_left_60_120_20260902.json`。
"""
    path.write_text(md, encoding="utf-8")


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    client = TushareClient()
    last_fetch = 0.0
    frames_60: dict[str, pd.DataFrame] = {}
    coverage: list[dict[str, Any]] = []

    for sym in SYMBOLS:
        ts_code = _ts_code(sym)
        path = _cache_path(ts_code)
        if not path.exists():
            wait = RATE_LIMIT_SECONDS - (time.time() - last_fetch)
            if last_fetch and wait > 0:
                print(f"rate-limit sleep {wait:.0f}s before {ts_code}", flush=True)
                time.sleep(wait)
            last_fetch = time.time()
        raw = load_or_fetch_60m(ts_code, client)
        if raw is None or raw.empty:
            print(f"SKIP {ts_code}: no 60m", flush=True)
            continue
        df60 = normalize_stk_mins(raw)
        start_d = pd.Timestamp(START[:10])
        end_d = pd.Timestamp(END[:10])
        df60 = df60[(df60["trade_date"] >= start_d) & (df60["trade_date"] <= end_d)].reset_index(drop=True)
        if df60.empty:
            print(f"SKIP {ts_code}: empty after normalize", flush=True)
            continue
        frames_60[ts_code] = df60
        df120 = resample_session_halves(df60)
        coverage.append(
            {
                "symbol": ts_code,
                "n_60": int(len(df60)),
                "n_120": int(len(df120)),
                "n_days": int(df60["trade_date"].nunique()),
                "min_date": str(df60["trade_date"].min().date()),
                "max_date": str(df60["trade_date"].max().date()),
            }
        )
        print(
            f"OK {ts_code}: {len(df60)} x 60m, {len(df120)} x 120m, "
            f"{coverage[-1]['min_date']}~{coverage[-1]['max_date']}",
            flush=True,
        )

    if len(frames_60) < 2:
        print("need at least 2 symbols", file=sys.stderr)
        return 1

    frames_120 = {s: resample_session_halves(df) for s, df in frames_60.items()}
    grid = param_grid(FAMILY_VWAP)
    # 诊断格：同默认参数但关闭 T+1（不可交易，只对照）
    diag = dict(grid[0])
    diag["t1_exit"] = False
    grid_plus = grid + [diag]

    g60 = run_grid(frames_60, grid_plus, "60m")
    g120 = run_grid(frames_120, grid_plus, "120m")
    ranked60 = _rank_left(g60[g60["t1_exit"] == True])  # noqa: E712
    ranked120 = _rank_left(g120[g120["t1_exit"] == True])  # noqa: E712
    best_60 = ranked60.iloc[0].to_dict()
    best_120 = ranked120.iloc[0].to_dict()
    best_60_params = {
        "rule_variant": best_60["rule_variant"],
        "entry_dev_bps": int(best_60["entry_dev_bps"]),
        "stop_dev_bps": int(best_60["stop_dev_bps"]),
        "max_hold_bars": int(best_60["max_hold_bars"]),
        "t1_exit": True,
    }
    best_120_params = {
        "rule_variant": best_120["rule_variant"],
        "entry_dev_bps": int(best_120["entry_dev_bps"]),
        "stop_dev_bps": int(best_120["stop_dev_bps"]),
        "max_hold_bars": int(best_120["max_hold_bars"]),
        "t1_exit": True,
    }
    gate_60 = _gate_row(frames_60, best_60_params)
    gate_120 = _gate_row(frames_120, best_120_params)

    report_md = REPORT_DIR / "vwap_left_60_120_20260902.md"
    report_json = REPORT_DIR / "vwap_left_60_120_20260902.json"
    write_report(
        coverage=coverage,
        grid_60=ranked60,
        grid_120=ranked120,
        best_60=best_60,
        best_120=best_120,
        gate_60=gate_60,
        gate_120=gate_120,
        path=report_md,
    )

    def _jsonable(df: pd.DataFrame) -> list[dict[str, Any]]:
        out = []
        for rec in df.to_dict(orient="records"):
            rec.pop("per_symbol", None)
            cleaned = {}
            for k, v in rec.items():
                if isinstance(v, (np.floating, float)):
                    cleaned[k] = None if not np.isfinite(v) else float(v)
                elif isinstance(v, (np.integer,)):
                    cleaned[k] = int(v)
                else:
                    cleaned[k] = v
            out.append(cleaned)
        return out

    payload = {
        "eligibility": "research_only",
        "source": "sina.getKLineData.scale60",
        "start": START,
        "end": END,
        "coverage": coverage,
        "best_60": {k: best_60[k] for k in best_60 if k != "per_symbol"},
        "best_120": {k: best_120[k] for k in best_120 if k != "per_symbol"},
        "gate_60": gate_60,
        "gate_120": gate_120,
        "grid_60": _jsonable(ranked60),
        "grid_120": _jsonable(ranked120),
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {report_md}")
    print(f"wrote {report_json}")
    print("BEST 60m", best_60["key"], "wr", best_60.get("pooled_win_rate"), "n", best_60.get("pooled_n"))
    print("BEST 120m", best_120["key"], "wr", best_120.get("pooled_win_rate"), "n", best_120.get("pooled_n"))
    print("GATE 60m", gate_60["go"], "GATE 120m", gate_120["go"])
    return 0


def _jsonable_grid(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for rec in df.to_dict(orient="records"):
        rec.pop("per_symbol", None)
        cleaned: dict[str, Any] = {}
        for k, v in rec.items():
            if isinstance(v, (np.floating, float)):
                cleaned[k] = None if not np.isfinite(v) else float(v)
            elif isinstance(v, (np.integer,)):
                cleaned[k] = int(v)
            else:
                cleaned[k] = v
        out.append(cleaned)
    return out


def run_daily() -> int:
    """同一套 VWAP 左侧网格跑日线，并对齐分钟样本窗口做对比。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    align_start = pd.Timestamp("2025-08-14")
    align_end = pd.Timestamp(END[:10])
    frames_full: dict[str, pd.DataFrame] = {}
    frames_align: dict[str, pd.DataFrame] = {}
    coverage: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        ts_code = _ts_code(sym)
        df = load_daily_ohlcv(ts_code)
        if df is None or df.empty:
            print(f"SKIP {ts_code}: no daily csv", flush=True)
            continue
        # 日线 VWAP 必须落在价格附近；否则单位换算错了。
        typical = df["amount"] / df["volume"].replace(0, np.nan)
        ratio = float((typical / df["close"]).median())
        print(f"OK {ts_code}: {len(df)} daily {df['trade_date'].min().date()}~{df['trade_date'].max().date()} vwap/close median={ratio:.3f}", flush=True)
        if not (0.9 <= ratio <= 1.1):
            print(f"WARN {ts_code}: daily VWAP not near close; check vol/amount units", flush=True)
        frames_full[ts_code] = df
        aligned = df[(df["trade_date"] >= align_start) & (df["trade_date"] <= align_end)].reset_index(drop=True)
        frames_align[ts_code] = aligned
        coverage.append(
            {
                "symbol": ts_code,
                "n_full": int(len(df)),
                "n_align": int(len(aligned)),
                "min_date": str(df["trade_date"].min().date()),
                "max_date": str(df["trade_date"].max().date()),
            }
        )
    if len(frames_align) < 2:
        print("need at least 2 daily symbols", file=sys.stderr)
        return 1

    grid = [p for p in param_grid(FAMILY_VWAP) if p.get("rule_variant") == "close_dip"]
    # 日线一根 bar 就是一个会话，dev_reclaim 要求 bars_in_session>=2，不会触发。
    g_align = _rank_left(run_grid(frames_align, grid, "1d-align"))
    g_full = _rank_left(run_grid(frames_full, grid, "1d-full"))
    best_align = g_align.iloc[0].to_dict()
    best_full = g_full.iloc[0].to_dict()
    best_params = {
        "rule_variant": best_align["rule_variant"],
        "entry_dev_bps": int(best_align["entry_dev_bps"]),
        "stop_dev_bps": int(best_align["stop_dev_bps"]),
        "max_hold_bars": int(best_align["max_hold_bars"]),
        "t1_exit": True,
    }
    gate_align = _gate_row(frames_align, best_params)
    gate_full = _gate_row(frames_full, best_params)

    prev = REPORT_DIR / "vwap_left_60_120_20260902.json"
    minute = json.loads(prev.read_text(encoding="utf-8")) if prev.exists() else {}

    def _row(label: str, rec: dict[str, Any]) -> str:
        wr = rec.get("pooled_win_rate")
        net = rec.get("pooled_avg_ret_net")
        gross = rec.get("pooled_avg_ret")
        pf = rec.get("pooled_profit_factor")
        wr_s = "" if wr is None else f"{100 * wr:.1f}%"
        g_s = "" if gross is None else f"{100 * gross:.2f}%"
        n_s = "" if net is None else f"{100 * net:.2f}%"
        pf_s = "" if pf is None else f"{pf:.2f}"
        return (
            f"| {label} | {rec.get('rule_variant')} | {rec.get('entry_dev_bps')} | "
            f"{rec.get('max_hold_bars')} | {rec.get('pooled_n')} | {wr_s} | {g_s} | {n_s} | {pf_s} |"
        )

    b60 = minute.get("best_60") or {}
    b120 = minute.get("best_120") or {}
    cov_lines = [
        f"- `{c['symbol']}`: 全日线 {c['n_full']} 根（{c['min_date']}~{c['max_date']}），对齐窗 {c['n_align']} 根"
        for c in coverage
    ]
    md = f"""# 日线 VWAP 左侧 vs 60m / 120m

- 日期：2026-09-02
- 日线源：Application Support `cs_data_*.csv`（Tushare daily；vol 手→股，amount 千元→元）
- 规则：与分钟同一族 `vwap.close_dip`（日线每天一根，`dev_reclaim` 不会触发）
- 对齐窗口：2025-08-14 ~ 2026-09-02（与新浪 60m 样本重合）
- 全日线窗口：各票 CSV 全长（约 2023-01-03 ~ 2026-09-02）
- 成本：往返 {SLIPPAGE_BPS * 2:.0f} bp；执行：收盘信号 → 次日开盘

## 覆盖

{chr(10).join(cov_lines)}

## 三周期对比（各周期自己的最高分格子，T+1 开）

| TF | rule | entry bp | hold bars | n | win | gross avg | net avg | PF |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
{_row("60m (prior)", b60)}
{_row("120m (prior)", b120)}
{_row("1d align", best_align)}
{_row("1d full", best_full)}

日线 `hold bars` 是交易日；分钟 `hold bars` 是 60m/120m 根数，持有日历跨度更短。

## 日线对齐窗网格（close_dip only）

{g_align[["rule_variant", "entry_dev_bps", "max_hold_bars", "pooled_n", "pooled_win_rate", "pooled_avg_ret", "pooled_avg_ret_net", "pooled_profit_factor", "symbols_positive"]].head(8).to_markdown(index=False)}

首选对齐窗：`{best_align.get("key")}`  
胜率 **{(best_align.get("pooled_win_rate") or 0) * 100:.1f}%**，n={best_align.get("pooled_n")}，滑点后均笔 {(best_align.get("pooled_avg_ret_net") or 0) * 100:.2f}%。

### 对齐窗五维（最长样本票）

- 裁决标的：`{gate_align["symbol"]}` **{"GO" if gate_align["go"] else "NO-GO"}**
{chr(10).join(f"- {d['name']}: {'PASS' if d['passed'] else 'FAIL'} — {d['detail']}" for d in gate_align["dims"])}

## 全日线网格

首选：`{best_full.get("key")}`  
胜率 **{(best_full.get("pooled_win_rate") or 0) * 100:.1f}%**，n={best_full.get("pooled_n")}，滑点后均笔 {(best_full.get("pooled_avg_ret_net") or 0) * 100:.2f}%。

### 全日线五维

- 裁决标的：`{gate_full["symbol"]}` **{"GO" if gate_full["go"] else "NO-GO"}**
{chr(10).join(f"- {d['name']}: {'PASS' if d['passed'] else 'FAIL'} — {d['detail']}" for d in gate_full["dims"])}

## 怎么读对比

- 分钟 `close_dip` 用的是**当日会话 VWAP**（15:00 vs 盘中累计均价）。
- 日线 `close_dip` 用的是**当日 VWAP**（收盘 vs 全日成交额/量）。两者都是「收在均价下方再隔夜」，不是滚动多日 VWAP。
- `dev_reclaim` 需要同一会话至少 2 根 bar，日线结构上不会开仓。
"""
    report_md = REPORT_DIR / "vwap_left_daily_20260902.md"
    report_json = REPORT_DIR / "vwap_left_daily_20260902.json"
    report_md.write_text(md, encoding="utf-8")
    payload = {
        "eligibility": "research_only",
        "source": "cs_data.daily",
        "align_start": "2025-08-14",
        "align_end": END[:10],
        "coverage": coverage,
        "best_align": {k: best_align[k] for k in best_align if k != "per_symbol"},
        "best_full": {k: best_full[k] for k in best_full if k != "per_symbol"},
        "gate_align": gate_align,
        "gate_full": gate_full,
        "grid_align": _jsonable_grid(g_align),
        "grid_full": _jsonable_grid(g_full),
        "minute_best_60": b60,
        "minute_best_120": b120,
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {report_md}")
    print(f"wrote {report_json}")
    print("BEST 1d align", best_align["key"], "wr", best_align.get("pooled_win_rate"), "n", best_align.get("pooled_n"), "net", best_align.get("pooled_avg_ret_net"))
    print("BEST 1d full", best_full["key"], "wr", best_full.get("pooled_win_rate"), "n", best_full.get("pooled_n"), "net", best_full.get("pooled_avg_ret_net"))
    print("GATE align", gate_align["go"], "GATE full", gate_full["go"])
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="只跑日线 VWAP 并与已有 60m/120m 结果对比")
    args = ap.parse_args()
    raise SystemExit(run_daily() if args.daily else main())
