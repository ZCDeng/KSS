import Foundation
import Combine

@MainActor
final class KSSStore: ObservableObject {
    @Published var snapshot: AppSnapshot?
    @Published var selectedSection: WorkspaceSection = .dashboard
    @Published var selectedSymbol: String?
    @Published var selectedReportPath: String?
    @Published var reportDetail: ReportDetail?
    @Published var stockDetail: StockDetail?
    @Published var perillaEnrichment: PerillaEnrichment?   // 紫苏叶个股富化（仅 core/main 票有内容）
    @Published var isLoadingPerilla = false
    @Published var sectorRotationDetail: HotspotRotationSnapshot?
    @Published var isLoadingSectorRotation = false
    @Published var newsDigest: NewsDigestResponse?
    @Published var isLoadingNewsDigest = false

    @Published var isLoading = false
    @Published var isLoadingReport = false
    // 引用计数：并发任务（U5 快速连加自选每次起独立 Task）下，单个完成不会把
    // 「运行中」过早清零——只有全部完成才回到 idle。
    @Published private(set) var runningTasks = 0
    var isRunningTask: Bool { runningTasks > 0 }
    /// 进行中的形式任务 id（`KSSTask.rawValue`）；用于日线徽标只绑 update-cs-data，避免其它任务冒充「日线更新中」。
    @Published private(set) var activeFormalTaskId: String?
    var isUpdatingCsData: Bool { isRunningTask && activeFormalTaskId == KSSTask.updateCsData.rawValue }
    @Published var taskResults: [TaskRunResult] = []
    @Published var scheduledJobs: [ScheduledJob] = []
    // 分类展示顺序由 bridge cron-list 下发(清单单一真源,U5);默认值仅作首帧/失败兜底。
    @Published var cronCategoryOrder: [String] = ["数据更新", "扫描选股", "板块复盘", "盘中快讯", "纸交易", "校验回测", "系统", "其他"]
    @Published var scheduledBusy: Set<String> = []   // 正在操作的 label（行级 loading）
    @Published var scheduledBatchBusy = false         // 批量补跑/重跑进行中
    @Published var scheduledBatchNote: String?        // 批量操作结果提示（一次性 toast 文案）
    @Published var themeLeaders: [ThemeLeaders] = []
    @Published var trendMonth: TrendMonth?            // 当前月月度格子
    @Published var trendDayDetail: TrendDayDetail?    // 选中日明细
    @Published var trendsLoading = false
    @Published var selectedTrendDate: String?         // 选中日（YYYY-MM-DD）
    @Published var importingSymbol: String?   // 点击导入进行中的代码（行级/全局指示）
    @Published var errorMessage: String?

    // MARK: U2 资讯雷达 — bridge news-digest 回应（多赛道分组）
    @Published var intelDigest: NewsDigestResponse?
    @Published var isLoadingIntel = false

    // MARK: 资讯雷达 AI digest（plan 2026-07-09-001）
    @Published var intelDigests: [String: IntelDigestResponse] = [:]
    /// 显式 loading 集合：勿用「text 为空」兼作 loading（并发重入会盖掉已成功结果并永远转圈）。
    @Published var intelDigestLoadingKeys: Set<String> = []
    /// 每赛道序号，丢弃过期的 in-flight 响应。
    private var intelDigestEpoch: [String: Int] = [:]
    @Published var bulkDigest = BulkDigestState()
    /// 12 赛道全景热点（独立 LLM，跟一键提炼一并生成）
    @Published var intelPanorama: IntelPanoramaResponse?
    @Published var intelPanoramaLoading = false
    /// 启动时检测 Keychain 中是否有 OpenAI/DeepSeek 凭据
    @Published var hasLLMCredentials: Bool = false

    // MARK: 启动自检（plan 2026-07-12-005 / U8）
    @Published var selfCheckItems: [SelfCheckItem] = []
    @Published var selfCheckGeneratedAt: String?
    @Published var isRunningSelfCheck = false
    /// 当前会话内是否已手动关闭 fail 横幅（会话内不再自动弹，重跑自检后重置）。
    @Published var selfCheckBannerDismissed = false
    var selfCheckHasFail: Bool { selfCheckItems.contains { $0.isFail } }
    var selfCheckHasWarn: Bool { selfCheckItems.contains { $0.isWarn } }
    var showSelfCheckBanner: Bool { selfCheckHasFail && !selfCheckBannerDismissed }

    /// 单一凭证真源（U9/R12）：某数据源是否已配置。以 self-check 结果为准（沿用
    /// U4 hasLLMCredentials 的先例，按源扩展为字典查询）。自检结果到达前返回 nil
    /// （"未知"而非"未配置"）——避免首帧还没跑完自检就误判成缺凭证闪一下卡片。
    /// source ∈ "tushare" | "longbridge" | "telegram" | "llm"。
    func isCredentialConfigured(_ source: String) -> Bool? {
        guard let item = selfCheckItems.first(where: { $0.item == source }) else { return nil }
        return !item.isWarn   // warn＝该源未配置；ok＝已配置（fail 不会用于凭证项，只用于 venv/storage）
    }

    // MARK: 资讯雷达 reader workbench（plan 2026-07-10-001）
    @Published var selectedIntelItemID: String?
    @Published var intelArticleByID: [String: IntelArticleResponse] = [:]
    /// kind → itemId → response（investment / chinese）
    @Published var intelRewriteByKind: [String: [String: IntelRewriteResponse]] = [:]
    @Published var isLoadingIntelDetail = false
    private var intelDetailTask: Task<Void, Never>?
    private var intelRewriteRunTask: Task<Void, Never>?

    /// 兼容旧调用：投研改写 map
    var intelRewriteByID: [String: IntelRewriteResponse] {
        intelRewriteByKind["investment"] ?? [:]
    }

    func rewrite(for itemID: String, kind: String) -> IntelRewriteResponse? {
        intelRewriteByKind[kind]?[itemID]
    }

    func setRewrite(_ resp: IntelRewriteResponse, itemID: String, kind: String) {
        var bucket = intelRewriteByKind[kind] ?? [:]
        bucket[itemID] = resp
        intelRewriteByKind[kind] = bucket
    }

    /// Bulk 一键提炼全部要点状态。
    struct BulkDigestState {
        var running: Bool = false
        var done: Int = 0
        var total: Int = 0
        var failedCount: Int = 0
        var currentTask: Task<Void, Never>?
        var summaryShownUntil: Date?  // 4s 后消失
    }

