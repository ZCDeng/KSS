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

def _env_path(var: str) -> Path | None:
    v = os.environ.get(var)
    return Path(v).expanduser().resolve() if v else None


# 不可变代码根（脚本/config 所在）；bundle-mode 由 KSS_PROJECT_ROOT 指定。
PROJECT_ROOT = _env_path("KSS_PROJECT_ROOT") or Path(__file__).resolve().parents[1]
# 可变状态根（storage/.cache）；默认回落 PROJECT_ROOT → 未设 env 时与历史行为逐字一致。
STATE_ROOT = _env_path("KSS_STATE_ROOT") or PROJECT_ROOT
PAPER_DIR = STATE_ROOT / "storage" / "paper_trade"
REVIEW_DIR = STATE_ROOT / "storage" / "daily_review"
REPORT_DIR = STATE_ROOT / "storage" / "reports"
BJ_SCAN_DIR = REPORT_DIR / "bj50_scan"
BJ_CACHE_DIR = STATE_ROOT / "storage" / "bj_cache"
APP_RUN_DIR = STATE_ROOT / "storage" / "app_runs"
TASK_LOG_PATH = APP_RUN_DIR / "kss_desktop_tasks.jsonl"
NAMES_PATH = STATE_ROOT / "storage" / "stock_names.csv"
SUPPLY_CHAIN_PATH = PROJECT_ROOT / "kss" / "config" / "supply_chain.yaml"  # config = 代码，随 bundle
SECTOR_ROTATION_DIR = STATE_ROOT / "storage" / "sector_rotation"
TOP_N = 5
TOP_PCT = 0.2
FRESHNESS_DAYS = 7
REQUIRED_FULL_MODULES = ("pandas", "lightgbm", "tushare", "akshare")
ETF_PARQUET_MODULES = ("pyarrow", "fastparquet")


# 桥协议版本（KTD3）。Swift supportedSchemaVersion 必须同 commit 同步。
# additive 改动不 bump；字段重命名/删除/语义变更才 bump。
BRIDGE_SCHEMA_VERSION = 1


def _envelope_json(payload: Any) -> str:
    """版本化信封 {schemaVersion, data} 的 JSON 行（U4/U5：subprocess 与 sidecar 共用）。"""
    envelope = {"schemaVersion": BRIDGE_SCHEMA_VERSION, "data": payload}
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _json_dump(payload: Any) -> None:
    print(_envelope_json(payload))


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
    # 全市场名称索引兜底（导入的票补名称/行业）
    idx_meta = _load_name_index().get("meta", {})
    for symbol, m in idx_meta.items():
        existing = out.get(symbol)
        if existing is None:
            out[symbol] = {"name": m.get("name", ""), "industry": m.get("industry", ""), "concept": ""}
        elif not existing.get("name"):
            existing["name"] = m.get("name", "")
            if not existing.get("industry"):
                existing["industry"] = m.get("industry", "")
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
    return STATE_ROOT / f"cs_data_{code}.csv"


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
        "open": _safe_float(latest.get("open")),
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
    paths = sorted(STATE_ROOT.glob("cs_data_*.csv"))
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
            "latestOpen": stock.get("open"),
            "latestClose": stock.get("close"),
            "trackingReturn": ret,
            "status": status,
        })
    return date, items


# U3: 按股归档文件名 {date}_{tscode}.md (新) vs {date}.md (旧, 兼容)。
_REVIEW_PERSYMBOL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{6}\.(?:SH|SZ|BJ))$")
# 个股段标题行 "📊 *奥比中光(688322) R...*" → 取 "奥比中光(688322)" 作可读标题。
_REVIEW_STOCK_TITLE_RE = re.compile(r"\*([^*()]+\(\d{6}(?:\.\w+)?\))")


def _reviews() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(REVIEW_DIR.glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.rstrip() for line in text.splitlines()]
        body_lines = [line for line in lines if line.strip() and not line.startswith("#")]
        excerpt = "\n".join(body_lines[:16])

        m = _REVIEW_PERSYMBOL_RE.match(path.stem)
        if m:
            # 按股产物: date / focusSymbols 由文件名确定 (非正则反解)。
            date = m.group(1)
            symbol = m.group(2)
            focus = [symbol]
            tm = _REVIEW_STOCK_TITLE_RE.search(text)
            title = tm.group(1).strip() if tm else f"{symbol} 复盘"
        else:
            # 旧按日产物兼容: date=stem, 标题取首个 #, symbols 正则反解。
            date = path.stem
            title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), path.stem)
            symbols = sorted(set(re.findall(r"\b(?:688|300|301|920)\d{3}(?:\.(?:SH|SZ|BJ))?\b", text)))
            focus = symbols[:12]

        out.append({
            "date": date,
            "title": title,
            "excerpt": excerpt,
            "path": str(path.relative_to(STATE_ROOT)),
            "focusSymbols": focus,
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
            "path": str(path.relative_to(STATE_ROOT)),
            "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "metrics": _report_metrics(text),
            "excerpt": "\n".join(excerpt_lines[:10]),
        })
    return out


def _resolve_markdown_path(path_text: str) -> Path:
    raw = Path(path_text)
    if raw.is_absolute():
        raise SystemExit("report path must be relative to the project root")
    path = (STATE_ROOT / raw).resolve()
    try:
        path.relative_to(STATE_ROOT.resolve())
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
        "path": str(path.relative_to(STATE_ROOT)),
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "text": text,
    }


