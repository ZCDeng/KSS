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
# --enable-kaipan：2026-08-05 排查实测，duanxianxia 的 getLongByPlate 只支持 kaipan
#   代码段（80xxxx），ths 代码段（88xxxx）全部返回"当日无领涨"。不开 kaipan 则
#   name_to_code 只有 ths 代码 → leaderStocks 全 None → leaderCoverage=0 →
#   style_sector_rotation 因子缺失。kaipan 外部源实测稳定，必须开。
# refresh 脚本 --date 默认 latest；额外参数（如手动 --date 20260618）经 "$@" 透传。
"$PYTHON" scripts/refresh_hotspot_rotation.py --enable-kaipan --enable-leaders "$@"

# 链式触发信号卡日终（plan 2026-07-28-002 / U6）：hotspot 成功后 kick signal_cards_daily。
# 显式传参（回填/定向）时不 kick，避免干扰手动跑。
if [ "$#" -eq 0 ]; then
  : "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/scripts/lib_cron_chain.sh"
  kss_kick_next signal_cards_daily
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') hotspot_rotation_daily 完成 ====="
