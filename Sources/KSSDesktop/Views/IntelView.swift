import SwiftUI

/// U2 资讯雷达独立页面（R1/R4/R5/R16）
struct IntelView: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var expandedTracks: Set<String> = []

    var body: some View {
        GeometryReader { geo in
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    HStack {
                        PageTitle("资讯雷达", subtitle: "多赛道资讯聚合 · 一键 AI 提炼")
                        Spacer()
                        RealtimeFreshnessBadge(
                            quote: store.realtimeQuote,
                            hours: store.tradingHours,
                            authFailed: store.realtimeAuthFailed,
                            updatedAt: store.realtimeUpdatedAt,
                            onRetry: { Task { await store.retryRealtime() } }
                        )
                    }
                    if let digest = store.intelDigest, digest.available,
                       let tracks = digest.tracks {
                        ForEach(tracks, id: \.key) { track in
                            IntelTrackCardView(track: track)
                        }
                    } else {
                        ProgressView("加载资讯…").frame(maxWidth: .infinity)
                    }
                }
                .frame(width: min(geo.size.width - 48, 1040))
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden).background(theme.canvas)
        }
        .onAppear { Task { await store.loadIntel() } }
    }
}

private struct IntelTrackCardView: View {
    @Environment(\.kssTheme) private var theme
    var track: IntelTrack

    var body: some View {
        HStack(spacing: 8) {
            Rectangle().fill(
                Color(red: 0.4, green: 0.4, blue: 0.95)
            ).frame(width: 4, height: 24)
            Text(track.name).font(.system(size: 14, weight: .semibold))
            Spacer()
            Text("\(track.items?.count ?? 0) 条").font(.system(size: 11))
        }
        .padding(12)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: theme.cardRadius))
    }
}
