"""Agent resource packaging guards for the signed desktop bundle.

U7：签名 Harness Node 树 + 三崩溃域失败关闭（脚本内容断言；本环境未跑完整 codesign）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "script" / "sign_and_build.sh"
_PREPARE = _REPO / "script" / "prepare_harness_node.sh"

sys.path.insert(0, str(_REPO / "scripts"))
import kss_sidecar as sidecar  # noqa: E402
import kss_app_bridge as bridge  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_harness_crash_domains():
    sidecar.reset_harness_crash_domains()
    yield
    sidecar.reset_harness_crash_domains()


def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def test_signed_package_includes_agent_skill_roots_for_bundle_mode() -> None:
    """Bundle-mode PROJECT_ROOT=Resources must expose repo-local agent skills."""
    script = _script_text()

    assert "for skills_root in .claude/skills .agents/skills; do" in script
    assert 'copy_resource_item "$skills_root" "$APP_RESOURCES/$(dirname "$skills_root")"' in script
    assert '"$APP_RESOURCES/.claude/skills"' in script
    assert '"$APP_RESOURCES/.agents/skills"' in script
    assert 'chmod -R a-w "$skills_root"' in script


def test_signed_package_excludes_cache_git_and_unsafe_state_roots() -> None:
    script = _script_text()

    for token in [
        "--exclude '.git/'",
        "--exclude '__pycache__/'",
        "--exclude '.pytest_cache/'",
        "--exclude '.ruff_cache/'",
        "--exclude '.cache/'",
        "--exclude 'cache/'",
        "--exclude 'caches/'",
        "--exclude '.omx/'",
        "--exclude '.codex/'",
        "--exclude 'state/'",
        "--exclude '.state/'",
        "--exclude 'logs/'",
    ]:
        assert token in script


def test_signed_package_does_not_copy_top_level_storage() -> None:
    script = _script_text()

    baseline_loop_start = script.index(
        "for item in scripts kss deploy pyproject.toml uv.lock backtest_etf_radar.py run_scanner.sh; do"
    )
    baseline_loop_end = script.index("done", baseline_loop_start)
    baseline_loop = script[baseline_loop_start:baseline_loop_end]

    assert " storage " not in f" {baseline_loop} "
    assert 'rm -rf "$APP_RESOURCES/storage"' in script


def test_signed_package_copies_harness_tree_and_codesigns_dsh_node() -> None:
    script = _script_text()
    prepare = _PREPARE.read_text(encoding="utf-8")

    assert _PREPARE.is_file()
    assert 'NODE_VERSION="22.19.0"' in prepare
    assert "c59006db713c770d6ec63ae16cb3edc11f49ee093b5c415d667bb4f436c6526d" in prepare
    assert "npm-cli.js" in prepare
    assert "ci --omit=dev --ignore-scripts" in prepare
    assert "--exclude 'node_modules/'" in prepare
    assert "kss-profile" in prepare and "kss-plugins" in prepare
    assert "@deepseek-ai/dsh/lib/bin.js" in prepare

    assert 'script/prepare_harness_node.sh' in script
    assert 'cp -R "$HARNESS_BUILD_ROOT/runtime" "$APP_RESOURCES/harness-runtime"' in script
    assert 'cp -R "$HARNESS_BUILD_ROOT/harness/kss-profile" "$APP_RESOURCES/harness/kss-profile"' in script
    assert 'cp -R "$HARNESS_BUILD_ROOT/harness/kss-plugins" "$APP_RESOURCES/harness/kss-plugins"' in script
    assert "prune_foreign_harness_natives" in prepare
    assert "! -name 'darwin-arm64'" in prepare
    assert 'ERROR: Harness tree contains unsupported native .node modules.' not in prepare
    assert 'ERROR: Harness tree contains unsupported native .node modules.' not in script
    assert 'list_harness_macho.py' in script
    assert '签名 Harness native:' in script
    assert (_REPO / 'script' / 'list_harness_macho.py').is_file()
    assert 'codesign --verify --strict --verbose=2 "$APP_RESOURCES/harness-runtime/bin/node"' in script
    assert '"$APP_RESOURCES/harness-runtime/bin/node"' in script
    assert "--entitlements \"$NODE_ENTITLEMENTS\"" in script
    assert "com.apple.security.cs.allow-jit" in (
        _REPO / "script" / "NodeHelper.entitlements"
    ).read_text(encoding="utf-8")
    assert 'harness/kss-profile/node_modules/@deepseek-ai/dsh/lib/bin.js' in script
    assert 'script/prune_signed_resources.sh' in script
    assert "-name node_modules" in (_REPO / 'script' / 'prune_signed_resources.sh').read_text(encoding='utf-8')
    assert "ERROR: prune removed OpenTelemetry source dirs" in script




def test_signed_resource_prune_keeps_otel_logs_dir(tmp_path: Path) -> None:
    """Packaging must not delete node_modules dirs named logs/ or state/."""
    helper = _REPO / "script" / "prune_signed_resources.sh"
    resources = tmp_path / "Resources"
    otel_logs = (
        resources
        / "harness"
        / "kss-profile"
        / "node_modules"
        / "@opentelemetry"
        / "otlp-transformer"
        / "build"
        / "src"
        / "logs"
    )
    otel_state = (
        resources
        / "harness"
        / "kss-profile"
        / "node_modules"
        / "@opentelemetry"
        / "sdk-metrics"
        / "build"
        / "src"
        / "state"
    )
    stray_logs = resources / "scripts" / "logs"
    otel_logs.mkdir(parents=True)
    (otel_logs / "protobuf.js").write_text("keep\n", encoding="utf-8")
    otel_state.mkdir(parents=True)
    (otel_state / "MeterProvider.js").write_text("keep\n", encoding="utf-8")
    stray_logs.mkdir(parents=True)
    (stray_logs / "sidecar.log").write_text("drop\n", encoding="utf-8")

    subprocess.check_call(["bash", str(helper), str(resources)])

    assert (otel_logs / "protobuf.js").is_file()
    assert (otel_state / "MeterProvider.js").is_file()
    assert not stray_logs.exists()


def test_harness_node_modules_stay_out_of_git() -> None:
    gitignore = (_REPO / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore
    assert (_REPO / "harness" / "kss-profile" / "package-lock.json").is_file()
    assert (_REPO / "harness" / "kss-plugins" / "package.json").is_file()


def test_node_dead_python_alive_blocks_live_dispatch_and_pending_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c})
    sidecar.grant_harness_write("pending-when-node-dies", command="run")
    sidecar.mark_harness_kernel_dead()
    assert sidecar._HARNESS_GRANTS == {}
    with pytest.raises(ValueError, match="kernel is not available"):
        sidecar.grant_harness_write("new-after-death", command="run")
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="pending-when-node-dies",
    )
    assert dispatched == []
    assert out.get("error") == "harness_unavailable"
    assert out.get("ok") is not True


def test_python_death_during_granted_call_id_is_not_silent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grant 表只在进程内存。Python 死掉 = 表消失；同一 callId 无新 allow 不得 dispatch。"""
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c})
    sidecar.grant_harness_write("mid-crash", command="run")
    sidecar.clear_harness_grants()
    replay = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="mid-crash",
    )
    assert dispatched == []
    assert replay.get("error") == "not_allowed"
    assert replay.get("ok") is not True

    sidecar.grant_harness_write("once-only", command="run")
    first = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="once-only",
    )
    assert first.get("ok") is True
    second = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="once-only",
    )
    assert dispatched == ["run"]
    assert second.get("error") == "not_allowed"


