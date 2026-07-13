---
title: KSSDesktop 真机反馈修复 - Plan
type: fix
date: 2026-07-13
topic: desktop-feedback-polish
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# KSSDesktop 真机反馈修复 - Plan

## Goal Capsule

- **目标**：修复真机 dogfood 测试发现的 5 项问题——实时数据标签失实、设置入口呈现单薄、设置页内部风格不统一、任务入口命名混淆、边栏图标尺寸不一致。
- **产品权威**：用户真机测试的直接反馈。
- **待解阻塞项**：无——主要分歧点（设置入口位置是否要动、任务分区改造范围）已在 brainstorm 对话中消解。

## Product Contract

### Summary

修复「实时」badge 只看"是否曾拿到过活报价"、不比对数据新鲜度的问题，并排查行情静默失效的根因；设置入口留在工具栏但加强呈现分量；统一设置页与任务工具栏页的视觉语言；区分工具栏「任务」与设置页「任务」的命名；边栏 GitHub 图标换成官方 Octocat 并与架构图标同尺寸。

### Problem Frame

真机测试当天，实时行情在 09:46 后停止刷新，但自选、推荐、股票池等页面的「实时」badge 持续显示，用户无法从界面判断数据已经陈旧。代码层面，badge 只检查"该标的是否曾经成功拿到过活报价"（`Sources/KSSDesktop/Models/KSSModels.swift:1695`、`Sources/KSSDesktop/Support/RealtimeMerge.swift:131`），不比对最新更新时间；单个标的软失败时也不会从缓存 map 里清除（`Sources/KSSDesktop/Services/KSSStore.swift:396-406`），导致陈旧数据可以无限期地顶着「实时」的名号展示。

同一次测试还发现四处 UI 呈现问题：设置入口在工具栏里显得单薄；设置页内「密钥」「数据源」用了 `kssCard` 视觉体系，「任务」分区却直接复用 `RunbookView` 的旧风格组件（`plan 2026-07-12-005` U5 迁移时"组件本身不动，纯搬迁"）；工具栏「任务」按钮与设置页「任务」分区同名但功能不同（前者是手动运行台，后者是定时任务配置），造成认知混淆；边栏底部「架构」图标与 GitHub 链接尺寸不一（GitHub 展开态缩小到 11pt 且附带文字 wordmark，架构固定 14pt/28×28）。

### Key Decisions

- **设置入口留在工具栏，不迁回边栏导航** — `plan 2026-07-12-005` 刚把设置、任务、架构、Seesaw 明确移出边栏导航列表（`KSSModels.swift:1453-1456`），本轮尊重这个决定，只用视觉权重（更大点击区域/文字标签）解决"单薄"的观感问题，不做位置迁移。
- **统一「任务」视觉时改动组件本体，而非只包一层壳** — `ScheduledTasksSection` 目前只被设置页「任务」分区渲染；任务工具栏页（`RunbookView` 手动运行台）按 `plan 2026-07-12-005` U5 已把该组件迁出，只保留 `PythonEnvironmentBanner`/`TaskGrid`/`TaskResultCard`。本轮直接改造 `ScheduledTasksSection` 的视觉语言为 `kssCard` 体系；若要让任务工具栏页外观也同步统一，需要另外改造其 `TaskGrid`（目前按钮用 `.buttonStyle(.bordered)`，未套 kssCard 体系），两处是两次独立改造，不是改一处自动带出两处。
- **工具栏「任务」与设置页「任务」改名区分** — 二者本质是不同工具（手动运行台+记录 vs 定时任务配置），只是同名造成混淆，本轮通过改名而非合并/删除来解决认知问题。
- **实时数据问题本轮只做"诚实展示 + 根因诊断"，不做交互层补救** — 不添加用户可触发的手动重连/重试控件；如果根因排查发现是外部接口限制（如 Longbridge 配额/限流），本轮不保证"彻底不再断"，只保证 UI 不再撒谎。

### Requirements

**实时数据可信度**

R1. 「实时」badge 的展示状态由行情实际新鲜度决定，而非仅凭"是否曾经拿到过活报价"；当自上次成功更新起超过 5 分钟（约 2.5 个轮询周期）仍未刷新，badge 降级为「已过期」状态（非实时）并显示上次更新时间。

R2. 单个标的的软失败（拿不到新报价但接口未报 `auth_failed`）不再被全局刷新时间戳掩盖——该标的的展示状态独立反映自身数据陈旧程度，不因页面上其他标的刷新成功而被误判为"实时"。

