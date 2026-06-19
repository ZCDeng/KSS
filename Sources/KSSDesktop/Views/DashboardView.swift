import SwiftUI

struct DashboardView: View {
    var snapshot: AppSnapshot
    var onSelectSymbol: (String) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top, spacing: 12) {
                    StatTile(title: "数据日期", value: snapshot.latestDataDate ?? "-")
                    StatTile(title: "自有股票池", value: "\(snapshot.stockCount)")
                    StatTile(title: "最新推荐", value: snapshot.recommendationDate ?? "-")
                    StatTile(title: "跟踪 Sharpe", value: KSSFormat.number(snapshot.tracking.sharpe), tint: KSSTheme.signColor(snapshot.tracking.sharpe))
                }

                SectionHeader("Daily Recommendations")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                    ForEach(snapshot.recommendations.prefix(6)) { item in
                        RecommendationCard(item: item)
                            .onTapGesture { onSelectSymbol(item.symbol) }
                    }
                }

                SectionHeader("Recent Reviews")
                VStack(spacing: 10) {
                    ForEach(snapshot.reviews.prefix(4)) { review in
                        ReviewRow(review: review)
                    }
                }

                SectionHeader("Backtest Evidence")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 320), spacing: 12)], spacing: 12) {
                    ForEach(snapshot.backtests.prefix(4)) { report in
                        BacktestCard(report: report)
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(KSSTheme.canvas)
        .navigationTitle("KSS Workbench")
    }
}

struct SectionHeader: View {
    var title: String

    init(_ title: String) {
        self.title = title
    }

    var body: some View {
        // Discord section-eyebrow: mono, uppercase, tracked, accent.
        Text(title.uppercased())
            .font(.system(.caption, design: .monospaced).weight(.semibold))
            .tracking(1.2)
            .foregroundStyle(KSSTheme.accent)
            .padding(.top, 6)
    }
}

struct StatTile: View {
    var title: String
    var value: String
    var tint: Color = KSSTheme.textPrimary

    var body: some View {
        // Discord KPI tile: uppercase tracked muted label, display value, optional delta tint.
        VStack(alignment: .leading, spacing: 5) {
            Text(title.uppercased())
                .font(.system(size: 10.5, weight: .medium))
                .tracking(0.6)
                .foregroundStyle(KSSTheme.textSecondary)
            Text(value)
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct RecommendationCard: View {
    var item: Recommendation

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("#\(item.rank)")
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(KSSTheme.accent)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(KSSTheme.accent.opacity(0.15), in: Capsule())
                Spacer()
                Text(item.status)
                    .font(.caption)
                    .foregroundStyle(KSSTheme.textSecondary)
            }
            Text(item.name.isEmpty ? item.symbol : item.name)
                .font(.headline)
                .foregroundStyle(KSSTheme.textPrimary)
                .lineLimit(1)
            Text(item.symbol)
                .font(.subheadline.monospaced())
                .foregroundStyle(KSSTheme.textSecondary)
            HStack {
                LabeledMetric("权重", KSSFormat.percent(item.weight))
                LabeledMetric("跟踪", KSSFormat.percent(item.trackingReturn), tint: KSSTheme.signColor(item.trackingReturn))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }
}

struct LabeledMetric: View {
    var label: String
    var value: String
    var tint: Color

    init(_ label: String, _ value: String, tint: Color = KSSTheme.textPrimary) {
        self.label = label
        self.value = value
        self.tint = tint
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(KSSTheme.textSecondary)
            Text(value)
                .font(.callout.monospacedDigit())
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
