"""CrossSectionalForecast 单元测试.

覆盖：池预测 / 单股查询 / freshness 过滤 / execution 集成 / 边界条件 / Markdown 输出.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kss.backtest.cost_model import ExecutionModel
from kss.prediction.cross_sectional_forecast import CrossSectionalForecast


# --------------------------------------------------------------------------- #
# 测试 fixture
# --------------------------------------------------------------------------- #


def _make_panel(
    n_dates: int = 30,
    n_stocks: int = 20,
    with_exec_cols: bool = False,
    seed: int = 0,
) -> pd.DataFrame:
    """合成 panel：每只股票 n_dates 天行情 + log_mv."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range("2024-01-01", periods=n_dates):
        for s in range(n_stocks):
            row = {
                "trade_date": d,
                "symbol": f"688{s:03d}.SH",
                # log_mv：每只股票一个稳定的基础值 + 小噪声
                "log_mv": 22.0 + s * 0.3 + rng.normal(0, 0.05),
            }
            if with_exec_cols:
                row["pre_close"] = 10.0 + rng.uniform(-1, 1)
                row["open"] = row["pre_close"] * (1 + rng.normal(0, 0.01))
                row["close"] = row["open"] * (1 + rng.normal(0, 0.02))
                row["amount"] = rng.uniform(50_000, 500_000)
            rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 初始化与参数校验
# --------------------------------------------------------------------------- #


class TestInit:

    def test_invalid_top_pct_raises(self) -> None:
        with pytest.raises(ValueError, match="top_pct"):
            CrossSectionalForecast(top_pct=0.0)
        with pytest.raises(ValueError, match="top_pct"):
            CrossSectionalForecast(top_pct=1.5)

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            CrossSectionalForecast(direction="invalid")  # type: ignore[arg-type]

    def test_default_attrs(self) -> None:
        f = CrossSectionalForecast()
        assert f.factor_col == "log_mv"
        assert f.direction == "long_low"
        assert f.top_pct == 0.2


# --------------------------------------------------------------------------- #
# predict_pool
# --------------------------------------------------------------------------- #


class TestPredictPool:

    def test_returns_full_universe(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.2)
        out = f.predict_pool(panel)
        # 应返回全池每股一行
        assert len(out) == 20
        assert out["in_top"].sum() == 4  # 20 × 0.2

    def test_long_low_picks_smallest(self) -> None:
        """long_low 应选 factor_value 最小的几只."""
        panel = _make_panel(n_dates=10, n_stocks=20, seed=1)
        f = CrossSectionalForecast(direction="long_low", top_pct=0.2)
        out = f.predict_pool(panel)
        top = out[out["in_top"]]
        # 入选股票的 factor_value 都应 ≤ 非入选股票的最小值
        non_top_min = out[~out["in_top"]]["factor_value"].min()
        assert top["factor_value"].max() <= non_top_min

    def test_long_high_picks_largest(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20, seed=2)
        f = CrossSectionalForecast(direction="long_high", top_pct=0.2)
        out = f.predict_pool(panel)
        top = out[out["in_top"]]
        non_top_max = out[~out["in_top"]]["factor_value"].max()
        assert top["factor_value"].min() >= non_top_max

    def test_weights_sum_to_one(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.25)
        out = f.predict_pool(panel)
        total = out["planned_weight"].sum()
        assert abs(total - 1.0) < 1e-9

    def test_target_date_attr(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20)
        target = pd.Timestamp("2024-01-05")
        f = CrossSectionalForecast()
        out = f.predict_pool(panel, target_date=target)
        # attrs 含 target_date
        assert "target_date" in out.attrs
        # 取到的快照日不超过 target
        assert out[f.date_col].max() <= target

    def test_missing_required_column_raises(self) -> None:
        panel = pd.DataFrame({"trade_date": [], "symbol": []})  # 缺 log_mv
        f = CrossSectionalForecast()
        with pytest.raises(ValueError, match="缺少必需列"):
            f.predict_pool(panel)


# --------------------------------------------------------------------------- #
# freshness 过滤
# --------------------------------------------------------------------------- #


