#!/bin/bash
# 分时收盘采集 - 每个交易日 15:05 launchd wrapper（U6 / KTD6 / 评审 S1）.
#
# 用 .venv-desktop 解释器（有 pyarrow，project-owned，不被 brew cleanup GC）—— 见 plan KTD7.
#
# 凭据纪律（评审 S1）：
#   - **不** grep 任何 .env 取 TUSHARE_TOKEN
#   - **不** 手写 export <凭据名>=<值>；凭据一律经 kss_load_credential 走 Keychain 优先链
#   - **不** echo 任何 token 相关值（长度 / 来源 / 等）
#   token 由采集器从 $KSS_STATE_ROOT/secrets/tushare_token（0600，git-ignored）读
#   （_resolve_token，KTD4）。
#   本 wrapper 注入/引用的 env：KSS_STATE_ROOT、no_proxy/NO_PROXY（代理兜底，非凭据），
#   以及 6387d339 起经 kss_load_credential 装入的 LONGBRIDGE_APP_KEY /
#   LONGBRIDGE_APP_SECRET / LONGBRIDGE_ACCESS_TOKEN 三件套（值不落 wrapper 正文）。
#
# KSS_STATE_ROOT 由 plist EnvironmentVariables 注入（不在此硬编码 state root；调和
# 静态 wrapper 约定与 bundle 双根）。WorkingDirectory / 解释器走 PROJECT_ROOT（code root）.
#
# 手动测试：KSS_STATE_ROOT="$(pwd)" bash scripts/run_collect_intraday.sh --dry-run

set -e
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${KSS_STATE_ROOT:=$PROJECT_ROOT}"
# 显式导出：launchd 场景 plist 会注入 env，但手动/链式运行时默认值只是 shell 变量，
# 采集器（python 子进程）读不到 → secrets/tushare_token 解析落空（R3-U3 实测坑）。
export KSS_STATE_ROOT

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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') collect_intraday 开始 ====="

# 代理兜底：cron 时刻 Clash（127.0.0.1:7890）可能抖断，Tushare trade_cal 走
# HTTP 明文会被代理拦截导致 calendar_unknown 失败。显式把 tushare 域名加进
# no_proxy，确保即使系统代理开着也直连。TushareClient._bypass_system_proxy()
# 也会在 Python 侧做同样的事，但 launchd 注入的环境变量在 shell 层更可靠。
export no_proxy="api.tushare.pro,api.waditu.com,${no_proxy:-}"
export NO_PROXY="$no_proxy"

# auto 路由优先 longbridge：cron 无 App 注入 env，须从 Keychain 装凭据（否则永远 auth_failed → 东财）。
# 东财为 fallback；Tushare 仅 trade_cal。不 echo 任何密钥。
# shellcheck source=scripts/lib_cron_credentials.sh
source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
_lb_n=0
for _k in LONGBRIDGE_APP_KEY LONGBRIDGE_APP_SECRET LONGBRIDGE_ACCESS_TOKEN; do
    if kss_load_credential "$_k"; then
        _lb_n=$((_lb_n + 1))
    fi
done
if [ "$_lb_n" -eq 3 ]; then
    echo "[wrapper] longbridge creds: keychain/env ok"
else
    echo "[wrapper] longbridge creds: incomplete ($_lb_n/3) — auto 将更多走东财 fallback"
fi
cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/collect_intraday.py --mode close "$@"
