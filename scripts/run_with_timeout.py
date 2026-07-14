#!/usr/bin/env python3
"""子进程组超时守护（plan 2026-07-14-001 / U1, KTD3）。

macOS 无 coreutils timeout；bridge 的 subprocess timeout 只杀直接子进程，
不满足「kill 进程树」需求（07-14 悬空事故：下游任务撞 DNS 断网期挂死数小时）。
本工具用 start_new_session 起独立进程组，超时 killpg 整组、exit 124 留痕。

用法：run_with_timeout.py <seconds> -- <cmd> [args...]
退出码：子进程退出码原样透传；超时=124；用法错误=2。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

EXIT_TIMEOUT = 124


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[2] != "--":
        print("usage: run_with_timeout.py <seconds> -- <cmd> [args...]", file=sys.stderr)
        return 2
    try:
        limit = float(argv[1])
    except ValueError:
        print(f"invalid timeout: {argv[1]!r}", file=sys.stderr)
        return 2
    cmd = argv[3:]
    if not cmd:
        print("missing command after --", file=sys.stderr)
        return 2

    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        return proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        print(f"[timeout-guard] 超时 {limit:.0f}s，killpg 进程组 pgid={proc.pid}: {' '.join(cmd)}",
              file=sys.stderr, flush=True)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        except ProcessLookupError:
            pass
        return EXIT_TIMEOUT


if __name__ == "__main__":
    sys.exit(main(sys.argv))
