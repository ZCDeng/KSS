#!/usr/bin/env python3
"""分时收盘采集器（U5 完整加固版）.

**职责分层（KTD2：数据层不抛 vs 存储层失败闭合，是两层）**：

- **取数层**（``provider.fetch_bars`` + :func:`_fetch_with_retry`）：serial 限速、有界
  重试、永不抛异常；最终失败返回带 ``error`` 的 :class:`FetchResult`（数据层契约）。
- **存储/run 层**（``IntradayStore``）：``calendar_unknown`` / ``retention_limit`` /
  ``mapping_ambiguous`` 等都是**终止 run + 非零退出 + 持久化失败记录、无可查部分行**。

**收盘流（KTD3 失败闭合）**：
1. ``trade_cal`` 查 SSE 交易日——**失败即终止 ``calendar_unknown``，绝不 weekday/archive
   猜**（刻意不抄 ``daily_review.py:168`` / ``hotspot_rotation.py:125-149`` 的 fallback）。
2. retention 硬限**采集前**检查（DB 字节 / 单 run raw 字节）；超 → 终止 ``retention_limit``。
3. fail-closed 解析每符号：unknown 跳过（零调用）、ambiguous 终止（零调用）。
4. serial 取**当场** session；不得用滚动响应（零/改 opens）更新先前日（改记 gap）。
5. 单事务写 run + observation + 内容寻址 blob；canonical 追认 + ``complete`` 评估。
6. **窗内追补（RB3 + M2）**：对仍在 provider 滚动窗内、库中无 ``complete`` 评估的交易日
   重拉（新 observation/revision，**不盲覆盖**）；canonical 归一化**仅对无 complete 的日**
   运行（外科式，不靠写一次守卫兜底）。
7. **窗外 gap（RB3）**：滚动窗外无覆盖交易日 → ``permanent_gap`` + 告警（仅 Tushare 历史
   proxy 可填）。
8. **漂移自报（M1）**：每根 bar 记 ``retrieved_at - bar_end_ts``，超配置 ``publication_delay``
   即写 ``publication_delay_exceeded`` 告警（fail-loud，**不依赖** watch 模式）。

凭据从不进 plist；token 解析推迟到接 Tushare 路径（东财前向无需 token）。

手动测试：
    .venv-desktop/bin/python scripts/collect_intraday.py --mode close --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.config.paths import INTRADAY_DB  # noqa: E402
from kss.data.intraday_client import (  # noqa: E402
    EASTMOTNEY_1M_MAX_HISTORY_DAYS,
    EastmoneyAkshareProvider,
    FetchResult,
    IntradayProvider,
    classify_eligibility,
)
from kss.data.intraday_store import (  # noqa: E402
    IntradayStore,
    MappingStatus,
    ObservationInput,
)

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# ----- retention 硬限（KTD2；影子后由 U8 校准；env 可覆盖） ----- #
INTRADAY_MAX_DB_BYTES: int = int(
    os.environ.get("INTRADAY_MAX_DB_BYTES", str(4294967296))  # 4 GiB
)
INTRADAY_MAX_RAW_BYTES_PER_RUN: int = int(
    os.environ.get("INTRADAY_MAX_RAW_BYTES_PER_RUN", str(67108864))  # 64 MiB
)

# 取数有界重试（serial、指数退避；镜像 tushare_client._fetch_with_retry 思路）。
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0


# 日历依赖签名：给定横杠日期窗 [start, end]，返回该窗内**开市**交易日集合（横杠）。
# 失败必须**抛异常**（由 collect_close 捕获终止 calendar_unknown，KTD3），绝不返回猜测。
CalendarFn = Callable[[str, str], "set[str]"]


def _today_shanghai() -> str:
    """今天的横杠日期 ``YYYY-MM-DD``（KTD7）。"""
    return datetime.now(tz=_SHANGHAI).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# trade_cal 依赖（KTD3：失败必抛，绝不 fallback）
# --------------------------------------------------------------------------- #


def tushare_trade_cal(start_date: str, end_date: str) -> set[str]:
    """经 Tushare 查 SSE 开市交易日（横杠日期集合）。**失败抛异常**（KTD3）.

    刻意**不** weekday/archive 兜底：调用约定沿用 ``client.get_pro().trade_cal(
    exchange='SSE', ...)`` + ``is_open==1`` 过滤（同 ``daily_review.py:162``），但失败
    向上抛，由 :func:`collect_close` 终止 ``calendar_unknown``。
    """
    from kss.data.tushare_client import TushareClient

    pro = TushareClient().get_pro()
    cal = pro.trade_cal(
        exchange="SSE",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )
    if cal is None:
        raise RuntimeError("trade_cal 返回 None")
    open_rows = cal[cal["is_open"] == 1]
    out: set[str] = set()
    for compact in open_rows["cal_date"].tolist():
        s = str(compact)
        out.add(f"{s[0:4]}-{s[4:6]}-{s[6:8]}")
    return out


def _fetch_with_retry(
    fetch: Callable[[], FetchResult],
    label: str,
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    backoff_base: float = _BACKOFF_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """对单符号取数应用有界重试（serial、指数退避；数据层契约：永不抛）.

    ``fetch`` 永远返回 :class:`FetchResult`（provider 已吞异常）。``ok`` 为真即返回；
    否则退避重试到 ``max_attempts``，最终返回**最后一次**带 ``error`` 的结果（业务层
    据 ``error`` 计 per-symbol 失败，区别于抛异常）。
    """
    last: FetchResult | None = None
    for attempt in range(1, max_attempts + 1):
        result = fetch()
        last = result
        if result.ok:
            return result
        if attempt >= max_attempts:
            logger.warning("%s 最终失败（已重试 %d 次）: %s",
                           label, max_attempts - 1, result.error)
            return result
        wait = backoff_base * (2 ** (attempt - 1))
        logger.info("%s 第 %d 次失败: %s；%.1fs 后重试",
                    label, attempt, result.error, wait)
        sleep(wait)
    assert last is not None  # max_attempts >= 1
    return last


# --------------------------------------------------------------------------- #
# canonical 上下文（profile/contract）解析
# --------------------------------------------------------------------------- #


def _expected_bar_ends(store: IntradayStore, session_profile_version: str) -> tuple[str, ...]:
    """从已注册 profile 取合法 ``HH:MM`` bar-end 端点（assess_complete 用）。"""
    prof = store.get_session_profile(session_profile_version)
    if prof is None:
        return ()
    return tuple(prof["legal_bar_ends"])


def _drift_seconds(retrieved_at: str, bar_end_ts: str) -> float:
    """``retrieved_at - bar_end_ts`` 秒数（M1 漂移自报）。两者皆带时区 ISO。"""
    return (
        datetime.fromisoformat(retrieved_at) - datetime.fromisoformat(bar_end_ts)
    ).total_seconds()


# --------------------------------------------------------------------------- #
# 收盘采集（完整 --mode close）
# --------------------------------------------------------------------------- #


def collect_close(
    store: IntradayStore,
    provider: IntradayProvider,
    *,
    trade_date: str,
    calendar: CalendarFn = tushare_trade_cal,
    session_profile_version: str | None = None,
    contract_version: str | None = None,
    publication_delay_seconds: int | None = None,
    rolling_window_days: int = EASTMOTNEY_1M_MAX_HISTORY_DAYS,
    gap_lookback_days: int = 30,
    interval_minutes: int = 1,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now_ts: str | None = None,
) -> dict[str, Any]:
    """收盘采集一场（完整 U5）。返回机读汇总 dict（含 ``exit_code`` / ``alerts``）.

    流程（KTD3 失败闭合）：trade_cal → retention → 非交易日跳过 → fail-closed 解析 →
    serial 有界重试取当场 session → 单事务写 → canonical+complete 评估 → 窗内追补 +
    窗外 permanent_gap。详见模块 docstring。
    """
    alerts: list[dict[str, Any]] = []

    if dry_run:
        symbols = store.list_registered_symbols(provider.name)
        plan = [{"symbol": s, "asset_kind": k} for s, k in symbols]
        return {
            "mode": "close", "dry_run": True, "trade_date": trade_date,
            "provider": provider.name, "planned_symbols": plan,
            "status": "planned", "exit_code": 0,
        }

    # 1) trade_cal（KTD3：失败终止 calendar_unknown，绝不 weekday/archive 猜）。
    # 回看窗须显著宽于滚动窗：窗外（仍可检的）交易日才能被检为 permanent_gap（RB3）。
    window_start = (
        datetime.strptime(trade_date, "%Y-%m-%d")
        - timedelta(days=max(rolling_window_days + 5, gap_lookback_days))
    ).strftime("%Y-%m-%d")
    try:
        open_days = calendar(window_start, trade_date)
    except Exception as exc:  # noqa: BLE001 — 终止闭合，不向上抛
        run_id = store.record_terminal_failure(
            provider=provider.name, mode="close", trade_date=trade_date,
            failure_reason="calendar_unknown",
            error_summary=f"trade_cal lookup failed: {type(exc).__name__}: {exc}"[:200],
        )
        return {
            "mode": "close", "trade_date": trade_date, "provider": provider.name,
            "status": "failed", "failure_reason": "calendar_unknown",
            "run_id": run_id, "exit_code": 1, "alerts": alerts,
        }

    # 非交易日：跳过 + 记录（非失败）。
    if trade_date not in open_days:
        return {
            "mode": "close", "trade_date": trade_date, "provider": provider.name,
            "status": "skipped", "reason": "non_trade_day",
            "exit_code": 0, "alerts": alerts,
        }

    # 2) retention 硬限（采集前）：DB 字节超限 → 终止 retention_limit（无可查行）。
    if store.db_byte_size() > INTRADAY_MAX_DB_BYTES:
        run_id = store.record_terminal_failure(
            provider=provider.name, mode="close", trade_date=trade_date,
            failure_reason="retention_limit",
            error_summary=(
                f"db bytes {store.db_byte_size()} > limit {INTRADAY_MAX_DB_BYTES}"
            ),
        )
        return {
            "mode": "close", "trade_date": trade_date, "provider": provider.name,
            "status": "failed", "failure_reason": "retention_limit",
            "run_id": run_id, "exit_code": 1, "alerts": alerts,
        }

    # 3) 采当场 session（serial fail-closed），单事务写。
    current = _collect_one_day(
        store, provider, trade_date=trade_date, interval_minutes=interval_minutes,
        session_profile_version=session_profile_version,
        contract_version=contract_version,
        publication_delay_seconds=publication_delay_seconds,
        sleep=sleep, now_ts=now_ts, alerts=alerts,
    )
    if current.get("failure_reason") == "mapping_ambiguous":
        return {**current, "exit_code": 1, "alerts": alerts}
    if current.get("failure_reason") == "retention_limit":
        return {**current, "exit_code": 1, "alerts": alerts}

    # 4) 窗内追补（RB3 + M2）：对仍在窗内、库中无 complete 评估的交易日重拉。
    window_open_days = sorted(
        d for d in open_days
        if window_start <= d <= trade_date
        and _within_rolling_window(d, trade_date, rolling_window_days)
    )
    catchup: list[dict[str, Any]] = []
    for d in window_open_days:
        if d == trade_date:
            continue  # 当场已采
        day_res = _collect_one_day(
            store, provider, trade_date=d, interval_minutes=interval_minutes,
            session_profile_version=session_profile_version,
            contract_version=contract_version,
            publication_delay_seconds=publication_delay_seconds,
            sleep=sleep, now_ts=now_ts, alerts=alerts, catchup=True,
        )
        if day_res.get("collected"):
            catchup.append(day_res)

    # 5) 窗外 gap（RB3）：滚动窗外、库中无任何 complete 覆盖的交易日 → permanent_gap。
    out_window_days = sorted(
        d for d in open_days
        if window_start <= d <= trade_date
        and not _within_rolling_window(d, trade_date, rolling_window_days)
    )
    permanent_gaps = _detect_permanent_gaps(
        store, provider, out_window_days, interval_minutes=interval_minutes,
    )
    for gap in permanent_gaps:
        alerts.append({"alert": "permanent_gap", **gap})

    exit_code = 0 if current.get("status") == "completed" and not current.get(
        "any_symbol_failed"
    ) else (1 if current.get("any_symbol_failed") else 0)
    return {
        "mode": "close", "trade_date": trade_date, "provider": provider.name,
        "status": current.get("status"),
        "run_id": current.get("run_id"),
        "requested_symbols": current.get("requested_symbols"),
        "succeeded_symbols": current.get("succeeded_symbols"),
        "failed_symbols": current.get("failed_symbols"),
        "skipped_symbols": current.get("skipped_symbols"),
        "observations_written": current.get("observations_written"),
        "blobs_written": current.get("blobs_written"),
        "complete_assessed": current.get("complete_assessed"),
        "catchup_days": [c["trade_date"] for c in catchup],
        "permanent_gaps": [g["trade_date"] for g in permanent_gaps],
        "alerts": alerts,
        "exit_code": exit_code,
    }


def _within_rolling_window(day: str, anchor: str, window_days: int) -> bool:
    """``day`` 是否在以 ``anchor`` 为右端、宽 ``window_days`` 日历日的滚动窗内（含端点）。"""
    d = datetime.strptime(day, "%Y-%m-%d")
    a = datetime.strptime(anchor, "%Y-%m-%d")
    return 0 <= (a - d).days < window_days


def _collect_one_day(
    store: IntradayStore,
    provider: IntradayProvider,
    *,
    trade_date: str,
    interval_minutes: int,
    session_profile_version: str | None,
    contract_version: str | None,
    publication_delay_seconds: int | None,
    sleep: Callable[[float], None],
    now_ts: str | None,
    alerts: list[dict[str, Any]],
    catchup: bool = False,
) -> dict[str, Any]:
    """采单交易日（serial fail-closed + 有界重试 + 写 + 追认 + complete 评估）.

    **M2 外科式**：追补日（``catchup=True``）若该 instrument 已有 ``complete`` 评估 →
    **跳过取数与 canonical**（不靠写一次守卫兜底），杜绝改写 PIT 冻结历史。
    """
    symbols = store.list_registered_symbols(provider.name)
    observations: list[ObservationInput] = []
    obs_instrument_ids: list[int] = []
    skipped: list[str] = []
    failed: list[str] = []
    succeeded: list[str] = []
    raw_bytes = 0

    for symbol, asset_kind in symbols:
        res = store.resolve_instrument(symbol, provider.name, trade_date)
        if res.status is MappingStatus.UNKNOWN:
            skipped.append(symbol)  # 零 provider 调用
            continue
        if res.status is MappingStatus.AMBIGUOUS:
            run_id = store.record_terminal_failure(
                provider=provider.name, mode="close", trade_date=trade_date,
                failure_reason="mapping_ambiguous",
                error_summary=f"symbol {symbol} has >1 active mapping",
            )
            return {
                "trade_date": trade_date, "status": "failed",
                "failure_reason": "mapping_ambiguous", "run_id": run_id,
                "collected": False,
            }
        # M2：追补且已 complete → 外科式跳过（不重做 canonical/revision）。
        if catchup and store.has_complete_assessment(
            instrument_id=res.instrument_id, trade_date=trade_date,
            interval_minutes=interval_minutes, provider=provider.name,
        ):
            continue

        fetched = _fetch_with_retry(
            lambda rs=res, ak=asset_kind: provider.fetch_bars(
                rs.provider_symbol, interval_minutes=interval_minutes, asset_kind=ak,
            ),
            label=f"{provider.name} {symbol} {trade_date}",
            sleep=sleep,
        )
        if fetched.ok:
            # 仅采**当场** session 行（不得用滚动响应回填先前日；按 trade_date 过滤）。
            rows = _rows_for_trade_date(fetched.rows, trade_date)
            raw_bytes += _estimate_raw_bytes(rows)
            succeeded.append(symbol)
            eligibility = classify_eligibility(provider.name, reachable=True)
            observations.append(
                _make_observation(
                    res, provider.name, interval_minutes, asset_kind, rows,
                    fetched, eligibility.value,
                    session_profile_version, contract_version,
                )
            )
            obs_instrument_ids.append(res.instrument_id)
            # M1 漂移自报：每根 bar 记 retrieved_at - bar_end_ts，超 delay 即告警。
            _check_publication_delay(
                rows, fetched, provider.name, symbol, trade_date,
                publication_delay_seconds, now_ts, alerts,
            )
        else:
            failed.append(symbol)
            eligibility = classify_eligibility(provider.name, reachable=False)
            observations.append(
                _make_observation(
                    res, provider.name, interval_minutes, asset_kind, None,
                    fetched, eligibility.value,
                    session_profile_version, contract_version,
                )
            )

    # retention 单 run raw 字节硬限（写盘前）：超 → 终止 retention_limit、无可查行。
    if raw_bytes > INTRADAY_MAX_RAW_BYTES_PER_RUN:
        run_id = store.record_terminal_failure(
            provider=provider.name, mode="close", trade_date=trade_date,
            failure_reason="retention_limit",
            error_summary=(
                f"run raw bytes {raw_bytes} > limit {INTRADAY_MAX_RAW_BYTES_PER_RUN}"
            ),
        )
        return {
            "trade_date": trade_date, "status": "failed",
            "failure_reason": "retention_limit", "run_id": run_id,
            "collected": False,
        }

    if not observations:
        return {
            "trade_date": trade_date, "status": "completed", "run_id": None,
            "requested_symbols": len(symbols), "succeeded_symbols": [],
            "failed_symbols": [], "skipped_symbols": skipped,
            "observations_written": 0, "blobs_written": 0,
            "complete_assessed": [], "any_symbol_failed": False,
            "collected": False,
        }

    result = store.ingest_run(
        provider=provider.name, mode="close", trade_date=trade_date,
        observations=observations,
    )

    # canonical 追认 + complete 评估（仅成功 observation 有 blob；失败日不算 complete）。
    complete_assessed: list[int] = []
    if (
        result.status == "completed"
        and session_profile_version
        and contract_version
    ):
        store.certify_observations_to_canonical(run_id=result.run_id)
        expected_ends = _expected_bar_ends(store, session_profile_version)
        assessed_at = now_ts or _now_iso_shanghai()
        for iid in dict.fromkeys(obs_instrument_ids):
            # M2：追补日若已 complete 则前面已跳过；此处仅评估本次实采的 instrument。
            assessment = store.assess_complete(
                instrument_id=iid, trade_date=trade_date,
                interval_minutes=interval_minutes, provider=provider.name,
                contract_version=contract_version,
                session_profile_version=session_profile_version,
                expected_bar_ends=expected_ends, assessed_at=assessed_at,
            )
            if assessment["status"] == "passed":
                complete_assessed.append(iid)

    return {
        "trade_date": trade_date, "status": result.status,
        "run_id": result.run_id,
        "requested_symbols": len(symbols),
        "succeeded_symbols": succeeded,
        "failed_symbols": failed,
        "skipped_symbols": skipped,
        "observations_written": result.observations_written,
        "blobs_written": result.blobs_written,
        "complete_assessed": complete_assessed,
        "any_symbol_failed": bool(failed),
        "collected": True,
    }


def _make_observation(
    res: Any,
    provider_name: str,
    interval_minutes: int,
    asset_kind: str,
    rows: list[dict[str, Any]] | None,
    fetched: FetchResult,
    eligibility: str,
    session_profile_version: str | None,
    contract_version: str | None,
) -> ObservationInput:
    """构造 :class:`ObservationInput`（冻结 profile/contract 上下文供 U3 追认）.

    ``source_asof_ts`` 是 PIT 可见性键（U4 inherited 约束）：取 provider 自报最晚 bar
    时间（前向源已保守=收盘后），re-ingest/追补不覆写为更早值——honest late-biased。
    """
    return ObservationInput(
        instrument_id=res.instrument_id, provider=provider_name,
        interval_minutes=interval_minutes,
        request_meta={
            "method": "GET", "provider": provider_name,
            "params": {
                "symbol": res.provider_symbol, "asset_kind": asset_kind,
                "period": interval_minutes,
            },
        },
        payload_rows=rows or None,
        source_asof_ts=fetched.source_asof_ts,
        availability_class="forward_observed",
        eligibility=eligibility,
        status_code=fetched.status_code,
        error=fetched.error,
        frozen_session_profile_version=session_profile_version,
        frozen_contract_version=contract_version,
    )


def _rows_for_trade_date(
    rows: list[dict[str, Any]], trade_date: str
) -> list[dict[str, Any]]:
    """仅保留 ``trade_date`` 当场 session 行（不得用滚动响应回填先前日）.

    东财裸时间串前缀 ``YYYY-MM-DD``；过滤掉响应里其它日期的 bar，确保「当场 session-only」。
    无可解析时间戳的行保留（交给 U3 标 incomplete，不静默丢）。
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = r.get("时间")
        if ts is None:
            out.append(r)
            continue
        if str(ts).strip().startswith(trade_date):
            out.append(r)
    return out


