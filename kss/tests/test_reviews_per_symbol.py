# -*- coding: utf-8 -*-
"""U3: _reviews() 按股归档解析 + 旧按日产物兼容 (kss_app_bridge)。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load("kss_app_bridge", "scripts/kss_app_bridge.py")

_PERSYMBOL = """# KSS 2026-06-18 复盘 / 2026-06-22 预测

📊 *KSS R06-18/F06-22*

🌐 *大盘背景*
  🟢 科创100: 2157.01 (+2.35%)

---

📊 *{name}({code}) R06-18/F06-22*
  🚀 板块龙头 (alpha 主升)
  收 133.73 (+2.33%)
  形态: ❌ MACD极值

---

_自动生成 · 非投资建议_
"""

_OLD_PERDATE = """# KSS 2026-05-22 复盘 / 2026-05-23 预测

📊 *奥比中光(688322) R05-22/F05-23*
  收 100.00 (+1.00%)

---

📊 *绿的谐波(688017) R05-22/F05-23*
  收 50.00 (+2.00%)
"""


@pytest.fixture
def review_dir(tmp_path, monkeypatch):
    rd = tmp_path / "storage" / "daily_review"
    rd.mkdir(parents=True)
    monkeypatch.setattr(bridge, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "REVIEW_DIR", rd)
    return rd


def test_per_symbol_files_yield_one_entry_each(review_dir):
    (review_dir / "2026-06-18_688322.SH.md").write_text(
        _PERSYMBOL.format(name="奥比中光", code="688322"), encoding="utf-8")
    (review_dir / "2026-06-18_688017.SH.md").write_text(
        _PERSYMBOL.format(name="绿的谐波", code="688017"), encoding="utf-8")

    rows = bridge._reviews()
    assert len(rows) == 2
    by_focus = {r["focusSymbols"][0]: r for r in rows}
    assert set(by_focus) == {"688322.SH", "688017.SH"}
    # 同日两条, date 相同但 focusSymbols/title/path 不同 (逐股覆盖)
    assert all(r["date"] == "2026-06-18" for r in rows)
    assert by_focus["688322.SH"]["title"] == "奥比中光(688322)"
    assert by_focus["688017.SH"]["title"] == "绿的谐波(688017)"
    assert by_focus["688322.SH"]["path"].endswith("2026-06-18_688322.SH.md")


def test_focus_symbols_from_filename_not_regex(review_dir):
    # 正文里出现别的代码也不该污染 focusSymbols (按文件名确定)
    body = _PERSYMBOL.format(name="奥比中光", code="688322") + "\n参考 300750 宁德\n"
    (review_dir / "2026-06-18_688322.SH.md").write_text(body, encoding="utf-8")
    rows = bridge._reviews()
    assert rows[0]["focusSymbols"] == ["688322.SH"]


def test_old_per_date_file_still_parsed(review_dir):
    (review_dir / "2026-05-22.md").write_text(_OLD_PERDATE, encoding="utf-8")
    rows = bridge._reviews()
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-05-22"
    assert set(r["focusSymbols"]) == {"688322", "688017"}  # 旧文件正则反解
    assert r["title"].startswith("KSS 2026-05-22")


def test_mixed_old_and_new_coexist(review_dir):
    (review_dir / "2026-06-18_688322.SH.md").write_text(
        _PERSYMBOL.format(name="奥比中光", code="688322"), encoding="utf-8")
    (review_dir / "2026-05-22.md").write_text(_OLD_PERDATE, encoding="utf-8")
    rows = bridge._reviews()
    assert len(rows) == 2
    dates = sorted(r["date"] for r in rows)
    assert dates == ["2026-05-22", "2026-06-18"]