R3. 排查交易时段内实时行情静默失效（不报错但停止更新）的根因，覆盖自选、推荐、股票池等所有展示「实时」标签的页面；排查结论（无论是代码缺陷还是外部接口限制）需要记录下来。

**设置入口呈现**

R4. 设置入口保留在工具栏（不迁入侧边栏导航），但呈现方式从纯图标按钮加强为更有视觉分量的样式（如加大点击区域或补充文字标签）。

**设置页视觉一致性**

R5. 设置页「密钥」「数据源」分区与「任务」分区的视觉语言统一——字号、卡片样式、按钮风格保持一致；「任务」分区所复用的 `ScheduledTasksSection` 组件本体同步改造为 `kssCard` 体系。任务工具栏页（`RunbookView` 手动运行台）需要单独改造其 `TaskGrid`（目前按钮用 `.buttonStyle(.bordered)`，未套 kssCard 体系），使两处任务相关界面视觉一致——不能只改 `ScheduledTasksSection` 就自动覆盖两处。

R6. 工具栏「任务」入口与设置页「任务」分区改用不同名称，明确区分二者是不同工具（前者是手动任务运行台+运行记录，后者是定时任务配置）。

**边栏图标**

R7. 侧边栏底部 GitHub 入口改用官方 Octocat 图标资产，替换当前的 SF Symbol 代码符号；展开态下图标与「架构」图标保持相同尺寸（14pt 图标 / 28×28 点击区域），不再附带文字 wordmark。

### Acceptance Examples

AE1. **Covers R1, R2.** Given 实时行情在 09:46 后不再收到新 tick，When 距上次成功更新超过 5 分钟，Then 该标的的「实时」badge 降级为"已过期"状态并显示上次更新时间，不再持续显示「实时」。

AE2. **Covers R2.** Given 自选页展示多个标的、其中一个刷新失败进入过期状态、其余标的仍在刷新成功，When 全局 `realtimeUpdatedAt` 时间戳因其他标的成功而保持新鲜，Then 该过期标的仍需独立显示自己的过期状态，不被全局时间戳掩盖。

AE3. **Covers R6.** Given 用户先后打开工具栏「任务」入口和设置页「任务」分区，When 两处都展示各自内容，Then 两处的标签文案不同，用户无需读内容就能判断这是手动运行台还是定时任务配置。

### Scope Boundaries

- 不添加用户可触发的手动"重连"/重试控件——本轮只做诚实展示 + 根因诊断，交互层补救留待后续评估。
- 不扩大「实时」覆盖范围到当前未展示该标签的页面。

### Outstanding Questions

**Deferred to Implementation：**

- Longbridge 静默失效的具体根因（报价上下文冻结 vs 网络/鉴权问题）需要交易时段实跑观察 U3 的诊断日志才能定案；诊断方法已在 Planning Contract 里定（见 KTD3），结论留给实现/观察阶段。

> 改名文案、设置入口呈现方案、过期时间展示格式、Octocat 主题适配这四项此前列在 Outstanding Questions 里的问题，已在 Planning Contract 的 Key Technical Decisions 中拍板（KTD4-KTD6 及任务改名决定），不再是待解问题。

### Sources / Research

