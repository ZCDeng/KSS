#!/usr/bin/env python3
"""趋势页单日归档构建器（U1）。

给定日期 → 聚合三类内容落 kss.db trends_days 表：
  - 主力资金：北向净额（hsgt_daily.parquet，需 pandas+parquet 引擎）+ A500ETF（best-effort）
  - 板块：etf_radar_snapshots（kss.db）经 bridge _pulse_from_dict 取 top 主题
  - 推荐：bridge _build_logmv_picks(date) 重算 + T+1/T+5/T+20（_horizon_return）
          并记 asof 实际落点，防停牌/跳空把远期行当近期。

被 backfill_trends.py（U2，历史批量）与 archive_trends_daily.sh（U3，每日）共用。
重活（parquet）走 .venv-desktop；bridge 函数为 stdlib，可直接 import。
缺源字段置 null + flags 标记，stderr 记缺失（Fail loud）。

用法： python archive_trends_daily.py [YYYY-MM-DD]   # 缺省=最新交易日
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ROOT 仅作**代码根**（bridge/kss 包 import）；数据路径一律走 kss.config.paths
# （吃 KSS_PROJECT_ROOT/KSS_STATE_ROOT env）——bundle 副本执行时 __file__ 推导的
# ROOT 落在 .app/Contents/Resources，往里写 = 07-14 PermissionError 实锤（R6 U3）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import kss_app_bridge as kb  # noqa: E402  bridge 的 stdlib 函数（picks 重算 / T+N / 板块切片）
from kss.config.paths import KSS_DB, MACRO_ROOT, PROJECT_ROOT  # noqa: E402

HSGT_PARQUET = MACRO_ROOT / "hsgt_daily.parquet"
NORTH_HEAT_REF_YI = 60.0  # 北向量级归一化参考（亿元），|净额|≥此值热度封顶


def _log(msg: str) -> None:
    print(f"[trends] {msg}", file=sys.stderr)


def _compact(date: str) -> str:
    """YYYY-MM-DD → YYYYMMDD。"""
    return date.replace("-", "")


def _north_for_date(compact_date: str) -> dict[str, Any] | None:
    """北向净额（亿元，方向）。需 pandas + parquet 引擎；缺则返回 None 并记日志。"""
    if not HSGT_PARQUET.exists():
        _log(f"hsgt parquet 缺失: {HSGT_PARQUET}")
        return None
    try:
        import pandas as pd  # 仅此处需重依赖
        df = pd.read_parquet(HSGT_PARQUET)
    except Exception as exc:  # noqa: BLE001  含缺 parquet 引擎
        _log(f"读 hsgt parquet 失败（装 pyarrow?）: {exc}")
        return None
    row = df[df["trade_date"].astype(str) == compact_date]
    if row.empty:
        _log(f"hsgt 无 {compact_date} 行（非交易日?）")
        return None
    try:
        wan = float(row.iloc[0]["north_money"])  # 万元
    except (TypeError, ValueError):
        return None
    yi = round(wan / 1e4, 2)
    return {"money": yi, "unit": "亿", "dir": "in" if yi > 0 else ("out" if yi < 0 else "flat")}


_ETFS = (("563360.SH", "A500ETF"), ("159361.SZ", "A500ETF"))
_ETF_HISTORY: dict[str, dict[str, float]] | None = None  # {code: {compact_date: pct}}


def _ensure_tushare_token() -> None:
    """token 不在 env 则从 .env 读（backfill/daily 都能自动拿到，无需手动 export）。"""
    if os.environ.get("TUSHARE_TOKEN"):
        return
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("TUSHARE_TOKEN="):
            os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip().strip('"')
            break


def _etf_history() -> dict[str, dict[str, float]]:
    """懒加载并缓存两只 A500ETF 的 fund_daily 历史 {code: {YYYYMMDD: pct}}。
    失败（无 token / 网络 / tushare 缺失）→ 返回空 dict，ETF 段优雅置 null。仅 2 次 API 调用。"""
    global _ETF_HISTORY
    if _ETF_HISTORY is not None:
        return _ETF_HISTORY
    _ETF_HISTORY = {}
    _ensure_tushare_token()
    try:
        from kss.data.tushare_client import TushareClient, _fetch_with_retry
        pro = TushareClient().get_pro()
    except Exception as exc:  # noqa: BLE001
        _log(f"ETF 历史不可用（tushare/token?）: {exc}")
        return _ETF_HISTORY
    for code, _name in _ETFS:
        try:
            df = _fetch_with_retry(
                lambda c=code: pro.fund_daily(ts_code=c, start_date="20250101", end_date="20261231"),
                f"fund_daily {code}",
            )
            if df is None or df.empty:
                continue
            _ETF_HISTORY[code] = {
                str(r["trade_date"]): round(float(r["pct_chg"]), 2)
                for _, r in df.iterrows()
                if r.get("pct_chg") is not None
            }
        except Exception as exc:  # noqa: BLE001
            _log(f"取 {code} fund_daily 失败: {exc}")
    return _ETF_HISTORY


def _etfs_for_date(date: str) -> list[dict[str, Any]] | None:
    """A500ETF 当日涨跌幅：从 fund_daily 历史缓存取（回填+going-forward 统一口径）。"""
    cd = _compact(date)
    hist = _etf_history()
    out = [
        {"code": code, "name": name, "pct": hist[code][cd]}
        for code, name in _ETFS
        if code in hist and cd in hist[code]
    ]
    return out or None


def _sector_for_date(compact_date: str) -> tuple[list[dict[str, Any]], int, float | None]:
    """板块 top 主题（对齐 _pulse_from_dict 的 grade 字段）+ 总数 + 强度归一化。"""
    from kss.storage.etf_radar import read_by_date

    d = read_by_date(compact_date, KSS_DB)
    if d is None:
        _log(f"etf_radar 无 {compact_date}（板块段空）")
        return [], 0, None
    pulse = kb._pulse_from_dict(d)
    if not pulse:
        return [], 0, None
    themes = pulse.get("themes") or []
    top = [
        {"name": t.get("name", ""), "grade": t.get("grade", ""), "past5Ret": t.get("past5Ret")}
        for t in themes[:5]
    ]
    strong = sum(1 for t in themes if "强" in str(t.get("grade", "")))
    sector_heat = round(min(strong / 6.0, 1.0), 3) if themes else None
    return top, len(themes), sector_heat


def _horizon_with_asof(symbol: str, prediction_date: str, hold: int) -> tuple[float | None, str | None]:
    """复用 _horizon_return 的口径，额外回传实际落点 trade_date（asof），防按行偏移张冠李戴。"""
    ret = kb._horizon_return(symbol, prediction_date, hold)
    asof = None
    try:
        path = kb._stock_file(symbol)
        if path.exists():
            rows = kb._read_csv_rows(path)
            future = [r for r in rows if r.get("trade_date", "") > prediction_date]
            if len(future) >= hold + 1:
                asof = future[hold].get("trade_date")
    except Exception:  # noqa: BLE001
        pass
    return ret, asof


def _recs_for_date(date: str) -> tuple[list[dict[str, Any]], float | None]:
    """重算 log_mv picks + T+1/T+5/T+20（含 asof）。picks 天然属 cs_data 池，csv 必存在。
    注意：cs_data 的 trade_date 为带横杠 YYYY-MM-DD，故此处传 date 原样（非 _compact）。"""
    try:
        _, picks = kb._build_logmv_picks(date)
    except Exception as exc:  # noqa: BLE001
        _log(f"重算 log_mv picks 失败: {exc}")
        return [], None
    recs: list[dict[str, Any]] = []
    t5_vals: list[float] = []
    for p in picks:
        symbol = p.get("symbol", "")
        fwd: dict[str, Any] = {}
        asof_last = None
        for key, hold in kb._HORIZONS:  # (ret1d,1)/(ret5d,5)/(ret20d,20)
            short = {"ret1d": "t1", "ret5d": "t5", "ret20d": "t20"}[key]
            ret, asof = _horizon_with_asof(symbol, date, hold)
            fwd[short] = round(ret * 100, 2) if ret is not None else None  # 百分比
            if asof:
                asof_last = asof
        if fwd.get("t5") is not None:
            t5_vals.append(fwd["t5"])
        fwd["asof"] = asof_last
        recs.append({"symbol": symbol, "name": p.get("name", ""), "fwd": fwd})
    rec_avg = round(sum(t5_vals) / len(t5_vals), 2) if t5_vals else None
    return recs, rec_avg


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# 增量资金合成（强度+方向）：宽基(两只 A500ETF 涨跌幅，主导) + 北向(亿，佐证)。
# 单位不同故各自归一后加权：ETF 均值 0.6、北向 0.4；结果 -1..1（红流入/绿流出）。
NORTH_NORM_YI = 50.0   # 北向 ±50 亿归一到 ±1
ETF_NORM_PCT = 1.5     # ETF ±1.5% 归一到 ±1
W_NORTH = 0.4
W_ETF = 0.6


def _inflow_score(north: dict[str, Any] | None, etfs: list[dict[str, Any]] | None) -> tuple[float | None, str]:
    """三指标合成增量资金分（-1..1）+ 方向。缺项按可得指标退化加权，全缺返回 None。"""
    parts: list[tuple[float, float]] = []  # (归一值, 权重)
    if north is not None:
        parts.append((_clamp(north["money"] / NORTH_NORM_YI), W_NORTH))
    etf_pcts = [e["pct"] for e in (etfs or []) if e.get("pct") is not None]
    if etf_pcts:
        avg = sum(etf_pcts) / len(etf_pcts)
        parts.append((_clamp(avg / ETF_NORM_PCT), W_ETF))
    if not parts:
        return None, "flat"
    wsum = sum(w for _, w in parts)
    score = round(sum(v * w for v, w in parts) / wsum, 3)
    return score, ("in" if score > 0 else ("out" if score < 0 else "flat"))


def build_trend_day(date: str) -> dict[str, Any]:
    """聚合单日 trends dict（契约见计划 U1）。缺源置 null + flags，不抛。"""
    cd = _compact(date)
    north = _north_for_date(cd)
    etfs = _etfs_for_date(date)
    sector_top, sector_count, sector_heat = _sector_for_date(cd)
    recs, rec_avg = _recs_for_date(date)

    heat = None
    if north is not None:
        heat = round(min(abs(north["money"]) / NORTH_HEAT_REF_YI, 1.0), 3)

    inflow_score, inflow_dir = _inflow_score(north, etfs)
    top_sector = sector_top[0]["name"] if sector_top else None

    return {
        "date": date,
        "isTrading": north is not None or sector_count > 0 or bool(recs),
        "north": north,
        "etfs": etfs,
        "inflowScore": inflow_score,     # 增量资金合成强度(-1..1)，驱动顶部热力图
        "inflowDir": inflow_dir,         # in/out/flat
        "sectorTop": sector_top,
        "sectorCount": sector_count,
        "topSector": top_sector,         # 当天最强主题名，日历格子直观显示
        "recs": recs,
        "recCount": len(recs),
        "recAvgFwd": rec_avg,
        "heat": heat,
        "sectorHeat": sector_heat,
        "flags": {
            "north": north is not None,
            "etf": bool(etfs),
            "sector": sector_count > 0,
            "recs": bool(recs),
        },
    }


def write_trend_day(date: str, force: bool = True) -> dict[str, Any]:
    """写 kss.db trends_days 表。返回写入（或已存在）的 payload。"""
    from kss.storage.trends import day_exists, read_by_date, write_day

    db_path = KSS_DB
    if not force and day_exists(date, db_path):
        return read_by_date(date, db_path)
    payload = build_trend_day(date)
    write_day(payload, db_path)
    return payload


def _compact_to_dash(cd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD。"""
    return f"{cd[:4]}-{cd[4:6]}-{cd[6:8]}"


