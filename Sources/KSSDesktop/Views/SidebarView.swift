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
            Spacer(minLength: 0)

            SidebarFooter(collapsed: collapsed)
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.bottom, 10)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.canvas)   // 实色暖纸底，覆盖窗口 vibrancy
    }

    /// 展开态：图标 + 文字，选中态铺 clay、图标统一 clay。
    /// 总览（pinned）固定置顶不可拖；其余可拖拽重排。
    private var expandedNav: some View {
        VStack(spacing: 3) {
            ForEach(sections) { section in
                let isPinned = WorkspaceSection.pinned.contains(section)
                navRow(section)
                    .opacity(dragging == section ? 0.4 : 1)
                    .if(!isPinned) { row in
                        row
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
        }
        .padding(.horizontal, 8)
        .padding(.top, 4)
    }

    private func navRow(_ section: WorkspaceSection) -> some View {
        let isOn = selection == section
        return Button { selection = section } label: {
            HStack(spacing: 11) {
                Image(systemName: section.symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 22)
                    .foregroundStyle(isOn ? theme.onAccent : theme.accent)
                Text(section.displayName)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(isOn ? theme.onAccent : theme.textBody)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(
                isOn ? theme.accent : Color.clear,
                in: RoundedRectangle(cornerRadius: KSSTheme.shapeS)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// 折叠态：仅图标导航，跟随同一顺序（无拖拽）。
    private var collapsedNav: some View {
        VStack(spacing: 4) {
            ForEach(sections) { section in
                let isOn = selection == section
                Button { selection = section } label: {
                    Image(systemName: section.symbol)
                        .font(.system(size: 17, weight: .semibold))
                        .frame(width: 46, height: 38)
                        .foregroundStyle(isOn ? theme.onAccent : theme.accent)
                        .background(
                            isOn ? theme.accent : Color.clear,
                            in: RoundedRectangle(cornerRadius: KSSTheme.shapeS)
                        )
                }
                .buttonStyle(.plain)
                .help(section.displayName)
            }
        }
        .padding(.top, 4)
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

/// 条件修饰：仅对可拖项挂 onDrag/onDrop。
extension View {
    @ViewBuilder
    func `if`<Transformed: View>(_ condition: Bool, transform: (Self) -> Transformed) -> some View {
        if condition { transform(self) } else { self }
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
        Button(action: onToggleCollapse) {
            Image(systemName: "sidebar.leading")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 26, height: 26)
        }
        .buttonStyle(.plain)
        .help(collapsed ? "展开边栏" : "折叠边栏")
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
                .font(.system(size: 18, weight: .heavy))
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

/// 边栏底部：只保留 GitHub 跳转（折叠态仅图标）。
struct SidebarFooter: View {
    @Environment(\.kssTheme) private var theme
    var collapsed: Bool

    var body: some View {
        if let url = URL(string: "https://github.com/ZCDeng/KSS") {
            Link(destination: url) {
                if collapsed {
                    Image(systemName: "chevron.left.forwardslash.chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(theme.accent)
                        .frame(maxWidth: .infinity, minHeight: 28)
                } else {
                    HStack(spacing: 8) {
                        Image(systemName: "chevron.left.forwardslash.chevron.right")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(theme.accent)
                            .frame(width: 15)
                        Text("GitHub · ZCDeng/KSS")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(theme.textBody)
                        Spacer()
                        Image(systemName: "arrow.up.forward")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(theme.textSecondary)
                    }
                    .padding(.horizontal, 6)
                }
            }
            .buttonStyle(.plain)
            .help("GitHub · ZCDeng/KSS")
        }
    }
}
