"""Agent resource packaging guards for the signed desktop bundle."""

from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "script" / "sign_and_build.sh"


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
