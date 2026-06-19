import SwiftUI

struct SidebarView: View {
    @Binding var selection: WorkspaceSection
    var snapshot: AppSnapshot?
    var watchlist: [String]

    var body: some View {
        VStack(spacing: 0) {
            List(selection: $selection) {
                ForEach(WorkspaceSection.allCases) { section in
                    Label(section.displayName, systemImage: section.symbol)
                        .font(.system(size: 15, weight: .semibold))
                        .tag(section)
                }
            }
            .listStyle(.sidebar)

            if let snapshot {
                StatusCard(snapshot: snapshot, watchlistCount: watchlist.count)
                    .padding(10)
            }
        }
        .navigationTitle("KSS")
    }
}

/// Compact status block: tight icon rows instead of the loose default
/// LabeledContent spacing.
struct StatusCard: View {
    var snapshot: AppSnapshot
    var watchlistCount: Int

    var body: some View {
        VStack(spacing: 0) {
            row("chart.bar.fill", "股票数", "\(snapshot.stockCount)")
            Divider().overlay(KSSTheme.hairline)
            row("calendar", "数据日期", snapshot.latestDataDate ?? "-")
            Divider().overlay(KSSTheme.hairline)
            row("target", "最新推荐", snapshot.recommendationDate ?? "-")
            Divider().overlay(KSSTheme.hairline)
            row("star.fill", "自选数", "\(watchlistCount)")
        }
        .padding(.vertical, 4)
        .background(KSSTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(KSSTheme.hairline))
    }

    private func row(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(KSSTheme.accent)
                .frame(width: 16)
            Text(label)
                .font(.system(size: 12.5, weight: .medium))
                .foregroundStyle(KSSTheme.textSecondary)
            Spacer()
            Text(value)
                .font(.system(size: 12.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textPrimary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
    }
}
