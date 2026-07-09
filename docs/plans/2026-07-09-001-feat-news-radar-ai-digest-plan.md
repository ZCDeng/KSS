---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: ce-plan-bootstrap
title: "feat: 资讯雷达 AI 一键提炼新闻要点（Vibe-Research 多赛道适配）"
date: 2026-07-09
---

# Summary

补强 KSSDeck「资讯雷达」页面（[Sources/KSSDesktop/Views/IntelView.swift](../../Sources/KSSDesktop/Views/IntelView.swift)）：单赛道「让 AI 提炼今日要点」按钮 + 全局「一键提炼全部要点」按钮，调 LLM 读取当前赛道 25 条最新 RSS 资讯生成 3-5 条要点；接入既有 OpenAI 兼容 LLMClient 与 Keychain 凭据（**不**复用 chat-turn 流式长连，digest 是 sync fire-and-forget）；要点结果可存入沉淀库供回看。

# Problem Frame

**当前状态**：[IntelView.swift](../../Sources/KSSDesktop/Views/IntelView.swift) 已复刻 Vibe-Research 多赛道 RSS 列表布局（commit `f19323e`），但截图显示「一键提炼全部要点 / 让 AI 提炼今日要点 / 存入沉淀」三件套缺失。KSSDeck 既有 AI 复盘（[Sources/KSSDesktop/Views/AIChatView.swift](../../Sources/KSSDesktop/Views/AIChatView.swift)）走 chat-turn 流式链路，链路完整但手动喂长 prompt 体验差。

**期望状态**：用户在 12 赛道任选一个，点「让 AI 提炼今日要点」→ 后端把该赛道 25 条最新资讯打包成 prompt → 调既有 LLMClient（Keychain 凭据 / OpenAI 兼容）→ 同步返回 3-5 条要点 → 展示在「今日要点」卡片 → 「存入沉淀」一键存到本地 notes 目录（`STATE_ROOT/storage/notes/`）。

**非目标**：
- 不引入新的 LLM 客户端（复用 `kss/llm/openai_client.py` 的 `LLMClient`）
- 不改造 chat-turn 长连接（digest 是非交互式单轮 sync 调用，与 chat-turn 流式协议无关）
- 不加新的 cron 任务（按需触发的用户操作）
- 不做要点的二次编辑（沉淀只是只读快照）

# Requirements

## R1. 单赛道 AI 要点提炼
- 在当前选中赛道的资讯列表上方加「今日要点 · {track.name}」卡片
- 卡片三种状态：idle（显示「让 AI 提炼今日要点」按钮）/ loading（spinner + 「AI 正在读这个赛道的资讯…」）/ done（要点列表 + 「重新提炼」+ 「存入沉淀」）
- idle 状态且 `intelDigest?.tracks` 无该 track 数据 → 不显示整张卡（避免空态噪音）

## R2. 全局一键提炼全部要点
- 顶部 statsRefreshRow 或 PageTitle HStack 加「一键提炼全部要点」按钮（避免 stat-line 横向拥挤）
- 点击后**串行**逐赛道提炼（避免同时 12 路 LLM 调用打爆 OpenAI rate limit）
- 按钮带进度：`提炼中 {done}/{total}`，**total = 非空赛道数**（空赛道不计入）；完成后回到 idle
- 仅对有 `items.length > 0` 的赛道触发，跳过空赛道
- 完成后显示「完成 X/{total} · 失败 Y」4s 后自动消失
- 提供「取消」按钮（点击设 `Task.isCancelled`，**下次循环前退出**；当前正在跑的 LLM 调用会跑完才退出——不要做 SIGTERM 杀进程，避免半成品文件）
- bulk 完成后若有失败 track：按钮文案改为「重试 N 个失败赛道」，仅重新跑失败的，跳过成功的

## R3. LLM 调用复用 OpenAI 兼容 LLMClient 与 Keychain 凭据
- 新增 bridge 命令 `intel-digest`（无流式，单次同步返回），参数：JSON 单参数 `{track_key, track_name, items[≤25], force?}`
- 后端用 `LLMClient(model=KSS_LLM_MODEL).complete(system=PROMPTTemplate, user=formatted_items)`（**不是 `chat_completion`**，后者不存在）
- 凭据来源：复用 `KeychainStore.injectedEnvironment()` 注入的环境变量（OpenAI/DeepSeek），无新增 key 管理 UI
- **不要复用 chat-turn 长连**——chat-turn 是 streaming + confirm gate 的交互协议，digest 是 sync fire-and-forget，shape 不同；我们只复用底层 LLMClient 与凭据

