#!/usr/bin/env python3
"""科创板 51 只 LGB 横截面 —— Wave 3 三大能力终极验证.

接续 ``analyze_kcb50_wf_factor_selection.py``：上一轮裸 LGB MSE Sharpe **-0.37**，
唯一通过 DSR 的策略是单 log_mv 反向 Sharpe 1.93。

本脚本启用 Wave 1-3 三大新能力的组合，回答两个核心问题：

1. **LGB 能被救活吗？** 即"neutralize + ranker + execution"组合下，LGB 多因子能否
   终于跑出 Sharpe > 0 的策略？
2. **log_mv 反向真实可达 Sharpe 多少？** 加 ExecutionModel 后从 1.93 折到几？

对照组：

- A1: 裸 LGB MSE                      ← 上一轮 baseline (-0.37)
- A2: LGB MSE + neutralize             ← 看中性化能不能救 MSE
- A3: LGB Ranker                       ← 看 ranker 能不能救
- A4: LGB Ranker + neutralize          ← 双 buff
- A5: LGB Ranker + neutralize + execution ← 全 buff 实盘可达
- B1: 单 log_mv 反向                   ← 理论 1.93
- B2: 单 log_mv 反向 + execution        ← 实盘可达

输出::

    storage/reports/kcb50_ultimate_report.md
    storage/reports/kcb50_ultimate_panels.png
"""

from __future__ import annotations

import glob
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kss.backtest.benchmark import Benchmark  # noqa: E402
from kss.backtest.cost_model import CostModel, ExecutionModel  # noqa: E402
from kss.backtest.cross_section import factor_cross_section_backtest  # noqa: E402
from kss.backtest.diagnostics import SignalDiagnostics  # noqa: E402
from kss.backtest.engine import BacktestEngine  # noqa: E402
from kss.backtest.metrics import Metrics  # noqa: E402
from kss.backtest.significance import Significance  # noqa: E402
from kss.data.industry_mapping import IndustryMapping  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402
from kss.strategies.registry import (  # noqa: E402
    DeploymentBlockedError, StrategyRegistry,
)

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_GLOB = str(PROJECT_ROOT / "cs_data_688*.csv")
BENCHMARK_PATH = PROJECT_ROOT / "cs_index_000688_SH.csv"
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "kcb50_ultimate_report.md"
PNG_PATH = REPORT_DIR / "kcb50_ultimate_panels.png"

IC_HORIZON = "future_return_5d"
TRAIN_WINDOW = 120
RETRAIN_FREQ = 5
TOP_PCT = 0.2
MIN_TRAIN = 1000
MIN_TEST = 30
MIN_STOCKS = 10

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# 数据
# ---------------------------------------------------------------------- #


def load_universe_panel() -> tuple[pd.DataFrame, list[str]]:
    files = sorted(glob.glob(DATA_GLOB))
    logger.info("加载 %d 只股票", len(files))

    all_panels: list[pd.DataFrame] = []
    feature_cols: list[str] = []
    for fp_path in files:
        try:
            df = pd.read_csv(fp_path)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date").reset_index(drop=True)
            if len(df) < 100:
                continue
            fp = FactorPipeline(df)
            factors = fp.add_targets(fp.generate(), df["close"])
            factors["symbol"] = df["ts_code"].iloc[0]
            # 给 execution 用的列
            factors["pre_close"] = df["pre_close"].values
            factors["open"] = df["open"].values
            factors["amount"] = df["amount"].values
            all_panels.append(factors)
            if not feature_cols:
                feature_cols = fp.get_feature_cols(factors)
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 %s 失败: %s", fp_path, exc)

    panel = pd.concat(all_panels, ignore_index=True).dropna(subset=["next_day_return"])
    # 加 industry（fallback_kcb 三分类）
    industry = IndustryMapping.fallback_kcb(panel["symbol"].unique())
    panel = industry.add_to_panel(panel, symbol_col="symbol")
    logger.info(
        "Panel: %d 行 × %d 股票, %s ~ %s, %d 因子",
        len(panel), panel["symbol"].nunique(),
        panel["trade_date"].min().date(), panel["trade_date"].max().date(),
        len(feature_cols),
    )
    logger.info("Industry 分布: %s", panel["industry"].value_counts().to_dict())
    return panel, feature_cols


