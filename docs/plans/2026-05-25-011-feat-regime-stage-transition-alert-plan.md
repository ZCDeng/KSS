---
title: "feat: Telegram 阶段切换告警（regime stage flip）"
status: pending
created: 2026-05-25
type: feat
depth: light
---

## Summary

plan 002 §Deferred 第 3 条："阶段切换告警 Telegram 推送（先静默上线，避免
误报骚扰）"。第 11 轮跑 60 日数据后，hit-rate 报告（plan 008）确认分类
质量后开启。本 plan：每日 update_macro_daily 后比较前后两日 stage_smoothed，
若发生切换（含 `Unknown→X`、`X→Y`）→ 推 Telegram 告警，附阶段含义 + 建议动作.

---

## Problem Frame

**现状**：
- regime_daily.parquet 每日刷新，含 stage_smoothed (hysteresis 后)
- 阶段切换时 scan_combo_signals 自动调 entry 数（regime III 砍半等），但用户没有事件通知
- 重大切换（如 IV→I 谷底反转、II→III 顶部预警）应当主动 push，不应等用户次日跑 scan 才看到

**目标**：
- 每日 update_macro_daily 末段加 `_check_stage_transition_and_alert()`
- 检测 stage_smoothed 与前日不同 → 推 Telegram
- 消息含：日期 / 切换方向 / 新阶段含义 / 推荐操作
- `--skip-alert` flag 可关

**非目标**：
- 不每日推（只切换日推）
- 不推 Unknown→Unknown（无信息量）
- 不推 confidence 变化（只 stage 变）

---

## Scope Boundaries

### In-Scope

- **新函数** `kss/macro/queries.py::detect_stage_transition(yesterday, today) -> StageTransition | None`
  - StageTransition dataclass: from_stage, to_stage, confidence_delta, as_of_date

- **新模板** `kss/notifications/templates/regime_transition.py`:
  ```
  🔄 宏观阶段切换：[from] → [to]
  日期：YYYY-MM-DD
  置信度：X.XX
  含义：[to 阶段一句话]
  建议：[基于 plan 003 部门轮换 + plan 004 估值规则]
  ```

- **新增 update_macro_daily.py**:
  - `--skip-alert` flag
  - main() 末段调 `_check_stage_transition_and_alert()`
  - 读 yesterday + today 两行，detect，推送，写 sentinel 文件防重复

- **去重机制**:
  - `storage/macro/regime_alert_sentinel.txt` 记录最后推送的 (date, from, to)
  - 同日多次跑 update_macro_daily 不重复推

- **单测** `kss/tests/test_regime_transition.py` (8+ cases):
  - I→II 触发推送
  - II→II 不推送
  - Unknown→I 触发推送（首次有效信号）
  - I→Unknown 不推送（降级到 Unknown 不视为切换）
  - sentinel 防重复

### Deferred

- 切换前 N 日预警（"可能即将进入 III"，需 confidence 趋势监测）
- 跨平台通知（钉钉 / 飞书）—— 走 NotificationManager 抽象自然支持

### Out-of-Scope

- 改阶段切换算法
- 推送 valuation rule 切换（normal→hot 等，下一轮）

---

## Implementation Plan

1. 写 `detect_stage_transition` + 测试
2. 写 regime_transition 推送模板
3. update_macro_daily 接入 + sentinel
4. 等 plan 008 hit-rate 报告确认质量 → 默认启用
5. 上线后观察 1 个月，过滤误报（特别是 Unknown 期边界）

---

## Verification

- 8+ 单测 pass
- e2e: 手动改 regime_daily.parquet 最后两行模拟切换，跑 update_macro_daily
  → Telegram 收到一条消息
- 同日 rerun → sentinel 阻止第二次推送
- regime hit-rate 报告（plan 008）显示总 hit > 60% 才默认启用

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| Hysteresis 边缘日反复切换造成 spam | 已有 3 日 min_consecutive_days 滞后；本 plan 不动 |
| Unknown 转换误报 | 显式过滤 Unknown→* 与 *→Unknown |
| Telegram 服务挂掉漏推 | NotificationManager 已有重试 + 降级；本 plan 不重做 |
| 用户在敏感时段（开盘 / 收盘）不想被打扰 | --skip-alert 留出口；后续可加时段过滤 |
