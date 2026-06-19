#!/usr/bin/env python3
"""JSON bridge for the KSS macOS desktop app.

The desktop app is a thin native client over KSS' existing local artifacts.
Read paths intentionally avoid pandas and network calls so app startup stays
fast. Explicit ``run`` commands may write local audit artifacts when the user
clicks a task button in the app.
"""

from __future__ import annotations

import csv
import glob
import json
import math
import os
import re
import subprocess
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAPER_DIR = PROJECT_ROOT / "storage" / "paper_trade"
REVIEW_DIR = PROJECT_ROOT / "storage" / "daily_review"
REPORT_DIR = PROJECT_ROOT / "storage" / "reports"
BJ_SCAN_DIR = REPORT_DIR / "bj50_scan"
BJ_CACHE_DIR = PROJECT_ROOT / "storage" / "bj_cache"
APP_RUN_DIR = PROJECT_ROOT / "storage" / "app_runs"
TASK_LOG_PATH = APP_RUN_DIR / "kss_desktop_tasks.jsonl"
NAMES_PATH = PROJECT_ROOT / "storage" / "stock_names.csv"
SUPPLY_CHAIN_PATH = PROJECT_ROOT / "kss" / "config" / "supply_chain.yaml"
TOP_N = 5
TOP_PCT = 0.2
FRESHNESS_DAYS = 7
REQUIRED_FULL_MODULES = ("pandas", "lightgbm", "tushare", "akshare")
ETF_PARQUET_MODULES = ("pyarrow", "fastparquet")


def _json_dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def _shorten(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated {len(text) - limit} chars ..."


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except UnicodeDecodeError:
        with path.open("r", encoding="gb18030", newline="") as f:
            return list(csv.DictReader(f))


def _load_names() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    supply_names = _load_supply_chain_names()
    if NAMES_PATH.exists():
        rows = _read_csv_rows(NAMES_PATH)
        for row in rows:
            symbol = row.get("ts_code", "")
            if symbol:
                out[symbol] = {
                    "name": row.get("name", "") or "",
                    "industry": row.get("industry", "") or "",
                    "concept": row.get("concept", "") or "",
                }
    for symbol, meta in supply_names.items():
        existing = out.get(symbol, {})
        out[symbol] = {
            "name": existing.get("name") or meta.get("name", ""),
            "industry": existing.get("industry") or meta.get("industry", ""),
            "concept": existing.get("concept") or meta.get("concept", ""),
        }
    return out


def _load_supply_chain_names() -> dict[str, dict[str, str]]:
    if not SUPPLY_CHAIN_PATH.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    current: str | None = None
    in_chains = False
    chains: list[str] = []
    for raw in SUPPLY_CHAIN_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^\s{2}((?:300|301|302|688)\d{3}\.(?:SZ|SH)):\s*$", raw)
        if match:
            if current and current in out:
                out[current]["concept"] = " / ".join(chains)
            current = match.group(1)
            chains = []
            in_chains = False
            out[current] = {"name": "", "industry": "", "concept": ""}
            continue
        if current is None:
            continue
        name_match = re.match(r"^\s{4}name:\s*(.+?)\s*$", raw)
        if name_match:
            out[current]["name"] = name_match.group(1).strip().strip("\"'")
            continue
        if re.match(r"^\s{4}demand_chains:\s*$", raw):
            in_chains = True
            continue
        if in_chains:
            chain_match = re.match(r"^\s{4}-\s*(.+?)\s*$", raw)
            if chain_match:
                chains.append(chain_match.group(1).strip().strip("\"'"))
                if not out[current]["industry"]:
                    out[current]["industry"] = chains[0]
                continue
            if raw.startswith("    ") and not raw.startswith("    -"):
                in_chains = False
    if current and current in out:
        out[current]["concept"] = " / ".join(chains)
    return {symbol: meta for symbol, meta in out.items() if meta.get("name")}


def _stock_file(symbol: str) -> Path:
    code = symbol.split(".")[0].replace("cs_data_", "")
    return PROJECT_ROOT / f"cs_data_{code}.csv"


def _stock_summary(path: Path, names: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    rows = _read_csv_rows(path)
    if not rows:
        return None
    latest = rows[-1]
    symbol = latest.get("ts_code") or path.stem.replace("cs_data_", "") + ".SH"
    meta = names.get(symbol, {})
    closes = [_safe_float(row.get("close")) for row in rows]
    closes_clean = [v for v in closes if v is not None]
    last20 = closes_clean[-20:]
    return {
        "symbol": symbol,
        "name": meta.get("name", ""),
        "industry": meta.get("industry", ""),
        "concept": meta.get("concept", ""),
        "latestDate": latest.get("trade_date", ""),
        "close": _safe_float(latest.get("close")),
        "pctChange": _safe_float(latest.get("pct_chg")),
        "turnoverRate": _safe_float(latest.get("turnover_rate")),
        "amount": _safe_float(latest.get("amount")),
        "pe": _safe_float(latest.get("pe")),
        "pb": _safe_float(latest.get("pb")),
        "totalMv": _safe_float(latest.get("total_mv")),
        "ma5": _mean(closes_clean[-5:]),
        "ma20": _mean(last20),
        "high20": max(last20) if last20 else None,
        "low20": min(last20) if last20 else None,
    }


def _load_stock_summaries(names: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    paths = sorted(PROJECT_ROOT.glob("cs_data_*.csv"))
    summaries = [_stock_summary(path, names) for path in paths]
    combined = [item for item in summaries if item is not None] + _bj_stock_summaries()
    return sorted(combined, key=lambda item: (item.get("symbol") or ""))


def _latest_paper_log() -> dict[str, Any] | None:
    files = sorted(PAPER_DIR.glob("*.json"))
    for path in reversed(files):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _tracking_return(symbol: str, prediction_date: str) -> float | None:
    path = _stock_file(symbol)
    if not path.exists():
        return None
    rows = _read_csv_rows(path)
    future = [row for row in rows if row.get("trade_date", "") > prediction_date]
    if len(future) < 2:
        return None
    t1 = _safe_float(future[0].get("open"))
    t2 = _safe_float(future[1].get("open"))
    if t1 in (None, 0) or t2 is None:
        return None
    return t2 / t1 - 1


def _recommendations(
    names: dict[str, dict[str, str]],
    stock_by_symbol: dict[str, dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    log = _latest_paper_log()
    if not log:
        return None, []
    date = log.get("prediction_date")
    items: list[dict[str, Any]] = []
    for pick in log.get("picks", []):
        symbol = pick.get("symbol", "")
        meta = names.get(symbol, {})
        stock = stock_by_symbol.get(symbol, {})
        ret = _tracking_return(symbol, date) if date else None
        if ret is None:
            status = "waiting T+2"
        elif ret >= 0:
            status = "positive"
        else:
            status = "negative"
        items.append({
            "date": date or "",
            "symbol": symbol,
            "name": meta.get("name", ""),
            "industry": meta.get("industry", ""),
            "rank": int(pick.get("rank_position", len(items) + 1)),
            "weight": _safe_float(pick.get("planned_weight")) or 0,
            "factorValue": _safe_float(pick.get("factor_value")),
            "latestClose": stock.get("close"),
            "trackingReturn": ret,
            "status": status,
        })
    return date, items


def _reviews() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(REVIEW_DIR.glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.rstrip() for line in text.splitlines()]
        title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), path.stem)
        body_lines = [line for line in lines if line.strip() and not line.startswith("#")]
        excerpt = "\n".join(body_lines[:16])
        symbols = sorted(set(re.findall(r"\b(?:688|300|301|920)\d{3}(?:\.(?:SH|SZ|BJ))?\b", text)))
        out.append({
            "date": path.stem,
            "title": title,
            "excerpt": excerpt,
            "path": str(path.relative_to(PROJECT_ROOT)),
            "focusSymbols": symbols[:12],
        })
    return out


def _report_metrics(text: str) -> list[dict[str, str]]:
    metrics: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip().strip("*") for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        joined = " ".join(cells)
        if any(token in joined for token in ("Sharpe", "年化", "最大回撤", "DSR", "胜率")):
            metrics.append({"name": cells[0], "value": " | ".join(cells[1:4])})
        if len(metrics) >= 8:
            break
    if metrics:
        return metrics

    patterns = [
        ("Sharpe", r"Sharpe[^0-9+\-]*(?P<v>[+\-]?\d+(?:\.\d+)?)"),
        ("年化", r"年化[^0-9+\-]*(?P<v>[+\-]?\d+(?:\.\d+)?%)"),
        ("最大回撤", r"最大回撤[^0-9+\-]*(?P<v>[+\-]?\d+(?:\.\d+)?%)"),
    ]
    for name, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            metrics.append({"name": name, "value": match.group("v")})
    return metrics


def _backtest_reports() -> list[dict[str, Any]]:
    candidates = [
        REPORT_DIR / "kss_desktop_logmv_backtest.md",
        REPORT_DIR / "kss_desktop_radar_archive_analysis.md",
        REPORT_DIR / "kcb50_ultimate_report.md",
        REPORT_DIR / "etf_radar_backtest_20260607.md",
        REPORT_DIR / "sample_weight_ab.md",
        REPORT_DIR / "alpha158_screening.md",
        REPORT_DIR / "kcb50_wf_factor_selection_report.md",
        REPORT_DIR / "kcb50_lgb_cross_section_report.md",
    ]
    out: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.rstrip() for line in text.splitlines()]
        title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), path.stem)
        excerpt_lines = [line for line in lines if line.strip() and not line.startswith("#") and not line.startswith("|")]
        out.append({
            "title": title,
            "path": str(path.relative_to(PROJECT_ROOT)),
            "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "metrics": _report_metrics(text),
            "excerpt": "\n".join(excerpt_lines[:10]),
        })
    return out


