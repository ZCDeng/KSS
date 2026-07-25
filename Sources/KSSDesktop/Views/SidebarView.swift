import SwiftUI
import UniformTypeIdentifiers

struct SidebarView: View {
    @Environment(\.kssTheme) private var theme
    @Binding var selection: WorkspaceSection
    var collapsed: Bool
    /// 用户自定义顺序（总览置顶）。由 ContentView 持有 @AppStorage 并解析后传入。
    var sections: [WorkspaceSection]
    var onToggleCollapse: () -> Void
    /// 把 dragged 拖到 target 之前，由 ContentView 持久化。
    var onReorder: (_ dragged: WorkspaceSection, _ target: WorkspaceSection) -> Void
    /// 导航角标映射（自检 / 推荐等）；无信号时为空。
    var badges: [WorkspaceSection: SidebarNavBadge] = [:]

    @State private var dragging: WorkspaceSection?
    /// xcom 模式 hover 反馈：展开态与折叠态共用同一份状态。
    @State private var hoveredSection: WorkspaceSection?

    var body: some View {
        VStack(spacing: 0) {
            AppHeader(collapsed: collapsed, onToggleCollapse: onToggleCollapse)
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.top, 12)
                .padding(.bottom, 8)

            Group {
                if collapsed {
                    collapsedNav
                } else {
                    expandedNav
                }
            }
            .frame(maxHeight: .infinity)

            seesawCTA
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.top, 14)
                .padding(.bottom, 12)

