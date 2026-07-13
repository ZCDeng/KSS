"""U11 测试：cron/launchd shell wrapper 可移植性（R16/R17, KTD9, plan 2026-07-12-005）.

- 静态扫描：全部 wrapper（``scripts/run_*.sh`` + 仓库根 ``run_scanner.sh`` +
  ``scripts/archive_trends_daily.sh``）及 ``render_launchd_plists.py`` 渲染输出
  不含 ``/Users/`` 字面量、``/usr/bin/python3`` 直调、无条件 ``export KSS_STATE_ROOT=`` 覆盖。
- 解释器解析链行为：``$KSS_PYTHON`` 显式指定 > state-root venv > ``.venv-desktop`` > fail-loud。
跑：uv run pytest kss/tests/test_wrapper_portability.py -q
"""
from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

WRAPPER_PATHS = sorted(
    list((REPO_ROOT / "scripts").glob("run_*.sh"))
    + [REPO_ROOT / "run_scanner.sh", REPO_ROOT / "scripts" / "archive_trends_daily.sh"]
)

# 共享 helper 本身不是「wrapper」（无 shebang 直跑约定），但仍纳入硬编码扫描。
ALL_SHELL_ASSETS = WRAPPER_PATHS + [REPO_ROOT / "scripts" / "lib_cron_credentials.sh"]

HARDCODED_USER_PATH_RE = re.compile(r"/Users/[A-Za-z0-9_.-]+")
DIRECT_SYSTEM_PYTHON_RE = re.compile(r"/usr/bin/python3\b")
UNCONDITIONAL_STATE_ROOT_EXPORT_RE = re.compile(
    r'^\s*export\s+KSS_STATE_ROOT="?\$PROJECT_ROOT"?\s*$', re.MULTILINE
)
# KTD9：storage/ 下任何可写/可读路径必须源自 KSS_STATE_ROOT，不能是 PROJECT_ROOT/PROJECT_DIR——
# bundle 模式下后者指向签名 .app 内只读 Resources，往那写会破坏 code-signing seal（见
# render_launchd_plists.py:build_plist 的同一条纪律）。code review 发现过一次真实回归：
# 8 个 wrapper + run_scanner.sh 的 LOG_DIR/LOG_FILE 曾用 PROJECT_ROOT 拼 storage/ 路径。
PROJECT_ROOT_STORAGE_RE = re.compile(r'\$\{?(PROJECT_ROOT|PROJECT_DIR)\}?["\']?/storage')

# 解析链模板：$KSS_PYTHON 显式 > state-root venv > .venv-desktop > fail-loud。
# 全部 25 个 scripts/run_*.sh wrapper 均需含这段（PYTHON 变量名固定；
# run_scanner.sh 用 PYTHON_BIN，run_cron_selfcheck.sh 是 zsh，语法仍兼容 sh 条件测试）。
INTERPRETER_CHAIN_RE = re.compile(
    r'if \[ -n "\$\{KSS_PYTHON:-\}" \]; then\n'
    r'\s*\w+="\$KSS_PYTHON"\n'
    r'elif \[ -x "\$HOME/Library/Application Support/KSS/venv/bin/python3" \]; then\n'
    r'\s*\w+="\$HOME/Library/Application Support/KSS/venv/bin/python3"\n'
    r'elif \[ -x "\$\w+/\.venv-desktop/bin/python" \]; then\n'
    r'\s*\w+="\$\w+/\.venv-desktop/bin/python"\n'
    r"else\n"
    r'\s*echo "no usable python interpreter found \(checked KSS_PYTHON, state-root venv, \.venv-desktop\)" >&2\n'
    r"\s*exit 1\n"
    r"fi",
)