## R4. 沉淀库（本地 notes，**本轮仅写不读**）
- 沉淀目录：`STATE_ROOT/storage/notes/`
- 文件命名：`intel_digest_<YYYYMMDD>_<track_key>.md`（人读 + 简单 grep 友好）+ 旁挂 `.json`（结构化，含 prompt + response + generated_at + model）
- 「存入沉淀」按钮：当前要点文本 + track 名 + 时间戳写入 JSON
- 已存 → 按钮变灰显示「已存入沉淀」
- **本轮不提供沉淀库查看入口**——只写不读；后续 Notes 页面会暴露阅读界面。**用户决定**：本轮不要求加入口

## R5. 错误与空态
- 无 LLM 凭据（`KSSStore.hasLLMCredentials == false`）：卡片显示「未接入 AI — 前往设置」+ 按钮禁用（`theme.ma5` 提示色，与 saved-disabled 视觉区分）
- LLM 调用失败：卡片显示错误条（**`theme.down` 配 `exclamationmark.triangle.fill`，不要用红色 raw 或 `theme.up`——A 股红涨绿跌语义冲突**）+ 「重试」按钮
- 资讯为空：卡片不渲染（emptyState 区域兜底）
- 网络超时（30s，由 `LLMClient(timeout=30, max_retries=0)` 强制）：视为失败，UI 显示「请求超时，请重试」+ `error_type="timeout"` 标记
- 沉淀库写入成功 → 「已存入沉淀」按钮 disabled + ✓ checkmark

# Key Technical Decisions

## KTD-1. 复用 `kss/llm/openai_client.py` 的 `LLMClient` 而非新建客户端
依据：sidecar 的 `_handle_chat_turn` 已用此模块，调 OpenAI 兼容 API。digest 是一轮 sync 调用，直接用 `LLMClient(model=KSS_LLM_MODEL).complete(system=PROMPTTemplate, user=formatted_items)`。**注意**：实际 API 是 `complete(system, user)`，不是 plan 初稿写的 `chat_completion(messages, model)`——后者不存在。
影响：U1 Python 端 `digest_ai.py` 薄壳封装；prompt 在 Python 端组装后传入。

## KTD-2. bridge 命令同步（非流式）而非 chat-turn 长连
依据：digest 是 fire-and-forget，3-5 条要点不需要逐字渲染；chat-turn 是 streaming + confirm gate 的交互协议，shape 不同。**不要把 chat-turn 当作"被复用的链路"**——我们只复用 LLM 凭据/客户端。新建 `intel-digest` 同步命令走 subprocess，3-15s 内返回完整文本。
影响：U2 在 Swift `subprocessOnlyCommands` 加 `"intel-digest"`（绕过 3s sidecar 超时）；UI 显示单一 loading state。

## KTD-3. 沉淀库用 Markdown + JSON 双格式
依据：用户回看要易读（md），agent 检索要可解析（json）。两份写入，atomic 改名防半写。**复用** `kss/news/digest.py` 的 `archive_digest` 模式（`tempfile.NamedTemporaryFile` + `os.replace`），不复建独立归档基础设施。
影响：U1 实现 `kss/storage/notes.py` 的 `save_intel_digest(...)`。

## KTD-4. 一键提炼走串行而非并发
依据：12 路并发 LLM 调用触发 OpenAI 429 风险 + 增加客户端内存峰值；3-5 路/批次的小并发也不必要（赛道数固定 12）。**plan 初稿误以为有 TaskGroup，实际 IntelView/KSSStore 当前没有 digest 相关 TaskGroup——这是新功能**，不是"从并发替换为串行"。建议引用 BridgeClient.subprocessOnlyCommands 与 sidecar 3s 超时的真实约束来说明串行必要。
影响：U4 Swift 端 `for await` 串行 await；提供取消按钮（`Task.isCancelled`）。

