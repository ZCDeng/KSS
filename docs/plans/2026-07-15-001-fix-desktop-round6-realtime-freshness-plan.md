---
title: KSSDesktop 第六轮反馈：盘中新鲜度统一与 cron 写路径修复 - Plan
type: fix
date: 2026-07-15
topic: desktop-round6-realtime-freshness
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# KSSDesktop 第六轮反馈：盘中新鲜度统一与 cron 写路径修复 - Plan

## Goal Capsule

- **目标**：修复第六轮盘中实测暴露的五项问题——今日板块卡数字换行、首行 ETF 卡缺实时标识、趋势观察停更（app 侧任务执行落入 bundle 路径被拒 + 归档日轴滞后）、推荐页日期语义误读、自选/详情页盘中新鲜度口径割裂——并把自选页确立为「盘中全实时化」语义。
- **产品权威**：用户 07-15 盘中真机实测反馈（含截图）+ 本轮诊断的日志/DB/真机证据。
- **待解阻塞项**：无——自选页语义（盘中全实时化）、推荐页标注（双日期）、三处规划期偏移（R3 归因修正、趋势日轴换交易日历、徽标口径先诊断）均已确认。

> **Product Contract preservation**：Requirements（R1-R9）与 Scope Boundaries 未变；Problem Frame 的 R3 归因段按规划期证据就地修正（cron wrapper 解释器链无辜，真根因是 app/bundle 进程的 PROJECT_ROOT 解析）——已经用户确认。

---

## Product Contract

### Summary

自选页与个股详情在盘中做到"像行情软件"：列表涨幅切实时、日 K 末尾拼当日实时 bar（仅展示层，EOD 库真值不动）、页头收敛为单一综合新鲜度徽标；推荐页改双日期标注（执行日为主、数据日为注脚）；把 app 侧任务执行/plist 渲染的项目根从 bundle 解出来，恢复趋势观察/因子健康度/科创扫描日更并回补缺口；趋势归档日轴切换到真交易日历（消除 hsgt 滞后拖累）；修今日板块卡数字换行；首行 ETF 卡补轻量实时标识；核查「刚更新即显示已过期」的徽标误报。

### Problem Frame

07-15 盘中（约 14:50）真机实测，五项问题全部复现并定位：

**今日板块卡数字换行（截图实锤）。** 上一轮给板块卡加的「今日」实时涨跌段与既有「近5日」大号数字行内并排，把自适应网格 152pt 最小宽挤爆——`+3.54%` 断成 `+3.5` / `4%` 两行。纯排版缺陷。

**首行卡片已接实时，缺的是标识。** 盘中实测 A500ETF 两卡显示当日实时跌幅（非昨收快照），上一轮批量实时预算修复已生效；但卡上没有「实时」标识，用户无从分辨。北向资金卡上游只有 T-2 数据（固有约束）。

**趋势观察停更 = app/bundle 双根缺陷 + 归档日轴滞后（规划期修正归因）。** 日志时间线证实：07-13 的 launchd 定时跑（项目脚本）**健康**；07-14 18:16 的执行跑的是 **bundle 里的脚本副本**（`/Applications/.../Resources/scripts/archive_trends_daily.py`），bundle 相对路径全断——hsgt parquet 找不到、写 `kss.db` 时 `mkdir Resources/storage` 被系统拒（PermissionError）。机制：`kss/config/paths.py` 的 `PROJECT_ROOT` 按 `__file__` 推导，**app（bundle）进程里渲染 plist / 执行任务时它落在 bundle Resources**——`kss/config/cron_manifest.py` 的 `wrapper_path = PROJECT_ROOT / wrapper` 会把 app 下发的 plist 指向 bundle wrapper；且 `archive_trends_daily.py` 自身还有一处 `__file__` 推导的 ROOT（不吃任何 env）。已排除：bridge 的 `cron-rerun`/`cron-catchup` 走 `launchctl kickstart`（项目 plist），干净。**叠加缺陷**：归档脚本用 hsgt parquet 的交易日轴决定归档哪些天，hsgt 数据天然滞后——07-13 的健康跑也只归档到 07-10。两个缺陷都修才能让趋势页跟到最新收盘日。因子健康度同报错；科创扫描报 bundle 内脚本不存在，同株连。