def test_answerer_gone_is_unavailable_and_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c})
    sidecar.grant_harness_write("no-chrome", command="run")
    sidecar.mark_harness_answerer_dead()
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="no-chrome",
    )
    assert dispatched == []
    assert out.get("error") == "harness_unavailable"
    assert sidecar._HARNESS_GRANTS == {}


def test_research_grant_survives_desktop_answerer_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", True)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c})
    sidecar.grant_harness_write("research-live", command="run", surface="research")
    sidecar.mark_harness_answerer_dead()
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="research-live",
    )
    assert out.get("ok") is True
    assert dispatched == ["run"]


def test_live_kill_switch_still_blocks_granted_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(sidecar, "_CHAT_LOOP_LIVE", False)
    monkeypatch.setattr(bridge, "dispatch", lambda c, a: dispatched.append(c) or {"ran": c})
    sidecar.grant_harness_write("kill-switch", command="run")
    out = sidecar.execute_harness_tool(
        name="run_task",
        args={"task": "update-cs-data"},
        call_id="kill-switch",
    )
    assert dispatched == []
    assert out.get("error") == "not_live"



def test_sign_and_build_stops_detached_sidecar() -> None:
    script = _script_text()
    assert 'pkill -x "$APP_NAME"' in script
    assert 'pkill -f "kss_sidecar.py"' in script
    assert 'pkill -f "kss_harness_host.mjs"' in script
    assert "kss-sidecar.pid" in script



def test_sidecar_kernel_boot_log_does_not_use_undefined_driver() -> None:
    """Boot logging must use the kwargs driver; a bare `driver` NameError marks Harness dead."""
    text = (_REPO / "scripts" / "kss_sidecar.py").read_text(encoding="utf-8")
    assert 'logger.info("[harness] Node kernel started driver=%s", driver)' not in text
    assert 'logger.info("[harness] Node kernel started driver=%s", kwargs["driver"])' in text
    serve = text[text.index("async def _serve()"):]
    boot = serve.split("async with server:", 1)[0]
    assert "mark_harness_kernel_dead()" in boot
    assert "kernel.alive" in boot
