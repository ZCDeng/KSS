---
title: "feat: 边栏可拖拽排序 + 定时任务（launchd）可视化管理面板"
date: 2026-06-20
type: feat
status: planned
depth: standard
plan_id: 2026-06-20-001
tags: [kss-desktop, swiftui, sidebar, launchd, scheduled-tasks, python-bridge]
---

# feat: 边栏可拖拽排序 + 定时任务（launchd）可视化管理面板

KSSDesktop（macOS SwiftUI app）两项体验补强：(1) 边栏导航支持按个人喜好拖拽重排，「总览」永久锁定置顶；(2) 新增定时任务面板，统一展示 9 个 launchd 计划任务的调度、运行状态，并支持一键重跑与启用/停用。两项均为应用侧改动（SwiftUI + Python bridge），底层 `run_*.sh` 脚本与 plist 文件本身不重写。

---

## 问题背景（Problem Frame）

**功能 1 — 边栏顺序固定。** 当前 `SidebarView` 用 `WorkspaceSection.allCases` 渲染导航行，顺序写死在 enum 里（总览 / 推荐 / 自选 / 复盘 / 回测 / 股票池 / 任务 / 架构）。用户高频访问的页面无法靠前，没有任何个性化能力。

**功能 2 — 定时任务不可见。** 真正的定时任务是 `deploy/launchd/` 下的 **9 个 launchd plist**（crontab 已是 legacy），各自 `StartCalendarInterval` 调度、shell 出一个 `scripts/run_*.sh`、把 stdout/stderr 写到 `storage/logs/cron/<name>.log`。应用现有的「任务」页（`RunbookView`）只能**手动**跑 `KSSTask`，对这些**计划**任务既看不到调度、也看不到上次跑成没跑成、更不能重跑或临时停用。排障只能去终端敲 `launchctl` + `tail` 日志。

**目标：** 把 launchd 任务的「调度 + 状态 + 重跑 + 启停」搬进应用，和现有「任务」页统一在一处。

---

## 范围边界（Scope Boundaries）

**本次交付：**
- 边栏导航拖拽重排（展开态），顺序本地持久化，总览锁定置顶不参与排序。
- 定时任务面板：只读展示 9 个 launchd 任务（调度时间、加载状态、上次运行结果/时间）+ 一键重跑（`launchctl kickstart`）+ 启用/停用开关（`launchctl bootout/bootstrap` + `enable/disable`）。
- bridge 新增 launchd 自省与操作命令（stdlib `subprocess` / `plistlib`）。

### 推迟到后续（Deferred to Follow-Up Work）
- 在应用内**编辑调度时间**、**新增/删除**定时任务（即对 plist 的写操作与完整 CRUD）。
- 折叠态边栏的拖拽重排（折叠态过窄，仅跟随展开态确定的顺序，本次不加拖拽）。
- 日志全文查看器 / 历史多次运行时间线（本次只取「上次运行」一行摘要）。
- 把 legacy `crontab.txt` 残留迁移或清理。

### 非目标（Non-Goals）
- 不改 `run_*.sh` 脚本逻辑，不改 plist 的调度内容。
- 不引入第三方调度框架，不脱离 launchd。

---

## 需求（Requirements）

- **R1** 边栏展开态可用鼠标拖拽重排导航项，松手即持久化，重启应用后保持。
- **R2** 「总览」永久置顶，不可拖动、不可被其他项插到它之前。
- **R3** 排序持久化需向前兼容：未来 enum 新增的 section 若不在已存顺序中，自动追加到末尾，不丢失。
- **R4** 折叠态边栏按展开态确定的同一顺序渲染（总览仍置顶）。
- **R5** 定时任务面板列出全部 9 个 `com.zcdeng.kss.*` launchd 任务，每项含：中文任务名、人类可读调度（如「工作日 17:30」「每周五 17:00」）、加载状态、上次运行结果（成功/失败/未知）+ 时间。
- **R6** 每个任务可一键重跑（`launchctl kickstart -k`），面板就地刷新该任务状态。
- **R7** 每个任务可启用/停用切换，状态持久化（停用后下次调度不再触发），切换后就地反映加载状态。
- **R8** 所有 launchd 操作只接受来自 plist 文件枚举出的固定 label 白名单，绝不拼接用户输入（防注入，Fail loud）。
- **R9** 面板与现有「任务」页视觉统一（M3 响应式：内容封顶居中、统一边距、clay 强调色、红涨绿跌的状态色复用）。

