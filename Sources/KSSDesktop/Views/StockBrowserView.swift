import SwiftUI

struct StockBrowserView: View {
    var title: String
    var stocks: [StockSummary]
    var selectedSymbol: String?
    var detail: StockDetail?
    var watchlist: [String]
    @Binding var searchText: String
    var onSelect: (String) -> Void
    var onToggleWatchlist: (String) -> Void

    private var filteredStocks: [StockSummary] {
        guard !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return stocks
        }
        let q = searchText.lowercased()
        return stocks.filter {
            $0.symbol.lowercased().contains(q)
                || $0.name.lowercased().contains(q)
                || $0.industry.lowercased().contains(q)
                || $0.concept.lowercased().contains(q)
        }
    }

    var body: some View {
        NavigationSplitView {
            List(filteredStocks, selection: Binding(
                get: { selectedSymbol },
                set: { symbol in if let symbol { onSelect(symbol) } }
            )) { stock in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(stock.name.isEmpty ? stock.symbol : stock.name)
                            .font(.headline)
                            .lineLimit(1)
                        if watchlist.contains(stock.symbol) {
                            Image(systemName: "star.fill")
                                .foregroundStyle(.yellow)
                        }
                    }
                    Text("\(stock.symbol) · \(stock.industry)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("\(KSSFormat.number(stock.close))  \(KSSFormat.pctPoints(stock.pctChange))")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(KSSTheme.signColor(stock.pctChange))
                }
                .tag(stock.symbol)
            }
            .searchable(text: $searchText, placement: .sidebar)
        } detail: {
            if let detail {
                StockDetailView(
                    detail: detail,
                    isWatched: watchlist.contains(detail.symbol),
                    onToggleWatchlist: { onToggleWatchlist(detail.symbol) }
                )
            } else {
                Text("Select a stock")
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle(title)
    }
}

struct StockDetailView: View {
    var detail: StockDetail
    var isWatched: Bool
    var onToggleWatchlist: () -> Void

    private var analysis: StockAnalysis {
        StockAnalysis(points: detail.history, latest: detail.latest)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(detail.name.isEmpty ? detail.symbol : detail.name)
                            .font(.largeTitle.weight(.semibold))
                        Text("\(detail.symbol) · \(detail.industry)")
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(action: onToggleWatchlist) {
                        Label(isWatched ? "Remove" : "Watch", systemImage: isWatched ? "star.fill" : "star")
                    }
                }

                if let latest = detail.latest {
                    HStack(spacing: 12) {
                        StatTile(title: "收盘", value: KSSFormat.number(latest.close))
                        StatTile(title: "涨跌", value: KSSFormat.pctPoints(latest.pctChange), tint: KSSTheme.signColor(latest.pctChange))
                        StatTile(title: "MA5 / MA20", value: "\(KSSFormat.number(latest.ma5)) / \(KSSFormat.number(latest.ma20))")
                        StatTile(title: "成交额", value: KSSFormat.compactMoney(latest.amount))
                    }
                }

                SectionHeader("Analysis")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), spacing: 12)], spacing: 12) {
                    StatTile(title: "20日收益", value: KSSFormat.percent(analysis.return20), tint: KSSTheme.signColor(analysis.return20))
                    StatTile(title: "60日收益", value: KSSFormat.percent(analysis.return60), tint: KSSTheme.signColor(analysis.return60))
                    StatTile(title: "20日波动", value: KSSFormat.percent(analysis.volatility20))
                    StatTile(title: "60日回撤", value: KSSFormat.percent(analysis.maxDrawdown60), tint: KSSTheme.signColor(analysis.maxDrawdown60))
                    StatTile(title: "距20日高点", value: KSSFormat.percent(analysis.distanceToHigh20), tint: KSSTheme.signColor(analysis.distanceToHigh20))
                    StatTile(title: "MA20偏离", value: KSSFormat.percent(analysis.ma20Distance), tint: KSSTheme.signColor(analysis.ma20Distance))
                }

                SectionHeader("行情 · 日K")
                VStack(alignment: .leading, spacing: 0) {
                    ChartLegend()
                    ChartWebView(points: detail.history)
                        .frame(minHeight: 320)
                }
                .frame(height: 360)
                .background(KSSTheme.chartSurface)
                .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: KSSTheme.cardRadius)
                        .stroke(KSSTheme.hairline)
                )

                if !detail.concept.isEmpty {
                    Text(detail.concept)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
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
