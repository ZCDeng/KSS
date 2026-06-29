"""U3 单测：清单 → launchd 幂等对账同步器（plan 2026-06-23-001）.

覆盖 plan U3 test scenarios：happy / 漂移(stale) / 变化(update) / 幂等 / dry-run / 金标闸。

铁律（IMPORTANT CONSTRAINTS）：
- **绝不**调真 ``launchctl``、**绝不**写真 ``~/Library/LaunchAgents``——全程注入 FakeRunner +
  tmp_path。
- 纯 diff 核（compute_plan）直接喂构造好的 target/installed dict，零盘零进程。
- apply 序列断言走 FakeRunner.calls（launchctl）+ FakeRunner.writes/removes（文件系统）。
"""
from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest

from kss.config.cron_manifest import load_manifest

_REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    path = _REPO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# render_launchd_plists 必须先于 sync_launchd 注册（后者 import 前者）。
render_mod = _load("render_launchd_plists", "scripts/render_launchd_plists.py")
sync_mod = _load("sync_launchd", "scripts/sync_launchd.py")

SyncPlan = sync_mod.SyncPlan
RenderError = render_mod.RenderError
_LABEL_PREFIX = sync_mod._LABEL_PREFIX


# --------------------------------------------------------------------------- #
# 注入替身：launchctl + 文件系统全部记录，零真实副作用
# --------------------------------------------------------------------------- #
class FakeRunner:
    """SyncRunner 的测试替身：launchctl 调用与 fs 改动只记录、不执行。"""

    def __init__(self, *, uid: int = 501, installed: dict[str, bytes] | None = None):
        self.uid = uid
        self._installed = dict(installed or {})
        self.calls: list[list[str]] = []  # launchctl 参数序列
        self.writes: list[tuple[str, bytes]] = []  # (path, data)
        self.removes: list[str] = []  # path

    def launchctl(self, args: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(args))
        return 0, "", ""

    def list_installed(self, agents_dir: Path) -> dict[str, bytes]:
        return dict(self._installed)

    def write(self, path: Path, data: bytes) -> None:
        self.writes.append((str(path), data))

    def remove(self, path: Path) -> None:
        self.removes.append(str(path))


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    p = tmp_path / "code"
    p.mkdir()
    return p


def _plist_bytes(project_root: Path, suffix: str) -> bytes:
    """用 U2 渲染器构造某任务的 plist bytes（已装副本模拟）。"""
    job = load_manifest().job(suffix)
    assert job is not None
    pl = render_mod.build_plist(str(project_root), job)
    return render_mod._to_xml(pl)


def _target(project_root: Path, suffixes: list[str]) -> dict[str, dict]:
    """构造目标态 {label: plist_dict}（不写盘）。"""
    out: dict[str, dict] = {}
    for s in suffixes:
        job = load_manifest().job(s)
        assert job is not None
        out[f"{_LABEL_PREFIX}{s}"] = render_mod.build_plist(str(project_root), job)
    return out


# --------------------------------------------------------------------------- #
# 纯 diff 核：四态
# --------------------------------------------------------------------------- #
def test_compute_plan_empty_agents_all_install(project_root):
    """happy：空 LaunchAgents + 清单基线 → install = N，stale = 0。"""
    suffixes = [j.suffix for j in load_manifest().jobs if j.enabled]
    target = _target(project_root, suffixes)
    plan = sync_mod.compute_plan(target, installed={})
    assert set(plan.install) == set(target)
    assert plan.update == ()
    assert plan.stale == ()
    assert len(plan.install) == len(suffixes)


def test_compute_plan_stale_label_not_in_manifest(project_root):
    """漂移：已装一个不在清单的 label → 进 stale 集。"""
    suffixes = ["scanner", "selfcheck"]
    target = _target(project_root, suffixes)
    installed = {
        f"{_LABEL_PREFIX}scanner": _plist_bytes(project_root, "scanner"),
        f"{_LABEL_PREFIX}selfcheck": _plist_bytes(project_root, "selfcheck"),
        f"{_LABEL_PREFIX}ghost_job": _plist_bytes(project_root, "scanner"),  # 内容借用
    }
    plan = sync_mod.compute_plan(target, installed)
    assert plan.stale == (f"{_LABEL_PREFIX}ghost_job",)
    assert plan.install == ()
    assert plan.update == ()


