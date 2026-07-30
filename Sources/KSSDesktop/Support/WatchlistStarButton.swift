import SwiftUI

/// 行尾自选星标：点星 toggle，不参与行导航。
struct WatchlistStarButton: View {
    @Environment(\.kssTheme) private var theme
    var isWatched: Bool
    var disabled: Bool = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: isWatched ? "star.fill" : "star")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(isWatched ? theme.accent : theme.textSecondary)
                .frame(width: 28, height: 28)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .help(isWatched ? "取消自选" : "加为自选")
        .accessibilityLabel(isWatched ? "取消自选" : "加为自选")
        .accessibilityAddTraits(.isButton)
    }
}
