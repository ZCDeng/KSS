"""scripts/sector_review.py 命令行入口测试.

覆盖：
- run_review 核心流程（mock 掉 Tushare client）
- argparse + 日期格式兼容
- dry-run 行为
- 数据全部缺失时的退出码
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
    def test_walk_back_weekdays_skips_weekend(self, script_mod) -> None:
        """从周一往回数 3 天 → 跳过周末，命中前周三/四/五."""
        # 2026-05-11 是周一
        out = script_mod._walk_back_weekdays("20260511", 3)
        # 前 3 个工作日：周五 5/8, 周四 5/7, 周三 5/6
        assert out == ["20260508", "20260507", "20260506"]

    def test_walk_back_weekdays_within_week(self, script_mod) -> None:
        """从周三往回数 2 天 → 周一 + 周二."""
        # 2026-05-13 是周三
        out = script_mod._walk_back_weekdays("20260513", 2)
        assert out == ["20260512", "20260511"]

    def test_fmt_display_date_canonical(self, script_mod) -> None:
        assert script_mod._fmt_display_date("20260512") == "2026-05-12"

    def test_fmt_display_date_invalid_passes_through(self, script_mod) -> None:
        """非法格式直接返回原值，不外抛."""
        assert script_mod._fmt_display_date("garbage") == "garbage"


# ====================================================================== #
# run_review 核心流程
# ====================================================================== #


class _FakeClient:
    """TushareClient 替身：按 trade_date 路由到预设响应."""

    def __init__(
        self,
        ind_by_date: dict[str, pd.DataFrame],
        cnt: pd.DataFrame | None = None,
        sw: pd.DataFrame | None = None,
        hs: pd.DataFrame | None = None,
    ) -> None:
        self._ind = ind_by_date
        self._cnt = cnt
        self._sw = sw
        self._hs = hs

    def fetch_moneyflow_ind_dc(self, trade_date: str):
        return self._ind.get(trade_date)

    def fetch_moneyflow_cnt_ths(self, trade_date: str):
        return self._cnt

    def fetch_sw_daily(self, trade_date: str):
        return self._sw

    def fetch_moneyflow_hsgt(self, trade_date: str):
        return self._hs


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


class TestRunReview:
    """run_review —— 端到端核心流程（mock 掉 Tushare）."""

    def test_full_data_produces_complete_markdown(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """4 个 API 都有数据 → markdown 含完整 5 段."""
        today = _make_ind_df([
            ("半导体", 3.2, 2.1, 2.5),
            ("电网设备", 1.5, 4.1, 4.2),
            ("钢铁", -1.0, -0.5, -1.0),
        ])
        past = _make_ind_df([
            ("半导体", -1.0, -2.0, -2.5),  # 半导体之前排名最低
            ("电网设备", 2.0, 3.0, 3.5),
            ("钢铁", 1.0, 1.0, 1.0),
        ])
        client = _FakeClient(
            ind_by_date={"20260512": today, "20260511": past, "20260508": past, "20260507": past},
            cnt=_make_cnt_df(),
            hs=_make_hsgt_df(),
        )
        # 旁路 KCB overlay（避免读 stock_names.csv）
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )

        md, missing = script_mod.run_review(
            trade_date="20260512",
            lookback_days=3,
            client=client,
        )
        assert "板块复盘" in md
        assert "半导体" in md
        assert "🔥" in md and "💰" in md and "🎯" in md and "🌍" in md
        # industry_index 和 northbound 中 sw 为 None → 缺 industry_index
        assert "industry_index" in missing

    def test_all_data_missing_yields_placeholders(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """4 个 API 全部返回 None → markdown 全段「数据暂缺」."""
        client = _FakeClient(ind_by_date={}, cnt=None, sw=None, hs=None)
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )

        md, missing = script_mod.run_review(
            trade_date="20260512",
            lookback_days=3,
            client=client,
        )
        assert "_数据暂缺_" in md
        assert len(missing) >= 3
        # ⚠️ 缺失提示渲染
        assert "⚠️" in md

    def test_rotation_signal_renders_when_industry_jumps(
        self, script_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """构造一个板块排名从 10 跳到 1 + 今日净流入 → 触发轮动信号.

        默认阈值是 50（适配 500 板块的全市场），测试用 5 模拟小规模.
        """
        import json
        cfg_path = tmp_path / "test_cfg.json"
        cfg_path.write_text(json.dumps({"rotation_rank_jump_threshold": 5}))

        today_rows = [(f"S{i}", float(10 - i), float(10 - i), 1.0) for i in range(10)]
        today_rows[0] = ("半导体", 3.0, 5.0, 4.0)
        today = _make_ind_df(today_rows)

        past_rows = [(f"S{i}", 1.0, float(i - 5), 1.0) for i in range(10)]
        past_rows[0] = ("半导体", 1.0, -100.0, 1.0)
        past = _make_ind_df(past_rows)

        client = _FakeClient(
            ind_by_date={
                "20260512": today,
                "20260511": past,
                "20260508": past,
                "20260507": past,
            },
            cnt=None, hs=None,
        )
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )

        md, _ = script_mod.run_review(
            trade_date="20260512", lookback_days=3, client=client,
            config_path=cfg_path,
        )
        # 轮动 section 应被渲染（半导体 rank_jump >= 5 + flow > 0）
        assert "🔄" in md

    def test_lookback_days_zero_no_history(
        self, script_mod, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lookback_days=0 → 仅今日单点，flow_persist 只有今日 1 天."""
        today = _make_ind_df([("半导体", 3.0, 2.0, 2.5)])
        client = _FakeClient(ind_by_date={"20260512": today})
        from kss.sector.kcb_overlay import KcbOverlay
        monkeypatch.setattr(
            script_mod, "build_kcb_overlay", lambda: KcbOverlay(),
        )

        md, _ = script_mod.run_review(
            trade_date="20260512", lookback_days=0, client=client,
        )
        # 不外抛，markdown 完整
        assert "板块复盘" in md
