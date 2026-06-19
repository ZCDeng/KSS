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

/// 边栏顶部：app 图标 + 名称 + 系统说明。
struct AppHeader: View {
    var body: some View {
        HStack(spacing: 10) {
            logo
                .frame(width: 34, height: 34)
            VStack(alignment: .leading, spacing: 1) {
                Text("KSS 工作台")
                    .font(KSSFont.serif(16, .semibold))
                    .foregroundStyle(KSSTheme.textPrimary)
                Text("科创 · 创业 · 北证 量化选股")
                    .font(.system(size: 10.5))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .lineLimit(1)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(KSSTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(KSSTheme.hairline))
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

/// 边栏底部：GitHub 链接 + 架构说明。
struct SidebarFooter: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let url = URL(string: "https://github.com/ZCDeng/KSS") {
                Link(destination: url) {
                    HStack(spacing: 6) {
                        Image(systemName: "chevron.left.forwardslash.chevron.right")
                            .font(.system(size: 11, weight: .semibold))
                        Text("GitHub · ZCDeng/KSS")
                            .font(.system(size: 12, weight: .semibold))
                        Spacer()
                        Image(systemName: "arrow.up.forward")
                            .font(.system(size: 10, weight: .semibold))
                    }
                    .foregroundStyle(KSSTheme.textPrimary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(KSSTheme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(KSSTheme.hairline))
                }
                .buttonStyle(.plain)
            }
            Text("「架构」页可查看交互版系统架构图")
                .font(.system(size: 10))
                .foregroundStyle(KSSTheme.textSecondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Compact status block: tight icon rows instead of the loose default
/// LabeledContent spacing.
struct StatusCard: View {
    var snapshot: AppSnapshot
    var watchlistCount: Int

    var body: some View {
        VStack(spacing: 0) {
            row("chart.bar.fill", "股票数", "\(snapshot.stockCount)")
            Divider().overlay(KSSTheme.hairline)
            row("calendar", "数据日期", snapshot.latestDataDate ?? "-")
            Divider().overlay(KSSTheme.hairline)
            row("target", "最新推荐", snapshot.recommendationDate ?? "-")
            Divider().overlay(KSSTheme.hairline)
            row("star.fill", "自选数", "\(watchlistCount)")
        }
        .padding(.vertical, 4)
        .background(KSSTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(KSSTheme.hairline))
    }

    private func row(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(KSSTheme.accent)
                .frame(width: 16)
            Text(label)
                .font(.system(size: 12.5, weight: .medium))
                .foregroundStyle(KSSTheme.textSecondary)
            Spacer()
            Text(value)
                .font(.system(size: 12.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(KSSTheme.textPrimary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
    }
}
