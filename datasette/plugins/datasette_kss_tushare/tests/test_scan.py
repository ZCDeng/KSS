"""Tests for the KSS anomaly-scan background-agent goal template."""

from __future__ import annotations

from datasette_kss_tushare import _scan


def test_goal_includes_required_sections():
    goal = _scan.make_anomaly_goal("净流入>2亿 且 涨幅>5%")
    assert "kc_daily" in goal
    assert "kc_moneyflow" in goal
    assert "mark_finished" in goal
    assert "append_to_report" in goal
    assert "净流入>2亿 且 涨幅>5%" in goal
    # The 5 numbered steps must all be present
    for marker in ("1. 确定锚日", "2. 跑筛选 SQL", "3. 对前 top_n",
                   "4. 汇总段", "5. 调 mark_finished"):
        assert marker in goal


def test_goal_anchor_date_default_tells_agent_to_compute_it():
    goal = _scan.make_anomaly_goal("test", anchor_date=None)
    assert "SELECT max(trade_date)" in goal


def test_goal_anchor_date_explicit_pinned():
    goal = _scan.make_anomaly_goal("test", anchor_date="20260520")
    assert "20260520" in goal
    assert "SELECT max(trade_date)" not in goal


def test_goal_documents_table_schemas_and_units():
    """The agent has zero schema context unless we hand it over here."""
    goal = _scan.make_anomaly_goal("test")
    assert "net_mf_amount" in goal
    assert "万元" in goal  # amount unit hint
    assert "千元" in goal  # daily.amount unit hint


def test_goal_parameters_thread_through():
    goal = _scan.make_anomaly_goal("c", anchor_date="20260101",
                                   lookback_days=20, top_n=50)
    assert "回看天数：20" in goal
    assert "最多展开：50" in goal


def test_goal_steers_display_mode_to_model():
    """Agent shouldn't dump rows to user mid-scan; report is the user surface."""
    goal = _scan.make_anomaly_goal("test")
    assert "display='model'" in goal
