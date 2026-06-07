"""scripts/sector_review.py 命令行入口测试.

覆盖：
- run_review 核心流程（mock 掉 Tushare client + LLM）
- argparse + 日期格式兼容
- dry-run 行为
- LLM 失败 fallback
- 数据全部缺失时的 fallback 文本
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "sector_review.py"


def _load_script_module():
    """以模块形式加载脚本（不执行 main），用于直接测内部函数."""
    spec = importlib.util.spec_from_file_location(
        "sector_review_for_tests", SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def script_mod():
    return _load_script_module()


# ====================================================================== #
# Date helpers
# ====================================================================== #


class TestDateHelpers:
    def test_fmt_display_date_canonical(self, script_mod) -> None:
        assert script_mod._fmt_display_date("20260512") == "2026-05-12"

    def test_fmt_display_date_invalid_passes_through(self, script_mod) -> None:
        """非法格式直接返回原值，不外抛."""
        assert script_mod._fmt_display_date("garbage") == "garbage"


# ====================================================================== #
# Fake TushareClient
# ====================================================================== #


class _FakeClient:
    """TushareClient 替身：按 trade_date 路由到预设响应."""

    def __init__(
        self,
        ind_by_date: dict[str, pd.DataFrame],
        cnt: pd.DataFrame | None = None,
        sw: pd.DataFrame | None = None,
        hs: pd.DataFrame | None = None,
        index_by_code: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        self._ind = ind_by_date
        self._cnt = cnt
        self._sw = sw
        self._hs = hs
        self._index = index_by_code or {}

    def fetch_moneyflow_ind_dc(self, trade_date: str):
        return self._ind.get(trade_date)

    def fetch_moneyflow_cnt_ths(self, trade_date: str):
        return self._cnt

    def fetch_sw_daily(self, trade_date: str):
        return self._sw

    def fetch_moneyflow_hsgt(self, trade_date: str):
        return self._hs

    def fetch_index_daily(self, ts_code: str, start: str, end: str):
        return self._index.get(ts_code)

    def fetch_fund_share(self, ts_code: str, start: str, end: str):
        # 默认无 ETF 份额数据 → etf_radar 走 None/missing 降级路径
        return None


def _make_ind_df(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """构造 industry DataFrame（带 content_type='行业' + 3 维资金流字段）."""
    return pd.DataFrame({
        "content_type": ["行业"] * len(rows),
        "name": [r[0] for r in rows],
        "pct_change": [r[1] for r in rows],
        "net_amount_rate": [r[2] for r in rows],
        "buy_elg_amount_rate": [r[3] for r in rows],
    })


def _make_cnt_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["集成电路概念", "AIGC"],
        "pct_change": [2.5, 3.1],
        "net_amount": [-1e8, 3.2e8],
    })


def _make_hsgt_df() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["20260512"],
        "north_money": ["405543.48"],
        "south_money": ["53807.88"],
    })


def _make_index_df(ts_code: str) -> pd.DataFrame:
    """构造 index_daily 返回：trade_date 倒序."""
    return pd.DataFrame({
        "ts_code": [ts_code] * 6,
        "trade_date": ["20260512", "20260509", "20260508", "20260507", "20260506", "20260505"],
        "close": [3500.0, 3490.0, 3480.0, 3470.0, 3460.0, 3450.0],
        "pct_chg": [1.5, 0.1, 0.1, 0.1, 0.1, 0.1],
        "amount": [150000.0, 100000.0, 100000.0, 100000.0, 100000.0, 100000.0],
    })


# ====================================================================== #
# run_review 核心流程
# ====================================================================== #


class TestRunReview:
    """run_review —— 端到端核心流程（mock 掉 Tushare + LLM）."""

    def test_full_data_calls_llm_and_returns_commentary(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """数据全齐 → LLM 被调用，返回 commentary 文本."""
        today = _make_ind_df([
            ("半导体", 3.2, 2.1, 2.5),
            ("电网设备", 1.5, 4.1, 4.2),
        ])
        client = _FakeClient(
            ind_by_date={"20260512": today},
            cnt=_make_cnt_df(),
            hs=_make_hsgt_df(),
            index_by_code={
                "000001.SH": _make_index_df("000001.SH"),
                "399001.SZ": _make_index_df("399001.SZ"),
                "399006.SZ": _make_index_df("399006.SZ"),
                "000688.SH": _make_index_df("000688.SH"),
            },
        )
        # 旁路 KCB overlay
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )
        # Mock LLM
        llm_mock = type("MockLLM", (), {
            "complete": lambda self, system, user: (
                "<b>大盘总览</b>\n沪深主板涨 0.85%...\n\n"
                "<b>加减仓建议</b>\n半导体可适当加仓（中线视角）"
            )
        })()

        text, missing = script_mod.run_review(
            trade_date="20260512",
            client=client,
            llm_client=llm_mock,
        )
        assert "<b>加减仓建议</b>" in text
        # industry_index 和 sw 均为 None → 缺失列表应有 industry_index
        assert "industry_index" in missing

    def test_llm_failure_returns_fallback(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LLM 不可用 → fallback 结构化文本，含 ⚠️ 标记."""
        today = _make_ind_df([("半导体", 3.2, 2.1, 2.5)])
        client = _FakeClient(
            ind_by_date={"20260512": today},
            cnt=_make_cnt_df(),
        )
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )
        from kss.llm.openai_client import LLMUnavailable
        llm_fail = type("MockLLM", (), {
            "complete": lambda self, system, user: (_ for _ in ()).throw(
                LLMUnavailable("429 rate limit"),
            ),
        })()

        text, missing = script_mod.run_review(
            trade_date="20260512", client=client, llm_client=llm_fail,
        )
        assert "📊 板块复盘" in text
        assert "⚠️" in text
        assert "LLM" in text or "失败" in text

    def test_all_data_missing_fallback_text(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """4 个 API 全部返回 None → fallback 文本."""
        client = _FakeClient(ind_by_date={}, cnt=None, sw=None, hs=None)
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )
        from kss.llm.openai_client import LLMUnavailable
        llm_fail = type("MockLLM", (), {
            "complete": lambda self, system, user: (_ for _ in ()).throw(
                LLMUnavailable("no key"),
            ),
        })()

        text, missing = script_mod.run_review(
            trade_date="20260512", client=client, llm_client=llm_fail,
        )
        assert "⚠️" in text
        assert len(missing) >= 3

    def test_output_contains_html_tags_not_markdown(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LLM 偷偷输出 **bold** → sanitize 转成 <b>."""
        today = _make_ind_df([("半导体", 3.2, 2.1, 2.5)])
        client = _FakeClient(
            ind_by_date={"20260512": today},
            cnt=_make_cnt_df(),
        )
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )
        llm_md = type("MockLLM", (), {
            "complete": lambda self, system, user: (
                "**重要**：半导体活跃，建议关注<b>AI算力</b>"
            ),
        })()

        text, _ = script_mod.run_review(
            trade_date="20260512", client=client, llm_client=llm_md,
        )
        # markdown ** 应被 sanitize 成 <b>
        assert "**" not in text
        assert "<b>重要</b>" in text
        assert "<b>AI算力</b>" in text
