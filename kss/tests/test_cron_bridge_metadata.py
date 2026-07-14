"""U4 单测：bridge 任务元数据从清单派生（plan 2026-06-23-001 / U4）。

覆盖 U4 Test scenarios：
- happy：cron-list 每任务 title/category 来自清单（改清单 → cron-list 跟着变）；
- catchup：catchup:false 的任务（collect_intraday）不进 _cron_catchup 的 kickstart 集；
- 枚举源：清单有而 ~/Library/LaunchAgents 未装的任务以 needsInstall=True 显式列出，绝不静默漏；
- categoryOrder：cron-list payload 顶层含 categoryOrder（= 清单 category_order）。

铁律：清单改写落 tmp_path，不污染真实 kss/config/cron_jobs.yaml；monkeypatch
cron_manifest.MANIFEST_PATH + 清缓存使 bridge 读 tmp 清单。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import kss_app_bridge as b  # noqa: E402
from kss.config import cron_manifest as cm  # noqa: E402


def _base_manifest() -> dict:
    """两条任务 + 排序的最小合法清单（wrapper 取真实存在的脚本以过校验）。"""
    return {
        "category_order": ["数据更新", "扫描选股", "系统", "其他"],
        "jobs": [
            {
                "suffix": "update_data_daily",
                "wrapper": "scripts/run_update_data_daily.sh",
                "schedule": {"hour": 8, "minute": 30, "weekdays": [1, 2, 3, 4, 5]},
                "title": "股票池日线更新",
                "category": "数据更新",
                "catchup": True,
                "enabled": True,
            },
            {
                "suffix": "collect_intraday",
                "wrapper": "scripts/run_collect_intraday.sh",
                "schedule": {"hour": 15, "minute": 5},
                "title": "分时收盘采集",
                "category": "数据更新",
                "catchup": False,
                "enabled": True,
            },
        ],
    }


@pytest.fixture
def manifest_at_tmp(tmp_path, monkeypatch):
    """把 bridge 读的清单指向 tmp，返回一个「写 dict → 生效」的 setter。"""
    p = tmp_path / "cron_jobs.yaml"

    def _set(data: dict) -> Path:
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr(cm, "MANIFEST_PATH", p)
        cm._cache.update(mtime=None, manifest=None)  # 强制重载
        return p

    yield _set
    cm._cache.update(mtime=None, manifest=None)  # 收尾清缓存，不泄漏到他测


def _fake_status_loaded(monkeypatch):
    """让 _scheduled_job 不依赖真实 launchctl：状态空、无 disabled。"""
    monkeypatch.setattr(b, "_launchctl_status", lambda label, uid: {"loaded": True, "lastExit": None, "pid": None})
    monkeypatch.setattr(b, "_disabled_labels", lambda uid: set())


def test_title_category_from_manifest(manifest_at_tmp, monkeypatch):
    """改清单 title/category → cron-list 跟着变（证明元数据来自清单而非硬编码）。"""
    data = _base_manifest()
    data["jobs"][0]["title"] = "改名后的日更"
    data["jobs"][0]["category"] = "扫描选股"
    manifest_at_tmp(data)
    _fake_status_loaded(monkeypatch)
    # 让两任务都算「已装」，隔离掉 needsInstall 噪声。
    monkeypatch.setattr(
        b, "_installed_plists",
        lambda: {f"com.zcdeng.kss.{s}": _REPO / "deploy" / "launchd" / f"com.zcdeng.kss.{s}.plist"
                 for s in ("update_data_daily", "collect_intraday")},
    )

    out = b.dispatch("cron-list", [])
    jobs = {j["label"].replace("com.zcdeng.kss.", ""): j for j in out["jobs"]}
    assert jobs["update_data_daily"]["title"] == "改名后的日更"
    assert jobs["update_data_daily"]["category"] == "扫描选股"
    assert jobs["collect_intraday"]["title"] == "分时收盘采集"
    assert jobs["collect_intraday"]["category"] == "数据更新"


def test_category_order_present_and_from_manifest(manifest_at_tmp, monkeypatch):
    """cron-list 顶层含 categoryOrder，= 清单 category_order。"""
    manifest_at_tmp(_base_manifest())
    _fake_status_loaded(monkeypatch)
    monkeypatch.setattr(b, "_installed_plists", lambda: {})
    out = b.dispatch("cron-list", [])
    assert out["categoryOrder"] == ["数据更新", "扫描选股", "系统", "其他"]


def test_unknown_suffix_falls_back(manifest_at_tmp, monkeypatch):
    """清单缺某 suffix → title/category 回退 suffix/「其他」（不崩）。"""
    manifest_at_tmp(_base_manifest())
    assert cm.title_for("does_not_exist") == "does_not_exist"
    assert cm.category_for("does_not_exist") == "其他"


def test_manifest_not_installed_job_surfaces_flagged(manifest_at_tmp, monkeypatch):
    """清单有而 ~/Library/LaunchAgents 未装的任务以 needsInstall=True 列出，绝不静默漏。"""
    manifest_at_tmp(_base_manifest())
    _fake_status_loaded(monkeypatch)
    # 模拟：update_data_daily 已装、collect_intraday 未装。
    installed_label = "com.zcdeng.kss.update_data_daily"
    monkeypatch.setattr(
        b, "_installed_plists",
        lambda: {installed_label: _REPO / "deploy" / "launchd" / f"{installed_label}.plist"},
    )
    out = b.dispatch("cron-list", [])
    jobs = {j["label"].replace("com.zcdeng.kss.", ""): j for j in out["jobs"]}
    # 未装任务仍出现，且打 needsInstall 标
    assert "collect_intraday" in jobs, "未装任务被静默漏掉（违反 R4）"
    assert jobs["collect_intraday"]["needsInstall"] is True
    assert jobs["update_data_daily"]["needsInstall"] is False


def test_catchup_eligible_drives_catchup_exclusion(manifest_at_tmp, monkeypatch):
    """catchup:false 的任务（collect_intraday）不进 _cron_catchup 的 kickstart 集。"""
    manifest_at_tmp(_base_manifest())
    monkeypatch.setattr(b, "_disabled_labels", lambda uid: set())
    # 让两任务都「应跑未跑」（stale=True），catchup 仅应放行 catchup:true 的那个。
    monkeypatch.setattr(
        b, "_scheduled_job",
        lambda label, path, uid, disabled, **kw: {
            "label": label, "title": label, "enabled": True, "stale": True,
        },
    )
    # 拦截 launchctl，记录谁被真 kickstart。
    kicked: list[str] = []

    def _fake_launchctl(args):
        if args and args[0] == "kickstart":
            kicked.append(args[-1])
        return (0, "", "")

    monkeypatch.setattr(b, "_run_launchctl", _fake_launchctl)
    # _kickstart_labels 还会查 _launchd_plists（白名单）——两 label 都需在内。
    monkeypatch.setattr(
        b, "_launchd_plists",
        lambda: {f"com.zcdeng.kss.{s}": _REPO / "deploy" / "launchd" / f"com.zcdeng.kss.{s}.plist"
                 for s in ("update_data_daily", "collect_intraday")},
    )

    res = b._cron_catchup()
    kicked_suffixes = {k.split("/")[-1].replace("com.zcdeng.kss.", "") for k in kicked}
    assert "collect_intraday" not in kicked_suffixes, "catchup:false 任务不应被补跑"
    assert "update_data_daily" in kicked_suffixes, "catchup:true 任务应被补跑"
    assert "com.zcdeng.kss.collect_intraday" in res["skipped"]


def test_rerun_many_empty_labels_uses_manifest_enabled_jobs(
    manifest_at_tmp, monkeypatch
):
    """全部重跑的候选集来自 manifest enabled jobs，而不是 deploy plist glob。"""
    data = _base_manifest()
    data["jobs"].append({
        "suffix": "disabled_job",
        "wrapper": "scripts/run_update_data_daily.sh",
        "schedule": {"hour": 7, "minute": 0},
        "title": "停用任务",
        "category": "系统",
        "catchup": True,
        "enabled": False,
    })
    manifest_at_tmp(data)
    monkeypatch.setattr(b, "_disabled_labels", lambda uid: set())
    monkeypatch.setattr(
        b,
        "_scheduled_job",
        lambda label, path, uid, disabled, **kw: {
            "label": label, "title": label, "enabled": True, "stale": False,
        },
    )
    kicked: list[str] = []

    def _fake_launchctl(args):
        if args and args[0] == "kickstart":
            kicked.append(args[-1])
        return (0, "", "")

    monkeypatch.setattr(b, "_run_launchctl", _fake_launchctl)
    monkeypatch.setattr(
        b,
        "_launchd_plists",
        lambda: {
            "com.zcdeng.kss.update_data_daily": _REPO / "deploy" / "launchd" / "com.zcdeng.kss.update_data_daily.plist",
            "com.zcdeng.kss.collect_intraday": _REPO / "deploy" / "launchd" / "com.zcdeng.kss.collect_intraday.plist",
            "com.zcdeng.kss.deploy_only_stale": _REPO / "deploy" / "launchd" / "com.zcdeng.kss.deploy_only_stale.plist",
            "com.zcdeng.kss.disabled_job": _REPO / "deploy" / "launchd" / "com.zcdeng.kss.disabled_job.plist",
        },
    )

    res = b._cron_rerun_many([])
    kicked_labels = {k.split("/")[-1] for k in kicked}
    assert kicked_labels == {"com.zcdeng.kss.update_data_daily"}
    assert "com.zcdeng.kss.deploy_only_stale" not in res["skipped"]
    assert "com.zcdeng.kss.collect_intraday" in res["skipped"]
    assert "com.zcdeng.kss.disabled_job" not in kicked_labels


def test_chain_member_schedule_carries_trigger_relation():
    """R3-U4（plan 2026-07-14-001 / R5）：链成员 cron-list 带 triggeredBy 且
    schedule 人读文案标明「随 xx 触发 · 兜底」。"""
    out = b.dispatch("cron-list", [])
    jobs = {j["label"]: j for j in out["jobs"]}
    picks = jobs.get("com.zcdeng.kss.formal_daily_picks")
    assert picks is not None
    assert picks["triggeredBy"] == "update_data_daily_eod"
    assert "触发" in picks["schedule"] and "兜底" in picks["schedule"]
    # 非链成员不带触发文案
    eod = jobs.get("com.zcdeng.kss.update_data_daily_eod")
    assert eod is not None
    assert eod.get("triggeredBy") is None
    assert "触发" not in eod["schedule"]