def _resolve_markdown_path(path_text: str) -> Path:
    raw = Path(path_text)
    if raw.is_absolute():
        raise SystemExit("report path must be relative to the project root")
    path = (PROJECT_ROOT / raw).resolve()
    try:
        path.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit("report path escapes the project root") from exc
    if path.suffix.lower() != ".md":
        raise SystemExit("report path must point to a markdown file")
    if not path.exists():
        raise SystemExit(f"report not found: {path_text}")
    return path


def report_detail(path_text: str) -> dict[str, Any]:
    path = _resolve_markdown_path(path_text)
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.rstrip() for line in text.splitlines()]
    title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), path.stem)
    return {
        "title": title,
        "path": str(path.relative_to(PROJECT_ROOT)),
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "text": text,
    }


def _python_candidates() -> list[Path]:
    raw = [
        PROJECT_ROOT / ".venv-desktop" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "bin" / "python",
        Path("/Users/zcdeng/.local/bin/python3.11"),
        Path("/opt/homebrew/bin/python3"),
        Path("/usr/bin/python3"),
    ]
    out: list[Path] = []
    for path in raw:
        if path.exists() and os.access(path, os.X_OK) and path not in out:
            out.append(path)
    return out


def _missing_modules(path: Path, modules: tuple[str, ...]) -> list[str] | None:
    code = (
        "import importlib.util, json, sys; "
        f"mods={list(modules)!r}; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(json.dumps({'executable': sys.executable, 'missing': missing}))"
    )
    proc = subprocess.run(
        [str(path), "-c", code],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        return list(modules)
    payload = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
    missing = payload.get("missing", list(modules))
    return list(missing)


def _has_any_module(path: Path, modules: tuple[str, ...]) -> tuple[bool, list[str]]:
    missing = _missing_modules(path, modules) or list(modules)
    return len(missing) < len(modules), missing


def _check_python(path: Path) -> dict[str, Any]:
    try:
        missing = _missing_modules(path, REQUIRED_FULL_MODULES) or []
        _, missing_etf = _has_any_module(path, ETF_PARQUET_MODULES)
        return {
            "path": str(path),
            "usable": not missing,
            "missingModules": missing,
            "missingOptionalModules": {"etfRadarBacktest": missing_etf},
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "path": str(path),
            "usable": False,
            "missingModules": list(REQUIRED_FULL_MODULES),
            "missingOptionalModules": {"etfRadarBacktest": list(ETF_PARQUET_MODULES)},
            "error": repr(exc),
        }


def _python_env_status() -> dict[str, Any]:
    candidates = [_check_python(path) for path in _python_candidates()]
    selected = next((item for item in candidates if item["usable"]), None)
    return {
        "selected": selected["path"] if selected else None,
        "usable": selected is not None,
        "requiredModules": list(REQUIRED_FULL_MODULES),
        "optionalModules": {"etfRadarBacktest": list(ETF_PARQUET_MODULES)},
        "candidates": candidates,
    }


def _full_python() -> Path | None:
    selected = _python_env_status().get("selected")
    return Path(selected) if isinstance(selected, str) else None


def _paper_summary() -> dict[str, Any]:
    files = sorted(PAPER_DIR.glob("*.json"))
    entries: list[dict[str, Any]] = []
    for path in files:
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue

    daily_returns: list[dict[str, Any]] = []
    for entry in entries:
        date = entry.get("prediction_date")
        if not date:
            continue
        weighted = 0.0
        weight_sum = 0.0
        filled = 0
        for pick in entry.get("picks", []):
            ret = _tracking_return(pick.get("symbol", ""), date)
            weight = _safe_float(pick.get("planned_weight")) or 0.0
            if ret is None or weight <= 0:
                continue
            weighted += ret * weight
            weight_sum += weight
            filled += 1
        if weight_sum > 0:
            daily_returns.append({
                "date": date,
                "return": weighted / weight_sum,
                "filled": filled,
            })

    if not daily_returns:
        return {
            "nDaysLogged": len(entries),
            "nDaysWithReturns": 0,
            "sampleStart": None,
            "sampleEnd": None,
            "annualized": None,
            "sharpe": None,
            "maxDrawdown": None,
            "winRate": None,
            "avgDailyReturn": None,
            "message": "预测已记录但尚无足够 T+2 数据可评估",
        }

    returns = [item["return"] for item in daily_returns]
    avg = sum(returns) / len(returns)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = (avg / stdev * math.sqrt(252)) if stdev > 0 else None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    return {
        "nDaysLogged": len(entries),
        "nDaysWithReturns": len(daily_returns),
        "sampleStart": daily_returns[0]["date"],
        "sampleEnd": daily_returns[-1]["date"],
        "annualized": avg * 252,
        "sharpe": sharpe,
        "maxDrawdown": max_dd,
        "winRate": sum(1 for ret in returns if ret > 0) / len(returns),
        "avgDailyReturn": avg,
        "message": None,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_result(
    task_id: str,
    title: str,
    status: str,
    summary: str,
    started_at: str,
    stdout: str = "",
    stderr: str = "",
    artifacts: list[str] | None = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "title": title,
        "startedAt": started_at,
        "finishedAt": _now_iso(),
        "status": status,
        "exitCode": exit_code,
        "summary": summary,
        "stdout": stdout,
        "stderr": stderr,
        "artifacts": artifacts or [],
    }


def _append_task_history(result: dict[str, Any]) -> None:
    APP_RUN_DIR.mkdir(parents=True, exist_ok=True)
    with TASK_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def _task_history(limit: int = 25) -> list[dict[str, Any]]:
    if not TASK_LOG_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in TASK_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return list(reversed(rows[-limit:]))


def _run_process_task(
    task_id: str,
    title: str,
    command: list[str],
    started_at: str,
    artifacts: list[str] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    cache_dir = PROJECT_ROOT / ".cache"
    mpl_dir = cache_dir / "matplotlib"
    home_dir = cache_dir / "home"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MPLCONFIGDIR"] = str(mpl_dir)
    env["XDG_CACHE_HOME"] = str(cache_dir)
    env["HOME"] = str(home_dir)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.update(_load_project_env())
    proc = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return _task_result(
        task_id,
        title,
        "success" if proc.returncode == 0 else "failed",
        f"Exit {proc.returncode}: {' '.join(command[:3])}",
        started_at,
        stdout=_shorten(proc.stdout),
        stderr=_shorten(proc.stderr),
        artifacts=artifacts or [],
        exit_code=proc.returncode,
    )


def _load_project_env() -> dict[str, str]:
    allowed = {
        "TUSHARE_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_API_URL",
    }
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in allowed:
            continue
        value = value.strip().strip("\"'")
        if value:
            loaded[key] = value
    return loaded


def _normalize_script_date(date_text: str | None) -> str | None:
    if not date_text:
        return None
    compact = date_text.replace("-", "")
    if re.fullmatch(r"\d{8}", compact):
        return compact
    return None


def _latest_local_date_for(symbol_codes: tuple[str, ...]) -> str | None:
    dates: list[str] = []
    for code in symbol_codes:
        path = PROJECT_ROOT / f"cs_data_{code}.csv"
        if not path.exists():
            continue
        rows = _read_csv_rows(path)
        if rows:
            date = rows[-1].get("trade_date", "")
            if date:
                dates.append(date.replace("-", ""))
    return min(dates) if dates else None


def _missing_full_env_result(task_id: str, title: str, started: str) -> dict[str, Any]:
    status = _python_env_status()
    return _task_result(
        task_id,
        title,
        "failed",
        "No Python environment has the required KSS dependencies",
        started,
        stdout=json.dumps(status, ensure_ascii=False, indent=2),
        stderr="Run: .venv-desktop/bin/python -m pip install -r kss/requirements.txt",
        exit_code=127,
    )


def _parse_args(argv: list[str]) -> dict[str, str | bool]:
    out: dict[str, str | bool] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[key] = argv[i + 1]
                i += 2
            else:
                out[key] = True
                i += 1
        else:
            i += 1
    return out


def _rows_by_symbol() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for path in sorted(PROJECT_ROOT.glob("cs_data_688*.csv")):
        rows = _read_csv_rows(path)
        if not rows:
            continue
        symbol = rows[-1].get("ts_code") or path.stem.replace("cs_data_", "") + ".SH"
        out[symbol] = rows
    return out


def _date_to_ordinal(date_text: str) -> int | None:
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date().toordinal()
    except ValueError:
        return None


def _candidate_for_date(
    symbol: str,
    rows: list[dict[str, str]],
    target_date: str,
    names: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    target_ord = _date_to_ordinal(target_date)
    if target_ord is None:
        return None
    chosen: dict[str, str] | None = None
    chosen_index = -1
    for index, row in enumerate(rows):
        row_date = row.get("trade_date", "")
        row_ord = _date_to_ordinal(row_date)
        if row_ord is not None and row_ord <= target_ord:
            chosen = row
            chosen_index = index
        elif row_ord is not None and row_ord > target_ord:
            break
    if chosen is None:
        return None
    chosen_ord = _date_to_ordinal(chosen.get("trade_date", ""))
    if chosen_ord is None or target_ord - chosen_ord > FRESHNESS_DAYS:
        return None
    total_mv = _safe_float(chosen.get("total_mv"))
    if total_mv is None or total_mv <= 0:
        return None
    meta = names.get(symbol, {})
    return {
        "symbol": symbol,
        "name": meta.get("name", ""),
        "industry": meta.get("industry", ""),
        "trade_date": chosen.get("trade_date", ""),
        "factor_value": math.log(total_mv),
        "total_mv": total_mv,
        "close": _safe_float(chosen.get("close")),
        "row_index": chosen_index,
    }


def _latest_kcb_date(rows_by_symbol: dict[str, list[dict[str, str]]]) -> str | None:
    dates = [rows[-1].get("trade_date", "") for rows in rows_by_symbol.values() if rows]
    dates = [date for date in dates if date]
    return max(dates) if dates else None


def _build_logmv_picks(date: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    names = _load_names()
    rows_by = _rows_by_symbol()
    target_date = date or _latest_kcb_date(rows_by)
    if not target_date:
        raise RuntimeError("No KCB csv data found")
    candidates = [
        _candidate_for_date(symbol, rows, target_date, names)
        for symbol, rows in rows_by.items()
    ]
    ranked = sorted(
        [item for item in candidates if item is not None],
        key=lambda item: (item["factor_value"], item["symbol"]),
    )
    picks: list[dict[str, Any]] = []
    for index, item in enumerate(ranked[:TOP_N], start=1):
        picks.append({
            "symbol": item["symbol"],
            "name": item["name"],
            "industry": item["industry"],
            "factor_value": item["factor_value"],
            "rank_pct": index / max(len(ranked), 1),
            "rank_position": index,
            "planned_weight": 1 / TOP_N,
            "latest_close": item["close"],
        })
    return target_date, picks


def _save_picks(date: str, picks: list[dict[str, Any]], force: bool) -> tuple[Path, bool]:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    out = PAPER_DIR / f"{date}.json"
    if out.exists() and not force:
        return out, False
    payload = {
        "prediction_date": date,
        "generated_at": datetime.now().isoformat(),
        "strategy": "log_mv_reverse",
        "source": "KSSDesktop stdlib bridge",
        "use_execution": False,
        "top_pct": TOP_PCT,
        "top_n": TOP_N,
        "picks": [
            {
                "symbol": item["symbol"],
                "factor_value": item["factor_value"],
                "rank_pct": item["rank_pct"],
                "rank_position": item["rank_position"],
                "planned_weight": item["planned_weight"],
            }
            for item in picks
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, True


def _run_daily_picks(args: dict[str, str | bool]) -> dict[str, Any]:
    started = _now_iso()
    force = bool(args.get("force"))
    save = not bool(args.get("preview"))
    date_arg = args.get("date")
    date = str(date_arg) if isinstance(date_arg, str) else None
    try:
        target_date, picks = _build_logmv_picks(date)
        lines = [
            f"{item['rank_position']}. {item['symbol']} {item['name']} "
            f"log_mv={item['factor_value']:.3f} weight={item['planned_weight']:.0%}"
            for item in picks
        ]
        artifacts: list[str] = []
        if save:
            path, wrote = _save_picks(target_date, picks, force=force)
            artifacts.append(str(path.relative_to(PROJECT_ROOT)))
            status = "success" if wrote else "skipped"
            action = "saved" if wrote else "already exists"
        else:
            status = "success"
            action = "preview"
        return _task_result(
            "daily-picks",
            "Generate Daily Picks",
            status,
            f"{target_date}: {action}; {len(picks)} picks",
            started,
            stdout="\n".join(lines),
            artifacts=artifacts,
        )
    except Exception as exc:  # noqa: BLE001
        return _task_result(
            "daily-picks",
            "Generate Daily Picks",
            "failed",
            str(exc),
            started,
            stderr=repr(exc),
            exit_code=1,
        )


def _calc_return_for_pick(rows: list[dict[str, str]], index: int) -> float | None:
    if index + 2 >= len(rows):
        return None
    t1 = _safe_float(rows[index + 1].get("open"))
    t2 = _safe_float(rows[index + 2].get("open"))
    if t1 in (None, 0) or t2 is None:
        return None
    return t2 / t1 - 1


def _equity_metrics(returns: list[float]) -> dict[str, float | None]:
    if not returns:
        return {
            "annualized": None,
            "sharpe": None,
            "maxDrawdown": None,
            "winRate": None,
            "avgDailyReturn": None,
        }
    avg = sum(returns) / len(returns)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    return {
        "annualized": avg * 252,
        "sharpe": (avg / stdev * math.sqrt(252)) if stdev > 0 else None,
        "maxDrawdown": max_dd,
        "winRate": sum(1 for ret in returns if ret > 0) / len(returns),
        "avgDailyReturn": avg,
    }


def _run_logmv_backtest(args: dict[str, str | bool]) -> dict[str, Any]:
    started = _now_iso()
    lookback_raw = args.get("lookback")
    try:
        lookback = int(lookback_raw) if isinstance(lookback_raw, str) else 120
    except ValueError:
        lookback = 120
    rows_by = _rows_by_symbol()
    names = _load_names()
    all_dates = sorted({
        row.get("trade_date", "")
        for rows in rows_by.values()
        for row in rows[:-2]
        if row.get("trade_date")
    })
    if lookback > 0:
        all_dates = all_dates[-lookback:]

    daily: list[dict[str, Any]] = []
    for date in all_dates:
        candidates: list[dict[str, Any]] = []
        for symbol, rows in rows_by.items():
            for index, row in enumerate(rows[:-2]):
                if row.get("trade_date") != date:
                    continue
                total_mv = _safe_float(row.get("total_mv"))
                if total_mv is None or total_mv <= 0:
                    continue
                ret = _calc_return_for_pick(rows, index)
                if ret is None:
                    continue
                meta = names.get(symbol, {})
                candidates.append({
                    "symbol": symbol,
                    "name": meta.get("name", ""),
                    "factor_value": math.log(total_mv),
                    "return": ret,
                })
                break
        if len(candidates) < 10:
            continue
        picks = sorted(candidates, key=lambda item: (item["factor_value"], item["symbol"]))[:TOP_N]
        daily_ret = sum(item["return"] for item in picks) / len(picks)
        daily.append({
            "date": date,
            "return": daily_ret,
            "symbols": [item["symbol"] for item in picks],
        })

    returns = [item["return"] for item in daily]
    metrics = _equity_metrics(returns)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "kss_desktop_logmv_backtest.md"
    lines = [
        "# KSS Desktop log_mv 反向轻量回测",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本天数: {len(daily)}",
        f"- 区间: {daily[0]['date'] if daily else '-'} ~ {daily[-1]['date'] if daily else '-'}",
        f"- 规则: 每日选 total_mv 最小的 {TOP_N} 只, T+1 open 到 T+2 open 等权收益",
        f"- 年化: {metrics['annualized']:.2%}" if metrics["annualized"] is not None else "- 年化: -",
        f"- Sharpe: {metrics['sharpe']:.2f}" if metrics["sharpe"] is not None else "- Sharpe: -",
        f"- 最大回撤: {metrics['maxDrawdown']:.2%}" if metrics["maxDrawdown"] is not None else "- 最大回撤: -",
        f"- 胜率: {metrics['winRate']:.1%}" if metrics["winRate"] is not None else "- 胜率: -",
        "",
        "| 日期 | 收益 | 股票 |",
        "|---|---:|---|",
    ]
    for item in daily[-20:]:
        lines.append(
            f"| {item['date']} | {item['return']:.2%} | {', '.join(item['symbols'])} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stdout = "\n".join(lines[:10])
    return _task_result(
        "logmv-backtest",
        "Run log_mv Backtest",
        "success" if daily else "failed",
        f"{len(daily)} signal days; Sharpe={metrics['sharpe']:.2f}" if metrics["sharpe"] is not None else f"{len(daily)} signal days",
        started,
        stdout=stdout,
        stderr="" if daily else "No daily returns could be computed",
        artifacts=[str(report_path.relative_to(PROJECT_ROOT))],
        exit_code=0 if daily else 1,
    )


def _load_radar_archives() -> list[dict[str, Any]]:
    archives: list[dict[str, Any]] = []
    for path in sorted((PROJECT_ROOT / "storage" / "etf_radar").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        payload["_path"] = str(path.relative_to(PROJECT_ROOT))
        archives.append(payload)
    return archives


def _run_radar_archive_analysis() -> dict[str, Any]:
    started = _now_iso()
    archives = _load_radar_archives()
    if not archives:
        return _task_result(
            "radar-archive-analysis",
            "Radar Archive Analysis",
            "failed",
            "No ETF radar archives found",
            started,
            exit_code=1,
        )

    grade_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}
    divergence_count = 0
    accel_count = 0
    rows: list[tuple[str, str, str, float | None, float | None, float | None, bool]] = []
    for archive in archives:
        date = str(archive.get("trade_date") or archive.get("data_date") or "")
        themes = archive.get("themes") if isinstance(archive.get("themes"), dict) else {}
        for theme, data in themes.items():
            if not isinstance(data, dict):
                continue
            grade = str(data.get("grade") or "unknown")
            grade_counts[grade] = grade_counts.get(grade, 0) + 1
            theme_counts[str(theme)] = theme_counts.get(str(theme), 0) + 1
            divergence = bool(data.get("divergence"))
            accel = bool(data.get("accel"))
            divergence_count += 1 if divergence else 0
            accel_count += 1 if accel else 0
            rows.append((
                date,
                str(theme),
                grade,
                _safe_float(data.get("flow_1d")),
                _safe_float(data.get("flow_5d")),
                _safe_float(data.get("past5_ret")),
                divergence,
            ))

    latest = archives[-1]
    latest_date = str(latest.get("trade_date") or latest.get("data_date") or "")
    latest_themes = latest.get("themes") if isinstance(latest.get("themes"), dict) else {}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "kss_desktop_radar_archive_analysis.md"
    lines = [
        "# KSS Desktop ETF Radar Archive Analysis",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 归档天数: {len(archives)}",
        f"- 样本点: {len(rows)}",
        f"- 最新雷达日期: {latest_date}",
        f"- divergence 次数: {divergence_count}",
        f"- accel 次数: {accel_count}",
        "",
        "## Grade 分布",
        "",
        "| Grade | 次数 |",
        "|---|---:|",
    ]
    for grade, count in sorted(grade_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {grade} | {count} |")
    lines += [
        "",
        "## 最新雷达",
        "",
        "| 主题 | Grade | 1日申赎 | 5日申赎 | 过去5日收益 | Divergence |",
        "|---|---|---:|---:|---:|---|",
    ]
    for theme, data in latest_themes.items():
        if not isinstance(data, dict):
            continue
        lines.append(
            f"| {theme} | {data.get('grade', '-')} | "
            f"{KSSNumber.percent_like(_safe_float(data.get('flow_1d')))} | "
            f"{KSSNumber.percent_like(_safe_float(data.get('flow_5d')))} | "
            f"{KSSNumber.percent_like(_safe_float(data.get('past5_ret')))} | "
            f"{'Y' if data.get('divergence') else 'N'} |"
        )
    lines += [
        "",
        "## 最近 20 个主题信号",
        "",
        "| 日期 | 主题 | Grade | 5日申赎 | 过去5日收益 | Divergence |",
        "|---|---|---|---:|---:|---|",
    ]
    for date, theme, grade, _, flow5, past5, divergence in rows[-20:]:
        lines.append(
            f"| {date} | {theme} | {grade} | "
            f"{KSSNumber.percent_like(flow5)} | {KSSNumber.percent_like(past5)} | "
            f"{'Y' if divergence else 'N'} |"
        )
    lines += [
        "",
        "---",
        "",
        "_生成: scripts/kss_app_bridge.py radar-archive-analysis · 仅汇总已归档 ETF radar JSON, 不替代 parquet 正式回测._",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _task_result(
        "radar-archive-analysis",
        "Radar Archive Analysis",
        "success",
        f"{len(archives)} archive days; {len(rows)} theme signals",
        started,
        stdout="\n".join(lines[:18]),
        artifacts=[str(report_path.relative_to(PROJECT_ROOT))],
    )


class KSSNumber:
    @staticmethod
    def percent_like(value: float | None) -> str:
        return "-" if value is None else f"{value:+.2f}%"


def _run_formal_daily_picks(args: dict[str, str | bool]) -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("formal-daily-picks", "Formal Daily Picks", started)
    command = [str(python), "scripts/paper_trade_log_mv.py"]
    date_arg = args.get("date")
    if isinstance(date_arg, str):
        command += ["--date", date_arg]
    if args.get("no_execution"):
        command.append("--no-execution")
    if args.get("force"):
        command.append("--force-save")
    return _run_process_task(
        "formal-daily-picks",
        "Formal Daily Picks",
        command,
        started,
        timeout=300,
    )


def _run_formal_paper_summary() -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("formal-paper-summary", "Formal Paper Summary", started)
    return _run_process_task(
        "formal-paper-summary",
        "Formal Paper Summary",
        [str(python), "scripts/paper_trade_log_mv.py", "--summary"],
        started,
        timeout=300,
    )


def _run_formal_daily_review(args: dict[str, str | bool]) -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("formal-daily-review", "Formal Daily Review", started)

    date_arg = args.get("date")
    target = _normalize_script_date(str(date_arg)) if isinstance(date_arg, str) else None
    target = target or _latest_local_date_for(("688322", "688017"))
    if target is None:
        return _task_result(
            "formal-daily-review",
            "Formal Daily Review",
            "failed",
            "No local stock data date is available for 688322/688017",
            started,
            exit_code=1,
        )

    archive_date = f"{target[:4]}-{target[4:6]}-{target[6:8]}"
    command = [
        str(python),
        "scripts/daily_review_322_017.py",
        "--date",
        target,
        "--channel",
        "console",
        "--dry-run",
    ]
    return _run_process_task(
        "formal-daily-review",
        "Formal Daily Review",
        command,
        started,
        artifacts=[f"storage/daily_review/{archive_date}.md"],
        timeout=300,
    )


def _run_formal_sector_review(args: dict[str, str | bool]) -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("formal-sector-review", "Formal Sector Review", started)

    date_arg = args.get("date")
    target = _normalize_script_date(str(date_arg)) if isinstance(date_arg, str) else None
    target = target or _normalize_script_date(_latest_kcb_date(_rows_by_symbol()))
    if target is None:
        return _task_result(
            "formal-sector-review",
            "Formal Sector Review",
            "failed",
            "No local KCB date is available for sector review default",
            started,
            exit_code=1,
        )

    command = [
        str(python),
        "scripts/sector_review.py",
        "--date",
        target,
        "--channel",
        "console",
        "--dry-run",
    ]
    return _run_process_task(
        "formal-sector-review",
        "Formal Sector Review",
        command,
        started,
        artifacts=[f"storage/etf_radar/{target}.json", "storage/app_runs/kss_desktop_tasks.jsonl"],
        timeout=360,
    )


def _run_formal_etf_backtest(args: dict[str, str | bool]) -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result(
            "formal-etf-radar-backtest",
            "Formal ETF Radar Backtest",
            started,
        )
    has_parquet, missing = _has_any_module(python, ETF_PARQUET_MODULES)
    if not has_parquet:
        return _task_result(
            "formal-etf-radar-backtest",
            "Formal ETF Radar Backtest",
            "failed",
            "Missing parquet support for cached ETF radar data",
            started,
            stdout=json.dumps({"missingModules": missing}, ensure_ascii=False, indent=2),
            stderr="Install pyarrow or fastparquet before running the formal ETF radar backtest.",
            artifacts=["storage/reports/etf_radar_backtest_20260607.md"],
            exit_code=127,
        )
    command = [str(python), "backtest_etf_radar.py"]
    if args.get("refresh"):
        command.append("--refresh")
    return _run_process_task(
        "formal-etf-radar-backtest",
        "Formal ETF Radar Backtest",
        command,
        started,
        artifacts=["storage/reports/etf_radar_backtest_20260607.md"],
        timeout=600,
    )


def _run_refresh_bj_daily() -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("refresh-bj-daily", "刷新北证日线", started)
    return _run_process_task(
        "refresh-bj-daily",
        "刷新北证日线",
        [str(python), "scripts/refresh_bj_daily.py"],
        started,
        artifacts=["storage/bj_cache"],
        timeout=600,
    )


def _run_refresh_market_strip() -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("refresh-market-strip", "刷新市场速览", started)
    return _run_process_task(
        "refresh-market-strip",
        "刷新市场速览",
        [str(python), "scripts/refresh_market_strip.py"],
        started,
        artifacts=["storage/macro/market_strip.json"],
        timeout=180,
    )


def _run_refresh_daily_basic() -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("refresh-daily-basic", "刷新流通市值/估值", started)
    return _run_process_task(
        "refresh-daily-basic",
        "刷新流通市值/估值",
        [str(python), "scripts/refresh_daily_basic.py"],
        started,
        artifacts=["storage/macro/dailybasic_latest.json"],
        timeout=300,
    )


def run_task(task_id: str, argv: list[str]) -> dict[str, Any]:
    args = _parse_args(argv)
    if task_id == "daily-picks":
        return _run_daily_picks(args)
    if task_id == "daily-picks-preview":
        args["preview"] = True
        return _run_daily_picks(args)
    if task_id == "logmv-backtest":
        return _run_logmv_backtest(args)
    if task_id == "radar-archive-analysis":
        return _run_radar_archive_analysis()
    if task_id == "paper-summary":
        started = _now_iso()
        summary = _paper_summary()
        return _task_result(
            "paper-summary",
            "Refresh Paper Tracking",
            "success",
            f"{summary['nDaysWithReturns']} / {summary['nDaysLogged']} days evaluated",
            started,
            stdout=json.dumps(summary, ensure_ascii=False, indent=2),
        )
    if task_id == "formal-daily-picks":
        return _run_formal_daily_picks(args)
    if task_id == "formal-paper-summary":
        return _run_formal_paper_summary()
    if task_id == "formal-daily-review":
        return _run_formal_daily_review(args)
    if task_id == "formal-sector-review":
        return _run_formal_sector_review(args)
    if task_id == "formal-etf-radar-backtest":
        return _run_formal_etf_backtest(args)
    if task_id == "refresh-bj-daily":
        return _run_refresh_bj_daily()
    if task_id == "refresh-daily-basic":
        return _run_refresh_daily_basic()
    if task_id == "refresh-market-strip":
        return _run_refresh_market_strip()
    return _task_result(
        task_id,
        task_id,
        "failed",
        f"Unknown task: {task_id}",
        _now_iso(),
        exit_code=2,
    )


def _horizon_return(symbol: str, prediction_date: str, hold: int) -> float | None:
    """Equal-entry realized return: buy T+1 open, sell T+(1+hold) open."""
    path = _stock_file(symbol)
    if not path.exists():
        return None
    rows = _read_csv_rows(path)
    future = [row for row in rows if row.get("trade_date", "") > prediction_date]
    if len(future) < hold + 1:
        return None
    entry = _safe_float(future[0].get("open"))
    exit_price = _safe_float(future[hold].get("open"))
    if entry in (None, 0) or exit_price is None:
        return None
    return exit_price / entry - 1


# 日 = 持有 1 个交易日 (T+1 open -> T+2 open); 周 ~= 5 个交易日; 月 ~= 20 个交易日
_HORIZONS = (("ret1d", 1), ("ret5d", 5), ("ret20d", 20))


def _recommendation_tracking(names: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(PAPER_DIR.glob("*.json"), reverse=True):
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = log.get("prediction_date")
        if not date:
            continue
        picks_out: list[dict[str, Any]] = []
        buckets: dict[str, list[float]] = {key: [] for key, _ in _HORIZONS}
        for pick in log.get("picks", []):
            symbol = pick.get("symbol", "")
            meta = names.get(symbol, {})
            pick_row: dict[str, Any] = {"symbol": symbol, "name": meta.get("name", "")}
            for key, hold in _HORIZONS:
                ret = _horizon_return(symbol, date, hold)
                pick_row[key] = ret
                if ret is not None:
                    buckets[key].append(ret)
            picks_out.append(pick_row)
        row: dict[str, Any] = {"date": date, "nPicks": len(picks_out), "picks": picks_out}
        for key, _ in _HORIZONS:
            values = buckets[key]
            row[key] = (sum(values) / len(values)) if values else None
        out.append(row)
    return out


def _latest_bj_scan() -> tuple[str | None, list[dict[str, str]]]:
    files = sorted(BJ_SCAN_DIR.glob("scan_*.csv"))
    if not files:
        return None, []
    path = files[-1]
    return path.stem.replace("scan_", ""), _read_csv_rows(path)


def _bj_scan_summary() -> dict[str, Any] | None:
    date, rows = _latest_bj_scan()
    if not rows:
        return None
    passed = [row for row in rows if str(row.get("pass", "")).strip().lower() == "true"]
    ranked = sorted(rows, key=lambda row: _safe_float(row.get("total_score")) or 0, reverse=True)
    top = [
        {
            "symbol": row.get("ts_code", ""),
            "name": row.get("name", ""),
            "industry": row.get("industry", ""),
            "score": _safe_float(row.get("total_score")),
            "ret20d": _safe_float(row.get("ret_20d")),
            "close": _safe_float(row.get("close")),
            "tag": row.get("perilla_tag", ""),
        }
        for row in ranked[:8]
    ]
    return {"scanDate": date, "total": len(rows), "passed": len(passed), "top": top}


def _bj_daily_path(symbol: str) -> Path:
    return BJ_CACHE_DIR / f"{symbol}_daily.csv"


def _bj_history(symbol: str) -> list[dict[str, Any]]:
    """北证日线历史（来自 scan_bj50 的 Tushare 缓存 storage/bj_cache）。"""
    path = _bj_daily_path(symbol)
    if not path.exists():
        return []
    history: list[dict[str, Any]] = []
    for row in _read_csv_rows(path)[-160:]:
        close = _safe_float(row.get("close"))
        if close is None:
            continue
        history.append({
            "date": row.get("trade_date", ""),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": close,
            "pctChange": _safe_float(row.get("pct_chg")),
            "volume": _safe_float(row.get("vol")),
            "amount": _safe_float(row.get("amount")),
        })
    return history


def _bj_summary(scan_row: dict[str, str], archive_date: str) -> dict[str, Any]:
    symbol = scan_row.get("ts_code", "")
    daily = _read_csv_rows(_bj_daily_path(symbol)) if _bj_daily_path(symbol).exists() else []
    closes = [v for v in (_safe_float(r.get("close")) for r in daily) if v is not None]
    last20 = closes[-20:]
    latest = daily[-1] if daily else {}
    mv_yi = _safe_float(scan_row.get("total_mv_yi"))
    return {
        "symbol": symbol,
        "name": scan_row.get("name", ""),
        "industry": scan_row.get("industry", ""),
        "concept": scan_row.get("perilla_tag", "") or "北证50",
        "latestDate": latest.get("trade_date") or archive_date,
        "close": _safe_float(latest.get("close")) if daily else _safe_float(scan_row.get("close")),
        "pctChange": _safe_float(latest.get("pct_chg")) if daily else None,
        "turnoverRate": _safe_float(scan_row.get("turnover_mean")),
        "amount": _safe_float(latest.get("amount")) if daily else None,
        "pe": _safe_float(scan_row.get("pe_ttm")),
        "pb": _safe_float(scan_row.get("pb")),
        "totalMv": (mv_yi * 1e8) if mv_yi is not None else None,
        "ma5": _mean(closes[-5:]) if closes else None,
        "ma20": _mean(last20) if last20 else None,
        "high20": max(last20) if last20 else None,
        "low20": min(last20) if last20 else None,
    }


def _bj_stock_summaries() -> list[dict[str, Any]]:
    date, rows = _latest_bj_scan()
    archive_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if date and len(date) == 8 else (date or "")
    return [_bj_summary(row, archive_date) for row in rows if row.get("ts_code")]


def _bj_detail(symbol: str) -> dict[str, Any] | None:
    date, rows = _latest_bj_scan()
    archive_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if date and len(date) == 8 else (date or "")
    for row in rows:
        if row.get("ts_code") == symbol:
            history = _bj_history(symbol)
            note = "" if history else "（无日线缓存）"
            return {
                "symbol": symbol,
                "name": row.get("name", ""),
                "industry": row.get("industry", ""),
                "concept": (row.get("perilla_tag", "") or "北证50扫描") + note,
                "latest": _bj_summary(row, archive_date),
                "history": history,
            }
    return None


_DAILYBASIC_JSON = PROJECT_ROOT / "storage" / "macro" / "dailybasic_latest.json"
_MARKET_STRIP_JSON = PROJECT_ROOT / "storage" / "macro" / "market_strip.json"
ETF_RADAR_DIR = PROJECT_ROOT / "storage" / "etf_radar"


def _market_strip() -> dict[str, Any] | None:
    """总览第一行市场速览：A500ETF 当日行情 + 北向资金（refresh_market_strip.py 产出）。"""
    if not _MARKET_STRIP_JSON.exists():
        return None
    try:
        return json.loads(_MARKET_STRIP_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_dailybasic_cache() -> dict[str, Any]:
    """流通市值 / PE / PB 切片缓存（scripts/refresh_daily_basic.py 产出，单位万元）。"""
    if not _DAILYBASIC_JSON.exists():
        return {}
    try:
        return json.loads(_DAILYBASIC_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cs_metrics(symbol: str) -> dict[str, Any]:
    """从 cs_data_<code>.csv 计算 日/周/月/年 涨幅 + 最新 PE/PB/总市值。"""
    digits = symbol.split(".")[0]
    fp = PROJECT_ROOT / f"cs_data_{digits}.csv"
    if not fp.exists():
        return {}
    rows = _read_csv_rows(fp)
    if not rows:
        return {}
    rows.sort(key=lambda r: r.get("trade_date", ""))
    closes: list[float] = []
    for r in rows:
        try:
            closes.append(float(r["close"]))
        except (TypeError, ValueError):
            pass
    if len(closes) < 2:
        return {}

    def ret(n: int) -> float | None:
        if len(closes) > n and closes[-1 - n]:
            return round(closes[-1] / closes[-1 - n] - 1.0, 4)
        return None

    last = rows[-1]

    def fnum(key: str) -> float | None:
        try:
            return float(last[key])
        except (TypeError, ValueError, KeyError):
            return None

    yr_n = min(244, len(closes) - 1)
    ret_year = round(closes[-1] / closes[-1 - yr_n] - 1.0, 4) if yr_n > 0 and closes[-1 - yr_n] else None
    return {
        "ret1d": ret(1),
        "ret5d": ret(5),
        "ret20d": ret(20),
        "retYear": ret_year,
        "pe": fnum("pe"),
        "pb": fnum("pb"),
        "totalMv": fnum("total_mv"),
    }


def _pulse_from_dict(d: dict[str, Any]) -> dict[str, Any] | None:
    """把一份 etf_radar 切片转成板块脉冲结构（资金申赎 + 强势确认分级）。"""
    themes_raw = d.get("themes") or {}
    themes: list[dict[str, Any]] = []
    for name, v in themes_raw.items():
        if not isinstance(v, dict):
            continue
        themes.append({
            "name": name,
            "flow1d": v.get("flow_1d"),
            "flow5d": v.get("flow_5d"),
            "past5Ret": v.get("past5_ret"),
            "grade": v.get("grade", ""),
            "divergence": bool(v.get("divergence")),
            "accel": bool(v.get("accel")),
            "rank5d": v.get("rank_5d"),
            "nFunds": v.get("n_funds"),
        })
    if not themes:
        return None
    # 强势确认优先，其次按近 5 日涨幅降序
    themes.sort(key=lambda x: (x["grade"] != "强势确认", -(x["past5Ret"] if x["past5Ret"] is not None else -999)))
    regime = d.get("momentum_regime_r3") or {}
    return {
        "tradeDate": str(d.get("trade_date", "")),
        "dataDate": str(d.get("data_date", "")),
        "stale": bool(d.get("stale")),
        "note": d.get("note", ""),
        "regimeInRegime": regime.get("in_regime"),
        "regimeMom20": regime.get("mom20"),
        "regimeMom20Th": regime.get("mom20_th"),
        "themes": themes,
    }


def _commentary_to_md(raw: str) -> str:
    """投顾点评 HTML 化标签 → Markdown：段标题转 ##，行内 <b>/<i>/<u> 转强调。"""
    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        header = re.fullmatch(r"<b>(.+?)</b>", s)
        if header:
            lines.append(f"## {header.group(1)}")
        else:
            lines.append(line)
    text = "\n".join(lines)
    for src, dst in (("<b>", "**"), ("</b>", "**"), ("<i>", "*"), ("</i>", "*"), ("<u>", "**"), ("</u>", "**")):
        text = text.replace(src, dst)
    return text


def _sector_reviews(limit: int = 40) -> list[dict[str, Any]]:
    """每日板块复盘序列：逐份 etf_radar 切片，新到旧。

    数据源 storage/etf_radar/YYYYMMDD.json；同名 .commentary.md 为投顾点评
    （含 概念轮动 / 七大主题 / 加减仓建议 等段落）。板块复盘与个股复盘一样每日一篇，
    返回列表供复盘页按日期浏览；总览板块信息图取首项（最新一天）。
    """
    if not ETF_RADAR_DIR.exists():
        return []
    files = sorted(ETF_RADAR_DIR.glob("*.json"), reverse=True)[:limit]
    out: list[dict[str, Any]] = []
    for fp in files:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        pulse = _pulse_from_dict(d)
        if not pulse:
            continue
        commentary_path = fp.with_name(f"{fp.stem}.commentary.md")
        if commentary_path.exists():
            try:
                pulse["commentary"] = _commentary_to_md(commentary_path.read_text(encoding="utf-8"))
            except Exception:
                pulse["commentary"] = None
        else:
            pulse["commentary"] = None
        out.append(pulse)
    return out


def _perilla_picks(top_n: int = 12, min_score: float = 0.4) -> list[dict[str, Any]]:
    """紫苏叶（供应链护城河）选股：按 perilla_score 排序的注册表标的。

    数据源 = kss/config/supply_chain.yaml（curated）+ ChainRegistry 评分。
    每条返回结构化列字段，供前端表格直接渲染（不依赖展示字符串解析）。
    """
    try:
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from kss.supply_chain.registry import ChainRegistry
    except Exception:
        return []
    try:
        reg = ChainRegistry.from_yaml()
        candidates = reg.candidates(min_score=min_score)  # 已按分数降序
    except Exception:
        return []

    daily_basic = _load_dailybasic_cache()
    picks: list[dict[str, Any]] = []
    for code, score in candidates[:top_n]:
        info = reg.get(code)
        if info is None:
            continue
        if info.n_competitors_domestic <= 1:
            moat = f"全球{info.n_competitors_global}家国内独家"
        else:
            moat = f"全球{info.n_competitors_global}家"

        metrics = _cs_metrics(code)
        dbv = daily_basic.get(code, {}) if isinstance(daily_basic, dict) else {}
        circ_mv_wan = dbv.get("circ_mv")            # 流通市值（万元）
        pe = dbv.get("pe") if dbv.get("pe") is not None else metrics.get("pe")
        pb = dbv.get("pb") if dbv.get("pb") is not None else metrics.get("pb")
        # 流通市值优先；缺失时回退 cs_data 总市值
        mv_wan = circ_mv_wan if circ_mv_wan is not None else metrics.get("totalMv")

        picks.append({
            "symbol": code,
            "name": info.name or code,
            "chains": " / ".join(info.demand_chains),
            "layer": int(info.chain_layer),
            "role": info.chain_role or "",
            "moat": moat,
            "locked": bool(info.demand_locked),
            "score": round(float(score), 3),
            "ret1d": metrics.get("ret1d"),
            "ret5d": metrics.get("ret5d"),
            "ret20d": metrics.get("ret20d"),
            "retYear": metrics.get("retYear"),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) else None,
            "pb": round(pb, 2) if isinstance(pb, (int, float)) else None,
            "circMvYi": round(mv_wan / 10000.0, 1) if isinstance(mv_wan, (int, float)) else None,
            "mvIsFloat": circ_mv_wan is not None,
        })
    return picks


def snapshot() -> dict[str, Any]:
    names = _load_names()
    stocks = _load_stock_summaries(names)
    stock_by_symbol = {item["symbol"]: item for item in stocks}
    recommendation_date, recs = _recommendations(names, stock_by_symbol)
    latest_dates = [item["latestDate"] for item in stocks if item.get("latestDate")]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectRoot": str(PROJECT_ROOT),
        "latestDataDate": max(latest_dates) if latest_dates else None,
        "stockCount": len(stocks),
        "recommendationDate": recommendation_date,
        "stocks": stocks,
        "recommendations": recs,
        "reviews": _reviews(),
        "backtests": _backtest_reports(),
        "tracking": _paper_summary(),
        "recommendationTracking": _recommendation_tracking(names),
        "bjScan": _bj_scan_summary(),
        "perillaPicks": _perilla_picks(),
        "sectorReviews": _sector_reviews(),
        "marketStrip": _market_strip(),
        "pythonEnvironment": _python_env_status(),
        "recentTaskRuns": _task_history(),
    }


def stock_detail(symbol: str) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    if "." not in symbol:
        symbol = f"{symbol}.SH" if symbol.startswith("688") else f"{symbol}.SZ"
    names = _load_names()
    path = _stock_file(symbol)
    if not path.exists():
        bj = _bj_detail(symbol)
        if bj is not None:
            return bj
        raise SystemExit(f"stock data not found: {symbol}")
    rows = _read_csv_rows(path)
    history = []
    for row in rows[-160:]:
        close = _safe_float(row.get("close"))
        if close is None:
            continue
        history.append({
            "date": row.get("trade_date", ""),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": close,
            "pctChange": _safe_float(row.get("pct_chg")),
            "volume": _safe_float(row.get("vol")),
            "amount": _safe_float(row.get("amount")),
        })
    latest = _stock_summary(path, names)
    meta = names.get(symbol, {})
    return {
        "symbol": symbol,
        "name": meta.get("name", ""),
        "industry": meta.get("industry", ""),
        "concept": meta.get("concept", ""),
        "latest": latest,
        "history": history,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: kss_app_bridge.py snapshot|stock SYMBOL|report PATH|paper-summary|run TASK",
            file=sys.stderr,
        )
        return 2
    command = argv[1]
    if command == "snapshot":
        _json_dump(snapshot())
        return 0
    if command == "stock":
        if len(argv) < 3:
            print("stock command requires SYMBOL", file=sys.stderr)
            return 2
        _json_dump(stock_detail(argv[2]))
        return 0
    if command == "report":
        if len(argv) < 3:
            print("report command requires PATH", file=sys.stderr)
            return 2
        _json_dump(report_detail(argv[2]))
        return 0
    if command == "paper-summary":
        _json_dump(_paper_summary())
        return 0
    if command == "python-env":
        _json_dump(_python_env_status())
        return 0
    if command == "run":
        if len(argv) < 3:
            print("run command requires TASK", file=sys.stderr)
            return 2
        result = run_task(argv[2], argv[3:])
        _append_task_history(result)
        _json_dump(result)
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
