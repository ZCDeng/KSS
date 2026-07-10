---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
title: "Intel Bulk Digest Visible - Plan"
date: 2026-07-11
---

# Intel Bulk Digest Visible - Plan

## Goal Capsule

- **Objective:** 资讯雷达「一键提炼全部要点」完成后，**左侧「今日要点」卡片**必须稳定展示各赛道要点正文；禁止出现「跑完了但看不见」或永久转圈。
- **Product authority:** Product Contract（本文件）；用户确认展示位 = 左侧今日要点卡片。
- **Open blockers:** None（范围已锁定）。

---

## Product Contract

### Summary

Solo desk 用户批量提炼后，在**当前赛道左侧「今日要点 · {赛道名}」卡片**直接读到要点列表（bullet）。切顶部赛道 pill 可看其他赛道。不新增顶部全局结果墙；不依赖右侧详情区展示要点。

### Problem Frame

- 用户报告：一键提炼后无 UI 内容。
- 已存在一轮修复（显式 loading、世代号、不盖正文），但验收仍不达标或未覆盖全部失败路径。
- 展示位曾易被误解（右侧详情不是要点宿主）；产品确认仍以**左栏卡片**为准。

### Key Decisions

- **KD1.** 主展示位 = 左侧列表顶部的「今日要点」卡片；批量成功后**当前活跃赛道**卡片必须可见正文（若该赛道有结果）。
- **KD2.** 加载态不得清空/覆盖已成功正文；并发/重入只采纳最新一次结果。
- **KD3.** 失败可区分：卡片展示失败原因 + 可重试；批量失败计数可触发「重试失败赛道」。
- **KD4.** 不新增全局「各赛道要点汇总墙」；不把要点塞进右侧单条资讯详情作为主路径。
- **KD5.** 后端可出文时，前端必须能渲染（含常见 markdown 列表形态）。

### Requirements

- **R1.** 批量提炼结束后，若某赛道 `text` 非空，切到该赛道 pill 后左栏卡片**立即**显示要点列表（非空、非仅转圈）。
- **R2.** 批量进行中：当前赛道可显示轻量进度；**不得**因 loading 占位导致已有正文消失后无法恢复。
- **R3.** 批量摘要（完成 N/M · 失败 K）可保留；失败 K>0 时提供重试失败赛道入口。
- **R4.** 空回包 / 跳过：卡片明示「未生成 / 过少跳过」，禁止静默空白。
- **R5.** 单赛道「让 AI 提炼」与批量共用同一展示契约。

### Scope Boundaries

**In:** 资讯雷达 bulk + 单赛道 digest 的**可见性与状态机**；左栏卡片渲染。  
**Deferred:** 顶部多赛道折叠墙、右侧详情内嵌要点、改写池策略变更。  
**Outside:** 北证数据（另 plan）；LLM 模型选型。

### Success Criteria

- **SC1.** 对 ≥3 个有资讯的赛道一键提炼后，逐个切 pill，左栏卡片均能读到要点或明确失败/跳过文案。
- **SC2.** 重提过程中旧正文仍可见或完成后被新正文替换，无「永久转圈无文」。
- **SC3.** 失败赛道可通过重试恢复展示。

### Sources

- 用户反馈 + 既有 `IntelView` / `summarizeAllIntelTracks` 行为
- 已合并修复：显式 `intelDigestLoadingKeys` + 世代号（需验证是否已完全覆盖）

---

## Planning Contract

### Summary (HOW)

加固左栏「今日要点」状态发布：`Set` 原地 mutate 可能不触发 `@Published`；批量结束后强制刷新当前赛道卡片；失败/空文路径保持可重试。不改 bridge 契约。

### Key Technical Decisions

- **KTD1.** `intelDigestLoadingKeys` 整集合赋值发布（insert/remove 后 `= next`）。
- **KTD2.** 批量完成时 `objectWillChange`/`intelDigests` 整表再赋值一次，确保活跃赛道卡片重建。
- **KTD3.** 可选：`summaryShownUntil` 延长到 8–10s，降低「只看见进度条闪一下」的误判。

### Implementation Units

### U1. Publish loading Set correctly

- **Goal:** R2, SC2  
- **Files:** modify `Sources/KSSDesktop/Services/KSSStore.swift`  
- **Approach:** 所有 `intelDigestLoadingKeys` 变更走 copy-assign。  
- **Test expectation:** none — SwiftUI 发布语义；桌面目视 SC2。

### U2. Bulk completion UI refresh

- **Goal:** R1, R3, SC1  
- **Files:** modify `KSSStore.swift`（`summarizeAllIntelTracks` / retry）  
- **Approach:** 循环结束后整表 `intelDigests = intelDigests` 或 bump 令牌；保持失败计数 + 重试入口。  
- **Verification:** 批量后不切换 pill 也能在当前赛道见正文（若有结果）。

### U3. Smoke SC1–SC3

- **Goal:** SC1–SC3  
- **Approach:** 目视 + 可选 bridge 单赛道 digest 对照。  
- **Test expectation:** none beyond smoke.

### Verification Contract

- 批量 3+ 赛道后切 pill 均见正文或明确失败/跳过。  
- 重提不永久转圈。  
- `swift build` 通过。

### Definition of Done

- [ ] U1–U3  
- [ ] SC1–SC3 过一遍  

### Product Contract preservation

Product Contract unchanged。
