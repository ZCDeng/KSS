import SwiftUI

struct SidebarView: View {
    @Binding var selection: WorkspaceSection
    var snapshot: AppSnapshot?
    var watchlist: [String]

    var body: some View {
        List(selection: $selection) {
            Section {
                ForEach(WorkspaceSection.allCases) { section in
                    Label(section.displayName, systemImage: section.symbol)
                        .font(.system(size: 15, weight: .semibold))
                        .tag(section)
                }
            }
            if let snapshot {
                Section("状态") {
                    LabeledContent("股票数", value: "\(snapshot.stockCount)")
                    LabeledContent("数据日期", value: snapshot.latestDataDate ?? "-")
                    LabeledContent("最新推荐", value: snapshot.recommendationDate ?? "-")
                    LabeledContent("自选数", value: "\(watchlist.count)")
                }
                .font(.system(size: 12.5))
                .foregroundStyle(KSSTheme.textSecondary)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("KSS")
    }
}
