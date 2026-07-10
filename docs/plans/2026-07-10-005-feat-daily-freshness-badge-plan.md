---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
title: "Daily Bar Freshness Badge - Plan"
date: 2026-07-10
---

# Daily Bar Freshness Badge - Plan

## Goal Capsule

- **Objective:** 自选/个股界面清楚标出**日线数据截至哪一天**；当相对最新交易日落后 ≥1 个交易日时，提示「可能陈旧」并提供**一键跑 `update-cs-data`**，避免再出现「图停在 6 月却无解释」的静默失败。
- **Product authority:** Product Contract（ce-brainstorm）；上游 ideation：`docs/ideation/2026-07-10-cs-data-storage-db-ideation.html` Idea #2。
- **Open blockers:** None。
- **Product Contract preservation:** Product Contract unchanged（KD/R/SC 与 brainstorm 一致；规划仅补 HOW）。

---

## Product Contract

### Summary

Solo desk operator 在看**自选列表**与**个股详情主图**时，始终能看到该票日线 `latestDate`。若该日期落后于「应有的最新交易日」≥1 个交易日，界面用可点徽标标明陈旧，并允许触发全池日更任务 `update-cs-data`。任务进行中有明确状态；完成后列表/详情刷新到新末日期。与 Longbridge **实时**徽标并列但语义分离：实时管盘中价，本功能管**日线底稿新鲜度**。

### Problem Frame

- 日线停在旧日期时，主图与指标静默展示陈旧 K 线，用户误以为市场停涨或 App 坏了。
- 根因曾是 git 冲盘；脱钩后仍可能因 cron 漏跑、日更失败、新 clone 无数据导致陈旧——需要**产品级可观测**，而非仅靠日志。
- 已有 `update-cs-data` Runbook 与 `update_cs_data_last.json` 告警，但自选图不展示。

### Key Decisions

- **KD1.** 陈旧阈值：**落后最新交易日 ≥1 个交易日** 即陈旧（休市日不算落后）。
- **KD2.** 交互：徽标 + **一键 `update-cs-data`**（全池日更，可能数分钟）；不做「仅当前票」MVP。
- **KD3.** 展示面：**个股详情主图区** + **自选列表行**（每行可见数据日或陈旧点）。
- **KD4.** 与实时状态分离：实时 badge 不吞并日线陈旧；两者可同时出现。
- **KD5.** 日线末日期真源：该票本地日线产物的最新 `trade_date`（与 bridge stock / 列表摘要同源）；不编造。
- **KD6.** 一键日更走既有任务通道（与 Runbook 同权），运行中禁用重复点按并展示进度/结果摘要；失败可感知、可重试。
- **KD7.（产品确认）** 一键前 **需要简短确认**：「将全池更新日线，约数分钟」。

### Actors / Flows

- **A1.** Operator 打开自选：每行看到数据日；陈旧行有视觉区分。
- **A2.** Operator 打开个股详情：主图旁/状态区显示「日线截至 YYYY-MM-DD」或「日线陈旧 · 截至 …」。
- **A3.** Operator 点陈旧入口 → 确认 → 启动 `update-cs-data` → 等待完成 → 当前票与列表末日期更新（或明确失败原因）。
- **A4.** 非交易日：相对「上一交易日」判定；已对齐上一交易日则**不**标陈旧。

### Requirements

- **R1.** 自选列表每一有日线的行展示末数据日（紧凑格式可接受，如 `07-09`）；无日线文件时展示「无日线」。
- **R2.** 详情页在图表上下文展示完整「日线截至 YYYY-MM-DD」；陈旧时文案含「陈旧」语义且可点。
- **R3.** 陈旧判定：`referenceTradeDate − barDate ≥ 1` 个**交易日**（见 KTD1）。
- **R4.** 陈旧入口触发 `update-cs-data`，与 Runbook 同任务 id；进行中 loading，禁止连点。
- **R5.** 任务成功后刷新 snapshot 列表摘要 + 当前详情，使末日期反映新文件。
- **R6.** 任务失败：可见错误；保留重试。
- **R7.** 实时 Longbridge 状态与日线陈旧**同时**可展示。
- **R8.** 北证仅有 bj_cache：用其日线末日期；无则「无日线」。

### Scope Boundaries

**In:** 列表行 + 详情图区；≥1 交易日陈旧；一键全池 `update-cs-data` + 确认 + 完成后刷新。  
**Deferred:** 单票日更；今日看盘纸交易区；自动通知；SQLite 迁库。  
**Outside:** 改 log_mv / Longbridge 语义；把 cs_data 重新入库。

