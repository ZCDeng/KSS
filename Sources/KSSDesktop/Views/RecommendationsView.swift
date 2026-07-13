import SwiftUI

enum RecSort: String, CaseIterable, Identifiable {
    case rank = "排名"
    case weight = "权重"
    case tracking = "跟踪收益"
    var id: String { rawValue }
}

enum RecTab: String, CaseIterable, Identifiable {
    case current = "当日推荐"
    case history = "往期跟踪"
    var id: String { rawValue }
}

struct RecommendationsView: View {
    @Environment(\.kssTheme) private var theme
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void
    var realtimeQuotes: [String: LongbridgeQuote] = [:]
    var realtimeReceivedAtBySymbol: [String: Date] = [:]
    var tradingHours: TradingHours? = nil
    var realtimeAuthFailed: Bool = false
    var realtimeUpdatedAt: Date? = nil
    var onRetryRealtime: () -> Void = {}
    var onLoadRealtime: () -> Void = {}

    @State private var tab: RecTab = .current
    @State private var sort: RecSort = .rank
    @State private var ascending = true

    private var sortedRecs: [Recommendation] {
        snapshot.recommendations.sorted { a, b in
            switch sort {
            case .rank: return ascending ? a.rank < b.rank : a.rank > b.rank
            case .weight: return ascending ? a.weight < b.weight : a.weight > b.weight
            case .tracking: return ascending ? (a.trackingReturn ?? 0) < (b.trackingReturn ?? 0) : (a.trackingReturn ?? 0) > (b.trackingReturn ?? 0)
            }
        }
    }

    private var recSymbols: [String] {
        RealtimeMerge.symbolsFromRecommendations(snapshot.recommendations)
    }

    private var displayedFreshness: RealtimeFreshness {
        RealtimeMerge.worstFreshness(symbols: recSymbols, quotes: realtimeQuotes, receivedAtBySymbol: realtimeReceivedAtBySymbol)
    }

    var body: some View {
        // M3：内容封顶 1080 居中，统一外边距（与总览一致）。
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top) {
                    PageTitle("推荐", subtitle: snapshot.recommendationDate)
                    Spacer(minLength: 12)
                    RealtimeStatusBadge(
                        freshness: displayedFreshness,
                        hours: tradingHours,
                        authFailed: realtimeAuthFailed,
                        updatedAt: realtimeUpdatedAt,
                        onRetry: onRetryRealtime
                    )
                }
                .padding(.horizontal, 16)
                .padding(.top, 18)

                KSSSegmentedControl(options: RecTab.allCases.map { ($0, $0.rawValue) }, selection: $tab)
                    .padding(.horizontal, 16)
                    .padding(.top, 10)
                    .padding(.bottom, 4)

