"""横截面 ranking 选股预测 —— 接续 v3 实验认知更新.

与 :class:`~kss.prediction.daily_forecast.DailyForecast` 的设计差异：

- ``DailyForecast``：用单一已训练 LGB ``model.predict`` 打分 → 单股阈值切信号.
  经过 8 轮实验证伪：在 A 股弱信号截面上 LGB MSE 训练-排序测试目标错配，
  Sharpe 普遍跑负.
- ``CrossSectionalForecast``：直接用因子值（如 ``log_mv``）做截面 rank，
  选 Top Pct 等权持有. 不训练任何模型，与
  :func:`~kss.backtest.cross_section.factor_cross_section_backtest` 同构.
  log_mv 反向是 KSS 体系内唯一通过 :meth:`Significance.is_deployable`
  (``strategy_family="prior"``) 的策略.

支持两种调用模式：

1. ``predict_pool(panel)``：对整池打分排名 → 返回 Top Pct + 计划权重；
2. ``predict_single(panel, "688017.SH")``：单股查询，返回该股在截面内的 rank
   与是否入选 Top Pct，便于人工复盘.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import pandas as pd

from kss.backtest.cost_model import ExecutionModel

logger = logging.getLogger(__name__)

Direction = Literal["long_high", "long_low"]
Verdict = Literal["buy", "hold", "ignore"]


class CrossSectionalForecast:
    """横截面 ranking 选股预测器.

    Attributes:
        factor_col: 作为截面 score 的因子列名（默认 ``"log_mv"``）.
        direction: ``"long_high"`` 取高分（趋势 / 量价类）；``"long_low"`` 取低分
            （估值 / size 类，反向使用）.
        top_pct: 选股比例.
        execution: 可选 :class:`ExecutionModel`. 启用涨停过滤 + 部分成交.
        freshness_days: 数据陈旧度阈值（日历日）；超过此值的股票被剔除.
        min_stocks: 截面最少股票数；低于此值返回空.
    """

    def __init__(
        self,
        factor_col: str = "log_mv",
        direction: Direction = "long_low",
        top_pct: float = 0.2,
        execution: ExecutionModel | None = None,
        freshness_days: int = 7,
        min_stocks: int = 10,
        date_col: str = "trade_date",
        symbol_col: str = "symbol",
    ) -> None:
        """初始化预测器.

        Args:
            factor_col: 截面 score 因子列.
            direction: 选股方向.
            top_pct: 选股比例（0~1）.
            execution: 实盘成交模型.
            freshness_days: 数据陈旧度阈值（日历日）.
            min_stocks: 截面最少股票数.
            date_col / symbol_col: 列名.

        Raises:
            ValueError: ``top_pct`` 越界 / ``direction`` 非法.
        """
        if not 0 < top_pct <= 1:
            raise ValueError(f"top_pct 必须在 (0, 1] 内，收到: {top_pct}")
        if direction not in ("long_high", "long_low"):
            raise ValueError(f"direction 必须是 long_high|long_low, 收到 {direction!r}")
        self.factor_col = factor_col
        self.direction = direction
        self.top_pct = float(top_pct)
        self.execution = execution
        self.freshness_days = int(freshness_days)
        self.min_stocks = int(min_stocks)
        self.date_col = date_col
        self.symbol_col = symbol_col

    # ------------------------------------------------------------------ #
    # 单日全市场快照（用于内部）
    # ------------------------------------------------------------------ #

    def _resolve_snapshot(
        self,
        panel: pd.DataFrame,
        target_date: pd.Timestamp | None = None,
    ) -> tuple[pd.DataFrame, pd.Timestamp]:
        """从 panel 中取截至 ``target_date`` 的最新行情快照（每股 1 行）.

        - ``target_date=None``：取每只股票最后一行；
        - ``target_date`` 指定：每只股票取 ``<= target_date`` 的最后一行；
        - 应用 ``freshness_days`` 过滤陈旧数据.

        Returns:
            (snapshot DataFrame, 实际取到的 target_date).
        """
        required = {self.date_col, self.symbol_col, self.factor_col}
        missing = required - set(panel.columns)
        if missing:
            raise ValueError(f"panel 缺少必需列: {missing}")

        if target_date is not None:
            sub = panel[panel[self.date_col] <= target_date]
        else:
            sub = panel
        if sub.empty:
            raise ValueError(f"target_date={target_date} 下 panel 为空")

        # 每股取最新行
        sub_sorted = sub.sort_values([self.symbol_col, self.date_col])
        snap = sub_sorted.groupby(self.symbol_col, as_index=False).tail(1).copy()

        # freshness 过滤
        ref_date = target_date if target_date is not None else snap[self.date_col].max()
        ref_date = pd.Timestamp(ref_date)
        gap_days = (ref_date - snap[self.date_col]).dt.days
        fresh_mask = gap_days <= self.freshness_days
        n_stale = int((~fresh_mask).sum())
        if n_stale:
            logger.info(
                "剔除 %d 只数据陈旧的股票（gap > %d 日历日）",
                n_stale, self.freshness_days,
            )
        snap = snap.loc[fresh_mask].reset_index(drop=True)
        return snap, ref_date

    # ------------------------------------------------------------------ #
    # 池级预测：选 Top Pct
    # ------------------------------------------------------------------ #

    def predict_pool(
        self,
        panel: pd.DataFrame,
        target_date: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """对整池打分排名，返回 Top Pct 选股名单.

        Args:
            panel: 含 ``date_col / symbol_col / factor_col`` 的面板;
                若启用 ``execution`` 还需含 ``pre_close / open / amount``.
            target_date: 目标日期；``None`` 取 panel 中最新.

        Returns:
            DataFrame，列：

            - ``symbol``           股票代码
            - ``trade_date``       该股的最新数据日（freshness 过滤后）
            - ``factor_value``     因子值
            - ``rank_pct``         截面 rank 百分位（0~1，靠前的更优）
            - ``rank_position``    截面整数排名（1 = 最优）
            - ``in_top``           ``True`` = 入选 Top Pct
            - ``planned_weight``   计划权重（execution 启用后按 max_tradable_ratio 调权）

            非 Top 股票也包含在内便于"查全市场" 单股回看；
            ``DataFrame.attrs["target_date"]`` 记录实际预测日.
        """
        snap, ref_date = self._resolve_snapshot(panel, target_date)
        snap = snap.dropna(subset=[self.factor_col])
        if len(snap) < self.min_stocks:
            logger.warning(
                "可用股票数 %d < min_stocks=%d，返回空",
                len(snap), self.min_stocks,
            )
            out = snap.iloc[0:0].copy()
            out.attrs["target_date"] = ref_date
            return out

        # rank：long_high 用 pct=True 升序（高值靠尾 → top 取尾），long_low 反之
        if self.direction == "long_high":
            snap["rank_pct"] = snap[self.factor_col].rank(method="first", pct=True)
            snap["rank_position"] = (
                snap[self.factor_col].rank(method="first", ascending=False).astype(int)
            )
            top_mask = snap["rank_pct"] >= (1 - self.top_pct)
        else:
            # long_low：值越小越优
            snap["rank_pct"] = snap[self.factor_col].rank(method="first", pct=True)
            snap["rank_position"] = (
                snap[self.factor_col].rank(method="first", ascending=True).astype(int)
            )
            top_mask = snap["rank_pct"] <= self.top_pct
        snap["in_top"] = top_mask

        # execution 涨停过滤 → 重选 top（与 BacktestEngine 同口径）
        if self.execution is not None and top_mask.any():
            top_df = snap.loc[top_mask]
            tradable_top = self.execution.filter_tradable(
                top_df, side="buy", symbol_col=self.symbol_col,
            )
            if len(tradable_top) < self.min_stocks:
                # 降级：扩展到全可成交池再取 top_pct
                tradable_all = self.execution.filter_tradable(
                    snap, side="buy", symbol_col=self.symbol_col,
                )
                if len(tradable_all) >= self.min_stocks:
                    n_take = max(
                        self.min_stocks,
                        int(round(len(tradable_all) * self.top_pct)),
                    )
                    if self.direction == "long_high":
                        tradable_top = tradable_all.nlargest(n_take, self.factor_col)
                    else:
                        tradable_top = tradable_all.nsmallest(n_take, self.factor_col)
            # 重写 in_top
            snap["in_top"] = snap.index.isin(tradable_top.index)

        # 计划权重
        top_rows = snap[snap["in_top"]]
        if len(top_rows) == 0:
            snap["planned_weight"] = 0.0
        elif self.execution is not None:
            n_top = len(top_rows)
            per_position = 1.0 / n_top
            ratios = top_rows.apply(
                lambda r: self.execution.max_tradable_ratio(r, per_position),
                axis=1,
            ).astype(float)
            weights = ratios / ratios.sum() if ratios.sum() > 0 else ratios
            snap["planned_weight"] = 0.0
            snap.loc[top_rows.index, "planned_weight"] = weights.values
        else:
            snap["planned_weight"] = 0.0
            snap.loc[top_rows.index, "planned_weight"] = 1.0 / len(top_rows)

        out_cols = [
            self.symbol_col, self.date_col, self.factor_col,
            "rank_pct", "rank_position", "in_top", "planned_weight",
        ]
        out = snap[out_cols].rename(columns={self.factor_col: "factor_value"})
        out = out.sort_values("rank_position").reset_index(drop=True)
        out.attrs["target_date"] = ref_date
        out.attrs["factor_col"] = self.factor_col
        out.attrs["direction"] = self.direction
        out.attrs["top_pct"] = self.top_pct
        return out

    # ------------------------------------------------------------------ #
    # 单股查询
    # ------------------------------------------------------------------ #

    def predict_single(
        self,
        panel: pd.DataFrame,
        symbol: str,
        target_date: pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        """单股查询：返回该股在截面内的 rank + 是否入选 Top Pct.

        Args:
            panel: 全池面板（必须含目标股票）.
            symbol: 目标股票代码（与 ``symbol_col`` 列匹配）.
            target_date: 目标日期.

        Returns:
            字典字段：

            - ``symbol``           查询的代码
            - ``trade_date``       数据日
            - ``factor_value``     该股因子值
            - ``rank_pct``         截面 rank 百分位
            - ``rank_position``    整数排名（1 = 最优）
            - ``n_universe``       截面有效股票数
            - ``in_top``           是否入选 Top Pct
            - ``verdict``          ``"buy" / "hold" / "ignore"``
            - ``planned_weight``   若 in_top，计划权重
            - ``not_in_universe``  若股票未在 freshness 过滤后的截面，置 True
        """
        pool = self.predict_pool(panel, target_date=target_date)
        if pool.empty:
            return {
                "symbol": symbol,
                "trade_date": pool.attrs.get("target_date"),
                "factor_value": float("nan"),
                "rank_pct": float("nan"),
                "rank_position": -1,
                "n_universe": 0,
                "in_top": False,
                "verdict": "ignore",
                "planned_weight": 0.0,
                "not_in_universe": True,
            }
        row = pool[pool[self.symbol_col] == symbol]
        if row.empty:
            # 未在截面（freshness 过滤剔除 / 不在股票池）
            return {
                "symbol": symbol,
                "trade_date": pool.attrs.get("target_date"),
                "factor_value": float("nan"),
                "rank_pct": float("nan"),
                "rank_position": -1,
                "n_universe": len(pool),
                "in_top": False,
                "verdict": "ignore",
                "planned_weight": 0.0,
                "not_in_universe": True,
            }
        r = row.iloc[0]
        in_top = bool(r["in_top"])
        # 三档 verdict：Top 取 buy；非 Top 但排名前 1/3 → hold；其余 ignore
        if in_top:
            verdict: Verdict = "buy"
        elif r["rank_pct"] <= self.top_pct * 1.5:
            verdict = "hold"
        else:
            verdict = "ignore"
        return {
            "symbol": symbol,
            "trade_date": r[self.date_col],
            "factor_value": float(r["factor_value"]),
            "rank_pct": float(r["rank_pct"]),
            "rank_position": int(r["rank_position"]),
            "n_universe": len(pool),
            "in_top": in_top,
            "verdict": verdict,
            "planned_weight": float(r["planned_weight"]),
            "not_in_universe": False,
        }

    # ------------------------------------------------------------------ #
    # 格式化输出
    # ------------------------------------------------------------------ #

    def format_pool_markdown(self, pool: pd.DataFrame, max_rows: int = 20) -> str:
        """整池预测 → Markdown 表格.

        Args:
            pool: :meth:`predict_pool` 返回的 DataFrame.
            max_rows: 最多展示前 max_rows 行（默认 20）.

        Returns:
            Markdown 字符串.
        """
        if pool.empty:
            return "# 横截面选股\n\n（无可选股票）"

        date = pool.attrs.get("target_date")
        factor = pool.attrs.get("factor_col", self.factor_col)
        direction = pool.attrs.get("direction", self.direction)
        top_pct = pool.attrs.get("top_pct", self.top_pct)
        date_str = pd.Timestamp(date).date() if date is not None else "?"
        dir_label = "高分买入 (long_high)" if direction == "long_high" else "低分买入 / 反向 (long_low)"

        in_top = pool[pool["in_top"]]
        has_name = "stock_name" in pool.columns
        has_industry = "industry" in pool.columns
        has_concept = "concept" in pool.columns

        cols = ["排名", "代码", factor, "rank%", "计划权重"]
        if has_name:
            cols.append("名称")
        if has_industry:
            cols.append("行业")
        if has_concept:
            cols.append("概念板块")
        header_cols = "| " + " | ".join(cols) + " |"
        sep_cols = "|" + "|".join("-" * (len(c) + 2) for c in cols) + "|"

        lines = [
            f"# {date_str} 横截面选股 (`{factor}` / {dir_label})",
            "",
            f"**策略**：截面 rank 取 Top {top_pct:.0%}，等权（或按可成交比例调权）",
            f"**入选**：{len(in_top)} 只 / 全池 {len(pool)} 只",
            "",
            "## 买入名单（T+1 开盘建仓）",
            "",
            header_cols,
            sep_cols,
        ]
        for _, r in in_top.head(max_rows).iterrows():
            cells = [
                str(int(r["rank_position"])),
                f"`{r[self.symbol_col]}`",
                f"{r['factor_value']:.3f}",
                f"{r['rank_pct']*100:.1f}%",
                f"{r['planned_weight']*100:.1f}%",
            ]
            if has_name:
                cells.append(str(r.get("stock_name", "")))
            if has_industry:
                cells.append(str(r.get("industry", "")))
            if has_concept:
                cells.append(str(r.get("concept", "")))
            lines.append("| " + " | ".join(cells) + " |")
        if len(in_top) > max_rows:
            lines.append(f"| … | 还有 {len(in_top) - max_rows} 只省略 | | | |")
        lines.append("")
        return "\n".join(lines)

    def format_single_markdown(self, info: dict[str, Any]) -> str:
        """单股查询 → Markdown.

        Args:
            info: :meth:`predict_single` 返回的字典.

        Returns:
            Markdown 字符串.
        """
        sym = info["symbol"]
        date = info.get("trade_date")
        date_str = pd.Timestamp(date).date() if date is not None else "?"
        if info.get("not_in_universe"):
            return (
                f"# {sym} 截面查询 ({date_str})\n\n"
                f"❌ 该股票不在当前截面内"
                f"（可能数据陈旧 / 不在股票池 / freshness > {self.freshness_days} 日）"
            )
        verdict_label = {
            "buy": "🟢 **入选 Top Pct，建议买入**",
            "hold": "🟡 排名靠前但未入选，观察",
            "ignore": "⚪ 排名靠后，本期忽略",
        }[info["verdict"]]
        return "\n".join([
            f"# {sym} 截面查询 ({date_str})",
            "",
            f"- **因子值** (`{self.factor_col}`): {info['factor_value']:.3f}",
            f"- **截面排名**: 第 **{info['rank_position']}** 位 "
            f"/ 共 {info['n_universe']} 只 (rank_pct={info['rank_pct']*100:.1f}%)",
            f"- **策略方向**: {self.direction} (Top {self.top_pct:.0%})",
            f"- **判定**: {verdict_label}",
            f"- **计划权重**: {info['planned_weight']*100:.2f}%",
            "",
        ])


__all__ = ["CrossSectionalForecast", "Direction", "Verdict"]