## KTD-5. 25 条资讯截断而非全部
依据：news_sources.json 的 `per_source: 6` × 平均 12 源 ≈ 72 条/赛道，但时间倒序后的 25 条是当周代表性内容；25 条 × ~60 token/条 ≈ 1500 token prompt 余量，留给指令与回复 4-5k token。**注意**：60 token/条是估算，实际 RSS 标题长度差异大；应同时按字符数（如 ≤ 12k chars）二次截断保底。
影响：U1 Python 端 `items[:25]` 截断 + 字符数兜底。

# Scope Boundaries

## In Scope
- U1: bridge 后端 `intel-digest` 命令 + Python 沉淀库 (`kss/news/digest_ai.py`、`kss/storage/notes.py`)
- U2: BridgeClient + KSSStore + dispatch 注册（含 Swift `subprocessOnlyCommands` 白名单 + `WRITE_COMMANDS`）
- U3: SwiftUI 卡片 + 一键提炼按钮 + 存入沉淀按钮
- U4: 串行批处理全部赛道 + 进度反馈
- U5: 测试覆盖（Python unit + Swift UI smoke）

## Out of Scope (this plan)
- 要点的二次编辑 / diff / 删除已沉淀的要点
- 多模型选择（A/B 比较不同 LLM 输出）
- 跨赛道对比视图（一次生成"今日跨赛道要点"）
- 要点的 push 通知 / Telegram 转发
- 把沉淀库暴露到 MCP / Seesaw agent tool

## Deferred to Follow-Up Work
- 沉淀库全文搜索 UI（沉淀库有但缺入口，留作下一轮 Notes 页面）
- 要点缓存（同一赛道不重复调 LLM，留作 cron 优化）
- 把 ChatMessage 历史拉回到「今日要点」支持多轮追问

# Implementation Units

### U1. Bridge: intel-digest 命令 + Python 沉淀库

**Goal**: 新增 `intel-digest` bridge 命令接收 track 信息和资讯列表，调 LLM 生成要点并保存到沉淀库。

**Requirements**: R3, R4, R5

**Files**:
- `scripts/kss_app_bridge.py` — 注册 `intel-digest` 命令、新增 `_intel_digest()` handler、`COMMANDS` 注册、`WRITE_COMMANDS` 同步
- `kss/news/digest_ai.py` — 新增：`build_prompt(track_name, items)`、`call_llm(system, user)`（薄壳封装 `LLMClient.complete`）、`run_digest(track_key, track_name, items, force=False)`
- `kss/storage/notes.py` — 新增：`save_intel_digest(track_key, track_name, prompt, response, model, items, date=None)` 写 md+json（用 `tempfile.NamedTemporaryFile` + `os.replace` 实现 atomic write）
- `kss/tests/test_intel_digest.py` — 新增：`test_build_prompt_limit_25`、`test_save_intel_digest_creates_files`

**Approach**:
- `_intel_digest(json_payload)`：解析单参数 JSON `{track_key, track_name, items}` → 截断 25 → 组装 prompt → 调 `LLMClient(model=KSS_LLM_MODEL).complete(system=PROMPTTemplate, user=formatted_items)` → 拿到 text → 调 `kss.storage.notes.save_intel_digest()` → 返回 `{text, model, saved_path, generated_at, error_type?}`
- prompt 模板：
  ```
  以下是「{track_name}」赛道近期资讯。请提炼「今日要点」3-5 条：
  每条一句话（≤40 字），只客观陈述重要事件/趋势，不推荐标的、不预测涨跌、不构成建议。
  直接用「- 」列点，不要多余前后缀。

  [07-09 03:30] TechCrunch AI｜SpaceXAI releases Grok 4.5...
  ...
  ```
- 错误：OpenAI 返回非 200 → 返回 `{"error": "<status>: <msg>", "error_type": "auth|rate_limit|server"}`（不要抛 Swift 类型）；items 为空 → 立即返回 `{text: "", skipped: true}`；30s 网络超时由 LLMClient 的 `timeout=30, max_retries=0` 强制，超时则 `error_type="timeout"`
- 沉淀文件：md 内容 = `# {track_name} 要点 · {date}\n\n{response}`；json 字段 = `{track_key, track_name, prompt, response, model, item_count, generated_at}`。文件名：`intel_digest_<YYYYMMDD>_<track_key>.md` / `.json`（YYYYMMDD 与 `date` 字段同步）
- `force=False` 时，若今日（`YYYYMMDD`）沉淀已存在则直接返回（不重复调 LLM）
- `force=True` 强制重新生成