                if tab == .current {
                    currentTab
                } else {
                    historyTab
                }
            }
            .frame(width: w)
            .frame(maxWidth: .infinity, alignment: .center)
            .background(theme.canvas)
        }
        .background(theme.canvas)
        .onAppear { onLoadRealtime() }
    }

    // MARK: - 当日推荐 (aligned table)

    private var currentTab: some View {
        VStack(spacing: 0) {
            HStack {
                SortControl(
                    options: RecSort.allCases.map { ($0, $0.rawValue) },
                    selection: $sort,
                    ascending: $ascending
                )
                Spacer()
                Text("\(sortedRecs.count) 只")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)

            // column header
            HStack(spacing: 12) {
                SortHeaderCell(title: "#", key: RecSort.rank, selection: $sort, ascending: $ascending,
                               alignment: .leading, width: 44)
                Text("名称 / 代码").frame(maxWidth: .infinity, alignment: .leading)
                Text("状态").frame(width: 80, alignment: .center)
                Text("现价").frame(width: 72, alignment: .trailing)
                Text("涨跌").frame(width: 64, alignment: .trailing)
                Text("log_mv").frame(width: 72, alignment: .trailing)
                SortHeaderCell(title: "权重", key: RecSort.weight, selection: $sort, ascending: $ascending,
                               alignment: .trailing, width: 56)
                SortHeaderCell(title: "跟踪", key: RecSort.tracking, selection: $sort, ascending: $ascending,
                               alignment: .trailing, width: 72)
            }
            .font(KSSFont.themed(11, .semibold, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .padding(.horizontal, 16)
            .padding(.bottom, 6)

            List(sortedRecs) { item in
                Button { onSelectSymbol(item.symbol) } label: {
                    HStack(spacing: 12) {
                        Text("#\(item.rank)")
                            .font(.system(size: 16, weight: .heavy, design: .monospaced))
                            .foregroundStyle(theme.accent)
                            .frame(width: 44, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name.isEmpty ? item.symbol : item.name)
                                .font(KSSFont.themed(15.5, .bold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                            Text("\(item.symbol) · \(item.industry)")
                                .font(.system(size: 11.5, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        StatusBadge.tracking(item.status).frame(width: 80, alignment: .center)
                        recPriceCells(item)
                        Text(KSSFormat.number(item.factorValue, digits: 3))
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(theme.textPrimary)
                            .frame(width: 72, alignment: .trailing)
                        Text(KSSFormat.percent(item.weight))
                            .font(.system(size: 13, design: .monospaced))
                            .foregroundStyle(theme.textPrimary)
                            .frame(width: 56, alignment: .trailing)
                        Text(KSSFormat.percent(item.trackingReturn))
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundStyle(theme.signColor(item.trackingReturn))
                            .frame(width: 72, alignment: .trailing)
                    }
                    .contentShape(Rectangle())
                    .padding(.vertical, 3)
                }
                .buttonStyle(.plain)
                .listRowBackground(theme.surfaceContainer)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
    }

    /// 现价 / 日内涨跌：live quote 优先，否则 latestClose 快照，皆无则 —。
    @ViewBuilder
    private func recPriceCells(_ item: Recommendation) -> some View {
        let quote = realtimeQuotes[item.symbol.uppercased()]
        let freshness = RealtimeMerge.freshness(
            for: item.symbol,
            quotes: realtimeQuotes,
            receivedAtBySymbol: realtimeReceivedAtBySymbol
        )
        let isFreshLive = { (disp: (close: Double, pct: Double, isLive: Bool)) in disp.isLive && freshness == .fresh }
        if let disp = RealtimeMerge.displayPrice(
            snapshotClose: item.latestClose,
            snapshotPct: nil,
            quote: quote
        ) {
            LivePriceText(
                value: disp.close,
                text: KSSFormat.number(disp.close),
                baseColor: freshness == .stale ? theme.ma5 : theme.signColor(disp.isLive ? disp.pct : 0),
                isLive: isFreshLive(disp),
                font: .system(size: 13, weight: .semibold, design: .monospaced)
            )
            .frame(width: 72, alignment: .trailing)
            if disp.isLive {
                LivePriceText(
                    value: disp.pct,
                    text: KSSFormat.pctPoints(disp.pct),
                    baseColor: freshness == .stale ? theme.ma5 : theme.signColor(disp.pct),
                    isLive: isFreshLive(disp),
                    font: .system(size: 12, weight: .semibold, design: .monospaced)
                )
                .frame(width: 64, alignment: .trailing)
            } else {
                Text("—")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .frame(width: 64, alignment: .trailing)
            }
        } else {
            Text("—")
                .font(.system(size: 13, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 72, alignment: .trailing)
            Text("—")
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 64, alignment: .trailing)
        }
    }

    // MARK: - 往期跟踪 (by 日/周/月, 可展开看当日选股)

    private var historyTab: some View {
        let days = snapshot.recommendationTracking ?? []
        return Group {
            if days.isEmpty {
                Text("暂无往期推荐记录")
                    .font(KSSFont.themed(13, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(spacing: 8) {
                        HStack(spacing: 12) {
                            Text("预测日").frame(width: 150, alignment: .leading)
                            Text("选股").frame(width: 50, alignment: .trailing)
                            Text("日 (1d)").frame(maxWidth: .infinity, alignment: .trailing)
                            Text("周 (5d)").frame(maxWidth: .infinity, alignment: .trailing)
                            Text("月 (20d)").frame(maxWidth: .infinity, alignment: .trailing)
                            Spacer().frame(width: 20)
                        }
                        .font(KSSFont.themed(11, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, 14)

                        ForEach(days) { day in
                            TrackingDayCard(day: day, onSelectSymbol: onSelectSymbol)
                        }
                    }
                    .padding(16)
                }
                .scrollContentBackground(.hidden)
                .background(theme.canvas)
            }
        }
    }
}

/// 一个预测日的跟踪卡：摘要行 + 点击展开当日逐只选股的 1d/5d/20d 收益。
struct TrackingDayCard: View {
    @Environment(\.kssTheme) private var theme
    var day: RecTrackingDay
    var onSelectSymbol: (String) -> Void
    @State private var expanded = false

    var body: some View {
        VStack(spacing: 0) {
            Button { withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() } } label: {
                HStack(spacing: 12) {
                    HStack(spacing: 6) {
                        Image(systemName: expanded ? "chevron.down" : "chevron.right")
                            .font(KSSFont.themed(10, .bold, theme: theme)).foregroundStyle(theme.textSecondary)
                        Image(systemName: "calendar").font(KSSFont.themed(11, .semibold, theme: theme)).foregroundStyle(theme.accent)
                        Text(day.date).font(.system(size: 14, weight: .bold, design: .monospaced)).foregroundStyle(theme.textPrimary).lineLimit(1).fixedSize()
                    }
                    .frame(width: 150, alignment: .leading)
                    Text("\(day.nPicks)")
                        .font(.system(size: 13, design: .monospaced)).foregroundStyle(theme.textSecondary)
                        .frame(width: 50, alignment: .trailing)
                    horizonCell(day.ret1d, bold: true)
                    horizonCell(day.ret5d, bold: true)
                    horizonCell(day.ret20d, bold: true)
                    Spacer().frame(width: 20)
                }
                .contentShape(Rectangle())
                .padding(.vertical, 4)
            }
            .buttonStyle(.plain)

            if expanded {
                Divider().overlay(theme.hairline).padding(.vertical, 6)
                VStack(spacing: 6) {
                    ForEach(day.picks) { pick in
                        Button { onSelectSymbol(pick.symbol) } label: {
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(pick.name.isEmpty ? pick.symbol : pick.name)
                                        .font(KSSFont.themed(13, .semibold, theme: theme)).foregroundStyle(theme.textPrimary)
                                    Text(pick.symbol)
                                        .font(.system(size: 10.5, design: .monospaced)).foregroundStyle(theme.textSecondary)
                                }
                                .frame(width: 150, alignment: .leading)
                                Color.clear.frame(width: 50)   // 对齐表头的「数量」列，保持三个收益列与摘要行同位
                                horizonCell(pick.ret1d, bold: false)
                                horizonCell(pick.ret5d, bold: false)
                                horizonCell(pick.ret20d, bold: false)
                                Spacer().frame(width: 20)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(theme.surfaceContainer)
        .clipShape(RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
    }

    private func horizonCell(_ value: Double?, bold: Bool) -> some View {
        Text(value == nil ? "待结算" : KSSFormat.percent(value))
            .font(.system(size: bold ? 13.5 : 12.5, weight: bold ? .bold : .medium, design: .monospaced))
            .foregroundStyle(value == nil ? theme.textSecondary : theme.signColor(value))
            .frame(maxWidth: .infinity, alignment: .trailing)
    }
}
