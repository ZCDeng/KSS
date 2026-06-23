"""U2 单测：清单驱动 launchd plist 渲染器（plan 2026-06-23-001）.

覆盖 plan U2 test scenarios：3 调度形态、args、安全（env 白名单 / token 拒绝 /
Label≠stem / 残留 marker / contains_credential 被调用）、确定性、金标（vs 14 已装
+ data_catalog 模板，故意错调度 → 拒）、plutil -lint。

铁律：所有产物落 ``tmp_path``，不写真实 deploy/ 或 storage/。
"""
from __future__ import annotations

import importlib.util
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from kss.config.cron_manifest import load_manifest

_REPO = Path(__file__).resolve().parents[2]
_RENDER_PATH = _REPO / "scripts" / "render_launchd_plists.py"
_spec = importlib.util.spec_from_file_location("render_launchd_plists", _RENDER_PATH)
render_mod = importlib.util.module_from_spec(_spec)
sys.modules["render_launchd_plists"] = render_mod
_spec.loader.exec_module(render_mod)

RenderError = render_mod.RenderError

# 已装金标基线：~/Library/LaunchAgents 的 14 个 plist。
_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
# data_catalog_daily 未装，其金标以 deploy/launchd 模板为基线。
_DEPLOY = _REPO / "deploy" / "launchd"
# collect_intraday 走 bundle-mode 独立通道（state-root + launchd/ 日志），不入本统一
# cron-log 金标比对；其等价由 test_intraday_render_plist.py 单独覆盖。
_GOLDEN_EXCLUDE = {"collect_intraday"}


def _job(suffix: str):
    job = load_manifest().job(suffix)
    assert job is not None, f"清单缺任务 {suffix}"
    return job


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    p = tmp_path / "code"
    p.mkdir()
    return p


def _render(project_root: Path, suffix: str, tmp_path: Path) -> dict:
    output = tmp_path / "out" / f"com.zcdeng.kss.{suffix}.plist"
    return render_mod.render(str(project_root), _job(suffix), output)


# --------------------------------------------------------------------------- #
# 3 调度形态
# --------------------------------------------------------------------------- #


def test_daily_weekdays_emits_five_entries(project_root, tmp_path):
    pl = _render(project_root, "update_data_daily", tmp_path)
    sci = pl["StartCalendarInterval"]
    assert isinstance(sci, list) and len(sci) == 5
    assert [e["Weekday"] for e in sci] == [1, 2, 3, 4, 5]
    assert all(e["Hour"] == 8 and e["Minute"] == 30 for e in sci)


@pytest.mark.parametrize("suffix", ["collect_intraday", "data_catalog_daily", "selfcheck"])
def test_daily_no_weekday_single_entry(project_root, tmp_path, suffix):
    pl = _render(project_root, suffix, tmp_path)
    sci = pl["StartCalendarInterval"]
    assert isinstance(sci, dict)
    assert "Weekday" not in sci
    assert "Hour" in sci and "Minute" in sci


def test_weekly_single_entry(project_root, tmp_path):
    pl = _render(project_root, "paper_trade_weekly", tmp_path)
    sci = pl["StartCalendarInterval"]
    assert isinstance(sci, dict)
    assert sci["Weekday"] == 5 and sci["Hour"] == 17 and sci["Minute"] == 0


# --------------------------------------------------------------------------- #
# args
# --------------------------------------------------------------------------- #


def test_args_appended_to_program_arguments(project_root, tmp_path):
    pl = _render(project_root, "scan_bj50_daily", tmp_path)
    prog = pl["ProgramArguments"]
    assert len(prog) == 2
    assert Path(prog[0]).name == "run_scan_bj50_daily.sh"
    assert prog[1] == "--push"


def test_no_args_single_program_argument(project_root, tmp_path):
    pl = _render(project_root, "scanner", tmp_path)
    assert len(pl["ProgramArguments"]) == 1


# --------------------------------------------------------------------------- #
# 安全
# --------------------------------------------------------------------------- #


def test_contains_credential_called_during_render(project_root, tmp_path):
    """mock canonical contains_credential，断言渲染期被调用（防新渲染器另写正则漂移）。"""
    output = tmp_path / "out" / "com.zcdeng.kss.scanner.plist"
    with mock.patch.object(render_mod, "contains_credential", wraps=render_mod.contains_credential) as m:
        render_mod.render(str(project_root), _job("scanner"), output)
    assert m.called, "渲染期未调用 contains_credential（凭据扫描缺失）"


def test_reject_non_whitelist_env(project_root, tmp_path):
    pl = render_mod.build_plist(str(project_root), _job("scanner"))
    pl["EnvironmentVariables"]["EVIL"] = "x"
    with pytest.raises(RenderError):
        render_mod._validate(pl, tmp_path / "com.zcdeng.kss.scanner.plist")


def test_reject_token_form_string(project_root, tmp_path):
    pl = render_mod.build_plist(str(project_root), _job("scanner"))
    pl["WorkingDirectory"] = "/" + "a" * 40  # 40-hex 命中 CREDENTIAL_VALUE_RE
    with pytest.raises(RenderError):
        render_mod._validate(pl, tmp_path / "com.zcdeng.kss.scanner.plist")


