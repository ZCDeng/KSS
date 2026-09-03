"""紫苏叶产业链标注动态更新器.

Phase 3: 把静态 YAML 中 analyst_count 替换为 Tushare report_rc 动态数据,
并在结构标注过期时发出告警. 结构标注与分析师覆盖使用两套更新时间，
避免只刷新动态字段就把人工产业链判断伪装成已经复核.

用法:
    python -m kss.supply_chain.updater              # 更新全部标注的 analyst_count
    python -m kss.supply_chain.updater --check-only  # 仅检查过期状态, 不修改

设计约束:
    - 只更新 analyst_count, 不碰 chain_layer / n_competitors / demand_locked (人工字段)
    - Tushare API 失败时保留原值 + warning, 不清零
    - 原子写: 先写 .tmp → os.replace, 防止写坏 YAML
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "supply_chain.yaml"
# 标注过期阈值: 超过 N 天未更新 → 告警
_STALENESS_DAYS = 90


def count_analyst_coverage(
    ts_code: str,
    lookback_days: int = 365,
) -> int | None:
    """统计最近 N 天覆盖该股的独立券商机构数.

    排除 ``report_type='非个股'`` 的行业报告, 只计个股级覆盖.

    Args:
        ts_code: 股票代码, 如 ``688012.SH``.
        lookback_days: 回溯天数, 默认 365.

    Returns:
        独立机构数; Tushare 不可用或无数据时返回 ``None``.
    """
    try:
        from kss.data.tushare_client import TushareClient
    except ImportError:
        logger.warning("TushareClient 不可用, 跳过 analyst_count 更新")
        return None

    end = datetime.now()
    start = end - timedelta(days=lookback_days)
    end_str = end.strftime("%Y%m%d")
    start_str = start.strftime("%Y%m%d")

    client = TushareClient()
    df = client.fetch_report_rc(ts_code, start_str, end_str)
    if df is None or df.empty:
        return None

    # 排除行业报告, 只计个股级研报
    individual = df[df["report_type"] != "非个股"]
    if individual.empty:
        return 0

    return int(individual["org_name"].nunique())


def check_staleness(yaml_path: Path | None = None) -> dict[str, Any]:
    """检查 YAML 中人工结构标注是否过期.

    ``structural_updated`` 是新字段；旧配置继续读取 ``updated``。动态更新器
    写入的 ``analyst_updated`` 只描述研报覆盖口径，不参与结构新鲜度判断.

    Returns:
        包含结构/分析师日期、结构年龄和过期状态的字典.
    """
    p = yaml_path or _CONFIG_PATH
    if not p.exists():
        return {
            "updated": None,
            "structural_updated": None,
            "analyst_updated": None,
            "age_days": -1,
            "is_stale": True,
            "threshold_days": _STALENESS_DAYS,
            "stocks_count": 0,
        }

    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    structural_updated = raw.get("structural_updated") or raw.get("updated", "")
    analyst_updated = raw.get("analyst_updated")
    stocks_count = len(raw.get("stocks") or {})

    try:
        updated_dt = datetime.strptime(str(structural_updated), "%Y-%m-%d")
        age_days = (datetime.now() - updated_dt).days
    except (ValueError, TypeError):
        age_days = 999

    return {
        # ``updated`` 保留给既有调用方，语义固定为结构标注日期。
        "updated": structural_updated,
        "structural_updated": structural_updated,
        "analyst_updated": analyst_updated,
        "age_days": age_days,
        "is_stale": age_days > _STALENESS_DAYS,
        "threshold_days": _STALENESS_DAYS,
        "stocks_count": stocks_count,
    }


def refresh_analyst_counts(
    yaml_path: Path | None = None,
    dry_run: bool = False,
    sleep_between: float = 0.5,
) -> dict[str, Any]:
    """拉 Tushare 更新全部标注股票的 analyst_count.

    Args:
        yaml_path: 覆盖默认路径 (测试用).
        dry_run: ``True`` 时只打印不写文件.
        sleep_between: 每只票之间的休眠秒数, 避免触发 Tushare 限频.

    Returns:
        ``{"total": int, "updated": int, "failed": int, "changes": list}``.
    """
    import time

    p = yaml_path or _CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    stocks = raw.get("stocks") or {}
    total = len(stocks)
    updated_count = 0
    failed_count = 0
    changes: list[dict[str, Any]] = []

    for ts_code, info in stocks.items():
        old_val = info.get("analyst_count")
        new_val = count_analyst_coverage(ts_code)

        if new_val is None:
            failed_count += 1
            logger.warning("%s: Tushare 拉取失败, 保留原值 %s", ts_code, old_val)
            continue

        if new_val != old_val:
            changes.append({
                "ts_code": ts_code,
                "name": info.get("name", ""),
                "old": old_val,
                "new": new_val,
            })
            info["analyst_count"] = new_val
            updated_count += 1
        time.sleep(sleep_between)

    if not dry_run and updated_count > 0:
        raw["analyst_updated"] = datetime.now().strftime("%Y-%m-%d")
        _atomic_write_yaml(p, raw)
        logger.info("已更新 %d/%d 只的 analyst_count", updated_count, total)

    return {
        "total": total,
        "updated": updated_count,
        "failed": failed_count,
        "changes": changes,
    }


def check_demand_chain_health(
    regime_stage: str | None,
    prev_regime_stage: str | None = None,
    yaml_path: Path | None = None,
) -> list[dict[str, str]]:
    """当 regime 阶段切换时, 检查 demand_locked=True 的票是否需要复查.

    规则:
    - II→IV (扩张→衰退): 所有 demand_locked=True 的票触发 warning
    - III→IV (顶部→衰退): 同上
    - 其他切换: 不触发

    Returns:
        告警列表 ``[{"ts_code": ..., "name": ..., "reason": ...}]``.
    """
    # 只在特定方向的阶段切换时告警
    risky_transitions = {
        ("II", "IV"), ("III", "IV"), ("II", "III"),
        ("I", "IV"),  # 极端: 从谷底直接跳衰退 (罕见但可能数据波动)
    }
    if not regime_stage or not prev_regime_stage:
        return []
    if (prev_regime_stage, regime_stage) not in risky_transitions:
        return []

    p = yaml_path or _CONFIG_PATH
    if not p.exists():
        return []

    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    alerts = []
    for ts_code, info in (raw.get("stocks") or {}).items():
        if info.get("demand_locked"):
            alerts.append({
                "ts_code": ts_code,
                "name": info.get("name", ""),
                "reason": (
                    f"宏观阶段 {prev_regime_stage}→{regime_stage}, "
                    f"demand_locked=True 但环境恶化, 复查 "
                    f"{','.join(info.get('demand_chains', []))} 需求链"
                ),
            })
    return alerts


def _atomic_write_yaml(path: Path, data: dict) -> None:
    """原子写 YAML: 先写 tmp, 再 os.replace."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".yaml", dir=path.parent, prefix=".supply_chain_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                      sort_keys=False, width=120)
        os.replace(tmp_path, path)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------- #