def _latest_trading_date() -> str:
    """缺省日期 = cs_data 最新交易日（当天 8:30 已入库，不随北向 T+1 滞后）。

    锚 cs_data 而非 hsgt：北向资金次日早 8:35 才到，若按 hsgt 取最新日，
    日历恒落后一个交易日。cs_data 在当天开盘前更新，当晚归档即可写当天。
    回退链：cs_data → hsgt parquet（可能滞后一日）→ 系统今日。
    """
    try:
        latest = kb._latest_kcb_date(kb._rows_by_symbol())  # cs_data 的 trade_date 即 YYYY-MM-DD
        if latest:
            return latest
    except Exception as exc:  # noqa: BLE001
        _log(f"cs_data 最新日不可用，回退 hsgt: {exc}")
    try:
        import pandas as pd
        df = pd.read_parquet(HSGT_PARQUET)
        return _compact_to_dash(str(df["trade_date"].astype(str).max()))
    except Exception:  # noqa: BLE001
        from datetime import date as _d
        return _d.today().isoformat()


# 每日 going-forward 归档的回扫窗口（交易日数）。
# 必要性：当天 north 当晚还没到（T+1），写出时 north=null；次日 north 入库后，
# 下一次运行回扫重写即自愈。窗口 ≥2 保证「昨天」的 north 今晚补齐；取 3 留余量
# （容忍一次漏跑 / 长假），write_trend_day 幂等覆盖，重写无副作用。
ARCHIVE_WINDOW = 3
# 缺口回补上限（交易日数）：单次运行最多回补这么多缺失日，防长期停更后一次跑爆。
MAX_BACKFILL = 15


