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

        let icon = Image(systemName: section.symbol)
            .symbolVariant(isXcom && isOn ? .fill : .none)
            .font(KSSFont.themed(isXcom ? 20 : 15, .semibold, chirpWeight: isOn ? .heavy : .regular, theme: theme))
            .fontWeight(isXcom ? (isOn ? .heavy : .regular) : nil)
            .frame(width: isXcom ? 26 : 22)
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
            Image(systemName: section.symbol)
                .symbolVariant(isXcom && isOn ? .fill : .none)
                .font(KSSFont.themed(isXcom ? 20 : 17, .semibold, chirpWeight: isOn ? .heavy : .regular, theme: theme))
                .fontWeight(isXcom ? (isOn ? .heavy : .regular) : nil)
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

    /// Seesaw：xcom 展开对标 Paper Post（ink 底 + 对比前景、≥52 高、约 90% 宽、纯文字）；
    /// dark 用浅 ink 底 + 近黑字；经典仍 accent/onAccent。
    private var seesawCTA: some View {
        let isXcom = theme.system == .xcom
        let isDark = theme.appearance == .dark
        // light：ink 黑底 + 白字；dark：浅 ink 底 + 近黑字；经典：accent/onAccent
        let fillColor: Color = isXcom ? theme.textPrimary : theme.accent
        let postForeground: Color = {
            if !isXcom { return theme.onAccent }
            return isDark ? Color.black.opacity(0.92) : Color.white
        }()

        return Button { selection = .aiChat } label: {
            if collapsed {
                Image(systemName: WorkspaceSection.aiChat.symbol)
                    .font(KSSFont.themed(19, .semibold, chirpWeight: .semibold, theme: theme))
                    .foregroundStyle(postForeground)
                    .frame(width: 50, height: 50)
                    .background(fillColor, in: Circle())
            } else {
                Text(WorkspaceSection.aiChat.displayName)
                    .font(KSSFont.themed(17, .bold, chirpWeight: .bold, theme: theme))
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

/// 边栏顶部：xcom 展开只留 kmark（热区约 50）；经典保留 wordmark + 折叠钮。
struct AppHeader: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool
    var onToggleCollapse: () -> Void

    var body: some View {
        let isXcom = theme.system == .xcom
        if collapsed {
            VStack(spacing: 10) {
                if !isXcom {
                    toggleButton
                }
                kmarkButton
            }
            .frame(maxWidth: .infinity)
        } else if isXcom {
            HStack {
                kmarkButton
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
        } else {
            HStack(alignment: .center, spacing: 6) {
                kmark.frame(height: 26)
                wordmark.frame(height: 20)
                Spacer(minLength: 0)
                toggleButton
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
        }
    }

    private var toggleButton: some View {
        ToggleButton(theme: theme, action: onToggleCollapse, collapsed: collapsed)
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
        guard let url = Bundle.module.url(forResource: name, withExtension: "png") else { return nil }
        return NSImage(contentsOf: url)
    }
}

private struct ToggleButton: View {
    let theme: KSSThemeTokens
    let action: () -> Void
    let collapsed: Bool
    @State private var isHovering = false

    var body: some View {
        let isXcom = theme.system == .xcom
        Button(action: action) {
            Image(systemName: "sidebar.leading")
                .font(KSSFont.themed(13, .semibold, chirpWeight: .medium, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 26, height: 26)
                .background(
                    isXcom && isHovering ? theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.08) : Color.clear,
                    in: Circle()
                )
        }
        .buttonStyle(.plain)
        .help(collapsed ? "展开边栏" : "折叠边栏")
        .onHover { isHovering = $0 }
    }
}

// MARK: - Account footer

/// 账户级底栏：kmark + 标题 + ⋯ 菜单（架构 / GitHub / 折叠）。
struct SidebarAccountRow: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool
    var isArchitectureSelected: Bool
    var onSelectArchitecture: () -> Void
    var onToggleCollapse: () -> Void
    @State private var isHovering = false

    private var hoverTint: Color {
        theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.08)
    }

    var body: some View {
        if collapsed {
            Menu {
                accountMenuItems
            } label: {
                avatar
                    .frame(width: 36, height: 36)
                    .background(isHovering ? hoverTint : Color.clear, in: Circle())
            }
            .menuStyle(.borderlessButton)
            .frame(maxWidth: .infinity)
            .help("账户与更多")
            .onHover { isHovering = $0 }
        } else {
            HStack(spacing: 0) {
                HStack(spacing: 0) {
                    avatar
                        .frame(width: 40, height: 40)
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
                        accountMenuItems
                    } label: {
                        Image(systemName: "ellipsis")
                            .font(KSSFont.themed(15, .semibold, chirpWeight: .medium, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .frame(width: 28, height: 28)
                            .contentShape(Rectangle())
                    }
                    .menuStyle(.borderlessButton)
                }
                .padding(12)
                .background(isHovering ? hoverTint : Color.clear, in: RoundedRectangle(cornerRadius: theme.chipRadius))
                Spacer(minLength: 0)
            }
            .onHover { isHovering = $0 }
        }
    }

    @ViewBuilder private var accountMenuItems: some View {
        Button(WorkspaceSection.architecture.displayName) {
            onSelectArchitecture()
        }
        if let url = URL(string: "https://github.com/ZCDeng/KSS") {
            Link("GitHub · ZCDeng/KSS", destination: url)
        }
        Divider()
        Button(collapsed ? "展开边栏" : "折叠边栏") {
            onToggleCollapse()
        }
    }

    @ViewBuilder private var avatar: some View {
        if let img = bundledImage("kmark") ?? bundledImage("logo") {
            Image(nsImage: img)
                .resizable()
                .scaledToFit()
                .clipShape(Circle())
        } else {
            Image(systemName: "person.crop.circle.fill")
                .resizable()
                .scaledToFit()
                .foregroundStyle(theme.textPrimary)
        }
    }

    private func bundledImage(_ name: String) -> NSImage? {
        guard let url = Bundle.module.url(forResource: name, withExtension: "png") else { return nil }
        return NSImage(contentsOf: url)
    }
}

