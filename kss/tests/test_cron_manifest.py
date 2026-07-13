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
def test_real_manifest_loads_20_jobs() -> None:
    m = load_manifest()  # 默认读 kss/config/cron_jobs.yaml
    assert len(m.jobs) == 20
    suffixes = {j.suffix for j in m.jobs}
    assert len(suffixes) == 20  # 全唯一
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
    """scanner wrapper 的内层日志必须与 launchd StandardOutPath 对齐。

    wrapper 侧改用 $KSS_STATE_ROOT（非 $PROJECT_DIR）拼日志路径（code review 发现的
    真实回归：bundle 模式下 PROJECT_DIR 指向签名 .app 内只读 Resources，往那写会破坏
    code-signing seal——同 render_launchd_plists.py:108-111 的 KTD9 纪律）。dev 模式下
    KSS_STATE_ROOT 未被 plist 注入，wrapper 自身 ``: "${KSS_STATE_ROOT:=$PROJECT_DIR}"``
    回落到 PROJECT_DIR，数值上仍与已渲染 plist 的 StandardOutPath 一致。
    """
    with (_DEPLOY / "com.zcdeng.kss.scanner.plist").open("rb") as fh:
        pl = plistlib.load(fh)
    expected = pl["StandardOutPath"].replace(str(_REPO), "$KSS_STATE_ROOT")
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


# --------------------------------------------------------------------------- #
# U6（plan 2026-07-12-005）：state-root 排期 overlay 合并
# --------------------------------------------------------------------------- #

def _write_overlay(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "cron_overrides.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


class TestOverlayMerge:
    def test_no_overlay_file_is_normal_state(self, tmp_path: Path) -> None:
        """overlay 文件不存在 → 空覆盖，清单原样返回（不是错误）。"""
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        m = load_manifest(manifest_path, overlay_path=tmp_path / "nope.yaml")
        assert m.jobs[0].schedule.hour == 8
        assert m.jobs[0].schedule.minute == 30

    def test_overlay_changes_schedule_hour_minute(self, tmp_path: Path) -> None:
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        overlay_path = _write_overlay(
            tmp_path, {"demo_daily": {"hour": 18, "minute": 30, "weekdays": [1, 2, 3, 4, 5]}}
        )
        m = load_manifest(manifest_path, overlay_path=overlay_path)
        job = m.job("demo_daily")
        assert job is not None
        assert job.schedule.hour == 18
        assert job.schedule.minute == 30

    def test_overlay_only_touches_named_suffix(self, tmp_path: Path) -> None:
        manifest_path = _write(
            tmp_path,
            _doc([_minimal_job(), _minimal_job(suffix="other_daily")]),
        )
        overlay_path = _write_overlay(tmp_path, {"demo_daily": {"hour": 9, "minute": 0}})
        m = load_manifest(manifest_path, overlay_path=overlay_path)
        assert m.job("demo_daily").schedule.hour == 9
        assert m.job("other_daily").schedule.hour == 8  # 未被 overlay 提及，原样保留

    def test_overlay_unknown_suffix_rejected(self, tmp_path: Path) -> None:
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        overlay_path = _write_overlay(tmp_path, {"nonexistent_suffix": {"hour": 9, "minute": 0}})
        with pytest.raises(CronManifestError, match="未知 suffix"):
            load_manifest(manifest_path, overlay_path=overlay_path)

    def test_overlay_rejects_credential_like_keys(self, tmp_path: Path) -> None:
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        overlay_path = _write_overlay(
            tmp_path, {"demo_daily": {"hour": 9, "minute": 0, "api_token": "leaked"}}
        )
        with pytest.raises(CronManifestError):
            load_manifest(manifest_path, overlay_path=overlay_path)

    def test_overlay_enabled_field_not_supported(self, tmp_path: Path) -> None:
        """overlay 只覆盖 schedule；enabled 不是允许的顶层结构（拒绝 dict 形态外的字段）。

        overlay 里每个 suffix 的值本身就是 schedule 原始字典（走 _parse_schedule 校验），
        混入非 schedule 键（如凭据/enabled）会被 _parse_schedule 或凭据扫描拒绝——
        这里验证一个非法 schedule 形态（缺 hour/minute 且非 weekly）会报错，而不是
        被静默接受为某种「enabled」语义。
        """
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        overlay_path = _write_overlay(tmp_path, {"demo_daily": {"enabled": False}})
        with pytest.raises(CronManifestError):
            load_manifest(manifest_path, overlay_path=overlay_path)

    def test_overlay_weekly_schedule(self, tmp_path: Path) -> None:
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        overlay_path = _write_overlay(
            tmp_path, {"demo_daily": {"weekly": {"weekday": 5, "hour": 20, "minute": 0}}}
        )
        m = load_manifest(manifest_path, overlay_path=overlay_path)
        job = m.job("demo_daily")
        assert job.schedule.is_weekly
        assert job.schedule.weekday == 5

    def test_default_manifest_cache_invalidates_on_overlay_change(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_manifest 的缓存键须含 overlay mtime，排期编辑后无需进程重启即生效。"""
        manifest_path = _write(tmp_path, _doc([_minimal_job()]))
        overlay_path = tmp_path / "cron_overrides.yaml"
        monkeypatch.setattr(cm, "MANIFEST_PATH", manifest_path)
        monkeypatch.setattr(cm, "OVERLAY_PATH", overlay_path)
        cm._cache.update(key=None, manifest=None)  # 清模块级缓存，避免跨测试污染

        assert cm.title_for("demo_daily") == "示例"
        first = cm._default_manifest().job("demo_daily").schedule.hour
        assert first == 8

        overlay_path.write_text(
            yaml.safe_dump({"demo_daily": {"hour": 22, "minute": 0}}, allow_unicode=True),
            encoding="utf-8",
        )
        second = cm._default_manifest().job("demo_daily").schedule.hour
        assert second == 22