def _estimate_raw_bytes(rows: list[dict[str, Any]]) -> int:
    """估算一组行序列化后的字节数（retention 单 run 限用，保守 UTF-8）。"""
    import json
    return len(json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8"))


def _check_publication_delay(
    rows: list[dict[str, Any]],
    fetched: FetchResult,
    provider_name: str,
    symbol: str,
    trade_date: str,
    publication_delay_seconds: int | None,
    now_ts: str | None,
    alerts: list[dict[str, Any]],
) -> None:
    """M1 漂移自报：每根 bar 记 ``retrieved_at - bar_end_ts``，超 delay 即告警.

    fail-loud、**不依赖** watch 模式。``retrieved_at`` 取 ``now_ts``（注入）或当下；
    ``bar_end_ts`` 取响应最晚 bar（``source_asof_ts``）。delay 未配则跳过（无基线可比）。
    """
    if publication_delay_seconds is None or fetched.source_asof_ts is None:
        return
    retrieved_at = now_ts or _now_iso_shanghai()
    try:
        drift = _drift_seconds(retrieved_at, fetched.source_asof_ts)
    except (ValueError, TypeError):
        return
    if drift > publication_delay_seconds:
        alerts.append({
            "alert": "publication_delay_exceeded",
            "provider": provider_name, "symbol": symbol,
            "trade_date": trade_date,
            "bar_end_ts": fetched.source_asof_ts,
            "retrieved_at": retrieved_at,
            "observed_delay_seconds": round(drift, 1),
            "configured_delay_seconds": publication_delay_seconds,
        })


def _detect_permanent_gaps(
    store: IntradayStore,
    provider: IntradayProvider,
    out_window_days: list[str],
    *,
    interval_minutes: int,
) -> list[dict[str, Any]]:
    """窗外、库中无任何 ``complete`` 覆盖的交易日 → ``permanent_gap``（仅历史 proxy 可填）.

    对每个窗外交易日，查该 provider 下所有注册 instrument 是否有 ``complete`` 评估；
    全无 → 该日整体标 permanent_gap（窗外不可追补，重拉只会拿到当前窗 session）。
    """
    gaps: list[dict[str, Any]] = []
    instrument_ids = _provider_instrument_ids(store, provider.name)
    for d in out_window_days:
        has_any = any(
            store.has_complete_assessment(
                instrument_id=iid, trade_date=d,
                interval_minutes=interval_minutes, provider=provider.name,
            )
            for iid in instrument_ids
        )
        if not has_any:
            gaps.append({
                "trade_date": d, "provider": provider.name,
                "reason": "outside_rolling_window_no_coverage",
            })
    return gaps


def _provider_instrument_ids(store: IntradayStore, provider: str) -> list[int]:
    """该 provider 下今日（开放区间）可解析的 instrument_id 列表（permanent_gap 用）。"""
    today = _today_shanghai()
    out: list[int] = []
    for symbol, _ in store.list_registered_symbols(provider):
        res = store.resolve_instrument(symbol, provider, today)
        if res.status is MappingStatus.RESOLVED and res.instrument_id is not None:
            out.append(res.instrument_id)
    return out


def _now_iso_shanghai() -> str:
    return datetime.now(tz=_SHANGHAI).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# watch 模式（shadow / observability only —— 不接策略）
# --------------------------------------------------------------------------- #


def collect_watch(
    store: IntradayStore,
    provider: IntradayProvider,
    *,
    trade_date: str,
    watchlist: list[str] | None = None,
    interval_minutes: int = 5,
    session_profile_version: str | None = None,
    contract_version: str | None = None,
    publication_delay_seconds: int | None = None,
    now_ts: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """盘中小 watchlist 轮询（shadow / 可观测性）—— **不接策略**，喂 publication_delay 复核.

    刻意最小：对配置的小 watchlist 取一轮当场 session，落 observation（与收盘同 store
    路径），并跑 M1 漂移自报。不做 canonical 评估、不产 complete（盘中 session 未收盘）。
    单独启用前不接任何策略消费（PRD scope）。
    """
    alerts: list[dict[str, Any]] = []
    registered = dict(store.list_registered_symbols(provider.name))
    targets = [s for s in (watchlist or list(registered)) if s in registered]
    observations: list[ObservationInput] = []
    for symbol in targets:
        res = store.resolve_instrument(symbol, provider.name, trade_date)
        if res.status is not MappingStatus.RESOLVED:
            continue
        asset_kind = registered[symbol]
        fetched = _fetch_with_retry(
            lambda rs=res, ak=asset_kind: provider.fetch_bars(
                rs.provider_symbol, interval_minutes=interval_minutes, asset_kind=ak,
            ),
            label=f"watch {provider.name} {symbol} {trade_date}",
            sleep=sleep,
        )
        eligibility = classify_eligibility(provider.name, reachable=fetched.ok)
        observations.append(
            _make_observation(
                res, provider.name, interval_minutes, asset_kind,
                fetched.rows if fetched.ok else None, fetched, eligibility.value,
                session_profile_version, contract_version,
            )
        )
        if fetched.ok:
            _check_publication_delay(
                fetched.rows, fetched, provider.name, symbol, trade_date,
                publication_delay_seconds, now_ts, alerts,
            )
    run_id = None
    if observations:
        run_id = store.ingest_run(
            provider=provider.name, mode="watch", trade_date=trade_date,
            observations=observations,
        ).run_id
    return {
        "mode": "watch", "trade_date": trade_date, "provider": provider.name,
        "run_id": run_id, "polled_symbols": targets, "alerts": alerts,
        "exit_code": 0,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="分时收盘采集器（U5 完整加固）")
    p.add_argument("--mode", choices=["close", "watch"], default="close",
                   help="采集模式（close 收盘；watch shadow-only 盘中轮询）")
    p.add_argument("--trade-date", default=None,
                   help="交易日 YYYY-MM-DD（默认今天；走 trade_cal 确认）")
    p.add_argument("--interval", type=int, default=1, help="分钟周期（默认 1）")
    p.add_argument("--profile-version", default=None,
                   help="已注册 session_profile 版本（缺则只落 blob 不 canonical）")
    p.add_argument("--contract-version", default=None,
                   help="已注册 provider_bar_contract 版本")
    p.add_argument("--dry-run", action="store_true", help="只规划不取数不落盘")
    p.add_argument("--db", default=None, help="库路径（默认 INTRADAY_DB / STATE_ROOT）")
    return p


def main(argv: list[str] | None = None) -> int:
    import json

    args = build_argparser().parse_args(argv)
    db_path = Path(args.db) if args.db else INTRADAY_DB
    store = IntradayStore(db_path)
    provider = EastmoneyAkshareProvider()
    trade_date = args.trade_date or _today_shanghai()

    if args.mode == "watch":
        summary = collect_watch(
            store, provider, trade_date=trade_date,
            interval_minutes=args.interval,
            session_profile_version=args.profile_version,
            contract_version=args.contract_version,
        )
    else:
        summary = collect_close(
            store, provider, trade_date=trade_date,
            interval_minutes=args.interval,
            session_profile_version=args.profile_version,
            contract_version=args.contract_version,
            dry_run=args.dry_run,
        )
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return int(summary.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
