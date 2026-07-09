import SwiftUI

/// 概念主题页：十五五科技主题 → 各板块龙头(龙一二)/第二梯队(龙三+)。
/// 数据源 = themes_15th_5y.yaml（主题→板块）+ 热点轮动归档（板块龙头）。
/// 数据驱动：yaml 里几个主题就显示几个，覆盖度随每日归档累积提升。
struct ThemesView: View {
    @Environment(\.kssTheme) private var theme
    var themes: [ThemeLeaders]
    var onLoad: () -> Void
    var onSelectSymbol: (String) -> Void
    var realtimeQuotes: [String: LongbridgeQuote] = [:]
    var tradingHours: TradingHours? = nil
    var realtimeAuthFailed: Bool = false
    var realtimeUpdatedAt: Date? = nil
    var onRetryRealtime: () -> Void = {}
    var onLoadRealtime: () -> Void = {}

    private let margin: CGFloat = 24

    private var themeSymbols: [String] {
        RealtimeMerge.symbolsFromThemes(themes)
    }

    private var hasLiveFields: Bool {
        RealtimeMerge.hasAnyLive(symbols: themeSymbols, quotes: realtimeQuotes)
    }

    var body: some View {
        GeometryReader { geo in
            let contentW = min(geo.size.width - margin * 2, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top) {
                        PageTitle("概念主题", subtitle: "十五五科技主题 · 各板块龙头与第二梯队")
                        Spacer(minLength: 12)
                        RealtimeStatusBadge(
                            hasLiveFields: hasLiveFields,
                            hours: tradingHours,
                            authFailed: realtimeAuthFailed,
                            updatedAt: realtimeUpdatedAt,
                            onRetry: onRetryRealtime
                        )
                    }

                    if themes.isEmpty {
                        emptyState
                    } else {
                        let withLeaders = themes.reduce(0) { $0 + $1.leaderBoardCount }
                        Text("共 \(themes.count) 个主题 · \(withLeaders) 个板块已有龙头数据。龙头数据来自热点轮动归档，随每日任务累积逐步补全。")
                            .font(.system(size: 12))
                            .foregroundStyle(theme.textSecondary)
                        ForEach(themes) { theme in
                            ThemeCard(
                                theme: theme,
                                quotes: realtimeQuotes,
                                onSelectSymbol: onSelectSymbol
                            )
                        }
                    }
                }
                .frame(width: contentW, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, margin)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
        .background(theme.canvas)
        .task {
            onLoad()
            onLoadRealtime()
        }
        .onChange(of: themes.count) { _, _ in onLoadRealtime() }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("暂无主题数据")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(theme.textPrimary)
            Text("检查 storage/themes_15th_5y.yaml 是否存在，并运行「板块热点轮动归档」任务累积龙头数据。")
                .font(.system(size: 13))
                .foregroundStyle(theme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .kssCard()
    }
}

private struct ThemeCard: View {
    @Environment(\.kssTheme) private var tokens
    var theme: ThemeLeaders
    var quotes: [String: LongbridgeQuote] = [:]
    var onSelectSymbol: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2).fill(tokens.accent).frame(width: 4, height: 18)
                Text(theme.name)
                    .font(KSSFont.serif(18, .semibold))
                    .foregroundStyle(tokens.textPrimary)
                Spacer()
                Text("\(theme.boardCount) 板块 · \(theme.leaderBoardCount) 有龙头")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(tokens.textSecondary)
            }

            if theme.boards.isEmpty {
                Text("该主题板块龙头数据待归档积累")
                    .font(.system(size: 12))
                    .foregroundStyle(tokens.textSecondary)
                FlowChips(items: theme.boardNames, tint: tokens.textSecondary)
            } else {
                ForEach(theme.boards) { board in
                    boardBlock(board)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard()
    }

    private func boardBlock(_ board: ThemeBoard) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(board.board)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(tokens.textPrimary)
                if let cls = board.classification {
                    classificationBadge(cls)
                }
                Spacer(minLength: 0)
            }
            tierRow("龙头", board.leaders, tokens.up)
            tierRow("第二梯队", board.secondTier, tokens.ma20)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tokens.surfaceContainerHighest, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
    }

    @ViewBuilder
    private func tierRow(_ label: String, _ stocks: [HotspotLeaderStock], _ tint: Color) -> some View {
        if stocks.isEmpty {
            HStack(spacing: 8) {
                tierLabel(label, tint)
                Text("—").font(.system(size: 12)).foregroundStyle(tokens.textSecondary)
            }
        } else {
            HStack(alignment: .top, spacing: 8) {
                tierLabel(label, tint)
                FlowChips2 {
                    ForEach(stocks) { s in
                        stockChip(s, tint)
                    }
                }
            }
        }
    }

    private func tierLabel(_ label: String, _ tint: Color) -> some View {
        Text(label)
            .font(.system(size: 11, weight: .bold))
            .foregroundStyle(tint)
            .frame(width: 56, alignment: .leading)
            .padding(.top, 3)
    }

    private func stockChip(_ s: HotspotLeaderStock, _ tint: Color) -> some View {
        let quote = quotes[s.symbol.uppercased()]
        let live = RealtimeMerge.displayPrice(snapshotClose: nil, quote: quote)
        return Button { onSelectSymbol(s.symbol) } label: {
            HStack(spacing: 5) {
                Text(s.name)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(tokens.textBody)
                if let live {
                    LivePriceText(
                        value: live.close,
                        text: String(format: "%.2f", live.close),
                        baseColor: tokens.signColor(live.pct),
                        isLive: live.isLive,
                        font: .system(size: 11, weight: .bold, design: .monospaced)
                    )
                    if live.isLive {
                        LivePriceText(
                            value: live.pct,
                            text: String(format: "%+.1f%%", live.pct),
                            baseColor: tokens.signColor(live.pct),
                            isLive: true,
                            font: .system(size: 10, weight: .bold, design: .monospaced)
                        )
                    }
                }
                Text("\(s.appearances)")
                    .font(.system(size: 10, weight: .bold, design: .monospaced))
                    .foregroundStyle(tint)
            }
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(tint.opacity(0.10), in: Capsule())
            .overlay(Capsule().stroke(
                (live.map { $0.isLive ? tokens.signColor($0.pct).opacity(0.35) : tint.opacity(0.22) })
                    ?? tint.opacity(0.22),
                lineWidth: 0.5
            ))
            .lineLimit(1).fixedSize()
        }
        .buttonStyle(.plain)
        .help(chipHelp(s, live: live))
    }

    private func chipHelp(_ s: HotspotLeaderStock, live: (close: Double, pct: Double, isLive: Bool)?) -> String {
        if let live, live.isLive {
            return "\(s.name) \(s.symbol) · 现价 \(String(format: "%.2f", live.close)) \(String(format: "%+.2f%%", live.pct)) · 霸榜 \(s.appearances) 次"
        }
        return "\(s.name) \(s.symbol) · 霸榜 \(s.appearances) 次"
    }

    private func classificationBadge(_ cls: String) -> some View {
        let (text, tint): (String, Color) = {
            switch cls {
            case "mainline": return ("真主线", tokens.up)
            case "demonBoard": return ("妖板", tokens.accent)
            case "oldHotspotFading": return ("退潮", tokens.textSecondary)
            case "satellite": return ("卫星", tokens.ma20)
            default: return (cls, tokens.textSecondary)
            }
        }()
        return Text(text)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(tint)
            .padding(.horizontal, 6).padding(.vertical, 1.5)
            .background(tint.opacity(0.12), in: Capsule())
    }
}

/// FlowChips 的 ViewBuilder 版（容纳 Button 等异构子视图）。
struct FlowChips2<Content: View>: View {
    @Environment(\.kssTheme) private var theme
    @ViewBuilder var content: Content
    var body: some View {
        FlowLayout(spacing: 6, lineSpacing: 6) { content }
    }
}