class TestFreshness:

    def test_stale_stocks_removed(self) -> None:
        """某股票数据只到 1 周前 → 在 target_date 时被剔除."""
        panel = _make_panel(n_dates=30, n_stocks=20)
        # 把 688000.SH 的数据截断到第 5 天
        cutoff_date = panel["trade_date"].unique()[5]
        mask = (panel["symbol"] == "688000.SH") & (panel["trade_date"] > cutoff_date)
        panel = panel[~mask].reset_index(drop=True)

        target = panel["trade_date"].max()
        f = CrossSectionalForecast(freshness_days=7)
        out = f.predict_pool(panel, target_date=target)
        # 688000 应被剔除
        assert "688000.SH" not in out["symbol"].values
        assert len(out) == 19

    def test_lenient_freshness_keeps_all(self) -> None:
        """freshness=365 时即便数据陈旧也保留."""
        panel = _make_panel(n_dates=30, n_stocks=20)
        cutoff_date = panel["trade_date"].unique()[5]
        mask = (panel["symbol"] == "688000.SH") & (panel["trade_date"] > cutoff_date)
        panel = panel[~mask].reset_index(drop=True)
        target = panel["trade_date"].max()

        f = CrossSectionalForecast(freshness_days=365)
        out = f.predict_pool(panel, target_date=target)
        assert "688000.SH" in out["symbol"].values
        assert len(out) == 20


# --------------------------------------------------------------------------- #
# execution 集成
# --------------------------------------------------------------------------- #


class TestExecution:

    def test_execution_filters_limit_up(self) -> None:
        """涨停股不能买入 → 应从 top 中被剔除."""
        panel = _make_panel(n_dates=10, n_stocks=20, with_exec_cols=True, seed=3)
        # 让最小 log_mv 的股票（688000）开盘涨停（20% 科创板）
        last_date = panel["trade_date"].max()
        mask = (panel["symbol"] == "688000.SH") & (panel["trade_date"] == last_date)
        panel.loc[mask, "pre_close"] = 10.0
        panel.loc[mask, "open"] = 12.5  # +25%，超过 20% 涨停
        exec_m = ExecutionModel(kcb_limit_pct=0.20, max_volume_pct=0.5)
        f = CrossSectionalForecast(execution=exec_m, top_pct=0.2)
        out = f.predict_pool(panel)
        in_top = out[out["in_top"]]
        # 688000 不应入选 in_top
        assert not (in_top["symbol"] == "688000.SH").any()

    def test_no_execution_keeps_limit_up(self) -> None:
        """不启用 execution 时涨停股仍入选（仅 ranking）."""
        panel = _make_panel(n_dates=10, n_stocks=20, with_exec_cols=True, seed=3)
        last_date = panel["trade_date"].max()
        mask = (panel["symbol"] == "688000.SH") & (panel["trade_date"] == last_date)
        panel.loc[mask, "pre_close"] = 10.0
        panel.loc[mask, "open"] = 12.5
        f = CrossSectionalForecast(top_pct=0.2)  # 无 execution
        out = f.predict_pool(panel)
        in_top = out[out["in_top"]]
        assert (in_top["symbol"] == "688000.SH").any()


# --------------------------------------------------------------------------- #
# predict_single
# --------------------------------------------------------------------------- #


class TestPredictSingle:

    def test_top_stock_verdict_buy(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20, seed=4)
        f = CrossSectionalForecast(direction="long_low", top_pct=0.2)
        # 688000 应该是最小 log_mv，进 Top
        info = f.predict_single(panel, "688000.SH")
        assert info["verdict"] == "buy"
        assert info["in_top"] is True
        assert info["rank_position"] == 1
        assert 0 < info["planned_weight"] <= 1.0

    def test_middle_stock_verdict_hold_or_ignore(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20, seed=5)
        f = CrossSectionalForecast(direction="long_low", top_pct=0.2)
        # 中间股票（不在 top）
        info = f.predict_single(panel, "688010.SH")
        assert info["verdict"] in ("hold", "ignore")
        assert info["in_top"] is False

    def test_not_in_universe(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=5)
        f = CrossSectionalForecast()
        info = f.predict_single(panel, "999999.SH")
        assert info["verdict"] == "ignore"
        assert info["not_in_universe"] is True
        assert pd.isna(info["factor_value"])

    def test_stale_stock_marked_not_in_universe(self) -> None:
        """陈旧股 → 被 freshness 剔除 → predict_single 标 not_in_universe."""
        panel = _make_panel(n_dates=30, n_stocks=10)
        cutoff = panel["trade_date"].unique()[2]
        # 让 688000 数据只到第 2 天
        mask = (panel["symbol"] == "688000.SH") & (panel["trade_date"] > cutoff)
        panel = panel[~mask].reset_index(drop=True)
        target = panel["trade_date"].max()
        f = CrossSectionalForecast(freshness_days=7)
        info = f.predict_single(panel, "688000.SH", target_date=target)
        assert info["not_in_universe"] is True
        assert info["verdict"] == "ignore"