def test_compute_plan_update_on_schedule_change(project_root):
    """变化：已装副本 schedule 与目标不同 → 进 update 集。"""
    suffix = "scanner"
    target = _target(project_root, [suffix])
    label = f"{_LABEL_PREFIX}{suffix}"
    # 已装副本：把 schedule 篡改成不同时刻 → 语义指纹偏离。
    installed_pl = plistlib.loads(_plist_bytes(project_root, suffix))
    sci = installed_pl["StartCalendarInterval"]
    entries = sci if isinstance(sci, list) else [sci]
    for e in entries:
        e["Hour"] = (int(e.get("Hour", 0)) + 3) % 24
    installed = {label: plistlib.dumps(installed_pl)}
    plan = sync_mod.compute_plan(target, installed)
    assert plan.update == (label,)
    assert plan.install == ()
    assert plan.stale == ()


def test_compute_plan_update_on_runtime_path_change(tmp_path):
    """变化：wrapper/working-dir/env/log 绝对路径漂移 → 进 update 集。"""
    suffix = "scanner"
    old_root = tmp_path / "old-root"
    new_root = tmp_path / "new-root"
    old_root.mkdir()
    new_root.mkdir()
    label = f"{_LABEL_PREFIX}{suffix}"
    target = _target(new_root, [suffix])
    installed = {label: _plist_bytes(old_root, suffix)}

    plan = sync_mod.compute_plan(target, installed)

    assert plan.update == (label,)
    assert plan.install == ()
    assert plan.stale == ()


def test_compute_plan_idempotent_when_aligned(project_root):
    """幂等：已装内容 == 目标 → 三集皆空，全部 aligned。"""
    suffixes = ["scanner", "selfcheck", "update_data_daily"]
    target = _target(project_root, suffixes)
    installed = {
        f"{_LABEL_PREFIX}{s}": _plist_bytes(project_root, s) for s in suffixes
    }
    plan = sync_mod.compute_plan(target, installed)
    assert plan.is_empty
    assert plan.install == () and plan.update == () and plan.stale == ()
    assert set(plan.aligned) == set(target)


# --------------------------------------------------------------------------- #
# apply 序列：install / update / stale(prune on/off)
# --------------------------------------------------------------------------- #
def _domain(uid: int) -> str:
    return f"gui/{uid}"


def test_apply_install_sequence(project_root, tmp_path):
    """install：write plist + enable + bootstrap，序列正确。"""
    suffix = "scanner"
    target = _target(project_root, [suffix])
    label = f"{_LABEL_PREFIX}{suffix}"
    plan = SyncPlan(install=(label,), update=(), stale=())
    runner = FakeRunner(uid=501)
    agents = tmp_path / "LaunchAgents"
    notices = sync_mod.apply_plan(
        plan, target, runner=runner, agents_dir=agents, prune=False
    )
    # 文件写入了目标目录（注入 runner，不碰真 ~/Library）。
    assert len(runner.writes) == 1
    assert runner.writes[0][0] == str(agents / f"{label}.plist")
    # launchctl: enable 然后 bootstrap。
    d = _domain(501)
    assert runner.calls == [
        ["enable", f"{d}/{label}"],
        ["bootstrap", d, str(agents / f"{label}.plist")],
    ]
    assert runner.removes == []
    assert notices == []


def test_apply_update_sequence_bootout_then_bootstrap(project_root, tmp_path):
    """update：bootout → write → bootstrap，序列正确。"""
    suffix = "scanner"
    target = _target(project_root, [suffix])
    label = f"{_LABEL_PREFIX}{suffix}"
    plan = SyncPlan(install=(), update=(label,), stale=())
    runner = FakeRunner(uid=501)
    agents = tmp_path / "LaunchAgents"
    sync_mod.apply_plan(plan, target, runner=runner, agents_dir=agents, prune=False)
    d = _domain(501)
    assert runner.calls == [
        ["bootout", f"{d}/{label}"],
        ["bootstrap", d, str(agents / f"{label}.plist")],
    ]
    # bootout 在 write 之前发生（序列体现在 calls，写发生在两次 launchctl 之间）。
    assert len(runner.writes) == 1
    assert runner.removes == []


