"""scripts/morning_divergence_alert.py 单元测试.

覆盖：
- 快讯正文：divergence 格式化 / 动量期加重提示 / 无预警 → None
- state 文件去重
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "morning_divergence_alert",
    PROJECT_ROOT / "scripts" / "morning_divergence_alert.py")
mda = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mda)

from kss.sector.etf_radar import EtfRadar  # noqa: E402


def _radar(div: bool, in_regime: bool = False) -> EtfRadar:
    return EtfRadar(
        data_date="20260605",
        themes={"机器人": {"divergence": div, "past5_ret": 3.7, "flow_5d": 0.8},
                "芯片": {"divergence": False, "past5_ret": -1.0, "flow_5d": -3.0}},
        regime={"in_regime": in_regime, "mom20": 9.0} if in_regime else None,
    )


def test_message_with_divergence():
    msg = mda.build_alert_message(_radar(div=True))
    assert "见顶预警" in msg and "机器人" in msg
    assert "+3.7%" in msg and "+0.80%" in msg
    assert "胜率仅 38%" in msg
    assert "芯片" not in msg.split("历史")[0].replace("科创芯片", "")  # 非预警主题不列
    assert "动量期" not in msg


def test_message_momentum_aggravation():
    msg = mda.build_alert_message(_radar(div=True, in_regime=True))
    assert "动量期" in msg and "28-41%" in msg


def test_message_none_without_divergence():
    assert mda.build_alert_message(_radar(div=False)) is None


def test_state_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(mda, "STATE_FILE", tmp_path / "state")
    assert not mda.already_alerted("20260605")
    mda.mark_alerted("20260605")
    assert mda.already_alerted("20260605")
    assert not mda.already_alerted("20260608")  # 新日期不去重