**Patterns to follow**: 
- `scripts/kss_app_bridge.py` 中 `_intel_radar()` 的 `INTEL_RADAR_DIR` 路径处理模式
- `kss/news/digest.py` 中 `archive_digest(..., overwrite=True)` 的 atomic write 模式（`tempfile` + `os.replace`）
- `kss/llm/openai_client.py` 的 `LLMClient.complete(system, user)` 接口签名（不是 `chat_completion`）

**Test scenarios**:
- Happy path: 传入 30 条 items → 只截取前 25 条进入 prompt
- Happy path: LLM 成功返回 → md 文件包含 track_name + 4 行 ` - ` bullet
- Happy path: 今日沉淀已存在 + `force=False` → 不调 LLM，直接返回已有 text
- Edge: items 为空 → 返回 `{text: "", skipped: true}`，不写文件
- Error: OpenAI 返回 401 → 返回 `{"error": "401: ...", "error_type": "auth"}`
- Error: items 字段为非法 JSON → 返回 `{"error": "invalid items_json"}`
- Integration: 30s 网络超时通过 `LLMClient(timeout=30, max_retries=0)` 强制

**Verification**: `python3 kss/tests/test_intel_digest.py` 全绿；`python3 scripts/kss_app_bridge.py intel-digest '{"track_key":"ai","track_name":"AI / 大模型","items":[{"title":"test","time":"07-09 10:00","source":"X"}]}'` 返回带 `text` 的 JSON envelope（依赖真实 key；用 mock 测试路径）

### U2. BridgeClient + KSSStore + Swift dispatch 注册

**Goal**: 在 Swift 端注册新 bridge 命令到 `subprocessOnlyCommands`（绕过 3s sidecar 超时）+ KSSStore 添加 digest 状态字段。

**Requirements**: R1, R2, R3

**Files**:
- `Sources/KSSDesktop/Services/BridgeClient.swift` — `subprocessOnlyCommands` 加 `"intel-digest"`（line 211）；新增 `func intelDigest(trackKey:trackName:items:force:) throws -> IntelDigestResponse`
- `Sources/KSSDesktop/Services/KSSStore.swift` — 新增 `@Published var intelDigests: [String: IntelDigestResponse]`；新增 `@Published var bulkDigest: BulkDigestState`（running/done/total）；新增 `@Published var hasLLMCredentials: Bool`（启动时检测）；新增 `func summarizeIntelTrack(_ key: String, name: String, items: [IntelItem], force: Bool) async`；新增 `func summarizeAllIntelTracks(force: Bool) async` 串行实现（`guard let tracks = intelDigest?.tracks else { return }`）；新增 `func saveIntelDigestToNotes(trackKey:) async`

**Approach**:
- Swift `subprocessOnlyCommands` 加 `"intel-digest"`（line 211 已有 4 个，加第 5 个）；该命令 5-15s，必须走 subprocess 避免 sidecar 3s socket 超时
- `WRITE_COMMANDS` frozenset 加 `"intel-digest"`（Python 端会写文件到 STATE_ROOT/storage/notes/，属于写副作用）
- `KSSStore.bulkDigest` 是 `@Published` 而非 View `@State`（view 不能 mutate 跨方法的状态）
- `hasLLMCredentials` 启动时检测 Keychain 中的 `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` 任意一个存在即可
- `summarizeAllIntelTracks` 串行：`for track in tracks where !track.items.isEmpty { await summarizeIntelTrack; bulkDigest.done += 1; if Task.isCancelled { break } }`；提供取消支持（`Task` 句柄存到 store + 「取消」按钮）
- `saveIntelDigestToNotes` 是另一条路径：UI 「存入沉淀」按钮触发，调新加的 `intel-digest-save` bridge 命令（不是 `intel-digest`），或扩展 `intel-digest` 的参数（`save=true`）；详见下方的写沉决定义

**Patterns to follow**: 
- `Sources/KSSDesktop/Services/BridgeClient.swift` 中已有的 `intelRadar(force:)` 接口签名
- `Sources/KSSDesktop/Services/KSSStore.swift` 中 `loadIntelRadar(force:)` 的 `do/catch` 错误捕获模式
- `Sources/KSSDesktop/Services/KSSStore.swift` 中 `loadSectorRotation(...)` 的串行 await 模式

