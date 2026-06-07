"""kss/sector/etf_radar.py 单元测试.

覆盖：
- 份额加权 1d/5d 流计算正确性
- 主题间 rank_5d 排名
- 单只 ETF 缺数降级（主题内其他只不受影响）
- 全部失败 → None（fail loud）
- staleness 标记（数据滞后 >4 自然日）
- accel 加速阈值
- build_context 注入 etf_radar payload + missing 维度
"""

from __future__ import annotations

import pandas as pd
import pytest

from kss.sector.etf_radar import (
    ETF_BASKET,
    EtfRadar,
    _theme_flows,
    build_etf_radar,
)

# 雷达窗口内的 7 个交易日（升序）
_DATES = ["20260527", "20260528", "20260529", "20260601",
          "20260602", "20260603", "20260604"]


def _share_df(shares: list[float]) -> pd.DataFrame:
    """构造 fund_share 模拟返回（Tushare 实际为日期倒序，模块需自行排序）."""
    return pd.DataFrame({
        "trade_date": list(reversed(_DATES[:len(shares)])),
        "fd_share": list(reversed(shares)),
    })


class _FakeClient:
    """按 ts_code 返回预设份额/净值表；不在表内 → None（模拟拉取失败）."""

    def __init__(self, data: dict[str, pd.DataFrame],
                 navs: dict[str, pd.DataFrame] | None = None):
        self._data = data
        self._navs = navs or {}
        self.calls: list[str] = []

    def fetch_fund_share(self, ts_code: str, start: str, end: str):
        self.calls.append(ts_code)
        return self._data.get(ts_code)

    def fetch_fund_nav(self, ts_code: str, start: str, end: str):
        return self._navs.get(ts_code)


def test_theme_flows_weighted():
    """两只 ETF 合计份额：100+300=400 → 末日 110+290=400 → flow_1d=0."""
    df = pd.concat([
        pd.DataFrame({"trade_date": _DATES[:6], "fd_share": [100] * 5 + [110]}),
        pd.DataFrame({"trade_date": _DATES[:6], "fd_share": [300] * 5 + [290]}),
    ])
    flow_1d, flow_5d = _theme_flows(df)
    assert flow_1d == pytest.approx(0.0)
    assert flow_5d == pytest.approx(0.0)


def test_theme_flows_insufficient_history():
    df = pd.DataFrame({"trade_date": _DATES[:1], "fd_share": [100]})
    assert _theme_flows(df) == (None, None)


def _full_fake_client(last_share: dict[str, float]) -> _FakeClient:
    """给 basket 每只 ETF 造 7 天平稳份额，末日按 last_share 偏移."""
    data = {}
    for funds in ETF_BASKET.values():
        for ts_code, _ in funds:
            end = last_share.get(ts_code, 100.0)
            data[ts_code] = _share_df([100.0] * 6 + [end])
    return _FakeClient(data)


def test_build_radar_ranks_and_flows():
    # 科创芯片 (588200) 末日 +2%, 机器人两只各 -3% → 排名首尾分明
    client = _full_fake_client({
        "588200.SH": 102.0,
        "562500.SH": 97.0,
        "159770.SZ": 97.0,
    })
    radar = build_etf_radar("20260604", client=client)
    assert radar is not None
    assert radar.data_date == "20260604"
    assert not radar.stale
    assert radar.themes["科创芯片"]["flow_1d"] == pytest.approx(2.0)
    assert radar.themes["机器人"]["flow_1d"] == pytest.approx(-3.0)
    # rank_5d: 科创芯片最获配置 (其余主题 5d 流=0, 科创芯片 +2, 机器人 -3)
    assert radar.themes["科创芯片"]["rank_5d"] == 1
    assert radar.themes["机器人"]["rank_5d"] == len(radar.themes)
    # accel: |2.0| > 1.5 → True; |0| → False
    assert radar.themes["科创芯片"]["accel"] is True
    assert radar.themes["科创50"]["accel"] is False


def test_build_radar_partial_fund_failure():
    """机器人主题一只缺数 → 主题仍在（用剩余一只），缺的进 missing_funds."""
    client = _full_fake_client({})
    del client._data["159770.SZ"]
    radar = build_etf_radar("20260604", client=client)
    assert radar is not None
    assert "159770.SZ" in radar.missing_funds
    assert "机器人" in radar.themes
    assert radar.themes["机器人"]["flow_1d"] == pytest.approx(0.0)


def test_build_radar_total_failure_returns_none():
    radar = build_etf_radar("20260604", client=_FakeClient({}))
    assert radar is None