def _python_candidates() -> list[Path]:
    raw = []
    # U2: 首启 bootstrap venv（state root）+ KSS_PYTHON 显式覆盖优先于 dev .venv。
    env_py = os.environ.get("KSS_PYTHON")
    if env_py:
        raw.append(Path(env_py))
    raw.append(STATE_ROOT / "venv" / "bin" / "python")
    raw += [
        PROJECT_ROOT / ".venv-desktop" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "bin" / "python",
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
    usable = [item for item in candidates if item["usable"]]

    # 优先选同时具备 parquet 支持（pyarrow/fastparquet 至少其一）的 python，
    # 避免 ETF radar 正式回测因选中缺 pyarrow 的解释器而失败（另有候选有 pyarrow 时）。
    def _has_parquet(item: dict[str, Any]) -> bool:
        missing = item.get("missingOptionalModules", {}).get("etfRadarBacktest", [])
        return len(missing) < len(ETF_PARQUET_MODULES)

    selected = next((item for item in usable if _has_parquet(item)),
                    usable[0] if usable else None)
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
    cache_dir = STATE_ROOT / ".cache"
    mpl_dir = cache_dir / "matplotlib"
    home_dir = cache_dir / "home"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MPLCONFIGDIR"] = str(mpl_dir)
    env["XDG_CACHE_HOME"] = str(cache_dir)
    env["HOME"] = str(home_dir)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    # 显式下传，使派生子脚本（各自 parents[1] 算 root）的 storage 写入重定向到 state root。
    env["KSS_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["KSS_STATE_ROOT"] = str(STATE_ROOT)
    # U3：Keychain 经 Swift 注入到 os.environ 的凭据优先；.env/network.env 仅填空缺。
    for key, value in _load_project_env().items():
        env.setdefault(key, value)
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
    loaded: dict[str, str] = {}
    # U3：dev .env（代码根）+ bundle-mode network.env（state root，非敏感，非 iCloud）。
    # 敏感凭据正路是 Swift 经 Keychain 注入到 os.environ（见 _run_process_task setdefault 优先）；
    # 这两个文件是 dev / 非敏感回落。
    for env_path in (PROJECT_ROOT / ".env", STATE_ROOT / "network.env"):
        if not env_path.exists():
            continue
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
                loaded.setdefault(key, value)
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
        path = STATE_ROOT / f"cs_data_{code}.csv"
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
    for path in sorted(STATE_ROOT.glob("cs_data_688*.csv")):
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
            artifacts.append(str(path.relative_to(STATE_ROOT)))
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
        artifacts=[str(report_path.relative_to(STATE_ROOT))],
        exit_code=0 if daily else 1,
    )


def _load_radar_archives() -> list[dict[str, Any]]:
    archives: list[dict[str, Any]] = []
    for path in sorted((STATE_ROOT / "storage" / "etf_radar").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        payload["_path"] = str(path.relative_to(STATE_ROOT))
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
        artifacts=[str(report_path.relative_to(STATE_ROOT))],
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
    # cron 批保留原 322/017 —— 显式传 --symbols (那两只的唯一存活处, 非脚本隐藏默认)。
    command = [
        str(python),
        "scripts/daily_review.py",
        "--symbols",
        "688322.SH,688017.SH",
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
        artifacts=[
            f"storage/daily_review/{archive_date}_688322.SH.md",
            f"storage/daily_review/{archive_date}_688017.SH.md",
        ],
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


def _run_update_cs_data() -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("update-cs-data", "同步股票池日线", started)
    return _run_process_task(
        "update-cs-data",
        "同步股票池日线",
        [str(python), "scripts/update_cs_data.py"],
        started,
        artifacts=["cs_data_*.csv"],
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

def _run_refresh_sector_rotation() -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("refresh-sector-rotation", "刷新板块热点轮动", started)
    return _run_process_task(
        "refresh-sector-rotation",
        "刷新板块热点轮动",
        [str(python), "scripts/refresh_hotspot_rotation.py", "--date", "latest", "--lookback-days", "5", "--enable-kaipan", "--enable-leaders"],
        started,
        artifacts=["storage/sector_rotation"],
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
    if task_id == "refresh-sector-rotation":
        return _run_refresh_sector_rotation()
    if task_id == "update-cs-data":
        return _run_update_cs_data()
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


def _ledger_tracking(names: dict[str, dict[str, str]]) -> list[dict[str, Any]] | None:
    """从预测生命周期账本读复盘跟踪 (U2).

    已结算记录直接取账本 ``realized_ret`` (= ret1d, 不重扫 csv); 未结算记录
    (``status="open"``) 回退到 ``_horizon_return`` 临时重算. 账本不可用 / 为空时
    返回 None, 调用方回退旧 JSON 逻辑 (退役过渡期的回放来源).
    """
    try:
        from kss.prediction.ledger import PredictionLedger, STATUS_SETTLED
    except Exception:
        return None
    try:
        ledger = PredictionLedger()
        records = ledger.query()
    except Exception:
        return None
    if not records:
        return None

    by_date: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        by_date.setdefault(rec.get("prediction_date", ""), []).append(rec)

    out: list[dict[str, Any]] = []
    for date in sorted(by_date, reverse=True):
        if not date:
            continue
        picks_out: list[dict[str, Any]] = []
        buckets: dict[str, list[float]] = {key: [] for key, _ in _HORIZONS}
        for rec in by_date[date]:
            symbol = rec.get("symbol", "")
            meta = names.get(symbol, {})
            pick_row: dict[str, Any] = {"symbol": symbol, "name": meta.get("name", "")}
            settled = rec.get("status") == STATUS_SETTLED and rec.get("realized_ret") is not None
            for key, hold in _HORIZONS:
                if key == "ret1d" and settled:
                    ret = _safe_float(rec.get("realized_ret"))  # 账本真值, 不重扫 csv
                else:
                    ret = _horizon_return(symbol, date, hold)
                pick_row[key] = ret
                if ret is not None:
                    buckets[key].append(ret)
            pick_row["status"] = rec.get("status")
            pick_row["outcome"] = rec.get("outcome")
            pick_row["attribution"] = rec.get("attribution_category")
            picks_out.append(pick_row)
        row: dict[str, Any] = {"date": date, "nPicks": len(picks_out), "picks": picks_out}
        for key, _ in _HORIZONS:
            values = buckets[key]
            row[key] = (sum(values) / len(values)) if values else None
        out.append(row)
    return out


def _recommendation_tracking(names: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    ledger_rows = _ledger_tracking(names)
    if ledger_rows is not None:
        return ledger_rows
    # 回退: 账本不可用时读旧 JSON (退役过渡期的回放来源)
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


_DAILYBASIC_JSON = STATE_ROOT / "storage" / "macro" / "dailybasic_latest.json"
_MARKET_STRIP_JSON = STATE_ROOT / "storage" / "macro" / "market_strip.json"
ETF_RADAR_DIR = STATE_ROOT / "storage" / "etf_radar"


def _market_strip() -> dict[str, Any] | None:
    """总览第一行市场速览：A500ETF 当日行情 + 北向资金（refresh_market_strip.py 产出）。"""
    if not _MARKET_STRIP_JSON.exists():
        return None
    try:
        return json.loads(_MARKET_STRIP_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


_NAME_INDEX_JSON = STATE_ROOT / "storage" / "macro" / "stock_name_index.json"


def _load_name_index() -> dict[str, Any]:
    if not _NAME_INDEX_JSON.exists():
        return {}
    try:
        return json.loads(_NAME_INDEX_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_stocks(text: str) -> list[dict[str, Any]]:
    """把自由文本/OCR 结果（名称/代码，多种分隔）解析为 ts_code。供股票池导入用。

    策略（券商自选截图 = 名称 + 6 位代码，常含价格/涨跌噪声）：
      1) 正则抽全部独立 6 位代码（最可靠，覆盖 A股 + ETF）。
      2) 名称 token：精确 byName，否则子串模糊（截断的 ETF 名是规范名子串 → 命中并按代码去重）。
      3) 跳过含数字/纯符号 token（价格、涨跌幅等噪声）。
    每条返回 {query, code, name, kind, ok, inPool}。
    """
    index = _load_name_index()
    by_name: dict[str, str] = index.get("byName", {})
    by_code: dict[str, str] = index.get("byCode", {})
    pairs: list[list[str]] = index.get("pairs", [])
    meta: dict[str, dict[str, str]] = index.get("meta", {})
    existing = {p.stem.replace("cs_data_", "") for p in STATE_ROOT.glob("cs_data_*.csv")}

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(query: str, code: str) -> None:
        if code and code in seen:
            return
        if code:
            seen.add(code)
        info = meta.get(code, {}) if code else {}
        out.append({
            "query": query,
            "code": code,
            "name": info.get("name", ""),
            "kind": info.get("kind", "stock") if code else "",
            "ok": bool(code),
            "inPool": code.split(".")[0] in existing if code else False,
        })

    text = text or ""
    # 名称 token：含中文字符的才算名称（指数名带数字如「科创100」也保留；价格/涨跌幅/纯代码无中文 → 排除）
    name_tokens = [t.strip() for t in re.split(r"[\s,，、;；/\|]+", text.strip())]
    name_tokens = [t for t in name_tokens if t and re.search(r"[一-龥]", t)]
    has_codes = bool(re.search(r"(?<![\d.])\d{6}(?![\d.])", text))
    claimed: set[str] = set()   # 被指数名认领的 6 位代码，避免与同号个股重复

    # A) 精确名称（含指数）：指数认领其代码
    for tok in name_tokens:
        ts = by_name.get(tok)
        if ts:
            push(tok, ts)
            if meta.get(ts, {}).get("kind") == "index":
                claimed.add(ts.split(".")[0])

    # B) 6 位代码（跳过被指数认领的；忽略价格里的数字）
    for code6 in re.findall(r"(?<![\d.])\d{6}(?![\d.])", text):
        if code6 in claimed:
            continue
        ts = by_code.get(code6)
        if ts:
            push(code6, ts)

    # C) 纯名称输入（无代码）才做模糊 + 未匹配反馈；券商截图有代码，截断名不模糊以免误配
    if not has_codes:
        for tok in name_tokens:
            if any(o["query"] == tok for o in out):
                continue
            hit = ""
            for n, c in pairs:
                if len(tok) >= 2 and (tok in n or n in tok):
                    hit = c
                    break
            push(tok, hit)
    return out


def _run_import_stocks(codes: list[str]) -> dict[str, Any]:
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("import-stocks", "导入股票同步", started)
    return _run_process_task(
        "import-stocks",
        "导入股票同步",
        [str(python), "scripts/fetch_stock_data.py", "--codes", ",".join(codes)],
        started,
        artifacts=[f"cs_data_{c.split('.')[0]}.csv" for c in codes],
        timeout=300,
    )


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
    fp = STATE_ROOT / f"cs_data_{digits}.csv"
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

def _sector_rotation_snapshot(path: Path) -> dict[str, Any] | None:
    """读取一份板块热点轮动归档；失败返回 None."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sector_rotation_history(limit: int = 30) -> list[dict[str, Any]]:
    """板块热点轮动归档列表：最新 N 个交易日，新到旧。

    仅返回用于日期列表的轻量字段（tradeDate、leaderCoverage、
    crossSourceSignals 计数），避免把全部 leader 矩阵塞进快照。
    """
    if not SECTOR_ROTATION_DIR.exists():
        return []
    files = sorted(SECTOR_ROTATION_DIR.glob("*.json"), reverse=True)
    out: list[dict[str, Any]] = []
    for fp in files:
        if len(out) >= limit:
            break
        snap = _sector_rotation_snapshot(fp)
        if snap is None:
            continue
        signals = snap.get("crossSourceSignals") or {}
        out.append({
            "tradeDate": snap.get("tradeDate", fp.stem),
            "lookbackDays": snap.get("lookbackDays"),
            "historyCoverage": snap.get("historyCoverage"),
            "leaderCoverage": snap.get("leaderCoverage"),
            "mainlineCount": len(signals.get("mainline") or []),
            "demonBoardCount": len(signals.get("demonBoard") or []),
            "oldHotspotFadingCount": len(signals.get("oldHotspotFading") or []),
            "satelliteCount": len(signals.get("satellite") or []),
        })
    return out


def _latest_sector_rotation(limit_boards: int = 6, limit_leaders: int = 5) -> dict[str, Any] | None:
    """最新一份板块热点轮动的摘要卡片数据。

    Args:
        limit_boards: 每个分类保留的板块数量。
        limit_leaders: 返回的龙头总数。
    """
    if not SECTOR_ROTATION_DIR.exists():
        return None
    files = sorted(SECTOR_ROTATION_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    snap = _sector_rotation_snapshot(files[0])
    if snap is None:
        return None
    signals = snap.get("crossSourceSignals") or {}
    all_boards = (snap.get("industries") or []) + (snap.get("concepts") or []) + (snap.get("kaipanBoards") or [])
    leaders: list[dict[str, Any]] = []
    for board in snap.get("leaderBoards") or []:
        for leader in board.get("leaderStocks") or []:
            leaders.append({
                "boardName": board.get("name"),
                "symbol": leader.get("code"),
                "name": leader.get("name"),
                "appearances": leader.get("count"),
                "positions": leader.get("positions"),
            })
    leaders.sort(key=lambda x: (x.get("appearances") or 0), reverse=True)
    return {
        "tradeDate": snap.get("tradeDate"),
        "lookbackDays": snap.get("lookbackDays"),
        "leaderCoverage": snap.get("leaderCoverage"),
        "historyCoverage": snap.get("historyCoverage"),
        "mainline": (signals.get("mainline") or [])[:limit_boards],
        "demonBoard": (signals.get("demonBoard") or [])[:limit_boards],
        "oldHotspotFading": (signals.get("oldHotspotFading") or [])[:limit_boards],
        "topLeaders": leaders[:limit_leaders],
        "boardCount": len(all_boards),
    }


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


# ===================================================================== #
# U6 截 1 —— 统一发现管道合并 + 去重 + 共识溢价（相关性预检门控）
#
# 四发现管道已在本 bridge 汇聚（_build_logmv_picks / _bj_scan / 板块热点 /
# _perilla_picks），单一消费者，故合并逻辑做在 bridge 内私有函数 `_discovery_merge`，
# 不新建 kss/discovery/ 模块（推迟到出现第二个消费者再抽）。
#
# 设计不变量：
#   - 各管道 adapter 内部 min-max 归一化到 [0,1]（量纲隔离，避免跨管道 look-ahead，
#     见 brainstorm Key Decisions：全局归一化引入跨管道联动）。
#   - 共识溢价 consensus_multiplier 默认 1.2，仅在 hit_count >= 2 时生效。
#   - 相关性预检（关键护栏）：合并前先算管道两两候选集 Jaccard；任意对 >= 阈值
#     视为高相关，对**该对涉及的 ts_code** 取消共识溢价——A 股横截面高相关下，
#     相关管道的「共识」是放大共同偏差，不是独立确认。门控在已证独立性上。
#   - pipeline_weights 从 storage/pipeline_weights.json 读取（缺失→等权），运行时不改。
#   - 金融数字代码渲染：score/raw_score 全部代码算，无 LLM 介入。
# ===================================================================== #

PIPELINE_WEIGHTS_PATH = STATE_ROOT / "storage" / "pipeline_weights.json"
PIPELINE_IDS = ("log_mv", "bj50_scan", "sector_hotspot", "supply_chain")
CONSENSUS_MULTIPLIER = 1.2
MIN_HIT_COUNT_FOR_BONUS = 2
CORRELATION_JACCARD_THRESHOLD = 0.6


def _minmax(values: list[float]) -> list[float]:
    """min-max 归一化到 [0,1]；常数序列 → 全 0.5（中性，不引入虚假排序）。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]
    span = hi - lo
    return [(v - lo) / span for v in values]


def _adapt_logmv(date: str | None = None) -> dict[str, Any] | None:
    """log_mv 反向管道 → PipelineResult。score = 反转后的归一化 log_mv 因子值。

    log_mv 反向逻辑：市值越小（factor_value 越低）越优 → score 越高。故对
    factor_value 归一化后取 1 - x（小市值得高分），与管道选股方向一致。
    """
    try:
        target_date, picks = _build_logmv_picks(date)
    except Exception:
        return None
    if not picks:
        return {"pipeline_id": "log_mv", "date": target_date, "candidates": []}
    fvals = [float(p["factor_value"]) for p in picks]
    norm = _minmax(fvals)
    candidates = []
    for p, nx in zip(picks, norm):
        candidates.append({
            "ts_code": p["symbol"],
            "name": p.get("name", ""),
            "score": round(1.0 - nx, 6),  # 反向：小市值 → 高分
            "raw_score": round(float(p["factor_value"]), 6),
            "metadata": {"rank_position": p.get("rank_position"), "rank_pct": p.get("rank_pct")},
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"pipeline_id": "log_mv", "date": str(target_date), "candidates": candidates}


def _adapt_bj50() -> dict[str, Any] | None:
    """北证扫描管道 → PipelineResult。score = total_score 归一化。"""
    date, rows = _latest_bj_scan()
    if not rows:
        return None
    scored = [(r.get("ts_code", ""), _safe_float(r.get("total_score")), r.get("name", "")) for r in rows]
    scored = [(c, s, n) for c, s, n in scored if c and s is not None]
    if not scored:
        return {"pipeline_id": "bj50_scan", "date": date or "", "candidates": []}
    norm = _minmax([s for _, s, _ in scored])
    candidates = []
    for (code, raw, name), nx in zip(scored, norm):
        candidates.append({
            "ts_code": code,
            "name": name,
            "score": round(nx, 6),
            "raw_score": round(float(raw), 6),
            "metadata": {"universe": "bj50"},
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"pipeline_id": "bj50_scan", "date": date or "", "candidates": candidates}


def _expand_sector_leaders(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """板块热点 → 成分股展开（板块无直接 ts_code，须从 leaderStocks 取代码）。

    成分股 score 从板块 heatScore 继承（等分继承：同板块龙头共享板块热度）。
    展开来源优先级：leaderBoards.leaderStocks > industries/concepts[].leaderStocks。
    **降级**：归档快照若全程无 leaderStocks（实测当前归档即如此），返回空列表——
    板块管道当日不贡献候选（fail loud：不编造 ts_code）。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _emit(code: str, name: str, heat: float | None, board: str) -> None:
        code = (code or "").strip()
        if not code or code in seen:
            return
        seen.add(code)
        out.append({"ts_code": code, "name": name or "", "heat": heat, "board": board})

    # 1) 顶层 leaderBoards（含 board heatScore 时继承，否则后续归一化兜底）
    for b in snap.get("leaderBoards") or []:
        bname = b.get("name", "")
        bheat = _safe_float(b.get("heatScore"))
        for s in b.get("leaderStocks") or []:
            _emit(s.get("code", ""), s.get("name", ""), bheat, bname)
    # 2) industries / concepts 各板块内的 leaderStocks（board heatScore 继承）
    for key in ("industries", "concepts"):
        for b in snap.get(key) or []:
            bheat = _safe_float(b.get("heatScore"))
            bname = b.get("name", "")
            for s in b.get("leaderStocks") or []:
                _emit(s.get("code", ""), s.get("name", ""), bheat, bname)
    return out


def _adapt_sector_hotspot() -> dict[str, Any] | None:
    """板块热点管道 → PipelineResult（成分股展开后归一化 heatScore）。"""
    if not SECTOR_ROTATION_DIR.exists():
        return None
    files = sorted(SECTOR_ROTATION_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    snap = _sector_rotation_snapshot(files[0])
    if snap is None:
        return None
    date = str(snap.get("tradeDate") or files[0].stem)
    expanded = _expand_sector_leaders(snap)
    if not expanded:
        # 降级：归档无 leaderStocks → 板块管道当日空候选（不编造 ts_code）
        return {"pipeline_id": "sector_hotspot", "date": date, "candidates": [], "degraded": "no_leader_stocks"}
    heats = [e["heat"] if e["heat"] is not None else 0.0 for e in expanded]
    norm = _minmax(heats)
    candidates = []
    for e, nx in zip(expanded, norm):
        candidates.append({
            "ts_code": e["ts_code"],
            "name": e["name"],
            "score": round(nx, 6),
            "raw_score": round(float(e["heat"]), 6) if e["heat"] is not None else None,
            "metadata": {"board": e["board"]},
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"pipeline_id": "sector_hotspot", "date": date, "candidates": candidates}


def _adapt_supply_chain(top_n: int = 30, min_score: float = 0.4) -> dict[str, Any] | None:
    """紫苏叶产业链管道 → PipelineResult。perilla_score 已是 [0,1]，直接用。"""
    picks = _perilla_picks(top_n=top_n, min_score=min_score)
    if not picks:
        return None
    candidates = []
    for p in picks:
        sc = p.get("score")
        if sc is None:
            continue
        candidates.append({
            "ts_code": p["symbol"],
            "name": p.get("name", ""),
            "score": round(float(sc), 6),  # 已 0-1，无需再归一化
            "raw_score": round(float(sc), 6),
            "metadata": {"layer": p.get("layer"), "role": p.get("role")},
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    today = datetime.now().strftime("%Y%m%d")
    return {"pipeline_id": "supply_chain", "date": today, "candidates": candidates}


def _load_pipeline_weights() -> dict[str, float]:
    """读 storage/pipeline_weights.json；缺失 / 损坏 → 四管道等权（各 0.25）。

    合并层每次读取，运行时不修改（权重更新走离线 compute_pipeline_alpha.py + 人工确认）。
    """
    equal = {pid: 0.25 for pid in PIPELINE_IDS}
    if not PIPELINE_WEIGHTS_PATH.exists():
        return equal
    try:
        raw = json.loads(PIPELINE_WEIGHTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return equal
    if not isinstance(raw, dict):
        return equal
    out = dict(equal)
    for pid in PIPELINE_IDS:
        v = raw.get(pid)
        if isinstance(v, (int, float)) and v >= 0:
            out[pid] = float(v)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _correlation_precheck(
    results: list[dict[str, Any]],
    threshold: float = CORRELATION_JACCARD_THRESHOLD,
) -> tuple[list[list[str]], set[str]]:
    """管道两两命中相关性预检（共识加权前置护栏）。

    A 股横截面高相关：相关管道的「共识」放大共同偏差，伪装独立确认。故先算
    任意两管道候选集 Jaccard；任意对 >= threshold 视为高相关 →

      1. 该对管道名记入 warnings（前端提示「共识溢价可信度降低」）；
      2. 该对涉及的**所有 ts_code** 标记为「suppressed」——这些票的共识溢价被取消
         （门控在已证独立性：只有低相关管道间的共识才享受溢价）。

    Returns:
        (warnings, suppressed_codes)
        warnings: 高相关管道对名称列表（如 [["sector_hotspot","bj50_scan"]]）。
        suppressed_codes: 共识溢价被抑制的 ts_code 集合。
    """
    sets: dict[str, set[str]] = {
        r["pipeline_id"]: {c["ts_code"] for c in r.get("candidates", [])}
        for r in results
    }
    ids = list(sets.keys())
    warnings: list[list[str]] = []
    suppressed: set[str] = set()
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pa, pb = ids[i], ids[j]
            jac = _jaccard(sets[pa], sets[pb])
            if jac >= threshold:
                warnings.append([pa, pb])
                # 高相关对的**交集**票享受不到独立确认 → 抑制其溢价
                suppressed |= (sets[pa] & sets[pb])
    return warnings, suppressed


def _discovery_merge(
    results: list[dict[str, Any]] | None = None,
    *,
    pipeline_weights: dict[str, float] | None = None,
    consensus_multiplier: float = CONSENSUS_MULTIPLIER,
    min_hit_count_for_bonus: int = MIN_HIT_COUNT_FOR_BONUS,
    correlation_threshold: float = CORRELATION_JACCARD_THRESHOLD,
) -> dict[str, Any]:
    """四发现管道合并 + 去重 + 共识溢价（相关性门控）。

    Args:
        results: PipelineResult dict 列表；None → 触发四管道 adapter 实时取数。
        pipeline_weights: 管道权重 dict；None → 读 storage/pipeline_weights.json（缺失等权）。
        consensus_multiplier: hit_count>=门槛 时的共识溢价（默认 1.2）。
        min_hit_count_for_bonus: 触发共识溢价的最小命中管道数（默认 2）。
        correlation_threshold: Jaccard 高相关阈值（默认 0.6，触发溢价抑制）。

    Returns:
        MergedCandidateList dict：candidates（按 final_score 降序、ts_code 唯一）
        + warnings（高相关管道对）+ pipelineWeights + 各管道命中数 meta。

    合并语义：
        final_score = (Σ w_i × score_i over hit pipelines)
                      × (consensus_multiplier if hit_count>=门槛 且 该票未被相关性抑制 else 1.0)
        分母只计入 score 非 None 的管道（None 管道不当 0 分，见 Acceptance Example C）。
    """
    if results is None:
        results = [
            r for r in (
                _adapt_logmv(),
                _adapt_bj50(),
                _adapt_sector_hotspot(),
                _adapt_supply_chain(),
            ) if r is not None
        ]
    weights = pipeline_weights if pipeline_weights is not None else _load_pipeline_weights()

    warnings, suppressed = _correlation_precheck(results, threshold=correlation_threshold)

    # 按 ts_code 聚合
    agg: dict[str, dict[str, Any]] = {}
    for r in results:
        pid = r["pipeline_id"]
        for c in r.get("candidates", []):
            code = c.get("ts_code")
            if not code:
                continue
            sc = c.get("score")
            if sc is None:  # None score 管道不参与加权 / 不计入分母（Example C）
                continue
            slot = agg.setdefault(code, {
                "ts_code": code,
                "name": c.get("name", ""),
                "sources": [],
                "pipeline_scores": {},
                "weighted_sum": 0.0,
            })
            if not slot["name"] and c.get("name"):
                slot["name"] = c.get("name")
            slot["sources"].append(pid)
            slot["pipeline_scores"][pid] = sc
            slot["weighted_sum"] += float(weights.get(pid, 0.0)) * float(sc)

    merged: list[dict[str, Any]] = []
    for code, slot in agg.items():
        hit_count = len(slot["sources"])
        base = slot["weighted_sum"]
        bonus = 1.0
        consensus_applied = False
        if hit_count >= min_hit_count_for_bonus and code not in suppressed:
            bonus = consensus_multiplier
            consensus_applied = True
        merged.append({
            "ts_code": code,
            "name": slot["name"],
            "final_score": round(base * bonus, 6),
            "base_score": round(base, 6),
            "hit_count": hit_count,
            "sources": sorted(slot["sources"]),
            "pipeline_scores": {k: round(float(v), 6) for k, v in slot["pipeline_scores"].items()},
            "consensus_applied": consensus_applied,
            "consensus_suppressed": (hit_count >= min_hit_count_for_bonus and code in suppressed),
        })
    merged.sort(key=lambda x: (x["final_score"], x["ts_code"]), reverse=True)

    pipeline_meta = [
        {
            "pipeline_id": r["pipeline_id"],
            "date": r.get("date", ""),
            "nCandidates": len(r.get("candidates", [])),
            "degraded": r.get("degraded"),
        }
        for r in results
    ]
    return {
        "candidates": merged,
        "warnings": warnings,
        "pipelineWeights": weights,
        "pipelines": pipeline_meta,
        "consensusMultiplier": consensus_multiplier,
        "correlationThreshold": correlation_threshold,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


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
        "sectorRotationHistory": _sector_rotation_history(),
        "latestSectorRotation": _latest_sector_rotation(),
        "marketStrip": _market_strip(),
        "pythonEnvironment": _python_env_status(),
        "recentTaskRuns": _task_history(),
    }


def _stock_review(symbol: str) -> dict[str, Any] | None:
    """从最新一份包含该股的每日复盘里抽取结论（标题/快照/预期区间/建议）。"""
    digits = symbol.split(".")[0]
    if not REVIEW_DIR.exists():
        return None
    header_re = re.compile(r"\*?\s*[^*()]+\((\d{6})\)")
    for fp in sorted(REVIEW_DIR.glob("*.md"), reverse=True):
        lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = None
        for i, ln in enumerate(lines):
            if "📊" in ln:
                m = header_re.search(ln)
                if m and m.group(1) == digits:
                    start = i
                    break
        if start is None:
            continue

        body: list[str] = []
        for ln in lines[start + 1:]:
            if ln.strip() == "---":
                break
            body.append(ln)

        headline = ""
        snapshot = ""
        expectation: list[str] = []
        suggestions: list[str] = []
        mode = ""
        for ln in body:
            s = ln.strip()
            if not s:
                continue
            if not headline and (s[0] in "🚀🌊📈📉🔥⚡" or "主升" in s or "龙头" in s):
                headline = s.lstrip("🚀🌊📈📉🔥⚡ ").strip()
                continue
            if s.startswith("收 ") and not snapshot:
                snapshot = s
            if s.startswith("*预期区间*"):
                mode = "exp"
                continue
            if s.startswith("*建议*"):
                mode = "sug"
                continue
            if s.startswith("*") or s[0] in "📊📈📉🚀":
                mode = ""
            if mode == "exp":
                expectation.append(s.replace("*", ""))
            elif mode == "sug" and s.startswith("•"):
                suggestions.append(s.lstrip("• ").replace("*", "").strip())

        if not (headline or snapshot or suggestions):
            return None
        return {
            "date": fp.stem,
            "headline": headline,
            "snapshot": snapshot,
            "expectation": " ".join(expectation),
            "suggestions": suggestions,
        }
    return None


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
    for row in rows[-400:]:   # 多给历史以支撑周/月/年线重采样
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
        "reviewConclusion": _stock_review(symbol),
    }


# ---------------------------------------------------------------------------
# 定时任务（launchd）自省与操作
#
# 真正的定时任务是 deploy/launchd/com.zcdeng.kss.*.plist（crontab 已是 legacy）。
# 这里只用标准库 plistlib + 系统 launchctl 读状态、重跑、启停，绝不改 plist 内容。
# 所有 launchctl 调用都走列表参数 + label 白名单（白名单由 plist 文件名派生），
# 不拼接用户输入，杜绝注入。
# ---------------------------------------------------------------------------

LAUNCHD_DIR = PROJECT_ROOT / "deploy" / "launchd"

# label 后缀 → 中文任务名（新增 launchd 任务时在此补一条）。
LABEL_TITLES = {
    "macro_daily": "宏观数据更新",
    "morning_divergence_alert": "开盘见顶快讯",
    "paper_trade_daily": "纸交易日更",
    "paper_trade_weekly": "纸交易周报",
    "prediction_validation_weekly": "预测校验周报",
    "scan_bj50_daily": "北证50扫描",
    "scanner": "科创共振扫描",
    "sector_review_daily": "板块复盘",
    "update_data_daily": "股票池日线更新",
    "hotspot_rotation_daily": "板块热点轮动归档",
    "trends_archive_daily": "趋势归档",
    "selfcheck": "开机自检补跑",
}

# label 后缀 → 分类（任务页按此分组 + 批量重跑）。
LABEL_CATEGORY = {
    "update_data_daily": "数据更新",
    "macro_daily": "数据更新",
    "scanner": "扫描选股",
    "scan_bj50_daily": "扫描选股",
    "sector_review_daily": "板块复盘",
    "hotspot_rotation_daily": "板块复盘",
    "trends_archive_daily": "数据更新",
    "paper_trade_daily": "纸交易",
    "paper_trade_weekly": "纸交易",
    "prediction_validation_weekly": "校验回测",
    "morning_divergence_alert": "盘中快讯",
    "selfcheck": "系统",
}
# 分类展示顺序（未列出的归「其他」并排末尾）。
CATEGORY_ORDER = ["数据更新", "扫描选股", "板块复盘", "盘中快讯", "纸交易", "校验回测", "系统", "其他"]

_WEEKDAY_CN = {0: "日", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}


def _launchd_plists() -> dict[str, Path]:
    """label → plist 路径。白名单的唯一事实源。"""
    out: dict[str, Path] = {}
    for path in sorted(LAUNCHD_DIR.glob("com.zcdeng.kss.*.plist")):
        label = path.stem  # 文件名去 .plist == Label
        out[label] = path
    return out


def _hm(entry: dict) -> str:
    return f"{int(entry.get('Hour', 0)):02d}:{int(entry.get('Minute', 0)):02d}"


def _parse_schedule(interval: Any) -> str:
    """StartCalendarInterval（dict 或 list[dict]）→ 人读调度串。"""
    if interval is None:
        return "未设定"
    entries = interval if isinstance(interval, list) else [interval]
    entries = [e for e in entries if isinstance(e, dict)]
    if not entries:
        return "未设定"

    times = {_hm(e) for e in entries}
    weekdays = {int(e["Weekday"]) for e in entries if "Weekday" in e}

    # 工作日同一时刻
    if len(times) == 1 and weekdays == {1, 2, 3, 4, 5}:
        return f"工作日 {next(iter(times))}"
    # 单条
    if len(entries) == 1:
        e = entries[0]
        if "Weekday" in e:
            return f"每周{_WEEKDAY_CN.get(int(e['Weekday']), '?')} {_hm(e)}"
        return f"每天 {_hm(e)}"
    # 其余：逐条列出
    parts = []
    for e in entries:
        if "Weekday" in e:
            parts.append(f"周{_WEEKDAY_CN.get(int(e['Weekday']), '?')} {_hm(e)}")
        else:
            parts.append(_hm(e))
    return " / ".join(parts)


def _interval_entries(interval: Any) -> list[dict]:
    if interval is None:
        return []
    entries = interval if isinstance(interval, list) else [interval]
    return [e for e in entries if isinstance(e, dict)]


def _entry_dt_on(entry: dict, day) -> "datetime | None":
    """某天 day 上这条 StartCalendarInterval 的触发时刻；weekday 不匹配返回 None。"""
    wd = entry.get("Weekday")
    if wd is not None:
        wd_norm = 7 if int(wd) == 0 else int(wd)  # launchd 0/7=周日；对齐 isoweekday
        if day.isoweekday() != wd_norm:
            return None
    return datetime(day.year, day.month, day.day, int(entry.get("Hour", 0)), int(entry.get("Minute", 0)))


def _fire_times(interval: Any, now: "datetime") -> tuple["datetime | None", "datetime | None"]:
    """(expected, next)：最近一次 ≤now 的预定触发 / 最近一次 >now 的预定触发。"""
    from datetime import timedelta

    entries = _interval_entries(interval)
    if not entries:
        return None, None
    prev = nxt = None
    for d in range(0, 9):
        day = (now - timedelta(days=d)).date()
        for e in entries:
            dt = _entry_dt_on(e, day)
            if dt is not None and dt <= now and (prev is None or dt > prev):
                prev = dt
    for d in range(0, 9):
        day = (now + timedelta(days=d)).date()
        for e in entries:
            dt = _entry_dt_on(e, day)
            if dt is not None and dt > now and (nxt is None or dt < nxt):
                nxt = dt
    return prev, nxt


def _missed_cycles(interval: Any, now: "datetime", last_run: "datetime | None") -> int:
    """(last_run, now] 区间内本该触发的次数；last_run 为空按全区间计。"""
    from datetime import timedelta

    entries = _interval_entries(interval)
    if not entries:
        return 0
    cnt = 0
    for d in range(0, 15):
        day = (now - timedelta(days=d)).date()
        for e in entries:
            dt = _entry_dt_on(e, day)
            if dt is not None and dt <= now and (last_run is None or dt > last_run):
                cnt += 1
    return cnt


def _run_launchctl(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["launchctl", *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "launchctl not found"
    except subprocess.TimeoutExpired:
        return 124, "", "launchctl timeout"


def _disabled_labels(uid: int) -> set[str]:
    """gui 域里被 disable 的 label 集合。"""
    rc, out, _ = _run_launchctl(["print-disabled", f"gui/{uid}"])
    if rc != 0:
        return set()
    disabled: set[str] = set()
    for line in out.splitlines():
        m = re.search(r'"([^"]+)"\s*=>\s*(?:disabled|true)', line)
        if m and "true" in line:
            disabled.add(m.group(1))
    return disabled


def _launchctl_status(label: str, uid: int) -> dict[str, Any]:
    """单任务运行态：是否加载、上次退出码、pid。"""
    rc, out, _ = _run_launchctl(["print", f"gui/{uid}/{label}"])
    if rc == 0:
        last_exit = None
        pid = None
        m = re.search(r"last exit code\s*=\s*(\-?\d+)", out)
        if m:
            last_exit = int(m.group(1))
        m = re.search(r"\bpid\s*=\s*(\d+)", out)
        if m:
            pid = int(m.group(1))
        return {"loaded": True, "lastExit": last_exit, "pid": pid}
    # 未 bootstrap：回退 launchctl list 取 LastExitStatus
    rc2, out2, _ = _run_launchctl(["list", label])
    if rc2 == 0:
        m = re.search(r'"LastExitStatus"\s*=\s*(\-?\d+)', out2)
        last_exit = int(m.group(1)) if m else None
        return {"loaded": False, "lastExit": last_exit, "pid": None}
    return {"loaded": False, "lastExit": None, "pid": None}


def _last_run(log_path: str | None) -> dict[str, Any]:
    """从任务日志取上次运行时间（文件 mtime）+ 末行摘要。"""
    if not log_path:
        return {"at": None, "line": None}
    p = Path(log_path)
    if not p.exists() or p.stat().st_size == 0:
        return {"at": None, "line": None}
    at = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    line = None
    try:
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
        if lines:
            line = _shorten(lines[-1], 160)
    except OSError:
        pass
    return {"at": at, "line": line}


def _scheduled_job(label: str, path: Path, uid: int, disabled: set[str]) -> dict[str, Any]:
    import plistlib

    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, ValueError):
        pl = {}

    suffix = label.replace("com.zcdeng.kss.", "")
    interval = pl.get("StartCalendarInterval")
    prog = pl.get("ProgramArguments") or []
    script = Path(prog[0]).name if prog else ""
    schedule = _parse_schedule(interval)
    status = _launchctl_status(label, uid)
    out_path = pl.get("StandardOutPath")
    last = _last_run(out_path)

    last_exit = status["lastExit"]
    if last_exit is None:
        last_status = "unknown"
    elif last_exit == 0:
        last_status = "success"
    else:
        last_status = "failed"

    # 漏跑判定：日志 mtime 当作上次实际运行时刻，与最近一次预定触发比较。
    last_run_dt = None
    if out_path:
        p = Path(out_path)
        if p.exists() and p.stat().st_size > 0:
            last_run_dt = datetime.fromtimestamp(p.stat().st_mtime)
    now = datetime.now()
    expected, nxt = _fire_times(interval, now)
    enabled = label not in disabled
    # selfcheck 是补跑看门狗本身，永不算漏跑（否则会把自己列进补跑横幅）。
    is_watchdog = label.endswith(".selfcheck")
    stale = bool(not is_watchdog and enabled and expected is not None and (last_run_dt is None or last_run_dt < expected))
    missed = _missed_cycles(interval, now, last_run_dt) if stale else 0

    return {
        "label": label,
        "title": LABEL_TITLES.get(suffix, suffix),
        "category": LABEL_CATEGORY.get(suffix, "其他"),
        "schedule": schedule,
        "script": script,
        "enabled": enabled,
        "loaded": status["loaded"],
        "running": status["pid"] is not None,
        "lastStatus": last_status,
        "lastRunAt": last["at"],
        "lastLine": last["line"],
        "stale": stale,
        "missedCycles": missed,
        "expectedAt": expected.strftime("%Y-%m-%d %H:%M") if expected else None,
        "nextRunAt": nxt.strftime("%Y-%m-%d %H:%M") if nxt else None,
    }


def _scheduled_jobs() -> list[dict[str, Any]]:
    uid = os.getuid()
    disabled = _disabled_labels(uid)
    plists = _launchd_plists()
    return [_scheduled_job(label, path, uid, disabled) for label, path in plists.items()]


def _cron_action(label: str, action: str) -> dict[str, Any]:
    """对单个 launchd 任务重跑/启用/停用。label 必须命中白名单。"""
    plists = _launchd_plists()
    if label not in plists:
        return {"ok": False, "error": f"unknown label: {label}"}
    if action not in {"rerun", "enable", "disable"}:
        return {"ok": False, "error": f"unknown action: {action}"}

    uid = os.getuid()
    domain = f"gui/{uid}"
    path = str(plists[label])
    errors: list[str] = []

    if action == "rerun":
        rc, _, err = _run_launchctl(["kickstart", "-k", f"{domain}/{label}"])
        if rc != 0:
            errors.append(err.strip() or f"kickstart rc={rc}")
    elif action == "disable":
        # 立即卸载（未加载则忽略错误）+ 持久化停用
        _run_launchctl(["bootout", f"{domain}/{label}"])
        rc, _, err = _run_launchctl(["disable", f"{domain}/{label}"])
        if rc != 0:
            errors.append(err.strip() or f"disable rc={rc}")
    elif action == "enable":
        _run_launchctl(["enable", f"{domain}/{label}"])
        rc, _, err = _run_launchctl(["bootstrap", domain, path])
        # bootstrap 对已加载任务会报错，可忽略
        if rc != 0 and "already" not in err.lower():
            errors.append(err.strip() or f"bootstrap rc={rc}")

    disabled = _disabled_labels(uid)
    job = _scheduled_job(label, plists[label], uid, disabled)
    if errors:
        return {"ok": False, "error": "; ".join(errors), "job": job}
    return {"ok": True, "job": job}


def _kickstart_labels(labels: list[str], require_stale: bool) -> dict[str, Any]:
    """批量 kickstart：require_stale=True 只补跑漏跑项（开机自检/补跑），否则重跑全部启用项。
    label 必须命中白名单；停用项跳过；selfcheck 自身永不参与（避免递归补跑）。"""
    uid = os.getuid()
    domain = f"gui/{uid}"
    plists = _launchd_plists()
    disabled = _disabled_labels(uid)
    ran: list[dict[str, Any]] = []
    skipped: list[str] = []
    for label in labels:
        if label not in plists or label.endswith(".selfcheck"):
            skipped.append(label)
            continue
        job = _scheduled_job(label, plists[label], uid, disabled)
        if not job["enabled"]:
            skipped.append(label)
            continue
        if require_stale and not job["stale"]:
            skipped.append(label)
            continue
        rc, _, err = _run_launchctl(["kickstart", "-k", f"{domain}/{label}"])
        ran.append({
            "label": label,
            "title": job["title"],
            "ok": rc == 0,
            "error": (err.strip() or f"kickstart rc={rc}") if rc != 0 else None,
        })
    return {
        "ok": all(r["ok"] for r in ran) if ran else True,
        "count": len(ran),
        "ran": ran,
        "skipped": skipped,
    }


def _cron_catchup() -> dict[str, Any]:
    """补跑所有「应跑未跑」的启用任务（开机自检 / 应用内一键补跑共用）。"""
    return _kickstart_labels(list(_launchd_plists().keys()), require_stale=True)


def _cron_rerun_many(labels: list[str]) -> dict[str, Any]:
    """批量重跑指定 label（按分类全部重跑 / 全部重跑），无视漏跑与否，但跳过停用项。"""
    if not labels:
        labels = list(_launchd_plists().keys())
    return _kickstart_labels(labels, require_stale=False)


# ---------------------------------------------------------------------------
# 趋势页（U4）：只读 storage/trends/*.json 归档，聚合月度格子 + 单日明细。
# 归档由 .venv-desktop 脚本产出（archive_trends_daily/backfill_trends）；
# 本命令纯 stdlib，日期参数走正则白名单防注入/路径穿越。
# ---------------------------------------------------------------------------

_TRENDS_DIR = STATE_ROOT / "storage" / "trends"
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read_trend_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _trends_month(month: str) -> dict[str, Any]:
    """某月所有归档 → 月度格子（驱动热力格底色 + 板块点 + 推荐微条）。"""
    if not _MONTH_RE.match(month or ""):
        return {"month": month, "days": [], "error": "bad month (want YYYY-MM)"}
    days: list[dict[str, Any]] = []
    for path in sorted(_TRENDS_DIR.glob(f"{month}-*.json")):
        d = _read_trend_file(path)
        if not d:
            continue
        flags = d.get("flags") or {}
        days.append({
            "date": d.get("date"),
            "isTrading": d.get("isTrading", True),
            "heat": d.get("heat"),
            "inflowScore": d.get("inflowScore"),
            "inflowDir": d.get("inflowDir"),
            "sectorHeat": d.get("sectorHeat"),
            "recAvgFwd": d.get("recAvgFwd"),
            "north": d.get("north"),
            "sectorCount": d.get("sectorCount", 0),
            "topSector": d.get("topSector"),
            "recCount": d.get("recCount", 0),
            "flags": flags,
            "hasData": any(flags.values()) if flags else False,
        })
    return {"month": month, "days": days}


def _trends_day(date: str) -> dict[str, Any]:
    """单日完整明细；无归档返回明确空态。"""
    if not _DAY_RE.match(date or ""):
        return {"date": date, "found": False, "error": "bad date (want YYYY-MM-DD)"}
    path = _TRENDS_DIR / f"{date}.json"
    if not path.exists():
        return {"date": date, "found": False}
    d = _read_trend_file(path)
    if not d:
        return {"date": date, "found": False, "error": "unreadable archive"}
    d["found"] = True
    return d


# ---------------------------------------------------------------------------
# 概念主题龙头 / 第二梯队（十五五科技主题）
#
# 数据 = storage/themes_15th_5y.yaml（主题 → 行业/概念板块名）
#      + 最新归档快照 leaderBoards 的 leaderStocks（龙一~龙五 positions）。
# 龙头 = 最近一次位列龙一/龙二；第二梯队 = 龙三/龙四/龙五。
# 板块龙头数据只在快照 leaderBoards 里（getLongByPlate 拉到的 top 板块），
# 故主题覆盖度随每日 hotspot_rotation 归档逐日累积而提升。
# ---------------------------------------------------------------------------

_LEADER_RANK = {"龙一": 1, "龙二": 2, "龙三": 3, "龙四": 4, "龙五": 5}
THEMES_PATH = STATE_ROOT / "storage" / "themes_15th_5y.yaml"


def _load_themes() -> dict[str, dict[str, list[str]]]:
    """读 themes_15th_5y.yaml → {主题名: {industries, concepts}}。

    直接用 PyYAML 解析，不走 kss.sector.themes（其包 __init__ 会 import tushare，
    系统 python 没装）。文件缺失 / 解析失败 → 空字典（页面显示空态，不崩）。
    """
    if not THEMES_PATH.exists():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(THEMES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("themes"), dict):
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    for name, body in raw["themes"].items():
        if not isinstance(name, str) or not isinstance(body, dict):
            continue
        out[name] = {
            "industries": [str(x) for x in (body.get("industries") or [])],
            "concepts": [str(x) for x in (body.get("concepts") or [])],
        }
    return out


def _split_leader_tiers(stocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """leaderStocks → (龙头[龙一二], 第二梯队[龙三+])，按最近一次 position 的龙X 排名。"""
    leaders: list[dict] = []
    second: list[dict] = []
    for s in stocks:
        positions = s.get("positions") or []
        rank = 99
        if positions:
            rank = _LEADER_RANK.get(positions[0].split("/")[-1], 99)
        item = {
            "symbol": s.get("code") or s.get("symbol") or "",
            "name": s.get("name") or "",
            "appearances": s.get("count") or s.get("appearances") or 0,
            "positions": positions,
        }
        (leaders if rank <= 2 else second).append(item)
    return leaders, second


def _theme_leaders() -> list[dict[str, Any]]:
    """十五五科技主题 → 各板块龙头 / 第二梯队（数据驱动，主题数以 yaml 为准）。"""
    themes = _load_themes()
    if not themes:
        return []

    snap: dict[str, Any] = {}
    if SECTOR_ROTATION_DIR.exists():
        files = sorted(SECTOR_ROTATION_DIR.glob("*.json"), reverse=True)
        if files:
            snap = _sector_rotation_snapshot(files[0]) or {}

    leaders_by_board: dict[str, list[dict]] = {}
    for b in snap.get("leaderBoards") or []:
        if b.get("leaderStocks"):
            leaders_by_board[b.get("name", "")] = b["leaderStocks"]
    cls_by_board: dict[str, str] = {}
    for key in ("industries", "concepts"):
        for b in snap.get(key) or []:
            if b.get("classification"):
                cls_by_board[b.get("name", "")] = b["classification"]

    out: list[dict[str, Any]] = []
    for name, bucket in themes.items():
        board_names = list(bucket["industries"]) + list(bucket["concepts"])
        boards: list[dict[str, Any]] = []
        for bn in board_names:
            stocks = leaders_by_board.get(bn)
            if not stocks:
                continue
            tier1, tier2 = _split_leader_tiers(stocks)
            boards.append({
                "board": bn,
                "classification": cls_by_board.get(bn),
                "leaders": tier1,
                "secondTier": tier2,
            })
        out.append({
            "name": name,
            "boardNames": board_names,
            "boardCount": len(board_names),
            "boards": boards,
            "leaderBoardCount": len(boards),
        })
    return out


# 写命令（产生副作用 / 修改状态）；U6 MCP 的 paper-only 闸据此分类。
WRITE_COMMANDS = frozenset({
    "run", "import", "resolve",
    "cron-rerun", "cron-enable", "cron-disable", "cron-catchup", "cron-rerun-many",
})


def dispatch(command: str, args: list[str]) -> Any:
    """命令 → payload（传给 _json_dump 的对象）。subprocess(main) 与 sidecar 共用。
    参数错误 raise ValueError；下游可能 raise SystemExit（如 report 路径护栏）——
    sidecar 须捕获，不可让 daemon 退出。"""
    if command == "snapshot":
        return snapshot()
    if command == "stock":
        if not args:
            raise ValueError("stock command requires SYMBOL")
        return stock_detail(args[0])
    if command == "report":
        if not args:
            raise ValueError("report command requires PATH")
        return report_detail(args[0])
    if command == "paper-summary":
        return _paper_summary()
    if command == "resolve":
        return resolve_stocks(args[0] if args else "")
    if command == "import":
        codes = [c.strip() for c in (args[0] if args else "").split(",") if c.strip()]
        result = _run_import_stocks(codes)
        _append_task_history(result)
        return result
    if command == "python-env":
        return _python_env_status()
    if command == "sector-rotation":
        if not args:
            return _latest_sector_rotation() or {}
        return _sector_rotation_snapshot(SECTOR_ROTATION_DIR / f"{args[0]}.json") or {}
    if command == "sector-rotation-history":
        limit = int(args[0]) if args and args[0].isdigit() else 30
        return _sector_rotation_history(limit=limit)
    if command == "run":
        if not args:
            raise ValueError("run command requires TASK")
        result = run_task(args[0], args[1:])
        _append_task_history(result)
        return result
    if command == "theme-leaders":
        return _theme_leaders()
    if command == "get-discovery-candidates":
        return _discovery_merge()
    if command == "cron-list":
        return _scheduled_jobs()
    if command in {"cron-rerun", "cron-enable", "cron-disable"}:
        if not args:
            raise ValueError(f"{command} requires LABEL")
        return _cron_action(args[0], command.split("-", 1)[1])
    if command == "cron-catchup":
        return _cron_catchup()
    if command == "cron-rerun-many":
        labels = [s for s in (args[0].split(",") if args else []) if s]
        return _cron_rerun_many(labels)
    if command == "trends-month":
        if not args:
            raise ValueError("trends-month requires YYYY-MM")
        return _trends_month(args[0])
    if command == "trends-day":
        if not args:
            raise ValueError("trends-day requires YYYY-MM-DD")
        return _trends_day(args[0])
    raise ValueError(f"unknown command: {command}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: kss_app_bridge.py snapshot|stock SYMBOL|report PATH|paper-summary|run TASK"
            "|cron-list|cron-rerun LABEL|cron-enable LABEL|cron-disable LABEL"
            "|cron-catchup|cron-rerun-many LABEL,LABEL",
            file=sys.stderr,
        )
        return 2
    try:
        payload = dispatch(argv[1], argv[2:])
    except (ValueError, SystemExit) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _json_dump(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
