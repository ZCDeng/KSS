import SwiftUI

// MARK: - 舆情热点（顶级页面）

/// 舆情热点选中键（日期 + 场景）；一次 tap 同时定 date/scene，单一 onChange 触发加载。
private struct NewsSelectionKey: Equatable {
    var date: String
    var scene: String
}

/// 舆情热点独立页：左栏档案列表（date + scene），右栏详情面板。
struct NewsDigestView: View {
    @Environment(\.kssTheme) private var theme
    var newsDigest: NewsDigestResponse?
    var isLoadingNewsDigest: Bool
    var onSelectNewsDigest: (String, String) -> Void

    @State private var selectedNews: NewsSelectionKey?

    var body: some View {
        HStack(spacing: 0) {
            newsDigestList
                .frame(width: 300)

            Divider().overlay(theme.hairline)

            detailPane
                .background(theme.canvas)
        }
        .background(theme.canvas)
        .onAppear { loadNewsDigestIfNeeded() }
        .onChange(of: selectedNews) { _, sel in
            if let sel { onSelectNewsDigest(sel.date, sel.scene) }
        }
    }

    /// 进入舆情热点页时若尚无数据则拉最新档（无参 = latest），mirror 妖板情绪惰性加载。
    private func loadNewsDigestIfNeeded() {
        if newsDigest == nil { onSelectNewsDigest("", "") }
    }

    // MARK: 左栏