    // MARK: Longbridge 实时（U1/U2）—— 页面加载时拉取，失败保持 nil（UI 回退存量 + 标注"非实时"）
    @Published var realtimeQuote: LongbridgeQuote?     // 兼容 canary（上证 / 任一 live）
    @Published var realtimeQuotesBySymbol: [String: LongbridgeQuote] = [:]  // 多标的 map，供 今日看盘 overlay
    /// 堆叠卡等分时线：产品码 → 当日 1m 收盘序列（Longbridge intraday-bars）
    @Published var realtimeSparklinesBySymbol: [String: [Double]] = [:]
    @Published var tradingHours: TradingHours?         // 交易时段门控（R13）
    @Published var realtimeAuthFailed = false          // auth_failed → 停定时刷新 + "实时源未连接"（R4）
    @Published var realtimeUpdatedAt: Date?            // 最近一次实时拉取成功时间（"更新于 HH:MM"）

    // MARK: U5 Timer 基础设施（R9/R10/R13/R14）
    @Published var refreshTimestamp: Date?             // 定时刷新 tick（紫苏叶/国产替代 Section 监听此值触发重算）
    private var timerCancellable: AnyCancellable?
    private var scenePhaseActive = false
    private var lastDispatchCache: [String: Date] = [:]  // R14: coalescing cache (cmd:symbol → last dispatch)
    private static let minIntervalSeconds: Double = 120  // R14: 最小间隔 2min
    private static let coalesceSeconds: Double = 30     // R14: 同标的+同命令 30s 内复用
    /// 产品：交易时段 2 分钟真刷新（非 MainActor 默认参数安全的字面量）
    nonisolated private static let refreshIntervalSeconds: Double = 120

    // MARK: AI 复盘助手聊天态（#4 U4/U5）—— 会话历史归 store，section 切换不丢
    @Published var chatMessages: [ChatMessage] = []
    @Published var isChatStreaming = false
    @Published var chatToolInProgress: String?            // 正在调用的工具名（进度指示）
    @Published var pendingWriteConfirm: PendingWriteConfirm?   // 待人工确认的写（app-modal）
    /// 当前阻塞中的 confirm 闸（后台流式线程持，UI tap 后 resolve）。
    private var activeConfirmGate: ChatConfirmGate?

    let bridge: BridgeClient?

    init() {
        self.bridge = try? BridgeClient()
        refreshLLMCredentialsStatus()
    }

    // MARK: - 聊天一轮（流式 + 人在环内写闸）

    func sendChat(_ text: String) {
        guard let bridge else { errorMessage = "Cannot locate KSS project root"; return }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isChatStreaming else { return }   // 单活动轮（R11）
        chatMessages.append(ChatMessage(role: .user, text: trimmed))
        let assistant = ChatMessage(role: .assistant, text: "", numbersUnverified: true)
        chatMessages.append(assistant)
        let assistantId = assistant.id
        isChatStreaming = true
        chatToolInProgress = nil
        // 发给 sidecar 的历史：仅 user/assistant，去掉本轮空 assistant 占位。
        let payload: [[String: String]] = chatMessages.dropLast().map { m in
            ["role": m.role == .user ? "user" : "assistant", "content": m.text]
        }
        Task.detached { [weak self] in
            bridge.chatTurn(
                messages: payload,
                onFrame: { frame in
                    Task { @MainActor [weak self] in self?.applyChatFrame(frame, assistantId: assistantId) }
                },
                onConfirmRequired: { frame in
                    // 后台线程:建闸 → 主线程弹 modal → 阻塞等本人 tap（默认拒由 dismiss 触发）。
                    //
                    // 同一会话内多轮写确认时，SwiftUI 的 .sheet(item:) 有时不会为一次状态切换
                    // 触发呈现——底层状态（pendingWriteConfirm）已经设对（日志证实过），弹窗却
                    // 悄悄不出现。复现过两种触发方式（连续 approve、reject 后隔了近一分钟的新一
                    // 轮），根因没能钉死在 SwiftUI 内部的哪一层，于是曾经尝试过"超时未 tap 就重新
                    // 呈现一次"的自愈重试——**这个方向本身是错的，已回退**：重试时若上一次其实
                    // 已经真呈现在屏幕上，再次把 pendingWriteConfirm 置 nil 会被 SwiftUI 当成一次
                    // 真实的用户 dismiss，触发 .sheet 的 onDismiss（= 隐式拒绝），把用户还没来得及
                    // 看到/点的写操作静默拒掉——比原来的卡死更糟（错误结果不可见，而不是可见地卡住）。
                    // 现只保留"首次呈现前置空一次、下 tick 再赋值"这一步（对 nil→nil 无害，不会误
                    // 触发 onDismiss），不做后续定时重试；SwiftUI 呈现失败仍可能复现，但至少不会
                    // 把静默拒绝当成用户的真实决定。
                    let gate = ChatConfirmGate()
                    DispatchQueue.main.async { [weak self] in
                        guard let self else { gate.resolve(false); return }
                        self.activeConfirmGate = gate
                        let ctx = self.chatMessages.last(where: { $0.role == .assistant })?.text ?? ""
                        let confirm = PendingWriteConfirm(
                            callId: frame.callId ?? "", tool: frame.tool ?? "",
                            command: frame.command ?? "", effect: frame.effect ?? "执行写操作",
                            argsText: frame.argsText ?? "", contextLine: ctx)
                        self.pendingWriteConfirm = nil
                        DispatchQueue.main.async { [weak self] in
                            self?.pendingWriteConfirm = confirm
                        }
                    }
                    return gate.wait()
                },
                onEnd: { err in
                    Task { @MainActor [weak self] in self?.endChat(assistantId: assistantId, error: err) }
                })
        }
    }

    /// 用户 tap 确认/拒绝（或 dismiss=拒）。解阻塞后台流式线程。
    func resolveWriteConfirm(approved: Bool) {
        pendingWriteConfirm = nil
        activeConfirmGate?.resolve(approved)
        activeConfirmGate = nil
    }

    private func applyChatFrame(_ frame: ChatFrame, assistantId: UUID) {
        guard let idx = chatMessages.firstIndex(where: { $0.id == assistantId }) else { return }
        switch frame.type {
        case "chunk":
            chatMessages[idx].text += frame.text ?? ""
        case "tool_call":
            chatToolInProgress = frame.name
        case "tool_done":
            mergeChatEvidence(frame, into: idx)
            chatToolInProgress = nil
        case "done":
            chatToolInProgress = nil
            // 数字守卫:无未核实数字则转正样式（R7/KTD-5）。
            let unverified = frame.numberGuard?.unverified ?? []
            chatMessages[idx].numbersUnverified = !unverified.isEmpty
            if frame.reason == "max_steps" {
                chatMessages[idx].text += "\n\n_（已达步数上限，优雅终止；如未答全请追问）_"
            } else if frame.reason == "timeout" {
                chatMessages[idx].text += "\n\n_（已达时间上限，优雅终止）_"
            }
        case "error":
            chatToolInProgress = nil
            if chatMessages[idx].text.isEmpty {
                chatMessages[idx].text = "出错了：\(frame.error ?? "未知错误")"
                chatMessages[idx].isError = true
            } else {
                errorMessage = frame.error
            }
        default:
            break
        }
    }

