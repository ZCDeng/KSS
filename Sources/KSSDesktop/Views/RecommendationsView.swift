import SwiftUI

enum RecSort: String, CaseIterable, Identifiable {
    case rank = "排名"
    case weight = "权重"
    case tracking = "跟踪收益"
    var id: String { rawValue }
}

struct RecommendationsView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void

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

    var body: some View {
        VStack(spacing: 0) {
            PageTitle("每日推荐", subtitle: snapshot.recommendationDate)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.top, 14)
                .padding(.bottom, 2)
            HStack {
                SortControl(
                    options: RecSort.allCases.map { ($0, $0.rawValue) },
                    selection: $sort,
                    ascending: $ascending
                )
                Spacer()
                Text("\(sortedRecs.count) 只")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)

            List(sortedRecs) { item in
                Button {
                    onSelectSymbol(item.symbol)
                } label: {
                    HStack(spacing: 14) {
                        Text("#\(item.rank)")
                            .font(.system(size: 16, weight: .heavy, design: .monospaced))
                            .foregroundStyle(KSSTheme.accent)
                            .frame(width: 46, alignment: .leading)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.name.isEmpty ? item.symbol : item.name)
                                .font(.system(size: 15.5, weight: .bold))
                                .foregroundStyle(KSSTheme.textPrimary)
                            Text("\(item.symbol) · \(item.industry)")
                                .font(.system(size: 11.5, design: .monospaced))
                                .foregroundStyle(KSSTheme.textSecondary)
                        }
                        Spacer()
                        StatusBadge.tracking(item.status)
                        LabeledMetric("log_mv", KSSFormat.number(item.factorValue, digits: 3))
                        LabeledMetric("权重", KSSFormat.percent(item.weight))
                        LabeledMetric("跟踪", KSSFormat.percent(item.trackingReturn), tint: KSSTheme.signColor(item.trackingReturn))
                    }
                    .contentShape(Rectangle())
                    .padding(.vertical, 2)
                }
                .buttonStyle(.plain)
                .listRowBackground(KSSTheme.surface)
            }
            .scrollContentBackground(.hidden)
            .background(KSSTheme.canvas)
        }
        .navigationTitle("每日推荐 \(snapshot.recommendationDate ?? "")")
    }
}
