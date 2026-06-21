# -*- coding: utf-8 -*-
"""bug_013: validate_predictions.collect 新旧产物共存时按 (fdate, code) 去重。"""
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


vp = _load("validate_predictions", "scripts/validate_predictions.py")

# 一个 stock chunk（parse_review 需要 收/概率 等才完整，但 collect 去重只看 code）。
_STOCK = """📊 *{name}({code}) R06-18/F06-19*
  收 100.00 (+1.00%)
"""

_HEADER = "# KSS 2026-06-18 复盘 / 2026-06-19 预测\n\n📊 *KSS R06-18/F06-19*\n"


def _perdate(*stocks):
    return _HEADER + "\n---\n" + "\n---\n".join(stocks) + "\n"


@pytest.fixture
def review_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vp, "REVIEW_DIR", tmp_path)
    # load_actual 读 cs_data，测试里桩掉
    monkeypatch.setattr(vp, "load_actual", lambda code, date: {
        "close": 101.0, "low": 99.0, "pct_chg": 1.0})
    return tmp_path


def test_old_and_new_same_date_deduped(review_dir):
    # 旧按日 (322+017) + 新按股 (322) 同 date → 322 只算一次
    (review_dir / "2026-06-18.md").write_text(
        _perdate(_STOCK.format(name="奥比中光", code="688322"),
                 _STOCK.format(name="绿的谐波", code="688017")), encoding="utf-8")
    (review_dir / "2026-06-18_688322.SH.md").write_text(
        _HEADER + "\n---\n" + _STOCK.format(name="奥比中光", code="688322") + "\n",
        encoding="utf-8")

    rows = vp.collect(lookback_days=100000)  # cutoff 远在过去, 全收
    codes = sorted(r["code"] for r in rows)
    assert codes == ["688017", "688322"]  # 322 不翻倍
    assert len(rows) == 2


def test_per_symbol_only_no_dup(review_dir):
    (review_dir / "2026-06-18_688322.SH.md").write_text(
        _HEADER + "\n---\n" + _STOCK.format(name="奥比中光", code="688322") + "\n",
        encoding="utf-8")
    (review_dir / "2026-06-18_688017.SH.md").write_text(
        _HEADER + "\n---\n" + _STOCK.format(name="绿的谐波", code="688017") + "\n",
        encoding="utf-8")
    rows = vp.collect(lookback_days=100000)
    assert sorted(r["code"] for r in rows) == ["688017", "688322"]
