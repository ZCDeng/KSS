"""kss/sector/formatter.py 单元测试.

覆盖：
- 5 段全齐时的 markdown 结构
- 各 section 缺失时的「数据暂缺」降级
- 表头 pipe 分隔符正确（S620 回归保护）
- KCB 池子标注：0 → '—'，>0 → '⭐N'
- 浮点格式：保留 2 位小数 + ``%``
"""

from __future__ import annotations

import pandas as pd

from kss.sector.formatter import (
    _format_pct,
    _kcb_badge,
    _markdown_table,
    format_review_markdown,
)
from kss.sector.kcb_overlay import KcbOverlay


def _empty_overlay() -> KcbOverlay:
    return KcbOverlay()


def _populated_overlay() -> KcbOverlay:
    return KcbOverlay(
        industry_to_codes={"半导体": ["688008.SH", "688012.SH"], "电网设备": ["688100.SH"]},
        concept_to_codes={"集成电路概念": ["688008.SH"]},
        active_pool={"688008.SH", "688012.SH", "688100.SH"},
    )


# ====================================================================== #
# 辅助函数
# ====================================================================== #


class TestFormatHelpers:
    def test_format_pct_positive(self) -> None:
        assert _format_pct(1.234) == "+1.23%"

    def test_format_pct_negative(self) -> None:
        assert _format_pct(-2.5) == "-2.50%"

    def test_format_pct_none(self) -> None:
        assert _format_pct(None) == "—"

    def test_format_pct_nan(self) -> None:
        assert _format_pct(float("nan")) == "—"

    def test_kcb_badge_zero(self) -> None:
        assert _kcb_badge(0) == "—"

    def test_kcb_badge_positive(self) -> None:
        assert _kcb_badge(3) == "⭐3"


class TestMarkdownTable:
    """_markdown_table —— S620 表头 bug 回归保护."""

    def test_pipe_separators_in_header(self) -> None:
        """表头每列之间都有 ``|`` 分隔符."""
        out = _markdown_table(["A", "B", "C"], [["1", "2", "3"]])
        lines = out.splitlines()
        # 表头行：| A | B | C |
        assert lines[0] == "| A | B | C |"
        # 分隔行：| --- | --- | --- |
        assert lines[1] == "| --- | --- | --- |"
        # 数据行
        assert lines[2] == "| 1 | 2 | 3 |"

    def test_pipe_count_consistent_across_rows(self) -> None:
        """所有行的 ``|`` 数量必须一致 —— 表格不错位的必要条件."""
        out = _markdown_table(
            ["X", "Y", "Z", "W"],
            [["1", "2", "3", "4"], ["a", "b", "c", "d"]],
        )
        pipe_counts = [line.count("|") for line in out.splitlines()]
        assert len(set(pipe_counts)) == 1, f"|数不一致: {pipe_counts}"

    def test_row_too_short_padded_with_dash(self) -> None:
        """行长度短于表头 → 补齐 ``—``，不外抛."""
        out = _markdown_table(["A", "B", "C"], [["1"]])
        lines = out.splitlines()
        # 数据行被补齐
        assert "—" in lines[2]


# ====================================================================== #
# format_review_markdown 主入口
# ====================================================================== #


def _make_industry_heat() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["半导体", "电网设备"],
        "pct_change": [3.2, 1.48],
        "net_amount_rate": [2.1, 4.13],
        "buy_elg_amount_rate": [2.5, 4.19],
        "heat_score": [0.85, 0.72],
    })


def _make_flow_persistence() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["半导体", "电网设备"],
        "cum_inflow": [5.5, 8.2],
        "persist_days": [2, 3],
    })


def _make_concept_heat() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["集成电路概念", "AIGC"],
        "pct_change": [2.5, 3.1],
        "net_amount": [-1e8, 3.2e8],
        "heat_score": [0.78, 0.88],
    })


def _make_northbound() -> dict[str, float]:
    return {
        "north_money": 405543.48,
        "south_money": 53807.88,
    }