def test_build_radar_stale_flag():
    """数据最新日落后目标日 >4 自然日 → stale=True."""
    client = _full_fake_client({})
    radar = build_etf_radar("20260612", client=client)  # 数据只到 0604
    assert radar is not None
    assert radar.stale


def _nav_df(navs: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "nav_date": _DATES[:len(navs)],
        "unit_nav": navs,
        "accum_nav": [None] * len(navs),  # 缺 accum → 回退 unit_nav
    })


def test_grade_semantics():
    """flow_5d ≤-2 强势确认 / -2~0 中性偏多 / >0 偏弱."""
    from kss.sector.etf_radar import _grade
    assert _grade(-2.5) == "强势确认"
    assert _grade(-0.5) == "中性偏多"
    assert _grade(1.0) == "偏弱"
    assert _grade(None) is None


def test_divergence_flag_and_past5_ret():
    """强动量 (past5≥3%) + flow_5d 转正 → divergence=True."""
    client = _full_fake_client({"588200.SH": 102.0})  # 科创芯片 flow_5d=+2
    # NAV 7 天从 1.00 涨到 1.06: past5 = 1.06/1.01-1 ≈ +4.95%
    client._navs = {"588200.SH": _nav_df([1.00, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06])}
    radar = build_etf_radar("20260604", client=client)
    assert radar is not None
    chip = radar.themes["科创芯片"]
    assert chip["past5_ret"] == pytest.approx(4.95, abs=0.01)
    assert chip["grade"] == "偏弱"
    assert chip["divergence"] is True
    # 无 NAV 的主题: past5_ret=None, divergence=False, grade 仍有
    assert radar.themes["科创50"]["past5_ret"] is None
    assert radar.themes["科创50"]["divergence"] is False
    assert radar.themes["科创50"]["grade"] == "中性偏多"


def test_divergence_requires_positive_flow():
    """强动量但仍在赎回 (flow_5d<0) → 不预警 (那是强势确认)."""
    client = _full_fake_client({"588200.SH": 97.0})  # flow_5d=-3
    client._navs = {"588200.SH": _nav_df([1.00, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06])}
    radar = build_etf_radar("20260604", client=client)
    chip = radar.themes["科创芯片"]
    assert chip["grade"] == "强势确认"
    assert chip["divergence"] is False


def test_regime_passthrough_in_payload():
    client = _full_fake_client({})
    regime = {"in_regime": True, "mom20": 9.1}
    radar = build_etf_radar("20260604", client=client, regime=regime)
    assert radar.to_payload()["momentum_regime_r3"] == regime
    # 不传 → None
    radar2 = build_etf_radar("20260604", client=client)
    assert radar2.to_payload()["momentum_regime_r3"] is None


def test_payload_and_context_injection():
    radar = EtfRadar(data_date="20260604", themes={"科创50": {"flow_1d": -1.0}})
    payload = radar.to_payload()
    assert payload["data_date"] == "20260604"
    assert "强势确认" in payload["note"] and "见顶预警" in payload["note"]

    # build_context: radar=None → missing 含 etf_radar; 有 radar → payload 注入
    from kss.sector.commentary import build_context
    from kss.sector.data_fetcher import SectorSnapshot
    from kss.sector.kcb_overlay import KcbOverlay

    snap = SectorSnapshot(trade_date="20260604")
    overlay = KcbOverlay()
    ctx_none = build_context(snap, {}, {}, overlay)
    assert "etf_radar" in ctx_none["missing"]
    ctx = build_context(snap, {}, {}, overlay, etf_radar=radar)
    assert ctx["etf_radar"]["data_date"] == "20260604"
    assert "etf_radar" not in ctx["missing"]


def test_fallback_includes_divergence():
    """LLM fallback 必须播报见顶预警与强势确认 (不依赖 LLM 可用性)."""
    from kss.sector.commentary import fallback_text
    from kss.sector.data_fetcher import SectorSnapshot

    radar = EtfRadar(data_date="20260605", themes={
        "机器人": {"grade": "偏弱", "divergence": True},
        "科创芯片": {"grade": "强势确认", "divergence": False},
    })
    text = fallback_text("20260605", SectorSnapshot(trade_date="20260605"),
                         {}, reason="no key", etf_radar=radar)
    assert "见顶预警" in text and "机器人" in text
    assert "强势确认" in text and "科创芯片" in text
    # 不传雷达 → 不输出雷达行 (向后兼容)
    text2 = fallback_text("20260605", SectorSnapshot(trade_date="20260605"), {})
    assert "ETF 雷达" not in text2