- `Sources/KSSDesktop/Models/KSSModels.swift:1695-1696` — `LongbridgeQuote.isLive` 定义：`error == nil && lastDone != nil`，不比对时间戳。
- `Sources/KSSDesktop/Support/RealtimeMerge.swift:131-136` — `hasAnyLive` 是 badge「实时」口径的判定入口。
- `Sources/KSSDesktop/Services/KSSStore.swift:396-406` — 单标的软失败不清除 map 里的旧报价。
- `Sources/KSSDesktop/Services/KSSStore.swift:415-418` — `realtimeUpdatedAt` 只要任一标的成功即整体刷新，掩盖其他标的的陈旧状态。
- `Sources/KSSDesktop/Services/KSSStore.swift:127-130,928-958` — 轮询间隔 120s；定时器只在 `auth_failed` 时停止，静默失败不触发降级。
- `Sources/KSSDesktop/Support/DailyBarFreshness.swift:24-30` 与后端 `kss/tests/test_data.py:44-66`（`CacheManager.is_stale`）— 项目里已有的按日期比对陈旧度模式，可作为实时新鲜度判定的参考写法。
- `Sources/KSSDesktop/Models/KSSModels.swift:1453-1456` — `WorkspaceSection.hidden` 明确把设置/任务/架构/Seesaw 移出侧边栏导航（引用 `plan 2026-07-12-005`）。
- `Sources/KSSDesktop/Views/SettingsView.swift:84-153,277-307,369-393` — 「密钥」「数据源」用 `.kssCard()`，「任务」分区是 `ScheduledTasksSection` 的薄包装。
- `Sources/KSSDesktop/Views/RunbookView.swift:57-58,130-230` — `ScheduledTasksSection` 的原始视觉语言（`surfaceRaised`/胶囊按钮），及其"薄包装迁移"注释。
- `Sources/KSSDesktop/Views/RunbookView.swift:18-55,520-554` — `RunbookView.body` 只渲染 `PythonEnvironmentBanner`/`TaskGrid`/`TaskResultCard`，不渲染 `ScheduledTasksSection`（该组件按 U5 已迁出，只在 `SettingsView.swift:374` 的 `SettingsTasksSection` 里实例化一次）；`TaskGrid` 按钮用 `.buttonStyle(.bordered)`，未套 kssCard 体系（doc review 校正了 R5 与对应 Key Decision 里"改一处自动带出两处"的错误前提）。
- `Sources/KSSDesktop/Views/SidebarView.swift:376-421` — 架构图标固定 14pt/28×28；GitHub 图标展开态缩小为 11pt + 文字 wordmark。
- `docs/plans/2026-07-12-005-feat-release-hardening-settings-plan.md` — 上一轮设置模块与图标迁移的完整决策记录。
- `kss/data/intraday_client.py:644-674` — `LongbridgeProvider.fetch_quote` 里 `source_asof_ts = _to_iso_shanghai_any(rows[0].get("timestamp"))`，是 Longbridge 报价自身的时间戳（而非请求接收时间）。
- `scripts/kss_app_bridge.py:4780-4824` — `_longbridge_quote`/`_longbridge_quote_inner`：bridge 侧薄 retry 层 + 核心逻辑，`source_asof_ts` 原样透传进返回 dict。
- `Sources/KSSDesktop/Services/BridgeClient.swift:96` — Swift 侧调用入口 `run(["longbridge-quote", symbol], as: LongbridgeQuote.self)`；实际 API 调用发生在 Python bridge 子进程，不在 Swift 侧。
- `Sources/KSSDesktop/Services/KSSStore.swift:468-475` — 已有的手动 `retryRealtime()` 入口（清缓存 + 重新拉取），工具栏既有的手动"刷新"按钮已经覆盖了"用户手动重试"的需要，印证 Scope Boundaries 里"不新增重连控件"的判断。
- `Sources/KSSDesktop/Views/SidebarView.swift:290-315` — `wordmark`/`kmark` 已验证的自定义资产加载模式：`Bundle.module.url(forResource:withExtension:"png")` → `NSImage(contentsOf:)` → `Image(nsImage:).renderingMode(.template)` + `.foregroundStyle(theme.textPrimary)`；仓库无 `Assets.xcassets`，无 `Image("name")` 用法。
- `Sources/KSSDesktop/Support/RealtimeChrome.swift:59-63` — `RealtimeStatusBadge.formatted(ts)` 已有的"更新于 HH:mm"格式化写法，过期态复用同一函数。
- `Sources/KSSDesktop/Support/Theme.swift:106-145` — `KSSCardStyle`/`KSSCard`/`.kssCard(padding:)` 的定义；`Sources/KSSDesktop/Support/RealtimeChrome.swift:201`（`LiveStatTile`）是 kssCard 包装 + 主题字号的代表写法，供 U5 参照。
- `Tests/KSSDesktopTests/DailyBarFreshnessTests.swift`、`Tests/KSSDesktopTests/RealtimeMergeTests.swift` — 既有测试写法，供 U1/U2 新增测试参照。
- `kss/tests/test_bridge_longbridge.py:45-116` — `_FakeLongbridge` fixture 与 `test_longbridge_quote_covered_returns_true_value_fields` 等既有测试，供 U3 扩展参照。

## Planning Contract

**Product Contract preservation:** changed — R1（措辞对齐"已过期"状态命名）、R5（改正"改一处自动带出两处"的错误前提，拆成两次独立改造）。均为 `ce-doc-review` 校正的事实/一致性错误，非产品范围变更；其余 R-ID 与 brainstorm 阶段确认的内容一致。

