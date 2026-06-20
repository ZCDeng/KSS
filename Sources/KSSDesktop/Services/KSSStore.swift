import Foundation

@MainActor
final class KSSStore: ObservableObject {
    @Published var snapshot: AppSnapshot?
    @Published var selectedSection: WorkspaceSection = .dashboard
    @Published var selectedSymbol: String?
    @Published var selectedReportPath: String?
    @Published var reportDetail: ReportDetail?
    @Published var stockDetail: StockDetail?
    @Published var sectorRotationDetail: HotspotRotationSnapshot?
    @Published var isLoadingSectorRotation = false

    @Published var isLoading = false
    @Published var isLoadingReport = false
    @Published var isRunningTask = false
    @Published var taskResults: [TaskRunResult] = []
    @Published var scheduledJobs: [ScheduledJob] = []
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

    private let bridge: BridgeClient?

    init() {
        self.bridge = try? BridgeClient()
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
        do {
            let detail = try await Task.detached {
                try bridge.stock(symbol: symbol)
            }.value
            self.stockDetail = detail
        } catch {
            errorMessage = error.localizedDescription
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

    func runTask(_ task: KSSTask) async {
        guard let bridge else {
            errorMessage = "Cannot locate KSS project root"
            return
        }
        isRunningTask = true
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
        isRunningTask = false
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

    /// 解析自由文本（名称/代码/OCR 结果）为 ts_code。
    func resolveStocks(_ text: String) async -> [ResolvedStock] {
        guard let bridge else { return [] }
        return (try? await Task.detached { try bridge.resolveStocks(text) }.value) ?? []
    }

    /// 导入并同步：拉取这些代码的日线，完成后刷新快照（新股进入股票池）。
    @discardableResult
    func importStocks(_ codes: [String]) async -> TaskRunResult? {
        guard let bridge, !codes.isEmpty else { return nil }
        isRunningTask = true
        errorMessage = nil
        defer { isRunningTask = false }
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
        let jobs = (try? await Task.detached { try bridge.scheduledJobs() }.value) ?? []
        self.scheduledJobs = jobs
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
