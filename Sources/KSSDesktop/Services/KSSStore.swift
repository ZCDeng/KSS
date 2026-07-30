import Foundation
import Combine
import AppKit
import PDFKit

@MainActor
final class KSSStore: ObservableObject {
    @Published var snapshot: AppSnapshot?
    /// Seesaw is the post-launch home workspace. Data pages remain available on
    /// demand and must not block the first usable interaction on a snapshot.
    @Published var selectedSection: WorkspaceSection = .aiChat
    /// 设置页深链目标 tab（R2-U4 KTD3）：进设置页时消费一次即清空，默认落密钥 tab。
    @Published var settingsTargetTab: SettingsTab?
    /// xcom 设置左栏分类深链（plan 2026-07-23-003）：优先于 tab；消费一次即清空。
    @Published var settingsTargetCategory: SettingsCategory?
    /// Cross-workspace deep link used by self-checks and inline Seesaw recovery
    /// actions. This is deliberately separate from Settings routing so model
    /// credentials never reappear as a global Settings category.
    @Published var seesawDestination: SeesawDestination?

    /// 打开设置并落到具体分类（同时投影经典 tab，调用点统一走这里）。
    func openSettings(category: SettingsCategory) {
        settingsTargetCategory = category
        settingsTargetTab = category.tab
        selectedSection = .settings
    }

    /// 仅有旧 tab 深链时，供设置页解析默认 Category。
    func consumeSettingsDestination() -> SettingsCategory? {
        if let cat = settingsTargetCategory {
            settingsTargetCategory = nil
            settingsTargetTab = nil
            return cat
        }
        if let tab = settingsTargetTab {
            settingsTargetTab = nil
            return tab.defaultCategory
        }
        return nil
    }

    func openSeesawModels() {
        seesawDestination = .models
        selectedSection = .aiChat
    }

    func consumeSeesawDestination() -> SeesawDestination? {
        defer { seesawDestination = nil }
        return seesawDestination
    }
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
    /// kind → itemId → response（investment / translation；chinese 数据保留无入口）
    @Published var intelRewriteByKind: [String: [String: IntelRewriteResponse]] = [:]
    @Published var isLoadingIntelDetail = false
    private var intelDetailTask: Task<Void, Never>?
    private var intelRewriteRunTask: Task<Void, Never>?
    /// 会话内已预热赛道（plan 2026-07-22-001 U5：切赛道触发一次 track 级 Top-K）
    private var prewarmedIntelTracks: Set<String> = []

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
    @Published var realtimeQuotesBySymbol: [String: LongbridgeQuote] = [:]  // 多标的 map，供盯盘 overlay
    /// R6 R6：watchlist 镜像（真源 ContentView @AppStorage，经 syncWatchlistToDB 同步）——
    /// 进 refreshRealtimeQuotes 的 priority 采集，使自选列表盘中有实时 quote。
    @Published var watchlistSymbols: [String] = []
    /// 堆叠卡会话分时（R2-U7 KTD7）：产品码 → 含昨收锚点/单调最大偏离/会话日的结构体，
    /// 供 Y 轴范围计算脱离"当前已加载了多少个 bar"。
    @Published var realtimeSparklinesBySymbol: [String: SparklineSeries] = [:]
    @Published var tradingHours: TradingHours?         // 交易时段门控（R13）
    @Published var realtimeAuthFailed = false          // auth_failed → 停定时刷新 + "实时源未连接"（R4）
    @Published var realtimeUpdatedAt: Date?            // 最近一次实时拉取成功时间（"更新于 HH:MM"）
    /// 按标的记录的本地接收时间，供 RealtimeFreshness 在 sourceAsofTs 缺失时按标的独立回退——
    /// 不可复用 realtimeUpdatedAt（全局），否则单标的新鲜度会被其他标的的刷新成功掩盖。
    @Published var realtimeReceivedAtBySymbol: [String: Date] = [:]

    // MARK: 隔夜美股行情——独立于 ChinaConnect 的状态、覆盖与定时器
    @Published var usMarketQuotesByCode: [String: USMarketQuote] = [:]
    @Published var usMarketPhase: String?
    @Published var usMarketUpdatedAt: Date?
    @Published var usMarketCoverage: USMarketCoverage?
    @Published var usMarketLastError: String?

    nonisolated static let usMarketCodes = [
        "MCHI", "IXIC", "DJI", "XIN9", "ROBO", "BOTZ",
        "NVDA", "SOXX", "SMH", "TSLA", "MU", "AVGO",
    ]
    private var usMarketTimerCancellable: AnyCancellable?
    nonisolated private static let usMarketRefreshIntervalSeconds: Double = 60

    // MARK: U5 Timer 基础设施（R9/R10/R13/R14）
    @Published var refreshTimestamp: Date?             // 定时刷新 tick（紫苏叶/国产替代 Section 监听此值触发重算）
    private var timerCancellable: AnyCancellable?
    private var scenePhaseActive = false
    private var lastDispatchCache: [String: Date] = [:]  // R14: coalescing cache (cmd:symbol → last dispatch)
    private static let minIntervalSeconds: Double = 120  // R14: 最小间隔 2min
    private static let coalesceSeconds: Double = 30     // R14: 同标的+同命令 30s 内复用
    /// 产品：交易时段 2 分钟真刷新（非 MainActor 默认参数安全的字面量）
    nonisolated private static let refreshIntervalSeconds: Double = 120
    // R2-U6：盘后分时缩略图独立 timer（KTD6）——与 quote timer 分开管理，不受 authFailed 影响。
    private var sparklineTimerCancellable: AnyCancellable?
    nonisolated private static let sparklineIntervalSeconds: Double = 300

    // MARK: AI 复盘助手聊天态（#4 U4/U5）—— 会话历史归 store，section 切换不丢
    @Published var chatMessages: [ChatMessage] = []
    @Published var isChatStreaming = false
    @Published var chatToolInProgress: String?            // 正在调用的工具名（进度指示）
    @Published var pendingWriteConfirm: PendingWriteConfirm?   // 待人工确认的写（app-modal）
    /// 从盯盘组件旁 AI 钮预填 Seesaw 输入（region 上下文）；AIChatView 消费后清空。
    @Published var chatComposerPrefill: String?
    @Published var agentSessions: [AgentSession] = []
    @Published var selectedAgentSessionId: String?
    @Published var agentSkills: [AgentSkill] = []
    @Published var agentSkillDiagnostics: [AgentSkillDiagnostic] = []
    @Published var pinnedAgentSkillIds: Set<String> = []
    @Published var agentMemories: [AgentMemoryRecord] = []
    @Published var agentMemoryCandidates: [AgentMemoryCandidate] = []
    @Published var agentSourceRecalls: [AgentSourceRecall] = []
    /// Last explicitly requested Longbridge context. This is presentation
    /// metadata only; the immutable full payload stays in the Agent JSONL
    /// transcript/evidence ledger.
    @Published var agentLiveMarketContexts: [AgentLiveMarketContext] = []
    @Published var agentContextUsage: AgentContextUsage?
    @Published var agentModel: String?
    @Published var agentProvider: String?
    @Published var agentProviders: [AgentProviderDescriptor] = []
    /// Global defaults for newly-created sessions. Do not use these as the
    /// current conversation route after a session has selected its own model.
    @Published var agentGlobalPrimaryRoute: AgentProviderRoute?
    @Published var agentPrimaryRoute: AgentProviderRoute?
    @Published var agentFallbackRoute: AgentProviderRoute?
    @Published var agentProviderStatus: String?
    @Published var agentProviderTestOK: Bool?
    @Published var agentProviderTestError: String?
    @Published var agentProviderTestHint: String?
    /// Composer 只显示用户明确启用的模型。此偏好不含密钥，和 Provider
    /// route 一样是 Seesaw 的本地非秘密状态；空集合表示尚未做过筛选，
    /// 因而保守地展示目录中的全部模型，避免升级后把 Composer 变成空列表。
    @Published private(set) var seesawVisibleModelRouteIDs: Set<String>
    @Published var agentUsage: AgentUsage?
    @Published var agentExistingRunId: String?
    @Published var agentLastEventIsError: Bool?
    @Published var agentTerminationReason: String?
    @Published var agentSequenceIssue: String?
    @Published var agentQueuedInputs: [AgentQueuedInput] = []
    @Published var agentSteeringCount = 0
    @Published var agentFollowUpCount = 0
    @Published var agentQueueAcknowledgement: AgentQueueAcknowledgement?
    @Published var agentProtocolUnavailable = false
    @Published var pendingAgentAttachments: [AgentAttachment] = []
    @Published var isImportingAgentAttachment = false
    @Published var agentAttachmentError: String?
    // MARK: Deep Research workbench
    @Published var researchGoals: [ResearchGoalSummary] = []
    @Published var investmentAnalysisReports: [InvestmentAnalysisReportSummary] = []
    @Published var selectedResearchGoalId: String?
    @Published var selectedResearchGoal: ResearchGoalDetail?
    @Published var researchProfiles: [ResearchProfileSummary] = []
    @Published var researchEventsByGoal: [String: [ResearchEvent]] = [:]
    @Published var researchSequenceIssues: [String: String] = [:]
    @Published var isLoadingResearch = false
    @Published var researchCandidate: ResearchCandidate?
    /// 当前阻塞中的 confirm 闸（后台流式线程持，UI tap 后 resolve）。
    private var activeConfirmGate: ChatConfirmGate?
    private var activeAgentControl: BridgeClient.AgentControlChannel?
    private var activeAgentRunId: String?
    private var activeAgentStreamId: UUID?
    private var userAbortedAgentRun = false
    private var agentSeenSequences: [String: Set<Int>] = [:]
    private var agentExpectedSequence: [String: Int] = [:]
    private var agentDuplicateHydrationKeys: Set<String> = []
    private var chatMessagesByAgentSession: [String: [ChatMessage]] = [:]
    private var pendingQueueClientMessageId: String?
    private var agentMessageStartCounts: [String: Int] = [:]
    private var agentCurrentAssistantMessageIds: [String: UUID] = [:]
    private var researchSeenSequences: [String: Set<Int>] = [:]
    private var researchSeenEventIds: [String: Set<String>] = [:]
    private var researchExpectedSequence: [String: Int] = [:]
    private var researchEventEpoch: [String: UUID] = [:]

    private static let seesawVisibleModelRouteIDsKey = "kss.seesaw.visibleModelRoutes.v1"

    let bridge: BridgeClient?

