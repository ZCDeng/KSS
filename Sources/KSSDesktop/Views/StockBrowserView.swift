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
    var title: String
    var stocks: [StockSummary]
    var selectedSymbol: String?
    var detail: StockDetail?
    var watchlist: [String]
    @Binding var searchText: String
    var onSelect: (String) -> Void
    var onToggleWatchlist: (String) -> Void

    @State private var sort: StockSort = .symbol
    @State private var ascending = true
    @State private var showChartFullscreen = false

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
                        .foregroundStyle(KSSTheme.textSecondary)
                    TextField("搜索代码 / 名称 / 行业", text: $searchText)
                        .textFieldStyle(.plain)
                        .font(.system(size: 13))
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(KSSTheme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .padding(.horizontal, 12)
                .padding(.top, 12)

                HStack {
                    SortControl(
                        options: StockSort.allCases.map { ($0, $0.rawValue) },
                        selection: $sort,
                        ascending: $ascending
                    )
                    Spacer()
                    Text("\(filteredStocks.count)")
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        .foregroundStyle(KSSTheme.textSecondary)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)

                List(filteredStocks, selection: Binding(
                    get: { selectedSymbol },
                    set: { symbol in if let symbol { onSelect(symbol) } }
                )) { stock in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(stock.name.isEmpty ? stock.symbol : stock.name)
                                .font(.system(size: 15, weight: .bold))
                                .foregroundStyle(KSSTheme.textPrimary)
                                .lineLimit(1)
                            if watchlist.contains(stock.symbol) {
                                Image(systemName: "star.fill")
                                    .font(.system(size: 10))
                                    .foregroundStyle(KSSTheme.ma5)
                            }
                            Spacer()
                            Text(KSSFormat.pctPoints(stock.pctChange))
                                .font(.system(size: 12.5, weight: .bold, design: .monospaced))
                                .foregroundStyle(KSSTheme.signColor(stock.pctChange))
                        }
                        Text("\(stock.symbol) · \(stock.industry)")
                            .font(.system(size: 11.5, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                    .padding(.vertical, 2)
                    .tag(stock.symbol)
                }
                .scrollContentBackground(.hidden)
                .background(KSSTheme.canvas)
            }
            .frame(width: 300)

            Divider().overlay(KSSTheme.hairline)

            Group {
                if let detail {
                    StockDetailView(
                        detail: detail,
                        isWatched: watchlist.contains(detail.symbol),
                        onToggleWatchlist: { onToggleWatchlist(detail.symbol) },
                        onZoom: { showChartFullscreen = true }
                    )
                } else {
                    Text("选择一只股票查看详情")
                        .font(.system(size: 14))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
        // 放大：铺满整个浏览区（列表+详情，随窗口尺寸动态最大化），而非尺寸受限的 sheet。
        .overlay {
            if showChartFullscreen, let detail {
                ChartFullscreenView(detail: detail) { showChartFullscreen = false }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(KSSTheme.canvas)
                    .transition(.opacity)
            }
        }
    }
}

struct StockDetailView: View {
    var detail: StockDetail
    var isWatched: Bool
    var onToggleWatchlist: () -> Void
    var onZoom: () -> Void

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
                            .foregroundStyle(KSSTheme.textPrimary)
                        Text("\(detail.symbol) · \(detail.industry)")
                            .font(.system(size: 14, weight: .medium, design: .monospaced))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                    Spacer()
                    Button(action: onToggleWatchlist) {
                        Label(isWatched ? "取消自选" : "加自选", systemImage: isWatched ? "star.fill" : "star")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .tint(KSSTheme.accent)
                }

                if let latest = detail.latest {
                    HStack(spacing: 10) {
                        StatTile(title: "收盘", value: KSSFormat.number(latest.close))
                        StatTile(title: "涨跌", value: KSSFormat.pctPoints(latest.pctChange), tint: KSSTheme.signColor(latest.pctChange))
                        StatTile(title: "MA5 / MA20", value: "\(KSSFormat.number(latest.ma5)) / \(KSSFormat.number(latest.ma20))")
                        StatTile(title: "成交额", value: KSSFormat.compactMoney(latest.amount))
                    }
                }

                SectionHeader("分析指标")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 10)], spacing: 10) {
                    StatTile(title: "20日收益", value: KSSFormat.percent(analysis.return20), tint: KSSTheme.signColor(analysis.return20))
                    StatTile(title: "60日收益", value: KSSFormat.percent(analysis.return60), tint: KSSTheme.signColor(analysis.return60))
                    StatTile(title: "20日波动", value: KSSFormat.percent(analysis.volatility20))
                    StatTile(title: "60日回撤", value: KSSFormat.percent(analysis.maxDrawdown60), tint: KSSTheme.signColor(analysis.maxDrawdown60))
                    StatTile(title: "距20日高点", value: KSSFormat.percent(analysis.distanceToHigh20), tint: KSSTheme.signColor(analysis.distanceToHigh20))
                    StatTile(title: "MA20偏离", value: KSSFormat.percent(analysis.ma20Distance), tint: KSSTheme.signColor(analysis.ma20Distance))
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
                    .tint(KSSTheme.accent)
                }
                VStack(alignment: .leading, spacing: 0) {
                    ChartLegend()
                    ChartWebView(points: detail.history)
                        .frame(minHeight: 640)
                }
                .frame(height: 680)
                .background(KSSTheme.chartSurface)
                .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: KSSTheme.cardRadius)
                        .stroke(KSSTheme.hairline)
                )

                if !detail.concept.isEmpty {
                    Text(detail.concept)
                        .font(.system(size: 13.5))
                        .foregroundStyle(KSSTheme.textSecondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
    }
}

/// Large interactive K-line view. Mouse-wheel zoom and drag-pan work here
/// without the surrounding ScrollView intercepting the wheel.
struct ChartFullscreenView: View {
    var detail: StockDetail
    var onClose: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(detail.name.isEmpty ? detail.symbol : detail.name)
                        .font(.system(size: 20, weight: .heavy))
                        .foregroundStyle(KSSTheme.textPrimary)
                    Text("\(detail.symbol) · 滚轮缩放 · 拖动平移")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(KSSTheme.textSecondary)
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
        .background(KSSTheme.chartSurface)
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
    var body: some View {
        HStack(spacing: 16) {
            legendDot(KSSTheme.up, "涨")
            legendDot(KSSTheme.down, "跌")
            legendDot(KSSTheme.ma5, "MA5")
            legendDot(KSSTheme.ma20, "MA20")
            Spacer()
        }
        .font(.caption2)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private func legendDot(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(label).foregroundStyle(KSSTheme.textSecondary)
        }
    }
}
