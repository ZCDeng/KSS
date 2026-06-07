"""kss/sector/momentum_regime.py 单元测试.

覆盖：
- mom20 计算（21 个交易日窗口）
- breadth：池规模过滤 + 不完整日跳过 + 实际数据日期标注
- R3 组合判定（双条件 / breadth 缺失保守判 False / mom 缺失 → None）
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kss.sector.momentum_regime import (
    BIG_UP_PCT,
    MIN_POOL,
    _breadth_5dma,
    _mom20,
    build_regime_status,
)

_TRADE_DAYS = [f"2026-05-{d:02d}" for d in range(4, 30) if d % 7 not in (2, 3)]


class _FakeClient:
    def __init__(self, closes: list[float] | None):
        self._closes = closes

    def fetch_index_daily(self, ts_code: str, start: str, end: str):
        if self._closes is None:
            return None
        return pd.DataFrame({
            "trade_date": [f"202605{i + 1:02d}" for i in range(len(self._closes))],
            "close": self._closes,
        })


def _write_pool(tmp: Path, n_stocks: int, big_up_days: dict[str, int]):
    """造 n_stocks 只票 × _TRADE_DAYS; big_up_days[d]=该日大阳家数."""
    for k in range(n_stocks):
        rows = []
        for d in _TRADE_DAYS:
            pct = 10.0 if k < big_up_days.get(d, 0) else 0.5
            rows.append({"trade_date": d, "pct_chg": pct})
        pd.DataFrame(rows).to_csv(tmp / f"cs_data_{600000 + k}.csv", index=False)


def test_mom20():
    closes = [100.0] * 1 + [100.0] * 20 + [110.0]  # 22 行, 末值/倒数21值 = +10%
    assert _mom20("20260522", _FakeClient(closes)) == pytest.approx(10.0)
    assert _mom20("20260522", _FakeClient(None)) is None
    assert _mom20("20260522", _FakeClient([100.0] * 10)) is None  # 不足 21 行


def test_breadth_pool_filter_and_date(tmp_path):
    # 400 只票 (>=MIN_POOL), 最近 5 个交易日每天 12 只大阳 → 3%
    assert MIN_POOL <= 400
    big = {d: 12 for d in _TRADE_DAYS}
    _write_pool(tmp_path, 400, big)
    last = _TRADE_DAYS[-1].replace("-", "")
    breadth, bdate = _breadth_5dma(last, tmp_path)
    assert breadth == pytest.approx(0.03)
    assert bdate == last


def test_breadth_insufficient_pool(tmp_path):
    _write_pool(tmp_path, 50, {})  # 池 < MIN_POOL → 全部日剔除
    breadth, bdate = _breadth_5dma(_TRADE_DAYS[-1].replace("-", ""), tmp_path)
    assert breadth is None and bdate is None


def test_build_regime_status_combinations(tmp_path):
    last = _TRADE_DAYS[-1].replace("-", "")
    _write_pool(tmp_path, 400, {d: 12 for d in _TRADE_DAYS})  # breadth 3% ≥ 2%
    up = [100.0] * 2 + [100.0] * 19 + [110.0]  # mom20 +10% > 8%
    flat = [100.0] * 22                          # mom20 0%

    st = build_regime_status(last, client=_FakeClient(up), cs_dir=tmp_path)
    assert st["in_regime"] is True
    assert st["mom20"] == pytest.approx(10.0)
    assert st["breadth_5dma"] == pytest.approx(0.03)

    st2 = build_regime_status(last, client=_FakeClient(flat), cs_dir=tmp_path)
    assert st2["in_regime"] is False

    # breadth 不可用 → 保守 False, 但 mom20 字段保留
    empty = tmp_path / "empty"
    empty.mkdir()
    st3 = build_regime_status(last, client=_FakeClient(up), cs_dir=empty)
    assert st3["in_regime"] is False
    assert st3["breadth_5dma"] is None

    # mom 不可用 → None
    assert build_regime_status(last, client=_FakeClient(None), cs_dir=tmp_path) is None


def test_big_up_threshold_boundary(tmp_path):
    """涨幅恰好 9.5% 计入大阳, 9.4% 不计."""
    rows = [{"trade_date": d, "pct_chg": BIG_UP_PCT} for d in _TRADE_DAYS]
    for k in range(MIN_POOL):
        pd.DataFrame(rows if k == 0 else
                     [{"trade_date": d, "pct_chg": 9.4} for d in _TRADE_DAYS]
                     ).to_csv(tmp_path / f"cs_data_{600000 + k}.csv", index=False)
    breadth, _ = _breadth_5dma(_TRADE_DAYS[-1].replace("-", ""), tmp_path)
    assert breadth == pytest.approx(1 / MIN_POOL)
