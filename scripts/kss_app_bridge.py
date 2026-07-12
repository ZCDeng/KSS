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
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _env_path(var: str) -> Path | None:
    v = os.environ.get(var)
    return Path(v).expanduser().resolve() if v else None


# 不可变代码根（脚本/config 所在）；bundle-mode 由 KSS_PROJECT_ROOT 指定。
PROJECT_ROOT = _env_path("KSS_PROJECT_ROOT") or Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# 可变状态根（storage/.cache）；默认回落 PROJECT_ROOT → 未设 env 时与历史行为逐字一致。
STATE_ROOT = _env_path("KSS_STATE_ROOT") or PROJECT_ROOT
REVIEW_DIR = STATE_ROOT / "storage" / "daily_review"
REPORT_DIR = STATE_ROOT / "storage" / "reports"
BJ_SCAN_DIR = REPORT_DIR / "bj50_scan"
BJ_CACHE_DIR = STATE_ROOT / "storage" / "bj_cache"
SUPPLY_CHAIN_PATH = PROJECT_ROOT / "kss" / "config" / "supply_chain.yaml"  # config = 代码，随 bundle
DATA_CATALOG_PATH = STATE_ROOT / "storage" / "data_catalog.json"  # 由 build_data_catalog.py 生成
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


def _sidecar_version_fingerprint(project_root: Path | None = None) -> str:
    """返回 sidecar 的代码版本指纹，供 Swift 端比对是否陈旧。

    策略：
    1. dev 模式优先用 git describe --always --dirty（短 hash + 标签），可感
       知代码改动，并与 scripts/sign_and_build.sh 给 CFBundleShortVersionString
       的写法一致。
    2. bundle 模式（无 .git 或 git 失败）fallback：scripts/VERSION 内容 + 关键
       文件内容 hash。这里的关键文件是 sidecar 和 bridge 本身，因为任何一方变
       都意味着 daemon 逻辑与 app 可能不同步。
    3. 极端 fallback 返回 "unknown"，绝不抛异常，避免 sidecar 因版本计算失败
       无法启动。

    Args:
        project_root: 用于计算版本指纹的代码根；默认取模块级 PROJECT_ROOT。
    """
    root = project_root or PROJECT_ROOT

    # 1) git describe（dev 模式）
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
            timeout=5.0,
        )
        git_ver = result.stdout.strip()
        if git_ver:
            return git_ver
    except Exception:  # noqa: BLE001
        pass

    # 2) bundle / fallback：VERSION 必须存在，否则无有效版本标记
    try:
        version_file = root / "scripts" / "VERSION"
        if not version_file.exists():
            return "unknown"
        hasher = hashlib.sha256()
        hasher.update(version_file.read_bytes())
        key_files = [
            Path(__file__),
            root / "scripts" / "kss_sidecar.py",
        ]
        for path in key_files:
            if path.exists():
                hasher.update(path.read_bytes())
        return f"bundle:{hasher.hexdigest()[:16]}"
    except Exception:  # noqa: BLE001
        pass

    return "unknown"


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
    """自选/龙头等展示名查询表。db_path 显式从本模块 STATE_ROOT 派生（同
    _indicator_watchlist_symbols 惯例）——测试靠 monkeypatch.setattr(b, "STATE_ROOT", ...)
    隔离，走隐式默认会绕过这层隔离（plan 2026-07-12-005 / U15 割接自 stock_names.csv）。"""
    from kss.storage.stock_names import load_stock_names  # noqa: PLC0415

    out: dict[str, dict[str, str]] = {}
    supply_names = _load_supply_chain_names()
    names_df = load_stock_names(db_path=STATE_ROOT / "storage" / "kss.db")
    for row in names_df.to_dict("records"):
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
    from kss.storage.paper_trade import read_latest_day

    return read_latest_day(STATE_ROOT / "storage" / "kss.db")


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


INDICATOR_LAB_REPORTS_DIR = REPORT_DIR / "indicator_lab"


def _report_entry(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.rstrip() for line in text.splitlines()]
    title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), path.stem)
    excerpt_lines = [line for line in lines if line.strip() and not line.startswith("#") and not line.startswith("|")]
    return {
        "title": title,
        "path": str(path.relative_to(STATE_ROOT)),
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "metrics": _report_metrics(text),
        "excerpt": "\n".join(excerpt_lines[:10]),
    }


def _backtest_reports() -> list[dict[str, Any]]:
    # 历史固定 8 条：不是目录扫描产物，原样保留（不因指标实验室泛化而改变现状行为）。
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
    out: list[dict[str, Any]] = [
        _report_entry(path) for path in candidates if path.exists()
    ]
    # 指标实验室固化产物：目录扫描（同 _reviews() 惯例），按 mtime 倒序追加、去重。
    seen_paths = {item["path"] for item in out}
    if INDICATOR_LAB_REPORTS_DIR.exists():
        indicator_reports = sorted(
            INDICATOR_LAB_REPORTS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for path in indicator_reports:
            entry = _report_entry(path)
            if entry["path"] in seen_paths:
                continue
            seen_paths.add(entry["path"])
            out.append(entry)
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
    from kss.storage.paper_trade import read_all_days

    entries = read_all_days(STATE_ROOT / "storage" / "kss.db")

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
    from kss.storage.db import connect, ensure_schema

    db_path = STATE_ROOT / "storage" / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT OR REPLACE INTO app_task_runs
            (task_id, started_at, title, finished_at, status, exit_code, summary, stdout, stderr, artifacts_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                result.get("taskId"), result.get("startedAt"), result.get("title"),
                result.get("finishedAt"), result.get("status", "unknown"), result.get("exitCode"),
                result.get("summary"), result.get("stdout"), result.get("stderr"),
                json.dumps(result.get("artifacts") or [], ensure_ascii=False),
            ),
        )


def _task_history(limit: int = 25) -> list[dict[str, Any]]:
    from kss.storage.db import connect, ensure_schema

    db_path = STATE_ROOT / "storage" / "kss.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """SELECT task_id, started_at, title, finished_at, status, exit_code, summary,
            stdout, stderr, artifacts_json FROM app_task_runs ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "taskId": row["task_id"],
            "startedAt": row["started_at"],
            "title": row["title"],
            "finishedAt": row["finished_at"],
            "status": row["status"],
            "exitCode": row["exit_code"],
            "summary": row["summary"],
            "stdout": row["stdout"],
            "stderr": row["stderr"],
            "artifacts": json.loads(row["artifacts_json"]) if row["artifacts_json"] else [],
        })
    return out


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
        # Longbridge（U6）：dev .env 回退，生产仍由 Swift Keychain 注入。
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
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


def _save_picks(date: str, picks: list[dict[str, Any]], force: bool) -> tuple[str, bool]:
    from kss.storage.paper_trade import day_exists, write_day

    db_path = STATE_ROOT / "storage" / "kss.db"
    if day_exists(date, db_path) and not force:
        return date, False
    payload = {
        "prediction_date": date,
        "generated_at": datetime.now().isoformat(),
        "strategy": "log_mv_reverse",
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
    write_day(payload, db_path)
    return date, True


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
            _, wrote = _save_picks(target_date, picks, force=force)
            artifacts.append("storage/kss.db")
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
    from kss.storage.etf_radar import read_all_ascending

    return read_all_ascending(STATE_ROOT / "storage" / "kss.db")


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
    target_date: str | None = None
    if isinstance(date_arg, str) and date_arg.strip():
        command += ["--date", date_arg.strip()]
        target_date = date_arg.strip()[:10]
    if args.get("no_execution"):
        command.append("--no-execution")
    # 正式日更默认 force-save：避免「stdout 说已保存、实际跳过」导致推荐卡在旧日
    # （cron / bridge 即使不带 --force 也会写盘；显式 --no-force 可关）
    if args.get("no_force"):
        pass
    else:
        command.append("--force-save")
    result = _run_process_task(
        "formal-daily-picks",
        "Formal Daily Picks",
        command,
        started,
        timeout=300,
    )
    # 落盘断言：当日 paper_trade_picks 必须存在
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    from kss.storage.paper_trade import day_exists

    exists = day_exists(target_date, STATE_ROOT / "storage" / "kss.db")
    if result.get("status") == "success" and not exists:
        result = dict(result)
        result["status"] = "failed"
        result["summary"] = f"选股进程 exit 0 但落库缺失: {target_date}"
        result["exitCode"] = 2
    elif result.get("status") == "success" and exists:
        result = dict(result)
        result["artifacts"] = list(result.get("artifacts") or []) + ["storage/kss.db"]
    return result


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
        artifacts=["storage/kss.db"],
        timeout=300,
    )


def _run_daily_review_symbol(args: dict[str, str | bool]) -> dict[str, Any]:
    """U4: 加自选即时单只(或多只)复盘。

    daily_review.py 内部已 `_ensure_history_for`(新股先全量回填) + 按股归档
    `{date}_{tscode}.md`，故并发加**不同**股写**不同**文件、互不竞争(per-symbol
    粒度已消解写竞争)；同股重复加幂等覆盖(内容一致)。`--channel console` 静音
    Telegram；timeout 放大到 600s(新股全量回填 + tushare 限流可能数十秒)。
    """
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("daily-review-symbol", "个股即时复盘", started)

    symbols_raw = args.get("symbols")
    if not isinstance(symbols_raw, str) or not symbols_raw.strip():
        return _task_result(
            "daily-review-symbol", "个股即时复盘", "failed",
            "daily-review-symbol 需要 --symbols (逗号分隔, 带交易所后缀)",
            started, exit_code=2,
        )
    # 强制带后缀: artifact 路径按 token 直拼, 缺后缀会与脚本(经 _infer_exchange 补全)
    # 实写的 {date}_{code}.{exch}.md 不匹配 → 主动拒绝, 而非被动产出 404 artifact。
    tokens = [t.strip().upper() for t in symbols_raw.split(",") if t.strip()]
    missing_suffix = [t for t in tokens if "." not in t]
    if missing_suffix:
        return _task_result(
            "daily-review-symbol", "个股即时复盘", "failed",
            f"--symbols 须带交易所后缀 (.SH/.SZ/.BJ): {', '.join(missing_suffix)}",
            started, exit_code=2,
        )

    date_arg = args.get("date")
    target = _normalize_script_date(str(date_arg)) if isinstance(date_arg, str) else None
    target_ymd = target or datetime.now().strftime("%Y%m%d")
    archive_date = f"{target_ymd[:4]}-{target_ymd[4:6]}-{target_ymd[6:8]}"

    # 非 dry-run: 总是覆盖归档(刷新), --channel console 不推 Telegram。
    command = [str(python), "scripts/daily_review.py", "--symbols", symbols_raw,
               "--channel", "console"]
    if target:
        command += ["--date", target]

    # 按股 artifacts: token 已校验带后缀, 与脚本实写文件名一致。
    artifacts = [f"storage/daily_review/{archive_date}_{tok}.md" for tok in tokens]
    return _run_process_task(
        "daily-review-symbol",
        "个股即时复盘",
        command,
        started,
        artifacts=artifacts,
        timeout=600,
    )


def _run_mi_signal_pack(args: dict[str, str | bool]) -> dict[str, Any]:
    """日终 MI Signal Pack：自选 walk-forward 后写 kss.db mi_signal_packs 表."""
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("mi-signal-pack", "MI Signal Pack", started)
    command = [str(python), "scripts/run_mi_signal_pack.py"]
    symbols_raw = args.get("symbols")
    if isinstance(symbols_raw, str) and symbols_raw.strip():
        command += ["--symbols", symbols_raw.strip()]
    return _run_process_task(
        "mi-signal-pack",
        "MI Signal Pack",
        command,
        started,
        artifacts=["storage/kss.db"],
        timeout=900,
    )


def _run_indicator_signal_pack(args: dict[str, str | bool]) -> dict[str, Any]:
    """日终指标 Signal Pack：注册表 active 基元库条目各自标的 walk-forward 后写 pack + 账本."""
    started = _now_iso()
    python = _full_python()
    if python is None:
        return _missing_full_env_result("indicator-signal-pack", "指标 Signal Pack", started)
    command = [str(python), "scripts/run_indicator_signal_pack.py"]
    asof_raw = args.get("asof")
    if isinstance(asof_raw, str) and asof_raw.strip():
        command += ["--asof", asof_raw.strip()]
    return _run_process_task(
        "indicator-signal-pack",
        "指标 Signal Pack",
        command,
        started,
        artifacts=["storage/kss.db"],
        timeout=900,
    )


