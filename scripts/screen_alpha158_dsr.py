#!/usr/bin/env python3
"""Alpha158 集体 DSR 筛选 —— 在科创板 51 只面板上把 Qlib 因子库走第 7 层防御.

策略 family = ``"mined"`` (n_trials = 158)：必须经得起"用 158 个因子一起试"
的 selection bias 矫正，才能进入 KSS 主路径。

流程：

1. 加载 51 只科创板 ``cs_data_688*.csv``，逐股调 :meth:`Alpha158.generate`；
2. 拼成长 panel + 目标收益 (``future_return_5d`` / ``next_day_return``)；
3. :class:`SignalDiagnostics.cross_section_ic_scan` 跑全 158 因子 × 2 horizons IC；
4. 对每个 |t| ≥ 2 的因子做 :func:`factor_cross_section_backtest`（自动判 direction）；
5. :meth:`Significance.is_deployable` (strategy_family="mined", n_trials=158) 集体筛；
6. 与 KSS 既有 ``log_mv`` 反向 baseline 对比；
7. 输出 ``storage/reports/alpha158_screening.md``.

预期：DSR 通过的 < 5 个；这是 KSS 第 7 层 meta-bias 防御的目的——
158 个因子集体试时门槛会自动收紧，把多数"看起来好"的因子拦在门外.
"""

from __future__ import annotations

import glob
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.cost_model import CostModel  # noqa: E402
from kss.backtest.cross_section import factor_cross_section_backtest  # noqa: E402
from kss.backtest.diagnostics import SignalDiagnostics  # noqa: E402
from kss.backtest.significance import Significance  # noqa: E402
from kss.features.alpha158 import Alpha158  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402

# ---------------------------------------------------------------------- #
DATA_GLOB = str(PROJECT_ROOT / "cs_data_688*.csv")
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "alpha158_screening.md"

IC_HORIZON = "future_return_5d"
TOP_PCT = 0.2
MIN_STOCKS = 10
T_STAT_FOR_BACKTEST = 2.0       # 只对 |t| ≥ 此值的因子跑 backtest（省时间）

# DSR 多重检验矫正：跑 158 个因子，按 mined family 取 n_trials=158
N_TRIALS_DSR = 158

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据加载
# ---------------------------------------------------------------------- #


def load_alpha158_panel() -> pd.DataFrame:
    """加载 51 只科创板，逐股算 Alpha158，拼成长 panel."""
    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        raise FileNotFoundError(f"未找到 {DATA_GLOB}")
    logger.info("加载 %d 只股票", len(files))

    panels: list[pd.DataFrame] = []
    t0 = time.time()
    for i, fp_path in enumerate(files):
        try:
            df = pd.read_csv(fp_path)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date").reset_index(drop=True)
            if len(df) < 100:
                continue

            # Alpha158 因子（158 列）
            a158 = Alpha158.generate(df)
            a158["trade_date"] = df["trade_date"].values
            a158["symbol"] = df["ts_code"].iloc[0]

            # 标签：跟 FactorPipeline 一致 (future_return_Nd + next_day_return)
            fp = FactorPipeline(df)
            base = fp.add_targets(fp.generate(), df["close"])
            for lbl in ["future_return_5d", "future_return_10d", "next_day_return", "log_mv"]:
                if lbl in base.columns:
                    a158[lbl] = base[lbl].values

            panels.append(a158)
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 %s 失败: %s", fp_path, exc)

        if (i + 1) % 10 == 0:
            logger.info("  已处理 %d/%d 只", i + 1, len(files))

    panel = pd.concat(panels, ignore_index=True)
    panel = panel.dropna(subset=["next_day_return", "future_return_5d"])
    logger.info(
        "Panel %d 行 × %d 股票 × %d 因子，耗时 %.1fs",
        len(panel), panel["symbol"].nunique(),
        len([c for c in panel.columns if c not in ("trade_date", "symbol",
            "next_day_return", "future_return_5d", "future_return_10d", "log_mv")]),
        time.time() - t0,
    )
    return panel


# ---------------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------------- #


def fmt(x: float, d: int = 4) -> str:
    if pd.isna(x):
        return "—"
    return f"{x:+.{d}f}"