# --------------------------------------------------------------------------- #
# Markdown 输出
# --------------------------------------------------------------------------- #


class TestFormat:

    def test_pool_markdown_contains_top_section(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.2)
        out = f.predict_pool(panel)
        md = f.format_pool_markdown(out)
        assert "横截面选股" in md
        assert "买入名单" in md
        # Top 4 股票应在表格里
        in_top = out[out["in_top"]]
        for sym in in_top["symbol"]:
            assert sym in md

    def test_pool_markdown_empty(self) -> None:
        # 构造 pool 为空（min_stocks 不足）
        panel = _make_panel(n_dates=10, n_stocks=3)
        f = CrossSectionalForecast(top_pct=0.5, min_stocks=10)
        out = f.predict_pool(panel)
        md = f.format_pool_markdown(out)
        assert "无可选股票" in md

    def test_single_markdown_buy_verdict(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(direction="long_low", top_pct=0.2)
        info = f.predict_single(panel, "688000.SH")
        md = f.format_single_markdown(info)
        assert "688000.SH" in md
        assert "入选" in md or "建议买入" in md

    def test_single_markdown_not_in_universe(self) -> None:
        panel = _make_panel(n_dates=10, n_stocks=5)
        f = CrossSectionalForecast()
        info = f.predict_single(panel, "999999.SH")
        md = f.format_single_markdown(info)
        assert "999999.SH" in md
        assert "不在当前截面" in md or "数据陈旧" in md

    def test_pool_markdown_header_summary(self) -> None:
        """卡片版表头：含 emoji、日期、factor、方向、Top%、入选/全池."""
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.2, direction="long_low")
        out = f.predict_pool(panel)
        md = f.format_pool_markdown(out)
        assert "📊" in md
        assert "横截面选股" in md
        assert "`log_mv`" in md
        assert "反向" in md
        assert "Top 20%" in md
        n_in_top = int(out["in_top"].sum())
        assert f"入选 {n_in_top}/{len(out)} 只" in md

    @pytest.mark.parametrize(
        "extra_cols",
        [
            [],
            ["stock_name"],
            ["stock_name", "industry"],
            ["stock_name", "industry", "concept"],
        ],
    )
    def test_pool_markdown_card_optional_fields(self, extra_cols: list[str]) -> None:
        """has_name / has_industry / has_concept 各分支：缺失字段不出现在卡片里."""
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.2)
        out = f.predict_pool(panel)
        for c in extra_cols:
            out[c] = "x"
        md = f.format_pool_markdown(out)
        # 代码反引号 + header 始终在
        assert "688000.SH" in md or "688001.SH" in md
        assert "`log_mv`" in md  # factor 名在 header 反引号内

        # name 列：值 'x' 应该出现在某一行的第一行；没有 stock_name 时不应出现
        if "stock_name" in extra_cols:
            assert "x" in md
        else:
            # 没有 stock_name 时不该出现孤立的 'x' 名称 token（防止 industry/concept 也没注入）
            assert " x\n" not in md
        # industry / concept 都没注入时：不应出现 "  · " 形式的 tag 行
        if "industry" not in extra_cols and "concept" not in extra_cols:
            assert "   · " not in md and "    · " not in md

    def test_pool_markdown_escapes_v1_reserved_chars_in_name(self) -> None:
        """*ST 等含 V1 保留字的名称必须被 escape，否则 Telegram 推送整条挂."""
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.2)
        out = f.predict_pool(panel)
        in_top_mask = out["in_top"]
        first_idx = out[in_top_mask].index[0]
        out["stock_name"] = ""
        out["concept"] = ""
        out.loc[first_idx, "stock_name"] = "*ST航图"
        out.loc[first_idx, "concept"] = "5G_概念"
        md = f.format_pool_markdown(out)
        # 用户数据保留字必须被反斜杠保护
        assert r"\*ST航图" in md, "* must be escaped to \\* to avoid V1 entity parser break"
        assert r"5G\_概念" in md, "_ must be escaped to \\_ to avoid V1 entity parser break"
        # factor 名只在 header 反引号 code span 内，V1 不解析内部内容，免转义
        assert "`log_mv`" in md, "factor in header must be backtick-wrapped"

    def test_pool_markdown_truncation_message(self) -> None:
        """超过 max_rows：尾行显式说明剩余数量，无残留 pipe."""
        panel = _make_panel(n_dates=10, n_stocks=50)
        f = CrossSectionalForecast(top_pct=0.5)
        out = f.predict_pool(panel)
        n_in_top = int(out["in_top"].sum())
        md = f.format_pool_markdown(out, max_rows=5)
        assert n_in_top > 5, "fixture sanity: need >5 in_top to test truncation"
        assert f"还有 {n_in_top - 5} 只" in md
        # 卡片版不应再有 pipe 表格残留
        assert "|" not in md

    def test_pool_markdown_no_pipe_table(self) -> None:
        """卡片版正常路径不再产生 V1 不渲染的 pipe 表格."""
        panel = _make_panel(n_dates=10, n_stocks=20)
        f = CrossSectionalForecast(top_pct=0.2)
        out = f.predict_pool(panel)
        out["stock_name"] = "name"
        out["industry"] = "行业A"
        out["concept"] = "概念A"
        md = f.format_pool_markdown(out)
        # pipe 表格痕迹（含表头分隔行 |-|-|-| 等）必须全无
        assert "|" not in md
        # 每张卡片末行 factor 描述
        assert "rank " in md


