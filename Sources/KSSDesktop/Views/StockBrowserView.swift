import SwiftUI
import AppKit

enum StockSort: String, CaseIterable, Identifiable {
    case symbol = "代码"
    case name = "名称"
    case pct = "涨跌幅"
    case close = "收盘价"
    var id: String { rawValue }
}

struct StockBrowserView: View {
    @Environment(\.kssTheme) private var theme
    var title: String
    var stocks: [StockSummary]
    var selectedSymbol: String?
    var detail: StockDetail?
    var enrichment: PerillaEnrichment?
    var watchlist: [String]
    @Binding var searchText: String
    var onSelect: (String) -> Void
    var onToggleWatchlist: (String) -> Void
    /// P1: BridgeClient from store（不在 view 内构造第二条 sidecar）
    var bridge: BridgeClient? = nil
    // 实时：选中股 quote + 四态 badge
    var realtimeQuotes: [String: LongbridgeQuote] = [:]
    var realtimeReceivedAtBySymbol: [String: Date] = [:]
    var tradingHours: TradingHours? = nil
    var realtimeAuthFailed: Bool = false
    var realtimeUpdatedAt: Date? = nil
    var onRetryRealtime: () -> Void = {}
    var onLoadRealtimeForSymbol: (String) -> Void = { _ in }
    /// 日线新鲜度：一键 update-cs-data
    var isRunningTask: Bool = false
    var onUpdateCsData: () -> Void = {}
    /// 可投资地图暴露数据与写回调（plan U6/U7）。
    var exposure = ExposureContext()

    @State private var sort: StockSort = .symbol
    @State private var ascending = true
    @State private var showChartFullscreen = false
    @State private var showImport = false
    @State private var showUpdateCsConfirm = false
    @State private var exposureFilter: ExposureFilter = .all