### Key Technical Decisions

- **KTD1 — 新鲜度信号取后端已有的 `source_asof_ts`，不新造本地时钟；回退时间戳按标的独立记录，不用全局时间戳。** `LongbridgeProvider.fetch_quote` 已经把 Longbridge 报价自身的时间戳透传到 `sourceAsofTs`（`KSSModels.swift:1675`），只是从未被使用。用它判新鲜度比"多久没轮询成功"更准——能识别"轮询没报错但报价本身冻结"这种情况。`sourceAsofTs` 缺失或解析失败但该标的本次拿到的报价仍是 `isLive`（`error == nil && lastDone != nil`）时，不能回退比较 `KSSStore.realtimeUpdatedAt`——那是全局时间戳，只要页面上其他标的仍在刷新成功就会保持新鲜，会把 R2 明确要修的"被全局时间戳掩盖"问题重新引入。回退必须是**按标的独立**记录的接收时间（`KSSStore` 在写入某标的报价时顺带记一份 `[String: Date]` 的按标的时间戳，与该标的本次刷新绑定，不受其他标的影响）。
- **KTD2 — 新鲜度逐标的实时计算，不做"过期清除"。** 参考 `DailyBarFreshness` 的三态枚举形状，新建 `RealtimeFreshness { fresh, stale, missing }`，用 `sourceAsofTs` 与当前时间比较、5 分钟阈值。过期标的照常显示最后价格 + 过期标签，不从 `KSSStore` 的 quotes map 里删除——避免额外写一套清除/重建逻辑，也避免用户看到数字突然消失。这满足 R2"不被全局时间戳掩盖"的意图，但用了比 brainstorm 阶段设想的"清除 map"更简单的机制。
- **KTD3 — 断连诊断日志加在 Python bridge，不在 Swift 侧；冻结判定靠日志扫描，不靠进程内计数器。** 真实的 Longbridge API 调用发生在 `scripts/kss_app_bridge.py::_longbridge_quote_inner`（经 `LongbridgeProvider.fetch_quote`），Swift 侧只是消费者，加日志也看不到 Longbridge 那头发生了什么。`longbridge-quote` 命令不在 `BridgeClient.subprocessOnlyCommands` 里，正常走持久 sidecar 进程；但 sidecar socket 不可用时 `BridgeClient` 会回退为每次调用起一个全新 subprocess（`kss_app_bridge.py` 独立进程），此时任何模块级/进程内的滚动计数器都会在每次调用间被重置，恰好在最需要探测的连接不稳定场景里失效。因此每次 `_longbridge_quote_inner` 成功返回时无条件记一行 `symbol` + `source_asof_ts` 到日志（不做进程内计数/比对），"是否冻结"的判定放到日志文件外部做——扫描同一 symbol 连续 N 条记录（N=3）是否为同一个 `source_asof_ts`。这样无论请求是走持久 sidecar 还是每次新起的 subprocess，判定都基于落盘的日志而非易失的进程内存，不受进程生命周期影响。日志写入 sidecar 既有的日志文件（实现时用 `log-list` 命令核实路径），不新建文件。
- **KTD4 — 设置入口用 icon+text label，不只是放大点击区域。** 工具栏没有"加强按钮"的既有先例；采用图标+常显文字标签（`Label` 加 `.labelStyle(.titleAndIcon)` 或等效方案），与 U5 把工具栏「任务」改名为「任务台」的呈现方式保持一致——两个工具栏入口都是 icon+text，视觉分量统一。
- **KTD5 — 过期时间复用既有 `formatted(ts)` 格式，不引入 tooltip/hover。** `RealtimeStatusBadge.formatted(ts)`（`RealtimeChrome.swift:59-63`）已经是"实时"badge 展示"更新于 HH:mm"的既有格式；过期态复用同一函数，只换文案（如"已过期 · 更新于 HH:mm"）和配色，不为此单独设计交互态，避免列表页信息密度膨胀。
- **KTD6 — GitHub 图标走 template rendering，不维护固定配色资产。** 复用 `wordmark`/`kmark` 已验证的 `Bundle.module` + `NSImage(contentsOf:)` + `Image(nsImage:).renderingMode(.template)` + `.foregroundStyle(theme.textPrimary)` 模式（`SidebarView.swift:290-315`）。Octocat 资产做成单色 template 图像，自动跟随 8 套主题着色，不需要另外维护深浅色两套资产或逐主题测对比度。
- **KTD7 — 任务改名文案定为「任务台」（工具栏）/「定时任务」（设置页）。** 二者本质不同（手动运行台+记录 vs 定时任务配置），用这两个词组既保留"任务"这个用户已熟悉的词根，又能一眼区分。