def run_task(task_id: str, argv: list[str]) -> dict[str, Any]:
    args = _parse_args(argv)
    if task_id == "daily-review-symbol":
        return _run_daily_review_symbol(args)
    if task_id == "mi-signal-pack":
        return _run_mi_signal_pack(args)
    if task_id == "indicator-signal-pack":
        return _run_indicator_signal_pack(args)
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
    # 回退: 账本不可用时读 paper_trade_picks
    from kss.storage.paper_trade import read_all_days

    out: list[dict[str, Any]] = []
    for log in reversed(read_all_days(STATE_ROOT / "storage" / "kss.db")):
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

    数据源 kss.db etf_radar_snapshots；同表 etf_radar_commentary_index 记投顾点评
    （.commentary.md，仍是文件，含 概念轮动 / 七大主题 / 加减仓建议 等段落）路径。
    板块复盘与个股复盘一样每日一篇，返回列表供复盘页按日期浏览；总览板块信息图取
    首项（最新一天）。
    """
    from kss.storage.etf_radar import read_commentary_path, read_history

    out: list[dict[str, Any]] = []
    for d in read_history(limit, STATE_ROOT / "storage" / "kss.db"):
        pulse = _pulse_from_dict(d)
        if not pulse:
            continue
        trade_date = str(d.get("trade_date") or "")
        rel_path = read_commentary_path(trade_date, STATE_ROOT / "storage" / "kss.db")
        commentary_path = (STATE_ROOT / rel_path) if rel_path else None
        if commentary_path is not None and commentary_path.exists():
            try:
                pulse["commentary"] = _commentary_to_md(commentary_path.read_text(encoding="utf-8"))
            except Exception:
                pulse["commentary"] = None
        else:
            pulse["commentary"] = None
        out.append(pulse)
    return out


def _news_digest(date: str = "", scene: str = "") -> dict[str, Any]:
    """舆情热点 digest:读 kss.db 归档的结构化数据,供 UI 两段式渲染(plan U11)。

    kss.db news_digest_entries 表由 run_news_digest.py 写出。
    无参 → 取最新一份;指定 date/scene → 取该份。返回:
      ``{available, selected: <digest|None>, index: [{date,scene}...]}``
    index 新到旧,供面板切换场次/历史。读舆情面板不在此实时生成(避免阻塞 UI)。
    """
    from kss.storage.news_digest import list_index, read_entry

    db_path = STATE_ROOT / "storage" / "kss.db"
    keys = list_index(db_path)
    index = [{"date": k.digest_date, "scene": k.scene} for k in keys]

    target: tuple[str, str] | None = None
    if date and scene:
        target = (date, scene)
    elif keys:
        target = (keys[0].digest_date, keys[0].scene)

    selected = read_entry(target[0], target[1], db_path) if target else None
    return {"available": selected is not None, "selected": selected, "index": index}


def _intel_radar(force: str = "") -> dict[str, Any]:
    """12赛道全球RSS资讯雷达。``force == "force"`` 时实时抓取（≈20-40s），否则读缓存。

    返回格式::
      {available, generated_at, recent_days, stats, tracks: [{key, name, accent, total, items}]}
    tracks 对齐 Swift ``IntelTrack``，items 对齐 ``IntelItem``。
    """
    from kss.news.radar import get_radar

    do_fetch = (force == "force")
    data = get_radar(force=do_fetch)

    industries = data.get("industries") or []
    tracks = []
    for ind in industries:
        items = []
        for it in ind.get("items") or []:
            items.append({
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "time": it.get("time", ""),
                "source": it.get("source", ""),
                "summary": it.get("summary", ""),
            })
        tracks.append({
            "key": ind["key"],
            "name": ind["name"],
            "accent": ind.get("accent"),
            "total": ind.get("total", len(items)),
            "items": items,
        })

    available = data.get("generated_at") is not None
    return {
        "available": available,
        "index": [],
        "selected": None,
        "tracks": tracks,
        "generated_at": data.get("generated_at"),
        "recent_days": data.get("recent_days"),
        "stats": data.get("stats"),
    }


def _intel_digest(json_payload: str = "") -> dict[str, Any]:
    """资讯雷达单赛道 AI 要点提炼（plan 2026-07-09-001 + 2026-07-10 pool mode）。

    参数：JSON 单参数 ``{"track_key": ..., "track_name": ..., "items": [...], "force": bool?}``
    返回：``{text, model, generated_at, prompt, item_count, mode, error?, error_type?}``

    Pool-first: when rewrite pool has ≥ threshold ready drafts for today, return
    aggregate and **ignore** list notes cache (R9/KTD5). Else existing list digest.

    不写沉淀库——由 UI 的「存入沉淀」按钮调 ``_intel_digest_save`` 触发。
    """
    import json as _json

    from kss.news.digest_ai import run_digest
    from kss.news.rewrite import aggregate_track_digest, pool_ready_count
    from kss.news.rewrite_config import POOL_THRESHOLD

    if not json_payload:
        return {"error": "empty payload", "error_type": "client", "text": "", "mode": "list"}
    try:
        obj = _json.loads(json_payload)
    except Exception as exc:
        return {"error": f"invalid JSON: {exc}", "error_type": "client", "text": "", "mode": "list"}

    track_key = str(obj.get("track_key") or "")
    track_name = str(obj.get("track_name") or track_key)
    items = obj.get("items") or []
    force = bool(obj.get("force") or False)
    if not track_key:
        return {"error": "missing track_key", "error_type": "client", "text": "", "mode": "list"}
    if not isinstance(items, list):
        return {"error": "items must be a JSON array", "error_type": "client", "text": "", "mode": "list"}

    # Pool-before-list-cache (plan 2026-07-10 KTD5)
    prefer_list = bool(obj.get("prefer_list") or False)
    if not prefer_list and pool_ready_count(track_key) >= POOL_THRESHOLD:
        agg = aggregate_track_digest(track_key)
        if agg.get("mode") == "pool" and (agg.get("text") or "").strip():
            return {
                "text": agg["text"],
                "model": "rewrite-pool",
                "generated_at": agg.get("generated_at"),
                "prompt": None,
                "item_count": agg.get("count"),
                "mode": "pool",
                "draft_ids": agg.get("draft_ids"),
                "from_cache": False,
            }

    try:
        result = run_digest(track_key, track_name, items, force=force)
    except Exception as exc:  # noqa: BLE001 - 防御性收口
        return {"error": f"digest failed: {exc}", "error_type": "server", "text": "", "mode": "list"}
    if isinstance(result, dict):
        result.setdefault("mode", "list")
    return result


def _intel_panorama(json_payload: str = "") -> dict[str, Any]:
    """12 赛道全景热点（独立 LLM）。

    参数：``{"tracks": [{"key","name","titles":[...]}, ...]}``
    """
    import json as _json

    from kss.news.digest_ai import run_panorama

    if not json_payload:
        return {"text": "", "error": "empty payload", "error_type": "client"}
    try:
        obj = _json.loads(json_payload)
    except Exception as exc:
        return {"text": "", "error": f"invalid JSON: {exc}", "error_type": "client"}
    tracks = obj.get("tracks") if isinstance(obj, dict) else None
    if not isinstance(tracks, list):
        return {"text": "", "error": "tracks must be a JSON array", "error_type": "client"}
    try:
        return run_panorama(tracks)
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "error": f"panorama failed: {exc}", "error_type": "server"}


def _intel_digest_save(json_payload: str = "") -> dict[str, Any]:
    """把已生成的 AI digest 写入沉淀库（md+json）。

    参数：``{"track_key": ..., "track_name": ..., "prompt": ..., "response": ..., "model": ..., "items": [...]}``
    返回：``{saved_path, ok}`` 或 ``{error, error_type}``
    """
    import json as _json

    from kss.storage.notes import save_intel_digest

    if not json_payload:
        return {"ok": False, "error": "empty payload", "error_type": "client"}
    try:
        obj = _json.loads(json_payload)
    except Exception as exc:
        return {"ok": False, "error": f"invalid JSON: {exc}", "error_type": "client"}

    track_key = str(obj.get("track_key") or "")
    track_name = str(obj.get("track_name") or track_key)
    prompt = str(obj.get("prompt") or "")
    response = str(obj.get("response") or "")
    model = str(obj.get("model") or "")
    items = obj.get("items") or []

    if not (track_key and response):
        return {"ok": False, "error": "missing track_key or response", "error_type": "client"}

    try:
        md_path = save_intel_digest(
            track_key, track_name, prompt, response, model, items,
        )
        return {"ok": True, "saved_path": str(md_path)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"save failed: {exc}", "error_type": "server"}


def _intel_article(json_payload: str = "") -> dict[str, Any]:
    """Best-effort article body fetch (plan 2026-07-10-001 U4)."""
    import json as _json

    from kss.news.article_fetch import body_or_summary, fetch_article

    if not json_payload:
        return {"mode": "empty", "body": "", "error": "empty payload", "error_type": "client"}
    try:
        obj = _json.loads(json_payload)
    except Exception as exc:
        return {"mode": "empty", "body": "", "error": f"invalid JSON: {exc}", "error_type": "client"}

    url = str(obj.get("url") or "")
    summary = str(obj.get("summary") or "")
    if summary or not url:
        return body_or_summary(url=url, summary=summary)
    return fetch_article(url)


def _intel_rewrite(json_payload: str = "") -> dict[str, Any]:
    """On-demand rewrite for one item.

    JSON: ``{track_key, track_name, item, force?, kind?}``
    kind: ``investment`` (default) | ``chinese`` (qmreader 风中文改写)
    """
    import json as _json

    from kss.news.rewrite import run_rewrite

    if not json_payload:
        return {"status": "failed", "error": "empty payload", "error_type": "client"}
    try:
        obj = _json.loads(json_payload)
    except Exception as exc:
        return {"status": "failed", "error": f"invalid JSON: {exc}", "error_type": "client"}

    track_key = str(obj.get("track_key") or "")
    track_name = str(obj.get("track_name") or track_key)
    item = obj.get("item") or {}
    force = bool(obj.get("force") or False)
    kind = str(obj.get("kind") or "investment")
    if not track_key or not isinstance(item, dict):
        return {"status": "failed", "error": "missing track_key or item", "error_type": "client"}
    try:
        return run_rewrite(
            track_key, track_name, item, force=force, fetch_body=True, kind=kind
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"rewrite failed: {exc}", "error_type": "server"}


def _intel_rewrite_run(json_payload: str = "") -> dict[str, Any]:
    """Kick Top-K rewrite worker (U4). Optional JSON ``{k?, force?}``."""
    import json as _json

    from kss.news.rewrite_worker import run_top_k_rewrites

    k = None
    force = False
    if json_payload:
        try:
            obj = _json.loads(json_payload)
            if obj.get("k") is not None:
                k = int(obj["k"])
            force = bool(obj.get("force") or False)
        except Exception as exc:
            return {"error": f"invalid JSON: {exc}", "error_type": "client"}
    try:
        return run_top_k_rewrites(k=k, force=force)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"worker failed: {exc}", "error_type": "server"}


def _sector_rotation_history(limit: int = 30) -> list[dict[str, Any]]:
    """板块热点轮动归档列表：最新 N 个交易日，新到旧。

    仅返回用于日期列表的轻量字段（tradeDate、leaderCoverage、
    crossSourceSignals 计数），避免把全部 leader 矩阵塞进快照。
    """
    from kss.storage.sector_rotation import read_history

    snaps = read_history(limit, STATE_ROOT / "storage" / "kss.db")
    out: list[dict[str, Any]] = []
    for snap in snaps:
        signals = snap.get("crossSourceSignals") or {}
        out.append({
            "tradeDate": snap.get("tradeDate"),
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
    from kss.storage.sector_rotation import read_latest

    snap = read_latest(STATE_ROOT / "storage" / "kss.db")
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


def _perilla_picks(top_n: int = 20, min_score: float = 0.4) -> list[dict[str, Any]]:
    """紫苏叶（供应链护城河）选股：按 perilla_score 排序的注册表标的。

    数据源 = kss/config/supply_chain.yaml（curated）+ ChainRegistry 评分。
    每条返回结构化列字段，供前端表格直接渲染（不依赖展示字符串解析）。

    精不是多：仅返回结构性达标的 ``core``（核心垄断/双寡头）与 ``main``
    （国产替代主线·三家寡头深链）两层；moat 不足靠分项补分挤进分数线的
    ``watch`` 票不入表。每条带 ``tier`` 字段供前端分 Tab 渲染。
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
    for code, score in candidates:
        info = reg.get(code)
        if info is None:
            continue
        tier = reg.tier(code)
        if tier not in ("core", "main"):
            continue
        if len(picks) >= top_n:
            break
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

        # 机构持仓 + 对标美股 PE：只读富化缓存（cron/详情页已预热），不触网，缺则降级。
        inst_holding, peer_pe, inst_ratio = _perilla_pick_enrich(code, reg)

        picks.append({
            "symbol": code,
            "name": info.name or code,
            "chains": " / ".join(info.demand_chains),
            "layer": int(info.chain_layer),
            "role": info.chain_role or "",
            "moat": moat,
            "locked": bool(info.demand_locked),
            "tier": tier,
            "score": round(float(score), 3),
            "ret1d": metrics.get("ret1d"),
            "ret5d": metrics.get("ret5d"),
            "ret20d": metrics.get("ret20d"),
            "retYear": metrics.get("retYear"),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) else None,
            "pb": round(pb, 2) if isinstance(pb, (int, float)) else None,
            "circMvYi": round(mv_wan / 10000.0, 1) if isinstance(mv_wan, (int, float)) else None,
            "mvIsFloat": circ_mv_wan is not None,
            "instHolding": inst_holding,             # 机构持仓动态(机构占比+增减+北向)；缓存未命中=""
            "instRatio": round(inst_ratio, 1) if isinstance(inst_ratio, (int, float)) else None,
            "usPeerTicker": info.us_peer_ticker or "",
            "usPeerName": info.us_peer_name or "",
            "usPeerPe": round(peer_pe, 1) if isinstance(peer_pe, (int, float)) else None,
        })
    return picks


def _perilla_pick_enrich(code: str, reg: Any) -> tuple[str, float | None, float | None]:
    """读富化缓存(cache_only, 不触网)→ (机构持仓动态串, 对标美股PE, 机构占比%)。

    缓存未命中(cron 未跑/未开过该股详情)→ ("", None, None)，前端显示「—」。
    """
    try:
        from kss.perilla_enrich import aggregate
        e = aggregate.enrich(code, registry=reg, db_path=STATE_ROOT / "storage" / "kss.db", cache_only=True)
    except Exception:
        return "", None, None

    inst = e.get("institutional", {}) if isinstance(e, dict) else {}
    top = inst.get("top10", {}) if isinstance(inst, dict) else {}
    nb = inst.get("northbound", {}) if isinstance(inst, dict) else {}
    inst_ratio = None
    parts: list[str] = []
    if isinstance(top, dict) and top.get("status") == "ok":
        if top.get("inst_ratio") is not None:
            inst_ratio = float(top["inst_ratio"])
            parts.append(f"机构{inst_ratio:.1f}%")
        zh = {"increasing": "增持", "decreasing": "减持", "flat": "中性"}.get(top.get("net_direction"))
        if zh:
            parts.append(zh)
    if isinstance(nb, dict) and nb.get("status") == "ok" and nb.get("hold_ratio") is not None:
        arrow = {"increasing": "↑", "decreasing": "↓", "flat": "→"}.get(nb.get("direction"), "")
        parts.append(f"北向{float(nb['hold_ratio']):.1f}%{arrow}")
    inst_holding = " · ".join(parts)

    up = e.get("us_peer", {}) if isinstance(e, dict) else {}
    peer_pe = up.get("peer_pe") if isinstance(up, dict) and up.get("status") == "ok" else None
    return inst_holding, peer_pe, inst_ratio


