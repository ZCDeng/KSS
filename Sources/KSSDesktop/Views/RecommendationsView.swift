import SwiftUI

struct RecommendationsView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void

    var body: some View {
        List(snapshot.recommendations) { item in
            Button {
                onSelectSymbol(item.symbol)
            } label: {
                HStack(spacing: 14) {
                    Text("#\(item.rank)")
                        .font(.headline.monospacedDigit())
                        .frame(width: 44, alignment: .leading)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.name.isEmpty ? item.symbol : item.name)
                            .font(.headline)
                        Text("\(item.symbol) · \(item.industry)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    LabeledMetric("log_mv", KSSFormat.number(item.factorValue, digits: 3))
                    LabeledMetric("计划权重", KSSFormat.percent(item.weight))
                    LabeledMetric("跟踪收益", KSSFormat.percent(item.trackingReturn), tint: KSSTheme.signColor(item.trackingReturn))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .listRowBackground(KSSTheme.surface)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
        .navigationTitle("Daily Picks \(snapshot.recommendationDate ?? "")")
    }
}
