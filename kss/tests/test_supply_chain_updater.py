"""紫苏叶产业链动态更新器测试."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from kss.supply_chain.updater import (
    check_staleness,
    check_demand_chain_health,
    count_analyst_coverage,
    refresh_analyst_counts,
    _STALENESS_DAYS,
)


def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _minimal_yaml(updated: str = "2026-06-02", stocks: dict | None = None) -> dict:
    return {
        "version": 1,
        "updated": updated,
        "scoring_weights": {"layer": 0.25, "moat": 0.35, "lock": 0.25, "coverage_gap": 0.15},
        "moat_tiers": {1: 1.0, 2: 0.7, 3: 0.3, "default": 0.0},
        "ranking_multiplier": 0.3,
        "stocks": stocks or {
            "688012.SH": {
                "name": "中微公司", "chain_layer": 4, "chain_role": "equipment",
                "n_competitors_global": 3, "n_competitors_domestic": 1,
                "substitutability": "medium", "expansion_cycle_years": 3,
                "demand_locked": True, "analyst_count": 20,
                "demand_chains": ["半导体"],
            },
        },
    }


# ────────────────────────────────────────────────────────────────
# check_staleness
# ────────────────────────────────────────────────────────────────

class TestCheckStaleness:
    def test_fresh_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml(datetime.now().strftime("%Y-%m-%d")))
        result = check_staleness(p)
        assert not result["is_stale"]
        assert result["age_days"] <= 1
        assert result["stocks_count"] == 1

    def test_stale_yaml(self, tmp_path: Path) -> None:
        old = (datetime.now() - timedelta(days=_STALENESS_DAYS + 10)).strftime("%Y-%m-%d")
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml(old))
        result = check_staleness(p)
        assert result["is_stale"]
        assert result["age_days"] >= _STALENESS_DAYS

    def test_missing_file(self, tmp_path: Path) -> None:
        result = check_staleness(tmp_path / "nope.yaml")
        assert result["is_stale"]
        assert result["stocks_count"] == 0

    def test_bad_date_format(self, tmp_path: Path) -> None:
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml("not-a-date"))
        result = check_staleness(p)
        assert result["is_stale"]
        assert result["age_days"] == 999


# ────────────────────────────────────────────────────────────────
# check_demand_chain_health
# ────────────────────────────────────────────────────────────────

class TestDemandChainHealth:
    def test_expansion_to_recession_triggers(self, tmp_path: Path) -> None:
        """II→IV 触发 demand_locked=True 的告警."""
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        alerts = check_demand_chain_health("IV", "II", p)
        assert len(alerts) == 1
        assert alerts[0]["ts_code"] == "688012.SH"
        assert "复查" in alerts[0]["reason"]

    def test_peak_to_recession_triggers(self, tmp_path: Path) -> None:
        """III→IV 也触发."""
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        alerts = check_demand_chain_health("IV", "III", p)
        assert len(alerts) == 1

    def test_no_alert_on_recovery(self, tmp_path: Path) -> None:
        """IV→I (衰退→复苏) 不触发告警."""
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        alerts = check_demand_chain_health("I", "IV", p)
        assert len(alerts) == 0

    def test_no_alert_when_unlocked(self, tmp_path: Path) -> None:
        """demand_locked=False 的票不触发."""
        p = tmp_path / "sc.yaml"
        data = _minimal_yaml()
        data["stocks"]["688012.SH"]["demand_locked"] = False
        _write_yaml(p, data)
        alerts = check_demand_chain_health("IV", "II", p)
        assert len(alerts) == 0

    def test_no_alert_no_stage(self) -> None:
        """阶段为 None 不触发."""
        alerts = check_demand_chain_health(None, "II")
        assert len(alerts) == 0

    def test_same_stage_no_alert(self, tmp_path: Path) -> None:
        """阶段没变不触发 (虽然不是有效 transition)."""
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        alerts = check_demand_chain_health("II", "II", p)
        assert len(alerts) == 0


# ────────────────────────────────────────────────────────────────
# count_analyst_coverage (mocked Tushare)
# ────────────────────────────────────────────────────────────────

class TestCountAnalystCoverage:
    @patch("kss.data.tushare_client.TushareClient")
    def test_counts_unique_orgs_excluding_non_individual(self, mock_cls: MagicMock) -> None:
        import pandas as pd
        mock_client = mock_cls.return_value
        mock_client.fetch_report_rc.return_value = pd.DataFrame({
            "org_name": ["券商A", "券商B", "券商A", "券商C"],
            "report_type": ["点评", "深度", "一般", "非个股"],
        })
        result = count_analyst_coverage("688012.SH")
        assert result == 2  # A + B (C is 非个股)

    @patch("kss.data.tushare_client.TushareClient")
    def test_returns_none_on_failure(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.fetch_report_rc.return_value = None
        result = count_analyst_coverage("688012.SH")
        assert result is None

    @patch("kss.data.tushare_client.TushareClient")
    def test_returns_zero_when_all_non_individual(self, mock_cls: MagicMock) -> None:
        import pandas as pd
        mock_client = mock_cls.return_value
        mock_client.fetch_report_rc.return_value = pd.DataFrame({
            "org_name": ["券商A"],
            "report_type": ["非个股"],
        })
        result = count_analyst_coverage("688012.SH")
        assert result == 0


# ────────────────────────────────────────────────────────────────
# refresh_analyst_counts (mocked)
# ────────────────────────────────────────────────────────────────

class TestRefreshAnalystCounts:
    @patch("kss.supply_chain.updater.count_analyst_coverage")
    def test_updates_changed_values(self, mock_count: MagicMock, tmp_path: Path) -> None:
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())  # analyst_count=20
        mock_count.return_value = 40  # 变了

        result = refresh_analyst_counts(yaml_path=p, dry_run=False, sleep_between=0)
        assert result["updated"] == 1
        assert result["changes"][0]["old"] == 20
        assert result["changes"][0]["new"] == 40

        # 验证 YAML 已写入
        with open(p, encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        assert saved["stocks"]["688012.SH"]["analyst_count"] == 40
        # updated 日期也应该刷新
        assert saved["updated"] == datetime.now().strftime("%Y-%m-%d")

    @patch("kss.supply_chain.updater.count_analyst_coverage")
    def test_dry_run_does_not_write(self, mock_count: MagicMock, tmp_path: Path) -> None:
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        mock_count.return_value = 40

        result = refresh_analyst_counts(yaml_path=p, dry_run=True, sleep_between=0)
        assert result["updated"] == 1

        # YAML 应该没被修改
        with open(p, encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        assert saved["stocks"]["688012.SH"]["analyst_count"] == 20  # 原值

    @patch("kss.supply_chain.updater.count_analyst_coverage")
    def test_failure_preserves_original(self, mock_count: MagicMock, tmp_path: Path) -> None:
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        mock_count.return_value = None  # API 失败

        result = refresh_analyst_counts(yaml_path=p, dry_run=False, sleep_between=0)
        assert result["failed"] == 1
        assert result["updated"] == 0

        with open(p, encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        assert saved["stocks"]["688012.SH"]["analyst_count"] == 20  # 保持不变

    @patch("kss.supply_chain.updater.count_analyst_coverage")
    def test_no_change_when_same(self, mock_count: MagicMock, tmp_path: Path) -> None:
        p = tmp_path / "sc.yaml"
        _write_yaml(p, _minimal_yaml())
        mock_count.return_value = 20  # 跟原值一样

        result = refresh_analyst_counts(yaml_path=p, dry_run=False, sleep_between=0)
        assert result["updated"] == 0
        assert len(result["changes"]) == 0
