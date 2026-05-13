"""kss/sector/commentary.py 单元测试.

覆盖：
- build_context / compute_theme_metrics 数据聚合
- render_prompt 输出结构
- fallback_text 数据缺失时的兜底
- _sanitize_html 处理 markdown + 未授权标签
- clip_to_max_len 长度兜底
- generate_commentary LLM 失败时降级
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from kss.llm.openai_client import LLMUnavailable
from kss.sector.commentary import (
    _MAX_MSG_LEN,
    _sanitize_html,
    build_context,
    clip_to_max_len,
    compute_theme_metrics,
    fallback_text,
    generate_commentary,
    render_prompt,
)
from kss.sector.data_fetcher import SectorSnapshot
from kss.sector.kcb_overlay import KcbOverlay
from kss.sector.themes import ThemeBucket


# ====================================================================== #
# Fixtures —— 复用的 mock 数据
# ====================================================================== #


def _make_industry_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["半导体", "电池", "化学制药", "其他"],
        "pct_change": [3.5, 2.1, 1.8, 0.5],
        "net_amount_rate": [5.2, 3.1, 1.5, 0.2],
        "buy_elg_amount_rate": [4.8, 2.9, 1.2, 0.1],
    })


def _make_concept_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["AI服务器", "算力概念", "创新药", "其他概念"],
        "pct_change": [4.2, 3.8, 2.5, 0.3],
        "net_amount": [3.5, 2.8, 1.2, 0.1],
    })


def _make_snapshot(
    *,
    industry: pd.DataFrame | None = None,
    concept: pd.DataFrame | None = None,
    northbound: dict[str, float] | None = None,
    missing: list[str] | None = None,
) -> SectorSnapshot:
    return SectorSnapshot(
        trade_date="20260512",
        industry=industry,
        concept=concept,
        northbound=northbound,
        missing=missing or [],
    )


def _make_indices() -> dict[str, dict[str, float]]:
    return {
        "上证主板": {"close": 3500.0, "pct_chg": 0.85, "amount": 5000000.0, "volume_ratio": 1.2},
        "创业板": {"close": 2200.0, "pct_chg": 1.5, "amount": 3000000.0, "volume_ratio": 1.5},
        "科创板": {"close": 1100.0, "pct_chg": 2.1, "amount": 800000.0, "volume_ratio": 1.8},
    }


def _make_themes() -> dict[str, ThemeBucket]:
    return {
        "半导体": ThemeBucket(
            name="半导体",
            industries=["半导体"],
            concepts=[],
        ),
        "AI算力": ThemeBucket(
            name="AI算力",
            industries=[],
            concepts=["AI服务器", "算力概念"],
        ),
        "生物医药": ThemeBucket(
            name="生物医药",
            industries=["化学制药"],
            concepts=["创新药"],
        ),
    }


def _make_overlay(industry_counts: dict[str, int] | None = None,
                  concept_counts: dict[str, int] | None = None) -> KcbOverlay:
    """构造一个 KcbOverlay mock，按 name 返回固定 count."""
    ind_map = industry_counts or {}
    cnt_map = concept_counts or {}

    overlay = MagicMock(spec=KcbOverlay)
    overlay.count_for_industry.side_effect = lambda n: ind_map.get(n, 0)
    overlay.count_for_concept.side_effect = lambda n: cnt_map.get(n, 0)
    return overlay


# ====================================================================== #
# build_context
# ====================================================================== #


class TestBuildContext:
    def test_happy_path(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay({"半导体": 5}, {"AI服务器": 3})
        ctx = build_context(snap, _make_indices(), _make_themes(), overlay)
        assert ctx["top_industries"][0]["name"] == "半导体"
        assert ctx["top_industries"][0]["kcb_count"] == 5
        assert ctx["top_concepts"][0]["name"] == "AI服务器"
        assert ctx["top_concepts"][0]["kcb_count"] == 3
        assert "market_indices" not in ctx["missing"]
        assert "themes" not in ctx["missing"]

    def test_indices_missing_flagged(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        ctx = build_context(snap, {}, _make_themes(), overlay)
        assert "market_indices" in ctx["missing"]

    def test_themes_missing_flagged(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        ctx = build_context(snap, _make_indices(), {}, overlay)
        assert "themes" in ctx["missing"]

    def test_industry_data_none(self) -> None:
        snap = _make_snapshot(industry=None, concept=_make_concept_df(), missing=["industry"])
        overlay = _make_overlay()
        ctx = build_context(snap, _make_indices(), _make_themes(), overlay)
        assert ctx["top_industries"] == []
        assert "industry" in ctx["missing"]

    def test_top_industries_sorted_by_pct_change_desc(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        ctx = build_context(snap, _make_indices(), _make_themes(), overlay)
        pcts = [x["pct_change"] for x in ctx["top_industries"]]
        assert pcts == sorted(pcts, reverse=True)


# ====================================================================== #
# compute_theme_metrics
# ====================================================================== #


class TestComputeThemeMetrics:
    def test_matches_industry_to_theme(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay({"半导体": 5})
        themes = _make_themes()
        metrics = compute_theme_metrics(snap, themes, overlay)
        assert metrics["半导体"]["matched_industries"][0]["name"] == "半导体"
        assert metrics["半导体"]["avg_industry_pct"] == 3.5
        assert metrics["半导体"]["kcb_count"] == 5

    def test_matches_concepts_to_theme(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        metrics = compute_theme_metrics(snap, _make_themes(), overlay)
        ai = metrics["AI算力"]
        names = {x["name"] for x in ai["matched_concepts"]}
        assert names == {"AI服务器", "算力概念"}
        # avg(4.2, 3.8) = 4.0
        assert ai["avg_concept_pct"] == pytest.approx(4.0)

    def test_no_match_returns_empty_lists(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        themes = {
            "不存在": ThemeBucket(
                name="不存在",
                industries=["不存在的行业"],
                concepts=["不存在的概念"],
            )
        }
        metrics = compute_theme_metrics(snap, themes, overlay)
        assert metrics["不存在"]["matched_industries"] == []
        assert metrics["不存在"]["matched_concepts"] == []
        assert metrics["不存在"]["avg_industry_pct"] is None
        assert metrics["不存在"]["avg_concept_pct"] is None

    def test_empty_themes_returns_empty(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        assert compute_theme_metrics(snap, {}, overlay) == {}


# ====================================================================== #
# render_prompt
# ====================================================================== #


class TestRenderPrompt:
    def test_includes_date_and_missing_dimensions(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), missing=["concept"])
        overlay = _make_overlay()
        ctx = build_context(snap, _make_indices(), _make_themes(), overlay)
        theme_metrics = compute_theme_metrics(snap, _make_themes(), overlay)
        system, user = render_prompt("20260512", ctx, theme_metrics)
        assert "20260512" in user
        assert "missing_dimensions" in user
        assert "投资顾问" in system
        assert "6 段" in system

    def test_user_contains_json_payload(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        ctx = build_context(snap, _make_indices(), _make_themes(), overlay)
        tm = compute_theme_metrics(snap, _make_themes(), overlay)
        _, user = render_prompt("20260512", ctx, tm)
        assert "```json" in user
        assert "半导体" in user  # 主题名 + 行业名都在


# ====================================================================== #
# fallback_text
# ====================================================================== #


class TestFallbackText:
    def test_includes_date_and_indices(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        text = fallback_text("20260512", snap, _make_indices(), reason="429")
        assert "2026-05-12" in text
        assert "<b>大盘指数</b>" in text
        assert "<b>Top 行业</b>" in text
        assert "⚠️" in text
        assert "429" in text  # reason 透传

    def test_empty_indices_shows_未到位(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df())
        text = fallback_text("20260512", snap, {})
        assert "数据未到位" in text

    def test_all_data_missing(self) -> None:
        snap = _make_snapshot(missing=["industry", "concept", "northbound"])
        text = fallback_text("20260512", snap, {})
        assert "数据未到位" in text
        assert "缺失数据" in text

    def test_fallback_is_pure_html_no_markdown(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        text = fallback_text("20260512", snap, _make_indices())
        # fallback 输出绝对不能含 markdown 加粗符号
        assert "**" not in text
        assert "__" not in text


# ====================================================================== #
# _sanitize_html
# ====================================================================== #


class TestSanitizeHTML:
    def test_markdown_bold_converted(self) -> None:
        assert _sanitize_html("**重要**") == "<b>重要</b>"

    def test_markdown_italic_converted(self) -> None:
        # 单星 italic
        out = _sanitize_html("这个 *板块* 涨")
        assert "<i>板块</i>" in out

    def test_disallowed_tag_escaped(self) -> None:
        out = _sanitize_html("<script>alert(1)</script>")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_allowed_tags_kept(self) -> None:
        out = _sanitize_html("<b>粗</b> <i>斜</i> <u>下划</u> <code>code</code>")
        assert out == "<b>粗</b> <i>斜</i> <u>下划</u> <code>code</code>"

    def test_code_fence_removed(self) -> None:
        out = _sanitize_html("```html\n<b>x</b>\n```")
        assert "```" not in out

    def test_unbalanced_disallowed_tag_escaped(self) -> None:
        """单独的 <div> 没有闭合也要 escape，不能让 Telegram 报错."""
        out = _sanitize_html("<div>开头")
        assert "<div>" not in out
        assert "&lt;div&gt;" in out


# ====================================================================== #
# clip_to_max_len
# ====================================================================== #


class TestClipToMaxLen:
    def test_under_limit_unchanged(self) -> None:
        text = "短文本"
        assert clip_to_max_len(text) == text

    def test_over_limit_truncated(self) -> None:
        text = "x" * (_MAX_MSG_LEN + 1000)
        out = clip_to_max_len(text)
        assert len(out) <= _MAX_MSG_LEN
        assert out.endswith("（已截断）")

    def test_truncation_prefers_paragraph_boundary(self) -> None:
        """有段落 \\n\\n 的话裁剪点应在段落边界."""
        para = "A" * 500 + "\n\n" + "B" * 500 + "\n\n" + "C" * 500
        long_text = (para + "\n\n") * 4  # ~6000 字符
        out = clip_to_max_len(long_text)
        assert len(out) <= _MAX_MSG_LEN
        assert "（已截断）" in out


# ====================================================================== #
# generate_commentary （端到端）
# ====================================================================== #


class TestGenerateCommentary:
    def test_happy_path_calls_llm(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        client = MagicMock()
        client.complete.return_value = (
            "<b>大盘总览</b>\n沪深主板涨 0.85%...\n\n"
            "<b>加减仓建议</b>\n半导体可适当加仓（中线视角）"
        )
        out = generate_commentary(
            "20260512", snap, _make_indices(), _make_themes(), overlay, client=client,
        )
        assert "加减仓建议" in out
        client.complete.assert_called_once()

    def test_llm_failure_returns_fallback(self) -> None:
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        client = MagicMock()
        client.complete.side_effect = LLMUnavailable("429 rate limit")
        out = generate_commentary(
            "20260512", snap, _make_indices(), _make_themes(), overlay, client=client,
        )
        assert "📊 板块复盘" in out
        assert "⚠️" in out
        assert "429" in out

    def test_data_missing_in_prompt_passed_to_llm(self) -> None:
        """indices 缺失时，prompt 应包含 missing_dimensions: market_indices."""
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        client = MagicMock()
        client.complete.return_value = "<b>大盘</b>数据未到位"
        generate_commentary(
            "20260512", snap, {}, _make_themes(), overlay, client=client,
        )
        # 检查传给 LLM 的 user prompt 含 market_indices missing 标记
        kwargs = client.complete.call_args.kwargs
        user_prompt = kwargs.get("user") or client.complete.call_args.args[1]
        assert "market_indices" in user_prompt

    def test_output_sanitized_when_llm_returns_markdown(self) -> None:
        """LLM 偷偷输出 **bold** → sanitize 转为 <b>."""
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        client = MagicMock()
        client.complete.return_value = "**重要**：半导体活跃"
        out = generate_commentary(
            "20260512", snap, _make_indices(), _make_themes(), overlay, client=client,
        )
        assert "**" not in out
        assert "<b>重要</b>" in out

    def test_output_clipped_when_over_4000(self) -> None:
        """LLM 失控返回 10000 字 → 兜底裁剪."""
        snap = _make_snapshot(industry=_make_industry_df(), concept=_make_concept_df())
        overlay = _make_overlay()
        client = MagicMock()
        client.complete.return_value = "a" * 10000
        out = generate_commentary(
            "20260512", snap, _make_indices(), _make_themes(), overlay, client=client,
        )
        assert len(out) <= _MAX_MSG_LEN
        assert "（已截断）" in out
