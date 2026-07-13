#!/usr/bin/env python3
"""cron 任务日志轮转（plan 2026-07-12-005 / U7 KTD10）：storage/logs/cron/*.log
超 5MB 轮转（保留 3 代，同 sidecar 日志纪律）；轮转代（.1/.2/.3）超 14 天直接清除。
日终 cron 任务触发，纯 stdlib，幂等可重跑。

手动测试：python3 scripts/rotate_cron_logs.py [STATE_ROOT]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROTATE_THRESHOLD_BYTES = 5 * 1024 * 1024
_KEEP_GENERATIONS = 3
_PRUNE_AGE_DAYS = 14


def _rotate_one(log_path: Path) -> bool:
    """单文件轮转：超阈值才动，未超返回 False（幂等）。"""
    if not log_path.is_file() or log_path.stat().st_size <= _ROTATE_THRESHOLD_BYTES:
        return False
    oldest = log_path.with_name(log_path.name + f".{_KEEP_GENERATIONS}")
    oldest.unlink(missing_ok=True)
    for gen in range(_KEEP_GENERATIONS - 1, 0, -1):
        src = log_path.with_name(log_path.name + f".{gen}")
        dst = log_path.with_name(log_path.name + f".{gen + 1}")
        if src.is_file():
            src.rename(dst)
    log_path.rename(log_path.with_name(log_path.name + ".1"))
    log_path.touch()  # 下次 cron 运行直接续写新空文件，wrapper 不用改
    return True


def _prune_old_generations(cron_dir: Path) -> int:
    """轮转代（*.log.N）超 14 天直接删——只清数字后缀的轮转代，原始 .log 不动。"""
    cutoff = time.time() - _PRUNE_AGE_DAYS * 86400
    pruned = 0
    for p in cron_dir.glob("*.log.*"):
        if not p.is_file():
            continue
        try:
            int(p.suffix.lstrip("."))
        except ValueError:
            continue  # 非 .N 形态（理论不该出现），跳过不动
        if p.stat().st_mtime < cutoff:
            p.unlink()
            pruned += 1
    return pruned


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    state_root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    cron_dir = state_root / "storage" / "logs" / "cron"
    if not cron_dir.is_dir():
        print(f"no cron log dir: {cron_dir}")
        return 0
    rotated = sum(1 for p in sorted(cron_dir.glob("*.log")) if _rotate_one(p))
    pruned = _prune_old_generations(cron_dir)
    print(f"rotated={rotated} pruned={pruned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