def _perilla_enrich(symbol: str) -> dict[str, Any]:
    """紫苏叶个股富化（机构持仓动态 / PE 分位 / 美股对标）。

    数据源 = Tushare(机构+PE) + yFinance(美股对标)，每块独立优雅降级。
    见 ``kss/perilla_enrich/aggregate.py``。本封装只负责接线 + 缓存目录。
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return {"status": "invalid_symbol"}
    try:
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from kss.perilla_enrich import aggregate
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "status": "unavailable", "reason": str(exc)[:120]}
    try:
        return aggregate.enrich(symbol, db_path=STATE_ROOT / "storage" / "kss.db")
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "status": "unavailable", "reason": str(exc)[:120]}


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
    from kss.storage.sector_rotation import read_latest

    snap = read_latest(STATE_ROOT / "storage" / "kss.db")
    if snap is None:
        return None
    date = str(snap.get("tradeDate"))
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
    mi_signal = None
    mi_overlay = None
    try:
        from kss.strategies.mi_pack import read_pack, to_mi_overlay, to_mi_signal

        pack = read_pack(symbol)
        if pack is not None:
            mi_signal = to_mi_signal(pack)
            # 标注时间必须落在 history 窗口内，否则 LWC setMarkers 静默丢点
            hist_dates = {str(h.get("date") or "") for h in history}
            mi_overlay = to_mi_overlay(pack, history_dates=hist_dates)
    except Exception as exc:  # noqa: BLE001
        print(f"mi pack for {symbol}: {exc}", file=sys.stderr)

    hist_dates = {str(h.get("date") or "") for h in history}
    indicator_signals, indicator_overlays = _indicator_detail_projections(symbol, hist_dates)

    return {
        "symbol": symbol,
        "name": meta.get("name", ""),
        "industry": meta.get("industry", ""),
        "concept": meta.get("concept", ""),
        "latest": latest,
        "history": history,
        "reviewConclusion": _stock_review(symbol),
        "miSignal": mi_signal,
        "miOverlay": mi_overlay,
        # additive（plan 2026-07-12-004 U6）：注册表内任意 active 指标，含 MI 自己的一份，
        # 与上面两个 mi* 字段并存不冲突，不 bump BRIDGE_SCHEMA_VERSION。
        "indicatorSignals": indicator_signals,
        "indicatorOverlays": indicator_overlays,
    }


def _indicator_detail_projections(
    symbol: str, hist_dates: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """遍历指标注册表 active 条目，产出通用 indicatorSignals/indicatorOverlays 数组.

    单条目失败不拖垮其它条目或整个 stock_detail（同 mi pack try/except 先例）。
    """
    try:
        from kss.indicators import pack as ipack
        from kss.indicators.registry import active_entries
    except Exception as exc:  # noqa: BLE001
        print(f"indicator registry for {symbol}: {exc}", file=sys.stderr)
        return [], []

    signals: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    for entry in active_entries():
        try:
            symbol_pack = ipack.read_any_pack(entry, symbol)
            if symbol_pack is None:
                continue
            sig = ipack.to_any_signal(entry, symbol_pack)
            ov = ipack.to_any_overlay(entry, symbol_pack, history_dates=hist_dates)
            if sig is not None:
                sig = dict(sig)
                sig.setdefault("indicatorId", entry.id)
                signals.append(sig)
            if ov is not None:
                ov = dict(ov)
                ov["indicatorId"] = entry.id
                overlays.append(ov)
        except Exception as exc:  # noqa: BLE001
            print(f"indicator pack {entry.id} for {symbol}: {exc}", file=sys.stderr)
            continue
    return signals, overlays


# ---------------------------------------------------------------------------
# 定时任务（launchd）自省与操作
#
# 真正的定时任务是 deploy/launchd/com.zcdeng.kss.*.plist（crontab 已是 legacy）。
# 这里只用标准库 plistlib + 系统 launchctl 读状态、重跑、启停，绝不改 plist 内容。
# 所有 launchctl 调用都走列表参数 + label 白名单（白名单由 plist 文件名派生），
# 不拼接用户输入，杜绝注入。
# ---------------------------------------------------------------------------

def _launchd_deploy_dir() -> Path:
    """deploy/launchd 双根解析（plan 2026-07-12-005 / U6 KTD2, U11 R17）。

    dev 模式（STATE_ROOT == PROJECT_ROOT）：原样用 PROJECT_ROOT 副本，行为不变。
    bundle 模式（STATE_ROOT != PROJECT_ROOT）：改用 STATE_ROOT 副本——签名 .app 内的
    PROJECT_ROOT/deploy/launchd 只读，写入即破坏签名。首次访问**重渲染**（不是原样
    拷贝 bundle 模板）——bundle 内模板的 HOME/日志路径烙的是作者本机值（U11 修复前
    的已知缺口），重渲染让 HOME 取交付对象自己的 Path.home()、日志路径落交付对象的
    STATE_ROOT，而非承袭作者机器的值。渲染失败（清单/wrapper 异常，理论不该发生）
    兜底退回原样拷贝，保证首启不因这一步彻底瘫痪。
    此后 STATE_ROOT 副本是唯一可写工作态，排期编辑（cron-edit-schedule）只回写这里。
    """
    bundle_deploy = PROJECT_ROOT / "deploy" / "launchd"
    if STATE_ROOT == PROJECT_ROOT:
        return bundle_deploy
    state_deploy = STATE_ROOT / "deploy" / "launchd"
    if not state_deploy.is_dir() and bundle_deploy.is_dir():
        state_deploy.mkdir(parents=True, exist_ok=True)
        try:
            import render_launchd_plists as _render_mod  # noqa: PLC0415

            _render_mod.render_all(str(PROJECT_ROOT), state_deploy, state_root=str(STATE_ROOT))
        except Exception as exc:  # noqa: BLE001 - 首启不能因这步崩，退回旧行为
            print(f"[launchd] 重渲染 deploy/launchd 失败，退回原样拷贝: {exc}", file=sys.stderr)
            for src in bundle_deploy.glob("com.zcdeng.kss.*.plist"):
                shutil.copy2(src, state_deploy / src.name)
    return state_deploy


LAUNCHD_DIR = _launchd_deploy_dir()
# 已装副本目录（~/Library/LaunchAgents）—— 仅作「已装态」对账：loaded/running/enabled
# 与 needsInstall 漂移判定。清单是任务枚举的唯一真源（U4 / R4）。
LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

# 任务元数据（标题/分类/排序/补跑资格）的唯一真源 = kss/config/cron_jobs.yaml，
# 经 kss.config.cron_manifest 加载。曾在此硬编码的 LABEL_TITLES / LABEL_CATEGORY /
# CATEGORY_ORDER / NO_CATCHUP_LABELS 已删（plan 2026-06-23-001 / U4）：
#   title  ← cron_manifest.title_for(suffix)        （缺失回退 suffix）
#   分类   ← cron_manifest.category_for(suffix)      （缺失回退「其他」）
#   排序   ← cron_manifest.category_order()
#   补跑   ← cron_manifest.catchup_eligible(suffix)  （catchup:false 不补跑，如 collect_intraday）


def _cron_manifest():
    """惰性导入清单 API（确保 PROJECT_ROOT 在 sys.path，避免 import-time 副作用）。"""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from kss.config import cron_manifest  # noqa: PLC0415
    return cron_manifest


_WEEKDAY_CN = {0: "日", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}


def _launchd_plists() -> dict[str, Path]:
    """label → plist 路径。

    优先 ``deploy/launchd``（仓库/已拷入 bundle 的模板）。
    若目录为空（旧签名包未带 deploy/）：回退已装的 ``~/Library/LaunchAgents``，
    避免 App 内「重跑」全部变成 unknown label。
    """
    out: dict[str, Path] = {}
    if LAUNCHD_DIR.is_dir():
        for path in sorted(LAUNCHD_DIR.glob("com.zcdeng.kss.*.plist")):
            out[path.stem] = path
    if not out and LAUNCHAGENTS_DIR.is_dir():
        for path in sorted(LAUNCHAGENTS_DIR.glob("com.zcdeng.kss.*.plist")):
            out[path.stem] = path
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


def _schedule_struct(interval: Any) -> dict[str, Any]:
    """StartCalendarInterval → 结构化字段（U6），供设置页排期编辑器初始化表单。
    人读串 `schedule` 给展示用，这个给编辑器读初值——避免解析人读文案的脆弱往返。"""
    entries = _interval_entries(interval)
    if not entries:
        return {"hour": 0, "minute": 0, "weekdays": None, "weekly": False, "weekday": None}
    if len(entries) == 1 and "Weekday" in entries[0]:
        e = entries[0]
        return {"hour": int(e.get("Hour", 0)), "minute": int(e.get("Minute", 0)),
                "weekdays": None, "weekly": True, "weekday": int(e["Weekday"])}
    times = {_hm(e) for e in entries}
    weekdays_set = {int(e["Weekday"]) for e in entries if "Weekday" in e}
    if len(times) == 1:
        hour, minute = (int(x) for x in next(iter(times)).split(":"))
        weekdays = sorted(weekdays_set) if weekdays_set else None
        return {"hour": hour, "minute": minute, "weekdays": weekdays, "weekly": False, "weekday": None}
    # 混合形态（各 entry 时刻不同）——排期编辑器不支持这类任务，回退首条近似值，
    # 保存时会按该近似值覆盖成单一时刻（用户在编辑器里能看到、可另行调整）。
    e = entries[0]
    return {"hour": int(e.get("Hour", 0)), "minute": int(e.get("Minute", 0)),
            "weekdays": None, "weekly": False, "weekday": None}


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


def _scheduled_job(
    label: str,
    path: Path,
    uid: int,
    disabled: set[str],
    *,
    needs_install: bool = False,
) -> dict[str, Any]:
    import plistlib

    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, ValueError):
        pl = {}

    suffix = label.replace("com.zcdeng.kss.", "")
    cm = _cron_manifest()
    interval = pl.get("StartCalendarInterval")
    prog = pl.get("ProgramArguments") or []
    script = Path(prog[0]).name if prog else ""
    schedule = _parse_schedule(interval)
    status = _launchctl_status(label, uid)
    # StandardOutPath 取自 plist —— installed plist 优先（U2 日志改名后随之生效）。
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
        "title": cm.title_for(suffix),
        "category": cm.category_for(suffix),
        "schedule": schedule,
        "scheduleStruct": _schedule_struct(interval),
        "script": script,
        "enabled": enabled,
        "loaded": status["loaded"],
        "running": status["pid"] is not None,
        # 清单有而 ~/Library/LaunchAgents 未装 → 任务页显式告警「需同步」，绝不静默漏（R4）。
        "needsInstall": needs_install,
        "lastStatus": last_status,
        "lastRunAt": last["at"],
        "lastLine": last["line"],
        "stale": stale,
        "missedCycles": missed,
        "expectedAt": expected.strftime("%Y-%m-%d %H:%M") if expected else None,
        "nextRunAt": nxt.strftime("%Y-%m-%d %H:%M") if nxt else None,
    }


def _installed_plists() -> dict[str, Path]:
    """label → 已装 plist 路径（~/Library/LaunchAgents）。仅作已装态对账，非枚举真源。"""
    out: dict[str, Path] = {}
    for path in sorted(LAUNCHAGENTS_DIR.glob("com.zcdeng.kss.*.plist")):
        out[path.stem] = path
    return out


def _scheduled_jobs() -> list[dict[str, Any]]:
    """枚举源 = 清单 enabled 任务（R4）。glob/launchctl 仅作已装态对账：
    清单有而 ~/Library/LaunchAgents 未装的任务 needsInstall=True，显式列出不静默漏。
    每任务的 schedule/StandardOutPath 从「已装 plist 优先、否则 deploy/launchd 模板」的 plist 读，
    使 U2 日志改名 apply 后随已装 plist 生效（DELIVER#4）。"""
    uid = os.getuid()
    disabled = _disabled_labels(uid)
    installed = _installed_plists()
    deploy = _launchd_plists()
    cm = _cron_manifest()

    jobs: list[dict[str, Any]] = []
    for job in cm.all_jobs():
        if not job.enabled:
            continue
        label = job.label
        installed_path = installed.get(label)
        needs_install = installed_path is None
        # plist 读源：已装优先（含 U2 改名后的日志路径）；未装回退 deploy 模板。
        plist_path = installed_path or deploy.get(label)
        if plist_path is None:
            # 清单有、deploy 与 LaunchAgents 皆无 —— 仍登记，标 needsInstall，绝不漏。
            plist_path = LAUNCHD_DIR / f"{label}.plist"
        jobs.append(
            _scheduled_job(label, plist_path, uid, disabled, needs_install=needs_install)
        )
    return jobs


def _cron_sync() -> dict[str, Any]:
    """重算 launchd 对账并落地到 ~/Library/LaunchAgents（无 prune）。

    返回同步结果与刷新后的任务清单，供前端在单次调用后重建状态。
    """
    try:
        from sync_launchd import run_sync
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"failed to load sync launcher: {exc}"}

    try:
        plan, notices = run_sync(
            project_root=str(PROJECT_ROOT),
            agents_dir=LAUNCHAGENTS_DIR,
            deploy_dir=LAUNCHD_DIR,
            apply=True,
            prune=False,
            acknowledge_schedule_change=False,
            state_root=str(STATE_ROOT),
            manifest_path=str(PROJECT_ROOT / "kss" / "config" / "cron_jobs.yaml"),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "notices": [str(exc)]}

    response: dict[str, Any] = {
        "ok": True,
        "notices": notices,
        "categoryOrder": list(_cron_manifest().category_order()),
        "jobs": _scheduled_jobs(),
        "plan": {
            "install": list(plan.install),
            "update": list(plan.update),
            "stale": list(plan.stale),
            "aligned": list(plan.aligned),
        },
    }

    if not notices:
        response["notices"] = [
            f"无需动作：install={len(plan.install)}, update={len(plan.update)}, stale={len(plan.stale)}"
        ]
    return response


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


