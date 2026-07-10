import SwiftUI
import AppKit

/// U2 资讯雷达独立页面 —— list|detail 阅读工作台（plan 2026-07-10-001 Layout A）。
/// 数据由 bridge `intel-radar` 命令提供（12 赛道 108 公开 RSS 源）。
struct IntelView: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var activeTrack: String = "tech"
    /// 默认首 Tab：投研改写
    @State private var readerTab: IntelReaderTab = .investment
    /// 全景热点条默认折叠（约 2 行）
    @State private var panoramaExpanded = false
    /// 当前赛道「今日要点」默认折叠，给下方列表/正文腾高
    @State private var digestExpanded = false

    private var digest: NewsDigestResponse? { store.intelDigest }
    private var tracks: [IntelTrack] { digest?.tracks ?? [] }
    private var hasData: Bool { digest?.available ?? false }
    private var currentTrack: IntelTrack? {
        tracks.first(where: { $0.key == activeTrack })
    }
    private var currentItems: [IntelItem] { currentTrack?.items ?? [] }
    private var selectedItem: IntelItem? {
        guard let id = store.selectedIntelItemID else { return nil }
        return currentItems.first(where: { $0.id == id })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // ---- 顶栏 ----
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .firstTextBaseline) {
                    PageTitle("资讯雷达", subtitle: "12 赛道 · 列表阅读 + 投研改写")
                    Spacer()
                    bulkDigestButton
                    if hasData {
                        let totalSources = digest?.stats?.totalSources ?? 108
                        StatusBadge(icon: "antenna.radiowaves.left.and.right",
                                    text: "\(totalSources) 源", tint: theme.accent)
                    }
                }
                if shouldShowBulkSummary { bulkSummaryView }
                statsRefreshRow
                if let err = store.errorMessage {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 12))
                        Text(err).font(.system(size: 12.5)).lineLimit(4)
                    }
                    .foregroundStyle(theme.down)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(theme.down.opacity(0.1), in: RoundedRectangle(cornerRadius: theme.chipRadius))
                }
                // 12 赛道 pill 上方：全景热点
                if hasData || store.intelPanorama != nil || store.intelPanoramaLoading {
                    panoramaBar
                }
                if !tracks.isEmpty { trackPills }
            }
            .padding(.horizontal, 20)
            .padding(.top, 18)
            .padding(.bottom, 12)

            Divider().overlay(theme.hairline)

            // ---- 内容：整行今日要点 → 左列表 | 右正文 ----
            Group {
                if store.isLoadingIntel {
                    loadingState
                } else if tracks.isEmpty && !hasData {
                    emptyState
                } else if let cur = currentTrack {
                    VStack(spacing: 0) {
                        // 全宽今日要点（当前赛道）
                        if !(cur.items ?? []).isEmpty {
                            digestCardView(track: cur, items: cur.items ?? [])
                                .padding(.horizontal, 16)
                                .padding(.top, 12)
                                .padding(.bottom, 8)
                        }
                        Divider().overlay(theme.hairline)
                        HStack(spacing: 0) {
                            trackListColumn(cur)
                                .frame(width: 380)
                            Divider().overlay(theme.hairline)
                            detailPane(track: cur)
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(theme.canvas)
        .onAppear { Task { await store.loadIntel() } }
        .onChange(of: activeTrack) { _, _ in
            digestExpanded = false  // 切赛道收起要点，优先阅读区
            store.selectIntelItem(nil, trackKey: activeTrack, trackName: currentTrack?.name ?? "")
            // 切赛道时尝试拉要点（池优先）
            if let cur = currentTrack, let items = cur.items, !items.isEmpty {
                Task { await store.summarizeIntelTrack(cur.key, name: cur.name, items: items) }
            }
        }
    }

    // MARK: - 加载态

    private var loadingState: some View {
        VStack(spacing: 14) {
            ProgressView().scaleEffect(1.2)
            Text("正在抓取 12 赛道 RSS 资讯…")
                .font(.system(size: 13.5, weight: .medium))
                .foregroundStyle(theme.textSecondary)
            Text("约 20–40 秒，108 个公开源并发获取")
                .font(.system(size: 11.5, design: .monospaced))
                .foregroundStyle(theme.textSecondary.opacity(0.5))
        }
        .frame(maxWidth: .infinity, minHeight: 240)
        .kssCard(.filled, padding: 32)
    }

    // MARK: - 统计 + 刷新行

    private var statsRefreshRow: some View {
        HStack(spacing: 10) {
            Text(statLine)
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            Spacer()
            Button(action: {
                store.errorMessage = nil
                Task { await store.refreshIntelRadar() }
            }) {
                HStack(spacing: 5) {
                    if store.isLoadingIntel {
                        ProgressView().scaleEffect(0.75)
                    }
                    Image(systemName: store.isLoadingIntel ? "" : "arrow.clockwise")
                        .font(.system(size: 11, weight: .bold))
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
            let total = digest?.stats?.totalSources ?? 108
            return "\(tracks.count) 赛道 · \(total) 个公开源 · 点刷新拉取"
        }
        let totalItems = tracks.reduce(0) { $0 + ($1.items?.count ?? 0) }
        let days = digest?.recentDays ?? 7
        let updated = digest?.generatedAt ?? "—"
        return "\(tracks.count) 赛道 / \(totalItems) 条资讯 · 近 \(days) 天 · 更新于 \(updated)"
    }

    // MARK: - 12 赛道全景热点（pill 上方）

    @ViewBuilder
    private var panoramaBar: some View {
        let pan = store.intelPanorama
        let body = (pan?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "globe.asia.australia.fill")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(theme.accent)
                Text("12 赛道 · 当日热点")
                    .font(KSSFont.title(13, .bold, design: theme.titleDesign))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                if store.intelPanoramaLoading {
                    ProgressView().scaleEffect(0.65)
                    Text("生成中…")
                        .font(.system(size: 11))
                        .foregroundStyle(theme.textSecondary)
                } else if !body.isEmpty {
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            panoramaExpanded.toggle()
                        }
                    } label: {
                        Text(panoramaExpanded ? "收起" : "展开")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(theme.accent)
                    }
                    .buttonStyle(.plain)
                    Button {
                        Task { await store.generateIntelPanorama() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(theme.textSecondary)
                    }
                    .buttonStyle(.plain)
                    .help("重新生成全景摘要")
                }
            }

            if store.intelPanoramaLoading && body.isEmpty {
                Text("一键提炼全部要点时将生成跨赛道全景…")
                    .font(.system(size: 12.5))
                    .foregroundStyle(theme.textSecondary)
            } else if let err = pan?.error, body.isEmpty {
                Text("全景生成失败：\(err)")
                    .font(.system(size: 12.5))
                    .foregroundStyle(theme.down)
                    .lineLimit(2)
                Button {
                    Task { await store.generateIntelPanorama() }
                } label: {
                    Text("重试")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(theme.accent)
                }
                .buttonStyle(.plain)
            } else if body.isEmpty {
                Text("点右上角「一键提炼全部要点」生成 12 赛道全景热点")
                    .font(.system(size: 12.5))
                    .foregroundStyle(theme.textSecondary)
            } else {
                Text(body)
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textBody)
                    .lineLimit(panoramaExpanded ? nil : 2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let model = pan?.model, !model.isEmpty {
                    Text(model)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(theme.textSecondary.opacity(0.75))
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: theme.cardRadius)
                .strokeBorder(theme.outlineVariant, lineWidth: 1)
        )
    }

    // MARK: - Bulk digest 按钮 + 摘要

    /// 是否显示 bulk 完成摘要（running 时显示进度，结束后 4s 内显示结果）
    private var shouldShowBulkSummary: Bool {
        let bulk = store.bulkDigest
        if bulk.running { return true }
        if let until = bulk.summaryShownUntil, until > Date() { return true }
        return false
    }

    @ViewBuilder
    private var bulkDigestButton: some View {
        let bulk = store.bulkDigest
        if bulk.failedCount > 0 && !bulk.running {
            // 完成后失败 N 个 → 显示重试按钮
            Button {
                Task { await store.retryFailedBulkDigests() }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "arrow.counterclockwise")
                        .font(.system(size: 11, weight: .bold))
                    Text("重试 \(bulk.failedCount) 个失败赛道")
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1).fixedSize()
                }
                .foregroundStyle(theme.ma5)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(theme.ma5.opacity(0.12), in: RoundedRectangle(cornerRadius: theme.chipRadius))
            }
            .buttonStyle(.plain)
        } else if bulk.running {
            // running → 显示进度 + 取消
            HStack(spacing: 6) {
                ProgressView().scaleEffect(0.7)
                Text("提炼中 \(bulk.done)/\(bulk.total)")
                    .font(.system(size: 12, weight: .semibold).monospaced())
                    .foregroundStyle(theme.accent)
                Button {
                    store.cancelBulkDigest()
                } label: {
                    Text("取消")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(theme.textSecondary)
                }
                .buttonStyle(.plain)
            }
        } else if hasData && !store.hasLLMCredentials == false {
            // 默认触发按钮（仅当有 key 才显示）
            Button {
                Task { await store.summarizeAllIntelTracks() }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "sparkles").font(.system(size: 11, weight: .bold))
                    Text("一键提炼全部要点")
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1).fixedSize()
                }
                .foregroundStyle(theme.accent)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: theme.chipRadius))
            }
            .buttonStyle(.plain)
            .disabled(store.isLoadingIntel)
        }
    }

    @ViewBuilder
    private var bulkSummaryView: some View {
        let bulk = store.bulkDigest
        HStack(spacing: 8) {
            Image(systemName: bulk.failedCount > 0 ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .font(.system(size: 12))
            if bulk.running {
                Text("正在提炼 \(bulk.done)/\(bulk.total)...")
                    .font(.system(size: 12))
            } else {
                Text("完成 \(bulk.done)/\(bulk.total) · 失败 \(bulk.failedCount)")
                    .font(.system(size: 12))
            }
        }
        .foregroundStyle(bulk.failedCount > 0 ? theme.ma5 : theme.accent)
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            (bulk.failedCount > 0 ? theme.ma5 : theme.accent).opacity(0.08),
            in: RoundedRectangle(cornerRadius: theme.chipRadius)
        )
    }

    // MARK: - 赛道 Pills

    private var trackPills: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(tracks, id: \.key) { track in
                    Button(action: {
                        withAnimation(.easeInOut(duration: 0.15)) { activeTrack = track.key }
                    }) {
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

    // MARK: - 新闻列表（qmreader entry-card 节奏：卡间距 8、圆角 10、右缩略/favicon）

    private func trackListColumn(_ cur: IntelTrack) -> some View {
        let items = cur.items ?? []
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                let pillColor = parseHexColor(cur.accent) ?? theme.accent
                RoundedRectangle(cornerRadius: 2)
                    .fill(pillColor).frame(width: 3, height: 14)
                Text(cur.name)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                Text("\(items.count)")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(theme.textSecondary.opacity(0.85))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)

            if items.isEmpty {
                Text("近 \(digest?.recentDays ?? 7) 天该赛道暂无更新")
                    .font(.system(size: 13))
                    .foregroundStyle(theme.textSecondary.opacity(0.7))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .padding(.top, 40)
            } else {
                // qmreader `.entry-list { padding: 8px }` + card gap
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(items) { item in
                            newsRow(item, track: cur)
                        }
                    }
                    .padding(8)
                }
            }
        }
        .background(theme.canvas)
    }

    @ViewBuilder
    private func detailPane(track: IntelTrack) -> some View {
        if let item = selectedItem {
            VStack(alignment: .leading, spacing: 0) {
                // header sticky-ish top
                VStack(alignment: .leading, spacing: 0) {
                    Text(item.title)
                        .font(.system(size: 22, weight: .bold))
                        .foregroundStyle(theme.textPrimary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, 10)

                    HStack(spacing: 10) {
                        sourceFavicon(item: item, size: 16)
                        if let s = item.source {
                            Text(s).font(.system(size: 12.5, weight: .semibold))
                        }
                        if let t = item.time {
                            Text("·").foregroundStyle(theme.textSecondary.opacity(0.4))
                            Text(t).font(.system(size: 12, weight: .medium, design: .monospaced))
                        }
                        Spacer()
                        if store.isLoadingIntelDetail {
                            ProgressView().scaleEffect(0.7)
                        }
                        if let urlString = item.url, let url = URL(string: urlString) {
                            Link(destination: url) {
                                Label("外链打开", systemImage: "arrow.up.right.square")
                                    .font(.system(size: 12, weight: .semibold))
                            }
                            .foregroundStyle(theme.accent)
                        }
                    }
                    .foregroundStyle(theme.textSecondary)
                    .padding(.bottom, 14)

                    // qmreader .reader-tabs
                    readerTabBar
                        .padding(.bottom, 4)
                }
                .padding(.horizontal, 36)
                .padding(.top, 28)
                .frame(maxWidth: 780, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)

                Divider().overlay(theme.hairline.opacity(0.7))

                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        readerTabPanel(item: item, track: track)
                            .frame(maxWidth: 720, alignment: .leading)
                    }
                    .frame(maxWidth: 780, alignment: .leading)
                    .padding(.horizontal, 36)
                    .padding(.vertical, 24)
                    .frame(maxWidth: .infinity, alignment: .center)
                }
            }
            .background(theme.canvas)
            .onChange(of: item.id) { _, _ in
                // 默认投研改写；有现成稿也保持该 Tab
                readerTab = .investment
            }
        } else {
            VStack(spacing: 16) {
                ZStack {
                    RoundedRectangle(cornerRadius: 20)
                        .fill(theme.surfaceContainer)
                        .frame(width: 64, height: 64)
                    Image(systemName: "doc.text.magnifyingglass")
                        .font(.system(size: 28, weight: .medium))
                        .foregroundStyle(theme.textSecondary.opacity(0.55))
                }
                Text("选择左侧一条资讯开始阅读")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(theme.textSecondary)
                Text("投研改写 · 中文改写 · 原文")
                    .font(.system(size: 12.2))
                    .foregroundStyle(theme.textSecondary.opacity(0.65))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(theme.canvas)
        }
    }

    // MARK: - Reader tabs（qmreader .reader-tabs）

    private var readerTabBar: some View {
        HStack(spacing: 4) {
            ForEach(IntelReaderTab.allCases) { tab in
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { readerTab = tab }
                } label: {
                    Text(tab.label)
                        .font(.system(size: 13, weight: readerTab == tab ? .semibold : .medium))
                        .foregroundStyle(readerTab == tab ? theme.textPrimary : theme.textSecondary)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        .background(
                            readerTab == tab
                                ? theme.surface
                                : Color.clear,
                            in: RoundedRectangle(cornerRadius: 8)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .strokeBorder(
                                    readerTab == tab ? theme.outlineVariant : Color.clear,
                                    lineWidth: 1
                                )
                        )
                        .shadow(
                            color: readerTab == tab ? Color.black.opacity(0.04) : .clear,
                            radius: 2, x: 0, y: 1
                        )
                }
                .buttonStyle(.plain)
            }
            Spacer(minLength: 0)
        }
        .padding(4)
        .background(theme.surfaceContainer.opacity(0.65), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private func readerTabPanel(item: IntelItem, track: IntelTrack) -> some View {
        switch readerTab {
        case .investment:
            rewritePanel(
                item: item, track: track, kind: "investment",
                title: "投研改写",
                generateLabel: "生成投研改写",
                emptyHint: "尚未生成投研改写。后台 Top-K 或点下方按钮生成。"
            )
        case .chinese:
            rewritePanel(
                item: item, track: track, kind: "chinese",
                title: "中文改写",
                generateLabel: "生成中文改写",
                emptyHint: "这篇文章还没有中文改写。可一键生成流畅中文稿（qmreader 风格）。"
            )
        case .original:
            originalBodyPanel(item: item)
        }
    }

    @ViewBuilder
    private func originalBodyPanel(item: IntelItem) -> some View {
        let article = store.intelArticleByID[item.id]
        let bodyMode = article?.mode ?? "summary"
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text("原文")
                    .font(.system(size: 12, weight: .bold))
                Text(bodyModeLabel(bodyMode))
                    .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(theme.surfaceContainer, in: Capsule())
            }
            Group {
                if let body = article?.body, !body.isEmpty {
                    Text(body)
                        .font(.system(size: 16.5))
                        .foregroundStyle(theme.textBody)
                        .lineSpacing(16.5 * 0.88)
                        .fixedSize(horizontal: false, vertical: true)
                } else if let sum = item.summary, !sum.isEmpty {
                    Text(sum)
                        .font(.system(size: 16.5))
                        .foregroundStyle(theme.textBody)
                        .lineSpacing(16.5 * 0.88)
                    Text("全文抓取失败或未完成，以上为 RSS 摘要")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                } else {
                    Text("暂无正文，可尝试外链打开")
                        .font(.system(size: 14))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
    }

    private func bodyModeLabel(_ mode: String) -> String {
        switch mode {
        case "fulltext": return "全文"
        case "summary": return "摘要"
        default: return "不可用"
        }
    }

    @ViewBuilder
    private func rewritePanel(
        item: IntelItem,
        track: IntelTrack,
        kind: String,
        title: String,
        generateLabel: String,
        emptyHint: String
    ) -> some View {
        let rw = store.rewrite(for: item.id, kind: kind)
        let status = rw?.status ?? "none"
        let isChinese = kind == "chinese"

        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(isChinese
                        ? Color(red: 0.48, green: 0.39, blue: 0.18) // qmreader amber-ish
                        : theme.accent)
                Text(statusLabel(status))
                    .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                Spacer()
                if status == "ready" {
                    Button {
                        Task {
                            await store.requestIntelRewrite(
                                item: item, trackKey: track.key, trackName: track.name,
                                force: true, kind: kind
                            )
                        }
                    } label: {
                        Label("重新生成", systemImage: "arrow.clockwise")
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(theme.accent)
                }
            }

            if status == "generating" || (store.isLoadingIntelDetail && rw == nil) {
                HStack(spacing: 8) {
                    ProgressView().scaleEffect(0.85)
                    Text(isChinese ? "正在生成中文改写…" : "正在生成投研改写…")
                        .font(.system(size: 13))
                        .foregroundStyle(theme.textSecondary)
                }
                .padding(.vertical, 24)
            } else if status == "ready", let text = rw?.text, !text.isEmpty {
                // 投研 / 中文改写统一：分节 + 圆点列表 + 阅读体，不用裸 markdown
                if isChinese {
                    structuredReadingBody(text)
                } else {
                    investmentStructuredBody(text: text, sections: rw?.sections)
                }
                if let model = rw?.model {
                    Text(model)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.top, 8)
                }
            } else if status == "failed" {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 12))
                    Text(rw?.error ?? "生成失败")
                        .font(.system(size: 12.5))
                        .lineLimit(4)
                }
                .foregroundStyle(theme.down)
                Button {
                    Task {
                        await store.requestIntelRewrite(
                            item: item, trackKey: track.key, trackName: track.name,
                            force: true, kind: kind
                        )
                    }
                } label: {
                    Label("重试", systemImage: "arrow.clockwise")
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundStyle(theme.accent)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(theme.accent.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    Text(emptyHint)
                        .font(.system(size: 13.5))
                        .foregroundStyle(theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if store.hasLLMCredentials {
                        Button {
                            Task {
                                await store.requestIntelRewrite(
                                    item: item, trackKey: track.key, trackName: track.name,
                                    force: true, kind: kind
                                )
                            }
                        } label: {
                            Label(generateLabel, systemImage: "sparkles")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 14).padding(.vertical, 8)
                                .background(theme.accent, in: RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    } else {
                        Text("未接入 AI — 前往设置")
                            .font(.system(size: 12))
                            .foregroundStyle(theme.ma5)
                    }
                }
                .padding(.vertical, 12)
            }
        }
    }

    private func statusLabel(_ status: String) -> String {
        switch status {
        case "ready": return "ready"
        case "generating": return "generating"
        case "failed": return "failed"
        default: return "not queued"
        }
    }

    /// 正文阅读样式（16.5 / ~1.88 行距）；支持 **加粗**，不显示 * 号。
    private func readingBodyText(_ text: String) -> some View {
        Text(attributedReading(text))
            .font(.system(size: 16.5))
            .foregroundStyle(theme.textBody)
            .lineSpacing(16.5 * 0.88)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
    }

    private func attributedReading(_ raw: String) -> AttributedString {
        var s = raw
        // 去掉残留标题/列表标记（段落级）
        s = s.replacingOccurrences(of: #"^#{1,6}\s+"#, with: "", options: .regularExpression)
        // **bold** → 粗体
        if let attr = try? AttributedString(
            markdown: s,
            options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return attr
        }
        return AttributedString(s.replacingOccurrences(of: "**", with: ""))
    }

    // MARK: 统一结构化阅读块（中文改写 / 投研改写共用）

    private enum ReadingBlock: Identifiable {
        case heading(String)
        case paragraph(String)
        case list([String])

        var id: String {
            switch self {
            case .heading(let t): return "h-\(t)"
            case .paragraph(let t): return "p-\(t.prefix(48))-\(t.count)"
            case .list(let items): return "l-\(items.joined().prefix(48))-\(items.count)"
            }
        }
    }

    /// 中文改写：解析 ## / 列表 / 段落，视觉与投研分节一致。
    @ViewBuilder
    private func structuredReadingBody(_ text: String) -> some View {
        let blocks = parseReadingBlocks(text)
        VStack(alignment: .leading, spacing: 18) {
            ForEach(blocks) { block in
                switch block {
                case .heading(let title):
                    Text(title)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(theme.accent)
                        .tracking(0.3)
                        .padding(.top, 4)
                case .paragraph(let p):
                    readingBodyText(p)
                case .list(let items):
                    bulletList(items)
                }
            }
        }
        .textSelection(.enabled)
    }

    /// 投研改写：固定四节；无 sections 时走同一套 markdown 块解析。
    @ViewBuilder
    private func investmentStructuredBody(text: String, sections: [String: String]?) -> some View {
        let order = ["事件", "影响", "标的线索", "待验证"]
        let parsed = parseInvestmentSections(text: text, sections: sections)
        let hasAny = order.contains { !(parsed[$0] ?? "").isEmpty }

        if hasAny {
            VStack(alignment: .leading, spacing: 22) {
                ForEach(order, id: \.self) { key in
                    let body = (parsed[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                    if !body.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(key)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(theme.accent)
                                .tracking(0.3)
                            sectionBodyLines(body)
                        }
                    }
                }
            }
            .textSelection(.enabled)
        } else {
            structuredReadingBody(text)
        }
    }

    @ViewBuilder
    private func sectionBodyLines(_ body: String) -> some View {
        let lines = body
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .map { stripListMarker($0) }

        if lines.count <= 1 {
            readingBodyText(lines.first ?? body)
        } else {
            bulletList(lines)
        }
    }

    private func bulletList(_ items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, line in
                HStack(alignment: .top, spacing: 10) {
                    Circle()
                        .fill(theme.accent.opacity(0.85))
                        .frame(width: 5, height: 5)
                        .padding(.top, 10)
                    readingBodyText(line)
                }
            }
        }
    }

    /// 把 markdown 文章拆成 heading / paragraph / list，不保留 # * 符号。
    private func parseReadingBlocks(_ text: String) -> [ReadingBlock] {
        var blocks: [ReadingBlock] = []
        var para: [String] = []
        var list: [String] = []

        func flushPara() {
            let t = para.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { blocks.append(.paragraph(t)) }
            para.removeAll()
        }
        func flushList() {
            if !list.isEmpty { blocks.append(.list(list)) }
            list.removeAll()
        }

        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty {
                flushList()
                flushPara()
                continue
            }
            // 水平线忽略
            if line == "---" || line == "***" || line == "___" {
                flushList()
                flushPara()
                continue
            }
            // ## 标题
            if line.hasPrefix("#") {
                flushList()
                flushPara()
                var t = line
                while t.hasPrefix("#") { t = String(t.dropFirst()) }
                t = t.trimmingCharacters(in: .whitespaces)
                if !t.isEmpty { blocks.append(.heading(t)) }
                continue
            }
            // 列表
            if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("• ")
                || line.range(of: #"^\d+[\.、]\s+"#, options: .regularExpression) != nil {
                flushPara()
                list.append(stripListMarker(line))
                continue
            }
            flushList()
            para.append(line)
        }
        flushList()
        flushPara()
        return blocks
    }

    private func parseInvestmentSections(text: String, sections: [String: String]?) -> [String: String] {
        var out: [String: String] = [:]
        if let sections {
            for (k, v) in sections where !v.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                out[k] = v
            }
        }
        if !out.isEmpty { return out }

        let keys = ["事件", "影响", "标的线索", "待验证"]
        let parts = text.components(separatedBy: "##")
        for part in parts {
            let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines)
            for k in keys where trimmed.hasPrefix(k) {
                var body = String(trimmed.dropFirst(k.count))
                body = body.trimmingCharacters(in: CharacterSet(charactersIn: " \n：:"))
                if !body.isEmpty { out[k] = body }
            }
        }
        return out
    }

    private func stripListMarker(_ line: String) -> String {
        var s = line
        if s.hasPrefix("- ") || s.hasPrefix("* ") || s.hasPrefix("• ") {
            s = String(s.dropFirst(2))
        }
        if let r = try? Regex(#"^\d+[\.、]\s*"#), let m = s.firstMatch(of: r) {
            s = String(s[m.range.upperBound...])
        }
        return s.trimmingCharacters(in: .whitespaces)
    }

    // MARK: - qmreader-like media helpers

    /// 域名 favicon 作列表缩略；失败时字母块（对齐 feed-item / entry-thumb）。
    @ViewBuilder
    private func sourceFavicon(item: IntelItem, size: CGFloat) -> some View {
        let letter = sourceLetter(item)
        let favURL = faviconURL(for: item.url)
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.28)
                .fill(theme.surfaceContainer)
            if let favURL {
                AsyncImage(url: favURL) { phase in
                    switch phase {
                    case .success(let img):
                        img.resizable().scaledToFill()
                    default:
                        Text(letter)
                            .font(.system(size: size * 0.48, weight: .bold))
                            .foregroundStyle(theme.textSecondary)
                    }
                }
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: size * 0.28))
            } else {
                Text(letter)
                    .font(.system(size: size * 0.48, weight: .bold))
                    .foregroundStyle(theme.textSecondary)
            }
        }
        .frame(width: size, height: size)
    }

    private func sourceLetter(_ item: IntelItem) -> String {
        let s = (item.source ?? item.title).trimmingCharacters(in: .whitespacesAndNewlines)
        guard let ch = s.first else { return "·" }
        return String(ch).uppercased()
    }

    private func faviconURL(for articleURL: String?) -> URL? {
        guard let articleURL, let host = URL(string: articleURL)?.host, !host.isEmpty else { return nil }
        // Google s2 favicons：轻量、无 key；失败时 AsyncImage 走字母兜底
        var comp = URLComponents(string: "https://www.google.com/s2/favicons")
        comp?.queryItems = [
            URLQueryItem(name: "domain", value: host),
            URLQueryItem(name: "sz", value: "128"),
        ]
        return comp?.url
    }

    // MARK: - AI digest 卡片（全宽可折叠，操作在右上角）

    @ViewBuilder
    private func digestCardView(track: IntelTrack, items: [IntelItem]) -> some View {
        let state = store.intelDigests[track.key]
        let isLoading = store.intelDigestLoadingKeys.contains(track.key)
        let isSaved = state?.fromCache == true
        let isNeedKey = !store.hasLLMCredentials
        let bodyText = (state?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let hasBody = !bodyText.isEmpty
        let canToggle = hasBody || isLoading || state?.error != nil || state?.skipped == true || state != nil

        VStack(alignment: .leading, spacing: digestExpanded ? 10 : 4) {
            // header：标题左，操作/折叠右上
            HStack(spacing: 8) {
                Image(systemName: "lightbulb.fill")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(theme.accent)
                Text("今日要点 · \(track.name)")
                    .font(KSSFont.title(14, .bold, design: theme.titleDesign))
                    .foregroundStyle(theme.accent)
                if let mode = state?.mode {
                    Text(mode == "pool" ? "改写池" : "列表提炼")
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundStyle(mode == "pool" ? theme.accent : theme.textSecondary)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(theme.accent.opacity(mode == "pool" ? 0.12 : 0.06), in: Capsule())
                }
                if !digestExpanded, hasBody {
                    Text(bodyText.replacingOccurrences(of: "\n", with: " · "))
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)

                // 右上角：状态 + 操作 + 折叠
                if isLoading {
                    ProgressView().scaleEffect(0.65)
                }
                if isSaved {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(theme.textSecondary)
                        .help("已存入沉淀")
                }
                if !isNeedKey {
                    Button {
                        Task { await store.summarizeIntelTrack(track.key, name: track.name, items: items) }
                    } label: {
                        Image(systemName: hasBody ? "arrow.clockwise" : "sparkles")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(theme.accent)
                    }
                    .buttonStyle(.plain)
                    .help(hasBody ? "重新提炼" : "让 AI 提炼今日要点")
                    .disabled(isLoading)
                }
                if hasBody, !isSaved, let state {
                    Button {
                        Task {
                            _ = await store.saveIntelDigestToNotes(
                                trackKey: track.key,
                                trackName: track.name,
                                prompt: state.prompt ?? "",
                                response: state.text,
                                model: state.model ?? "",
                                items: items
                            )
                        }
                    } label: {
                        Image(systemName: "bookmark")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(theme.textSecondary)
                    }
                    .buttonStyle(.plain)
                    .help("存入沉淀")
                }
                if canToggle {
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            digestExpanded.toggle()
                        }
                    } label: {
                        HStack(spacing: 3) {
                            Text(digestExpanded ? "收起" : "展开")
                                .font(.system(size: 11, weight: .semibold))
                            Image(systemName: digestExpanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 10, weight: .bold))
                        }
                        .foregroundStyle(theme.accent)
                    }
                    .buttonStyle(.plain)
                }
            }

            // 展开后正文区（操作已上移，不再在底部占行）
            if digestExpanded {
                if let err = state?.error, bodyText.isEmpty, !isLoading {
                    Text("提炼失败：\(err)")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.down)
                        .lineLimit(3)
                } else if hasBody {
                    digestMarkdownView(bodyText)
                    if let model = state?.model, !model.isEmpty {
                        HStack(spacing: 8) {
                            Text(model)
                                .font(.system(size: 10.5, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                            if let at = state?.generatedAt, !at.isEmpty {
                                Text(at)
                                    .font(.system(size: 10.5, design: .monospaced))
                                    .foregroundStyle(theme.textSecondary.opacity(0.7))
                            }
                        }
                    }
                    if let err = state?.error {
                        Text("最近一次重提失败：\(err)")
                            .font(.system(size: 11))
                            .foregroundStyle(theme.down)
                            .lineLimit(2)
                    }
                } else if isLoading {
                    Text("AI 正在读 \(min(items.count, 25)) 条资讯…")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                } else if state?.skipped == true {
                    Text("该赛道资讯过少，跳过提炼")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                } else if state != nil {
                    Text("未生成要点正文，可点右上角重试")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                } else if isNeedKey {
                    Text("未接入 AI — 前往设置")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.ma5)
                } else {
                    Text("点右上角 ✨ 提炼今日要点")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, digestExpanded ? 12 : 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: theme.cardRadius)
                .strokeBorder(theme.accent.opacity(0.35), lineWidth: 1)
        )
    }

    /// 把 LLM 返回的 markdown bullet 文本渲染为列表（不依赖 AttributedString markdown）
    private func digestMarkdownView(_ text: String) -> some View {
        // 兼容 `\r\n`、全角破折、无换行用 `；`/`•` 分句的模型输出
        let normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        let lines: [String] = {
            let split = normalized.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
            if split.count > 1 { return split }
            // 单行多要点：按常见子弹切开
            let one = normalized.trimmingCharacters(in: .whitespacesAndNewlines)
            if one.isEmpty { return [] }
            for sep in ["；", ";", "•", "·"] {
                if one.contains(sep) {
                    return one.components(separatedBy: sep).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                }
            }
            return [one]
        }()
        return VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                let s = line.trimmingCharacters(in: .whitespaces)
                if s.isEmpty {
                    EmptyView()
                } else {
                    HStack(alignment: .top, spacing: 6) {
                        let bullet = s.hasPrefix("- ") || s.hasPrefix("* ") || s.hasPrefix("• ")
                        let body: String = {
                            if s.hasPrefix("- ") || s.hasPrefix("* ") { return String(s.dropFirst(2)) }
                            if s.hasPrefix("• ") { return String(s.dropFirst(2)) }
                            return s
                        }()
                        if bullet {
                            Text("•").font(.system(size: 12, weight: .bold)).foregroundStyle(theme.accent)
                        } else {
                            Text("•").font(.system(size: 12, weight: .bold)).foregroundStyle(theme.accent.opacity(0.55))
                        }
                        Text(body)
                            .font(.system(size: 13))
                            .foregroundStyle(theme.textBody)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// qmreader `.entry-card`：左文案 + 右 58px 缩略，padding 11–12、圆角 10、gap 10。
    private func newsRow(_ item: IntelItem, track: IntelTrack) -> some View {
        let isOn = store.selectedIntelItemID == item.id
        let zhReady = store.rewrite(for: item.id, kind: "chinese")?.status == "ready"
        let invStatus = store.rewrite(for: item.id, kind: "investment")?.status
        return Button {
            store.selectIntelItem(item, trackKey: track.key, trackName: track.name)
            readerTab = .investment
        } label: {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 0) {
                    // top meta: source · time
                    HStack(spacing: 6) {
                        sourceFavicon(item: item, size: 13)
                        if let source = item.source, !source.isEmpty {
                            Text(source)
                                .font(.system(size: 11.3, weight: .medium))
                                .lineLimit(1)
                        }
                        if let time = item.time, !time.isEmpty {
                            Text(time)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(theme.textSecondary.opacity(0.85))
                        }
                        if zhReady {
                            Text("中文")
                                .font(.system(size: 10, weight: .bold, design: .monospaced))
                                .foregroundStyle(Color(red: 0.48, green: 0.39, blue: 0.18))
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(Color(red: 0.48, green: 0.39, blue: 0.18).opacity(0.12), in: Capsule())
                        }
                        if invStatus == "ready" {
                            Text("投研")
                                .font(.system(size: 10, weight: .bold, design: .monospaced))
                                .foregroundStyle(theme.accent)
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(theme.accent.opacity(0.1), in: Capsule())
                        } else if invStatus == "generating" {
                            ProgressView().scaleEffect(0.55)
                        }
                        Spacer(minLength: 0)
                    }
                    .foregroundStyle(theme.textSecondary)
                    .padding(.bottom, 6)

                    Text(item.title)
                        .font(.system(size: 13.5, weight: isOn ? .semibold : .medium))
                        .foregroundStyle(isOn ? theme.textPrimary : theme.textBody)
                        .lineLimit(2)
                        .lineSpacing(2)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)

                    if let sum = item.summary, !sum.isEmpty {
                        Text(sum)
                            .font(.system(size: 12.2))
                            .foregroundStyle(theme.textSecondary.opacity(0.92))
                            .lineLimit(2)
                            .lineSpacing(2)
                            .padding(.top, 5)
                            .multilineTextAlignment(.leading)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                // entry-thumb 58×58
                sourceFavicon(item: item, size: 58)
                    .shadow(color: Color.black.opacity(0.06), radius: 2, x: 0, y: 1)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(isOn ? theme.surface : Color.clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(isOn ? theme.outlineVariant : Color.clear, lineWidth: 1)
            )
            .shadow(color: isOn ? Color.black.opacity(0.055) : .clear, radius: 4, x: 0, y: 2)
        }
        .buttonStyle(.plain)
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
            Button(action: {
                store.errorMessage = nil
                Task { await store.refreshIntelRadar() }
            }) {
                Text("立即拉取")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(theme.accent)
                    .padding(.horizontal, 16).padding(.vertical, 7)
                    .background(theme.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: theme.chipRadius))
            }
            .buttonStyle(.plain)
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }
}

// MARK: - Hex Color Parsing

private func parseHexColor(_ hex: String?) -> Color? {
    guard let hex, hex.hasPrefix("#"), hex.count == 7 else { return nil }
    let r = hex.dropFirst(1).prefix(2)
    let g = hex.dropFirst(3).prefix(2)
    let b = hex.dropFirst(5).prefix(2)
    guard let ri = UInt8(r, radix: 16), let gi = UInt8(g, radix: 16), let bi = UInt8(b, radix: 16) else { return nil }
    return Color(red: Double(ri) / 255, green: Double(gi) / 255, blue: Double(bi) / 255)
}
