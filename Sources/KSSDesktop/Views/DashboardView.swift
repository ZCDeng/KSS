import SwiftUI

struct DashboardView: View {
    @Environment(\.kssTheme) private var theme
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void
    var onOpenSection: (WorkspaceSection) -> Void
    /// nil＝自检尚未跑完（未知，不判定）；false＝明确未配置（U9/R12，AE1）。
    var tushareConfigured: Bool? = nil
    // U2 实时接线：页面加载触发 Longbridge 实时拉取，展示新鲜度徽标。
    var realtimeQuote: LongbridgeQuote? = nil
    var realtimeQuotes: [String: LongbridgeQuote] = [:]
    /// 堆叠卡 live 分时（产品码 → 1m 收盘）
    var realtimeSparklines: [String: SparklineSeries] = [:]
    /// 按标的记录的本地接收时间，供 sourceAsofTs 缺失时的新鲜度回退（KTD1，逐标的隔离）
    var realtimeReceivedAtBySymbol: [String: Date] = [:]
    var tradingHours: TradingHours? = nil
    var realtimeAuthFailed: Bool = false
    var realtimeUpdatedAt: Date? = nil
    var onLoadRealtime: () -> Void = {}
    var onRetryRealtime: () -> Void = {}
    var usMarketQuotes: [String: USMarketQuote] = [:]
    var usMarketPhase: String? = nil
    var usMarketCoverage: USMarketCoverage? = nil
    var usMarketUpdatedAt: Date? = nil
    var onLoadUSMarket: () -> Void = {}
    /// surface 配置变更后重载 snapshot（由 Store 注入）
    var onReloadSnapshot: () -> Void = {}
    var bridge: BridgeClient? = nil
    /// 打开 Seesaw 并预填 surface region 上下文（U5 / R10）
    var onOpenSurfaceAI: (String) -> Void = { _ in }
    /// 自选星标（真源 ContentView AppStorage）
    var watchlist: [String] = []
    var onToggleWatchlist: (String) -> Void = { _ in }
    /// 可投资地图：两张信号卡的色点、区位标与就地补标（plan U6/U7）。
    var exposure = ExposureContext()

    // Material 3 响应式栅格：统一外边距 / 沟槽，内容封顶居中，断点决定主区列数。
    private let margin: CGFloat = 24
    private let gutter: CGFloat = 20
    private let sectionSpacing: CGFloat = 22
    private let maxContent: CGFloat = 1040

    /// 页头 badge：**核心展示集合**（堆叠卡+首行 ETF）的最差新鲜度（R6 R9/KTD6）。
    /// 全量 harvest 口径曾让任一低频外围标的把刚刷新的页头拖成「已过期」；
    /// 外围卡（指数一览/板块/推荐现价）自带逐标的口径，不参与页头汇总。
    private var displayedFreshness: RealtimeFreshness {
        RealtimeMerge.worstFreshness(
            symbols: RealtimeMerge.coreDisplaySymbols(strip: snapshot.marketStrip),
            quotes: realtimeQuotes,
            receivedAtBySymbol: realtimeReceivedAtBySymbol
        )
    }

    /// 诊断 tooltip（R6 U8）：悬停页头徽标可见最陈旧标的与落后秒数，误报排障不用翻日志。
    private var freshnessDiagnostic: String? {
        guard let worst = RealtimeMerge.stalestSymbol(
            symbols: RealtimeMerge.coreDisplaySymbols(strip: snapshot.marketStrip),
            quotes: realtimeQuotes,
            receivedAtBySymbol: realtimeReceivedAtBySymbol
        ) else { return nil }
        return String(format: "最旧: %@ · %.0fs", worst.symbol, worst.ageSeconds)
    }

    private var usMarketHeaderStatus: USMarketHeaderStatus {
        USMarketQuoteMerge.headerStatus(usMarketCoverage, phase: usMarketPhase)
    }

    private var usMarketHeaderText: String {
        guard let usMarketUpdatedAt else { return usMarketHeaderStatus.text }
        return "\(usMarketHeaderStatus.text) · \(usMarketUpdatedAt.formatted(date: .omitted, time: .shortened))"
    }

    var body: some View {
        GeometryReader { geo in
            let contentW = min(geo.size.width - margin * 2, maxContent)
            ScrollView {
                VStack(alignment: .leading, spacing: sectionSpacing) {
                    HStack(alignment: .top) {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            PageTitle("盯盘")
                            RealtimeStatusBadge(
                                freshness: displayedFreshness,
                                hours: tradingHours,
                                authFailed: realtimeAuthFailed,
                                updatedAt: realtimeUpdatedAt,
                                onRetry: onRetryRealtime,
                                style: .pageHeader
                            )
                            .help(freshnessDiagnostic ?? "")
                        }
                        Spacer(minLength: 16)
                        EditorialDateView()
                    }

                    // 缺 Tushare 凭证 + 股票池确实为空 → 明确指引（U9/R12，AE1），不是静默空白。
                    // 凭证已配但数据加载失败走既有错误路径，不用这张卡（两种情况不能混淆）。
                    if tushareConfigured == false, snapshot.stocks.isEmpty {
                        MissingCredentialCard(sourceDisplayName: "Tushare Token") {
                            onOpenSection(.settings)
                        }
                    }

                    // 第一行：市场速览（ETF / 北向 / 指标小卡 + Sparkle）。
                    // 只要有 marketStrip 就渲染（指标卡始终带 Sparkle），勿因 etf/北向暂空整行消失。
                    if let strip = snapshot.marketStrip {
                        MarketStripRow(
                            strip: strip,
                            quotes: realtimeQuotes,
                            bridge: bridge,
                            onReloadSnapshot: onReloadSnapshot,
                            onOpenAIWithRegion: onOpenSurfaceAI
                        )
                    }

                    // 第二行：三列指数堆叠（主板 / 成长 / 港股）+ Longbridge 实盘 + 分时
                    if let stacks = snapshot.marketStrip?.indexStacks, !stacks.isEmpty {
                        IndexStackRow(
                            stacks: stacks,
                            quotes: realtimeQuotes,
                            liveSparklines: realtimeSparklines
                        )
                    } else if let indices = snapshot.marketStrip?.indices, !indices.isEmpty {
                        // 兼容旧 strip（无 indexStacks）
                        MarketIndexRow(indices: indices, quotes: realtimeQuotes)
                    }

                    // 指数跑马灯：紧贴指数行下方，无标题，13 指数按涨跌幅排序滚动
                    if let board = snapshot.marketStrip?.indexBoard, !board.isEmpty {
                        IndexMarquee(indices: board, quotes: realtimeQuotes)
                    }

                    // 隔夜美股：标题行始终可管理（+ / AI）；跑马灯仅有报价时显示
                    OvernightUSSection(
                        overnight: snapshot.marketStrip?.overnightUS ?? [],
                        surfaceConfig: snapshot.marketStrip?.surfaceConfig,
                        usMarketHeaderText: usMarketHeaderText,
                        usMarketHeaderStatus: usMarketHeaderStatus,
                        usMarketQuotes: usMarketQuotes,
                        bridge: bridge,
                        onOpenAI: { onOpenSurfaceAI("overnight_us") },
                        onReloadSnapshot: onReloadSnapshot
                    )

                    if let pulse = snapshot.sectorReviews?.first, !pulse.themes.isEmpty {
                        SectorPulseStrip(pulse: pulse, quotes: realtimeQuotes)
                    }

                    mainRow(contentW: contentW)

                    if let picks = snapshot.perillaPicks, !picks.isEmpty {
                        SectionHeader("紫苏叶结构候选", caption: "🌿 供应链研究 overlay · 不参与交易信号加权 · 核心/国产替代主线分层 · 点击看个股")
                        PerillaPicksTable(
                            items: picks,
                            watchlist: watchlist,
                            onSelect: onSelectSymbol,
                            onToggleWatchlist: onToggleWatchlist,
                            exposure: exposure
                        )
                    }

                    if let scan = snapshot.bjScan {
                        SectionHeader("北证 50 扫描", caption: "扫描表评分 Top 标的 · 点击看个股")
                        BJScanSection(
                            scan: scan,
                            watchlist: watchlist,
                            onSelect: onSelectSymbol,
                            onToggleWatchlist: onToggleWatchlist
                        )
                    }

                    // 底部：指数一览（区块级 Sparkle 可增删改）
                    if let board = snapshot.marketStrip?.indexBoard, !board.isEmpty {
                        IndexBoardSection(
                            indices: board,
                            quotes: realtimeQuotes,
                            bridge: bridge,
                            onReloadSnapshot: onReloadSnapshot,
                            onOpenAI: { onOpenSurfaceAI("index_board") }
                        )
                    }
                }
                .frame(width: contentW, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)   // 内容块居中，余量进外边距
                .padding(.vertical, margin)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
        .onAppear {
            onLoadRealtime()
            onLoadUSMarket()
        }
    }

    /// 主区：今日推荐 | 纸交易跟踪。宽屏并排（推荐自适应 + 跟踪定宽），窄屏纵向堆叠。
    @ViewBuilder
    private func mainRow(contentW: CGFloat) -> some View {
        let trackingW = min(max(contentW * 0.32, 300), 360)
        let twoCol = contentW >= 720

        if twoCol {
            HStack(alignment: .top, spacing: gutter) {
                picksColumn.frame(maxWidth: .infinity, alignment: .topLeading)
                trackingColumn.frame(width: trackingW, alignment: .topLeading)
            }
        } else {
            VStack(alignment: .leading, spacing: sectionSpacing) {
                picksColumn
                trackingColumn
            }
        }
    }

    private var picksColumn: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader("今日推荐",
                          caption: snapshot.recommendationSubtitle.map { "\($0) · log_mv 反向低市值 Top 5" }
                              ?? "log_mv 反向选出的低市值 Top 5 · 买入 T+1 开盘")
            TodayPicksList(
                items: Array(snapshot.recommendations.prefix(5)),
                quotes: realtimeQuotes,
                watchlist: watchlist,
                onSelect: onSelectSymbol,
                onToggleWatchlist: onToggleWatchlist,
                exposure: exposure
            )
        }
    }

    private var trackingColumn: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader("纸交易跟踪", caption: "log_mv 策略纸面累计表现")
            TrackingSummaryCard(tracking: snapshot.tracking)
            CountCard(icon: "chart.xyaxis.line", count: snapshot.backtests.count, unit: "份", label: "AI回测") {
                onOpenSection(.backtests)
            }
        }
    }
}