    private var newsDigestList: some View {
        let entries = newsDigest?.index ?? []
        return List(entries) { entry in
            let isOn = isNewsEntrySelected(entry)
            Button { selectedNews = NewsSelectionKey(date: entry.date, scene: entry.scene) } label: {
                NewsDigestIndexRow(entry: entry)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .listRowBackground(isOn ? theme.accent.opacity(0.16) : Color.clear)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
        .overlay {
            if entries.isEmpty {
                placeholder(isLoadingNewsDigest ? "加载中…" : "暂无舆情档案")
            }
        }
    }

    /// 列表高亮：优先匹配用户选中键，否则跟随当前已加载 digest 的 date/scene。
    private func isNewsEntrySelected(_ entry: NewsDigestIndexEntry) -> Bool {
        if let sel = selectedNews {
            return sel.date == entry.date && sel.scene == entry.scene
        }
        if let cur = newsDigest?.selected {
            return cur.date == entry.date && cur.scene == entry.scene
        }
        return false
    }

    // MARK: 详情

    @ViewBuilder private var detailPane: some View {
        if isLoadingNewsDigest {
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let digest = newsDigest?.selected, newsDigest?.available == true {
            NewsDigestPanel(digest: digest)
        } else {
            placeholder(isLoadingNewsDigest ? "加载中…" : "暂无舆情热点数据")
        }
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(KSSFont.themed(14, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// 把 bridge 下发的 `<b>`/`<u>` HTML-ish 文本转可读 AttributedString：
/// `<b>` 映射为 markdown 粗体，去掉 `<u>` 与任何残留标签（不向用户暴露原始标签）。
func newsAttributed(_ raw: String) -> AttributedString {
    var t = raw
        .replacingOccurrences(of: "<b>", with: "**")
        .replacingOccurrences(of: "</b>", with: "**")
        .replacingOccurrences(of: "<u>", with: "")
        .replacingOccurrences(of: "</u>", with: "")
    t = t.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
    return (try? AttributedString(
        markdown: t,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    )) ?? AttributedString(t)
}

/// 舆情热点：日期 + 场景列表行。
struct NewsDigestIndexRow: View {
    @Environment(\.kssTheme) private var theme
    var entry: NewsDigestIndexEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "newspaper.fill")
                    .font(KSSFont.themed(10, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                Text(dateLabel)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text("舆情热点")
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text(entry.scene)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
        }
        .padding(.vertical, 3)
    }

    private var dateLabel: String {
        let raw = entry.date
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }
}

/// 舆情热点详情面板：集中热点方向 + 重大催化事件两段。
struct NewsDigestPanel: View {
    @Environment(\.kssTheme) private var theme
    var digest: NewsDigest

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    PageTitle("舆情热点", subtitle: digest.scene)
                    Spacer()
                    StatusBadge(icon: "calendar", text: dateLabel, tint: theme.accent)
                }

                if let meta = metaLine {
                    Text(meta)
                        .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }

                section(icon: "🔥", title: "集中热点方向") {
                    if digest.directions.isEmpty {
                        emptyHint("当前无集中热点方向")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(digest.directions) { NewsDirectionCard(direction: $0) }
                        }
                    }
                }

                section(icon: "⚡", title: "重大催化事件") {
                    if digest.catalysts.isEmpty {
                        emptyHint("当前无重大催化事件")
                    } else {
                        VStack(spacing: 8) {
                            ForEach(digest.catalysts) { NewsCatalystRow(catalyst: $0) }
                        }
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(theme.canvas)
    }

    @ViewBuilder
    private func section<Content: View>(icon: String, title: String,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 2).fill(theme.accent).frame(width: 4, height: 16)
                Text("\(icon) \(title)")
                    .font(KSSFont.themed(16, .semibold, theme: theme, design: .serif))
                    .foregroundStyle(theme.textPrimary)
            }
            content()
        }
    }

    private func emptyHint(_ text: String) -> some View {
        Text(text)
            .font(KSSFont.themed(12.5, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: 14)
    }

    private var dateLabel: String {
        let raw = digest.date
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    private var metaLine: String? {
        var parts: [String] = []
        if digest.llmStatus == "unavailable" { parts.append("LLM 不可用（回退情绪）") }
        if let q = digest.quarantinedCount, q > 0 { parts.append("隔离 \(q) 条") }
        if digest.partial == true { parts.append("部分数据") }
        if let sources = digest.sources, !sources.isEmpty {
            let total = sources.values.reduce(0, +)
            parts.append("信息源 \(sources.count) 平台 / \(total) 条")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// 舆情热点：单个方向卡片（情绪徽章 + 热度 + 信号质量 + 可展开源帖 + 龙头/二梯队）。
struct NewsDirectionCard: View {
    @Environment(\.kssTheme) private var theme
    var direction: NewsDirection
    @State private var showPosts = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(direction.label)
                    .font(KSSFont.themed(15, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                sentimentBadge
                Spacer(minLength: 4)
            }

            if let heat = direction.heatLine, !heat.isEmpty {
                Text(newsAttributed(heat))
                    .font(KSSFont.themed(12.5, theme: theme))
                    .foregroundStyle(theme.textBody)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text(signalLine)
                .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)

            if let posts = direction.sourcePosts, !posts.isEmpty {
                Button { withAnimation(.easeInOut(duration: 0.18)) { showPosts.toggle() } } label: {
                    HStack(spacing: 4) {
                        Image(systemName: showPosts ? "chevron.down" : "chevron.right")
                            .font(KSSFont.themed(9, .bold, theme: theme))
                        Text("信息源帖 \(posts.count)")
                            .font(KSSFont.themed(11.5, .semibold, theme: theme))
                    }
                    .foregroundStyle(theme.accent)
                }
                .buttonStyle(.plain)
                if showPosts {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(posts) { post in
                            NewsPostLine(post: post)
                        }
                    }
                    .padding(.leading, 4)
                }
            }

            if let stocks = direction.stocks, !stocks.isEmpty {
                stockGroups(stocks)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }

    private var sentimentBadge: some View {
        let (icon, tint) = sentimentStyle
        return StatusBadge(icon: icon, text: direction.sentiment.isEmpty ? "—" : direction.sentiment, tint: tint)
    }

    private var sentimentStyle: (String, Color) {
        switch direction.sentiment {
        case "偏多": return ("arrow.up.right", theme.up)
        case "偏空": return ("arrow.down.right", theme.down)
        default:     return ("arrow.left.arrow.right", theme.textSecondary)
        }
    }

    /// 独立源 N / 有|无真实催化 / 映射直达·N只|降级|未挂。
    private var signalLine: String {
        let q = direction.signalQuality
        let n = q?.independentSources
            ?? direction.independentConfirmations
            ?? direction.distinctSources?.count ?? 0
        let catalyst = (q?.hasRealCatalyst == true) ? "有真实催化" : "无真实催化"
        let mapping = q?.mapping ?? direction.mapping ?? "off"
        let mappingText: String
        switch mapping {
        case "direct":
            mappingText = "映射直达·\(direction.stocks?.count ?? 0)只"
        case "degrade":
            if let reason = direction.degradeReason, !reason.isEmpty {
                mappingText = "降级（\(reason)）"
            } else {
                mappingText = "降级"
            }
        default:
            mappingText = "未挂"
        }
        return "独立源 \(n) / \(catalyst) / \(mappingText)"
    }

    @ViewBuilder
    private func stockGroups(_ stocks: [NewsStock]) -> some View {
        let leaders = stocks.filter { $0.tier == "leader" }
        let seconds = stocks.filter { $0.tier == "second" }
        let others = stocks.filter { $0.tier != "leader" && $0.tier != "second" }
        VStack(alignment: .leading, spacing: 4) {
            if !leaders.isEmpty { stockRow("龙头", leaders, tint: theme.accent) }
            if !seconds.isEmpty { stockRow("二梯队", seconds, tint: theme.textBody) }
            if !others.isEmpty { stockRow("相关", others, tint: theme.textSecondary) }
        }
        .padding(.top, 2)
    }

    private func stockRow(_ title: String, _ stocks: [NewsStock], tint: Color) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(title)
                .font(KSSFont.themed(10.5, .bold, theme: theme))
                .foregroundStyle(tint)
                .frame(width: 40, alignment: .leading)
            Text(stocks.map { "\($0.name)(\($0.symbol))" }.joined(separator: " · "))
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textBody)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// 舆情热点：单条信息源帖（平台 + 标题，有链接可点）。
struct NewsPostLine: View {
    @Environment(\.kssTheme) private var theme
    var post: NewsSourcePost

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(post.source ?? "—")
                .font(KSSFont.themed(10.5, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 52, alignment: .leading)
                .lineLimit(1)
            if let urlString = post.url, let url = URL(string: urlString) {
                Link(post.title, destination: url)
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.accent)
                    .lineLimit(2)
            } else {
                Text(post.title)
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(2)
            }
        }
    }
}

/// 舆情热点：单条重大催化事件。
struct NewsCatalystRow: View {
    @Environment(\.kssTheme) private var theme
    var catalyst: NewsCatalyst

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("[\(catalyst.type)]")
                    .font(KSSFont.themed(10.5, .bold, theme: theme))
                    .foregroundStyle(theme.accent)
                if let urlString = catalyst.url, let url = URL(string: urlString) {
                    Link(catalyst.title, destination: url)
                        .font(KSSFont.themed(13, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(catalyst.title)
                        .font(KSSFont.themed(13, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 4)
            }
            HStack(spacing: 8) {
                if let source = catalyst.source, !source.isEmpty {
                    Text("— \(source)")
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                if catalyst.attachStocks == false {
                    Text("不挂个股")
                        .font(KSSFont.themed(10, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(theme.surfaceRaised, in: Capsule())
                        .overlay(Capsule().stroke(theme.hairline))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 12)
    }
}