---

## 关键技术决策（Key Technical Decisions）

**KTD1 — launchd 而非 crontab 作为唯一数据源。** recon 确认 `crontab.txt` 是 `pre-launchd-2026-05-13` 的 legacy 残留，`launchctl list` 实际加载的是 `com.zcdeng.kss.*` 这 9 个 plist。面板只读 `deploy/launchd/*.plist` + `launchctl`，不碰 crontab。

**KTD2 — plist 自省走 `plistlib`，状态走 `launchctl`，日志走 `StandardOutPath`。** 每个 plist 已设 `StandardOutPath = storage/logs/cron/<name>.log`（recon 已验证），所以任务→日志映射直接读 plist 的该键，无需猜测。调度时间从 `StartCalendarInterval` 解析，加载/退出码从 `launchctl print gui/$UID/<label>`（或 `launchctl list <label>` 的 `LastExitStatus`/`PID`）解析，上次运行时间取日志文件 mtime + 末行。三者都在 bridge 用 stdlib 完成。

**KTD3 — 启停用 `bootout/bootstrap` + `disable/enable` 组合。** `launchctl disable gui/$UID/<label>` 持久化停用态（重启后仍停），配 `bootout` 立即卸载；启用反之（`enable` + `bootstrap <plist>`）。即时效果 + 持久态都覆盖。具体加载路径（repo `deploy/launchd` vs `~/Library/LaunchAgents` 符号链接）执行时确认 —— 见开放问题 OQ1。

**KTD4 — label 白名单由 plist 文件名派生，命令参数化。** bridge 启动时 glob `deploy/launchd/com.zcdeng.kss.*.plist` 得到合法 label 集合；rerun/enable/disable 命令收到的 label 必须命中该集合才执行 `launchctl`，否则报错返回。杜绝 shell 注入，符合「确定性变换交给代码」。

**KTD5 — 拖拽重排自己实现，不回退 `List`。** 边栏此前为了 clay 选中态特意从 `List(.sidebar)` 换成自定义 Button 行（蓝色选中态无法 retint）。不为了拿 `List.onMove` 而退回去。改用 `onDrag`/`onDrop`（NSItemProvider 携带 section rawValue）在自定义行上算目标下标、重排数组、持久化。总览行渲染在可拖拽列表之外，天然不可拖。

**KTD6 — 定时任务面板并入「任务」页，不新开边栏 section。** `RunbookView` 已是「运行台」语义，新增「定时任务」段落（手动任务 / 正式任务 / **定时任务** / 任务记录）。避免边栏再加一项（且边栏此刻正好在做可排序改造）。

**KTD7 — 顺序持久化用单个 `@AppStorage` 字符串。** 存非置顶 section 的 rawValue 逗号拼接（如 `"Daily Picks,Watchlist,Reviews,..."`）。读取时：总览强制第一，按存储顺序排其余，未出现的 enum 项追加末尾（满足 R3）。沿用应用已有的 `@AppStorage` 约定（`sidebarCollapsed` / `appearanceMode`）。

---

## 高层技术设计（High-Level Technical Design）

定时任务面板的控制/数据流，跨 应用 → bridge → launchd/plist/日志 三层：

```mermaid
flowchart LR
    UI["RunbookView<br/>定时任务段落"] -->|scheduledJobs / rerun / setEnabled| Store[KSSStore]
    Store -->|run as: [ScheduledJob]| Bridge["BridgeClient.run()"]
    Bridge -->|/usr/bin/python3 cron-list \| cron-rerun \| cron-enable \| cron-disable| Py["kss_app_bridge.py"]

    Py -->|plistlib 解析| Plist["deploy/launchd/*.plist<br/>(Label / 调度 / StandardOutPath)"]
    Py -->|launchctl print/list| LC["launchd (gui/$UID)<br/>加载态 / LastExitStatus / PID"]
    Py -->|读 mtime + tail| Log["storage/logs/cron/*.log<br/>上次运行时间/末行"]
    Py -->|kickstart -k / bootout+disable / bootstrap+enable| LC

    Py -->|JSON: [ScheduledJob]| Bridge
```