**推荐页"没有今日数据"是语义误读，链路正常。** 推荐页副标题显示 `prediction_date`（数据日 = 2026-07-14），而这批名单正是今天（07-15）开盘执行的操作名单。数据链 07-15 凌晨已跑完，问题在标注。

**自选/详情页盘中口径割裂（一屏四种口径实锤）。** 页头「实时 · 更新于14:54」与「日线截至 2026-07-14」并列；现价实时，左侧列表涨幅却是 07-14 日线值；日/周/月/年 K 线止于 07-14，分钟线又是当日。

**附带发现：**今日看盘页头「已过期 · 更新于 14:52」出现在 14:53——现状是页头 `worstFreshness` 对全部 60 个已命中标的取最差值，任一标的 `source_asof_ts` 落后 300s（`RealtimeFreshness.staleThresholdSeconds`）即拖垮全页，低频成交标的极易触发。

### Key Decisions

- **自选/详情盘中全实时化（用户拍板）** — 列表涨幅盘中切实时价；日 K 末尾拼当日实时 bar（open/high/low/last，未收盘态标识）；页头只留一个综合新鲜度徽标，「日线截至」只在图表区出现一次。拼 bar 仅在展示层，EOD 库纪律不破。
- **周/月/年 K 线不拼当日 bar（假设）** — 盘中视图由分钟线 + 日 K 当日 bar 承载。
- **推荐页双日期标注（用户拍板）** — 「MM-DD 执行 · 基于 MM-DD 收盘数据」，执行日为主。今日看盘「今日推荐」区同步。
- **app 侧任务执行与 plist 渲染必须锚定真实项目根（规划期修正）** — bundle 进程渲染 plist 前必须解析出真实项目根；解析不出时**拒绝渲染（fail loud）**而非默默用 bundle 根。渲染出口加守卫：ProgramArguments/日志路径不得含 `.app/Contents`。已装 plist 全量重刷核对。
- **趋势归档日轴切换到交易日历（规划期扩展，已确认）** — 归档哪些天不再由 hsgt parquet 轴决定；北向段数据未到时标缺失照写其余段（脚本已有分段容错先例：07-08 行 sector=False 照写）。
- **「已过期」徽标先诊断后改口径** — 诊断哪个标的拖垮页头；修法方向是调整页头汇总口径（如关键标的集合/剔除低频标的），诚实降级语义保留。
- **板块卡排版重排** — 数字在网格最小宽度下不换行；验收标准是任何主题名长度下数字完整成行。
- **首行 ETF 卡只补轻量实时标识**，不加分时缩略图。

### Requirements

**排版与标识**

R1. 今日板块卡在自适应网格最小宽度下，「近5日」与「今日」两组数字均完整成行，任何主题名长度下不出现数字断行。

R2. 首行 ETF 卡盘中命中实时 quote 时带轻量「实时」标识；北向资金卡维持数据日期标注不变。

**cron 写路径与数据恢复**

R3. 趋势归档、因子健康度、科创共振扫描三个 launchd 任务恢复正常日更：任务进程解析到项目代码、写入项目/STATE_ROOT 存储；任何任务执行路径（launchd 定时、app 重跑/补跑/排期编辑下发）不得再解析 bundle 脚本或写 /Applications bundle 内路径。

R4. 趋势观察回补 07-13、07-14 缺口后，页面显示最近已收盘交易日数据；修复当晚（07-15）EOD 后自然新增当日条目；归档不再受 hsgt 数据滞后拖累。

**语义统一**

R5. 推荐页副标题为双日期形态：执行日为主，数据日为注脚；今日看盘「今日推荐」区同步。

R6. 自选页左侧股票列表涨幅盘中显示实时值（带实时标识）；非交易时段回退最近收盘值并标数据日。

R7. 个股详情页头收敛为单一综合新鲜度徽标；「日线截至 YYYY-MM-DD」只在行情图表区标注一次。

R8. 日 K 线（自选详情与放大视图）盘中在末尾拼一根当日实时 bar（含未收盘态标识）；EOD 库不写盘中数据；周/月/年维持 EOD 并保留图表区截至标注。

**徽标诚实性**

