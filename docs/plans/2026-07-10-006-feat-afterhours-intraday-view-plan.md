---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
title: "After-Hours Intraday View - Plan"
date: 2026-07-10
---

# After-Hours Intraday View - Plan

## Goal Capsule

- **Objective:** 非交易时段打开个股 **1分/5分** 与今日看盘 **堆叠卡分时** 时，不再静默空白：优先拉源上「最近完整会话」分钟序列；源失败则用本地沉淀降级，并标明来源。
- **Product authority:** 本 Product Contract（ce-brainstorm 2026-07-10）。
- **Open blockers:** None.
- **Product Contract preservation:** Product Contract unchanged（R/KD/SC 与 brainstorm 一致；规划只补 HOW 与 OQ 默认）。

---

## Product Contract

### Summary

Solo desk operator 在收盘后、晚间、周末仍要能看「最近一个完整 A 股交易会话」的分钟走势。行为统一为：**联网源优先 → 失败降级本地 → 来源可辨**。本地数据靠 **全池盘后收一次完整会话**，并对 **自选（及近期打开过分钟线的票）加厚**。个股详情 m1/m5 与堆叠卡 sparkline **同一套规则**。不把分钟线当 PIT 回测源。

### Problem Frame

- 现状：详情切 1分/5分 **无交易时段门控**，但几乎全票在非交易时段 **失败或空白**；用户得不到「为何空」的可操作解释。
- 今日看盘堆叠卡 live sparkline 挂在 `refreshRealtimeQuotes` 内，**非交易时段 early-return**，与详情体验分裂。
- 仓库已有 `storage/intraday_quotes.db`、`scripts/collect_intraday.py --mode close`、launchd `com.zcdeng.kss.collect_intraday`、页内 `_persist_page_pull`，但 **桌面分钟图主路径只消费 live bridge 响应**，无本地读回。
- 根因是 **会话级分钟序列在收盘后源不可用且无回退**，不是日线陈旧。

### Key Decisions

- **KD1.** 非交易时段 **不禁用** 分钟线；目标是 **最近完整交易会话** 可看。
- **KD2.** 取数策略：**源优先**；源空/失败 → **本地降级**；禁止「无文案空白」。
- **KD3.** 本地如何来：**全池盘后** 收完整会话 + **自选加厚** + 页内成功拉取附带沉淀。
- **KD4.** **同一规则** 覆盖：个股详情 1分/5分 + 今日看盘堆叠卡 sparkline。
- **KD5.** 界面标明 **源拉** vs **本地会话存档**。
- **KD6.** 分钟线仍为 **前向观测层**（`forward_observed`），不进 PIT 回测。
- **KD7.** 复用 `intraday_quotes.db` / close 采集 / page_pull，扩展 **读路径 + 覆盖承诺**，不另起存储。

### Actors / Flows

- **A1–A5.** 见 brainstorm 原文：详情切 TF → 源或本地；堆叠卡非交易时段同规则；盘后全池底稿 + 自选更稳。

### Requirements

- **R1–R10.** 同 brainstorm（非交易时段可渲染或可理解失败；源优先本地降级；来源标签；全池盘后；自选加厚；页内附带存；堆叠卡同规则；双失败诚实；覆盖可观测）。

### Scope Boundaries

**In:** 详情 m1/m5、堆叠卡 sparkline、live→local 读路径、全池 close + 自选加厚、来源标签、覆盖/失败可观测。  
**Deferred:** 多日分钟对比 UI；分钟回测/PIT 扩大；tick/盘口；美股隔夜分钟主路径；推送通知。  
**Outside:** 改 `cs_data`/日线新鲜度合同；混用实时价徽标语义；下单。

### Success Criteria

- **SC1–SC4, SC6.** 同 brainstorm（收盘后自选 3 票可读；断源本地降级；周末上一交易日；堆叠卡非交易时段有会话分时；盘中不回退）。
- **SC5.** close 完成后 **registered complete ≥ 90%**；自选文件存在时 **自选 complete ≥ 95%**；否则 fail-loud（非静默成功）。

### Outstanding Questions (resolved for planning)

