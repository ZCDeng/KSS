#!/bin/bash
# 板块热点轮动归档 - 每个交易日 17:50 收盘后 cron/launchd wrapper.
#
# 目的：每日把当天的板块热点轮动快照写入 storage/sector_rotation/YYYYMMDD.json，
# 逐日累积历史，喂养四象限分类 / Top3 频次 / streak / 龙头 persistence（妖王榜）。
# 与 run_sector_review_daily.sh 对齐的包装纪律：
#   1. cron/launchd 不读 zshrc → 自己从 .env 显式取 TUSHARE_TOKEN
#   2. 用 Homebrew Python 绝对路径，避免 PATH 不一致
#   3. 失败返回非零，交给系统监控
#
# 手动测试：
#   bash scripts/run_hotspot_rotation_daily.sh                  # 拉 latest 交易日
#   bash scripts/run_hotspot_rotation_daily.sh --date 20260618  # 指定日期
#
# launchd 部署：每个交易日 17:50（晚于 17:30 板块复盘，等 Tushare pro 数据 buffer）。

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -n "${KSS_PYTHON:-}" ]; then
    PYTHON="$KSS_PYTHON"
elif [ -x "$HOME/Library/Application Support/KSS/venv/bin/python3" ]; then
    PYTHON="$HOME/Library/Application Support/KSS/venv/bin/python3"
elif [ -x "$PROJECT_ROOT/.venv-desktop/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"
else
    echo "no usable python interpreter found (checked KSS_PYTHON, state-root venv, .venv-desktop)" >&2
    exit 1
fi
KSS_ENV="$PROJECT_ROOT/.env"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') hotspot_rotation_daily 开始 ====="

# 只 grep 需要的变量（.env 可能混有特殊字符行，整体 source 会炸）。
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
kss_load_credential TUSHARE_TOKEN "$KSS_ENV" || true

cd "$PROJECT_ROOT"

# --enable-leaders：累积板块龙头 persistence（喂「概念主题龙头」页与妖王榜）。
# 不开 --enable-kaipan：KAIPAN 外部源不稳定，缺它仍能产出基础快照，避免整任务失败。
# refresh 脚本 --date 默认 latest；额外参数（如手动 --date 20260618）经 "$@" 透传。
"$PYTHON" scripts/refresh_hotspot_rotation.py --enable-leaders "$@"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') hotspot_rotation_daily 完成 ====="
