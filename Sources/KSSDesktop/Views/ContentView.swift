import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: KSSStore
    @AppStorage("watchlistSymbols") private var watchlistSymbols = "688017.SH,688322.SH"
    @AppStorage("appearanceMode") private var appearanceMode = "dark"
    @AppStorage("sidebarCollapsed") private var sidebarCollapsed = false
    @AppStorage("sidebarOrder") private var sidebarOrder = ""
    @State private var searchText = ""

    private var watchlist: [String] {
        watchlistSymbols
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    /// 用户自定义导航顺序（总览置顶），由 sidebarOrder 解析。
    private var orderedSections: [WorkspaceSection] {
        WorkspaceSection.ordered(from: sidebarOrder)
    }

    /// 把 dragged 移到 target 之前并持久化（总览不可被移动/越过）。
    private func reorderSections(_ dragged: WorkspaceSection, before target: WorkspaceSection) {
        guard dragged != target,
              !WorkspaceSection.pinned.contains(dragged),
              !WorkspaceSection.pinned.contains(target) else { return }
        var items = orderedSections
        guard let from = items.firstIndex(of: dragged) else { return }
        items.remove(at: from)
        guard let to = items.firstIndex(of: target) else { return }
        items.insert(dragged, at: to)
        sidebarOrder = WorkspaceSection.encode(items)
    }

    var body: some View {
        // 自定义两栏布局：NavigationSplitView 不肯把侧栏缩到 ~180pt 以下，
        // 无法做图标栏，故改用 HStack 自管宽度，detail 仍包在 NavigationStack
        // 里以保留窗口工具栏（主题/刷新）。
        HStack(spacing: 0) {
            SidebarView(
                selection: $store.selectedSection,
                collapsed: sidebarCollapsed,
                sections: orderedSections,
                onToggleCollapse: {
                    withAnimation(.easeInOut(duration: 0.2)) { sidebarCollapsed.toggle() }
                },
                onReorder: { dragged, target in
                    withAnimation(.easeInOut(duration: 0.18)) {
                        reorderSections(dragged, before: target)
                    }
                }
            )
            .frame(width: sidebarCollapsed ? 64 : 224)
            .frame(maxHeight: .infinity)
            .background(KSSTheme.canvas)

            Divider().overlay(KSSTheme.hairline)

            NavigationStack {
                detail
                    .background(KSSTheme.canvas)
                    .toolbar {
                        ToolbarItemGroup {
                            if store.isLoading {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            Button {
                                appearanceMode = (appearanceMode == "dark") ? "light" : "dark"
                            } label: {
                                Label("主题", systemImage: appearanceMode == "dark" ? "sun.max" : "moon")
                            }
                            .help(appearanceMode == "dark" ? "切换到亮色" : "切换到暗色")
                            Button {
                                Task { await store.loadSnapshot() }
                            } label: {
                                Label("刷新", systemImage: "arrow.clockwise")
                            }
                        }
                    }
            }
        }
        .frame(minWidth: 1080, minHeight: 720)
        .alert("KSS", isPresented: Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    @ViewBuilder
    private var detail: some View {
        if let snapshot = store.snapshot {
            switch store.selectedSection {
            case .dashboard:
                DashboardView(
                    snapshot: snapshot,
                    onSelectSymbol: { symbol in
                        Task { await store.loadStock(symbol: symbol) }
                        store.selectedSection = .stocks
                    },
                    onOpenSection: { section in store.selectedSection = section }
                )
            case .recommendations:
                RecommendationsView(snapshot: snapshot) { symbol in
                    Task { await store.loadStock(symbol: symbol) }
                    store.selectedSection = .stocks
                }
            case .watchlist:
                StockBrowserView(
                    title: "Watchlist",
                    stocks: snapshot.stocks.filter { watchlist.contains($0.symbol) },
                    selectedSymbol: store.selectedSymbol,
                    detail: store.stockDetail,
                    watchlist: watchlist,
                    searchText: $searchText,
                    onSelect: { symbol in Task { await store.loadStock(symbol: symbol) } },
                    onToggleWatchlist: toggleWatchlist
                )
            case .runbook:
                RunbookView(
                    pythonEnvironment: snapshot.pythonEnvironment,
                    isRunning: store.isRunningTask,
                    results: store.taskResults,
                    scheduledJobs: store.scheduledJobs,
                    scheduledBusy: store.scheduledBusy,
                    onRun: { task in Task { await store.runTask(task) } },
                    onLoadSchedules: { Task { await store.loadScheduledJobs() } },
                    onRerunSchedule: { label in Task { await store.rerunScheduledJob(label) } },
                    onToggleSchedule: { label, enabled in Task { await store.toggleScheduledJob(label, enabled: enabled) } }
                )
            case .reviews:
                ReviewsView(
                    reviews: snapshot.reviews,
                    sectorReviews: snapshot.sectorReviews ?? [],
                    selectedPath: store.selectedReportPath,
                    detail: store.reportDetail,
                    isLoadingDetail: store.isLoadingReport,
                    onSelectReview: { path in Task { await store.loadReport(path: path) } }
                )
            case .backtests:
                BacktestsView(
                    reports: snapshot.backtests,
                    tracking: snapshot.tracking,
                    selectedPath: store.selectedReportPath,
                    detail: store.reportDetail,
                    isLoadingDetail: store.isLoadingReport,
                    onSelectReport: { path in Task { await store.loadReport(path: path) } }
                )
            case .stocks:
                StockBrowserView(
                    title: "Stocks",
                    stocks: snapshot.stocks,
                    selectedSymbol: store.selectedSymbol,
                    detail: store.stockDetail,
                    watchlist: watchlist,
                    searchText: $searchText,
                    onSelect: { symbol in Task { await store.loadStock(symbol: symbol) } },
                    onToggleWatchlist: toggleWatchlist
                )
            case .architecture:
                ArchitectureView()
            }
        } else {
            VStack(spacing: 12) {
                ProgressView()
                Text("Loading KSS workspace")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func toggleWatchlist(_ symbol: String) {
        var symbols = watchlist
        if let index = symbols.firstIndex(of: symbol) {
            symbols.remove(at: index)
        } else {
            symbols.append(symbol)
        }
        watchlistSymbols = symbols.joined(separator: ",")
    }
}
