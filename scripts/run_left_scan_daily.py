#!/usr/bin/env python3
"""Ingest today's 左侧机会扫描 file as the investment-analysis daily report."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kss.research.left_scan import (  # noqa: E402
    LeftScanError,
    convert_to_fragment,
    dated_candidates,
    pick_scan_file,
    resolve_trade_date,
    trade_date_from_name,
)
from kss.research.left_scan_fetch import (  # noqa: E402
    ScanCandidate,
    collect_candidates,
    download_candidate,
)
from kss.research.left_scan_ingest import ingest_left_scan_report  # noqa: E402
from kss.research.service import ResearchService  # noqa: E402


def _load_env(project_root: Path, state_root: Path) -> None:
    for env_path in (project_root / ".env", Path(state_root) / "network.env"):
        if not env_path.is_file():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drive/local 左侧机会扫描 → 投资分析日报")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--state-root", default=os.environ.get("KSS_STATE_ROOT") or str(PROJECT_ROOT))
    parser.add_argument("--trade-date", help="YYYY-MM-DD；缺省按当天（20:00 前可回退昨天）")
    parser.add_argument("--source-file", help="跳过发现，直接解析这个本地文件")
    return parser.parse_args(argv)


def _candidate_from_path(path: Path) -> ScanCandidate:
    stamp = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    suffix = path.suffix.lower()
    mime = "application/pdf" if suffix == ".pdf" else "text/html"
    return ScanCandidate(
        name=path.name,
        mime=mime,
        modified=stamp,
        size=path.stat().st_size,
        path=str(path),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(args.project_root).resolve()
    state_root = Path(args.state_root).resolve()
    _load_env(project_root, state_root)

    if args.source_file:
        source = Path(args.source_file).expanduser().resolve()
        if not source.is_file():
            print(f"source file not found: {source}", file=sys.stderr)
            return 2
        candidate = _candidate_from_path(source)
        trade = (
            date.fromisoformat(args.trade_date)
            if args.trade_date
            else trade_date_from_name(source.name) or date.today()
        )
        data = source.read_bytes()
    else:
        try:
            candidates = collect_candidates(state_root)
        except LeftScanError as exc:
            print(f"left-scan discovery failed: {exc}", file=sys.stderr)
            return 2
        if args.trade_date:
            trade = date.fromisoformat(args.trade_date)
        else:
            trade = resolve_trade_date(datetime.now(), dated_candidates(candidates))
        try:
            candidate = pick_scan_file(candidates, trade)
        except LeftScanError as exc:
            print(f"waiting_user: {exc}", file=sys.stderr)
            return 2
        try:
            data = download_candidate(candidate, state_root=state_root)
        except LeftScanError as exc:
            print(f"left-scan download failed: {exc}", file=sys.stderr)
            return 2

    try:
        fragment = convert_to_fragment(candidate.name, candidate.mime, data)
    except LeftScanError as exc:
        print(f"left-scan convert failed: {exc}", file=sys.stderr)
        return 1

    service = ResearchService(state_root=state_root, project_root=project_root)
    result = ingest_left_scan_report(
        service,
        fragment=fragment,
        trade_date=trade,
        source_name=candidate.name,
    )
    if not result.get("ok"):
        print(f"left-scan ingest failed: {result}", file=sys.stderr)
        return 1
    print(
        f"{result.get('event')} goal={result.get('goal_id')} "
        f"date={result.get('trade_date')} source={candidate.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
