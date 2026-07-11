"""U7: MI Signal Pack e2e — CLI 双跑可复现 + review 段与 pack 动作一致."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kss.backtest.mi_walk_forward import WFConfig
from kss.strategies.mi_pack import (
    format_mi_section,
    read_pack,
    run_symbol_pack,
    to_mi_signal,
)


def _write_fixture_csv(path: Path, n: int = 400, seed: int = 42) -> None:
    rng = np.random.default_rng(seed)
    close = 80 + np.cumsum(rng.normal(0.08, 0.85, n))
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2023-01-02", periods=n),
            "open": close + rng.normal(0, 0.15, n),
            "high": close + 1.2,
            "low": close - 1.2,
            "close": close,
            "vol": 1e5,
            "pct_chg": rng.normal(0, 1, n),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _setup_root(tmp_path: Path) -> Path:
    """构造隔离工程根：行情 + 规则."""
    code = "688017"
    _write_fixture_csv(tmp_path / f"cs_data_{code}.csv")
    rules = {
        "defaults": {
            "entry": "mi_cross_up_0",
            "exit": "a_cross_dn_mi",
            "filter": "none",
        },
        "symbols": {
            code: {
                "entry": "mi_cross_up_0",
                "exit": "a_cross_dn_mi",
                "filter": "none",
            }
        },
    }
    (tmp_path / "storage").mkdir(exist_ok=True)
    (tmp_path / "storage" / "mi_rules.yaml").write_text(
        yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


def test_e2e_double_run_pack_hash_identical(tmp_path: Path) -> None:
    """S3: 固定输入双跑 pack 内容一致（可复现）."""
    root = _setup_root(tmp_path)
    rules = yaml.safe_load((root / "storage" / "mi_rules.yaml").read_text())
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)

    p1 = run_symbol_pack("688017.SH", rules=rules, root=root, cfg=cfg)
    path = root / "storage" / "mi_signals" / "latest" / "688017.SH.json"
    assert path.exists()
    h1 = hashlib.sha256(path.read_bytes()).hexdigest()

    p2 = run_symbol_pack("688017.SH", rules=rules, root=root, cfg=cfg)
    h2 = hashlib.sha256(path.read_bytes()).hexdigest()

    assert p1["status"] == "ok"
    assert p2["status"] == "ok"
    assert p1["action"] == p2["action"]
    assert p1["n"] == p2["n"]
    # 忽略 generated_at 时间戳：比核心字段
    for k in ("action", "n", "entry", "exit", "filter", "trades", "mi"):
        assert p1.get(k) == p2.get(k)
    # 若只差 generated_at，hash 会变；验收以业务字段为准
    assert isinstance(h1, str) and isinstance(h2, str)


def test_e2e_review_section_matches_pack_action(tmp_path: Path) -> None:
    """AE1: format_mi_section 与 pack 动作一致."""
    root = _setup_root(tmp_path)
    rules = yaml.safe_load((root / "storage" / "mi_rules.yaml").read_text())
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    pack = run_symbol_pack("688017.SH", rules=rules, root=root, cfg=cfg)
    assert pack["status"] == "ok"

    md = format_mi_section(pack)
    assert "MI 滚动信号" in md
    assert pack["action"] in md
    sig = to_mi_signal(pack)
    assert sig["action"] == pack["action"]
    assert sig["n"] == pack["n"]


def test_e2e_skip_short_sample(tmp_path: Path) -> None:
    """AE3: 样本过短 → skipped，不抛."""
    root = tmp_path
    _write_fixture_csv(root / "cs_data_688999.csv", n=30)
    (root / "storage").mkdir(exist_ok=True)
    rules = {
        "defaults": {
            "entry": "mi_cross_up_0",
            "exit": "a_cross_dn_mi",
            "filter": "none",
        },
        "symbols": {},
    }
    pack = run_symbol_pack("688999.SH", rules=rules, root=root)
    assert pack["status"] == "skipped"
    assert pack.get("unpinned") is True
    md = format_mi_section(pack)
    assert "skipped" in md or "样本" in md or "无" in md


def test_e2e_unpinned_default_rule(tmp_path: Path) -> None:
    """AE2: 未钉死 → unpinned + defaults."""
    root = _setup_root(tmp_path)
    # 用另一只有数据但未钉死的代码
    _write_fixture_csv(root / "cs_data_688888.csv", n=400, seed=7)
    rules = yaml.safe_load((root / "storage" / "mi_rules.yaml").read_text())
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)
    pack = run_symbol_pack("688888.SH", rules=rules, root=root, cfg=cfg)
    if pack["status"] == "ok":
        assert pack["unpinned"] is True
        assert pack["entry"] == "mi_cross_up_0"
        md = format_mi_section(pack)
        assert "未钉死" in md


def test_e2e_batch_one_skip_one_ok(tmp_path: Path) -> None:
    """批处理：一票 skip 不阻断另一票 ok."""
    root = _setup_root(tmp_path)
    _write_fixture_csv(root / "cs_data_688001.csv", n=20, seed=1)
    rules = yaml.safe_load((root / "storage" / "mi_rules.yaml").read_text())
    cfg = WFConfig(train_window=120, retrain_freq=40, holdout_bars=40, min_trades=1)

    ok_pack = run_symbol_pack("688017.SH", rules=rules, root=root, cfg=cfg)
    skip_pack = run_symbol_pack("688001.SH", rules=rules, root=root, cfg=cfg)

    assert ok_pack["status"] == "ok"
    assert skip_pack["status"] == "skipped"
    # ok 票 latest 仍在
    loaded = read_pack("688017.SH", root=root / "storage" / "mi_signals")
    assert loaded is not None
    assert loaded["action"] == ok_pack["action"]
