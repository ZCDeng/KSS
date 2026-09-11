"""U4/U5: HiThink special-data mappers keep existing column contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from kss.data.hithink_special import map_dragon_tiger, map_ths_hot
from kss.sector.commentary import _dragon_tiger_summary, _hot_reason_tags, render_dragon_tiger_line
from kss.sector.data_fetcher import SectorSnapshot

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hithink"


def _limit_up_items() -> list[dict]:
    path = _FIXTURE / "limit_up_pool.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("item") or []
        if items:
            return items
    return [
        {
            "thscode": "688008.SH",
            "ticker": "688008",
            "name": "澜起科技",
            "last_price": 80.1,
            "price_change_ratio_pct": 9.98,
            "limit_up_time": "09:31",
            "limit_up_reason": "算力租赁+Token工厂",
            "continue_day_cnt": 1,
            "seal_money": 1.2e8,
        }
    ]


def _anomaly() -> dict:
    path = _FIXTURE / "anomaly_analysis_list.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "item": [
            {
                "thscode": "300033.SZ",
                "stock_name": "同花顺",
                "keyword_list": ["AI应用", "金融科技"],
                "analysis_content": "软件股高开",
            }
        ]
    }


def test_map_ths_hot_columns_and_reason_tags() -> None:
    items = _limit_up_items()
    df = map_ths_hot(items, _anomaly())
    assert df is not None
    assert {"code", "name", "reason", "pct_change", "close"}.issubset(df.columns)
    # Live 2026-09-11 fixture: official limit_up_reason uses + joins.
    if (df["code"] == "000993").any():
        row = df.loc[df["code"] == "000993"].iloc[0]
        assert row["reason"] == "清洁能源+海上风电+福建国资"
        assert abs(float(row["pct_change"]) - 9.9842) < 1e-6
    snap = SectorSnapshot(trade_date="20260911", ths_hot=df)
    tags = _hot_reason_tags(snap, 8)
    assert tags, "reason must split into tags"
    assert all("+" not in t["tag"] for t in tags)


def test_map_ths_hot_empty_is_none() -> None:
    assert map_ths_hot([], None) is None


def _lhb_payload() -> dict:
    path = _FIXTURE / "dragon_tiger_list.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("stock_items"):
            return data
    return {
        "stock_items": [
            {
                "thscode": "688507.SH",
                "ticker": "688507",
                "name": "索辰科技",
                "buy_value": 8.18e8,
                "sell_value": 3.00e8,
                "net_value": 5.18e8,
                "limit_reason": "日涨幅偏离值达7%的证券",
                "concept_list": ["工业软件"],
            },
            {
                "thscode": "002119.SZ",
                "ticker": "002119",
                "name": "康强电子",
                "buy_value": 1.0e8,
                "sell_value": 4.70e8,
                "net_value": -3.70e8,
                "limit_reason": "日换手率达20%的证券",
            },
        ]
    }


def test_map_dragon_tiger_net_amount_yuan_and_summary() -> None:
    df = map_dragon_tiger(_lhb_payload())
    assert df is not None
    assert {"code", "name", "net_amount", "reason"}.issubset(df.columns)
    # Fixture / synthetic 5.18e8 yuan must stay yuan (commentary divides by 1e8).
    top = df.iloc[0]
    assert abs(float(top["net_amount"])) >= 1e6
    snap = SectorSnapshot(trade_date="20260911", dragon_tiger=df)
    summary = _dragon_tiger_summary(snap)
    assert summary is not None
    line = render_dragon_tiger_line(summary)
    assert line is not None
    expected_yi = round(float(df.loc[df["net_amount"] > 0, "net_amount"].sum()) / 1e8, 2)
    assert f"{expected_yi:+.2f}" in line
    assert summary["net_buy_total_yi"] == expected_yi
    reasons = " ".join(str(v) for v in df["reason"].tolist())
    assert "{" not in reasons
    # Live 2026-09-11 fixture: concept_list is ``{name: ...}``, no limit_reason.
    if (df["code"] == "000759").any():
        row = df.loc[df["code"] == "000759"].iloc[0]
        assert abs(float(row["net_amount"]) - 67123708.26) < 1e-6
        assert row["reason"] == "预制菜+免税店+农业种植"


def test_map_dragon_tiger_concept_list_objects() -> None:
    payload = {
        "stock_items": [
            {
                "ticker": "000001",
                "name": "平安银行",
                "net_value": 1.5e8,
                "concept_list": [{"name": "银行"}, {"name": "高股息"}],
            }
        ]
    }
    df = map_dragon_tiger(payload)
    assert df is not None
    assert df.iloc[0]["reason"] == "银行+高股息"


def test_map_dragon_tiger_prefers_official_net_value() -> None:
    payload = {
        "stock_items": [
            {
                "ticker": "600000",
                "name": "浦发银行",
                "buy_value": 10,
                "sell_value": 1,
                "net_value": 42.0,
                "limit_reason": "涨幅偏离",
            }
        ]
    }
    df = map_dragon_tiger(payload)
    assert df is not None
    assert float(df.iloc[0]["net_amount"]) == 42.0
