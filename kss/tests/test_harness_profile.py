"""U1：KSS DeepSeek Harness profile 的 dump-config 金样。

跑：uv run pytest kss/tests/test_harness_profile.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = REPO_ROOT / "harness" / "kss-profile"
PLUGINS_DIR = REPO_ROOT / "harness" / "kss-plugins"
PINNED_SHA_HEX_LEN = 40


def _profile_manifest() -> dict:
    path = PROFILE_DIR / "package.json"
    assert path.is_file(), f"缺少 KSS profile 清单: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_wrapper() -> Path:
    wrapper = PROFILE_DIR / "dump-config.mjs"
    dsh = PROFILE_DIR / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if not wrapper.is_file():
        raise FileNotFoundError(f"缺少 dump-config 包装: {wrapper}")
    if not dsh.is_file():
        raise FileNotFoundError(
            "未找到 vendored dsh 入口；请在 harness/kss-profile 执行 npm ci"
        )
    return wrapper


def _dump_config(
    tmp_path: Path,
    *,
    extra_args: list[str] | None = None,
    overlay: str | None = None,
) -> subprocess.CompletedProcess[str]:
    dsh_home = tmp_path / "dsh-home"
    profiles = dsh_home / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "kss").symlink_to(PROFILE_DIR.resolve())

    argv: list[str] = ["node", str(_dump_wrapper()), "--profile", "kss"]
    if overlay is not None:
        overlay_path = tmp_path / "overlay.cordis.yml"
        overlay_path.write_text(overlay, encoding="utf-8")
        argv.extend(["--patch", str(overlay_path)])
    argv.append("--dump-config")
    if extra_args:
        argv.extend(extra_args)

    env = os.environ.copy()
    env["DSH_HOME"] = str(dsh_home)
    return subprocess.run(
        argv,
        cwd=str(PROFILE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_kss_plugins_skeleton_exists() -> None:
    assert (PLUGINS_DIR / "package.json").is_file()
    assert (PLUGINS_DIR / "README.md").is_file()


def test_profile_pins_real_upstream_commit() -> None:
    manifest = _profile_manifest()
    pin = manifest.get("kssHarness", {}).get("upstreamCommit")
    assert isinstance(pin, str) and len(pin) == PINNED_SHA_HEX_LEN
    assert all(c in "0123456789abcdef" for c in pin)
    bundles = manifest.get("dsh", {}).get("profile", {}).get("bundles", [])
    assert "@deepseek-ai/dsh-base" in bundles
    assert not any("dsh-web-app" in str(item) for item in bundles)


def test_dump_config_excludes_web_app_and_includes_kss_insert(
    tmp_path: Path,
) -> None:
    result = _dump_config(tmp_path)
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, combined
    assert "dsh-web-app" not in combined
    assert "id: kss" in result.stdout
    assert "apiKeyEnv: OPENAI_API_KEY" in result.stdout
    assert "apiKeyEnv: DEEPSEEK_API_KEY" in result.stdout
    assert "sk-" not in result.stdout


def test_missing_patch_target_id_fails_loudly(tmp_path: Path) -> None:
    overlay = """\
- id: kss-missing-patch-target-id
  config:
    phantom: true
"""
    result = _dump_config(tmp_path, overlay=overlay)
    combined = f"{result.stdout}\n{result.stderr}"
    assert "kss-missing-patch-target-id" in combined
    assert result.returncode != 0, "缺 patch 目标 id 必须失败响亮，不能静默成功"
