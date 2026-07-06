import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: KSSStore
    @EnvironmentObject private var themeController: ThemeController
    @Environment(\.kssTheme) private var theme
    @AppStorage("watchlistSymbols") private var watchlistSymbols = "688017.SH,688322.SH"
    @AppStorage("sidebarCollapsed") private var sidebarCollapsed = false
    @AppStorage("sidebarOrder") private var sidebarOrder = ""
    @State private var searchText = ""
    @State private var showNetworkSettings = false

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
            .background(theme.canvas)

            Divider().overlay(theme.hairline)

            NavigationStack {
                ZStack {
                    detail
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .id(store.selectedSection)
                        .transition(KSSTheme.fadeThrough)
                }
                .animation(KSSTheme.motionStandard, value: store.selectedSection)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(theme.canvas)
                .toolbar {
                        ToolbarItemGroup {
                            if store.isLoading {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            themeMenu
                            Button {
                                showNetworkSettings = true
                            } label: {
                                Label("网络与凭据", systemImage: "key.fill")
                            }
                            Button {
                                Task { await store.loadSnapshot() }
                            } label: {
                                Label("刷新", systemImage: "arrow.clockwise")
                            }
                        }
                    }
            }
        }
        .sheet(isPresented: $showNetworkSettings) {
            NetworkSettingsView()
        }
        .frame(minWidth: 1080, minHeight: 720)
        .overlay(alignment: .bottom) {
            if let sym = store.importingSymbol {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("正在导入 \(sym) … 拉取日线并加入股票池")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(theme.textPrimary)
                }
                .padding(.horizontal, 16).padding(.vertical, 11)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
                .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeL).stroke(theme.hairline))
                .shadow(color: .black.opacity(0.18), radius: 12, y: 4)
                .padding(.bottom, 24)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.25), value: store.importingSymbol)
        .alert("KSS", isPresented: Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.errorMessage ?? "")
        }
        // U5：人在环内写确认 app-modal（sheet 阻塞窗口；dismiss=拒，gate 幂等）。
        .sheet(item: $store.pendingWriteConfirm,
               onDismiss: { store.resolveWriteConfirm(approved: false) }) { pending in
            WriteConfirmView(pending: pending) { approved in
                store.resolveWriteConfirm(approved: approved)
            }
        }
    }

    /// 工具栏「主题」入口：只读当前摘要 + 设计系统区 + 外观区，全部带勾选状态。
    /// 选中状态由文本/勾选表达，不依赖颜色；明确的亮/暗条目取代了原二元切换按钮。
    private var themeMenu: some View {
        Menu {
            Section("当前：\(themeController.summary)") {
                EmptyView()
            }
            Section("设计系统") {
                ForEach(KSSDesignSystem.allCases) { system in
                    Button {
                        themeController.select(system: system)
                    } label: {
                        Label(system.displayName,
                              systemImage: themeController.designSystem == system ? "checkmark" : "")
                    }
                }
            }
            Section("外观") {
                ForEach(KSSAppearance.allCases) { appearance in
                    Button {
                        themeController.select(appearance: appearance)
                    } label: {
                        Label(appearance.displayName,
                              systemImage: themeController.appearance == appearance ? "checkmark" : "")
                    }
                }
            }
        } label: {
            Label("主题", systemImage: "paintpalette")
        }
        .help("设计系统与外观：\(themeController.summary)")
        .accessibilityLabel("主题")
        .accessibilityValue(themeController.summary)
    }

    @ViewBuilder
    private var detail: some View {
        if let snapshot = store.snapshot {
            switch store.selectedSection {
            case .dashboard:
                DashboardView(
                    snapshot: snapshot,
                    onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } },
                    onOpenSection: { section in store.selectedSection = section }
                )
            case .recommendations:
                RecommendationsView(snapshot: snapshot) { symbol in
                    Task { await store.selectStock(symbol) }
                }
            case .watchlist:
                StockBrowserView(
                    title: "Watchlist",
                    stocks: snapshot.stocks.filter { watchlist.contains($0.symbol) },
                    selectedSymbol: store.selectedSymbol,
                    detail: store.stockDetail,
                    enrichment: store.perillaEnrichment,
                    watchlist: watchlist,
                    searchText: $searchText,
                    onSelect: { symbol in Task { await store.selectStock(symbol, navigate: false) } },
                    onToggleWatchlist: toggleWatchlist
                )
            case .runbook:
                RunbookView(
                    pythonEnvironment: snapshot.pythonEnvironment,
                    isRunning: store.isRunningTask,
                    results: store.taskResults,
                    scheduledJobs: store.scheduledJobs,
                    categoryOrder: store.cronCategoryOrder,
                    scheduledBusy: store.scheduledBusy,
                    scheduledBatchBusy: store.scheduledBatchBusy,
                    scheduledBatchNote: store.scheduledBatchNote,
                    onRun: { task in Task { await store.runTask(task) } },
                    onLoadSchedules: { Task { await store.loadScheduledJobs() } },
                    onRerunSchedule: { label in Task { await store.rerunScheduledJob(label) } },
                    onToggleSchedule: { label, enabled in Task { await store.toggleScheduledJob(label, enabled: enabled) } },
                    onSyncSchedule: { label in Task { await store.syncScheduledJobs(label) } },
                    onCatchUp: { Task { await store.catchUpStaleJobs() } },
                    onRerunMany: { labels in Task { await store.rerunScheduledJobs(labels) } },
                    onDismissBatchNote: { store.scheduledBatchNote = nil }
                )
            case .themes:
                ThemesView(
                    themes: store.themeLeaders,
                    onLoad: { Task { await store.loadThemeLeaders() } },
                    onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } }
                )
            case .trends:
                TrendsView(
                    month: store.trendMonth,
                    detail: store.trendDayDetail,
                    selectedDate: store.selectedTrendDate,
                    loading: store.trendsLoading,
                    onLoadMonth: { m in Task { await store.loadTrendsMonth(m) } },
                    onSelectDay: { d in Task { await store.loadTrendsDay(d) } },
                    onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } }
                )
            case .reviews:
                ReviewsView(
                    reviews: snapshot.reviews,
                    sectorReviews: snapshot.sectorReviews ?? [],
                    sectorRotationHistory: snapshot.sectorRotationHistory ?? [],
                    sectorRotationDetail: store.sectorRotationDetail,
                    isLoadingSectorRotation: store.isLoadingSectorRotation,
                    selectedPath: store.selectedReportPath,
                    detail: store.reportDetail,
                    isLoadingDetail: store.isLoadingReport,
                    onSelectReview: { path in Task { await store.loadReport(path: path) } },
                    onSelectSectorRotationDate: { date in Task { await store.loadSectorRotation(date: date) } },
                    onOpenExternally: { path in store.openReportInMarkEdit(path: path) }
                )
            case .newsDigest:
                NewsDigestView(
                    newsDigest: store.newsDigest,
                    isLoadingNewsDigest: store.isLoadingNewsDigest,
                    onSelectNewsDigest: { date, scene in Task { await store.loadNewsDigest(date: date, scene: scene) } }
                )
            case .backtests:
                BacktestsView(
                    reports: snapshot.backtests,
                    tracking: snapshot.tracking,
                    selectedPath: store.selectedReportPath,
                    detail: store.reportDetail,
                    isLoadingDetail: store.isLoadingReport,
                    onSelectReport: { path in Task { await store.loadReport(path: path) } },
                    onOpenExternally: { path in store.openReportInMarkEdit(path: path) }
                )
            case .stocks:
                StockBrowserView(
                    title: "Stocks",
                    stocks: snapshot.stocks,
                    selectedSymbol: store.selectedSymbol,
                    detail: store.stockDetail,
                    enrichment: store.perillaEnrichment,
                    watchlist: watchlist,
                    searchText: $searchText,
                    onSelect: { symbol in Task { await store.selectStock(symbol, navigate: false) } },
                    onToggleWatchlist: toggleWatchlist
                )
            case .aiChat:
                AIChatView()
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
        let result = WatchlistToggle.apply(watchlist, toggling: symbol)
        // watchlist 先持久化，生成失败不回滚自选。
        watchlistSymbols = result.list.joined(separator: ",")
        // U5: 仅「加入」分支触发即时复盘（取消不触发）。
        if result.generate {
            Task { await store.generateReview(for: symbol) }
        }
    }
}

/// 自选列表 toggle 的纯决策（可单测）：加入返回 generate=true，取消返回 false。
enum WatchlistToggle {
    static func apply(_ current: [String], toggling symbol: String) -> (list: [String], generate: Bool) {
        var symbols = current
        if let index = symbols.firstIndex(of: symbol) {
            symbols.remove(at: index)
            return (symbols, false)   // 取消不触发生成
        }
        symbols.append(symbol)
        return (symbols, true)        // 加入触发即时复盘
    }
}