调度时间渲染规则（`StartCalendarInterval` → 人读字符串，纯函数）：
- `Weekday ∈ {1..5}` 同一 `Hour:Minute` → `工作日 HH:MM`
- 单个 `Weekday=N` → `每周{一..日} HH:MM`
- 仅 `Hour:Minute`（无 Weekday）→ `每天 HH:MM`

---

## 实现单元（Implementation Units）

### U1. 边栏顺序模型 + 总览置顶逻辑

**Goal:** 提供「持久化的导航顺序」单一事实源，总览永远第一，向前兼容新 section。
**Requirements:** R2, R3, R7(无关) ; 直接支撑 R1/R4。
**Dependencies:** 无。
**Files:**
- `Sources/KSSDesktop/Models/KSSModels.swift`（在 `WorkspaceSection` 旁加排序工具：`pinned`/`reorderable` 划分 + `ordered(from saved: String) -> [WorkspaceSection]` + `encode(_:) -> String`）
- `Sources/KSSDesktop/Services/KSSStore.swift`（持有 `@AppStorage("sidebarOrder")` 或等价状态，暴露 `orderedSections` 与 `moveSection(_:before:)`/`persistOrder(_:)`）
**Approach:** 纯逻辑：`pinned = [.dashboard]`；`reorderable = allCases - pinned`。`ordered(from:)` 解析逗号字符串为 rawValue 序列，过滤掉非法/置顶项，按其排其余，再把 `reorderable` 里未出现的项按 enum 原序追加末尾，最后 `pinned + 结果`。空字符串/损坏值回退 enum 原序。
**Patterns to follow:** 现有 `@AppStorage` 用法（`ContentView.swift` 的 `sidebarCollapsed`/`appearanceMode`）。
**Test scenarios（独立自检脚本，纯函数；无 XCTest target，见「测试基建」）:**
- 空存储 → 返回 enum 默认顺序，总览第一。
- 存储 `"Watchlist,Daily Picks"` → 总览仍第一，其后为 自选、推荐，其余 section 按 enum 原序追加，无丢失（覆盖 R3）。
- 存储含一个已不存在的 rawValue（模拟删除的 section）→ 被忽略，不崩。
- 存储里把 `Dashboard` 也写进去 → 去重，总览只出现一次且置顶（覆盖 R2）。
- `encode(ordered)` 往返 `ordered(from: encode(x)) == x`。

### U2. 边栏展开态拖拽重排

**Goal:** 展开态导航行可拖拽重排，松手持久化；总览不可拖。
**Requirements:** R1, R2, R4。
**Dependencies:** U1。
**Files:** `Sources/KSSDesktop/Views/SidebarView.swift`、`Sources/KSSDesktop/Views/ContentView.swift`（把 `store.orderedSections` 与重排回调传入 `SidebarView`）
**Approach:** `expandedNav` 改为遍历 `store.orderedSections`：第一项（总览）渲染为普通不可拖行；其余包在可拖拽区，行加 `.onDrag { NSItemProvider(object: section.rawValue as NSString) }` 与 `.onDrop(of: [.text])`，drop 时解析源 rawValue、算目标下标、`store.moveSection`、`store.persistOrder`。`collapsedNav` 仅按同一 `orderedSections` 渲染（无 `.onDrag`，满足 R4 且符合范围边界）。拖拽中给目标行一个插入位指示（上/下分隔线或高亮），复用 clay 色。
**Patterns to follow:** 现有 `expandedNav`/`collapsedNav` 的 clay 选中态与圆角背景；`ImportStocksView` 已有 `onDrop` 用法可参考 provider 解析。
**Execution note:** 先让拖拽把顺序「显示上」改对，再接持久化，避免状态与视图打架。
**Test scenarios:** 无 UI 测试 harness — 手动验证：拖「自选」到「推荐」之上→顺序变；重启应用→保持（覆盖 R1）；尝试把任意项拖到总览之上→落点被钳制在总览之后，总览不动（覆盖 R2）；折叠后顺序与展开一致（覆盖 R4）。

