import SwiftUI

enum DateRange: String, CaseIterable, Identifiable {
    case all = "全部"
    case d7 = "近 7 天"
    case d30 = "近 30 天"
    var id: String { rawValue }
}

enum ReviewMode: String, CaseIterable, Identifiable {
    case sector = "板块复盘"
    case hotspotRotation = "妖板情绪"
    case stock = "个股复盘"
    var id: String { rawValue }
}

struct ReviewsView: View {
    @Environment(\.kssTheme) private var theme
    var reviews: [DailyReview]
    var sectorReviews: [SectorPulse]
    var sectorRotationHistory: [HotspotRotationHistoryItem]
    var sectorRotationDetail: HotspotRotationSnapshot?
    var isLoadingSectorRotation: Bool
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReview: (String) -> Void
    var onSelectSectorRotationDate: (String) -> Void
    var onOpenExternally: (String) -> Void

    @State private var mode: ReviewMode = .sector
    @State private var selectedReview: DailyReview?
    @State private var selectedSectorDate: String?
    @State private var selectedHotspotRotationDate: String?
    @State private var ascending = false
    @State private var range: DateRange = .all
    @State private var hoveredListID: String?

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    private var visibleReviews: [DailyReview] {
        var items = reviews
        if range != .all, let latest = reviews.map(\.date).max() {
            let cutoff = Self.cutoff(latest: latest, days: range == .d7 ? 7 : 30)
            items = items.filter { $0.date >= cutoff }
        }
        return items.sorted { ascending ? $0.date < $1.date : $0.date > $1.date }
    }

    private var selectedSector: SectorPulse? {
        if let date = selectedSectorDate, let hit = sectorReviews.first(where: { $0.tradeDate == date }) {
            return hit
        }
        return sectorReviews.first
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                modeTabBar
                switch mode {
                case .stock: stockList
                case .sector: sectorList
                case .hotspotRotation: hotspotRotationList
                }
            }
            .frame(width: XcomListChrome.listColumnWidth(theme.system))

            Divider().overlay(theme.hairline)

