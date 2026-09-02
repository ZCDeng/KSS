#!/usr/bin/env python3
"""下游任务三态 gate（plan 2026-07-14-001 / U1, KTD2）。

事件驱动链的每个下游 wrapper（picks/mi/indicator/review）开跑前调用本工具判定：

- RUN (exit 0)        —— 数据新鲜且产物缺失，正常执行
- NOOP (exit 3)       —— 目标日产物已存在且完整（兜底档重复触发时静默让路）
- STALE_DATA (exit 4) —— 数据侧不完整/滞后，响亮失败，不得基于旧数据跑出成功

目标交易日从数据侧自锚（KTD2）：取固定 sentinel 标的的 cs_data `max(trade_date)`
的最大值为目标日，并要求全部 sentinel 都已写到该日（quorum）——部分池写入
（如 07-14 实测 85/115）判 STALE_DATA，而非静默用不完整横截面出产物。
`--data-root` 必须指向 cs_data 的写入根（bundle-mode = `$KSS_STATE_ROOT`）；
锚到仓库根会把目标日钉在停更的副本上，下游天天 NOOP（2026-08-14 事故）。
不从日历工作日推导：仓内离线假日表只到 2025，节假日 EOD 无新行时目标日
自然停在上一交易日、产物同日已在 → NOOP，误报被数据侧自锚天然消化。

产物判定用统一完成标记（storage/pipeline_markers/<task>_<date>.json，由
lib_cron_chain.sh 在任务成功后写入）：标记缺失或损坏（不可解析 JSON）均视为
「产物缺失」→ RUN——半途 kill 留下的残缺状态不会骗过 no-op 判定。

cs_data 日期为横杠格式（2026-07-13），与 parquet/etf_radar 紧凑格式是两套（既有坑）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from enum import Enum
from pathlib import Path
from typing import NamedTuple

# 固定 sentinel：高流动性个股 + ETF，覆盖沪深两市与 ETF 资产类。
# 全员应在每个交易日的 EOD 更新中出现新行；任一缺席即视为数据侧不完整。
DEFAULT_SENTINELS: tuple[str, ...] = (
    "688017.SH",
    "688008.SH",
    "300059.SZ",
    "159915.SZ",
)

EXIT_RUN = 0
EXIT_NOOP = 3
EXIT_STALE = 4


class GateDecision(Enum):
    RUN = "run"
    NOOP = "noop"
    STALE_DATA = "stale_data"


class GateResult(NamedTuple):
    decision: GateDecision
    target_day: str | None
    reason: str


def read_latest_trade_date(csv_path: Path) -> str | None:
    """cs_data csv 的最后一行 trade_date（横杠格式）；文件缺失/空/畸形返回 None。"""
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            last = None
            for row in csv.reader(f):
                if row:
                    last = row
        if not last or len(last) < 2:
            return None
        date = last[1].strip()
        # 简单形状校验：YYYY-MM-DD
        if len(date) == 10 and date[4] == "-" and date[7] == "-":
            return date
        return None
    except OSError:
        return None


def compute_target_day(latest_by_sentinel: dict[str, str | None]) -> tuple[str | None, list[str]]:
    """目标日 = 各 sentinel 最新日期的最大值；返回 (目标日, 未到位的 sentinel 列表)。"""
    dates = [d for d in latest_by_sentinel.values() if d]
    if not dates:
        return None, sorted(latest_by_sentinel)
    target = max(dates)
    lagging = sorted(sym for sym, d in latest_by_sentinel.items() if d != target)
    return target, lagging


def marker_state(marker_path: Path) -> str:
    """完成标记状态：absent / intact / corrupt。intact 要求 JSON 可解析且含 task 字段。"""
    if not marker_path.is_file():
        return "absent"
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "corrupt"
    if isinstance(payload, dict) and payload.get("task"):
        return "intact"
    return "corrupt"


def decide(
    target_day: str | None,
    lagging_sentinels: list[str],
    marker: str,
) -> GateResult:
    """三态判定纯函数（KTD2）。"""
    if target_day is None:
        return GateResult(GateDecision.STALE_DATA, None, "sentinel 数据文件全部缺失或不可读")
    if lagging_sentinels:
        return GateResult(
            GateDecision.STALE_DATA,
            target_day,
            f"数据侧不完整：{','.join(lagging_sentinels)} 未写到 {target_day}（部分池写入）",
        )
    if marker == "intact":
        return GateResult(GateDecision.NOOP, target_day, f"{target_day} 产物已存在且完整")
    # absent 或 corrupt 都视为缺失 → RUN（残缺标记不骗过 no-op）
    return GateResult(GateDecision.RUN, target_day, f"{target_day} 数据齐、产物缺失（marker={marker}）")


def run_gate(task: str, data_root: Path, state_root: Path, sentinels: tuple[str, ...]) -> GateResult:
    latest = {sym: read_latest_trade_date(data_root / f"cs_data_{sym.split('.')[0]}.csv") for sym in sentinels}
    target, lagging = compute_target_day(latest)
    marker = "absent"
    if target is not None:
        marker = marker_state(state_root / "storage" / "pipeline_markers" / f"{task}_{target}.json")
    return decide(target, lagging, marker)


def write_marker(task: str, data_root: Path, state_root: Path, sentinels: tuple[str, ...]) -> str | None:
    """任务成功后落完成标记（目标日与 gate 同源自锚）；返回目标日，无目标日返回 None。"""
    import datetime

    latest = {sym: read_latest_trade_date(data_root / f"cs_data_{sym.split('.')[0]}.csv") for sym in sentinels}
    target, _ = compute_target_day(latest)
    if target is None:
        return None
    marker_dir = state_root / "storage" / "pipeline_markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / f"{task}_{target}.json").write_text(
        json.dumps({
            "task": task,
            "target_day": target,
            "completed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="任务名（picks/mi_signal/indicator_signal/review）")
    parser.add_argument("--action", choices=("check", "mark-done", "target-day"), default="check",
                        help="check=三态判定（默认）；mark-done=成功后落完成标记；"
                             "target-day=只打印目标交易日（供落盘校验取参照系）")
    parser.add_argument("--data-root", default=".", help="cs_data csv 所在目录（bundle-mode 必须是 KSS_STATE_ROOT）")
    parser.add_argument("--state-root", default=".", help="KSS_STATE_ROOT（pipeline_markers 落点）")
    parser.add_argument("--sentinels", default=",".join(DEFAULT_SENTINELS),
                        help="逗号分隔 sentinel 列表（默认内置四只）")
    args = parser.parse_args()

    sentinels = tuple(s.strip() for s in args.sentinels.split(",") if s.strip())

    if args.action == "target-day":
        latest = {sym: read_latest_trade_date(Path(args.data_root) / f"cs_data_{sym.split('.')[0]}.csv")
                  for sym in sentinels}
        target, _ = compute_target_day(latest)
        if target is None:
            return 1
        print(target)
        return 0

    if args.action == "mark-done":
        target = write_marker(args.task, Path(args.data_root), Path(args.state_root), sentinels)
        if target is None:
            print(f"[gate] task={args.task} mark-done 失败：sentinel 数据全部缺失", file=sys.stderr)
            return 1
        print(f"[gate] task={args.task} 完成标记 {target}")
        return 0

    result = run_gate(args.task, Path(args.data_root), Path(args.state_root), sentinels)
    print(f"[gate] task={args.task} decision={result.decision.value} "
          f"target={result.target_day} reason={result.reason}")
    return {
        GateDecision.RUN: EXIT_RUN,
        GateDecision.NOOP: EXIT_NOOP,
        GateDecision.STALE_DATA: EXIT_STALE,
    }[result.decision]


if __name__ == "__main__":
    sys.exit(main())