def _trade_axis_dates(latest: str) -> list[str]:
    """交易日轴（升序 YYYY-MM-DD，≤ latest）。

    R6 KTD2：轴改锚 **cs_data 面板**的 trade_date 并集（本地文件、无网络）——
    hsgt 北向 T+1 滞后曾把日历恒压后 1-3 个交易日（07-13 健康跑只归档到 07-10）。
    cs_data 出现过的 trade_date 即已收盘交易日。面板不可用时退回 hsgt 轴。
    """
    axis: set[str] = set()
    try:
        rows_by_symbol = kb._rows_by_symbol()
        for rows in list(rows_by_symbol.values())[:5]:  # 5 只并集足够覆盖轴（防单票停牌洞）
            axis |= {str(r.get("trade_date", "")) for r in rows if r.get("trade_date")}
    except Exception as exc:  # noqa: BLE001
        _log(f"cs_data 面板轴不可用，退回 hsgt: {exc}")
    if not axis:
        try:
            import pandas as pd
            df = pd.read_parquet(HSGT_PARQUET)
            axis = {_compact_to_dash(str(x)) for x in df["trade_date"].astype(str).tolist()}
        except Exception as exc:  # noqa: BLE001
            _log(f"hsgt 轴亦不可用，仅归档 latest: {exc}")
    axis.add(latest)
    return sorted(d for d in axis if len(d) == 10 and d <= latest)


def _pending_dates(latest: str) -> list[str]:
    """候选归档日（升序）：轴上 (已归档缺口) ∪ 尾部 ARCHIVE_WINDOW 回扫（north 自愈）。

    缺口 = 轴尾 MAX_BACKFILL 内 trends_days 尚无行的日期——停更多日后一次跑齐
    （R6 R4：07-13/07-14 回补即走此路径），幂等覆盖无副作用。
    """
    from kss.storage.trends import day_exists

    axis = _trade_axis_dates(latest)
    if not axis:
        return [latest]
    tail_rescan = axis[-ARCHIVE_WINDOW:]
    gaps = [d for d in axis[-MAX_BACKFILL:] if not day_exists(d, KSS_DB)]
    return sorted(set(gaps) | set(tail_rescan))


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        dates = [argv[1]]  # 显式日期：只归档这一天
    else:
        dates = _pending_dates(_latest_trading_date())
    for date in dates:
        payload = write_trend_day(date)
        flags = payload["flags"]
        _log(f"写 {date}: north={flags['north']} etf={flags['etf']} "
             f"sector={flags['sector']}({payload['sectorCount']}) recs={flags['recs']}({payload['recCount']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