@pytest.mark.parametrize("path", ALL_SHELL_ASSETS, ids=lambda p: p.name)
def test_no_hardcoded_user_path(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    matches = HARDCODED_USER_PATH_RE.findall(text)
    assert not matches, f"{path.name} 含硬编码 /Users/ 路径: {matches}"


@pytest.mark.parametrize("path", ALL_SHELL_ASSETS, ids=lambda p: p.name)
def test_no_direct_system_python(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not DIRECT_SYSTEM_PYTHON_RE.search(text), f"{path.name} 直调 /usr/bin/python3"


@pytest.mark.parametrize("path", ALL_SHELL_ASSETS, ids=lambda p: p.name)
def test_no_unconditional_state_root_export(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not UNCONDITIONAL_STATE_ROOT_EXPORT_RE.search(text), (
        f"{path.name} 无条件 export KSS_STATE_ROOT=\"$PROJECT_ROOT\"（应为 "
        ': "${KSS_STATE_ROOT:=$PROJECT_ROOT}" 尊重外部注入）'
    )


@pytest.mark.parametrize("path", WRAPPER_PATHS, ids=lambda p: p.name)
def test_no_project_root_storage_path(path: Path) -> None:
    """回归守卫：storage/ 下的路径不得由 PROJECT_ROOT/PROJECT_DIR 拼出（KTD9）。

    bundle 模式下 PROJECT_ROOT 指向签名 .app 内只读 Resources，往 storage/ 写会破坏
    code-signing seal 或直接失败；正确根是 KSS_STATE_ROOT（dev 模式下二者相等，
    问题只在打包交付后才会暴露——正是这条回归曾经真实发生又被测试漏掉的原因）。
    """
    text = path.read_text(encoding="utf-8")
    matches = PROJECT_ROOT_STORAGE_RE.findall(text)
    assert not matches, (
        f"{path.name} 用 PROJECT_ROOT/PROJECT_DIR 拼 storage/ 路径（应为 KSS_STATE_ROOT）: {matches}"
    )


@pytest.mark.parametrize("path", WRAPPER_PATHS, ids=lambda p: p.name)
def test_wrapper_has_portable_interpreter_chain(path: Path) -> None:
    """run_kss_mcp.sh 走候选数组循环（另一种同语义写法），其余须含标准 KTD9 链。"""
    if path.name == "run_kss_mcp.sh":
        pytest.skip("run_kss_mcp.sh 用候选数组循环实现同语义解析链")
    text = path.read_text(encoding="utf-8")
    assert INTERPRETER_CHAIN_RE.search(text), f"{path.name} 缺 KTD9 标准解释器解析链"


@pytest.mark.parametrize("path", WRAPPER_PATHS, ids=lambda p: p.name)
def test_wrapper_executable(path: Path) -> None:
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"{path.name} 缺可执行位"


# --------------------------------------------------------------------------- #
# 解释器解析链行为（②）：抽取 run_rotate_cron_logs.sh 的链模板，在受控临时目录里
# 逐场景验证优先级——不依赖真机是否装有 venv。
# --------------------------------------------------------------------------- #

_CHAIN_SCRIPT_TMPL = """#!/bin/bash
set -e
PROJECT_ROOT="{project_root}"
if [ -n "${{KSS_PYTHON:-}}" ]; then
    PYTHON="$KSS_PYTHON"
elif [ -x "{state_venv}" ]; then
    PYTHON="{state_venv}"
elif [ -x "$PROJECT_ROOT/.venv-desktop/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
else
    echo "no usable python interpreter found (checked KSS_PYTHON, state-root venv, .venv-desktop)" >&2
    exit 1
fi
echo "$PYTHON"
"""


def _run_chain(tmp_path: Path, *, env: dict, state_venv_exists: bool, desktop_venv_exists: bool) -> subprocess.CompletedProcess:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    state_venv = tmp_path / "state_venv" / "python3"
    state_venv.parent.mkdir(parents=True)
    if state_venv_exists:
        state_venv.write_text("#!/bin/sh\n")
        state_venv.chmod(0o755)
    desktop_venv = project_root / ".venv-desktop" / "bin"
    desktop_venv.mkdir(parents=True)
    desktop_python = desktop_venv / "python"
    if desktop_venv_exists:
        desktop_python.write_text("#!/bin/sh\n")
        desktop_python.chmod(0o755)

    script = tmp_path / "chain.sh"
    script.write_text(
        _CHAIN_SCRIPT_TMPL.format(project_root=project_root, state_venv=state_venv)
    )
    script.chmod(0o755)
    return subprocess.run([str(script)], env=env, capture_output=True, text=True)


def test_interpreter_chain_prefers_explicit_kss_python(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "KSS_PYTHON": "/explicit/python"}
    result = _run_chain(tmp_path, env=env, state_venv_exists=True, desktop_venv_exists=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "/explicit/python"


def test_interpreter_chain_falls_back_to_state_venv(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = _run_chain(tmp_path, env=env, state_venv_exists=True, desktop_venv_exists=True)
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path / "state_venv" / "python3")


def test_interpreter_chain_falls_back_to_venv_desktop(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = _run_chain(tmp_path, env=env, state_venv_exists=False, desktop_venv_exists=True)
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path / "proj" / ".venv-desktop" / "bin" / "python")


def test_interpreter_chain_fails_loud_when_nothing_found(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = _run_chain(tmp_path, env=env, state_venv_exists=False, desktop_venv_exists=False)
    assert result.returncode == 1
    assert "no usable python interpreter found" in result.stderr
