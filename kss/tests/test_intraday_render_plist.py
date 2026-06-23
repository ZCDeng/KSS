"""U6 单测：launchd plist 确定性渲染器 + wrapper 凭据纪律 + F1 非补跑排除.

覆盖 test-spec 集成 #5/#6，及评审 S5（token 拒绝）/ S1（wrapper 凭据）/ F1（非补跑）.

铁律（KTD6 / 学习 #4）：所有渲染用例 **state-root≠code-root**，dev-mode 两根相等会
掩盖 100% bundle-mode bug。所有产物落 ``tmp_path``，不写真实 deploy/ 或 storage/。
"""
from __future__ import annotations

import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import importlib.util

# 直接按文件路径 import 渲染器脚本（scripts/ 非 package）。
_REPO = Path(__file__).resolve().parents[2]
_RENDER_PATH = _REPO / "scripts" / "render_intraday_launchd_plist.py"
_spec = importlib.util.spec_from_file_location("render_intraday_launchd_plist", _RENDER_PATH)
render_mod = importlib.util.module_from_spec(_spec)
sys.modules["render_intraday_launchd_plist"] = render_mod
_spec.loader.exec_module(render_mod)

RenderError = render_mod.RenderError
LABEL = render_mod.LABEL

_WRAPPER = _REPO / "scripts" / "run_collect_intraday.sh"
_TEMPLATE = _REPO / "deploy" / "launchd" / "com.zcdeng.kss.collect_intraday.plist.template"


# --------------------------------------------------------------------------- #
# fixtures：始终 state-root ≠ code-root（KTD6）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(project_root, state_root, output)，两根刻意不同。"""
    project_root = tmp_path / "code"
    state_root = tmp_path / "state"
    project_root.mkdir()
    state_root.mkdir()
    output = tmp_path / "deploy" / "launchd" / f"{LABEL}.plist"
    return project_root, state_root, output


# --------------------------------------------------------------------------- #
# 集成 #5：渲染 → 无标记 / 无相对路径 / 路径解析于供给 root
# --------------------------------------------------------------------------- #


def test_render_resolves_under_supplied_roots(roots):
    project_root, state_root, output = roots
    render_mod.render(str(project_root), str(state_root), output)

    with output.open("rb") as fh:
        pl = plistlib.load(fh)

    # 无残留模板标记。
    placeholder = re.compile(r"__[A-Z][A-Z0-9_]*__")
    blob = output.read_text(encoding="utf-8")
    assert not placeholder.search(blob), "渲染产物残留占位符"

    # ProgramArguments 名绝对 wrapper，且在 code root 下。
    prog = pl["ProgramArguments"]
    assert len(prog) == 1
    wrapper = Path(prog[0])
    assert wrapper.is_absolute()
    assert wrapper == project_root / "scripts" / "run_collect_intraday.sh"

    # WorkingDirectory = code root。
    assert pl["WorkingDirectory"] == str(project_root)

    # EnvironmentVariables.KSS_STATE_ROOT = state root（且只这一注入项）。
    env = pl["EnvironmentVariables"]
    assert env["KSS_STATE_ROOT"] == str(state_root)

    # DB / raw storage 与 stdout/stderr 路径解析于 state root（KTD6 双根）。
    out_path = Path(pl["StandardOutPath"])
    err_path = Path(pl["StandardErrorPath"])
    assert out_path.is_absolute() and err_path.is_absolute()
    assert str(out_path).startswith(str(state_root))
    assert out_path == state_root / "storage" / "logs" / "launchd" / "collect_intraday.log"
    assert err_path == out_path
    # state-root≠code-root：日志不在 code root 下（否则 bundle 模式 FileNotFound）。
    assert not str(out_path).startswith(str(project_root))


def test_no_relative_path_anywhere(roots):
    project_root, state_root, output = roots
    pl = render_mod.render(str(project_root), str(state_root), output)
    for s in render_mod._scan_strings(pl):
        # 形如路径的字符串（含 '/'）必须绝对。
        if s.startswith("storage") or s.startswith("scripts") or s.startswith("./") or s.startswith("../"):
            pytest.fail(f"发现相对路径: {s!r}")


def test_label_equals_filename_stem(roots):
    project_root, state_root, output = roots
    pl = render_mod.render(str(project_root), str(state_root), output)
    assert pl["Label"] == LABEL == output.name[:-6]


def test_schedule_is_1505(roots):
    project_root, state_root, output = roots
    pl = render_mod.render(str(project_root), str(state_root), output)
    sci = pl["StartCalendarInterval"]
    assert sci["Hour"] == 15 and sci["Minute"] == 5


# --------------------------------------------------------------------------- #
# S5：token-pattern 拒绝 + EnvironmentVariables 无 TUSHARE_TOKEN
# --------------------------------------------------------------------------- #


def test_env_has_no_tushare_token(roots):
    project_root, state_root, output = roots
    pl = render_mod.render(str(project_root), str(state_root), output)
    env = pl["EnvironmentVariables"]
    assert "TUSHARE_TOKEN" not in env
    assert set(env.keys()) <= {"PATH", "HOME", "KSS_STATE_ROOT"}
    for k in env:
        assert "TOKEN" not in k.upper()