def test_reject_label_not_stem(project_root, tmp_path):
    pl = render_mod.build_plist(str(project_root), _job("scanner"))
    with pytest.raises(RenderError):
        render_mod._validate(pl, tmp_path / "com.zcdeng.kss.something_else.plist")


def test_reject_residual_marker(project_root, tmp_path):
    pl = render_mod.build_plist(str(project_root), _job("scanner"))
    pl["WorkingDirectory"] = "__PROJECT_ROOT__"
    with pytest.raises(RenderError):
        render_mod._validate(pl, tmp_path / "com.zcdeng.kss.scanner.plist")


def test_reject_token_env_key(project_root, tmp_path):
    pl = render_mod.build_plist(str(project_root), _job("scanner"))
    pl["EnvironmentVariables"]["TUSHARE_TOKEN"] = "x"
    with pytest.raises(RenderError):
        render_mod._validate(pl, tmp_path / "com.zcdeng.kss.scanner.plist")


# --------------------------------------------------------------------------- #
# 确定性
# --------------------------------------------------------------------------- #


def test_render_byte_identical_on_rerun(project_root, tmp_path):
    out1 = tmp_path / "a" / "com.zcdeng.kss.scanner.plist"
    out2 = tmp_path / "b" / "com.zcdeng.kss.scanner.plist"
    render_mod.render(str(project_root), _job("scanner"), out1)
    render_mod.render(str(project_root), _job("scanner"), out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_render_all_writes_every_job(project_root, tmp_path):
    out_dir = tmp_path / "deploy"
    rendered = render_mod.render_all(str(project_root), out_dir)
    manifest = load_manifest()
    assert set(rendered) == {j.suffix for j in manifest.jobs}
    for j in manifest.jobs:
        assert (out_dir / f"{j.label}.plist").exists()


def test_generated_header_present(project_root, tmp_path):
    out = tmp_path / "com.zcdeng.kss.scanner.plist"
    render_mod.render(str(project_root), _job("scanner"), out)
    text = out.read_text(encoding="utf-8")
    assert "GENERATED" in text and "cron_jobs.yaml" in text


# --------------------------------------------------------------------------- #
# 金标（R6）：渲染 vs 14 已装 + data_catalog 模板，语义等价；故意错调度 → 拒
# --------------------------------------------------------------------------- #


def _baseline_for(suffix: str) -> dict | None:
    """已装优先；data_catalog 未装取 deploy 模板。返回 None = 无基线（skip）。"""
    installed = _LAUNCH_AGENTS / f"com.zcdeng.kss.{suffix}.plist"
    if installed.is_file():
        with installed.open("rb") as fh:
            return plistlib.load(fh)
    template = _DEPLOY / f"com.zcdeng.kss.{suffix}.plist"
    if template.is_file():
        with template.open("rb") as fh:
            return plistlib.load(fh)
    return None


def test_golden_all_jobs_equivalent(project_root, tmp_path):
    """渲染每个清单任务 vs 已知良基线，schedule/wrapper/日志路径语义等价。"""
    manifest = load_manifest()
    checked = 0
    for job in manifest.jobs:
        if job.suffix in _GOLDEN_EXCLUDE:
            continue
        baseline = _baseline_for(job.suffix)
        if baseline is None:
            pytest.fail(f"{job.suffix} 无金标基线（既未装也无 deploy 模板）")
        out = tmp_path / f"com.zcdeng.kss.{job.suffix}.plist"
        rendered = render_mod.render(str(project_root), job, out)
        render_mod.assert_golden_equivalent(rendered, baseline)
        checked += 1
    # 15 jobs - collect_intraday(excluded) = 15 个比对（含 data_catalog 模板基线）。
    assert checked == len(manifest.jobs) - len(_GOLDEN_EXCLUDE)


def test_golden_rejects_deliberate_schedule_mismatch(project_root, tmp_path):
    """故意把某任务时刻改错 → 金标闸拒（不予 U3 --apply）。"""
    baseline = _baseline_for("update_data_daily")
    assert baseline is not None
    out = tmp_path / "com.zcdeng.kss.update_data_daily.plist"
    rendered = render_mod.render(str(project_root), _job("update_data_daily"), out)
    # 篡改渲染输出的小时 → 与基线不再等价。
    sci = rendered["StartCalendarInterval"]
    for e in sci:
        e["Hour"] = 23
    with pytest.raises(RenderError):
        render_mod.assert_golden_equivalent(rendered, baseline)


# --------------------------------------------------------------------------- #
# plutil -lint（macOS-only）
# --------------------------------------------------------------------------- #


def test_plutil_lint_all_outputs(project_root, tmp_path):
    if shutil.which("plutil") is None:
        pytest.skip("plutil 不可用（非 macOS）")
    out_dir = tmp_path / "deploy"
    render_mod.render_all(str(project_root), out_dir)
    for plist in sorted(out_dir.glob("com.zcdeng.kss.*.plist")):
        proc = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{plist.name}: {proc.stdout}{proc.stderr}"
