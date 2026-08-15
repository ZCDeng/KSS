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
    # plan 2026-07-14-001 / KTD6：分钟线源切 Longbridge，args 带 --provider auto。
    assert prog[1:] == ["--provider", "auto"]
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


def _code_lines(path: Path) -> list[str]:
    """去掉空行与整行注释——只留可执行行（注释里写 `export TOKEN=` 当反例不算违规）。"""
    return [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


# `export NAME=...`（赋值形）大小写通吃：原断言的 `[A-Z_]+` 看不见 `export no_proxy=`，
# 也看不见任何小写/混合大小写的凭据名——盲区 1。
_EXPORT_ASSIGN_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)
# `export NAME`（裸导出形，无 `=`）：只是把已存在的变量标记为导出，本身不引入值。
_EXPORT_BARE_RE = re.compile(
    r"^\s*export\s+((?:[A-Za-z_][A-Za-z0-9_]*[ \t]*)+)$", re.MULTILINE
)
_LIB = _REPO / "scripts" / "lib_cron_credentials.sh"


def _lib_sourcing_wrappers() -> list[Path]:
    """守护范围 = 已迁到 kss_load_credential 的 wrapper。

    这条边界是**结构性**的、不是人工豁免名单：wrapper 一旦 source 了
    lib_cron_credentials.sh，就等于声明「我的凭据走 Keychain 优先链」，此后再手搓
    `export <凭据名>=<值>` 就是绕过自己刚声明的纪律。尚未迁移的 wrapper
    （run_scanner.sh / run_formal_daily_*.sh 等仍在裸 grep .env）不在此列——
    它们该做的是迁移，不是被这条断言判死后加豁免。
    """
    return sorted(
        p
        for p in (_REPO / "scripts").glob("run_*.sh")
        if "lib_cron_credentials.sh" in p.read_text(encoding="utf-8")
    )


_GUARDED = _lib_sourcing_wrappers()


def test_guard_covers_the_wrappers_that_claim_the_discipline():
    """守护范围非空，且确实含两个 Tushare 日更 wrapper（防 glob 悄悄扫空）。"""
    names = {p.name for p in _GUARDED}
    assert len(names) >= 10, f"守护范围异常收缩，只剩 {sorted(names)}"
    assert {"run_update_data_daily.sh", "run_update_macro_daily.sh"} <= names


def test_wrapper_text_has_no_env_grep_or_token_export():
    """collect_intraday wrapper **可执行行**不含 .env grep / token 引用（S1）。

    这条是 collect_intraday 专属的更严口径：它的凭据全从 Keychain 取，连 .env
    都不该碰（对比 run_update_data_daily.sh 合法地把 $KSS_ENV 传给 kss_load_credential）。
    """
    code_lines = _code_lines(_WRAPPER)
    code = "\n".join(code_lines).lower()
    assert "tushare_token" not in code, "wrapper 可执行行不得引用 TUSHARE_TOKEN"
    assert ".env" not in code, "wrapper 可执行行不得 grep .env"
    assert not re.search(r"grep.*token", code), "wrapper 不得 grep token"
    assert "export tushare_token" not in code
    joined = "\n".join(code_lines)
    exports = set(_EXPORT_ASSIGN_RE.findall(joined))
    for group in _EXPORT_BARE_RE.findall(joined):
        exports.update(group.split())
    # no_proxy/NO_PROXY 是代理兜底（5437d23f 引入），非凭据，显式授权。
    allowed = {"KSS_STATE_ROOT", "no_proxy", "NO_PROXY"}
    assert exports <= allowed, f"wrapper export 了非授权 env: {sorted(exports - allowed)}"


@pytest.mark.parametrize("path", _GUARDED, ids=lambda p: p.name)
def test_credential_shaped_env_only_enters_via_kss_load_credential(path: Path):
    """凭据形态的名字不得由 wrapper 正文直接赋值导出（正面形态断言，复用 S3 常量）。

    盲区 1：原断言的 `[A-Z_]+` 只匹配大写，且只扫 collect_intraday 一个文件；
    run_update_data_daily.sh 的 `export TUSHARE_TOKEN=$(cat "$HOME/.tushare/token")`
    从来没被任何测试看见过。改用 :data:`CREDENTIAL_KEY_RE` 判名字形态：凭据可以进
    env，但只能经 kss_load_credential（Keychain 优先）进，不能在 wrapper 里手搓。

    裸 `export TUSHARE_TOKEN`（无 `=`）不算违规——它不引入值，只标记已有变量导出。
    """
    from kss.security import redaction

    assigned = _EXPORT_ASSIGN_RE.findall("\n".join(_code_lines(path)))
    offenders = [n for n in assigned if redaction.CREDENTIAL_KEY_RE.search(n)]
    assert not offenders, (
        f"{path.name} 在 wrapper 正文里直接 export 了凭据形态变量 {offenders}；"
        f"应改走 kss_load_credential <KEY> \"$KSS_ENV\"（Keychain 优先、.env 回落）"
    )


def test_credential_lib_exports_only_via_parameterized_indirection():
    """扫 sourced lib 本身——盲区 2：真凭据进 env 的实际入口在这儿，不在 wrapper 正文。

    LONGBRIDGE_* / TUSHARE_TOKEN 是被 lib_cron_credentials.sh 第 28 行那句
    `export "${key?}=${val}"` 送进 env 的。原断言只 read wrapper 文本，对这句完全
    盲视。这里正面钉死：lib 的**唯一** export 必须是那句参数化间接导出——任何人往
    lib 里加一句写死凭据名的 export（绕开 Keychain 链），这条立刻红。
    """
    exports = [ln.strip() for ln in _code_lines(_LIB) if re.match(r"^\s*export\b", ln)]
    assert exports == ['export "${key?}=${val}"'], (
        f"lib_cron_credentials.sh 的 export 语句集合变了: {exports}；"
        "凭据只能经 kss_load_credential 的参数化间接导出进 env"
    )