def test_reject_output_containing_token_pattern(roots):
    """渲染输出任一字符串命中 token-pattern → 拒绝、非零、不写文件（S5）。

    用一个值里嵌 40-hex token 的 state-root 触发（CREDENTIAL_VALUE_RE 命中）。
    """
    project_root, _state_root, output = roots
    token = "a" * 40  # 40-hex，命中 CREDENTIAL_VALUE_RE（独立路径段，前后是 '/' 边界）
    poisoned_state = project_root.parent / "state" / token
    poisoned_state.mkdir(parents=True)
    with pytest.raises(RenderError):
        render_mod.render(str(project_root), str(poisoned_state), output)
    assert not output.exists(), "拒绝时不得写出文件"


def test_token_pattern_uses_canonical_redaction_constant():
    """复用 kss.security.redaction 的单一 canonical 常量（S3），非另定义。"""
    from kss.security import redaction

    assert render_mod.CREDENTIAL_VALUE_RE is redaction.CREDENTIAL_VALUE_RE
    assert render_mod.contains_credential is redaction.contains_credential


# --------------------------------------------------------------------------- #
# 占位符 / 相对路径未解析 → 拒绝
# --------------------------------------------------------------------------- #


def test_reject_relative_project_root(roots):
    _project_root, state_root, output = roots
    with pytest.raises(RenderError):
        render_mod.render("relative/code", str(state_root), output)
    assert not output.exists()


def test_reject_relative_state_root(roots):
    project_root, _state_root, output = roots
    with pytest.raises(RenderError):
        render_mod.render(str(project_root), "relative/state", output)
    assert not output.exists()


def test_reject_unresolved_placeholder(roots):
    """build_plist 注入残留占位符 → _validate 拒绝（模拟漏替换）。"""
    project_root, state_root, output = roots
    pl = render_mod.build_plist(str(project_root), str(state_root))
    pl["WorkingDirectory"] = "__PROJECT_ROOT__"
    with pytest.raises(RenderError):
        render_mod._validate(pl, output)


def test_reject_label_mismatch_filename(roots):
    project_root, state_root, _output = roots
    pl = render_mod.build_plist(str(project_root), str(state_root))
    wrong_output = Path("/tmp/com.zcdeng.kss.something_else.plist")
    with pytest.raises(RenderError):
        render_mod._validate(pl, wrong_output)


# --------------------------------------------------------------------------- #
# 集成 #6：bridge 自省（不调 launchctl）+ plutil -lint smoke
# --------------------------------------------------------------------------- #


