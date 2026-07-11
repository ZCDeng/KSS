---
title: "MI Walk-Forward Signal Pack - Plan"
date: 2026-07-11
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
type: feat
topic: mi-walkforward-signal-pack
status: active
origin: docs/plans/2026-07-11-007-feat-mi-walkforward-signal-pack-plan.md
product_contract_preservation: "Product Contract unchanged"
---

# MI Walk-Forward Signal Pack - Plan

## Goal Capsule

**Objective:** 每个交易日收盘后，对自选股做 MI 的 walk-forward 滚动重估（只动 N/阈值），产出统一「信号包」；KSS 内嵌 K 线自动标注买卖点与当前动作，自选个股复盘自动嵌入研究级 MI 段。

**Product authority:** 信号包是图与复盘的唯一日终真源；每股钉死买卖规则形态；解释与可复现是上线底线，省事是默认交付。

**Open blockers:** 无。

## Product Contract

### Summary

在现有自选 MI 寻优与日线回测之上，补上**滚动 walk-forward 重估**与**日终自动化闭环**：跑完后自选每只有一致的动作、参数、买卖点与 MI 序列，同时进入桌面内嵌图表标注与个股复盘。不接 tradingview.com 云端，不做盘中重估。

### Problem Frame

当前链路是一次性脚本：`backtest_mi_watchlist` 用单次 70/30 切分选参，不会随时间滚动；结果停在报告/JSON，进不了自选复盘，也画不进 App 的 TradingView 风格 K 线（lightweight-charts）。用户已在自选（688017/688322）上验证过规则价值，痛点是**参数过期、展示断裂、日终仍要手跑**。

### Key Decisions

- **三端一体，日终一次：** 收盘后重估 → 信号包 → 图 + 复盘；盘中不刷新参数。
- **Signal Pack 为唯一日终真源：** 图与复盘只消费信号包，禁止各写一套信号。
- **只滚 N 与阈值：** 买卖规则形态（entry/exit/filter 语义）每股钉死；不日更规则脸。
- **图表表面 = KSS 内嵌 lightweight-charts：** 非 tradingview.com；四类标注全要（历史买卖点、当前动作条、生效参数角标、MI 辅助线）。
- **复盘深度 = 研究级：** 短结论 + 近若干笔买卖表 + 相对上期参数变化。
- **新自选：** 先用全局默认形态出信号，复盘/角标高亮「未钉死」；不阻塞日终任务。
- **验收分层：** 可复现解释 + 自动写入为 v1 门禁；滚动 OOS「不丢人」为后续度量，不挡首版。

### Actors

| Actor | Role |
|-------|------|
| 研究员本人 | 维护自选与每股钉死形态；日终扫复盘与图上标注 |
| 日终自动化 | 在交易日收盘后对自选批量跑 WF 并写信号包 / 触发图与复盘消费 |
| KSS 桌面 | 渲染内嵌 K 线标注；展示自选个股复盘中的 MI 段 |

### Key Flows

**F1 · 日终 Signal Pack 生成（自选全量）**

1. 读取当前自选列表与每股钉死形态（缺则默认形态 + `unpinned` 标记）。
2. 对每只用 walk-forward 在滚动训练窗上重估 **N 与阈值**（形态固定）。
3. 用最新生效参数重放可见历史，生成买卖点、当前动作、pred 分、MI/A 序列摘要。
4. 写出当日信号包（每股一条，含 `asof`、参数、动作、理由、trades、series）。
5. 失败有界：单票数据不足/计算失败不拖垮整池；复盘写明跳过原因。

**F2 · 图表消费**

1. 打开自选个股详情时，加载该票最新信号包。
2. 主图：历史 BUY/SELL 标记（默认与复盘同窗的 `trades_preview`）；角标：生效 N、规则名、上次重估日、**unpinned 警示**；当前动作条（含未钉死副文案）。
3. 副图：MI 辅助线（仅日线 TF；切入 1m/5m/W/M/Y 时清除 K 线标注并提示「MI 仅日线」）。

