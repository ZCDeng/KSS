#!/usr/bin/env python3
"""regime 分类器 60 日运行后 hit-rate 回测 (plan 008).

把每个 stage 期间的前向 N 日 HS300 收益分布算出来，对比预期方向，
出 hit-rate 矩阵报告.

**触发条件**: regime_daily.parquet 中 trade_date >= 20260525 (P1 上线后的数据)
的行数 >= 60. 不满足则脚本直接退出（让定时任务空跑安全）.

预期触发日期：约 2026-08-25 (60 个交易日后).

用法::

    python3 scripts/regime_hit_rate_backtest.py                 # 默认 HS300, 20d 前向
    python3 scripts/regime_hit_rate_backtest.py --index 000688.SH   # 用 KCB50 指数
    python3 scripts/regime_hit_rate_backtest.py --horizons 5,20,60  # 多窗口对比
    python3 scripts/regime_hit_rate_backtest.py --force             # 跳过触发条件强跑
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.data.tushare_client import TushareClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


REGIME_PARQUET = PROJECT_ROOT / "storage" / "macro" / "regime_daily.parquet"
INDEX_CACHE_DIR = PROJECT_ROOT / "storage" / "macro" / "index_cache"
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"

# 触发条件：P1 上线日期之后累计的真实运行数据数
P1_GO_LIVE = "20260525"
TRIGGER_MIN_DAYS = 60

# 阶段 → 预期方向（plan 008 §度量定义）
STAGE_EXPECTATION = {
    "I": ("up", 0.0),         # next_Nd_pct > 0
    "II": ("up_strong", 0.02),  # next_Nd_pct > +2%
    "III": ("down", 0.0),     # next_Nd_pct < 0
    "IV": ("down", 0.0),      # next_Nd_pct < 0
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--index", default="000300.SH", help="对比指数 ts_code (默认 HS300)")
    p.add_argument("--horizons", default="5,20,60",
                   help="前向窗口逗号分隔（交易日数）")
    p.add_argument("--force", action="store_true",
                   help="跳过触发条件强跑（用于开发 / 早期诊断）")
    p.add_argument("--report", type=str, default=None,
                   help="报告输出路径，默认 storage/reports/regime_hit_rate_<date>.md")
    return p.parse_args()


def check_trigger(regime_df: pd.DataFrame) -> tuple[bool, int]:
    """返回 (触发条件是否满足, P1 后实跑天数)."""
    if regime_df.empty or "trade_date" not in regime_df.columns:
        return False, 0
    post_golive = regime_df[regime_df["trade_date"].astype(str) >= P1_GO_LIVE]
    n = len(post_golive)
    return n >= TRIGGER_MIN_DAYS, n


def load_index_history(client: TushareClient, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """指数日线（带本地缓存）."""
    INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = INDEX_CACHE_DIR / f"{ts_code.replace('.', '_')}_{start}_{end}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    df = client.fetch_index_daily(ts_code, start, end)
    if df is None or df.empty:
        return pd.DataFrame()
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df


def compute_forward_returns(index_df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """对每行算 N 日前向收益（close[i+N] / close[i] - 1）."""
    out = index_df[["trade_date", "close"]].copy()
    for h in horizons:
        out[f"fwd_{h}d"] = (out["close"].shift(-h) / out["close"] - 1.0)
    return out


def compute_hit_rate(
    regime_df: pd.DataFrame,
    fwd_df: pd.DataFrame,
    horizon: int,
) -> dict[str, dict[str, float]]:
    """按 stage 分组算 hit-rate.

    Returns:
        ``{stage: {n: int, mean_fwd: float, hit_rate: float, expected: str}}``
    """
    merged = regime_df.merge(
        fwd_df[["trade_date", f"fwd_{horizon}d"]],
        on="trade_date", how="inner",
    )
    out: dict[str, dict[str, float]] = {}
    for stage in ("I", "II", "III", "IV"):
        sub = merged[merged["stage"] == stage].dropna(subset=[f"fwd_{horizon}d"])
        if sub.empty:
            out[stage] = {"n": 0, "mean_fwd": float("nan"), "hit_rate": float("nan"),
                          "expected": STAGE_EXPECTATION[stage][0]}
            continue
        expected_dir, threshold = STAGE_EXPECTATION[stage]
        if expected_dir == "up":
            hits = (sub[f"fwd_{horizon}d"] > threshold).sum()
        elif expected_dir == "up_strong":
            hits = (sub[f"fwd_{horizon}d"] > threshold).sum()
        elif expected_dir == "down":
            hits = (sub[f"fwd_{horizon}d"] < threshold).sum()
        else:
            hits = 0
        out[stage] = {
            "n": len(sub),
            "mean_fwd": float(sub[f"fwd_{horizon}d"].mean()),
            "hit_rate": float(hits / len(sub)),
            "expected": expected_dir,
        }
    return out


def render_report(
    results: dict[int, dict[str, dict[str, float]]],
    regime_df: pd.DataFrame,
    index_code: str,
    n_post_golive: int,
) -> str:
    """Markdown report."""
    lines = [
        f"# Regime Hit-Rate Backtest — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"- 指数: `{index_code}`",
        f"- regime panel: {len(regime_df)} 日 ({regime_df['trade_date'].min()} ~ {regime_df['trade_date'].max()})",
        f"- P1 上线后实跑天数: {n_post_golive}（触发条件 ≥{TRIGGER_MIN_DAYS}）",
        "",
        "## Hit-rate by stage × horizon",
        "",
    ]
    horizons = sorted(results.keys())
    header = "| Stage | Expected | " + " | ".join(f"{h}d hit / mean" for h in horizons) + " |"
    sep = "|" + "---|" * (2 + len(horizons))
    lines.append(header)
    lines.append(sep)
    for stage in ("I", "II", "III", "IV"):
        cells = [stage, STAGE_EXPECTATION[stage][0]]
        for h in horizons:
            r = results[h][stage]
            if r["n"] == 0:
                cells.append("— (n=0)")
            else:
                cells.append(f"{r['hit_rate']:.1%} / {r['mean_fwd']:+.2%} (n={r['n']})")
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend([
        "",
        "## 解读",
        "",
        f"- 基线 hit-rate ≈ 50% (随机方向)",
        f"- 总 hit-rate > 60% → prior 假设有效，可启用 plan 011 stage 切换告警",
        f"- 单阶段 hit-rate < 50% → 该阶段规则可能反了，需调 macro_regime.yaml",
        "",
        "## Stage 分布",
        "",
    ])
    stage_counts = regime_df["stage"].value_counts().to_dict()
    for stage, count in sorted(stage_counts.items()):
        lines.append(f"- {stage}: {count} 天 ({100*count/len(regime_df):.1f}%)")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if not REGIME_PARQUET.exists():
        logger.error("regime_daily.parquet 不存在；先跑 backfill_regime_history.py")
        return 1

    regime_df = pd.read_parquet(REGIME_PARQUET)
    regime_df["trade_date"] = regime_df["trade_date"].astype(str)

    triggered, n_post = check_trigger(regime_df)
    if not triggered and not args.force:
        logger.info(
            "触发条件未满足：P1 上线 (%s) 后实跑 %d 天 < %d。脚本空跑退出。",
            P1_GO_LIVE, n_post, TRIGGER_MIN_DAYS,
        )
        logger.info("预计触发日期：约 2026-08-25（用 --force 强跑）")
        return 0

    if not triggered:
        logger.warning("--force：触发条件未满足（n_post=%d），仍跑（结果仅供诊断）", n_post)

    horizons = [int(x) for x in args.horizons.split(",")]
    client = TushareClient()
    index_df = load_index_history(
        client, args.index,
        regime_df["trade_date"].min(),
        datetime.now().strftime("%Y%m%d"),
    )
    if index_df.empty:
        logger.error("指数 %s 拉取无数据", args.index)
        return 1

    fwd_df = compute_forward_returns(index_df, horizons)
    results = {h: compute_hit_rate(regime_df, fwd_df, h) for h in horizons}
    report_md = render_report(results, regime_df, args.index, n_post)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.report) if args.report else (
        REPORT_DIR / f"regime_hit_rate_{datetime.now().strftime('%Y%m%d')}.md"
    )
    out_path.write_text(report_md, encoding="utf-8")
    logger.info("报告落地: %s", out_path)
    print("\n" + report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
