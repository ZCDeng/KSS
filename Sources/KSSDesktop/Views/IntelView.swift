import SwiftUI
import AppKit

/// U2 资讯雷达独立页面 —— list|detail 阅读工作台（plan 2026-07-10-001 Layout A）。
/// 数据由 bridge `intel-radar` 命令提供（12 赛道 108 公开 RSS 源）。
struct IntelView: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var activeTrack: String = "tech"

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
                if !tracks.isEmpty { trackPills }
            }
            .padding(.horizontal, 20)
            .padding(.top, 18)
            .padding(.bottom, 12)

            Divider().overlay(theme.hairline)

            // ---- 内容：list | detail ----
            Group {
                if store.isLoadingIntel {
                    loadingState
                } else if tracks.isEmpty && !hasData {
                    emptyState
                } else if let cur = currentTrack {
                    HStack(spacing: 0) {
                        trackListColumn(cur)
                            .frame(width: 360)
                        Divider().overlay(theme.hairline)
                        detailPane(track: cur)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(theme.canvas)
        .onAppear { Task { await store.loadIntel() } }
        .onChange(of: activeTrack) { _, _ in
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

    // MARK: - 新闻列表

    private func trackListColumn(_ cur: IntelTrack) -> some View {
        let items = cur.items ?? []
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                let pillColor = parseHexColor(cur.accent) ?? theme.accent
                RoundedRectangle(cornerRadius: 2)
                    .fill(pillColor).frame(width: 4, height: 16)
                Text(cur.name)
                    .font(KSSFont.title(14, .bold, design: theme.titleDesign))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                Text("\(items.count)")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)

            if !items.isEmpty {
                digestCardView(track: cur, items: items)
                    .padding(.horizontal, 10)
                    .padding(.bottom, 8)
            }

            if items.isEmpty {
                Text("近 \(digest?.recentDays ?? 7) 天该赛道暂无更新")
                    .font(.system(size: 12.5))
                    .foregroundStyle(theme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(items) { item in
                            newsRow(item, track: cur)
                            Divider().overlay(theme.hairline)
                        }
                    }
                }
            }
        }
        .background(theme.canvas)
    }

    @ViewBuilder
    private func detailPane(track: IntelTrack) -> some View {
        if let item = selectedItem {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    // header
                    Text(item.title)
                        .font(KSSFont.title(18, .bold, design: theme.titleDesign))
                        .foregroundStyle(theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 10) {
                        if let t = item.time { Text(t).font(.system(size: 11.5, design: .monospaced)) }
                        if let s = item.source { Text(s).font(.system(size: 11.5, weight: .medium)) }
                        Spacer()
                        if let urlString = item.url, let url = URL(string: urlString) {
                            Link(destination: url) {
                                Label("外链打开", systemImage: "arrow.up.right.square")
                                    .font(.system(size: 12, weight: .semibold))
                            }
                            .foregroundStyle(theme.accent)
                        }
                    }
                    .foregroundStyle(theme.textSecondary)

                    // body
                    let article = store.intelArticleByID[item.id]
                    let bodyMode = article?.mode ?? "summary"
                    HStack(spacing: 6) {
                        Text("正文")
                            .font(.system(size: 12, weight: .bold))
                        Text(bodyMode == "fulltext" ? "全文" : (bodyMode == "summary" ? "摘要" : "不可用"))
                            .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(theme.accent.opacity(0.08), in: Capsule())
                        if store.isLoadingIntelDetail {
                            ProgressView().scaleEffect(0.7)
                        }
                    }
                    if let body = article?.body, !body.isEmpty {
                        Text(body)
                            .font(.system(size: 13.5))
                            .foregroundStyle(theme.textBody)
                            .fixedSize(horizontal: false, vertical: true)
                    } else if let sum = item.summary, !sum.isEmpty {
                        Text(sum)
                            .font(.system(size: 13.5))
                            .foregroundStyle(theme.textBody)
                        Text("全文抓取失败或未完成，以上为 RSS 摘要")
                            .font(.system(size: 11))
                            .foregroundStyle(theme.textSecondary)
                    } else {
                        Text("暂无正文，可尝试外链打开")
                            .font(.system(size: 13))
                            .foregroundStyle(theme.textSecondary)
                    }

                    Divider().overlay(theme.hairline)

                    // rewrite
                    rewriteSection(item: item, track: track)
                }
                .padding(20)
            }
            .background(theme.canvas)
        } else {
            VStack(spacing: 10) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 28))
                    .foregroundStyle(theme.textSecondary.opacity(0.5))
                Text("选择左侧一条资讯开始阅读")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(theme.textSecondary)
                Text("详情板展示正文（尽量全文）+ 投研向改写")
                    .font(.system(size: 12))
                    .foregroundStyle(theme.textSecondary.opacity(0.7))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(theme.canvas)
        }
    }

    @ViewBuilder
    private func rewriteSection(item: IntelItem, track: IntelTrack) -> some View {
        let rw = store.intelRewriteByID[item.id]
        let status = rw?.status ?? "none"
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("投研改写")
                    .font(KSSFont.title(14, .bold, design: theme.titleDesign))
                    .foregroundStyle(theme.accent)
                Spacer()
                Text(statusLabel(status))
                    .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if status == "generating" || (store.isLoadingIntelDetail && rw == nil) {
                HStack(spacing: 8) {
                    ProgressView().scaleEffect(0.8)
                    Text("生成投研改写中…")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                }
            } else if status == "ready", let text = rw?.text, !text.isEmpty {
                digestMarkdownView(text)
                if let model = rw?.model {
                    Text(model).font(.system(size: 10.5, design: .monospaced)).foregroundStyle(theme.textSecondary)
                }
                Button {
                    Task { await store.requestIntelRewrite(item: item, trackKey: track.key, trackName: track.name, force: true) }
                } label: {
                    Label("重新改写", systemImage: "arrow.clockwise")
                        .font(.system(size: 12, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
            } else if status == "failed" {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 12))
                    Text(rw?.error ?? "改写失败")
                        .font(.system(size: 12.5))
                        .lineLimit(3)
                }
                .foregroundStyle(theme.down)
                Button {
                    Task { await store.requestIntelRewrite(item: item, trackKey: track.key, trackName: track.name, force: true) }
                } label: {
                    Label("重试", systemImage: "arrow.clockwise")
                        .font(.system(size: 12, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
            } else {
                Text("尚未生成改写（后台 Top-K 或点下方按钮）")
                    .font(.system(size: 12.5))
                    .foregroundStyle(theme.textSecondary)
                if store.hasLLMCredentials {
                    Button {
                        Task { await store.requestIntelRewrite(item: item, trackKey: track.key, trackName: track.name, force: true) }
                    } label: {
                        Label("生成投研改写", systemImage: "sparkles")
                            .font(.system(size: 12.5, weight: .semibold))
                            .foregroundStyle(theme.accent)
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(theme.accent.opacity(0.1), in: RoundedRectangle(cornerRadius: theme.chipRadius))
                    }
                    .buttonStyle(.plain)
                } else {
                    Text("未接入 AI — 前往设置").font(.system(size: 12)).foregroundStyle(theme.ma5)
                }
            }
        }
        .padding(12)
        .background(theme.accent.opacity(0.04), in: RoundedRectangle(cornerRadius: theme.chipRadius))
    }

    private func statusLabel(_ status: String) -> String {
        switch status {
        case "ready": return "ready"
        case "generating": return "generating"
        case "failed": return "failed"
        default: return "not queued"
        }
    }

    // MARK: - AI digest 卡片（plan 2026-07-09-001）

    @ViewBuilder
    private func digestCardView(track: IntelTrack, items: [IntelItem]) -> some View {
        let state = store.intelDigests[track.key]
        let isLoading = state != nil && (state?.text.isEmpty ?? true) && state?.error == nil && state?.skipped != true && state?.fromCache != true
        let isSaved = state?.fromCache == true
        let isNeedKey = !store.hasLLMCredentials
        let showSavedBadge = isSaved

        VStack(alignment: .leading, spacing: 10) {
            // header
            HStack(spacing: 8) {
                Image(systemName: "lightbulb.fill")
                    .font(.system(size: 12, weight: .bold))
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
                Spacer()
                if showSavedBadge {
                    Label("已存入沉淀", systemImage: "checkmark.circle.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(theme.textSecondary)
                }
            }

            // body
            if let state, !isLoading {
                if let err = state.error {
                    // error
                    HStack(spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 12))
                        Text("提炼失败：\(err)")
                            .font(.system(size: 12.5))
                            .lineLimit(3)
                    }
                    .foregroundStyle(theme.down)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    HStack(spacing: 8) {
                        digestActionButton(track: track, items: items, label: "重试", icon: "arrow.clockwise", isPrimary: true)
                    }
                } else if !(state.text.isEmpty) {
                    // done: render markdown bullets
                    digestMarkdownView(state.text)
                    if let model = state.model, !model.isEmpty {
                        HStack(spacing: 8) {
                            Text(model)
                                .font(.system(size: 10.5, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                            if let at = state.generatedAt, !at.isEmpty {
                                Text(at)
                                    .font(.system(size: 10.5, design: .monospaced))
                                    .foregroundStyle(theme.textSecondary.opacity(0.7))
                            }
                        }
                    }
                    HStack(spacing: 8) {
                        digestActionButton(track: track, items: items, label: "重新提炼", icon: "arrow.clockwise", isPrimary: false)
                        if !isSaved {
                            digestSaveButton(track: track, items: items, state: state)
                        }
                    }
                } else if state.skipped == true {
                    Text("该赛道资讯过少，跳过提炼")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                }
            } else if isLoading {
                HStack(spacing: 8) {
                    ProgressView().scaleEffect(0.8)
                    Text("AI 正在读 \(min(items.count, 25)) 条资讯…")
                        .font(.system(size: 12.5))
                        .foregroundStyle(theme.textSecondary)
                }
            } else {
                // idle - 显示触发按钮
                if isNeedKey {
                    HStack(spacing: 6) {
                        Image(systemName: "key.fill")
                            .font(.system(size: 11))
                        Text("未接入 AI — 前往设置")
                            .font(.system(size: 12.5))
                    }
                    .foregroundStyle(theme.ma5)
                }
                digestActionButton(track: track, items: items, label: "让 AI 提炼今日要点", icon: "sparkles", isPrimary: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: theme.cardRadius)
                .strokeBorder(theme.accent.opacity(0.35), lineWidth: 1)
        )
    }

    @ViewBuilder
    private func digestActionButton(track: IntelTrack, items: [IntelItem], label: String, icon: String, isPrimary: Bool) -> some View {
        Button(action: {
            Task { await store.summarizeIntelTrack(track.key, name: track.name, items: items) }
        }) {
            HStack(spacing: 5) {
                Image(systemName: icon).font(.system(size: 11, weight: .bold))
                Text(label).font(.system(size: 12, weight: .semibold))
            }
            .foregroundStyle(isPrimary ? theme.accent : theme.textSecondary)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: theme.chipRadius))
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func digestSaveButton(track: IntelTrack, items: [IntelItem], state: IntelDigestResponse) -> some View {
        Button(action: {
            Task {
                _ = await store.saveIntelDigestToNotes(
                    trackKey: track.key,
                    trackName: track.name,
                    prompt: state.prompt ?? "",
                    response: state.text,
                    model: state.model ?? "",
                    items: items,
                )
            }
        }) {
            HStack(spacing: 5) {
                Image(systemName: "bookmark.fill").font(.system(size: 11, weight: .bold))
                Text("存入沉淀").font(.system(size: 12, weight: .semibold))
            }
            .foregroundStyle(theme.accent)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: theme.chipRadius))
        }
        .buttonStyle(.plain)
    }

    /// 把 LLM 返回的 markdown bullet 文本渲染为列表（不依赖 AttributedString markdown）
    private func digestMarkdownView(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(text.split(separator: "\n").enumerated()), id: \.offset) { _, line in
                let s = line.trimmingCharacters(in: .whitespaces)
                if s.isEmpty { EmptyView() } else {
                    HStack(alignment: .top, spacing: 6) {
                        if s.hasPrefix("- ") {
                            Text("•").font(.system(size: 12, weight: .bold)).foregroundStyle(theme.accent)
                            Text(String(s.dropFirst(2)))
                                .font(.system(size: 13))
                                .foregroundStyle(theme.textBody)
                                .fixedSize(horizontal: false, vertical: true)
                        } else {
                            Text(s)
                                .font(.system(size: 13))
                                .foregroundStyle(theme.textBody)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func newsRow(_ item: IntelItem, track: IntelTrack) -> some View {
        let isOn = store.selectedIntelItemID == item.id
        let rwStatus = store.intelRewriteByID[item.id]?.status
        return Button {
            store.selectIntelItem(item, trackKey: track.key, trackName: track.name)
        } label: {
            HStack(alignment: .top, spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title)
                        .font(.system(size: 12.5, weight: isOn ? .semibold : .regular))
                        .foregroundStyle(theme.textBody)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    HStack(spacing: 6) {
                        if let time = item.time, !time.isEmpty {
                            Text(time).font(.system(size: 10.5, design: .monospaced))
                        }
                        if let source = item.source, !source.isEmpty {
                            Text(source).font(.system(size: 10.5, weight: .medium)).lineLimit(1)
                        }
                        if let rwStatus, rwStatus == "ready" {
                            Text("改写✓")
                                .font(.system(size: 9.5, weight: .bold, design: .monospaced))
                                .foregroundStyle(theme.accent)
                        } else if rwStatus == "generating" {
                            Text("…")
                                .font(.system(size: 9.5, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                    .foregroundStyle(theme.textSecondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .background(isOn ? theme.accent.opacity(0.16) : Color.clear)
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
