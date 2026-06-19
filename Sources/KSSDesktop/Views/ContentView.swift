import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: KSSStore
    @AppStorage("watchlistSymbols") private var watchlistSymbols = "688017.SH,688322.SH"
    @State private var searchText = ""

    private var watchlist: [String] {
        watchlistSymbols
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(
                selection: $store.selectedSection,
                snapshot: store.snapshot,
                watchlist: watchlist
            )
        } detail: {
            detail
                .background(KSSTheme.canvas)
                .toolbar {
                    ToolbarItemGroup {
                        if store.isLoading {
                            ProgressView()
                                .controlSize(.small)
                        }
                        Button {
                            Task { await store.loadSnapshot() }
                        } label: {
                            Label("Refresh", systemImage: "arrow.clockwise")
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
                    onRun: { task in Task { await store.runTask(task) } }
                )
            case .reviews:
                ReviewsView(
                    reviews: snapshot.reviews,
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
