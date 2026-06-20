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

    # ---- 漏跑判定（_fire_times / _missed_cycles）：固定 now，不依赖真实时间 ----
    from datetime import datetime
    # 2026-06-20 是周六；工作日 08:30 任务最近一次预定 = 周五 06-19 08:30，下次 = 周一 06-22 08:30
    now = datetime(2026, 6, 20, 15, 0)
    wd_interval = [{"Weekday": w, "Hour": 8, "Minute": 30} for w in range(1, 6)]
    exp, nxt = b._fire_times(wd_interval, now)
    check("fire-expected", exp, datetime(2026, 6, 19, 8, 30))
    check("fire-next-skips-weekend", nxt, datetime(2026, 6, 22, 8, 30))
    # 周六同日的「每天 12:00」：expected = 当天 12:00（已过），next = 次日 12:00
    daily = {"Hour": 12, "Minute": 0}
    exp2, nxt2 = b._fire_times(daily, now)
    check("daily-expected-today", exp2, datetime(2026, 6, 20, 12, 0))
    check("daily-next-tomorrow", nxt2, datetime(2026, 6, 21, 12, 0))
    # launchd Weekday 0/7 = 周日，对齐 isoweekday 7
    sun = b._entry_dt_on({"Weekday": 0, "Hour": 9, "Minute": 0}, datetime(2026, 6, 21).date())  # 06-21 是周日
    check("weekday-0-is-sunday", sun, datetime(2026, 6, 21, 9, 0))
    # 漏跑次数：上次运行在周三，到周六 15:00 应触发周四、周五两次
    missed = b._missed_cycles(wd_interval, now, datetime(2026, 6, 17, 8, 32))
    check("missed-cycles-2", missed, 2)
    # 无调度（selfcheck 之类 RunAtLoad-only）→ 无 expected/next，0 漏跑
    check("no-interval-fire", b._fire_times(None, now), (None, None))
    check("no-interval-missed", b._missed_cycles(None, now, None), 0)

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: cron bridge self-test passed ({len(labels)} launchd jobs in whitelist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