    private func mergeChatEvidence(_ frame: ChatFrame, into idx: Int) {
        if let summary = frame.evidenceSummary {
            chatMessages[idx].evidenceSummary.merge(summary)
        }
        if let drawer = frame.evidenceDrawer {
            chatMessages[idx].evidenceDrawer.merge(drawer)
        }
    }

    private func endChat(assistantId: UUID, error: String?) {
        isChatStreaming = false
        chatToolInProgress = nil
        activeConfirmGate = nil
        pendingWriteConfirm = nil
        guard let error else { return }
        if let idx = chatMessages.firstIndex(where: { $0.id == assistantId }),
           chatMessages[idx].text.isEmpty {
            chatMessages[idx].text = "连接中断：\(error)"
            chatMessages[idx].isError = true
        }
    }

    func loadSnapshot() async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        isLoading = true
        errorMessage = nil
        do {
            let snapshot = try await Task.detached {
                try bridge.snapshot()
            }.value
            self.snapshot = snapshot
            self.taskResults = mergeTaskResults(current: taskResults, persisted: snapshot.recentTaskRuns)
            if selectedSymbol == nil {
                selectedSymbol = snapshot.recommendations.first?.symbol ?? snapshot.stocks.first?.symbol
            }
            if let selectedSymbol {
                await loadStock(symbol: selectedSymbol)
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func loadStock(symbol: String) async {
        guard let bridge else { return }
        selectedSymbol = symbol
        perillaEnrichment = nil   // 清旧票富化，避免串台
        do {
            let detail = try await Task.detached {
                try bridge.stock(symbol: symbol)
            }.value
            self.stockDetail = detail
        } catch {
            errorMessage = error.localizedDescription
        }
        // 富化走外网较慢，fire-and-forget 异步加载，不阻塞个股明细渲染/caller。
        Task { await self.loadPerillaEnrichment(symbol: symbol) }
    }

    // MARK: - Longbridge 实时拉取（U2）

    /// 交易时段门控查询（R13）。返回是否应拉取实时——非交易时段直接展示存量。
    func loadTradingHours() async -> Bool {
        guard let bridge else { return false }
        let hours = try? await Task.detached { try bridge.tradingHours() }.value
        self.tradingHours = hours
        return hours?.isTradingSession ?? false
    }

    /// Dashboard onAppear / 手动重试：拉取 Longbridge 实时（默认可从 marketStrip 采标）。
    /// 非交易时段跳过；失败不整表清空（单标失败保留其它 map 项）。
    /// auth_failed → 置 realtimeAuthFailed（停后续定时刷新，展示"实时源未连接"）。
    func loadRealtimeData(symbol: String = RealtimeMerge.canarySymbol) async {
        // 默认 canary 入口 → 全量 harvest；显式其它 symbol 则至少包含该标 + canary。
        if symbol == RealtimeMerge.canarySymbol {
            await refreshRealtimeQuotes(symbols: nil)
        } else {
            await refreshRealtimeQuotes(symbols: [symbol, RealtimeMerge.canarySymbol])
        }
    }

    /// 多标的实时刷新（KTD1）：不得循环 `loadRealtimeData`（旧实现会写穿单槽）。
    /// - `symbols == nil`：priority=选中股+推荐+主题龙头+堆叠指数，再并 marketStrip
    /// - `symbols != nil`：以传入列表为 priority（页内 onAppear 聚焦）
    /// - 每标产品码 → Longbridge 码请求；**map key 仍为产品码**（HSI / 000001.SH）
    /// - 堆叠卡另拉 intraday-bars 作 live sparkline
    func refreshRealtimeQuotes(symbols: [String]? = nil) async {
        guard let bridge else { return }
        let inSession = await loadTradingHours()
        // 非交易时段：不刷 live 价，但仍刷堆叠卡会话分时（live→local，KTD4）
        if !inSession {
            realtimeQuote = nil
            await refreshRealtimeSparklines(
                displaySymbols: RealtimeMerge.symbolsFromIndexStacks(snapshot?.marketStrip?.indexStacks)
            )
            reevaluateTimer()
            return
        }
        if realtimeAuthFailed {
            stopRefreshTimer()
            return
        }

        var list: [String]
        if let symbols {
            // 页内聚焦：优先这些，仍补 canary + strip 热区（含 indexStacks）
            list = RealtimeMerge.harvestSymbols(
                strip: snapshot?.marketStrip,
                priority: symbols,
                extra: []
            )
        } else {
            var priority: [String] = []
            if let sel = selectedSymbol { priority.append(sel) }
            // 今日看盘堆叠卡：优先进 20 槽（实盘主视觉）
            priority.append(contentsOf: RealtimeMerge.symbolsFromIndexStacks(snapshot?.marketStrip?.indexStacks))
            priority.append(contentsOf: RealtimeMerge.symbolsFromRecommendations(snapshot?.recommendations ?? []))
            priority.append(contentsOf: RealtimeMerge.symbolsFromThemes(themeLeaders))
            list = RealtimeMerge.harvestSymbols(
                strip: snapshot?.marketStrip,
                priority: priority,
                extra: []
            )
        }
        if list.isEmpty {
            list = [RealtimeMerge.canarySymbol]
        } else if !list.contains(RealtimeMerge.canarySymbol) {
            list.insert(RealtimeMerge.canarySymbol, at: 0)
            if list.count > RealtimeMerge.maxSymbolsPerTick {
                list = Array(list.prefix(RealtimeMerge.maxSymbolsPerTick))
            }
        }

        var updated = realtimeQuotesBySymbol
        var anySuccess = false
        var sawAuthFailed = false

        for displaySym in list {
            guard let lbSym = RealtimeMerge.toLongbridgeSymbol(displaySym) else { continue }
            // coalesce 键用产品码，避免 HSI 与 HSI.HK 双槽
            if shouldSkipDispatch(cmd: "longbridge-quote", symbol: displaySym) {
                if updated[displaySym]?.isLive == true { anySuccess = true }
                continue
            }
            let quote = try? await Task.detached {
                try bridge.longbridgeQuote(symbol: lbSym)
            }.value
            guard let quote else { continue }
            if quote.error == "auth_failed" {
                sawAuthFailed = true
                break
            }
            if quote.isLive {
                updated[displaySym] = quote
                anySuccess = true
            }
            // 软失败：不删除 map 已有项
        }

        if sawAuthFailed {
            realtimeAuthFailed = true
            stopRefreshTimer()
            reevaluateTimer()
            return
        }

        if anySuccess {
            realtimeQuotesBySymbol = updated
            realtimeAuthFailed = false
            realtimeUpdatedAt = Date()
            if let canary = updated[RealtimeMerge.canarySymbol], canary.isLive {
                realtimeQuote = canary
            } else if let first = updated.first(where: { $0.value.isLive })?.value {
                realtimeQuote = first
            }
        }

        // 堆叠卡分时：与 quote 同 tick 尽力刷新（失败保留旧线）
        await refreshRealtimeSparklines(
            displaySymbols: RealtimeMerge.symbolsFromIndexStacks(snapshot?.marketStrip?.indexStacks)
        )
        reevaluateTimer()
    }

    /// 堆叠卡会话 sparkline：产品码 → `intraday-bars` 1m（live→local，非交易时段也跑）。
    private func refreshRealtimeSparklines(displaySymbols: [String]) async {
        guard let bridge else { return }
        guard !displaySymbols.isEmpty else { return }
        var sparks = realtimeSparklinesBySymbol
        var changed = false
        for displaySym in displaySymbols {
            let key = displaySym.uppercased()
            // 独立 coalesce 键，避免与详情 1 分线抢 30s 窗
            if shouldSkipDispatch(cmd: "intraday-bars-spark", symbol: key) {
                continue
            }
            // 请求优先 Longbridge 码；bridge 内会别名回退本地 cache
            let reqSym = RealtimeMerge.toLongbridgeSymbol(key) ?? key
            let bars = try? await Task.detached {
                try bridge.intradayBars(symbol: reqSym, interval: 1)
            }.value
            // 有 bars 就用（local 降级可能仍带 hint，但 error 为空或 bars 非空）
            guard let bars, bars.isRenderable else { continue }
            let closes = RealtimeMerge.sparklineCloses(from: bars.bars)
            if !closes.isEmpty {
                sparks[key] = closes
                // 产品码与请求码都写一份，避免 HSI / HSI.HK 键不一致
                if reqSym.uppercased() != key {
                    sparks[reqSym.uppercased()] = closes
                }
                changed = true
            }
        }
        if changed {
            realtimeSparklinesBySymbol = sparks
        }
    }

    /// 手动重试实时源（R4：avoid "未连接"状态永久滞留）。
    func retryRealtime() async {
        realtimeAuthFailed = false
        // 清 coalesce，保证重试真正打到 bridge（quote + bars）
        lastDispatchCache = lastDispatchCache.filter {
            !$0.key.hasPrefix("longbridge-quote:") && !$0.key.hasPrefix("intraday-bars:")
        }
        await refreshRealtimeQuotes(symbols: nil)
    }

    /// U2: 加载资讯雷达全量数据（bridge `intel-radar` 命令 → 12 赛道 RSS）。
    func loadIntel() async {
        await loadIntelRadar(force: false)
    }

    /// 强制刷新资讯雷达（实时抓 RSS，≈20-40s）。
    func refreshIntelRadar() async {
        await loadIntelRadar(force: true)
    }

    /// 统一入口：force=false 读缓存，force=true 实时抓取。
    private func loadIntelRadar(force: Bool) async {
        guard let bridge else { return }
        isLoadingIntel = true
        defer { isLoadingIntel = false }
        do {
            let digest = try await Task.detached {
                try bridge.intelRadar(force: force)
            }.value
            intelDigest = digest
            if force {
                kickIntelRewriteWorker()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Fire-and-forget Top-K rewrite worker after refresh (R8).
    func kickIntelRewriteWorker() {
        guard let bridge else { return }
        intelRewriteRunTask?.cancel()
        intelRewriteRunTask = Task { [weak self] in
            guard let self else { return }
            _ = try? await Task.detached {
                try bridge.intelRewriteRun()
            }.value
            // AE4: refresh digests so pool mode can flip without manual 提炼
            // Only re-query tracks that already have a card or pool may be rich — avoid 12 LLM burns.
            if let tracks = self.intelDigest?.tracks {
                for track in tracks {
                    let hasCard = self.intelDigests[track.key] != nil
                    let items = track.items ?? []
                    guard !items.isEmpty else { continue }
                    // Always try pool path via bridge (cheap when pool insufficient → list cache)
                    await self.summarizeIntelTrack(
                        track.key, name: track.name, items: items, force: false
                    )
                    if !hasCard {
                        // first auto fill only; still OK if pool insufficient returns list/skip
                        continue
                    }
                }
            }
        }
    }

    /// Select list item and load body + rewrite into detail panel.
    func selectIntelItem(_ item: IntelItem?, trackKey: String, trackName: String) {
        intelDetailTask?.cancel()
        guard let item else {
            selectedIntelItemID = nil
            isLoadingIntelDetail = false
            return
        }
        selectedIntelItemID = item.id
        isLoadingIntelDetail = true
        intelDetailTask = Task { [weak self] in
            guard let self else { return }
            defer { self.isLoadingIntelDetail = false }
            guard let bridge = self.bridge else { return }

            // 并行：正文 + 中文改写 + 投研改写（缓存命中快）
            async let articleTask: IntelArticleResponse? = {
                if let url = item.url, !url.isEmpty {
                    return try? await Task.detached {
                        try bridge.intelArticle(url: url, summary: item.summary ?? "")
                    }.value
                }
                return nil
            }()
            async let chineseTask: IntelRewriteResponse? = {
                try? await Task.detached {
                    try bridge.intelRewrite(
                        trackKey: trackKey, trackName: trackName, item: item,
                        force: false, kind: "chinese"
                    )
                }.value
            }()
            async let investTask: IntelRewriteResponse? = {
                try? await Task.detached {
                    try bridge.intelRewrite(
                        trackKey: trackKey, trackName: trackName, item: item,
                        force: false, kind: "investment"
                    )
                }.value
            }()

            let (article, chinese, invest) = await (articleTask, chineseTask, investTask)
            if let article {
                self.intelArticleByID[item.id] = article
            } else if let body = chinese?.bodyText ?? invest?.bodyText, !body.isEmpty {
                self.intelArticleByID[item.id] = IntelArticleResponse(
                    body: body, title: item.title,
                    mode: chinese?.bodyMode ?? invest?.bodyMode ?? "summary",
                    error: nil,
                    charCount: chinese?.bodyCharCount ?? invest?.bodyCharCount,
                    url: item.url
                )
            }
            if let chinese { self.setRewrite(chinese, itemID: item.id, kind: "chinese") }
            if let invest { self.setRewrite(invest, itemID: item.id, kind: "investment") }
        }
    }

    /// On-demand rewrite for selected item. kind: investment | chinese
    func requestIntelRewrite(
        item: IntelItem,
        trackKey: String,
        trackName: String,
        force: Bool = true,
        kind: String = "investment"
    ) async {
        guard let bridge else { return }
        setRewrite(
            IntelRewriteResponse(
                itemId: nil, trackKey: trackKey, kind: kind, status: "generating",
                text: nil, sections: nil, model: nil, generatedAt: nil,
                bodyText: intelArticleByID[item.id]?.body,
                bodyMode: intelArticleByID[item.id]?.mode,
                bodyCharCount: intelArticleByID[item.id]?.charCount,
                error: nil, errorType: nil, fromCache: nil
            ),
            itemID: item.id,
            kind: kind
        )
        do {
            let resp = try await Task.detached {
                try bridge.intelRewrite(
                    trackKey: trackKey, trackName: trackName, item: item,
                    force: force, kind: kind
                )
            }.value
            setRewrite(resp, itemID: item.id, kind: kind)
            if let body = resp.bodyText, !body.isEmpty {
                intelArticleByID[item.id] = IntelArticleResponse(
                    body: body, title: item.title, mode: resp.bodyMode ?? "summary",
                    error: nil, charCount: resp.bodyCharCount, url: item.url
                )
            }
        } catch {
            setRewrite(
                IntelRewriteResponse(
                    itemId: nil, trackKey: trackKey, kind: kind, status: "failed",
                    text: nil, sections: nil, model: nil, generatedAt: nil,
                    bodyText: nil, bodyMode: nil, bodyCharCount: nil,
                    error: error.localizedDescription, errorType: "client", fromCache: nil
                ),
                itemID: item.id,
                kind: kind
            )
        }
    }

    // MARK: AI digest（plan 2026-07-09-001）

    /// 单赛道 AI 要点提炼（池优先，bridge 侧 KTD5）。
    func summarizeIntelTrack(
        _ key: String,
        name: String,
        items: [IntelItem],
        force: Bool = false
    ) async {
        guard let bridge else { return }
        // 世代号：并发/重入时只采纳最新一次；勿用空 text 占位抹掉已展示正文。
        let epoch = (intelDigestEpoch[key] ?? 0) + 1
        intelDigestEpoch[key] = epoch
        // Set 原地 mutate 可能不触发 @Published；整集合赋值。
        var loading = intelDigestLoadingKeys
        loading.insert(key)
        intelDigestLoadingKeys = loading
        let capped = Array(items.prefix(25))
        do {
            let resp = try await Task.detached {
                try bridge.intelDigest(trackKey: key, trackName: name, items: capped, force: force)
            }.value
            guard intelDigestEpoch[key] == epoch else { return }
            var next = intelDigests
            next[key] = resp
            intelDigests = next
            var done = intelDigestLoadingKeys
            done.remove(key)
            intelDigestLoadingKeys = done
        } catch {
            guard intelDigestEpoch[key] == epoch else { return }
            var failResp = IntelDigestResponse(
                text: intelDigests[key]?.text ?? "",
                model: nil, generatedAt: nil, prompt: nil,
                itemCount: nil, error: error.localizedDescription, errorType: "client",
                fromCache: nil, cachedPath: nil, skipped: nil, mode: "list"
            )
            failResp.error = error.localizedDescription
            failResp.errorType = "client"
            var next = intelDigests
            next[key] = failResp
            intelDigests = next
            var done = intelDigestLoadingKeys
            done.remove(key)
            intelDigestLoadingKeys = done
            errorMessage = error.localizedDescription
        }
    }

    /// 把当前 digest 写入沉淀库。返回成功后的 savedPath。
    func saveIntelDigestToNotes(
        trackKey: String,
        trackName: String,
        prompt: String,
        response: String,
        model: String,
        items: [IntelItem]
    ) async -> String? {
        guard let bridge else { return nil }
        do {
            let resp = try await Task.detached {
                try bridge.intelDigestSave(
                    trackKey: trackKey, trackName: trackName,
                    prompt: prompt, response: response, model: model,
                    items: items,
                )
            }.value
            if resp.ok, let path = resp.savedPath {
                // 标记 saved 状态（整表赋值，确保 @Published 触发）
                if var current = intelDigests[trackKey] {
                    current.cachedPath = path
                    current.fromCache = true
                    var next = intelDigests
                    next[trackKey] = current
                    intelDigests = next
                }
                return path
            }
            errorMessage = resp.error ?? "沉淀失败"
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    /// 一键提炼全部要点（串行 + 进度 + 取消支持）。
    func summarizeAllIntelTracks(force: Bool = false) async {
        guard let tracks = intelDigest?.tracks else { return }
        let targets = tracks.filter { ($0.items?.isEmpty ?? true) == false }
        guard !targets.isEmpty else { return }

        // 重置状态
        bulkDigest = BulkDigestState()
        bulkDigest.running = true
        bulkDigest.total = targets.count

        let task = Task<Void, Never> { [weak self] in
            guard let self else { return }
            for track in targets {
                if Task.isCancelled { break }
                await self.summarizeIntelTrack(
                    track.key, name: track.name, items: track.items ?? [], force: force
                )
                await MainActor.run {
                    self.bulkDigest.done += 1
                    if let resp = self.intelDigests[track.key], resp.error != nil {
                        self.bulkDigest.failedCount += 1
                    }
                }
            }
            await MainActor.run {
                self.bulkDigest.running = false
                self.bulkDigest.currentTask = nil
                self.bulkDigest.summaryShownUntil = Date().addingTimeInterval(10)
                // 强制左栏卡片重建：整表再赋值一次（防 Set/Dict 发布漏触发）
                self.intelDigests = self.intelDigests
                self.intelDigestLoadingKeys = []
            }
            // 同批：12 赛道全景 LLM
            await self.generateIntelPanorama()
        }
        bulkDigest.currentTask = task
        await task.value
    }

    /// 用当前雷达各赛道头条采样生成全景热点。
    func generateIntelPanorama() async {
        guard let bridge else { return }
        guard let tracks = intelDigest?.tracks, !tracks.isEmpty else { return }
        intelPanoramaLoading = true
        defer { intelPanoramaLoading = false }
        let inputs: [IntelPanoramaTrackInput] = tracks.map { t in
            let titles = (t.items ?? []).prefix(4).map(\.title)
            return IntelPanoramaTrackInput(key: t.key, name: t.name, titles: Array(titles))
        }
        do {
            let resp = try await Task.detached {
                try bridge.intelPanorama(tracks: inputs)
            }.value
            intelPanorama = resp
        } catch {
            intelPanorama = IntelPanoramaResponse(
                text: "", model: nil, generatedAt: nil, trackCount: nil,
                error: error.localizedDescription, errorType: "client"
            )
        }
    }

    /// 取消正在运行的 bulk digest 任务（当前 LLM 调用会跑完，下个 track 不开始）。
    func cancelBulkDigest() {
        bulkDigest.currentTask?.cancel()
    }

    /// 重试 bulk 中失败的 track。
    func retryFailedBulkDigests() async {
        let failedKeys: [String] = intelDigests.compactMap { (key, resp) in
            if resp.error != nil { return key } else { return nil }
        }
        guard let tracks = intelDigest?.tracks, !failedKeys.isEmpty else { return }
        let byKey = Dictionary(uniqueKeysWithValues: tracks.map { ($0.key, $0) })

        bulkDigest = BulkDigestState()
        bulkDigest.running = true
        bulkDigest.total = failedKeys.count

        let task = Task<Void, Never> { [weak self] in
            guard let self else { return }
            for key in failedKeys {
                if Task.isCancelled { break }
                guard let track = byKey[key], let items = track.items, !items.isEmpty else { continue }
                await self.summarizeIntelTrack(key, name: track.name, items: items, force: true)
                await MainActor.run {
                    self.bulkDigest.done += 1
                    if let resp = self.intelDigests[key], resp.error != nil {
                        self.bulkDigest.failedCount += 1
                    }
                }
            }
            await MainActor.run {
                self.bulkDigest.running = false
                self.bulkDigest.currentTask = nil
                self.bulkDigest.summaryShownUntil = Date().addingTimeInterval(10)
                self.intelDigests = self.intelDigests
                self.intelDigestLoadingKeys = []
            }
        }
        bulkDigest.currentTask = task
        await task.value
    }

    /// 启动时检测 OpenAI/DeepSeek Keychain 凭据是否存在。
    func refreshLLMCredentialsStatus() {
        let env = KeychainStore.injectedEnvironment()
        hasLLMCredentials = (env["OPENAI_API_KEY"]?.isEmpty == false)
            || (env["DEEPSEEK_API_KEY"]?.isEmpty == false)
    }

    /// 启动/手动自检（plan 2026-07-12-005 / U8）。bridge 不可达（sidecar 起不来）本身
    /// 就是一项 fail——KTD4 明示由 Swift 侧兜底合成，self-check 命令自己跑不起来时
    /// 无法自证，只能靠调用方判断"够不到"这件事本身。
    func runSelfCheck() async {
        isRunningSelfCheck = true
        defer { isRunningSelfCheck = false }
        guard let bridge else {
            selfCheckItems = [SelfCheckItem(
                item: "sidecar", status: "fail",
                detail: "找不到项目根目录，后台服务未能启动",
                fixHint: "检查安装完整性", fixAction: nil
            )]
            selfCheckBannerDismissed = false
            return
        }
        let resp = try? await Task.detached { try bridge.selfCheck() }.value
        guard let resp else {
            selfCheckItems = [SelfCheckItem(
                item: "sidecar", status: "fail",
                detail: "后台服务无响应",
                fixHint: "重新初始化运行时", fixAction: "reinit_runtime"
            )]
            selfCheckBannerDismissed = false
            return
        }
        selfCheckItems = resp.items
        selfCheckGeneratedAt = resp.generatedAt
        selfCheckBannerDismissed = false   // 新一轮结果，重新允许横幅（若仍有 fail）
    }

    /// 横幅/设置页"关闭"——仅当前会话生效，重跑自检会重置。
    func dismissSelfCheckBanner() {
        selfCheckBannerDismissed = true
    }

    /// 自检 fixAction=reinit_runtime 的落地动作（U8）：强删旧 venv 重跑 uv sync，
    /// 修复"解释器文件在但已损坏"的场景；完成后重启 sidecar 并重跑自检验证是否恢复。
    @Published var isReinitializingRuntime = false

    func reinitializeRuntime() async {
        guard let bridge else { return }
        isReinitializingRuntime = true
        defer { isReinitializingRuntime = false }
        do {
            try await Task.detached {
                try BridgeClient.reinitializeRuntime(projectRoot: bridge.projectRoot, stateRoot: bridge.stateRoot)
            }.value
            BridgeClient.restartSidecarForEnvChange()
        } catch {
            errorMessage = "重新初始化运行时失败：\(error.localizedDescription)"
        }
        await runSelfCheck()
    }

    /// U3: 加载 Dashboard 资讯摘要（轻量，仅取赛道计数 + 最近标题）。
    func loadIntelSummary() async {
        guard let bridge else { return }
        _ = try? await Task.detached { try bridge.newsDigest() }.value
        // 摘要从全量数据中提取轻量字段。
    }

    /// U4: Seesaw 预温实时上下文（R3）——首轮 get_orientation 并行拉取快照，
    /// 为 LLM 提供"今日盘面"索引数据。
    func preheatRealtimeContext() async {
        // 与 Dashboard 同源：走 multi-symbol 管线（session 门控在 refresh 内）
        await refreshRealtimeQuotes(symbols: [RealtimeMerge.canarySymbol])
    }

    /// U4: 将 intraday-snapshot 工具返回的 bar 数据存入 chat attachment（R8）。
    /// Seesaw loop 的 tool_done 帧检测到 intraday bar 数据后调用本方法。
    func attachChartToLastMessage(bars: ChartAttachment) {
        guard let idx = chatMessages.lastIndex(where: { $0.role == .assistant }) else { return }
        chatMessages[idx].chartAttachment = bars
    }

    // MARK: U5 Timer 生命周期（R9/R10/R13/R14）

    /// Caller passes whether the scene/window is active (R14 gate).
    func updateSceneActive(_ active: Bool) {
        scenePhaseActive = active
        reevaluateTimer()
    }

    /// 交易时段门控更新后重新评估 Timer（trading-hours 查询与 loadTradingHours 异步）。
    /// auth_failed 时停表，直到 retryRealtime 成功。
    func reevaluateTimer() {
        guard scenePhaseActive,
              let hours = tradingHours, hours.isTradingSession,
              !realtimeAuthFailed else {
            stopRefreshTimer()
            return
        }
        startRefreshTimer()
    }

    func startRefreshTimer(intervalSeconds: Double = 120) {
        stopRefreshTimer()
        let effectiveInterval = max(intervalSeconds, Self.minIntervalSeconds)
        timerCancellable = Timer.publish(every: effectiveInterval, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in self?.onRefreshTick() }
    }

    func stopRefreshTimer() {
        timerCancellable?.cancel()
        timerCancellable = nil
    }

    private func onRefreshTick() {
        guard scenePhaseActive,
              tradingHours?.isTradingSession ?? false,
              !realtimeAuthFailed else { return }
        refreshTimestamp = Date()
        // KTD1: tick 真拉 Longbridge，不是只改 timestamp 的 no-op
        Task { await refreshRealtimeQuotes(symbols: nil) }
    }

    /// 检查 coalescing cache（R14）：同 command:symbol 30s 内跳过。
    func shouldSkipDispatch(cmd: String, symbol: String) -> Bool {
        let key = "\(cmd):\(symbol)"
        let now = Date()
        if let last = lastDispatchCache[key], now.timeIntervalSince(last) < Self.coalesceSeconds {
            return true
        }
        lastDispatchCache[key] = now
        return false
    }

    /// 紫苏叶个股富化（机构/PE/美股对标）。非紫苏叶票静默置空，不报错。
    func loadPerillaEnrichment(symbol: String) async {
        guard let bridge else { return }
        isLoadingPerilla = true
        defer { isLoadingPerilla = false }
        let result = try? await Task.detached {
            try bridge.perillaEnrichment(symbol: symbol)
        }.value
        // 仅当仍停留在同一票时落数据（防快速切票串台）。
        guard selectedSymbol == symbol else { return }
        if let result, result.status == "ok" {
            perillaEnrichment = result
        } else {
            perillaEnrichment = nil
        }
    }

    /// 点击任意页面出现的股票：在池→直接看；不在池→先导入（拉日线进池）再看。
    /// symbol 可为完整 ts_code（如 688114.SH）或裸 6 位码（如 603407）。
    func selectStock(_ symbol: String, navigate: Bool = true) async {
        if let s = poolStock(matching: symbol) {
            if navigate { selectedSection = .stocks }
            await loadStock(symbol: s.symbol)
            return
        }
        // 不在池 → 导入（resolve + fetch_stock_data 拉日线 + reload snapshot）
        importingSymbol = symbol
        defer { importingSymbol = nil }
        _ = await importStocks([symbol])
        if let s = poolStock(matching: symbol) {
            if navigate { selectedSection = .stocks }
            await loadStock(symbol: s.symbol)
        } else {
            errorMessage = "无法导入「\(symbol)」：未能解析或拉取该股票数据（需正式 Python 环境 + 可识别的代码）。"
        }
    }

    /// 在当前快照股票池里按 ts_code 或裸 6 位码匹配。
    private func poolStock(matching symbol: String) -> StockSummary? {
        let bare = String(symbol.split(separator: ".").first ?? Substring(symbol))
        return snapshot?.stocks.first { s in
            s.symbol == symbol || String(s.symbol.split(separator: ".").first ?? "") == bare
        }
    }

    func loadReport(path: String) async {
        guard let bridge else { return }
        selectedReportPath = path
        isLoadingReport = true
        do {
            let detail = try await Task.detached {
                try bridge.report(path: path)
            }.value
            self.reportDetail = detail
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoadingReport = false
    }

    /// 用 MarkEdit 打开当前选中报告（外部编辑桥）。仅打开文件——不改内容、不重载快照、不入 Python 桥。
    /// 路径校验复用 `ExternalReportOpener`（安全边界，见该文件 source-of-truth 注释）。
    func openReportInMarkEdit(path: String) {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        let stateRoot = bridge.stateRoot
        errorMessage = nil
        // open() 非阻塞返回（resolveReportURL 仅本地 stat，NSWorkspace.open 异步），不必 detach。
        ExternalReportOpener.open(relativePath: path, under: stateRoot) { [weak self] err in
            // 完成回调可能在主线程外触发——防御性 hop 回 MainActor 改 @Published（已在主线程亦安全）。
            Task { @MainActor in
                if let err { self?.errorMessage = err.errorDescription }
            }
        }
    }

    func runTask(_ task: KSSTask) async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        runningTasks += 1
        activeFormalTaskId = task.rawValue
        errorMessage = nil
        defer {
            if activeFormalTaskId == task.rawValue { activeFormalTaskId = nil }
            runningTasks -= 1
        }
        do {
            let result = try await Task.detached {
                try bridge.runTask(task)
            }.value
            taskResults.insert(result, at: 0)
            if result.status == "failed" {
                // R6：自选一键日更与 Runbook 共用此通道；失败须可感知（对齐 generateReview）。
                let summary = result.summary.trimmingCharacters(in: .whitespacesAndNewlines)
                errorMessage = summary.isEmpty
                    ? "任务失败：\(task.title)"
                    : "任务失败：\(task.title) — \(summary)"
            } else {
                await loadSnapshot()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// U5: 加自选即时生成该股复盘，完成后刷新 snapshot 使个股复盘列表即时纳入。
    /// 失败仅置横幅、不抛——watchlist 已由 ContentView 持久化，复盘缺失不影响自选。
    func generateReview(for symbol: String) async {
        guard let bridge else { return }
        runningTasks += 1
        errorMessage = nil
        do {
            let result = try await Task.detached {
                try bridge.runDailyReviewSymbol(symbol)
            }.value
            taskResults.insert(result, at: 0)
            if result.status == "failed" {
                errorMessage = "生成 \(symbol) 复盘失败：\(result.summary)"
            } else {
                await loadSnapshot()
            }
        } catch {
            errorMessage = "生成 \(symbol) 复盘失败：\(error.localizedDescription)"
        }
        runningTasks -= 1
    }

    func loadSectorRotation(date: String? = nil) async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        isLoadingSectorRotation = true
        errorMessage = nil
        do {
            let detail = try await Task.detached {
                try bridge.sectorRotation(date: date)
            }.value
            self.sectorRotationDetail = detail
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoadingSectorRotation = false
    }

    /// 舆情热点 digest：无参（空串）= 最新档；指定 date/scene 拉某档。
    func loadNewsDigest(date: String? = nil, scene: String? = nil) async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        isLoadingNewsDigest = true
        errorMessage = nil
        do {
            let resp = try await Task.detached {
                try bridge.newsDigest(date: date, scene: scene)
            }.value
            self.newsDigest = resp
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoadingNewsDigest = false
    }

    /// 解析自由文本（名称/代码/OCR 结果）为 ts_code。
    func resolveStocks(_ text: String) async -> [ResolvedStock] {
        guard let bridge else { return [] }
        return (try? await Task.detached { try bridge.resolveStocks(text) }.value) ?? []
    }

    /// 导入并同步：拉取这些代码的日线，完成后刷新快照（新股进入股票池）。
    @discardableResult
    func importStocks(_ codes: [String]) async -> TaskRunResult? {
        guard let bridge, !codes.isEmpty else { return nil }
        runningTasks += 1
        errorMessage = nil
        defer { runningTasks -= 1 }
        do {
            let result = try await Task.detached { try bridge.importStocks(codes) }.value
            taskResults.insert(result, at: 0)
            if result.status != "failed" {
                await loadSnapshot()
            }
            return result
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    // MARK: 定时任务（launchd）

    /// 拉取 launchd 任务清单（调度 / 状态 / 上次运行）。
    func loadScheduledJobs() async {
        guard let bridge else { return }
        let resp = try? await Task.detached { try bridge.cronList() }.value
        self.scheduledJobs = resp?.jobs ?? []
        if let order = resp?.categoryOrder, !order.isEmpty { self.cronCategoryOrder = order }
    }

    /// 趋势页：加载某月月度格子（YYYY-MM）。
    func loadTrendsMonth(_ month: String) async {
        guard let bridge else { return }
        trendsLoading = true
        defer { trendsLoading = false }
        let m = (try? await Task.detached { try bridge.trendsMonth(month) }.value)
        self.trendMonth = m
        // 默认选中本月最后一个有数据日，方便首屏看到明细。
        if selectedTrendDate == nil, let last = m?.days.last(where: { $0.hasData })?.date {
            await loadTrendsDay(last)
        }
    }

    /// 趋势页：加载某日明细并记为选中日。
    func loadTrendsDay(_ date: String) async {
        guard let bridge else { return }
        selectedTrendDate = date
        let d = (try? await Task.detached { try bridge.trendsDay(date) }.value)
        self.trendDayDetail = d
    }

    /// 拉取十五五科技主题 → 板块龙头/第二梯队。
    func loadThemeLeaders() async {
        guard let bridge else { return }
        let themes = (try? await Task.detached { try bridge.themeLeaders() }.value) ?? []
        self.themeLeaders = themes
    }

    /// 一键重跑某任务，就地刷新该行状态。
    func rerunScheduledJob(_ label: String) async {
        await runScheduledAction(label) { bridge in try bridge.rerunJob(label) }
    }

    /// 启用/停用某任务，就地刷新该行状态。
    func toggleScheduledJob(_ label: String, enabled: Bool) async {
        await runScheduledAction(label) { bridge in try bridge.setJobEnabled(label, enabled: enabled) }
    }

    /// 应用内编辑任务排期（设置页任务分区，plan 2026-07-12-005 / U6），就地刷新该行状态。
    func editScheduledJob(_ label: String, suffix: String, scheduleJSON: String) async {
        await runScheduledAction(label) { bridge in
            try bridge.editCronSchedule(suffix: suffix, scheduleJSON: scheduleJSON)
        }
    }

    /// 自选列表同步给 Python 读者（plan 2026-07-12-005 / U15）。UI 真源仍是
    /// @AppStorage("watchlistSymbols")，这里只是把它镜像进 kss.db 供 cron/bridge 读——
    /// 同原先的 syncWatchlistFile 定位，静默失败不影响 UI（自选已经落地在 AppStorage）。
    func syncWatchlistToDB(_ symbols: [String]) async {
        guard let bridge else { return }
        _ = try? await Task.detached { try bridge.setWatchlist(symbols) }.value
    }

    /// 漏跑任务（关机自检命中的）。
    var staleJobs: [ScheduledJob] { scheduledJobs.filter { $0.stale } }

    /// 一键补跑所有漏跑任务（关机自检）。
    func catchUpStaleJobs() async {
        await runScheduledBatch { bridge in try bridge.catchUpJobs() }
    }

    /// 批量重跑指定 label（某分类「全部重跑」/「全部重跑」）。
    func rerunScheduledJobs(_ labels: [String]) async {
        await runScheduledBatch { bridge in try bridge.rerunJobs(labels) }
    }

    /// 同步 LaunchAgent 到清单声明（用于 needsInstall 任务修复）。
    /// 同步成功后刷新任务清单并保留可见状态提示。
    func syncScheduledJobs(_ label: String) async {
        guard let bridge else { return }
        scheduledBusy.insert(label)
        defer { scheduledBusy.remove(label) }
        do {
            let result = try await Task.detached { try bridge.syncCronJobs() }.value
            if !result.ok {
                errorMessage = result.error ?? "定时任务同步失败"
                return
            }

            if let jobs = result.jobs {
                scheduledJobs = jobs
            } else {
                try? await Task.sleep(nanoseconds: 300_000_000)
                await loadScheduledJobs()
            }

            if let order = result.categoryOrder, !order.isEmpty {
                cronCategoryOrder = order
            }
            scheduledBatchNote = Self.formatCronSyncNote(result)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func runScheduledBatch(_ action: @escaping (BridgeClient) throws -> CronBatchResult) async {
        guard let bridge, !scheduledBatchBusy else { return }
        scheduledBatchBusy = true
        defer { scheduledBatchBusy = false }
        do {
            let result = try await Task.detached { try action(bridge) }.value
            if result.count == 0 {
                scheduledBatchNote = "没有需要触发的任务"
            } else {
                let failed = result.ran.filter { !$0.ok }
                scheduledBatchNote = failed.isEmpty
                    ? "已触发 \(result.count) 个任务"
                    : "触发 \(result.count) 个，\(failed.count) 个失败：\(failed.map(\.title).joined(separator: "、"))"
            }
            // launchctl kickstart 后状态有延迟，稍等再刷新行状态。
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            await loadScheduledJobs()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private static func formatCronSyncNote(_ response: CronSyncResponse) -> String {
        if let notices = response.notices, !notices.isEmpty {
            return notices.joined(separator: "；")
        }

        if let plan = response.plan {
            let parts = [
                "install:\(plan.install.count)",
                "update:\(plan.update.count)",
                "stale:\(plan.stale.count)",
                "aligned:\(plan.aligned.count)",
            ]
            return "LaunchAgent 对账完成（\(parts.joined(separator: ", "))）"
        }

        return "LaunchAgent 已同步"
    }

    private func runScheduledAction(_ label: String, _ action: @escaping (BridgeClient) throws -> CronActionResult) async {
        guard let bridge else { return }
        scheduledBusy.insert(label)
        defer { scheduledBusy.remove(label) }
        do {
            let result = try await Task.detached { try action(bridge) }.value
            if let job = result.job, let idx = scheduledJobs.firstIndex(where: { $0.label == label }) {
                scheduledJobs[idx] = job
            }
            if !result.ok {
                errorMessage = result.error ?? "定时任务操作失败"
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func mergeTaskResults(current: [TaskRunResult], persisted: [TaskRunResult]) -> [TaskRunResult] {
        var seen = Set<String>()
        var merged: [TaskRunResult] = []
        for result in current + persisted {
            guard !seen.contains(result.id) else { continue }
            seen.insert(result.id)
            merged.append(result)
        }
        return Array(merged.sorted { $0.startedAt > $1.startedAt }.prefix(25))
    }
}

/// confirm 闸:后台流式线程同步 wait,主线程 tap 后 resolve(人在环内写闸,U5)。
/// 非 MainActor —— 跨线程握手靠 DispatchSemaphore,本身线程安全。
final class ChatConfirmGate: @unchecked Sendable {
    private let sem = DispatchSemaphore(value: 0)
    private var approved = false
    private var resolved = false
    private let lock = NSLock()

    /// 后台线程调:阻塞至 resolve,返回是否批准。
    func wait() -> Bool {
        sem.wait()
        lock.lock(); defer { lock.unlock() }
        return approved
    }

    /// 主线程调:tap 确认/拒绝。幂等(重复 resolve 忽略)。
    func resolve(_ ok: Bool) {
        lock.lock()
        if resolved { lock.unlock(); return }
        resolved = true
        approved = ok
        lock.unlock()
        sem.signal()
    }
}