**F3 · 复盘消费**

1. 自选个股复盘生成或刷新时注入 MI 研究级段落；**桌面详情必有结构化 `miSignal` 卡片**（不仅依赖 md 刮取）。
2. 内容：动作与理由、生效参数、pred_score、相对昨动作/仓位变化、近 N 笔买卖表、相对上期参数变化；`unpinned` 时高亮提示。

### Requirements

**Walk-forward 与参数**

- R1. 每个交易日收盘后，对自选列表中每只股票执行一次 MI walk-forward：**仅重估 N 与阈值**，不改钉死的 entry/exit/filter 形态。
- R2. 每股形态来自显式配置（钉死）；缺省时用全局默认形态，并标记 `unpinned=true`，不得静默冒充已确认形态。
- R3. 同一输入（行情缓存 + 配置 + 日历日）重跑，信号包中动作、参数、买卖点集合必须一致（可复现）。
- R4. 信号执行语义保持现有研究纪律：t 日收盘信号，展示/归因按 t+1 开盘执行口径说明；不得暗示收盘价可成交。

**Signal Pack**

- R5. 日终产出统一信号包，至少含：`symbol`、`asof`、`status`（ok|skipped|error|stale）、`reason`、生效 `N`/阈值、`prev_N`/`prev_thr` 或 `param_delta`、钉死规则 id、`unpinned`、当前动作、`prev_action`、pred_score、`trades` 全量、`trades_preview`（近 8–12 笔）、MI 序列、重估元数据。
- R6. 图与复盘**只读**信号包；禁止在 UI 侧重算另一套买卖点。

**图表标注（KSS 内嵌图）**

- R7. 内嵌 K 线展示：历史买卖点标记、当前动作条、生效参数角标（含 unpinned）、MI 辅助线（四类均在 v1）。
- R8. 无信号包、`status≠ok`、或 `asof` 早于参考交易日（包过期）时：图与复盘用同一 `status`+`reason` 空态/占位，不显示陈旧买卖点冒充今日。

**自选复盘**

- R9. 自选个股复盘含研究级 MI 段：动作+理由、参数、pred、仓位变化、近笔买卖表、参数相对上期变化；`unpinned` 高亮。
- R10. 单票失败时整池仍完成；该票复盘写失败/跳过原因。

**范围与节奏**

- R11. 自动化默认为交易日**日终一次**；不要求盘中重估 N/阈值。
- R12. 不对接 tradingview.com 云端图表/API 作为 v1 交付面。

### Acceptance Examples

- AE1. **Given** 自选含 688017 且已钉死形态，**When** 日终任务成功，**Then** 存在该日信号包，图上可见买卖标记与 MI 线，复盘出现研究级 MI 段，且图与复盘动作字段一致。
- AE2. **Given** 新股刚进自选未钉死，**When** 日终运行，**Then** 仍产出信号与标注，但复盘与角标标明未钉死/默认形态。
- AE3. **Given** 某票本地日线不足，**When** 日终运行，**Then** 其他自选正常出包；该票复盘说明跳过，图为空态而非错信号。
- AE4. **Given** 连续两日 N 从 12 变为 9，**When** 用户读复盘，**Then** 可见「相对上期」参数变化说明。

### Success Criteria

- S1. 日终后打开任一自选：复盘有 MI 段、K 线有四类标注之一致集合，无需手跑脚本。
- S2. 任意信号可追溯到信号包内的 `asof` + 生效参数 + 规则 id。
- S3. 固定输入重跑 diff 为空。
- S4.（后续，非 v1 门禁）滚动 OOS 相对固定 N=12 的对比报表可生成。

### Scope Boundaries

**In v1**

- 自选股范围；MI 单标的时序择时；日终 WF（N/阈）；Signal Pack；KSS 内嵌图标注；个股复盘研究级段。

