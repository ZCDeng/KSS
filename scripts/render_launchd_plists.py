#!/usr/bin/env python3
"""清单驱动的 launchd plist 确定性渲染器（plan 2026-06-23-001 / U2）。

``kss/config/cron_jobs.yaml``（U1 清单）是单一真源。本渲染器把每条任务展开成
``deploy/launchd/com.zcdeng.kss.<suffix>.plist``，确定性、可复跑、带安全纪律。
本文件由 ``scripts/render_intraday_launchd_plist.py``（分时单任务渲染器）**泛化/重命名**
而来——KTD3 要求只保留一个共享渲染器，不再造第二套。

3 种调度形态（U1 实证）：
- daily + weekdays [1-5]  → ``StartCalendarInterval`` 为**列表**，每工作日一条 ``{Weekday,Hour,Minute}``。
- daily 无 weekdays        → **单条** ``{Hour,Minute}``（无 Weekday 键）。
- weekly                   → **单条** ``{Weekday,Hour,Minute}``。

安全纪律（全部以「拒绝、不写文件、fail-loud」实现，承接 intraday 渲染器）：
- env 仅允许 ``_ALLOWED_ENV_KEYS = {PATH, HOME, KSS_STATE_ROOT}``；token 类键拒绝。
- 渲染结果任意字符串命中 token-pattern（复用 ``kss.security.redaction`` 的 **单一 canonical**
  ``CREDENTIAL_VALUE_RE`` / ``contains_credential``，不本地另写正则）→ 拒绝。
- 任何残留占位符标记（``__MARKER__``）→ 拒绝。
- 关键运行期路径须绝对。
- ``Label`` 必须 == 输出文件名 stem == ``com.zcdeng.kss.<suffix>``。

仅标准库（plistlib + argparse + pathlib + re）+ U1 清单加载器。CLI:
    python3 scripts/render_launchd_plists.py --project-root <abs> [--state-root <abs>] \
        [--output-dir deploy/launchd] [--suffix <one-job>]
"""
from __future__ import annotations

import argparse
import plistlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.config.cron_manifest import (  # noqa: E402
    CronJob,
    load_manifest,
)
from kss.security.redaction import (  # noqa: E402
    CREDENTIAL_VALUE_RE,
    contains_credential,
)

# 渲染产物默认落点（code root 下；bridge glob 注册表）。
_DEFAULT_OUTPUT_DIR = "deploy/launchd"

# 静态 plist 沿用的 PATH（与现装 14 个 plist 完全一致）。
_DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# 占位符残留检测：模板里的 __MARKER__ 形态。渲染后若仍有命中即漏替换 → 拒绝。
_PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")

# plist EnvironmentVariables 允许出现的 key 白名单（与 intraday 渲染器共用语义）。
# KSS_STATE_ROOT 仅在 bundle-mode（state_root≠project_root）注入；TUSHARE_TOKEN 显式不在内。
_ALLOWED_ENV_KEYS = {"PATH", "HOME", "KSS_STATE_ROOT"}

# 「生成勿改」头注释（写在 plist <plist> 标签后；plistlib 不带注释，故附加在 XML 字符串里）。
_GENERATED_HEADER = (
    "<!-- GENERATED — do not edit; edit kss/config/cron_jobs.yaml -->"
)


class RenderError(Exception):
    """渲染前置校验失败——调用方应非零退出、不写文件。"""


