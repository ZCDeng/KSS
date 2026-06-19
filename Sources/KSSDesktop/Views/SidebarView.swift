import SwiftUI

struct SidebarView: View {
    @Binding var selection: WorkspaceSection
    var collapsed: Bool
    var onToggleCollapse: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            AppHeader(collapsed: collapsed, onToggleCollapse: onToggleCollapse)
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.top, 12)
                .padding(.bottom, 8)

            if collapsed {
                collapsedNav
                Spacer(minLength: 0)
            } else {
                List(selection: $selection) {
                    ForEach(WorkspaceSection.allCases) { section in
                        Label(section.displayName, systemImage: section.symbol)
                            .font(.system(size: 15, weight: .semibold))
                            .tag(section)
                    }
                }
                .listStyle(.sidebar)
            }

            SidebarFooter(collapsed: collapsed)
                .padding(.horizontal, collapsed ? 8 : 12)
                .padding(.bottom, 10)
        }
    }

    /// 折叠态：仅图标导航，保留选中高亮。
    private var collapsedNav: some View {
        VStack(spacing: 4) {
            ForEach(WorkspaceSection.allCases) { section in
                Button { selection = section } label: {
                    Image(systemName: section.symbol)
                        .font(.system(size: 17, weight: .semibold))
                        .frame(width: 46, height: 38)
                        .foregroundStyle(selection == section ? Color.white : KSSTheme.textBody)
                        .background(
                            selection == section ? KSSTheme.accent : Color.clear,
                            in: RoundedRectangle(cornerRadius: 9)
                        )
                }
                .buttonStyle(.plain)
                .help(section.displayName)
            }
        }
        .padding(.top, 4)
    }
}

/// 边栏顶部：KSSDeck 锁定式标志 + 折叠/展开按钮。折叠态只留 K 标。
struct AppHeader: View {
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
                .foregroundStyle(KSSTheme.textSecondary)
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
                .foregroundStyle(KSSTheme.textPrimary)
        } else {
            Text("KSSDeck")
                .font(.system(size: 18, weight: .heavy))
                .foregroundStyle(KSSTheme.textPrimary)
        }
    }

    @ViewBuilder private var kmark: some View {
        if let img = bundledImage("kmark") ?? bundledImage("logo") {
            Image(nsImage: img).resizable().scaledToFit()
        } else {
            Image(systemName: "k.square.fill").resizable().scaledToFit().foregroundStyle(KSSTheme.up)
        }
    }

    private func bundledImage(_ name: String) -> NSImage? {
        guard let url = Bundle.module.url(forResource: name, withExtension: "png") else { return nil }
        return NSImage(contentsOf: url)
    }
}

/// 边栏底部：只保留 GitHub 跳转（折叠态仅图标）。
struct SidebarFooter: View {
    var collapsed: Bool

    var body: some View {
        if let url = URL(string: "https://github.com/ZCDeng/KSS") {
            Link(destination: url) {
                if collapsed {
                    Image(systemName: "chevron.left.forwardslash.chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(KSSTheme.accent)
                        .frame(maxWidth: .infinity, minHeight: 28)
                } else {
                    HStack(spacing: 8) {
                        Image(systemName: "chevron.left.forwardslash.chevron.right")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(KSSTheme.accent)
                            .frame(width: 15)
                        Text("GitHub · ZCDeng/KSS")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(KSSTheme.textBody)
                        Spacer()
                        Image(systemName: "arrow.up.forward")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(KSSTheme.textSecondary)
                    }
                    .padding(.horizontal, 6)
                }
            }
            .buttonStyle(.plain)
            .help("GitHub · ZCDeng/KSS")
        }
    }
}