            SidebarAccountRow(
                collapsed: collapsed,
                isArchitectureSelected: selection == .architecture,
                onSelectArchitecture: { selection = .architecture },
                onToggleCollapse: onToggleCollapse
            )
            .padding(.horizontal, collapsed ? 8 : 8)
            .padding(.bottom, 10)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.canvas)
        .onChange(of: collapsed) { _, _ in hoveredSection = nil }
    }

    private var pinnedSection: WorkspaceSection? {
        sections.first { WorkspaceSection.pinned.contains($0) }
    }

    private var reorderableSections: [WorkspaceSection] {
        sections.filter { !WorkspaceSection.pinned.contains($0) }
    }

    /// xcom hover：ink 叠加；light 略提到 0.08 贴近手感。
    private var hoverTint: Color {
        theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.08)
    }

    private var expandedNav: some View {
        VStack(spacing: 0) {
            if let pinned = pinnedSection {
                navRow(pinned)
                    .padding(.horizontal, 8)
                    .padding(.top, 4)
            }
            ScrollView {
                VStack(spacing: 5) {
                    ForEach(reorderableSections) { section in
                        navRow(section)
                            .opacity(dragging == section ? 0.4 : 1)
                            .onDrag {
                                dragging = section
                                return NSItemProvider(object: section.rawValue as NSString)
                            }
                            .onDrop(
                                of: [UTType.text],
                                delegate: SectionDropDelegate(
                                    target: section,
                                    dragging: $dragging,
                                    onReorder: onReorder
                                )
                            )
                    }
                }
                .padding(.horizontal, 8)
                .padding(.top, 3)
                .padding(.bottom, 4)
            }
        }
    }

    private func navRow(_ section: WorkspaceSection) -> some View {
        let isOn = selection == section
        let isXcom = theme.system == .xcom
        let isHovered = isXcom && hoveredSection == section && dragging != section
        let badge = badges[section]

        let icon = SidebarSectionIcon(
            section: section,
            filled: isOn,
            pointSize: isXcom ? 22 : 16,
            frameWidth: isXcom ? 26 : 22,
            fontWeight: isXcom ? (isOn ? .heavy : .regular) : .semibold,
            chirpWeight: isOn ? .heavy : .regular,
            theme: theme
        )
        .foregroundStyle(isXcom
            ? theme.textPrimary
            : (isOn ? theme.onAccent : theme.accent))
        .overlay(alignment: .topTrailing) {
            if let badge {
                SidebarBadgeView(badge: badge, theme: theme)
                    .offset(x: 6, y: -6)
            }
        }

        let label = Text(section.displayName)
            .font(KSSFont.themed(
                isXcom ? 18 : 15,
                isXcom ? (isOn ? .bold : .regular) : (isOn ? .semibold : .semibold),
                chirpWeight: isOn ? .heavy : .regular,
                theme: theme
            ))
            .foregroundStyle(isXcom
                ? theme.textPrimary
                : (isOn ? theme.onAccent : theme.textBody))

        return Button { selection = section } label: {
            if isXcom {
                HStack(spacing: 0) {
                    HStack(spacing: 20) {
                        icon
                        label
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 12)
                    .background(isHovered ? hoverTint : Color.clear, in: RoundedRectangle(cornerRadius: theme.chipRadius))
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            } else {
                HStack(spacing: 11) {
                    icon
                    label
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 9)
                .background(isOn ? theme.accent : Color.clear, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                .contentShape(Rectangle())
            }
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            hoveredSection = hovering ? section : (hoveredSection == section ? nil : hoveredSection)
        }
    }

    private var collapsedNav: some View {
        VStack(spacing: 0) {
            if let pinned = pinnedSection {
                collapsedRow(pinned)
                    .padding(.top, 4)
            }
            ScrollView {
                VStack(spacing: 4) {
                    ForEach(reorderableSections) { section in
                        collapsedRow(section)
                    }
                }
                .padding(.top, 4)
            }
        }
    }

    private func collapsedRow(_ section: WorkspaceSection) -> some View {
        let isOn = selection == section
        let isXcom = theme.system == .xcom
        let isHovered = isXcom && hoveredSection == section
        let badge = badges[section]
        let hit: CGFloat = isXcom ? 50 : 38
        return Button { selection = section } label: {
            SidebarSectionIcon(
                section: section,
                filled: isOn,
                pointSize: isXcom ? 22 : 18,
                frameWidth: isXcom ? 50 : 46,
                fontWeight: isXcom ? (isOn ? .heavy : .regular) : .semibold,
                chirpWeight: isOn ? .heavy : .regular,
                theme: theme
            )
            .frame(width: isXcom ? 50 : 46, height: hit)
            .foregroundStyle(isXcom
                ? theme.textPrimary
                : (isOn ? theme.onAccent : theme.accent))
            .overlay(alignment: .topTrailing) {
                if let badge {
                    SidebarBadgeView(badge: badge, theme: theme, compact: true)
                        .offset(x: -4, y: 4)
                }
            }
            .background(
                isHovered ? hoverTint : ((!isXcom && isOn) ? theme.accent : Color.clear),
                in: isXcom ? AnyShape(Circle()) : AnyShape(RoundedRectangle(cornerRadius: KSSTheme.shapeS))
            )
        }
        .buttonStyle(.plain)
        .help(section.displayName)
        .onHover { hovering in
            hoveredSection = hovering ? section : (hoveredSection == section ? nil : hoveredSection)
        }
    }

    /// Seesaw：xcom 展开对标 Paper Post（ink 底 + 对比前景、≥52 高、约 90% 宽）；
    /// dark 用浅 ink 底 + 近黑字；经典仍 accent/onAccent。
    /// 折叠态走 `SeesawCollapsedCTA`（outline + 跷跷板/呼吸动效）——侧栏唯一 AI 入口的在场提示。
    private var seesawCTA: some View {
        let isXcom = theme.system == .xcom
        let isDark = theme.appearance == .dark
        let fillColor: Color = isXcom ? theme.textPrimary : theme.accent
        let postForeground: Color = {
            if !isXcom { return theme.onAccent }
            return isDark ? Color.black.opacity(0.92) : Color.white
        }()

        return Button { selection = .aiChat } label: {
            if collapsed {
                SeesawCollapsedCTA(
                    fillColor: fillColor,
                    foreground: postForeground,
                    theme: theme
                )
            } else {
                HStack(spacing: 8) {
                    SidebarSectionIcon(
                        section: .aiChat,
                        filled: true,
                        pointSize: 18,
                        theme: theme
                    )
                    Text(WorkspaceSection.aiChat.displayName)
                        .font(KSSFont.themed(17, .bold, chirpWeight: .bold, theme: theme))
                }
                .foregroundStyle(postForeground)
                .frame(maxWidth: .infinity)
                .frame(minHeight: 52)
                .background(fillColor, in: Capsule())
                .padding(.horizontal, isXcom ? 8 : 0)
            }
        }
        .buttonStyle(.plain)
        .help(WorkspaceSection.aiChat.displayName)
    }
}

// MARK: - Seesaw 折叠态 CTA（AI 在场动效）

/// 折叠边栏里的 Seesaw 圆钮：outline 图标 + 连续「跷跷板」轻摇 + 外圈呼吸脉冲。
/// 用 `TimelineView` 驱动，不依赖状态机跳变；尊重「减少动态效果」。
private struct SeesawCollapsedCTA: View {
    let fillColor: Color
    let foreground: Color
    let theme: KSSThemeTokens
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let size: CGFloat = 50
    private let iconPoint: CGFloat = 28

    var body: some View {
        if reduceMotion {
            staticMark
        } else {
            TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: false)) { context in
                let t = context.date.timeIntervalSinceReferenceDate
                // 跷跷板：~1.7s 半周期，±11°；相位独立于呼吸
                let rock = sin(t * (2 * .pi / 1.7)) * 11
                // 呼吸环：~2.4s 一圈，scale 1→1.22（64pt 折叠栏内不顶破）
                let phase = (t.truncatingRemainder(dividingBy: 2.4)) / 2.4
                let ringScale = 1.0 + 0.22 * phase
                let ringOpacity = 0.5 * (1.0 - phase)
                // 圆钮本体微幅呼吸（1.0↔1.04）
                let bodyPulse = 1.0 + 0.03 * sin(t * (2 * .pi / 2.4))

                ZStack {
                    Circle()
                        .stroke(fillColor.opacity(0.55), lineWidth: 1.5)
                        .frame(width: size, height: size)
                        .scaleEffect(ringScale)
                        .opacity(ringOpacity)

                    Circle()
                        .fill(fillColor)
                        .frame(width: size, height: size)
                        .scaleEffect(bodyPulse)

                    SidebarSectionIcon(
                        section: .aiChat,
                        filled: false,
                        pointSize: iconPoint,
                        frameWidth: size,
                        theme: theme
                    )
                    .foregroundStyle(foreground)
                    .rotationEffect(.degrees(rock))
                }
                .frame(width: size, height: size)
            }
        }
    }

    private var staticMark: some View {
        SidebarSectionIcon(
            section: .aiChat,
            filled: false,
            pointSize: iconPoint,
            frameWidth: size,
            theme: theme
        )
        .foregroundStyle(foreground)
        .frame(width: size, height: size)
        .background(fillColor, in: Circle())
    }
}

