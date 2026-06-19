import SwiftUI

enum DateRange: String, CaseIterable, Identifiable {
    case all = "全部"
    case d7 = "近 7 天"
    case d30 = "近 30 天"
    var id: String { rawValue }
}

enum ReviewMode: String, CaseIterable, Identifiable {
    case sector = "板块复盘"
    case stock = "个股复盘"
    var id: String { rawValue }
}

struct ReviewsView: View {
    var reviews: [DailyReview]
    var sectorReviews: [SectorPulse]
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReview: (String) -> Void

    @State private var mode: ReviewMode = .sector
    @State private var selectedReview: DailyReview?
    @State private var selectedSectorDate: String?
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

                if mode == .stock {
                    stockList
                } else {
                    sectorList
                }
            }
            .frame(width: 300)

            Divider().overlay(KSSTheme.hairline)

            detailPane
                .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
        .onAppear {
            if sectorReviews.isEmpty { mode = .stock }
            if selectedSectorDate == nil { selectedSectorDate = sectorReviews.first?.tradeDate }
            if selectedReview == nil { selectedReview = visibleReviews.first }
            if mode == .stock, let review = selectedReview, detail?.path != review.path {
                onSelectReview(review.path)
            }
        }
        .onChange(of: selectedReview) { _, review in
            if let review { onSelectReview(review.path) }
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
            .foregroundStyle(KSSTheme.textSecondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            List(visibleReviews, selection: $selectedReview) { review in
                ReviewRow(review: review).tag(review)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
    }

    private var sectorList: some View {
        List(sectorReviews, selection: $selectedSectorDate) { pulse in
            SectorReviewRow(pulse: pulse).tag(pulse.tradeDate)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
    }

    // MARK: 详情

    @ViewBuilder private var detailPane: some View {
        if mode == .sector {
            if let pulse = selectedSector {
                SectorReviewPanel(pulse: pulse)
            } else {
                placeholder("暂无板块复盘数据")
            }
        } else if let review = selectedReview {
            stockDetail(review)
        } else {
            placeholder("选择一篇复盘查看全文")
        }
    }

    private func stockDetail(_ review: DailyReview) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                PageTitle(review.title)
                Spacer()
                StatusBadge(icon: "calendar", text: review.date, tint: KSSTheme.accent)
            }
            if !review.focusSymbols.isEmpty {
                Text("关注 " + review.focusSymbols.joined(separator: "  "))
                    .font(.system(size: 12.5, weight: .medium, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(2)
            }
            if isLoadingDetail && selectedPath == review.path {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if detail?.path == review.path, let detail {
                MarkdownWebView(text: detail.text)
                    .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                    .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
            } else {
                MarkdownWebView(text: review.excerpt)
                    .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                    .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 14))
            .foregroundStyle(KSSTheme.textSecondary)
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
    var pulse: SectorPulse

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "square.grid.2x2.fill")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(KSSTheme.accent)
                Text(dateLabel)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            Text("板块复盘")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
            Text(summary)
                .font(.system(size: 12.5))
                .foregroundStyle(KSSTheme.textSecondary)
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
    var pulse: SectorPulse

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    PageTitle("板块复盘")
                    Spacer()
                    StatusBadge(icon: "calendar", text: dateLabel, tint: KSSTheme.accent)
                }
                if let regime = regimeLine {
                    Text(regime)
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(KSSTheme.textBody)
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 152), spacing: 12)], spacing: 12) {
                    ForEach(pulse.themes) { SectorChip(theme: $0) }
                }

                SectorReviewTable(themes: pulse.themes)

                // 投顾点评：概念轮动 / 七大主题 / 加减仓建议 等文字，附在表格下方
                if let commentary = pulse.commentary, !commentary.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            RoundedRectangle(cornerRadius: 2).fill(KSSTheme.accent).frame(width: 4, height: 16)
                            Text("投顾点评")
                                .font(KSSFont.serif(16, .semibold))
                                .foregroundStyle(KSSTheme.textPrimary)
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
                            .foregroundStyle(KSSTheme.textSecondary)
                        Text(pulse.note)
                            .font(.system(size: 12))
                            .foregroundStyle(KSSTheme.textBody)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: 14)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(KSSTheme.canvas)
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
                        .foregroundStyle(KSSTheme.accent)
                        .padding(.top, 2)
                } else {
                    Text(attributed(block.text))
                        .font(.system(size: 13))
                        .foregroundStyle(KSSTheme.textBody)
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
    var themes: [SectorTheme]

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(KSSTheme.hairline)
            ForEach(Array(themes.enumerated()), id: \.element.id) { index, t in
                row(t)
                if index < themes.count - 1 {
                    Divider().overlay(KSSTheme.hairline)
                }
            }
        }
        .background(KSSTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("板块").frame(width: 96, alignment: .leading)
            Text("近5日").frame(width: 72, alignment: .trailing)
            Text("资金1日").frame(width: 72, alignment: .trailing)
            Text("资金5日").frame(width: 72, alignment: .trailing)
            Text("基金").frame(width: 48, alignment: .trailing)
            Text("5日排名").frame(width: 64, alignment: .trailing)
            Spacer(minLength: 8)
            Text("分级").frame(width: 84, alignment: .trailing)
        }
        .font(.system(size: 10.5, weight: .medium))
        .foregroundStyle(KSSTheme.textSecondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }

    private func row(_ t: SectorTheme) -> some View {
        HStack(spacing: 10) {
            Text(t.name)
                .font(.system(size: 13.5, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .frame(width: 96, alignment: .leading)
            num(t.past5Ret.map { KSSFormat.percent($0 / 100) }, tint: KSSTheme.signColor(t.past5Ret ?? 0))
                .frame(width: 72, alignment: .trailing)
            num(t.flow1d.map { String(format: "%+.1f", $0) }, tint: KSSTheme.textBody)
                .frame(width: 72, alignment: .trailing)
            num(t.flow5d.map { String(format: "%+.1f", $0) }, tint: KSSTheme.textBody)
                .frame(width: 72, alignment: .trailing)
            num(t.nFunds.map(String.init), tint: KSSTheme.textSecondary)
                .frame(width: 48, alignment: .trailing)
            num(t.rank5d.map { "#\($0)" }, tint: KSSTheme.textSecondary)
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
        if t.divergence || t.grade.contains("预警") || t.grade.contains("见顶") { return KSSTheme.up }
        if t.grade.contains("强势") { return KSSTheme.accent }
        return KSSTheme.textBody
    }
}

struct ReviewRow: View {
    var review: DailyReview

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "calendar")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(KSSTheme.accent)
                Text(review.date)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            Text(review.title)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
            Text(review.excerpt)
                .font(.system(size: 12.5))
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(2)
        }
        .padding(.vertical, 3)
    }
}