R9. 新鲜度徽标在成功更新后的合理窗口内不得显示「已过期」；真实过期时仍如实降级。

### Scope Boundaries

- **不做**：北向资金卡实时化；首行 ETF 卡分时缩略图；周/月/年 K 线拼当日 bar；EOD 数据库写入任何盘中数据。
- **不改**：推荐生成链路本身（prediction_date=数据日的落库语义不动，只改展示层标注）。

### Acceptance Examples

AE1. 盘中打开今日看盘：板块卡数字全部单行完整；首行 A500ETF 卡显示当日实时涨跌 + 实时标识。

AE2. 盘中打开自选：列表涨幅与详情现价同为实时口径；详情页头只有一个新鲜度徽标；日 K 最后一根是今日（未收盘态），分钟线为当日。

AE3. 打开推荐页：副标题显示「07-15 执行 · 基于 07-14 收盘数据」（对应日期滚动）。

AE4. 趋势观察显示 07-13/07-14 回补数据；07-15 EOD 后出现当日条目；`trends_archive_daily.log`、因子健康度日志连续两个交易日无 PermissionError / bundle 路径。

AE5. 今日看盘页头在一次成功刷新后的窗口内不显示「已过期」。

---

## Planning Contract

### Key Technical Decisions

KTD1. **bundle 模式的真实项目根解析规则（评审修正：env 注入管道已存在，缺的是根的来源）**：`BridgeClient.swift` 已向 sidecar 与子进程注入 `KSS_PROJECT_ROOT`（386/503 行），但 bundle 模式下 `resolveRoots()`（902-907 行）刻意把 projectRoot 设为 bundle Resources（保持脚本与 app 版本对齐的既有设计）。本单元交付的是**分域根规则**：sidecar/脚本代码根维持 bundle（版本对齐设计不动）；**plist 渲染与 launchd 任务派发用安装期 breadcrumb（`~/Library/Application Support/KSS/breadcrumb.json`，BridgeClient.swift:856-875）的 projectRoot**——经校验（hasBridge 且不含 `.app/Contents`）后注入渲染上下文；breadcrumb 缺失或失效时拒绝渲染（fail loud），不得回退 bundle 根。app 触发的任务重跑继续走 `launchctl kickstart`（项目 plist → 项目脚本），与该规则自洽。
KTD1a. **渲染出口守卫**：`cron_manifest.py` 渲染与 `_validate_wrapper` 在出口断言 ProgramArguments/日志路径不含 `.app/Contents`，违规拒绝渲染并返回结构化错误。不改 `paths.py` 的 `__file__` 推导默认值（dev/项目内运行零回归）。

KTD2. **趋势归档日轴 = bridge 交易日历**：`archive_trends_daily.py` 的候选日集合改由交易日历驱动（可参照 `kss/sector/hotspot_rotation.py:_load_trade_calendar` / `scripts/daily_review.py` 的 trade_cal 先例），从上次已归档日的次日补到最近已收盘交易日；北向段无数据时该字段标缺失，其余段照写（沿用现有分段容错）。**同单元迁移脚本自身的路径推导**：`ROOT = Path(__file__)...` 派生的 HSGT_PARQUET/DB 路径改走 `kss.config.paths`（STORAGE_ROOT/KSS_DB 已存在且吃 env override），保留 bridge import 的 sys.path 设置。

KTD3. **日 K 拼 bar 在 Swift 侧组装、chart.html 渲染态标记**：`StockDetailView`/放大视图把 daily 序列传给 `ChartWebView` 前，若处于交易时段且该标的 quote `isLive`，追加一根 `date=今天, o/h/l/c=quote.open/high/low/lastDone, volume=quote.volume`（volume 缺失置 0，接受 VOL 面板当日为空）的 pseudo bar 并带 `provisional` 标记；指标/VOL 序列直接消费追加后的序列（与行情软件当日未收盘惯例一致）。**跨 session 门控用 `quote.sourceAsofTs` 的上海时区日期分量 ≠ 今天则不拼**（quote 无 sessionDate 字段——那是 intraday-bars payload 的；sourceAsofTs 缺失/不可解析同样不拼）；quote 缺任一 OHLC 字段不拼。渲染层空心/半透明 + **未收盘标注用图例文字或 series marker**（chart.html 无既有 tooltip 设施，不为此从零建悬浮层）。