**Deferred for later**

- 滚动 OOS 质量门禁自动告警；全网格自动换形态；盘中分钟刷新；预测台账验真闭环；paper 自动下单。
- tradingview.com Pine/画线导出。
- 非自选全市场扫描。

**Outside this product's identity**

- 个性化投资建议或自动实盘委托。
- 用 LLM 临场编造买卖点。

### Dependencies / Assumptions

- A1. 自选列表以 `storage/watchlist_symbols.txt` 为源。
- A2. 行情以本地日线缓存为真源。
- A3. `TechnicalFactors.mi` 与 T+1 执行口径为计算基线。
- A4. 现有 LGB/多因子 walk-forward **不**覆盖 MI N/阈滚动——本特性新建 MI 专用路径。
- A5. `chart.html` 当前仅有 `kssSetData` / 主题 / 日内 API，**无 markers**——v1 需扩展图表契约。

---

## Planning Contract

### Summary

实现路径：库化 **MI 规则回放 + walk-forward 重估** → 写出 **Signal Pack** → **日终 CLI/cron** 刷自选 → **daily_review 注入** + **bridge 暴露给桌面** → **chart.html / ChartWebView 消费标注**。优先复用 `scripts/backtest_mi_watchlist.py` 的规则与成本口径，以及 `WalkForwardCombiner` 的时间切分精神（训练窗内选参、窗外应用）。

### Problem Frame (implementation)

- 有：固定 N 回测、自选网格 + 单次 IS/OOS、复盘按股 md、内嵌 K 线日线数据注入。
- 缺：滚动重估、统一信号包、图 markers/MI 副图、复盘 MI 段、日终钩子。

### Key Technical Decisions

- **KTD1. Signal Pack 为唯一真源。** 路径：`storage/mi_signals/{asof}/{symbol}.json` + `storage/mi_signals/latest/{symbol}.json`。图与复盘只读 pack。OHLCV 与 bridge 图同源（统一 loader，避免 `cs_data/` 与根目录 CSV 双路径错位）。
- **KTD2. 配置与信号分离。** `storage/mi_rules.yaml`：每股钉死 `entry`/`exit`/`filter`（形态键）；缺省 `defaults` + `unpinned=true`。WF 输出的 N/thr **只写在 pack 内**（不另建 UI 可读的 mi_params 旁路）。
- **KTD3. 规则 API = 形态键 + 可选 thr 字典。** 钉死 entry/exit/filter 字符串；`thr` 仅 z 族（如 `mi_z_gt` 的上下阈）可搜，穿越族 `thr=null` 只搜 N。默认：N∈{6,9,12,14,20}；z 上下阈网格显式列表；`train_window=252`、`retrain_freq=20`、holdout=训练窗末 63 日 Sharpe、`min_trades≥4`。
- **KTD4. 库优先、脚本薄。** `kss/strategies/mi_signal.py` + `kss/backtest/mi_walk_forward.py` + `kss/strategies/mi_pack.py`；CLI 薄封装。
- **KTD5. 图表 `kssSetMiOverlay` + 生命周期。** markers / mi 线 / actionBanner / paramBadge（含 unpinned）；JS 缓存 lastOverlay，在 `setData`/`renderTF` 后 re-apply；非日线 TF 与 intraday **强制 clear**；空态用共享 `status`+`reason`。markers 默认绑 `trades_preview`。
- **KTD6. 复盘：md 段 + 必选结构化 `miSignal`。** `daily_review` 写 `### MI 滚动信号`；bridge stock_detail **必返回** `miSignal`；桌面详情 **必渲染** `MISignalCard`（当前 review 卡吃不下研究级字段）。
- **KTD7. 日终串行。** 顺序钉死：`mi-signal-pack` → `formal-daily-review`（读当日 pack 注入）。禁止并行。v1 仅此一条触发面（不做 daily-review-symbol 后钩）。pack 软失败写 skip，不阻断 review。