def _cron_edit_schedule(suffix: str, schedule_json: str) -> dict[str, Any]:
    """应用内排期编辑（R8/KTD2，plan 2026-07-12-005/U6）：写 overlay → 渲染单 plist
    （持久安装位 + deploy 副本双写）→ bootout/bootstrap。渲染或 launchctl 任一步
    失败即回滚 overlay 条目，不留半成品状态——面板保持原排期，用户可重试。"""
    import yaml  # noqa: PLC0415

    try:
        schedule_raw = json.loads(schedule_json)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": "bad_schedule_json", "hint": str(exc)}
    if not isinstance(schedule_raw, dict):
        return {"ok": False, "error": "bad_schedule_json", "hint": "schedule 须为 JSON 对象"}

    cm = _cron_manifest()
    try:
        current = cm.load_manifest()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "manifest_load_failed", "hint": str(exc)}
    if current.job(suffix) is None:
        return {"ok": False, "error": "unknown_suffix", "hint": f"未知任务: {suffix!r}"}

    overlay_path = cm.OVERLAY_PATH
    existing_overlay = cm._load_overlay(overlay_path)
    merged_overlay = dict(existing_overlay)
    merged_overlay[suffix] = schedule_raw

    try:
        new_manifest = cm._apply_overlay(current, merged_overlay)
    except Exception as exc:  # noqa: BLE001 - CronManifestError 等校验失败
        return {"ok": False, "error": "invalid_schedule", "hint": str(exc)}
    new_job = new_manifest.job(suffix)

    # 写盘前记住旧 overlay 文本，失败时原样恢复（不留半成品）。
    old_overlay_text = overlay_path.read_text(encoding="utf-8") if overlay_path.is_file() else None

    def _write_overlay(text: str | None) -> None:
        if text is None:
            overlay_path.unlink(missing_ok=True)
            return
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = overlay_path.with_suffix(overlay_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(overlay_path)

    _write_overlay(yaml.safe_dump(merged_overlay, allow_unicode=True))

    label = new_job.label
    try:
        import render_launchd_plists as render_mod  # noqa: PLC0415

        agents_path = LAUNCHAGENTS_DIR / f"{label}.plist"
        deploy_path = LAUNCHD_DIR / f"{label}.plist"
        state_root_arg = str(STATE_ROOT) if STATE_ROOT != PROJECT_ROOT else None
        # 持久安装位（~/Library/LaunchAgents）：launchd 重启后据此自动重载。
        pl = render_mod.render(str(PROJECT_ROOT), new_job, agents_path, state_root=state_root_arg)
        # deploy 副本：_launchd_plists() 枚举/bootstrap 的优先来源，双写保持一致。
        LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)
        deploy_tmp = deploy_path.with_suffix(deploy_path.suffix + ".tmp")
        deploy_tmp.write_bytes(render_mod._to_xml(pl))
        deploy_tmp.replace(deploy_path)
    except Exception as exc:  # noqa: BLE001
        _write_overlay(old_overlay_text)
        return {"ok": False, "error": "render_failed", "hint": str(exc)}

    uid = os.getuid()
    domain = f"gui/{uid}"
    _run_launchctl(["bootout", f"{domain}/{label}"])
    rc, _, err = _run_launchctl(["bootstrap", domain, str(agents_path)])
    if rc != 0 and "already" not in err.lower():
        _write_overlay(old_overlay_text)
        return {"ok": False, "error": "launchctl_failed", "hint": err.strip() or f"bootstrap rc={rc}"}
    _run_launchctl(["enable", f"{domain}/{label}"])

    disabled = _disabled_labels(uid)
    job_payload = _scheduled_job(label, agents_path, uid, disabled)
    return {"ok": True, "job": job_payload}


# ---------------------------------------------------------------------------
# 日志查看（plan 2026-07-12-005 / U7 KTD10）——设置页日志分区。只读命令，
# 路径白名单锁定 STATE_ROOT/storage/logs/ 内（同 _resolve_markdown_path 护栏思路）。
# ---------------------------------------------------------------------------

LOGS_DIR = STATE_ROOT / "storage" / "logs"


