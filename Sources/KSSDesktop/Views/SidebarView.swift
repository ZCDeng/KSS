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

    @State private var dragging: WorkspaceSection?
    /// xcom 模式 hover 反馈：展开态与折叠态共用同一份状态。
    @State private var hoveredSection: WorkspaceSection?

    var body: some View {
        VStack(spacing: 0) {
            AppHeader(collapsed: collapsed, onToggleCollapse: onToggleCollapse)
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.top, 12)
                .padding(.bottom, 8)

            if collapsed {
                collapsedNav
            } else {
                expandedNav
            }
            seesawCTA
                .padding(.horizontal, collapsed ? 8 : 8)
                .padding(.top, 8)
            Spacer(minLength: 0)

            SidebarFooter(collapsed: collapsed)
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.bottom, 10)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.canvas)   // 实色暖纸底，覆盖窗口 vibrancy
        // 展开/折叠切换时视图树整体替换，SwiftUI 不保证旧视图的 onHover(false) 会触发，显式清空避免残留高亮。
        .onChange(of: collapsed) { _, _ in hoveredSection = nil }
    }

    private var pinnedSection: WorkspaceSection? {
        sections.first { WorkspaceSection.pinned.contains($0) }
    }

    private var reorderableSections: [WorkspaceSection] {
        sections.filter { !WorkspaceSection.pinned.contains($0) }
    }

    /// xcom 模式下 hover 中性灰胶囊/圆形反馈色：取自 `theme.textPrimary`（对应 ThemeCatalog 的 `ink`），
    /// 不进 ThemeCatalog 单独开字段，dark/light 按 R2 的目标透明度取值。
    private var hoverTint: Color {
        theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.06)
    }

    /// 展开态：图标 + 文字，选中态铺 clay、图标统一 clay。
    /// 总览（pinned）固定悉顶（ScrollView 外）；其余可拖拽重排（ScrollView 内）。
    private var expandedNav: some View {
        VStack(spacing: 0) {
            if let pinned = pinnedSection {
                navRow(pinned)
                    .padding(.horizontal, 8)
                    .padding(.top, 4)
            }
            ScrollView {
                VStack(spacing: 3) {
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
            }
        }
    }

    /// 选中态渲染分两支：经典模式沿用背景色块；xcom 模式不铺背景，改用图标填充 + 加粗 label
    /// 表达选中(x.com「选中项图标填充变化」规范)。hover 是额外叠加的中性灰胶囊，与选中态互不覆盖。
    private func navRow(_ section: WorkspaceSection) -> some View {
        let isOn = selection == section
        let isXcom = theme.system == .xcom
        let isHovered = isXcom && hoveredSection == section && dragging != section
        return Button { selection = section } label: {
            HStack(spacing: isXcom ? 18 : 11) {
                Image(systemName: section.symbol)
                    .symbolVariant(isXcom && isOn ? .fill : .none)
                    .font(KSSFont.themed(isXcom ? 16 : 15, .semibold, chirpWeight: isOn ? .semibold : .medium, theme: theme))
                    .frame(width: isXcom ? 24 : 22)
                    .foregroundStyle(isXcom
                        ? (isOn ? theme.accent : theme.textSecondary)
                        : (isOn ? theme.onAccent : theme.accent))
                Text(section.displayName)
                    .font(KSSFont.themed(isXcom ? 16 : 15, isXcom && isOn ? .bold : .semibold, chirpWeight: isOn ? .semibold : .medium, theme: theme))
                    .foregroundStyle(isXcom
                        ? (isOn ? theme.textPrimary : theme.textBody)
                        : (isOn ? theme.onAccent : theme.textBody))
                Spacer(minLength: 0)
            }
            .padding(.horizontal, isXcom ? 12 : 10)
            .padding(.vertical, isXcom ? 14 : 9)
            .background(
                isHovered ? hoverTint : ((!isXcom && isOn) ? theme.accent : Color.clear),
                in: RoundedRectangle(cornerRadius: isXcom ? theme.chipRadius : KSSTheme.shapeS)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            hoveredSection = hovering ? section : (hoveredSection == section ? nil : hoveredSection)
        }
    }

    /// 折叠态：仅图标导航，跟随同一顺序（无拖拽——折叠态本来就不支持拖拽重排）。
    /// 总览（pinned）固定悉顶（ScrollView 外）；其余项在 ScrollView 内。选中态视觉分支同 `navRow`。
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
        return Button { selection = section } label: {
            Image(systemName: section.symbol)
                .symbolVariant(isXcom && isOn ? .fill : .none)
                .font(KSSFont.themed(isXcom ? 18 : 17, .semibold, chirpWeight: isOn ? .semibold : .medium, theme: theme))
                .frame(width: 46, height: isXcom ? 44 : 38)
                .foregroundStyle(isXcom
                    ? (isOn ? theme.accent : theme.textSecondary)
                    : (isOn ? theme.onAccent : theme.accent))
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

    /// Seesaw 是全应用唯一的 AI 入口，比照 x.com 侧边栏的「Post」按钮做成常驻强调色大按钮，
    /// 不再是工具栏里跟其他图标同款的小按钮——展开态图标+文字居中，折叠态收成圆形图标按钮。
    /// 点击行为跟其余导航项一致：直接切主内容区，不弹窗、不开新窗口。
    private var seesawCTA: some View {
        Button { selection = .aiChat } label: {
            if collapsed {
                Image(systemName: WorkspaceSection.aiChat.symbol)
                    .font(KSSFont.themed(17, .semibold, chirpWeight: .semibold, theme: theme))
                    .foregroundStyle(theme.onAccent)
                    .frame(width: 46, height: 44)
                    .background(theme.accent, in: Circle())
            } else {
                HStack(spacing: 8) {
                    Image(systemName: WorkspaceSection.aiChat.symbol)
                        .font(KSSFont.themed(15, .semibold, chirpWeight: .semibold, theme: theme))
                    Text(WorkspaceSection.aiChat.displayName)
                        .font(KSSFont.themed(15, .bold, chirpWeight: .bold, theme: theme))
                }
                .foregroundStyle(theme.onAccent)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 11)
                .background(theme.accent, in: Capsule())
            }
        }
        .buttonStyle(.plain)
        .help(WorkspaceSection.aiChat.displayName)
    }
}

/// 拖拽重排落点：drop 到某行 = 把被拖项移到该行之前。
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

/// 边栏顶部：KSSDeck 锁定式标志 + 折叠/展开按钮。折叠态只留 K 标。
struct AppHeader: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool
    var onToggleCollapse: () -> Void

    var body: some View {
        if collapsed {
            VStack(spacing: 10) {
                toggleButton
                kmark.frame(width: 30, height: 30)
            }
            .frame(maxWidth: .infinity)
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

/// 页头折叠/展开按钮：xcom 模式下补圆形 hover 反馈,与 U4 折叠态图标的圆形 hover 惯例一致。
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
                    isXcom && isHovering ? theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.06) : Color.clear,
                    in: Circle()
                )
        }
        .buttonStyle(.plain)
        .help(collapsed ? "展开边栏" : "折叠边栏")
        .onHover { isHovering = $0 }
    }
}