# CLI 入口
# ---------------------------------------------------------------------- #


def main() -> None:
    """命令行: 更新 analyst_count 或检查过期状态."""
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="紫苏叶产业链标注动态更新")
    parser.add_argument("--check-only", action="store_true",
                        help="仅检查过期状态, 不修改 YAML")
    parser.add_argument("--dry-run", action="store_true",
                        help="拉数据但不写文件, 仅打印变更")
    args = parser.parse_args()

    # 1. 过期检查
    staleness = check_staleness()
    if staleness["is_stale"]:
        print(f"⚠️  supply_chain.yaml 已过期 ({staleness['age_days']} 天, "
              f"阈值 {staleness['threshold_days']} 天)")
    else:
        print(f"✓ supply_chain.yaml 更新于 {staleness['updated']} "
              f"({staleness['age_days']} 天前, {staleness['stocks_count']} 只)")

    if args.check_only:
        sys.exit(1 if staleness["is_stale"] else 0)

    # 2. 刷新 analyst_count
    print(f"\n[*] 从 Tushare report_rc 更新 analyst_count ...")
    result = refresh_analyst_counts(dry_run=args.dry_run)
    print(f"    总计 {result['total']} 只, "
          f"更新 {result['updated']} 只, 失败 {result['failed']} 只")

    if result["changes"]:
        print("\n    变更明细:")
        for c in result["changes"]:
            direction = "↑" if c["new"] > c["old"] else "↓"
            print(f"    {c['ts_code']} {c['name']}: "
                  f"{c['old']} → {c['new']} {direction}")

    if args.dry_run:
        print("\n    (dry-run 模式, 未写入文件)")


if __name__ == "__main__":
    main()
