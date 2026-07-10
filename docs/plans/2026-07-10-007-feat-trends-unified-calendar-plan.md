---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
title: "Trends Unified Calendar - Plan"
date: 2026-07-10
---

# Trends Unified Calendar - Plan

## Goal Capsule

- **Objective:** 趋势观察 **双日历 → 单一大日历**（底色=资金热度，字=顶板块）；移除增量资金独立历；点日详情以 **热点板块 + 代表股** 为主。
- **Product authority:** Product Contract（ce-brainstorm）；与 2026-06-20 单历分层意图对齐。
- **Open blockers:** None.
- **Product Contract preservation:** Product Contract unchanged（R/KD/SC 与 brainstorm 一致）。

---

## Product Contract

### Summary

Solo desk operator 用 **一个大月历** 同权扫 **资金热度 + 主线板块**。格底色 = `inflowScore` 热力；格内字 = `topSector`。删除「增量资金」矮历 Section。点日后详情默认 **板块/代表股** 优先，资金摘要化。

### Problem Frame

- 实现拆成 ① 26pt 增量资金热力 + ② 60pt 板块文字历，同源双绑定，割裂且占高。
- 原 plan 已是单热力格叠标；现状 drift。
- 数据已在 `TrendDayCell` / 归档同日具备；主改 UI。

### Key Decisions

- **KD1.** 一个大日历为月扫唯一主视图；**移除**独立增量资金矮日历。
- **KD2.** 格：**底色** = 资金热度（`inflowScore`）；**字** = `topSector`（双信号同权扫月）。
- **KD3.** 形态骨架 **A（底色+叠字）**；字号/截断/选中态可调，不改回双历。
- **KD4.** 点日详情：**板块 chips + 代表股（`recs`）** 优先；资金摘要次之。
- **KD5.** 切月/选日/空日语义与现页一致。
- **KD6.** 不改 bridge/归档契约；MVP 用现有 month/day 字段。

### Requirements

- **R1.** 主区仅一套月历网格。
- **R2.** 有数据日格同时表达热度底色 + 板块短名（有则显示）。
- **R3.** 删除「增量资金」独立日历 UI（非仅隐藏）。
- **R4.** 选中日清晰选中态。
- **R5.** 日详情默认板块 + `recs` 在资金大块之前；资金可摘要/折叠。
- **R6.** 仅资金 / 仅板块 / 皆无 不崩溃。
- **R7.** 保持现有 7 列周布局与切月控件。
- **R8.** 加载/无数据日有可理解状态。

### Scope Boundaries

**In:** TrendsView 月历合并 + 详情信息序。  
**Deferred:** 新指标、年视图、旧视图切换、推荐微条三叠回格。  
**Outside:** 非趋势页、交易、bridge 字段扩展（除非 U 实施中发现硬缺）。

### Success Criteria

- **SC1.** 首屏仅一个承载资金+板块的月历。
- **SC2.** 抽 5 个双信号日：底色随分档变、格内板块名与数据一致。
- **SC3.** 点日后先见板块/代表股，资金不抢主位。
- **SC4.** 同窗下月历可读面积较双历明显改善（目视）。
- **SC5.** 残缺日/空日不崩。

### Sources

- `docs/plans/2026-06-20-002-feat-trends-calendar-page-plan.md`
- Grounding `/tmp/compound-engineering/ce-brainstorm/trends-unified-cal-20260710/grounding.md`
- `Sources/KSSDesktop/Views/TrendsView.swift`；`TrendDayCell` / `TrendDayDetail`

---

## Planning Contract

### Summary (HOW)

在 **TrendsView** 用 **单一 `monthGrid`** 替换 `inflowHeatmap` + `calendarGrid`：格高约 56–64，底色复用 `inflowBackground` 逻辑，内容复用 `topSector`/`sectorCount` 叠字；删除 Section「增量资金」及其 legend（色阶可并入月历 caption 一行）；`inflowBreakdown` 从「热力下挂」挪到 day detail 或折叠；dayDetail 重排：板块 chips + recs 表在上，north/etf 摘要在下。

### Key Technical Decisions

- **KTD1. 单组件 `unifiedDayCell`**  
  合并 `inflowCell` + `calendarCell` 的 Button/选中描边；底色 = 现 `inflowBackground`；前景 = 日号 + `topSector`（一行截断）+ 可选 `+N`；可选极小 ▲/▼ 当 `|inflowScore|≥0.45`（不另开历）。

- **KTD2. 删除而非隐藏**  
  去掉 `inflowHeatmap` / 独立 `SectionHeader("增量资金")` / 紧跟其下的 `inflowBreakdown` 强制展示；breakdown 并入 day detail。