### High-Level Technical Design

```text
watchlist_symbols.txt + mi_rules.yaml
            │
            ▼
   run_mi_signal_pack (日终)
            │
            ├─ load OHLCV (cs_data)
            ├─ mi_walk_forward.reestimate(N, thr)
            ├─ mi_signal.replay → trades, action, series
            └─ write storage/mi_signals/{asof|latest}/
                        │
          ┌─────────────┴──────────────┐
          ▼                            ▼
  chart: kssSetMiOverlay        daily_review + bridge stock detail
  markers / MI / badge / action   ### MI 滚动信号 + miSignal JSON
```

**Walk-forward（方向性伪码，非实现规格）：**

```
for t in retrain_dates:
  train = bars[t - train_window : t]
  best = argmax_{N, thr in grid} score(train, fixed_rule, N, thr)
  apply best on [t, t+retrain_freq) without peeking future bars
last_best used for asof signal + full-history trade list under last_best
```

### Scope Boundaries (implementation)

- 不改 LGB `BacktestEngine.walk_forward` 截面路径。
- 不把 MI 塞进 prediction ledger（可 follow-up）。
- 不做 tradingview.com 导出。

### Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| 滚动重估过拟合 / 规则日日变脸 | 只滚 N/阈；最小交易数；可选 retrain_freq>1 冻结 |
| chart.html 无 markers 历史 | 独立 API + 单测/截图级手工验；空态显式 |
| 复盘 md 解析脆弱 | bridge 结构化 `miSignal` 字段优先 |
| 日终算力（自选变多） | 默认仅 watchlist；单票失败隔离；缓存最新 params |

### Delivery slices（thin-slice）

| Slice | Units | 可演示价值 |
|-------|-------|------------|
| **A** | U1–U3 | CLI 可复现 pack |
| **B** | U4a + U5 | 日终/手跑后复盘+详情卡有 MI |
| **C** | U6a + U6b | 四类图标注 |
| **D** | U4b + U7 | cron 无人值守 + e2e |

### Open Questions (implementation-time)

- 688017/322 初始钉死写入 `mi_rules.yaml` 的具体键（来自会话寻优：017=N20 零轴买/A下穿卖；322=mi_z 进出——实现时固化）。
- z 阈值网格具体列表（默认建议 entry 侧 {0.3,0.5,0.8,1.0}，exit 侧 {0,-0.3,-0.5}，可配置）。

---

## Implementation Units

### U1. MI 规则引擎库（特征、仓位、trades、pred、reason）

**Goal:** 从 OHLCV + 钉死形态键 + N + 可选 thr，确定性产出仓位、买卖点、当前动作、`reason`、pred_score、MI 序列。

**Requirements:** R3, R4, R5（计算侧）

**Dependencies:** 无

**Files:**
- create: `kss/strategies/mi_signal.py`
- create: `kss/tests/test_mi_signal.py`

**Approach:** 迁出 watchlist 脚本逻辑；形态键固定；z 族读 `thr`；穿越族忽略 thr。`reason` 由规则键+触发条件确定性生成。

**Patterns to follow:** `scripts/backtest_mi_watchlist.py`；`TechnicalFactors.mi`。

**Test scenarios:**
- Happy: 合成序列 `mi_cross_up_0` 出 BUY/SELL；`exec_*` 相对 `signal_*` 为下一 bar。
- Happy: z 规则 thr 两侧仓位翻转。
- Edge: 预热期仓位全 0。
- Edge: 双跑 `replay` dict 相等（含 reason/pred）。
- Error: 非法 entry raise ValueError。

**Verification:** 单测绿；字段表含 reason/action/trades/mi。

---

### U2. Walk-forward 重估（仅 N × thr）

**Goal:** 固定形态键下滚动选 N/thr；输出 `param_history`、生效参数、末日参数重放 trades。