KTD4. **列表实时化复用 `RealtimeMerge.displayPrice(snapshotClose:snapshotPct:quote:)`**：自选列表行涨幅经 quotes map 合并（与推荐页现价列同模式）。**watchlist 位于 ContentView 的 `@AppStorage("watchlistSymbols")`（store 不持有）**——接线：KSSStore 增设 watchlist 镜像（`syncWatchlistToDB(_:)` 已在每次变更时被调用，顺手更新镜像 + ContentView onAppear 初始播种），`refreshRealtimeQuotes` 的 priority 列表追加该镜像。实时标识与首行 ETF 卡统一复用 `RealtimeChrome.swift` 既有 live 视觉约定（LivePriceText 变化闪色 + 同一处静态「实时」小标记），不再各自发明。

KTD5. **推荐执行日在 bridge 计算**：`_recommendations()` 增 `executionDate` 字段 = prediction_date 的下一交易日。**bridge 现无前向交易日 helper**（只有 `_is_trade_day` 单日判定与嵌套的 `_prev_open`）——新增 `_next_open` helper，仿照 `scripts/daily_review.py:163 next_trade_date` 的既有先例（trade_cal 前向 15 日窗口 + 工作日兜底）；日历失败时 executionDate 为空、UI 退回单数据日显示。真值字段透传，不拼自然语言（number_guard 纪律）。

KTD6. **页头徽标口径：诊断先行**：先在 `worstFreshness` 路径加最差标的诊断输出定位拖垮者；修法预设为「页头汇总只看核心展示集合（堆叠卡+首行 ETF，实现期显式枚举成员并写进口径单测）」——诊断结果若指向解析或时区缺陷则直接修缺陷。

### Risks & Dependencies

- **已装 plist 现场状态未知**：07-14 的 bundle 执行意味着历史上有 app 下发的 bundle 路径 plist；U2 收尾必须 `sync_launchd.py` dry-run 核对全部清单 label（当前 26 个）后 --apply 重刷，避免残留。评审附带发现：26 个 job 当前全部 enabled（含记忆中应停用的 news_digest 两个）——重刷前人工过目一遍 enabled 集合。
- **breadcrumb 新鲜度**：KTD1 依赖 breadcrumb.json 的 projectRoot 在本机是最新的；U2 动工先核验其写入方（`writeBreadcrumb`）与当前值，失效则先修 breadcrumb 刷新链。
- **拼 bar 的昨收锚**：pseudo bar 的涨跌着色依赖 prevClose；quote.prevClose 缺失时退快照 close 反推（`prevCloseFallback` 已有同式先例）。
- **执行日计算依赖交易日历可用性**：`_is_trade_day` 每次调用都打一次 tushare trade_cal（无缓存）；`_recommendations` 在快照渲染路径上，U4 实施时考虑按日缓存执行日结果。日历接口失败时 subtitle 退回单数据日显示（不阻塞页面）。
- **自选规模与符号预算**：watchlist 并入 60 符号预算后，自选很大时可能出现部分行实时/部分行回退的混合态；当前自选仅 2 只，暂不设计分页采集，预算逼近时 harvest 顺序已保证 watchlist（priority）优先。
- **顺序依赖**：U3 依赖 U2（写路径修复后回补才有意义）；其余单元互相独立。

---

## Implementation Units

### U1. 今日板块卡排版修复 + 首行 ETF 卡实时标识

- **Goal**：板块卡数字任何宽度下单行完整；首行 ETF 卡盘中命中实时 quote 时带轻量实时标识（北向资金卡维持数据日期标注不变）。
- **Requirements**：R1、R2 / AE1。
- **Dependencies**：无。
- **Files**：`Sources/KSSDesktop/Views/DashboardView.swift`（SectorChip + MarketStripRow）。
- **Approach**：板块卡——「近5日」与「今日」拆为上下两行（或近5日缩为 harmonyNumber(16) + 今日右对齐同行 + 网格 minimum 提到 176），数字 Text 加 `.fixedSize()` + `.lineLimit(1)` 防断行；实施时按 8 套主题实测选定。首行 ETF 卡——命中 live quote 时加实时标识，视觉复用 `RealtimeChrome.swift` 的 live 约定（与 U5 列表标识同语言，见 KTD4）；北向卡不动。
- **Test scenarios**：`Test expectation: none — 纯布局与标识调整`；真机验收覆盖 AE1（最长主题名「人工智能」+ 双负号数值 + 首行实时标识 + 北向卡日期标注不变）。
- **Verification**：盘中截图核对 6 张主题卡全部单行、首行 A500ETF 卡带实时标识。

