import SwiftUI

/// 工作区列表/Tab 的 x.com 视觉策略（AI复盘、AI回测等 list|detail 页共用）。
/// 与 `IntelXcomChrome` 对齐：仅 `theme.system == .xcom` 启用 timeline 选中与 underline Tab。
enum XcomListChrome {
    static func isXcom(_ system: KSSDesignSystem) -> Bool {
        system == .xcom
    }

    /// 列表选中底：xcom 用 ink 浅叠/ surfaceContainer；经典保留 accent 染色。
    static func listSelectionFill(
        isOn: Bool,
        isHovered: Bool,
        theme: KSSThemeTokens
    ) -> Color {
        if !isXcom(theme.system) {
            return isOn ? theme.accent.opacity(0.16) : Color.clear
        }
        if isOn {
            return theme.appearance == .dark
                ? theme.textPrimary.opacity(0.12)
                : theme.surfaceContainer
        }
        if isHovered {
            let o = theme.appearance == .dark ? 0.10 : 0.07
            return theme.textPrimary.opacity(o)
        }
        return Color.clear
    }

    /// 详情标题字号：xcom 线程感略小。
    static func detailTitlePointSize(_ system: KSSDesignSystem) -> CGFloat {
        isXcom(system) ? 18 : 22
    }

    /// 列表栏宽度（xcom 略宽贴近 timeline）。
    static func listColumnWidth(_ system: KSSDesignSystem) -> CGFloat {
        isXcom(system) ? 320 : 300
    }
}

/// Seesaw Focus Layout 的共享几何。主题只影响 token，不能再改变会话、技能与记忆的
/// 信息架构。
enum SeesawXcomChrome {
    static let feedColumnWidth: CGFloat = 760
    // Legacy branch constants remain while its private helpers are retired. The
    // live `AIChatView` rendering path only uses the Focus Layout metrics below.
    static let sessionRailWidth: CGFloat = 320
    static let utilityPanelWidth: CGFloat = 400
    static let headerHeight: CGFloat = 53
    static let avatarSize: CGFloat = 40
    static let rowHorizontalPadding: CGFloat = 16
    static let rowVerticalPadding: CGFloat = 12
    static let compactContentWidth: CGFloat = 820
    static let overlayWidth: CGFloat = 380
    static let minimumThreeColumnWidth =
        sessionRailWidth + feedColumnWidth + utilityPanelWidth
}

/// xcom 底蓝下划线 Tab；经典调用方仍用 `KSSSegmentedControl`。
struct XcomUnderlineTabBar<Key: Hashable>: View {
    @Environment(\.kssTheme) private var theme
    var options: [(key: Key, label: String)]
    @Binding var selection: Key
    var stretch: Bool = true

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(options.enumerated()), id: \.offset) { _, option in
                let isActive = selection == option.key
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { selection = option.key }
                } label: {
                    VStack(spacing: 0) {
                        Text(option.label)
                            .font(KSSFont.themed(15, isActive ? .bold : .medium, theme: theme))
                            .foregroundStyle(isActive ? theme.textPrimary : theme.textSecondary)
                            .frame(maxWidth: stretch ? .infinity : nil)
                            .padding(.horizontal, stretch ? 8 : 16)
                            .padding(.vertical, 12)
                        Capsule()
                            .fill(isActive ? theme.accent : Color.clear)
                            .frame(height: 4)
                            .padding(.horizontal, 10)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(isActive ? .isSelected : [])
            }
        }
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
        .frame(maxWidth: stretch ? .infinity : nil)
    }
}
