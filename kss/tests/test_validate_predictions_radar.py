"""scripts/validate_predictions.py 雷达 grade 校验段单元测试.

覆盖：
- 存档加载（损坏文件跳过）
- grade 分档统计 + 未成熟信号剔除 + divergence 事件 + backfill 计数
- 空存档 → None → render 输出跳过文案
- render 的基线漂移标记
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "validate_predictions", PROJECT_ROOT / "scripts" / "validate_predictions.py")
vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vp)

_DATES = [f"202605{d:02d}" for d in range(11, 23)]  # 12 个伪交易日


def _arc(trade_date: str, grade: str, divergence: bool = False,
         source: str = "live") -> dict:
    return {"trade_date": trade_date, "data_date": trade_date, "source": source,
            "themes": {"科创50": {"grade": grade, "divergence": divergence}}}


def _fake_panel(daily_ret: float = 1.0):
    """ret1: 12 日 × 1 主题, 每日 daily_ret%."""
    ret1 = pd.DataFrame({"科创50": [daily_ret] * len(_DATES)}, index=_DATES)
    return None, ret1


def test_validate_radar_grades(monkeypatch):
    monkeypatch.setattr(vp, "build_radar_panel", lambda s, e: _fake_panel(1.0))
    archives = [
        _arc(_DATES[0], "强势确认"),
        _arc(_DATES[1], "偏弱", divergence=True, source="backfill"),
        _arc(_DATES[-1], "强势确认"),  # 末日: 后向不足 6 日 → 未成熟
    ]
    stats = vp.validate_radar_grades(archives)
    assert stats["n_immature"] == 1
    assert stats["n_backfill"] == 1
    assert stats["grades"]["强势确认"]["n"] == 1
    # 每日 +1% 复利 5 日 ≈ +5.1%
    assert stats["grades"]["强势确认"]["mean"] == pytest.approx(5.1, abs=0.01)
    assert stats["grades"]["强势确认"]["win"] == 100.0
    assert len(stats["divergence"]) == 1
    assert stats["divergence"][0]["theme"] == "科创50"


def test_validate_radar_grades_empty():
    assert vp.validate_radar_grades([]) is None


def test_load_radar_archives_reads_kss_db(tmp_path, monkeypatch):
    from kss.storage.etf_radar import write_snapshot

    monkeypatch.setattr(vp, "PROJECT_ROOT", tmp_path)
    write_snapshot(_arc("20260522", "偏弱"), tmp_path / "storage" / "kss.db")
    arcs = vp.load_radar_archives()
    assert len(arcs) == 1 and arcs[0]["trade_date"] == "20260522"


def test_render_radar_section():
    assert "跳过" in vp.render_radar_section(None)
    stats = {
        "grades": {"强势确认": {"n": 10, "n_dates": 5, "mean": 3.0, "win": 80.0},
                   "偏弱": {"n": 4, "n_dates": 3, "mean": -4.0, "win": 25.0}},
        "divergence": [{"date": "20260522", "theme": "机器人", "fwd5": -3.1}],
        "n_archives": 8, "n_immature": 2, "n_backfill": 4,
    }
    text = vp.render_radar_section(stats)
    assert "✅ 强势确认" in text          # 80 vs 基线 75 → 漂移 ≤15pp
    assert "⚠️ 偏弱" in text             # 25 vs 基线 50 → 漂移 >15pp
    assert "机器人" in text and "-3.10%" in text
    assert "回填样本 4" in text