| OQ | Product default (HOW) |
|----|------------------------|
| OQ1 5m | **只规范沉淀 1m**；5m 展示优先 live 5m；live 失败则 **本地 1m 聚合**（不强制第二份 5m 全池库） |
| OQ2 保留 | **全池至少最近 1 个 complete 会话**；自选目标 **≥5 个交易日**（受东财 ~5 日窗约束，Longbridge 不承诺更深） |
| OQ3 加厚 | close 任务 **自选优先跑 + 失败有界重试**；页内成功拉取继续 page_pull |

### Assumptions

- **A-1.** 「最近完整会话」= 该标的最近一个 **已收盘 A 股交易日** 的连续分钟序列。
- **A-2.** `IntradayStore.list_canonical_bars` / coverage `complete` 可支撑「本地会话」读模型（可补薄查询 helper）。
- **A-3.** 收盘后源可能 empty；不得假设 live 永可用。

### Sources

- Brainstorm 2026-07-10 + grounding `/tmp/compound-engineering/ce-brainstorm/intraday-afterhours-20260710/grounding.md`
- `docs/plans/2026-06-22-005-feat-intraday-data-layer-plan.md`, `docs/plans/intraday-data/prd-intraday-data-layer.md`
- `docs/plans/2026-07-10-004-feat-index-card-stack-intraday-sparkline-plan.md`
- Code: `StockBrowserView.loadIntraday`, `KSSStore.refreshRealtimeQuotes` / `refreshRealtimeSparklines`, bridge `_intraday_bars` / `_persist_page_pull`, `kss/data/intraday_store.py`, `scripts/collect_intraday.py`

---

## Planning Contract

### Summary (HOW)

在 **bridge `intraday-bars` 单响应**里做 **live 优先 → 本地 complete 会话回填**，返回 `source`/`session_date`；Swift 详情与堆叠卡 **解耦「非交易时段跳过」与「会话分钟序列」**；UI 展示来源；盘后 close 保证全池 + 自选优先。

### Key Technical Decisions

- **KTD1. 回退落在 bridge，不在 Swift 各调一遍**  
  `intraday-bars`：先现有 provider `fetch_bars`；`ok` 且 bars **充分** → `source=live` + page_pull。否则查 `IntradayStore` 该 symbol 最近 **complete** 会话 1m bars → `source=local`；仍无则 `bars=[]` + 明确 `error`/`hint`（含「无本地存档」）。  
  **充分性（防 live 半截冒充成功）：** 若 live bars 非空但数量 **&lt; 期望会话 bar 数的 50%**（期望来自 session_profile 或默认全日 1m ≈ 240），**仍尝试 local complete**；若 local 更完整则用 local 并 `source=local`，否则保留 live 并 `source=live_partial`（UI 仍可渲染，但文案诚实）。  
  **Rationale:** 详情与 sparkline 共用；测试可钉 Python 纯路径。

- **KTD2. 「完整会话」判定**  
  本地读：优先 coverage 状态为 **complete** 的 `trade_date`；仅 page_pull 半成品且未 complete → **不得**作为成功降级（hint：存档不完整）。返回 bars 时 **只含该 `session_date` 一根会话**，禁止把多日 canonical 混进一条序列。

- **KTD3. 5m**  
  Live 仍请求 interval=5。Local 无 5m 时由 bridge **从本地 1m 聚合**（OHLCV 标准桶）或返回 1m 并让 chart 侧选 5m 失败提示——**推荐 bridge 聚合**，chart 契约仍收 `bars`。

- **KTD4. 堆叠卡与 quote 门控解耦**  
  `refreshRealtimeQuotes` 非交易时段可继续 **跳过 longbridge-quote 定时刷新**（实时价语义不变），但 **必须仍调用** 会话 sparkline 刷新路径（live bars → local fallback）。盘中保持现路径。

- **KTD5. 来源标签**  
  Swift 模型扩展可选 `source: String?`、`sessionDate: String?`（snake_case decode）。详情状态条：`源 · 1分` / `本地 · 2026-07-10`；堆叠卡用 `.help` 或极简角标。