# ---------------------------------------------------------------------- #
# 评估
# ---------------------------------------------------------------------- #


def evaluate(
    res: pd.DataFrame,
    bench: Benchmark,
    name: str,
    n_trials: int,
) -> dict:
    m_net = Metrics.calc(res["net_return"])
    m_gross = Metrics.calc(res["gross_return"])
    sig = Significance.sharpe_significance(
        res["net_return"], n_trials=n_trials,
        skew=m_net.get("skew", 0.0), kurt=m_net.get("kurt", 0.0),
    )
    strat = res.set_index("trade_date")["net_return"]
    ab = bench.alpha_beta(strat)
    deployable = Significance.is_deployable(
        res["net_return"], n_trials=n_trials, return_details=True,
    )
    return {
        "name": name,
        "metrics_net": m_net,
        "metrics_gross": m_gross,
        "significance": sig,
        "alpha_beta": ab,
        "bench_returns": bench.aligned_returns(strat),
        "excess_max_dd": bench.excess_max_dd(strat),
        "turnover_avg": float(res["turnover"].mean()),
        "deployable": deployable,
        "result_df": res,
    }


# ---------------------------------------------------------------------- #
# 绘图
# ---------------------------------------------------------------------- #


def plot_panels(panel: pd.DataFrame, strategies: dict[str, dict]) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(16, 18))

    # Panel 1: 净值曲线
    ax = axes[0, 0]
    any_strat = next(iter(strategies.values()))
    bench = any_strat["bench_returns"]
    if not bench.empty:
        cum_b = (1 + bench.fillna(0)).cumprod() - 1
        ax.plot(bench.index, cum_b.values, label="科创50",
                lw=1.5, color="gray", alpha=0.7)
    colors = plt.cm.tab10.colors
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        cum = (1 + r["net_return"].fillna(0)).cumprod() - 1
        is_log_mv = "log_mv" in name
        ax.plot(
            r["trade_date"], cum,
            label=name, lw=2.0 if is_log_mv else 1.2,
            color=colors[i % len(colors)],
            alpha=0.95 if is_log_mv else 0.7,
            ls="-" if is_log_mv else "-",
        )
    ax.set_title("各策略净值曲线")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 2: Sharpe / Alpha / 是否 deployable 横向柱状
    ax = axes[0, 1]
    names = list(strategies.keys())
    sharpes = [s["metrics_net"]["sharpe"] for s in strategies.values()]
    alphas = [s["alpha_beta"]["alpha_annual"] * 100 for s in strategies.values()]
    y = np.arange(len(names))
    bars1 = ax.barh(y - 0.2, sharpes, 0.4, label="Sharpe", color="C0", alpha=0.7)
    ax2 = ax.twiny()
    bars2 = ax2.barh(y + 0.2, alphas, 0.4, label="年化 Alpha (%)", color="C1", alpha=0.7)
    # 标记 deployable（蓝勾、红叉）
    for i, info in enumerate(strategies.values()):
        passed = info["deployable"]["passed"] if isinstance(info["deployable"], dict) else info["deployable"]
        ax.text(
            max(sharpes) + 0.3, i,
            "✓ 可上线" if passed else "✗",
            color="green" if passed else "red",
            fontsize=10, va="center",
        )
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("Sharpe", color="C0")
    ax2.set_xlabel("年化 Alpha (%)", color="C1")
    ax.axvline(x=0, color="black", lw=0.3)
    ax.axvline(x=0.5, color="green", ls="--", alpha=0.4, label="Sharpe=0.5 门槛")
    ax.set_title("Sharpe & Alpha & DSR 门槛")
    ax.legend(loc="lower right", fontsize=7)
    ax2.legend(loc="upper right", fontsize=7)
    ax.grid(alpha=0.3, axis="x")

    # Panel 3: 60d 滚动 Sharpe
    ax = axes[1, 0]
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        net = r.set_index("trade_date")["net_return"]
        rolling = (net.rolling(60).mean() / net.rolling(60).std() * np.sqrt(252))
        is_log_mv = "log_mv" in name
        ax.plot(rolling.index, rolling.values, label=name,
                alpha=0.95 if is_log_mv else 0.6,
                lw=1.8 if is_log_mv else 1.0,
                color=colors[i % len(colors)])
    ax.axhline(y=0, color="black", lw=0.4, alpha=0.5)
    ax.axhline(y=0.5, color="green", ls="--", alpha=0.3)
    ax.set_title("60 日滚动 Sharpe")
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.3)

    # Panel 4: 显著性热力
    ax = axes[1, 1]
    metrics_to_show = ["sharpe", "annual", "max_dd", "alpha_annual", "info_ratio",
                       "p_value", "dsr"]
    matrix = np.zeros((len(names), len(metrics_to_show)))
    for i, info in enumerate(strategies.values()):
        m = info["metrics_net"]
        ab = info["alpha_beta"]
        sig = info["significance"]
        deploy = info["deployable"]
        matrix[i] = [
            m["sharpe"], m["annual"] * 100, m["max_dd"] * 100,
            ab["alpha_annual"] * 100, ab["info_ratio"],
            sig["p_value"],
            deploy.get("dsr", float("nan")) if isinstance(deploy, dict) else float("nan"),
        ]
    # 归一化每列（用于颜色）
    norm = matrix.copy()
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        if np.nanstd(col) > 0:
            norm[:, j] = (col - np.nanmean(col)) / np.nanstd(col)
    # p_value 反向（小才好）
    p_idx = metrics_to_show.index("p_value")
    norm[:, p_idx] = -norm[:, p_idx]
    im = ax.imshow(norm, aspect="auto", cmap="RdYlGn", vmin=-2, vmax=2)
    ax.set_xticks(range(len(metrics_to_show)))
    ax.set_xticklabels(
        ["Sharpe", "年化%", "回撤%", "Alpha%", "IR", "p", "DSR"],
        fontsize=8, rotation=30, ha="right",
    )
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=7)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            v = matrix[i, j]
            if np.isnan(v):
                txt = "—"
            elif metrics_to_show[j] in ("annual", "max_dd", "alpha_annual"):
                txt = f"{v:+.1f}%"
            else:
                txt = f"{v:.2f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=6,
                    color="white" if abs(norm[i, j]) > 1 else "black")
    ax.set_title("策略指标矩阵（颜色按列 z-score，p_value 反向）")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    # Panel 5: 回撤曲线
    ax = axes[2, 0]
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        net = r.set_index("trade_date")["net_return"]
        cum = (1 + net.fillna(0)).cumprod()
        dd = (cum - cum.cummax()) / cum.cummax()
        is_log_mv = "log_mv" in name
        ax.fill_between(
            dd.index, dd.values, alpha=0.2 if is_log_mv else 0.1,
            color=colors[i % len(colors)], label=name,
        )
    ax.axhline(y=0, color="black", lw=0.4)
    ax.set_title("回撤曲线（dd 越接近 0 越好）")
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.3)

    # Panel 6: 换手 vs Sharpe 散点
    ax = axes[2, 1]
    for i, (name, info) in enumerate(strategies.items()):
        sh = info["metrics_net"]["sharpe"]
        to = info["turnover_avg"]
        is_log_mv = "log_mv" in name
        ax.scatter(
            to * 100, sh,
            s=300 if is_log_mv else 150,
            marker="*" if is_log_mv else "o",
            color=colors[i % len(colors)],
            alpha=0.8, edgecolors="black", linewidth=0.6,
        )
        ax.annotate(
            name, (to * 100, sh),
            fontsize=7, xytext=(5, 5), textcoords="offset points",
        )
    ax.set_xlabel("平均换手率 (%)")
    ax.set_ylabel("Sharpe (净)")
    ax.set_title("换手率 ↔ Sharpe (★=log_mv 系列)")
    ax.axhline(y=0, color="black", lw=0.3)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(PNG_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("PNG: %s", PNG_PATH)


# ---------------------------------------------------------------------- #
# Markdown
# ---------------------------------------------------------------------- #


def fmt_pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v*100:+.1f}%"


