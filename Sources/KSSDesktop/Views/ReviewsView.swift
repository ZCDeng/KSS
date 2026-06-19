import SwiftUI

enum DateRange: String, CaseIterable, Identifiable {
    case all = "全部"
    case d7 = "近 7 天"
    case d30 = "近 30 天"
    var id: String { rawValue }
}

struct ReviewsView: View {
    var reviews: [DailyReview]
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReview: (String) -> Void

    @State private var selectedReview: DailyReview?
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

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                HStack {
                    Menu {
                        ForEach(DateRange.allCases) { option in
                            Button {
                                range = option
                            } label: {
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
                    Button {
                        ascending.toggle()
                    } label: {
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
                    ReviewRow(review: review)
                        .tag(review)
                }
                .scrollContentBackground(.hidden)
                .background(KSSTheme.canvas)
            }
            .frame(width: 300)

            Divider().overlay(KSSTheme.hairline)

            Group {
                if let selectedReview {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .firstTextBaseline) {
                            PageTitle(selectedReview.title)
                            Spacer()
                            StatusBadge(icon: "calendar", text: selectedReview.date, tint: KSSTheme.accent)
                        }
                        if !selectedReview.focusSymbols.isEmpty {
                            Text("关注 " + selectedReview.focusSymbols.joined(separator: "  "))
                                .font(.system(size: 12.5, weight: .medium, design: .monospaced))
                                .foregroundStyle(KSSTheme.textSecondary)
                                .lineLimit(2)
                        }
                        if isLoadingDetail && selectedPath == selectedReview.path {
                            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else if detail?.path == selectedReview.path, let detail {
                            MarkdownWebView(text: detail.text)
                                .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                                .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
                        } else {
                            MarkdownWebView(text: selectedReview.excerpt)
                                .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
                                .overlay(RoundedRectangle(cornerRadius: KSSTheme.cardRadius).stroke(KSSTheme.hairline))
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                } else {
                    Text("选择一篇复盘查看全文")
                        .font(.system(size: 14))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(KSSTheme.canvas)
        }
        .background(KSSTheme.canvas)
        .onAppear {
            if selectedReview == nil {
                selectedReview = visibleReviews.first
            }
            if let selectedReview, detail?.path != selectedReview.path {
                onSelectReview(selectedReview.path)
            }
        }
        .onChange(of: selectedReview) { review in
            if let review {
                onSelectReview(review.path)
            }
        }
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
