"""U10 单测：sidecar 版本指纹计算与版本命令。

覆盖：
- dev 模式（git 可用）：返回 git describe 输出，非 bundle 前缀。
- bundle 模式（无 .git 但 VERSION + 关键文件存在）：返回 bundle:<hash>。
- 文件缺失/极端 fallback：返回 "unknown"，不抛异常。
- dispatch 中 "version" 命令返回包含 version 字段的字典。
- 真实 sidecar 启动后写入 version 文件，SIGHUP 重载后覆盖旧值。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

import kss_app_bridge as bridge  # noqa: E402


def test_dev_mode_returns_git_describe() -> None:
    """真实仓库下 git describe 可用，应返回类似 desktop-v1.5.0-172-g1e4b818-dirty。"""
    fp = bridge._sidecar_version_fingerprint()
    assert not fp.startswith("bundle:")
    assert fp != "unknown"
    # 至少包含当前短 hash 或 tag 前缀
    assert "-g" in fp or "v" in fp


def test_bundle_mode_returns_hash_prefix(tmp_path: Path) -> None:
    """无 .git 目录时 fallback 到 VERSION + 关键文件 hash。"""
    root = tmp_path / "fake_bundle"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "VERSION").write_text("0.2.0")
    (scripts / "kss_app_bridge.py").write_text("bridge")
    (scripts / "kss_sidecar.py").write_text("sidecar")

    fp = bridge._sidecar_version_fingerprint(project_root=root)
    assert fp.startswith("bundle:")
    # 16 位 hex
    assert len(fp) == len("bundle:") + 16


def test_bundle_mode_changes_when_content_changes(tmp_path: Path) -> None:
    """关键文件内容改变，指纹必须改变。"""
    root = tmp_path / "fake_bundle"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "VERSION").write_text("0.2.0")
    (scripts / "kss_app_bridge.py").write_text("bridge")
    (scripts / "kss_sidecar.py").write_text("sidecar")

    fp1 = bridge._sidecar_version_fingerprint(project_root=root)
    (scripts / "kss_sidecar.py").write_text("sidecar_v2")
    fp2 = bridge._sidecar_version_fingerprint(project_root=root)
    assert fp1 != fp2


def test_extreme_fallback_unknown(tmp_path: Path) -> None:
    """目录不存在任何文件时返回 unknown。"""
    root = tmp_path / "empty"
    root.mkdir()
    fp = bridge._sidecar_version_fingerprint(project_root=root)
    assert fp == "unknown"


def test_dispatch_version_command() -> None:
    """dispatch version 命令返回包含 version 字段的字典。"""
    payload = bridge.dispatch("version", [])
    assert isinstance(payload, dict)
    assert "version" in payload
    assert payload["version"] != "unknown"
    assert not str(payload["version"]).startswith("bundle:")
    # 真实仓库下应该也是 git describe
    assert "-g" in payload["version"] or "v" in payload["version"]


def test_sidecar_writes_version_file(tmp_path: Path) -> None:
    """启动真实 sidecar 后，run/kss-sidecar.version 文件写入并与 Python 指纹一致。

    注意：Unix socket 路径长度受限（~104B），因此使用 /tmp 下短目录而不是 pytest
    生成的长 tmp_path。
    """
    # 使用固定短路径，避免 AF_UNIX path too long
    state_root = Path("/tmp/kss_sv_test_state")
    run_dir = state_root / "run"
    if state_root.exists():
        import shutil
        shutil.rmtree(state_root)
    run_dir.mkdir(parents=True)

    env = os.environ.copy()
    env["KSS_PROJECT_ROOT"] = str(_REPO)
    env["KSS_STATE_ROOT"] = str(state_root)
    env["KSS_PYTHON"] = sys.executable

    # 捕获 stderr 方便调试启动失败
    stderr_path = tmp_path / "sidecar.stderr"
    stderr_f = stderr_path.open("w")
    proc = subprocess.Popen(
        [sys.executable, str(_REPO / "scripts" / "kss_sidecar.py")],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=stderr_f,
    )
    try:
        version_path = run_dir / "kss-sidecar.version"
        # 等待 sidecar 写 socket / version 文件，最多 5s
        for _ in range(50):
            if version_path.exists():
                break
            time.sleep(0.1)
        stderr_f.close()
        if not version_path.exists():
            stderr = stderr_path.read_text() if stderr_path.exists() else ""
            pytest.fail(f"sidecar 未在 5s 内写入 version 文件。stderr:\n{stderr}")
        written = version_path.read_text().strip()
        expected = bridge._sidecar_version_fingerprint()
        assert written == expected, f"version 文件内容不一致: {written!r} != {expected!r}"

        # 模拟旧 sidecar 场景：把 version 文件改成旧值，SIGHUP 后应被覆盖
        version_path.write_text("old-stale")
        os.kill(proc.pid, signal.SIGHUP)
        # 等待 re-exec 后的新 sidecar 重写 version 文件
        time.sleep(0.3)
        for _ in range(50):
            if version_path.exists() and version_path.read_text().strip() != "old-stale":
                break
            time.sleep(0.1)
        assert version_path.read_text().strip() == expected
    finally:
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil
        shutil.rmtree(state_root, ignore_errors=True)