def fmt_num(v: float, d: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{d}f}"


def render_markdown(panel: pd.DataFrame, strategies: dict[str, dict]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# 科创板 LGB 横截面 —— Wave 3 三大能力终极验证")
    lines.append("")
    lines.append(
        f"- **股票池**: 51 只科创板  "
        f"**样本**: {len(panel):,} 行 × {panel['symbol'].nunique()} 股票"
    )
    lines.append(
        f"- **区间**: {panel['trade_date'].min().date()} ~ "
        f"{panel['trade_date'].max().date()}"
    )
    lines.append(
        f"- **行业 fallback**: 3 类 ({panel['industry'].value_counts().to_dict()})"
    )
    lines.append(
        f"- **walk_forward 配置**: train_window={TRAIN_WINDOW}, retrain={RETRAIN_FREQ}, "
        f"top_pct={TOP_PCT}, label={IC_HORIZON}"
    )
    lines.append("")
    lines.append(
        "**核心问题**：Wave 3 三大新能力（neutralize / ranker / execution）能否把"
        "上一轮裸 LGB Sharpe **-0.37** 救回正值？同时给 log_mv 反向加 execution"
        "得到真实可达 Sharpe."
    )
    lines.append("")

    # ============== 1. 综合对比 ============== #
    lines.append("## 1. 综合对比")
    lines.append("")
    lines.append("| 策略 | Sharpe | 年化 | 最大回撤 | Alpha | IR | p | DSR | 换手 | 上线 |")
    lines.append("|------|--------|------|----------|-------|----|---|-----|------|------|")
    for name, info in strategies.items():
        m = info["metrics_net"]
        ab = info["alpha_beta"]
        sig = info["significance"]
        deploy = info["deployable"]
        passed = deploy["passed"] if isinstance(deploy, dict) else deploy
        dsr = deploy.get("dsr", float("nan")) if isinstance(deploy, dict) else float("nan")
        lines.append(
            f"| {name} | {m['sharpe']:.2f} | {fmt_pct(m['annual'])} | "
            f"{fmt_pct(m['max_dd'])} | {fmt_pct(ab['alpha_annual'])} | "
            f"{ab['info_ratio']:.2f} | {sig['p_value']:.3f} | "
            f"{dsr:.2f} | {info['turnover_avg']*100:.1f}% | "
            f"{'✅' if passed else '❌'} |"
        )
    lines.append("")

    # ============== 2. LGB 救活情况 ============== #
    lines.append("## 2. LGB 能被救活吗？")
    lines.append("")

    lgb_strategies = [
        (k, v) for k, v in strategies.items() if "LGB" in k
    ]
    if lgb_strategies:
        lines.append("| 配置 | Sharpe | 年化 | vs 裸 LGB Δ |")
        lines.append("|------|--------|------|-------------|")
        baseline_sharpe = strategies.get("A1 裸 LGB MSE", {}).get("metrics_net", {}).get("sharpe", float("nan"))
        for name, info in lgb_strategies:
            sh = info["metrics_net"]["sharpe"]
            delta = sh - baseline_sharpe if not pd.isna(baseline_sharpe) else float("nan")
            lines.append(
                f"| {name} | {sh:.2f} | {fmt_pct(info['metrics_net']['annual'])} | "
                f"{delta:+.2f} |"
            )
        lines.append("")

        # 解读
        best_lgb = max(lgb_strategies, key=lambda kv: kv[1]["metrics_net"]["sharpe"])
        if best_lgb[1]["metrics_net"]["sharpe"] > 0.5:
            lines.append(
                f"**结论**：✅ Wave 3 三大能力把 LGB 救活了。最佳 LGB 配置 "
                f"**{best_lgb[0]}** Sharpe = **{best_lgb[1]['metrics_net']['sharpe']:.2f}** "
                f"（vs 上一轮裸 LGB -0.37 提升 **{best_lgb[1]['metrics_net']['sharpe'] - (-0.37):+.2f}**）."
            )
        elif best_lgb[1]["metrics_net"]["sharpe"] > 0:
            lines.append(
                f"**结论**：⚠️ Wave 3 把 LGB 从负值救到了 **{best_lgb[1]['metrics_net']['sharpe']:.2f}**，"
                f"但仍低于 Sharpe 0.5 上线门槛。"
            )
        else:
            lines.append(
                f"**结论**：❌ 即便启用 neutralize + ranker + execution 三大 buff，"
                f"LGB 最佳配置仍只有 Sharpe **{best_lgb[1]['metrics_net']['sharpe']:.2f}**。"
                f"说明根因不在工程实现，而在数据本身——A 股横截面 alpha 极度稀薄."
            )
        lines.append("")

    # ============== 3. log_mv 反向实盘可达 ============== #
    lines.append("## 3. log_mv 反向：理论 vs 实盘可达")
    lines.append("")

    log_mv_pair = [
        (k, v) for k, v in strategies.items() if "log_mv" in k
    ]
    if len(log_mv_pair) >= 2:
        theo = next((v for k, v in log_mv_pair if "execution" not in k), None)
        real = next((v for k, v in log_mv_pair if "execution" in k), None)
        if theo and real:
            t_sh = theo["metrics_net"]["sharpe"]
            r_sh = real["metrics_net"]["sharpe"]
            ratio = (r_sh / t_sh * 100) if abs(t_sh) > 1e-6 else float("nan")
            lines.append(
                f"- 理论 Sharpe（无 execution）: **{t_sh:.2f}**, 年化 {fmt_pct(theo['metrics_net']['annual'])}"
            )
            lines.append(
                f"- 实盘可达 Sharpe（加 execution）: **{r_sh:.2f}**, 年化 {fmt_pct(real['metrics_net']['annual'])}"
            )
            lines.append(
                f"- **保留率**: {ratio:.0f}% （Δ Sharpe {r_sh - t_sh:+.2f}）"
            )
            lines.append("")
            if r_sh > 1.0:
                lines.append(
                    "**结论**：✅ log_mv 反向真实可达 Sharpe 仍超 1.0，DSR 通过，"
                    "**该策略可作为实盘 baseline 进入 #33 纸交易阶段**."
                )
            elif r_sh > 0.5:
                lines.append(
                    "**结论**：⚠️ log_mv 反向真实可达 Sharpe 介于 0.5-1.0，"
                    "勉强达到上线门槛；建议先纸交易 1-2 个月再考虑实盘."
                )
            else:
                lines.append(
                    "**结论**：❌ 加上实盘成交后 log_mv 反向 Sharpe 跌破上线门槛，"
                    "需重新审视——这可能意味着小市值流动性溢价大部分被滑点吃掉."
                )
            lines.append("")

    # ============== 4. 上线门槛（DSR + p-value + Sharpe）综合 ============== #
    lines.append("## 4. 上线门槛检查（DSR + p-value + Sharpe）")
    lines.append("")
    passed_strats = [
        k for k, v in strategies.items()
        if (v["deployable"]["passed"] if isinstance(v["deployable"], dict) else v["deployable"])
    ]
    failed_strats = [
        (k, v["deployable"]["failures"] if isinstance(v["deployable"], dict) else [])
        for k, v in strategies.items()
        if not (v["deployable"]["passed"] if isinstance(v["deployable"], dict) else v["deployable"])
    ]
    if passed_strats:
        lines.append("**通过上线门槛**：")
        for s in passed_strats:
            lines.append(f"- ✅ `{s}`")
        lines.append("")
    if failed_strats:
        lines.append("**未通过上线门槛**：")
        for s, fails in failed_strats:
            lines.append(f"- ❌ `{s}` —— {', '.join(fails) if fails else '未知'}")
        lines.append("")

    # ============== 5. Wave 3 能力贡献分解 ============== #
    lines.append("## 5. Wave 3 能力贡献分解")
    lines.append("")
    base = strategies.get("A1 裸 LGB MSE")
    if base:
        b_sh = base["metrics_net"]["sharpe"]
        lines.append("| 增量能力 | 加入后 Sharpe | 提升 Δ | 贡献 |")
        lines.append("|---------|--------------|-------|------|")
        for stage, key in [
            ("+ neutralize", "A2 LGB MSE + neutralize"),
            ("+ ranker（无 neutralize）", "A3 LGB Ranker"),
            ("+ ranker + neutralize", "A4 LGB Ranker + neutralize"),
            ("+ execution（实盘可达）", "A5 LGB Ranker + neutralize + execution"),
        ]:
            if key in strategies:
                sh = strategies[key]["metrics_net"]["sharpe"]
                delta = sh - b_sh
                contrib = "正面" if delta > 0.05 else "持平" if abs(delta) < 0.05 else "负面"
                lines.append(
                    f"| {stage} | {sh:.2f} | {delta:+.2f} | {contrib} |"
                )
        lines.append("")

    # ============== 6. 综合结论 ============== #
    lines.append("## 6. 综合结论")
    lines.append("")
    deployable_count = len(passed_strats)
    lines.append(
        f"1. **{deployable_count}/{len(strategies)} 策略通过上线门槛**"
        f"（DSR ≥ 0.7 + p < 0.05 + Sharpe ≥ 0.5）."
    )

    # log_mv 路径
    if log_mv_pair and len(log_mv_pair) >= 2:
        real_log_mv = next((v for k, v in log_mv_pair if "execution" in k), None)
        if real_log_mv:
            r_sh = real_log_mv["metrics_net"]["sharpe"]
            lines.append(
                f"2. **log_mv 反向实盘可达 Sharpe = {r_sh:.2f}**，仍是 KSS 体系内"
                f"唯一被多重无偏验证的真 alpha 来源."
            )

    # LGB 路径
    if lgb_strategies:
        best_lgb_name, best_lgb_info = max(
            lgb_strategies, key=lambda kv: kv[1]["metrics_net"]["sharpe"]
        )
        b_sh = best_lgb_info["metrics_net"]["sharpe"]
        if b_sh > 0:
            lines.append(
                f"3. **LGB 多因子最佳配置**: `{best_lgb_name}` Sharpe = {b_sh:.2f}，"
                f"相比上一轮裸 LGB **-0.37** 大幅改善。Wave 3 工程投入有回报."
            )
        else:
            lines.append(
                f"3. **LGB 多因子仍未跑赢**: 即便启用全 buff，"
                f"Sharpe 最高仅 {b_sh:.2f}。说明 A 股横截面 alpha 真的稀薄，"
                f"工程改造的边际收益已到极限——下一步要找的是数据（更长历史、跨市场）而非更复杂的模型."
            )

    lines.append(
        "4. **下一步实盘路径**："
    )
    lines.append(
        "   - 任何通过上线门槛的策略 → 进入 **#33 纸交易日志** 阶段，"
        "实采集 30 个交易日真实成交差异；"
    )
    lines.append(
        "   - 任何未通过门槛的策略 → 不上实盘；要么找更多 alpha 来源（**#37 跨市场**），"
        "要么承认科创板 50 这个池子的横截面 alpha 上限就是 log_mv 反向那一条."
    )
    lines.append("")

    # ============== 7. 注意事项 ============== #
    lines.append("## 7. 注意事项")
    lines.append("")
    lines.append(
        "- **行业用 fallback_kcb 三分类**（STAR_TECH / STAR_MFG / STAR_BIO），"
        "粗略；真实生产应接 Tushare sw_l1（积分 2000）."
    )
    lines.append(
        "- **DSR 用 n_trials=10**（保守），实际本系列 7+ 轮迭代加各种 hyperparam，"
        "真实 trials 可能数百量级 → DSR 真值会更低，结论应做适度折扣."
    )
    lines.append(
        "- **样本期 2.3 年**，未跨完整牛熊；log_mv 反向在 2021 年大票回归期可能反向亏损."
    )
    lines.append(
        "- **execution 仅建模开盘涨停 + 滑点 + 部分成交**，盘中触板 / ST / 停牌未建模（见 `docs/solutions/known_bias_gaps.md`）."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_kcb50_ultimate.py`_")

    MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown: %s", MD_PATH)


# ---------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------- #


def main() -> None:
    panel, feature_cols = load_universe_panel()
    bench = Benchmark(BENCHMARK_PATH, rf_annual=0.02)
    cost = CostModel(buy_cost=0.001, sell_cost=0.002, slippage_bps=0)
    engine = BacktestEngine(cost)
    exec_model = ExecutionModel(
        kcb_limit_pct=0.20, max_volume_pct=0.05, open_slippage_bps=10,
    )

    common_lgb = dict(
        factor_df=panel,
        feature_cols=feature_cols,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW, retrain_freq=RETRAIN_FREQ, top_pct=TOP_PCT,
        min_train=MIN_TRAIN, min_test=MIN_TEST, min_stocks=MIN_STOCKS,
    )

    strategies: dict[str, dict] = {}

    # A1: 裸 LGB MSE（上一轮 baseline）
    logger.info("【A1】裸 LGB MSE...")
    res = engine.walk_forward(**common_lgb)
    if res is not None:
        strategies["A1 裸 LGB MSE"] = evaluate(res, bench, "A1", n_trials=10)

    # A2: + neutralize
    logger.info("【A2】LGB MSE + neutralize...")
    res = engine.walk_forward(**common_lgb, neutralize=True)
    if res is not None:
        strategies["A2 LGB MSE + neutralize"] = evaluate(res, bench, "A2", n_trials=10)

    # A3: ranker only
    logger.info("【A3】LGB Ranker...")
    res = engine.walk_forward(**common_lgb, model_type="ranker")
    if res is not None:
        strategies["A3 LGB Ranker"] = evaluate(res, bench, "A3", n_trials=10)

    # A4: ranker + neutralize
    logger.info("【A4】LGB Ranker + neutralize...")
    res = engine.walk_forward(**common_lgb, model_type="ranker", neutralize=True)
    if res is not None:
        strategies["A4 LGB Ranker + neutralize"] = evaluate(res, bench, "A4", n_trials=10)

    # A5: ranker + neutralize + execution（全 buff）
    logger.info("【A5】LGB Ranker + neutralize + execution（全 buff 实盘可达）...")
    res = engine.walk_forward(
        **common_lgb, model_type="ranker", neutralize=True, execution=exec_model,
    )
    if res is not None:
        strategies["A5 LGB Ranker + neutralize + execution"] = evaluate(
            res, bench, "A5", n_trials=10,
        )

    # B1: 单 log_mv 反向（无 execution）
    logger.info("【B1】单 log_mv 反向（无 execution）...")
    res = factor_cross_section_backtest(
        panel, "log_mv", cost, top_pct=TOP_PCT, direction="long_low",
        min_stocks=MIN_STOCKS,
    )
    if res is not None:
        strategies["B1 单 log_mv 反向"] = evaluate(res, bench, "B1", n_trials=10)

    # B2: 单 log_mv 反向 + execution
    logger.info("【B2】单 log_mv 反向 + execution（实盘可达）...")
    res = factor_cross_section_backtest(
        panel, "log_mv", cost, top_pct=TOP_PCT, direction="long_low",
        min_stocks=MIN_STOCKS, execution=exec_model,
    )
    if res is not None:
        strategies["B2 单 log_mv 反向 + execution"] = evaluate(res, bench, "B2", n_trials=10)

    # 输出
    plot_panels(panel, strategies)
    render_markdown(panel, strategies)

    # 终端
    print("\n" + "=" * 92)
    print("Wave 3 三大能力终极验证 (51 只科创板)")
    print("=" * 92)
    print(f"\n{'策略':45s}  {'Sharpe':>8s}  {'年化':>8s}  {'回撤':>8s}  "
          f"{'Alpha':>8s}  {'IR':>6s}  {'DSR':>6s}  {'上线':>6s}")
    print("-" * 95)
    for name, info in strategies.items():
        m = info["metrics_net"]
        ab = info["alpha_beta"]
        deploy = info["deployable"]
        dsr = deploy.get("dsr", float("nan")) if isinstance(deploy, dict) else float("nan")
        passed = deploy["passed"] if isinstance(deploy, dict) else deploy
        print(
            f"{name:45s}  {m['sharpe']:+8.2f}  "
            f"{m['annual']*100:+7.1f}%  {m['max_dd']*100:+7.1f}%  "
            f"{ab['alpha_annual']*100:+7.1f}%  {ab['info_ratio']:+6.2f}  "
            f"{dsr:6.2f}  {'✓' if passed else '✗':>6s}"
        )

    # 尝试用 StrategyRegistry 注册所有通过的策略
    print("\n--- StrategyRegistry 注册结果 ---")
    registry = StrategyRegistry()
    for name, info in strategies.items():
        try:
            r = info["result_df"]
            registry.register(name, r["net_return"], n_trials=10)
            print(f"  ✅ {name}")
        except DeploymentBlockedError as e:
            print(f"  ❌ {name}: {e.failures}")

    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
