import SwiftUI
import AppKit

/// U2 资讯雷达独立页面 —— 复刻 Vibe-Research 多赛道 RSS 布局，套 KSSDeck M3 设计规范。
/// 数据由 bridge `intel-radar` 命令提供（12 赛道 108 公开 RSS 源）。
struct IntelView: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var activeTrack: String = "tech"
    @State private var errorMessage: String? = nil

    private var digest: NewsDigestResponse? { store.intelDigest }
    private var tracks: [IntelTrack] { digest?.tracks ?? [] }
    private var hasData: Bool { digest?.available ?? false }

    var body: some View {
        GeometryReader { geo in
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    // ---- 标题 ----
                    HStack(alignment: .firstTextBaseline) {
                        PageTitle("资讯雷达", subtitle: "12 赛道全球 RSS 资讯 · Investment News")
                        Spacer()
                        if hasData {
                            StatusBadge(icon: "antenna.radiowaves.left.and.right",
                                        text: "\(digest?.stats?.totalSources ?? 108) 源", tint: theme.accent)
                        }
                    }

                    // ---- 统计栏 + 刷新 ----
                    statsRefreshRow

                    // ---- 错误横幅 ----
                    if let err = errorMessage {
                        HStack(spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(.system(size: 12))
                            Text(err).font(.system(size: 12.5))
                        }
                        .foregroundStyle(theme.down)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(theme.down.opacity(0.1), in: RoundedRectangle(cornerRadius: theme.chipRadius))
                    }

                    // ---- 赛道 Pills ----
                    if !tracks.isEmpty {
                        trackPills
                    }

                    // ---- 内容区 ----
                    if store.isLoadingIntel {
                        VStack(spacing: 12) {
                            ProgressView()
                            Text("加载资讯…").font(.system(size: 12.5)).foregroundStyle(theme.textSecondary)
                        }
                        .frame(maxWidth: .infinity, minHeight: 200)
                    } else if tracks.isEmpty && !hasData {
                        emptyState
                    } else if let cur = tracks.first(where: { $0.key == activeTrack }) {
                        trackNewsList(cur)
                    }
                }
                .frame(width: min(geo.size.width - 48, 1040))
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden).background(theme.canvas)
        }
        .onAppear { Task { await store.loadIntel() } }
    }

    // MARK: - 统计 + 刷新行

    private var statsRefreshRow: some View {
        HStack(spacing: 10) {
            Text(statLine)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            Spacer()
            Button {
                errorMessage = nil
                Task { await store.refreshIntelRadar() }
            } label: {
                HStack(spacing: 5) {
                    if store.isLoadingIntel {
                        ProgressView().scaleEffect(0.7)
                    } else {
                        Image(systemName: "arrow.clockwise").font(.system(size: 11, weight: .bold))
                    }
                    Text(store.isLoadingIntel ? "抓取中…" : "刷新")
                        .font(.system(size: 12.5, weight: .semibold))
                }
                .foregroundStyle(store.isLoadingIntel ? theme.textSecondary : theme.accent)
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(theme.accent.opacity(0.1), in: RoundedRectangle(cornerRadius: theme.chipRadius))
            }
            .buttonStyle(.plain)
            .disabled(store.isLoadingIntel)
        }
    }

    private var statLine: String {
        guard hasData else {
            return "\(tracks.count) 赛道 · \(digest?.stats?.totalSources ?? 108) 个公开源 · 点刷新拉取"
        }
        let totalItems = tracks.reduce(0) { $0 + ($1.items?.count ?? 0) }
        let days = digest?.recentDays ?? 7
        let updated = digest?.generatedAt ?? "—"
        return "\(tracks.count) 赛道 / \(totalItems) 条资讯 · 近 \(days) 天 · 更新于 \(updated)"
    }

    // MARK: - 赛道 Pills

    private var trackPills: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(tracks, id: \.key) { track in
                    Button { withAnimation(.easeInOut(duration: 0.15)) { activeTrack = track.key } } label: {
                        trackPillLabel(track)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 2)
        }
    }

    private func trackPillLabel(_ track: IntelTrack) -> some View {
        let isActive = track.key == activeTrack
        let pillColor = parseHexColor(track.accent) ?? theme.accent
        return HStack(spacing: 5) {
            Circle().fill(pillColor).frame(width: 7, height: 7)
            Text(track.name)
                .font(.system(size: 12, weight: isActive ? .semibold : .medium))
            Text("\(track.items?.count ?? 0)")
                .font(.system(size: 10.5, weight: .medium, design: .monospaced))
                .foregroundStyle(isActive ? theme.textSecondary : theme.textSecondary.opacity(0.6))
        }
        .foregroundStyle(isActive ? theme.textPrimary : theme.textSecondary)
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(
            isActive
                ? pillColor.opacity(0.12)
                : theme.surfaceContainer,
            in: RoundedRectangle(cornerRadius: theme.chipRadius)
        )
        .overlay(
            RoundedRectangle(cornerRadius: theme.chipRadius)
                .strokeBorder(isActive ? pillColor.opacity(0.35) : theme.outlineVariant, lineWidth: 1)
        )
    }

    // MARK: - 新闻列表

    private func trackNewsList(_ cur: IntelTrack) -> some View {
        let items = cur.items ?? []
        return VStack(alignment: .leading, spacing: 0) {
            // 赛道标题条
            HStack(spacing: 8) {
                let pillColor = parseHexColor(cur.accent) ?? theme.accent
                RoundedRectangle(cornerRadius: 2)
                    .fill(pillColor).frame(width: 4, height: 18)
                Text(cur.name)
                    .font(KSSFont.title(16, .bold, design: theme.titleDesign))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                Text("\(items.count) 条")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            .padding(.bottom, 10)

            if items.isEmpty {
                HStack {
                    Spacer()
                    Text("近 \(digest?.recentDays ?? 7) 天该赛道暂无更新")
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.vertical, 32)
                    Spacer()
                }
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(items.enumerated()), id: \.offset) { idx, item in
                        newsRow(item)
                        if idx < items.count - 1 {
                            Divider().overlay(theme.hairline)
                        }
                    }
                }
                .kssCard(.outlined, padding: 2)
            }
        }
    }

    private func newsRow(_ item: IntelItem) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Group {
                if let time = item.time, !time.isEmpty {
                    Text(time)
                        .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                } else {
                    Text("—")
                        .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(theme.textSecondary.opacity(0.4))
                }
            }
            .frame(width: 72, alignment: .leading)

            Group {
                if let source = item.source, !source.isEmpty {
                    Text(source)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
            }
            .frame(width: 68, alignment: .leading)

            if let urlString = item.url, let url = URL(string: urlString) {
                Link(item.title, destination: url)
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(theme.textSecondary.opacity(0))
                    .opacity(0)
            } else {
                Text(item.title)
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }

    // MARK: - 空态

    private var emptyState: some View {
        VStack(spacing: 10) {
            Text("暂无资讯雷达数据")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(theme.textSecondary)
            Text("点击上方「刷新」拉取 12 赛道 RSS 资讯（约 20–40 秒）")
                .font(.system(size: 12.5))
                .foregroundStyle(theme.textSecondary.opacity(0.6))
            Button("立即拉取") {
                errorMessage = nil
                Task { await store.refreshIntelRadar() }
            }
            .buttonStyle(.plain)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(theme.accent)
            .padding(.horizontal, 16).padding(.vertical, 7)
            .background(theme.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: theme.chipRadius))
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }
}

// MARK: - 合规声明

extension IntelView {
    /// 底部合规声明（可在页面底部加，当前简化为空——完整版由外部调用方渲染）。
    static var disclaimer: some View {
        Text("只做公开信息聚合、不做推荐、不预测涨跌。已按合规词表过滤。")
            .font(.system(size: 10.5))
            .foregroundStyle(.secondary)
    }
}

// MARK: - Hex Color Parsing

/// 解析 `#rrggbb` 十六进制颜色字符串为 SwiftUI `Color`，失败返回 nil。
private func parseHexColor(_ hex: String?) -> Color? {
    guard let hex, hex.hasPrefix("#"), hex.count == 7 else { return nil }
    let r = hex.dropFirst(1).prefix(2)
    let g = hex.dropFirst(3).prefix(2)
    let b = hex.dropFirst(5).prefix(2)
    guard let ri = UInt8(r, radix: 16), let gi = UInt8(g, radix: 16), let bi = UInt8(b, radix: 16) else { return nil }
    return Color(red: Double(ri) / 255, green: Double(gi) / 255, blue: Double(bi) / 255)
}