## Implementation Units

### U1. 实时新鲜度判定核心逻辑

**Goal:** 建立逐标的新鲜度判定，取代当前"是否曾经拿到过活报价"的布尔口径。

**Requirements:** R1, R2

**Dependencies:** 无

**Files:**
- `Sources/KSSDesktop/Support/RealtimeFreshness.swift`（新建）
- `Sources/KSSDesktop/Services/KSSStore.swift`（新增按标的记录的 `realtimeReceivedAtBySymbol: [String: Date]`，在 `refreshRealtimeQuotes` 写入某标的报价时同步更新该标的自己的条目）
- `Tests/KSSDesktopTests/RealtimeFreshnessTests.swift`（新建）

**Approach:** 按 KTD1/KTD2，新建 `enum RealtimeFreshness { case fresh, stale, missing }` + `static func status(sourceAsofTs: String?, fallbackReceivedAt: Date?, now: Date) -> RealtimeFreshness`，阈值 300 秒（5 分钟）作为文件内常量。`sourceAsofTs` 是 ISO-8601 字符串（`_to_iso_shanghai_any` 的输出，即 Python `dt.astimezone(...).isoformat()`——若原始时间带微秒会输出形如 `2026-07-12T09:46:03.123456+08:00` 的带小数秒格式，不带微秒则不带小数点）。解析必须同时兼容两种格式：先用 `ISO8601DateFormatter(formatOptions: [.withInternetDateTime, .withFractionalSeconds])` 尝试，失败再退回不带 `.withFractionalSeconds` 的默认配置重试——只试一种会在另一种格式上解析失败，静默转入回退路径，等于把 R2 要修的 bug 用"看似正常工作"的方式带回来。两种格式都解析失败，或 `sourceAsofTs` 本身缺失但该标的仍是 `isLive` 时，回退比较 `fallbackReceivedAt`——调用方必须传入**该标的自己**这次刷新的接收时间，不能传全局 `KSSStore.realtimeUpdatedAt`（见 KTD1，避免重新引入 R2 的全局时间戳掩盖问题）。`KSSStore` 需要新增一个按标的记录的 `[String: Date]`（如 `realtimeReceivedAtBySymbol`），在每次某标的报价成功写入 `realtimeQuotes` map 时同步更新该标的自己的条目。函数签名接收 `now: Date` 而非内部调用 `Date()`，保证可测试。

**Patterns to follow:** `Sources/KSSDesktop/Support/DailyBarFreshness.swift`（三态枚举 + 纯函数判定的形状）。

**Test scenarios:**
- Happy path: `sourceAsofTs` 距 `now` 2 分钟 → `.fresh`
- Happy path: `sourceAsofTs` 带微秒（如 `2026-07-12T09:46:03.123456+08:00`）能正确解析（回归带小数秒格式的解析分支，避免既有 `_FakeLongbridge` fixture 只覆盖不带小数秒场景的盲区）
- Edge: `sourceAsofTs` 距 `now` 恰好 300 秒 → `.fresh`（边界不算过期，写死在实现里并测试）
- Edge: `sourceAsofTs` 距 `now` 超过 300 秒（如 10 分钟）→ `.stale`
- Edge: `sourceAsofTs` 为 nil，`fallbackReceivedAt` 距今 2 分钟 → `.fresh`（回退路径）
- Edge: `sourceAsofTs` 与 `fallbackReceivedAt` 都为 nil → `.missing`
- Error: `sourceAsofTs` 是不可解析字符串 → 不崩溃，回退到 `fallbackReceivedAt` 路径
- Integration: 页面上标的 A 的 `sourceAsofTs` 解析失败但刚被写入 map（`fallbackReceivedAt` 是它自己的接收时间），标的 B 同时成功刷新 → A 的新鲜度只看自己的 `fallbackReceivedAt`，不受 B 影响（回归 KTD1 的按标的隔离要求）

**Verification:** `swift test --filter RealtimeFreshnessTests` 全绿。

---

### U2. Badge UI 接入新鲜度状态

**Goal:** 把 U1 的逐标的新鲜度状态接入所有展示「实时」badge 的界面，替换布尔 `hasAnyLive`/`hasLiveFields` 判定。