class TestFormatReviewMarkdown:
    """格式化主入口 —— 5 段结构."""

    def test_all_sections_rendered_when_data_complete(self) -> None:
        """4 数据源齐全 → 5 段 markdown 都生成."""
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=_make_flow_persistence(),
            concept_heat=_make_concept_heat(),
            northbound=_make_northbound(),
            overlay=_populated_overlay(),
        )
        assert "板块复盘" in md
        assert "2026-05-12" in md
        assert "🔥" in md
        assert "💰" in md
        assert "🎯" in md
        assert "🌍" in md
        # 没有缺失提示
        assert "⚠️" not in md
        # 未传 dragon_tiger → 龙虎榜 section 不渲染
        assert "🐲" not in md

    def test_dragon_tiger_section_rendered_when_passed(self) -> None:
        """传入龙虎榜 DataFrame → 🐲 section 含个股名 + 净买入 + 上榜原因."""
        lhb = pd.DataFrame({
            "code": ["688507", "002119"],
            "name": ["索辰科技", "康强电子"],
            "net_amount": [5.18e8, -3.70e8],
            "reason": ["日涨幅偏离值达7%的证券", "日换手率达20%的证券"],
        })
        md = format_review_markdown(
            trade_date="2026-06-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=_make_flow_persistence(),
            concept_heat=_make_concept_heat(),
            northbound=_make_northbound(),
            overlay=_populated_overlay(),
            dragon_tiger=lhb,
        )
        assert "🐲" in md
        assert "索辰科技" in md
        assert "+5.18 亿" in md  # 净买入按元 → 亿换算
        assert "日涨幅偏离值达7%" in md

    def test_industry_heat_missing_section_shows_placeholder(self) -> None:
        """行业热度数据缺失 → 该 section 显示「数据暂缺」，其他 section 正常."""
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=None,
            flow_persistence=_make_flow_persistence(),
            concept_heat=_make_concept_heat(),
            northbound=_make_northbound(),
            overlay=_populated_overlay(),
        )
        assert "🔥" in md  # title 保留
        # 该 section 显示占位符
        assert "_数据暂缺_" in md
        # 其他 section 正常渲染
        assert "电网设备" in md  # 来自 flow_persistence

    def test_northbound_missing_renders_placeholder(self) -> None:
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=_make_flow_persistence(),
            concept_heat=_make_concept_heat(),
            northbound=None,
            overlay=_populated_overlay(),
        )
        assert "🌍" in md
        assert "_数据暂缺_" in md

    def test_concept_missing_isolated(self) -> None:
        """概念 API 失败 → 概念 section 占位，行业 / 北向 不受影响."""
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=_make_flow_persistence(),
            concept_heat=None,
            northbound=_make_northbound(),
            overlay=_populated_overlay(),
        )
        assert "🎯" in md
        assert "_数据暂缺_" in md
        assert "405543" in md or "40.55" in md  # 北向数字渲染

    def test_kcb_zero_shows_dash(self) -> None:
        """KCB 池子无持仓的板块显示 ``—``."""
        df = pd.DataFrame({
            "name": ["不在池里的板块"],
            "pct_change": [1.0],
            "net_amount_rate": [1.0],
            "buy_elg_amount_rate": [1.0],
            "heat_score": [0.5],
        })
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=df,
            flow_persistence=None,
            concept_heat=None,
            northbound=None,
            overlay=_empty_overlay(),
        )
        # 行业热度行的 KCB 池列应是 —
        # 检查：找到包含 "不在池里的板块" 的行
        line = next(l for l in md.splitlines() if "不在池里" in l)
        assert "—" in line

    def test_kcb_positive_shows_star_count(self) -> None:
        """有持仓板块显示 ``⭐N``."""
        df = pd.DataFrame({
            "name": ["半导体"],
            "pct_change": [3.0],
            "net_amount_rate": [2.0],
            "buy_elg_amount_rate": [2.5],
            "heat_score": [0.9],
        })
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=df,
            flow_persistence=None,
            concept_heat=None,
            northbound=None,
            overlay=_populated_overlay(),
        )
        line = next(l for l in md.splitlines() if "半导体" in l)
        assert "⭐2" in line

    def test_missing_data_footer_rendered(self) -> None:
        """missing list 非空 → 底部 ⚠️ 提示."""
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=None,
            flow_persistence=None,
            concept_heat=None,
            northbound=None,
            overlay=_empty_overlay(),
            missing=["industry", "concept", "northbound"],
        )
        assert "⚠️" in md
        assert "industry" in md and "concept" in md

    def test_pipe_separators_consistent_in_full_output(self) -> None:
        """主输出中所有表格的 ``|`` 数应一致（S620 回归保护）."""
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=_make_flow_persistence(),
            concept_heat=_make_concept_heat(),
            northbound=_make_northbound(),
            overlay=_populated_overlay(),
        )
        # 每个表格内（连续多个 | 开头的行）pipe count 应一致
        cur_block: list[int] = []
        blocks: list[list[int]] = []
        for line in md.splitlines():
            if line.startswith("| "):
                cur_block.append(line.count("|"))
            else:
                if cur_block:
                    blocks.append(cur_block)
                    cur_block = []
        if cur_block:
            blocks.append(cur_block)
        for block in blocks:
            assert len(set(block)) == 1, f"表格内 |数不一致: {block}"

    def test_rotation_signal_section_rendered_when_present(self) -> None:
        """轮动信号 DataFrame 非空 → 渲染 🔄 section."""
        rot = pd.DataFrame({
            "name": ["半导体"],
            "today_rank": [1],
            "past_rank": [10],
            "rank_jump": [9],
            "today_flow": [3.5],
        })
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=None,
            concept_heat=None,
            northbound=None,
            overlay=_populated_overlay(),
            rotation_signal=rot,
        )
        assert "🔄" in md
        assert "+9" in md

    def test_rotation_signal_empty_skipped(self) -> None:
        """轮动信号空 DataFrame → 不渲染该 section."""
        md = format_review_markdown(
            trade_date="2026-05-12",
            industry_heat=_make_industry_heat(),
            flow_persistence=None,
            concept_heat=None,
            northbound=None,
            overlay=_populated_overlay(),
            rotation_signal=pd.DataFrame(),
        )
        assert "🔄" not in md
