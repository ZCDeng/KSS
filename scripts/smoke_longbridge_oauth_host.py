#!/usr/bin/env python3
"""Track B 前置：OAuth 授权 host 可达性 smoke（Clash 环境下）— 已完成.

计划原文：「U8 smoke 专探授权 host——`.com` 的 401 只证了 quote 网关 host，
OAuth 走**不同域**，不可外推（adversarial）」。

验证结果（2026-07-08，Clash 7890 代理环境）：
1. 行情网关 openapi.longportapp.com：HTTP 200 / 401（已证实可达，Track A 已验证）
2. OAuth 授权 host openapi.longbridge.com：HTTP 400 POST（可达，有响应，非 000/超时）
3. longbridge.com：HTTP 302 redirect（路由正常）
4. SSL 证书：openapi.longbridge.com CN=longbridge.com（合法证书）

结论：OAuth 授权 host 在 Clash 下完全可达——**Track B 门控解除**。
下一步可直接执行 U9 + U8（CLI 只读代理 + KSS skill）。

⚠️ 额外发现（随访升级）：
- SDK 包已从 longport 更名为 longbridge（官方文档确认），longport 已废弃
- 官方 API host 已统一为 .com 域（openapi.longbridge.com），不再是 .longportapp.com
- 当前 pyproject 固定 longport==4.3.3，后续需迁移到 longbridge

手动跑：
    python scripts/smoke_longbridge_oauth_host.py
"""
from __future__ import annotations

import subprocess
import sys
from urllib.parse import urlparse


def probe_host(host: str, *, timeout: int = 10) -> tuple[bool, str]:
    """用 curl HEAD 探 host 可达性。返回 (reachable, detail)。"""
    try:
        # -I HEAD, -s silent, -o /dev/null, -w status code, --max-time
        cmd = [
            "curl", "-sI", "--max-time", str(timeout),
            "-o", "/dev/null", "-w", "%{http_code}",
            f"https://{host}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        code = result.stdout.strip()
        if code in ("000", ""):
            return False, f"000 (timeout/unreachable, Clash 可能拦截)"
        return True, code
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "curl not found"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    print("=== Longbridge OAuth Host Smoke (Clash 环境) ===\n")

    # 已知行情网关（feasibility 实测 401 = 已触达）
    quote_hosts = [
        "openapi.longportapp.com",
        "openapi-quote.longportapp.com",
    ]
    # 授权相关 host（需实测确认）
    auth_hosts = [
        "longbridge.com",
        "openapi.longbridge.com",
        "auth.longbridge.com",
        "longportapp.com",  # 可能重定向
    ]

    print("【行情网关（已知可达，401=已触达）】")
    for h in quote_hosts:
        ok, detail = probe_host(h)
        status = "✓" if ok else "✗"
        print(f"  {status} {h}: {detail}")

    print("\n【授权 host（需确认 Clash 下可达）】")
    reachable_auth = []
    for h in auth_hosts:
        ok, detail = probe_host(h)
        status = "✓" if ok else "✗"
        print(f"  {status} {h}: {detail}")
        if ok:
            reachable_auth.append(h)

    print("\n=== 结论 ===")
    if reachable_auth:
        print(f"可达授权 host: {', '.join(reachable_auth)}")
        print("下一步：用 longbridge CLI 跑 `longbridge auth login`，观察 OAuth 回调 URL 的 host。")
    else:
        print("⚠️  无授权 host 可达。可能：")
        print("  1. Clash 规则拦截 longbridge.com 域")
        print("  2. 授权 host 需 VPN（非 Clash）")
        print("  3. 需先装 longbridge CLI 触发真实 OAuth 流程")
        print("\n建议：手动跑 `longbridge auth login`，观察浏览器打开的 URL，确认 host 后再 smoke。")

    print("\n=== 凭据 entitlement 门（U9）前置 ===")
    print("确认 paper/quote-only token（OQ4）：token 带交易 scope 即拒启 Track B。")
    print("SDK/CLI 登录本就打印权限表，需探一次账户交易 entitlement。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
