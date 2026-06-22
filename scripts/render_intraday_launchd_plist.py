#!/usr/bin/env python3
"""确定性 launchd plist 渲染器（仓库首例 render-plist；KTD6 / D4 / SG6 / S5）.

仓库内 14 个 plist 全是静态硬编码绝对路径文件，无任何 render/template 先例。分时
采集 plist 须由本渲染器产出，因 **bundle 双根**（学习 #4）正当化：渲染产物落
``PROJECT_ROOT/deploy/launchd/``（code root，bridge glob），但运行期路径
（``StandardOutPath`` / ``KSS_STATE_ROOT``）指向 **state root**。

设计约束（全部以「拒绝并非零退出、不写文件」实现，fail-loud）：
- 任何残留占位符标记（``__...__``）→ 拒绝。
- 任何应为绝对而给了相对的路径 → 拒绝。
- 渲染结果任意字符串命中 token-pattern（复用 ``kss.security.redaction`` 的
  **单一 canonical** ``CREDENTIAL_VALUE_RE`` / ``contains_credential``，S3/S5）→ 拒绝。
- ``EnvironmentVariables`` 只允许注入 ``KSS_STATE_ROOT``；``TUSHARE_TOKEN`` 绝不出现。
- ``Label`` 必须 == 输出文件名 stem。

仅标准库（plistlib + argparse + pathlib + re）。CLI:
    python3 scripts/render_intraday_launchd_plist.py \
        --project-root <abs> --state-root <abs> \
        --output deploy/launchd/com.zcdeng.kss.collect_intraday.plist
"""
from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.security.redaction import (  # noqa: E402
    CREDENTIAL_VALUE_RE,
    contains_credential,
)

# Label / 文件名 stem 单一事实源（bridge 白名单 + selfcheck 由文件名派生）。
LABEL = "com.zcdeng.kss.collect_intraday"

# 占位符残留检测：模板里的 __MARKER__ 形态。渲染后若仍有命中即漏替换 → 拒绝。
_PLACEHOLDER_RE = __import__("re").compile(r"__[A-Z][A-Z0-9_]*__")

# plist EnvironmentVariables 允许出现的 key 白名单（KSS_STATE_ROOT 为本 plist 唯一
# 注入项；PATH/HOME 沿用静态 plist 约定）。TUSHARE_TOKEN 显式不在内。
_ALLOWED_ENV_KEYS = {"PATH", "HOME", "KSS_STATE_ROOT"}


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


def build_plist(project_root: str, state_root: str) -> dict:
    """构造 plist dict（确定性、纯函数）；校验在 :func:`render` 里统一做。

    state-root≠code-root 是常态（bundle-mode）：运行期路径解析于 state root，
    WorkingDirectory / wrapper 落 code root。
    """
    proj = _require_abs(project_root, "--project-root")
    state = _require_abs(state_root, "--state-root")

    wrapper = proj / "scripts" / "run_collect_intraday.sh"
    log_dir = state / "storage" / "logs" / "launchd"
    log_path = log_dir / "collect_intraday.log"

    return {
        "Label": LABEL,
        "StartCalendarInterval": {"Hour": 15, "Minute": 5},
        "ProgramArguments": [str(wrapper)],
        "WorkingDirectory": str(proj),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": "/Users/zcdeng",
            # 仓库首个注入 KSS_STATE_ROOT 的 plist（新约定，合双根设计）。
            # 故意不注入 TUSHARE_TOKEN —— 采集器从 <state>/secrets/ 读（KTD4/S5）。
            "KSS_STATE_ROOT": str(state),
        },
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
    if pl.get("Label") != LABEL:
        raise RenderError(f"Label 必须为 {LABEL!r}")

    # EnvironmentVariables 只允许白名单 key；TUSHARE_TOKEN 绝不出现（S5）。
    env = pl.get("EnvironmentVariables", {})
    for key in env:
        if key not in _ALLOWED_ENV_KEYS:
            raise RenderError(f"EnvironmentVariables 含未授权 key: {key!r}")
        if "TOKEN" in key.upper():
            raise RenderError(f"EnvironmentVariables 不得注入 token 类 key: {key!r}")

    # 所有字符串：无残留占位符、无 token-pattern。
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


def render(project_root: str, state_root: str, output: Path) -> dict:
    """构造 + 校验 + 原子写 plist。校验失败 raise RenderError（不写文件）。"""
    pl = build_plist(project_root, state_root)
    _validate(pl, output)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    data = plistlib.dumps(pl, sort_keys=False)
    tmp.write_bytes(data)
    tmp.replace(output)
    return pl


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="确定性渲染分时采集 launchd plist")
    p.add_argument("--project-root", required=True, help="代码根（绝对路径；bridge glob 落点）")
    p.add_argument("--state-root", required=True, help="状态根（绝对路径；运行期路径根）")
    p.add_argument("--output", required=True, help="输出 plist 路径")
    args = p.parse_args(argv)

    output = Path(args.output)
    try:
        render(args.project_root, args.state_root, output)
    except RenderError as exc:
        print(f"[render] 拒绝渲染: {exc}", file=sys.stderr)
        return 2
    print(f"[render] 已写出 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
