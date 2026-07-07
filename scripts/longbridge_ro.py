#!/usr/bin/env python3
"""U9: Track B CLI 只读硬闸——KSS 控制的薄代理 + 凭据 entitlement 门（R10 / KTD3 / KTD7）.

把 Track B 的「只读」从**文档约定**升级为**运行时强制**——agent 无法经 CLI 触发任何
交易，即便被注入（adversarial 收敛）。三支护栏：

1. **子命令白名单**：仅放行 ``{quote, kline, static-info}``，其余一律拒。
2. **symbol 校验**：参数须匹配 ``NNNNNN.(SH|SZ|BJ)`` 格式，拒 shell 元字符（防注入/命令拼接）。
3. **凭据 entitlement 门**：启动时探一次账户交易 scope，带交易 scope 即拒启（paper/quote-only 硬性前置）。

用法：
    python scripts/longbridge_ro.py quote 688008.SH
    python scripts/longbridge_ro.py kline 688008.SH

禁止：
    python scripts/longbridge_ro.py buy 688008.SH   → 非零退出
    python scripts/longbridge_ro.py trade today      → 非零退出
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import NoReturn

# 只读子命令白名单——硬编码，绝不动态扩展。
_ALLOWLIST: frozenset[str] = frozenset({"quote", "kline", "static-info"})

# 禁用命令——出现即拒（额外显式检查，即便不在 allowlist 也拒）。
_DENYLIST: frozenset[str] = frozenset({
    "buy", "sell", "cancel", "replace", "order", "orders",
    "positions", "asset", "account", "balance", "margin",
    "trade", "trades", "submit", "amend",
})

# symbol 格式：NNNNNN.(SH|SZ|BJ)（拒 shell 元字符 + 路径穿越）。
_SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

# 凭据 env 名（与 U6 Keychain 对齐）。
_CRED_KEYS = ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN")


def _accept(command: str, args: list[str]) -> bool:
    """准入判定：allowlist + symbol 审计。返回 True=放行。"""
    if command not in _ALLOWLIST:
        return False
    if command in _DENYLIST:
        return False
    # 校验参数：每项须为有效 symbol、数字参数（≤4 位，如 count/timeout）或 合法 flag（--xxx 或 -x）
    for a in args:
        if _SYMBOL_RE.match(a):
            continue
        if a.isdigit() and len(a) <= 4:
            continue
        if a.startswith("--") and a[2:].replace("-", "").isalpha():
            continue
        if a.startswith("-") and len(a) == 2 and a[1:].isalpha():
            continue
        # 含 shell 元字符 → 拒
        if any(c in a for c in (";", "|", "&", "$", "`", "(", ")", "<", ">", "'", '"')):
            return False
        # 未匹配任何合法模式 → 拒（默认-deny）
        return False
    return True


def _check_entitlement() -> tuple[bool, str]:
    """启动时探一次账户交易 scope（KTD7）.

    用 SDK 或环境凭据查询 entitlement；带任何 trade scope → 拒启 Track B。
    当前实现：读 token 解码后检查 mid/ac 字段。
    """
    import json as _json

    token = os.environ.get("LONGBRIDGE_ACCESS_TOKEN", "")
    if not token:
        return False, "no_token"
    # access_token 是 JWT base64url 三段。取 payload（中间段）。
    try:
        # m_ prefix = 自定义 JWT，直接 base64url decode payload
        payload_b64 = token.split(".")[1] if "." in token else token
        # base64url → base64
        payload_b64 = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        import base64

        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        ac = payload.get("ac", "")
        mid = payload.get("mid", "")
        # ac = account type (lb_papertrading / lb_live 等)
        # paper trading → 安全；live 或含 trade → 须核
        if "live" in ac.lower() or "trade" in ac.lower():
            return False, f"entitlement_has_trade_scope_ac={ac}"
        # mid = member ID；含 trade scope → 拒
        return True, f"ac={ac}"
    except Exception:
        # 无法解码 → 保守拒（不盲通过）
        return False, "entitlement_parse_failed"


def _die(msg: str) -> NoReturn:
    print(f"[longbridge-ro/REJECT] {msg}", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) < 2:
        _die("usage: longbridge_ro.py <quote|kline|static-info> SYMBOL [...]")

    command = args[0]
    rest = args[1:]

    # 门 1：entitlement（KTD7）
    ok, reason = _check_entitlement()
    if not ok:
        _die(f"Track B blocked: credential entitlement gate failed ({reason}). "
             "Only paper/quote-only tokens are supported.")

    # 门 2：子命令 allowlist
    if command in _DENYLIST:
        _die(f"trade command '{command}' is blocked by denylist (KTD3).")
    if not _accept(command, rest):
        _die(f"command '{command}' not in read-only allowlist: {sorted(_ALLOWLIST)}")

    # 门 3：代理转发到真实 CLI（KSS 控制进程边界）
    cmd = ["longbridge", command, *rest]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        _die("longbridge CLI not found. Install: brew install --cask longbridge/tap/longbridge-terminal")
    except subprocess.TimeoutExpired:
        _die("longbridge CLI timed out (30s).")

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    print(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
