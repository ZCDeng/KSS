#!/usr/bin/env python3
"""U6 截 2 —— 离线管道级 alpha / IC 估算（只读计算，不改管道，不自动改权重）.

把四发现管道的历史归档命中股票，对齐 cs_data 前向收益，逐管道算：
  - 截面 Rank-IC 序列 → 复用 U3 ``compute_ic_snapshot``（有效 n=去重交易日数、
    ICIR、t_stat、半衰期，全部代码确定性渲染）；
  - top-quintile 等权前向收益 - 全候选等权（管道内基准）= top_quintile_alpha。

产物：
  - ``storage/pipeline_alpha/{run_date}.json`` —— 每管道 ic_mean/ic_std/icir/
    sample_n/top_quintile_alpha + #8 双源仲裁动作（回测先验可注入）。
  - **绝不**自动写 ``storage/pipeline_weights.json``（更新走人工确认，见 R4 / Key Decision）。

#8 双源（Global Decision #8）：管道 alpha 走「回测先验（U5 CPCV，可注入）+ 实盘后验
（本脚本算的命中前向收益 IC）」双源仲裁。回测先验通过 ``--cpcv-prior <json>`` 注入
（格式 ``{pipeline_id: [ic_mean, icir]}``，对齐 ``CPCVBacktestICSource.from_factor_ic``）；
缺失时升权路径保守不放行（仅供参考/降权）。

数据可行性（fail loud）：归档样本极薄（实测 log_mv=4 / bj50=3 期）→ 有效 n 远低于
``realized_ic_min_n``，仲裁判 ``insufficient_n``「仅供参考」，脚本照算照写但在
``sample_n`` / ``feasible`` 字段显式标注不可信，不静默假装可信。

用法::

    .venv-desktop/bin/python scripts/compute_pipeline_alpha.py --horizon 5
    .venv-desktop/bin/python scripts/compute_pipeline_alpha.py --cpcv-prior storage/cpcv_cache/prior.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.factor_health import (  # noqa: E402
    ArbitrationInput,
    arbitrate,
    compute_ic_snapshot,
    load_thresholds,
)

PAPER_DIR = PROJECT_ROOT / "storage" / "paper_trade"
BJ_SCAN_DIR = PROJECT_ROOT / "storage" / "reports" / "bj50_scan"
SECTOR_DIR = PROJECT_ROOT / "storage" / "sector_rotation"
ALPHA_DIR = PROJECT_ROOT / "storage" / "pipeline_alpha"


# --------------------------------------------------------------------------- #
# 前向收益（cs_data，PIT：买 T+1 open，卖 T+(1+hold) open）
# --------------------------------------------------------------------------- #

def _read_cs(symbol: str) -> pd.DataFrame | None:
    """读 cs_data_{code}.csv（裸 6 位码）。缺失 → None。"""
    code6 = symbol.split(".")[0]
    path = PROJECT_ROOT / f"cs_data_{code6}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype={"trade_date": str})
    except Exception:
        return None
    if "trade_date" not in df.columns:
        return None
    return df.sort_values("trade_date").reset_index(drop=True)


def _forward_return(symbol: str, prediction_date: str, hold: int) -> float | None:
    """等权入场前向收益：T+1 open 买、T+(1+hold) open 卖（PIT，不看预测日当日）。"""
    df = _read_cs(symbol)
    if df is None:
        return None
    future = df[df["trade_date"] > prediction_date]
    if len(future) < hold + 1:
        return None
    try:
        entry = float(future.iloc[0]["open"])
        exit_price = float(future.iloc[hold]["open"])
    except (KeyError, ValueError, TypeError):
        return None
    if entry == 0 or pd.isna(entry) or pd.isna(exit_price):
        return None
    return exit_price / entry - 1.0


# --------------------------------------------------------------------------- #
# 管道历史命中读取 → {prediction_date: [(ts_code, score), ...]}
# --------------------------------------------------------------------------- #

def _hits_log_mv() -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for fp in sorted(PAPER_DIR.glob("*.json")):
        try:
            log = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = log.get("prediction_date")
        if not date:
            continue
        rows = []
        for p in log.get("picks", []):
            code = p.get("symbol")
            fv = p.get("factor_value")
            if code is None or fv is None:
                continue
            # log_mv 反向：score = -factor_value（小市值高分），符号与选股方向一致
            rows.append((str(code), -float(fv)))
        if rows:
            out[date] = rows
    return out


def _hits_bj50() -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for fp in sorted(BJ_SCAN_DIR.glob("scan_*.csv")):
        stem = fp.stem.replace("scan_", "")
        if len(stem) != 8:
            continue
        date = f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if "ts_code" not in df.columns or "total_score" not in df.columns:
            continue
        rows = [
            (str(r["ts_code"]), float(r["total_score"]))
            for _, r in df.iterrows()
            if pd.notna(r.get("ts_code")) and pd.notna(r.get("total_score"))
        ]
        if rows:
            out[date] = rows
    return out


def _hits_sector_hotspot() -> dict[str, list[tuple[str, float]]]:
    """板块热点：从归档 leaderStocks 展开成分股（无 leaderStocks → 当日无命中）。"""
    out: dict[str, list[tuple[str, float]]] = {}
    for fp in sorted(SECTOR_DIR.glob("*.json")):
        try:
            snap = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = snap.get("tradeDate") or fp.stem
        rows: list[tuple[str, float]] = []
        seen: set[str] = set()
        boards = (snap.get("leaderBoards") or [])
        for key in ("industries", "concepts"):
            boards = boards + (snap.get(key) or [])
        for b in boards:
            heat = b.get("heatScore")
            for s in b.get("leaderStocks") or []:
                code = (s.get("code") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                rows.append((code, float(heat) if heat is not None else 0.0))
        if rows:
            out[str(date)] = rows
    return out


PIPELINE_HIT_READERS = {
    "log_mv": _hits_log_mv,
    "bj50_scan": _hits_bj50,
    "sector_hotspot": _hits_sector_hotspot,
    # supply_chain 无逐日历史归档（curated 静态名单）→ 不算 IC（fail loud below）
}


# --------------------------------------------------------------------------- #
# per-pipeline IC + top-quintile alpha
# --------------------------------------------------------------------------- #

def _daily_rank_ic(
    hits: dict[str, list[tuple[str, float]]], horizon: int
) -> tuple[pd.Series, list[float], list[float]]:
    """逐日截面 Rank-IC + (top-quintile 收益, 全候选收益) 配对收集.

    Returns:
        (ic_series index=date, top_q_returns, all_returns)
    """
    ic_by_date: dict[str, float] = {}
    top_q_rets: list[float] = []
    all_rets: list[float] = []
    for date in sorted(hits):
        pairs = []  # (score, fwd_return)
        for code, score in hits[date]:
            fwd = _forward_return(code, date, horizon)
            if fwd is None:
                continue
            pairs.append((score, fwd))
        if len(pairs) < 2:
            continue
        sub = pd.DataFrame(pairs, columns=["score", "ret"])
        ic = sub["score"].corr(sub["ret"], method="spearman")
        if pd.notna(ic):
            ic_by_date[date] = float(ic)
        # top-quintile（至少 1 只）等权收益
        n_top = max(1, int(round(len(sub) * 0.2)))
        top = sub.sort_values("score", ascending=False).head(n_top)
        top_q_rets.append(float(top["ret"].mean()))
        all_rets.append(float(sub["ret"].mean()))
    return pd.Series(ic_by_date), top_q_rets, all_rets


def compute_pipeline_alpha(
    pipeline_id: str,
    hits: dict[str, list[tuple[str, float]]],
    horizon: int,
    thresholds: dict[str, Any],
    cpcv_prior: dict[str, tuple[float, float]] | None,
) -> dict[str, Any]:
    """单管道 IC + top-quintile alpha + #8 双源仲裁动作（纯计算）."""
    ic_series, top_q, all_q = _daily_rank_ic(hits, horizon)
    if ic_series.empty:
        return {
            "pipeline_id": pipeline_id,
            "feasible": False,
            "reason": "无足够前向收益对齐样本（cs_data 缺失或归档过薄）",
            "sample_n": 0,
        }
    snap = compute_ic_snapshot(
        pipeline_id, ic_series.index.max(), ic_series,
        thresholds=thresholds, source="realized",
    )
    top_q_alpha = (
        float(pd.Series(top_q).mean() - pd.Series(all_q).mean())
        if top_q and all_q else None
    )

    # #8 双源仲裁：实盘后验（本脚本 IC）+ 回测先验（CPCV 可注入）
    prior = cpcv_prior.get(pipeline_id) if cpcv_prior else None
    bt_ic_mean = float(prior[0]) if prior is not None else None
    bt_icir = float(prior[1]) if prior is not None else None
    verdict = arbitrate(
        ArbitrationInput(
            realized_icir=snap.icir,
            realized_ic_mean=snap.ic_mean,
            realized_n=snap.n_periods,
            backtest_ic_mean=bt_ic_mean,
            backtest_icir=bt_icir,
        ),
        thresholds,
    )
    min_n = int(thresholds["realized_ic_min_n"])
    return {
        "pipeline_id": pipeline_id,
        "feasible": True,
        "horizon": horizon,
        "ic_mean": round(snap.ic_mean, 6),
        "ic_std": round(snap.ic_std, 6),
        "icir": round(snap.icir, 6),
        "ic_t_stat": round(snap.ic_t_stat, 4),
        "ic_positive_rate": round(snap.ic_positive_rate, 4),
        "sample_n": snap.n_periods,                       # 去重交易日数（有效 n）
        "min_n_for_trust": min_n,
        "trustworthy": snap.n_periods >= min_n,           # n 不足 → 仅供参考
        "top_quintile_alpha": round(top_q_alpha, 6) if top_q_alpha is not None else None,
        "backtest_prior": {"ic_mean": bt_ic_mean, "icir": bt_icir} if prior else None,
        "arbitration": {"verdict": verdict.verdict, "reason": verdict.reason,
                        "divergence": verdict.divergence},
    }


