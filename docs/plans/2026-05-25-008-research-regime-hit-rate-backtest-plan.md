---
title: "research: regime 分类器 60 日运行后 hit-rate 回测"
status: pending
created: 2026-05-25
type: research
depth: light
---

## Summary

P1 regime classifier 上线时按 plan 002 §Non-goals 第 4 条明确：
"不回测分类器准确率（先上线，运行 60 个交易日后再算 hit rate）"。
本 plan：等 2026-05-25 起累计 60 个交易日后（约 2026-08-25），
跑历史 vs 实际牛熊真实对应关系的 hit-rate 分析.

---

## Problem Frame

**现状**：
- regime_daily.parquet 已有 2018-2026 共 2090 日历史标注
- 未做 vs 真实市场（沪深 300 / KCB 50 等指数）走势的对应关系分析
- plan 002 §Verification 提到 3 个时点应当符合：
  - 2020Q1: 应判 IV→I 切换（实测当时数据不足，全 Unknown）
  - 2022Q1: 应判 II→III（实测 IV+III 为主 — 部分对）
  - 2024Q4: 应判 I 早期（实测 III 为主 — 不对）

**目标**：
- 定义 hit-rate 度量：stage X 期间，指数 N 日后涨跌方向预期是否对应
- 算出 4 阶段各自的 hit-rate（基线 = 随机 50%）
- 找出系统性 mis-classify 的时段，给阈值调整提供依据

**非目标**：
- 不重新设计分类器（rule-based 留 plan 012 升级到 HMM 时再说）
- 不调阈值（本 plan 只出报告 + 数据）

---

## Scope Boundaries

### In-Scope

- **新脚本** `scripts/regime_hit_rate_backtest.py`:
  - 读 regime_daily.parquet + index_daily (HS300/KCB50)
  - 按 stage 分组，算每个 stage 期内"5/20/60 日前向收益"分布
  - 输出 4 阶段 hit-rate 矩阵 + 时间轴对齐图

- **度量定义**:
  | 阶段 | 期望 | hit 判定 |
  |------|------|----------|
  | I 谷底前 | 20 日后 HS300 ↑ | next_20d_pct > 0 |
  | II 扩张 | 20 日后 HS300 ↑↑ | next_20d_pct > +2% |
  | III 顶部 | 20 日后 HS300 →/↓ | next_20d_pct < 0 |
  | IV 衰退 | 20 日后 HS300 ↓ | next_20d_pct < 0 |

- **报告** `storage/reports/regime_hit_rate_60d.md`:
  - 总 hit-rate
  - 4 阶段各 hit-rate
  - 与基线（"全 normal" / 随机）对比
  - mis-classify 时段 timeline（标 stage label + 实际指数走势）
  - 调阈值建议（基于哪些 mis-classify 模式）

- **触发条件**: regime_daily.parquet 中 trade_date >= 20260525 的行数 ≥ 60

### Deferred

- HMM 概率分类器（hit-rate 表现差再说，#012）
- 跨指数对比（HS300 / KCB / 中证 500 hit-rate 不同）→ 下一轮

### Out-of-Scope

- 实盘策略回测（只看分类器表现，不接 combo_scan）

---

## Implementation Plan

1. 写 `scripts/regime_hit_rate_backtest.py` 骨架（可空跑，触发条件不满足直接退出）
2. 等运行 60 日（约 2026-08-25）
3. 跑脚本 → 出报告
4. 根据 mis-classify 模式调 `kss/config/macro_regime.yaml` 阈值（再开 plan 013）

---

## Verification

- 报告完整含 4 阶段 hit-rate + 时间轴对齐图
- 总 hit-rate > 60% 算"prior 假设有效"
- 单阶段 hit-rate < 50% 视为该阶段规则可能反了（需调）

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| 60 日太短，统计噪声大 | 报告里同时算 252 日历史 hit-rate（含 backfill 期）对比 |
| HS300 不能代表所有市场 | 加 KCB50 / 中证 500 双指数对比 |
| 阶段 III/IV 频率不平衡 | 用 stratified bootstrap 算每阶段单独的置信区间 |
