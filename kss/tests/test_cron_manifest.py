"""U1 单测：cron 任务清单加载 + 校验 + 与 deploy/launchd plist 调度一致性.

覆盖 plan 2026-06-23-001 U1 的 Test scenarios：happy / 校验 / 安全 / weekly 边界 /
R6 对照（清单誊录的 schedule == 对应 plist StartCalendarInterval，防誊错）。

铁律：所有错误用例落 ``tmp_path``，不污染真实 kss/config/cron_jobs.yaml。
"""
from __future__ import annotations

import plistlib
import textwrap
from pathlib import Path

import pytest
import yaml

from kss.config import cron_manifest as cm
from kss.config.cron_manifest import CronManifestError, load_manifest

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "deploy" / "launchd"


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "cron_jobs.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def _minimal_job(**overrides) -> dict:
    job = {
        "suffix": "demo_daily",
        "wrapper": "scripts/run_update_data_daily.sh",  # 真实存在的 wrapper
        "schedule": {"hour": 8, "minute": 30, "weekdays": [1, 2, 3, 4, 5]},
        "title": "示例",
        "category": "数据更新",
        "catchup": True,
        "enabled": True,
    }
    job.update(overrides)
    return job


def _doc(jobs: list[dict], order: list[str] | None = None) -> dict:
    return {
        "category_order": order or ["数据更新", "扫描选股", "系统", "其他"],
        "jobs": jobs,
    }


# --------------------------------------------------------------------------- #
# happy：真实清单
# --------------------------------------------------------------------------- #
def test_real_manifest_loads_19_jobs() -> None:
    m = load_manifest()  # 默认读 kss/config/cron_jobs.yaml
    assert len(m.jobs) == 19
    suffixes = {j.suffix for j in m.jobs}
    assert len(suffixes) == 19  # 全唯一
    # 舆情热点两场已注册
    assert {"news_digest_premarket", "news_digest_postclose"} <= suffixes
    # 紫苏叶富化预热已注册（默认停用）
    assert "perilla_enrich_daily" in suffixes


def test_real_manifest_fields_complete() -> None:
    m = load_manifest()
    for j in m.jobs:
        assert j.suffix
        assert j.title
        assert j.category in m.category_order
        assert isinstance(j.catchup, bool)
        assert isinstance(j.enabled, bool)
        assert j.wrapper_path.is_file()


def test_metadata_backfilled_jobs_not_other() -> None:
    """factor_health / ledger_settle / collect_intraday 之前掉「其他」，须有正确分类。"""
    m = load_manifest()
    by = {j.suffix: j for j in m.jobs}
    assert by["factor_health"].category == "校验回测"
    assert by["ledger_settle"].category == "纸交易"
    assert by["collect_intraday"].category == "数据更新"
    for s in ("factor_health", "ledger_settle", "collect_intraday"):
        assert by[s].category != "其他"
        assert by[s].title


def test_scanner_wrapper_uses_launchd_log_path() -> None:
    """scanner wrapper 的内层日志必须与 launchd StandardOutPath 对齐。"""
    with (_DEPLOY / "com.zcdeng.kss.scanner.plist").open("rb") as fh:
        pl = plistlib.load(fh)
    expected = pl["StandardOutPath"].replace(str(_REPO), "$PROJECT_DIR")
    wrapper = (_REPO / "run_scanner.sh").read_text(encoding="utf-8")
    assert f'LOG_FILE="{expected}"' in wrapper


def test_signed_package_includes_root_scanner_wrapper() -> None:
    """bundle-mode 的 PROJECT_ROOT=Resources，必须带根级 run_scanner.sh。"""
    script = (_REPO / "script" / "sign_and_build.sh").read_text(encoding="utf-8")
    assert "run_scanner.sh" in script


def test_collect_intraday_catchup_false() -> None:
    m = load_manifest()
    by = {j.suffix: j for j in m.jobs}
    assert by["collect_intraday"].catchup is False
    # 其余（除 collect_intraday）catchup=true
    assert by["update_data_daily"].catchup is True


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def test_duplicate_suffix_rejected(tmp_path: Path) -> None:
    doc = _doc([_minimal_job(suffix="dup"), _minimal_job(suffix="dup")])
    with pytest.raises(CronManifestError, match="重复"):
        load_manifest(_write(tmp_path, doc))


def test_unknown_category_rejected(tmp_path: Path) -> None:
    doc = _doc([_minimal_job(category="不存在的分类")])
    with pytest.raises(CronManifestError, match="category"):
        load_manifest(_write(tmp_path, doc))


def test_missing_wrapper_file_rejected(tmp_path: Path) -> None:
    doc = _doc([_minimal_job(wrapper="scripts/does_not_exist_xyz.sh")])
    with pytest.raises(CronManifestError, match="不存在"):
        load_manifest(_write(tmp_path, doc))


