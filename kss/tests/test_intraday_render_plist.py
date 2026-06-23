"""collect_intraday 渲染等价性 + wrapper 凭据纪律（plan 2026-06-23-001 / U2 承接）.

原 `render_intraday_launchd_plist.py`（单任务渲染器）已被 `render_launchd_plists.py`
（清单驱动通用渲染器）**泛化取代**。本测试改指向新渲染器，断言 `collect_intraday`
经清单渲染**等价**：Label、15:05 单条目（无 Weekday）、env 白名单、凭据拒绝。
另保留 wrapper 凭据纪律（S1）静态 + 运行行为检查。

铁律（KTD6 / 学习 #4）：所有渲染用例落 ``tmp_path``，不写真实 deploy/ 或 storage/。
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kss.config.cron_manifest import load_manifest

# 直接按文件路径 import 渲染器脚本（scripts/ 非 package）。
_REPO = Path(__file__).resolve().parents[2]
_RENDER_PATH = _REPO / "scripts" / "render_launchd_plists.py"
_spec = importlib.util.spec_from_file_location("render_launchd_plists", _RENDER_PATH)
render_mod = importlib.util.module_from_spec(_spec)
sys.modules["render_launchd_plists"] = render_mod
_spec.loader.exec_module(render_mod)

RenderError = render_mod.RenderError

_SUFFIX = "collect_intraday"
_LABEL = "com.zcdeng.kss.collect_intraday"
_WRAPPER = _REPO / "scripts" / "run_collect_intraday.sh"


def _collect_intraday_job():
    job = load_manifest().job(_SUFFIX)
    assert job is not None, "清单缺 collect_intraday 任务"
    return job


@pytest.fixture()
def roots(tmp_path: Path) -> tuple[Path, Path]:
    """(project_root, output)；project_root 落 tmp，避免触碰真实仓库。"""
    project_root = tmp_path / "code"
    project_root.mkdir()
    output = tmp_path / "deploy" / "launchd" / f"{_LABEL}.plist"
    return project_root, output


# --------------------------------------------------------------------------- #
# collect_intraday 渲染等价：Label / 15:05 单条目 / env 白名单 / 凭据拒绝
# --------------------------------------------------------------------------- #


def test_collect_intraday_label_equals_stem(roots):
    project_root, output = roots
    pl = render_mod.render(str(project_root), _collect_intraday_job(), output)
    assert pl["Label"] == _LABEL == output.name[:-6]


def test_collect_intraday_schedule_is_1505_single_no_weekday(roots):
    project_root, output = roots
    pl = render_mod.render(str(project_root), _collect_intraday_job(), output)
    sci = pl["StartCalendarInterval"]
    # 单条目（dict，非 list），无 Weekday 键。
    assert isinstance(sci, dict)
    assert "Weekday" not in sci
    assert sci["Hour"] == 15 and sci["Minute"] == 5


def test_collect_intraday_wrapper_and_log(roots):
    project_root, output = roots
    pl = render_mod.render(str(project_root), _collect_intraday_job(), output)
    prog = pl["ProgramArguments"]
    assert len(prog) == 1
    assert Path(prog[0]) == project_root / "scripts" / "run_collect_intraday.sh"
    assert pl["StandardOutPath"] == str(
        project_root / "storage" / "logs" / "cron" / "collect_intraday.log"
    )


def test_collect_intraday_env_whitelist_no_token(roots):
    project_root, output = roots
    pl = render_mod.render(str(project_root), _collect_intraday_job(), output)
    env = pl["EnvironmentVariables"]
    assert set(env.keys()) <= {"PATH", "HOME", "KSS_STATE_ROOT"}
    assert "TUSHARE_TOKEN" not in env
    for k in env:
        assert "TOKEN" not in k.upper()


def test_collect_intraday_rejects_token_in_path(roots):
    """渲染输出任一字符串命中 token-pattern → 拒绝、不写文件（S5）。"""
    _project_root, output = roots
    token = "a" * 40  # 40-hex，命中 CREDENTIAL_VALUE_RE
    poisoned = output.parent.parent.parent / "code" / token
    poisoned.mkdir(parents=True)
    with pytest.raises(RenderError):
        render_mod.render(str(poisoned), _collect_intraday_job(), output)
    assert not output.exists(), "拒绝时不得写出文件"


def test_token_pattern_uses_canonical_redaction_constant():
    """复用 kss.security.redaction 的单一 canonical 常量（S3），非另定义。"""
    from kss.security import redaction

    assert render_mod.CREDENTIAL_VALUE_RE is redaction.CREDENTIAL_VALUE_RE
    assert render_mod.contains_credential is redaction.contains_credential


def test_reject_relative_project_root(roots):
    _project_root, output = roots
    with pytest.raises(RenderError):
        render_mod.render("relative/code", _collect_intraday_job(), output)
    assert not output.exists()


def test_reject_label_mismatch_filename(roots):
    project_root, _output = roots
    pl = render_mod.build_plist(str(project_root), _collect_intraday_job())
    wrong_output = Path("/tmp/com.zcdeng.kss.something_else.plist")
    with pytest.raises(RenderError):
        render_mod._validate(pl, wrong_output)


def test_plutil_lint_smoke(roots):
    """命令级部署 smoke：渲染产物过 plutil -lint（macOS-only；缺则 skip）。"""
    if shutil.which("plutil") is None:
        pytest.skip("plutil 不可用（非 macOS）")
    project_root, output = roots
    render_mod.render(str(project_root), _collect_intraday_job(), output)
    proc = subprocess.run(["plutil", "-lint", str(output)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# S1：wrapper 凭据纪律（静态文本 + 运行行为）
# --------------------------------------------------------------------------- #


def test_wrapper_text_has_no_env_grep_or_token_export():
    """wrapper **可执行行**不含 .env grep / export TUSHARE_TOKEN / token echo（S1）。"""
    text = _WRAPPER.read_text(encoding="utf-8")
    code_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines).lower()
    assert "tushare_token" not in code, "wrapper 可执行行不得引用 TUSHARE_TOKEN"
    assert ".env" not in code, "wrapper 可执行行不得 grep .env"
    assert not re.search(r"grep.*token", code), "wrapper 不得 grep token"
    assert "export tushare_token" not in code
    exports = re.findall(r"export\s+([A-Z_]+)", "\n".join(code_lines))
    assert set(exports) <= {"KSS_STATE_ROOT"}, f"wrapper export 了非授权 env: {exports}"