def _require_abs(value: str, label: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        raise RenderError(f"{label} 必须是绝对路径，给的是相对路径: {value!r}")
    return p


def _scan_strings(obj: object):
    """递归 yield plist 结构里的所有字符串（含 key 与 value）。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _scan_strings(k)
            yield from _scan_strings(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _scan_strings(item)


def _calendar_interval(job: CronJob):
    """schedule → ``StartCalendarInterval`` 值（3 形态，U1 实证）。

    - weekly            → 单 dict ``{Weekday,Hour,Minute}``。
    - daily + weekdays  → list[dict]，每工作日一条 ``{Weekday,Hour,Minute}``。
    - daily 无 weekdays → 单 dict ``{Hour,Minute}``（无 Weekday 键）。
    """
    s = job.schedule
    if s.is_weekly:
        return {"Weekday": s.weekday, "Hour": s.hour, "Minute": s.minute}
    if s.weekdays:
        return [
            {"Weekday": wd, "Hour": s.hour, "Minute": s.minute} for wd in s.weekdays
        ]
    return {"Hour": s.hour, "Minute": s.minute}


def build_plist(project_root: str, job: CronJob, state_root: str | None = None) -> dict:
    """构造单任务 plist dict（确定性、纯函数）；校验在 :func:`render` 里统一做。

    日志路径沿用现装 14 个 plist 的约定：``<project_root>/storage/logs/cron/<suffix>.log``。
    ``state_root`` 仅在 bundle-mode（≠ project_root）时注入 ``KSS_STATE_ROOT`` env。
    """
    proj = _require_abs(project_root, "--project-root")

    wrapper = (proj / job.wrapper).resolve()
    log_path = proj / "storage" / "logs" / "cron" / f"{job.suffix}.log"

    env: dict[str, str] = {
        "PATH": _DEFAULT_PATH,
        "HOME": "/Users/zcdeng",
    }
    if state_root is not None:
        state = _require_abs(state_root, "--state-root")
        if str(state) != str(proj):
            # bundle-mode：运行期路径根注入 KSS_STATE_ROOT（合双根设计）。
            env["KSS_STATE_ROOT"] = str(state)

    program_args = [str(wrapper)] + list(job.args)

    return {
        "Label": job.label,
        "StartCalendarInterval": _calendar_interval(job),
        "ProgramArguments": program_args,
        "WorkingDirectory": str(proj),
        "EnvironmentVariables": env,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "RunAtLoad": False,
        "ThrottleInterval": 60,
    }


def _validate(pl: dict, output: Path) -> None:
    """渲染前置不变式——任一失败 raise RenderError（fail-loud，不写文件）。"""
    # Label 必须 == 输出文件名 stem（bridge glob / selfcheck 派生）。
    stem = output.name[:-6] if output.name.endswith(".plist") else output.stem
    if pl.get("Label") != stem:
        raise RenderError(
            f"Label {pl.get('Label')!r} 与输出文件名 stem {stem!r} 不一致"
        )
    if not str(pl.get("Label", "")).startswith("com.zcdeng.kss."):
        raise RenderError(f"Label 须以 com.zcdeng.kss. 开头：{pl.get('Label')!r}")

    # EnvironmentVariables 只允许白名单 key；token 类 key 绝不出现。
    env = pl.get("EnvironmentVariables", {})
    for key in env:
        if key not in _ALLOWED_ENV_KEYS:
            raise RenderError(f"EnvironmentVariables 含未授权 key: {key!r}")
        if "TOKEN" in key.upper():
            raise RenderError(f"EnvironmentVariables 不得注入 token 类 key: {key!r}")

    # 所有字符串：无残留占位符、无 token-pattern（复用 canonical 真源）。
    for s in _scan_strings(pl):
        if _PLACEHOLDER_RE.search(s):
            raise RenderError(f"残留未解析占位符: {s!r}")
        if contains_credential(s) or CREDENTIAL_VALUE_RE.search(s):
            raise RenderError(f"渲染输出命中 token-pattern，拒绝写出: {s!r}")

    # 关键运行期路径须绝对。
    for key in ("WorkingDirectory", "StandardOutPath", "StandardErrorPath"):
        val = pl.get(key, "")
        if not Path(val).is_absolute():
            raise RenderError(f"{key} 非绝对路径: {val!r}")
    prog = pl.get("ProgramArguments") or []
    if not prog or not Path(prog[0]).is_absolute():
        raise RenderError(f"ProgramArguments[0] 非绝对 wrapper 路径: {prog!r}")


def _to_xml(pl: dict) -> bytes:
    """plist dict → 带「生成勿改」头注释的 XML bytes（确定性）。"""
    data = plistlib.dumps(pl, sort_keys=False)
    text = data.decode("utf-8")
    # 在 <plist ...> 行后插入生成头注释（plistlib 自身不写注释）。
    marker = "<plist version=\"1.0\">\n"
    if marker in text:
        text = text.replace(marker, marker + _GENERATED_HEADER + "\n", 1)
    return text.encode("utf-8")


def render(project_root: str, job: CronJob, output: Path, state_root: str | None = None) -> dict:
    """构造 + 校验 + 原子写单任务 plist。校验失败 raise RenderError（不写文件）。"""
    pl = build_plist(project_root, job, state_root=state_root)
    _validate(pl, output)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_bytes(_to_xml(pl))
    tmp.replace(output)
    return pl


def render_all(
    project_root: str,
    output_dir: Path,
    *,
    state_root: str | None = None,
    manifest_path: Path | str | None = None,
) -> dict[str, dict]:
    """渲染清单全部任务到 ``output_dir/com.zcdeng.kss.<suffix>.plist``。

    返回 ``{suffix: plist_dict}``。任一任务校验失败即 raise（fail-loud，不静默跳过）。
    """
    manifest = load_manifest(manifest_path)
    rendered: dict[str, dict] = {}
    for job in manifest.jobs:
        output = Path(output_dir) / f"{job.label}.plist"
        rendered[job.suffix] = render(
            project_root, job, output, state_root=state_root
        )
    return rendered


# --------------------------------------------------------------------------- #
# 金标闸（R6 守护，U3 --apply 前置）：渲染输出 vs 已知良 plist 语义等价
# --------------------------------------------------------------------------- #
def _normalize_calendar(value: object) -> list[tuple]:
    """``StartCalendarInterval`` → 规范化、可比较的有序条目集（无关键序/列表序）。"""
    entries = value if isinstance(value, list) else [value]
    norm = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        norm.append(
            (
                e.get("Weekday"),
                e.get("Hour"),
                e.get("Minute"),
            )
        )
    return sorted(norm, key=lambda t: tuple(-1 if x is None else x for x in t))


def schedule_signature(pl: dict) -> list[tuple]:
    """plist 的调度语义指纹（金标比对维度之一）。"""
    return _normalize_calendar(pl.get("StartCalendarInterval"))


def wrapper_signature(pl: dict) -> tuple[str, ...]:
    """plist 的 wrapper 语义指纹：``(wrapper_basename, *args)``（路径前缀无关）。"""
    prog = pl.get("ProgramArguments") or []
    if not prog:
        return ()
    return (Path(prog[0]).name,) + tuple(prog[1:])


def log_signature(pl: dict) -> tuple[str, str]:
    """plist 的日志语义指纹：(stdout_dir_tail, stderr_dir_tail)。

    比的是**日志目录**（``storage/logs/cron``）而非 basename：现装 3 个 plist
    （macro_daily / factor_health / ledger_settle）的 log basename 与 suffix 历史性
    不一致（``update_macro_daily.log`` / ``*_daily.log``）。清单已把 basename 规范化为
    ``<suffix>.log``（U1/U2 设计），属有意收敛；R6 守护的是调度/wrapper，非日志文件名。
    """
    return (
        "/".join(Path(pl.get("StandardOutPath", "")).parts[-3:-1]),
        "/".join(Path(pl.get("StandardErrorPath", "")).parts[-3:-1]),
    )


def assert_golden_equivalent(rendered: dict, baseline: dict) -> None:
    """渲染输出 vs 已知良基线在 schedule / wrapper / 日志**目录**上语义等价。

    任一维度不符即 raise RenderError（金标闸拒绝；U3 不予 --apply）。
    刻意不比 byte / 注释 / ThrottleInterval / RunAtLoad / 日志 basename——那些是渲染
    细节或有意规范化项，非 R6 守护的语义（调度时刻 + wrapper 不变）。
    """
    rs, bs = schedule_signature(rendered), schedule_signature(baseline)
    if rs != bs:
        raise RenderError(f"金标闸：schedule 不等价 rendered={rs} baseline={bs}")
    rw, bw = wrapper_signature(rendered), wrapper_signature(baseline)
    if rw != bw:
        raise RenderError(f"金标闸：wrapper 不等价 rendered={rw} baseline={bw}")
    rl, bl = log_signature(rendered), log_signature(baseline)
    if rl != bl:
        raise RenderError(f"金标闸：日志目录不等价 rendered={rl} baseline={bl}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="清单驱动渲染 KSS launchd plist")
    p.add_argument("--project-root", required=True, help="代码根（绝对路径；bridge glob 落点）")
    p.add_argument("--state-root", help="状态根（绝对路径；bundle-mode 注入 KSS_STATE_ROOT）")
    p.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR, help="plist 输出目录")
    p.add_argument("--suffix", help="只渲染单个任务（缺省渲染全清单）")
    args = p.parse_args(argv)

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (Path(args.project_root) / output_dir)

    try:
        if args.suffix:
            manifest = load_manifest()
            job = manifest.job(args.suffix)
            if job is None:
                print(f"[render] 清单无此任务: {args.suffix}", file=sys.stderr)
                return 2
            output = output_dir / f"{job.label}.plist"
            render(args.project_root, job, output, state_root=args.state_root)
            print(f"[render] 已写出 {output}")
        else:
            rendered = render_all(
                args.project_root, output_dir, state_root=args.state_root
            )
            print(f"[render] 已写出 {len(rendered)} 个 plist 到 {output_dir}")
    except RenderError as exc:
        print(f"[render] 拒绝渲染: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
