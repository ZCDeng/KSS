# -*- coding: utf-8 -*-
"""R3-U1: 下游任务三态 gate + 超时守护（plan 2026-07-14-001, KTD2/KTD3）。"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load("check_pipeline_gate", "scripts/check_pipeline_gate.py")


def _write_cs_data(root: Path, symbol: str, dates: list[str]) -> None:
    code = symbol.split(".")[0]
    lines = ["ts_code,trade_date,open,close"]
    lines += [f"{symbol},{d},1.0,1.0" for d in dates]
    (root / f"cs_data_{code}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


SENTINELS = ("688017.SH", "159915.SZ")


def _write_all(root: Path, dates: list[str]) -> None:
    for sym in SENTINELS:
        _write_cs_data(root, sym, dates)


def _marker(root: Path, task: str, day: str, content: str | None = None) -> Path:
    d = root / "storage" / "pipeline_markers"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{task}_{day}.json"
    p.write_text(content if content is not None
                 else json.dumps({"task": task, "target_day": day}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 三态判定
# ---------------------------------------------------------------------------

def test_fresh_data_missing_artifact_runs(tmp_path):
    """Covers AE1（半边）：数据含目标日、产物缺失 → RUN。"""
    _write_all(tmp_path, ["2026-07-13", "2026-07-14"])
    r = gate.run_gate("picks", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.RUN
    assert r.target_day == "2026-07-14"


def test_intact_marker_noops(tmp_path):
    """数据含目标日、产物完整已在 → NOOP（兜底档静默让路）。"""
    _write_all(tmp_path, ["2026-07-14"])
    _marker(tmp_path, "picks", "2026-07-14")
    r = gate.run_gate("picks", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.NOOP


def test_partial_pool_write_is_stale(tmp_path):
    """Covers AE1：部分池写入（07-14 事故形态 85/115）→ STALE_DATA 响亮失败。"""
    _write_cs_data(tmp_path, "688017.SH", ["2026-07-13", "2026-07-14"])
    _write_cs_data(tmp_path, "159915.SZ", ["2026-07-13"])   # 滞后 sentinel
    r = gate.run_gate("picks", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.STALE_DATA
    assert "159915.SZ" in r.reason


def test_holiday_noops_instead_of_false_alarm(tmp_path):
    """节假日：sentinel max 停在上一交易日且产物同日已在 → NOOP 而非误报。"""
    _write_all(tmp_path, ["2026-07-14"])          # 假日无新行，停在上一交易日
    _marker(tmp_path, "review", "2026-07-14")     # 上一交易日产物已生成
    r = gate.run_gate("review", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.NOOP


def test_corrupt_marker_treated_as_missing(tmp_path):
    """产物标记残缺（不可解析）→ 视为缺失 → RUN（半途 kill 不骗过判定）。"""
    _write_all(tmp_path, ["2026-07-14"])
    _marker(tmp_path, "picks", "2026-07-14", content="{truncated")
    r = gate.run_gate("picks", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.RUN


def test_all_sentinels_missing_is_stale(tmp_path):
    """数据文件全部缺失 → STALE_DATA 而非崩溃。"""
    r = gate.run_gate("picks", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.STALE_DATA
    assert r.target_day is None


def test_mark_done_writes_marker_and_gate_noops_after(tmp_path):
    """mark-done 落标记后同日 gate 变 NOOP（闭环）。"""
    _write_all(tmp_path, ["2026-07-14"])
    target = gate.write_marker("mi_signal", tmp_path, tmp_path, SENTINELS)
    assert target == "2026-07-14"
    r = gate.run_gate("mi_signal", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.NOOP


def test_cli_exit_codes(tmp_path):
    """CLI 退出码约定：0=RUN / 3=NOOP / 4=STALE。"""
    _write_all(tmp_path, ["2026-07-14"])
    base = [sys.executable, str(PROJECT_ROOT / "scripts" / "check_pipeline_gate.py"),
            "--task", "picks", "--data-root", str(tmp_path), "--state-root", str(tmp_path),
            "--sentinels", ",".join(SENTINELS)]
    assert subprocess.run(base, capture_output=True).returncode == 0
    _marker(tmp_path, "picks", "2026-07-14")
    assert subprocess.run(base, capture_output=True).returncode == 3
    _write_cs_data(tmp_path, "159915.SZ", ["2026-07-13"])
    assert subprocess.run(base, capture_output=True).returncode == 4


def test_cli_split_roots_stale_noop_vs_fresh_run(tmp_path):
    """CLI 必须读传入的 data-root/state-root，不得偷偷锚仓库 cs_data.

    停更树 + 完整 marker → NOOP；另一棵新鲜树无 marker → RUN。
    """
    stale = tmp_path / "stale"
    fresh = tmp_path / "fresh"
    stale.mkdir()
    fresh.mkdir()
    _write_all(stale, ["2026-08-14"])
    _marker(stale, "signal_cards", "2026-08-14")
    _write_all(fresh, ["2026-09-01"])
    script = str(PROJECT_ROOT / "scripts" / "check_pipeline_gate.py")

    def _run(root: Path) -> int:
        return subprocess.run(
            [sys.executable, script, "--task", "signal_cards",
             "--data-root", str(root), "--state-root", str(root),
             "--sentinels", ",".join(SENTINELS)],
            capture_output=True,
        ).returncode

    assert _run(stale) == 3
    assert _run(fresh) == 0


def test_write_marker_target_day_match(tmp_path):
    _write_all(tmp_path, ["2026-07-14"])
    target = gate.write_marker(
        "signal_cards", tmp_path, tmp_path, SENTINELS, target_day="2026-07-14",
    )
    assert target == "2026-07-14"
    assert (tmp_path / "storage" / "pipeline_markers" / "signal_cards_2026-07-14.json").exists()


def test_write_marker_target_day_mismatch_writes_nothing(tmp_path):
    _write_all(tmp_path, ["2026-07-14"])
    target = gate.write_marker(
        "signal_cards", tmp_path, tmp_path, SENTINELS, target_day="2026-07-13",
    )
    assert target is None
    marker_dir = tmp_path / "storage" / "pipeline_markers"
    assert not marker_dir.exists() or not any(marker_dir.iterdir())


def test_cli_mark_done_target_day_mismatch(tmp_path):
    _write_all(tmp_path, ["2026-07-14"])
    p = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "check_pipeline_gate.py"),
         "--task", "signal_cards", "--action", "mark-done",
         "--data-root", str(tmp_path), "--state-root", str(tmp_path),
         "--sentinels", ",".join(SENTINELS),
         "--target-day", "2026-07-13"],
        capture_output=True,
    )
    assert p.returncode == 1
    marker_dir = tmp_path / "storage" / "pipeline_markers"
    assert not marker_dir.exists() or not any(marker_dir.iterdir())


def test_chain_wrappers_anchor_cs_data_to_state_root():
    """bundle-mode：EOD 写 $KSS_STATE_ROOT/cs_data_*.csv，gate 必须读同一根。

    读 PROJECT_ROOT 会把目标日钉死在仓库停更副本上，天天 NOOP（2026-08-14 事故）。
    """
    chain = (PROJECT_ROOT / "scripts" / "lib_cron_chain.sh").read_text(encoding="utf-8")
    assert '--data-root "$KSS_STATE_ROOT"' in chain
    assert '--data-root "$PROJECT_ROOT"' not in chain
    picks = (PROJECT_ROOT / "scripts" / "run_formal_daily_picks.sh").read_text(encoding="utf-8")
    assert '--data-root "$KSS_STATE_ROOT"' in picks
    assert '--data-root "$PROJECT_ROOT"' not in picks
    cards = (PROJECT_ROOT / "scripts" / "run_signal_cards_daily.sh").read_text(encoding="utf-8")
    assert "export KSS_STATE_ROOT" in cards
    assert '--date "$TARGET_DAY"' in cards
    assert 'kss_mark_done signal_cards "$TARGET_DAY"' in cards
    bridge = (PROJECT_ROOT / "scripts" / "kss_app_bridge.py").read_text(encoding="utf-8")
    assert "read_latest_trade_date(STATE_ROOT" in bridge
    assert "read_latest_trade_date(PROJECT_ROOT" not in bridge



def test_split_brain_data_root_lags_state_root_is_stale(tmp_path):
    """仓库根停更、state-root 已前进：即使旧日 marker 完整，也不得 NOOP。"""
    data = tmp_path / "repo"
    state = tmp_path / "state"
    data.mkdir()
    state.mkdir()
    _write_all(data, ["2026-08-14"])
    _write_all(state, ["2026-08-14", "2026-09-01"])
    _marker(state, "signal_cards", "2026-08-14")
    r = gate.run_gate("signal_cards", data, state, SENTINELS)
    assert r.decision is gate.GateDecision.STALE_DATA
    assert "split-brain" in r.reason
    assert r.target_day == "2026-08-14"


def test_same_root_intact_marker_still_noops(tmp_path):
    """双根合一且标记完整 → 仍是合法 NOOP（兜底档让路）。"""
    _write_all(tmp_path, ["2026-09-01"])
    _marker(tmp_path, "signal_cards", "2026-09-01")
    r = gate.run_gate("signal_cards", tmp_path, tmp_path, SENTINELS)
    assert r.decision is gate.GateDecision.NOOP

# ---------------------------------------------------------------------------
# 超时守护（KTD3）
# ---------------------------------------------------------------------------

GUARD = str(PROJECT_ROOT / "scripts" / "run_with_timeout.py")


def test_timeout_guard_passes_through_exit_code():
    p = subprocess.run([sys.executable, GUARD, "10", "--", sys.executable, "-c", "raise SystemExit(7)"],
                       capture_output=True)
    assert p.returncode == 7


def test_timeout_guard_kills_process_group_on_timeout():
    """子进程组超限被 killpg、退出码 124、stderr 含超时标记。"""
    start = time.monotonic()
    p = subprocess.run(
        [sys.executable, GUARD, "1", "--", sys.executable, "-c",
         "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); time.sleep(60)"],
        capture_output=True, timeout=30)
    elapsed = time.monotonic() - start
    assert p.returncode == 124
    assert b"timeout-guard" in p.stderr
    assert elapsed < 25   # 整组被杀，不等 60s


def test_timeout_guard_usage_error():
    p = subprocess.run([sys.executable, GUARD, "abc", "--", "true"], capture_output=True)
    assert p.returncode == 2
