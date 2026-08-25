import SwiftUI

/// Full-market A-share cloud. Routed before the dashboard snapshot (plan U5 / KTD3).
struct HeatmapView: View {
    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    var onSelectSymbol: (String) -> Void

    var body: some View {
        main
            .background(theme.canvas)
            .task {
                if store.heatmapSnapshot == nil && !store.heatmapLoading {
                    await store.loadHeatmapSnapshot()
                }
            }
    }

    @ViewBuilder
    private var main: some View {
        if let message = store.heatmapError {
            failureState(message)
        } else if let snapshot = store.heatmapSnapshot {
            if snapshot.tiles.isEmpty {
                emptyState
            } else {
                content(snapshot)
            }
        } else {
            loadingState
        }
    }

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("加载热力图…")
                .font(KSSFont.themed(13, theme: theme))
                .foregroundStyle(theme.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func failureState(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 26))
                .foregroundStyle(theme.ma5)
            Text("当前行情无法显示")
                .font(KSSFont.themed(16, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text(message)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 520)
            Button {
                Task { await store.loadHeatmapSnapshot() }
            } label: {
                Label("重试", systemImage: "arrow.clockwise")
                    .font(KSSFont.themed(13, .semibold, theme: theme))
            }
            .buttonStyle(.bordered)
            .tint(theme.accent)
            .disabled(store.heatmapLoading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "square.grid.3x3")
                .font(.system(size: 26))
                .foregroundStyle(theme.textSecondary)
            Text("当前行情无法显示")
                .font(KSSFont.themed(16, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text("快照已返回但没有可画的成分股，不当作今日行情。")
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    private func content(_ snapshot: HeatmapSnapshot) -> some View {
        ZStack {
            HeatmapWebView(snapshot: snapshot) { message in
                switch message {
                case .selectStock(let symbol):
                    onSelectSymbol(symbol)
                case .refetch(let market, let period):
                    Task { await store.loadHeatmapSnapshot(market: market, period: period) }
                }
            }
            if store.heatmapLoading {
                Color.black.opacity(0.28)
                    .overlay(ProgressView())
                    .allowsHitTesting(true)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