### U2. app 侧任务执行/plist 渲染锚定项目根

- **Goal**：任何执行路径（launchd/app 重跑/排期编辑/cron-sync 下发）都解析项目代码、写 STATE_ROOT/项目存储；bundle 路径执行清零。
- **Requirements**：R3 / AE4。
- **Dependencies**：无。
- **Files**：`Sources/KSSDesktop/Services/BridgeClient.swift`（bundle 模式分域根规则，见 KTD1）、`kss/config/cron_manifest.py`（渲染守卫）、`scripts/kss_app_bridge.py`（cron-sync/排期编辑出口断言）、`scripts/sync_launchd.py`（重刷核对）、`kss/tests/test_cron_manifest.py`（守卫回归）。
- **Approach**：按 KTD1/KTD1a——breadcrumb 真根解析（先核验 breadcrumb 写入方与当前值）+ 渲染出口 fail-loud 守卫 + 已装 plist（全部清单 label，当前 26 个）dry-run 核对后 --apply 重刷。补诊断留痕：bridge 渲染 plist 时把解析到的项目根记进结构化响应。
- **Test scenarios**：(a) 伪造项目根含 `.app/Contents` 时渲染报结构化错误不落盘；(b) 正常项目根渲染的 ProgramArguments/日志路径均在项目内；(c) `KSS_PROJECT_ROOT` env 注入时 `paths.PROJECT_ROOT` 取 env 值（现有行为回归钉住）；(d) breadcrumb 缺失/失效时渲染拒绝而非回退 bundle 根。
- **Verification**：重刷后 `plutil -p` 抽查 3 个 label 全指项目路径；app 内手动重跑趋势归档一次，日志无 bundle 路径。

### U3. 趋势归档日轴换交易日历 + 路径推导迁移 + 缺口回补 + 株连任务恢复

- **Goal**：趋势页跟到最近已收盘交易日；归档脚本任何执行上下文下写 STATE_ROOT；07-13/07-14 回补；因子健康度/科创扫描恢复。
- **Requirements**：R3、R4 / AE4。
- **Dependencies**：U2。
- **Files**：`scripts/archive_trends_daily.py`（日轴 + 北向可缺 + `__file__` ROOT 迁移到 `kss.config.paths`）、`kss/tests/test_trends_archive.py`（新建，就近参照 `test_bridge_trends.py`）。
- **Approach**：按 KTD2——候选日集合 = 交易日历上 (上次已归档日, 最近已收盘日] 区间；北向段无数据标缺失照写其余段；HSGT_PARQUET/DB 路径改走 `kss.config.paths`（吃 env override），保留 bridge import 的 sys.path 设置。回补跑 07-13/07-14；因子健康度/科创扫描在 U2 重刷后手动各触发一次验证（数据语义为当日快照，不回补历史）。
- **Test scenarios**：(a) hsgt 数据滞后 2 日时仍归档到最近收盘日、north 字段为空；(b) 无缺口时幂等（重复跑不重写）；(c) 跨周末候选日集合正确（周一跑补周五）；(d) 设置 KSS_STATE_ROOT/KSS_PROJECT_ROOT 时脚本写目标解析在 STATE_ROOT 下。
- **Verification**：`trends_days` 最新行 = 最近已收盘交易日；趋势页 UI 显示回补条目。

### U4. 推荐页双日期标注

