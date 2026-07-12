# cron/launchd 凭证加载共享 helper（plan 2026-07-12-005 / U11, KTD9）。
#
# 交付机器上没有 .env 可 grep——Keychain 是 cron 短进程唯一凭证源（U3 起密钥统一存
# service=com.zcdeng.KSSDesktop.credentials，账户名 == 环境变量名）。解析顺序：
#   1. Keychain（`security find-generic-password`）
#   2. dev 模式 .env 回落（本机作者 checkout 场景，Keychain 未写入时仍可跑）
#
# 用法（wrapper 内 source 后逐 key 调用）：
#   source "$PROJECT_ROOT/scripts/lib_cron_credentials.sh"
#   kss_load_credential TUSHARE_TOKEN "$PROJECT_ROOT/.env"
#   kss_load_credential TELEGRAM_BOT_TOKEN "$PROJECT_ROOT/.env"
# 成功解析且非空 -> export 同名变量并 return 0；否则不 export、return 1（调用方按需 WARN）。

kss_load_credential() {
  local key="$1"
  local env_file="${2:-}"
  local val=""

  if command -v security >/dev/null 2>&1; then
    val="$(security find-generic-password -s com.zcdeng.KSSDesktop.credentials -a "$key" -w 2>/dev/null || true)"
  fi

  if [ -z "$val" ] && [ -n "$env_file" ] && [ -f "$env_file" ]; then
    val="$( (grep -E "^${key}=" "$env_file" || true) | head -1 | cut -d= -f2- | sed 's/^"//;s/"$//')"
  fi

  if [ -n "$val" ]; then
    export "${key?}=${val}"
    return 0
  fi
  return 1
}
