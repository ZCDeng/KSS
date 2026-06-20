#!/usr/bin/env python3
"""趋势页单日归档构建器（U1）。

给定日期 → 聚合三类内容落 storage/trends/YYYY-MM-DD.json：
  - 主力资金：北向净额（hsgt_daily.parquet，需 pandas+parquet 引擎）+ A500ETF（best-effort）
  - 板块：etf_radar/<YYYYMMDD>.json 经 bridge _pulse_from_dict 取 top 主题
  - 推荐：bridge _build_logmv_picks(date) 重算 + T+1/T+5/T+20（_horizon_return）
          并记 asof 实际落点，防停牌/跳空把远期行当近期。

被 backfill_trends.py（U2，历史批量）与 archive_trends_daily.sh（U3，每日）共用。
重活（parquet）走 .venv-desktop；bridge 函数为 stdlib，可直接 import。
原子写：.tmp → os.replace。缺源字段置 null + flags 标记，stderr 记缺失（Fail loud）。

用法： python archive_trends_daily.py [YYYY-MM-DD]   # 缺省=最新交易日
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import kss_app_bridge as kb  # noqa: E402  bridge 的 stdlib 函数（picks 重算 / T+N / 板块切片）

TRENDS_DIR = ROOT / "storage" / "trends"
HSGT_PARQUET = ROOT / "storage" / "macro" / "hsgt_daily.parquet"
ETF_RADAR_DIR = ROOT / "storage" / "etf_radar"
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
    env = ROOT / ".env"
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
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
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
    fp = ETF_RADAR_DIR / f"{compact_date}.json"
    if not fp.exists():
        _log(f"etf_radar 无 {compact_date}.json（板块段空）")
        return [], 0, None
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _log(f"读 etf_radar 失败: {exc}")
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

    return {
        "date": date,
        "isTrading": north is not None or sector_count > 0 or bool(recs),
        "north": north,
        "etfs": etfs,
        "sectorTop": sector_top,
        "sectorCount": sector_count,
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


def write_trend_day(date: str, force: bool = True) -> Path:
    """原子写 storage/trends/<date>.json。"""
    TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    out = TRENDS_DIR / f"{date}.json"
    if out.exists() and not force:
        return out
    payload = build_trend_day(date)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)  # 原子
    return out


def _latest_trading_date() -> str:
    """缺省日期 = hsgt parquet 最新交易日（YYYY-MM-DD）。"""
    try:
        import pandas as pd
        df = pd.read_parquet(HSGT_PARQUET)
        cd = str(df["trade_date"].astype(str).max())
        return f"{cd[:4]}-{cd[4:6]}-{cd[6:8]}"
    except Exception:  # noqa: BLE001
        from datetime import date as _d
        return _d.today().isoformat()


def main(argv: list[str]) -> int:
    date = argv[1] if len(argv) > 1 else _latest_trading_date()
    out = write_trend_day(date)
    payload = json.loads(out.read_text(encoding="utf-8"))
    flags = payload["flags"]
    _log(f"写 {out.name}: north={flags['north']} etf={flags['etf']} "
         f"sector={flags['sector']}({payload['sectorCount']}) recs={flags['recs']}({payload['recCount']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