**Requirements:** R1, R3

**Dependencies:** U1

**Files:**
- create: `kss/backtest/mi_walk_forward.py`
- create: `kss/tests/test_mi_walk_forward.py`

**Approach:** `train_window=252`、`retrain_freq=20`；训练窗末 63 日 holdout Sharpe，`min_trades≥4`。参数只应用于 retrain 之后的未来段。当日展示用末日参数重放 + `param_history`/`param_delta`。

**Execution note:** fixture 锁定「未来段不用未来参数」。

**Test scenarios:**
- Happy: 前半/后半最优 N 不同 → `param_history` 变化。
- Edge: 训练窗不足 → skip 结构。
- Integration: 固定 seed 双跑 N/thr 一致。

**Verification:** holdout 参数仅来自过去 retrain。

---

### U3. 规则配置 + Signal Pack I/O

**Goal:** 读自选与钉死形态；写出/读回 pack（含 status 体系与 latest）。

**Requirements:** R2, R5, R6, R8, R10

**Dependencies:** U1, U2

**Files:**
- create: `storage/mi_rules.yaml`（017/322 钉死 + defaults）
- create: `kss/strategies/mi_pack.py`
- create: `kss/tests/test_mi_pack.py`

**Approach:** `schema_version: 1`；`status∈{ok,skipped,error,stale}`；投影辅助：`to_mi_signal(pack)` / `to_mi_overlay(pack)`（复盘与图同源）。

**Test scenarios:**
- Happy: 写读字段齐全（含 param_delta、trades_preview、reason）。
- Covers AE2: unpinned + defaults。
- Covers AE3: skip 不阻断批写。
- Covers R8: asof 过期 → stale 不可作 UI ok 展示。

**Verification:** golden pack 快照。

---

### U4a. CLI 批跑（Slice B 入口）

**Goal:** 对自选手跑生成全量 pack。

**Requirements:** R1, R10, R11

**Dependencies:** U3

**Files:**
- create: `scripts/run_mi_signal_pack.py`
- create: `kss/tests/test_run_mi_signal_pack.py`

**Approach:** `--watchlist` / `--symbols` / `--asof`；统一 OHLCV loader。

**Test scenarios:**
- Happy: 双票 fixture → 2× ok pack。
- Error: 缺数票 skipped，整体成功。

**Verification:** 真实自选 dry-run 出 pack。

---

### U4b. Bridge task + cron 串行（Slice D）

**Goal:** 无人值守：`mi-signal-pack` 先于 `formal-daily-review`。

**Requirements:** R11, S1

**Dependencies:** U4a

**Files:**
- create: `scripts/run_mi_signal_pack_daily.sh`
- modify: `scripts/kss_app_bridge.py`（task `mi-signal-pack`）
- modify: `kss/config/cron_jobs.yaml` / formal-daily-review wrapper（串行前置）
- create: `kss/tests/test_bridge_mi_signal_pack.py`

**Approach:** 禁止并行；pack 软失败不阻断 review。

**Test scenarios:**
- Integration: bridge run 返回 artifacts。
- Cron 清单可见且顺序正确（文档或 manifest 断言）。

**Verification:** cron 路径文档化。

---

### U5. 复盘 md 段 + 桌面 `miSignal` 卡（Slice B）

**Goal:** 研究级 MI 在 console md **与** App 详情卡可见且一致。

**Requirements:** R6, R9, R10, AE1, AE2, AE4

**Dependencies:** U3, U4a

**Files:**
- modify: `scripts/daily_review.py`
- modify: `scripts/kss_app_bridge.py`（stock_detail **必**含 `miSignal`）
- modify: `Sources/KSSDesktop/Models/KSSModels.swift`
- modify: `Sources/KSSDesktop/Views/StockBrowserView.swift`（`MISignalCard`）
- create: `kss/tests/test_daily_review_mi_section.py`

