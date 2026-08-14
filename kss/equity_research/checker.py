"""A/港口径检查器：CAS / HKFRS、扣非、业绩预告/盈利警告。禁止 US GAAP vs Non-GAAP 必过。"""

from __future__ import annotations

from typing import Any


def run_checker(
    *,
    suffix: str,
    fundamentals: dict[str, Any] | None = None,
    excerpts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    hk = suffix.upper() == ".HK"
    framework = "HKFRS" if hk else "CAS"
    fund = fundamentals or {}
    profit_dedt = fund.get("profit_dedt")
    if profit_dedt is None or profit_dedt == "":
        profit_dedt = "未获取到"
    warning_key = "盈利警告" if hk else "业绩预告"
    warning_val = fund.get("profit_warning")
    if warning_val is None or warning_val == "":
        warning_val = "未获取到"
    kpis = {
        "profit_dedt": profit_dedt if not hk else "不适用",
        warning_key: warning_val,
    }
    if hk:
        kpis.pop("profit_dedt", None)
        kpis["扣非"] = "不适用"
    grade = str(fund.get("quality_grade") or "B")
    return {
        "framework": framework,
        "us_gaap_non_gaap_required": False,
        "kpis": kpis,
        "quality_grade": grade,
        "excerpt_count": len(excerpts or []),
    }
