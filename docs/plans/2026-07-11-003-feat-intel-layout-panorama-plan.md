---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
title: "Intel Layout + Panorama Summary - Plan"
date: 2026-07-11
---

# Intel Layout + Panorama Summary - Plan

## Goal Capsule

- **Objective:** 资讯雷达阅读区改为 **上：整行「今日要点」→ 下：左列表 | 右正文**；并在 **12 赛道 pill 上方**增加 **12 赛道当日热点全景摘要**（单独 LLM，跟一键提炼一并生成，默认可折叠 2 行）。
- **Product authority:** Product Contract（本文件）。
- **Open blockers:** None。

---

## Product Contract

### Summary

Solo desk 用户在资讯雷达用 **纵向分区**：全景热点（跨赛道）→ 赛道 pill → **整行当前赛道今日要点** → 下方左新闻列表 / 右正文（投研改写 Tab 不变）。一键提炼全部要点时，除 12 赛道各自要点外，**再调一次 LLM** 生成全景条。

### Problem Frame

- 今日要点嵌在左栏顶部，挤占列表宽度，读完要点再扫列表/正文视线折返多。
- 缺跨赛道「今天全市场/全 12 赛道在吵什么」的一瞥入口。

### Key Decisions

- **KD1.** 布局骨架（自上而下）：  
  顶栏（标题 / 一键提炼 / 统计）→ **全景热点条** → 赛道 pill → **整行「今日要点 · 当前赛道」** → **HStack：左列表 | 右正文**。
- **KD2.** 「今日要点」卡片 **全宽**，仅绑定 **当前 activeTrack**（切 pill 换内容）。
- **KD3.** 全景摘要 = **独立 LLM 调用**（输入：12 赛道头条/列表采样），**非**简单拼接各赛道 digest。
- **KD4.** 触发：与 **「一键提炼全部要点」同批**（赛道串行提炼完成后跑全景，或合理并行策略由 plan 定）；条上可 **单独重生成**。
- **KD5.** 全景展示：**默认可折叠，默认约 2 行**，点开看全文；加载/失败有明确态。
- **KD6.** 不改 RSS 源集合与单条改写（投研/中文）主流程；单赛道 digest 契约尽量复用。

### Requirements

- **R1.** 左栏不再内嵌今日要点；列表区垂直空间主要给新闻行。
- **R2.** 今日要点在 list|detail **上方整行**展示当前赛道结果（有文/加载/失败/空 与现卡片语义一致）。
- **R3.** 12 赛道 pill **上方**有全景热点条；未生成时显示占位或引导「一键提炼」；生成中/失败可辨。
- **R4.** 一键提炼全部要点成功路径下，全景条最终有正文或明确失败。
- **R5.** 全景默认折叠约 2 行；展开可读完整 LLM 输出；可再次生成。
- **R6.** 窄窗/常规桌面宽下列表+正文仍可用（左栏可保持约 380 或等比，由 plan 定）。

### Scope Boundaries

**In:** IntelView 布局重组；全景摘要的产品行为与触发；store 状态位。  
**Deferred:** 全景写入沉淀库；全景按日归档历史；多模型路由。  
**Outside:** 北证日线；RSS 源扩充；改写 Tab 内容逻辑。

### Success Criteria

- **SC1.** 打开任一有数据的赛道：先见全宽今日要点区，其下才是左列表右正文。
- **SC2.** 一键提炼结束后：当前赛道要点更新 **且** 全景条有内容（或失败文案）；折叠默认 2 行。
- **SC3.** 展开全景可读完整摘要；点重生成可刷新全景而不必重跑 12 赛道（若实现成本过高可降为「随一键重跑」并在 plan 注明）。
- **SC4.** 切赛道只换整行要点与列表/正文，不重置全景（除非用户重生成）。

### Sources

- 用户描述 + 当前 `IntelView`（list|detail + 左栏 digestCard）
- 既有 bulk digest / `intelDigests` 状态机

---

## Planning Contract

### Summary (HOW)

`IntelView` 重组为顶栏 → 全景条 → pills → 全宽 digest → 左列表|右正文。`run_panorama` + bridge `intel-panorama`；`summarizeAllIntelTracks` 末尾 `generateIntelPanorama`。

### Implementation Units

### U1. Bridge + digest_ai panorama
### U2. Store + models
### U3. IntelView layout + panorama bar (collapsible)

### Product Contract preservation
Unchanged.
