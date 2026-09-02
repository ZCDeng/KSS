#!/usr/bin/env bash
# 事件驱动链共享函数（plan 2026-07-14-001 / U1-U2, KTD1/KTD2/KTD3）。
#
# 供 EOD 与四个下游 wrapper source。三个能力：
#   kss_gate_or_exit <task>       —— 三态 gate：NOOP(3) 静默成功退出 / STALE(4) 响亮失败 / RUN 继续
#   kss_mark_done <task>          —— 任务成功后落完成标记（gate 的产物判定依据）
#   kss_kick_next <suffix>        —— 踢下一环。不带 -k（防腰斩运行中实例）；rc 局部捕获，
#                                    失败只记日志不影响本环退出码（KTD1 防污染细则）
#   kss_run_with_timeout <sec> …  —— 进程组超时守护（KTD3），超时 exit 124
#
# 依赖调用方已定义 PROJECT_ROOT 与 KSS_STATE_ROOT（各 wrapper 既有惯例）。
# data-root 必须是 cs_data 的写入根：bundle-mode 下 EOD 写 $KSS_STATE_ROOT，
# 若仍锚 PROJECT_ROOT，仓库里那份停更 CSV 会把目标日钉死，天天 NOOP（2026-08-14 事故）。

kss_chain_python() {
  # gate/timeout 是纯 stdlib 工具，系统 python3 即可；优先 wrapper 已解析的 $PYTHON。
  if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON:-}" ]; then
    echo "$PYTHON"
  else
    command -v python3
  fi
}

kss_gate_or_exit() {
  local task="$1"
  local py; py="$(kss_chain_python)"
  local rc=0
  "$py" "$PROJECT_ROOT/scripts/check_pipeline_gate.py" \
    --task "$task" --data-root "$KSS_STATE_ROOT" --state-root "$KSS_STATE_ROOT" || rc=$?
  case "$rc" in
    0) return 0 ;;                                        # RUN
    3) echo "[chain] $task: 目标日产物已在，no-op 退出"; exit 0 ;;   # NOOP＝合法成功
    4) echo "[chain] $task: 数据侧不完整/滞后，拒绝基于旧数据运行" >&2; exit 4 ;;
    *) echo "[chain] $task: gate 自身异常 rc=$rc" >&2; exit "$rc" ;;
  esac
}

kss_mark_done() {
  local task="$1"
  local py; py="$(kss_chain_python)"
  # 标记落盘失败不改判本环成败（产物已真实生成），只留日志。
  "$py" "$PROJECT_ROOT/scripts/check_pipeline_gate.py" \
    --task "$task" --action mark-done \
    --data-root "$KSS_STATE_ROOT" --state-root "$KSS_STATE_ROOT" \
    || echo "[chain] $task: mark-done 落盘失败（不影响本环结果）"
  return 0
}

kss_kick_next() {
  # KTD1 细则：不带 -k；rc 局部捕获，本环退出码只反映自身工作。
  local suffix="$1"
  local label="com.zcdeng.kss.${suffix}"
  local rc=0
  launchctl kickstart "gui/$(id -u)/${label}" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[chain] kickstart ${label} 已触发"
  else
    echo "[chain] kickstart ${label} rc=${rc}（失败不影响本环结果，下一环由兜底档/看门狗接管）"
  fi
  return 0
}

kss_run_with_timeout() {
  local seconds="$1"; shift
  local py; py="$(kss_chain_python)"
  "$py" "$PROJECT_ROOT/scripts/run_with_timeout.py" "$seconds" -- "$@"
}