**Test scenarios**:
- Happy: dispatch 表的 key 集合 ⊆ COMMANDS 元数据（保持既有漂移守卫）
- Integration: 调用 `intel-digest '{"track_key":"ai","track_name":"X","items":[]}'` 返回 `{text: "", skipped: true}`
- Swift `subprocessOnlyCommands` 含 `"intel-digest"`（编译期验证：`bridge.intelDigest(...)` 不会走 sidecar）
- `WRITE_COMMANDS` 含 `"intel-digest"`（防 reads-only 闸误判）

**Verification**: `python3 -c "import scripts.kss_app_bridge as b; assert 'intel-digest' in b.COMMANDS"` 通过；`grep intel-digest Sources/KSSDesktop/Services/BridgeClient.swift` 显示已加白名单

### U3. Swift: IntelView digest 状态机 + 卡片 UI

**Goal**: 给 IntelView 加 digest 状态、UI 卡片、单赛道「让 AI 提炼今日要点 / 重新提炼 / 存入沉淀」按钮。沉淀库写入由 Swift `saveIntelDigestToNotes` 调用 `intel-digest-save` 触发（不在 bridge 自动写）。

**Requirements**: R1, R4, R5

**Files**:
- `Sources/KSSDesktop/Views/IntelView.swift` — 新增 `digestCardView(_ track: IntelTrack)` 渲染；新增 `IntelDigestState` enum (idle/loading/done/error/saved/needKey)；新增「一键提炼全部要点」按钮（位置：statsRefreshRow 或 PageTitle HStack，避免与 stat-line 挤位）

**Approach**:
- **写沉决定义（U2 决议）**：bridge `intel-digest` **不**写文件，只返回 `{text, model, error_type?, generated_at}`；UI 「存入沉淀」按钮调 `store.saveIntelDigestToNotes(trackKey:)`，后者调新加的 `intel-digest-save` bridge 命令写 md+json。这避免「bridge 自动写 + UI 按钮也写」的矛盾。
- 卡片主题：fill 用 `theme.accentSoft`（dark 0.16 / light 0.12，已有 token）；border 用 `theme.accent.opacity(0.35)`（匹配 L165 active-pill）；外层 `.kssCard(.outlined, padding: 14)`，**不要** raw alpha 写 0.05 / 0.3
- 标题：`Image(systemName: "lightbulb") + Text(" · \(cur.name)")`，`.font(KSSFont.title(15, .bold, design: theme.titleDesign))`，`.foregroundStyle(theme.accent)`。**不要**用 emoji 💡（theme tint 受限）；**不要**用 KSSFont.serif（旧 API）
- bullets：`Text(d).font(.system(size: 13)).foregroundStyle(theme.textBody)`；用 `try? AttributedString(markdown: response, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))` 渲染（AIChatView L278 同款），**不要**用 `newsAttributed()`（其剥 `<b>`/`<u>` 标签会同时剥掉 markdown bullet）
- meta line：`HStack { Text(model).font(.system(size: 10.5, design: .monospaced)); Text(generatedAt).font(.system(size: 10.5, design: .monospaced)) }` 用 `theme.textSecondary`
- 错误条：`Image(systemName: "exclamationmark.triangle.fill") + Text(err)` over `theme.down.opacity(0.1)` bg（**不要**用红色 raw 或 `theme.up`——A 股红涨绿跌语义会冲突）；下面加「重试」按钮（plain-button 风格同 statsRefreshRow）
- saved-disabled vs needKey-disabled 视觉区分：saved 用 `theme.textSecondary` + `opacity(0.5)` + ✓ checkmark；needKey 用 `theme.ma5` 提示色（金/橙）+ 「前往设置」链接（点击切到 Settings section）
- 「一键提炼全部要点」按钮位置：`HStack { PageTitle(...); Spacer(); bulkButton; StatusBadge(...); }`（参考 DashboardView market-strip badges 模式），避免 statsRefreshRow 横向拥挤；label 用 `.lineLimit(1).fixedSize()` 控制宽度
- 卡片放在 `trackNewsList(_ cur:)` 函数内部、track-header HStack 之后、items VStack 之前（继承 activeTrack 上下文）
- 进度反馈：每个赛道进入 loading 前同步设 `digests[key] = .loading`（用户能看见哪个 track 当前正在跑）；bulk 完成时显示「完成 X/12 · 失败 Y」4s 后自动消失