**Approach:** `format_mi_section` + 结构化卡：动作/理由/N·thr/asof/unpinned/prev_action/param_delta/近笔/失败 reason。

**Test scenarios:**
- Covers AE1/AE2/AE4。
- Edge: missing pack → 统一占位 status，非假买卖点。

**Verification:** 017 review md + bridge JSON 动作与 pack 一致。

---

### U6a. 图 overlay 契约：banner / badge / MI 线 / 空态（Slice C）

**Goal:** `kssSetMiOverlay` 生命周期正确；三件套 + 空态。

**Requirements:** R6, R7（部分）, R8, AE2（角标 unpinned）

**Dependencies:** U3

**Files:**
- modify: `Sources/KSSDesktop/Resources/chart.html`
- modify: `Sources/KSSDesktop/Views/ChartWebView.swift`
- modify: bridge stock payload `miOverlay`

**Approach:** 缓存 re-apply；非日线 clear；badge 含 unpinned；布局：顶栏 banner、badge 靠 TF，MI 为可切换副带。

**Test scenarios:**
- Covers R8 clear。
- 切 1m 再回日：无串 marker / MI 仅日线提示。

**Verification:** 手工 smoke 清单项勾选。

---

### U6b. 买卖 markers（Slice C）

**Goal:** BUY/SELL 标记（`trades_preview`）；完成 R7 四类。

**Requirements:** R7, R12, AE1

**Dependencies:** U6a

**Files:**
- modify: `Sources/KSSDesktop/Resources/chart.html`
- modify: `ChartWebView` / fullscreen 同源注入

**Approach:** `setMarkers` 优先；失败降级记注释。全屏与内嵌共用。

**Test scenarios:**
- Covers AE1 图上可见买卖点与 pack preview 日期一致。
- R12: 无 tradingview.com 依赖。

**Verification:** 017/322 UI smoke。

---

### U7. E2E 可复现 + runbook（Slice D）

**Goal:** pack 双跑 hash 一致；review 与 pack 动作一致；runbook 一行。

**Requirements:** S1–S3, AE1–AE3, R12

**Dependencies:** U4a, U5, U6b

**Files:**
- create: `kss/tests/test_mi_signal_pack_e2e.py`
- modify: 简短 runbook（`docs/` 或脚本头注释）

**Approach:** 仅自动化 pack+review；图验收引用 U6 smoke 清单，不在本单元加 feature。

**Test scenarios:**
- Covers AE1–AE3 自动化部分；S3 hash。

**Verification:** e2e 绿。

---

## Verification Contract

| Gate | Outcome |
|------|---------|
| Slice A | U1–U3 单测绿；pack schema 含 R5 字段 |
| Slice B | review md + `miSignal` 与 pack 动作一致 |
| Slice C | 四类图标注 UI smoke（017/322 + 未钉死第三票） |
| Slice D | cron 串行；e2e 双跑一致 |
| Non-goals | 无 tradingview.com；无盘中 WF；无并行 pack/review |

## Definition of Done

- [ ] Slice A–D 按序关闭（允许 C 与 D 部分并行，但 A 的可复现门禁不可跳）
- [ ] R1–R12 均有实现挂载或 Verification 负面检查
- [ ] 日终：`mi-signal-pack` → `formal-daily-review` 可无人值守
- [ ] 图与复盘动作/`trades_preview` 来自同一 pack

## Sources & Research

- Product: 本文件 Product Contract（ce-brainstorm 对话）
- Code: `scripts/backtest_mi_watchlist.py`, `kss/features/technical.py`, `kss/backtest/walk_forward_combiner.py`, `scripts/daily_review.py`, `scripts/kss_app_bridge.py` (`daily-review-symbol`, `_stock_review`), `Sources/KSSDesktop/Resources/chart.html` (`kssSetData` only)
- Grounding: `/tmp/compound-engineering/ce-brainstorm/mi-wf-tv-20260711/grounding.md`