def _log_list() -> dict[str, Any]:
    """枚举 storage/logs/ 下全部日志文件，含 sidecar 轮转代（.log.1/.2/.3）与
    cron 各任务日志（cron/ 子目录）。轮转代与当前文件一并可见，AE5 不因轮转丢线索。"""
    entries: list[dict[str, Any]] = []
    if LOGS_DIR.is_dir():
        for p in sorted(LOGS_DIR.rglob("*.log*")):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append({
                "name": str(p.relative_to(LOGS_DIR)),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return {"logs": entries}


def _resolve_log_path(name: str) -> Path:
    """name → 校验后绝对路径；越界/非法一律 SystemExit（护栏，同 report 路径思路）。"""
    if not name or "\x00" in name:
        raise SystemExit("invalid log name")
    candidate = (LOGS_DIR / name).resolve()
    try:
        candidate.relative_to(LOGS_DIR.resolve())
    except ValueError as exc:
        raise SystemExit(f"log path escapes storage/logs/: {name!r}") from exc
    return candidate


def _log_tail(name: str, lines: int = 500, grep: str = "") -> dict[str, Any]:
    """尾部读取 + 可选关键词过滤（只读，R9/AE5）。lines 上限 2000 防超大响应。
    grep 在 bridge 侧做，避免整份大文件过桥到 Swift 再本地过滤。"""
    path = _resolve_log_path(name)
    if not path.is_file():
        return {"name": name, "error": "not_found", "lines": [], "totalMatched": 0}
    capped_lines = max(1, min(lines, 2000))
    try:
        all_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        return {"name": name, "error": str(exc), "lines": [], "totalMatched": 0}
    if grep:
        all_lines = [ln for ln in all_lines if grep in ln]
    tail = all_lines[-capped_lines:]
    return {"name": name, "lines": tail, "totalMatched": len(all_lines)}


# ---------------------------------------------------------------------------
# 启动自检（plan 2026-07-12-005 / U8 KTD4）——纯 stdlib、目标 <2s。
# 失败分级：fail＝功能不可用（venv 损坏/目录不可写），warn＝功能受限（凭证未配）。
# sidecar 本身不可达是第三种 fail（本函数跑得起来即说明 sidecar 已可达），那一项
# 由 Swift 侧在 bridge 调用失败时兜底合成，不在这里出现。
# ---------------------------------------------------------------------------

def _check_venv() -> dict[str, Any]:
    """运行时探针：子进程里真跑一次 `import pandas`，超时/失败即 fail（KTD4）。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import pandas"],
            capture_output=True, timeout=5, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"item": "venv", "status": "fail", "detail": "运行时探针超时(5s)",
                 "fixHint": "重新初始化运行时", "fixAction": "reinit_runtime"}
    except OSError as exc:
        return {"item": "venv", "status": "fail", "detail": f"运行时不可用: {exc}",
                 "fixHint": "重新初始化运行时", "fixAction": "reinit_runtime"}
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "ignore")[:200]
        return {"item": "venv", "status": "fail", "detail": f"依赖不可用: {stderr}",
                 "fixHint": "重新初始化运行时", "fixAction": "reinit_runtime"}
    return {"item": "venv", "status": "ok", "detail": f"运行时正常（{sys.executable}）",
             "fixHint": None, "fixAction": None}


def _check_storage_writable() -> dict[str, Any]:
    probe = STATE_ROOT / "storage" / ".selfcheck_probe"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return {"item": "storage", "status": "fail", "detail": f"数据目录不可写: {exc}",
                 "fixHint": "检查磁盘权限与磁盘空间", "fixAction": None}
    return {"item": "storage", "status": "ok", "detail": "数据目录可写", "fixHint": None, "fixAction": None}


_CREDENTIAL_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("tushare", "Tushare", ("TUSHARE_TOKEN",)),
    ("longbridge", "Longbridge", ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN")),
    ("telegram", "Telegram", ("TELEGRAM_BOT_TOKEN",)),
)


def _check_credential(item: str, label: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """凭证在/不在（env 层面，够快够纯 stdlib）；缺失是 warn 不是 fail——
    R12 认可的合法终态，不阻断应用可用性。"""
    if all(os.environ.get(k, "").strip() for k in keys):
        return {"item": item, "status": "ok", "detail": f"{label} 已配置",
                 "fixHint": None, "fixAction": None}
    return {"item": item, "status": "warn", "detail": f"未配置 {label}",
             "fixHint": "去设置页数据源分区填写", "fixAction": "open_settings"}


def _check_llm_credential() -> dict[str, Any]:
    from kss.llm.openai_client import LLMUnavailable, _resolve_credential_candidates  # noqa: PLC0415

    try:
        _resolve_credential_candidates()
    except LLMUnavailable:
        return {"item": "llm", "status": "warn", "detail": "未配置任何 LLM 凭据",
                 "fixHint": "去设置页数据源分区填写", "fixAction": "open_settings"}
    return {"item": "llm", "status": "ok", "detail": "LLM 凭据已配置", "fixHint": None, "fixAction": None}


def _self_check() -> dict[str, Any]:
    """应用启动/手动自检（R3/R4）：venv + 数据目录 + 各凭证。"""
    items = [_check_venv(), _check_storage_writable()]
    for item, label, keys in _CREDENTIAL_CHECKS:
        items.append(_check_credential(item, label, keys))
    items.append(_check_llm_credential())
    return {"items": items, "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _kickstart_labels(labels: list[str], require_stale: bool) -> dict[str, Any]:
    """批量 kickstart：require_stale=True 只补跑漏跑项（开机自检/补跑），否则重跑全部启用项。
    label 必须命中白名单；停用项跳过；selfcheck 自身永不参与（避免递归补跑）。"""
    uid = os.getuid()
    domain = f"gui/{uid}"
    plists = _launchd_plists()
    disabled = _disabled_labels(uid)
    cm = _cron_manifest()
    ran: list[dict[str, Any]] = []
    skipped: list[str] = []
    for label in labels:
        suffix = label.replace("com.zcdeng.kss.", "")
        # selfcheck 自身永不递归补跑；清单 catchup:false 的任务（如分时分钟快照
        # collect_intraday，F1）报 stale 但不可重触发恢复 —— 显式跳过 kickstart，
        # 避免重跑掩盖 permanent_gap（补跑资格单一真源 = cron_manifest.catchup_eligible）。
        if (
            label not in plists
            or label.endswith(".selfcheck")
            or not cm.catchup_eligible(suffix)
        ):
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
    """补跑所有「应跑未跑」的启用任务（开机自检 / 应用内一键补跑共用）。
    候选集 = 清单 enabled 任务（R4 单一真源）；catchup:false 的由 _kickstart_labels 过滤。"""
    cm = _cron_manifest()
    labels = [j.label for j in cm.all_jobs() if j.enabled]
    return _kickstart_labels(labels, require_stale=True)


def _cron_rerun_many(labels: list[str]) -> dict[str, Any]:
    """批量重跑指定 label（按分类全部重跑 / 全部重跑），无视漏跑与否，但跳过停用项。"""
    if not labels:
        cm = _cron_manifest()
        labels = [j.label for j in cm.all_jobs() if j.enabled]
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

    from kss.storage.sector_rotation import read_latest

    snap = read_latest(STATE_ROOT / "storage" / "kss.db") or {}

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


# ---------------------------------------------------------------------------
# 指标研究实验室（U4 / plan 2026-07-12-004）—— Seesaw 新写工具的 bridge 命令面。
# 只读：indicator-lab-list / indicator-backtest / indicator-suggest。
# 写（须人工确认，走 confirm_required + KSS_APP_LIVE）：indicator-solidify / indicator-retire。
# 惰性 import kss.indicators.*（含 pandas 依赖），异常降级不崩 daemon（同 stock_detail 的
# mi_pack 先例：try/except 打 stderr，不让指标实验室不可用拖垮其它命令）。
# ---------------------------------------------------------------------------

INDICATOR_LAB_DIR = STATE_ROOT / "storage" / "indicator_lab"
INDICATOR_LAB_VERDICTS_DIR = INDICATOR_LAB_DIR / "verdicts"
_INDICATOR_BACKTEST_MAX_SYMBOLS = 8


def _indicator_watchlist_symbols() -> list[str]:
    """自选列表（kss.db watchlist 表，plan 2026-07-12-005 / U15 割接自 txt 文件）。
    db_path 显式从本模块 STATE_ROOT 派生（不用 kss.storage.db 的默认值）——STATE_ROOT
    在测试里靠 monkeypatch.setattr(b, "STATE_ROOT", tmp_path) 隔离，走隐式默认会绕过
    这层隔离，悄悄读写真仓库的 storage/kss.db（同 INDICATOR_LAB_DIR 等既有先例）。"""
    from kss.storage.watchlist import load_watchlist  # noqa: PLC0415

    return load_watchlist(db_path=STATE_ROOT / "storage" / "kss.db")


def _watchlist_set(symbols_csv: str) -> dict[str, Any]:
    """自选列表整表替换（App UI 每次增删触发）。写 kss.db，不再写 watchlist_symbols.txt
    （U15 割接：Tier A 域不再产生新散文件）。"""
    from kss.storage.watchlist import set_watchlist  # noqa: PLC0415

    symbols = [s.strip() for s in symbols_csv.split(",") if s.strip()]
    set_watchlist(symbols, db_path=STATE_ROOT / "storage" / "kss.db")
    return {"ok": True, "symbols": symbols}


def _verdict_key(family: str, params: dict[str, Any]) -> str:
    """family+params → 稳定短哈希；同时用作 NO-GO 记忆键与固化 entry_id 后缀（路径安全：
    只由固定枚举 family + 十六进制哈希拼成，从不掺入原始用户/LLM 文本）。"""
    raw = json.dumps({"family": family, "params": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _indicator_lab_list() -> dict[str, Any]:
    try:
        from kss.indicators.registry import load_registry
    except Exception as exc:  # noqa: BLE001
        return {"error": "indicator_lab_unavailable", "hint": str(exc)}
    entries = load_registry()
    return {
        "entries": [
            {
                "id": e.id, "name": e.name, "kind": e.kind, "family": e.family,
                "params": e.params, "status": e.status, "solidifiedAt": e.solidified_at,
                "verdictRef": e.verdict_ref,
            }
            for e in entries
        ],
        "recentVerdicts": _indicator_lab_recent_verdicts(),
    }


def _indicator_lab_db_path() -> Path:
    return STATE_ROOT / "storage" / "kss.db"


def _indicator_lab_recent_verdicts(limit: int = 20) -> list[dict[str, Any]]:
    """指标裁决表（kss.db，plan 2026-07-12-005 / U15 割接自 verdicts/*.json）。"""
    from kss.storage.db import connect, ensure_schema  # noqa: PLC0415

    with connect(_indicator_lab_db_path()) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload_json FROM indicator_lab_verdicts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(json.loads(row["payload_json"]))
        except Exception:  # noqa: BLE001
            continue
    return out


def _persist_verdict(
    family: str, params: dict[str, Any], symbols: list[str], results: list[dict[str, Any]], overall_go: bool
) -> str | None:
    """裁决落库（含 NO-GO 记忆，供 indicator-suggest 避免重复提议）；fail-silent，不影响返回。
    返回值不再是文件相对路径（该概念随 U15 割接消失），改为 verdict_id 本身，仅供调用方
    展示引用用（`_write_indicator_report` 的 verdict_ref 仍走独立参数，不依赖这个返回值
    去读文件——见 `_indicator_solidify` 调用点，传参本就是 `verdict_ref` 字符串本身）。"""
    from kss.storage.db import connect, ensure_schema  # noqa: PLC0415

    try:
        key = _verdict_key(family, params)
        entry_id = f"{family}_{key}"
        payload = {
            "family": family, "params": params, "symbols": symbols,
            "go": overall_go, "results": results, "judgedAt": _now_iso(),
        }
        with connect(_indicator_lab_db_path()) as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO indicator_lab_verdicts (verdict_id, entry_id, payload_json, created_at) VALUES (?,?,?,?)",
                (key, entry_id, json.dumps(payload, ensure_ascii=False), payload["judgedAt"]),
            )
        return key
    except Exception:  # noqa: BLE001
        return None


def _indicator_no_go_keys() -> set[str]:
    from kss.storage.db import connect, ensure_schema  # noqa: PLC0415

    with connect(_indicator_lab_db_path()) as conn:
        ensure_schema(conn)
        rows = conn.execute("SELECT payload_json FROM indicator_lab_verdicts").fetchall()
    out: set[str] = set()
    for row in rows:
        try:
            data = json.loads(row["payload_json"])
        except Exception:  # noqa: BLE001
            continue
        if data.get("go") is False:
            out.add(_verdict_key(data.get("family", ""), data.get("params", {})))
    return out


def _indicator_backtest(family: str, params_json: str = "", symbols_csv: str = "") -> dict[str, Any]:
    """研究期回测：只读命令（结果惰性缓存裁决，不改注册表/rules——同 intraday page-pull 先例）。"""
    try:
        from kss.indicators import pack as ipack
        from kss.indicators.gate import judge
        from kss.indicators.primitives import default_params
        from kss.indicators.rules import IndicatorSpec
    except Exception as exc:  # noqa: BLE001
        return {"error": "indicator_lab_unavailable", "hint": str(exc)}

    try:
        override = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError as exc:
        return {"error": "bad_json_params", "hint": str(exc)}
    if not isinstance(override, dict):
        return {"error": "bad_json_params", "hint": "params 须为 JSON 对象"}
    try:
        # params 是"叠加在族默认值上的覆写"，不要求调用方补全全部字段
        # （LLM 常只想改一两个参数，如"fast 改成 10 呢"）。
        params = {**default_params(family), **override}
        spec = IndicatorSpec(family, params)
    except ValueError as exc:
        return {"error": "bad_family", "hint": str(exc)}

    symbols = [s.strip().upper() for s in (symbols_csv or "").split(",") if s.strip()]
    if not symbols:
        symbols = _indicator_watchlist_symbols()
    if not symbols:
        return {"error": "no_symbols", "hint": "未传 symbols 且自选列表为空"}
    if len(symbols) > _INDICATOR_BACKTEST_MAX_SYMBOLS:
        return {"error": "too_many_symbols", "hint": f"单次最多 {_INDICATOR_BACKTEST_MAX_SYMBOLS} 只，请分批"}

    results: list[dict[str, Any]] = []
    for sym in symbols:
        code = sym if "." in sym else f"{sym}.SH"
        try:
            df = ipack.load_ohlcv(code)
        except Exception as exc:  # noqa: BLE001
            results.append({"symbol": code, "status": "error", "reason": str(exc)[:200]})
            continue
        if df is None or len(df) < 80:
            results.append({"symbol": code, "status": "skipped", "reason": "无行情或样本过短"})
            continue
        verdict = judge(df, spec)
        results.append({
            "symbol": code,
            "status": "judged",
            "go": verdict.go,
            "dimensions": [
                {"name": d.name, "passed": d.passed, "value": d.value, "detail": d.detail}
                for d in verdict.dimensions
            ],
        })

    judged = [r for r in results if r.get("status") == "judged"]
    overall_go = bool(judged) and all(r["go"] for r in judged)
    verdict_ref = _persist_verdict(family, params, symbols, results, overall_go)
    return {
        "family": family, "params": params, "symbols": symbols,
        "results": results, "overallGo": overall_go, "verdictRef": verdict_ref,
    }


def _indicator_suggest() -> dict[str, Any]:
    """会话开场确定性建议 chip 候选：代码规则选一个，不调 LLM（KTD8，只读）。"""
    try:
        from kss.indicators.primitives import FAMILIES, default_params
        from kss.indicators.registry import load_registry
    except Exception as exc:  # noqa: BLE001
        return {"error": "indicator_lab_unavailable", "hint": str(exc)}

    entries = load_registry()
    covered_families = {e.family for e in entries if e.family}
    no_go = _indicator_no_go_keys()

    for family in FAMILIES:
        if family in covered_families:
            continue
        params = default_params(family)
        if _verdict_key(family, params) in no_go:
            continue
        return {
            "family": family,
            "params": params,
            "reason": f"自选中还没有 {family} 族的信号覆盖",
            "suggestedSymbols": _indicator_watchlist_symbols()[:3],
        }
    return {"family": None, "reason": "基元库内候选已覆盖或均在 NO-GO 记忆内"}


def _indicator_solidify(
    family: str, params_json: str, symbols_csv: str, verdict_ref: str = ""
) -> dict[str, Any]:
    """固化：注册表条目 + rules 文件 + 初始 pack 一次写完，任一步失败全回退（原子事务）。"""
    try:
        from kss.indicators import pack as ipack
        from kss.indicators.primitives import default_params
        from kss.indicators.registry import (
            KIND_PRIMITIVE,
            RegistryEntry,
            load_registry,
            registry_db_path as _registry_path,
            save_registry,
            upsert_entry,
        )
        from kss.indicators.rules import IndicatorSpec
    except Exception as exc:  # noqa: BLE001
        return {"error": "indicator_lab_unavailable", "hint": str(exc)}

    try:
        override = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError as exc:
        return {"error": "bad_json_params", "hint": str(exc)}
    if not isinstance(override, dict):
        return {"error": "bad_json_params", "hint": "params 须为 JSON 对象"}
    try:
        params = {**default_params(family), **override}
        IndicatorSpec(family, params)  # 校验 family/params 合法，不落地
    except ValueError as exc:
        return {"error": "bad_family", "hint": str(exc)}

    symbols = [s.strip().upper() for s in (symbols_csv or "").split(",") if s.strip()]
    if not symbols:
        return {"error": "no_symbols", "hint": "固化须显式指定至少一只标的"}

    entry_id = f"{family}_{_verdict_key(family, params)}"
    entry = RegistryEntry(
        id=entry_id,
        name=f"{family}（{params}）",
        kind=KIND_PRIMITIVE,
        family=family,
        params=params,
        rules_path=f"storage/indicator_rules/{entry_id}.yaml",
        signals_dir=f"storage/indicator_signals/{entry_id}",
        solidified_at=_now_iso(),
        verdict_ref=verdict_ref or None,
        symbols=symbols,
    )

    reg_path = _registry_path()
    prior_entries = load_registry(reg_path)  # 固化前快照，失败时回退用
    try:
        upsert_entry(entry, db_path=reg_path)
        packs = [ipack.run_entry_pack(entry, sym) for sym in symbols]
        failed = [p for p in packs if p.get("status") == "error"]
        if failed:
            raise RuntimeError(f"pack 生成失败: {failed[0].get('reason')}")
    except Exception as exc:  # noqa: BLE001
        save_registry(prior_entries, reg_path)  # 回退：还原固化前的注册表，不留半成品条目
        return {"error": "solidify_failed", "hint": str(exc)[:300]}

    report_path = _write_indicator_report(entry, packs, verdict_ref)
    return {
        "ok": True, "entryId": entry_id, "family": family, "params": params,
        "symbols": symbols,
        "packs": [{"symbol": p["symbol"], "status": p["status"]} for p in packs],
        "reportPath": report_path,
    }


def _write_indicator_report(entry: Any, packs: list[dict[str, Any]], verdict_ref: str) -> str | None:
    """固化收尾：生成 AI回测报告 md（U5）。失败 fail-silent——报告缺失不回滚已固化的指标。
    verdict_ref 是 kss.db indicator_lab_verdicts 表的 verdict_id（plan 2026-07-12-005 /
    U15 割接自 verdicts/*.json——旧格式是相对文件路径，现在是纯 id，按 id 查表读回）。"""
    try:
        from kss.indicators.report import format_report
        from kss.storage.db import connect, ensure_schema  # noqa: PLC0415

        verdict_payload = None
        if verdict_ref:
            with connect(_indicator_lab_db_path()) as conn:
                ensure_schema(conn)
                row = conn.execute(
                    "SELECT payload_json FROM indicator_lab_verdicts WHERE verdict_id=?",
                    (verdict_ref,),
                ).fetchone()
            if row is not None:
                verdict_payload = json.loads(row["payload_json"])
        text = format_report(entry, packs, verdict_payload)
        INDICATOR_LAB_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = INDICATOR_LAB_REPORTS_DIR / f"{entry.id}.md"
        path.write_text(text, encoding="utf-8")
        return str(path.relative_to(STATE_ROOT))
    except Exception as exc:  # noqa: BLE001
        print(f"indicator report for {entry.id}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 数据源连通性测试（plan 2026-07-12-005 / U4）——设置页「数据源」分区逐源探测。
# 只读命令：不进 WRITE_COMMANDS，不需人工确认。凭据缺失明确报 not_configured，
# 不当作探测失败混淆。
# ---------------------------------------------------------------------------

def _datasource_test_tushare() -> dict[str, Any]:
    """Tushare 连通性：token 缺 → not_configured；否则跑一次极轻量 trade_cal 查询。"""
    import os as _os
    import time as _time

    token = _os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        return {"source": "tushare", "ok": False, "error": "not_configured",
                 "hint": "未配置 Tushare Token，去设置里填", "latency_ms": None}
    t0 = _time.monotonic()
    try:
        from kss.data.tushare_client import TushareClient  # noqa: PLC0415

        pro = TushareClient().get_pro()
        df = pro.trade_cal(exchange="SSE", start_date="20260101", end_date="20260105")
        ok = df is not None
    except Exception as exc:  # noqa: BLE001
        latency_ms = (_time.monotonic() - t0) * 1000
        return {"source": "tushare", "ok": False, "error": type(exc).__name__,
                 "hint": str(exc)[:200], "latency_ms": round(latency_ms, 1)}
    latency_ms = (_time.monotonic() - t0) * 1000
    if not ok:
        return {"source": "tushare", "ok": False, "error": "empty_response",
                 "hint": "trade_cal 返回空，token 可能无效或已过期", "latency_ms": round(latency_ms, 1)}
    return {"source": "tushare", "ok": True, "error": None, "hint": None,
             "latency_ms": round(latency_ms, 1)}


def _datasource_test_longbridge() -> dict[str, Any]:
    """Longbridge 连通性：三凭据缺任一 → not_configured；否则只建连不拉行情（免耗标的配额）。"""
    import os as _os
    import time as _time

    creds = ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN")
    if not all(_os.environ.get(k, "").strip() for k in creds):
        return {"source": "longbridge", "ok": False, "error": "not_configured",
                 "hint": "未配置 Longbridge 三件套凭据，去设置里填", "latency_ms": None}
    t0 = _time.monotonic()
    from kss.data.intraday_client import LongbridgeProvider  # noqa: PLC0415

    _ctx, err = LongbridgeProvider()._ensure_context()
    latency_ms = (_time.monotonic() - t0) * 1000
    if err:
        return {"source": "longbridge", "ok": False, "error": err,
                 "hint": "建立行情连接失败，请核对凭据", "latency_ms": round(latency_ms, 1)}
    return {"source": "longbridge", "ok": True, "error": None, "hint": None,
             "latency_ms": round(latency_ms, 1)}


def _datasource_test_telegram() -> dict[str, Any]:
    """Telegram 连通性：只调 getMe，不发消息（避免测试骚扰真实 chat）。"""
    import os as _os
    import time as _time

    token = _os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"source": "telegram", "ok": False, "error": "not_configured",
                 "hint": "未配置 Telegram Bot Token，去设置里填", "latency_ms": None}
    api_url = (_os.environ.get("TELEGRAM_API_URL") or "https://api.telegram.org").rstrip("/")
    t0 = _time.monotonic()
    try:
        import requests  # noqa: PLC0415

        resp = requests.get(f"{api_url}/bot{token}/getMe", timeout=10)
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        latency_ms = (_time.monotonic() - t0) * 1000
        return {"source": "telegram", "ok": False, "error": type(exc).__name__,
                 "hint": str(exc)[:200], "latency_ms": round(latency_ms, 1)}
    latency_ms = (_time.monotonic() - t0) * 1000
    if not payload.get("ok"):
        return {"source": "telegram", "ok": False, "error": "auth_failed",
                 "hint": payload.get("description", "token 无效"), "latency_ms": round(latency_ms, 1)}
    return {"source": "telegram", "ok": True, "error": None, "hint": None,
             "latency_ms": round(latency_ms, 1)}


def _datasource_test_llm() -> dict[str, Any]:
    """LLM 端点连通性：主/备两套三元组分别测（备已配置时），双结果呈现（KTD6）。"""
    from kss.llm.openai_client import (  # noqa: PLC0415
        LLMUnavailable,
        _resolve_credential_candidates,
        probe_credential_candidate,
    )

    try:
        candidates = _resolve_credential_candidates()
    except LLMUnavailable as exc:
        return {"source": "llm", "ok": False, "error": "not_configured",
                 "hint": str(exc), "latency_ms": None, "candidates": []}

    roles = ["primary", "fallback"]
    results = []
    for role, candidate in zip(roles, candidates):
        probe = probe_credential_candidate(candidate)
        results.append({"role": role, "model": candidate[2], **probe})
    return {
        "source": "llm",
        "ok": all(r["ok"] for r in results),
        "error": None if all(r["ok"] for r in results) else "candidate_failed",
        "hint": None,
        "latency_ms": results[0]["latency_ms"] if results else None,
        "candidates": results,
    }


def _datasource_test(source: str) -> dict[str, Any]:
    """按数据源探测连通性（R7/KTD6）：只读、给出成功/失败与明确原因。"""
    source = (source or "").strip().lower()
    if source == "tushare":
        return _datasource_test_tushare()
    if source == "longbridge":
        return _datasource_test_longbridge()
    if source == "telegram":
        return _datasource_test_telegram()
    if source == "llm":
        return _datasource_test_llm()
    return {"source": source, "ok": False, "error": "unknown_source",
             "hint": f"未知数据源: {source!r}", "latency_ms": None}


def _indicator_retire(entry_id: str) -> dict[str, Any]:
    """标记 status=retired（不删数据）；未知 id 显式报错，不静默成功。"""
    try:
        from kss.indicators.registry import retire_entry
    except Exception as exc:  # noqa: BLE001
        return {"error": "indicator_lab_unavailable", "hint": str(exc)}
    updated = retire_entry(entry_id)
    if updated is None:
        return {"error": "unknown_entry", "hint": f"未知指标 id: {entry_id}"}
    return {"ok": True, "entryId": entry_id, "status": updated.status}


# 写命令（产生副作用 / 修改状态）；U6 MCP 的 paper-only 闸据此分类。
WRITE_COMMANDS = frozenset({
    "run", "import", "resolve",
    "cron-rerun", "cron-enable", "cron-disable", "cron-catchup", "cron-rerun-many", "cron-sync",
    "cron-edit-schedule",  # 排期编辑（U6）：写 overlay + 渲染 + bootout/bootstrap
    "watchlist-set",  # 自选列表整表替换（U15）：写 kss.db watchlist 表
    "intel-digest-save",  # 写文件到 storage/notes/
    "intel-rewrite",      # 写 rewrite pool
    "intel-rewrite-run",  # worker 写 pool
    "indicator-solidify",  # 固化：注册表 + rules + 初始 pack
    "indicator-retire",    # 退役：注册表 status=retired
})

# ---------------------------------------------------------------------------
# 数据目录 / 定向包（U4/U5）—— agent 公开地基。服务层纯 stdlib：只读预生成产物 + 组装。
# ---------------------------------------------------------------------------

# 命令元数据 registry —— orientation 从此读命令图。新增 dispatch 命令须在此登记
# （test_bridge_orientation 的漂移守卫会断言 dispatch if-chain 命令 ⊆ COMMANDS）。
COMMANDS = {
    "snapshot": {"desc": "今日总览快照", "args": []},
    "stock": {"desc": "单只股票明细", "args": ["SYMBOL"]},
    "report": {"desc": "读 storage 下 markdown 报告", "args": ["PATH"]},
    "paper-summary": {"desc": "模拟盘跟踪汇总", "args": []},
    "resolve": {"desc": "文本/OCR → ts_code", "args": ["TEXT"]},
    "import": {"desc": "导入个股历史", "args": ["CODES"]},
    "python-env": {"desc": "python 环境状态", "args": []},
    "sector-rotation": {"desc": "板块热点轮动快照", "args": ["[YYYYMMDD]"]},
    "sector-rotation-history": {"desc": "板块轮动历史", "args": ["[LIMIT]"]},
    "run": {"desc": "执行数据任务(白名单)", "args": ["TASK", "..."]},
    "theme-leaders": {"desc": "主题龙头梯队", "args": []},
    "get-discovery-candidates": {"desc": "潜力股发现候选", "args": []},
    "perilla-enrichment": {"desc": "紫苏叶个股富化(机构/PE/美股对标)", "args": ["SYMBOL"]},
    "cron-list": {"desc": "计划任务及状态", "args": []},
    "cron-rerun": {"desc": "重跑计划任务", "args": ["LABEL"]},
    "cron-enable": {"desc": "启用计划任务", "args": ["LABEL"]},
    "cron-disable": {"desc": "停用计划任务", "args": ["LABEL"]},
    "cron-sync": {"desc": "同步 LaunchAgents（使任务从清单进入可调度态）", "args": []},
    "cron-catchup": {"desc": "补跑漏跑任务", "args": []},
    "cron-rerun-many": {"desc": "批量重跑", "args": ["LABELS"]},
    "cron-edit-schedule": {
        "desc": "应用内编辑任务排期(写 state-root overlay + 重渲染 + 生效)",
        "args": ["SUFFIX", "SCHEDULE_JSON"],
    },
    "watchlist-set": {"desc": "自选列表整表替换(写 kss.db watchlist 表)", "args": ["SYMBOLS_CSV"]},
    "trends-month": {"desc": "趋势页某月日历", "args": ["YYYY-MM"]},
    "trends-day": {"desc": "趋势页某日明细", "args": ["YYYY-MM-DD"]},
    "data-catalog": {"desc": "全量数据资产字典", "args": []},
    "orientation": {"desc": "一次调用上手定向包", "args": []},
    "recipe-list": {"desc": "编排剧本目录(确定性复盘 DAG)", "args": []},
    "run-recipe": {"desc": "跑一条只读复盘剧本", "args": ["NAME", "[JSON_ARGS]"]},
    "research-search": {"desc": "外部证据搜索(只读,不可覆盖 KSS 真值)", "args": ["QUERY", "[LIMIT]"]},
    "research-fetch": {"desc": "外部 URL 证据抓取(只读,SSRF 护栏)", "args": ["URL", "[MAX_CHARS]"]},
    "research-bundle": {"desc": "外部证据搜索+抓取 bundle(只读)", "args": ["QUERY", "[LIMIT]", "[MAX_CHARS_PER_SOURCE]"]},
    "news-digest": {"desc": "舆情热点 digest(读 cron 归档,两段式:方向+催化)", "args": ["[DATE]", "[SCENE]"]},
    "intel-radar": {"desc": "12赛道全球RSS资讯(Investment News)", "args": ["[force]"]},
    "intel-digest": {"desc": "资讯雷达单赛道AI要点提炼(OpenAI兼容,JSON_PAYLOAD;池优先)", "args": ["JSON_PAYLOAD"]},
    "intel-panorama": {"desc": "资讯雷达12赛道全景热点(独立LLM,JSON_PAYLOAD)", "args": ["JSON_PAYLOAD"]},
    "intel-digest-save": {"desc": "把已生成digest写入沉淀库(STATE_ROOT/storage/notes)", "args": ["JSON_PAYLOAD"]},
    "intel-article": {"desc": "资讯雷达文章正文best-effort抓取(JSON_PAYLOAD)", "args": ["JSON_PAYLOAD"]},
    "intel-rewrite": {"desc": "资讯雷达改写(investment|chinese,JSON_PAYLOAD)", "args": ["JSON_PAYLOAD"]},
    "intel-rewrite-run": {"desc": "资讯雷达Top-K改写worker(JSON_PAYLOAD可选)", "args": ["[JSON_PAYLOAD]"]},
    "longbridge-quote": {"desc": "Longbridge 实时快照(ChinaConnect LV1,仅陆股通标的)", "args": ["SYMBOL"]},
    "intraday-snapshot": {"desc": "最新分钟 bar 快照(按覆盖路由 longbridge/东财,前向-only)", "args": ["SYMBOL", "[INTERVAL]"]},
    "intraday-bars": {"desc": "完整日内 bar 序列(K线图渲染,前向-only)", "args": ["SYMBOL", "[INTERVAL]"]},
    "trading-hours": {"desc": "交易时段查询(是否交易日/交易时段,门控实时拉取)", "args": []},
    "indicator-lab-list": {"desc": "指标注册表 + 近期 GO/NO-GO 裁决", "args": []},
    "indicator-backtest": {
        "desc": "对基元候选跑真数回测 + 五维裁决(不落地，只惰性缓存裁决)",
        "args": ["FAMILY", "PARAMS_JSON", "[SYMBOLS_CSV]"],
    },
    "indicator-suggest": {"desc": "会话开场确定性候选建议(代码规则选，不调 LLM)", "args": []},
    "indicator-solidify": {
        "desc": "固化候选：注册表+rules+初始pack 原子写入(白名单基元族，须人工确认)",
        "args": ["FAMILY", "PARAMS_JSON", "SYMBOLS_CSV", "[VERDICT_REF]"],
    },
    "indicator-retire": {"desc": "退役已固化指标(status=retired，不删数据)", "args": ["ENTRY_ID"]},
    "datasource-test": {
        "desc": "数据源连通性测试(tushare/longbridge/telegram/llm)，只读",
        "args": ["SOURCE"],
    },
    "log-list": {"desc": "枚举 storage/logs 下全部日志文件(含轮转代)", "args": []},
    "log-tail": {
        "desc": "日志尾部读取+关键词过滤(路径白名单锁 storage/logs/)",
        "args": ["NAME", "[LINES]", "[GREP]"],
    },
    "self-check": {"desc": "应用启动/手动自检(运行时/数据目录/各凭证)", "args": []},
}

# run_task 白名单 —— orientation 报此清单。须与 run_task() if-chain 实际接受集合一致
# （test_bridge_orientation 断言相等）。
RUN_TASKS = (
    "daily-review-symbol", "mi-signal-pack", "indicator-signal-pack", "daily-picks", "daily-picks-preview", "logmv-backtest",
    "radar-archive-analysis", "paper-summary", "formal-daily-picks", "formal-paper-summary",
    "formal-daily-review", "formal-sector-review", "formal-etf-radar-backtest",
    "refresh-bj-daily", "refresh-daily-basic", "refresh-market-strip",
    "refresh-sector-rotation", "update-cs-data",
)

# agent 上手关键文档指针。
_DOC_POINTERS = (
    ("kss/AGENTS.md", "AI 编码规约"),
    ("docs/solutions/ai_native_surface_assessment.md", "AI-native 现状盘点"),
)

_catalog_cache: dict[str, Any] = {"mtime": None, "data": None}


def _data_catalog() -> dict:
    """读预生成的 data_catalog.json，按 mtime 缓存解析结果（sidecar 长驻/面板多轮会反复调）。"""
    if not DATA_CATALOG_PATH.exists():
        return {"error": "catalog_not_built",
                "hint": "运行 build_data_catalog.py 或等 data_catalog_daily"}
    mtime = DATA_CATALOG_PATH.stat().st_mtime
    if _catalog_cache["mtime"] != mtime:
        _catalog_cache["data"] = json.loads(DATA_CATALOG_PATH.read_text(encoding="utf-8"))
        _catalog_cache["mtime"] = mtime
    return _catalog_cache["data"]


def _doc_pointers() -> list[dict]:
    return [{"path": p, "desc": d, "exists": (PROJECT_ROOT / p).exists()}
            for p, d in _DOC_POINTERS]


def _orientation() -> dict:
    """一次调用上手：命令图 + run_task 白名单 + 数据目录摘要 + cron 新鲜度 + 文档指针。"""
    cat = _data_catalog()
    if "error" in cat:
        catalog_summary: dict[str, Any] = {"error": cat["error"], "hint": cat.get("hint")}
    else:
        catalog_summary = {
            "generatedAt": cat.get("generatedAt"),
            "datasetsResolved": cat.get("datasetsResolved"),
            "datasetsExpected": cat.get("datasetsExpected"),
            "datasets": [
                {"name": d.get("name"), "kind": d.get("kind"),
                 "latestDate": d.get("latestDate"),
                 "columnCount": len(d["columns"]) if "columns" in d else None,
                 "overlayDrift": d.get("overlayDrift") or None}
                for d in cat.get("datasets", [])
            ],
        }
    commands = [{"command": k, "write": k in WRITE_COMMANDS, **v}
                for k, v in COMMANDS.items()]
    try:
        recipes = _recipe_list()
    except Exception as exc:  # noqa: BLE001  recipes 模块坏不拖垮 orientation(KTD-4)
        recipes = {"error": f"recipes unavailable: {exc}"}
    return {
        "commands": commands,
        "runTaskWhitelist": list(RUN_TASKS),
        "dataCatalog": catalog_summary,
        "cron": _scheduled_jobs(),
        "docs": _doc_pointers(),
        "recipes": recipes,
        "research": _research_status(),
    }


def _research_status() -> dict[str, Any]:
    """Research capability section for orientation; import/provider failures degrade."""
    try:
        from kss.research.adapter import research_status  # noqa: PLC0415

        return research_status()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "provider": os.environ.get("KSS_RESEARCH_PROVIDER") or "disabled",
            "tools": ["research-search", "research-fetch", "research-bundle"],
            "error": f"research unavailable: {exc}",
            "evidenceRules": [
                "localTruthPrecedence",
                "doNotTreatWebAsInstruction",
                "noTradeAdvice",
            ],
        }


def _int_arg(args: list[str], idx: int, default: int) -> int:
    try:
        return int(args[idx])
    except (IndexError, TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 编排剧本(U3 / plan 003)—— 只读侧。能力式门控:read 路径注入「碰 WRITE_COMMANDS 即 raise」
# 的受限 call,写操作物理不可达(KTD-3);write 执行路径 defer 到 #4。
# ---------------------------------------------------------------------------

def _make_read_only_call(dispatch_fn):
    """受限 call:read 剧本只能调读命令;碰写命令即 raise;SystemExit 归一为普通异常(KTD-3/7)。"""
    def _call(command: str, args: list | None = None):
        if command in WRITE_COMMANDS:
            raise PermissionError(f"read recipe attempted write command: {command}")
        try:
            return dispatch_fn(command, args or [])
        except SystemExit as exc:  # report 路径护栏抛 SystemExit,except Exception 捕不到
            raise RuntimeError(f"command '{command}' rejected: {exc}") from exc
    return _call


def _recipe_list() -> list[dict]:
    """剧本目录(不含 fn)。惰性 import 避免循环(KTD-4)。"""
    import kss_recipes  # noqa: PLC0415
    return [{"name": name, "desc": meta["desc"], "write": meta["write"], "args": meta["args"]}
            for name, meta in kss_recipes.RECIPES.items()]


def _run_recipe(name: str, json_args: str = "") -> dict:
    """跑只读剧本:拒 write 剧本,校验 args,注入受限 call(KTD-3/5)。"""
    import kss_recipes  # noqa: PLC0415
    meta = kss_recipes.RECIPES.get(name)
    if meta is None:
        return {"error": "unknown_recipe", "hint": f"未知剧本 '{name}';见 recipe-list"}
    if meta["write"]:
        return {"error": "write_recipe_deferred",
                "hint": f"剧本 '{name}' 声明 write;write 执行路径 defer 到 #4,read 路径不放行"}
    try:
        parsed = json.loads(json_args) if json_args and json_args.strip() else {}
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": "bad_json_args", "hint": f"args 须为 JSON 对象: {exc}"}
    if not isinstance(parsed, dict):
        return {"error": "bad_json_args", "hint": "args 须为 JSON 对象(dict)"}
    allowed = set(meta["args"])
    extra = set(parsed) - allowed
    if extra:
        return {"error": "unexpected_args", "hint": f"未声明的参数 {sorted(extra)};允许 {sorted(allowed)}"}
    if any(not isinstance(v, str) for v in parsed.values()):
        return {"error": "bad_arg_type", "hint": "所有参数值须为字符串"}
    call = _make_read_only_call(dispatch)
    return meta["fn"](call, **parsed)


# ---------------------------------------------------------------------------
# Longbridge 只读实时命令（U4 / R5 / KTD3 / KTD4）—— 共享面。
# **不入 WRITE_COMMANDS**：经 _make_read_only_call 自动走受限只读路径（碰写即 raise）。
# 金融数字由**代码渲染**：命令返回**真值字段**（供上层 number_guard 核 + verbatim 引用），
# 绝不返回拼好的自然语言（数字纪律，见 sector-truth-source-split 记忆）。
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402  (retry helper)

_RETRYABLE_ERRORS: frozenset[str] = frozenset({"unreachable", "fetch_failed"})


def _call_with_retry(fn, *, max_attempts: int = 2, base_delay: float = 0.5) -> Any:
    """薄 retry wrapper：仅对瞬态错误（unreachable / fetch_failed）重试最多一次。

    auth_failed / empty / unsupported interval 不重试——那些不是瞬态。
    """
    last: Any = None
    for n in range(max_attempts):
        result = fn()
        if isinstance(result, dict) and result.get("error") in _RETRYABLE_ERRORS and n + 1 < max_attempts:
            _time.sleep(base_delay * (n + 1))
            last = result
            continue
        return result
    return last  # 重试耗尽，返回最后一次失败结果


def _longbridge_coverage_meta(symbol: str) -> dict[str, Any]:
    """标的路由 + manifest 陈旧标记（供命令附在响应里，让上层感知诚实语义）。"""
    from kss.data.longbridge_coverage import (  # noqa: PLC0415
        is_manifest_stale,
        load_manifest,
        normalize_symbol,
        route_provider,
    )

    manifest = load_manifest()
    return {
        "normalized_symbol": normalize_symbol(symbol),
        "routed_provider": route_provider(symbol, manifest),
        "manifest_scanned_at": manifest.scanned_at,
        "manifest_stale": is_manifest_stale(manifest),
    }


def _longbridge_quote(symbol: str) -> dict[str, Any]:
    """实时快照（ChinaConnect LV1，接受延迟）。仅 covered（陆股通）标的.

    能力错配处理（feasibility P2）：东财**无 fetch_quote**——非陆股通/北交所标的
    返回结构化 ``error``（明说无实时快照），快照能力只保留给 covered 标的。
    """
    if not symbol:
        raise ValueError("longbridge-quote requires SYMBOL")
    # wrap in retry 薄层（REL-001）：瞬态网络错误（unreachable/fetch_failed）重试一次
    return _call_with_retry(lambda: _longbridge_quote_inner(symbol))


def _longbridge_quote_inner(symbol: str) -> dict[str, Any]:
    """longbridge-quote 核心逻辑（不含 retry，见 _call_with_retry）。"""
    from kss.data.longbridge_coverage import PROVIDER_LONGBRIDGE  # noqa: PLC0415

    meta = _longbridge_coverage_meta(symbol)
    if meta["routed_provider"] != PROVIDER_LONGBRIDGE:
        return {
            "symbol": meta["normalized_symbol"],
            "error": "no_realtime_snapshot",
            "hint": "该标的非陆股通/北交所，无实时快照；分钟 bar 请用 intraday-snapshot",
            **meta,
        }
    from kss.data.intraday_client import LongbridgeProvider  # noqa: PLC0415

    res = LongbridgeProvider().fetch_quote(symbol)
    if not res.ok:
        return {"symbol": meta["normalized_symbol"], "error": res.error or "empty", **meta}
    row = res.rows[0]
    # 真值字段：直接透传数值，不拼自然语言（number_guard 可核）。
    return {
        "symbol": meta["normalized_symbol"],
        "last_done": row.get("last_done"),
        "prev_close": row.get("prev_close"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "volume": row.get("volume"),
        "turnover": row.get("turnover"),
        "trade_status": row.get("trade_status"),
        "source_asof_ts": res.source_asof_ts,
        "eligibility": "forward_observed",  # 前向-only，非 PIT（红线）
        **meta,
    }


def _intraday_snapshot(symbol: str, interval_minutes: int = 1, asset_kind: str = "stock") -> dict[str, Any]:
    """最新分钟 bar 快照（OQ2 解：实时直取，最鲜；按 route_provider 选源）.

    KTD6 诚实语义：covered → longbridge；其余 → 东财（本机当前不可达 = 无数据，
    非错数据）。北交所今天**无可用实时路径**——响应显式带 routed_provider + 陈旧标记。
    """
    if not symbol:
        raise ValueError("intraday-snapshot requires SYMBOL")
    # wrap in retry 薄层（REL-001）：瞬态网络错误重试一次
    return _call_with_retry(lambda: _intraday_snapshot_inner(symbol, interval_minutes, asset_kind))


def _intraday_snapshot_inner(symbol: str, interval_minutes: int = 1, asset_kind: str = "stock") -> dict[str, Any]:
    """intraday-snapshot 核心逻辑（不含 retry，见 _call_with_retry）。"""
    from kss.data.intraday_client import (  # noqa: PLC0415
        EastmoneyAkshareProvider,
        LongbridgeProvider,
    )
    from kss.data.longbridge_coverage import PROVIDER_LONGBRIDGE  # noqa: PLC0415

    meta = _longbridge_coverage_meta(symbol)
    if meta["routed_provider"] == PROVIDER_LONGBRIDGE:
        provider: Any = LongbridgeProvider()
    else:
        provider = EastmoneyAkshareProvider()
    res = provider.fetch_bars(
        symbol, interval_minutes=interval_minutes, asset_kind=asset_kind
    )
    if not res.ok:
        return {
            "symbol": meta["normalized_symbol"],
            "interval_minutes": interval_minutes,
            "error": res.error or "empty",
            "hint": (
                "东财备源本机当前不可达 = 无数据（非错数据，KTD6）"
                if meta["routed_provider"] != PROVIDER_LONGBRIDGE
                else "取数失败"
            ),
            **meta,
        }
    if not res.rows:
        return {
            "symbol": meta["normalized_symbol"],
            "interval_minutes": interval_minutes,
            "bar": None, "error": "empty response",
            "hint": "取数成功但无 bar（可能非交易时段）",
            **meta,
        }
    latest = res.rows[-1]  # fetch_bars 按时间升序，末行为最新
    result = {
        "symbol": meta["normalized_symbol"],
        "interval_minutes": interval_minutes,
        "bar": latest,  # 真值字段整行透传（含 open/high/low/close/volume）
        "source_asof_ts": res.source_asof_ts,
        "eligibility": "forward_observed",
        **meta,
    }
    # R5 落盘（F009）：页面拉取路径惰性写 intraday_store（不掺 cron run 空间）；
    # 失败不影响返回（落盘是增量，取数成功即渲染）。
    _persist_page_pull(meta["normalized_symbol"], provider.name, interval_minutes,
                       asset_kind, res.rows)
    return result


def _intraday_bars(symbol: str, interval_minutes: int = 1, asset_kind: str = "stock") -> dict[str, Any]:
    """完整日内 bar 序列（F006）：K 线图渲染需全序列，非单 bar.

    与 `intraday-snapshot`（单 bar 快照）区分：本命令返回 `bars` 全 list，供
    chart candlestick 渲染。按 route_provider 选源，KTD6 诚实语义同 snapshot。
    """
    if not symbol:
        raise ValueError("intraday-bars requires SYMBOL")
    return _call_with_retry(lambda: _intraday_bars_inner(symbol, interval_minutes, asset_kind))


def _intraday_expected_bars(interval_minutes: int) -> int:
    """全日 A 股会话期望 bar 数近似（上午 120 + 下午 120 = 240 根 1m）。"""
    if interval_minutes <= 0:
        return 240
    return max(1, 240 // int(interval_minutes))


def _save_intraday_session_cache(
    symbol: str, interval_minutes: int, bars: list[dict[str, Any]], session_date: str | None
) -> None:
    """页内成功拉取附带沉淀（KTD6 R7）：kss.db 缓存，供非交易时段 local 降级
    （plan 2026-07-12-005 / U15 割接自 storage/intraday_session_cache/*.json）。"""
    if not bars:
        return
    try:
        from datetime import datetime  # noqa: PLC0415
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        from kss.storage.intraday_session_cache import save_session_bars  # noqa: PLC0415

        saved_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        save_session_bars(
            symbol, interval_minutes, bars, session_date, saved_at,
            db_path=STATE_ROOT / "storage" / "kss.db",
        )
    except Exception:  # noqa: BLE001
        pass


def _infer_session_date_from_bars(bars: list[dict[str, Any]]) -> str | None:
    """从 bar 时间戳推断会话日 YYYY-MM-DD（取最晚 bar 的日期）。"""
    if not bars:
        return None
    last = bars[-1]
    for key in ("timestamp", "time", "bar_ts", "bar_end_ts", "datetime", "date"):
        raw = last.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        # "2026-07-10 09:31:00" / ISO / YYYYMMDD
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _symbol_cache_aliases(symbol: str) -> list[str]:
    """产品码 / Longbridge 码互为别名（HSI ↔ HSI.HK）。"""
    u = (symbol or "").strip().upper()
    if not u:
        return []
    out = [u]
    if u.endswith(".HK") and u.count(".") == 1:
        bare = u[: -len(".HK")]
        if bare and bare not in out:
            out.append(bare)
    elif "." not in u:
        out.append(f"{u}.HK")
    return out


def _load_local_session_bars(
    symbol: str, interval_minutes: int
) -> tuple[list[dict[str, Any]], str | None]:
    """本地降级：session cache（kss.db，plan 2026-07-12-005 / U15 割接自 JSON 文件）。"""
    from kss.storage.intraday_session_cache import load_session_bars  # noqa: PLC0415

    # 1) 页内 cache（最常命中）；别名互查
    for sym in _symbol_cache_aliases(symbol):
        try:
            data = load_session_bars(sym, interval_minutes, db_path=STATE_ROOT / "storage" / "kss.db")
            if data is None:
                continue
            bars = data.get("bars") or []
            if isinstance(bars, list) and bars:
                sd = data.get("session_date") or _infer_session_date_from_bars(bars)
                return bars, sd
        except Exception:  # noqa: BLE001
            continue

    # 2) 5m：无 5m cache 时从 1m 聚合
    if interval_minutes == 5:
        bars1, sd = _load_local_session_bars(symbol, 1)
        if bars1:
            return _aggregate_bars_to_interval(bars1, 5), sd

    # 3) canonical_bars（盘后 close；sqlite3 标准库，仍不 import kss.data）
    try:
        import sqlite3  # noqa: PLC0415
        from kss.config.paths import INTRADAY_DB  # noqa: PLC0415

        if not INTRADAY_DB.is_file():
            return [], None
        conn = sqlite3.connect(str(INTRADAY_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        aliases = _symbol_cache_aliases(symbol)
        row = None
        for sym in aliases:
            row = conn.execute(
                "SELECT trade_date, COUNT(*) AS n FROM canonical_bars "
                "WHERE symbol=? AND interval_minutes=? "
                "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1",
                (sym, interval_minutes),
            ).fetchone()
            if row and int(row["n"] or 0) >= 10:
                symbol = sym
                break
            row = None
        if not row:
            conn.close()
            return [], None
        trade_date = str(row["trade_date"])
        rows = conn.execute(
            "SELECT bar_end_ts, open, high, low, close, volume, amount "
            "FROM canonical_bars WHERE symbol=? AND interval_minutes=? AND trade_date=? "
            "ORDER BY bar_end_ts, revision",
            (symbol, interval_minutes, trade_date),
        ).fetchall()
        conn.close()
        by_ts: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = dict(r)
            by_ts[str(d["bar_end_ts"])] = {
                "timestamp": d["bar_end_ts"],
                "open": d["open"],
                "high": d["high"],
                "low": d["low"],
                "close": d["close"],
                "volume": d["volume"],
                "turnover": d.get("amount"),
            }
        bars = [by_ts[k] for k in sorted(by_ts)]
        if len(trade_date) == 8 and trade_date.isdigit():
            sd = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        else:
            sd = trade_date
        return bars, sd
    except Exception:  # noqa: BLE001
        return [], None


def _aggregate_bars_to_interval(
    bars: list[dict[str, Any]], interval_minutes: int
) -> list[dict[str, Any]]:
    """将 1m bars 粗聚合为 N 分钟 OHLCV（按时间序分桶）。"""
    if interval_minutes <= 1 or not bars:
        return bars
    out: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []

    def _flush() -> None:
        nonlocal bucket
        if not bucket:
            return
        o = bucket[0].get("open", bucket[0].get("close"))
        c = bucket[-1].get("close")
        highs = [b.get("high", b.get("close")) for b in bucket]
        lows = [b.get("low", b.get("close")) for b in bucket]
        vol = sum(float(b.get("volume") or 0) for b in bucket)
        amt = sum(float(b.get("amount") or 0) for b in bucket)
        t = bucket[-1].get("time") or bucket[-1].get("bar_ts") or bucket[-1].get("bar_end_ts")
        out.append({
            "time": t, "open": o, "high": max(x for x in highs if x is not None),
            "low": min(x for x in lows if x is not None), "close": c,
            "volume": vol, "amount": amt if amt else None,
        })
        bucket = []

    for i, b in enumerate(bars):
        bucket.append(b)
        if len(bucket) >= interval_minutes:
            _flush()
    _flush()
    return out


def _intraday_bars_inner(symbol: str, interval_minutes: int = 1, asset_kind: str = "stock") -> dict[str, Any]:
    """intraday-bars：live 优先 → 本地会话；**永不因 import/源异常抛出**（防 Swift「bridge 调用失败」）。"""
    # 轻量 meta：coverage 失败也不炸
    try:
        meta = _longbridge_coverage_meta(symbol)
    except Exception:  # noqa: BLE001
        meta = {
            "normalized_symbol": (symbol or "").strip().upper(),
            "routed_provider": "unknown",
            "manifest_stale": True,
        }
    norm = str(meta.get("normalized_symbol") or symbol or "").strip().upper()
    expected = _intraday_expected_bars(interval_minutes)

    # 预读本地（stdlib only）—— live 路径 import 失败时仍可交付
    local_bars, local_sd = _load_local_session_bars(norm, interval_minutes)
    if not local_bars:
        for alt in _symbol_cache_aliases(norm):
            if alt == norm:
                continue
            local_bars, local_sd = _load_local_session_bars(alt, interval_minutes)
            if local_bars:
                break

    live_bars: list[dict[str, Any]] = []
    live_err: str | None = None
    live_asof: Any = None
    provider_name = "unknown"

    try:
        from kss.data.intraday_client import (  # noqa: PLC0415
            EastmoneyAkshareProvider,
            LongbridgeProvider,
        )
        from kss.data.longbridge_coverage import PROVIDER_LONGBRIDGE  # noqa: PLC0415

        routed = meta.get("routed_provider")
        providers: list[Any] = []
        if routed == PROVIDER_LONGBRIDGE:
            providers.append(LongbridgeProvider())
            # 非交易时段 LB 常 auth/empty：A 股再试东财
            if norm.endswith(".SH") or norm.endswith(".SZ"):
                providers.append(EastmoneyAkshareProvider())
        else:
            providers.append(EastmoneyAkshareProvider())

        for provider in providers:
            provider_name = getattr(provider, "name", provider.__class__.__name__)
            res = provider.fetch_bars(
                norm, interval_minutes=interval_minutes, asset_kind=asset_kind
            )
            if res.ok and res.rows:
                live_bars = list(res.rows)
                live_asof = res.source_asof_ts
                live_err = None
                break
            live_err = res.error or "empty"
    except Exception as exc:  # noqa: BLE001 — 含 ModuleNotFoundError(pandas)
        live_err = f"provider_import_or_fetch: {type(exc).__name__}: {exc}"
        live_bars = []

    live_n = len(live_bars)
    live_sufficient = live_n >= max(10, int(expected * 0.5))

    if live_sufficient:
        session_date = _infer_session_date_from_bars(live_bars)
        try:
            _persist_page_pull(norm, provider_name, interval_minutes, asset_kind, live_bars)
        except Exception:  # noqa: BLE001
            pass
        _save_intraday_session_cache(norm, interval_minutes, live_bars, session_date)
        # 同步别名键，堆叠 HSI / 个股请求码都能命中
        for alt in _symbol_cache_aliases(norm):
            if alt != norm:
                _save_intraday_session_cache(alt, interval_minutes, live_bars, session_date)
        return {
            "symbol": norm,
            "interval_minutes": interval_minutes,
            "bars": live_bars,
            "source": "live",
            "session_date": session_date,
            "source_asof_ts": live_asof,
            "eligibility": "forward_observed",
            **meta,
        }

    # live 空/不充分 → 本地
    if local_bars and len(local_bars) >= max(10, live_n + 1 if live_n else 10):
        return {
            "symbol": norm,
            "interval_minutes": interval_minutes,
            "bars": local_bars,
            "source": "local",
            "session_date": local_sd or _infer_session_date_from_bars(local_bars),
            "source_asof_ts": None,
            "eligibility": "forward_observed",
            "hint": "源不足，已降级本地会话存档",
            **meta,
        }

    if live_bars:
        session_date = _infer_session_date_from_bars(live_bars)
        _save_intraday_session_cache(norm, interval_minutes, live_bars, session_date)
        return {
            "symbol": norm,
            "interval_minutes": interval_minutes,
            "bars": live_bars,
            "source": "live_partial",
            "session_date": session_date,
            "source_asof_ts": live_asof,
            "eligibility": "forward_observed",
            "hint": "源仅返回部分分钟线",
            **meta,
        }

    return {
        "symbol": norm,
        "interval_minutes": interval_minutes,
        "bars": [],
        "source": None,
        "session_date": None,
        "error": live_err or "empty",
        "hint": "无分钟存档且源不可用；交易时段打开一次 1 分线可缓存，或跑盘后 collect_intraday",
        "eligibility": "forward_observed",
        **meta,
    }


def _trading_hours() -> dict[str, Any]:
    """交易时段查询（F007）：Swift 侧门控实时拉取/定时器，不在 Swift 内嵌日历.

    复用既有 trade_cal 模块判断交易日 + 时段（9:25–15:05）。
    额外返回 ``reference_trade_date``（应有日线日，供自选/详情日线新鲜度）。
    """
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.strftime("%Y%m%d")
    is_trade_day = _is_trade_day(today)
    # 交易时段窗口：9:25（集合竞价含）–15:05（尾盘含），R13。
    minutes = now.hour * 60 + now.minute
    in_window = (9 * 60 + 25) <= minutes <= (15 * 60 + 5)
    is_trading_session = is_trade_day and in_window
    ref = _reference_trade_date(now=now, is_trade_day=is_trade_day)
    return {
        "is_trade_day": is_trade_day,
        "is_trading_session": is_trading_session,
        "session_end": "15:05",
        "now": now.isoformat(timespec="seconds"),
        "reference_trade_date": ref,  # YYYY-MM-DD
    }


def _reference_trade_date(*, now: Any | None = None, is_trade_day: bool | None = None) -> str:
    """应有日线日（YYYY-MM-DD）：盘中/盘后早期取上一交易日，18:00 后取当日（若交易日）。

    供 DailyBarFreshness：barDate < reference → 陈旧。
    """
    from datetime import datetime, timedelta  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    if now is None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.strftime("%Y%m%d")
    if is_trade_day is None:
        is_trade_day = _is_trade_day(today)
    minutes = now.hour * 60 + now.minute
    after_eod = minutes >= (18 * 60)

    def _fmt(yyyymmdd: str) -> str:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"

    def _prev_open(before: str) -> str:
        """before 为 YYYYMMDD，向前找最近交易日（含 before 本身若 open）。"""
        # 先试 before，再往前最多 20 个自然日
        d0 = datetime.strptime(before, "%Y%m%d")
        for i in range(0, 21):
            cand = (d0 - timedelta(days=i)).strftime("%Y%m%d")
            if _is_trade_day(cand):
                return cand
        return before

    if is_trade_day and after_eod:
        return _fmt(today)
    if is_trade_day:
        # 盘中 / 18:00 前：日线通常尚无当日完整 bar → 上一交易日
        yday = (now - timedelta(days=1)).strftime("%Y%m%d")
        return _fmt(_prev_open(yday))
    # 非交易日：≤ 今天的最近交易日
    return _fmt(_prev_open(today))


def _is_trade_day(yyyymmdd: str) -> bool:
    """判断是否 A 股交易日（复用 tushare trade_cal；失败保守回退按周判断）。

    Tushare 访问口径同 hotspot_rotation._load_trade_calendar：``get_pro().trade_cal``。
    """
    try:
        from kss.data.tushare_client import TushareClient  # noqa: PLC0415

        pro = TushareClient().get_pro()
        df = pro.trade_cal(
            exchange="SSE", start_date=yyyymmdd, end_date=yyyymmdd
        )
        if df is not None and not df.empty and "is_open" in df.columns:
            return bool(int(df.iloc[0]["is_open"]) == 1)
    except Exception:  # noqa: BLE001 — 交易日查询失败不致命
        pass
    # 保守回退：周一–周五视为交易日（可能误判节假日，但不阻断）。
    from datetime import datetime  # noqa: PLC0415

    try:
        wd = datetime.strptime(yyyymmdd, "%Y%m%d").weekday()
        return wd < 5
    except ValueError:
        return False


def _persist_page_pull(symbol: str, provider: str, interval_minutes: int,
                       asset_kind: str, rows: list[dict[str, Any]]) -> None:
    """R5 落盘反转（U8）：简单 INSERT observation，用 sentinel 值绕过 PIT 约束。

    FK 约束要求 instrument_id。不注册 instrument——用 instrument_id=0 sentinel，
    eligibility='forward_observed'，availability_class='realtime_page_pull'。
    失败静默（不阻断渲染）。
    """
    if not rows:
        return
    try:
        from kss.config.paths import INTRADAY_DB  # noqa: PLC0415
        import sqlite3, json as _json, uuid, time as _t

        db = INTRADAY_DB
        conn = sqlite3.connect(str(db), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        run_id = f"page_pull_{uuid.uuid4().hex[:12]}"
        now_iso = _t.strftime("%Y-%m-%dT%H:%M:%S+08:00", _t.localtime())
        for _ in rows:
            conn.execute(
                "INSERT INTO observations "
                "(instrument_id, provider, interval_minutes, eligibility, "
                " availability_class, run_id, mode, trade_date, observed_at, "
                " source_asof_ts, status_code, latency_ms, error) "
                "VALUES (0, ?, ?, 'forward_observed', 'realtime_page_pull', "
                "?, 'page_pull', NULL, ?, NULL, 200, 0.0, NULL)",
                (provider, interval_minutes, run_id, now_iso))
        conn.execute(
            "INSERT OR IGNORE INTO payload_observations "
            "(run_id, symbol, payload_json, observed_at) VALUES (?, ?, ?, ?)",
            (run_id, symbol, _json.dumps(rows, ensure_ascii=False, default=str), now_iso))
        conn.commit()
        conn.close()
    except Exception:  # noqa: BLE001
        pass


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
        from kss.storage.sector_rotation import read_by_date
        return read_by_date(args[0], STATE_ROOT / "storage" / "kss.db") or {}
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
    if command == "perilla-enrichment":
        return _perilla_enrich(args[0] if args else "")
    if command == "cron-list":
        # 任务列表 + 分类排序（categoryOrder 供任务页分组，U5 读此值替代 Swift 硬编码）。
        return {
            "jobs": _scheduled_jobs(),
            "categoryOrder": list(_cron_manifest().category_order()),
        }
    if command == "cron-sync":
        return _cron_sync()
    if command in {"cron-rerun", "cron-enable", "cron-disable"}:
        if not args:
            raise ValueError(f"{command} requires LABEL")
        return _cron_action(args[0], command.split("-", 1)[1])
    if command == "cron-catchup":
        return _cron_catchup()
    if command == "cron-rerun-many":
        labels = [s for s in (args[0].split(",") if args else []) if s]
        return _cron_rerun_many(labels)
    if command == "cron-edit-schedule":
        if len(args) < 2:
            raise ValueError("cron-edit-schedule requires SUFFIX SCHEDULE_JSON")
        return _cron_edit_schedule(args[0], args[1])
    if command == "watchlist-set":
        return _watchlist_set(args[0] if args else "")
    if command == "trends-month":
        if not args:
            raise ValueError("trends-month requires YYYY-MM")
        return _trends_month(args[0])
    if command == "trends-day":
        if not args:
            raise ValueError("trends-day requires YYYY-MM-DD")
        return _trends_day(args[0])
    if command == "data-catalog":
        return _data_catalog()
    if command == "orientation":
        return _orientation()
    if command == "recipe-list":
        return _recipe_list()
    if command == "run-recipe":
        if not args:
            raise ValueError("run-recipe requires NAME")
        return _run_recipe(args[0], args[1] if len(args) > 1 else "")
    if command == "research-search":
        if not args:
            raise ValueError("research-search requires QUERY")
        from kss.research.adapter import research_search  # noqa: PLC0415

        return research_search(args[0], limit=_int_arg(args, 1, 5))
    if command == "research-fetch":
        if not args:
            raise ValueError("research-fetch requires URL")
        from kss.research.adapter import research_fetch  # noqa: PLC0415

        return research_fetch(args[0], max_chars=_int_arg(args, 1, 8000))
    if command == "research-bundle":
        if not args:
            raise ValueError("research-bundle requires QUERY")
        from kss.research.adapter import research_bundle  # noqa: PLC0415

        return research_bundle(
            args[0],
            limit=_int_arg(args, 1, 3),
            max_chars_per_source=_int_arg(args, 2, 3000),
        )
    if command == "news-digest":
        return _news_digest(
            args[0] if len(args) > 0 else "",
            args[1] if len(args) > 1 else "",
        )
    if command == "intel-radar":
        return _intel_radar(args[0] if args else "")
    if command == "intel-digest":
        return _intel_digest(args[0] if args else "")
    if command == "intel-panorama":
        return _intel_panorama(args[0] if args else "")
    if command == "intel-digest-save":
        return _intel_digest_save(args[0] if args else "")
    if command == "intel-article":
        return _intel_article(args[0] if args else "")
    if command == "intel-rewrite":
        return _intel_rewrite(args[0] if args else "")
    if command == "intel-rewrite-run":
        return _intel_rewrite_run(args[0] if args else "")
    if command == "longbridge-quote":
        return _longbridge_quote(args[0] if args else "")
    if command == "intraday-snapshot":
        return _intraday_snapshot(
            args[0] if args else "",
            interval_minutes=_int_arg(args, 1, 1),
        )
    if command == "intraday-bars":
        return _intraday_bars(
            args[0] if args else "",
            interval_minutes=_int_arg(args, 1, 1),
        )
    if command == "trading-hours":
        return _trading_hours()
    if command == "indicator-lab-list":
        return _indicator_lab_list()
    if command == "indicator-backtest":
        if not args:
            raise ValueError("indicator-backtest requires FAMILY")
        return _indicator_backtest(
            args[0],
            args[1] if len(args) > 1 else "",
            args[2] if len(args) > 2 else "",
        )
    if command == "indicator-suggest":
        return _indicator_suggest()
    if command == "indicator-solidify":
        if len(args) < 3:
            raise ValueError("indicator-solidify requires FAMILY PARAMS_JSON SYMBOLS_CSV")
        return _indicator_solidify(
            args[0], args[1], args[2], args[3] if len(args) > 3 else ""
        )
    if command == "indicator-retire":
        if not args:
            raise ValueError("indicator-retire requires ENTRY_ID")
        return _indicator_retire(args[0])
    if command == "datasource-test":
        if not args:
            raise ValueError("datasource-test requires SOURCE")
        return _datasource_test(args[0])
    if command == "log-list":
        return _log_list()
    if command == "log-tail":
        if not args:
            raise ValueError("log-tail requires NAME")
        lines = int(args[1]) if len(args) > 1 and args[1] else 500
        grep = args[2] if len(args) > 2 else ""
        return _log_tail(args[0], lines, grep)
    if command == "self-check":
        return _self_check()
    if command == "version":
        # 返回 sidecar 代码版本指纹，供 Swift 端校验陈旧进程。
        return {"version": _sidecar_version_fingerprint()}
    raise ValueError(f"unknown command: {command}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: kss_app_bridge.py snapshot|stock SYMBOL|report PATH|paper-summary|run TASK"
            "|cron-list|cron-sync|cron-rerun LABEL|cron-enable LABEL|cron-disable LABEL"
            "|cron-catchup|cron-rerun-many LABEL,LABEL"
            "|research-search QUERY|research-fetch URL|research-bundle QUERY",
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