**Patterns to follow**:
- `KSSStore.sendChat()` 的错误捕获模式 → `summarizeIntelTrack` 同样 `do/catch` 写 `errorMessage`
- `IntelView` 既有 trackPills 的胶囊按钮风格
- `StatusBadge` 已有 role enum，可复用 `.accent` / `.neutral` / `.skipped` tint

**Test scenarios**:
- 卡片在 `digests[key] == nil` 或 `.idle` 时不渲染（避免空态噪音）
- 卡片在 `loading` 状态显示 spinner + 「AI 正在读 {N} 条资讯…」
- 卡片在 `done` 状态显示 markdown 渲染的要点列表（≥3 条 ≤5 条 ` - ` 开头）
- 卡片在 `saved` 状态：button 变灰禁用（textSecondary.opacity(0.5)）+ 文案「已存入沉淀」+ ✓ checkmark
- 卡片在 `error` 状态：theme.down 错误条 + 「重试」按钮
- 卡片在 `needKey` 状态：theme.ma5 提示 + 「前往设置」链接
- 卡片在切换赛道时不残留前一个赛道的内容（`activeTrack` 变化不重置 digests，多赛道状态并存）
- bulk 运行时 `bulkDigest.done` 计数准确（跳过空赛道）
- bulk 取消按钮：`Task.isCancelled` 时立即退出循环

**Verification**: SwiftUI Preview 渲染五个状态截图一致；运行 `swift build --build-system native` 通过

### U4. Swift: 一键提炼全部要点（串行 + 进度 + 取消 + 重试失败）

**Goal**: 顶部 statsRefreshRow 加「一键提炼全部要点」按钮，串行逐赛道调用；支持取消和失败重试。

**Requirements**: R2, R5

**Files**:
- `Sources/KSSDesktop/Views/IntelView.swift` — `statsRefreshRow` 加「一键提炼全部要点」按钮、「取消」按钮、「重试 N 个失败」按钮（完成后按需显示）
- `Sources/KSSDesktop/Services/KSSStore.swift` — 新增 `func summarizeAllIntelTracks(force: Bool) async` 串行实现；`func retryFailedBulkDigests()` 仅重试 `digests[key].error` 的 track；`func cancelBulkDigest()` 触发 Task 取消；`bulkDigest` 改为 `@Published` 而非 View `@State`（view 不能 mutate 跨方法状态）；`failedBulkKeys: [String]` 跟踪失败 key

**Approach**:
- `bulkDigest` 结构: `{ running: Bool, done: Int, total: Int, failedCount: Int, currentTask: Task<Void, Never>? }`
- `summarizeAllIntelTracks()`：遍历 `intelDigest?.tracks`，对 `items.count > 0` 的赛道逐个 await `summarizeIntelTrack`，每完成更新 `bulkDigest.done += 1`；每开始一个 track 同步设 `digests[key] = .loading` 让用户能看到当前 track 状态；遇 `Task.isCancelled` 立即退出循环
- 「取消」按钮：`bulkDigest.currentTask?.cancel()`（取消会在下次 await 检查 `isCancelled` 时退出；**当前正在跑的 LLM 调用会跑完**，不杀进程）
- 「重试 N 个失败」按钮：扫描 `digests` 收集 `error` 状态的 keys，串行重跑这些 keys（用 `digests[key] = .loading` 进入 loading）
- 「一键提炼全部要点」 + 「取消」 + 「重试 N 失败」三按钮根据 `bulkDigest.running / failedCount` 状态切换显示
- 完成后显示 summary「完成 X/{total} · 失败 Y」4s 后自动消失

**Patterns to follow**:
- `IntelView` trackPills 的 `Button(action:)` 模式
- `KSSStore.loadSectorRotation(...)` 类似的串行 await 模式

**Test scenarios**:
- 串行：12 个赛道（5 空 + 7 有数据）调用 7 次 LLM，跳过 5 个空赛道
- 并发约束：调用期间 store 内部不并发派发多个 LLM
- 进度反馈：`bulkDigest.done` 在每个赛道完成后 +1；`digests[key]` 同步进入 loading 状态
- 单赛道失败不影响其他赛道：`bulkDigest.running` 仍为 true，继续后续；失败 track 进 `digests[key] = .error`
- 「取消」：点取消后当前 LLM 跑完后退出；下一个 track 不再开始
- 「重试 N 失败」：bulk 跑完后 failedCount > 0 时按钮显示；点击后只跑失败的 key，成功的不重跑
- 全部完成后：`bulkDigest.running = false`，按钮恢复可点

