"""固化报告生成单测."""

from __future__ import annotations

from kss.indicators.registry import KIND_PRIMITIVE, RegistryEntry
from kss.indicators.report import format_report


def _entry() -> RegistryEntry:
    return RegistryEntry(
        id="ma_cross_abc123",
        name="ma_cross（{'fast': 5, 'slow': 20}）",
        kind=KIND_PRIMITIVE,
        family="ma_cross",
        params={"fast": 5, "slow": 20, "kind": "sma"},
        rules_path="storage/indicator_rules/ma_cross_abc123.yaml",
        signals_dir="storage/indicator_signals/ma_cross_abc123",
        solidified_at="2026-07-12T10:00:00",
    )


def _verdict_payload(go: bool = True) -> dict:
    return {
        "family": "ma_cross",
        "params": {"fast": 5, "slow": 20, "kind": "sma"},
        "symbols": ["688017.SH"],
        "go": go,
        "results": [
            {
                "symbol": "688017.SH",
                "status": "judged",
                "go": go,
                "dimensions": [
                    {
                        "name": "经济意义",
                        "passed": go,
                        "value": {"strategy_sharpe": 1.2, "strategy_total": 0.15, "buy_and_hold_total": 0.1},
                        "detail": "策略总收益 15.00%（夏普 1.20）vs buy&hold 10.00%",
                    },
                    {"name": "稳健", "passed": go, "value": {}, "detail": "网格 8 组合中还有 2 组同样正收益"},
                    {"name": "可交易", "passed": go, "value": {}, "detail": "共 5 笔交易"},
                    {"name": "可解释", "passed": True, "value": {}, "detail": "均线交叉规则"},
                    {"name": "运维", "passed": True, "value": {}, "detail": "可批跑"},
                ],
            }
        ],
    }


def _packs() -> list[dict]:
    return [
        {"symbol": "688017.SH", "status": "ok", "action": "BUY", "trades": [{"trade_return": 0.02}] * 5}
    ]


def test_format_report_contains_go_and_metrics() -> None:
    md = format_report(_entry(), _packs(), _verdict_payload(go=True))
    assert "ma_cross_abc123" in md
    assert "Sharpe" in md
    assert "GO/NO-GO" in md
    assert "**总裁决：GO**" in md
    assert "688017.SH" in md


def test_format_report_no_go() -> None:
    md = format_report(_entry(), _packs(), _verdict_payload(go=False))
    assert "**总裁决：NO-GO**" in md


def test_format_report_without_verdict_payload() -> None:
    md = format_report(_entry(), _packs(), None)
    assert "GO/NO-GO" not in md
    assert "标的与信号" in md
    assert "688017.SH" in md


def test_format_report_skips_non_judged_results() -> None:
    payload = _verdict_payload(go=True)
    payload["results"].append({"symbol": "688999.SH", "status": "skipped", "reason": "样本过短"})
    md = format_report(_entry(), _packs(), payload)
    assert "688999.SH" not in md.split("## 标的与信号")[0]
