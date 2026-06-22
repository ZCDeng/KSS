"""U5 测试：dispatch orientation + COMMANDS registry 漂移守卫 + 可证伪代理 + doc 存在性。
跑：.venv-desktop/bin/python -m pytest kss/tests/test_bridge_orientation.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402

_SRC = (Path(__file__).resolve().parents[2] / "scripts" / "kss_app_bridge.py").read_text(encoding="utf-8")


def _dispatch_body() -> str:
    i = _SRC.index("def dispatch(command")
    return _SRC[i:_SRC.index("\n\ndef ", i)]


def _run_task_body() -> str:
    i = _SRC.index("def run_task(task_id")
    return _SRC[i:i + 4000]


def test_orientation_has_all_regions(tmp_path, monkeypatch):
    p = tmp_path / "data_catalog.json"
    p.write_text(json.dumps({"generatedAt": "x", "datasetsResolved": 22, "datasetsExpected": 22,
                             "datasets": [{"name": "margin_daily", "kind": "parquet",
                                           "latestDate": "20260618", "columns": [{"name": "rzye"}]}]}),
                 encoding="utf-8")
    monkeypatch.setattr(b, "DATA_CATALOG_PATH", p)
    monkeypatch.setattr(b, "_scheduled_jobs", lambda: [{"label": "x"}])
    b._catalog_cache["mtime"] = None
    o = b.dispatch("orientation", [])
    for region in ("commands", "runTaskWhitelist", "dataCatalog", "cron", "docs"):
        assert o[region], f"empty region: {region}"
    assert o["dataCatalog"]["datasets"][0]["latestDate"] == "20260618"


def test_command_read_write_marks():
    o_cmds = {c["command"]: c for c in
              [{"command": k, "write": k in b.WRITE_COMMANDS, **v} for k, v in b.COMMANDS.items()]}
    assert o_cmds["run"]["write"] is True
    assert o_cmds["snapshot"]["write"] is False
    assert o_cmds["data-catalog"]["write"] is False


def test_command_drift_guard():
    """dispatch if-chain 出现的每个命令字面量都须在 COMMANDS（漏登记即红）—— KTD-3。"""
    body = _dispatch_body()
    eq = set(re.findall(r'command == "([^"]+)"', body))
    inset = set()
    for grp in re.findall(r'command in \{([^}]+)\}', body):
        inset |= set(re.findall(r'"([^"]+)"', grp))
    used = eq | inset
    missing = used - set(b.COMMANDS)
    assert not missing, f"dispatch 命令未登记 COMMANDS: {missing}"


def test_run_task_whitelist_in_sync():
    """orientation 报的 RUN_TASKS 须 == run_task if-chain 实际接受集合。"""
    actual = set(re.findall(r'task_id == "([^"]+)"', _run_task_body()))
    assert set(b.RUN_TASKS) == actual, f"RUN_TASKS 与 run_task 不一致: {set(b.RUN_TASKS) ^ actual}"


def test_falsifiable_zero_arg_commands_wired(monkeypatch):
    """可证伪代理(R6.a)：orientation 报的零参 read 命令实调不报 unknown command。"""
    monkeypatch.setattr(b, "_scheduled_jobs", lambda: [])
    # 取零参、只读、且不触发重 IO 的命令做 wiring 探针。
    for cmd in ("orientation",):
        out = b.dispatch(cmd, [])
        assert isinstance(out, dict)
    # 全量零参命令至少不抛 "unknown command"（wiring 存在）。
    zero_arg = [k for k, v in b.COMMANDS.items() if not v["args"] and k not in b.WRITE_COMMANDS]
    for cmd in zero_arg:
        try:
            b.dispatch(cmd, [])
        except ValueError as e:
            assert "unknown command" not in str(e), f"{cmd} 未接线"
        except Exception:
            pass  # 真实 IO 失败不在本测试范围；只验证命令已接线


def test_doc_pointers_exist():
    """doc 指针须解析到真实文件（防「手维 doc 必烂」同类漂移）—— product P3。"""
    for ptr in b._doc_pointers():
        assert ptr["exists"], f"doc 指针失效: {ptr['path']}"


def test_catalog_missing_degrades_other_regions(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "DATA_CATALOG_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(b, "_scheduled_jobs", lambda: [{"label": "x"}])
    b._catalog_cache["mtime"] = None
    o = b.dispatch("orientation", [])
    assert "error" in o["dataCatalog"]      # catalog 区降级
    assert o["commands"] and o["cron"]      # 其余区仍正常


def test_collect_intraday_label_registered():
    """U7：collect_intraday 标签已在 U6 注册 → bridge 暴露正确 title/category。"""
    assert b.LABEL_TITLES["collect_intraday"] == "分时收盘采集"
    assert b.LABEL_CATEGORY["collect_intraday"] == "数据更新"


def test_scheduled_job_collect_intraday_title_category(tmp_path):
    """U7：_scheduled_job 对 collect_intraday plist 返回正确 title/category（不调 launchctl 真实状态依赖）。"""
    import plistlib

    label = "com.zcdeng.kss.collect_intraday"
    plist = tmp_path / f"{label}.plist"
    with plist.open("wb") as fh:
        plistlib.dump(
            {
                "Label": label,
                "ProgramArguments": ["/abs/scripts/run_collect_intraday.sh"],
                "StartCalendarInterval": {"Hour": 15, "Minute": 5},
                "StandardOutPath": str(tmp_path / "out.log"),
            },
            fh,
        )
    job = b._scheduled_job(label, plist, uid=501, disabled=set())
    assert job["title"] == "分时收盘采集"
    assert job["category"] == "数据更新"
    assert job["script"] == "run_collect_intraday.sh"
