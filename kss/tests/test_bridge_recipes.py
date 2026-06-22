"""U3 测试:bridge recipe-list/run-recipe + 能力式门控 + arg 校验 + orientation 露出。
跑:.venv-desktop/bin/python -m pytest kss/tests/test_bridge_recipes.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402
import kss_recipes as r  # noqa: E402


@pytest.fixture
def inject_recipe(monkeypatch):
    """临时往 RECIPES 注入测试剧本。"""
    added = []

    def _add(name, meta):
        monkeypatch.setitem(r.RECIPES, name, meta)
        added.append(name)
    return _add


def test_recipe_list_returns_catalog():
    out = b.dispatch("recipe-list", [])
    assert isinstance(out, list)
    names = {x["name"] for x in out}
    assert "explain_stock_today" in names
    for x in out:
        assert set(x) == {"name", "desc", "write", "args"}   # 无 fn 泄漏


def test_positive_wiring_run_recipe(inject_recipe):
    """正向接线(KTD-6):证明 run-recipe 分支真接上 dispatch,不靠单向漂移守卫。"""
    inject_recipe("_t_ok", {"desc": "t", "write": False, "args": [],
                            "fn": lambda call: {"ok": True}})
    out = b.dispatch("run-recipe", ["_t_ok", "{}"])
    assert out == {"ok": True}


def test_capability_gate_blocks_write_command(inject_recipe):
    """核心安全(KTD-3):write=False 剧本内部串写命令 → 受限 call 物理 raise。"""
    inject_recipe("_t_sneaky", {"desc": "sneaky", "write": False, "args": [],
                                "fn": lambda call: call("run", ["refresh-sector-rotation"])})
    with pytest.raises(PermissionError):
        b.dispatch("run-recipe", ["_t_sneaky", "{}"])


def test_systemexit_normalized(inject_recipe):
    """report 路径护栏的 SystemExit 经受限 call 归一为普通异常,不崩 sidecar(KTD-7)。"""
    inject_recipe("_t_report", {"desc": "r", "write": False, "args": [],
                                "fn": lambda call: call("report", ["../../etc/passwd"])})
    with pytest.raises(RuntimeError):   # 非 SystemExit
        b.dispatch("run-recipe", ["_t_report", "{}"])


def test_write_declared_recipe_rejected(inject_recipe):
    inject_recipe("_t_w", {"desc": "w", "write": True, "args": [],
                           "fn": lambda call: {"x": 1}})
    out = b.dispatch("run-recipe", ["_t_w", "{}"])
    assert out["error"] == "write_recipe_deferred"


def test_arg_validation(inject_recipe):
    inject_recipe("_t_args", {"desc": "a", "write": False, "args": ["symbol"],
                              "fn": lambda call, symbol="": {"sym": symbol}})
    assert b.dispatch("run-recipe", ["_t_args", '{"bogus":"x"}'])["error"] == "unexpected_args"
    assert b.dispatch("run-recipe", ["_t_args", '{"symbol":123}'])["error"] == "bad_arg_type"
    assert b.dispatch("run-recipe", ["_t_args", "{not json}"])["error"] == "bad_json_args"
    # 空 json → {} → 零参跑通
    assert b.dispatch("run-recipe", ["_t_args", ""]) == {"sym": ""}


def test_unknown_recipe():
    assert b.dispatch("run-recipe", ["nope", "{}"])["error"] == "unknown_recipe"


def test_orientation_exposes_recipes():
    o = b.dispatch("orientation", [])
    assert isinstance(o["recipes"], list)
    assert any(x["name"] == "explain_stock_today" for x in o["recipes"])


def test_orientation_degrades_on_broken_recipes(monkeypatch):
    def boom():
        raise ImportError("recipes broken")
    monkeypatch.setattr(b, "_recipe_list", boom)
    o = b.dispatch("orientation", [])
    assert isinstance(o["recipes"], dict) and "error" in o["recipes"]   # 降级
    assert o["commands"] and o["cron"]                                  # 其余区不崩


def test_recipe_commands_registered_read_only():
    assert "recipe-list" in b.COMMANDS and "run-recipe" in b.COMMANDS
    assert "recipe-list" not in b.WRITE_COMMANDS and "run-recipe" not in b.WRITE_COMMANDS
