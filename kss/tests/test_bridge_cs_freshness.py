"""cs_data 新鲜度自检（selfcheck 看门狗数据线）.

背景：并行会话 git restore 旧 stash 冲掉根目录 cs_data_*.csv，自选价格静默停留
数月无人发现。检查：自选每只票 cs_data_<code>.csv 的 max(trade_date) 落后应有
日线日 >1 个交易日 → selfcheck fail（App 横幅）+ cs-freshness notify 推 Telegram。

- 宽限恰好 1 个交易日：单次漏跑 8:30 日更不告警（交给 catchup 恢复）。
- 缺文件 / 坏文件与陈旧同罪。
- 自选为空 / 库不可用 → 跳过不告警（不是数据事故）。

跑：uv run pytest kss/tests/test_bridge_cs_freshness.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kss_app_bridge as b  # noqa: E402

from kss.storage.watchlist import set_watchlist  # noqa: E402

# 合成日历：周四五 + 下周一二三开市（覆盖跨周末回看）。
_OPEN_DAYS = {"20260716", "20260717", "20260720", "20260721", "20260722"}
_REFERENCE = "2026-07-22"  # 应有日线日（周三）；宽限阈值 = 上一交易日 2026-07-21

_HEADER = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,"
    "vol,amount,turnover_rate,volume_ratio,pe,pb,total_mv"
)


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(b, "STATE_ROOT", tmp_path)
    (tmp_path / "storage").mkdir()
    monkeypatch.setattr(b, "_is_trade_day", lambda d: d in _OPEN_DAYS)
    monkeypatch.setattr(b, "_reference_trade_date", lambda *a, **k: _REFERENCE)
    return tmp_path


def _write_cs(root: Path, symbol: str, dates: list[str]) -> None:
    """按真实列结构写最小 cs_data CSV（文件名无交易所后缀，行内 ts_code 带）。"""
    code = symbol.split(".")[0]
    rows = [f"{symbol},{d},1,1,1,1,1,0,0,1,1,1,1,1,1,1" for d in dates]
    (root / f"cs_data_{code}.csv").write_text(
        "\n".join([_HEADER, *rows]) + "\n", encoding="utf-8"
    )


def _set_watchlist(root: Path, symbols: list[str]) -> None:
    set_watchlist(symbols, db_path=root / "storage" / "kss.db")


# --------------------------------------------------------------------------- #
# ① 交易日回看
# --------------------------------------------------------------------------- #


def test_prev_trade_day_skips_weekend(state_root: Path) -> None:
    assert b._prev_trade_day("20260720") == "20260717"  # 周一 → 上周五
    assert b._prev_trade_day("20260722") == "20260721"


# --------------------------------------------------------------------------- #
# ② 核心判定：>1 个交易日才算陈旧
# --------------------------------------------------------------------------- #


def test_fresh_when_max_date_is_reference(state_root: Path) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-21", "2026-07-22"])
    r = b._cs_data_freshness()
    assert r["ok"] is True
    assert r["stale"] == []
    assert r["checked"] == 1


def test_lag_of_one_trading_day_is_tolerated(state_root: Path) -> None:
    """单次漏跑 8:30 日更的正常窗口——不告警，交给 catchup。"""
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-21"])
    assert b._cs_data_freshness()["ok"] is True


def test_lag_of_two_trading_days_is_stale(state_root: Path) -> None:
    _set_watchlist(state_root, ["688017.SH", "688322.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-20"])
    _write_cs(state_root, "688322.SH", ["2026-07-22"])
    r = b._cs_data_freshness()
    assert r["ok"] is False
    assert r["threshold"] == "2026-07-21"
    assert [s["symbol"] for s in r["stale"]] == ["688017.SH"]
    assert r["stale"][0] == {"symbol": "688017.SH", "maxDate": "2026-07-20", "reason": "stale"}


def test_stash_restore_incident_months_behind(state_root: Path) -> None:
    """复现原始事故：文件被旧 stash 冲回数月前。"""
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-04-09"])
    r = b._cs_data_freshness()
    assert r["ok"] is False
    assert r["stale"][0]["maxDate"] == "2026-04-09"


def test_missing_file_counts_as_stale(state_root: Path) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    r = b._cs_data_freshness()
    assert r["ok"] is False
    assert r["stale"][0] == {"symbol": "688017.SH", "maxDate": None, "reason": "missing"}


def test_corrupt_csv_counts_as_stale(state_root: Path) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    (state_root / "cs_data_688017.csv").write_bytes(b"\x00\xff not a csv")
    assert b._cs_data_freshness()["ok"] is False


def test_empty_watchlist_skips_without_alert(state_root: Path) -> None:
    r = b._cs_data_freshness()
    assert r["ok"] is True
    assert r["skipped"] is True


# --------------------------------------------------------------------------- #
# ③ selfcheck 项（App 横幅路径）：stale → fail 才弹横幅
# --------------------------------------------------------------------------- #


def test_selfcheck_item_ok_when_fresh(state_root: Path) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-22"])
    item = b._check_cs_data_freshness()
    assert item["item"] == "cs_data"
    assert item["status"] == "ok"


def test_selfcheck_item_fail_names_symbols(state_root: Path) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-09"])
    item = b._check_cs_data_freshness()
    assert item["status"] == "fail"
    assert "688017.SH" in item["detail"]
    assert "2026-07-09" in item["detail"]
    assert "update-cs-data" in item["fixHint"]


def test_self_check_includes_cs_data_item(state_root: Path) -> None:
    result = b._self_check()
    assert "cs_data" in {item["item"] for item in result["items"]}


# --------------------------------------------------------------------------- #
# ④ cs-freshness 命令（看门狗 Telegram 路径）
# --------------------------------------------------------------------------- #


def test_command_registered() -> None:
    assert "cs-freshness" in b.COMMANDS
    # notify 外发 Telegram 消息属副作用，按写命令归类（MCP paper-only 闸拦得住）。
    assert "cs-freshness" in b.WRITE_COMMANDS


def test_notify_pushes_telegram_when_stale(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-09"])
    sent: list[tuple[str, str, str | None]] = []

    def fake_send(message: str, channel: str, title: str | None = None, **kw):  # noqa: ANN202
        sent.append((message, channel, title))
        return {"telegram": True}

    monkeypatch.setattr("kss.notifications.manager.send_to_channels", fake_send)
    r = b._cs_freshness_cmd(notify=True)
    assert r["notified"] is True
    message, channel, title = sent[0]
    assert channel == "telegram"
    assert "688017.SH" in message
    assert _REFERENCE in message
    assert "陈旧" in (title or "")


def test_notify_silent_when_fresh(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_watchlist(state_root, ["688017.SH"])
    _write_cs(state_root, "688017.SH", ["2026-07-22"])
    monkeypatch.setattr(
        "kss.notifications.manager.send_to_channels",
        lambda *a, **k: pytest.fail("fresh 不该推送"),
    )
    r = b._cs_freshness_cmd(notify=True)
    assert r["ok"] is True
    assert r["notified"] is False


def test_dispatch_without_notify_never_sends(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_watchlist(state_root, ["688017.SH"])  # 缺文件 → stale，但无 notify 不推
    monkeypatch.setattr(
        "kss.notifications.manager.send_to_channels",
        lambda *a, **k: pytest.fail("无 notify 不该推送"),
    )
    r = b.dispatch("cs-freshness", [])
    assert r["ok"] is False
    assert r["notified"] is False