def test_apply_stale_warn_only_without_prune(project_root, tmp_path):
    """漂移 --prune 关：stale 仅告警，绝不 bootout / 删文件。"""
    label = f"{_LABEL_PREFIX}ghost_job"
    plan = SyncPlan(install=(), update=(), stale=(label,))
    runner = FakeRunner(uid=501)
    agents = tmp_path / "LaunchAgents"
    notices = sync_mod.apply_plan(
        plan, target={}, runner=runner, agents_dir=agents, prune=False
    )
    assert runner.calls == []  # 零 launchctl
    assert runner.removes == []  # 零删除
    assert any("stale" in n and "ghost_job" in n for n in notices)


def test_apply_stale_removed_with_prune(project_root, tmp_path):
    """漂移 --prune 开：stale 进 bootout + rm 序列。"""
    label = f"{_LABEL_PREFIX}ghost_job"
    plan = SyncPlan(install=(), update=(), stale=(label,))
    runner = FakeRunner(uid=501)
    agents = tmp_path / "LaunchAgents"
    sync_mod.apply_plan(
        plan, target={}, runner=runner, agents_dir=agents, prune=True
    )
    d = _domain(501)
    assert runner.calls == [["bootout", f"{d}/{label}"]]
    assert runner.removes == [str(agents / f"{label}.plist")]


# --------------------------------------------------------------------------- #
# dry-run：零副作用（注入替身记录为空）
# --------------------------------------------------------------------------- #
def test_dry_run_zero_side_effects(project_root, tmp_path):
    """--dry-run：真实 build_target 也不得写 deploy/LaunchAgents/launchctl。"""
    runner = FakeRunner(uid=501, installed={})
    deploy = tmp_path / "deploy"
    agents = tmp_path / "LaunchAgents"
    plan, notices = sync_mod.run_sync(
        project_root=str(project_root),
        agents_dir=agents,
        deploy_dir=deploy,
        apply=False,
        prune=False,
        acknowledge_schedule_change=False,
        runner=runner,
    )
    suffixes = [j.suffix for j in load_manifest().jobs if j.enabled]
    assert set(plan.install) == {f"{_LABEL_PREFIX}{s}" for s in suffixes}
    # dry-run 铁律：零 launchctl、零写、零删。
    assert runner.calls == []
    assert runner.writes == []
    assert runner.removes == []
    assert not deploy.exists()
    assert not agents.exists()
    assert notices == []


def test_run_sync_idempotent_zero_ops(project_root, tmp_path, monkeypatch):
    """已对齐再跑 --apply → 三集空，零 launchctl / fs 操作。"""
    suffixes = ["scanner", "selfcheck"]
    target = _target(project_root, suffixes)
    installed = {
        f"{_LABEL_PREFIX}{s}": _plist_bytes(project_root, s) for s in suffixes
    }
    monkeypatch.setattr(sync_mod, "build_target", lambda **kw: target)
    # apply 路径会过金标闸 → 注入空闸（已对齐场景金标无关）。
    monkeypatch.setattr(sync_mod, "assert_golden_gate", lambda *a, **k: None)
    runner = FakeRunner(uid=501, installed=installed)
    plan, notices = sync_mod.run_sync(
        project_root=str(project_root),
        agents_dir=tmp_path / "LaunchAgents",
        deploy_dir=tmp_path / "deploy",
        apply=True,
        prune=False,
        acknowledge_schedule_change=False,
        runner=runner,
    )
    assert plan.is_empty
    assert runner.calls == []
    assert runner.writes == []
    assert runner.removes == []


