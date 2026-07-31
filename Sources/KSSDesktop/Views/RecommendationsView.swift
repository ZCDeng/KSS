import SwiftUI

enum RecSort: String, CaseIterable, Identifiable {
    case rank = "排名"
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
    var watchlist: [String] = []
    var onToggleWatchlist: (String) -> Void = { _ in }
    var realtimeQuotes: [String: LongbridgeQuote] = [:]
    var realtimeReceivedAtBySymbol: [String: Date] = [:]
    var tradingHours: TradingHours? = nil
    var realtimeAuthFailed: Bool = false
    var realtimeUpdatedAt: Date? = nil
    var onRetryRealtime: () -> Void = {}
    var onLoadRealtime: () -> Void = {}
    /// 对照整池写入影子纸交易；参数为 styleId。
    var onWriteShadow: (String) -> Void = { _ in }

    @State private var tab: RecTab = .current
    @State private var sort: RecSort = .rank
    @State private var ascending = true

    private var sortedRecs: [Recommendation] {
        snapshot.recommendations.sorted { a, b in
            switch sort {
            case .rank: return ascending ? a.rank < b.rank : a.rank > b.rank
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
        // 整页 ScrollView + 顶对齐：避免 GeometryReader/List/内层 ScrollView 把表体撑到页面底部，
        // 造成「表头与第一行之间大块空白」。
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top) {
                    PageTitle("推荐", subtitle: snapshot.recommendationSubtitle)
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
            .frame(maxWidth: 1080)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.bottom, 24)
        }
        .background(theme.canvas)
        .onAppear { onLoadRealtime() }
    }

    // MARK: - 当日推荐 (aligned table)

    /// 与盯盘 TodayPicksList 相同：表头 + ForEach 紧贴，无嵌套可伸缩 List/ScrollView。
    private var currentTab: some View {
        VStack(alignment: .leading, spacing: 0) {
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

            VStack(spacing: 0) {
                recTableHeader
                Divider().overlay(theme.hairline)
                let rows = sortedRecs
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                    recTableRow(item)
                    if index < rows.count - 1 {
                        Divider().overlay(theme.hairline)
                    }
                }
            }
            .background(theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline)
            )
            .padding(.horizontal, 16)

