#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MI 动量指标 · A 股真实数据回测.

数据源：本地 ``cs_data/cs_data_{symbol}.csv``（与 DataLoader 缓存同构）。
指标口径：通达信 / 天勤 tqsdk —— ``A=C_t-C_{t-N}``，``MI=SMA(A,N,1)``。

执行纪律（防 look-ahead）：
  - t 日收盘后用 close 计算 MI 并生成仓位信号
  - 收益用 open[t+2]/open[t+1]-1（与 FactorPipeline.next_day_return 一致）
  - 成本：默认买 0.1% / 卖 0.2%（CostModel）

策略：
  1. zero_cross  —— MI 上穿 0 做多，下穿 0 空仓（多头择时）
  2. a_cross_mi  —— a 线上穿 mi 线做多，下穿空仓
  3. buy_hold    —— 始终满仓对照

用法::

    .venv/bin/python scripts/backtest_mi.py
    .venv/bin/python scripts/backtest_mi.py --pool kcb50 --n 12
    .venv/bin/python scripts/backtest_mi.py --symbols 688008,688017,688322
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 允许从仓库根直接跑
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kss.backtest.cost_model import CostModel
from kss.backtest.metrics import Metrics
from kss.features.technical import TechnicalFactors

DEFAULT_POOL = "kcb50"
CACHE_DIR = ROOT / "cs_data"
REPORT_PATH = ROOT / "storage" / "reports" / "mi_backtest_report.md"


@dataclass
class StockResult:
    """单股回测结果."""

    symbol: str
    strategy: str
    n: int
    metrics: dict[str, Any]
    n_trades: int
    turnover: float
    n_days: int
    daily_net: pd.Series  # index = trade_date


def _normalize_symbol(raw: str) -> str:
    s = raw.strip().upper().replace(".SH", "").replace(".SZ", "")
    return s


def load_pool_symbols(pool: str) -> list[str]:
    """读取 ``{pool}_symbols.csv`` 的 symbol 列."""
    path = ROOT / f"{pool}_symbols.csv"
    if not path.exists():
        raise FileNotFoundError(f"股票池文件不存在: {path}")
    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        raise ValueError(f"{path} 缺少 symbol 列")
    return [_normalize_symbol(x) for x in df["symbol"].astype(str).tolist()]


def load_stock_csv(symbol: str) -> pd.DataFrame | None:
    """从 cs_data 缓存加载单股 OHLCV（离线，不打 tushare）."""
    path = CACHE_DIR / f"cs_data_{symbol}.csv"
    if not path.exists():
        # 兼容仓库根目录扁平缓存
        alt = ROOT / f"cs_data_{symbol}.csv"
        if not alt.exists():
            return None
        path = alt
    df = pd.read_csv(path)
    if "trade_date" not in df.columns or "close" not in df.columns:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("trade_date").drop_duplicates("trade_date").reset_index(drop=True)
    return df


def compute_next_day_return(df: pd.DataFrame) -> pd.Series:
    """t 日信号 → t+1 开盘建仓、t+2 开盘换仓的可达单日收益."""
    o = df["open"]
    return o.shift(-2) / o.shift(-1) - 1.0


def positions_from_zero_cross(mi: pd.Series) -> pd.Series:
    """MI > 0 持多，否则空仓；中间沿用上一日（ffill 后 0 填充）."""
    raw = pd.Series(np.nan, index=mi.index, dtype=float)
    raw.loc[mi > 0] = 1.0
    raw.loc[mi < 0] = 0.0
    # mi == 0 或 NaN 沿用
    return raw.ffill().fillna(0.0)


def positions_from_a_cross_mi(a: pd.Series, mi: pd.Series) -> pd.Series:
    """a > mi 持多，否则空仓."""
    raw = pd.Series(np.nan, index=mi.index, dtype=float)
    valid = a.notna() & mi.notna()
    raw.loc[valid & (a > mi)] = 1.0
    raw.loc[valid & (a < mi)] = 0.0
    return raw.ffill().fillna(0.0)