def test_illegal_weekday_rejected(tmp_path: Path) -> None:
    for bad in (0, 8):
        doc = _doc([_minimal_job(schedule={"hour": 8, "minute": 30, "weekdays": [bad]})])
        with pytest.raises(CronManifestError, match="weekday"):
            load_manifest(_write(tmp_path, doc))


# --------------------------------------------------------------------------- #
# 安全
# --------------------------------------------------------------------------- #
def test_wrapper_outside_repo_rejected(tmp_path: Path) -> None:
    doc = _doc([_minimal_job(wrapper="/usr/bin/curl")])
    with pytest.raises(CronManifestError, match="逃逸"):
        load_manifest(_write(tmp_path, doc))


def test_wrapper_traversal_escape_rejected(tmp_path: Path) -> None:
    doc = _doc([_minimal_job(wrapper="../../../etc/hosts")])
    with pytest.raises(CronManifestError, match="逃逸"):
        load_manifest(_write(tmp_path, doc))


def test_credential_key_rejected(tmp_path: Path) -> None:
    """清单含命中 CREDENTIAL_KEY_RE 的键（tushare_token:）→ 拒。"""
    # 直接写原始 YAML 文本注入凭据键（绕过 _minimal_job 结构）。
    text = textwrap.dedent(
        """\
        category_order: [数据更新, 其他]
        tushare_token: abc123
        jobs:
          - suffix: demo
            wrapper: scripts/run_update_data_daily.sh
            schedule: {hour: 8, minute: 30}
            title: 示例
            category: 数据更新
            catchup: true
            enabled: true
        """
    )
    p = tmp_path / "cron_jobs.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(CronManifestError, match="凭据"):
        load_manifest(p)


def test_credential_key_nested_in_job_rejected(tmp_path: Path) -> None:
    job = _minimal_job()
    job["api_key"] = "deadbeef"
    doc = _doc([job])
    with pytest.raises(CronManifestError, match="凭据"):
        load_manifest(_write(tmp_path, doc))


# --------------------------------------------------------------------------- #
# weekly 边界
# --------------------------------------------------------------------------- #
def test_weekly_jobs_parsed() -> None:
    m = load_manifest()
    by = {j.suffix: j for j in m.jobs}
    for suffix, weekday, hour, minute in (
        ("paper_trade_weekly", 5, 17, 0),
        ("prediction_validation_weekly", 5, 19, 30),
    ):
        sched = by[suffix].schedule
        assert sched.is_weekly is True
        assert sched.weekday == weekday
        assert sched.hour == hour
        assert sched.minute == minute
        assert sched.weekdays is None


# --------------------------------------------------------------------------- #
# R6 对照：清单 schedule == deploy/launchd plist StartCalendarInterval
# --------------------------------------------------------------------------- #
def _schedule_to_sci(sched: cm.Schedule):
    """把清单 schedule 展开成 launchd StartCalendarInterval 等价结构（与 plist 对齐）。"""
    if sched.is_weekly:
        return {"Weekday": sched.weekday, "Hour": sched.hour, "Minute": sched.minute}
    if sched.weekdays is None:
        return {"Hour": sched.hour, "Minute": sched.minute}
    return [
        {"Weekday": wd, "Hour": sched.hour, "Minute": sched.minute}
        for wd in sched.weekdays
    ]


def _normalize_sci(sci):
    """plist 的 SCI 可能是单 dict 或 list；统一为可比较结构（list 排序无关）。"""
    if isinstance(sci, list):
        return sorted(
            (dict(item) for item in sci),
            key=lambda d: (d.get("Weekday", -1), d.get("Hour", -1), d.get("Minute", -1)),
        )
    return dict(sci)


@pytest.mark.parametrize("job", load_manifest().jobs, ids=lambda j: j.suffix)
def test_manifest_schedule_matches_plist(job: cm.CronJob) -> None:
    """R6 守护：每条清单 schedule 与对应 deploy/launchd plist 的 SCI 等价（拦誊错）。"""
    plist_path = _DEPLOY / f"{job.label}.plist"
    assert plist_path.is_file(), f"缺 plist：{plist_path}"
    data = plistlib.loads(plist_path.read_bytes())
    expected = _schedule_to_sci(job.schedule)
    actual = data["StartCalendarInterval"]
    assert _normalize_sci(actual) == _normalize_sci(expected), (
        f"{job.suffix}: 清单 {expected} != plist {actual}"
    )


def test_manifest_wrapper_matches_plist_program() -> None:
    """wrapper（+args）须与 plist ProgramArguments 一致（防 wrapper 誊错）。"""
    m = load_manifest()
    for job in m.jobs:
        data = plistlib.loads((_DEPLOY / f"{job.label}.plist").read_bytes())
        prog = data["ProgramArguments"]
        assert Path(prog[0]).name == Path(job.wrapper).name, job.suffix
        assert tuple(prog[1:]) == job.args, job.suffix
