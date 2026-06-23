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
                Picker("", selection: $mode) {
                    ForEach(ReviewMode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .padding(.horizontal, 12)
                .padding(.top, 10)
                .padding(.bottom, 6)

                switch mode {
                case .stock: stockList
                case .sector: sectorList
                case .hotspotRotation: hotspotRotationList
                }
            }
            .frame(width: 300)

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
                        Image(systemName: "calendar").font(.system(size: 11, weight: .semibold))
                        Text(range.rawValue).font(.system(size: 12.5, weight: .semibold))
                    }
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                Spacer()
                Button { ascending.toggle() } label: {
                    HStack(spacing: 4) {
                        Image(systemName: ascending ? "arrow.up" : "arrow.down").font(.system(size: 11, weight: .bold))
                        Text(ascending ? "最早" : "最新").font(.system(size: 12.5, weight: .semibold))
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
                .listRowBackground(isOn ? theme.accent.opacity(0.16) : Color.clear)
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
            .listRowBackground(isOn ? theme.accent.opacity(0.16) : Color.clear)
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
            .listRowBackground(isOn ? theme.accent.opacity(0.16) : Color.clear)
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
        VStack(alignment: .leading, spacing: 10) {
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
            if isLoadingDetail && selectedPath == review.path {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if detail?.path == review.path, let detail {
                MarkdownWebView(text: detail.text)
                    .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
                    .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
            } else {
                MarkdownWebView(text: review.excerpt)
                    .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
                    .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 14))
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
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(theme.accent)
                Text(dateLabel)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text("板块复盘")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(theme.textPrimary)
            Text(summary)
                .font(.system(size: 12.5))
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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    PageTitle("板块复盘")
                    Spacer()
                    StatusBadge(icon: "calendar", text: dateLabel, tint: theme.accent)
                }
                if let regime = regimeLine {
                    Text(regime)
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(theme.textBody)
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 152), spacing: 12)], spacing: 12) {
                    ForEach(pulse.themes) { SectorChip(theme: $0) }
                }

                SectorReviewTable(themes: pulse.themes)

                // 投顾点评：概念轮动 / 七大主题 / 加减仓建议 等文字，附在表格下方
                if let commentary = pulse.commentary, !commentary.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            RoundedRectangle(cornerRadius: 2).fill(theme.accent).frame(width: 4, height: 16)
                            Text("投顾点评")
                                .font(KSSFont.serif(16, .semibold))
                                .foregroundStyle(theme.textPrimary)
                        }
                        CommentaryView(markdown: commentary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .kssCard(padding: 16)
                    }
                }

                if !pulse.note.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("一年回测语义")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(theme.textSecondary)
                        Text(pulse.note)
                            .font(.system(size: 12))
                            .foregroundStyle(theme.textBody)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: 14)
                }
            }
            .padding(16)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            ForEach(blocks) { block in
                if block.isHeader {
                    Text(block.text)
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(theme.accent)
                        .padding(.top, 2)
                } else {
                    Text(attributed(block.text))
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textBody)
                        .lineSpacing(3)
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
        .background(theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
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
        .font(.system(size: 10.5, weight: .medium))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func row(_ t: SectorTheme) -> some View {
        HStack(spacing: 10) {
            Text(t.name)
                .font(.system(size: 13.5, weight: .bold))
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
                .font(.system(size: 10.5, weight: .bold))
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
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(theme.accent)
                Text(review.date)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text(review.title)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
            Text(review.excerpt)
                .font(.system(size: 12.5))
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
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(theme.accent)
                Text(dateLabel)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            Text("妖板情绪")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(theme.textPrimary)
            Text(summary)
                .font(.system(size: 12.5))
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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    PageTitle("妖板情绪")
                    Spacer()
                    StatusBadge(icon: "calendar", text: dateLabel, tint: theme.accent)
                }

                HStack(spacing: 12) {
                    classificationTile("主线", snap.crossSourceSignals.mainline.count, theme.up)
                    classificationTile("妖板", snap.crossSourceSignals.demonBoard.count, theme.accent)
                    classificationTile("退潮", snap.crossSourceSignals.oldHotspotFading.count, theme.textSecondary)
                    classificationTile("卫星", snap.crossSourceSignals.satellite.count, theme.textBody)
                }

                HStack(spacing: 10) {
                    coverageBadge("龙头映射", snap.leaderCoverage)
                    coverageBadge("历史覆盖", snap.historyCoverage)
                }

                if !snap.leaderBoards.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            RoundedRectangle(cornerRadius: 2).fill(theme.accent).frame(width: 4, height: 16)
                            Text("妖王榜")
                                .font(KSSFont.serif(16, .semibold))
                                .foregroundStyle(theme.textPrimary)
                        }
                        HotspotLeaderTable(boards: snap.leaderBoards)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        RoundedRectangle(cornerRadius: 2).fill(theme.accent).frame(width: 4, height: 16)
                        Text("行业")
                            .font(KSSFont.serif(16, .semibold))
                            .foregroundStyle(theme.textPrimary)
                    }
                    HotspotBoardTable(boards: snap.industries)
                }

                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        RoundedRectangle(cornerRadius: 2).fill(theme.accent).frame(width: 4, height: 16)
                        Text("概念")
                            .font(KSSFont.serif(16, .semibold))
                            .foregroundStyle(theme.textPrimary)
                    }
                    HotspotBoardTable(boards: snap.concepts)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(theme.canvas)
    }

    private var dateLabel: String {
        let raw = snap.tradeDate
        guard raw.count == 8 else { return raw }
        return "\(raw.prefix(4))-\(raw.dropFirst(4).prefix(2))-\(raw.suffix(2))"
    }

    private func classificationTile(_ title: String, _ count: Int, _ tint: Color) -> some View {
        VStack(spacing: 4) {
            Text(title)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(tint)
            Text("\(count)")
                .font(KSSFont.harmonyNumber(20))
                .foregroundStyle(theme.textPrimary)
        }
        .frame(maxWidth: .infinity)
        .kssCard(padding: 10)
    }

    private func coverageBadge(_ name: String, _ value: Double) -> some View {
        Text("\(name) \(String(format: "%.0f%%", value * 100))")
            .font(.system(size: 11, weight: .semibold, design: .monospaced))
            .foregroundStyle(theme.textSecondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(theme.surfaceRaised, in: Capsule())
            .overlay(Capsule().stroke(theme.hairline))
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
        .background(theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
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
        .font(.system(size: 10.5, weight: .medium))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func row(_ b: HotspotBoard) -> some View {
        HStack(spacing: 10) {
            Text(b.name)
                .font(.system(size: 13.5, weight: .bold))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 96, alignment: .leading)
            num(b.pctChange.map { KSSFormat.percent($0 / 100) }, tint: theme.signColor(b.pctChange ?? 0))
                .frame(width: 64, alignment: .trailing)
            num("#\(b.todayRank)", tint: theme.textSecondary)
                .frame(width: 48, alignment: .trailing)
            Text(classificationLabel(b.classification))
                .font(.system(size: 10.5, weight: .bold))
                .foregroundStyle(classificationColor(b.classification))
                .frame(width: 84, alignment: .trailing)
            Spacer(minLength: 8)
            Text(topLeadersText(b.leaderStocks))
                .font(.system(size: 11))
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
        .background(theme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: theme.cardRadius).stroke(theme.hairline))
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("板块").frame(width: 96, alignment: .leading)
            Text("代码").frame(width: 72, alignment: .leading)
            Text("名称").frame(width: 96, alignment: .leading)
            Text("频次").frame(width: 56, alignment: .trailing)
            Spacer(minLength: 8)
        }
        .font(.system(size: 10.5, weight: .medium))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func row(_ leader: LeaderRow) -> some View {
        HStack(spacing: 10) {
            Text(leader.boardName)
                .font(.system(size: 13.5, weight: .bold))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 96, alignment: .leading)
            Text(leader.symbol)
                .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 72, alignment: .leading)
                .lineLimit(1)
            Text(leader.name)
                .font(.system(size: 12.5))
                .foregroundStyle(theme.textBody)
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