def apply_cost_to_positions(
    positions: pd.Series,
    ret: pd.Series,
    cost: CostModel,
) -> tuple[pd.Series, float, int]:
    """仓位 × 收益 − 换手成本 → 日净收益、累计换手、交易次数."""
    pos = positions.astype(float)
    r = ret.astype(float)
    turnover = pos.diff().abs()
    if len(pos):
        turnover.iloc[0] = abs(pos.iloc[0])
    delta = pos.diff()
    if len(pos):
        delta.iloc[0] = pos.iloc[0]
    cost_rate = pd.Series(0.0, index=pos.index)
    cost_rate.loc[delta > 0] = cost.buy_total
    cost_rate.loc[delta < 0] = cost.sell_total
    gross = pos * r
    net = gross - turnover * cost_rate
    mask = r.notna()
    net = net.where(mask)
    n_trades = int((delta.abs() > 1e-9).sum())
    return net, float(turnover.fillna(0).sum()), n_trades


def backtest_one(
    symbol: str,
    df: pd.DataFrame,
    n: int,
    strategy: str,
    cost: CostModel,
) -> StockResult | None:
    """单股单策略回测."""
    mi_dict = TechnicalFactors.mi(df["close"], periods=(n,))
    a = mi_dict[f"mi_a_{n}"]
    mi = mi_dict[f"mi_{n}"]
    ret = compute_next_day_return(df)

    if strategy == "zero_cross":
        pos = positions_from_zero_cross(mi)
    elif strategy == "a_cross_mi":
        pos = positions_from_a_cross_mi(a, mi)
    elif strategy == "buy_hold":
        pos = pd.Series(1.0, index=df.index)
    else:
        raise ValueError(f"未知策略: {strategy}")

    # 指标预热期：前 n+5 日强制空仓，避免半初始化 MI
    warm = n + 5
    if len(pos) > warm:
        pos.iloc[:warm] = 0.0

    net, turnover, n_trades = apply_cost_to_positions(pos, ret, cost)
    daily = pd.Series(net.values, index=df["trade_date"].values).dropna()
    if len(daily) < 30:
        return None
    m = Metrics.calc(daily)
    if not m:
        return None
    return StockResult(
        symbol=symbol,
        strategy=strategy,
        n=n,
        metrics=m,
        n_trades=n_trades,
        turnover=turnover,
        n_days=int(m.get("n", len(daily))),
        daily_net=daily,
    )


def equal_weight_portfolio(results: list[StockResult]) -> dict[str, Any]:
    """把多只股票日净收益对齐后等权合成组合."""
    if not results:
        return {}
    series_list = [r.daily_net.rename(r.symbol) for r in results]
    panel = pd.concat(series_list, axis=1, join="outer").sort_index()
    # 等权：当日有收益的股票取均值（缺失视为未交易，不参与分母）
    port = panel.mean(axis=1, skipna=True).dropna()
    m = Metrics.calc(port)
    m["n_stocks"] = len(results)
    m["coverage_mean"] = float(panel.notna().sum(axis=1).mean())
    return {"metrics": m, "daily": port, "panel": panel}


def summarize_table(results: list[StockResult]) -> pd.DataFrame:
    """单股结果汇总表."""
    rows = []
    for r in results:
        m = r.metrics
        rows.append({
            "symbol": r.symbol,
            "strategy": r.strategy,
            "n": r.n,
            "total": m.get("total", np.nan),
            "annual": m.get("annual", np.nan),
            "sharpe": m.get("sharpe", np.nan),
            "max_dd": m.get("max_dd", np.nan),
            "calmar": m.get("calmar", np.nan),
            "win": m.get("win", np.nan),
            "sortino": m.get("sortino", np.nan),
            "n_trades": r.n_trades,
            "turnover": r.turnover,
            "n_days": r.n_days,
        })
    return pd.DataFrame(rows)


def fmt_pct(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x * 100:.{digits}f}%"


def fmt_num(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:.{digits}f}"


