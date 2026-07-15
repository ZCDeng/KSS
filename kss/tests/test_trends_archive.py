"""R6 U3：趋势归档日轴/回补/路径迁移回归。

- 日轴锚 cs_data（hsgt 滞后不再拖累归档水位）；
- 缺口回补：(已归档, latest] 的洞一次跑齐，幂等；
- 跨周末候选日正确；
- 数据路径来自 kss.config.paths（bundle 副本执行也写 STATE_ROOT，不写 __file__ 推导根）。

跑：.venv/bin/python -m pytest kss/tests/test_trends_archive.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO))

_spec = importlib.util.spec_from_file_location(
    "archive_trends_daily", _REPO / "scripts" / "archive_trends_daily.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["archive_trends_daily"] = mod
_spec.loader.exec_module(mod)


def _fake_rows(dates: list[str]):
    return {"600000.SH": [{"trade_date": d} for d in dates]}


def test_axis_anchors_cs_data_not_hsgt(monkeypatch):
    """cs_data 轴含最新收盘日时，轴水位不受 hsgt 滞后影响。"""
    dates = ["2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"]
    monkeypatch.setattr(mod.kb, "_rows_by_symbol", lambda: _fake_rows(dates))
    axis = mod._trade_axis_dates("2026-07-15")
    assert axis[-1] == "2026-07-15"
    assert "2026-07-14" in axis


def test_pending_dates_backfills_gaps(monkeypatch):
    """已归档只到 07-10 时，候选集含 07-13/07-14/07-15 缺口（升序）。"""
    dates = ["2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"]
    monkeypatch.setattr(mod.kb, "_rows_by_symbol", lambda: _fake_rows(dates))
    archived = {"2026-07-09", "2026-07-10"}
    import kss.storage.trends as trends_mod
    monkeypatch.setattr(trends_mod, "day_exists", lambda d, _db=None: d in archived)
    out = mod._pending_dates("2026-07-15")
    assert out == sorted(out)
    for d in ("2026-07-13", "2026-07-14", "2026-07-15"):
        assert d in out


def test_pending_dates_idempotent_when_no_gap(monkeypatch):
    """无缺口时候选集 = 尾部回扫窗口（north 自愈），不重复扩张。"""
    dates = ["2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"]
    monkeypatch.setattr(mod.kb, "_rows_by_symbol", lambda: _fake_rows(dates))
    import kss.storage.trends as trends_mod
    monkeypatch.setattr(trends_mod, "day_exists", lambda d, _db=None: True)
    out = mod._pending_dates("2026-07-15")
    assert out == dates[-mod.ARCHIVE_WINDOW:]


def test_pending_dates_cross_weekend(monkeypatch):
    """周一跑：候选含上周五（轴上没有周六周日）。"""
    dates = ["2026-07-09", "2026-07-10", "2026-07-13"]  # 五四五→周五 07-10、周一 07-13
    monkeypatch.setattr(mod.kb, "_rows_by_symbol", lambda: _fake_rows(dates))
    archived = {"2026-07-09"}
    import kss.storage.trends as trends_mod
    monkeypatch.setattr(trends_mod, "day_exists", lambda d, _db=None: d in archived)
    out = mod._pending_dates("2026-07-13")
    assert "2026-07-10" in out and "2026-07-13" in out
    assert "2026-07-11" not in out and "2026-07-12" not in out


def test_data_paths_come_from_kss_config_paths():
    """写路径迁移钉死：脚本数据路径 == kss.config.paths 常量（吃 env override），
    不再是 __file__ 推导——bundle 副本执行时这就是 07-14 PermissionError 的差别。"""
    from kss.config import paths

    assert mod.KSS_DB == paths.KSS_DB
    assert mod.HSGT_PARQUET == paths.MACRO_ROOT / "hsgt_daily.parquet"
