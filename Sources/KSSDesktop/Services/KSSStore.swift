import Foundation

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

    // MARK: AI 复盘助手聊天态（#4 U4/U5）—— 会话历史归 store，section 切换不丢
    @Published var chatMessages: [ChatMessage] = []
    @Published var isChatStreaming = false
    @Published var chatToolInProgress: String?            // 正在调用的工具名（进度指示）
    @Published var pendingWriteConfirm: PendingWriteConfirm?   // 待人工确认的写（app-modal）
    /// 当前阻塞中的 confirm 闸（后台流式线程持，UI tap 后 resolve）。
    private var activeConfirmGate: ChatConfirmGate?

    private let bridge: BridgeClient?

    init() {
        self.bridge = try? BridgeClient()
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
                    let gate = ChatConfirmGate()
                    DispatchQueue.main.async { [weak self] in
                        guard let self else { gate.resolve(false); return }
                        self.activeConfirmGate = gate
                        let ctx = self.chatMessages.last(where: { $0.role == .assistant })?.text ?? ""
                        self.pendingWriteConfirm = PendingWriteConfirm(
                            callId: frame.callId ?? "", tool: frame.tool ?? "",
                            command: frame.command ?? "", effect: frame.effect ?? "执行写操作",
                            argsText: frame.argsText ?? "", contextLine: ctx)
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
        errorMessage = nil
        do {
            let result = try await Task.detached {
                try bridge.runTask(task)
            }.value
            taskResults.insert(result, at: 0)
            if result.status != "failed" {
                await loadSnapshot()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        runningTasks -= 1
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
