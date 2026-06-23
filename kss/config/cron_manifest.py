"""KSS 定时任务清单加载器 + 校验（plan 2026-06-23-001 / U1）。

``kss/config/cron_jobs.yaml`` 是所有 KSS launchd 任务的单一真源。本模块把它加载成
带类型的 dataclass 条目并做强校验，供三条下游链路共用：plist 渲染器（U2）、
安装/同步器（U3）、bridge 任务元数据（U4）。

校验纪律：
- suffix 唯一；
- wrapper 解析后须落在 ``PROJECT_ROOT`` 之内（拒 ``/usr/bin/curl`` 这类逃逸）；
- category ∈ ``category_order``；
- schedule 形态合法（daily 含 weekdays 1-7 / 缺省=每天；weekly 单 weekday）；
- 拒绝任何匹配 ``kss.security.redaction.CREDENTIAL_KEY_RE`` 的键（清单禁带凭据，
  token 由 wrapper 运行时加载）。

为什么 wrapper 边界是 ``PROJECT_ROOT`` 而非 ``PROJECT_ROOT/scripts/``：现网 ``scanner``
任务 wrapper = ``run_scanner.sh``（仓库根，非 scripts/）。R6 要求忠实誊录，故边界放宽到
仓库根；真正的安全闸是「不得逃逸出仓库」（``/usr/bin/curl`` 仍被拒）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kss.config.paths import PROJECT_ROOT
from kss.security.redaction import CREDENTIAL_KEY_RE

# 默认清单路径（与 kss/config/settings.yaml 同处）。
MANIFEST_PATH: Path = PROJECT_ROOT / "kss" / "config" / "cron_jobs.yaml"

# launchd Weekday 约定：1=周一 … 7=周日。0/8 越界非法。
_MIN_WEEKDAY = 1
_MAX_WEEKDAY = 7


class CronManifestError(ValueError):
    """清单结构 / 校验失败。"""


@dataclass(frozen=True)
class Schedule:
    """规范化调度。

    daily：``weekly is None``；``weekdays`` 为 None 表示每天（含周末），
    或显式工作日列表（如 ``(1,2,3,4,5)``）。
    weekly：``weekly`` = ``(weekday, hour, minute)``，``hour``/``minute`` 此时为该周点。
    """

    hour: int
    minute: int
    weekdays: tuple[int, ...] | None = None
    weekly: bool = False
    weekday: int | None = None  # weekly 专用单 weekday

    @property
    def is_weekly(self) -> bool:
        return self.weekly


@dataclass(frozen=True)
class CronJob:
    """一条清单任务条目。"""

    suffix: str
    wrapper: str  # 相对清单写法（如 scripts/run_x.sh）
    schedule: Schedule
    title: str
    category: str
    catchup: bool
    enabled: bool
    args: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"com.zcdeng.kss.{self.suffix}"

    @property
    def wrapper_path(self) -> Path:
        """wrapper 的仓库内绝对路径（已校验不逃逸）。"""
        return (PROJECT_ROOT / self.wrapper).resolve()


_REQUIRED_JOB_KEYS = {
    "suffix",
    "wrapper",
    "schedule",
    "title",
    "category",
    "catchup",
    "enabled",
}
_ALLOWED_JOB_KEYS = _REQUIRED_JOB_KEYS | {"args"}


def _reject_credential_keys(obj: Any, *, where: str) -> None:
    """递归扫描 dict 键名，命中凭据 pattern 即拒（清单禁带 token）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and CREDENTIAL_KEY_RE.search(k):
                raise CronManifestError(
                    f"{where}: 键 {k!r} 命中凭据 pattern，清单禁带凭据"
                )
            _reject_credential_keys(v, where=where)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _reject_credential_keys(item, where=where)