// MARK: - Badge chrome

private struct SidebarBadgeView: View {
    let badge: SidebarNavBadge
    let theme: KSSThemeTokens
    var compact: Bool = false

    var body: some View {
        switch badge {
        case .dot:
            Circle()
                .fill(theme.accent)
                .frame(width: compact ? 7 : 8, height: compact ? 7 : 8)
        case .count(let n):
            let text = n > 99 ? "99+" : "\(max(0, n))"
            Text(text)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(.white)
                .padding(.horizontal, 5)
                .frame(minWidth: 18, minHeight: 18)
                .background(theme.accent, in: Capsule())
                .overlay(Capsule().strokeBorder(Color.white, lineWidth: 1))
        }
    }
}

// MARK: - Drag reorder

private struct SectionDropDelegate: DropDelegate {
    let target: WorkspaceSection
    @Binding var dragging: WorkspaceSection?
    let onReorder: (_ dragged: WorkspaceSection, _ target: WorkspaceSection) -> Void

    func dropEntered(info: DropInfo) {
        guard let dragged = dragging, dragged != target else { return }
        onReorder(dragged, target)
    }

    func performDrop(info: DropInfo) -> Bool {
        dragging = nil
        return true
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        DropProposal(operation: .move)
    }
}

// MARK: - Header

/// 边栏顶部：品牌 kmark 仅作标识（不当头像）。
/// 折叠/展开主入口在底栏 `SidebarAccountRow` 的显式按钮，避免 xcom 顶栏塞工具钮。
struct AppHeader: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool
    var onToggleCollapse: () -> Void

    var body: some View {
        let isXcom = theme.system == .xcom
        if collapsed {
            // 折叠态：只留品牌 K；展开入口在底栏（sidebar.leading 大钮）
            kmarkButton
                .frame(maxWidth: .infinity)
        } else if isXcom {
            HStack(alignment: .center, spacing: 6) {
                kmarkButton
                Spacer(minLength: 0)
                // xcom 展开：顶栏次要折叠入口（主入口仍是底栏 ⋯）
                ToggleButton(theme: theme, action: onToggleCollapse, collapsed: collapsed, size: 28)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
        } else {
            HStack(alignment: .center, spacing: 6) {
                kmark.frame(height: 26)
                wordmark.frame(height: 20)
                Spacer(minLength: 0)
                ToggleButton(theme: theme, action: onToggleCollapse, collapsed: collapsed, size: 26)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
        }
    }

    private var kmarkButton: some View {
        let isXcom = theme.system == .xcom
        return kmark
            .frame(width: isXcom ? 30 : 26, height: isXcom ? 30 : 26)
            .frame(width: isXcom ? 50 : 30, height: isXcom ? 50 : 30)
            .contentShape(Circle())
    }

    @ViewBuilder private var wordmark: some View {
        if let img = bundledImage("wordmark") {
            Image(nsImage: img)
                .resizable()
                .renderingMode(.template)
                .scaledToFit()
                .foregroundStyle(theme.textPrimary)
        } else {
            Text("KSSDeck")
                .font(KSSFont.themed(18, .heavy, chirpWeight: .medium, theme: theme))
                .foregroundStyle(theme.textPrimary)
        }
    }

    @ViewBuilder private var kmark: some View {
        if let img = bundledImage("kmark") ?? bundledImage("logo") {
            Image(nsImage: img).resizable().scaledToFit()
        } else {
            Image(systemName: "k.square.fill").resizable().scaledToFit().foregroundStyle(theme.up)
        }
    }

    private func bundledImage(_ name: String) -> NSImage? {
        guard let url = KSSResources.bundle.url(forResource: name, withExtension: "png") else { return nil }
        return NSImage(contentsOf: url)
    }
}

/// 折叠/展开边栏：普通 `Button`（非 Menu），圆形 hover；折叠态用更大 hit。
private struct ToggleButton: View {
    let theme: KSSThemeTokens
    let action: () -> Void
    let collapsed: Bool
    var size: CGFloat = 26
    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: "sidebar.leading")
                .font(KSSFont.themed(size >= 36 ? 17 : 13, .semibold, chirpWeight: .medium, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(width: size, height: size)
                .background(
                    isHovering ? theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.08) : Color.clear,
                    in: Circle()
                )
        }
        .buttonStyle(.plain)
        .help(collapsed ? "展开边栏" : "折叠边栏")
        .accessibilityLabel(collapsed ? "展开边栏" : "折叠边栏")
        .onHover { isHovering = $0 }
    }
}