- **Goal**：推荐页与今日看盘推荐区显示「执行日为主 + 数据日注脚」。
- **Requirements**：R5 / AE3。
- **Dependencies**：无。
- **Files**：`scripts/kss_app_bridge.py`（`_next_open` helper + `_recommendations` 增 executionDate）、`Sources/KSSDesktop/Models/KSSModels.swift`（payload 字段）、`Sources/KSSDesktop/Views/RecommendationsView.swift`（subtitle）、`Sources/KSSDesktop/Views/DashboardView.swift`（picksColumn caption）、`kss/tests/test_bridge_recommendations.py`（新建，就近参照既有 bridge 测试）。
- **Approach**：按 KTD5——`_next_open` 仿 `scripts/daily_review.py:163 next_trade_date`（trade_cal 前向窗口 + 工作日兜底），考虑按日缓存避免快照渲染路径重复打日历。副标题模板「{执行日} 执行 · 基于 {数据日} 收盘数据」；日历不可用时退回单数据日。
- **Test scenarios**：(a) 周五数据日 → 执行日为下周一；(b) 节假日跳过；(c) 日历失败时 executionDate 为空、UI 退化不崩。
- **Verification**：真机推荐页副标题符合 AE3。

### U5. 自选列表盘中实时化

- **Goal**：自选/股票池列表行涨幅盘中为实时口径，带实时标识；非交易时段回退收盘值+日期。
- **Requirements**：R6 / AE2。
- **Dependencies**：无（依赖第五轮已就绪的批量 quote 链路）。
- **Files**：`Sources/KSSDesktop/Views/StockBrowserView.swift`（列表行合并 quotes）、`Sources/KSSDesktop/Views/ContentView.swift`（watchlist 播种）、`Sources/KSSDesktop/Services/KSSStore.swift`（watchlist 镜像 + harvest priority）、`Tests/KSSDesktopTests/RealtimeMergeTests.swift`（就近扩展）。
- **Approach**：按 KTD4——行渲染经 `RealtimeMerge.displayPrice(snapshotClose:snapshotPct:quote:)`；实时标识复用 `RealtimeChrome.swift` 既有 live 视觉约定（LivePriceText 闪色 + 静态「实时」小标记），非 live 保留现有 07-14 日期徽标。watchlist 镜像经 `syncWatchlistToDB(_:)` 更新 + onAppear 播种。排序（涨跌幅列）与显示同口径。
- **Test scenarios**：(a) quote live 时行涨幅=实时 pct；(b) 无 quote 回退快照 pct；(c) 排序在混合 live/非 live 集合下稳定确定；(d) watchlist 镜像在 sync 调用后进入 harvest priority。
- **Verification**：盘中列表值与详情现价一致（AE2）。

### U6. 详情页头新鲜度收敛

- **Goal**：详情页头只保留一个综合新鲜度徽标；「日线截至」只在行情区标一次。
- **Requirements**：R7 / AE2。
- **Dependencies**：无。
- **Files**：`Sources/KSSDesktop/Views/StockBrowserView.swift`（页头/行情区标注）。
- **Approach**：页头保留 RealtimeStatusBadge（综合口径，四态语义已覆盖非交易时段）；移除页头「日线截至」行，行情区块头保留唯一一处；复盘结论区自带的档案日期徽标不动（语义不同）。
- **Test scenarios**：`Test expectation: none — 标注位置调整`；真机验收 AE2。
- **Verification**：截图核对页头单徽标。

### U7. 日 K 盘中拼当日实时 bar

- **Goal**：日 K（详情+放大）盘中末尾出现当日未收盘 bar（含量能与指标序列一致性）；周/月/年不拼。
- **Requirements**：R8 / AE2。
- **Dependencies**：无。
- **Files**：`Sources/KSSDesktop/Views/StockBrowserView.swift`（daily 序列组装）、`Sources/KSSDesktop/Views/ChartWebView.swift`（provisional 参数）、`Sources/KSSDesktop/Resources/chart.html`（未收盘态渲染）、`Tests/KSSDesktopTests/`（pseudo-bar 组装单测）。
- **Approach**：按 KTD3。组装函数纯化（输入 daily 序列 + quote + 会话状态 → 输出序列），Swift 单测覆盖门控矩阵；pseudo bar 带 volume（缺失置 0）；指标/VOL 消费追加后序列；渲染层空心/半透明 + 图例/series marker「未收盘」标注（不建 tooltip 层）。
- **Test scenarios**：(a) 交易时段 + live quote → 追加 bar 且日期=今天；(b) 非交易时段不拼；(c) quote 缺 high/low 不拼；(d) daily 序列末行已是今天（数据晚到）不重复拼；(e) 涨跌色以 prevClose 为锚、缺失时快照 close 反推；(f) `sourceAsofTs` 日期 ≠ 今天（或缺失）不拼——盘后重启无幽灵 bar；(g) pseudo bar 带 volume 且指标序列长度含当日。
- **Verification**：盘中真机日 K 最后一根为今日（AE2）；盘后重启 app 不出现幽灵 bar。

