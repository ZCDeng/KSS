---
title: "feat: 全市场 SW L1 industry_map + per-stock 部门轮换评分"
status: pending
created: 2026-05-25
type: feat
depth: light
---

## Summary

P2 部门轮换映射当前只在 scan 顶部 banner 显示"本阶段优先/回避板块"，
个股层面没有真正打分。原因：`storage/industry_map.csv` 仅 13 行 + 英文名
（"Software" / "Semiconductors"）与 `sector_rotation.yaml` 中文（"半导体设备"）
对不上。本 plan：拉全市场 SW L1 中文名映射 + 在 combo_scan Top-N 排序中加
`rotation_bonus = score_industry_fit(stk_industry, regime.stage) * 0.2`.

---

## Problem Frame

**现状**：
- `kss/macro/rotation.py::score_industry_fit` 已就绪（+1/-1/0 分）
- `storage/stock_names.csv` 含 608 KCB 股票 + 中文行业（SW L2/L3 颗粒度，如"运输设备"）
- `storage/industry_map.csv` 仅 13 行英文，与 yaml 对不上
- scan_combo_signals.py 的 `_apply_risk_prefilters` 已经用 stock_names.csv 抽 industry_map，
  但只用于 risk filter；Top-N 选股排序未消费

**目标**：
- 拉全市场 SW L1 中文名 + ts_code 映射（5000+ 股）落地 `storage/industry_map_swl1.parquet`
- combo_scan Top-N 排序加 rotation_bonus
- 阶段 III 时银行/白酒优先级↑，钢铁/煤炭优先级↓（plan 003 验证标准）

**非目标**：
- 不做 SW L2/L3 颗粒度（一级够用，越细越漂移）
- 不改 risk_filters 已用的 industry_map 路径

---

## Scope Boundaries

### In-Scope

- **新脚本** `scripts/backfill_industry_map.py`：
  - 调 Tushare `index_classify` (level='L1') + `index_member`（获取 SW L1 成员）
  - 或调 `stock_basic` + `daily_basic` 取 industry 字段（如果 SW 接口有积分门槛）
  - 落地 `storage/industry_map_swl1.parquet`（ts_code, sw_l1_name, sw_l1_code）

- **新模块函数** `kss/macro/rotation.py::load_industry_map(path=None) -> dict[str, str]`：
  优先读 swl1 parquet，缺失时降级到 `stock_names.csv`

- **scan_combo_signals.py 改造**:
  - `_apply_risk_prefilters` 增加返回 `industry_map` 供后续重用（避免重复读 csv）
  - `top_n_picks` 排序键加 `rotation_score` 字段：
    `agg.sort_values(['has_check', 'rotation_score', 'n_combo', 'mv'], ascending=[F, F, F, F])`
  - 仅在 `regime is not None` 时生效；regime 缺时退回原排序

- **单测** `kss/tests/test_rotation.py`：
  - `load_industry_map` parquet 优先 + csv 兜底
  - integration test: combo_scan 风格的小 fixture → 阶段 III 时白酒排名↑

### Deferred

- SW L2 / L3 二级三级行业
- 概念板块映射（plan 003 §Deferred 已声明）
- 动态权重（当前 hardcode 0.2，后续按 60 日 hit-rate 调）

### Out-of-Scope

- 改 rotation.yaml 阶段池
- 接入万得 / 朝阳行业分类

---

## Implementation Plan

1. 写 `scripts/backfill_industry_map.py`，跑一次拉全 5000+ 股
2. 加 `kss/macro/rotation.py::load_industry_map` + 测试
3. scan_combo_signals.py 在 entry 排序加 rotation_bonus
4. 跑一周观察 Top-5 板块分布是否与 plan 003 §Verification 标准对齐（阶段 III avoid 池 < 20%）

---

## Verification

- 全市场 industry_map 覆盖率 ≥ 95%（缺的应是新股 / 退市）
- 阶段 II 跑：Top-5 至少 60% 落在 yaml `preferred` 池（钢铁/煤炭/有色等）
- 阶段 III 跑：Top-5 中 `avoid` 池（钢铁/煤炭）占比 < 20%
- 24 现有 rotation 测试 + 新增 integration test 全绿

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| Tushare SW 接口需要 2000 积分 | 用 `stock_basic` 的 industry 字段兜底（值与 SW L1 一致只是命名不同，加 alias 映射表） |
| 行业归属随公司业务调整 | 加 list_date 字段，按 trade_date 查当时分类（非本期范围，标 TODO） |
| 中文行业名编码 | 落 parquet 时显式 utf-8，read_csv 加 encoding='utf-8' |