def _parse_schedule(raw: Any, *, suffix: str) -> Schedule:
    if not isinstance(raw, dict):
        raise CronManifestError(f"{suffix}: schedule 须为 mapping，得到 {type(raw).__name__}")

    if "weekly" in raw:
        if set(raw) != {"weekly"}:
            raise CronManifestError(f"{suffix}: weekly schedule 不应混入其它键 {set(raw) - {'weekly'}}")
        w = raw["weekly"]
        if not isinstance(w, dict) or set(w) != {"weekday", "hour", "minute"}:
            raise CronManifestError(
                f"{suffix}: weekly 须含且仅含 weekday/hour/minute，得到 {w!r}"
            )
        weekday = _check_weekday(w["weekday"], suffix=suffix)
        hour = _check_range(w["hour"], 0, 23, suffix=suffix, field_name="hour")
        minute = _check_range(w["minute"], 0, 59, suffix=suffix, field_name="minute")
        return Schedule(hour=hour, minute=minute, weekly=True, weekday=weekday)

    extra = set(raw) - {"hour", "minute", "weekdays"}
    if extra:
        raise CronManifestError(f"{suffix}: daily schedule 含未知键 {extra}")
    if "hour" not in raw or "minute" not in raw:
        raise CronManifestError(f"{suffix}: daily schedule 须含 hour 与 minute")
    hour = _check_range(raw["hour"], 0, 23, suffix=suffix, field_name="hour")
    minute = _check_range(raw["minute"], 0, 59, suffix=suffix, field_name="minute")

    weekdays: tuple[int, ...] | None = None
    if raw.get("weekdays") is not None:
        wds = raw["weekdays"]
        if not isinstance(wds, list) or not wds:
            raise CronManifestError(f"{suffix}: weekdays 须为非空列表，得到 {wds!r}")
        weekdays = tuple(_check_weekday(d, suffix=suffix) for d in wds)
    return Schedule(hour=hour, minute=minute, weekdays=weekdays)


