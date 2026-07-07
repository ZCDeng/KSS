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

    @State private var sort: StockSort = .symbol
    @State private var ascending = true
    @State private var showChartFullscreen = false
    @State private var showImport = false

    private var filteredStocks: [StockSummary] {
        var items = stocks
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
            case .pct: return ascending ? (a.pctChange ?? 0) < (b.pctChange ?? 0) : (a.pctChange ?? 0) > (b.pctChange ?? 0)
            case .close: return ascending ? (a.close ?? 0) < (b.close ?? 0) : (a.close ?? 0) > (b.close ?? 0)
            }
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                    TextField("搜索代码 / 名称 / 行业", text: $searchText)
                        .textFieldStyle(.plain)
                        .font(.system(size: 13))
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
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(theme.accent)
                    Text("\(filteredStocks.count)")
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)

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
                    Button { onSelect(stock.symbol) } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(stock.name.isEmpty ? stock.symbol : stock.name)
                                    .font(.system(size: 15, weight: .bold))
                                    .foregroundStyle(isOn ? theme.accent : theme.textPrimary)
                                    .lineLimit(1)
                                if watchlist.contains(stock.symbol) {
                                    Image(systemName: "star.fill")
                                        .font(.system(size: 10))
                                        .foregroundStyle(theme.ma5)
                                }
                                Spacer()
                                Text(KSSFormat.pctPoints(stock.pctChange))
                                    .font(.system(size: 12.5, weight: .bold, design: .monospaced))
                                    .foregroundStyle(theme.signColor(stock.pctChange))
                            }
                            Text("\(stock.symbol) · \(stock.industry)")
                                .font(.system(size: 11.5, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                        }
                        .padding(.vertical, 2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .listRowBackground(isOn ? theme.accent.opacity(0.16) : Color.clear)
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
                        bridge: bridge
                    )
                } else {
                    Text("选择一只股票查看详情")
                        .font(.system(size: 14))
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
        // 放大：铺满整个浏览区（列表+详情，随窗口尺寸动态最大化），而非尺寸受限的 sheet。
        .overlay {
            if showChartFullscreen, let detail {
                ChartFullscreenView(detail: detail) { showChartFullscreen = false }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(theme.canvas)
                    .transition(.opacity)
            }
        }
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
    // U3 分钟 K 线模式（R7/R15）
    @State private var chartMode: ChartDataMode = .daily
    @State private var intradayBars: IntradayBars? = nil
    @State private var intradayLoading = false
    @State private var intradayError: String? = nil

    private var analysis: StockAnalysis {
        StockAnalysis(points: detail.history, latest: detail.latest)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(detail.name.isEmpty ? detail.symbol : detail.name)
                            .font(KSSFont.serif(30, .bold))
                            .foregroundStyle(theme.textPrimary)
                        Text("\(detail.symbol) · \(detail.industry)")
                            .font(.system(size: 14, weight: .medium, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                    }
                    Spacer()
                    Button(action: onToggleWatchlist) {
                        Label(isWatched ? "取消自选" : "加自选", systemImage: isWatched ? "star.fill" : "star")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .tint(theme.accent)
                }

                if let latest = detail.latest {
                    HStack(spacing: 10) {
                        StatTile(title: "收盘", value: KSSFormat.number(latest.close))
                        StatTile(title: "涨跌", value: KSSFormat.pctPoints(latest.pctChange), tint: theme.signColor(latest.pctChange))
                        StatTile(title: "MA5 / MA20", value: "\(KSSFormat.number(latest.ma5)) / \(KSSFormat.number(latest.ma20))")
                        StatTile(title: "成交额", value: KSSFormat.compactMoney(latest.amount))
                    }
                }

                if let review = detail.reviewConclusion {
                    StockReviewCard(review: review)
                }

                if let enrichment {
                    PerillaEnrichmentCard(data: enrichment)
                }

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
                    SectionHeader("行情 · 日K")
                    Spacer()
                    Button {
                        onZoom()
                    } label: {
                        Label("放大", systemImage: "arrow.up.left.and.arrow.down.right")
                            .font(.system(size: 12.5, weight: .semibold))
                    }
                    .buttonStyle(.bordered)
                    .tint(theme.accent)
                }
                VStack(alignment: .leading, spacing: 0) {
                    // U3 分钟 K 线模式选择器
                    Picker("", selection: $chartMode) {
                        Text("日线").tag(ChartDataMode.daily)
                        Text("1分钟").tag(ChartDataMode.m1)
                        Text("5分钟").tag(ChartDataMode.m5)
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 180)
                    .onChange(of: chartMode) { _, newMode in
                        if newMode != .daily {
                            Task { await loadIntraday(symbol: detail.symbol, mode: newMode) }
                        }
                    }
                    ChartLegend()
                    if chartMode == .daily {
                        ChartWebView(points: detail.history)
                            .frame(minHeight: 640)
                    } else {
                        // R15 四状态
                        if intradayLoading {
                            ProgressView("加载分钟线…")
                                .frame(minHeight: 640)
                        } else if let err = intradayError {
                            VStack(spacing: 8) {
                                Text("分钟线不可用")
                                    .font(.caption).foregroundStyle(theme.textSecondary)
                                Text(err)
                                    .font(.caption2).foregroundStyle(theme.textSecondary)
                                // 回退日线
                                ChartWebView(points: detail.history)
                                    .frame(minHeight: 320)
                            }
                            .frame(minHeight: 640)
                        } else if let bars = intradayBars, bars.isRenderable {
                            ChartWebView(points: detail.history, intradayBars: bars.bars)
                                .frame(minHeight: 640)
                        } else {
                            Text("暂无成交数据")
                                .font(.caption).foregroundStyle(theme.textSecondary)
                                .frame(minHeight: 640)
                        }
                    }
                }
                .id(detail.symbol)  // 切换标的时重建 chart
                .frame(height: 680)
                .background(theme.chartSurface)
                .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: theme.cardRadius)
                        .stroke(theme.hairline)
                )

                if !detail.concept.isEmpty {
                    Text(detail.concept)
                        .font(.system(size: 13.5))
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
/// 个股复盘结论卡：来自每日复盘的 标题 / 预期区间 / 建议。
struct StockReviewCard: View {
    @Environment(\.kssTheme) private var theme
    var review: StockReview

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(theme.accent)
                Text("复盘结论")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(theme.textPrimary)
                if !review.headline.isEmpty {
                    Text(review.headline)
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundStyle(theme.onAccent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(theme.accent, in: Capsule())
                        .lineLimit(1)
                }
                Spacer()
                StatusBadge(icon: "calendar", text: review.date, tint: theme.accent)
            }

            if !review.snapshot.isEmpty || !review.expectation.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    if !review.snapshot.isEmpty {
                        Text(review.snapshot)
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(theme.textBody)
                    }
                    if !review.expectation.isEmpty {
                        Text("预期区间 · " + review.expectation)
                            .font(.system(size: 12.5))
                            .foregroundStyle(theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            if !review.suggestions.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(review.suggestions.enumerated()), id: \.offset) { _, s in
                        HStack(alignment: .top, spacing: 7) {
                            Circle().fill(theme.accent).frame(width: 5, height: 5).padding(.top, 6)
                            Text(s)
                                .font(.system(size: 13))
                                .foregroundStyle(theme.textBody)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
    }
}

/// 紫苏叶个股富化卡：机构持仓动态 / PE 分位 / 美股对标。缺失项显示「暂不可用」。
struct PerillaEnrichmentCard: View {
    @Environment(\.kssTheme) private var theme
    var data: PerillaEnrichment

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("🌿 紫苏叶富化")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(theme.textPrimary)
                if let tier = data.tier {
                    Text(tier == "core" ? "核心" : "国产替代主线")
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundStyle(theme.onAccent)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(theme.accent, in: Capsule())
                }
                Spacer()
            }

            row("机构持仓动态", institutionalText)
            row("PE 估值", peText)
            row("美股对标", usPeerText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
    }

    private func row(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.system(size: 11.5, weight: .semibold))
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
    var onClose: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(detail.name.isEmpty ? detail.symbol : detail.name)
                        .font(.system(size: 20, weight: .heavy))
                        .foregroundStyle(theme.textPrimary)
                    Text("\(detail.symbol) · 滚轮缩放 · 拖动平移")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer()
                Button {
                    onClose()
                } label: {
                    Label("关闭", systemImage: "xmark")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.bordered)
                .keyboardShortcut(.cancelAction)
            }
            .padding(14)
            ChartLegend()
            ChartWebView(points: detail.history)
        }
        // 作为浏览区上的覆盖层铺满：随 app 窗口尺寸动态最大化。
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.chartSurface)
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
            Spacer()
        }
        .font(.caption2)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private func legendDot(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(label).foregroundStyle(theme.textSecondary)
        }
    }
}

// MARK: - U3 ChartDataMode

enum ChartDataMode: Hashable {
    case daily, m1, m5
}

extension StockDetailView {
    /// 拉取日内分钟 bar 序列（U3/R2/R7/F006）。切到 1m/5m 时触发。
    func loadIntraday(symbol: String, mode: ChartDataMode) async {
        intradayLoading = true; intradayError = nil; intradayBars = nil
        defer { intradayLoading = false }
        let interval = mode == .m5 ? 5 : 1
        guard let bridge else { intradayError = "无法定位 bridge"; return }
        let bars = try? await Task.detached {
            try bridge.intradayBars(symbol: symbol, interval: interval)
        }.value
        if let bars {
            if bars.isRenderable { intradayBars = bars; intradayError = nil }
            else { intradayError = bars.error ?? "暂无成交数据" }
        } else {
            intradayError = "bridge 调用失败"
        }
    }
}
