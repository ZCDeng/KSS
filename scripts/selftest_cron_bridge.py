#!/usr/bin/env python3
"""定时任务 bridge 纯逻辑自检（无第三方依赖，不触发 launchctl 副作用）。

覆盖：调度字符串人读化、日志缺失兜底、label 白名单 + 注入防护。
用法： python3 scripts/selftest_cron_bridge.py   （退出码非 0 即失败）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kss_app_bridge as b  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # 调度人读化
    check("工作日", b._parse_schedule([{"Weekday": w, "Hour": 17, "Minute": 30} for w in range(1, 6)]), "工作日 17:30")
    check("单周五-list", b._parse_schedule([{"Weekday": 5, "Hour": 17, "Minute": 0}]), "每周五 17:00")
    check("单周五-dict", b._parse_schedule({"Weekday": 5, "Hour": 19, "Minute": 30}), "每周五 19:30")
    check("每天", b._parse_schedule({"Hour": 9, "Minute": 0}), "每天 09:00")
    check("空", b._parse_schedule(None), "未设定")

    # 日志缺失兜底（Fail loud：返回明确未知态，不抛）
    check("log-missing", b._last_run("/nonexistent/x.log"), {"at": None, "line": None})
    check("log-None", b._last_run(None), {"at": None, "line": None})

    # 白名单 + 注入防护：非白名单 label 绝不触发 launchctl
    r = b._cron_action("com.evil.x", "rerun")
    check("reject-unknown-label", (r["ok"], "unknown label" in r["error"]), (False, True))
    r2 = b._cron_action("a; rm -rf /", "disable")
    check("reject-injection", (r2["ok"], "unknown label" in r2["error"]), (False, True))
    labels = b._launchd_plists()
    if labels:
        r3 = b._cron_action(next(iter(labels)), "evil-action")
        check("reject-unknown-action", (r3["ok"], "unknown action" in r3["error"]), (False, True))

    # 白名单由 plist 文件名派生，前缀受限
    check("whitelist-nonempty", len(labels) >= 9, True)
    check("whitelist-prefix", all(k.startswith("com.zcdeng.kss.") for k in labels), True)

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: cron bridge self-test passed ({len(labels)} launchd jobs in whitelist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