**Requirements:** R1, R2 — Covers AE1, AE2

**Dependencies:** U1

**Files:**
- `Sources/KSSDesktop/Support/RealtimeChrome.swift`（`RealtimeStatusBadge`/`RealtimeStatusDot`；`LivePriceText`/`LiveStatTile` 的 `isLive` 入参改造）
- `Sources/KSSDesktop/Support/RealtimeMerge.swift`（聚合逻辑：新增"取展示标的中最差状态"的聚合函数，供页头 badge 用）
- `Sources/KSSDesktop/Views/DashboardView.swift`（含 `MarketStripRow`/`IndexStackRow`）、`RecommendationsView.swift`（`LivePriceText` 内联调用处，`RecommendationsView.swift:175-188`）、`ThemesView.swift`（含 `ThemeCard`）、`StockBrowserView.swift`
- `Tests/KSSDesktopTests/RealtimeMergeTests.swift`（扩展）

**Approach:** 页头 badge（`RealtimeStatusBadge`）和逐行/逐卡片价格（`LivePriceText`/`LiveStatTile`）是两个不同粒度，需要分开处理，不能只改前者：

- **页头 badge（页面级汇总）：** `hasLiveDisplayedFields` 等聚合属性从"是否至少一条命中"（`hasAnyLive` OR 语义）改为"取展示标的中最差的新鲜度状态"（新建 `RealtimeMerge.worstFreshness(symbols:quotes:receivedAtBySymbol:now:)`，逐个算 U1 的 `RealtimeFreshness` 再取最差值）。只要有一个展示中的标的过期，页头就诚实降级，不再要求页头单独展示"某一个标的"的状态——那是逐行组件的职责。
- **逐行/逐卡片价格（`MarketStripRow`/`IndexStackRow`/`RecommendationCard`/`ThemeCard`/`StockBrowserView` 列表行）：** 这些组件目前把 `LongbridgeQuote.isLive`（`error == nil && lastDone != nil`）传给 `LivePriceText`/`LiveStatTile` 控制"实时变色/闪烁"。改造为传入该标的自己的 `RealtimeFreshness`（`.fresh` 才变色/闪烁，`.stale` 用降级样式），这样 R2/AE2 要求的"该过期标的独立显示自己的过期状态、不被其他标的掩盖"落在实际展示价格的地方，而不是无法承载"逐标的"信息的单一页头 badge 上。
- `StockBrowserView` 不是单标的详情页——它被 `ContentView.swift:267`（`.watchlist`/自选，`stocks` 参数过滤为自选列表）和 `ContentView.swift:344`（`.stocks`/股票池，`stocks` 参数为全量）复用两次，是多标的列表 + 详情面板的组合；列表里每一行标的按上述"逐行"规则处理，详情面板显示当前选中标的的状态。
- 过期态展示复用 KTD5 的 `formatted(ts)` 格式，只换文案与配色（如 `theme.textSecondary`）。`.missing` 状态复用现状——不展示「实时」标签，等同当前 `hasAnyLive == false` 时的呈现，不新增视觉样式。

**Patterns to follow:** `Sources/KSSDesktop/Support/RealtimeChrome.swift:6-64,138-171`（现有 badge/dot 结构）；`RealtimeMerge.hasAnyLive`（`RealtimeMerge.swift:131-136`）改造聚合函数时保持函数式风格。

**Test scenarios:**
- Happy: 单标的 `.fresh` → 页头 badge 显示「实时」，该标的价格保持现有实时变色/闪烁
- Happy: 单标的 `.stale` → 页头 badge 显示「已过期 · 更新于 HH:mm」，该标的价格切到降级样式
- Edge: 页面展示多个标的，一个 `.stale` 一个 `.fresh` → 页头 badge 按"最差状态"降级为已过期；两个标的各自的价格样式分别对应自己的状态，不互相影响（Covers AE2）
- Edge: 全部标的 `auth_failed` → 保持现有降级路径不变（回归测试）
- Edge: 单标的 `.missing`（从未拿到过报价）→ badge 不展示「实时」标签，等同现状
- Integration: badge 组件与逐行价格组件都从注入属性派生状态，不直接访问 `KSSStore`

**Verification:** `swift test --filter RealtimeMergeTests` 全绿；真机核对四个页面在行情停更 5 分钟以上后，页头 badge 与该标的自身价格样式都正确降级为「已过期」，不再持续显示「实时」。

---

### U3. 断连根因诊断日志

