#!/usr/bin/env python3
"""科创板 LGB 横截面 —— 因子选择 walk-forward 化（去掉选因子事后偏差）.

接续 ``analyze_kcb50_lgb_cross_section.py`` 的"下一步"：

- 上一版用 ``SignalDiagnostics.cross_section_ic_scan`` 在**全样本**上算 IC 选 Top 10 因子,
  这本身就是事后偏差——实盘上 t 时刻选哪些因子，只能用 [t-train_window, t] 历史数据.
- 本脚本在 ``BacktestEngine.walk_forward`` 中传入 ``feature_selector`` callable,
  让 IC scan 也在每个 retrain 点滚动重算，**彻底关闭最后一个 look-ahead 通道**.

实验组：

1. **静态 IC Top 10**（事后偏差版，重跑对照）—— 与上一报告一致
2. **WF IC Top 10**（滚动重选因子，**本轮主角**）
3. **WF IC Top 10 + min_t_stat=2**（仅取显著因子，自适应数量）
4. **全 45 因子 LGB**（不选因子）
5. **单 log_mv 反向**（最强单因子 baseline）

输出::

    storage/reports/kcb50_wf_factor_selection_report.md
    storage/reports/kcb50_wf_factor_selection_panels.png
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
from kss.backtest.cost_model import CostModel  # noqa: E402
from kss.backtest.cross_section import factor_cross_section_backtest  # noqa: E402
from kss.backtest.diagnostics import SignalDiagnostics  # noqa: E402
from kss.backtest.engine import BacktestEngine  # noqa: E402
from kss.backtest.metrics import Metrics  # noqa: E402
from kss.backtest.significance import Significance  # noqa: E402
from kss.features.cross_section_selection import make_ic_topk_selector  # noqa: E402
from kss.features.pipeline import FactorPipeline  # noqa: E402


# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #

DATA_GLOB = str(PROJECT_ROOT / "cs_data_688*.csv")
BENCHMARK_PATH = PROJECT_ROOT / "cs_index_000688_SH.csv"
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
MD_PATH = REPORT_DIR / "kcb50_wf_factor_selection_report.md"
PNG_PATH = REPORT_DIR / "kcb50_wf_factor_selection_panels.png"

IC_HORIZON = "future_return_5d"
IC_TOP_K = 10

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
    if not files:
        raise FileNotFoundError(f"未找到 {DATA_GLOB}")
    logger.info("加载 %d 只股票", len(files))

    all_factors: list[pd.DataFrame] = []
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
            all_factors.append(factors)
            if not feature_cols:
                feature_cols = fp.get_feature_cols(factors)
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载 %s 失败: %s", fp_path, exc)

    panel = pd.concat(all_factors, ignore_index=True)
    panel = panel.dropna(subset=["next_day_return"])
    logger.info(
        "Panel: %d 行 × %d 股票, %s ~ %s",
        len(panel), panel["symbol"].nunique(),
        panel["trade_date"].min().date(), panel["trade_date"].max().date(),
    )
    return panel, feature_cols


# ---------------------------------------------------------------------- #
# 评估
# ---------------------------------------------------------------------- #


def evaluate(
    res: pd.DataFrame,
    bench: Benchmark,
    name: str,
    n_trials: int = 5,
) -> dict:
    m_net = Metrics.calc(res["net_return"])
    m_gross = Metrics.calc(res["gross_return"])
    sig = Significance.sharpe_significance(
        res["net_return"], n_trials=n_trials,
        skew=m_net.get("skew", 0.0), kurt=m_net.get("kurt", 0.0),
    )
    strat = res.set_index("trade_date")["net_return"]
    ab = bench.alpha_beta(strat)
    return {
        "name": name,
        "metrics_net": m_net,
        "metrics_gross": m_gross,
        "significance": sig,
        "alpha_beta": ab,
        "bench_returns": bench.aligned_returns(strat),
        "excess_max_dd": bench.excess_max_dd(strat),
        "turnover_avg": float(res["turnover"].mean()),
        "result_df": res,
    }


# ---------------------------------------------------------------------- #
# 选因子历史追踪（用于诊断 WF selector 选了哪些因子）
# ---------------------------------------------------------------------- #


def make_tracking_selector(
    base_selector,
    history: list[dict],
) -> "callable":
    """把基础 selector 包一层，记录每次 retrain 选了哪些因子."""
    def _tracking(train_df: pd.DataFrame) -> list[str]:
        out = base_selector(train_df)
        history.append({
            "trade_date_end": train_df["trade_date"].max() if not train_df.empty else None,
            "n_factors": len(out),
            "factors": list(out),
        })
        return out
    return _tracking


# ---------------------------------------------------------------------- #
# 绘图
# ---------------------------------------------------------------------- #


def plot_panels(
    panel: pd.DataFrame,
    strategies: dict[str, dict],
    history_topk: list[dict],
    history_t2: list[dict],
) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(16, 18))

    # Panel 1: 各策略净值
    ax = axes[0, 0]
    any_strat = next(iter(strategies.values()))
    bench = any_strat["bench_returns"]
    if not bench.empty:
        cum_b = (1 + bench.fillna(0)).cumprod() - 1
        ax.plot(bench.index, cum_b.values, label="科创50", lw=1.5, color="gray", alpha=0.7)
    colors = ["C0", "C1", "C2", "C3", "C4", "C5"]
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        cum = (1 + r["net_return"].fillna(0)).cumprod() - 1
        ax.plot(r["trade_date"], cum, label=name, lw=1.5,
                color=colors[i % len(colors)], alpha=0.85)
    ax.set_title("策略净值对比")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)

    # Panel 2: Sharpe / Alpha 横向柱状对比
    ax = axes[0, 1]
    names = list(strategies.keys())
    sharpes = [s["metrics_net"]["sharpe"] for s in strategies.values()]
    alphas = [s["alpha_beta"]["alpha_annual"] * 100 for s in strategies.values()]
    y = np.arange(len(names))
    ax.barh(y - 0.2, sharpes, 0.4, label="Sharpe", color="C0", alpha=0.7)
    ax2 = ax.twiny()
    ax2.barh(y + 0.2, alphas, 0.4, label="年化 Alpha (%)", color="C1", alpha=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("Sharpe", color="C0")
    ax2.set_xlabel("年化 Alpha (%)", color="C1")
    ax.axvline(x=0, color="black", lw=0.3)
    ax.set_title("Sharpe & Alpha 横向对比")
    ax.legend(loc="lower right", fontsize=7)
    ax2.legend(loc="upper right", fontsize=7)
    ax.grid(alpha=0.3, axis="x")

    # Panel 3: WF top_k=10 选因子历史（柱状堆叠）
    ax = axes[1, 0]
    if history_topk:
        plot_selection_history(ax, history_topk,
                               title=f"WF IC Top {IC_TOP_K} 选因子历史")
    # Panel 4: WF min_t_stat=2 选因子历史
    ax = axes[1, 1]
    if history_t2:
        plot_selection_history(ax, history_t2,
                               title="WF IC + |t|≥2 显著门槛")

    # Panel 5: 滚动 60d Sharpe
    ax = axes[2, 0]
    window = 60
    for i, (name, info) in enumerate(strategies.items()):
        r = info["result_df"]
        net = r.set_index("trade_date")["net_return"]
        rolling = net.rolling(window).mean() / net.rolling(window).std() * np.sqrt(252)
        ax.plot(rolling.index, rolling.values, label=name, alpha=0.8,
                color=colors[i % len(colors)], lw=1.2)
    ax.axhline(y=0, color="black", lw=0.4, alpha=0.5)
    ax.set_title(f"{window} 日滚动 Sharpe")
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.3)

    # Panel 6: 因子出现频率（WF top_k 历史聚合）
    ax = axes[2, 1]
    if history_topk:
        counter: dict[str, int] = {}
        for h in history_topk:
            for f in h["factors"]:
                counter[f] = counter.get(f, 0) + 1
        if counter:
            top_freq = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:15]
            n_retrains = len(history_topk)
            facs = [f for f, _ in top_freq]
            counts = [c for _, c in top_freq]
            ax.barh(range(len(facs)), counts, color="C0", alpha=0.7)
            ax.set_yticks(range(len(facs)))
            ax.set_yticklabels(facs, fontsize=8)
            ax.invert_yaxis()
            ax.set_xlabel(f"在 {n_retrains} 次 retrain 中出现次数")
            ax.set_title(f"WF Top {IC_TOP_K} 因子稳定性")
            ax.axvline(x=n_retrains, color="red", ls="--", alpha=0.4,
                       label="100% (始终入选)")
            ax.legend(fontsize=7)
            ax.grid(alpha=0.3, axis="x")

    plt.tight_layout()
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(PNG_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("PNG: %s", PNG_PATH)


def plot_selection_history(ax: plt.Axes, history: list[dict], title: str) -> None:
    """因子选择历史可视化：x=retrain 时点，y=因子，每点标记 / 不标记."""
    if not history:
        ax.set_visible(False)
        return
    dates = [h["trade_date_end"] for h in history]
    all_factors = sorted({f for h in history for f in h["factors"]})
    if not all_factors:
        ax.set_visible(False)
        return
    # 矩阵：rows=factors, cols=dates, 1 if selected
    matrix = np.zeros((len(all_factors), len(dates)))
    for j, h in enumerate(history):
        for i, f in enumerate(all_factors):
            if f in h["factors"]:
                matrix[i, j] = 1
    ax.imshow(matrix, aspect="auto", cmap="Greens", interpolation="nearest")
    ax.set_yticks(range(len(all_factors)))
    ax.set_yticklabels(all_factors, fontsize=7)
    # x 轴显示稀疏标签
    step = max(1, len(dates) // 8)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels(
        [d.strftime("%Y-%m") if pd.notna(d) else "" for d in dates[::step]],
        rotation=30, ha="right", fontsize=7,
    )
    ax.set_title(title)


# ---------------------------------------------------------------------- #
# Markdown
# ---------------------------------------------------------------------- #


def fmt_pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v*100:+.1f}%"


def fmt_num(v: float, d: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{d}f}"


def render_markdown(
    panel: pd.DataFrame,
    strategies: dict[str, dict],
    history_topk: list[dict],
    history_t2: list[dict],
    static_top_factors: list[str],
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# 科创板 LGB 横截面 —— 因子选择 walk-forward 化")
    lines.append("")
    lines.append(
        f"- **股票池**: 51 只科创板  "
        f"**样本**: {len(panel):,} 行 × {panel['symbol'].nunique()} 股票"
    )
    lines.append(
        f"- **区间**: {panel['trade_date'].min().date()} ~ {panel['trade_date'].max().date()}"
    )
    lines.append(
        f"- **主干**: ``BacktestEngine.walk_forward`` train_window={TRAIN_WINDOW}, "
        f"retrain={RETRAIN_FREQ}, top_pct={TOP_PCT}, label={IC_HORIZON}"
    )
    lines.append(
        "- **关键改动**: `feature_selector` 参数让因子选择也在每个 retrain 点滚动重做，"
        "**完全去除选因子环节的事后偏差**."
    )
    lines.append("")

    # ============== 1. 综合对比 ============== #
    lines.append("## 1. 综合对比")
    lines.append("")
    lines.append("| 策略 | Sharpe | 年化 | 最大回撤 | Alpha (年化) | IR | p-value | 平均换手 |")
    lines.append("|------|--------|------|----------|--------------|----|---------|----------|")
    for name, info in strategies.items():
        m = info["metrics_net"]
        sig = info["significance"]
        ab = info["alpha_beta"]
        lines.append(
            f"| {name} | {m['sharpe']:.2f} | {fmt_pct(m['annual'])} | "
            f"{fmt_pct(m['max_dd'])} | {fmt_pct(ab['alpha_annual'])} | "
            f"{ab['info_ratio']:.2f} | {sig['p_value']:.3f} | "
            f"{info['turnover_avg']*100:.1f}% |"
        )
    lines.append("")

    # ============== 2. 静态 vs WF 选因子 量化对比 ============== #
    static_name = "静态 IC Top 10（事后）"
    wf_name = f"WF IC Top {IC_TOP_K}"
    wf_t2_name = f"WF IC + |t|≥2"

    if static_name in strategies and wf_name in strategies:
        s = strategies[static_name]
        w = strategies[wf_name]
        d_sharpe = w["metrics_net"]["sharpe"] - s["metrics_net"]["sharpe"]
        d_alpha = w["alpha_beta"]["alpha_annual"] - s["alpha_beta"]["alpha_annual"]
        lines.append("## 2. 选因子事后偏差量化")
        lines.append("")
        lines.append(
            f"- **静态 IC Top 10（用全样本选 → 事后偏差）**: "
            f"Sharpe {s['metrics_net']['sharpe']:.2f}, 年化 {fmt_pct(s['metrics_net']['annual'])}, "
            f"Alpha {fmt_pct(s['alpha_beta']['alpha_annual'])}"
        )
        lines.append(
            f"- **WF IC Top 10（每窗历史选 → 无事后偏差）**: "
            f"Sharpe {w['metrics_net']['sharpe']:.2f}, 年化 {fmt_pct(w['metrics_net']['annual'])}, "
            f"Alpha {fmt_pct(w['alpha_beta']['alpha_annual'])}"
        )
        lines.append(
            f"- **Δ Sharpe**: {d_sharpe:+.2f}  "
            f"**Δ Alpha**: {d_alpha*100:+.1f}pp"
        )
        # 解读
        if abs(d_sharpe) < 0.1:
            lines.append(
                "- **解读**: 两者非常接近 → 静态选因子的事后偏差不显著（因子集稳定，"
                "或样本期太短不足以显现 selection bias）."
            )
        elif d_sharpe < -0.1:
            lines.append(
                f"- **解读**: WF 显著输给静态 → 选因子环节确实存在 {d_sharpe:.2f} Sharpe "
                f"的事后偏差。实盘可达 Sharpe 应取 WF 值（更保守、更真实）."
            )
        else:
            lines.append(
                f"- **解读**: WF 反而优于静态 (+{d_sharpe:.2f} Sharpe) —— 罕见但可能发生："
                "静态选因子选了「事后看强但实际滚动期不稳定」的因子，WF 每窗局部最优反而更稳健."
            )
        lines.append("")

    # ============== 3. WF 选因子稳定性 ============== #
    lines.append("## 3. WF 选因子稳定性诊断")
    lines.append("")

    for h_name, history, top_k_label in [
        (f"WF IC Top {IC_TOP_K}", history_topk, f"Top {IC_TOP_K}"),
        ("WF IC + |t|≥2", history_t2, "|t|≥2 显著"),
    ]:
        if not history:
            continue
        lines.append(f"### 3.{1 if h_name.endswith(str(IC_TOP_K)) else 2} `{h_name}`")
        lines.append("")
        n_retrains = len(history)
        avg_n = np.mean([h["n_factors"] for h in history])
        lines.append(f"- 共 **{n_retrains}** 次 retrain，平均每窗选 **{avg_n:.1f}** 个因子")
        # 统计因子出现频率
        counter: dict[str, int] = {}
        for h in history:
            for f in h["factors"]:
                counter[f] = counter.get(f, 0) + 1
        top_freq = sorted(counter.items(), key=lambda x: x[1], reverse=True)
        always_in = [f for f, c in top_freq if c == n_retrains]
        often_in = [f for f, c in top_freq if c >= 0.7 * n_retrains]

        lines.append("")
        lines.append(f"**始终入选（100%）**: {', '.join(f'`{f}`' for f in always_in) if always_in else '（无）'}")
        lines.append("")
        lines.append(f"**经常入选（≥70%）**: {', '.join(f'`{f}`' for f in often_in) if often_in else '（无）'}")
        lines.append("")
        lines.append("**Top 10 因子出现频率**:")
        lines.append("")
        lines.append("| 因子 | 出现次数 | 比例 |")
        lines.append("|------|----------|------|")
        for f, c in top_freq[:10]:
            lines.append(f"| `{f}` | {c} | {c/n_retrains*100:.0f}% |")
        lines.append("")

    # ============== 4. vs 静态选因子 ============== #
    if static_top_factors:
        lines.append("## 4. 静态选因子（事后）参考")
        lines.append("")
        lines.append(f"用全样本 IC scan 选出的 Top {IC_TOP_K}（5d horizon）：")
        lines.append("")
        for f in static_top_factors:
            lines.append(f"- `{f}`")
        lines.append("")
        # 对比 WF top_k 中哪些"始终入选"的因子也在静态 Top 10
        if history_topk:
            counter = {}
            for h in history_topk:
                for f in h["factors"]:
                    counter[f] = counter.get(f, 0) + 1
            wf_always = {f for f, c in counter.items() if c == len(history_topk)}
            static_set = set(static_top_factors)
            overlap = wf_always & static_set
            wf_only = wf_always - static_set
            static_only = static_set - wf_always
            lines.append(
                f"**WF 100% 入选 与 静态 Top {IC_TOP_K} 重合度**:"
            )
            lines.append("")
            lines.append(f"- 共同（稳健核心信号）: {', '.join(f'`{f}`' for f in sorted(overlap)) if overlap else '（无）'}")
            lines.append(f"- WF 始终入选但静态未选: {', '.join(f'`{f}`' for f in sorted(wf_only)) if wf_only else '（无）'}")
            lines.append(f"- 静态选了但 WF 不稳定: {', '.join(f'`{f}`' for f in sorted(static_only)) if static_only else '（无）'}")
            lines.append("")

    # ============== 5. 结论 ============== #
    lines.append("## 5. 结论")
    lines.append("")

    if static_name in strategies and wf_name in strategies:
        s_sharpe = strategies[static_name]["metrics_net"]["sharpe"]
        w_sharpe = strategies[wf_name]["metrics_net"]["sharpe"]
        log_mv_strat = strategies.get("单 log_mv 反向")

        lines.append(
            f"1. **选因子环节的事后偏差 Δ Sharpe = {w_sharpe - s_sharpe:+.2f}**"
        )
        lines.append(
            f"   - 静态（事后选）= {s_sharpe:.2f}"
        )
        lines.append(
            f"   - WF（无事后偏差）= {w_sharpe:.2f}"
        )
        lines.append("")

        if log_mv_strat:
            lm_sharpe = log_mv_strat["metrics_net"]["sharpe"]
            lines.append(
                f"2. **单 `log_mv` 反向 Sharpe = {lm_sharpe:.2f}（最强 baseline）**"
            )
            if lm_sharpe > w_sharpe + 0.2:
                lines.append(
                    f"   - WF LGB 多因子 ({w_sharpe:.2f}) **仍然输给** 单 log_mv 反向. "
                    "即使去掉了选因子事后偏差，多因子组合依然不如直接用最强单因子."
                )
                lines.append(
                    "   - **核心原因**：LGB MSE 训练 + 弱因子噪声仍是主要问题（参考上一报告分析）；"
                    "选因子事后偏差只是次要矛盾."
                )
            elif lm_sharpe > w_sharpe:
                lines.append(
                    f"   - WF LGB ({w_sharpe:.2f}) 与 log_mv ({lm_sharpe:.2f}) 接近. "
                    "WF 选因子带来的稳健性接近了简单单因子."
                )
            else:
                lines.append(
                    f"   - WF LGB ({w_sharpe:.2f}) **超越** log_mv ({lm_sharpe:.2f}). "
                    "多因子学习确实带来了边际信息."
                )
            lines.append("")

    # WF 选因子稳定性洞察
    if history_topk:
        counter = {}
        for h in history_topk:
            for f in h["factors"]:
                counter[f] = counter.get(f, 0) + 1
        always_in = [f for f, c in counter.items() if c == len(history_topk)]
        if always_in:
            lines.append(
                f"3. **WF 100% 入选的稳定因子**: {', '.join(f'`{f}`' for f in always_in)} "
                f"—— 这些是**真正可信的预测因子**，建议作为下一步研究的核心标的."
            )
        else:
            n_70 = sum(1 for c in counter.values() if c >= 0.7 * len(history_topk))
            lines.append(
                f"3. **WF 因子稳定性偏弱**: 无任何因子 100% 入选；仅 {n_70} 个 ≥70% 入选率. "
                "说明 IC scan 在 120 天 train_window 上方差较大，选因子结果不稳定 → "
                "建议增大 train_window 或减小 retrain_freq 让选择更平滑."
            )
        lines.append("")

    # ============== 6. 注意事项 ============== #
    lines.append("## 6. 注意事项")
    lines.append("")
    lines.append(
        "- 本轮 IC scan 在 120 天 train_window 上跑，每窗约 51 × 115 ≈ 5900 行样本，"
        "对截面 IC t-stat 估计的统计置信度有限；扩大 train_window 可能让选因子结果更稳定."
    )
    lines.append(
        "- WF 选因子 + LGB 训练双层滚动 → 模型方差进一步增加；"
        "理想情况下应加 ``min_t_stat`` 门槛过滤噪声因子."
    )
    lines.append(
        "- **selection bias 不止一个来源**：本轮去掉了「用全样本 IC 选因子」，"
        "但 ``cs_data_688*.csv`` 股票池本身是当前科创板成分股（幸存者偏差），"
        "未涵盖退市/未上市的股票."
    )
    lines.append(
        "- 阈值 ``top_pct=0.2`` 与 ``retrain_freq=5`` 均未做敏感性扫描；"
        "可继续用 ``Robustness`` 模块叠加."
    )
    lines.append("")
    lines.append("---")
    lines.append("_Generated by `scripts/analyze_kcb50_wf_factor_selection.py`_")

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

    strategies: dict[str, dict] = {}

    # ===== 1. 静态 IC Top 10（事后偏差，重跑对照）===== #
    logger.info("【1】静态 IC Top %d（事后偏差版，对照）...", IC_TOP_K)
    static_scan = SignalDiagnostics.cross_section_ic_scan(
        panel, feature_cols, horizons=[IC_HORIZON], method="spearman",
    )
    static_top_factors = static_scan.head(IC_TOP_K)["factor"].tolist()
    logger.info("  静态 Top %d: %s", IC_TOP_K, static_top_factors)
    res_static = engine.walk_forward(
        factor_df=panel,
        feature_cols=static_top_factors,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW, retrain_freq=RETRAIN_FREQ, top_pct=TOP_PCT,
        min_train=MIN_TRAIN, min_test=MIN_TEST, min_stocks=MIN_STOCKS,
    )
    if res_static is not None:
        strategies["静态 IC Top 10（事后）"] = evaluate(res_static, bench, "静态 IC Top 10（事后）")

    # ===== 2. WF IC Top 10 ===== #
    logger.info("【2】WF IC Top %d（滚动重选，无事后偏差）...", IC_TOP_K)
    history_topk: list[dict] = []
    base_sel_topk = make_ic_topk_selector(
        feature_cols, horizon=IC_HORIZON, top_k=IC_TOP_K, method="spearman",
    )
    res_wf_topk = engine.walk_forward(
        factor_df=panel,
        feature_cols=feature_cols,  # 全 45 候选
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW, retrain_freq=RETRAIN_FREQ, top_pct=TOP_PCT,
        min_train=MIN_TRAIN, min_test=MIN_TEST, min_stocks=MIN_STOCKS,
        feature_selector=make_tracking_selector(base_sel_topk, history_topk),
    )
    if res_wf_topk is not None:
        strategies[f"WF IC Top {IC_TOP_K}"] = evaluate(
            res_wf_topk, bench, f"WF IC Top {IC_TOP_K}",
        )

    # ===== 3. WF IC + min_t_stat=2 ===== #
    logger.info("【3】WF IC + |t|≥2 显著门槛...")
    history_t2: list[dict] = []
    base_sel_t2 = make_ic_topk_selector(
        feature_cols, horizon=IC_HORIZON, top_k=IC_TOP_K,
        method="spearman", min_t_stat=2.0,
    )
    res_wf_t2 = engine.walk_forward(
        factor_df=panel,
        feature_cols=feature_cols,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW, retrain_freq=RETRAIN_FREQ, top_pct=TOP_PCT,
        min_train=MIN_TRAIN, min_test=MIN_TEST, min_stocks=MIN_STOCKS,
        feature_selector=make_tracking_selector(base_sel_t2, history_t2),
    )
    if res_wf_t2 is not None:
        strategies["WF IC + |t|≥2"] = evaluate(res_wf_t2, bench, "WF IC + |t|≥2")

    # ===== 4. 全 45 因子 LGB ===== #
    logger.info("【4】LGB 全 45 因子（不选）...")
    res_full = engine.walk_forward(
        factor_df=panel,
        feature_cols=feature_cols,
        label_col=IC_HORIZON,
        train_window=TRAIN_WINDOW, retrain_freq=RETRAIN_FREQ, top_pct=TOP_PCT,
        min_train=MIN_TRAIN, min_test=MIN_TEST, min_stocks=MIN_STOCKS,
    )
    if res_full is not None:
        strategies["LGB 全 45 因子"] = evaluate(res_full, bench, "LGB 全 45 因子")

    # ===== 5. 单 log_mv 反向 baseline ===== #
    logger.info("【5】单 log_mv 反向 baseline...")
    res_lm = factor_cross_section_backtest(
        panel, "log_mv", cost, top_pct=TOP_PCT, direction="long_low",
        min_stocks=MIN_STOCKS,
    )
    if res_lm is not None:
        strategies["单 log_mv 反向"] = evaluate(res_lm, bench, "单 log_mv 反向")

    # 输出
    plot_panels(panel, strategies, history_topk, history_t2)
    render_markdown(
        panel, strategies, history_topk, history_t2, static_top_factors,
    )

    # 终端
    print("\n" + "=" * 90)
    print("科创板 LGB 横截面 —— 因子选择 walk-forward 化")
    print("=" * 90)
    print(f"\n{'策略':35s}  {'Sharpe':>8s}  {'年化':>8s}  {'回撤':>8s}  "
          f"{'Alpha':>8s}  {'IR':>6s}  {'p':>6s}")
    print("-" * 90)
    for name, info in strategies.items():
        m = info["metrics_net"]
        ab = info["alpha_beta"]
        sig = info["significance"]
        print(
            f"{name:35s}  {m['sharpe']:+8.2f}  "
            f"{m['annual']*100:+7.1f}%  {m['max_dd']*100:+7.1f}%  "
            f"{ab['alpha_annual']*100:+7.1f}%  {ab['info_ratio']:+6.2f}  "
            f"{sig['p_value']:6.3f}"
        )

    # 选因子事后偏差量化
    if "静态 IC Top 10（事后）" in strategies and f"WF IC Top {IC_TOP_K}" in strategies:
        s = strategies["静态 IC Top 10（事后）"]["metrics_net"]["sharpe"]
        w = strategies[f"WF IC Top {IC_TOP_K}"]["metrics_net"]["sharpe"]
        print(f"\n选因子事后偏差: 静态 {s:.2f} → WF {w:.2f}  Δ = {w - s:+.2f}")

    # WF 因子稳定性快速一览
    if history_topk:
        counter: dict[str, int] = {}
        for h in history_topk:
            for f in h["factors"]:
                counter[f] = counter.get(f, 0) + 1
        n_ret = len(history_topk)
        always_in = [f for f, c in counter.items() if c == n_ret]
        print(f"\nWF Top {IC_TOP_K} 共 {n_ret} 次 retrain，始终入选的因子: "
              f"{always_in if always_in else '（无）'}")

    print(f"\n报告: {MD_PATH}")
    print(f"图表: {PNG_PATH}")


if __name__ == "__main__":
    main()