### U3. bridge：launchd 任务自省命令 `cron-list`

**Goal:** 返回 9 个 launchd 任务的结构化清单（名称/调度/加载态/上次运行）。
**Requirements:** R5, R8。
**Dependencies:** 无（可与 U1/U2 并行）。
**Files:** `scripts/kss_app_bridge.py`（新增 `_scheduled_jobs()` 及辅助 `_parse_schedule()`/`_launchctl_status(label)`/`_last_run(log_path)`；`main()` 注册 `cron-list` 命令）
**Approach:** glob `deploy/launchd/com.zcdeng.kss.*.plist`，`plistlib.load` 取 `Label`、`StartCalendarInterval`、`ProgramArguments[0]`（脚本路径 → 反查中文任务名映射表）、`StandardOutPath`。`_parse_schedule` 把 interval（dict 或 list[dict]）转人读字符串（见 HTD 规则）。`_launchctl_status` 跑 `launchctl print gui/{uid}/{label}`，正则取 `state`/`last exit code`/`pid`；失败回退 `launchctl list {label}` 解析 `LastExitStatus`/`PID`；都拿不到则标 `unknown`。`_last_run` 读 `StandardOutPath` 文件 mtime 作为「上次运行时间」、tail 末行作摘要。任务中文名：维护一个 `LABEL_TITLES` dict（如 `sector_review_daily → 板块复盘`），与现有命名风格一致。
**Patterns to follow:** bridge 现有 `_task_history()`（读 jsonl 反序）与 `_run_process_task` 的 subprocess 用法；命令分发的 `if command == "...":` 链（`main()` 内）。
**Test scenarios（独立自检，subprocess 部分 mock）:**
- `_parse_schedule({Weekday:1..5, Hour:17, Minute:30})` → `工作日 17:30`。
- `_parse_schedule([{Weekday:5,Hour:17,Minute:0}])` → `每周五 17:00`。
- `_parse_schedule({Hour:9,Minute:0})` → `每天 09:00`。
- 缺失日志文件 → `_last_run` 返回「无记录」而非抛错（Fail loud：返回明确未知态，不静默空串）。
- `launchctl` 不可用/任务未加载 → 状态 `unknown`、`enabled=false`，整体不崩。
- 返回的每个 job label 都在 `deploy/launchd` glob 集合内（覆盖 R8 数据侧）。

### U4. bridge：重跑 / 启用 / 停用命令

**Goal:** 对单个 launchd 任务执行 kickstart / enable / disable，返回刷新后的该任务状态。
**Requirements:** R6, R7, R8。
**Dependencies:** U3（复用 `_scheduled_jobs` 的 label 白名单与状态读取）。
**Files:** `scripts/kss_app_bridge.py`（`_cron_action(label, action)` + `main()` 注册 `cron-rerun`/`cron-enable`/`cron-disable`）
**Approach:** 先校验 `label ∈ 白名单`（glob 派生），否则返回 `{ok:false, error:"unknown label"}`。`rerun → launchctl kickstart -k gui/{uid}/{label}`；`disable → launchctl bootout gui/{uid}/{label}` + `launchctl disable gui/{uid}/{label}`；`enable → launchctl enable gui/{uid}/{label}` + `launchctl bootstrap gui/{uid} {plist_path}`。每个动作后重新跑 `_launchctl_status` 返回最新态。所有 `launchctl` 调用用**列表参数**（非 shell 字符串），uid 取 `os.getuid()`。
**Patterns to follow:** U3 的 status 解析；`_task_result`/`_run_process_task` 的返回结构（success/failed + stdout/stderr 摘要），保持 bridge 返回 JSON 形状一致。
**Test scenarios（独立自检，`launchctl` mock）:**
- 非白名单 label（如 `com.evil.x` 或注入串 `a; rm -rf`）→ 不执行任何 subprocess，返回错误（覆盖 R8）。
- `rerun` 成功 → 返回 job 状态含刷新后的 `lastExit`/`pid`。
- `disable` 后 `_launchctl_status` 反映未加载 → `enabled=false`（覆盖 R7）。
- `enable` 时 plist 路径由 label 反查、确实存在 → bootstrap 用该绝对路径。
- subprocess 非零退出 → 返回 `ok:false` + stderr 摘要，不静默吞错。

