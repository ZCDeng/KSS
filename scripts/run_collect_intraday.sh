#!/bin/bash
# 分时收盘采集 - 每个交易日 15:05 launchd wrapper（U6 / KTD6 / 评审 S1）.
#
# 用 .venv-desktop 解释器（有 pyarrow，project-owned，不被 brew cleanup GC）—— 见 plan KTD7.
#
# 凭据纪律（评审 S1，刻意不抄 run_update_data_daily.sh:26-30 的 .env grep 习惯）：
#   - **不** grep 任何 .env 取 TUSHARE_TOKEN
#   - **不** export TUSHARE_TOKEN
#   - **不** echo 任何 token 相关值（长度 / 来源 / 等）
#   token 由采集器从 $KSS_STATE_ROOT/secrets/tushare_token（0600，git-ignored）读
#   （_resolve_token，KTD4）；本 wrapper 唯一注入/引用的 env 是 KSS_STATE_ROOT。
#
# KSS_STATE_ROOT 由 plist EnvironmentVariables 注入（不在此硬编码 state root；调和
# 静态 wrapper 约定与 bundle 双根）。WorkingDirectory / 解释器走 PROJECT_ROOT（code root）.
#
# 手动测试：KSS_STATE_ROOT=/Users/zcdeng/projects/KSS bash scripts/run_collect_intraday.sh --dry-run

set -e
set -o pipefail

PROJECT_ROOT="/Users/zcdeng/projects/KSS"
PYTHON="$PROJECT_ROOT/.venv-desktop/bin/python"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') collect_intraday 开始 ====="

# KSS_STATE_ROOT 由 plist 注入；缺省回落到 PROJECT_ROOT（dev / 手动运行兜底，
# 与 paths.py 的 STATE_ROOT 回落语义一致）。绝不在此读/导出任何 token。
if [ -z "${KSS_STATE_ROOT:-}" ]; then
  export KSS_STATE_ROOT="$PROJECT_ROOT"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/collect_intraday.py --mode close "$@"