**Goal:** 在 Longbridge 报价的实际调用点记录 `source_asof_ts` 随请求推进的情况，为根因判断提供依据。

**Requirements:** R3

**Dependencies:** 无（可与 U1/U2 并行）

**Files:**
- `scripts/kss_app_bridge.py`（`_longbridge_quote_inner` 附近）
- `kss/tests/test_bridge_longbridge.py`（扩展）

**Approach:** 按 KTD3，在 `_longbridge_quote_inner` 成功返回（`res.ok=True`）时无条件记一行 `symbol` + `source_asof_ts` 到日志，不维护进程内计数器（sidecar 与 subprocess 兜底两种运行模式都要覆盖到）。冻结判定是独立的日志分析步骤（可以是一个小脚本或 U3 之外的手动 `grep`/`awk`，不属于本单元的运行时代码）：扫描日志，若同一 symbol 连续 3 条记录的 `source_asof_ts` 相同，视为"疑似报价上下文冻结"。实现时先用 `log-list` bridge 命令核实 sidecar 实际日志落点，复用该文件。

**Execution note:** 根因结论（冻结 vs 网络失败）需要交易时段实跑观察至少一次才能定案，属于执行期发现——诊断日志到位即完成本单元，结论记录在 Definition of Done 里单独跟踪。

**Test scenarios:**
- Happy: `_longbridge_quote_inner` 成功返回 → 日志里写入一行含 `symbol` + `source_asof_ts` 的记录
- Edge: 连续多次调用、`source_asof_ts` 正常推进 → 各自记录不同的值（日志分析阶段能看出未冻结）
- Edge: 连续多次调用返回相同 `source_asof_ts` → 各自记录相同的值（日志分析阶段能看出疑似冻结）
- Error: 调用返回 `error`（如 `auth_failed`）→ 不记录诊断行（或记录但标注为失败，不参与冻结判定），不与正常路径混淆

**Verification:** `pytest kss/tests/test_bridge_longbridge.py -q` 全绿；真机交易时段至少观察一次（含一次 sidecar 不可用、走 subprocess 兜底的场景），把观察结论记入 commit message 或 progress.md。

---

### U4. 设置入口视觉加强

**Goal:** 工具栏「设置」按钮从纯图标升级为图标+文字，与「任务台」呈现方式统一。

**Requirements:** R4

**Dependencies:** U5（工具栏「任务」按钮改名为「任务台」，本单元的溢出核对以改名后的最终文案为准）

**Files:**
- `Sources/KSSDesktop/Views/ContentView.swift`

**Approach:** 按 KTD4，`Label(WorkspaceSection.settings.displayName, systemImage: WorkspaceSection.settings.symbol)`（`ContentView.swift:120-126`）当前渲染为纯图标是因为 `ToolbarItemGroup` 默认只显示图标；加 `.labelStyle(.titleAndIcon)` 或等效方案让文字常显。核对与「任务台」按钮（U5 改名后的文案）、「刷新」按钮、`Text("|")` 分隔符（`ContentView.swift:104-131`）在常见窗口宽度下不溢出/不挤压。

**Test scenarios:** Test expectation: none -- 纯 UI 呈现改动，无可断言的行为变化。

**Verification:** 真机查看工具栏在正常宽度下三个按钮 + 分隔符不溢出、不挤压。

---

### U5. 任务分区/工具栏视觉统一 + 改名

**Goal:** 统一「任务」相关界面的视觉语言并按 KTD7 改名区分。

**Requirements:** R5, R6 — Covers AE3

**Dependencies:** 无

**Files:**
- `Sources/KSSDesktop/Views/RunbookView.swift`（`ScheduledTasksSection` 改造为 `kssCard` 体系；`TaskGrid` 按钮同步改造；`PageTitle("任务", ...)`（`RunbookView.swift:24`）同步改为「任务台」——否则点进改名后的工具栏入口，页面标题还是旧的「任务」，改名等于没做完）
- `Sources/KSSDesktop/Views/ContentView.swift`（工具栏「任务」按钮文案 → 「任务台」）
- `Sources/KSSDesktop/Views/SettingsView.swift`（`SectionHeader("任务")` → 「定时任务」）
- `Sources/KSSDesktop/Models/KSSModels.swift`（`WorkspaceSection.runbook.displayName` 若即为该文案来源，需同步核实并改）