def _check_weekday(value: Any, *, suffix: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CronManifestError(f"{suffix}: weekday 须为整数，得到 {value!r}")
    if not (_MIN_WEEKDAY <= value <= _MAX_WEEKDAY):
        raise CronManifestError(
            f"{suffix}: weekday {value} 越界（须 {_MIN_WEEKDAY}-{_MAX_WEEKDAY}）"
        )
    return value


def _check_range(value: Any, lo: int, hi: int, *, suffix: str, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CronManifestError(f"{suffix}: {field_name} 须为整数，得到 {value!r}")
    if not (lo <= value <= hi):
        raise CronManifestError(f"{suffix}: {field_name} {value} 越界（须 {lo}-{hi}）")
    return value


def _validate_wrapper(wrapper: Any, *, suffix: str) -> str:
    if not isinstance(wrapper, str) or not wrapper:
        raise CronManifestError(f"{suffix}: wrapper 须为非空字符串")
    resolved = (PROJECT_ROOT / wrapper).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        raise CronManifestError(
            f"{suffix}: wrapper {wrapper!r} 解析到 {resolved}，逃逸出 PROJECT_ROOT"
        ) from None
    if not resolved.is_file():
        raise CronManifestError(f"{suffix}: wrapper 文件不存在：{resolved}")
    return wrapper


@dataclass(frozen=True)
class CronManifest:
    """加载校验后的清单：任务列表 + 分类排序。"""

    jobs: tuple[CronJob, ...]
    category_order: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_suffix", {j.suffix: j for j in self.jobs})

    def job(self, suffix: str) -> CronJob | None:
        return self._by_suffix.get(suffix)  # type: ignore[attr-defined]


def _build_manifest(data: Any, *, path: Path) -> CronManifest:
    if not isinstance(data, dict):
        raise CronManifestError(f"{path}: 顶层须为 mapping")
    _reject_credential_keys(data, where=str(path))

    order_raw = data.get("category_order")
    if not isinstance(order_raw, list) or not order_raw:
        raise CronManifestError(f"{path}: category_order 须为非空列表")
    category_order = tuple(str(c) for c in order_raw)
    order_set = set(category_order)

    jobs_raw = data.get("jobs")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise CronManifestError(f"{path}: jobs 须为非空列表")

    jobs: list[CronJob] = []
    seen: set[str] = set()
    for idx, raw in enumerate(jobs_raw):
        if not isinstance(raw, dict):
            raise CronManifestError(f"{path}: jobs[{idx}] 须为 mapping")
        missing = _REQUIRED_JOB_KEYS - set(raw)
        if missing:
            raise CronManifestError(f"{path}: jobs[{idx}] 缺字段 {missing}")
        unknown = set(raw) - _ALLOWED_JOB_KEYS
        if unknown:
            raise CronManifestError(f"{path}: jobs[{idx}] 含未知字段 {unknown}")

        suffix = raw["suffix"]
        if not isinstance(suffix, str) or not suffix:
            raise CronManifestError(f"{path}: jobs[{idx}] suffix 须为非空字符串")
        if suffix in seen:
            raise CronManifestError(f"{path}: suffix 重复：{suffix}")
        seen.add(suffix)

        wrapper = _validate_wrapper(raw["wrapper"], suffix=suffix)
        schedule = _parse_schedule(raw["schedule"], suffix=suffix)

        category = raw["category"]
        if category not in order_set:
            raise CronManifestError(
                f"{suffix}: category {category!r} 不在 category_order {category_order}"
            )

        for flag in ("catchup", "enabled"):
            if not isinstance(raw[flag], bool):
                raise CronManifestError(f"{suffix}: {flag} 须为 bool，得到 {raw[flag]!r}")

        title = raw["title"]
        if not isinstance(title, str) or not title:
            raise CronManifestError(f"{suffix}: title 须为非空字符串")

        args_raw = raw.get("args", [])
        if not isinstance(args_raw, list):
            raise CronManifestError(f"{suffix}: args 须为列表")
        args = tuple(str(a) for a in args_raw)

        jobs.append(
            CronJob(
                suffix=suffix,
                wrapper=wrapper,
                schedule=schedule,
                title=title,
                category=category,
                catchup=bool(raw["catchup"]),
                enabled=bool(raw["enabled"]),
                args=args,
            )
        )

    return CronManifest(jobs=tuple(jobs), category_order=category_order)


def load_manifest(path: Path | str | None = None) -> CronManifest:
    """加载并校验清单。``path`` 缺省读 :data:`MANIFEST_PATH`。"""
    p = Path(path) if path is not None else MANIFEST_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CronManifestError(f"清单不可读：{p}（{exc}）") from exc
    data = yaml.safe_load(text)
    return _build_manifest(data, path=p)


# --------------------------------------------------------------------------- #
# 便捷 helper（U4 bridge 元数据派生用；缺省读默认清单，带模块级缓存）
# --------------------------------------------------------------------------- #
_cache: dict[str, Any] = {"mtime": None, "manifest": None}


def _default_manifest() -> CronManifest:
    try:
        mtime = MANIFEST_PATH.stat().st_mtime
    except OSError:
        return load_manifest()
    if _cache["mtime"] != mtime:
        _cache.update(mtime=mtime, manifest=load_manifest())
    return _cache["manifest"]  # type: ignore[return-value]


def all_jobs() -> tuple[CronJob, ...]:
    """默认清单全部任务（含 disabled；调用方按需过滤 enabled）。"""
    return _default_manifest().jobs


def title_for(suffix: str) -> str:
    """suffix → 中文标题；缺失回退 suffix 本身（fail-safe，不崩）。"""
    job = _default_manifest().job(suffix)
    return job.title if job else suffix


def category_for(suffix: str) -> str:
    """suffix → 分类；缺失回退「其他」。"""
    job = _default_manifest().job(suffix)
    return job.category if job else "其他"


def category_order() -> tuple[str, ...]:
    return _default_manifest().category_order


def catchup_eligible(suffix: str) -> bool:
    """该任务是否参与漏跑补跑；缺失回退 False（保守不补）。"""
    job = _default_manifest().job(suffix)
    return bool(job.catchup) if job else False


__all__ = [
    "CronJob",
    "CronManifest",
    "CronManifestError",
    "MANIFEST_PATH",
    "Schedule",
    "all_jobs",
    "catchup_eligible",
    "category_for",
    "category_order",
    "load_manifest",
    "title_for",
]