### U5. Swift 模型 + BridgeClient + Store 接线

**Goal:** `ScheduledJob` 类型贯通 bridge JSON 到视图，提供异步读取与操作入口。
**Requirements:** R5, R6, R7, R9。
**Dependencies:** U3, U4。
**Files:**
- `Sources/KSSDesktop/Models/KSSModels.swift`（`struct ScheduledJob: Codable, Identifiable, Hashable` — `label/title/schedule/script/enabled/loaded/lastStatus/lastRunAt/lastLine`）
- `Sources/KSSDesktop/Services/BridgeClient.swift`（`scheduledJobs() throws -> [ScheduledJob]`、`rerunJob(_:)`/`setJobEnabled(_:enabled:)`）
- `Sources/KSSDesktop/Services/KSSStore.swift`（`@Published var scheduledJobs`、`loadScheduledJobs()`/`rerunScheduledJob(_:)`/`toggleScheduledJob(_:)`，操作后局部刷新对应 job）
**Approach:** 复用 `BridgeClient` 既有泛型 `run<T: Decodable>(_ args:as:)`。字段名与 bridge JSON 对齐（lastStatus 用字符串枚举 `success/failed/unknown`，前端映射状态色）。Store 操作走后台线程、主线程更新 `@Published`，与现有 `importStocks` 后 reload 的模式一致。
**Patterns to follow:** `BridgeClient.runTask`/`resolveStocks`/`importStocks` 的形状；`KSSStore` 现有 async 包装与 `@Published` 更新。
**Test scenarios:** 无 Swift 测试 harness — 手动验证：`scheduledJobs()` 解码 9 项无字段缺失崩溃；rerun 一个任务后该行状态/时间更新；toggle 后 `enabled` 翻转并持久（重开应用仍停用，覆盖 R7）。
**Execution note:** 先用 `cron-list` 真实输出对齐 Codable 字段，再接操作，避免解码错位返工。

### U6. 定时任务面板 UI（并入「任务」页）

**Goal:** 在「任务」页新增「定时任务」段落：任务表 + 状态徽章 + 重跑按钮 + 启停开关，M3 风格统一。
**Requirements:** R5, R6, R7, R9。
**Dependencies:** U5。
**Files:** `Sources/KSSDesktop/Views/RunbookView.swift`（新增 `ScheduledTasksSection` 与行视图 `ScheduledJobRow`；若 `RunbookView` 体积偏大则抽到新文件 `Sources/KSSDesktop/Views/ScheduledTasksView.swift`）
**Approach:** 在「正式任务」与「任务记录」之间插入 `SectionHeader("定时任务")` + 任务列表。每行：图标 + 中文名 + 人读调度 + `StatusBadge`（复用现有 task 状态徽章，成功/失败/未知映射 clay/红/灰）+ 上次运行时间 + `Toggle`（启停，tint clay）+ 「重跑」Button。操作中显示 `ProgressView`，完成后 `store` 局部刷新。沿用 `RunbookView` 的 GeometryReader M3 容器（封顶 1080 居中、统一边距）。停用态行整体降透明度以示禁用。
**Patterns to follow:** `TaskGrid`/`TaskResultCard`/`StatusBadge.task(...)`/`kssCard` 与 `SectionHeader`；红涨绿跌状态色取 `KSSTheme`。
**Test scenarios:** 手动验证：9 个任务全部渲染（覆盖 R5）；点「重跑」→ 出现进度→状态刷新（覆盖 R6）；切换 Toggle 停用→行变灰、`enabled=false`（覆盖 R7）；窗口缩放下布局与「任务」页其余段落对齐（覆盖 R9）。