            styleContrastSection
                .padding(.top, 18)
                .padding(.horizontal, 16)
        }
    }

    // MARK: - 风格对照栏
    // 约定：通用术语（研究·未过门禁 / missing 等）在栏头右侧用 icon+短文案图例；
    // 卡片内只保留 icon+状态，保证尺寸与对齐一致。

    private var styleSlots: [StyleContrastSlot] {
        snapshot.styleContrasts ?? []
    }

    @ViewBuilder
    private var styleContrastSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 12) {
                HStack(spacing: 8) {
                    Text("风格对照")
                        .font(KSSFont.themed(16, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    if let d = snapshot.styleContrastDate, !d.isEmpty {
                        Text(d)
                            .font(.system(size: 11.5, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                    }
                }
                Spacer(minLength: 8)
                styleContrastLegend
            }

            Text("主推荐仍为 log_mv · 对照不混入正式纸交易")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)

            if styleSlots.isEmpty {
                Text("尚无风格对照快照。日更 formal-daily-picks 后会跑四风格；也可任务页触发 style-contrast-daily。")
                    .font(KSSFont.themed(12.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(theme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
            } else {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 200), spacing: 12)],
                    spacing: 12
                ) {
                    ForEach(styleSlots) { slot in
                        styleContrastCard(slot)
                    }
                }
            }
        }
    }

    /// 栏头图例：icon + 短文案，释义只在这里出现一次。
    private var styleContrastLegend: some View {
        HStack(spacing: 12) {
            styleLegendItem(icon: "flask", color: theme.ma5, text: "研究·未过门禁")
            styleLegendItem(icon: "circle.dashed", color: theme.textSecondary, text: "无数据")
            styleLegendItem(icon: "xmark.circle", color: theme.ma5, text: "失败")
            styleLegendItem(icon: "checkmark.circle", color: theme.accent, text: "可用")
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("图例：研究未过门禁、无数据、失败、可用")
    }

    private func styleLegendItem(icon: String, color: Color, text: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(color)
                .frame(width: 14, height: 14)
            Text(text)
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
        }
    }

    private func styleContrastCard(_ slot: StyleContrastSlot) -> some View {
        let visual = styleSlotVisual(slot)
        return VStack(alignment: .leading, spacing: 8) {
            // 卡头：风格名 | icon + 状态（无长徽章）
            HStack(spacing: 8) {
                Text(slot.name)
                    .font(KSSFont.themed(14, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                HStack(spacing: 4) {
                    Image(systemName: visual.icon)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(visual.color)
                        .frame(width: 14, height: 14)
                    Text(visual.statusLabel)
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundStyle(visual.color)
                        .lineLimit(1)
                }
                .fixedSize()
                .help(visual.help)
            }

            if slot.status == "ok" {
                ForEach(slot.picks.prefix(5)) { pick in
                    Button { onSelectSymbol(pick.symbol) } label: {
                        HStack(spacing: 6) {
                            Text("#\(pick.rank)")
                                .font(.system(size: 11, weight: .bold, design: .monospaced))
                                .foregroundStyle(theme.accent)
                                .frame(width: 28, alignment: .leading)
                            Text((pick.name?.isEmpty == false ? pick.name! : pick.symbol))
                                .font(KSSFont.themed(12.5, .semibold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Text(pick.symbol)
                                .font(.system(size: 10.5, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                    .buttonStyle(.plain)
                }
                if !slot.picks.isEmpty {
                    Button {
                        onWriteShadow(slot.styleId)
                    } label: {
                        Text("写入影子纸交易")
                            .font(KSSFont.themed(11.5, .semibold, theme: theme))
                            .foregroundStyle(theme.accent)
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 2)
                }
            } else {
                // 失败/无数据：正文不重复长规则文案；细节放 help / 单行错误摘要
                Text(visual.bodyHint)
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(2)
                    .frame(minHeight: 36, alignment: .topLeading)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 96, alignment: .topLeading)
        .background(theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(slot.name)，\(visual.help)")
    }

    /// 卡片状态视觉：只编码 icon + 短 statusLabel。
    private func styleSlotVisual(_ slot: StyleContrastSlot) -> (
        icon: String, color: Color, statusLabel: String, help: String, bodyHint: String
    ) {
        let gatePassed = slot.gateLabel == "passed"
        switch slot.status {
        case "ok":
            if gatePassed {
                return (
                    "checkmark.circle",
                    theme.accent,
                    "ok",
                    "可用，已过上线门禁",
                    ""
                )
            }
            return (
                "flask",
                theme.ma5,
                "研究",
                "研究态：未过上线门禁，不可作正式主推荐",
                ""
            )
        case "failed":
            let err = slot.error?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return (
                "xmark.circle",
                theme.ma5,
                "失败",
                err.isEmpty ? "当日计算失败" : err,
                err.isEmpty ? "当日未算出" : err
            )
        default: // missing 等
            return (
                "circle.dashed",
                theme.textSecondary,
                "—",
                "无数据：当日无对照快照",
                "当日无快照"
            )
        }
    }

    private var recTableHeader: some View {
        HStack(spacing: 12) {
            SortHeaderCell(title: "#", key: RecSort.rank, selection: $sort, ascending: $ascending,
                           alignment: .leading, width: 44)
            Text("名称 / 代码").frame(width: 140, alignment: .leading)
            Text("入选理由").frame(maxWidth: .infinity, alignment: .leading)
            Text("状态").frame(width: 80, alignment: .center)
            Text("现价").frame(width: 72, alignment: .trailing)
            Text("涨跌").frame(width: 64, alignment: .trailing)
            Text("log_mv").frame(width: 72, alignment: .trailing)
            SortHeaderCell(title: "跟踪", key: RecSort.tracking, selection: $sort, ascending: $ascending,
                           alignment: .trailing, width: 72)
            Color.clear.frame(width: 28)
        }
        .font(KSSFont.themed(11, .semibold, theme: theme))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func recTableRow(_ item: Recommendation) -> some View {
        HStack(spacing: 0) {
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
                            .lineLimit(1)
                        Text("\(item.symbol) · \(item.industry)")
                            .font(.system(size: 11.5, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(1)
                    }
                    .frame(width: 140, alignment: .leading)
                    Text(recReasonText(item))
                        .font(KSSFont.themed(12.5, theme: theme))
                        .foregroundStyle(theme.textBody)
                        .lineLimit(2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    StatusBadge.tracking(item.status).frame(width: 80, alignment: .center)
                    recPriceCells(item)
                    Text(KSSFormat.number(item.factorValue, digits: 3))
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.textPrimary)
                        .frame(width: 72, alignment: .trailing)
                    Text(KSSFormat.percent(item.trackingReturn))
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .foregroundStyle(theme.signColor(item.trackingReturn))
                        .frame(width: 72, alignment: .trailing)
                }
                .contentShape(Rectangle())
                .padding(.leading, 14)
                .padding(.vertical, 10)
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, alignment: .leading)

            WatchlistStarButton(
                isWatched: watchlist.contains(item.symbol)
            ) { onToggleWatchlist(item.symbol) }
            .padding(.trailing, 10)
        }
        .background(theme.surfaceContainer)
    }

    private func recReasonText(_ item: Recommendation) -> String {
        let r = item.selectionReason?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return r.isEmpty ? "—" : r
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
                    .padding(16)
            } else {
                // 外层 body 已 ScrollView，这里不再嵌套，避免可伸缩空白。
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
                        TrackingDayCard(
                            day: day,
                            watchlist: watchlist,
                            onSelectSymbol: onSelectSymbol,
                            onToggleWatchlist: onToggleWatchlist
                        )
                    }
                }
                .padding(16)
            }
        }
    }
}

/// 一个预测日的跟踪卡：摘要行 + 点击展开当日逐只选股的 1d/5d/20d 收益。
struct TrackingDayCard: View {
    @Environment(\.kssTheme) private var theme
    var day: RecTrackingDay
    var watchlist: [String] = []
    var onSelectSymbol: (String) -> Void
    var onToggleWatchlist: (String) -> Void = { _ in }
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
                        HStack(spacing: 8) {
                            Button { onSelectSymbol(pick.symbol) } label: {
                                HStack(spacing: 12) {
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(pick.name.isEmpty ? pick.symbol : pick.name)
                                            .font(KSSFont.themed(13, .semibold, theme: theme)).foregroundStyle(theme.textPrimary)
                                        Text(pick.symbol)
                                            .font(.system(size: 10.5, design: .monospaced)).foregroundStyle(theme.textSecondary)
                                    }
                                    .frame(width: 150, alignment: .leading)
                                    Color.clear.frame(width: 50)
                                    horizonCell(pick.ret1d, bold: false)
                                    horizonCell(pick.ret5d, bold: false)
                                    horizonCell(pick.ret20d, bold: false)
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            WatchlistStarButton(
                                isWatched: watchlist.contains(pick.symbol)
                            ) { onToggleWatchlist(pick.symbol) }
                        }
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
