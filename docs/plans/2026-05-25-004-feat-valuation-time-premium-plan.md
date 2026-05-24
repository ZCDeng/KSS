---
title: "feat: 估值过热标尺 n（P3 of Bolton 周期框架）"
status: pending
created: 2026-05-25
type: feat
depth: light
---

## Summary

实现 Bolton《稳中求胜》第 7-8 章的"时间贴水" *n*：算"把当前指数压回无增长无风险价值需要多少年增长来弥补"，作为整体估值过热标尺。`n` 大 = 隐含极长高增长 = 顶部泡沫；`n` 极负 = 极度悲观 = 反转区。每日产出沪深 300 的 `n` 值，写入 dashboard 顶栏 + combo_scan 仓位上限。

---

## Problem Frame

**现状**：
- KSS 判断"市场过热"用的是底层信号聚合（双重过热信号占比），缺整体估值标尺
- 没有"现在的估值在过去 N 年的什么分位"的硬指标
- combo_scan 候选数不随大盘估值动，5 月初熊市底部和 12 月顶部用同一阈值

**目标**：
- 每日算沪深 300 的隐含时间区间 *n*，落地 `storage/macro/valuation_n_daily.parquet`
- `n > 5` 时所有 combo_scan 入场信号仓位上限砍半，`n > 10` 时只发警告不出 Top-5
- `n < -2` 时反向开始建 reversal watchlist
- scanner banner 顶栏增加："沪深 300 PE / n 隐含年数 / 历史分位"

---

## Scope Boundaries

### In-Scope

- **新模块** `kss/macro/valuation.py`：
  - `compute_time_premium(index_value, current_e, expected_growth, risk_free_rate, eq_premium) -> float`
    - 公式：`n` 使 `S = S_NWG`，按附录 7B：`n = log(P/S_NWG) / log(1+g)` 其中 `S_NWG = E/r`
  - `compute_hs300_n(date)` —— 拉沪深 300 当日点位 + TTM PE + 10Y 国债收益率 + 过去 12M 实际盈利增速
  - `compute_n_percentile(n_history, current_n)` —— 历史分位（默认回看 5 年）

- **数据源（P0 已有 + 新增）**：
  - 沪深 300 指数：Tushare `index_daily` ts_code=`000300.SH`（已有）
  - 沪深 300 估值：Tushare `index_dailybasic` ts_code=`000300.SH`（TTM PE / PB / DV）
  - 10Y 国债：P0 已有
  - 实际盈利增速：从沪深 300 成分股 TTM 净利润年同比加权

- **集成点**：
  1. `scripts/update_macro_daily.py` 末尾追加 `compute_hs300_n(today)` 写入 valuation_n_daily.parquet
  2. `scripts/scan_combo_signals.py` 启动时读 n，按阈值规则调整仓位上限
  3. `scripts/scanner.py` banner 顶栏增加：`HS300 n=X.X（5Y 分位 Y%）`
  4. `kss/sector/commentary.py` LLM prompt 增加：估值标尺段

- **阈值规则（初版，需历史回测调参）**：

  | n 值范围 | 含义 | combo_scan 行为 |
  |----------|------|-----------------|
  | n > 10 | 极度乐观，泡沫顶 | 不发 entry，仅推送 avoid |
  | 5 < n ≤ 10 | 偏热 | 仓位上限砍半，候选数减至 3 |
  | 0 ≤ n ≤ 5 | 正常 | 默认行为 |
  | -2 ≤ n < 0 | 偏冷 | 加大 entry 候选数至 7 |
  | n < -2 | 极度悲观 | 进入 reversal 模式，扫"防御被错杀"标的 |

- **单测** `kss/tests/test_valuation.py`：
  - 公式回归（书附录 7B 的数值案例：弥补 21% 差异 + 10% 增长率 = n≈2 年）
  - 极端值（无增长 = 算出当前 PE 等于 1/r）
  - 历史分位计算

### Deferred

- 中证 500 / 中证 1000 同样指标（不同市值层差异大，先聚焦沪深 300）
- 动态 expected_growth 估计（先用过去 12M 实际增速线性外推）
- 板块级 n（先做整体 n，板块下沉留后续）

### Out-of-Scope

- 实盘下单
- 因子择时（n 只调 combo_scan 行为，不替代因子模型）

---

## Implementation Plan

1. 扩展 MacroClient.fetch_index_daily_basic（沪深 300 估值）
2. `kss/macro/valuation.py` 主公式 + helper
3. 单测覆盖公式 + 历史分位 + 阈值规则
4. 集成到 update_macro_daily.py（追加一段）
5. 集成到 scan_combo_signals.py 仓位上限规则
6. 集成到 scanner.py banner
7. 历史回填脚本：跑 2015-2024 计算 n，验证书里"n>10 = 泡沫顶"的中国市场对应关系

---

## Verification

- 公式回归：书附录 7B 数值案例完整匹配
- 历史回测：2015-06 上证 5178 顶应有 n > 10；2018-12、2024-01 底应有 n < 0
- 单测 8+ cases 全绿
- scanner banner 顶栏正确显示

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| expected_growth 估计噪声大 | 用过去 12M + 过去 36M 双窗口，取较小值（保守） |
| 中国 risk premium ≠ 美国 7% | 用 5 年滚动估计实际 ERP，不硬编码 |
| TTM PE 受亏损股扰动 | 用沪深 300 指数 PE（Wind / Tushare 已剔除负值），不自算 |
