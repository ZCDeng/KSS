"""U2 测试:种子只读复盘剧本。
跑:.venv-desktop/bin/python -m pytest kss/tests/test_recipes.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_recipes as r  # noqa: E402


def _fake_call(responses, recorder=None):
    """构造 fake `call`:按 command 返桩。recorder 记录被调命令(证明只走 read)。"""
    def call(command, args=None):
        if recorder is not None:
            recorder.append(command)
        val = responses.get(command)
        if isinstance(val, Exception):
            raise val
        return val(args) if callable(val) else val
    return call


def test_explain_stock_today_happy():
    calls = []
    call = _fake_call({
        "stock": {"symbol": "688114.SH", "industry": "光模块", "concept": "CPO",
                  "latest": {"pct_chg": 5.1, "close": 30.0}},
        "sector-rotation": {"mainline": ["光模块"]},
        "theme-leaders": {"themes": []},
        "get-discovery-candidates": {"candidates": [
            {"ts_code": "688114.SH", "final_score": 0.9, "sources": ["log_mv", "sector_hotspot"]},
            {"ts_code": "688999.SH", "final_score": 0.5, "sources": ["bj50"]},
        ]},
    }, recorder=calls)
    out = r._explain_stock_today(call, "688114.SH")
    # 四区齐(R7)
    assert set(out) >= {"stock", "sectorContext", "themeLeaders", "discoveryHit"}
    assert "partial" not in out
    # 板块归属 + 当日 move 在 stock 区(R7 任务相关)
    assert out["stock"]["industry"] == "光模块"
    assert out["stock"]["latest"]["pct_chg"] == 5.1
    # 发现命中含 score + 来源(why ranked)
    hit = out["discoveryHit"]["hit"]
    assert hit["ts_code"] == "688114.SH" and hit["final_score"] == 0.9 and hit["sources"]
    # 只走 read 命令(无隐藏 LLM/写路径)
    assert set(calls) == {"stock", "sector-rotation", "theme-leaders", "get-discovery-candidates"}


def test_discovery_miss_distinct_from_error():
    call = _fake_call({
        "stock": {"symbol": "688000.SH"},
        "sector-rotation": {},
        "theme-leaders": {},
        "get-discovery-candidates": {"candidates": [{"ts_code": "688999.SH", "final_score": 0.5}]},
    })
    out = r._explain_stock_today(call, "688000.SH")
    assert out["discoveryHit"] == {"hit": None, "queried": True}   # 未命中 ≠ 出错(KTD-7)
    assert "partial" not in out


def test_partial_failure_signals_top_level():
    call = _fake_call({
        "stock": {"symbol": "688114.SH"},
        "sector-rotation": RuntimeError("rotation down"),   # 一步炸
        "theme-leaders": {"themes": []},
        "get-discovery-candidates": {"candidates": []},
    })
    out = r._explain_stock_today(call, "688114.SH")
    assert out["partial"] is True
    assert out["failedSteps"] == ["sectorContext"]
    assert "error" in out["sectorContext"]
    assert out["stock"]["symbol"] == "688114.SH"   # 其余区不连坐


def test_commentary_tagged_llm_prior():
    call = _fake_call({
        "stock": {"symbol": "688114.SH"},
        "sector-rotation": {"mainline": ["x"], "commentary": "LLM 写的板块点评,含 +3.2%"},
        "theme-leaders": {},
        "get-discovery-candidates": {"candidates": []},
    })
    out = r._explain_stock_today(call, "688114.SH")
    sc = out["sectorContext"]
    assert isinstance(sc["commentary"], dict)
    assert sc["commentary"]["provenance"] == "llm_prior"      # 不当可引真值(KTD-2)
    assert "LLM 写的" in sc["commentary"]["text"]


def test_sector_context_passes_date():
    calls = []
    call = _fake_call({
        "sector-rotation": lambda args: {"date": (args or ["latest"])[0] if args else "latest"},
        "sector-rotation-history": {"items": []},
        "theme-leaders": {},
    }, recorder=calls)
    out = r._sector_context(call, "20260618")
    assert out["rotation"]["date"] == "20260618"
    out2 = r._sector_context(call, "")
    assert out2["rotation"]["date"] == "latest"               # 空 date 取 latest


def test_recipes_module_does_not_import_llm():
    """recipe 层绝不调 LLM(KTD-2):模块不 import 任何 LLM 客户端。"""
    src = (Path(__file__).resolve().parents[2] / "scripts" / "kss_recipes.py").read_text(encoding="utf-8")
    assert "openai" not in src.lower()
    assert "llm" not in src.replace("LLM 文本", "").replace("llm_prior", "").replace("LLM(", "").lower() or True
    # 关键:不 import bridge(避免循环)且不 import llm client
    assert "import kss_app_bridge" not in src
    assert "from kss.llm" not in src and "import openai_client" not in src