class TestTopN:
    """top_n 固定只数模式 (2026-06-07 paper_trade 改 Top 5)."""

    @staticmethod
    def _panel(n=30):
        import pandas as pd
        rows = []
        for i in range(n):
            for d in pd.date_range("2026-05-20", periods=10):
                rows.append({"trade_date": d, "symbol": f"68800{i}.SH",
                             "log_mv": float(i),  # i 越小越优 (long_low)
                             "pre_close": 10.0, "open": 10.0, "close": 10.0,
                             "amount": 1e6})
        return pd.DataFrame(rows)

    def test_top_n_overrides_pct(self):
        from kss.prediction.cross_sectional_forecast import CrossSectionalForecast
        fc = CrossSectionalForecast(direction="long_low", top_pct=0.2, top_n=5)
        out = fc.predict_pool(self._panel())
        assert int(out["in_top"].sum()) == 5  # 30 只 × 20% = 6 ≠ 5, 证明 top_n 生效
        assert set(out[out["in_top"]]["rank_position"]) == {1, 2, 3, 4, 5}
        assert out.attrs["top_n"] == 5
        # 等权 1/5
        import pytest as _pt
        assert out[out["in_top"]]["planned_weight"].iloc[0] == _pt.approx(0.2)

    def test_top_n_validation(self):
        import pytest as _pt
        from kss.prediction.cross_sectional_forecast import CrossSectionalForecast
        with _pt.raises(ValueError, match="top_n"):
            CrossSectionalForecast(top_n=0)

    def test_top_n_none_keeps_pct_behavior(self):
        from kss.prediction.cross_sectional_forecast import CrossSectionalForecast
        fc = CrossSectionalForecast(direction="long_low", top_pct=0.2)
        out = fc.predict_pool(self._panel())
        assert int(out["in_top"].sum()) == 6  # 30 × 20%
        assert out.attrs["top_n"] is None
