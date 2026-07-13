"""Signal Pack I/O 与投影."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kss.backtest.mi_walk_forward import WFConfig
from kss.strategies.mi_pack import (
    build_pack_from_wf,
    format_mi_section,
    load_rules,
    read_pack,
    resolve_rule,
    run_symbol_pack,
    to_mi_overlay,
    to_mi_signal,
    write_pack,
)
from kss.backtest.mi_walk_forward import WFResult


def test_resolve_unpinned(tmp_path: Path) -> None:
    rules = {
        "defaults": {"entry": "mi_cross_up_0", "exit": "a_cross_dn_mi", "filter": "none"},
        "symbols": {"688017": {"entry": "mi_cross_up_0", "exit": "a_cross_dn_mi", "filter": "none"}},
    }
    e, x, f, unp = resolve_rule("688999.SH", rules)
    assert unp is True
    e2, _, _, unp2 = resolve_rule("688017.SH", rules)
    assert unp2 is False
    assert e2 == "mi_cross_up_0"


def test_write_read_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "kss.db"
    pack = {
        "schema_version": 1,
        "symbol": "688017.SH",
        "asof": "2026-07-10",
        "status": "ok",
        "reason": "test",
        "n": 12,
        "thr": {},
        "entry": "mi_cross_up_0",
        "exit": "a_cross_dn_mi",
        "filter": "none",
        "unpinned": False,
        "action": "STAY_FLAT",
        "trades": [],
        "trades_preview": [],
        "mi_series": [],
        "param_history": [],
        "param_delta": {},
    }
    write_pack(pack, db_path=db_path)
    loaded = read_pack("688017.SH", db_path=db_path)
    assert loaded is not None
    assert loaded["n"] == 12
    assert to_mi_signal(loaded)["action"] == "STAY_FLAT"
    ov = to_mi_overlay(loaded)
    assert ov is not None
    assert ov["status"] == "ok"


def test_read_pack_uses_state_root_env(tmp_path: Path, monkeypatch) -> None:
    """bundle 模式：代码在 Resources，pack 在 KSS_STATE_ROOT/storage/kss.db。"""
    import json

    from kss.strategies import mi_pack as mp
    from kss.storage.db import connect, ensure_schema

    state = tmp_path / "state"
    db_path = state / "storage" / "kss.db"
    pack = {
        "schema_version": 1,
        "symbol": "688017.SH",
        "asof": "2026-07-10",
        "status": "ok",
        "action": "STAY_FLAT",
        "n": 20,
    }
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO mi_signal_packs (asof, symbol, payload_json, created_at) VALUES (?,?,?,?)",
            (pack["asof"], pack["symbol"], json.dumps(pack), None),
        )
    monkeypatch.setenv("KSS_STATE_ROOT", str(state))
    # 模拟代码根 ≠ 状态根
    monkeypatch.setenv("KSS_PROJECT_ROOT", str(tmp_path / "code_only"))
    loaded = mp.read_pack("688017.SH")
    assert loaded is not None
    assert loaded["action"] == "STAY_FLAT"
    assert mp.state_root() == state


def test_format_unpinned_section() -> None:
    pack = {
        "status": "ok",
        "unpinned": True,
        "action": "BUY",
        "position": "FLAT",
        "pred_score": 0.1,
        "pred_bias": "neutral",
        "reason": "test",
        "n": 12,
        "thr": {},
        "entry": "mi_cross_up_0",
        "exit": "a_cross_dn_mi",
        "filter": "none",
        "asof": "2026-07-10",
        "close": 1.0,
        "exec_note": "note",
        "trades_preview": [],
        "param_delta": {"n": {"from": 12, "to": 9}},
    }
    md = format_mi_section(pack)
    assert "未钉死" in md
    assert "相对上期" in md


def test_run_symbol_pack_fixture(tmp_path: Path) -> None:
    # 构造行情 CSV
    n = 400
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0.1, 0.9, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "vol": 1e5,
        }
    )
    csv = tmp_path / "cs_data_688017.csv"
    df.to_csv(csv, index=False)
    rules = {
        "defaults": {"entry": "mi_cross_up_0", "exit": "a_cross_dn_mi", "filter": "none"},
        "symbols": {
            "688017": {
                "entry": "mi_cross_up_0",
                "exit": "a_cross_dn_mi",
                "filter": "none",
            }
        },
    }
    (tmp_path / "storage").mkdir()
    yaml.safe_dump(rules, open(tmp_path / "storage" / "mi_rules.yaml", "w"))
    # monkeypath via root=tmp_path — run_symbol_pack loads rules from project; pass rules=
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    pack = run_symbol_pack(
        "688017.SH", rules=rules, root=tmp_path, cfg=cfg
    )
    assert pack["symbol"] == "688017.SH"
    assert pack["status"] in ("ok", "skipped", "error")
    import sqlite3

    conn = sqlite3.connect(tmp_path / "storage" / "kss.db")
    row = conn.execute(
        "SELECT 1 FROM mi_signal_packs WHERE symbol=?", ("688017.SH",)
    ).fetchone()
    conn.close()
    assert row is not None or pack["status"] != "ok"
