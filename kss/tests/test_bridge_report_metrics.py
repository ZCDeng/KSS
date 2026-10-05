"""回测报告指标解析：表头行不能被当成指标值。

跑：uv run pytest kss/tests/test_bridge_report_metrics.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402


def test_column_header_pairs_with_first_data_row() -> None:
    text = (
        "| 策略 | Sharpe | 年化 | 最大回撤 |\n"
        "|---|---|---|---|\n"
        "| log_mv | 1.93 | 83.31% | -41.45% |\n"
        "| other | 1.00 | 10% | -5% |\n"
    )
    assert b._report_metrics(text) == [
        {"name": "Sharpe", "value": "1.93"},
        {"name": "年化", "value": "83.31%"},
        {"name": "最大回撤", "value": "-41.45%"},
    ]


def test_row_wise_metric_table_keeps_names_from_first_column() -> None:
    text = "| 指标 | 值 |\n|---|---|\n| Sharpe | 1.93 |\n| 年化 | 83.31% |\n"
    assert b._report_metrics(text) == [
        {"name": "Sharpe", "value": "1.93"},
        {"name": "年化", "value": "83.31%"},
    ]


def test_header_names_never_become_values() -> None:
    text = "| 组 | n | 后5日均值 | 后5日胜率 |\n|---|---|---|---|\n"
    assert all("|" not in metric["value"] for metric in b._report_metrics(text))
