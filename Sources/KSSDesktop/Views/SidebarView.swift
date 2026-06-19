import SwiftUI

struct SidebarView: View {
    @Binding var selection: WorkspaceSection
    var snapshot: AppSnapshot?
    var watchlist: [String]

    var body: some View {
        VStack(spacing: 0) {
            AppHeader()
                .padding(.horizontal, 12)
                .padding(.top, 12)
                .padding(.bottom, 6)

            List(selection: $selection) {
                ForEach(WorkspaceSection.allCases) { section in
                    Label(section.displayName, systemImage: section.symbol)
                        .font(.system(size: 15, weight: .semibold))
                        .tag(section)
                }
            }
            .listStyle(.sidebar)

            if let snapshot {
                StatusCard(snapshot: snapshot, watchlistCount: watchlist.count)
                    .padding(.horizontal, 10)
                    .padding(.bottom, 6)
            }

            SidebarFooter()
                .padding(.horizontal, 12)
                .padding(.bottom, 10)
        }
        .navigationTitle("KSS")
    }
}

/// 边栏顶部：透明 logo + “KSS Desktop”（无卡片边框）。
struct AppHeader: View {
    var body: some View {
        HStack(spacing: 9) {
            logo
                .frame(width: 30, height: 30)
            Text("KSS Desktop")
                .font(KSSFont.serif(18, .semibold))
                .foregroundStyle(KSSTheme.textPrimary)
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
    }

    @ViewBuilder private var logo: some View {
        if let url = Bundle.module.url(forResource: "logo", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            Image(nsImage: image).resizable().scaledToFit()
        } else {
            Image(systemName: "k.square.fill").resizable().scaledToFit().foregroundStyle(KSSTheme.up)
        }
    }
}

/// 边栏底部：GitHub 链接（纯图标+文字，无线框背景）。
struct SidebarFooter: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let url = URL(string: "https://github.com/ZCDeng/KSS") {
                Link(destination: url) {
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
                .buttonStyle(.plain)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 状态信息：纯图标 + 文字，不带线框和背景。
struct StatusCard: View {
    var snapshot: AppSnapshot
    var watchlistCount: Int

    var body: some View {
        VStack(spacing: 7) {
            row("chart.bar.fill", "股票数", "\(snapshot.stockCount)")
            row("calendar", "数据日期", snapshot.latestDataDate ?? "-")
            row("target", "最新推荐", snapshot.recommendationDate ?? "-")
            row("star.fill", "自选数", "\(watchlistCount)")
        }
    }

    private func row(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(KSSTheme.accent)
                .frame(width: 15)
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(KSSTheme.textSecondary)
            Spacer()
            Text(value)
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textBody)
        }
        .padding(.horizontal, 6)
    }
}