- **KTD6. 自选加厚 + 自选真源**  
  桌面自选在 `@AppStorage("watchlistSymbols")`（`ContentView`），**collector 默认只认 `IntradayStore.list_registered_symbols`，读不到 AppStorage**。  
  **HOW：** App 在自选变更时写 **state-root 可读文件**（如 `storage/watchlist_symbols.txt` 或既有 bridge 可写路径），close 任务 **读该文件优先排序**；文件缺失则仅用 registered 序。失败有界重试仍作用于优先队列。页内 page_pull 保持。不新增第二套 DB。

- **KTD7. 覆盖可观测 + SC5 数值**  
  close run 结束后：对 **registered 全池** 统计当日 complete 比例；**&lt; 90%** → 非零或既有告警通道 fail-loud（与 collector 既有 coverage 语义对齐，不新造 App 页）。自选子集目标 **≥ 95%** complete（加厚队列优先）。

### High-Level Technical Design

```text
[Detail m1/m5]                [Stack sparkline]
      |                              |
      v                              v
bridge intraday-bars(symbol, interval)
      |
      +-- live fetch_bars
      |      sufficient? --yes--> source=live + page_pull
      |      partial/empty? --+
      |                      v
      +-- IntradayStore last complete session (single trade_date)
      |      better/exists? --yes--> source=local
      |      no --> live_partial if any bars else error+hint

refreshRealtimeQuotes:
  if !isTradingSession:
    skip quote poll
    still refreshSparklinesSession()  // NEW seam
  else:
    existing quote + sparkline path
```

### Implementation Units

### U1. Bridge: live → local for `intraday-bars`

- **Goal:** 单命令满足 R2/R3/R9 的数据契约。  
- **Requirements:** R2, R3, R9, SC1–SC3  
- **Dependencies:** none  
- **Files:**  
  - modify: `scripts/kss_app_bridge.py` (`_intraday_bars_inner`)  
  - modify: `kss/data/intraday_store.py`（若缺「按 symbol 最近 complete 会话 bars」薄查询）  
  - create/modify: `kss/tests/test_bridge_intraday_local_fallback.py` 或扩展 `kss/tests/test_bridge_longbridge.py`  
- **Approach:**  
  1. Live 失败/空 → 解析 symbol 到 instrument_id → 取最近 complete trade_date → 导出 OHLCV 与 live 同 shape 的 `bars` 列表。  
  2. 响应增加 `source`（`live`|`local`）、`session_date`（YYYY-MM-DD）、保留 `eligibility=forward_observed`。  
  3. 5m：live 失败则 1m local + 聚合。  
- **Execution note:** 用 fixture DB 或 monkeypatch store，不依赖真网。  
- **Test scenarios:**  
  - Live ok → source=live，不读 store。  
  - Live empty + store complete → source=local，bars 非空。  
  - Live empty + store miss → error/hint 含无存档语义，bars=[]。  
  - Live 仅 20 根 + store complete 全日 → 选用 local，source=local。  
  - local bars 仅含单一 session_date。  
  - interval=5 local path 聚合后 bar 数合理（全日 1m 聚合约 48 桶量级可范围断言）。  

### U2. Swift 模型 + 详情来源标签与失败文案

- **Goal:** 详情 m1/m5 消费 `source`/`session_date`，失败不可静默。  
- **Requirements:** R1, R4, R9, SC1–SC3  
- **Dependencies:** U1  
- **Files:**  
  - modify: `Sources/KSSDesktop/Models/KSSModels.swift` (`IntradayBars`)  
  - modify: `Sources/KSSDesktop/Views/StockBrowserView.swift` (`loadIntraday`, chart status)  
  - create/modify: `Tests/KSSDesktopTests/IntradaySourceLabelTests.swift`（文案/解码纯测）  
- **Approach:**  
  - Decode 新字段可选。  
  - `chartStatusText` / 状态条：live vs 本地·日期；error 用 bridge hint。  
  - 日线主图在分钟失败时保持（已有行为，补回归）。  
- **Test scenarios:**  
  - JSON 含 source=local + session_date 解码。  
  - 缺 source 仍可解码旧响应。  
  - 标签格式：本地路径含日期。  

### U3. 堆叠卡：非交易时段会话 sparkline

