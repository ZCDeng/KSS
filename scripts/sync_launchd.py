#!/usr/bin/env python3
"""清单 → deploy/launchd → ~/Library/LaunchAgents → launchctl 幂等对账器（plan 2026-06-23-001 / U3）。

单一真源是 ``kss/config/cron_jobs.yaml``（U1）。本同步器把清单落地为已装态，对账三集：
- ``install``：清单有（enabled）、``~/Library/LaunchAgents`` 没装。
- ``update``：装了但内容（schedule/wrapper/log 语义）变了 → 须 bootout + 重 bootstrap。
- ``stale``：已装的 ``com.zcdeng.kss.*`` 不在清单 → 默认仅告警，``--prune`` 才移除。

设计纪律（KTD5 部署纪律）：
- **纯 diff 核**（:func:`compute_plan`）：给目标态 + 已装态 → 三集，**零副作用**、可单测。
- **默认 ``--dry-run``**：只打印 diff，``launchctl`` 与文件系统**零触碰**。
- **``--apply`` 前过 U2 金标闸**（:func:`render_launchd_plists.assert_golden_equivalent`）：
  渲染输出在 schedule/wrapper 上偏离已知良基线即拒，除非显式 ``--acknowledge-schedule-change``。
  U2 的 3 个已知 log-basename 规范化是有意收敛，金标闸只比日志**目录**，故不阻断。
- **可注入 launchctl + 文件系统**（:class:`SyncRunner` 协议）：测试注入 fake，断言调用序列，
  绝不碰真 ``launchctl`` / 真 ``~/Library/LaunchAgents``。

launchctl 习语镜像 ``scripts/kss_app_bridge.py``：``gui/{uid}`` 域、bootout/bootstrap/enable。
"""
from __future__ import annotations

import argparse
import os
import plistlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.config.cron_manifest import load_manifest  # noqa: E402
from kss.config.paths import PROJECT_ROOT  # noqa: E402

import render_launchd_plists as render_mod  # noqa: E402  (sibling script)

# 已装副本目录（macOS 用户级 LaunchAgents）。
DEFAULT_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
# 渲染产物入库目录（bridge glob 注册表）。
DEFAULT_DEPLOY_DIR = PROJECT_ROOT / "deploy" / "launchd"

_LABEL_PREFIX = "com.zcdeng.kss."