    init() {
        self.bridge = try? BridgeClient()
        self.seesawVisibleModelRouteIDs = Set(
            UserDefaults.standard.stringArray(forKey: Self.seesawVisibleModelRouteIDsKey) ?? []
        )
        restoreLastAgentSession()
        refreshLLMCredentialsStatus()
    }

    init(testBridge bridge: BridgeClient?) {
        self.bridge = bridge
        self.seesawVisibleModelRouteIDs = Set(
            UserDefaults.standard.stringArray(forKey: Self.seesawVisibleModelRouteIDsKey) ?? []
        )
        restoreLastAgentSession()
    }

    nonisolated static func seesawModelRouteID(providerID: String, modelID: String) -> String {
        "\(providerID)::\(modelID)"
    }

    func isSeesawModelVisible(providerID: String, modelID: String) -> Bool {
        seesawVisibleModelRouteIDs.isEmpty
            || seesawVisibleModelRouteIDs.contains(Self.seesawModelRouteID(providerID: providerID, modelID: modelID))
    }

    func setSeesawModelVisible(providerID: String, modelID: String, visible: Bool) {
        let routeID = Self.seesawModelRouteID(providerID: providerID, modelID: modelID)
        if visible {
            seesawVisibleModelRouteIDs.insert(routeID)
        } else {
            // Persist an explicit selection the first time a user hides a
            // model; keep every other discovered model visible by seeding the
            // current catalog before removing the requested route.
            if seesawVisibleModelRouteIDs.isEmpty {
                seesawVisibleModelRouteIDs = Set(agentProviders.flatMap { provider in
                    (provider.models ?? []).map {
                        Self.seesawModelRouteID(providerID: provider.id, modelID: $0.id)
                    }
                })
            }
            seesawVisibleModelRouteIDs.remove(routeID)
        }
        UserDefaults.standard.set(
            seesawVisibleModelRouteIDs.sorted(),
            forKey: Self.seesawVisibleModelRouteIDsKey
        )
    }

    func liveContextScope(for input: String) -> [String: String]? {
        Self.liveContextScope(for: input, watchlistSymbols: watchlistSymbols)
    }

    nonisolated static func liveContextScope(
        for input: String,
        watchlistSymbols: [String]
    ) -> [String: String]? {
        let normalized = input.lowercased()
        let realtimeTerms = ["实时", "盘中", "此刻", "当前", "报价", "分时", "分钟", "现价", "最新价"]
        guard realtimeTerms.contains(where: { normalized.contains($0) })
            || normalized.contains("今天大盘")
        else { return nil }
        let symbols: [String]
        let scope: String
        let explicitlyNamedSymbols = Self.marketSymbols(in: input)
        if !explicitlyNamedSymbols.isEmpty {
            scope = "symbols"
            symbols = Array(explicitlyNamedSymbols.prefix(12))
        } else if normalized.contains("自选") || normalized.contains("watchlist") {
            scope = "watchlist"
            symbols = watchlistSymbols.isEmpty
                ? Self.defaultLiveMarketSymbols()
                : Array(watchlistSymbols.prefix(12))
        } else {
            scope = "market"
            symbols = Self.defaultLiveMarketSymbols()
        }
        return [
            "scope": scope,
            "symbols": symbols.joined(separator: ","),
            "reason": "explicit_current_market_intent",
            "intent": "explain",
        ]
    }

    nonisolated private static func defaultLiveMarketSymbols() -> [String] {
        ["000001.SH", "399001.SZ", "399006.SZ", "000300.SH"]
    }

    nonisolated private static func marketSymbols(in input: String) -> [String] {
        let pattern = #"\b[0345689][0-9]{5}(?:\.(?:SH|SZ|BJ))?\b"#
        guard let expression = try? NSRegularExpression(
            pattern: pattern,
            options: [.caseInsensitive]
        ) else { return [] }
        let range = NSRange(input.startIndex..., in: input)
        return expression.matches(in: input, range: range).compactMap { match in
            Range(match.range, in: input).map { String(input[$0]).uppercased() }
        }.reduce(into: []) { result, symbol in
            if !result.contains(symbol) { result.append(symbol) }
        }
    }

    // MARK: - 聊天一轮（流式 + 人在环内写闸）

    /// 盯盘组件旁 AI 入口：预填 region，引导用 surface 工具（组件旁 NL 仍是主路径）。
    func seedSurfaceAIPrefill(region: String) {
        let r = region.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !r.isEmpty else { return }
        chatComposerPrefill = """
        region=\(r)。请用 surface_nl_interpret 解析我的自然语言绑定意图并展示真值预览；确认后再 apply_surface_patch。组件旁输入是主路径，这里是辅助。
        """
    }

    func sendChat(
        _ text: String,
        sourceQueueId: String? = nil,
        attachments: [AgentAttachment]? = nil
    ) {
        guard let bridge else { errorMessage = "Cannot locate KSS project root"; return }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let selectedAttachments = attachments ?? pendingAgentAttachments
        guard (!trimmed.isEmpty || !selectedAttachments.isEmpty), !isChatStreaming else { return }   // 单活动轮（R11）
        if !agentProtocolUnavailable {
            sendAgentChat(
                trimmed,
                sourceQueueId: sourceQueueId,
                attachments: selectedAttachments,
                bridge: bridge)
            return
        }
        guard selectedAttachments.isEmpty else {
            agentAttachmentError = "当前兼容聊天协议不支持附件，请恢复 Agent 服务后重试。"
            return
        }
        chatMessages.append(ChatMessage(role: .user, text: trimmed))
        let assistant = ChatMessage(role: .assistant, text: "", numbersUnverified: true)
        chatMessages.append(assistant)
        let assistantId = assistant.id
        startLegacyChat(bridge: bridge, assistantId: assistantId)
    }