def _load_cpcv_prior(path: str | None) -> dict[str, tuple[float, float]] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"--cpcv-prior 文件不存在: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    # 兼容 CPCV run 结果（含 factor_ic 键）或裸 {pipeline_id: [ic, icir]}
    table = raw.get("factor_ic", raw) if isinstance(raw, dict) else {}
    out: dict[str, tuple[float, float]] = {}
    for k, v in table.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out[str(k)] = (float(v[0]), float(v[1]))
    return out or None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="U6 截 2 管道 alpha/IC 离线估算（只读，不改权重）")
    ap.add_argument("--horizon", type=int, default=5, help="前向收益持有天数（T+1→T+1+hold）")
    ap.add_argument("--cpcv-prior", type=str, default=None,
                    help="回测 IC 先验 JSON（CPCV 产出或 {pipeline_id:[ic,icir]}），走 #8 仲裁")
    ap.add_argument("--out-date", type=str, default=None, help="产物文件名日期（默认今日 YYYYMMDD）")
    args = ap.parse_args(argv)

    thresholds = load_thresholds()  # 删 YAML 即 fail loud
    cpcv_prior = _load_cpcv_prior(args.cpcv_prior)

    results: list[dict[str, Any]] = []
    for pid, reader in PIPELINE_HIT_READERS.items():
        hits = reader()
        results.append(compute_pipeline_alpha(pid, hits, args.horizon, thresholds, cpcv_prior))
    # supply_chain：无逐日历史归档 → 显式标注不可算（fail loud，不静默跳过）
    results.append({
        "pipeline_id": "supply_chain",
        "feasible": False,
        "reason": "supply_chain 为 curated 静态名单，无逐日历史归档，无法算前向 IC；"
                  "需先补每日命中归档脚本（OQ-2 同源缺口）",
        "sample_n": 0,
    })

    run_date = args.out_date or datetime.now().strftime("%Y%m%d")
    payload = {
        "runDate": run_date,
        "horizon": args.horizon,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cpcvPriorInjected": cpcv_prior is not None,
        "note": "只读计算，绝不自动改 pipeline_weights.json；权重更新走人工确认（R4）。",
        "pipelines": results,
    }
    ALPHA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ALPHA_DIR / f"{run_date}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n[written] {out_path}", file=sys.stderr)
    print("[reminder] 权重更新需人工确认后手改 storage/pipeline_weights.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