// MARK: - Account footer

/// 底栏：SF Symbol 用户头像 + 明确折叠/展开入口。
/// - 折叠：上 = 展开大钮（sidebar.leading），下 = 用户 Menu（架构 / GitHub，不含折叠）
/// - 展开：用户行 + ⋯（架构 / GitHub / 折叠边栏）
struct SidebarAccountRow: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool
    var isArchitectureSelected: Bool
    var onSelectArchitecture: () -> Void
    var onToggleCollapse: () -> Void
    @State private var isHovering = false
    @State private var moreHovering = false

    private var hoverTint: Color {
        theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.08)
    }

    var body: some View {
        if collapsed {
            VStack(spacing: 8) {
                // 主入口：展开边栏（Button，不用 Menu，避免系统蓝箭头与尺寸失控）
                ToggleButton(theme: theme, action: onToggleCollapse, collapsed: true, size: 40)
                    .frame(maxWidth: .infinity)

                // 次入口：更多（架构 / GitHub）— label 仅用 SF Symbol，禁止 kmark 位图
                Menu {
                    moreMenuItems
                } label: {
                    userAvatar(size: 32)
                        .foregroundStyle(theme.textSecondary)
                        .frame(width: 40, height: 40)
                        .background(moreHovering ? hoverTint : Color.clear, in: Circle())
                }
                .menuStyle(.borderlessButton)
                .buttonStyle(.plain)
                .frame(width: 40, height: 40)
                .frame(maxWidth: .infinity)
                .help("更多 · 架构 / GitHub")
                .onHover { moreHovering = $0 }
            }
            .frame(maxWidth: .infinity)
        } else {
            HStack(spacing: 0) {
                HStack(spacing: 0) {
                    userAvatar(size: 40)
                        .foregroundStyle(theme.textSecondary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("KSS")
                            .font(KSSFont.themed(15, .bold, chirpWeight: .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .lineLimit(1)
                        Text("本地工作台")
                            .font(KSSFont.themed(13, .regular, chirpWeight: .regular, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(1)
                    }
                    .padding(.leading, 12)
                    Spacer(minLength: 4)
                    Menu {
                        moreMenuItems
                        Divider()
                        Button("折叠边栏") { onToggleCollapse() }
                    } label: {
                        Image(systemName: "ellipsis")
                            .font(KSSFont.themed(15, .semibold, chirpWeight: .medium, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .frame(width: 28, height: 28)
                            .contentShape(Rectangle())
                    }
                    .menuStyle(.borderlessButton)
                    .help("更多 · 架构 / GitHub / 折叠")
                }
                .padding(12)
                .background(isHovering ? hoverTint : Color.clear, in: RoundedRectangle(cornerRadius: theme.chipRadius))
                Spacer(minLength: 0)
            }
            .onHover { isHovering = $0 }
        }
    }

    /// 仅架构 + GitHub；折叠/展开不进此菜单（折叠态由独立 Button 负责）。
    @ViewBuilder private var moreMenuItems: some View {
        Button(WorkspaceSection.architecture.displayName) {
            onSelectArchitecture()
        }
        if let url = URL(string: "https://github.com/ZCDeng/KSS") {
            Link("GitHub · ZCDeng/KSS", destination: url)
        }
    }

    /// 固定 SF Symbol 用户头像，永不加载 kmark/logo 位图。
    private func userAvatar(size: CGFloat) -> some View {
        Image(systemName: "person.crop.circle.fill")
            .resizable()
            .scaledToFit()
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