- **KTD3. 详情序**  
  `dayDetail` 顺序建议：标题日期 → **sectorTop chips** → **recs 表** → 折叠或次要：`north`/`etfs` 一行摘要 + 可选原 breakdown tiles。

- **KTD4. 不改 bridge**  
  `trends-month` / `trends-day` 契约不变；`TrendDayDetail` 无 inflowScore 时资金摘要用 `north`/`etfs` 或从 month cell 查找同日 score（store 已有 `trendMonth`）。

- **KTD5. 本周时间线**  
  **保留**（非第二月历）；若与统一历信息重复可略缩，不在本期删除。

- **KTD6. 色阶**  
  沿用 `inflowScore` opacity 映射；caption 一行说明「底色=增量资金强度」。

- **KTD7. 对比度**  
  `|inflowScore|` 较高（如 ≥0.55）或选中态时，板块字用更高对比（`textPrimary` / 浅色底上的 deep ink，或 `onAccent` 于 accent 选中底）；避免深红底 + 暗红字。

- **KTD8. 代表股映射**  
  产品「代表股」= 现有 `TrendDayDetail.recs`（当日推荐及 T+N）；无 recs 则只展示板块 chips。

### High-Level Technical Design

```text
Before:
  [增量资金 Section] inflowHeatmap (h=26) + legend + optional breakdown
  [日历 Section]     calendarGrid (h=60)
  [本周] weekTimeline
  [日明细] north/etf + sectors + recs

After:
  [月历 Section]     unifiedGrid (h≈60)  // color=inflow, text=topSector
  [本周] weekTimeline  (keep)
  [日明细] sectors + recs first; capital summary secondary
```

### Implementation Units

### U1. Unified month cell + single grid

- **Goal:** R1–R2, R4, R6–R7, SC1–SC2, SC5  
- **Dependencies:** none  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/TrendsView.swift`  
  - optional tests: `Tests/KSSDesktopTests/` 纯函数若抽出色阶/文案  
- **Approach:**  
  - 新增 `unifiedGrid` / `unifiedCell`（或重构 `calendarGrid` 并入 inflow 底色）。  
  - 删除 `inflowHeatmap` 调用链。  
  - 格高取 56–64；日号 + topSector 截断；`sectorCount>1` 保留 `+N`。  
  - 选中描边统一 2pt accent。  
- **Test scenarios:**  
  - 有 score+sector 的 cell：背景非中性且文案非空（逻辑/预览）。  
  - 仅 score：无板块字不崩溃。  
  - 空日：不可点或点无 detail。  

### U2. Remove dual-section chrome; caption legend

- **Goal:** R3, SC1  
- **Dependencies:** U1  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/TrendsView.swift`  
- **Approach:**  
  - 单一 `SectionHeader("月历" / "趋势月历", caption: "底色=增量资金 · 字=强势板块 · 点日看明细")`。  
  - 删除独立 inflow legend 或压成 caption 内短说明。  
- **Test expectation:** none — 结构删除；目视 SC1。  

### U3. Day detail re-priority (sectors + stocks first)

- **Goal:** R5, SC3  
- **Dependencies:** U1  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/TrendsView.swift` (`dayDetail`, 可选 `inflowBreakdown` 复用)  
- **Approach:**  
  - 重排：sector chips → recs → capital summary（north/etf 一行或折叠 DisclosureGroup）。  
  - 若需 inflowScore：从 `month?.days` 按 date 查找 cell。  
  - 删除热力 Section 下自动展开的大块 breakdown。  
- **Test scenarios:**  
  - found detail：视图树顺序上板块/推荐在资金块之前（可读性/代码序断言若可行）。  

### U4. Empty / loading polish

- **Goal:** R8, SC5  
- **Dependencies:** U1  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/TrendsView.swift`  
- **Approach:**  
  - 保持 `.task` 加载月、下月 disabled；无 detail 文案保留。  
  - 统一历 loading 时 Progress 不挡死布局。  
- **Test expectation:** none beyond smoke.  

### Verification Contract

- 目视 SC1–SC5：仅一历；双信号格；详情序；空日。  
- `swift build` Trends 相关无新增 warning。  
- 不要求改 `archive_trends_daily` / bridge。  

### Definition of Done

- [ ] U1–U4 完成；无双 Section 日历  
- [ ] 详情板块/代表股优先  
- [ ] SC1–SC5 桌面过一遍  

### Risks

| Risk | Mitigation |
|------|------------|
| 格内字 + 深色底对比差 | 选中/深热力时字色用 onAccent 或描边 |
| 丢 ▲▼ 资金方向 | 格内保留小三角或依赖色相 |
| 详情砍资金过狠 | 摘要 + Disclosure 可展开 |

### Deferred to Follow-Up

- 推荐胜负微条叠回格内（原 U6）  
- 纯资金旧视图 toggle  

---

## End of Plan