/// 今日推荐：固定列宽的对齐表格（排名 / 名称 / 代码 / 行业 / 状态 / 权重）。
/// 列宽全部固定，表头与每一行共用，保证网格逐列对齐；代码与行业拆成独立列填满版面，
/// 消除名称与右侧之间的大片留白。
enum TodayPickSort: Hashable {
    case rank, name, symbol, industry, status, price, open, close
}

struct TodayPicksList: View {
    @Environment(\.kssTheme) private var theme
    var items: [Recommendation]
    var quotes: [String: LongbridgeQuote] = [:]
    var watchlist: [String] = []
    var onSelect: (String) -> Void
    var onToggleWatchlist: (String) -> Void = { _ in }
    /// 可投资地图暴露数据与就地补标（plan U6/U7）。
    var exposure = ExposureContext()

    @State private var sortKey: TodayPickSort = .rank
    @State private var ascending = false

    private let wRank: CGFloat = 36
    private let wName: CGFloat = 104
    private let wSymbol: CGFloat = 88
    private let wStatus: CGFloat = 76
    private let wOpen: CGFloat = 60
    private let wClose: CGFloat = 60
    private let wWeight: CGFloat = 68
    private let colSpacing: CGFloat = 12
    private let rowPadH: CGFloat = 14

    private var sortedItems: [Recommendation] {
        let asc = ascending
        switch sortKey {
        case .rank:
            // 默认（asc=false）即 #1→#5，与原视觉一致；排名列无可点列头，asc 不会再切
            return items.sorted { asc ? $0.rank > $1.rank : $0.rank < $1.rank }
        case .name:
            return items.sorted {
                let r = $0.name.localizedCompare($1.name)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .symbol:
            return items.sorted {
                let r = $0.symbol.localizedCompare($1.symbol)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .industry:
            return items.sorted {
                let r = $0.industry.localizedCompare($1.industry)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .status:
            return items.sorted {
                let r = $0.status.localizedCompare($1.status)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .price:
            return items.sorted { byNumber(displayPrice($0)?.close, displayPrice($1)?.close, asc: asc) }
        case .open:
            return items.sorted { byNumber($0.latestOpen, $1.latestOpen, asc: asc) }
        case .close:
            return items.sorted { byNumber($0.latestClose, $1.latestClose, asc: asc) }
        }
    }

    /// 现价列（R5，替代权重列——等权 20% 无信息量）：盘中 Longbridge 实时价，
    /// 盘后/无 quote 回退快照收盘（非实时时中性着色）。
    private func displayPrice(_ item: Recommendation) -> (close: Double, pct: Double, isLive: Bool)? {
        RealtimeMerge.displayPrice(
            snapshotClose: item.latestClose,
            snapshotPct: nil,
            quote: quotes[item.symbol.uppercased()]
        )
    }

    /// 价格显示：两位小数，缺失显示「—」。
    private func priceText(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.2f", v)
    }

    /// 数值列比较：降序大在前、升序小在前，nil 恒排末尾。
    private func byNumber(_ a: Double?, _ b: Double?, asc: Bool) -> Bool {
        switch (a, b) {
        case let (x?, y?): return asc ? x < y : x > y
        case (nil, _?): return false
        case (_?, nil): return true
        case (nil, nil): return false
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(theme.hairline)
            let rows = sortedItems
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                HStack(spacing: 0) {
                    Button { onSelect(item.symbol) } label: { row(item) }
                        .buttonStyle(.plain)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    // 就地补标灰点：行按钮的兄弟节点（嵌套会被行点击吞掉）。
                    ExposureMarkButton(
                        exposure: exposure.stock(item.symbol),
                        loaded: exposure.loaded
                    ) { exposure.onMark(item.symbol) }
                    WatchlistStarButton(
                        isWatched: watchlist.contains(item.symbol)
                    ) { onToggleWatchlist(item.symbol) }
                    .padding(.trailing, rowPadH)
                }
                if index < rows.count - 1 {
                    Divider().overlay(theme.hairline)
                }
            }
        }
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }

    private var header: some View {
        HStack(spacing: colSpacing) {
            Text("排名").frame(width: wRank, alignment: .leading)
            SortHeaderCell(title: "名称", key: TodayPickSort.name, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wName)
            SortHeaderCell(title: "代码", key: TodayPickSort.symbol, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wSymbol)
            SortHeaderCell(title: "行业", key: TodayPickSort.industry, selection: $sortKey, ascending: $ascending,
                           alignment: .leading)
            SortHeaderCell(title: "状态", key: TodayPickSort.status, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wStatus)
            SortHeaderCell(title: "开盘", key: TodayPickSort.open, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wOpen)
            SortHeaderCell(title: "收盘", key: TodayPickSort.close, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wClose)
            SortHeaderCell(title: "现价", key: TodayPickSort.price, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wWeight)
            Color.clear.frame(width: 24)   // 未上图灰点
            Color.clear.frame(width: 28)
        }
        .font(KSSFont.themed(10.5, .medium, theme: theme))
        .tracking(0.5)
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func row(_ item: Recommendation) -> some View {
        HStack(spacing: colSpacing) {
            Text("#\(item.rank)")
                .font(.system(size: 15, weight: .heavy, design: .monospaced))
                .foregroundStyle(theme.accent)
                .frame(width: wRank, alignment: .leading)
            Text(item.name.isEmpty ? item.symbol : item.name)
                .font(KSSFont.themed(14.5, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
                .frame(width: wName, alignment: .leading)
            Text(item.symbol)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
                .frame(width: wSymbol, alignment: .leading)
            // 行业列是弹性列：把暴露徽标挂在它下面，不动任何固定列宽。
            VStack(alignment: .leading, spacing: 2) {
                Text(item.industry.isEmpty ? "—" : item.industry)
                    .font(KSSFont.themed(12.5, theme: theme))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(1)
                ExposureBadge(exposure: exposure.stock(item.symbol),
                              loaded: exposure.loaded, showsDotWhenUnlabelled: false)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            StatusBadge.tracking(item.status)
                .frame(width: wStatus, alignment: .leading)
            Text(priceText(item.latestOpen))
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(theme.textBody)
                .lineLimit(1)
                .frame(width: wOpen, alignment: .trailing)
            Text(priceText(item.latestClose))
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
                .frame(width: wClose, alignment: .trailing)
            priceCell(item)
                .frame(width: wWeight, alignment: .trailing)
        }
        .contentShape(Rectangle())
        .padding(.leading, rowPadH)
        .padding(.vertical, 11)
    }

    /// 现价单元格：实时价按涨跌着色（对昨收），回退快照收盘时中性色。
    @ViewBuilder
    private func priceCell(_ item: Recommendation) -> some View {
        if let disp = displayPrice(item) {
            Text(priceText(disp.close))
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(disp.isLive ? theme.signColor(disp.pct) : theme.textSecondary)
                .lineLimit(1)
        } else {
            Text("—")
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
        }
    }
}

/// 紫苏叶选股表：供应链护城河评分 + 日/周/月/年涨幅 + PE/PB + 流通市值（全宽）。
/// 名称下挂代码、产业链下挂层级·护城河，省出横向空间给行情/估值列。
enum PerillaSort: Hashable {
    case none, name, chains, ret1d, ret5d, ret20d, retYear, pe, mv, score
}

/// 紫苏叶分层 Tab：core=核心垄断/双寡头 · main=国产替代主线（三家寡头深链）。
enum PerillaTier: String, CaseIterable, Identifiable {
    case core = "核心"
    case main = "国产替代主线"
    var id: String { rawValue }
    var key: String { self == .core ? "core" : "main" }
}

struct PerillaPicksTable: View {
    @Environment(\.kssTheme) private var theme
    var items: [PerillaPick]
    var watchlist: [String] = []
    var onSelect: (String) -> Void
    var onToggleWatchlist: (String) -> Void = { _ in }
    /// 可投资地图暴露数据与就地补标（plan U6/U7）。
    var exposure = ExposureContext()

    // 默认 .none = 保持 bridge 返回的原始顺序（不打乱当前视觉）
    @State private var sortKey: PerillaSort = .none
    @State private var ascending = false
    @State private var tab: PerillaTier = .core

    private let wName: CGFloat = 116
    private let wRet: CGFloat = 54
    private let wPe: CGFloat = 56
    private let wInst: CGFloat = 126   // 机构持仓动态(机构占比+增减·北向, 两行)
    private let wPeer: CGFloat = 76    // 对标美股(代码+PE)
    private let wMv: CGFloat = 72
    private let wScore: CGFloat = 46
    private let colSpacing: CGFloat = 10
    private let rowPadH: CGFloat = 14

    // 数值列：降序=大在前，nil 排末尾（无论升降）。
    private func byNumber(_ a: Double?, _ b: Double?, asc: Bool) -> Bool {
        switch (a, b) {
        case let (x?, y?): return asc ? x < y : x > y
        case (nil, _?): return false   // nil 永远靠后
        case (_?, nil): return true
        case (nil, nil): return false
        }
    }

    // 当前 Tab 过滤后的标的（tier 缺失兜底归 core，兼容旧 payload）。
    private var tabFiltered: [PerillaPick] {
        items.filter { ($0.tier ?? "core") == tab.key }
    }

    private var sortedItems: [PerillaPick] {
        let asc = ascending
        let items = tabFiltered
        switch sortKey {
        case .none:
            return items
        case .name:
            return items.sorted {
                let r = $0.name.localizedCompare($1.name)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .chains:
            return items.sorted {
                let r = $0.chains.localizedCompare($1.chains)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        case .ret1d:  return items.sorted { byNumber($0.ret1d, $1.ret1d, asc: asc) }
        case .ret5d:  return items.sorted { byNumber($0.ret5d, $1.ret5d, asc: asc) }
        case .ret20d: return items.sorted { byNumber($0.ret20d, $1.ret20d, asc: asc) }
        case .retYear: return items.sorted { byNumber($0.retYear, $1.retYear, asc: asc) }
        case .pe:     return items.sorted { byNumber($0.pe, $1.pe, asc: asc) }
        case .mv:     return items.sorted { byNumber($0.circMvYi, $1.circMvYi, asc: asc) }
        case .score:  return items.sorted { asc ? $0.score < $1.score : $0.score > $1.score }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                KSSSegmentedControl(options: PerillaTier.allCases.map { ($0, $0.rawValue) }, selection: $tab)
                Text(tab == .core
                     ? "全球供应商≤2家·垄断/双寡头·深链锁定"
                     : "全球三家寡头·深链锁定·国产替代赛道")
                    .font(KSSFont.themed(10.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            card
        }
    }

    private var card: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(theme.hairline)
            let rows = sortedItems
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                HStack(spacing: 0) {
                    Button { onSelect(item.symbol) } label: { row(item) }
                        .buttonStyle(.plain)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    // 就地补标灰点：行按钮的兄弟节点（嵌套会被行点击吞掉）。
                    ExposureMarkButton(
                        exposure: exposure.stock(item.symbol),
                        loaded: exposure.loaded
                    ) { exposure.onMark(item.symbol) }
                    WatchlistStarButton(
                        isWatched: watchlist.contains(item.symbol)
                    ) { onToggleWatchlist(item.symbol) }
                    .padding(.trailing, rowPadH)
                }
                if index < rows.count - 1 {
                    Divider().overlay(theme.hairline)
                }
            }
        }
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }

    private var header: some View {
        HStack(spacing: colSpacing) {
            SortHeaderCell(title: "评分", key: PerillaSort.score, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wScore)
            SortHeaderCell(title: "名称 / 代码", key: PerillaSort.name, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: wName)
            SortHeaderCell(title: "产业链 / 层级·护城河", key: PerillaSort.chains, selection: $sortKey, ascending: $ascending,
                           alignment: .leading)
            Text("机构持仓").frame(width: wInst, alignment: .leading)
            SortHeaderCell(title: "PE", key: PerillaSort.pe, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wPe)
            Text("对标美股").frame(width: wPeer, alignment: .leading)
            SortHeaderCell(title: "流通市值", key: PerillaSort.mv, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wMv)
            SortHeaderCell(title: "日", key: PerillaSort.ret1d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "周", key: PerillaSort.ret5d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "月", key: PerillaSort.ret20d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            SortHeaderCell(title: "年", key: PerillaSort.retYear, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: wRet)
            Color.clear.frame(width: 24)   // 未上图灰点
            Color.clear.frame(width: 28)
        }
        .font(KSSFont.themed(10.5, .medium, theme: theme))
        .tracking(0.3)
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func row(_ item: PerillaPick) -> some View {
        HStack(spacing: colSpacing) {
            Text(String(format: "%.2f", item.score))
                .font(.system(size: 13, weight: .heavy, design: .monospaced))
                .foregroundStyle(theme.accent)
                .lineLimit(1)
                .frame(width: wScore, alignment: .leading)

            // 名称 + 代码
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(KSSFont.themed(14, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1)
                Text(item.symbol)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
            .frame(width: wName, alignment: .leading)

            // 产业链 + 层级·护城河
            VStack(alignment: .leading, spacing: 2) {
                Text(item.chains.isEmpty ? "—" : item.chains)
                    .font(KSSFont.themed(12.5, .medium, theme: theme))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(1)
                HStack(spacing: 5) {
                    Text("\(layerLabel(item)) · \(item.moat)")
                        .font(KSSFont.themed(10.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                    if item.locked {
                        Image(systemName: "lock.fill")
                            .font(KSSFont.themed(8, theme: theme))
                            .foregroundStyle(theme.accent)
                    }
                    if item.assessmentStatus == "needs_review" {
                        Text("待证据")
                            .font(KSSFont.themed(8.5, .semibold, theme: theme))
                            .foregroundStyle(Color.orange)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.orange.opacity(0.12), in: Capsule())
                            .help(assessmentHelp(item))
                    } else if item.assessmentStatus == "qualified" {
                        Text("证据齐")
                            .font(KSSFont.themed(8.5, .semibold, theme: theme))
                            .foregroundStyle(theme.accent)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(theme.accent.opacity(0.10), in: Capsule())
                            .help(assessmentHelp(item))
                    }
                }
                // 产业链列是弹性列：徽标挂这里，不动任何固定列宽。
                ExposureBadge(exposure: exposure.stock(item.symbol),
                              loaded: exposure.loaded, showsDotWhenUnlabelled: false)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            // 机构持仓动态（机构占比 + 增减 + 北向）
            instCell(item)

            Text(numText(item.pe))
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(theme.textBody)
                .lineLimit(1)
                .frame(width: wPe, alignment: .trailing)

            // 对标美股（代码 + PE）
            peerCell(item)

            Text(mvText(item.circMvYi))
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textBody)
                .lineLimit(1)
                .frame(width: wMv, alignment: .trailing)

            retCell(item.ret1d)
            retCell(item.ret5d)
            retCell(item.ret20d)
            retCell(item.retYear)
        }
        .contentShape(Rectangle())
        .padding(.horizontal, rowPadH)
        .padding(.vertical, 9)
    }

    private func retCell(_ value: Double?) -> some View {
        Text(value.map { KSSFormat.percent($0) } ?? "—")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(value == nil ? theme.textSecondary : theme.signColor(value!))
            .lineLimit(1)
            .frame(width: wRet, alignment: .trailing)
    }

    // 机构持仓：第一行「机构 X%」(强调)，第二行「增减 · 北向」。缓存未命中=「—」。
    @ViewBuilder
    private func instCell(_ item: PerillaPick) -> some View {
        let s = item.instHolding ?? ""
        if s.isEmpty {
            Text("—")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(width: wInst, alignment: .leading)
        } else {
            // 串形如「机构49.0% · 减持 · 北向2.3%↓」，首段=机构占比，其余=动态。
            let segs = s.components(separatedBy: " · ")
            let head = segs.first ?? s
            let tail = segs.dropFirst().joined(separator: " · ")
            VStack(alignment: .leading, spacing: 1) {
                Text(head)
                    .font(KSSFont.themed(12, .semibold, theme: theme))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(1)
                if !tail.isEmpty {
                    Text(tail)
                        .font(KSSFont.themed(10, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
            }
            .frame(width: wInst, alignment: .leading)
        }
    }

    @ViewBuilder
    private func peerCell(_ item: PerillaPick) -> some View {
        let ticker = item.usPeerTicker ?? ""
        VStack(alignment: .leading, spacing: 1) {
            if ticker.isEmpty {
                Text("无对标")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            } else {
                Text(ticker)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(1)
                Text(item.usPeerPe.map { String(format: "PE %.1f", $0) } ?? "PE —")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
        }
        .frame(width: wPeer, alignment: .leading)
    }

    private func numText(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.1f", v)
    }

    private func mvText(_ yi: Double?) -> String {
        guard let yi else { return "—" }
        if yi >= 1000 { return String(format: "%.0f亿", yi) }
        return String(format: "%.1f亿", yi)
    }

    private func layerLabel(_ item: PerillaPick) -> String {
        let roleCN: String
        switch item.role {
        case "material": roleCN = "材料"
        case "equipment": roleCN = "设备"
        case "component": roleCN = "零部件"
        case "assembly": roleCN = "整机"
        default: roleCN = item.role
        }
        return "L\(item.layer)·\(roleCN)"
    }

    private func assessmentHelp(_ item: PerillaPick) -> String {
        var parts = item.reviewFlags ?? []
        if let date = item.structuralAsOf, !date.isEmpty {
            parts.append("结构标注截至 \(date)")
        }
        if let date = item.evidenceAsOf, !date.isEmpty {
            parts.append("证据截至 \(date)")
        }
        if let history = item.evidenceHistory, !history.isEmpty {
            let latest = history.max { ($0.asOf ?? "") < ($1.asOf ?? "") }
            var summary = "PIT \(history.count) 条"
            if let asOf = latest?.asOf, !asOf.isEmpty {
                summary += "，最新时间点 \(asOf)"
            }
            if let publishedAt = latest?.publishedAt, !publishedAt.isEmpty {
                summary += "，披露/可得 \(publishedAt)"
            }
            parts.append(summary)
        }
        return parts.isEmpty ? "结构与证据审计已通过" : parts.joined(separator: " · ")
    }
}

/// 今日板块信息图：6 个主题卡片，资金申赎 + 近 5 日涨幅 + 强势确认分级。
struct SectorPulseStrip: View {
    @Environment(\.kssTheme) private var theme
    var pulse: SectorPulse
    /// R5：代表 ETF 实时 quote map（产品码大写键），命中则主题卡显示当日实时涨跌。
    var quotes: [String: LongbridgeQuote] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2).fill(theme.accent).frame(width: 4, height: 18)
                Text("今日板块")
                    .font(KSSFont.themed(18, .semibold, theme: theme, design: .serif))
                    .foregroundStyle(theme.textPrimary)
                Text(regimeText)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(pulse.regimeInRegime == true ? theme.up : theme.textSecondary)
                Spacer()
                Text("资金正=申购/负=赎回 · 5日赎回≥2%=强势确认")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
            .padding(.top, 6)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 152), spacing: 12)], spacing: 12) {
                ForEach(pulse.themes) { theme in
                    SectorChip(theme: theme,
                               quote: theme.etfCode.flatMap { quotes[$0.uppercased()] })
                }
            }
        }
    }

    private var regimeText: String {
        guard let mom = pulse.regimeMom20 else { return "" }
        let on = pulse.regimeInRegime == true
        return "动量 \(String(format: "%.1f", mom)) · \(on ? "趋势确认" : "震荡")"
    }
}

struct SectorChip: View {
    @Environment(\.kssTheme) private var tokens
    var theme: SectorTheme
    /// 代表 ETF 实时 quote（R5）：live 时在近5日旁并列显示当日实时涨跌。
    var quote: LongbridgeQuote? = nil

    private var livePct: Double? {
        guard let quote, quote.isLive, let last = quote.lastDone,
              let prev = quote.prevClose, prev > 0 else { return nil }
        return (last - prev) / prev * 100.0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 5) {
                Text(theme.name)
                    .font(KSSFont.themed(14, .bold, theme: tokens))
                    .foregroundStyle(tokens.textPrimary)
                    .lineLimit(1)
                if theme.accel {
                    Image(systemName: "bolt.fill")
                        .font(KSSFont.themed(9, .bold, theme: tokens))
                        .foregroundStyle(tokens.accent)
                        .help("资金加速")
                }
                Spacer(minLength: 4)
                gradeBadge
            }
            // R6 R1：近5日/今日拆两行——行内并排曾把 152pt 网格挤爆致数字断行（+3.5|4%）。
            // 数字 fixedSize+lineLimit(1) 钉死单行；今日行只在盘中命中实时 quote 时出现。
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("近5日")
                    .font(KSSFont.themed(10, theme: tokens))
                    .foregroundStyle(tokens.textSecondary)
                Text(theme.past5Ret.map { KSSFormat.percent($0 / 100) } ?? "—")
                    .font(KSSFont.harmonyNumber(18))
                    .foregroundStyle(tokens.signColor(theme.past5Ret ?? 0))
                    .lineLimit(1)
                    .fixedSize()
            }
            if let pct = livePct {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text("今日")
                        .font(KSSFont.themed(10, theme: tokens))
                        .foregroundStyle(tokens.textSecondary)
                    Text(KSSFormat.percent(pct / 100))
                        .font(KSSFont.harmonyNumber(14))
                        .foregroundStyle(tokens.signColor(pct))
                        .lineLimit(1)
                        .fixedSize()
                }
            }
            HStack(spacing: 10) {
                flowItem("1日", theme.flow1d)
                flowItem("5日", theme.flow5d)
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 12)
    }

    private var gradeBadge: some View {
        let warn = theme.divergence || theme.grade.contains("预警") || theme.grade.contains("见顶")
        let strong = theme.grade.contains("强势")
        let bg = warn ? tokens.up : (strong ? tokens.accent : tokens.textSecondary.opacity(0.18))
        // warn 底=up(饱和红，白字为不随主题变化的 invariant)；strong 底=accent，须用 onAccent。
        let fg = warn ? Color.white : (strong ? tokens.onAccent : tokens.textBody)
        return Text(theme.divergence ? "见顶预警" : theme.grade)
            .font(KSSFont.themed(10, .bold, theme: tokens))
            .foregroundStyle(fg)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(bg, in: Capsule())
    }

    /// 资金流（正=申购/负=赎回）。语义上「赎回≠利空」由分级徽标承载，故此处中性着色，
    /// 只呈现方向与量级，避免把申购误读成上涨。
    private func flowItem(_ label: String, _ flow: Double?) -> some View {
        HStack(spacing: 3) {
            Text(label)
                .font(KSSFont.themed(10, theme: tokens))
                .foregroundStyle(tokens.textSecondary)
            Text(flow.map { String(format: "%+.1f", $0) } ?? "—")
                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(tokens.textBody)
        }
        .lineLimit(1)
        .fixedSize()
    }
}

/// 编辑风日期戳：大号衬线 MM.DD + 小号 年/星期 右侧堆叠（复刻杂志日期设计）。
struct EditorialDateView: View {
    @Environment(\.kssTheme) private var theme
    var date = Date()

    var body: some View {
        HStack(alignment: .top, spacing: 7) {
            Text(monthDay)
                .font(KSSFont.themed(34, .bold, theme: theme, design: .serif))
                .foregroundStyle(theme.textPrimary)
                .monospacedDigit()
            VStack(alignment: .leading, spacing: 1) {
                Text(year)
                    .foregroundStyle(theme.textSecondary)
                Text(weekday)
                    .foregroundStyle(theme.accent)
            }
            .font(KSSFont.themed(12, .semibold, theme: theme, design: .serif))
            .padding(.top, 3)
        }
        .fixedSize()
    }

    private var comps: DateComponents {
        Calendar.current.dateComponents([.year, .month, .day], from: date)
    }
    private var monthDay: String { String(format: "%02d.%02d", comps.month ?? 0, comps.day ?? 0) }
    private var year: String { String(comps.year ?? 0) }
    private var weekday: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US")
        f.dateFormat = "EEE"
        return f.string(from: date).uppercased()
    }
}

/// 总览第一行市场速览：固定 4 槽可配指标；区块级 Sparkle（标题行右侧）。
struct MarketStripRow: View {
    @Environment(\.kssTheme) private var theme
    var strip: MarketStrip
    var quotes: [String: LongbridgeQuote] = [:]
    var bridge: BridgeClient? = nil
    var onReloadSnapshot: () -> Void = {}
    var onOpenAIWithRegion: ((String) -> Void)? = nil
    @State private var metricBusy = false
    @State private var metricError: String?
    @State private var bindDraft: SurfaceBindDraft?
    @State private var selectedSlotId: String = "strip_0"

    private static let slotIds = ["strip_0", "strip_1", "strip_2", "strip_3"]
    private static let metricChoices: [(id: String, title: String)] = [
        ("etf_a500_563360", "A500ETF(563360)"),
        ("etf_a500_159361", "A500ETF(159361)"),
        ("north_money", "北向资金"),
        ("limit_max_board", "最高连板"),
        ("limit_seal_rate", "封板率"),
        ("limit_up_count", "涨停家数"),
        ("limit_break_rate", "破板率"),
        ("index_sse", "上证指数"),
        ("index_szse", "深证成指"),
        ("index_kcb50", "科创50"),
        ("index_cyb", "创业板指"),
        ("index_a50", "富时中国A50"),
    ]

    /// 优先 bridge 注入的四槽；缺则回退混排/单卡兼容。
    private var displaySlots: [StripMetricProps] {
        if let slots = strip.stripSlots, slots.count == 4 {
            return slots
        }
        // 兼容：etfs + north + stripMetric 保序取前 4
        var built: [StripMetricProps] = []
        for etf in strip.etfs.prefix(2) {
            built.append(StripMetricProps(
                slotId: nil, metricId: etf.code, title: etf.name,
                value: etf.close, valueText: String(format: "%.3f", etf.close),
                delta: etf.pct, deltaText: String(format: "%+.2f%%", etf.pct),
                sub: etf.code, reason: nil
            ))
        }
        if let nm = strip.northMoney {
            let yi = nm / 10000.0
            built.append(StripMetricProps(
                slotId: nil, metricId: "north_money", title: "北向资金",
                value: yi, valueText: String(format: "%+.1f 亿", yi),
                delta: yi, deltaText: yi >= 0 ? "净流入" : "净流出",
                sub: "沪深港通", reason: nil
            ))
        }
        if let m = strip.stripMetric {
            built.append(m)
        }
        while built.count < 4 {
            built.append(StripMetricProps(
                slotId: nil, metricId: nil, title: "—",
                value: nil, valueText: "—", delta: nil, deltaText: "",
                sub: nil, reason: "empty"
            ))
        }
        return Array(built.prefix(4))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .center, spacing: 10) {
                SectionHeader("市场速览", caption: "四槽可配指标")
                Spacer(minLength: 8)
                DashboardSparkleControl(
                    help: "用中文或列表配置四槽指标",
                    disabled: metricBusy,
                    sheetTitle: "配置市场速览",
                    region: "strip_metric",
                    nlPlaceholder: "例如：改成封板率（已选槽会自动带上）",
                    nlExamples: ["改成封板率", "改成北向资金", "改成上证指数", "改成最高连板"],
                    bridge: bridge,
                    onOpenAI: onOpenAIWithRegion.map { cb in { cb("strip_metric") } },
                    onDraft: { draft in bindDraft = draft },
                    selectedSlotId: $selectedSlotId,
                    listTabTitle: "列表选择",
                    listContent: { _ in
                        VStack(alignment: .leading, spacing: 10) {
                            Text("上方选槽后点指标；确认后写入。NL 会自动带当前槽。")
                                .font(KSSFont.themed(11, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                            DashboardSimpleChoiceList(
                                choices: Self.metricChoices,
                                selectedId: displaySlots.first(where: {
                                    $0.slotId == selectedSlotId
                                })?.metricId
                            ) { mid in
                                selectMetricForSlot(mid, slotId: selectedSlotId)
                            }
                        }
                    }
                )
            }
            DashboardStripCardRow {
                ForEach(Array(displaySlots.enumerated()), id: \.offset) { _, props in
                    slotCard(props)
                }
            }
            if let metricError {
                Text(metricError)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(.red.opacity(0.85))
                    .padding(.leading, 4)
            }
        }
        .sheet(item: $bindDraft) { draft in
            SurfaceBindConfirm(
                draft: draft,
                busy: metricBusy,
                onCancel: { bindDraft = nil },
                onConfirm: { confirmBind(draft) }
            )
        }
    }

    private func slotCard(_ props: StripMetricProps) -> some View {
        let title = props.title ?? "—"
        let valueText = props.valueText ?? "—"
        let deltaText = props.deltaText ?? ""
        let delta = props.delta ?? 0
        return DashboardStripCard(
            title: title,
            meta: props.sub,
            isLive: false
        ) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(valueText)
                    .font(KSSFont.harmonyNumber(22))
                    .foregroundStyle(
                        props.value == nil ? theme.textSecondary : theme.signColor(delta)
                    )
                    .lineLimit(1)
                if !deltaText.isEmpty, deltaText != title {
                    Text(deltaText)
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.signColor(delta))
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
        }
        .opacity(metricBusy ? 0.7 : 1)
    }

    /// 列表选中 → draft（不直写 apply）。
    private func selectMetricForSlot(_ metricId: String, slotId: String) {
        let title = Self.metricChoices.first(where: { $0.id == metricId })?.title ?? metricId
        let ops = """
        [{"op":"set_strip_slot","slot_id":"\(slotId)","metric_id":"\(metricId)"}]
        """
        let preview = SurfaceNlPreview(
            op: "set_strip_slot",
            code: nil,
            name: nil,
            close: nil,
            pct: nil,
            label: "\(slotId) → \(title)",
            metricId: metricId,
            title: title,
            valueText: nil,
            deltaText: nil,
            sub: slotId,
            reason: nil
        )
        bindDraft = SurfaceBindDraft(
            region: "strip_metric",
            summary: "将 \(slotId) 切换为 \(title)",
            opsJSON: ops,
            previews: [preview],
            failed: [],
            partial: false
        )
    }

    private func confirmBind(_ draft: SurfaceBindDraft) {
        guard let bridge else { return }
        metricBusy = true
        metricError = nil
        Task {
            do {
                let resp = try await Task.detached {
                    try bridge.surfaceApply(opsJSON: draft.opsJSON)
                }.value
                await MainActor.run {
                    metricBusy = false
                    if resp.ok == false {
                        metricError = resp.error ?? "应用失败"
                    } else {
                        bindDraft = nil
                        onReloadSnapshot()
                    }
                }
            } catch {
                await MainActor.run {
                    metricBusy = false
                    metricError = error.localizedDescription
                }
            }
        }
    }
}

/// 指数一览区块：标题行右侧 Sparkle + 自适应网格。
struct IndexBoardSection: View {
    @Environment(\.kssTheme) private var theme
    var indices: [IndexQuote]
    var quotes: [String: LongbridgeQuote] = [:]
    var bridge: BridgeClient? = nil
    var onReloadSnapshot: () -> Void = {}
    var onOpenAI: (() -> Void)? = nil
    @State private var busy = false
    @State private var errorText: String?
    @State private var bindDraft: SurfaceBindDraft?
    @State private var listLoading = false
    @State private var catalogItems: [SurfaceCatalogItem] = []

    /// bridge 取不到 catalog 时的离线兜底。可绑真源是 bind_catalog 的 index_board
    /// 槽，这里只保证子进程起不来时列表不空。
    ///
    /// 原先多一项 399005.SZ 中小板指：refresh_market_strip 的 INDEX_BOARD 不抓它，
    /// 绑上去 effective_index_board_quotes 只能给出 close=nil 的骨架行，已去掉。
    private static let defaultChoices: [(id: String, title: String)] = [
        ("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指"),
        ("000688.SH", "科创50"), ("000698.SH", "科创100"), ("000680.SH", "科创综指"),
        ("000300.SH", "沪深300"), ("000016.SH", "上证50"), ("000905.SH", "中证500"),
        ("000852.SH", "中证1000"), ("000510.SH", "中证A500"), ("932000.CSI", "中证2000"),
        ("899050.BJ", "北证50"),
    ]

    /// catalog 优先，拿不到才回落硬编码。
    private var choices: [(id: String, title: String)] {
        let fromCatalog = catalogItems.compactMap { item -> (id: String, title: String)? in
            let code = item.displayCode
            guard !code.isEmpty else { return nil }
            return (id: code, title: item.displayName)
        }
        return fromCatalog.isEmpty ? Self.defaultChoices : fromCatalog
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 10) {
                SectionHeader("指数一览", caption: "常用宽基 / 主题指数当日表现")
                Spacer(minLength: 8)
                DashboardSparkleControl(
                    help: "用中文或列表调整指数一览",
                    disabled: busy,
                    sheetTitle: "调整指数一览",
                    region: "index_board",
                    nlPlaceholder: "例如：加上中证1000、去掉北证50、恢复默认",
                    nlExamples: ["加上中证1000", "去掉北证50", "恢复默认"],
                    bridge: bridge,
                    onOpenAI: onOpenAI,
                    onDraft: { draft in bindDraft = draft },
                    listTabTitle: "列表选择",
                    onListTabAppear: { loadCatalog() },
                    listContent: { _ in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(listLoading ? "正在载入可绑目录…" : "选择后进入真值确认；不会直接写入。")
                                .font(KSSFont.themed(11, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                            DashboardSimpleChoiceList(
                                choices: choices,
                                selectedId: nil
                            ) { code in
                                draftAppend(code: code)
                            }
                        }
                    }
                )
            }
            if let errorText {
                Text(errorText)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(.red.opacity(0.85))
            }
            IndexBoardGrid(indices: indices, quotes: quotes)
        }
        .sheet(item: $bindDraft) { draft in
            SurfaceBindConfirm(
                draft: draft,
                busy: busy,
                onCancel: { bindDraft = nil },
                onConfirm: { confirm(draft) }
            )
        }
    }

    private func draftAppend(code: String) {
        let title = choices.first(where: { $0.id == code })?.title ?? code
        // 全量覆盖：当前 codes + 新 code
        var codes = indices.map { $0.code.uppercased() }
        if !codes.contains(code.uppercased()) {
            codes.append(code.uppercased())
        }
        let codesJSON = codes.map { "\"\($0)\"" }.joined(separator: ",")
        let ops = "[{\"op\":\"index_board_set\",\"codes\":[\(codesJSON)]}]"
        bindDraft = SurfaceBindDraft(
            region: "index_board",
            summary: "追加 \(title)（\(code)）到指数一览",
            opsJSON: ops,
            previews: [
                SurfaceNlPreview(
                    op: "index_board_append",
                    code: code,
                    name: title,
                    close: nil,
                    pct: nil,
                    label: "追加 \(title)",
                    metricId: nil,
                    title: title,
                    valueText: nil,
                    deltaText: nil,
                    sub: code,
                    reason: nil
                ),
            ],
            failed: [],
            partial: false
        )
    }

    private func loadCatalog() {
        guard let bridge else { return }
        listLoading = true
        errorText = nil
        Task {
            do {
                let resp = try await Task.detached {
                    try bridge.surfaceCatalog(slot: "index_board", q: "")
                }.value
                await MainActor.run {
                    listLoading = false
                    let items = resp.items ?? []
                    catalogItems = items
                    // 空目录说明 bind_catalog 的 index_board 槽没东西（2026-07-31
                    // 那次回归就是这样），列表会回落硬编码——说出来，别装没事。
                    if items.isEmpty {
                        errorText = "可绑目录为空，已回落内置名单；请重建 bind_catalog"
                    }
                }
            } catch {
                await MainActor.run {
                    listLoading = false
                    errorText = "载入可绑目录失败：\(error.localizedDescription)"
                }
            }
        }
    }

    private func confirm(_ draft: SurfaceBindDraft) {
        guard let bridge else { return }
        busy = true
        errorText = nil
        Task {
            do {
                let resp = try await Task.detached {
                    try bridge.surfaceApply(opsJSON: draft.opsJSON)
                }.value
                await MainActor.run {
                    busy = false
                    if resp.ok == false {
                        errorText = resp.error ?? "应用失败"
                    } else {
                        bindDraft = nil
                        onReloadSnapshot()
                    }
                }
            } catch {
                await MainActor.run {
                    busy = false
                    errorText = error.localizedDescription
                }
            }
        }
    }
}

/// 指数一览：自适应网格（名称 / 收盘 / 涨跌%，红涨绿跌）。
struct IndexBoardGrid: View {
    @Environment(\.kssTheme) private var theme
    var indices: [IndexQuote]
    var quotes: [String: LongbridgeQuote] = [:]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 158), spacing: 10)], spacing: 10) {
            ForEach(indices) { idx in
                let live = RealtimeMerge.applyLive(close: idx.close, pct: idx.pct, quote: quotes[idx.code.uppercased()])
                VStack(alignment: .leading, spacing: 5) {
                    Text(idx.name)
                        .font(KSSFont.themed(12.5, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .lineLimit(1)
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        LivePriceText(
                            value: live.close,
                            text: String(format: "%.2f", live.close),
                            baseColor: theme.signColor(live.pct),
                            isLive: live.isLive,
                            font: KSSFont.harmonyNumber(16)
                        )
                        .lineLimit(1)
                        Spacer(minLength: 0)
                        LivePriceText(
                            value: live.pct,
                            text: String(format: "%+.2f%%", live.pct),
                            baseColor: theme.signColor(live.pct),
                            isLive: live.isLive,
                            font: .system(size: 11.5, weight: .semibold, design: .monospaced)
                        )
                        .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .kssCard(padding: 11)
            }
        }
    }
}

/// 跑马灯 chip 底色：指数板用 raised；隔夜美股用纸白底以区分。
enum MarqueeChipSurface {
    case raised
    case paper
}

/// 指数跑马灯：13 指数按涨跌幅降序，TimelineView 驱动无缝循环横向滚动。
/// 参照 M3 carousel —— 圆角容器(shapeL) + 两端淡出遮罩 + 一致项高，展示型不可点。
struct IndexMarquee: View {
    @Environment(\.kssTheme) private var theme
    var indices: [IndexQuote]
    var quotes: [String: LongbridgeQuote] = [:]
    /// false = 保持传入顺序（隔夜美股）；true = 按涨跌幅降序（A 股指数板默认）
    var sortByPct: Bool = true
    var chipSurface: MarqueeChipSurface = .raised

    private let gap: CGFloat = 10
    private let speed: Double = 42            // 滚动速度 pts/s
    @State private var rowWidth: CGFloat = 0  // 单份内容宽（含内部间距）

    private struct LiveIndex: Identifiable {
        var id: String { code }
        var code: String
        var name: String
        var close: Double
        var pct: Double
        var isLive: Bool
    }

    private var sorted: [LiveIndex] {
        let mapped = indices.map { idx -> LiveIndex in
            let live = RealtimeMerge.applyLive(close: idx.close, pct: idx.pct, quote: quotes[idx.code.uppercased()])
            return LiveIndex(code: idx.code, name: idx.name, close: live.close, pct: live.pct, isLive: live.isLive)
        }
        return sortByPct ? mapped.sorted { $0.pct > $1.pct } : mapped
    }

    var body: some View {
        // GeometryReader 取内容列实际宽，把滚动行钉在该宽度内（leading 对齐 + clip），
        // 避免溢出内容列右边距冲到窗口边缘。
        GeometryReader { geo in
            TimelineView(.animation) { timeline in
                let period = rowWidth + gap    // 一个循环周期 = 单份宽 + 拼接缝
                let elapsed = timeline.date.timeIntervalSinceReferenceDate
                let offset = period > 0
                    ? -CGFloat((elapsed * speed).truncatingRemainder(dividingBy: Double(period)))
                    : 0
                HStack(spacing: gap) {
                    row(measured: true)
                    row(measured: false)       // 第二份用于无缝衔接
                }
                .offset(x: offset)
                .frame(width: geo.size.width, alignment: .leading)
            }
        }
        .frame(height: 46)
        .clipped()
        .mask(edgeFade)                        // M3 carousel 两端淡出
        .onPreferenceChange(MarqueeWidthKey.self) { rowWidth = $0 }
    }

    private func row(measured: Bool) -> some View {
        HStack(spacing: gap) {
            ForEach(sorted) { chip($0) }
        }
        .background {
            if measured {
                GeometryReader { g in
                    Color.clear.preference(key: MarqueeWidthKey.self, value: g.size.width)
                }
            }
        }
    }

    private func chip(_ idx: LiveIndex) -> some View {
        let paper = chipSurface == .paper
        // 纸白底：名称/次价用深色保证对比；raised 走主题 token
        let nameColor: Color = paper ? Color(white: 0.12) : theme.textPrimary
        let closeColor: Color = paper ? Color(white: 0.35) : theme.textSecondary
        let fill: Color = paper ? Color.white : theme.surfaceRaised
        let border = paper
            ? Color.black.opacity(0.08)
            : theme.signColor(idx.pct).opacity(0.18)

        return HStack(spacing: 6) {
            Image(systemName: idx.pct >= 0 ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
                .font(KSSFont.themed(9, .bold, theme: theme))
                .foregroundStyle(theme.signColor(idx.pct))
            Text(idx.name)
                .font(KSSFont.themed(12.5, .bold, theme: theme))
                .foregroundStyle(nameColor)
                .lineLimit(1)
            LivePriceText(
                value: idx.close,
                text: String(format: "%.2f", idx.close),
                baseColor: closeColor,
                isLive: idx.isLive,
                font: .system(size: 12, weight: .semibold, design: .monospaced)
            )
            .lineLimit(1)
            LivePriceText(
                value: idx.pct,
                text: String(format: "%+.2f%%", idx.pct),
                baseColor: theme.signColor(idx.pct),
                isLive: idx.isLive,
                font: .system(size: 12, weight: .heavy, design: .monospaced)
            )
            .lineLimit(1)
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
        .background(fill, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.shapeL)
                .strokeBorder(border, lineWidth: 1)
        )
        .shadow(color: paper ? Color.black.opacity(0.04) : .clear, radius: paper ? 2 : 0, y: paper ? 1 : 0)
        .fixedSize()
    }

    private var edgeFade: some View {
        LinearGradient(
            stops: [
                .init(color: .clear, location: 0),
                .init(color: .black, location: 0.035),
                .init(color: .black, location: 0.965),
                .init(color: .clear, location: 1),
            ],
            startPoint: .leading, endPoint: .trailing
        )
    }
}

private struct MarqueeWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

/// 隔夜美股分区：标题 + Sparkle（NL / 列表双 Tab）+ 跑马灯。
struct OvernightUSSection: View {
    @Environment(\.kssTheme) private var theme
    var overnight: [IndexQuote]
    var surfaceConfig: SurfaceConfigSnapshot?
    var usMarketHeaderText: String
    var usMarketHeaderStatus: USMarketHeaderStatus
    var usMarketQuotes: [String: USMarketQuote]
    var bridge: BridgeClient?
    var onOpenAI: () -> Void
    var onReloadSnapshot: () -> Void

    @State private var candidates: [SurfaceCandidate] = []
    @State private var catalogItems: [SurfaceCatalogItem] = []
    @State private var domainsOnline: [String] = []
    @State private var filter = ""
    @State private var listLoading = false
    @State private var busy = false
    @State private var errorText: String?
    @State private var bindDraft: SurfaceBindDraft?

    private var defaultCodes: Set<String> {
        Set(overnight.filter { !($0.isUserAppended ?? false) }.map { $0.code.uppercased() })
    }

    private var displayOvernight: [IndexQuote] {
        var list = overnight
        let have = Set(list.map { $0.code.uppercased() })
        for item in surfaceConfig?.overnightAppend ?? [] {
            let code = item.code.uppercased()
            if have.contains(code) { continue }
            list.append(IndexQuote(
                code: code,
                name: item.name ?? code,
                close: item.probeClose ?? 0,
                pct: 0,
                date: nil,
                isUserAppended: true,
                pending: true,
                kindSource: item.kindSource,
                probeClose: item.probeClose
            ))
        }
        return list
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 10) {
                SectionHeader("隔夜美股")
                Spacer(minLength: 8)
                Label(
                    usMarketHeaderText,
                    systemImage: usMarketHeaderStatus.systemImage
                )
                .font(KSSFont.themed(11.5, .medium, theme: theme))
                .foregroundStyle(
                    usMarketHeaderStatus.isActive ? theme.accent : theme.textSecondary
                )
                .lineLimit(1)
                .padding(.top, 6)
                DashboardSparkleControl(
                    help: "用中文或列表调整隔夜",
                    disabled: busy,
                    sheetTitle: "调整隔夜美股",
                    region: "overnight_us",
                    nlPlaceholder: "例如：加上苹果和阿斯麦、去掉苹果、清空我的追加",
                    nlExamples: ["加上苹果", "加上苹果和阿斯麦", "去掉苹果"],
                    bridge: bridge,
                    onOpenAI: onOpenAI,
                    onDraft: { draft in bindDraft = draft },
                    listTabTitle: "列表选择",
                    onListTabAppear: { loadCatalogList() },
                    listContent: { dismiss in
                        VStack(alignment: .leading, spacing: 8) {
                            if !domainsOnline.isEmpty {
                                Text("已上线域：" + domainsOnline.joined(separator: " · "))
                                    .font(KSSFont.themed(11, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
                            }
                            DashboardCandidatePickerList(
                                candidates: catalogAsCandidates,
                                disabledCodes: defaultCodes,
                                isLoading: listLoading,
                                filter: $filter
                            ) { c in
                                draftAppendCandidate(c, dismiss: dismiss)
                            }
                        }
                    }
                )
            }
            if let errorText {
                Text(errorText)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(.red.opacity(0.85))
            }
            if !displayOvernight.isEmpty {
                OvernightUSMarquee(
                    indices: displayOvernight,
                    quotes: usMarketQuotes,
                    onRemoveUser: removeUser
                )
            } else {
                Text("暂无隔夜报价 · 点 ✦ 用中文或列表追加")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.leading, 4)
            }
        }
        .sheet(item: $bindDraft) { draft in
            SurfaceBindConfirm(
                draft: draft,
                busy: busy,
                onCancel: { bindDraft = nil },
                onConfirm: { confirmBind(draft) }
            )
        }
    }

    private func confirmBind(_ draft: SurfaceBindDraft) {
        guard let bridge else { return }
        busy = true
        errorText = nil
        Task {
            do {
                let resp = try await Task.detached {
                    try bridge.surfaceApply(opsJSON: draft.opsJSON)
                }.value
                await MainActor.run {
                    busy = false
                    if resp.ok == false {
                        errorText = resp.error ?? "应用失败"
                    } else {
                        bindDraft = nil
                        onReloadSnapshot()
                        Task.detached {
                            _ = try? bridge.runTask(.refreshMarketStrip)
                        }
                    }
                }
            } catch {
                await MainActor.run {
                    busy = false
                    errorText = error.localizedDescription
                }
            }
        }
    }

    private var catalogAsCandidates: [SurfaceCandidate] {
        if !catalogItems.isEmpty {
            return catalogItems.map {
                SurfaceCandidate(
                    code: $0.displayCode.isEmpty ? $0.id : $0.displayCode,
                    name: $0.displayName,
                    kind: $0.kind == "equity" || $0.kind == "etf" || $0.kind == "index"
                        ? ($0.codes?["code"] != nil
                            ? overnightKindHint(for: $0)
                            : "yfinance")
                        : overnightKindHint(for: $0)
                )
            }
        }
        return candidates
    }

    private func overnightKindHint(for item: SurfaceCatalogItem) -> String {
        if item.market == "CN" { return "a_share" }
        if item.market == "HK" { return "hk" }
        if item.kind == "index" { return "index_global" }
        return "yfinance"
    }

    private func loadCatalogList() {
        guard let bridge else { return }
        listLoading = true
        errorText = nil
        let q = filter
        Task {
            do {
                let resp = try await Task.detached {
                    try bridge.surfaceCatalog(slot: "overnight_marquee", q: q)
                }.value
                await MainActor.run {
                    domainsOnline = resp.domainsOnline ?? []
                    catalogItems = resp.items ?? []
                    if catalogItems.isEmpty {
                        // fallback surface-get 旧候选
                        loadCandidatesFallback()
                    } else {
                        listLoading = false
                    }
                }
            } catch {
                await MainActor.run {
                    loadCandidatesFallback()
                    if candidates.isEmpty {
                        errorText = error.localizedDescription
                    }
                    listLoading = false
                }
            }
        }
    }

    private func loadCandidatesFallback() {
        guard let bridge else {
            listLoading = false
            return
        }
        Task {
            do {
                let resp = try await Task.detached { try bridge.surfaceGet() }.value
                await MainActor.run {
                    candidates = resp.candidates ?? []
                    listLoading = false
                }
            } catch {
                await MainActor.run {
                    errorText = error.localizedDescription
                    listLoading = false
                }
            }
        }
    }

    /// 列表选中 → draft → SurfaceBindConfirm（不直写 apply）。
    private func draftAppendCandidate(_ c: SurfaceCandidate, dismiss: @escaping () -> Void) {
        let name = (c.name ?? c.code).replacingOccurrences(of: "\"", with: "")
        let kind = c.kind ?? "yfinance"
        let ops = """
        [{"op":"overnight_append","code":"\(c.code)","name":"\(name)","kind":"\(kind)","kind_source":"candidate_table","added_via":"plus"}]
        """
        dismiss()
        bindDraft = SurfaceBindDraft(
            region: "overnight_us",
            summary: "追加 \(name)（\(c.code)）到隔夜美股",
            opsJSON: ops,
            previews: [
                SurfaceNlPreview(
                    op: "overnight_append",
                    code: c.code,
                    name: name,
                    close: nil,
                    pct: nil,
                    label: "追加 \(name)",
                    metricId: nil,
                    title: name,
                    valueText: nil,
                    deltaText: nil,
                    sub: c.code,
                    reason: nil
                ),
            ],
            failed: [],
            partial: false
        )
    }

    private func removeUser(_ code: String) {
        guard let bridge else { return }
        let ops = "[{\"op\":\"overnight_remove\",\"code\":\"\(code)\"}]"
        Task {
            _ = try? await Task.detached {
                try bridge.surfaceApply(opsJSON: ops)
            }.value
            await MainActor.run { onReloadSnapshot() }
        }
    }
}

/// 隔夜美股跑马灯：名单固定顺序，不按涨跌重排；行情状态集中放在分区标题，
/// chip 只承载名称、价格与涨跌，保持密度与连续滚动。
struct OvernightUSMarquee: View {
    @Environment(\.kssTheme) private var theme
    var indices: [IndexQuote]
    var quotes: [String: USMarketQuote] = [:]
    var onRemoveUser: ((String) -> Void)? = nil

    private let gap: CGFloat = 10
    private let speed: Double = 34
    @State private var rowWidth: CGFloat = 0

    var body: some View {
        GeometryReader { geo in
            TimelineView(.animation) { timeline in
                let period = rowWidth + gap
                let elapsed = timeline.date.timeIntervalSinceReferenceDate
                let offset = period > 0
                    ? -CGFloat((elapsed * speed).truncatingRemainder(dividingBy: Double(period)))
                    : 0
                HStack(spacing: gap) {
                    row(measured: true)
                    row(measured: false)
                }
                .offset(x: offset)
                .frame(width: geo.size.width, alignment: .leading)
            }
        }
        .frame(height: 46)
        .clipped()
        .mask(edgeFade)
        .onPreferenceChange(MarqueeWidthKey.self) { rowWidth = $0 }
    }

    private func row(measured: Bool) -> some View {
        HStack(spacing: gap) {
            ForEach(indices) { idx in
                chip(idx)
                    .contextMenu {
                        if idx.isUserAppended == true, let onRemoveUser {
                            Button("移除 \(idx.name)", role: .destructive) {
                                onRemoveUser(idx.code)
                            }
                        }
                    }
            }
        }
        .background {
            if measured {
                GeometryReader { geometry in
                    Color.clear.preference(
                        key: MarqueeWidthKey.self,
                        value: geometry.size.width
                    )
                }
            }
        }
    }

    private func chip(_ index: IndexQuote) -> some View {
        let pending = index.pending == true
        let live = quotes[index.code.uppercased()]
        let close = live?.last ?? index.close
        let pct = live?.pct ?? index.pct
        let status = live?.status ?? "static"
        let isObserved = ["live", "delayed"].contains(status)

        return HStack(spacing: 7) {
            if pending {
                Image(systemName: "clock")
                    .font(KSSFont.themed(9, .bold, theme: theme))
                    .foregroundStyle(Color(white: 0.45))
            } else {
                Image(
                    systemName: pct >= 0
                        ? "arrowtriangle.up.fill"
                        : "arrowtriangle.down.fill"
                )
                .font(KSSFont.themed(9, .bold, theme: theme))
                .foregroundStyle(theme.signColor(pct))
            }
            Text(index.name)
                .font(KSSFont.themed(12.5, .bold, theme: theme))
                .foregroundStyle(Color(white: 0.12))
                .lineLimit(1)
            if pending {
                Text("待刷新")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Color(white: 0.45))
                    .lineLimit(1)
            } else {
                LivePriceText(
                    value: close,
                    text: String(format: "%.2f", close),
                    baseColor: Color(white: 0.35),
                    isLive: isObserved,
                    font: .system(size: 12, weight: .semibold, design: .monospaced)
                )
                .lineLimit(1)
                LivePriceText(
                    value: pct,
                    text: String(format: "%+.2f%%", pct),
                    baseColor: theme.signColor(pct),
                    isLive: isObserved,
                    font: .system(size: 12, weight: .heavy, design: .monospaced)
                )
                .lineLimit(1)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
        .background(Color.white, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(
            RoundedRectangle(cornerRadius: KSSTheme.shapeL)
                .strokeBorder(Color.black.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.04), radius: 2, y: 1)
        .fixedSize()
        .help(live?.error ?? (pending ? "已追加，等待行情刷新" : ""))
    }

    private var edgeFade: some View {
        LinearGradient(
            stops: [
                .init(color: .clear, location: 0),
                .init(color: .black, location: 0.035),
                .init(color: .black, location: 0.965),
                .init(color: .clear, location: 1),
            ],
            startPoint: .leading,
            endPoint: .trailing
        )
    }
}

/// 总览第二行（旧）：上证 / 纳斯达克 / 恒生 — 无 stacks 时回退。
struct MarketIndexRow: View {
    @Environment(\.kssTheme) private var theme
    var indices: [IndexQuote]
    var quotes: [String: LongbridgeQuote] = [:]

    var body: some View {
        HStack(spacing: 12) {
            ForEach(indices) { idx in
                let live = RealtimeMerge.applyLive(close: idx.close, pct: idx.pct, quote: quotes[idx.code.uppercased()])
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        Text(idx.name)
                            .font(KSSFont.themed(13.5, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .lineLimit(1)
                        Spacer(minLength: 4)
                        Text(dateLabel(idx.date))
                            .font(.system(size: 10.5, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(1)
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        LivePriceText(
                            value: live.close,
                            text: String(format: "%.2f", live.close),
                            baseColor: theme.signColor(live.pct),
                            isLive: live.isLive,
                            font: KSSFont.harmonyNumber(22)
                        )
                        .lineLimit(1)
                        LivePriceText(
                            value: live.pct,
                            text: String(format: "%+.2f%%", live.pct),
                            baseColor: theme.signColor(live.pct),
                            isLive: live.isLive,
                            font: .system(size: 12, weight: .semibold, design: .monospaced)
                        )
                        .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .kssCard(padding: 14)
            }
        }
    }

    private func dateLabel(_ raw: String?) -> String {
        guard let raw, raw.count == 8 else { return raw ?? "" }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }
}

/// 三列指数堆叠：自动 4s 轮播 + 轻点切下一张；各自独立；价/线优先 Longbridge 实盘。
struct IndexStackRow: View {
    var stacks: [IndexStackColumn]
    var quotes: [String: LongbridgeQuote] = [:]
    var liveSparklines: [String: SparklineSeries] = [:]

    var body: some View {
        HStack(spacing: 12) {
            ForEach(stacks) { col in
                IndexStackColumnView(
                    column: col,
                    quotes: quotes,
                    liveSparklines: liveSparklines
                )
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct IndexStackColumnView: View {
    @Environment(\.kssTheme) private var theme
    var column: IndexStackColumn
    var quotes: [String: LongbridgeQuote] = [:]
    var liveSparklines: [String: SparklineSeries] = [:]

    @State private var page = 0
    private let interval: TimeInterval = 4

    private var items: [IndexStackItem] { column.items }
    private var current: IndexStackItem? {
        guard !items.isEmpty else { return nil }
        return items[page % items.count]
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            // 背后叠层提示
            if items.count > 1 {
                RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                    .fill(theme.surfaceRaised)
                    .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
                    .offset(x: 4, y: 6)
                    .opacity(0.55)
                    .padding(.trailing, 4)
            }
            if let item = current {
                stackCard(item)
            } else {
                Text("—")
                    .foregroundStyle(theme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 88)
                    .kssCard(padding: 14)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { advance() }
        .onReceive(Timer.publish(every: interval, on: .main, in: .common).autoconnect()) { _ in
            guard items.count > 1 else { return }
            advance()
        }
        .onChange(of: column.id) { _, _ in page = 0 }
        .onChange(of: items.count) { _, _ in page = 0 }
    }

    private func advance() {
        guard !items.isEmpty else { return }
        withAnimation(.easeInOut(duration: 0.22)) {
            page = (page + 1) % items.count
        }
    }

    private func stackCard(_ item: IndexStackItem) -> some View {
        let code = item.code.uppercased()
        let quote = quotes[code]
        let live = RealtimeMerge.applyLive(
            close: item.close,
            pct: item.pct,
            quote: quote
        )
        // 会话 1m（live/local）优先；无则回退 strip 快照 sparkline
        let liveSeries = liveSparklines[code] ?? liveSparklines[RealtimeMerge.toLongbridgeSymbol(code) ?? ""]
        let usingLive = (liveSeries?.points.count ?? 0) >= 2
        let spark = usingLive ? liveSeries!.points : (item.sparkline ?? []).map(\.c)
        let hasSpark = spark.count >= 2
        // R2-U7 KTD7：仅 live 序列且带有效昨收时启用锚定模式；静态快照兜底沿用旧自适应缩放。
        let sparkAnchor: (yMin: Double, yMax: Double, prevClose: Double)? = usingLive
            ? SparklineYAxis.range(for: liveSeries!).map { (yMin: $0.yMin, yMax: $0.yMax, prevClose: liveSeries!.prevClose ?? 0) }
            : nil
        let absChg = absoluteChange(close: live.close, pct: live.pct, quote: quote)
        let sign = theme.signColor(live.pct)

        // 参考终端「名 / 大号价 / 涨跌额+涨跌幅 | 右侧分时」；无分时不占空底栏
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(item.name)
                    .font(KSSFont.themed(13, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                    .lineLimit(1)
                if live.isLive {
                    Text("实时")
                        .font(KSSFont.themed(9, .bold, theme: theme))
                        .foregroundStyle(theme.accent)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(theme.accent.opacity(0.12), in: Capsule())
                }
                Spacer(minLength: 4)
                if items.count > 1 {
                    Text("\(page % items.count + 1)/\(items.count)")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                Text(live.isLive ? "盘中" : dateLabel(item.date))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }

            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    LivePriceText(
                        value: live.close,
                        text: formatIndexPrice(live.close),
                        baseColor: sign,
                        isLive: live.isLive,
                        font: KSSFont.harmonyNumber(22)
                    )
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)

                    HStack(spacing: 6) {
                        LivePriceText(
                            value: absChg,
                            text: String(format: "%+.2f", absChg),
                            baseColor: sign,
                            isLive: live.isLive,
                            font: .system(size: 12, weight: .semibold, design: .monospaced)
                        )
                        .lineLimit(1)
                        LivePriceText(
                            value: live.pct,
                            text: String(format: "%+.2f%%", live.pct),
                            baseColor: sign,
                            isLive: live.isLive,
                            font: .system(size: 12, weight: .semibold, design: .monospaced)
                        )
                        .lineLimit(1)
                    }
                }
                .layoutPriority(1)

                if hasSpark {
                    IntradaySparkline(points: spark, height: 40, showEmptyPlaceholder: false, anchor: sparkAnchor)
                        .frame(width: 88, height: 40)
                        .layoutPriority(0)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }

    /// 涨跌额：优先 quote last−prev；否则由现价与涨跌幅反推昨收。
    private func absoluteChange(close: Double, pct: Double, quote: LongbridgeQuote?) -> Double {
        if let quote, quote.isLive, let last = quote.lastDone, let prev = quote.prevClose, prev > 0 {
            return last - prev
        }
        if pct <= -100 { return 0 }
        let prev = close / (1 + pct / 100.0)
        return close - prev
    }

    private func formatIndexPrice(_ value: Double) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 2
        f.minimumFractionDigits = 2
        f.groupingSeparator = ","
        f.usesGroupingSeparator = true
        return f.string(from: NSNumber(value: value)) ?? String(format: "%.2f", value)
    }

    private func dateLabel(_ raw: String?) -> String {
        guard let raw, !raw.isEmpty else { return "" }
        if raw.count == 8 {
            return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
        }
        return raw
    }
}

/// 计数卡：回测这类「只看数量、点击跳转」的内容，不占大版面。
struct CountCard: View {
    @Environment(\.kssTheme) private var theme
    var icon: String
    var count: Int
    var unit: String
    var label: String
    var onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Image(systemName: icon)
                        .font(KSSFont.themed(14, theme: theme))
                        .foregroundStyle(theme.accent)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(KSSFont.themed(10, .bold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text("\(count)")
                        .font(KSSFont.harmonyNumber(24))
                        .foregroundStyle(theme.textPrimary)
                    Text(unit)
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                Text(label)
                    .font(KSSFont.themed(12, .medium, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: 12)
        }
        .buttonStyle(.plain)
    }
}

/// 纸交易跟踪汇总卡：年化 / Sharpe / 回撤 / 胜率 / 样本。
struct TrackingSummaryCard: View {
    @Environment(\.kssTheme) private var theme
    var tracking: TrackingSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                metric("年化", KSSFormat.percent(tracking.annualized), theme.signColor(tracking.annualized))
                metric("Sharpe", KSSFormat.number(tracking.sharpe), theme.signColor(tracking.sharpe))
                metric("最大回撤", KSSFormat.percent(tracking.maxDrawdown), theme.signColor(tracking.maxDrawdown))
                metric("胜率", KSSFormat.percent(tracking.winRate), theme.textPrimary)
            }
            Divider().overlay(theme.hairline)
            HStack {
                Text("样本天数")
                    .font(KSSFont.themed(12, theme: theme)).foregroundStyle(theme.textSecondary)
                Spacer()
                Text("\(tracking.nDaysWithReturns) / \(tracking.nDaysLogged)")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textPrimary)
            }
            if let message = tracking.message {
                Text(message)
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
    }

    private func metric(_ label: String, _ value: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(KSSFont.themed(10, .medium, theme: theme)).tracking(0.5)
                .foregroundStyle(theme.textSecondary)
            Text(value)
                .font(KSSFont.harmonyNumber(19))
                .foregroundStyle(tint)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct SectionHeader: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var caption: String?

    init(_ title: String, caption: String? = nil) {
        self.title = title
        self.caption = caption
    }

    var body: some View {
        // Bold section title with a blurple accent bar + optional caption.
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(theme.accent)
                    .frame(width: 4, height: 18)
                Text(title)
                    .font(KSSFont.themed(18, .semibold, theme: theme, design: .serif))
                    .foregroundStyle(theme.textPrimary)
            }
            if let caption {
                Text(caption)
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
        }
        .padding(.top, 6)
    }
}

struct StatTile: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var value: String
    var tint: Color? = nil

    var body: some View {
        // Discord KPI tile: uppercase tracked muted label, display value, optional delta tint.
        VStack(alignment: .leading, spacing: 5) {
            Text(title.uppercased())
                .font(KSSFont.themed(10.5, .medium, theme: theme))
                .tracking(0.6)
                .foregroundStyle(theme.textSecondary)
            Text(value)
                .font(KSSFont.harmonyNumber(20))
                .foregroundStyle(tint ?? theme.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct RecommendationCard: View {
    @Environment(\.kssTheme) private var theme
    var item: Recommendation

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("#\(item.rank)")
                    .font(.system(size: 13, weight: .bold).monospacedDigit())
                    .foregroundStyle(theme.accent)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(theme.accent.opacity(0.15), in: Capsule())
                Spacer()
                StatusBadge.tracking(item.status)
            }
            Text(item.name.isEmpty ? item.symbol : item.name)
                .font(KSSFont.themed(17, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
            Text(item.symbol)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            HStack {
                LabeledMetric("权重", KSSFormat.percent(item.weight))
                LabeledMetric("跟踪", KSSFormat.percent(item.trackingReturn), tint: theme.signColor(item.trackingReturn))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct BJScanSection: View {
    @Environment(\.kssTheme) private var theme
    var scan: BJScan
    var watchlist: [String] = []
    var onSelect: (String) -> Void
    var onToggleWatchlist: (String) -> Void = { _ in }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                StatTile(title: "扫描日", value: bjDate(scan.scanDate))
                StatTile(title: "标的数", value: "\(scan.total)")
                StatTile(title: "通过筛选", value: "\(scan.passed)", tint: theme.accent)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 10)], spacing: 10) {
                ForEach(scan.top) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(item.name.isEmpty ? item.symbol : item.name)
                                .font(KSSFont.themed(14.5, .bold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Text(KSSFormat.number(item.score, digits: 2))
                                .font(.system(size: 13, weight: .heavy, design: .monospaced))
                                .foregroundStyle(theme.accent)
                            WatchlistStarButton(
                                isWatched: watchlist.contains(item.symbol)
                            ) { onToggleWatchlist(item.symbol) }
                        }
                        Text("\(item.symbol) · \(item.industry)")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(1)
                        HStack {
                            Text(item.tag)
                                .font(KSSFont.themed(10.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .lineLimit(1)
                            Spacer()
                            Text("20日 " + KSSFormat.percent(item.ret20d))
                                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                                .foregroundStyle(theme.signColor(item.ret20d))
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: 12)
                    .contentShape(Rectangle())
                    .onTapGesture { onSelect(item.symbol) }
                }
            }
        }
    }

    private func bjDate(_ raw: String?) -> String {
        guard let raw, raw.count == 8 else { return raw ?? "-" }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }
}

struct LabeledMetric: View {
    @Environment(\.kssTheme) private var theme
    var label: String
    var value: String
    var tint: Color?

    init(_ label: String, _ value: String, tint: Color? = nil) {
        self.label = label
        self.value = value
        self.tint = tint
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(theme.textSecondary)
            Text(value)
                .font(.callout.monospacedDigit())
                .foregroundStyle(tint ?? theme.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Surface NL（compose 弹层 + 确认真值，布局不撑高组件）

enum SurfaceBindEncoding {
    /// 将 interpret 返回的 ops 编成 surface-apply 所需 JSON 数组（snake_case 键）。
    static func encodeOps(_ ops: [SurfaceNlOp]) -> String? {
        var arr: [[String: Any]] = []
        for op in ops {
            guard let name = op.op else { continue }
            var d: [String: Any] = ["op": name]
            if let code = op.code { d["code"] = code }
            if let n = op.name { d["name"] = n }
            if let k = op.kind { d["kind"] = k }
            if let ks = op.kindSource { d["kind_source"] = ks }
            if let av = op.addedVia { d["added_via"] = av }
            if let pc = op.probeClose { d["probe_close"] = pc }
            if let mid = op.metricId { d["metric_id"] = mid }
            arr.append(d)
        }
        guard !arr.isEmpty,
              let data = try? JSONSerialization.data(withJSONObject: arr, options: []),
              let s = String(data: data, encoding: .utf8) else { return nil }
        return s
    }

    static func draft(
        from resp: SurfaceNlInterpretResponse,
        region: String
    ) -> (SurfaceBindDraft?, String?) {
        if resp.ok != true {
            var msg = resp.errorZh ?? resp.error ?? "无法解析"
            if let s = resp.suggestions, !s.isEmpty {
                msg += " · 例：" + s.prefix(3).joined(separator: "、")
            }
            return (nil, msg)
        }
        guard let ops = resp.ops, !ops.isEmpty,
              let opsJSON = encodeOps(ops) else {
            return (nil, "无可用操作")
        }
        let summary: String
        if resp.partial == true {
            summary = "部分成功：将应用 \(ops.count) 项（另有失败项见下）"
        } else if let first = resp.previews?.first?.label {
            summary = (resp.previews?.count ?? 0) <= 1
                ? first
                : "将执行 \(resp.previews?.count ?? ops.count) 项变更"
        } else {
            summary = "确认应用 \(ops.count) 项变更"
        }
        let draft = SurfaceBindDraft(
            region: region,
            summary: summary,
            opsJSON: opsJSON,
            previews: resp.previews ?? [],
            failed: resp.failed ?? resp.items?.filter { $0.status != "ok" } ?? [],
            partial: resp.partial == true
        )
        return (draft, nil)
    }
}

/// 组件旁 NL 确认 sheet：展示代码算出的真值行，确认后才 apply。
struct SurfaceBindConfirm: View {
    @Environment(\.kssTheme) private var theme
    let draft: SurfaceBindDraft
    var busy: Bool = false
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Image(systemName: "text.badge.checkmark")
                    .foregroundStyle(theme.accent)
                Text("确认绑定")
                    .font(KSSFont.themed(16, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
            }
            Text(draft.summary)
                .font(KSSFont.themed(13, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)

            if !draft.previews.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(draft.previews) { row in
                        previewRow(row)
                    }
                }
            }

            if !draft.failed.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(draft.partial ? "未纳入本次应用" : "失败项")
                        .font(KSSFont.themed(11, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    ForEach(Array(draft.failed.enumerated()), id: \.offset) { _, item in
                        Text(item.errorZh ?? item.error ?? item.token ?? "失败")
                            .font(KSSFont.themed(11, theme: theme))
                            .foregroundStyle(.red.opacity(0.85))
                    }
                }
            }

            Text("数字来自代码探针/配置，确认后写入 surface。")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)

            HStack {
                Spacer()
                Button("取消") { onCancel() }
                    .keyboardShortcut(.cancelAction)
                    .disabled(busy)
                Button("确认应用") { onConfirm() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(busy)
            }
        }
        .padding(22)
        .frame(width: 420)
        .background(theme.canvas)
        .opacity(busy ? 0.75 : 1)
    }

    @ViewBuilder
    private func previewRow(_ row: SurfaceNlPreview) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(row.label ?? row.title ?? "\(row.op ?? "") \(row.code ?? row.metricId ?? "")")
                .font(KSSFont.themed(13, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            HStack(spacing: 12) {
                if let code = row.code {
                    Text(code)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                if let close = row.close {
                    Text(String(format: "%.2f", close))
                        .font(KSSFont.harmonyNumber(14))
                        .foregroundStyle(theme.textPrimary)
                }
                if let pct = row.pct {
                    Text(String(format: "%+.2f%%", pct))
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.signColor(pct))
                }
                if let vt = row.valueText {
                    Text(vt)
                        .font(KSSFont.harmonyNumber(14))
                        .foregroundStyle(theme.textPrimary)
                }
                if let dt = row.deltaText, !dt.isEmpty {
                    Text(dt)
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer(minLength: 0)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
    }
}

// RealtimeStatusBadge / LivePriceText → Support/RealtimeChrome.swift
