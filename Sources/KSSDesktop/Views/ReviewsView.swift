import SwiftUI

struct ReviewsView: View {
    var reviews: [DailyReview]
    var selectedPath: String?
    var detail: ReportDetail?
    var isLoadingDetail: Bool
    var onSelectReview: (String) -> Void

    @State private var selectedReview: DailyReview?

    var body: some View {
        NavigationSplitView {
            List(reviews, selection: $selectedReview) { review in
                ReviewRow(review: review)
                    .tag(review)
            }
        } detail: {
            if let selectedReview {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(selectedReview.title)
                            .font(.title2.weight(.semibold))
                        Text(selectedReview.path)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                        if isLoadingDetail && selectedPath == selectedReview.path {
                            ProgressView()
                        } else if detail?.path == selectedReview.path, let detail {
                            ReportTextView(detail: detail)
                        } else {
                            Text(selectedReview.excerpt)
                                .font(.body)
                                .textSelection(.enabled)
                        }
                        if !selectedReview.focusSymbols.isEmpty {
                            Text(selectedReview.focusSymbols.joined(separator: "  "))
                                .font(.callout.monospaced())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .scrollContentBackground(.hidden)
                .background(KSSTheme.canvas)
            } else {
                Text("Select a review")
                    .foregroundStyle(KSSTheme.textSecondary)
            }
        }
        .navigationTitle("Reviews")
        .onAppear {
            if selectedReview == nil {
                selectedReview = reviews.first
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
}

struct ReviewRow: View {
    var review: DailyReview

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(review.date)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            Text(review.title)
                .font(.headline)
                .lineLimit(1)
            Text(review.excerpt)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding(.vertical, 4)
    }
}