### Success Criteria

- **SC1.** 人为截断某票日线到 T−5 → 列表与详情均标陈旧。
- **SC2.** 一键日更成功 → 末日期推进，陈旧消失（源有新 bar 时）。
- **SC3.** 已对齐 reference 的票无误报（含周末）。
- **SC4.** 实时徽标与日线陈旧可同时正确。
- **SC5.** 无日线文件显示「无日线」，不崩溃。

### Sources

- Ideation: `docs/ideation/2026-07-10-cs-data-storage-db-ideation.html` (#2)
- Session: git 冲盘 → 图停 6 月；PR #58 脱钩
- Code: `StockSummary.latestDate`、`StockBrowserView`、`KSSTask.updateCsData`、`_is_trade_day` / `trading-hours`

---

## Planning Contract

### Summary (HOW)

在 **Swift 纯函数**里比较「票的 `latestDate`」与 bridge 给出的 **`reference_trade_date`（应有日线日）**；列表行与详情图区展示日期/陈旧；陈旧可点 → 确认对话框 → `store.runTask(.updateCsData)` → `loadSnapshot` + 重拉当前详情。交易日锚点扩展现有 `trading-hours` JSON，避免再开命令。

### Key Technical Decisions

- **KTD1. `reference_trade_date`（应有日线日）**  
  扩展 `trading-hours` 响应字段（snake_case → Swift camelCase）：  
  - 若「今天」是交易日且本地时间 **&lt; 18:00 上海** → 取**上一交易日**（盘中/盘后早期通常尚无当日完整日线）。  
  - 若今天是交易日且 **≥ 18:00** → 取**今天**（对齐盘后 cron 口径）。  
  - 若今天非交易日 → 取 **≤ 今天的最近交易日**。  
  Python 侧复用 `_is_trade_day` / `trade_cal`（失败时回退周一–五近似，与现有 trading-hours 一致）。  
  Swift 陈旧：`barDate` 为空 → 无日线；`barDate < referenceTradeDate`（按日历日字符串 `YYYY-MM-DD` 或 `YYYYMMDD` 归一后比较）且两者之间至少隔 1 个交易日——**MVP 简化**：归一后 **`barDate < referenceTradeDate` 即陈旧**（因 reference 已是「应有最新 bar 日」，严格等于则新鲜）。

- **KTD2. 日期真源**  
  列表：`StockSummary.latestDate`（已有）。详情：`detail.latest?.latestDate` 或 history 末日。北证 summary 已带 history 时取末日 `date` 字段填入展示。

- **KTD3. 陈旧纯函数**  
  新建 `Sources/KSSDesktop/Support/DailyBarFreshness.swift`：  
  `normalizeDate(_:) -> String?`、`isStale(barDate:reference:) -> Bool`、`compactLabel` / `detailLabel`。单测不依赖网络。

- **KTD4. 一键日更**  
  复用 `KSSStore.runTask(.updateCsData)`（已在 formal 任务组）。UI：`confirmationDialog` 文案固定。`isRunningTask`（或等价）为 true 时按钮 disabled。成功：`await loadSnapshot()`；若有 `selectedSymbol` 再 `selectStock` 刷新 history。

- **KTD5. 与实时 badge 布局**  
  详情顶栏/图上状态条：**日线新鲜度**靠近图表；**实时**保持现有 `RealtimeStatusBadge` 位置。列表行：名称旁或右侧次要文案显示 `07-09` / `陈旧·06-26` / `无日线`。

### High-Level Technical Design

```text
trading-hours ──► referenceTradeDate ──┐
StockSummary.latestDate ───────────────┼──► DailyBarFreshness.isStale
                                       │
                    ┌──────────────────┴──────────────────┐
                    ▼                                     ▼
            列表行 compact label                   详情 detail label
                    │                                     │
                    └──────── 陈旧 && 点击 ───────────────┘
                                      │
                         confirmationDialog
                                      │
                         runTask(updateCsData)
                                      │
                         loadSnapshot + reload detail
```

### Implementation Units

### U1. 陈旧纯函数 + 单测

- **Goal:** 可测的日期归一与陈旧判定，无 UI。  
- **Requirements:** R3, SC1/SC3  
- **Dependencies:** none  
- **Files:**  
  - create: `Sources/KSSDesktop/Support/DailyBarFreshness.swift`  
  - create: `Tests/KSSDesktopTests/DailyBarFreshnessTests.swift`  
- **Approach:** 支持 `YYYY-MM-DD` / `YYYYMMDD` / 空；`isStale` 在 bar 缺失时返回「无日线」态（enum: fresh / stale / missing）。  
- **Execution note:** 测试优先。  
- **Test scenarios:**  
  - bar=`2026-07-09`, ref=`2026-07-09` → fresh  
  - bar=`2026-06-26`, ref=`2026-07-10` → stale  
  - bar=nil/"" → missing  
  - 归一：`20260709` 与 `2026-07-09` 等价  

### U2. trading-hours 增加 reference_trade_date

- **Goal:** App 获得「应有日线日」锚点。  
- **Requirements:** R3, A4  
- **Dependencies:** none（可与 U1 并行）  
- **Files:**  
  - modify: `scripts/kss_app_bridge.py` (`_trading_hours`)  
  - modify: `Sources/KSSDesktop/Models/KSSModels.swift` (`TradingHours`)  
  - modify: `kss/tests/test_bridge_longbridge.py` 或现有 trading-hours 测（若无则轻量单测纯函数抽离）  
- **Approach:** 在 `_trading_hours` 用 trade_cal 找 reference；失败回退工作日近似。Swift 解码新字段 `referenceTradeDate`（可选，缺省则 UI 不标陈旧仅显示日期）。  
- **Test scenarios:**  
  - 模拟交易日 10:00 → reference = 上一交易日  
  - 模拟交易日 19:00 → reference = 今天  
  - 周六 → reference = 周五（或 cal 返回值）  

### U3. 列表行与详情 UI

- **Goal:** R1/R2/R7/R8 可见。  
- **Requirements:** R1, R2, R7, R8, SC4, SC5  
- **Dependencies:** U1, U2  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/StockBrowserView.swift`  
  - create or modify: 小组件可放在 `RealtimeChrome.swift` 旁，如 `DailyFreshnessLabel`  
- **Approach:**  
  - 列表：`stock.latestDate` + `store.tradingHours?.referenceTradeDate`  
  - 详情：图上 `statusText` 旁或 HStack 增加日线 label；陈旧时 button 样式对齐「非实时」重试链  
  - 北证：`latestDate` 若空则从 detail history 末日推导（若列表 summary 已填则不改 bridge）  
- **Test scenarios:** UI 以预览/逻辑测为主；枚举态渲染不崩溃。  

### U4. 确认 + runTask + 刷新

- **Goal:** R4–R6, SC2  
- **Dependencies:** U3  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/StockBrowserView.swift`（或 ContentView 传 onUpdateCsData）  
  - modify: `Sources/KSSDesktop/Services/KSSStore.swift`（若需 `isRunningUpdateCsData` 标志；可复用现有 task 运行态）  
- **Approach:**  
  - `@State showUpdateCsConfirm`  
  - `store.runTask(.updateCsData)`（长任务已在 subprocessOnly）  
  - 成功 → `loadSnapshot()`；有选中票 → `selectStock`  
  - 失败 → `errorMessage` 或内联 Text  
- **Execution note:** 手动/集成验证任务路径；不强制 mock bridge 全链路。  
- **Test scenarios:**  
  - 确认取消不触发任务  
  - 运行中二次点击无效  
  - 成功后 latestDate 变化（可用 store 单测注入假 snapshot 若已有模式）  

### Verification Contract

- `swift test --filter DailyBarFreshnessTests`  
- 手动：截断一票 csv 末日期 → 列表/详情陈旧 → 确认日更 → 恢复  
- 盘中打开：已对齐 T−1 的票不误报陈旧  

### Definition of Done

- U1–U4 完成；Product SC1–SC5 可演示  
- 不重新 git 跟踪 `cs_data_*.csv`  
- 不改 Longbridge 实时语义  

### Risks & Dependencies

| 风险 | 缓解 |
|------|------|
| trade_cal 失败 | 与 trading-hours 同回退；UI 仅显示日期不标陈旧 |
| 全池日更慢 | 确认文案写明；loading 态 |
| 盘中「今天」尚无 bar | KTD1 18:00 切点 |

### Deferred to Follow-Up

- 单票日更任务  
- 今日看盘纸交易区陈旧  
- SQLite 双写（ideation #3）  

### Assumptions

- `StockSummary.latestDate` 对 A 股池可靠；北证 summary 已有或可从 history 取。  
- `runTask(.updateCsData)` 与 Runbook 行为一致。  

---

## Sources & Research

- Local: `StockBrowserView`, `StockSummary.latestDate`, `KSSTask.updateCsData`, `kss_app_bridge._trading_hours` / `_is_trade_day`  
- Institutional: `docs/solutions/paper_trade_deployment.md`（cs_data 需日更）  
- External: none（本地模式充分）  