def fmt_pct(x: float) -> str:
    if pd.isna(x):
        return "—"
    return f"{x*100:+.1f}%"


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    panel = load_alpha158_panel()

    # 158 个因子列名（排除标签/标识列）
    exclude = {"trade_date", "symbol", "next_day_return", "future_return_5d",
               "future_return_10d", "log_mv", "close"}
    factor_cols = [c for c in panel.columns if c not in exclude]
    n_factors = len(factor_cols)
    logger.info("待筛因子数: %d", n_factors)

    # ============== 1. 截面 IC 扫描 158 因子 ============== #
    logger.info("[1] 跑 cross_section_ic_scan...")
    t0 = time.time()
    ic_scan = SignalDiagnostics.cross_section_ic_scan(
        panel,
        factor_cols,
        horizons=[IC_HORIZON, "next_day_return"],
        method="spearman",
    )
    logger.info("  耗时 %.1fs", time.time() - t0)

    ic_5d = ic_scan[ic_scan["horizon"] == IC_HORIZON].reset_index(drop=True)
    logger.info("  IC 5d Top10: %s",
                ic_5d.head(10)[["factor", "ic_mean", "ic_t_stat"]].to_dict("records"))

    # ============== 2. 对 |t| ≥ 2 的因子跑 backtest + DSR ============== #
    survivors = ic_5d[ic_5d["abs_t_stat"] >= T_STAT_FOR_BACKTEST].copy()
    logger.info("[2] |t| ≥ %.1f 的因子有 %d 个；逐个跑 cross_section_backtest + DSR",
                T_STAT_FOR_BACKTEST, len(survivors))

    cost = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)
    bt_results: list[dict] = []
    for _, row in survivors.iterrows():
        f = row["factor"]
        direction = "long_low" if row["ic_mean"] < 0 else "long_high"
        try:
            res = factor_cross_section_backtest(
                panel, f, cost, top_pct=TOP_PCT, direction=direction,
                min_stocks=MIN_STOCKS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest %s 失败: %s", f, exc)
            continue
        if res is None or len(res) < 30:
            continue

        net = res["net_return"]
        # 集体筛 DSR：n_trials = 158（mined family）
        deploy = Significance.is_deployable(
            net,
            strategy_family="mined",
            n_trials=N_TRIALS_DSR,
            return_details=True,
        )
        bt_results.append({
            "factor": f,
            "ic_mean": row["ic_mean"],
            "ic_t_stat": row["ic_t_stat"],
            "direction": direction,
            "sharpe": deploy["sharpe"],
            "p_value": deploy["p_value"],
            "dsr": deploy["dsr"],
            "passed": deploy["passed"],
            "annual_return": net.mean() * 252,
            "n_days": deploy["n"],
        })

    bt_df = (pd.DataFrame(bt_results)
             .sort_values("dsr", ascending=False)
             .reset_index(drop=True)) if bt_results else pd.DataFrame()
    passed = bt_df[bt_df["passed"]] if not bt_df.empty else pd.DataFrame()
    logger.info("[2 done] DSR 通过 (n_trials=%d): %d / %d",
                N_TRIALS_DSR, len(passed), len(bt_df))

    # ============== 3. log_mv 反向 baseline ============== #
    logger.info("[3] 跑 log_mv 反向 baseline 对照...")
    log_mv_row = None
    if "log_mv" in panel.columns and panel["log_mv"].notna().any():
        try:
            res_lm = factor_cross_section_backtest(
                panel, "log_mv", cost, top_pct=TOP_PCT,
                direction="long_low", min_stocks=MIN_STOCKS,
            )
            if res_lm is not None and len(res_lm) >= 30:
                d = Significance.is_deployable(
                    res_lm["net_return"],
                    strategy_family="mined", n_trials=N_TRIALS_DSR,
                    return_details=True,
                )
                # 同时算 single_factor family 下的 dsr 作"前 6 轮验证因子"参考
                d_prior = Significance.is_deployable(
                    res_lm["net_return"],
                    strategy_family="prior", n_trials=1,
                    return_details=True,
                )
                log_mv_row = {
                    "factor": "log_mv (反向, KSS baseline)",
                    "sharpe": d["sharpe"],
                    "p_value": d["p_value"],
                    "dsr_mined": d["dsr"],
                    "dsr_prior": d_prior["dsr"],
                    "passed_mined": d["passed"],
                    "passed_prior": d_prior["passed"],
                    "annual_return": res_lm["net_return"].mean() * 252,
                    "n_days": d["n"],
                }
                logger.info("  log_mv 反向: Sharpe=%.2f  DSR(mined,158)=%.3f  DSR(prior,1)=%.3f",
                            d["sharpe"], d["dsr"], d_prior["dsr"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("log_mv 回测失败: %s", exc)

    # ============== 4. 输出 Markdown ============== #
    render_md(panel, ic_5d, ic_scan, bt_df, passed, log_mv_row)

    # 终端摘要
    print("\n" + "=" * 78)
    print(f"Alpha158 集体 DSR 筛选 (n_trials={N_TRIALS_DSR}, mined family)")
    print("=" * 78)
    print(f"Panel: {len(panel):,} 行 × {panel['symbol'].nunique()} 股票 × {n_factors} 因子")
    print(f"|t| ≥ {T_STAT_FOR_BACKTEST}: {len(survivors)} 个；跑 backtest: {len(bt_df)} 个")
    print(f"DSR 通过 (n_trials={N_TRIALS_DSR}): **{len(passed)} 个**")
    if not passed.empty:
        print("\n通过名单:")
        for _, r in passed.iterrows():
            print(f"  ✓ {r['factor']:25s}  Sharpe={r['sharpe']:+.2f}  "
                  f"DSR={r['dsr']:+.3f}  IC={r['ic_mean']:+.4f} (t={r['ic_t_stat']:+.2f})  "
                  f"{r['direction']}")
    else:
        print("\n  无因子通过 mined-family DSR (n_trials=158)")
    print(f"\n报告: {MD_PATH}")


# ---------------------------------------------------------------------- #
# Markdown 渲染
# ---------------------------------------------------------------------- #


def render_md(
    panel: pd.DataFrame,
    ic_5d: pd.DataFrame,
    ic_scan_full: pd.DataFrame,
    bt_df: pd.DataFrame,
    passed: pd.DataFrame,
    log_mv_row: dict | None,
) -> None:
    lines: list[str] = []
    lines.append("# Alpha158 集体 DSR 筛选 —— KSS 第 7 层 meta-bias 防御")
    lines.append("")
    lines.append(
        f"- **数据集**: 51 只科创板 (`cs_data_688*.csv`), "
        f"{len(panel):,} 行 × {panel['symbol'].nunique()} 股票"
    )
    lines.append(
        f"- **区间**: {panel['trade_date'].min().date()} ~ {panel['trade_date'].max().date()}"
    )
    lines.append(f"- **因子库**: Qlib Alpha158 ({len([c for c in panel.columns if c not in ['trade_date','symbol','next_day_return','future_return_5d','future_return_10d','log_mv','close']])} 个, 9 KBAR + 4 价格 + 145 rolling)")
    lines.append(f"- **筛选 horizon**: `{IC_HORIZON}`")
    lines.append(f"- **DSR 矫正**: `strategy_family=\"mined\"`, n_trials = **{N_TRIALS_DSR}**")
    lines.append(f"- **门槛**: Sharpe ≥ 0.5, p-value < 0.05, DSR ≥ 0.4 (LdP 2014 默认)")
    lines.append("")
    lines.append(
        "**为什么集体筛**：KSS 第 7 层 meta-bias 教训——单个因子用 prior 矫正 "
        "(n_trials=1) 容易过；158 个因子一起试就必须按 mined family 矫正 "
        "(n_trials=158)，自然把多数 IC 噪声因子拦在门外."
    )
    lines.append("")

    # -------- IC 5d Top 30 + Bottom 5 ---------- #
    lines.append("## 1. 158 因子截面 IC (5d horizon)")
    lines.append("")
    lines.append("### 1.1 Top 30 by |t-stat|")
    lines.append("")
    lines.append("| # | 因子 | IC | t-stat | IR | IC>0 | n |")
    lines.append("|---|------|----|--------|----|------|---|")
    for i, row in ic_5d.head(30).iterrows():
        sig = " ★" if abs(row["ic_t_stat"]) >= 2 else ""
        sign = "🟢" if row["ic_mean"] > 0 else "🔴"
        lines.append(
            f"| {i+1} | `{row['factor']}` {sign}{sig} | "
            f"{fmt(row['ic_mean'])} | {row['ic_t_stat']:+.2f} | "
            f"{row['ir']:+.2f} | {row['ic_positive_rate']:.0%} | "
            f"{int(row['n'])} |"
        )
    lines.append("")
    if len(ic_5d) > 30:
        lines.append("### 1.2 Bottom 5 (最弱) by |t-stat|")
        lines.append("")
        lines.append("| 因子 | IC | t-stat | IR | n |")
        lines.append("|------|----|--------|----|---|")
        for _, row in ic_5d.tail(5).iterrows():
            lines.append(
                f"| `{row['factor']}` | {fmt(row['ic_mean'])} | "
                f"{row['ic_t_stat']:+.2f} | {row['ir']:+.2f} | {int(row['n'])} |"
            )
        lines.append("")

    sig_count = int((ic_5d["abs_t_stat"] >= 2.0).sum())
    lines.append(f"- **|t| ≥ 2 显著因子**: {sig_count} / {len(ic_5d)} "
                 f"({sig_count / len(ic_5d):.0%})")
    lines.append("")

    # -------- DSR 筛选结果 ---------- #
    lines.append("## 2. 集体 DSR 筛选结果")
    lines.append("")
    lines.append(
        f"对 |t-stat| ≥ {T_STAT_FOR_BACKTEST} 的因子 ({len(bt_df)} 个) 各跑一次 "
        f"`factor_cross_section_backtest`，再用 "
        f"`Significance.is_deployable(strategy_family=\"mined\", n_trials={N_TRIALS_DSR})` "
        "判定."
    )
    lines.append("")
    if not bt_df.empty:
        lines.append("### 2.1 全部回测结果（按 DSR 排序）")
        lines.append("")
        lines.append("| # | 因子 | direction | Sharpe | 年化 | p-value | DSR | 通过 |")
        lines.append("|---|------|-----------|--------|------|---------|-----|------|")
        for i, row in bt_df.iterrows():
            tick = "✅" if row["passed"] else "❌"
            lines.append(
                f"| {i+1} | `{row['factor']}` | {row['direction']} | "
                f"{row['sharpe']:+.2f} | {fmt_pct(row['annual_return'])} | "
                f"{row['p_value']:.3f} | {row['dsr']:+.3f} | {tick} |"
            )
        lines.append("")
    else:
        lines.append("无 |t| ≥ 2 的因子，跳过 backtest 阶段.")
        lines.append("")

    lines.append(f"### 2.2 通过 mined-family DSR 的因子: **{len(passed)}**")
    lines.append("")
    if passed.empty:
        lines.append(
            "**无因子通过** (n_trials=158 矫正下 DSR ≥ 0.4)。"
            "符合 KSS 第 7 层防御预期：158 个因子集体筛时，"
            "selection bias 矫正足以把所有看似显著的 IC 都打回噪声.")
    else:
        lines.append("| 因子 | direction | Sharpe | DSR | IC | t-stat |")
        lines.append("|------|-----------|--------|-----|----|--------|")
        for _, r in passed.iterrows():
            lines.append(
                f"| `{r['factor']}` | {r['direction']} | "
                f"{r['sharpe']:+.2f} | {r['dsr']:+.3f} | "
                f"{fmt(r['ic_mean'])} | {r['ic_t_stat']:+.2f} |"
            )
    lines.append("")

    # -------- log_mv baseline 对比 ---------- #
    lines.append("## 3. 与 KSS 既有 `log_mv` 反向 baseline 对比")
    lines.append("")
    if log_mv_row:
        lines.append(
            f"- **log_mv 反向** (KSS 历经 7 轮 bias 防御的 prior 因子):"
        )
        lines.append(
            f"  - Sharpe **{log_mv_row['sharpe']:+.2f}** "
            f"年化 {fmt_pct(log_mv_row['annual_return'])} "
            f"p-value {log_mv_row['p_value']:.3f}"
        )
        lines.append(
            f"  - DSR (prior, n_trials=1) **{log_mv_row['dsr_prior']:+.3f}** "
            f"→ {'✓ 通过' if log_mv_row['passed_prior'] else '✗ 未通过'}"
        )
        lines.append(
            f"  - DSR (mined, n_trials={N_TRIALS_DSR}) **{log_mv_row['dsr_mined']:+.3f}** "
            f"→ {'✓ 通过' if log_mv_row['passed_mined'] else '✗ 未通过'}"
        )
        lines.append("")
        if log_mv_row["passed_prior"] and not log_mv_row["passed_mined"]:
            lines.append(
                "**关键对比**：`log_mv` 在 prior family (n_trials=1) 下通过，"
                f"在 mined family (n_trials={N_TRIALS_DSR}) 下被拒——"
                "再次印证 strategy_family 决定生死。"
                "Alpha158 是 mined 家族（公开因子库 = 已被全行业试过），"
                "进入 KSS 必须按 mined 矫正，不能按 prior 给 free pass."
            )
    else:
        lines.append("log_mv 数据缺失，跳过对比.")
    lines.append("")

    # -------- 结论 ---------- #
    lines.append("## 4. 结论")
    lines.append("")
    n_pass = len(passed)
    if n_pass == 0:
        lines.append(
            "1. **零因子通过 mined-family DSR**：158 个 Qlib 因子在 51 只科创板上"
            f"集体筛 (n_trials={N_TRIALS_DSR}) 没有一个能过 DSR ≥ 0.4。"
            "这与 `qlib_paper_comparison.md` 第 4.1 节的预期一致——"
            "**因子库直接抄 = 大概率全军覆没**."
        )
    elif n_pass <= 5:
        lines.append(
            f"1. **少量 ({n_pass} 个) 因子通过 mined-family DSR**："
            "符合第 7 层预期 ('预期 < 5 个能活')。"
            "建议把这些因子加入 KSS 作 candidate，"
            "再走 walk-forward + 跨期 OOS 验证 (KSS 第 5/6 层) 二次确认."
        )
    else:
        lines.append(
            f"1. **{n_pass} 个因子通过——超出预期**。需深查："
            "(a) 是否样本期太短/区间幸存者偏差；"
            "(b) 因子间相关性是否过高（如 SUMP/SUMN/SUMD 同源）；"
            "(c) 是否 strategy_family 选错（应 mined 但被选 single_factor）."
        )
    lines.append("")
    if log_mv_row:
        if log_mv_row["passed_prior"] and not log_mv_row["passed_mined"] and n_pass == 0:
            lines.append(
                "2. **KSS `log_mv` 反向仍是当前最强 prior**：在 prior family 下能过 DSR，"
                "Alpha158 任何因子在 mined family 下都未能过。当前主路径继续用 `log_mv` 反向 "
                "+ 行业/市值中性化的方案是正确的；纳入 Alpha158 暂无显著增量."
            )
    lines.append(
        "3. **下一步建议**："
    )
    lines.append(
        "   - 若 0 通过：保持现状，把本筛选作"
        "negative result 归档至 `docs/solutions/qlib_paper_comparison.md`；"
        "扩大样本到全 A 重跑（51 只科创板样本量本身可能就不够 DSR 看到稳定信号）."
    )
    lines.append(
        "   - 若有少量通过：跑 7 层独立验证（IC 稳定性 / 分位单调性 / OOS / 交易摩擦 / 滑点）"
        "之后才能合并到 `FactorPipeline`."
    )
    lines.append(
        "   - 永不绕过 mined-family 矫正：KSS 8 轮实验里半数被证伪的教训就是 "
        "`strategy_family` 选错或 `n_trials` 给少了."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/screen_alpha158_dsr.py` "
                 "(KSS 第 4.1 节任务，Alpha158 集体 DSR 筛选)_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


if __name__ == "__main__":
    main()