# --------------------------------------------------------------------------- #
# 注入边界：launchctl + 文件系统（测试替身在此切口）
# --------------------------------------------------------------------------- #
class SyncRunner:
    """真实副作用执行器：``launchctl`` 子进程 + 文件系统读写。

    测试注入 :class:`FakeRunner`（见 test_sync_launchd.py），断言调用序列而不触碰真环境。
    """

    def __init__(self, *, uid: int | None = None) -> None:
        self.uid = uid if uid is not None else os.getuid()

    # ---- launchctl ----
    def launchctl(self, args: list[str]) -> tuple[int, str, str]:
        import subprocess

        try:
            proc = subprocess.run(
                ["launchctl", *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            return 127, "", "launchctl not found"
        except subprocess.TimeoutExpired:
            return 124, "", "launchctl timeout"

    # ---- 文件系统 ----
    def list_installed(self, agents_dir: Path) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        if not agents_dir.is_dir():
            return out
        for path in sorted(agents_dir.glob(f"{_LABEL_PREFIX}*.plist")):
            out[path.stem] = path.read_bytes()
        return out

    def write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 纯 diff 核（零副作用，单测主战场）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SyncPlan:
    """对账结果三集（纯数据，无副作用）。"""

    install: tuple[str, ...]  # 清单有、未装 → 须装
    update: tuple[str, ...]  # 装了但内容变 → bootout + bootstrap
    stale: tuple[str, ...]  # 已装但不在清单 → 默认告警，--prune 才删
    aligned: tuple[str, ...] = field(default_factory=tuple)  # 装了且内容一致 → 零操作

    @property
    def is_empty(self) -> bool:
        return not (self.install or self.update or self.stale)


def _semantic_signature(pl: dict) -> tuple:
    """plist 的语义指纹：schedule + wrapper + 日志目录（与金标闸同维度）。

    比语义而非字节：避免「生成头注释 / 字典键序」这类无关差异误判为 update。
    复用 U2 渲染器导出的签名函数（单一真源，不本地另写）。
    """
    return (
        tuple(render_mod.schedule_signature(pl)),
        render_mod.wrapper_signature(pl),
        render_mod.log_signature(pl),
    )


def compute_plan(
    target: dict[str, dict],
    installed: dict[str, bytes],
) -> SyncPlan:
    """纯函数对账：目标态（label→渲染 plist dict）vs 已装态（label→plist bytes）。

    - install：target 有、installed 无。
    - update：两边都有但语义指纹不同。
    - stale：installed 有、target 无（且 label 命中 ``com.zcdeng.kss.*``）。
    - aligned：两边都有且语义指纹相同（幂等关键——返回但不进任何操作集）。

    零副作用：不渲染、不读盘、不调 launchctl。调用方先备好两个 dict。
    """
    target_labels = set(target)
    installed_labels = {lbl for lbl in installed if lbl.startswith(_LABEL_PREFIX)}

    install = sorted(target_labels - installed_labels)

    update: list[str] = []
    aligned: list[str] = []
    for label in sorted(target_labels & installed_labels):
        try:
            installed_pl = plistlib.loads(installed[label])
        except Exception:
            # 已装副本损坏/不可解析 → 当作须更新（重写覆盖）。
            update.append(label)
            continue
        if _semantic_signature(target[label]) != _semantic_signature(installed_pl):
            update.append(label)
        else:
            aligned.append(label)

    stale = sorted(installed_labels - target_labels)

    return SyncPlan(
        install=tuple(install),
        update=tuple(update),
        stale=tuple(stale),
        aligned=tuple(aligned),
    )


# --------------------------------------------------------------------------- #
# 金标闸（--apply 前置；R6 守护）
# --------------------------------------------------------------------------- #
def _baseline_for(suffix: str, *, agents_dir: Path, deploy_dir: Path) -> dict | None:
    """已装优先；未装取 deploy 模板。None = 无基线（新任务，金标无可比，放行）。"""
    installed = agents_dir / f"{_LABEL_PREFIX}{suffix}.plist"
    if installed.is_file():
        with installed.open("rb") as fh:
            return plistlib.load(fh)
    template = deploy_dir / f"{_LABEL_PREFIX}{suffix}.plist"
    if template.is_file():
        with template.open("rb") as fh:
            return plistlib.load(fh)
    return None


def assert_golden_gate(
    rendered: dict[str, dict],
    *,
    agents_dir: Path,
    deploy_dir: Path,
) -> None:
    """渲染输出 vs 已知良基线在 schedule/wrapper/日志目录上语义等价，否则 raise RenderError。

    无基线的任务（首装/新任务）跳过——金标无可比对象。已知 log-basename 规范化不阻断
    （金标闸只比日志目录，见 U2 :func:`log_signature`）。
    """
    for suffix, pl in rendered.items():
        baseline = _baseline_for(suffix, agents_dir=agents_dir, deploy_dir=deploy_dir)
        if baseline is None:
            continue
        render_mod.assert_golden_equivalent(pl, baseline)


# --------------------------------------------------------------------------- #
# 渲染 → 目标态
# --------------------------------------------------------------------------- #
def build_target(
    *,
    project_root: str,
    deploy_dir: Path,
    state_root: str | None = None,
    manifest_path: Path | str | None = None,
) -> dict[str, dict]:
    """渲染清单 enabled 任务到 ``deploy_dir``，返回 ``{label: plist_dict}``（目标态）。

    复用 U2 ``render_all``（写文件 + 校验），再按 enabled 过滤。渲染本身会写 deploy_dir
    （入库生成物），但 ``~/Library/LaunchAgents`` / launchctl 一律走注入 runner。
    """
    manifest = load_manifest(manifest_path)
    enabled_suffixes = {j.suffix for j in manifest.jobs if j.enabled}
    rendered_all = render_mod.render_all(
        project_root, deploy_dir, state_root=state_root, manifest_path=manifest_path
    )
    return {
        f"{_LABEL_PREFIX}{suffix}": pl
        for suffix, pl in rendered_all.items()
        if suffix in enabled_suffixes
    }


# --------------------------------------------------------------------------- #
# apply：执行三集（副作用全走 runner）
# --------------------------------------------------------------------------- #
def apply_plan(
    plan: SyncPlan,
    target: dict[str, dict],
    *,
    runner: SyncRunner,
    agents_dir: Path,
    prune: bool,
) -> list[str]:
    """执行对账：cp + bootstrap/enable（install）、bootout+bootstrap（update）、
    可选 bootout+rm（stale，仅 --prune）。返回告警/错误行（人读）。

    副作用全部经 ``runner``（launchctl + 文件系统），故测试可断言调用序列。
    """
    uid = runner.uid
    domain = f"gui/{uid}"
    notices: list[str] = []

    for label in plan.install:
        dest = agents_dir / f"{label}.plist"
        runner.write(dest, render_mod._to_xml(target[label]))
        runner.launchctl(["enable", f"{domain}/{label}"])
        rc, _, err = runner.launchctl(["bootstrap", domain, str(dest)])
        if rc != 0 and "already" not in err.lower():
            notices.append(f"[install] {label}: bootstrap rc={rc} {err.strip()}")

    for label in plan.update:
        dest = agents_dir / f"{label}.plist"
        runner.launchctl(["bootout", f"{domain}/{label}"])
        runner.write(dest, render_mod._to_xml(target[label]))
        rc, _, err = runner.launchctl(["bootstrap", domain, str(dest)])
        if rc != 0 and "already" not in err.lower():
            notices.append(f"[update] {label}: bootstrap rc={rc} {err.strip()}")

    for label in plan.stale:
        if prune:
            runner.launchctl(["bootout", f"{domain}/{label}"])
            runner.remove(agents_dir / f"{label}.plist")
            notices.append(f"[prune] {label}: 已 bootout + 删除")
        else:
            notices.append(
                f"[stale] {label}: 已装但不在清单（--prune 才移除；当前仅告警）"
            )

    return notices


# --------------------------------------------------------------------------- #
# 报表（dry-run / apply 共用）
# --------------------------------------------------------------------------- #
def format_plan(plan: SyncPlan, *, prune: bool) -> str:
    lines: list[str] = []
    lines.append("=== sync_launchd 对账 ===")
    lines.append(f"install ({len(plan.install)}): {', '.join(plan.install) or '—'}")
    lines.append(f"update  ({len(plan.update)}): {', '.join(plan.update) or '—'}")
    stale_note = "" if prune else "  ← 默认仅告警，--prune 才移除"
    lines.append(f"stale   ({len(plan.stale)}): {', '.join(plan.stale) or '—'}{stale_note}")
    lines.append(f"aligned ({len(plan.aligned)}): 无需操作")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def run_sync(
    *,
    project_root: str,
    agents_dir: Path,
    deploy_dir: Path,
    apply: bool,
    prune: bool,
    acknowledge_schedule_change: bool,
    state_root: str | None = None,
    manifest_path: Path | str | None = None,
    runner: SyncRunner | None = None,
) -> tuple[SyncPlan, list[str]]:
    """主流程（dry-run 默认）。返回 (plan, notices)。

    dry-run（apply=False）：渲染目标态 + 算 diff + 打印，**不碰 launchctl / LaunchAgents**。
    apply=True：先过金标闸（除非 acknowledge），再 :func:`apply_plan` 落地。
    """
    runner = runner or SyncRunner()
    target = build_target(
        project_root=project_root,
        deploy_dir=deploy_dir,
        state_root=state_root,
        manifest_path=manifest_path,
    )
    installed = runner.list_installed(agents_dir)
    plan = compute_plan(target, installed)

    if not apply:
        return plan, []

    if not acknowledge_schedule_change:
        rendered_by_suffix = {
            label[len(_LABEL_PREFIX):]: pl for label, pl in target.items()
        }
        assert_golden_gate(
            rendered_by_suffix, agents_dir=agents_dir, deploy_dir=deploy_dir
        )

    notices = apply_plan(
        plan, target, runner=runner, agents_dir=agents_dir, prune=prune
    )
    return plan, notices


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="清单 → launchd 幂等对账同步器")
    p.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="代码根（绝对路径；wrapper/日志路径基准）",
    )
    p.add_argument("--state-root", help="状态根（bundle-mode 注入 KSS_STATE_ROOT）")
    p.add_argument(
        "--agents-dir",
        default=str(DEFAULT_LAUNCH_AGENTS),
        help="~/Library/LaunchAgents 目录",
    )
    p.add_argument(
        "--deploy-dir", default=str(DEFAULT_DEPLOY_DIR), help="deploy/launchd 渲染落点"
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="只打印 diff，零副作用（默认）"
    )
    mode.add_argument(
        "--apply", action="store_true", help="真正落地（cp + launchctl bootstrap/enable）"
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="（仅 --apply）移除 stale 任务（bootout + 删文件）；否则仅告警",
    )
    p.add_argument(
        "--acknowledge-schedule-change",
        action="store_true",
        help="显式确认调度偏离基线，跳过金标闸（慎用）",
    )
    args = p.parse_args(argv)

    if args.prune and not args.apply:
        print("[sync] --prune 仅在 --apply 下生效；dry-run 不移除任何东西", file=sys.stderr)

    try:
        plan, notices = run_sync(
            project_root=args.project_root,
            agents_dir=Path(args.agents_dir),
            deploy_dir=Path(args.deploy_dir),
            apply=bool(args.apply),
            prune=bool(args.prune),
            acknowledge_schedule_change=bool(args.acknowledge_schedule_change),
            state_root=args.state_root,
        )
    except render_mod.RenderError as exc:
        print(f"[sync] 金标闸拒绝 --apply：{exc}", file=sys.stderr)
        print("       渲染调度偏离已知良基线；确属预期请加 --acknowledge-schedule-change", file=sys.stderr)
        return 3

    print(format_plan(plan, prune=bool(args.prune)))
    for line in notices:
        print(line)
    if not args.apply:
        print("\n[dry-run] 未触碰 launchctl / LaunchAgents。落地请加 --apply。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
