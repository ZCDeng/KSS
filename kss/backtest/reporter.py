"""Markdown 诊断报告生成器.

把 walk_forward 主结果 + diagnostics / benchmark / robustness 子结果合并成一份
人类可读的 Markdown 报告，便于直接发送给 notifier 或归档.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from kss.backtest.metrics import Metrics

logger = logging.getLogger(__name__)


class BacktestReporter:
    """回测诊断报告生成器.

    用法：

    .. code-block:: python

        rep = BacktestReporter(
            result_df=res,
            diagnostics={"ic_summary": {...}, "quantile_df": ..., "long_short": ..., "ic_decay": ...},
            significance={...},
            benchmark={"alpha_beta": {...}, "excess_curve": ..., "excess_max_dd": ...},
            robustness={"cost_sensitivity": ..., "regime_split": ...},
        )
        rep.to_markdown("./report.md")
    """

    def __init__(
        self,
        result_df: pd.DataFrame,
        diagnostics: Mapping[str, Any] | None = None,
        significance: Mapping[str, Any] | None = None,
        benchmark: Mapping[str, Any] | None = None,
        robustness: Mapping[str, Any] | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """初始化.

        Args:
            result_df: ``walk_forward`` 返回的回测结果.
            diagnostics: ``SignalDiagnostics`` 输出汇总：
                可包含 ``ic_summary`` / ``quantile_df`` / ``long_short`` / ``ic_decay`` / ``monotonicity``.
            significance: ``Significance.sharpe_significance`` 等输出.
            benchmark: ``Benchmark.alpha_beta`` 等输出 + ``excess_max_dd``.
            robustness: ``Robustness.cost_sensitivity`` / ``regime_split`` 等输出.
            meta: 其他元信息（pool / period / model 名等），写入报告抬头.
        """
        self.result_df = result_df
        self.diagnostics = diagnostics or {}
        self.significance = significance or {}
        self.benchmark = benchmark or {}
        self.robustness = robustness or {}
        self.meta = meta or {}

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    def to_markdown(self, output_path: str | Path) -> str:
        """生成 Markdown 文本并写盘.

        Args:
            output_path: 输出文件路径.

        Returns:
            Markdown 字符串内容.
        """
        sections: list[str] = []
        sections.append(self._render_header())
        sections.append(self._render_summary())
        if self.diagnostics:
            sections.append(self._render_diagnostics())
        if self.significance:
            sections.append(self._render_significance())
        if self.benchmark:
            sections.append(self._render_benchmark())
        if self.robustness:
            sections.append(self._render_robustness())
        sections.append(self._render_footer())

        content = "\n\n".join(s for s in sections if s).rstrip() + "\n"
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        logger.info("诊断报告已写入: %s", out)
        return content

    # ------------------------------------------------------------------ #
    # 章节
    # ------------------------------------------------------------------ #

    def _render_header(self) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"# KSS 回测诊断报告", f"_生成时间: {ts}_"]
        if self.meta:
            lines.append("")
            for k, v in self.meta.items():
                lines.append(f"- **{k}**: {v}")
        return "\n".join(lines)

    def _render_summary(self) -> str:
        lines = ["## 1. 收益摘要"]
        if self.result_df.empty:
            lines.append("无有效回测结果.")
            return "\n".join(lines)

        m_gross = Metrics.calc(self.result_df["gross_return"])
        m_net = Metrics.calc(self.result_df["net_return"])
        lines.append("")
        lines.append("| 指标 | 毛收益 | 净收益 |")
        lines.append("|------|--------|--------|")
        keys = [
            ("total", "总收益", "%"),
            ("annual", "年化", "%"),
            ("sharpe", "夏普", ""),
            ("max_dd", "最大回撤", "%"),
            ("calmar", "Calmar", ""),
            ("win", "胜率", "%"),
            ("sortino", "Sortino", ""),
            ("downside_dev", "下行波动率", "%"),
            ("skew", "偏度", ""),
            ("kurt", "峰度", ""),
            ("n", "交易日", "天"),
        ]
        for key, label, suffix in keys:
            v_g = m_gross.get(key, float("nan"))
            v_n = m_net.get(key, float("nan"))
            lines.append(
                f"| {label} | {_fmt(v_g, suffix)} | {_fmt(v_n, suffix)} |"
            )
        lines.append("")
        lines.append(
            f"- 平均换手率: {self.result_df['turnover'].mean():.1%}  "
            f"  累计成本: {self.result_df['cost'].sum()*100:.2f}%"
        )
        return "\n".join(lines)

    def _render_diagnostics(self) -> str:
        lines = ["## 2. 信号质量诊断"]

        ic_summary = self.diagnostics.get("ic_summary")
        if ic_summary:
            lines.append("")
            lines.append("### 2.1 Information Coefficient (Rank IC)")
            lines.append("")
            lines.append("| 指标 | 值 |")
            lines.append("|------|----|")
            lines.append(f"| IC 均值 | {ic_summary.get('ic_mean', float('nan')):.4f} |")
            lines.append(f"| IC 标准差 | {ic_summary.get('ic_std', float('nan')):.4f} |")
            lines.append(f"| IR (年化) | {ic_summary.get('ir', float('nan')):.2f} |")
            lines.append(f"| IC t-stat | {ic_summary.get('ic_t_stat', float('nan')):.2f} |")
            lines.append(
                f"| IC > 0 占比 | {ic_summary.get('ic_positive_rate', float('nan')):.1%} |"
            )
            lines.append(
                f"| IC 最长连负天数 | {int(ic_summary.get('ic_max_consec_neg', 0))} |"
            )
            lines.append(f"| 有效日数 | {int(ic_summary.get('n', 0))} |")

        quantile_df = self.diagnostics.get("quantile_df")
        if isinstance(quantile_df, pd.DataFrame) and not quantile_df.empty:
            lines.append("")
            lines.append("### 2.2 分位数累计收益")
            lines.append("")
            lines.append("| 分位 | 累计收益 | 日均收益 |")
            lines.append("|------|----------|----------|")
            cum = (1 + quantile_df.fillna(0)).prod() - 1
            for col in quantile_df.columns:
                lines.append(
                    f"| {col} | {cum[col]*100:.1f}% | "
                    f"{quantile_df[col].mean()*100:.3f}% |"
                )
            mono = self.diagnostics.get("monotonicity")
            if mono is not None:
                lines.append("")
                lines.append(f"- **单调性得分**: {mono:.3f}（接近 +1 = 单调有效）")

        long_short = self.diagnostics.get("long_short")
        if isinstance(long_short, pd.Series) and not long_short.empty:
            m = Metrics.calc(long_short)
            lines.append("")
            lines.append("### 2.3 多空价差（最高分位 - 最低分位）")
            lines.append("")
            lines.append(
                f"- 年化: {m.get('annual', 0)*100:.1f}%  "
                f"夏普: {m.get('sharpe', 0):.2f}  "
                f"最大回撤: {m.get('max_dd', 0)*100:.1f}%"
            )

        ic_decay = self.diagnostics.get("ic_decay")
        if isinstance(ic_decay, pd.DataFrame) and not ic_decay.empty:
            lines.append("")
            lines.append("### 2.4 IC 衰减（不同 horizon）")
            lines.append("")
            lines.append("| Horizon | IC 均值 | IR | IC > 0 占比 |")
            lines.append("|---------|---------|----|-----------|")
            for _, row in ic_decay.iterrows():
                lines.append(
                    f"| {row['horizon']} | {row['ic_mean']:.4f} | "
                    f"{row['ir']:.2f} | {row['ic_positive_rate']:.1%} |"
                )

        return "\n".join(lines) if len(lines) > 1 else ""

    def _render_significance(self) -> str:
        lines = ["## 3. 统计显著性"]
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|------|----|")
        for k, label in [
            ("sharpe", "Sharpe"),
            ("t_stat", "日均收益 t-stat"),
            ("p_value", "p 值"),
            ("nw_se", "Newey-West SE"),
            ("deflated_sharpe", "Deflated Sharpe (PSR)"),
            ("n", "样本数"),
        ]:
            v = self.significance.get(k)
            if v is None:
                continue
            if k == "n":
                lines.append(f"| {label} | {int(v)} |")
            elif k in ("p_value",):
                lines.append(f"| {label} | {v:.4f} |")
            else:
                lines.append(f"| {label} | {v:.4f} |")
        if "annual_return_ci" in self.significance:
            lo, hi = self.significance["annual_return_ci"]
            lines.append(f"| 年化收益 95% CI | [{lo*100:.1f}%, {hi*100:.1f}%] |")
        return "\n".join(lines)

    def _render_benchmark(self) -> str:
        ab = self.benchmark.get("alpha_beta") or {}
        lines = ["## 4. 基准对比与归因"]
        if not ab:
            return ""
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|------|----|")
        lines.append(f"| Alpha (年化) | {ab.get('alpha_annual', 0)*100:.2f}% |")
        lines.append(f"| Beta | {ab.get('beta', 0):.3f} |")
        if "alpha_t_stat" in ab and pd.notna(ab["alpha_t_stat"]):
            lines.append(f"| Alpha t-stat | {ab['alpha_t_stat']:.2f} |")
            lines.append(f"| Alpha p-value | {ab.get('alpha_p_value', float('nan')):.4f} |")
        lines.append(f"| R² | {ab.get('r_squared', 0):.3f} |")
        lines.append(f"| Information Ratio | {ab.get('info_ratio', 0):.2f} |")
        excess_dd = self.benchmark.get("excess_max_dd")
        if excess_dd is not None:
            lines.append(f"| 超额收益最大回撤 | {excess_dd*100:.1f}% |")
        return "\n".join(lines)

    def _render_robustness(self) -> str:
        lines = ["## 5. 稳健性扫描"]
        any_section = False

        cost_sens = self.robustness.get("cost_sensitivity")
        if isinstance(cost_sens, pd.DataFrame) and not cost_sens.empty:
            any_section = True
            lines.append("")
            lines.append("### 5.1 成本敏感度")
            lines.append("")
            lines.append("| buy_cost | sell_cost | slippage_bps | 年化净收益 | 夏普 | 最大回撤 |")
            lines.append("|----------|-----------|--------------|------------|------|----------|")
            for _, row in cost_sens.iterrows():
                lines.append(
                    f"| {row['buy_cost']:.4f} | {row['sell_cost']:.4f} | "
                    f"{row['slippage_bps']:.0f} | {row['annual_net']*100:.1f}% | "
                    f"{row['sharpe_net']:.2f} | {row['max_dd']*100:.1f}% |"
                )

        regime = self.robustness.get("regime_split")
        if isinstance(regime, pd.DataFrame) and not regime.empty:
            any_section = True
            lines.append("")
            lines.append("### 5.2 市场状态分段")
            lines.append("")
            lines.append("| 状态 | 天数 | 策略年化 | 策略夏普 | 基准年化 | Alpha |")
            lines.append("|------|------|----------|----------|----------|-------|")
            for _, row in regime.iterrows():
                lines.append(
                    f"| {row['regime']} | {int(row['n_days'])} | "
                    f"{row['strat_annual']*100:.1f}% | {row['strat_sharpe']:.2f} | "
                    f"{row['bench_annual']*100:.1f}% | {row['alpha']*100:+.1f}% |"
                )

        return "\n".join(lines) if any_section else ""

    @staticmethod
    def _render_footer() -> str:
        return "---\n_Generated by KSS BacktestReporter_"


def _fmt(val: Any, suffix: str) -> str:
    """单元格格式化."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(v):
        return "—"
    if suffix == "%":
        return f"{v*100:.2f}%"
    if suffix == "天":
        return f"{int(v)}"
    return f"{v:.3f}"