# --------------------------------------------------------------------------- #
# 金标闸：--apply 在调度偏离时被拒，除非 --acknowledge-schedule-change
# --------------------------------------------------------------------------- #
def test_golden_gate_refuses_apply_on_schedule_mismatch(
    project_root, tmp_path, monkeypatch
):
    """渲染调度 vs 基线不符 → --apply 被金标闸拒（raise RenderError）。"""
    suffix = "scanner"
    target = _target(project_root, [suffix])
    monkeypatch.setattr(sync_mod, "build_target", lambda **kw: target)

    # 构造一个「已装基线」：把同任务渲染后篡改时刻，落到 agents_dir 当 baseline。
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    baseline_pl = render_mod.build_plist(str(project_root), load_manifest().job(suffix))
    sci = baseline_pl["StartCalendarInterval"]
    entries = sci if isinstance(sci, list) else [sci]
    for e in entries:
        e["Hour"] = (int(e.get("Hour", 0)) + 5) % 24  # 基线时刻与目标不同
    (agents / f"{_LABEL_PREFIX}{suffix}.plist").write_bytes(plistlib.dumps(baseline_pl))

    runner = FakeRunner(uid=501, installed={})

    # 不带 acknowledge → 金标闸 raise，且零副作用。
    with pytest.raises(RenderError):
        sync_mod.run_sync(
            project_root=str(project_root),
            agents_dir=agents,
            deploy_dir=tmp_path / "deploy",
            apply=True,
            prune=False,
            acknowledge_schedule_change=False,
            runner=runner,
        )
    assert runner.calls == [] and runner.writes == []


def test_golden_gate_bypassed_with_acknowledge(project_root, tmp_path, monkeypatch):
    """带 --acknowledge-schedule-change → 跳过金标闸，apply 照常推进。"""
    suffix = "scanner"
    target = _target(project_root, [suffix])
    label = f"{_LABEL_PREFIX}{suffix}"
    monkeypatch.setattr(sync_mod, "build_target", lambda **kw: target)

    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    baseline_pl = render_mod.build_plist(str(project_root), load_manifest().job(suffix))
    sci = baseline_pl["StartCalendarInterval"]
    entries = sci if isinstance(sci, list) else [sci]
    for e in entries:
        e["Hour"] = (int(e.get("Hour", 0)) + 5) % 24
    (agents / f"{label}.plist").write_bytes(plistlib.dumps(baseline_pl))

    # 已装存在但 schedule 不同 → 该是 update。
    runner = FakeRunner(
        uid=501, installed={label: plistlib.dumps(baseline_pl)}
    )
    plan, _ = sync_mod.run_sync(
        project_root=str(project_root),
        agents_dir=agents,
        deploy_dir=tmp_path / "deploy",
        apply=True,
        prune=False,
        acknowledge_schedule_change=True,  # 显式确认，跳闸
        runner=runner,
    )
    assert plan.update == (label,)
    d = _domain(501)
    # 跳闸后真走 update 序列。
    assert runner.calls == [
        ["bootout", f"{d}/{label}"],
        ["bootstrap", d, str(agents / f"{label}.plist")],
    ]


def test_golden_gate_uses_preexisting_deploy_baseline_for_uninstalled_job(
    project_root, tmp_path, monkeypatch
):
    """未安装任务的 deploy 金标必须在 sync 写入任何 target 前被读取。"""
    suffix = "data_catalog_daily"
    target = _target(project_root, [suffix])
    monkeypatch.setattr(sync_mod, "build_target", lambda **kw: target)

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    baseline_pl = render_mod.build_plist(str(project_root), load_manifest().job(suffix))
    sci = baseline_pl["StartCalendarInterval"]
    entries = sci if isinstance(sci, list) else [sci]
    for e in entries:
        e["Hour"] = (int(e.get("Hour", 0)) + 7) % 24
    (deploy / f"{_LABEL_PREFIX}{suffix}.plist").write_bytes(
        plistlib.dumps(baseline_pl)
    )

    runner = FakeRunner(uid=501, installed={})

    with pytest.raises(RenderError):
        sync_mod.run_sync(
            project_root=str(project_root),
            agents_dir=tmp_path / "LaunchAgents",
            deploy_dir=deploy,
            apply=True,
            prune=False,
            acknowledge_schedule_change=False,
            runner=runner,
        )
    assert runner.calls == []
    assert runner.writes == []