**Approach:** `ScheduledTasksSection` 的 `surfaceRaised` 背景、胶囊按钮、`healthStat()` 等自定义样式替换为 `.kssCard(padding:)` + `KSSFont.themed(...)`，参照 `LiveStatTile`（`RealtimeChrome.swift:201`）的 kssCard 包装写法。`TaskGrid`（`RunbookView.swift:520-554`）的按钮同步套 kssCard 体系（保持可点击语义，不变成纯展示卡片）。改名文案按 KTD7 落地到 `WorkspaceSection` 与 `SettingsView` 的 `SectionHeader`。

**Patterns to follow:** `Sources/KSSDesktop/Support/Theme.swift:106-145`（`kssCard` 定义）；`Sources/KSSDesktop/Views/SettingsView.swift:84-153,277-307`（`密钥`/`数据源` 分区已用的 kssCard + KSSFont.themed 写法）。

**Test scenarios:**
- Test expectation: none（视觉改造部分）-- 无行为断言，真机核对设置页「密钥」「数据源」「定时任务」三个分区字号/卡片/按钮风格一致，任务工具栏页外观同步更新。
- Happy（改名部分）：`WorkspaceSection.runbook.displayName` 与设置页「定时任务」`SectionHeader` 字符串不相等（Covers AE3）——若能轻量断言则加一条 Swift 测试，否则真机核对。

**Verification:** 如新增测试通过；真机核对两处「任务」外观与命名符合预期。

---

### U6. GitHub Octocat 图标资产

**Goal:** 边栏底部 GitHub 入口换成官方 Octocat 图标，尺寸对齐架构图标，展开态去掉文字 wordmark。

**Requirements:** R7

**Dependencies:** 无

**Files:**
- `Sources/KSSDesktop/Resources/`（新增 Octocat 资产文件，命名待实现时定，如 `octocat.png`）
- `Sources/KSSDesktop/Views/SidebarView.swift`（`GitHubFooterLink` 改造）

**Approach:** 按 KTD6，实现时从 GitHub 官方品牌资源获取单色 Octocat 标志（遵守 GitHub 品牌使用规范），按 `bundledImage(_:)`（`SidebarView.swift:304-310`）现有模式加载。展开态与折叠态统一渲染为 14pt 图标 / 28×28 点击区域（与 `ArchitectureFooterButton` 对齐，`SidebarView.swift:376-384`），去掉当前展开态的 `Text("GitHub · ZCDeng/KSS")` wordmark（点击跳转行为保留，可用 `.help("GitHub · ZCDeng/KSS")` 提供 tooltip）。

**Patterns to follow:** `Sources/KSSDesktop/Views/SidebarView.swift:290-315`（`wordmark`/`kmark` 资产加载 + template rendering 模式）。

**Test scenarios:** Test expectation: none -- 纯资产替换，无可断言行为。

**Verification:** 真机核对折叠/展开两态图标尺寸与「架构」图标一致，深浅主题下对比度正常；点击仍能跳转到 GitHub 仓库地址。

## Verification Contract

| Unit | Command | Applicability | Done signal |
|---|---|---|---|
| U1 | `swift test --filter RealtimeFreshnessTests` | 全部 | 测试全绿 |
| U2 | `swift test --filter RealtimeMergeTests` | 全部 | 测试全绿 + 真机核对四页面过期展示 |
| U3 | `pytest kss/tests/test_bridge_longbridge.py -q` | 全部 | 测试全绿 + 真机交易时段观察一次 |
| U4 | 真机核对 | UI-only | 工具栏三按钮+分隔符不溢出 |
| U5 | `swift build` + 真机核对 | 全部 | 构建通过 + 视觉/命名符合预期 |
| U6 | 真机核对 | UI-only | 图标尺寸对齐 + 跳转正常 |

## Definition of Done

- [ ] `swift test` 全绿（含新增 `RealtimeFreshnessTests`，扩展 `RealtimeMergeTests`）
- [ ] `pytest kss/tests -q` 全绿（含扩展 `test_bridge_longbridge.py`）
- [ ] 真机验证：行情停更 5 分钟以上后，自选/推荐/股票池/Dashboard 四处 badge 正确降级为「已过期」，不再持续显示「实时」
- [ ] 真机验证：设置入口 icon+text、任务台改名、任务视觉统一（含工具栏页 TaskGrid）、GitHub Octocat 图标四项 UI 改动逐一核对
- [ ] Longbridge 断连根因诊断结论已记录（至少一次交易时段真实观察，写入 commit message 或 progress.md）
