from __future__ import annotations

import re
from pathlib import Path
from textwrap import shorten

from .rank import rank
from .temporal_decay import parse_date_from_filename, timestamp_ms_for_date
from .types import Candidate

TOP_K = 5
_PERSYMBOL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_.+\.md$")
_SCENARIO_LABELS = {
    "A_break": "强势突破上行",
    "B_up": "温和上涨",
    "C_flat": "横盘震荡",
    "D_down": "温和回落",
    "E_break": "大跌破位",
}


def glob_symbol_reviews(ts_code: str, archive_dir: Path) -> list[Path]:
    if not archive_dir.exists():
        return []
    matches = [
        path for path in archive_dir.glob(f"*_{ts_code}.md")
        if _PERSYMBOL_RE.match(path.name) and parse_date_from_filename(path.name)
    ]
    return sorted(matches)


def extract_thesis_snippet(md_text: str, *, fallback_lines: int = 8) -> str:
    lines = md_text.splitlines()
    picked: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("形态:") or "  形态:" in line:
            picked.append(stripped)
            break

    advice_idx = next((i for i, line in enumerate(lines) if "*建议*" in line), None)
    if advice_idx is not None:
        picked.append(lines[advice_idx].strip())
        for line in lines[advice_idx + 1:]:
            stripped = line.strip()
            if not stripped:
                break
            if stripped.startswith("•") or stripped.startswith("-") or stripped.startswith("📈") or stripped.startswith("⚠️"):
                picked.append(stripped)
                continue
            if picked and not line.startswith(" "):
                break

    compact = _compact_lines(picked)
    if compact:
        return compact

    fallback: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        if stripped.startswith("🌐") or stripped.startswith("_自动生成"):
            continue
        fallback.append(stripped)
        if len(fallback) >= fallback_lines:
            break
    return _compact_lines(fallback)


def build_history_recap(
    ts_code: str,
    today_features: str,
    archive_dir: Path,
    *,
    now_ms: int,
    k: int = TOP_K,
    exclude_date: str | None = None,
) -> list[str]:
    candidates: list[Candidate] = []
    for path in glob_symbol_reviews(ts_code, archive_dir):
        dt = parse_date_from_filename(path.name)
        if dt is None:
            continue
        date_str = dt.strftime("%Y-%m-%d")
        if exclude_date and date_str == exclude_date:
            continue
        snippet = extract_thesis_snippet(path.read_text(encoding="utf-8"))
        if not snippet:
            continue
        candidates.append(Candidate(
            id=date_str,
            text=snippet,
            timestamp_ms=timestamp_ms_for_date(dt),
        ))

    selected = rank(candidates, query=today_features, now_ms=now_ms, top_k=k)
    if not selected:
        return []

    lines = ["  *近期复盘演变* (待验证先验)"]
    for item in selected:
        text = sanitize_validator_anchors(item.text)
        lines.append(f"     · {item.id}: {shorten(text, width=120, placeholder='...')}")
    lines.append("     ↑ 以上为过去判断，请用今日数据重新验证，变化点优先")
    return lines


def today_features_from_stock(stock: dict) -> str:
    flags = []
    flags.append("MACD极值" if stock.get("p1") else "非MACD极值")
    flags.append("量能极值" if stock.get("p2") else "非量能极值")
    flags.append("连续大阳" if stock.get("p3") else "非连续大阳")
    category = stock.get("category") or ""
    basis = stock.get("scenario_basis") or ""
    scenario = stock.get("scenario_adj") or stock.get("scenario") or {}
    scenario_items = [(key, val) for key, val in scenario.items() if key in _SCENARIO_LABELS]
    if scenario_items:
        lead_key, _ = max(scenario_items, key=lambda item: item[1])
        flags.append(_SCENARIO_LABELS[lead_key])
    flags.extend(part for part in [category, basis] if part)
    return " ".join(flags)


def _compact_lines(lines: list[str]) -> str:
    cleaned = [re.sub(r"\s+", " ", line).strip() for line in lines if line.strip()]
    return " | ".join(cleaned)


def sanitize_validator_anchors(text: str) -> str:
    sanitized = re.sub(r"止损位\s+\*([\d.]+)\*", r"历史止损 \1", text)
    sanitized = re.sub(r"收盘 50% 概率落 \*([\d.]+) ~ ([\d.]+)\*", r"历史50%区间 \1 ~ \2", sanitized)
    sanitized = re.sub(r"极端 80% 区间 ([\d.]+) ~ ([\d.]+)", r"历史80%区间 \1 ~ \2", sanitized)
    return sanitized
