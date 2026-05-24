---
title: "feat: 个股财务/流动性硬过滤（P4 of Bolton 周期框架）"
status: pending
created: 2026-05-25
type: feat
depth: light
---

## Summary

把 Bolton《稳中求胜》第 5 章的个股特殊风险（财务杠杆 f / 市场流动性 m）做成 combo_scan 的前置硬过滤。书里反复警告：杠杆过高 + 流动性差的小盘股在 r 上行 + 衰退期会复合下跌（联邦百货 LBO 破产的中国版即 ST 雷）。

---

## Problem Frame

**现状**：
- combo_scan 候选池纯按技术面（RPS + 模式命中）筛
- 不剔除高杠杆 / 低流动性 / 高质押 / 退市风险标的
- A 股 2018 / 2024 大量 ST 雷集中爆发，这条护城河 KSS 几乎没有

**目标**：
- 在 combo_scan 进入 bootstrap 模式匹配之前先做硬过滤
- 三道线：财务杠杆、市值流动性、ST/退市预警
- 出池标的不进 Top-5 也不进 avoid（直接踢出候选）

---

## Scope Boundaries

### In-Scope

- **新模块** `kss/strategies/risk_filters.py`：
  - `filter_high_leverage(stocks, threshold_quantile=0.80, industry_level="L1")`
    - 计算每只股票资产负债率（最新季报）
    - 与该行业 L1 历史 80 分位比较，超出剔除
  - `filter_low_liquidity(stocks, min_daily_amount=5_000_0000)`
    - 过去 20 个交易日均成交额 < 5000 万直接剔除
  - `filter_st_risk(stocks)`
    - 名称含 ST / *ST 直接剔除
    - 连续 2 年净利润为负 / 营收 < 1 亿 / 净资产为负 任一命中剔除
  - `apply_all_filters(stocks) -> (kept, removed_with_reasons)`

- **数据源（已有 + 复用）**：
  - 资产负债率：Tushare `fina_indicator` 单季报（`debt_to_assets`）
  - 行业归属：已有 `industry_mapping.py`
  - 日均成交额：cs_data CSV / sqlite_store 已有
  - ST 状态：Tushare `stock_basic` 中 `name` 字段 + `delist_date` 字段

- **集成点**：
  1. `scripts/scan_combo_signals.py` 主流程入口加 3 行：`stocks = apply_all_filters(stocks)`
  2. Telegram 推送结尾加一段："今日被风险过滤排除 N 只：[理由分布]"
  3. `kss/tests/test_risk_filters.py` 覆盖各过滤器边界

### Deferred

- 财务粉饰检测（应收账款异常、存货周转率、商誉占比）
- 股东减持公告 + 质押率（数据源未接，留后续）
- 重大诉讼 / 监管处罚（书第 8 章 "other risks"，本期不做）

### Out-of-Scope

- 实盘风控（仓位 / 止损规则）
- 量化反向操作（卖空 ST，需要融券标的，KSS 不做）

---

## Implementation Plan

1. 扩展 TushareClient.fetch_fina_indicator（季频，含 debt_to_assets / current_ratio）
2. `kss/strategies/risk_filters.py` 三个过滤器 + apply_all_filters
3. 缓存层：财务数据按季更新（不每天调 Tushare），cache 在 `storage/macro/fina_quarterly.parquet`
4. 单测 12+ cases，含边界值 + 全部命中 + 全部通过
5. 集成到 scan_combo_signals.py，输出"今日过滤池"统计
6. 跑一周观察日志，确认过滤池规模在 200-400 只之间合理（A 股 5000+ 池子）

---

## Verification

- 单测全绿
- 集成后 combo_scan 日志显示过滤前后池子大小变化
- 抽查若干 ST 标的（如 *ST 苏吴）确认被过滤
- 抽查若干高杠杆地产标的（如某些低评级地产）确认被过滤
- 主板权重股（如贵州茅台、宁德时代）不应被流动性过滤

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| 行业 80 分位本身随时间漂移 | 滚动 60 个月分位，每月初重算 |
| 财务数据滞后 1 个季度 | 用最新可得季报，明确标注 lag |
| 误杀正常杠杆经营行业（如银行/地产） | 按 L1 行业分位过滤，跨行业不比较绝对值 |
| 5000 万阈值在中小盘市场过严 | 阈值改为可配置（`kss/config/risk_filters.yaml`），按市场冷热调 |