**Verification**: 12 赛道 mock LLM 测试串行顺序；按钮 disabled/enabled 状态转换；取消路径测试；retry-failed 路径测试

### U5. 测试 + 部署

**Goal**: 单元测试 + 重新打包签名公证发布。

**Requirements**: R1-R5

**Files**:
- `kss/tests/test_intel_digest.py` — U1 单测（prompt 截断、`LLMClient.complete` mock、错误返回、timeout 路径）
- `kss/tests/test_notes_storage.py` — U1 沉淀库单测（atomic write、md/json 双格式）
- `Tests/KSSDesktopTests/IntelViewDigestTests.swift` — U3 SwiftUI snapshot/逻辑测试（**注意：项目 test target 实际路径是 `Tests/KSSDesktopTests/`，不是 `Sources/KSSDesktopTests/`**）

**Approach**:
- Python 单测：mock `LLMClient.complete`（不是 `chat_completion`），测 prompt 截断 + 文件写入 + 30s timeout 触发
- Swift 单测：`BridgeClient.intelDigest(...)` 用 mock URL session 注入；断言不经过 sidecar（`subprocessOnlyCommands` 含 `"intel-digest"`）
- 部署流程同 U1 模式：`swift package clean` → `script/sign_and_build.sh` → notarize → staple → `/Applications`

**Test scenarios**:
- 全套 Python 测试 `python3 -m pytest kss/tests/test_intel_digest.py kss/tests/test_notes_storage.py` 全绿
- 既有 `kss/tests/test_news_digest.py` 不被破坏（共用 OpenAI client）
- `swift build --build-system native` 通过且零新增 warning（既有 onChange deprecation warning 除外）
- Swift test：5 个 DigestState 视觉快照测试；bulkDigest 进度反馈测试；cancel 路径测试
- notarize 接受 + spctl 通过

**Verification**: `python3 -m pytest kss/tests/ -v` 全绿；app 部署后启动测试五种状态

# Risks & Dependencies

## Risks

- **R-R1**: OpenAI API key 缺失 → 用户体验断裂。Mitigation: U3 卡片显示「前往设置」链接，无凭据时禁用按钮。
- **R-R2**: 一键提炼全部赛道耗时 = 12 × 5s = 60s，用户体验长。Mitigation: 串行 + 进度反馈 + 可中途取消（`bulkDigest.running` flag + Task 取消）。
- **R-R3**: 沉淀库无清理机制，文件无限增长。Mitigation: 不在本轮处理；`### Deferred to Follow-Up Work` 列出"沉淀库全文搜索 UI"后续工作；可后加清理 cron。
- **R-R4**: LLM 输出含 HTML/Markdown 影响渲染。Mitigation: U3 Swift 端用 `newsAttributed(...)` 同款 markdown 解析器（已有 [NewsDigestView.swift:98](../../Sources/KSSDesktop/Views/NewsDigestView.swift)）。

## Dependencies

- **U1 → U2**: U1 实现 handler，U2 注册 dispatch
- **U2 → U3**: U2 命令注册后 U3 Swift 才能调用
- **U3 → U4**: U4 复用 U3 的 `summarizeIntelTrack` 单赛道入口
- **U4 → U5**: U5 测试 + 部署需要所有单元完成

# Open Questions

- **OQ-1**: 沉淀库文件命名是否需要带「track_key」版本？计划用 `intel_digest_<date>_<track_key>.md`，同日同赛道第二次保存覆盖。需要决定是否保留多版本快照？**推荐**：覆盖（简单），但若用户需求是历史快照可改为 `<date>_<track_key>_<timestamp>.md`。**Plan 默认覆盖**，实施时如需快照留 TODO。
- **OQ-2**: 一键提炼全部要点是否要后台异步（非阻塞 UI）还是前台串行（用户必须等）？**推荐**：前台串行（用户主动触发 + 进度反馈明确），不再开新方案。
- **OQ-3**: 沉淀库是否需要在 Notes 页面（侧栏）暴露入口？**本轮 OOS**，留作下一轮 Notes 页面工作。

# Acceptance Examples

