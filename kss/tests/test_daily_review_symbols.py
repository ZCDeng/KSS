# -*- coding: utf-8 -*-
"""U1: daily_review --symbols 参数化的纯逻辑单测.

parse_symbols / 后缀推断 / category 默认 / name 回退 / 情形段历史阈值。
不联网 (resolve_name 的 tushare 调用被 mock)。归档命名与情形段门控由
characterization dry-run 实测覆盖 (见 plan U1 verification)。
"""
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


dr = _load("daily_review", "scripts/daily_review.py")


# --- parse_symbols：happy path ---

def test_parse_symbols_explicit_suffix():
    out = dr.parse_symbols("688322.SH,688017.SH")
    assert out == [("688322", "SH", "alpha"), ("688017", "SH", "alpha")]


def test_parse_symbols_sz_and_bj():
    assert dr.parse_symbols("300750.SZ")[0] == ("300750", "SZ", "alpha")
    assert dr.parse_symbols("920819.BJ")[0] == ("920819", "BJ", "alpha")


def test_parse_symbols_default_category_is_alpha():
    assert dr.DEFAULT_CATEGORY == "alpha"
    assert all(cat == "alpha" for _, _, cat in dr.parse_symbols("688322.SH,300750.SZ"))


def test_parse_symbols_lowercase_and_whitespace_normalized():
    assert dr.parse_symbols(" 688322.sh , 300750.sz ") == [
        ("688322", "SH", "alpha"),
        ("300750", "SZ", "alpha"),
    ]


# --- parse_symbols：后缀推断兜底 ---

def test_infer_exchange():
    assert dr._infer_exchange("688322") == "SH"   # 科创
    assert dr._infer_exchange("601318") == "SH"   # 沪主
    assert dr._infer_exchange("300750") == "SZ"   # 创业
    assert dr._infer_exchange("000001") == "SZ"   # 深主
    assert dr._infer_exchange("920819") == "BJ"   # 北交


def test_parse_symbols_infer_when_suffix_missing():
    assert dr.parse_symbols("688322")[0] == ("688322", "SH", "alpha")
    assert dr.parse_symbols("300750")[0] == ("300750", "SZ", "alpha")


# --- parse_symbols：错误路径（大声报错，无静默回退）---

def test_parse_symbols_empty_raises():
    with pytest.raises(ValueError):
        dr.parse_symbols("")
    with pytest.raises(ValueError):
        dr.parse_symbols("   ,  ")


def test_parse_symbols_illegal_code_raises():
    with pytest.raises(ValueError):
        dr.parse_symbols("abc")
    with pytest.raises(ValueError):
        dr.parse_symbols("12345.SH")     # 非 6 位
    with pytest.raises(ValueError):
        dr.parse_symbols("688322.XX")    # 非法交易所


def test_no_hardcoded_stocks_constant():
    # KTD2: STOCKS 写死列表已删除, 不再有静默回退来源
    assert not hasattr(dr, "STOCKS")


# --- resolve_name：失败回退不阻断 ---

def test_resolve_name_falls_back_to_empty_on_error(monkeypatch):
    dr._NAME_CACHE.clear()

    def _boom():
        raise RuntimeError("tushare down")

    monkeypatch.setattr(dr, "_pro", _boom)
    assert dr.resolve_name("688322", "SH") == ""   # 不抛, 标题降级


def test_resolve_name_uses_stock_basic(monkeypatch):
    import pandas as pd
    dr._NAME_CACHE.clear()

    class _FakePro:
        def stock_basic(self, ts_code, fields):
            return pd.DataFrame([{"ts_code": ts_code, "name": "奥比中光"}])

    monkeypatch.setattr(dr, "_pro", lambda: _FakePro())
    assert dr.resolve_name("688322", "SH") == "奥比中光"
    # 命中缓存后不再调用
    monkeypatch.setattr(dr, "_pro", lambda: (_ for _ in ()).throw(AssertionError("不该再调用")))
    assert dr.resolve_name("688322", "SH") == "奥比中光"


# --- 次新股情形段阈值常量 ---

def test_scenario_min_history_constant():
    assert dr.SCENARIO_MIN_HISTORY == 120
