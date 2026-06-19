import SwiftUI

struct SidebarView: View {
    @Binding var selection: WorkspaceSection
    var snapshot: AppSnapshot?
    var watchlist: [String]

    var body: some View {
        List(selection: $selection) {
            Section {
                ForEach(WorkspaceSection.allCases) { section in
                    Label(section.rawValue, systemImage: section.symbol)
                        .tag(section)
                }
            }
            if let snapshot {
                Section("Status") {
                    LabeledContent("Stocks", value: "\(snapshot.stockCount)")
                    LabeledContent("Latest", value: snapshot.latestDataDate ?? "-")
                    LabeledContent("Picks", value: snapshot.recommendationDate ?? "-")
                    LabeledContent("Watchlist", value: "\(watchlist.count)")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("KSS")
    }
}