- **AE-1**: 在 AI 赛道，「让 AI 提炼今日要点」点击 → loading spinner → ≤15s 后显示 ≥3 条 ≤40 字的 bullet。
- **AE-2**: 「存入沉淀」点击 → 按钮变灰显示「已存入沉淀」→ 文件 `storage/notes/intel_digest_<today>_ai.json` 出现且包含 prompt、response、model 字段。
- **AE-3**: 「一键提炼全部要点」点击 → 按钮变「提炼中 1/{N} → 2/{N} → ... → {N}/{N}」（N = 非空赛道数 ≤ 12）→ 完成后回到 idle，全部非空赛道卡片都有内容。
- **AE-4**: 无 OpenAI key 时点击「让 AI 提炼今日要点」→ 显示「未接入 AI」占位 + 按钮禁用。
- **AE-5**: 切换到 AI 赛道后再切到 Semi 赛道 → AI 赛道卡片保留之前状态不丢失；新赛道卡片显示「让 AI 提炼今日要点」按钮（首次进入）。

# Sources & Research

- **本地调研**:
  - [Sources/KSSDesktop/Views/IntelView.swift](../../Sources/KSSDesktop/Views/IntelView.swift) — 既有 12 赛道 + 新闻列表布局，commit `f19323e`
  - [Sources/KSSDesktop/Views/AIChatView.swift](../../Sources/KSSDesktop/Views/AIChatView.swift) — 既有 chat-turn 流式集成模式 + `AttributedString(markdown:)` 渲染参考 L278
  - [Sources/KSSDesktop/Services/BridgeClient.swift](../../Sources/KSSDesktop/Services/BridgeClient.swift) — `chatTurn` 接口 + `subprocessOnlyCommands` 白名单 (line 211)
  - [Sources/KSSDesktop/Services/KSSStore.swift](../../Sources/KSSDesktop/Services/KSSStore.swift) — `loadIntelRadar`、`refreshIntelRadar` 已就位
  - [Sources/KSSDesktop/Support/Theme.swift](../../Sources/KSSDesktop/Support/Theme.swift) — `KSSFont.title(_:_:design:)` + `kssCard(.outlined, padding:)` 模式
  - [Sources/KSSDesktop/Support/ThemeTokens.swift](../../Sources/KSSDesktop/Support/ThemeTokens.swift) — `accentSoft` (0.12/0.16) + 主题 token 表
  - [Sources/KSSDesktop/Support/Components.swift](../../Sources/KSSDesktop/Support/Components.swift) — `StatusBadge` role enum + `.ma5` tint 用法
  - [scripts/kss_sidecar.py](../../scripts/kss_sidecar.py) — `_handle_chat_turn` 使用 `kss/llm/openai_client.py`（参考其凭据注入模式）
  - [scripts/kss_app_bridge.py](../../scripts/kss_app_bridge.py) — `_intel_radar()` 模式 + `COMMANDS`/`WRITE_COMMANDS` 注册
  - [kss/news/digest.py](../../kss/news/digest.py) — `archive_digest()` atomic write 模式（`tempfile` + `os.replace`）
  - [kss/llm/openai_client.py](../../kss/llm/openai_client.py) — `LLMClient.complete(system, user)` 实际接口（非 `chat_completion`）
- **外部参考**:
  - Vibe-Research `frontend/src/pages/Intel.tsx` — 截图对应布局（`InvestmentNewsPanel` + `genDigest` + `genAll`）
  - Vibe-Research `frontend/src/components/ui/SaveNoteButton.tsx` — 沉淀按钮模式
  - Vibe-Research `frontend/src/lib/llm.ts` — LLM chat stream 接口（`chatStream`）

# Definition of Done

- [ ] `python3 -m pytest kss/tests/` 全绿（含新增 test_intel_digest.py + test_notes_storage.py）
- [ ] `swift build --build-system native` 通过零新增 warning
- [ ] `script/sign_and_build.sh` 成功 + notarize Accepted + spctl passed
- [ ] `/Applications/KSSDesktop.app` 安装且 spctl 输出 `accepted (Notarized Developer ID)`
- [ ] 资讯雷达页面打开后所有 5 个验收示例（AE-1 至 AE-5）在真实 app 中验证通过
- [ ] 沉淀库文件生成于 `STATE_ROOT/storage/notes/` 且 md/json 双格式完整