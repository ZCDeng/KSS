import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: KSSStore
    @EnvironmentObject private var themeController: ThemeController
    @Environment(\.kssTheme) private var theme
    @AppStorage("watchlistSymbols") private var watchlistSymbols = "688017.SH,688322.SH"
    @AppStorage("sidebarCollapsed") private var sidebarCollapsed = false
    @Environment(\.scenePhase) private var scenePhase   // U5: Timer lifecycle gate (R14)
    @AppStorage("sidebarOrder") private var sidebarOrder = ""
    @State private var searchText = ""
    /// Seesaw 的专注布局只临时收起全局导航；不能改写用户的持久化偏好。
    @State private var seesawNavigationExpanded = false

    private var effectiveSidebarCollapsed: Bool {
        if store.selectedSection == .aiChat {
            return !seesawNavigationExpanded
        }
        return sidebarCollapsed
    }

    /// Seesaw owns a distinct workspace shell. Do not fade the previous detail
    /// through it, otherwise an outgoing legacy page is briefly visible before
    /// the focused conversation layout is ready.
    private var seesawDetailTransition: AnyTransition {
        if store.selectedSection == .aiChat { return .identity }
        return KSSTheme.fadeThrough
    }

    private var seesawDetailAnimation: Animation? {
        store.selectedSection == .aiChat ? nil : KSSTheme.motionStandard
    }

    private var watchlist: [String] {
        watchlistSymbols
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    /// 给 collect_intraday / mi_signal_pack 等 Python 读者同步自选列表：写 kss.db
    /// watchlist 表（plan 2026-07-12-005 / U15，割接自原先直接写 watchlist_symbols.txt）。
    private func syncWatchlistToStore(_ symbols: [String]) {
        Task { await store.syncWatchlistToDB(symbols) }
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
                collapsed: effectiveSidebarCollapsed,
                sections: orderedSections,
                onToggleCollapse: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        if store.selectedSection == .aiChat {
                            seesawNavigationExpanded.toggle()
                        } else {
                            sidebarCollapsed.toggle()
                        }
                    }
                },
                onReorder: { dragged, target in
                    withAnimation(.easeInOut(duration: 0.18)) {
                        reorderSections(dragged, before: target)
                    }
                },
                badges: SidebarBadgeMapping.badges(
                    selfCheckFailCount: store.selfCheckItems.filter(\.isFail).count
                )
            )
            .frame(width: effectiveSidebarCollapsed ? 64 : 272)
            .frame(maxHeight: .infinity)
            .background(theme.canvas)

            Divider().overlay(theme.hairline)

            NavigationStack {
                ZStack {
                    detail
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .id(store.selectedSection)
                        .transition(seesawDetailTransition)
                }
                .animation(seesawDetailAnimation, value: store.selectedSection)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(theme.canvas)
                .toolbar {
                        // R5 布局：刷新（最高频、独立组）→ 分隔符 → 任务台（纯图标）→ 主题 → 设置。
                        // 状态点已删（R5：新鲜度语义在价格页有完整 badge，工具栏点无用户价值）；
                        // 加载态合并进刷新按钮本身（图标换 spinner + 禁用），避免间歇出现的元素挤动分组布局。
                        ToolbarItemGroup {
                            Button {
                                Task { await store.loadSnapshot() }
                            } label: {
                                if store.isLoading {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Label("刷新", systemImage: "arrow.clockwise")
                                }
                            }
                            .disabled(store.isLoading)
                            .help("刷新")
                            // Divider() 在这个 ToolbarItemGroup 里渲染成水平短横线而非竖线分隔符（KTD5 预见到的风险），
                            // 改用固定宽度的竖线 Text 代替。分隔数据动作（刷新）与导航/配置组（任务台/主题/设置）。
                            // 架构入口（plan 2026-07-12-005 U2）已移到侧边栏页脚与 GitHub 并排，不再占工具栏位。
                            // Seesaw 不在工具栏——它是全应用唯一的 AI 入口，改成侧边栏里常驻的 Post 式大按钮
                            // （SidebarView.seesawCTA），比工具栏小图标更醒目。
                            Text("|")
                                .foregroundStyle(theme.textSecondary.opacity(0.4))
                                .padding(.horizontal, 2)
                            Button {
                                store.selectedSection = .runbook
                            } label: {
                                Label(WorkspaceSection.runbook.displayName, systemImage: WorkspaceSection.runbook.symbol)
                                    .labelStyle(.iconOnly)
                            }
                            .foregroundStyle(store.selectedSection == .runbook ? theme.accent : theme.textSecondary)
                            .help(WorkspaceSection.runbook.displayName)
                            themeMenu
                            Button {
                                store.selectedSection = .settings
                            } label: {
                                Label(WorkspaceSection.settings.displayName, systemImage: WorkspaceSection.settings.symbol)
                                    .labelStyle(.titleAndIcon)
                                    .font(KSSFont.themed(12.5, .semibold, theme: theme))
                            }
                            .foregroundStyle(store.selectedSection == .settings ? theme.accent : theme.textSecondary)
                            .help(WorkspaceSection.settings.displayName)
                        }
                    }
            }
        }
        .frame(minWidth: 1080, minHeight: 720)
        .onAppear { syncWatchlistToStore(watchlist) }
        .onChange(of: scenePhase) { _, phase in store.updateSceneActive(phase == .active) }   // U5: Timer lifecycle gate
        .overlay(alignment: .top) {
            if store.showSelfCheckBanner {
                SelfCheckBanner(
                    items: store.selfCheckItems.filter { $0.isFail },
                    isBusy: store.isRunningSelfCheck || store.isReinitializingRuntime,
                    onDismiss: { store.dismissSelfCheckBanner() },
                    onOpenSettings: { item in
                        store.openSettings(category: SettingsTabRouting.targetCategory(forSelfCheckItem: item))
                    },
                    onReinitRuntime: { Task { await store.reinitializeRuntime() } }
                )
                .padding(.top, 12)
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.25), value: store.showSelfCheckBanner)
        .overlay(alignment: .bottom) {
            if let sym = store.importingSymbol {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("正在导入 \(sym) … 拉取日线并加入股票池")
                        .font(KSSFont.themed(13, .semibold, theme: theme))
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

    /// 工具栏「主题」入口：只读当前摘要 + 模式区(新版/经典版) + 设计系统区(仅经典版展开) + 外观区。
    /// 选中状态由文本/勾选表达，不依赖颜色；明确的亮/暗条目取代了原二元切换按钮。
    private var themeMenu: some View {
        Menu {
            Section("当前：\(themeController.summary)") {
                EmptyView()
            }
            Section("模式") {
                ForEach(KSSUIGeneration.allCases) { generation in
                    Button {
                        themeController.select(generation: generation)
                    } label: {
                        Label(generation.displayName,
                              systemImage: themeController.uiGeneration == generation ? "checkmark" : "")
                    }
                }
            }
            if themeController.uiGeneration == .classic {
                Section("设计系统") {
                    ForEach(KSSDesignSystem.classicCases) { system in
                        Button {
                            themeController.select(system: system)
                        } label: {
                            Label(system.displayName,
                                  systemImage: themeController.designSystem == system ? "checkmark" : "")
                        }
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
        // Seesaw and the report archive have independent sidecar hydration.
        // They must not wait for the dashboard snapshot, otherwise startup
        // briefly exposes the old data workspace before the home appears.
        if store.selectedSection == .aiChat {
            AIChatView(globalNavigationExpanded: $seesawNavigationExpanded)
        } else if store.selectedSection == .investmentAnalysis {
            InvestmentAnalysisView(store: store)
        } else if let snapshot = store.snapshot {
            switch store.selectedSection {
            case .dashboard:
                DashboardView(
                    snapshot: snapshot,
                    onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } },
                    onOpenSection: { section in
                        // 唯一调用点是缺 Tushare 凭证卡（R3：Dashboard 入口落密钥 tab）。
                        if section == .settings { store.openSettings(category: .selfCheck) }
                        store.selectedSection = section
                    },
                    tushareConfigured: store.isCredentialConfigured("tushare"),
                    realtimeQuote: store.realtimeQuote,
                    realtimeQuotes: store.realtimeQuotesBySymbol,
                    realtimeSparklines: store.realtimeSparklinesBySymbol,
                    realtimeReceivedAtBySymbol: store.realtimeReceivedAtBySymbol,
                    tradingHours: store.tradingHours,
                    realtimeAuthFailed: store.realtimeAuthFailed,
                    realtimeUpdatedAt: store.realtimeUpdatedAt,
                    onLoadRealtime: { Task { await store.loadRealtimeData() } },
                    onRetryRealtime: { Task { await store.retryRealtime() } },
                    usMarketQuotes: store.usMarketQuotesByCode,
                    usMarketPhase: store.usMarketPhase,
                    usMarketCoverage: store.usMarketCoverage,
                    usMarketUpdatedAt: store.usMarketUpdatedAt,
                    onLoadUSMarket: {
                        Task { await store.loadUSMarketData() }
                    },
                    onReloadSnapshot: {
                        Task { await store.loadSnapshot() }
                    },
                    bridge: store.bridge,
                    onOpenSurfaceAI: { region in
                        store.seedSurfaceAIPrefill(region: region)
                        store.selectedSection = .aiChat
                    }
                )
            case .recommendations:
                RecommendationsView(
                    snapshot: snapshot,
                    onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } },
                    realtimeQuotes: store.realtimeQuotesBySymbol,
                    realtimeReceivedAtBySymbol: store.realtimeReceivedAtBySymbol,
                    tradingHours: store.tradingHours,
                    realtimeAuthFailed: store.realtimeAuthFailed,
                    realtimeUpdatedAt: store.realtimeUpdatedAt,
                    onRetryRealtime: { Task { await store.retryRealtime() } },
                    onLoadRealtime: {
                        let syms = RealtimeMerge.symbolsFromRecommendations(snapshot.recommendations)
                        Task { await store.refreshRealtimeQuotes(symbols: syms) }
                    }
                )
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
                    onToggleWatchlist: toggleWatchlist,
                    bridge: store.bridge,
                    realtimeQuotes: store.realtimeQuotesBySymbol,
                    realtimeReceivedAtBySymbol: store.realtimeReceivedAtBySymbol,
                    tradingHours: store.tradingHours,
                    realtimeAuthFailed: store.realtimeAuthFailed,
                    realtimeUpdatedAt: store.realtimeUpdatedAt,
                    onRetryRealtime: { Task { await store.retryRealtime() } },
                    onLoadRealtimeForSymbol: { sym in Task { await store.loadRealtimeData(symbol: sym) } },
                    isRunningTask: store.isUpdatingCsData,
                    onUpdateCsData: { Task { await updateCsDataAndRefreshDetail() } }
                )
            case .runbook:
                RunbookView(
                    store: store,
                    pythonEnvironment: snapshot.pythonEnvironment,
                    isRunning: store.isRunningTask,
                    results: store.taskResults,
                    onRun: { task in Task { await store.runTask(task) } }
                )
            case .themes:
                ThemesView(
                    themes: store.themeLeaders,
                    onLoad: { Task { await store.loadThemeLeaders() } },
                    onSelectSymbol: { symbol in Task { await store.selectStock(symbol) } },
                    realtimeQuotes: store.realtimeQuotesBySymbol,
                    realtimeReceivedAtBySymbol: store.realtimeReceivedAtBySymbol,
                    tradingHours: store.tradingHours,
                    realtimeAuthFailed: store.realtimeAuthFailed,
                    realtimeUpdatedAt: store.realtimeUpdatedAt,
                    onRetryRealtime: { Task { await store.retryRealtime() } },
                    onLoadRealtime: {
                        let syms = RealtimeMerge.symbolsFromThemes(store.themeLeaders)
                        Task { await store.refreshRealtimeQuotes(symbols: syms) }
                    }
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
            case .investmentAnalysis:
                EmptyView()
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
                    onToggleWatchlist: toggleWatchlist,
                    bridge: store.bridge,
                    realtimeQuotes: store.realtimeQuotesBySymbol,
                    realtimeReceivedAtBySymbol: store.realtimeReceivedAtBySymbol,
                    tradingHours: store.tradingHours,
                    realtimeAuthFailed: store.realtimeAuthFailed,
                    realtimeUpdatedAt: store.realtimeUpdatedAt,
                    onRetryRealtime: { Task { await store.retryRealtime() } },
                    onLoadRealtimeForSymbol: { sym in Task { await store.loadRealtimeData(symbol: sym) } },
                    isRunningTask: store.isUpdatingCsData,
                    onUpdateCsData: { Task { await updateCsDataAndRefreshDetail() } }
                )
            case .aiChat:
                EmptyView()
            case .newsDigest:
                IntelView()
                    .environmentObject(store)
            case .architecture:
                ArchitectureView()
            case .settings:
                SettingsView()
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
        syncWatchlistToStore(result.list)
        // U5: 仅「加入」分支触发即时复盘（取消不触发）。
        if result.generate {
            Task { await store.generateReview(for: symbol) }
        }
    }

    /// 全池日更后刷新 snapshot + 当前详情（runTask 成功时已 loadSnapshot）。
    private func updateCsDataAndRefreshDetail() async {
        // 任意形式任务在跑则不重复开火（含其它 Runbook 任务）
        guard !store.isRunningTask else { return }
        await store.runTask(.updateCsData)
        // 用当前选中票刷新，不强制回日更开始时的 symbol（避免长任务中途换票被拽回）
        if let symbol = store.selectedSymbol {
            await store.selectStock(symbol, navigate: false)
        }
        // 刷新 reference_trade_date 锚点（日更后可能跨过 18:00 或新 bar 日）
        _ = await store.loadTradingHours()
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
