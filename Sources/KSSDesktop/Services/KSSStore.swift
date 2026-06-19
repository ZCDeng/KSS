import Foundation

@MainActor
final class KSSStore: ObservableObject {
    @Published var snapshot: AppSnapshot?
    @Published var selectedSection: WorkspaceSection = .dashboard
    @Published var selectedSymbol: String?
    @Published var selectedReportPath: String?
    @Published var stockDetail: StockDetail?
    @Published var reportDetail: ReportDetail?
    @Published var isLoading = false
    @Published var isLoadingReport = false
    @Published var isRunningTask = false
    @Published var taskResults: [TaskRunResult] = []
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