def _load_bridge():
    bridge_path = _REPO / "scripts" / "kss_app_bridge.py"
    spec = importlib.util.spec_from_file_location("kss_app_bridge_u6", bridge_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bridge_scheduled_job_visibility(roots, monkeypatch):
    """渲染 plist 放 bridge-glob 目的地 → _scheduled_job 现 title/category/log/schedule，
    不调 launchctl（patch 掉 _launchctl_status / _disabled_labels）。"""
    project_root, state_root, output = roots
    render_mod.render(str(project_root), str(state_root), output)

    bridge = _load_bridge()
    # 不触碰真实 launchctl：stub 运行态。
    monkeypatch.setattr(bridge, "_launchctl_status", lambda label, uid: {"loaded": False, "lastExit": None, "pid": None})
    monkeypatch.setattr(bridge, "_disabled_labels", lambda uid: set())

    job = bridge._scheduled_job(LABEL, output, uid=501, disabled=set())
    assert job["title"] == "分时收盘采集"
    assert job["category"] == "数据更新"
    assert job["schedule"] == "每天 15:05"
    assert job["script"] == "run_collect_intraday.sh"


def test_plutil_lint_smoke(roots):
    """命令级部署 smoke：渲染产物过 plutil -lint（macOS-only；缺则 skip）。"""
    if shutil.which("plutil") is None:
        pytest.skip("plutil 不可用（非 macOS）")
    project_root, state_root, output = roots
    render_mod.render(str(project_root), str(state_root), output)
    proc = subprocess.run(["plutil", "-lint", str(output)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# F1：collect_intraday 报 stale 但**不**被 kickstart
# --------------------------------------------------------------------------- #


def test_f1_collect_intraday_reported_but_not_kickstarted(roots, monkeypatch):
    """陈旧 collect_intraday 被 _kickstart_labels 跳过（NO_CATCHUP_LABELS），
    但 _scheduled_job 仍报其 staleness（可见性）。"""
    bridge = _load_bridge()
    assert LABEL in bridge.NO_CATCHUP_LABELS

    # plists 含 collect_intraday + 一个普通 label。
    fake_plists = {
        LABEL: Path("/fake/com.zcdeng.kss.collect_intraday.plist"),
        "com.zcdeng.kss.update_data_daily": Path("/fake/com.zcdeng.kss.update_data_daily.plist"),
    }
    monkeypatch.setattr(bridge, "_launchd_plists", lambda: fake_plists)
    monkeypatch.setattr(bridge, "_disabled_labels", lambda uid: set())
    # 两者都 enabled + stale。
    monkeypatch.setattr(
        bridge, "_scheduled_job",
        lambda label, path, uid, disabled: {"label": label, "title": label, "enabled": True, "stale": True},
    )
    kicked = []
    monkeypatch.setattr(bridge, "_run_launchctl", lambda args, timeout=15: (kicked.append(args) or (0, "", "")))

    res = bridge._kickstart_labels(list(fake_plists.keys()), require_stale=True)

    # collect_intraday 跳过、不被 kickstart；普通 label 被补跑。
    assert LABEL in res["skipped"]
    assert all(LABEL not in " ".join(args) for args in kicked), "collect_intraday 不得被 kickstart"
    assert any("update_data_daily" in " ".join(args) for args in kicked)


# --------------------------------------------------------------------------- #
# S1：wrapper 凭据纪律（静态文本 + 运行行为）
# --------------------------------------------------------------------------- #


def test_wrapper_text_has_no_env_grep_or_token_export():
    """wrapper **可执行行**不含 .env grep / export TUSHARE_TOKEN / token echo（S1）。

    注释行（# 开头）合法地**文档化**这些禁令，故只审可执行行。
    """
    text = _WRAPPER.read_text(encoding="utf-8")
    code_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines).lower()
    # 唯一允许引用的 env 是 KSS_STATE_ROOT；不得 grep .env、export/读 TUSHARE_TOKEN。
    assert "tushare_token" not in code, "wrapper 可执行行不得引用 TUSHARE_TOKEN"
    assert ".env" not in code, "wrapper 可执行行不得 grep .env"
    assert not re.search(r"grep.*token", code), "wrapper 不得 grep token"
    assert "export tushare_token" not in code
    # 唯一 export 的 env 应是 KSS_STATE_ROOT。
    exports = re.findall(r"export\s+([A-Z_]+)", "\n".join(code_lines))
    assert set(exports) <= {"KSS_STATE_ROOT"}, f"wrapper export 了非授权 env: {exports}"


def test_wrapper_run_emits_no_token_value(tmp_path, monkeypatch):
    """wrapper 以陈旧 ambient TUSHARE_TOKEN 跑 → 输出无任何 token 相关字符串（S1）。

    把 collect_intraday.py 与解释器替成无害 stub，只验 wrapper 自身不回显 token。
    """
    fake_token = "deadbeef" * 5  # 40-hex 陈旧 token

    # 构造可控的 PROJECT_ROOT：复制 wrapper、塞一个假 python 与假 collect 脚本。
    project = tmp_path / "KSS"
    (project / "scripts").mkdir(parents=True)
    (project / ".venv-desktop" / "bin").mkdir(parents=True)

    wrapper_src = _WRAPPER.read_text(encoding="utf-8")
    # 把硬编码 PROJECT_ROOT 指向我们的 tmp project（只改这一行，不动逻辑）。
    wrapper_src = wrapper_src.replace(
        'PROJECT_ROOT="/Users/zcdeng/projects/KSS"',
        f'PROJECT_ROOT="{project}"',
    )
    wrapper_dst = project / "scripts" / "run_collect_intraday.sh"
    wrapper_dst.write_text(wrapper_src, encoding="utf-8")
    wrapper_dst.chmod(0o755)

    # 假 python：原样打印收到的所有 env + args（用于断言 wrapper 没泄露 token）。
    fake_py = project / ".venv-desktop" / "bin" / "python"
    fake_py.write_text("#!/bin/bash\necho \"args: $@\"\n", encoding="utf-8")
    fake_py.chmod(0o755)
    (project / "scripts" / "collect_intraday.py").write_text("# stub\n", encoding="utf-8")

    state_root = tmp_path / "state"
    state_root.mkdir()

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "KSS_STATE_ROOT": str(state_root),
        "TUSHARE_TOKEN": fake_token,  # 陈旧 ambient token
    }
    proc = subprocess.run(
        ["bash", str(wrapper_dst), "--dry-run"],
        capture_output=True, text=True, env=env,
    )
    combined = proc.stdout + proc.stderr
    assert fake_token not in combined, "wrapper 输出泄露了 token 值"
    assert "length=" not in combined, "wrapper 不得 echo token 长度"


# --------------------------------------------------------------------------- #
# 模板：占位符存在 + 落 code root（tracked）+ 与渲染器 key 一致
# --------------------------------------------------------------------------- #


def test_template_has_placeholders():
    text = _TEMPLATE.read_text(encoding="utf-8")
    for marker in ("__WRAPPER__", "__PROJECT_ROOT__", "__STATE_ROOT__"):
        assert marker in text, f"模板缺占位符 {marker}"
    # TUSHARE_TOKEN 可在 <!-- 注释 --> 里被文档化为禁令，但绝不能作为实际 plist
    # <key>/<string> 出现。剥离 XML 注释后再断言。
    no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    assert "TUSHARE_TOKEN" not in no_comments, "模板 plist body 不得含 TUSHARE_TOKEN"
    assert "<key>KSS_STATE_ROOT</key>" in no_comments
