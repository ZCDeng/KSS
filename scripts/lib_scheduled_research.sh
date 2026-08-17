# 投资分析日/周报共用的签名调度助手定位（Keychain credential broker）。
#
# launchd 不能把模型 API key 写进 plist。签名 Swift helper 从 Keychain 读出后
# 经一次性 Unix socket 交给 Python runner。若误用 .build 里 ad-hoc 签名的
# 开发版二进制，SecItemCopyMatching 会卡住等 GUI 授权，pi-ai helper 5 秒超时，
# goal 被标成 waiting_user / credential_*。
#
# 解析顺序：
#   1. 显式 KSS_SCHEDULED_RESEARCH_HELPER
#   2. 已安装的签名 app（/Applications 与 ~/Applications）
#   3. 仓库旁 Helpers/、scripts/ 下的拷贝
#   4. .build 开发产物（仅当上面都没有团队签名版本时回退，并打警告）
#
# 用法：
#   source "$PROJECT_ROOT/scripts/lib_scheduled_research.sh"
#   HELPER="$(kss_find_scheduled_research_helper "$PROJECT_ROOT")"

kss_helper_is_team_signed() {
  local path="$1"
  local info
  info="$(/usr/bin/codesign -dv --verbose=2 "$path" 2>&1)" || return 1
  case "$info" in
    *adhoc*) return 1 ;;
  esac
  case "$info" in
    *TeamIdentifier=not\ set*) return 1 ;;
    *TeamIdentifier=*) return 0 ;;
  esac
  return 1
}

kss_find_scheduled_research_helper() {
  local project_root="${1:?project_root required}"
  local sibling_helpers candidate fallback=""
  sibling_helpers="$(cd "$project_root/.." 2>/dev/null && pwd)/Helpers/KSSResearchSchedulerHelper"

  if [ -n "${KSS_SCHEDULED_RESEARCH_HELPER:-}" ] && [ -x "$KSS_SCHEDULED_RESEARCH_HELPER" ]; then
    printf '%s\n' "$KSS_SCHEDULED_RESEARCH_HELPER"
    return 0
  fi

  for candidate in \
    "/Applications/KSSDesktop.app/Contents/Helpers/KSSResearchSchedulerHelper" \
    "$HOME/Applications/KSSDesktop.app/Contents/Helpers/KSSResearchSchedulerHelper" \
    "$sibling_helpers" \
    "$project_root/scripts/KSSResearchSchedulerHelper" \
    "$project_root/.build/arm64-apple-macosx/release/KSSResearchSchedulerHelper" \
    "$project_root/.build/arm64-apple-macosx/debug/KSSResearchSchedulerHelper" \
    "$project_root/.build/release/KSSResearchSchedulerHelper" \
    "$project_root/.build/debug/KSSResearchSchedulerHelper"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if kss_helper_is_team_signed "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
    if [ -z "$fallback" ]; then
      fallback="$candidate"
    fi
  done

  if [ -n "$fallback" ]; then
    echo "scheduled research helper is not team-signed; using $fallback (Keychain reads may block)" >&2
    printf '%s\n' "$fallback"
    return 0
  fi
  return 1
}