def write_report(
    path: Path,
    pool: str,
    n: int,
    cost: CostModel,
    by_strategy: dict[str, list[StockResult]],
    portfolios: dict[str, dict[str, Any]],
    date_range: tuple[str, str],
) -> None:
    """写 Markdown 报告."""
    lines: list[str] = []
    lines.append("# MI 动量指标 A 股回测报告")
    lines.append("")
    lines.append("## 设定")
    lines.append("")
    lines.append(f"- 股票池: `{pool}`")
    lines.append(f"- MI 周期 N: `{n}`")
    lines.append(f"- 样本区间: `{date_range[0]}` → `{date_range[1]}`")
    lines.append(
        f"- 成本: 买 {cost.buy_total*100:.2f}% / 卖 {cost.sell_total*100:.2f}% "
        f"(含滑点 {cost.slippage_bps:.0f} bps)"
    )
    lines.append("- 执行: t 日收盘信号 → t+1 开盘建仓 → t+2 开盘换仓收益")
    lines.append("- 公式: `A=C_t-C_{t-N}`, `MI=(A+(N-1)*MI_prev)/N`")
    lines.append("")
    lines.append("## 等权组合绩效")
    lines.append("")
    lines.append("| 策略 | 股票数 | 总收益 | 年化 | 夏普 | 最大回撤 | Calmar | 日胜率 |")
    lines.append("|------|--------|--------|------|------|----------|--------|--------|")
    for name, pack in portfolios.items():
        m = pack.get("metrics") or {}
        lines.append(
            f"| {name} | {m.get('n_stocks', 0)} | {fmt_pct(m.get('total'))} | "
            f"{fmt_pct(m.get('annual'))} | {fmt_num(m.get('sharpe'))} | "
            f"{fmt_pct(m.get('max_dd'))} | {fmt_num(m.get('calmar'))} | "
            f"{fmt_pct(m.get('win'))} |"
        )
    lines.append("")

    for strat, results in by_strategy.items():
        if not results:
            continue
        table = summarize_table(results)
        lines.append(f"## 单股明细 · {strat}")
        lines.append("")
        lines.append(
            f"中位夏普 `{table['sharpe'].median():.2f}` · "
            f"夏普>0 占比 `{(table['sharpe'] > 0).mean()*100:.1f}%` · "
            f"中位年化 `{table['annual'].median()*100:.2f}%`"
        )
        lines.append("")
        top = table.nlargest(5, "sharpe")
        bot = table.nsmallest(5, "sharpe")
        lines.append("### Top5 夏普")
        lines.append("")
        lines.append("| symbol | 年化 | 夏普 | 最大回撤 | 交易次数 |")
        lines.append("|--------|------|------|----------|----------|")
        for _, row in top.iterrows():
            lines.append(
                f"| {row['symbol']} | {fmt_pct(row['annual'])} | "
                f"{fmt_num(row['sharpe'])} | {fmt_pct(row['max_dd'])} | "
                f"{int(row['n_trades'])} |"
            )
        lines.append("")
        lines.append("### Bottom5 夏普")
        lines.append("")
        lines.append("| symbol | 年化 | 夏普 | 最大回撤 | 交易次数 |")
        lines.append("|--------|------|------|----------|----------|")
        for _, row in bot.iterrows():
            lines.append(
                f"| {row['symbol']} | {fmt_pct(row['annual'])} | "
                f"{fmt_num(row['sharpe'])} | {fmt_pct(row['max_dd'])} | "
                f"{int(row['n_trades'])} |"
            )
        lines.append("")

    lines.append("## 说明")
    lines.append("")
    lines.append(
        "- 历史回测不代表未来表现；A 股传统动量偏弱，短期反转常见。"
    )
    lines.append(
        "- 本报告为研究复现，不构成投资建议。"
    )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MI 动量指标 A 股回测")
    p.add_argument("--pool", default=DEFAULT_POOL, help="股票池名（对应 {pool}_symbols.csv）")
    p.add_argument("--symbols", default=None, help="逗号分隔代码，覆盖 pool")
    p.add_argument("--n", type=int, default=12, help="MI 周期 N，默认 12")
    p.add_argument("--scan-n", default="6,9,12,20", help="参数扫描 N 列表（逗号分隔）；空则只跑 --n")
    p.add_argument("--buy-cost", type=float, default=0.001)
    p.add_argument("--sell-cost", type=float, default=0.002)
    p.add_argument("--slippage-bps", type=float, default=0.0)
    p.add_argument("--report", default=str(REPORT_PATH))
    p.add_argument("--no-scan", action="store_true", help="关闭 N 扫描，只跑 --n")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbols:
        symbols = [_normalize_symbol(s) for s in args.symbols.split(",") if s.strip()]
        pool_label = f"custom({len(symbols)})"
    else:
        symbols = load_pool_symbols(args.pool)
        pool_label = args.pool

    cost = CostModel(
        buy_cost=args.buy_cost,
        sell_cost=args.sell_cost,
        slippage_bps=args.slippage_bps,
    )
    strategies = ("zero_cross", "a_cross_mi", "buy_hold")

    # 加载行情
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = load_stock_csv(sym)
        if df is None or len(df) < 80:
            print(f"  skip {sym}: 无缓存或样本过短")
            continue
        frames[sym] = df
    if not frames:
        print("❌ 无可用股票数据")
        return 1

    min_d = min(df["trade_date"].min() for df in frames.values())
    max_d = max(df["trade_date"].max() for df in frames.values())
    date_range = (str(min_d.date()), str(max_d.date()))
    print(f"📊 MI 回测 | 池={pool_label} | 股票={len(frames)} | 区间={date_range[0]}→{date_range[1]}")
    print(f"   成本 买{cost.buy_total*100:.2f}% / 卖{cost.sell_total*100:.2f}% | 主 N={args.n}")

    # ---- 主 N 全策略 ----
    by_strategy: dict[str, list[StockResult]] = {s: [] for s in strategies}
    for sym, df in frames.items():
        for strat in strategies:
            res = backtest_one(sym, df, args.n, strat, cost)
            if res is not None:
                by_strategy[strat].append(res)

    portfolios: dict[str, dict[str, Any]] = {}
    print("\n=== 等权组合 (N={}) ===".format(args.n))
    print(f"{'策略':<14} {'年化':>8} {'夏普':>7} {'最大回撤':>9} {'日胜率':>8} {'股票':>5}")
    for strat in strategies:
        pack = equal_weight_portfolio(by_strategy[strat])
        portfolios[strat] = pack
        m = pack.get("metrics") or {}
        print(
            f"{strat:<14} {fmt_pct(m.get('annual')):>8} {fmt_num(m.get('sharpe')):>7} "
            f"{fmt_pct(m.get('max_dd')):>9} {fmt_pct(m.get('win')):>8} "
            f"{m.get('n_stocks', 0):>5}"
        )

    # 单股中位统计
    print("\n=== 单股中位统计 (zero_cross) ===")
    zc = summarize_table(by_strategy["zero_cross"])
    if not zc.empty:
        print(
            f"中位夏普 {zc['sharpe'].median():.2f} | "
            f"夏普>0 {(zc['sharpe']>0).mean()*100:.1f}% | "
            f"中位年化 {zc['annual'].median()*100:.2f}% | "
            f"中位最大回撤 {zc['max_dd'].median()*100:.2f}% | "
            f"中位交易次数 {zc['n_trades'].median():.0f}"
        )
        print("Top5 夏普:")
        for _, row in zc.nlargest(5, "sharpe").iterrows():
            print(
                f"  {row['symbol']}  年化{row['annual']*100:+6.1f}%  "
                f"夏普{row['sharpe']:5.2f}  回撤{row['max_dd']*100:6.1f}%  "
                f"交易{int(row['n_trades'])}"
            )

    # ---- N 参数扫描（仅 zero_cross 等权组合）----
    if not args.no_scan and args.scan_n.strip():
        ns = [int(x) for x in args.scan_n.split(",") if x.strip()]
        print("\n=== N 扫描 · zero_cross 等权组合 ===")
        print(f"{'N':>4} {'年化':>8} {'夏普':>7} {'最大回撤':>9} {'日胜率':>8}")
        for n in ns:
            batch: list[StockResult] = []
            for sym, df in frames.items():
                res = backtest_one(sym, df, n, "zero_cross", cost)
                if res is not None:
                    batch.append(res)
            pack = equal_weight_portfolio(batch)
            m = pack.get("metrics") or {}
            print(
                f"{n:>4} {fmt_pct(m.get('annual')):>8} {fmt_num(m.get('sharpe')):>7} "
                f"{fmt_pct(m.get('max_dd')):>9} {fmt_pct(m.get('win')):>8}"
            )

    report_path = Path(args.report)
    write_report(
        report_path,
        pool=pool_label,
        n=args.n,
        cost=cost,
        by_strategy=by_strategy,
        portfolios=portfolios,
        date_range=date_range,
    )
    print(f"\n✅ 报告已写入 {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