---

## 测试基建（Testing Note）

仓库现状：SwiftPM 可执行 target，**无 XCTest target**；Python 侧**无 `tests/` 目录**。因此：
- **纯逻辑**（U1 顺序解析、U3 调度字符串/状态解析、U4 白名单校验）→ 写**独立自检脚本**断言上述 scenarios（Python 用一个 `scripts/` 下的 `_selftest` 入口或临时 `python3 -c`；Swift 顺序逻辑若不便建 target，则在 U1 函数内保持纯函数并以 bridge 侧等价逻辑或手动核对覆盖）。riskiest 的是 launchctl 解析与白名单，优先覆盖。
- **UI / launchd 副作用**（U2、U5、U6）→ 诚实地走**手动验证**（构建 `.app` 跑 `script/build_and_run.sh`，按各单元 scenarios 核对），不假装有自动化覆盖。

---

## 风险与依赖（Risks & Dependencies）

- **R-A｜launchd 加载路径不确定。** plist 在 repo `deploy/launchd/`，但 `launchctl list` 已加载它们 —— 可能经 `~/Library/LaunchAgents` 符号链接 bootstrap。`enable` 的 `bootstrap` 需指向**实际加载的 plist 路径**。缓解：U4 执行前用 `launchctl print` 的 `path =` 字段拿到真实路径；见 OQ1。
- **R-B｜`launchctl print` 输出格式跨 macOS 版本漂移。** 解析靠正则，键名/缩进可能因系统版本不同。缓解：双路解析（`print` 优先，`list` 兜底），拿不到就标 `unknown` 而非崩溃。
- **R-C｜GUI 域权限。** `gui/$UID` 操作需在用户 GUI 会话内；应用以用户身份运行，正常满足。若从非 GUI 上下文跑 bridge（如 ssh）会失败 —— 标 `unknown`，不阻塞面板渲染。
- **R-D｜停用/启用的持久性误解。** `bootout` 仅卸载到下次 bootstrap；必须配 `disable`/`enable` 才跨重启持久。KTD3 已用组合命令覆盖，但需在 R-A 解决后验证重启后停用态确实保留。
- **依赖：** 无新增三方依赖（stdlib `plistlib`/`subprocess` + 系统 `launchctl`）。

---

## 开放问题（Open Questions）

- **OQ1（执行时解决）** 9 个 plist 的真实加载路径是 `deploy/launchd/` 直接 bootstrap，还是经 `~/Library/LaunchAgents` 符号链接？决定 U4 `bootstrap` 用哪个路径。执行 U4 时跑一次 `launchctl print gui/$UID/com.zcdeng.kss.sector_review_daily` 看 `path =` 即可确定，无需现在拍板。
- **OQ2（执行时解决）** 「上次运行时间」取日志文件 mtime 是否足够？多数脚本每次运行追加日志、mtime 即最近一次；若某脚本无输出则 mtime 不更新。可接受（标「无记录」），如需精确可后续在 `run_*.sh` 落一行时间戳 —— 属 Deferred。
- **OQ3** 中文任务名映射表（`LABEL_TITLES`）以哪份为准？默认沿用现有 `KSSTask.title` / 复盘命名风格手工维护 9 条；新增 launchd 任务时需补这张表（在 U3 注释里标注）。

---

## 系统影响（System-Wide Impact）

- **数据产物：** 不新增提交产物。launchd 操作改的是系统 launchd 状态与 `storage/logs/cron/*.log`（已 gitignore 范畴）。
- **安全：** 所有 `launchctl` 调用参数化 + label 白名单，无用户输入拼接（KTD4/R8）。不复述 `.env` 密钥。
- **构建链：** 仅改 SwiftUI 源 + bridge（stdlib），`script/build_and_run.sh` 流程不变。
- **回滚：** 两功能相互独立，可分别回滚；定时任务面板为新增段落，移除不影响现有「任务」页其余部分。