            detailPane
                .background(theme.canvas)
        }
        .background(theme.canvas)
        .onAppear {
            if sectorReviews.isEmpty && !sectorRotationHistory.isEmpty {
                mode = .hotspotRotation
            } else if sectorReviews.isEmpty {
                mode = .stock
            }
            if selectedSectorDate == nil { selectedSectorDate = sectorReviews.first?.tradeDate }
            if selectedHotspotRotationDate == nil { selectedHotspotRotationDate = sectorRotationHistory.first?.tradeDate }
            if selectedReview == nil { selectedReview = visibleReviews.first }
            if mode == .hotspotRotation, let date = selectedHotspotRotationDate {
                onSelectSectorRotationDate(date)
            }
            if mode == .stock, let review = selectedReview, detail?.path != review.path {
                onSelectReview(review.path)
            }
        }
        .onChange(of: selectedReview) { _, review in
            if let review { onSelectReview(review.path) }
        }
        .onChange(of: selectedHotspotRotationDate) { _, date in
            if let date { onSelectSectorRotationDate(date) }
        }
    }

    @ViewBuilder
    private var modeTabBar: some View {
        if isXcom {
            XcomUnderlineTabBar(
                options: ReviewMode.allCases.map { ($0, $0.rawValue) },
                selection: $mode,
                stretch: true
            )
        } else {
            KSSSegmentedControl(
                options: ReviewMode.allCases.map { ($0, $0.rawValue) },
                selection: $mode,
                stretch: true
            )
            .padding(.horizontal, 12)
            .padding(.top, 10)
            .padding(.bottom, 6)
        }
    }

    private func listRowFill(isOn: Bool, id: String) -> Color {
        XcomListChrome.listSelectionFill(
            isOn: isOn,
            isHovered: isXcom && hoveredListID == id,
            theme: theme
        )
    }

    // MARK: 左栏

    private var stockList: some View {
        VStack(spacing: 0) {
            HStack {
                Menu {
                    ForEach(DateRange.allCases) { option in
                        Button { range = option } label: {
                            Label(option.rawValue, systemImage: range == option ? "checkmark" : "calendar")
                        }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "calendar").font(KSSFont.themed(11, .semibold, theme: theme))
                        Text(range.rawValue).font(KSSFont.themed(12.5, .semibold, theme: theme))
                    }
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                Spacer()
                Button { ascending.toggle() } label: {
                    HStack(spacing: 4) {
                        Image(systemName: ascending ? "arrow.up" : "arrow.down").font(KSSFont.themed(11, .bold, theme: theme))
                        Text(ascending ? "最早" : "最新").font(KSSFont.themed(12.5, .semibold, theme: theme))
                    }
                }
                .buttonStyle(.plain)
            }
            .foregroundStyle(theme.textSecondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            List(visibleReviews) { review in
                let isOn = selectedReview?.id == review.id
                Button { selectedReview = review } label: {
                    ReviewRow(review: review)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .listRowBackground(listRowFill(isOn: isOn, id: review.id))
                .listRowSeparator(isXcom ? .visible : .automatic)
                .onHover { hovering in
                    guard isXcom else { return }
                    hoveredListID = hovering ? review.id : (hoveredListID == review.id ? nil : hoveredListID)
                }
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
    }

    private var sectorList: some View {
        List(sectorReviews) { pulse in
            let isOn = (selectedSectorDate ?? sectorReviews.first?.tradeDate) == pulse.tradeDate
            Button { selectedSectorDate = pulse.tradeDate } label: {
                SectorReviewRow(pulse: pulse)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .listRowBackground(listRowFill(isOn: isOn, id: pulse.id))
            .listRowSeparator(isXcom ? .visible : .automatic)
            .onHover { hovering in
                guard isXcom else { return }
                hoveredListID = hovering ? pulse.id : (hoveredListID == pulse.id ? nil : hoveredListID)
            }
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
    }

    private var hotspotRotationList: some View {
        List(sectorRotationHistory) { item in
            let isOn = (selectedHotspotRotationDate ?? sectorRotationHistory.first?.tradeDate) == item.tradeDate
            Button { selectedHotspotRotationDate = item.tradeDate } label: {
                HotspotRotationHistoryRow(item: item)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .listRowBackground(listRowFill(isOn: isOn, id: item.id))
            .listRowSeparator(isXcom ? .visible : .automatic)
            .onHover { hovering in
                guard isXcom else { return }
                hoveredListID = hovering ? item.id : (hoveredListID == item.id ? nil : hoveredListID)
            }
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
    }

    // MARK: 详情

    @ViewBuilder private var detailPane: some View {
        switch mode {
        case .sector:
            if let pulse = selectedSector {
                SectorReviewPanel(pulse: pulse)
            } else {
                placeholder("暂无板块复盘数据")
            }
        case .hotspotRotation:
            if isLoadingSectorRotation {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let snap = sectorRotationDetail {
                HotspotRotationPanel(snap: snap)
            } else {
                placeholder("选择日期查看妖板情绪")
            }
        case .stock:
            if let review = selectedReview {
                stockDetail(review)
            } else {
                placeholder("选择一篇复盘查看全文")
            }
        }
    }

    private func stockDetail(_ review: DailyReview) -> some View {
        VStack(alignment: .leading, spacing: isXcom ? 12 : 10) {
            if isXcom {
                // 线程头：标题 + meta 一行（日期 · 个股复盘），外链图标
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(review.title)
                            .font(KSSFont.themed(XcomListChrome.detailTitlePointSize(theme.system), .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .textSelection(.enabled)
                        Text("\(review.date) · 个股复盘")
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                    Spacer(minLength: 8)
                    Button { onOpenExternally(review.path) } label: {
                        Image(systemName: "arrow.up.right.square")
                            .font(KSSFont.themed(14, .semibold, theme: theme))
                            .foregroundStyle(theme.accent)
                    }
                    .buttonStyle(.plain)
                    .help("用 MarkEdit 打开当前报告")
                }
                if !review.focusSymbols.isEmpty {
                    // 关注标的：任务区同款胶囊，横向 wrap
                    FlowLayout(spacing: 6) {
                        ForEach(review.focusSymbols, id: \.self) { sym in
                            Text(sym)
                                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 1.5)
                                .background(theme.textSecondary.opacity(0.12), in: Capsule())
                        }
                    }
                }
            } else {
                HStack(alignment: .firstTextBaseline) {
                    PageTitle(review.title)
                    Spacer()
                    Button { onOpenExternally(review.path) } label: {
                        Image(systemName: "doc.text")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(theme.accent)
                    .help("用 MarkEdit 打开当前报告")
                    .padding(.trailing, 6)
                    StatusBadge(icon: "calendar", text: review.date, tint: theme.accent)
                }
                if !review.focusSymbols.isEmpty {
                    Text("关注 " + review.focusSymbols.joined(separator: "  "))
                        .font(.system(size: 12.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(2)
                }
            }

            if isLoadingDetail && selectedPath == review.path {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if detail?.path == review.path, let detail {
                markdownBody(detail.text)
            } else {
                markdownBody(review.excerpt)
            }
        }
        .padding(isXcom ? 20 : 16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    /// xcom：无圆角描边卡，正文直接铺在 canvas 上（thread 阅读感）。
    @ViewBuilder
    private func markdownBody(_ text: String) -> some View {
        if isXcom {
            MarkdownWebView(text: text)
        } else {
            MarkdownWebView(text: text)
                .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
                .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
        }
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(KSSFont.themed(14, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private static func cutoff(latest: String, days: Int) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let latestDate = formatter.date(from: latest),
              let cut = Calendar.current.date(byAdding: .day, value: -days, to: latestDate) else {
            return ""
        }
        return formatter.string(from: cut)
    }
}

/// 板块复盘列表行：日期 + 强势确认数 + 头部板块。
struct SectorReviewRow: View {
    @Environment(\.kssTheme) private var theme
    var pulse: SectorPulse

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "square.grid.2x2.fill")
                    .font(KSSFont.themed(10, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                Text(dateLabel)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text("板块复盘")
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text(summary)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(2)
        }
        .padding(.vertical, 3)
    }

    private var dateLabel: String {
        let raw = pulse.tradeDate
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    private var summary: String {
        let strong = pulse.themes.filter { $0.grade.contains("强势") }.count
        let names = pulse.themes.prefix(3).map(\.name).joined(separator: " · ")
        return "强势确认 \(strong) 个 · \(names)"
    }
}

/// 今日板块复盘：资金申赎 + 强势确认分级，含明细表与一年回测语义说明。
struct SectorReviewPanel: View {
    @Environment(\.kssTheme) private var theme
    var pulse: SectorPulse

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: isXcom ? SettingsFormStyle.blockSpacing : 14) {
                if isXcom {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("板块复盘")
                            .font(KSSFont.themed(SettingsFormStyle.pageTitle, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text("\(dateLabel) · 资金与分级")
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        PageTitle("板块复盘")
                        Spacer()
                        StatusBadge(icon: "calendar", text: dateLabel, tint: theme.accent)
                    }
                }
                if let regime = regimeLine {
                    Text(regime)
                        .font(KSSFont.themed(
                            isXcom ? SettingsFormStyle.bodyHint : 12.5,
                            isXcom ? .regular : .medium,
                            theme: theme
                        ))
                        .foregroundStyle(isXcom ? theme.textSecondary : theme.textBody)
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 152), spacing: isXcom ? 10 : 12)], spacing: isXcom ? 10 : 12) {
                    ForEach(pulse.themes) { SectorChip(theme: $0) }
                }

                SectorReviewTable(themes: pulse.themes)

                if let commentary = pulse.commentary, !commentary.isEmpty {
                    VStack(alignment: .leading, spacing: isXcom ? SettingsFormStyle.groupSpacing : 8) {
                        Text("投顾点评")
                            .font(KSSFont.themed(
                                isXcom ? SettingsFormStyle.sectionHeader : 16,
                                .bold,
                                theme: theme
                            ))
                            .foregroundStyle(isXcom ? theme.textSecondary : theme.textPrimary)
                        CommentaryView(markdown: commentary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .kssCard(padding: isXcom ? SettingsFormStyle.cardPadding : 16)
                    }
                }

                if !pulse.note.isEmpty {
                    VStack(alignment: .leading, spacing: isXcom ? SettingsFormStyle.titleMetaSpacing : 4) {
                        Text("一年回测语义")
                            .font(KSSFont.themed(
                                isXcom ? SettingsFormStyle.sectionHeader : 11,
                                .bold,
                                theme: theme
                            ))
                            .foregroundStyle(theme.textSecondary)
                        Text(pulse.note)
                            .font(KSSFont.themed(isXcom ? SettingsFormStyle.bodyHint : 12, theme: theme))
                            .foregroundStyle(isXcom ? theme.textSecondary : theme.textBody)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: isXcom ? SettingsFormStyle.cardPadding : 14)
                }
            }
            .padding(isXcom ? 20 : 16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(theme.canvas)
    }

    private var dateLabel: String {
        let raw = pulse.tradeDate
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    private var regimeLine: String? {
        guard let mom = pulse.regimeMom20 else { return nil }
        let on = pulse.regimeInRegime == true
        let th = pulse.regimeMom20Th.map { String(format: "%.1f", $0) } ?? "-"
        return "动量体制：mom20 \(String(format: "%.1f", mom))（阈值 \(th)）· \(on ? "趋势确认 ✅" : "未达阈值 / 震荡")"
    }
}

/// 投顾点评：把 `## 段标题` + `**强调**` 的 Markdown 原生渲染（避免 ScrollView 内嵌 WebView 测高问题）。
struct CommentaryView: View {
    @Environment(\.kssTheme) private var theme
    var markdown: String

    private struct Block: Identifiable {
        let id = UUID()
        let isHeader: Bool
        let text: String
    }

    private var blocks: [Block] {
        var out: [Block] = []
        for para in markdown.components(separatedBy: "\n\n") {
            let trimmed = para.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { continue }
            if trimmed.hasPrefix("## ") {
                out.append(Block(isHeader: true, text: String(trimmed.dropFirst(3))))
            } else {
                out.append(Block(isHeader: false, text: trimmed))
            }
        }
        return out
    }

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        VStack(alignment: .leading, spacing: isXcom ? 10 : 11) {
            ForEach(blocks) { block in
                if block.isHeader {
                    Text(block.text)
                        .font(KSSFont.themed(
                            isXcom ? SettingsFormStyle.itemTitle : 14,
                            .bold,
                            theme: theme
                        ))
                        .foregroundStyle(isXcom ? theme.textPrimary : theme.accent)
                        .padding(.top, 2)
                } else {
                    Text(attributed(block.text))
                        .font(KSSFont.themed(isXcom ? 15 : 13, theme: theme))
                        .foregroundStyle(isXcom ? theme.textPrimary : theme.textBody)
                        .lineSpacing(isXcom ? 4 : 3)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func attributed(_ text: String) -> AttributedString {
        (try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(text)
    }
}

/// 板块明细表：板块 / 近5日 / 资金1日 / 资金5日 / 基金数 / 5日排名 / 分级。
struct SectorReviewTable: View {
    @Environment(\.kssTheme) private var theme
    var themes: [SectorTheme]

    enum SectorSort: Hashable { case name, past5Ret, flow1d, flow5d, nFunds, rank5d }
    @State private var sortKey: SectorSort = .rank5d
    @State private var ascending = true

    private var sortedThemes: [SectorTheme] {
        let asc = ascending
        func cmp<T: Comparable>(_ a: T?, _ b: T?) -> Bool {
            switch (a, b) {
            case let (x?, y?): return asc ? x < y : x > y
            case (nil, _?): return false   // nil 末尾
            case (_?, nil): return true
            case (nil, nil): return false
            }
        }
        return themes.sorted { a, b in
            switch sortKey {
            case .name:
                let r = a.name.localizedCompare(b.name)
                return asc ? r == .orderedAscending : r == .orderedDescending
            case .past5Ret: return cmp(a.past5Ret, b.past5Ret)
            case .flow1d:   return cmp(a.flow1d, b.flow1d)
            case .flow5d:   return cmp(a.flow5d, b.flow5d)
            case .nFunds:   return cmp(a.nFunds, b.nFunds)
            case .rank5d:   return cmp(a.rank5d, b.rank5d)
            }
        }
    }

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(theme.hairline)
            ForEach(Array(sortedThemes.enumerated()), id: \.element.id) { index, t in
                row(t)
                if index < themes.count - 1 {
                    Divider().overlay(theme.hairline)
                }
            }
        }
        // P1 xcom：无圆角填色卡，仅 hairline 行表
        .background(isXcom ? theme.canvas : theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius)
                .stroke(isXcom ? Color.clear : theme.hairline)
        )
        .overlay(alignment: .top) {
            if isXcom { Rectangle().fill(theme.hairline).frame(height: 1) }
        }
        .overlay(alignment: .bottom) {
            if isXcom { Rectangle().fill(theme.hairline).frame(height: 1) }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            SortHeaderCell(title: "板块", key: SectorSort.name, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: 96)
            SortHeaderCell(title: "近5日", key: SectorSort.past5Ret, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 72)
            SortHeaderCell(title: "资金1日", key: SectorSort.flow1d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 72)
            SortHeaderCell(title: "资金5日", key: SectorSort.flow5d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 72)
            SortHeaderCell(title: "基金", key: SectorSort.nFunds, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 48)
            SortHeaderCell(title: "5日排名", key: SectorSort.rank5d, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 64)
            Spacer(minLength: 8)
            Text("分级").frame(width: 84, alignment: .trailing)
        }
        .font(KSSFont.themed(isXcom ? 12 : 10.5, isXcom ? .bold : .medium, theme: theme))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, isXcom ? 10 : 14)
        .padding(.vertical, isXcom ? 10 : 9)
    }

    private func row(_ t: SectorTheme) -> some View {
        HStack(spacing: 10) {
            Text(t.name)
                .font(KSSFont.themed(isXcom ? 13 : 13.5, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 96, alignment: .leading)
            num(t.past5Ret.map { KSSFormat.percent($0 / 100) }, tint: theme.signColor(t.past5Ret ?? 0))
                .frame(width: 72, alignment: .trailing)
            num(t.flow1d.map { String(format: "%+.1f", $0) }, tint: theme.textBody)
                .frame(width: 72, alignment: .trailing)
            num(t.flow5d.map { String(format: "%+.1f", $0) }, tint: theme.textBody)
                .frame(width: 72, alignment: .trailing)
            num(t.nFunds.map(String.init), tint: theme.textSecondary)
                .frame(width: 48, alignment: .trailing)
            num(t.rank5d.map { "#\($0)" }, tint: theme.textSecondary)
                .frame(width: 64, alignment: .trailing)
            Spacer(minLength: 8)
            Text(t.divergence ? "见顶预警" : t.grade)
                .font(KSSFont.themed(10.5, .bold, theme: theme))
                .foregroundStyle(gradeColor(t))
                .frame(width: 84, alignment: .trailing)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func num(_ text: String?, tint: Color) -> some View {
        Text(text ?? "—")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(tint)
            .lineLimit(1)
    }

    private func gradeColor(_ t: SectorTheme) -> Color {
        if t.divergence || t.grade.contains("预警") || t.grade.contains("见顶") { return theme.up }
        if t.grade.contains("强势") { return theme.accent }
        return theme.textBody
    }
}

struct ReviewRow: View {
    @Environment(\.kssTheme) private var theme
    var review: DailyReview

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "calendar")
                    .font(KSSFont.themed(10, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                Text(review.date)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text(review.title)
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
            Text(review.excerpt)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(2)
        }
        .padding(.vertical, 3)
    }
}

/// 热点轮动日期列表行.
struct HotspotRotationHistoryRow: View {
    @Environment(\.kssTheme) private var theme
    var item: HotspotRotationHistoryItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "flame.fill")
                    .font(KSSFont.themed(10, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                Text(dateLabel)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text("妖板情绪")
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text(summary)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(2)
        }
        .padding(.vertical, 3)
    }

    private var dateLabel: String {
        let raw = item.tradeDate
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    private var summary: String {
        "主线 \(item.mainlineCount) · 妖板 \(item.demonBoardCount) · 退潮 \(item.oldHotspotFadingCount)"
    }
}

/// 热点轮动详情面板：四象限统计 + 板块表 + 妖王榜.
struct HotspotRotationPanel: View {
    @Environment(\.kssTheme) private var theme
    var snap: HotspotRotationSnapshot

    enum BoardKind: String, CaseIterable, Identifiable {
        case industry = "行业"
        case concept = "概念"
        var id: String { rawValue }
    }
    @State private var boardKind: BoardKind = .industry

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: isXcom ? SettingsFormStyle.blockSpacing : 14) {
                if isXcom {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("妖板情绪")
                            .font(KSSFont.themed(SettingsFormStyle.pageTitle, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text("\(dateLabel) · 热点轮动")
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        PageTitle("妖板情绪")
                        Spacer()
                        StatusBadge(icon: "calendar", text: dateLabel, tint: theme.accent)
                    }
                }

                HStack(spacing: isXcom ? 10 : 12) {
                    classificationTile("主线", snap.crossSourceSignals.mainline.count, theme.up)
                    classificationTile("妖板", snap.crossSourceSignals.demonBoard.count, theme.accent)
                    classificationTile("退潮", snap.crossSourceSignals.oldHotspotFading.count, theme.textSecondary)
                    classificationTile("卫星", snap.crossSourceSignals.satellite.count, theme.textBody)
                }

                FlowLayout(spacing: 6) {
                    coverageBadge("龙头映射", snap.leaderCoverage)
                    coverageBadge("历史覆盖", snap.historyCoverage)
                }

                if !snap.leaderBoards.isEmpty {
                    VStack(alignment: .leading, spacing: isXcom ? SettingsFormStyle.groupSpacing : 8) {
                        Text("妖王榜")
                            .font(KSSFont.themed(
                                isXcom ? SettingsFormStyle.sectionHeader : 16,
                                .bold,
                                theme: theme
                            ))
                            .foregroundStyle(isXcom ? theme.textSecondary : theme.textPrimary)
                        HotspotLeaderTable(boards: snap.leaderBoards)
                    }
                }

                VStack(alignment: .leading, spacing: isXcom ? SettingsFormStyle.groupSpacing : 8) {
                    if isXcom {
                        XcomUnderlineTabBar(
                            options: BoardKind.allCases.map { ($0, "\($0.rawValue) \(boards(for: $0).count)") },
                            selection: $boardKind,
                            stretch: true
                        )
                    } else {
                        KSSSegmentedControl(
                            options: BoardKind.allCases.map { ($0, "\($0.rawValue) \(boards(for: $0).count)") },
                            selection: $boardKind,
                            stretch: true
                        )
                    }

                    HotspotBoardTable(boards: boards(for: boardKind))
                }
            }
            .padding(isXcom ? 20 : 16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(theme.canvas)
    }

    private func boards(for kind: BoardKind) -> [HotspotBoard] {
        switch kind {
        case .industry: return snap.industries
        case .concept: return snap.concepts
        }
    }

    private var dateLabel: String {
        let raw = snap.tradeDate
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    private func classificationTile(_ title: String, _ count: Int, _ tint: Color) -> some View {
        VStack(spacing: isXcom ? 3 : 4) {
            Text(title)
                .font(KSSFont.themed(isXcom ? SettingsFormStyle.meta : 11, .bold, theme: theme))
                .foregroundStyle(tint)
            Text("\(count)")
                .font(KSSFont.harmonyNumber(isXcom ? 18 : 20))
                .foregroundStyle(theme.textPrimary)
        }
        .frame(maxWidth: .infinity)
        .kssCard(padding: isXcom ? SettingsFormStyle.cardPadding : 10)
    }

    private func coverageBadge(_ name: String, _ value: Double) -> some View {
        Text("\(name) \(String(format: "%.0f%%", value * 100))")
            .font(KSSFont.themed(isXcom ? SettingsFormStyle.meta : 11, .semibold, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .padding(.horizontal, isXcom ? 7 : 8)
            .padding(.vertical, isXcom ? 1.5 : 4)
            .background(
                isXcom
                    ? theme.textSecondary.opacity(0.12)
                    : theme.surfaceRaised.opacity(1),
                in: Capsule()
            )
            .overlay(
                Capsule().stroke(isXcom ? Color.clear : theme.hairline)
            )
    }
}

/// 热点轮动板块明细表.
struct HotspotBoardTable: View {
    @Environment(\.kssTheme) private var theme
    var boards: [HotspotBoard]

    enum BoardSort: Hashable { case name, pctChange, rank, classification }
    @State private var sortKey: BoardSort = .rank
    @State private var ascending = true

    private var sortedBoards: [HotspotBoard] {
        let asc = ascending
        func cmp<T: Comparable>(_ a: T?, _ b: T?) -> Bool {
            switch (a, b) {
            case let (x?, y?): return asc ? x < y : x > y
            case (nil, _?): return false   // nil 末尾
            case (_?, nil): return true
            case (nil, nil): return false
            }
        }
        return boards.sorted { a, b in
            switch sortKey {
            case .name:
                let r = a.name.localizedCompare(b.name)
                return asc ? r == .orderedAscending : r == .orderedDescending
            case .pctChange: return cmp(a.pctChange, b.pctChange)
            case .rank:      return asc ? a.todayRank < b.todayRank : a.todayRank > b.todayRank
            case .classification:
                let r = a.classification.localizedCompare(b.classification)
                return asc ? r == .orderedAscending : r == .orderedDescending
            }
        }
    }

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(theme.hairline)
            ForEach(Array(sortedBoards.enumerated()), id: \.element.id) { index, b in
                row(b)
                if index < boards.count - 1 {
                    Divider().overlay(theme.hairline)
                }
            }
        }
        .background(isXcom ? theme.canvas : theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius)
                .stroke(isXcom ? Color.clear : theme.hairline)
        )
        .overlay(alignment: .top) {
            if isXcom { Rectangle().fill(theme.hairline).frame(height: 1) }
        }
        .overlay(alignment: .bottom) {
            if isXcom { Rectangle().fill(theme.hairline).frame(height: 1) }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            SortHeaderCell(title: "板块", key: BoardSort.name, selection: $sortKey, ascending: $ascending,
                           alignment: .leading, width: 96)
            SortHeaderCell(title: "今日", key: BoardSort.pctChange, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 64)
            SortHeaderCell(title: "排名", key: BoardSort.rank, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 48)
            SortHeaderCell(title: "分类", key: BoardSort.classification, selection: $sortKey, ascending: $ascending,
                           alignment: .trailing, width: 84)
            Spacer(minLength: 8)
            Text("龙头").frame(width: 120, alignment: .leading)
        }
        .font(KSSFont.themed(isXcom ? 12 : 10.5, isXcom ? .bold : .medium, theme: theme))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, isXcom ? 10 : 14)
        .padding(.vertical, isXcom ? 10 : 9)
    }

    private func row(_ b: HotspotBoard) -> some View {
        HStack(spacing: 10) {
            Text(b.name)
                .font(KSSFont.themed(isXcom ? 13 : 13.5, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 96, alignment: .leading)
            num(b.pctChange.map { KSSFormat.percent($0 / 100) }, tint: theme.signColor(b.pctChange ?? 0))
                .frame(width: 64, alignment: .trailing)
            num("#\(b.todayRank)", tint: theme.textSecondary)
                .frame(width: 48, alignment: .trailing)
            Text(classificationLabel(b.classification))
                .font(KSSFont.themed(10.5, .bold, theme: theme))
                .foregroundStyle(classificationColor(b.classification))
                .frame(width: 84, alignment: .trailing)
            Spacer(minLength: 8)
            Text(topLeadersText(b.leaderStocks))
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textBody)
                .frame(width: 120, alignment: .leading)
                .lineLimit(1)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func num(_ text: String?, tint: Color) -> some View {
        Text(text ?? "—")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(tint)
            .lineLimit(1)
    }

    private func classificationLabel(_ cls: String) -> String {
        switch cls {
        case "mainline": return "主线"
        case "demonBoard": return "妖板"
        case "oldHotspotFading": return "退潮"
        default: return "卫星"
        }
    }

    private func classificationColor(_ cls: String) -> Color {
        switch cls {
        case "mainline": return theme.up
        case "demonBoard": return theme.accent
        case "oldHotspotFading": return theme.textSecondary
        default: return theme.textBody
        }
    }

    private func topLeadersText(_ leaders: [HotspotLeaderStock]?) -> String {
        guard let leaders = leaders, !leaders.isEmpty else { return "—" }
        return leaders.prefix(2).map { "\($0.name)(\($0.appearances))" }.joined(separator: " · ")
    }
}

/// 妖王榜：按龙头跨天频次排序.
struct HotspotLeaderTable: View {
    @Environment(\.kssTheme) private var theme
    var boards: [HotspotBoard]

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    private struct LeaderRow: Identifiable, Hashable {
        var id: String { "\(boardName)-\(symbol)" }
        var boardName: String
        var symbol: String
        var name: String
        var appearances: Int
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(theme.hairline)
            let rows = allLeaders()
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, leader in
                row(leader)
                if index < rows.count - 1 {
                    Divider().overlay(theme.hairline)
                }
            }
        }
        .background(isXcom ? theme.canvas : theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: isXcom ? 0 : theme.cardRadius)
                .stroke(isXcom ? Color.clear : theme.hairline)
        )
        .overlay(alignment: .top) {
            if isXcom { Rectangle().fill(theme.hairline).frame(height: 1) }
        }
        .overlay(alignment: .bottom) {
            if isXcom { Rectangle().fill(theme.hairline).frame(height: 1) }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("板块").frame(width: 96, alignment: .leading)
            Text("代码").frame(width: 72, alignment: .leading)
            Text("名称").frame(width: 96, alignment: .leading)
            Text("频次").frame(width: 56, alignment: .trailing)
            Spacer(minLength: 8)
        }
        .font(KSSFont.themed(isXcom ? 12 : 10.5, isXcom ? .bold : .medium, theme: theme))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, isXcom ? 10 : 14)
        .padding(.vertical, isXcom ? 10 : 9)
    }

    private func row(_ leader: LeaderRow) -> some View {
        HStack(spacing: 10) {
            Text(leader.boardName)
                .font(KSSFont.themed(isXcom ? 13 : 13.5, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 96, alignment: .leading)
            Text(leader.symbol)
                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 72, alignment: .leading)
                .lineLimit(1)
            Text(leader.name)
                .font(KSSFont.themed(isXcom ? 13 : 12.5, theme: theme))
                .foregroundStyle(isXcom ? theme.textPrimary : theme.textBody)
                .frame(width: 96, alignment: .leading)
                .lineLimit(1)
            Text("\(leader.appearances)")
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                .foregroundStyle(theme.accent)
                .frame(width: 56, alignment: .trailing)
            Spacer(minLength: 8)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func allLeaders() -> [LeaderRow] {
        var rows: [LeaderRow] = []
        for board in boards {
            for leader in board.leaderStocks ?? [] {
                rows.append(LeaderRow(
                    boardName: board.name,
                    symbol: leader.symbol,
                    name: leader.name,
                    appearances: leader.appearances
                ))
            }
        }
        return rows.sorted { $0.appearances > $1.appearances }
    }
}