### U8. 「已过期」徽标误报诊断与口径修复

- **Goal**：成功刷新后的窗口内页头不再误报「已过期」；诚实降级保留。
- **Requirements**：R9 / AE5。
- **Dependencies**：无。
- **Files**：`Sources/KSSDesktop/Support/RealtimeMerge.swift`（worstFreshness 口径/诊断）、`Sources/KSSDesktop/Views/DashboardView.swift`（displayedFreshness 集合）、`Tests/KSSDesktopTests/`（口径单测）。
- **Approach**：按 KTD6——先加最差标的诊断（badge tooltip 或日志）盘中定位拖垮者；预设修法是页头汇总集合收敛为核心展示标的（堆叠卡+首行 ETF，实现期显式枚举成员写进单测），若诊断指向解析/时区缺陷则修缺陷本身。
- **Execution note**：诊断先行——先拿到"哪个标的、asof 差多少"的实证再改口径，避免把真陈旧修成假新鲜。
- **Test scenarios**：(a) 核心集合全 fresh + 外围标的 stale → 页头 fresh；(b) 核心集合任一 stale → 页头 stale；(c) sourceAsofTs 缺失回退 receivedAt 的既有行为不回归。
- **Verification**：盘中连续观察 10 分钟页头无闪烁误报（AE5）。

---

## Verification Contract

- `swift build` + `swift test` 全绿（含 U5/U7/U8 新增单测）。
- `.venv/bin/python -m pytest kss/tests -q --ignore=kss/tests/test_duck_query.py` 全绿（既有环境性失败除外；U2/U3/U4 新增测试通过）。
- 真机盘中核对 AE1、AE2、AE3、AE5；AE4 需跨两个交易日日志观察（回补部分当日可验）。
- `sync_launchd.py` dry-run 输出全部清单 label（当前 26 个）的 ProgramArguments 全部位于项目内。

## Definition of Done

- R1-R9 全部落地并对应 AE 验收通过（AE4 的连续两日观察项可留观察窗口，其余当日闭环）。
- 已装 plist 全量重刷且核对无 bundle 路径。
- 趋势观察 07-13/07-14 回补可见。
- 全部测试门禁绿；改动提交（是否打包发布听用户指令）。

---

## Sources & Research

- 本轮诊断（2026-07-15 盘中，本会话）：`storage/logs/cron/trends_archive_daily.log` 时间线（07-13 健康跑 vs 07-14 bundle 崩溃 traceback）、`kss/config/paths.py:22-26` PROJECT_ROOT 推导、`kss/config/cron_manifest.py` wrapper_path 渲染、bridge `cron-rerun/cron-catchup` kickstart 实现（已排除嫌疑）、`trends_days` 最新行 2026-07-10、真机截图（板块卡换行、自选页四口径并存、首行实时值）。
- 文档评审实证补强（4 评审员，2026-07-15）：`BridgeClient.swift:386/503` env 注入已存在、`resolveRoots()`（902-907）bundle 代码根为既有设计、breadcrumb 机制（856-875）、`archive_trends_daily.py` 自身 `__file__` ROOT、watchlist 在 `ContentView @AppStorage`、`daily_review.py:163 next_trade_date` 前向日历先例、chart 资产实际位于 `Sources/KSSDesktop/Resources/chart.html`、现装 plist 26 个。
- 上游需求：本文件 Product Contract（ce-brainstorm，2026-07-15，用户已确认 scoping 与三处规划期偏移）。
- 既有机制依赖：第五轮批量 quote 链路（`longbridge-quotes`、60 符号预算）、`RealtimeMerge.displayPrice`/`prevCloseFallback` 先例、`RealtimeChrome.swift` live 视觉约定。