/// 边栏底部：只保留 GitHub 跳转（折叠态仅图标）。xcom 模式下补胶囊形 hover 反馈。
struct SidebarFooter: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool
    @State private var isHovering = false

    var body: some View {
        if let url = URL(string: "https://github.com/ZCDeng/KSS") {
            let isXcom = theme.system == .xcom
            let hoverTint = theme.textPrimary.opacity(theme.appearance == .dark ? 0.10 : 0.06)
            Link(destination: url) {
                if collapsed {
                    Image(systemName: "chevron.left.forwardslash.chevron.right")
                        .font(KSSFont.themed(14, .semibold, chirpWeight: .medium, theme: theme))
                        .foregroundStyle(theme.accent)
                        .frame(width: 28, height: 28)
                        .background(
                            isXcom && isHovering ? hoverTint : Color.clear,
                            in: Circle()
                        )
                        .frame(maxWidth: .infinity, minHeight: 28)
                } else {
                    HStack(spacing: 8) {
                        Image(systemName: "chevron.left.forwardslash.chevron.right")
                            .font(KSSFont.themed(11, .semibold, chirpWeight: .medium, theme: theme))
                            .foregroundStyle(theme.accent)
                            .frame(width: 15)
                        Text("GitHub · ZCDeng/KSS")
                            .font(KSSFont.themed(12, .semibold, chirpWeight: .medium, theme: theme))
                            .foregroundStyle(theme.textBody)
                        Spacer()
                        Image(systemName: "arrow.up.forward")
                            .font(KSSFont.themed(9, .semibold, chirpWeight: .medium, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, isXcom ? 6 : 0)
                    .background(
                        isXcom && isHovering ? hoverTint : Color.clear,
                        in: RoundedRectangle(cornerRadius: theme.chipRadius)
                    )
                }
            }
            .buttonStyle(.plain)
            .help("GitHub · ZCDeng/KSS")
            .onHover { isHovering = $0 }
        }
    }
}