    private func sendAgentChat(
        _ trimmed: String,
        sourceQueueId: String?,
        attachments: [AgentAttachment],
        bridge: BridgeClient
    ) {
        ensureAgentSession()
        guard let sessionId = selectedAgentSessionId else { return }
        chatMessages.append(ChatMessage(role: .user, text: trimmed, attachments: attachments))
        let assistant = ChatMessage(role: .assistant, text: "", numbersUnverified: true)
        chatMessages.append(assistant)
        pendingAgentAttachments.removeAll()
        agentAttachmentError = nil
        chatMessagesByAgentSession[sessionId] = chatMessages
        persistLastAgentSession(sessionId)
        let assistantId = assistant.id
        let streamId = UUID()
        activeAgentStreamId = streamId
        isChatStreaming = true
        userAbortedAgentRun = false
        chatToolInProgress = nil
        agentModel = nil
        agentProvider = nil
        agentUsage = nil
        agentExistingRunId = nil
        agentLastEventIsError = nil
        agentTerminationReason = nil
        agentSequenceIssue = nil
        agentLiveMarketContexts = []
        agentDuplicateHydrationKeys.removeAll()
        agentMessageStartCounts.removeAll()
        let clientTurnId = UUID().uuidString
        // Do not warm Longbridge on every conversation open or historical
        // question. The sidecar receives an explicit scope only for a
        // current-market request and emits the resulting provenance visibly.
        let liveContextScope = liveContextScope(for: trimmed)
        Task.detached { [weak self] in
            bridge.agentTurn(
                sessionId: sessionId,
                clientTurnId: clientTurnId,
                input: trimmed,
                sourceQueueId: sourceQueueId,
                attachmentIds: attachments.map(\.id),
                liveContextScope: liveContextScope,
                onControlReady: { control in
                    Task { @MainActor [weak self] in
                        guard self?.activeAgentStreamId == streamId else { return }
                        self?.activeAgentControl = control
                    }
                },
                onFrame: { frame in
                    Task { @MainActor [weak self] in
                        guard self?.activeAgentStreamId == streamId else { return }
                        self?.applyAgentFrame(frame, assistantId: assistantId)
                    }
                },
                onConfirmRequired: { frame in
                    let gate = ChatConfirmGate()
                    DispatchQueue.main.async { [weak self] in
                        guard let self, self.activeAgentStreamId == streamId
                        else { gate.resolve(false); return }
                        self.activeConfirmGate = gate
                        let ctx = self.chatMessages.last(where: { $0.role == .assistant })?.text ?? ""
                        let confirm = PendingWriteConfirm(
                            callId: frame.callId ?? "", tool: frame.tool ?? frame.name ?? "",
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
                    Task { @MainActor [weak self] in
                        self?.endAgentChat(
                            bridge: bridge,
                            assistantId: assistantId,
                            input: trimmed,
                            streamId: streamId,
                            error: err)
                    }
                })
        }
    }

    private func startLegacyChat(bridge: BridgeClient, assistantId: UUID) {
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

    func stopChatGeneration() {
        userAbortedAgentRun = activeAgentControl != nil
        activeAgentControl?.abort(runId: activeAgentRunId)
        isChatStreaming = false
        chatToolInProgress = nil
        activeConfirmGate?.resolve(false)
        activeConfirmGate = nil
        pendingWriteConfirm = nil
        pendingQueueClientMessageId = nil
    }

    /// 将生成期间的新输入排入当前 run。编辑器只在服务端 queue_update=accepted
    /// 后清空；调用者通过返回的 client_message_id 关联这次确认。
    @discardableResult
    func enqueueAgentInput(
        _ text: String,
        mode: String,
        sourceQueueId: String? = nil
    ) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard isChatStreaming, !trimmed.isEmpty,
              pendingQueueClientMessageId == nil,
              let control = activeAgentControl,
              let runId = activeAgentRunId
        else { return nil }
        let clientMessageId = UUID().uuidString
        pendingQueueClientMessageId = clientMessageId
        agentQueueAcknowledgement = nil
        if mode == "follow_up" {
            control.followUp(
                runId: runId,
                clientMessageId: clientMessageId,
                input: trimmed,
                sourceQueueId: sourceQueueId)
        } else {
            control.steer(
                runId: runId,
                clientMessageId: clientMessageId,
                input: trimmed,
                sourceQueueId: sourceQueueId)
        }
        return clientMessageId
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

    @discardableResult
    func applyAgentFrame(_ frame: AgentFrame, assistantId: UUID? = nil) -> Bool {
        let runKey = frame.runId ?? activeAgentRunId ?? "default"
        if let sequence = frame.sequence {
            var seen = agentSeenSequences[runKey] ?? []
            if seen.contains(sequence) { return false }
            seen.insert(sequence)
            agentSeenSequences[runKey] = seen
            let expected = agentExpectedSequence[runKey] ?? 1
            if sequence > expected {
                agentSequenceIssue = "Agent frame gap: expected \(expected), got \(sequence)"
            }
            agentExpectedSequence[runKey] = max(expected, sequence + 1)
        }

        if let runId = frame.runId { activeAgentRunId = runId }
        if let sessionId = frame.sessionId {
            selectedAgentSessionId = sessionId
            persistLastAgentSession(sessionId)
        }
        if let usage = frame.contextUsage {
            agentContextUsage = usage
        }
        if let model = frame.model {
            agentModel = model
        }
        if let provider = frame.provider {
            agentProvider = provider
        }
        if let route = frame.providerRoute {
            agentPrimaryRoute = route
        }
        if let usage = frame.usage {
            agentUsage = usage
        }
        if let existingRunId = frame.existingRunId {
            agentExistingRunId = existingRunId
        }
        if let isError = frame.isError {
            agentLastEventIsError = isError
        }
        if let terminationReason = frame.terminationReason {
            agentTerminationReason = terminationReason
        } else if frame.type == "turn_end" || frame.type == "agent_end" {
            agentTerminationReason = frame.reason
        }

        let idx: Int? = {
            if let currentId = agentCurrentAssistantMessageIds[runKey],
               let idx = chatMessages.firstIndex(where: { $0.id == currentId }) {
                return idx
            }
            if let assistantId, let idx = chatMessages.firstIndex(where: { $0.id == assistantId }) { return idx }
            return chatMessages.lastIndex(where: { $0.role == .assistant })
        }()

        if let duplicateReason = Self.duplicateAgentReason(for: frame) {
            agentTerminationReason = duplicateReason
            chatToolInProgress = nil
            if let sessionId = frame.sessionId ?? selectedAgentSessionId {
                requestDuplicateSessionHydration(
                    sessionId: sessionId,
                    triggeringRunId: frame.runId ?? activeAgentRunId,
                    existingRunId: frame.existingRunId)
            }
            return true
        }

        switch frame.type {
        case "agent_start", "turn_start":
            chatToolInProgress = nil
        case "message_start":
            let messageCount = agentMessageStartCounts[runKey] ?? 0
            if messageCount > 0 || idx == nil {
                let assistant = ChatMessage(
                    role: .assistant,
                    text: "",
                    numbersUnverified: true)
                chatMessages.append(assistant)
                agentCurrentAssistantMessageIds[runKey] = assistant.id
            } else if let idx {
                agentCurrentAssistantMessageIds[runKey] = chatMessages[idx].id
            }
            agentMessageStartCounts[runKey] = messageCount + 1
        case "message_delta":
            if let idx {
                chatMessages[idx].text += frame.delta ?? frame.text ?? ""
            }
        case "message_end":
            if let idx {
                chatMessages[idx].numbersUnverified = false
                if let blocks = frame.contentBlocks {
                    chatMessages[idx].thinkingBlocks = blocks.filter { $0.type == "thinking" }
                    if chatMessages[idx].text.isEmpty {
                        chatMessages[idx].text = blocks
                            .filter { $0.type == "text" }
                            .compactMap(\.text)
                            .joined()
                    }
                }
                if let attachments = frame.attachments {
                    chatMessages[idx].attachments = attachments
                } else if let attachment = frame.attachment {
                    chatMessages[idx].attachments = [attachment]
                }
                mergeAgentEvidence(frame, into: idx)
            }
        case "thinking_start":
            if let idx {
                upsertThinkingBlock(frame, into: idx, appendDelta: false)
            }
        case "thinking_delta":
            if let idx {
                upsertThinkingBlock(frame, into: idx, appendDelta: true)
            }
        case "thinking_end":
            if let idx {
                upsertThinkingBlock(frame, into: idx, appendDelta: false)
            }
        case "attachment_import_start":
            isImportingAgentAttachment = true
            agentAttachmentError = nil
        case "attachment_import_end":
            isImportingAgentAttachment = false
            if let attachment = frame.attachment,
               !pendingAgentAttachments.contains(where: { $0.id == attachment.id }) {
                pendingAgentAttachments.append(attachment)
            }
            if let attachments = frame.attachments {
                pendingAgentAttachments = attachments
            }
        case "attachment_import_error":
            isImportingAgentAttachment = false
            agentAttachmentError = frame.error ?? frame.reason ?? "附件导入失败"
        case "provider_status":
            agentProviderStatus = frame.reason ?? frame.text
        case "tool_start", "tool_update":
            chatToolInProgress = frame.name ?? frame.tool
        case "tool_end":
            if let idx { mergeAgentEvidence(frame, into: idx) }
            chatToolInProgress = nil
        case "confirm_required":
            break
        case "memory_candidate":
            if let candidate = frame.memoryCandidate, !agentMemoryCandidates.contains(where: { $0.id == candidate.id }) {
                agentMemoryCandidates.append(candidate)
            }
        case "memory_recall":
            if let memories = frame.memories { agentMemories = memories }
        case "source_recall", "recall":
            if let recalls = frame.recalls {
                agentSourceRecalls = recalls
            }
            if let recall = frame.recall, !agentSourceRecalls.contains(where: { $0.id == recall.id }) {
                agentSourceRecalls.append(recall)
            }
        case "live_context":
            agentLiveMarketContexts = frame.liveContexts ?? []
        case "queue_update":
            applyAgentQueueUpdate(frame)
        case "research_candidate":
            // A candidate is only an affordance for the user. Receiving it must
            // never create or start a durable research goal.
            researchCandidate = frame.researchCandidate
        case "compaction_start":
            agentContextUsage = frame.contextUsage ?? AgentContextUsage(used: nil, limit: nil, percent: nil, label: "压缩中")
        case "compaction_end":
            if let usage = frame.contextUsage { agentContextUsage = usage }
        case "turn_end", "agent_end":
            chatToolInProgress = nil
            if let idx {
                mergeAgentEvidence(frame, into: idx)
                if let unverified = frame.numberGuard?.unverified {
                    chatMessages[idx].numbersUnverified = !unverified.isEmpty
                }
            }
            if frame.type == "agent_end" {
                agentCurrentAssistantMessageIds.removeValue(forKey: runKey)
            }
        case "error":
            chatToolInProgress = nil
            if let idx, chatMessages[idx].text.isEmpty {
                chatMessages[idx].text = "出错了：\(frame.error ?? "未知错误")"
                chatMessages[idx].isError = true
            } else {
                errorMessage = frame.error
            }
        default:
            break
        }
        if let sessionId = selectedAgentSessionId {
            chatMessagesByAgentSession[sessionId] = chatMessages
        }
        return true
    }

    private func upsertThinkingBlock(
        _ frame: AgentFrame,
        into messageIndex: Int,
        appendDelta: Bool
    ) {
        let contentIndex = frame.contentIndex ?? 0
        var blocks = chatMessages[messageIndex].thinkingBlocks
        let blockIndex = blocks.firstIndex {
            $0.type == "thinking" && ($0.contentIndex ?? 0) == contentIndex
        }
        let incomingText = frame.delta ?? frame.text ?? ""
        if let blockIndex {
            if appendDelta {
                blocks[blockIndex].text = (blocks[blockIndex].text ?? "") + incomingText
            } else if !incomingText.isEmpty {
                blocks[blockIndex].text = incomingText
            }
            if let signature = frame.signature { blocks[blockIndex].signature = signature }
            if let redacted = frame.redacted { blocks[blockIndex].redacted = redacted }
            if let provider = frame.provider { blocks[blockIndex].provider = provider }
            if let model = frame.model { blocks[blockIndex].model = model }
        } else {
            blocks.append(AgentContentBlock(
                type: "thinking",
                contentIndex: contentIndex,
                text: incomingText,
                signature: frame.signature,
                redacted: frame.redacted,
                provider: frame.provider,
                model: frame.model,
                attachmentId: nil,
                mimeType: nil))
            blocks.sort { ($0.contentIndex ?? 0) < ($1.contentIndex ?? 0) }
        }
        chatMessages[messageIndex].thinkingBlocks = blocks
    }

    @discardableResult
    func applyResearchEvent(_ event: ResearchEvent) -> Bool {
        let goalId = event.goalId
        if event.type == "research_snapshot", let snapshot = event.snapshot {
            ingestResearchDetail(snapshot)
            return true
        }
        var seen = researchSeenSequences[goalId] ?? []
        var seenEventIds = researchSeenEventIds[goalId] ?? []
        if seen.contains(event.sequence) || seenEventIds.contains(event.eventId) {
            return false
        }
        seen.insert(event.sequence)
        seenEventIds.insert(event.eventId)
        researchSeenSequences[goalId] = seen
        researchSeenEventIds[goalId] = seenEventIds

        let expected = researchExpectedSequence[goalId] ?? 1
        if event.sequence > expected {
            researchSequenceIssues[goalId] = "研究事件丢帧：预期 \(expected)，收到 \(event.sequence)"
        }
        researchExpectedSequence[goalId] = max(expected, event.sequence + 1)

        var events = researchEventsByGoal[goalId] ?? []
        events.append(event)
        events.sort {
            if $0.sequence == $1.sequence { return $0.eventId < $1.eventId }
            return $0.sequence < $1.sequence
        }
        researchEventsByGoal[goalId] = events
        reduceResearchState(with: event)
        return true
    }

    private func reduceResearchState(with event: ResearchEvent) {
        guard var goal = selectedResearchGoal, goal.goalId == event.goalId else { return }
        switch event.type {
        case "goal_status", "research_start", "research_end":
            if let status = event.status, !status.isEmpty {
                goal.status = status
            }
        case "research_error":
            goal.status = "failed"
        case "task_ready", "task_start", "task_end":
            if let taskId = event.taskId,
               let index = goal.tasks.firstIndex(where: { $0.taskId == taskId }) {
                switch event.type {
                case "task_ready":
                    goal.tasks[index].status = "ready"
                case "task_start":
                    goal.tasks[index].status = "running"
                default:
                    goal.tasks[index].status = event.status ?? "incomplete"
                }
            }
        default:
            break
        }
        let succeeded = goal.tasks.filter { $0.status == "succeeded" }.count
        goal.progress = goal.tasks.isEmpty
            ? 0
            : Double(succeeded) / Double(goal.tasks.count)
        selectedResearchGoal = goal
        upsertResearchGoal(goal.summary)
    }

    private func applyAgentQueueUpdate(_ frame: AgentFrame) {
        let operation = frame.operation ?? "updated"
        if operation != "rejected" {
            if let queuedInputs = frame.queuedInputs {
                agentQueuedInputs = queuedInputs.filter(\.isRestorable)
            } else if let item = frame.item {
                if item.isRestorable {
                    if let idx = agentQueuedInputs.firstIndex(where: { $0.id == item.id }) {
                        agentQueuedInputs[idx] = item
                    } else {
                        agentQueuedInputs.append(item)
                    }
                } else {
                    agentQueuedInputs.removeAll { $0.id == item.id }
                }
            }
            let hasSnapshot = frame.queuedInputs != nil
            agentSteeringCount = hasSnapshot
                ? agentQueuedInputs.filter { $0.mode == "steering" }.count
                : frame.steeringCount
                    ?? agentQueuedInputs.filter { $0.mode == "steering" }.count
            agentFollowUpCount = hasSnapshot
                ? agentQueuedInputs.filter { $0.mode == "follow_up" }.count
                : frame.followUpCount
                    ?? agentQueuedInputs.filter { $0.mode == "follow_up" }.count
        }

        if operation == "accepted" || operation == "rejected" {
            let clientMessageId = frame.item?.clientMessageId ?? pendingQueueClientMessageId
            if let clientMessageId {
                agentQueueAcknowledgement = AgentQueueAcknowledgement(
                    clientMessageId: clientMessageId,
                    accepted: operation == "accepted",
                    operation: operation,
                    reason: frame.reason)
            }
            pendingQueueClientMessageId = nil
        }
    }

    nonisolated static func shouldClearQueuedEditor(
        acknowledgement: AgentQueueAcknowledgement?,
        pendingClientMessageId: String?
    ) -> Bool {
        guard let acknowledgement, let pendingClientMessageId else { return false }
        return acknowledgement.accepted
            && acknowledgement.clientMessageId == pendingClientMessageId
    }

    static func duplicateAgentReason(for frame: AgentFrame) -> String? {
        frame.duplicateReason
    }

    private func requestDuplicateSessionHydration(
        sessionId: String,
        triggeringRunId: String?,
        existingRunId: String?
    ) {
        let runGeneration = triggeringRunId ?? activeAgentRunId
        let key = "\(sessionId):\(runGeneration ?? existingRunId ?? "duplicate")"
        guard agentDuplicateHydrationKeys.insert(key).inserted, let bridge else { return }
        Task { [weak self] in
            do {
                let response = try await Task.detached {
                    try bridge.agentSessions(action: "open", sessionId: sessionId)
                }.value
                self?.applyAgentSessionHydration(
                    response,
                    sessionId: sessionId,
                    triggeringRunId: runGeneration)
            } catch {
                guard let self, self.selectedAgentSessionId == sessionId else { return }
                self.errorMessage = "会话恢复失败：\(error.localizedDescription)"
            }
        }
    }

    /// Duplicate frames may race with stream teardown or a newly-started turn.
    /// Only replace UI state when the user is still viewing the same session and
    /// no newer run has taken ownership of the chat surface.
    @discardableResult
    func applyAgentSessionHydration(
        _ response: AgentSessionListResponse,
        sessionId: String,
        triggeringRunId: String?
    ) -> Bool {
        guard selectedAgentSessionId == sessionId else { return false }
        if let activeAgentRunId, let triggeringRunId, activeAgentRunId != triggeringRunId {
            return false
        }
        guard let hydrated = response.sessions.first(where: {
            $0.sessionId == sessionId && !$0.archived
        }) else {
            return false
        }
        agentSessions = response.sessions.filter { !$0.archived }
        chatMessages = hydrateChatMessages(from: hydrated.messages ?? [])
        chatMessagesByAgentSession[sessionId] = chatMessages
        if let contextUsage = hydrated.contextUsage {
            agentContextUsage = contextUsage
        }
        hydrateAgentQueue(hydrated.queuedInputs)
        persistLastAgentSession(sessionId)
        return true
    }

    private func mergeAgentEvidence(_ frame: AgentFrame, into idx: Int) {
        if let summary = frame.evidenceSummary {
            chatMessages[idx].evidenceSummary.merge(summary)
        }
        if let drawer = frame.evidenceDrawer {
            chatMessages[idx].evidenceDrawer.merge(drawer)
        }
    }

    private func endAgentChat(
        bridge: BridgeClient,
        assistantId: UUID,
        input: String,
        streamId: UUID,
        error: String?
    ) {
        guard Self.agentStreamOwnsChatSurface(
            endingStreamId: streamId,
            activeStreamId: activeAgentStreamId)
        else { return }
        let wasUserAbort = userAbortedAgentRun
        userAbortedAgentRun = false
        let normalizedError = error?.lowercased() ?? ""
        let isAbortError = normalizedError.contains("abort") || normalizedError.contains("client_abort")
        let assistant = chatMessages.first(where: { $0.id == assistantId })
        let shouldFallback = Self.shouldFallbackToLegacyAgent(
            error: error,
            terminationReason: agentTerminationReason,
            userAborted: wasUserAbort,
            assistantEmpty: assistant?.text.isEmpty == true,
            assistantIsError: assistant?.isError == true)
        if shouldFallback {
            agentProtocolUnavailable = true
            activeAgentControl = nil
            activeAgentRunId = nil
            activeAgentStreamId = nil
            startLegacyChat(bridge: bridge, assistantId: assistantId)
            return
        }
        isChatStreaming = false
        chatToolInProgress = nil
        activeAgentControl = nil
        activeAgentRunId = nil
        activeAgentStreamId = nil
        activeConfirmGate = nil
        pendingWriteConfirm = nil
        pendingQueueClientMessageId = nil
        if let sessionId = selectedAgentSessionId {
            chatMessagesByAgentSession[sessionId] = chatMessages
        }
        if wasUserAbort || isAbortError {
            if let idx = chatMessages.firstIndex(where: { $0.id == assistantId }),
               chatMessages[idx].text.isEmpty {
                chatMessages[idx].text = "（已停止）"
                chatMessages[idx].numbersUnverified = false
            }
            return
        }
        guard let error else { return }
        if let idx = chatMessages.firstIndex(where: { $0.id == assistantId }),
           chatMessages[idx].text.isEmpty {
            chatMessages[idx].text = "连接中断：\(error)"
            chatMessages[idx].isError = true
        }
    }

    nonisolated static func shouldFallbackToLegacyAgent(
        error: String?,
        terminationReason: String? = nil,
        userAborted: Bool,
        assistantEmpty: Bool,
        assistantIsError: Bool
    ) -> Bool {
        let normalized = error?.lowercased() ?? ""
        let isAbort = normalized.contains("abort") || normalized.contains("client_abort")
        let duplicateValues = [normalized, terminationReason?.lowercased() ?? ""]
        let isDuplicate = duplicateValues.contains {
            $0.contains("duplicate_completed") || $0.contains("already_running")
        }
        return !userAborted && !isAbort && !isDuplicate
            && error != nil && assistantEmpty && !assistantIsError
    }

    nonisolated static func agentStreamOwnsChatSurface(
        endingStreamId: UUID,
        activeStreamId: UUID?
    ) -> Bool {
        endingStreamId == activeStreamId
    }

    private func endChat(assistantId: UUID, error: String?) {
        isChatStreaming = false
        chatToolInProgress = nil
        activeConfirmGate = nil
        pendingWriteConfirm = nil
        if let sessionId = selectedAgentSessionId {
            chatMessagesByAgentSession[sessionId] = chatMessages
        }
        guard let error else { return }
        if let idx = chatMessages.firstIndex(where: { $0.id == assistantId }),
           chatMessages[idx].text.isEmpty {
            chatMessages[idx].text = "连接中断：\(error)"
            chatMessages[idx].isError = true
        }
    }

    func loadAgentBootstrap() async {
        async let sessions: Void = loadAgentSessions()
        async let skills: Void = loadAgentSkills()
        async let memories: Void = loadAgentMemories()
        async let providers: Void = loadAgentProviders(reloadCredentials: true)
        _ = await (sessions, skills, memories, providers)
    }

    func loadAgentProviders(reloadCredentials: Bool = false) async {
        guard let bridge else { return }
        do {
            let response = try await Task.detached {
                try bridge.agentProviders(
                    action: reloadCredentials ? "reload_credentials" : "list"
                )
            }.value
            agentProviders = response.providers
            agentGlobalPrimaryRoute = response.primary
            let sessionRoute = selectedAgentSessionId.flatMap { id in
                agentSessions.first(where: { $0.sessionId == id })?.providerRoute
            }
            agentPrimaryRoute = sessionRoute ?? response.primary
            agentFallbackRoute = response.fallback
            agentProviderStatus = response.status
        } catch {
            // Provider catalog is a protocol-v1 additive feature. An older
            // sidecar must not make the otherwise usable chat surface fail.
            agentProviders = []
        }
    }

    var seesawProviderReadiness: SeesawProviderReadiness {
        let selectedProviderID = agentPrimaryRoute?.providerId
        let catalogConfirmsCredential = agentProviders.first {
            $0.id == selectedProviderID
        }?.authenticated == true
        return Self.providerReadiness(
            route: agentPrimaryRoute,
            credentialPresent: catalogConfirmsCredential
                || KeychainStore.hasLLMCredential(forProviderID: selectedProviderID),
            providerStatus: agentProviderStatus,
            testOK: agentProviderTestOK,
            testError: agentProviderTestError,
            testHint: agentProviderTestHint
        )
    }

    nonisolated static func providerReadiness(
        route: AgentProviderRoute?,
        credentialPresent: Bool,
        providerStatus: String?,
        testOK: Bool?,
        testError: String?,
        testHint: String?
    ) -> SeesawProviderReadiness {
        guard let route,
              !(route.providerId ?? "").isEmpty,
              !(route.modelId ?? "").isEmpty
        else { return .missingRoute }
        guard credentialPresent else { return .missingCredential }
        if let testOK {
            return testOK ? .ready : .failed(
                testError ?? testHint ?? "模型连接测试失败"
            )
        }
        if providerStatus == "unavailable" {
            return .failed(testError ?? "Provider Helper 当前不可用")
        }
        if providerStatus == nil {
            return .brokerLoading
        }
        return .configuredUntested
    }

    func testAgentProviderConnection(route: AgentProviderRoute? = nil) async {
        guard let bridge else { return }
        let activeRoute = route ?? agentPrimaryRoute
        let activeFallback = agentFallbackRoute
        agentProviderTestOK = nil
        agentProviderTestError = nil
        agentProviderTestHint = nil
        do {
            let response = try await Task.detached {
                _ = try bridge.agentProviders(action: "reload_credentials")
                return try bridge.agentProviders(
                    action: "test",
                    primary: activeRoute,
                    fallback: activeFallback
                )
            }.value
            recordAgentProviderTest(response)
            await loadAgentProviders()
            refreshLLMCredentialsStatus()
        } catch {
            agentProviderTestOK = false
            agentProviderTestError = error.localizedDescription
            agentProviderTestHint = "请检查 API Key、模型与服务端点"
        }
    }

    /// Keep explicit probe results separate from catalog availability. A
    /// catalog refresh cannot turn a failed real connection test green.
    func recordAgentProviderTest(_ response: AgentProvidersResponse) {
        agentProviderTestOK = response.ok ?? false
        agentProviderTestError = response.error
        agentProviderTestHint = response.hint
        agentProviderStatus = response.status ?? agentProviderStatus
    }

    func importAgentAttachments(_ urls: [URL]) async {
        guard let bridge else { return }
        guard let sessionId = selectedAgentSessionId else {
            agentAttachmentError = "请先创建会话。"
            return
        }
        guard !isChatStreaming else {
            agentAttachmentError = "生成期间可继续输入，但附件请在下一轮添加。"
            return
        }
        let availableSlots = max(0, 4 - pendingAgentAttachments.count)
        let selected = Array(urls.prefix(availableSlots))
        guard !selected.isEmpty else {
            agentAttachmentError = "每轮最多添加 4 个附件。"
            return
        }

        isImportingAgentAttachment = true
        agentAttachmentError = nil
        defer { isImportingAgentAttachment = false }

        for url in selected {
            let accessed = url.startAccessingSecurityScopedResource()
            defer {
                if accessed { url.stopAccessingSecurityScopedResource() }
            }
            do {
                let prepared = try Self.prepareAgentAttachment(url)
                defer { prepared.cleanup?() }
                let response = try await Task.detached {
                    try bridge.agentAttachments(
                        action: "import",
                        sessionId: sessionId,
                        path: prepared.url.path,
                        extractedText: prepared.extractedText)
                }.value
                for attachment in response.allAttachments
                    where !pendingAgentAttachments.contains(where: { $0.id == attachment.id }) {
                    pendingAgentAttachments.append(attachment)
                }
                if let responseError = response.error, !responseError.isEmpty {
                    agentAttachmentError = responseError
                }
            } catch {
                agentAttachmentError = "无法导入 \(url.lastPathComponent)：\(error.localizedDescription)"
            }
        }
    }

    private nonisolated static func prepareAgentAttachment(
        _ url: URL
    ) throws -> (url: URL, extractedText: String?, cleanup: (() -> Void)?) {
        let suffix = url.pathExtension.lowercased()
        if suffix == "pdf" {
            let extracted = PDFDocument(url: url)?.string.map {
                String($0.prefix(64 * 1024))
            }
            return (url, extracted, nil)
        }
        guard suffix == "heic" || suffix == "heif" else {
            return (url, nil, nil)
        }
        guard let image = NSImage(contentsOf: url),
              let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let jpeg = bitmap.representation(
                using: .jpeg,
                properties: [.compressionFactor: 0.9])
        else {
            throw BridgeError.processFailed("HEIC 图片无法转换为 JPEG")
        }
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("kss-attachment-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true)
        let filename = url.deletingPathExtension().lastPathComponent + ".jpg"
        let target = directory.appendingPathComponent(filename)
        do {
            try jpeg.write(to: target, options: .atomic)
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
        return (
            target,
            nil,
            { try? FileManager.default.removeItem(at: directory) })
    }

    func removePendingAgentAttachment(_ attachment: AgentAttachment) {
        pendingAgentAttachments.removeAll { $0.id == attachment.id }
        guard let bridge, let sessionId = selectedAgentSessionId else { return }
        Task.detached {
            _ = try? bridge.agentAttachments(
                action: "remove",
                sessionId: sessionId,
                attachmentId: attachment.id)
        }
    }

    func loadAgentSessions() async {
        guard let bridge else { return }
        do {
            let response = try await Task.detached { try bridge.agentSessions() }.value
            agentProtocolUnavailable = false
            agentSessions = response.sessions.filter { !$0.archived }
            let preferred = response.selectedSessionId ?? selectedAgentSessionId
            let target = preferred.flatMap { candidate in
                agentSessions.contains(where: { $0.sessionId == candidate }) ? candidate : nil
            } ?? agentSessions.first?.sessionId
            if let target {
                openAgentSession(target)
            } else {
                createAgentSession()
            }
        } catch {
            agentProtocolUnavailable = true
            ensureAgentSession()
        }
    }

    func createAgentSession() {
        let title = "新会话"
        let localId = "local-\(UUID().uuidString)"
        let session = AgentSession(sessionId: localId, title: title)
        agentSessions.insert(session, at: 0)
        openAgentSession(localId)
        guard let bridge else { return }
        Task {
            let task = Task.detached {
                try bridge.agentSessions(action: "create", sessionId: localId, title: title)
            }
            if let response = try? await task.value {
                agentProtocolUnavailable = false
                agentSessions = response.sessions.filter { !$0.archived }
                if let selected = response.selectedSessionId ?? agentSessions.first?.sessionId {
                    openAgentSession(selected)
                }
            }
        }
    }

    func openAgentSession(_ sessionId: String) {
        // 切换前把当前会话消息写回缓存，避免切走丢未同步的本地态
        if let previous = selectedAgentSessionId, previous != sessionId {
            chatMessagesByAgentSession[previous] = chatMessages
        }
        selectedAgentSessionId = sessionId
        persistLastAgentSession(sessionId)

        if let session = agentSessions.first(where: { $0.sessionId == sessionId }) {
            if let hydrated = session.messages, !hydrated.isEmpty {
                chatMessages = hydrateChatMessages(from: hydrated)
            } else if let cached = chatMessagesByAgentSession[sessionId], !cached.isEmpty {
                chatMessages = cached
            } else if let hydrated = session.messages {
                // messages == []：明确空会话
                chatMessages = hydrateChatMessages(from: hydrated)
            } else {
                chatMessages = chatMessagesByAgentSession[sessionId] ?? []
            }
            chatMessagesByAgentSession[sessionId] = chatMessages
            agentContextUsage = session.contextUsage
            agentPrimaryRoute = session.providerRoute ?? agentGlobalPrimaryRoute ?? agentPrimaryRoute
            pendingAgentAttachments.removeAll()
            agentAttachmentError = nil
            hydrateAgentQueue(session.queuedInputs)
            // list 未带 messages 时 bridge open 补全
            if session.messages == nil {
                requestSessionOpenHydration(sessionId: sessionId)
            }
        } else if let cached = chatMessagesByAgentSession[sessionId] {
            chatMessages = cached
            hydrateAgentQueue(nil)
            requestSessionOpenHydration(sessionId: sessionId)
        } else {
            chatMessages = []
            hydrateAgentQueue(nil)
            requestSessionOpenHydration(sessionId: sessionId)
        }
    }

    /// 打开会话时从 bridge 拉完整消息（list 未 hydrate / 冷缓存 miss）。
    private func requestSessionOpenHydration(sessionId: String) {
        guard let bridge else { return }
        Task { [weak self] in
            do {
                let response = try await Task.detached {
                    try bridge.agentSessions(action: "open", sessionId: sessionId)
                }.value
                guard let self, self.selectedAgentSessionId == sessionId else { return }
                _ = self.applyAgentSessionHydration(
                    response,
                    sessionId: sessionId,
                    triggeringRunId: nil
                )
            } catch {
                guard let self, self.selectedAgentSessionId == sessionId else { return }
                if self.chatMessages.isEmpty {
                    self.errorMessage = "打开会话失败：\(error.localizedDescription)"
                }
            }
        }
    }

    func setAgentSessionProviderRoute(_ route: AgentProviderRoute) {
        guard !isChatStreaming,
              let sessionId = selectedAgentSessionId,
              let bridge
        else { return }
        Task {
            do {
                let response = try await Task.detached {
                    try bridge.agentSessions(
                        action: "set_provider_route",
                        sessionId: sessionId,
                        providerRoute: route
                    )
                }.value
                agentSessions = response.sessions.filter { !$0.archived }
                agentPrimaryRoute = route
                agentProviderTestOK = nil
                agentProviderTestError = nil
                agentProviderTestHint = nil
            } catch {
                agentProviderTestOK = false
                agentProviderTestError = error.localizedDescription
            }
        }
    }

    /// Update the default snapshot used only by future conversations. The
    /// active session keeps its own persisted route, so changing the default
    /// never reroutes an existing transcript.
    func setAgentGlobalDefaultRoute(_ route: AgentProviderRoute) {
        guard !isChatStreaming, let bridge else { return }
        let fallback = agentFallbackRoute
        Task {
            do {
                let response = try await Task.detached {
                    try bridge.agentProviders(
                        action: "set_route",
                        primary: route,
                        fallback: fallback
                    )
                }.value
                self.agentGlobalPrimaryRoute = response.primary ?? route
                self.agentFallbackRoute = response.fallback
                self.agentProviderStatus = response.status
                self.agentProviderTestOK = nil
                self.agentProviderTestError = nil
                self.agentProviderTestHint = nil
            } catch {
                self.agentProviderTestOK = false
                self.agentProviderTestError = error.localizedDescription
            }
        }
    }

    /// The fallback is deliberately global: it is a recovery policy for a
    /// brand-new provider stream, not another per-session preference. Runtime
    /// will only use it before the primary route has emitted content.
    func setAgentFallbackRoute(_ route: AgentProviderRoute) {
        guard !isChatStreaming, let bridge else { return }
        let primary = agentGlobalPrimaryRoute ?? agentPrimaryRoute
        Task {
            do {
                let response = try await Task.detached {
                    try bridge.agentProviders(
                        action: "set_route",
                        primary: primary,
                        fallback: route
                    )
                }.value
                self.agentGlobalPrimaryRoute = response.primary ?? primary
                self.agentFallbackRoute = response.fallback ?? route
                self.agentProviderStatus = response.status
                self.agentProviderTestOK = nil
                self.agentProviderTestError = nil
                self.agentProviderTestHint = nil
            } catch {
                self.agentProviderTestOK = false
                self.agentProviderTestError = error.localizedDescription
            }
        }
    }

    private func hydrateAgentQueue(_ inputs: [AgentQueuedInput]?) {
        agentQueuedInputs = (inputs ?? []).filter(\.isRestorable)
        agentSteeringCount = agentQueuedInputs.filter { $0.mode == "steering" }.count
        agentFollowUpCount = agentQueuedInputs.filter { $0.mode == "follow_up" }.count
        agentQueueAcknowledgement = nil
        pendingQueueClientMessageId = nil
    }

    func discardQueuedInput(_ item: AgentQueuedInput) {
        guard let sessionId = selectedAgentSessionId, let bridge else { return }
        Task {
            do {
                let response = try await Task.detached {
                    try bridge.agentQueue(
                        action: "discard", sessionId: sessionId, queueId: item.id)
                }.value
                guard self.selectedAgentSessionId == sessionId else { return }
                self.agentQueuedInputs = (response.queuedInputs ?? []).filter(\.isRestorable)
                self.agentSteeringCount = self.agentQueuedInputs
                    .filter { $0.mode == "steering" }.count
                self.agentFollowUpCount = self.agentQueuedInputs
                    .filter { $0.mode == "follow_up" }.count
            } catch {
                self.errorMessage = "队列操作失败：\(error.localizedDescription)"
            }
        }
    }

    func renameAgentSession(_ sessionId: String, title: String) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if let idx = agentSessions.firstIndex(where: { $0.sessionId == sessionId }) {
            agentSessions[idx].title = trimmed
        }
        guard let bridge else { return }
        Task { _ = try? await Task.detached { try bridge.agentSessions(action: "rename", sessionId: sessionId, title: trimmed) }.value }
    }

    func archiveAgentSession(_ sessionId: String) {
        agentSessions.removeAll { $0.sessionId == sessionId }
        chatMessagesByAgentSession[sessionId] = nil
        if selectedAgentSessionId == sessionId {
            if let next = agentSessions.first?.sessionId {
                openAgentSession(next)
            } else {
                createAgentSession()
            }
        }
        guard let bridge else { return }
        Task { _ = try? await Task.detached { try bridge.agentSessions(action: "archive", sessionId: sessionId) }.value }
    }

    func loadAgentSkills() async {
        guard let bridge else { return }
        let sessionId = selectedAgentSessionId
        let task = Task.detached { try bridge.agentSkills(sessionId: sessionId) }
        if let response = try? await task.value {
            agentSkills = response.skills
            agentSkillDiagnostics = response.diagnostics ?? []
            pinnedAgentSkillIds = Set(response.skills.filter { $0.pinned == true }.map(\.id))
        }
    }

    /// Adds or removes a Skill from the currently selected conversation.
    /// The sidecar action remains `pin` for protocol-v1 compatibility; "pin" is
    /// deliberately not exposed as user-facing Seesaw vocabulary.
    func setAgentSkillInConversation(_ skill: AgentSkill, selected: Bool) {
        if selected { pinnedAgentSkillIds.insert(skill.id) } else { pinnedAgentSkillIds.remove(skill.id) }
        guard let bridge else { return }
        let sessionId = selectedAgentSessionId
        Task {
            let response = try? await Task.detached {
                try bridge.agentSkills(
                    action: "pin", sessionId: sessionId, skillId: skill.id, pinned: selected)
            }.value
            if let response {
                agentSkills = response.skills
                agentSkillDiagnostics = response.diagnostics ?? []
                pinnedAgentSkillIds = Set(response.skills.filter { $0.pinned == true }.map(\.id))
            }
        }
    }

    /// Compatibility shim for retired UI surfaces until their removal.
    func setAgentSkillPinned(_ skill: AgentSkill, pinned: Bool) {
        setAgentSkillInConversation(skill, selected: pinned)
    }

    func setAgentSkillEnabled(_ skill: AgentSkill, enabled: Bool) {
        if let idx = agentSkills.firstIndex(where: { $0.id == skill.id }) {
            agentSkills[idx].enabled = enabled
        }
        guard let bridge else { return }
        let sessionId = selectedAgentSessionId
        Task {
            let response = try? await Task.detached {
                try bridge.agentSkills(
                    action: "enable", sessionId: sessionId, skillId: skill.id, enabled: enabled)
            }.value
            if let response {
                agentSkills = response.skills
                agentSkillDiagnostics = response.diagnostics ?? []
            }
        }
    }

    func reloadAgentSkills() {
        guard let bridge else { return }
        let sessionId = selectedAgentSessionId
        Task {
            let task = Task.detached { try bridge.agentSkills(action: "reload", sessionId: sessionId) }
            if let response = try? await task.value {
                agentSkills = response.skills
                agentSkillDiagnostics = response.diagnostics ?? []
                pinnedAgentSkillIds = Set(response.skills.filter { $0.pinned == true }.map(\.id))
            }
        }
    }

    func loadAgentMemories(query: String? = nil) async {
        guard let bridge else { return }
        let task = Task.detached { try bridge.agentMemories(action: query == nil ? "list" : "search", query: query) }
        if let response = try? await task.value {
            agentMemories = response.memories
            agentMemoryCandidates = response.candidates ?? agentMemoryCandidates
            agentSourceRecalls = response.recalls ?? agentSourceRecalls
        }
    }

    func resolveMemoryCandidate(_ candidate: AgentMemoryCandidate, approved: Bool) {
        agentMemoryCandidates.removeAll { $0.id == candidate.id }
        guard let bridge else { return }
        Task {
            let task = Task.detached {
                try bridge.agentMemories(action: "approve", candidateId: candidate.id, approved: approved)
            }
            if let response = try? await task.value {
                agentMemories = response.memories
            }
        }
    }

    func proposeAgentMemory(_ text: String, kind: String = "preference") {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let bridge else { return }
        let sessionId = selectedAgentSessionId
        Task {
            do {
                let response = try await Task.detached {
                    try bridge.agentMemories(
                        action: "propose", text: trimmed, kind: kind, sourceSession: sessionId)
                }.value
                agentMemoryCandidates = response.candidates ?? []
                agentMemories = response.memories
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func archiveAgentMemory(_ memory: AgentMemoryRecord) {
        agentMemories.removeAll { $0.id == memory.id }
        guard let bridge else { return }
        Task { _ = try? await Task.detached { try bridge.agentMemories(action: "archive", memoryId: memory.id) }.value }
    }

    func deleteAgentMemory(_ memory: AgentMemoryRecord) {
        agentMemories.removeAll { $0.id == memory.id }
        guard let bridge else { return }
        Task { _ = try? await Task.detached { try bridge.agentMemories(action: "delete", memoryId: memory.id) }.value }
    }

    func recallAgentSources(query: String) {
        guard let bridge else { return }
        Task {
            let task = Task.detached { try bridge.agentMemories(action: "source-recall", query: query) }
            if let response = try? await task.value {
                agentSourceRecalls = response.recalls ?? []
            }
        }
    }

    private func ensureAgentSession() {
        if selectedAgentSessionId != nil { return }
        if let last = UserDefaults.standard.string(forKey: "kss.agent.lastSessionId"),
           agentSessions.contains(where: { $0.sessionId == last }) {
            openAgentSession(last)
            return
        }
        let session = AgentSession(sessionId: "local-\(UUID().uuidString)", title: "本地会话")
        agentSessions = [session]
        openAgentSession(session.sessionId)
    }

    private func restoreLastAgentSession() {
        let last = UserDefaults.standard.string(forKey: "kss.agent.lastSessionId")
        let session = AgentSession(sessionId: last ?? "local-\(UUID().uuidString)", title: "本地会话")
        agentSessions = [session]
        openAgentSession(session.sessionId)
    }

    private func persistLastAgentSession(_ sessionId: String) {
        UserDefaults.standard.set(sessionId, forKey: "kss.agent.lastSessionId")
    }

    private func hydrateChatMessages(from messages: [AgentHydratedMessage]) -> [ChatMessage] {
        messages.compactMap { message in
            guard message.role == "user"
                    || (message.role == "assistant"
                        && (message.toolCalls?.isEmpty ?? true)
                        && !message.text.isEmpty)
            else {
                return nil
            }
            var chat = ChatMessage(
                role: message.role == "user" ? .user : .assistant,
                text: message.text,
                thinkingBlocks: (message.contentBlocks ?? []).filter { $0.type == "thinking" },
                attachments: message.attachments ?? [],
                numbersUnverified: false)
            if let summary = message.evidenceSummary { chat.evidenceSummary = summary }
            if let drawer = message.evidenceDrawer { chat.evidenceDrawer = drawer }
            return chat
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
            // 自选列表（R6 R6）：用户主动盯的票，优先级仅次于当前选中
            priority.append(contentsOf: watchlistSymbols)
            // 盯盘堆叠卡：优先进预算槽（实盘主视觉）
            priority.append(contentsOf: RealtimeMerge.symbolsFromIndexStacks(snapshot?.marketStrip?.indexStacks))
            priority.append(contentsOf: RealtimeMerge.symbolsFromRecommendations(snapshot?.recommendations ?? []))
            priority.append(contentsOf: RealtimeMerge.symbolsFromThemes(themeLeaders))
            list = RealtimeMerge.harvestSymbols(
                strip: snapshot?.marketStrip,
                priority: priority,
                // R5：今日板块代表 ETF——排在 strip 热区之后、indexBoard 之前
                extra: RealtimeMerge.symbolsFromSectorPulse(snapshot?.sectorReviews?.first)
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

        // R5 批量化：整 tick 一次 `longbridge-quotes` 往返（旧逐标串行在 20+ 标时
        // 每 tick 打 20 次 bridge，预算放宽到 60 后必须批量）。coalesce 仍按产品码逐标。
        var toFetch: [(display: String, lb: String)] = []
        for displaySym in list {
            guard let lbSym = RealtimeMerge.toLongbridgeSymbol(displaySym) else { continue }
            if shouldSkipDispatch(cmd: "longbridge-quote", symbol: displaySym) {
                if updated[displaySym]?.isLive == true { anySuccess = true }
                continue
            }
            toFetch.append((displaySym, lbSym))
        }
        if !toFetch.isEmpty {
            let lbSyms = toFetch.map(\.lb)
            let quotes = (try? await Task.detached {
                try bridge.longbridgeQuotes(symbols: lbSyms)
            }.value) ?? []
            // 响应行 symbol = 归一码（等于请求的 Longbridge 码）；映射回产品码
            var displayByLb: [String: String] = [:]
            for pair in toFetch { displayByLb[pair.lb.uppercased()] = pair.display }
            for quote in quotes {
                if quote.error == "auth_failed" {
                    sawAuthFailed = true
                    break
                }
                guard let norm = quote.symbol?.uppercased(),
                      let displaySym = displayByLb[norm] else { continue }
                if quote.isLive {
                    updated[displaySym] = quote
                    realtimeReceivedAtBySymbol[displaySym] = Date()
                    anySuccess = true
                }
                // 软失败：不删除 map 已有项
            }
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

    /// 昨收锚点回退（KTD7）：盘中优先 Longbridge quote.prevClose；无 quote（盘后/未订阅）时
    /// 由堆叠卡快照条目自身的 close/(1+pct/100) 反推（与 DashboardView.absoluteChange 同式）。
    private func prevCloseFallback(forCode code: String) -> Double? {
        if let prev = realtimeQuotesBySymbol[code]?.prevClose, prev > 0 { return prev }
        guard let item = (snapshot?.marketStrip?.indexStacks ?? [])
            .flatMap(\.items)
            .first(where: { $0.code.uppercased() == code }) else { return nil }
        if item.pct <= -100 { return nil }
        let prev = item.close / (1 + item.pct / 100.0)
        return prev > 0 ? prev : nil
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
                let prevClose = prevCloseFallback(forCode: key)
                let dayHigh = bars.bars.compactMap(\.high).max()
                let dayLow = bars.bars.compactMap(\.low).min()
                let merged = SparklineYAxis.merge(
                    existing: sparks[key],
                    newPoints: closes,
                    newPrevClose: prevClose,
                    newDayHigh: dayHigh,
                    newDayLow: dayLow,
                    newTradeDate: bars.sessionDate
                )
                sparks[key] = merged
                // 产品码与请求码都写一份，避免 HSI / HSI.HK 键不一致
                if reqSym.uppercased() != key {
                    sparks[reqSym.uppercased()] = merged
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
        // 清 coalesce，保证重试真正打到 bridge（quote + bars）。
        // R2-U6 修复：sparkline 实际 coalesce 键前缀是 "intraday-bars-spark:"（详情页 1 分线
        // 用 "intraday-bars:"），旧过滤条件只清了后者，堆叠卡 sparkline 手动重试后仍可能被
        // 30s coalesce 窗口吞掉。
        lastDispatchCache = lastDispatchCache.filter {
            !$0.key.hasPrefix("longbridge-quote:")
                && !$0.key.hasPrefix("intraday-bars:")
                && !$0.key.hasPrefix("intraday-bars-spark:")
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

    /// 切到某赛道时后台预热该赛道 Top-K 投研稿（会话内每赛道一次，fire-and-forget）。
    func prewarmIntelTrack(_ trackKey: String) {
        guard let bridge, !trackKey.isEmpty else { return }
        guard !prewarmedIntelTracks.contains(trackKey) else { return }
        prewarmedIntelTracks.insert(trackKey)
        Task.detached {
            _ = try? bridge.intelRewriteRun(trackKey: trackKey)
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

            // 并行：正文（读穿缓存）+ 投研改写（点开自动，claim/TTL 防重入）。
            // 中文改写自动生成已移除（plan 2026-07-22-001 KTD5）：点开只烧投研一路。
            async let articleTask: IntelArticleResponse? = {
                if let url = item.url, !url.isEmpty {
                    return try? await Task.detached {
                        try bridge.intelArticle(url: url, summary: item.summary ?? "")
                    }.value
                }
                return nil
            }()
            async let investTask: IntelRewriteResponse? = {
                try? await Task.detached {
                    try bridge.intelRewrite(
                        trackKey: trackKey, trackName: trackName, item: item,
                        force: false, kind: "investment"
                    )
                }.value
            }()

            let (article, invest) = await (articleTask, investTask)
            if let article {
                self.intelArticleByID[item.id] = article
            } else if let body = invest?.bodyText, !body.isEmpty {
                self.intelArticleByID[item.id] = IntelArticleResponse(
                    body: body, title: item.title,
                    mode: invest?.bodyMode ?? "summary",
                    error: nil,
                    charCount: invest?.bodyCharCount,
                    url: item.url
                )
            }
            if let invest { self.setRewrite(invest, itemID: item.id, kind: "investment") }
        }
    }

    /// On-demand rewrite for selected item. kind: investment | translation
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

    /// 启动时检测 LLM Keychain 凭据是否存在——新六键（KSS_LLM_PRIMARY_KEY 优先判定，
    /// FALLBACK_KEY 单独存在也算已配置）或旧 OpenAI/DeepSeek 键任一存在即算已配置，
    /// 与 openai_client._resolve_credential_candidates() 的判定口径保持一致（U3/U9）。
    func refreshLLMCredentialsStatus() {
        hasLLMCredentials = KeychainStore.hasLLMCredentials()
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
        var normalized = resp.items
        // Agent secrets intentionally travel through the Keychain/Broker, so
        // the sidecar's legacy environment-only probe can never be the Swift
        // UI's source of truth. Preserve all other self-check results.
        if let index = normalized.firstIndex(where: { $0.item == "llm" }),
           KeychainStore.hasLLMCredentials() {
            normalized[index] = SelfCheckItem(
                item: "llm",
                status: "ok",
                detail: "Seesaw LLM 凭据已存于 macOS Keychain",
                fixHint: nil,
                fixAction: nil
            )
        }
        selfCheckItems = normalized
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
        reevaluateUSMarketTimer()
    }

    /// 交易时段门控更新后重新评估两条独立 timer（trading-hours 查询与 loadTradingHours 异步）。
    /// quote timer：auth_failed 时停表，直到 retryRealtime 成功（既有行为不变）。
    /// sparkline timer（R2-U6 KTD6）：盘后交易日独立 5 分钟 tick，不受 authFailed 影响，
    /// 非交易日整天暂停；交易时段内不单独跑（随 quote tick 顺带刷新，见 refreshRealtimeQuotes）。
    func reevaluateTimer() {
        let decision = RealtimeTimerDecision.evaluate(
            scenePhaseActive: scenePhaseActive,
            isTradingSession: tradingHours?.isTradingSession ?? false,
            isTradeDay: tradingHours?.isTradeDay ?? false,
            authFailed: realtimeAuthFailed
        )
        if decision.quoteTimerOn {
            startRefreshTimer()
        } else {
            stopRefreshTimer()
        }
        if decision.sparklineTimerOn {
            startSparklineTimer()
        } else {
            stopSparklineTimer()
        }
    }

    /// 美股只在正常交易时段轮询。该门与 A 股交易时段、认证状态完全独立。
    func reevaluateUSMarketTimer() {
        if scenePhaseActive && usMarketPhase == "regular" {
            startUSMarketTimer()
        } else {
            stopUSMarketTimer()
        }
    }

    private func startUSMarketTimer() {
        guard usMarketTimerCancellable == nil else { return }
        usMarketTimerCancellable = Timer.publish(
            every: Self.usMarketRefreshIntervalSeconds,
            on: .main,
            in: .common
        )
        .autoconnect()
        .sink { [weak self] _ in
            guard let self, self.scenePhaseActive,
                  self.usMarketPhase == "regular" else { return }
            Task { await self.loadUSMarketData() }
        }
    }

    private func stopUSMarketTimer() {
        usMarketTimerCancellable?.cancel()
        usMarketTimerCancellable = nil
    }

    /// 拉取独立美股快照；逐项失败保留上一条有效值并显式降级为 stale。
    func loadUSMarketData() async {
        guard let bridge else { return }
        do {
            let response = try await Task.detached {
                try bridge.usMarketQuotes(symbols: Self.usMarketCodes)
            }.value
            usMarketQuotesByCode = USMarketQuoteMerge.merge(
                previous: usMarketQuotesByCode,
                incoming: response.quotes
            )
            usMarketPhase = response.marketPhase
            usMarketCoverage = USMarketQuoteMerge.coverage(
                quotes: usMarketQuotesByCode,
                orderedCodes: Self.usMarketCodes
            )
            usMarketUpdatedAt = Date()
            usMarketLastError = response.quotes.allSatisfy {
                $0.status == "unavailable"
            } ? "美股行情源当前不可用" : nil
        } catch {
            usMarketQuotesByCode = USMarketQuoteMerge.merge(
                previous: usMarketQuotesByCode,
                incoming: usMarketQuotesByCode.values.map {
                    var stale = $0
                    stale.status = stale.status == "static" ? "static" : "stale"
                    stale.error = "bridge_failed"
                    return stale
                }
            )
            usMarketCoverage = USMarketQuoteMerge.coverage(
                quotes: usMarketQuotesByCode,
                orderedCodes: Self.usMarketCodes
            )
            usMarketLastError = error.localizedDescription
        }
        reevaluateUSMarketTimer()
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

    private func startSparklineTimer() {
        guard sparklineTimerCancellable == nil else { return }
        sparklineTimerCancellable = Timer.publish(every: Self.sparklineIntervalSeconds, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in self?.onSparklineTick() }
    }

    private func stopSparklineTimer() {
        sparklineTimerCancellable?.cancel()
        sparklineTimerCancellable = nil
    }

    /// 盘后独立 tick：只刷堆叠卡分时（不碰 quote），失败一次不再永久空白（R7/AE3）。
    private func onSparklineTick() {
        guard scenePhaseActive, tradingHours?.isTradeDay ?? false,
              !(tradingHours?.isTradingSession ?? false) else { return }
        Task {
            await refreshRealtimeSparklines(
                displaySymbols: RealtimeMerge.symbolsFromIndexStacks(snapshot?.marketStrip?.indexStacks)
            )
        }
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

    // MARK: - Deep Research

    func loadResearchGoals(selecting goalId: String? = nil) async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        isLoadingResearch = true
        errorMessage = nil
        do {
            let response = try await Task.detached {
                try bridge.agentResearch(action: "list")
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "读取深度研究失败：\(error)"
            } else {
                researchGoals = response.goals
                if let profiles = response.profiles { researchProfiles = profiles }
                let target = goalId
                    ?? selectedResearchGoalId
                    ?? response.goals.first?.goalId
                if let target {
                    await openResearchGoal(target)
                }
            }
        } catch {
            errorMessage = "读取深度研究失败：\(error.localizedDescription)"
        }
        isLoadingResearch = false
    }

    func createResearchGoal(
        objective: String,
        profileId: String = "investment-weekly-v3",
        executionMode: String = "single",
        inputs: [String: String]? = nil
    ) async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        let trimmed = objective.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isLoadingResearch = true
        errorMessage = nil
        let resolvedInputs = inputs ?? Self.defaultResearchInputs()
        do {
            let response = try await Task.detached {
                try bridge.agentResearch(
                    action: "create",
                    clientRequestId: UUID().uuidString,
                    profileId: profileId,
                    executionMode: executionMode,
                    objective: trimmed,
                    inputs: resolvedInputs)
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "创建研究目标失败：\(error)"
            } else if let goal = response.goal {
                researchCandidate = nil
                selectedResearchGoalId = goal.goalId
                ingestResearchDetail(goal)
                beginResearchEventReplay(goalId: goal.goalId)
            } else {
                researchCandidate = nil
                researchGoals = response.goals.isEmpty ? researchGoals : response.goals
                let goalId = response.goals.first?.goalId
                isLoadingResearch = false
                await loadResearchGoals(selecting: goalId)
                return
            }
        } catch {
            errorMessage = "创建研究目标失败：\(error.localizedDescription)"
        }
        isLoadingResearch = false
    }

    static func defaultResearchInputs(referenceDate: Date = Date()) -> [String: String] {
        let calendar = Calendar(identifier: .gregorian)
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        let start = calendar.date(byAdding: .day, value: -6, to: referenceDate)
            ?? referenceDate
        return [
            "date_range": "\(formatter.string(from: start))_to_\(formatter.string(from: referenceDate))",
            "as_of": formatter.string(from: referenceDate),
        ]
    }

    static func defaultInvestmentDailyInputs(referenceDate: Date = Date()) -> [String: String] {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        let day = formatter.string(from: referenceDate)
        return ["trade_date": day, "as_of": day]
    }

    func loadInvestmentAnalysisReports(cadence: String? = nil) async {
        guard let bridge else { return }
        isLoadingResearch = true
        defer { isLoadingResearch = false }
        do {
            let response = try await Task.detached {
                try bridge.agentResearch(
                    action: "list",
                    cadence: cadence,
                    profileIds: ["investment-daily-v1", "investment-weekly-v3"],
                    limit: 100)
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "读取投资分析失败：\(error)"
            } else {
                investmentAnalysisReports = response.reports
            }
        } catch {
            errorMessage = "读取投资分析失败：\(error.localizedDescription)"
        }
    }

    func openInvestmentAnalysisReport(_ goalId: String) async {
        await openResearchGoal(goalId)
    }

    /// 为投资分析创建独立草稿 Goal，并导入用户通过系统文件选择器明确授权的语料。
    /// 这里只登记来源与哈希；没有通过独立 checker 的精判卡时不会启动正式报告。
    func importInvestmentAnalystCorpus(
        at url: URL,
        profileId: String,
        cadence: String
    ) async -> Bool {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return false
        }
        guard ["investment-daily-v1", "investment-weekly-v3"].contains(profileId),
              ["daily", "weekly"].contains(cadence)
        else {
            errorMessage = "不支持的投资分析类型"
            return false
        }
        isLoadingResearch = true
        errorMessage = nil
        defer { isLoadingResearch = false }
        let inputs = profileId == "investment-daily-v1"
            ? Self.defaultInvestmentDailyInputs()
            : Self.defaultResearchInputs()
        let objective = cadence == "daily"
            ? "基于受控分析师语料生成投资分析日报"
            : "基于受控分析师语料生成投资分析周报"
        do {
            let response = try await Task.detached {
                let created = try bridge.agentResearch(
                    action: "create",
                    clientRequestId: UUID().uuidString,
                    profileId: profileId,
                    executionMode: "single",
                    objective: objective,
                    inputs: inputs,
                    origin: "manual",
                    cadence: cadence)
                if let error = created.error, !error.isEmpty {
                    throw NSError(
                        domain: "KSS.InvestmentAnalysis",
                        code: 1,
                        userInfo: [NSLocalizedDescriptionKey: error])
                }
                guard let goalId = created.goal?.goalId, !goalId.isEmpty else {
                    throw NSError(
                        domain: "KSS.InvestmentAnalysis",
                        code: 2,
                        userInfo: [NSLocalizedDescriptionKey: "研究目标创建后未返回 goal_id"])
                }
                do {
                    let imported = try bridge.agentResearch(
                        action: "import_corpus",
                        goalId: goalId,
                        path: url.path)
                    if let error = imported.error, !error.isEmpty {
                        throw NSError(
                            domain: "KSS.InvestmentAnalysis",
                            code: 3,
                            userInfo: [NSLocalizedDescriptionKey: error])
                    }
                } catch {
                    // The source import and Goal creation cross a process boundary. If import
                    // fails, settle the draft explicitly so it cannot appear as runnable work.
                    _ = try? bridge.agentResearch(action: "cancel", goalId: goalId)
                    throw error
                }
                return goalId
            }.value
            selectedResearchGoalId = response
            await openResearchGoal(response)
            return true
        } catch {
            errorMessage = "导入分析师语料失败：\(error.localizedDescription)"
            return false
        }
    }

    func openResearchGoal(_ goalId: String) async {
        guard let bridge else { return }
        selectedResearchGoalId = goalId
        isLoadingResearch = true
        do {
            let response = try await Task.detached {
                try bridge.agentResearch(action: "open", goalId: goalId)
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "打开研究目标失败：\(error)"
            } else if let goal = response.goal {
                ingestResearchDetail(goal)
                if let profiles = response.profiles { researchProfiles = profiles }
                beginResearchEventReplay(goalId: goalId)
            }
        } catch {
            errorMessage = "打开研究目标失败：\(error.localizedDescription)"
        }
        isLoadingResearch = false
    }

    func performResearchAction(_ action: String, taskId: String? = nil) async {
        guard let bridge, let goalId = selectedResearchGoalId else { return }
        let keepsControlsAvailable = action == "start" || action == "resume"
        if !keepsControlsAvailable {
            isLoadingResearch = true
        }
        errorMessage = nil
        if keepsControlsAvailable, var goal = selectedResearchGoal {
            goal.status = "running"
            selectedResearchGoal = goal
            upsertResearchGoal(goal.summary)
        }
        do {
            let response = try await Task.detached {
                try bridge.agentResearch(
                    action: action,
                    clientRequestId: UUID().uuidString,
                    goalId: goalId,
                    taskId: taskId)
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "研究操作失败：\(error)"
            } else if let goal = response.goal {
                ingestResearchDetail(goal)
            }
            if !keepsControlsAvailable {
                isLoadingResearch = false
            }
            await openResearchGoal(goalId)
        } catch {
            errorMessage = "研究操作失败：\(error.localizedDescription)"
            if !keepsControlsAvailable {
                isLoadingResearch = false
            }
        }
    }

    func exportResearchDraft(
        _ artifact: ResearchArtifact,
        destination: String,
        overwrite: Bool = false
    ) async -> Bool {
        guard let bridge, let goalId = selectedResearchGoalId else { return false }
        do {
            let response = try await Task.detached {
                try bridge.agentArtifacts(
                    action: "export_draft",
                    goalId: goalId,
                    artifactId: artifact.artifactId,
                    destination: destination,
                    overwrite: overwrite)
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "导出草稿失败：\(error)"
                return false
            }
            return true
        } catch {
            errorMessage = "导出草稿失败：\(error.localizedDescription)"
            return false
        }
    }

    func publishResearchArtifact(
        _ artifact: ResearchArtifact,
        destination: String,
        overwrite: Bool = true
    ) async -> Bool {
        guard let bridge, let goalId = selectedResearchGoalId else { return false }
        do {
            let response = try await Task.detached {
                try bridge.agentArtifacts(
                    action: "publish",
                    goalId: goalId,
                    artifactId: artifact.artifactId,
                    destination: destination,
                    overwrite: overwrite)
            }.value
            if let error = response.error, !error.isEmpty {
                errorMessage = "发布研究产物失败：\(error)"
                return false
            }
            await openResearchGoal(goalId)
            return true
        } catch {
            errorMessage = "发布研究产物失败：\(error.localizedDescription)"
            return false
        }
    }

    private func upsertResearchGoal(_ goal: ResearchGoalSummary) {
        if let index = researchGoals.firstIndex(where: { $0.goalId == goal.goalId }) {
            researchGoals[index] = goal
        } else {
            researchGoals.insert(goal, at: 0)
        }
    }

    private func ingestResearchDetail(_ goal: ResearchGoalDetail) {
        selectedResearchGoal = goal
        upsertResearchGoal(goal.summary)
        for event in goal.events {
            _ = applyResearchEvent(event)
        }
    }

    private func beginResearchEventReplay(goalId: String) {
        guard let bridge else { return }
        let epoch = UUID()
        researchEventEpoch[goalId] = epoch
        Task.detached { [weak self] in
            guard let self else { return }
            var backoffNanoseconds: UInt64 = 750_000_000
            while !Task.isCancelled {
                let after = await MainActor.run {
                    self.researchEventsByGoal[goalId]?.last?.sequence ?? 0
                }
                var streamError: String?
                bridge.agentResearchEvents(
                    goalId: goalId,
                    afterSequence: after,
                    onEvent: { [weak self] event in
                        Task { @MainActor [weak self] in
                            guard self?.researchEventEpoch[goalId] == epoch else { return }
                            _ = self?.applyResearchEvent(event)
                        }
                    },
                    onEnd: { error in streamError = error })

                let shouldContinue = await MainActor.run { [weak self] in
                    guard let self,
                          self.researchEventEpoch[goalId] == epoch,
                          self.selectedResearchGoalId == goalId
                    else { return false }
                    let terminal = [
                        "completed", "failed", "cancelled", "aborted", "blocked",
                        "budget_limited", "insufficient_evidence", "needs_refresh",
                    ]
                    return !terminal.contains(self.selectedResearchGoal?.status.lowercased() ?? "")
                }
                guard shouldContinue else { return }
                if let streamError {
                    await MainActor.run { [weak self] in
                        guard self?.researchEventEpoch[goalId] == epoch else { return }
                        self?.errorMessage = streamError
                    }
                    backoffNanoseconds = min(backoffNanoseconds * 2, 5_000_000_000)
                } else {
                    backoffNanoseconds = 750_000_000
                }
                try? await Task.sleep(nanoseconds: backoffNanoseconds)
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
        // R6 R6：watchlist 真源在 ContentView @AppStorage，store 只留镜像供
        // refreshRealtimeQuotes 的 priority 采集（每次变更/onAppear 都会走到这里）。
        watchlistSymbols = symbols
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
