#!/usr/bin/env python3
"""趋势页 bridge 读取逻辑纯逻辑自检（无第三方依赖，不触发副作用）。

覆盖：月度聚合形状、单日明细、缺归档空态、日期参数注入/路径穿越防护。
用法： python3 scripts/selftest_trends_bridge.py   （退出码非 0 即失败）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kss_app_bridge as b  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # 日期参数白名单：注入/穿越一律拒绝，绝不读任意文件
    for bad in ["../etc/passwd", "2026-06; rm -rf /", "2026-6", "2026-06-18x", ""]:
        r = b._trends_day(bad)
        check(f"reject-day:{bad!r}", r.get("found") is False and "error" in r)
    for bad in ["../macro", "2026-6", "2026-06-18", "2026"]:
        r = b._trends_month(bad)
        check(f"reject-month:{bad!r}", r.get("days") == [] and "error" in r)

    # 合法格式：月返回 dict 含 days 列表；缺归档时 days=[] 不抛
    rm = b._trends_month("1990-01")
    check("month-shape", isinstance(rm, dict) and isinstance(rm.get("days"), list))

    # 合法单日缺归档 → found False 不报 error（区别于格式错）
    rd = b._trends_day("1990-01-01")
    check("day-missing", rd.get("found") is False and "error" not in rd)

    # 若本机已有真实归档，校验月度格子字段齐全（驱动热力格三标记）
    real = sorted(b._TRENDS_DIR.glob("2026-*.json")) if b._TRENDS_DIR.exists() else []
    if real:
        month = real[-1].stem[:7]
        rm2 = b._trends_month(month)
        check("real-month-nonempty", len(rm2["days"]) > 0)
        day0 = rm2["days"][0]
        check("cell-fields", all(k in day0 for k in ("date", "heat", "flags", "hasData", "recAvgFwd")))
        rd2 = b._trends_day(real[-1].stem)
        check("real-day-found", rd2.get("found") is True and "recs" in rd2)

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: trends bridge self-test passed ({len(real)} real archives present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