- **Goal:** R4/R8/SC4；quote 仍可按时段停，分时不整层死。  
- **Requirements:** R4, R8, SC4, SC6  
- **Dependencies:** U1  
- **Files:**  
  - modify: `Sources/KSSDesktop/Services/KSSStore.swift` (`refreshRealtimeQuotes`, `refreshRealtimeSparklines`)  
  - modify: `Sources/KSSDesktop/Views/DashboardView.swift`（若需来源 help；可选）  
  - modify: `Tests/KSSDesktopTests/RealtimeMergeTests.swift` 或 Store 测（若有测试缝）  
- **Approach:**  
  - 抽出 `refreshSessionSparklines`：不依赖 `isTradingSession`。  
  - `!inSession` 时：跳过 quote 轮询，仍刷新 stacks 的 bars（live→local 在 bridge）。  
  - `inSession` 时：保持现序。  
  - strip 快照 sparkline 仅在 bars 仍空时回退（R9）。  
- **Test scenarios:**  
  - 逻辑测：`!inSession` 路径仍调用 sparkline 刷新（可用依赖注入/spy 或拆 pure flag）。  
  - 有 local bars 时 closes 非空。  

### U4. 全池 close + 自选优先加厚

- **Goal:** R5, R6, R7, SC5  
- **Dependencies:** none（可与 U1 并行）  
- **Files:**  
  - modify: `Sources/KSSDesktop/Views/ContentView.swift`（或 Store）：自选变更时同步写 state-root 列表文件  
  - modify: `scripts/collect_intraday.py`（读该文件优先排序 + 有界重试；覆盖率门槛）  
  - verify: `deploy/launchd/com.zcdeng.kss.collect_intraday.plist` / `scripts/run_collect_intraday.sh` 仍 15:05  
  - modify: `kss/tests/test_intraday_collector.py`  
- **Approach:**  
  - 不改库 schema；**自选桥接文件 + 采集序 + 重试 + 覆盖门槛**。  
  - page_pull 失败不阻断 UI（已有）。  
  - 文件缺失：close 仍跑 registered 全池，不崩溃。  
- **Test scenarios:**  
  - 给定 watchlist 文件，符号序前缀含自选。  
  - 文件缺失：registered 序仍可跑。  
  - complete 比例 &lt; 90% → 触发告警/非零（mock coverage）。  
  - dry-run 不写库。  
  - 单票失败不取消整池（既有契约保持）。  

### U5. 集成验证清单（手工 + 轻量 smoke）

- **Goal:** SC1–SC6 可勾选。  
- **Dependencies:** U1–U4  
- **Files:**  
  - optional: `docs/plans/` 不强制；实现 PR 描述勾选  
- **Approach:**  
  - 交易日 15:05 后：自选 3 票 m1；断网或 mock 源失败看本地标签；周末再验上一交易日。  
  - 堆叠卡非交易时段有线。  
  - 盘中 spot-check 不回归。  
- **Test expectation:** none for new automated suite beyond U1–U4 — desk smoke is the gate.  

### Verification Contract

- `pytest` 覆盖 U1 local fallback + U4 排序  
- `swift test` 覆盖 IntradayBars 解码/标签  
- 手动：收盘后/周末 SC1–SC4；盘中 SC6  
- 可选：`collect_intraday --mode close --dry-run`  

### Definition of Done

- [ ] U1–U4 合并；桥响应含 source/session_date  
- [ ] 非交易时段详情 m1 不静默空白  
- [ ] 堆叠卡非交易时段可会话分时或诚实回退 strip  
- [ ] 盘后 close 自选优先；全池目标不变  
- [ ] SC1–SC6 桌面或脚本验证记录在 PR  

### Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| page_pull 非 complete，误当完整会话 | 仅 complete 降级成功 |
| 全池 close 耗时/源限流 | 自选优先；serial 既有限速；失败可观测 |
| 非交易时段仍打 live 浪费 | 可接受；失败快回落本地；后续可加「先 local 后 live」开关但不改产品默认 |
| 5m 聚合边界错误 | 单测桶数范围；盘中仍 live 5m |

### Deferred to Follow-Up Work

- 多日分钟对比 UI  
- MCP 暴露 session bars 给 agent  
- local-first 可选策略（产品默认仍 live-first）  

### System-Wide Impact

- Bridge 契约扩展：旧客户端忽略新字段安全  
- launchd close 任务更关键（覆盖）  
- 实时价定时器语义不变，仅 sparkline 解耦  

---

## End of Plan
