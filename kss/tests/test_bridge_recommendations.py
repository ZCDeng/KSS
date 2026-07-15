"""R6 U4：推荐执行日（_next_open + executionDate）回归。

- 周五数据日 → 执行日为下周一（跨周末）；
- 节假日跳过（trade_cal is_open=0 不入选）；
- 日历失败 → None（UI 退回单数据日，诚实语义不猜工作日）；
- 按日缓存：同日第二次调用不再打日历。

跑：.venv/bin/python -m pytest kss/tests/test_bridge_recommendations.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


class _FakePro:
    def __init__(self, frame: pd.DataFrame | None = None, raise_exc: bool = False):
        self._frame = frame
        self._raise = raise_exc
        self.calls = 0

    def trade_cal(self, **_kwargs):
        self.calls += 1
        if self._raise:
            raise RuntimeError("calendar down")
        return self._frame


def _install(monkeypatch, pro: _FakePro):
    import kss.data.tushare_client as tc

    class _FakeClient:
        def get_pro(self):
            return pro

    monkeypatch.setattr(tc, "TushareClient", _FakeClient)
    b._NEXT_OPEN_CACHE.clear()


def test_next_open_crosses_weekend(monkeypatch):
    """周五 07-10 → 下周一 07-13（周六周日 is_open=0）。"""
    frame = pd.DataFrame([
        {"cal_date": "20260710", "is_open": 1},
        {"cal_date": "20260711", "is_open": 0},
        {"cal_date": "20260712", "is_open": 0},
        {"cal_date": "20260713", "is_open": 1},
    ])
    _install(monkeypatch, _FakePro(frame))
    assert b._next_open("20260710") == "20260713"


def test_next_open_skips_holiday(monkeypatch):
    """节假日（工作日但 is_open=0）跳过。"""
    frame = pd.DataFrame([
        {"cal_date": "20261001", "is_open": 0},
        {"cal_date": "20261002", "is_open": 0},
        {"cal_date": "20261008", "is_open": 1},
    ])
    _install(monkeypatch, _FakePro(frame))
    assert b._next_open("20260930") == "20261008"


def test_next_open_calendar_failure_returns_none(monkeypatch):
    """日历失败 → None（不猜工作日；UI 退回单数据日）。"""
    _install(monkeypatch, _FakePro(raise_exc=True))
    assert b._next_open("20260710") is None


def test_next_open_cached_per_day(monkeypatch):
    """同日第二次调用命中缓存，不再打日历（快照渲染路径上的延迟保护）。"""
    frame = pd.DataFrame([
        {"cal_date": "20260714", "is_open": 1},
        {"cal_date": "20260715", "is_open": 1},
    ])
    pro = _FakePro(frame)
    _install(monkeypatch, pro)
    assert b._next_open("20260714") == "20260715"
    assert b._next_open("20260714") == "20260715"
    assert pro.calls == 1
