"""kss/macro/queries.py::detect_stage_transition + 模板 + sentinel 单测 (plan 011).

覆盖：

- ``detect_stage_transition`` 的 8 种 stage 转移分支
- ``render_transition_alert`` HTML 字符串格式
- ``scripts/update_macro_daily._check_and_send_alert`` 集成：
  - sentinel 同 transition 第二次跳过
  - 首次调用走 send_to_channels 并落 sentinel
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from kss.macro.queries import StageTransition, detect_stage_transition
from kss.notifications.templates.regime_transition import (
    STAGE_MEANING,
    render_transition_alert,
)


# ---------------------------------------------------------------------------- #
# fixtures
# ---------------------------------------------------------------------------- #


def _write_regime(
    path: Path,
    rows: list[tuple[str, str, float]],
) -> Path:
    """rows = [(trade_date, stage, confidence), ...] → 落 parquet."""
    df = pd.DataFrame(
        {
            "trade_date": [r[0] for r in rows],
            "stage": [r[1] for r in rows],
            "confidence": [r[2] for r in rows],
        }
    )
    df.to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------- #
# detect_stage_transition
# ---------------------------------------------------------------------------- #


def test_detect_transition_I_to_II(tmp_path):
    p = _write_regime(
        tmp_path / "regime.parquet",
        [("20260521", "I", 0.70), ("20260522", "II", 0.75)],
    )
    t = detect_stage_transition(parquet_path=p)
    assert t == StageTransition(
        from_stage="I",
        to_stage="II",
        confidence_delta=pytest.approx(0.05),
        as_of_date="20260522",
    )


def test_detect_transition_II_to_II_returns_none(tmp_path):
    p = _write_regime(
        tmp_path / "regime.parquet",
        [("20260521", "II", 0.70), ("20260522", "II", 0.80)],
    )
    assert detect_stage_transition(parquet_path=p) is None


def test_detect_transition_unknown_to_I_returns_none(tmp_path):
    """Unknown → 有效 stage 不告警（避免首次有效信号误报）."""
    p = _write_regime(
        tmp_path / "regime.parquet",
        [("20260521", "Unknown", 0.0), ("20260522", "I", 0.65)],
    )
    assert detect_stage_transition(parquet_path=p) is None


def test_detect_transition_I_to_unknown_returns_none(tmp_path):
    """降级到 Unknown 视为数据降级，不告警."""
    p = _write_regime(
        tmp_path / "regime.parquet",
        [("20260521", "I", 0.70), ("20260522", "Unknown", 0.0)],
    )
    assert detect_stage_transition(parquet_path=p) is None


def test_detect_transition_empty_parquet_returns_none(tmp_path):
    p = tmp_path / "regime.parquet"
    pd.DataFrame({"trade_date": [], "stage": [], "confidence": []}).to_parquet(
        p, index=False
    )
    assert detect_stage_transition(parquet_path=p) is None


def test_detect_transition_single_row_returns_none(tmp_path):
    p = _write_regime(
        tmp_path / "regime.parquet",
        [("20260522", "II", 0.75)],
    )
    assert detect_stage_transition(parquet_path=p) is None


def test_detect_transition_missing_file_returns_none(tmp_path):
    assert detect_stage_transition(parquet_path=tmp_path / "nope.parquet") is None


def test_detect_transition_uses_last_two_after_sort(tmp_path):
    """乱序输入也按 trade_date 末两行比较."""
    p = _write_regime(
        tmp_path / "regime.parquet",
        [
            ("20260522", "III", 0.80),    # 真末日
            ("20260520", "I", 0.60),
            ("20260521", "II", 0.70),     # 真倒数第二
        ],
    )
    t = detect_stage_transition(parquet_path=p)
    assert t is not None
    assert (t.from_stage, t.to_stage, t.as_of_date) == ("II", "III", "20260522")


def test_detect_transition_as_dict_round_trip(tmp_path):
    p = _write_regime(
        tmp_path / "regime.parquet",
        [("20260521", "III", 0.80), ("20260522", "IV", 0.60)],
    )
    t = detect_stage_transition(parquet_path=p)
    assert t is not None
    d = t.as_dict()
    assert d["from_stage"] == "III"
    assert d["to_stage"] == "IV"
    assert d["as_of_date"] == "20260522"
    assert d["confidence_delta"] == pytest.approx(-0.20)


# ---------------------------------------------------------------------------- #
# render_transition_alert
# ---------------------------------------------------------------------------- #


def test_render_alert_contains_core_fields():
    t = StageTransition(
        from_stage="I",
        to_stage="II",
        confidence_delta=0.05,
        as_of_date="20260522",
    )
    msg = render_transition_alert(t)
    assert "🔄" in msg
    assert "<b>宏观阶段切换</b>" in msg
    assert "I → II" in msg
    # 日期展开 YYYYMMDD → YYYY-MM-DD
    assert "2026-05-22" in msg
    # 置信度变化带正号
    assert "+0.05" in msg
    # 含义来自 STAGE_MEANING
    assert STAGE_MEANING["II"] in msg
    # 建议非空
    assert "建议:" in msg


def test_render_alert_negative_delta():
    t = StageTransition(
        from_stage="III",
        to_stage="IV",
        confidence_delta=-0.20,
        as_of_date="20260601",
    )
    msg = render_transition_alert(t)
    assert "III → IV" in msg
    assert "-0.20" in msg
    assert STAGE_MEANING["IV"] in msg


# ---------------------------------------------------------------------------- #
# scripts/update_macro_daily._check_and_send_alert 集成
# ---------------------------------------------------------------------------- #


@pytest.fixture
def update_macro_module(monkeypatch, tmp_path):
    """加载 scripts/update_macro_daily.py 并把 sentinel + parquet 重定向到 tmp."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "update_macro_daily.py"

    spec = importlib.util.spec_from_file_location(
        "_test_update_macro_daily", script_path
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    sentinel_path = tmp_path / "sentinel.txt"
    parquet_path = tmp_path / "regime.parquet"

    # 重定向脚本里捕获的 sentinel 路径常量
    monkeypatch.setattr(mod, "REGIME_ALERT_SENTINEL", sentinel_path)

    # detect_stage_transition 读默认 REGIME_PARQUET → 改成 tmp 的 parquet.
    # 局部 wrapper 避免污染其他测试.
    from kss.macro import queries as macro_queries

    real_detect = macro_queries.detect_stage_transition

    def _detect_default_tmp(parquet_path_arg=None):
        return real_detect(parquet_path=parquet_path_arg or parquet_path)

    monkeypatch.setattr(mod, "detect_stage_transition", _detect_default_tmp)

    return mod, parquet_path, sentinel_path


def test_check_and_send_alert_writes_sentinel_once(
    update_macro_module, monkeypatch
):
    """transition 存在 → 首次推送成功 + 写 sentinel；同 transition 第二次跳过."""
    mod, parquet_path, sentinel_path = update_macro_module
    _write_regime(
        parquet_path,
        [("20260521", "I", 0.70), ("20260522", "II", 0.75)],
    )

    calls: list[dict] = []

    def fake_send_to_channels(message, channel, title=None, parse_mode="Markdown"):
        calls.append(
            {
                "message": message,
                "channel": channel,
                "title": title,
                "parse_mode": parse_mode,
            }
        )
        return {"telegram": True}

    # send_to_channels 是函数内 import，monkeypatch 真实模块.
    import kss.notifications.manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "send_to_channels", fake_send_to_channels)

    # 1st call: should send + write sentinel
    mod._check_and_send_alert()
    assert len(calls) == 1
    assert calls[0]["channel"] == "telegram"
    assert calls[0]["parse_mode"] == "HTML"
    assert calls[0]["title"] == "宏观阶段切换 2026-05-22"
    assert "I → II" in calls[0]["message"]
    assert sentinel_path.exists()
    assert sentinel_path.read_text(encoding="utf-8") == "20260522|I|II"

    # 2nd call: sentinel hit → no new call
    mod._check_and_send_alert()
    assert len(calls) == 1, "同 transition 第二次应当被 sentinel 拦截"


def test_check_and_send_alert_no_transition_no_call(
    update_macro_module, monkeypatch
):
    """II → II 无切换 → 不推 + 不写 sentinel."""
    mod, parquet_path, sentinel_path = update_macro_module
    _write_regime(
        parquet_path,
        [("20260521", "II", 0.70), ("20260522", "II", 0.80)],
    )

    calls: list[dict] = []

    def fake_send_to_channels(**kwargs):
        calls.append(kwargs)
        return {"telegram": True}

    import kss.notifications.manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "send_to_channels", fake_send_to_channels)

    mod._check_and_send_alert()
    assert calls == []
    assert not sentinel_path.exists()


def test_check_and_send_alert_send_failure_keeps_old_sentinel(
    update_macro_module, monkeypatch
):
    """推送全失败 → 不写 sentinel（让下次 cron 重试）."""
    mod, parquet_path, sentinel_path = update_macro_module
    _write_regime(
        parquet_path,
        [("20260521", "II", 0.70), ("20260522", "III", 0.75)],
    )

    def fake_send_to_channels(**kwargs):
        return {"telegram": False}

    import kss.notifications.manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "send_to_channels", fake_send_to_channels)

    mod._check_and_send_alert()
    assert not sentinel_path.exists()