    private var filteredStocks: [StockSummary] {
        var items = stocks.filter {
            exposureFilter.matches(exposure.stock($0.symbol), loaded: exposure.loaded)
        }
        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            let q = trimmed.lowercased()
            items = items.filter {
                $0.symbol.lowercased().contains(q)
                    || $0.name.lowercased().contains(q)
                    || $0.industry.lowercased().contains(q)
                    || $0.concept.lowercased().contains(q)
            }
        }
        return items.sorted { a, b in
            switch sort {
            case .symbol: return ascending ? a.symbol < b.symbol : a.symbol > b.symbol
            case .name: return ascending ? a.name < b.name : a.name > b.name
            // R6 R6：涨跌幅排序与显示同口径——盘中命中实时 quote 用实时 pct
            case .pct:
                let pa = liveRow(a)?.pct ?? 0
                let pb = liveRow(b)?.pct ?? 0
                return ascending ? pa < pb : pa > pb
            case .close:
                let ca = liveRow(a)?.close ?? 0
                let cb = liveRow(b)?.close ?? 0
                return ascending ? ca < cb : ca > cb
            }
        }
    }

    /// 当前暴露色筛选的文本标签（筛选态永远有字，不靠色块表达）。
    private var exposureFilterLabel: String {
        ExposureFilter.options(palette: exposure.palette)
            .first { $0.0 == exposureFilter }?.1 ?? "全部"
    }

    /// 列表行展示口径（R6 R6）：盘中实时价/涨跌合并（与推荐页现价列同模式），
    /// 无 quote 回退快照日线值。
    private func liveRow(_ stock: StockSummary) -> (close: Double, pct: Double, isLive: Bool)? {
        RealtimeMerge.displayPrice(
            snapshotClose: stock.close,
            snapshotPct: stock.pctChange,
            quote: realtimeQuotes[stock.symbol.uppercased()]
        )
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    TextField("搜索代码 / 名称 / 行业", text: $searchText)
                        .textFieldStyle(.plain)
                        .font(KSSFont.themed(13, theme: theme))
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(theme.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                .padding(.horizontal, 12)
                .padding(.top, 12)

                HStack(spacing: 8) {
                    SortControl(
                        options: StockSort.allCases.map { ($0, $0.rawValue) },
                        selection: $sort,
                        ascending: $ascending
                    )
                    Spacer()
                    Button { showImport = true } label: {
                        Label("导入", systemImage: "plus.circle")
                            .font(KSSFont.themed(12, .semibold, theme: theme))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(theme.accent)
                    Text("\(filteredStocks.count)")
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)

                // 按暴露色筛选（R20）。筛选项以文本呈现——纯色块做可点目标，
                // 色盲用户与截图读者都无从下手。
                HStack(spacing: 6) {
                    Menu {
                        ForEach(Array(ExposureFilter.options(palette: exposure.palette).enumerated()),
                                id: \.offset) { _, option in
                            Button {
                                exposureFilter = option.0
                            } label: {
                                Label(option.1,
                                      systemImage: exposureFilter == option.0 ? "checkmark" : "")
                            }
                        }
                    } label: {
                        Text("暴露色 · \(exposureFilterLabel)")
                            .font(KSSFont.themed(11.5, .semibold, theme: theme))
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    .foregroundStyle(exposureFilter == .all ? theme.textSecondary : theme.accent)
                    if exposureFilter != .all {
                        Button("清除") { exposureFilter = .all }
                            .buttonStyle(.plain)
                            .font(KSSFont.themed(11, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                    Spacer()
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 6)

                // column header
                HStack(spacing: 6) {
                    SortHeaderCell(title: "名称 / 代码", key: StockSort.name, selection: $sort, ascending: $ascending,
                                   alignment: .leading)
                    SortHeaderCell(title: "涨跌幅", key: StockSort.pct, selection: $sort, ascending: $ascending,
                                   alignment: .trailing, width: 64)
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 4)

                List(filteredStocks) { stock in
                    let isOn = stock.symbol == selectedSymbol
                    let ref = tradingHours?.referenceTradeDate
                    let barFresh = DailyBarFreshness.status(
                        barDate: stock.latestDate,
                        referenceTradeDate: ref
                    )
                    // 行选与陈旧「更新」必须是兄弟 hit target：外层 Button 包住内层 Button
                    // 时 macOS List 会吞掉内层点击（只触发 onSelect）。
                    HStack(alignment: .center, spacing: 6) {
                        Button { onSelect(stock.symbol) } label: {
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 6) {
                                    Text(stock.name.isEmpty ? stock.symbol : stock.name)
                                        .font(KSSFont.themed(15, .bold, theme: theme))
                                        .foregroundStyle(isOn ? theme.accent : theme.textPrimary)
                                        .lineLimit(1)
                                    if watchlist.contains(stock.symbol) {
                                        Image(systemName: "star.fill")
                                            .font(KSSFont.themed(10, theme: theme))
                                            .foregroundStyle(theme.ma5)
                                    }
                                    Spacer()
                                    // R6 R6：盘中实时涨跌（小圆点标识）；无 quote 回退日线值
                                    if let live = liveRow(stock) {
                                        if live.isLive {
                                            Circle()
                                                .fill(theme.accent)
                                                .frame(width: 5, height: 5)
                                                .help("实时")
                                        }
                                        Text(KSSFormat.pctPoints(live.pct))
                                            .font(.system(size: 12.5, weight: .bold, design: .monospaced))
                                            .foregroundStyle(theme.signColor(live.pct))
                                    } else {
                                        Text(KSSFormat.pctPoints(stock.pctChange))
                                            .font(.system(size: 12.5, weight: .bold, design: .monospaced))
                                            .foregroundStyle(theme.signColor(stock.pctChange))
                                    }
                                }
                                Text("\(stock.symbol) · \(stock.industry)")
                                    .font(.system(size: 11.5, design: .monospaced))
                                    .foregroundStyle(theme.textSecondary)
                                    .lineLimit(1)
                                // 徽标进既有的名称与代码竖排，不新开一列（表头列宽是重复字面量）
                                ExposureBadge(
                                    exposure: exposure.stock(stock.symbol),
                                    loaded: exposure.loaded,
                                    showsDotWhenUnlabelled: false
                                )
                            }
                            .padding(.vertical, 2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                            .opacity(barFresh == .stale ? 0.95 : 1)
                        }
                        .buttonStyle(.plain)
                        // 未上图灰点：可点补标。与行按钮平级——嵌套进去会被 List 吞掉点击。
                        ExposureMarkButton(
                            exposure: exposure.stock(stock.symbol),
                            loaded: exposure.loaded
                        ) { exposure.onMark(stock.symbol) }
                        // 日线末日期 / 陈旧点（陈旧可点 → 确认日更）；独立 Button 不嵌套
                        DailyFreshnessLabel(
                            barDate: stock.latestDate,
                            referenceTradeDate: ref,
                            compact: true,
                            isRunning: isRunningTask,
                            onRequestUpdate: { showUpdateCsConfirm = true }
                        )
                    }
                    .listRowBackground(
                        isOn
                            ? theme.accent.opacity(0.16)
                            : (barFresh == .stale ? theme.ma5.opacity(0.08) : Color.clear)
                    )
                }
                .scrollContentBackground(.hidden)
                .background(theme.canvas)
            }
            .frame(width: 300)

            Divider().overlay(theme.hairline)

            Group {
                if let detail {
                    StockDetailView(
                        detail: detail,
                        enrichment: enrichment?.symbol == detail.symbol ? enrichment : nil,
                        isWatched: watchlist.contains(detail.symbol),
                        onToggleWatchlist: { onToggleWatchlist(detail.symbol) },
                        onZoom: { showChartFullscreen = true },
                        bridge: bridge,
                        liveQuote: realtimeQuotes[detail.symbol.uppercased()],
                        liveReceivedAt: realtimeReceivedAtBySymbol[detail.symbol.uppercased()],
                        tradingHours: tradingHours,
                        realtimeAuthFailed: realtimeAuthFailed,
                        realtimeUpdatedAt: realtimeUpdatedAt,
                        onRetryRealtime: onRetryRealtime,
                        isRunningTask: isRunningTask,
                        onRequestUpdateCsData: { showUpdateCsConfirm = true },
                        exposure: exposure
                    )
                    .onAppear { onLoadRealtimeForSymbol(detail.symbol) }
                    .onChange(of: detail.symbol) { _, sym in onLoadRealtimeForSymbol(sym) }
                } else {
                    Text("选择一只股票查看详情")
                        .font(KSSFont.themed(14, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(theme.canvas)
        }
        .background(theme.canvas)
        .sheet(isPresented: $showImport) {
            ImportStocksView { showImport = false }
        }
        .confirmationDialog(
            "更新日线数据",
            isPresented: $showUpdateCsConfirm,
            titleVisibility: .visible
        ) {
            Button("开始更新") {
                guard !isRunningTask else { return }
                onUpdateCsData()
            }
            .disabled(isRunningTask)
            Button("取消", role: .cancel) {}
        } message: {
            Text("将全池更新日线，约数分钟。任务进行中请勿重复点击。")
        }
        // 放大：铺满整个浏览区（列表+详情，随窗口尺寸动态最大化），而非尺寸受限的 sheet。
        .overlay {
            if showChartFullscreen, let detail {
                ChartFullscreenView(
                    detail: detail,
                    bridge: bridge,
                    liveQuote: realtimeQuotes[detail.symbol.uppercased()],
                    tradingHours: tradingHours
                ) { showChartFullscreen = false }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(theme.canvas)
                    .transition(.opacity)
            }
        }
    }
}

/// 个股档案区的四段：复盘结论 / 紫苏叶富化 / 可投资地图 / 8 问尽调。
/// 原来是四张卡竖着摞，且不是每只票都齐——摞出来行情图被推到一屏之外，缺的那几段
/// 还留着空位。收进一个 block 用 Tab 手动切：没内容的段不出 Tab，录入面永远在。
enum StockDossierSection: String, CaseIterable, Hashable {
    case review
    case perilla
    case exposure
    case questions

    var label: String {
        switch self {
        case .review: return "复盘结论"
        case .perilla: return "紫苏叶富化"
        case .exposure: return "可投资地图"
        case .questions: return "8 问尽调"
        }
    }

    /// 可见分段（纯函数，可单测）。复盘与富化按有无内容决定；地图与 8 问是录入面，
    /// 空着也得能进去补标注/答题，所以恒在——「没数据」和「不能录」是两回事。
    static func available(hasReview: Bool, hasEnrichment: Bool) -> [StockDossierSection] {
        var out: [StockDossierSection] = []
        if hasReview { out.append(.review) }
        if hasEnrichment { out.append(.perilla) }
        out.append(.exposure)
        out.append(.questions)
        return out
    }

    /// 选中项落位：换股后原选中段可能不存在（上只票有复盘、这只没有），
    /// 回落到第一个可见段，不把用户留在空段上。存在则保持——扫自选时 Tab 是粘的。
    static func resolve(selected: StockDossierSection,
                        available: [StockDossierSection]) -> StockDossierSection {
        available.contains(selected) ? selected : (available.first ?? .exposure)
    }

    /// Tab 上的待办点：未上图、8 问没答满，不用逐个点进去看。
    /// 标注字典没加载完不点——那时候 nil 只代表「还不知道」。
    static func pending(hasPrimaryNode: Bool,
                        exposureLoaded: Bool,
                        decided: Int,
                        total: Int) -> Set<StockDossierSection> {
        guard exposureLoaded else { return [] }
        var out: Set<StockDossierSection> = []
        if !hasPrimaryNode { out.insert(.exposure) }
        if decided < total { out.insert(.questions) }
        return out
    }
}

struct StockDetailView: View {
    @Environment(\.kssTheme) private var theme
    var detail: StockDetail
    var enrichment: PerillaEnrichment?
    var isWatched: Bool
    var onToggleWatchlist: () -> Void
    var onZoom: () -> Void
    /// P1: BridgeClient 注入（不在 view 内构造——共用 store 的单桥模式）
    var bridge: BridgeClient? = nil
    var liveQuote: LongbridgeQuote? = nil
    /// 该标的自己的本地接收时间，供 sourceAsofTs 缺失时按标的独立回退（KTD1）
    var liveReceivedAt: Date? = nil
    var tradingHours: TradingHours? = nil
    var realtimeAuthFailed: Bool = false
    var realtimeUpdatedAt: Date? = nil
    var onRetryRealtime: () -> Void = {}
    var isRunningTask: Bool = false
    var onRequestUpdateCsData: () -> Void = {}
    /// 可投资地图：节点路径卡与 8 问卡（plan U7 / R21）。
    var exposure = ExposureContext()
    // U3 分钟 K 线模式（R7/R15）
    @State private var chartMode: ChartDataMode = .daily
    @State private var intradayBars: IntradayBars? = nil
    @State private var intradayLoading = false
    @State private var intradayError: String? = nil
    /// 档案区选中段。View 不随换股重建（只有图表 VStack 带 .id），所以这份选择跨标的保留。
    @State private var dossierSection: StockDossierSection = .review

    private var analysis: StockAnalysis {
        StockAnalysis(points: detail.history, latest: detail.latest)
    }

    /// R6 R8：图表用日线序列（盘中拼当日未收盘 bar，EOD 库不动）。
    private var chartPoints: [PricePoint] {
        Self.appendingProvisionalBar(
            to: detail.history,
            quote: liveQuote,
            isTradingSession: tradingHours?.isTradingSession ?? false,
            today: Self.shanghaiToday()
        )
    }

    /// 上海时区今日（YYYY-MM-DD）。
    static func shanghaiToday(now: Date = Date()) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai")
        return f.string(from: now)
    }

    /// R6 KTD3：盘中把当日实时 quote 拼成日 K 末尾的未收盘 bar（纯函数，可单测）。
    /// 门控矩阵：非交易时段 / quote 非 live / 缺任一 OHLC / sourceAsofTs 日期 ≠ today
    /// （或缺失）/ 序列末行已是 today —— 任一命中都不拼，原样返回。
    static func appendingProvisionalBar(
        to points: [PricePoint],
        quote: LongbridgeQuote?,
        isTradingSession: Bool,
        today: String
    ) -> [PricePoint] {
        guard isTradingSession,
              let quote, quote.isLive,
              let o = quote.open, let h = quote.high, let l = quote.low,
              let c = quote.lastDone else { return points }
        // 跨 session 门控：quote 无 sessionDate 字段，用 sourceAsofTs（上海 ISO）的日期分量
        guard let asof = quote.sourceAsofTs, asof.hasPrefix(today) else { return points }
        // 数据晚到 EOD 已含当日行：不重复拼
        if points.last?.date == today { return points }
        // 涨跌以昨收为锚；quote.prevClose 缺失时用序列末收盘反推（prevCloseFallback 同式）
        var pct: Double?
        if let prev = quote.prevClose, prev > 0 {
            pct = (c - prev) / prev * 100.0
        } else if let prev = points.last?.close, prev > 0 {
            pct = (c - prev) / prev * 100.0
        }
        let bar = PricePoint(
            date: today, open: o, high: h, low: l, close: c,
            pctChange: pct, volume: quote.volume ?? 0, amount: quote.turnover,
            provisional: true
        )
        return points + [bar]
    }

    /// 日线末日期：summary → history 末日（北证等无 summary 时回退）
    private var barLatestDate: String? {
        if let d = detail.latest?.latestDate, !d.isEmpty { return d }
        return detail.history.last?.date
    }

    /// 推给 chart 状态条；失败时主图仍为日线。
    private var chartStatusText: String? {
        if chartMode == .daily { return nil }
        if intradayLoading { return "加载分钟线…" }
        if let err = intradayError { return err }
        if let bars = intradayBars, bars.isRenderable {
            // 来源标签：本地 / 源·部分 / 源
            if let label = bars.sourceLabel {
                let tf = chartMode == .m5 ? "5分" : "1分"
                return "\(label) · \(tf)"
            }
            return nil
        }
        return nil
    }

    /// 分钟档空态卡原因（R6/KTD7，plan 2026-07-14-001）：K 线区 #empty 的显式文案，
    /// 复用 loadIntraday 已分类的错误（无覆盖/无本地存档/网络失败）。
    private var intradayEmptyReason: String? {
        if intradayLoading { return "加载分钟线…" }
        if let err = intradayError { return "分钟数据不可用：\(err)（可点上方「重试」）" }
        return "暂无分钟数据 · 切回日线查看走势"
    }

    private var freshness: RealtimeFreshness {
        RealtimeFreshness.status(sourceAsofTs: liveQuote?.sourceAsofTs, fallbackReceivedAt: liveReceivedAt, now: Date())
    }

    private var liveClose: (close: Double, pct: Double, isLive: Bool)? {
        guard let latest = detail.latest, let close = latest.close else { return nil }
        return RealtimeMerge.applyLive(
            close: close,
            pct: latest.pctChange ?? 0,
            quote: liveQuote
        )
    }

    private var dossierSections: [StockDossierSection] {
        StockDossierSection.available(
            hasReview: detail.reviewConclusion != nil
                || detail.miSignal != nil
                || !(detail.indicatorSignals ?? []).isEmpty,
            hasEnrichment: enrichment != nil
        )
    }

    /// 档案区：四段收进一个 block，Tab 手动切。段内各卡不再自带外框与标题
    /// （Tab 已经报了名），只留自己的徽标/操作，避免框套框。
    @ViewBuilder
    private var dossierBlock: some View {
        let sections = dossierSections
        let active = StockDossierSection.resolve(selected: dossierSection, available: sections)
        let stock = exposure.stock(detail.symbol)
        VStack(alignment: .leading, spacing: 12) {
            KSSSegmentedControl(
                options: sections.map { (key: $0, label: $0.label) },
                selection: Binding(get: { active }, set: { dossierSection = $0 }),
                badgedKeys: StockDossierSection.pending(
                    hasPrimaryNode: stock?.primaryNode != nil,
                    exposureLoaded: exposure.loaded,
                    decided: stock?.zone.decided ?? 0,
                    total: ExposureAnswers.questionCount
                )
            )
            switch active {
            case .review:
                StockReviewSection(
                    review: detail.reviewConclusion,
                    miSignal: detail.miSignal,
                    indicatorSignals: detail.indicatorSignals
                )
            case .perilla:
                if let enrichment {
                    PerillaEnrichmentSection(data: enrichment)
                }
            case .exposure:
                ExposurePathSection(
                    symbol: detail.symbol,
                    exposure: stock,
                    loaded: exposure.loaded,
                    axes: exposure.axes,
                    palette: exposure.palette,
                    onEdit: { exposure.onMark(detail.symbol) },
                    onRemoveSecondary: { nodeId in
                        let primary = stock?.primaryNode?.nodeId ?? ""
                        let seconds = (stock?.secondaryNodes ?? [])
                            .map(\.nodeId).filter { $0 != nodeId }
                        exposure.onSetLabel(detail.symbol, primary, seconds)
                    },
                    onDeletePrimary: { exposure.onSetLabel(detail.symbol, "", []) }
                )
            case .questions:
                ExposureQuestionsSection(
                    symbol: detail.symbol,
                    exposure: stock,
                    loaded: exposure.loaded,
                    onAnswer: { question, value in
                        await exposure.onSetAnswer(detail.symbol, question, value)
                    },
                    draft: exposure.drafts[detail.symbol],
                    isDrafting: exposure.draftingSymbol == detail.symbol,
                    onRequestDraft: { exposure.onDraftAnswers(detail.symbol) }
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(detail.name.isEmpty ? detail.symbol : detail.name)
                            .font(KSSFont.themed(30, .bold, theme: theme, design: .serif))
                            .foregroundStyle(theme.textPrimary)
                        Text("\(detail.symbol) · \(detail.industry)")
                            .font(.system(size: 14, weight: .medium, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 8) {
                        // R6 R7：页头只留一个综合新鲜度徽标——「日线截至」与「实时」并列
                        // 曾造成同屏口径矛盾（07-15 实测），底稿日期收敛到行情区块头唯一一处。
                        RealtimeStatusBadge(
                            freshness: freshness,
                            hours: tradingHours,
                            authFailed: realtimeAuthFailed,
                            updatedAt: realtimeUpdatedAt,
                            onRetry: onRetryRealtime
                        )
                        Button(action: onToggleWatchlist) {
                            Label(isWatched ? "取消自选" : "加自选", systemImage: isWatched ? "star.fill" : "star")
                                .font(KSSFont.themed(13, .semibold, theme: theme))
                        }
                        .tint(theme.accent)
                    }
                }

                if let latest = detail.latest, let snapClose = latest.close {
                    let live = liveClose ?? (close: snapClose, pct: latest.pctChange ?? 0, isLive: false)
                    let isFreshLive = live.isLive && freshness == .fresh
                    HStack(spacing: 10) {
                        LiveStatTile(
                            title: live.isLive ? (freshness == .stale ? "已过期" : "现价") : "收盘",
                            value: live.close,
                            text: KSSFormat.number(live.close),
                            tint: freshness == .stale ? theme.ma5 : theme.signColor(live.pct),
                            isLive: isFreshLive
                        )
                        LiveStatTile(
                            title: "涨跌",
                            value: live.pct,
                            text: KSSFormat.pctPoints(live.pct),
                            tint: freshness == .stale ? theme.ma5 : theme.signColor(live.pct),
                            isLive: isFreshLive
                        )
                        StatTile(title: "MA5 / MA20", value: "\(KSSFormat.number(latest.ma5)) / \(KSSFormat.number(latest.ma20))")
                        StatTile(title: "成交额", value: KSSFormat.compactMoney(latest.amount))
                    }
                }

                dossierBlock

                SectionHeader("分析指标")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 10)], spacing: 10) {
                    StatTile(title: "20日收益", value: KSSFormat.percent(analysis.return20), tint: theme.signColor(analysis.return20))
                    StatTile(title: "60日收益", value: KSSFormat.percent(analysis.return60), tint: theme.signColor(analysis.return60))
                    StatTile(title: "20日波动", value: KSSFormat.percent(analysis.volatility20))
                    StatTile(title: "60日回撤", value: KSSFormat.percent(analysis.maxDrawdown60), tint: theme.signColor(analysis.maxDrawdown60))
                    StatTile(title: "距20日高点", value: KSSFormat.percent(analysis.distanceToHigh20), tint: theme.signColor(analysis.distanceToHigh20))
                    StatTile(title: "MA20偏离", value: KSSFormat.percent(analysis.ma20Distance), tint: theme.signColor(analysis.ma20Distance))
                }

                HStack {
                    SectionHeader("行情")
                    Spacer()
                    // 图区旁再挂一次日线截至日，便于扫图时看到底稿日期
                    DailyFreshnessLabel(
                        barDate: barLatestDate,
                        referenceTradeDate: tradingHours?.referenceTradeDate,
                        compact: false,
                        isRunning: isRunningTask,
                        onRequestUpdate: onRequestUpdateCsData
                    )
                    if chartMode != .daily {
                        if intradayLoading {
                            ProgressView().controlSize(.small)
                            Text("加载分钟线…")
                                .font(.caption2)
                                .foregroundStyle(theme.textSecondary)
                        } else if let err = intradayError {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(KSSFont.themed(11, theme: theme))
                                .foregroundStyle(theme.ma5)
                            Text(err)
                                .font(.caption2)
                                .foregroundStyle(theme.textSecondary)
                                .lineLimit(1)
                            Button("重试") {
                                Task { await loadIntraday(symbol: detail.symbol, mode: chartMode) }
                            }
                            .font(.caption2.weight(.semibold))
                            .buttonStyle(.plain)
                            .foregroundStyle(theme.accent)
                        }
                    }
                    Button {
                        onZoom()
                    } label: {
                        Label("放大", systemImage: "arrow.up.left.and.arrow.down.right")
                            .font(KSSFont.themed(12.5, .semibold, theme: theme))
                    }
                    .buttonStyle(.bordered)
                    .tint(theme.accent)
                }
                VStack(alignment: .leading, spacing: 0) {
                    // TF 全部在 chart.html 内（1分/5分/日/周/月/年）；主图始终全高
                    ChartLegend()
                    // MI 横幅放在图区外（图例与 K 线之间），绝不叠在 OHLC/K 线上
                    if chartMode == .daily, let mi = detail.miSignal,
                       mi.status == "ok" || mi.status == nil || mi.status == "stale" {
                        MiChartBanner(signal: mi, markerCount: detail.miOverlay?.markers?.count ?? 0)
                    }
                    ChartWebView(
                        points: chartPoints,
                        // R6/KTD7：分钟档无数据时传空数组（保持 intraday 分支 → #empty 空态卡），
                        // 不再传 nil 静默回落日线渲染——那正是「点了没反应」的根源。
                        intradayBars: chartMode != .daily
                            ? (intradayBars?.isRenderable == true ? (intradayBars?.bars ?? []) : [])
                            : nil,
                        activeMode: chartMode,
                        statusText: chartStatusText,
                        emptyReason: chartMode != .daily ? intradayEmptyReason : nil,
                        miOverlayJSON: Self.encodeMiOverlay(detail.miOverlay),
                        indicatorOverlaysJSON: Self.encodeIndicatorOverlays(detail.indicatorOverlays),
                        onSelectMode: { mode in
                            chartMode = mode
                            if mode == .daily {
                                intradayBars = nil
                                intradayError = nil
                                intradayLoading = false
                            } else {
                                Task { await loadIntraday(symbol: detail.symbol, mode: mode) }
                            }
                        }
                    )
                    .frame(minHeight: 600)
                }
                .id(detail.symbol)  // 切换标的时重建 chart
                .frame(height: 720)
                .background(theme.chartSurface)
                .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: theme.cardRadius)
                        .stroke(theme.hairline)
                )

                // MI 已并入上方「复盘结论」；此处不再重复 MISignalCard

                if !detail.concept.isEmpty {
                    Text(detail.concept)
                        .font(KSSFont.themed(13.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
    }
}

/// Large interactive K-line view. Mouse-wheel zoom and drag-pan work here
/// without the surrounding ScrollView intercepting the wheel.
/// 个股复盘结论卡：每日复盘标题/预期/建议 + MI 滚动信号（同源 Signal Pack）。
/// 复盘结论段（档案区 Tab 之一）。标题由 Tab 承担，这里只留 headline 与日期徽标。
struct StockReviewSection: View {
    @Environment(\.kssTheme) private var theme
    var review: StockReview?
    var miSignal: MISignal? = nil
    /// 通用指标信号（plan 2026-07-12-004 U6/U7）；MI 也会在这里出现一份，与上面的 miSignal
    /// 并存展示不冲突——迁移期两套并行，MI 卡片沿用既有 miBlock 排版不变。
    var indicatorSignals: [IndicatorSignal]? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if hasMetaRow {
                HStack(spacing: 8) {
                    if let review, !review.headline.isEmpty {
                        Text(review.headline)
                            .font(KSSFont.themed(11.5, .semibold, theme: theme))
                            .foregroundStyle(theme.onAccent)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(theme.accent, in: Capsule())
                            .lineLimit(1)
                    }
                    Spacer()
                    if let review {
                        StatusBadge(icon: "calendar", text: review.date, tint: theme.accent)
                    } else if let asof = miSignal?.asof {
                        StatusBadge(icon: "calendar", text: asof, tint: theme.accent)
                    }
                }
            }

            if let review, !review.snapshot.isEmpty || !review.expectation.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    if !review.snapshot.isEmpty {
                        Text(review.snapshot)
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(theme.textBody)
                    }
                    if !review.expectation.isEmpty {
                        Text("预期区间 · " + review.expectation)
                            .font(KSSFont.themed(12.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            if let review, !review.suggestions.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(review.suggestions.enumerated()), id: \.offset) { _, s in
                        HStack(alignment: .top, spacing: 7) {
                            Circle().fill(theme.accent).frame(width: 5, height: 5).padding(.top, 6)
                            Text(s)
                                .font(KSSFont.themed(13, theme: theme))
                                .foregroundStyle(theme.textBody)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            // MI 滚动信号：与复盘页 Markdown 段同源（Signal Pack）
            if let mi = miSignal {
                Divider().opacity(0.5)
                miBlock(mi)
            }

            // 通用指标信号（排除 "mi"——已由上面的 miBlock 展示，避免重复）
            ForEach(genericSignals) { sig in
                Divider().opacity(0.5)
                indicatorBlock(sig)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// headline 与日期都没有就不画这一行，免得留一道空白。
    private var hasMetaRow: Bool {
        if let review { return !review.headline.isEmpty || !review.date.isEmpty }
        return miSignal?.asof != nil
    }

    private var genericSignals: [IndicatorSignal] {
        (indicatorSignals ?? []).filter { $0.indicatorId != "mi" }
    }

    @ViewBuilder
    private func indicatorBlock(_ signal: IndicatorSignal) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(signal.indicatorId ?? "指标")
                    .font(KSSFont.themed(13, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                if signal.unpinned == true {
                    Text("默认参数·未钉死")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.orange)
                }
                Spacer()
            }
            if let st = signal.status, st != "ok" {
                Text("状态 \(st)：\(signal.reason ?? "")")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textSecondary)
            } else {
                let pred = signal.predScore.map { String(format: "%.2f", $0) } ?? "—"
                Text("\(signal.action ?? "—") · \(signal.position ?? "") · pred \(pred)")
                    .font(KSSFont.themed(14, .semibold, theme: theme))
                if let sentence = signal.ruleSentence, !sentence.isEmpty {
                    Text(sentence)
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                }
                Text("asof \(signal.asof ?? "—")")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                if let prev = signal.prevAction {
                    Text("昨动作 \(prev) → \(signal.action ?? "")")
                        .font(.system(size: 11))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
    }

    @ViewBuilder
    private func miBlock(_ signal: MISignal) -> some View {
        let pred: String = {
            guard let s = signal.predScore else { return "—" }
            return String(format: "%.2f", s)
        }()
        let nText = signal.n.map(String.init) ?? "—"
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("MI 滚动信号")
                    .font(KSSFont.themed(13, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                if signal.unpinned == true {
                    Text("默认形态·未钉死")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.orange)
                }
                Spacer()
            }
            if let st = signal.status, st != "ok" {
                Text("状态 \(st)：\(signal.reason ?? "")")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textSecondary)
            } else {
                Text("\(signal.action ?? "—") · \(signal.position ?? "") · pred \(pred)")
                    .font(KSSFont.themed(14, .semibold, theme: theme))
                if let r = signal.reason, !r.isEmpty {
                    Text(r)
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                }
                Text("N=\(nText) · \(signal.entry ?? "") / \(signal.exitRule ?? "") · asof \(signal.asof ?? "—")")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                if let prev = signal.prevAction {
                    Text("昨动作 \(prev) → \(signal.action ?? "")")
                        .font(.system(size: 11))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
    }
}

/// 紫苏叶个股富化段：机构持仓动态 / PE 分位 / 美股对标。缺失项显示「暂不可用」。
struct PerillaEnrichmentSection: View {
    @Environment(\.kssTheme) private var theme
    var data: PerillaEnrichment

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let tier = data.tier {
                HStack(spacing: 8) {
                    Text(tier == "core" ? "核心" : "国产替代主线")
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                        .foregroundStyle(theme.onAccent)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(theme.accent, in: Capsule())
                    Spacer()
                }
            }

            row("机构持仓动态", institutionalText)
            row("PE 估值", peText)
            row("美股对标", usPeerText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func row(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Text(value)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textBody)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private static let unavailable = "暂不可用"

    private var institutionalText: String {
        guard let inst = data.institutional else { return Self.unavailable }
        var parts: [String] = []
        if let t = inst.top10, t.status == "ok" {
            let dir = ["increasing": "整体增持", "decreasing": "整体减持", "flat": "增减相当"][t.netDirection ?? ""] ?? ""
            var line = "前十大流通股东\(t.latestPeriod.map { " " + $0 } ?? "")："
            if let inst = t.instRatio { line += String(format: "机构持仓 %.1f%% · ", inst) }
            if let all = t.top10Ratio { line += String(format: "合计 %.1f%% · ", all) }
            line += "增 \(t.nIncreasing ?? 0) / 减 \(t.nDecreasing ?? 0) \(dir)"
            parts.append(line)
        }
        if let nb = inst.northbound, nb.status == "ok", let r = nb.holdRatio {
            let dir = ["increasing": "↑", "decreasing": "↓", "flat": "→"][nb.direction ?? ""] ?? ""
            let qoq = nb.qoqChange.map { String(format: " 环比%+.2f", $0) } ?? ""
            parts.append(String(format: "北向 %.2f%% %@%@", r, dir, qoq))
        }
        return parts.isEmpty ? Self.unavailable : parts.joined(separator: "\n")
    }

    private var peText: String {
        guard let pe = data.valuationPe, pe.status == "ok", let v = pe.peTtm else { return Self.unavailable }
        let pct = pe.percentile.map { String(format: " · 历史分位 %.0f%%", $0 * 100) } ?? ""
        return String(format: "PE_TTM %.1f%@", v, pct)
    }

    private var usPeerText: String {
        guard let up = data.usPeer else { return Self.unavailable }
        switch up.status {
        case "no_peer": return "无干净美股对标"
        case "ok":
            var s = "\(up.ticker ?? "")\(up.name.map { " " + $0 } ?? "")"
            if let p = up.peerPe { s += String(format: " · 对标PE %.1f", p) }
            if let m = up.peerToAMcapMultiple { s += String(format: " · 对标市值 %.1f×A股", m) }
            if let r = up.peRatioAOverPeer { s += String(format: " · A/对标PE %.2f", r) }
            return s
        default: return Self.unavailable
        }
    }
}

struct ChartFullscreenView: View {
    @Environment(\.kssTheme) private var theme
    var detail: StockDetail
    var bridge: BridgeClient? = nil
    // R6 R8：放大视图与详情页同口径拼当日未收盘 bar
    var liveQuote: LongbridgeQuote? = nil
    var tradingHours: TradingHours? = nil
    var onClose: () -> Void

    // R4-1：放大图表分钟线接线——与详情页同语义的独立状态（全屏是独立 chart 实例）。
    @State private var chartMode: ChartDataMode = .daily
    @State private var intradayBars: IntradayBars? = nil
    @State private var intradayError: String? = nil
    @State private var intradayLoading = false

    private var statusText: String? {
        if chartMode == .daily { return nil }
        if intradayLoading { return "加载分钟线…" }
        if let err = intradayError { return err }
        if let bars = intradayBars, bars.isRenderable, let label = bars.sourceLabel {
            return "\(label) · \(chartMode == .m5 ? "5分" : "1分")"
        }
        return nil
    }

    private var emptyReason: String? {
        guard chartMode != .daily else { return nil }
        if intradayLoading { return "加载分钟线…" }
        if let err = intradayError { return "分钟数据不可用：\(err)" }
        return "暂无分钟数据 · 切回日线查看走势"
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(detail.name.isEmpty ? detail.symbol : detail.name)
                        .font(KSSFont.themed(20, .heavy, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text("\(detail.symbol) · 滚轮缩放 · 拖动平移")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                if chartMode != .daily, let err = intradayError {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.ma5)
                    Text(err)
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
                Spacer()
                Button {
                    onClose()
                } label: {
                    Label("关闭", systemImage: "xmark")
                        .font(KSSFont.themed(13, .semibold, theme: theme))
                }
                .buttonStyle(.bordered)
                .keyboardShortcut(.cancelAction)
            }
            .padding(14)
            ChartLegend()
            // 与详情页一致：MI 状态在图外，全屏也不压 TF 钮
            if let mi = detail.miSignal,
               mi.status == "ok" || mi.status == nil || mi.status == "stale" {
                MiChartBanner(signal: mi, markerCount: detail.miOverlay?.markers?.count ?? 0)
            }
            ChartWebView(
                points: StockDetailView.appendingProvisionalBar(
                    to: detail.history,
                    quote: liveQuote,
                    isTradingSession: tradingHours?.isTradingSession ?? false,
                    today: StockDetailView.shanghaiToday()
                ),
                intradayBars: chartMode != .daily
                    ? (intradayBars?.isRenderable == true ? (intradayBars?.bars ?? []) : [])
                    : nil,
                activeMode: chartMode,
                statusText: statusText,
                emptyReason: emptyReason,
                miOverlayJSON: StockDetailView.encodeMiOverlay(detail.miOverlay),
                indicatorOverlaysJSON: StockDetailView.encodeIndicatorOverlays(detail.indicatorOverlays),
                onSelectMode: { mode in
                    chartMode = mode
                    if mode == .daily {
                        intradayBars = nil
                        intradayError = nil
                        intradayLoading = false
                    } else {
                        Task { await loadIntraday(mode: mode) }
                    }
                }
            )
        }
        // 作为浏览区上的覆盖层铺满：随 app 窗口尺寸动态最大化。
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.chartSurface)
    }

    private func loadIntraday(mode: ChartDataMode) async {
        intradayLoading = true; intradayError = nil; intradayBars = nil
        defer { intradayLoading = false }
        guard let bridge else { intradayError = "无法定位 bridge"; return }
        let (bars, err) = await StockDetailView.fetchIntraday(
            bridge: bridge, symbol: detail.symbol, mode: mode)
        intradayBars = bars
        intradayError = err
    }
}

struct StockAnalysis {
    var return20: Double?
    var return60: Double?
    var volatility20: Double?
    var maxDrawdown60: Double?
    var distanceToHigh20: Double?
    var ma20Distance: Double?

    init(points: [PricePoint], latest: StockSummary?) {
        let closes = points.map(\.close)
        self.return20 = Self.periodReturn(closes: closes, lookback: 20)
        self.return60 = Self.periodReturn(closes: closes, lookback: 60)
        self.volatility20 = Self.annualizedVolatility(points: points, lookback: 20)
        self.maxDrawdown60 = Self.maxDrawdown(closes: Array(closes.suffix(60)))
        if let close = closes.last, let high20 = latest?.high20, high20 > 0 {
            self.distanceToHigh20 = close / high20 - 1
        }
        if let close = closes.last, let ma20 = latest?.ma20, ma20 > 0 {
            self.ma20Distance = close / ma20 - 1
        }
    }

    private static func periodReturn(closes: [Double], lookback: Int) -> Double? {
        guard closes.count > lookback else { return nil }
        let start = closes[closes.count - lookback - 1]
        guard start != 0, let end = closes.last else { return nil }
        return end / start - 1
    }

    private static func annualizedVolatility(points: [PricePoint], lookback: Int) -> Double? {
        let returns = points.suffix(lookback).compactMap { point -> Double? in
            guard let pctChange = point.pctChange else { return nil }
            return pctChange / 100
        }
        guard returns.count > 1 else { return nil }
        let mean = returns.reduce(0, +) / Double(returns.count)
        let variance = returns
            .map { pow($0 - mean, 2) }
            .reduce(0, +) / Double(returns.count - 1)
        return sqrt(variance) * sqrt(252)
    }

    private static func maxDrawdown(closes: [Double]) -> Double? {
        guard !closes.isEmpty else { return nil }
        var peak = closes[0]
        var drawdown = 0.0
        for close in closes {
            peak = max(peak, close)
            if peak > 0 {
                drawdown = min(drawdown, close / peak - 1)
            }
        }
        return drawdown
    }
}

/// Color key for the K-line overlays; the candlestick/volume drawing lives in
/// the bundled lightweight-charts web layer (ChartWebView).
struct ChartLegend: View {
    @Environment(\.kssTheme) private var theme
    var body: some View {
        HStack(spacing: 16) {
            legendDot(theme.up, "涨")
            legendDot(theme.down, "跌")
            legendDot(theme.ma5, "MA5")
            legendDot(theme.ma20, "MA20")
            legendDot(vwapSwatch, "VWAP")
            Spacer()
        }
        .font(.caption2)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private var vwapSwatch: Color {
        theme.appearance == .dark
            ? Color(red: 232 / 255, green: 121 / 255, blue: 249 / 255)
            : Color(red: 162 / 255, green: 28 / 255, blue: 175 / 255)
    }

    private func legendDot(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(label).foregroundStyle(theme.textSecondary)
        }
    }
}

/// 图区外 MI 状态条：动作 / pred / N / 标注数（不叠 K 线）
struct MiChartBanner: View {
    @Environment(\.kssTheme) private var theme
    var signal: MISignal
    var markerCount: Int

    private var predText: String {
        guard let s = signal.predScore else { return "—" }
        return String(format: "%.2f", s)
    }

    private var nText: String {
        signal.n.map(String.init) ?? "—"
    }

    var body: some View {
        HStack(spacing: 10) {
            Text(signal.action ?? "—")
                .font(.system(size: 12, weight: .bold, design: .monospaced))
                .foregroundStyle(theme.textPrimary)
            Text("pred \(predText)")
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            Text("N=\(nText)")
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            if let entry = signal.entry, !entry.isEmpty {
                Text(entry)
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
            if let asof = signal.asof, !asof.isEmpty {
                Text(asof)
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if markerCount > 0 {
                Text("买/卖 \(markerCount)")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if let r = signal.reason, !r.isEmpty {
                Text(r)
                    .font(.system(size: 11))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
            if signal.unpinned == true {
                Text("默认形态·未钉死")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.orange)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(theme.surfaceRaised.opacity(0.55))
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }
}

// MARK: - U3 ChartDataMode

enum ChartDataMode: Hashable {
    case daily, m1, m5
}

/// MI 滚动信号研究级卡片
struct MISignalCard: View {
    @Environment(\.kssTheme) private var theme
    var signal: MISignal

    private var predText: String {
        guard let s = signal.predScore else { return "—" }
        return String(format: "%.2f", s)
    }

    private var nText: String {
        signal.n.map(String.init) ?? "—"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("MI 滚动信号")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                Spacer()
                if signal.unpinned == true {
                    Text("默认形态·未钉死")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.orange)
                }
            }
            if let st = signal.status, st != "ok" {
                Text("状态 \(st)：\(signal.reason ?? "")")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textSecondary)
            } else {
                Text("\(signal.action ?? "—") · \(signal.position ?? "") · pred \(predText)")
                    .font(KSSFont.themed(16, .semibold, theme: theme))
                if let r = signal.reason, !r.isEmpty {
                    Text(r)
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                }
                Text("N=\(nText) · \(signal.entry ?? "") / \(signal.exitRule ?? "") · asof \(signal.asof ?? "—")")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                if let prev = signal.prevAction {
                    Text("昨动作 \(prev) → \(signal.action ?? "")")
                        .font(.system(size: 11))
                        .foregroundStyle(theme.textSecondary)
                }
                if let note = signal.execNote {
                    Text(note)
                        .font(.system(size: 10))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }
}

extension StockDetailView {
    static func encodeMiOverlay(_ overlay: MIOverlay?) -> String {
        guard let overlay else { return "null" }
        let enc = JSONEncoder()
        enc.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? enc.encode(overlay),
              let s = String(data: data, encoding: .utf8) else {
            return "null"
        }
        return s
    }

    /// 通用多指标 overlay 数组 → JSON（snake_case，同 encodeMiOverlay 惯例，chart.html 统一按
    /// snake_case 读字段：indicator_id / markers / series）。
    static func encodeIndicatorOverlays(_ overlays: [IndicatorOverlay]?) -> String {
        guard let overlays, !overlays.isEmpty else { return "[]" }
        let enc = JSONEncoder()
        enc.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? enc.encode(overlays),
              let s = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return s
    }
    /// 拉取日内分钟 bar 序列（U3/R2/R7/F006）。切到 1m/5m 时触发。
    func loadIntraday(symbol: String, mode: ChartDataMode) async {
        intradayLoading = true; intradayError = nil; intradayBars = nil
        defer { intradayLoading = false }
        guard let bridge else { intradayError = "无法定位 bridge"; return }
        let (bars, err) = await Self.fetchIntraday(bridge: bridge, symbol: symbol, mode: mode)
        intradayBars = bars
        intradayError = err
    }

    /// 分钟线拉取 + 错误分类（详情页与全屏放大图共用，R4-1 接线时抽出防两处漂移）。
    static func fetchIntraday(
        bridge: BridgeClient, symbol: String, mode: ChartDataMode
    ) async -> (IntradayBars?, String?) {
        let interval = mode == .m5 ? 5 : 1
        do {
            let bars = try await Task.detached {
                try bridge.intradayBars(symbol: symbol, interval: interval)
            }.value
            if bars.isRenderable {
                return (bars, nil)
            }
            // 失败时保留日线主图；禁止笼统「bridge 调用失败」
            switch bars.error {
            case "auth_failed":
                return (nil, bars.hint ?? "实时源未连接 · 打开「网络与凭据」或等交易时段缓存")
            case "not_covered", "unsupported_symbol":
                return (nil, "该标的暂无分钟线覆盖")
            case let e? where !e.isEmpty:
                if let h = bars.hint, !h.isEmpty { return (nil, "\(h)（\(e)）") }
                return (nil, e)
            default:
                return (nil, bars.hint ?? "暂无分钟线 · 无本地存档")
            }
        } catch {
            return (nil, error.localizedDescription)
        }
    }
}
