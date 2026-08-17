import AppKit
import SwiftUI

// MARK: - 注册制右侧工作台（dsh-better-sidebar 交互复刻，KSS 化 tab）

/// 一个可注册的工作台 tab。后续扩展（如新数据面板）只需在 AIChatView 的
/// `workbenchTabs` 里追加一条 spec，不改布局代码——对应 better-sidebar 的
/// `registerTab` 语义。
struct SeesawWorkbenchTabSpec: Identifiable {
    let id: String
    let title: String
    let icon: String
    var badge: Int?
    let content: () -> AnyView

    init(
        id: String,
        title: String,
        icon: String,
        badge: Int? = nil,
        @ViewBuilder content: @escaping () -> some View
    ) {
        self.id = id
        self.title = title
        self.icon = icon
        self.badge = badge
        let builder = content
        self.content = { AnyView(builder()) }
    }
}

/// 右侧工作台外壳：顶部 tab 轨道 + 选中内容。
struct SeesawWorkbenchSidebar: View {
    let tabs: [SeesawWorkbenchTabSpec]
    @Binding var selection: String
    var showsCloseButton = false
    var onClose: () -> Void = {}

    @Environment(\.kssTheme) private var theme

    private var selectedTab: SeesawWorkbenchTabSpec? {
        tabs.first { $0.id == selection } ?? tabs.first
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                ForEach(tabs) { tab in
                    Button {
                        selection = tab.id
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: tab.icon)
                                .font(.system(size: 11.5, weight: .semibold))
                            if selection == tab.id {
                                Text(tab.title)
                                    .font(KSSFont.themed(11.5, .semibold, theme: theme))
                            }
                            if let badge = tab.badge, badge > 0 {
                                Text("\(badge)")
                                    .font(KSSFont.themed(9.5, .bold, theme: theme))
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 1)
                                    .background(theme.accentSoft, in: Capsule())
                            }
                        }
                        .foregroundStyle(selection == tab.id ? theme.accent : theme.textSecondary)
                        .padding(.horizontal, 9)
                        .frame(height: 28)
                        .background(
                            selection == tab.id ? theme.accentSoft.opacity(0.7) : .clear,
                            in: Capsule()
                        )
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .help(tab.title)
                }
                Spacer(minLength: 0)
                if showsCloseButton {
                    Button(action: onClose) {
                        Image(systemName: "xmark")
                            .font(.system(size: 11, weight: .semibold))
                            .frame(width: 26, height: 26)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(theme.textSecondary)
                    .help("关闭工作台")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .overlay(alignment: .bottom) {
                Rectangle().fill(theme.hairline).frame(height: 1)
            }

            if let selectedTab {
                selectedTab.content()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
        }
        .background(theme.surface)
    }
}

// MARK: - 文件预览注册表（better-sidebar registerFileViewer 语义）

/// 按扩展名注册预览器；未注册的扩展名走 fallback。
enum SeesawFileViewerRegistry {
    typealias Viewer = (_ url: URL, _ text: String?) -> AnyView

    private static var viewers: [String: Viewer] = defaultViewers()

    static func register(extensions: [String], viewer: @escaping Viewer) {
        for ext in extensions {
            viewers[ext.lowercased()] = viewer
        }
    }

    static func view(for url: URL, text: String?) -> AnyView {
        let ext = url.pathExtension.lowercased()
        if let viewer = viewers[ext] {
            return viewer(url, text)
        }
        return AnyView(SeesawPlainTextPreview(text: text))
    }

    private static func defaultViewers() -> [String: Viewer] {
        var registry: [String: Viewer] = [:]
        let markdown: Viewer = { _, text in
            AnyView(
                MarkdownWebView(text: text ?? "", kind: .markdown)
            )
        }
        registry["md"] = markdown
        registry["markdown"] = markdown
        let html: Viewer = { _, text in
            AnyView(MarkdownWebView(text: text ?? "", kind: .htmlFragment))
        }
        registry["html"] = html
        let csv: Viewer = { _, text in
            AnyView(SeesawCSVPreview(text: text ?? ""))
        }
        registry["csv"] = csv
        registry["tsv"] = csv
        let image: Viewer = { url, _ in
            AnyView(SeesawImagePreview(url: url))
        }
        for ext in ["png", "jpg", "jpeg", "gif", "webp"] {
            registry[ext] = image
        }
        return registry
    }
}

struct SeesawPlainTextPreview: View {
    let text: String?
    @Environment(\.kssTheme) private var theme

    var body: some View {
        ScrollView {
            Text(text ?? "（无法读取文件内容）")
                .font(.system(size: 11.5, design: .monospaced))
                .foregroundStyle(theme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
                .padding(12)
        }
    }
}

struct SeesawCSVPreview: View {
    let text: String
    @Environment(\.kssTheme) private var theme

    private var rows: [[String]] {
        text.split(separator: "\n", omittingEmptySubsequences: false)
            .prefix(200)
            .map { line in
                line.split(separator: ",", omittingEmptySubsequences: false)
                    .map { $0.trimmingCharacters(in: .whitespaces) }
            }
    }

    var body: some View {
        ScrollView([.vertical, .horizontal]) {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 4) {
                ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Text(cell)
                                .font(.system(size: 11, design: .monospaced))
                                .fontWeight(index == 0 ? .semibold : .regular)
                                .foregroundStyle(index == 0 ? theme.textPrimary : theme.textSecondary)
                                .lineLimit(1)
                        }
                    }
                }
            }
            .padding(12)
        }
    }
}

struct SeesawImagePreview: View {
    let url: URL
    @Environment(\.kssTheme) private var theme

    var body: some View {
        ScrollView {
            if let image = NSImage(contentsOf: url) {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: .infinity)
                    .padding(12)
            } else {
                Text("（图片无法加载）")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(12)
            }
        }
    }
}

// MARK: - 文件 tab

/// 工作区文件浏览 + 预览：数据来自只读 workspace-files 命令（与 @file 引用
/// 同一白名单），预览按扩展名走 SeesawFileViewerRegistry。
struct SeesawFilesTab: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme

    @State private var query = ""
    @State private var hits: [WorkspaceFileHit] = []
    @State private var selected: WorkspaceFileHit?
    @State private var previewText: String?
    @State private var previewURL: URL?
    @State private var searchTask: Task<Void, Never>?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 7) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(theme.textSecondary)
                TextField("搜索报告、导出与文档…", text: $query)
                    .textFieldStyle(.plain)
                    .font(KSSFont.themed(12, theme: theme))
                    .onChange(of: query) { _, _ in scheduleSearch() }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .overlay(alignment: .bottom) {
                Rectangle().fill(theme.hairline).frame(height: 1)
            }

            if let selected, let previewURL {
                VStack(spacing: 0) {
                    HStack(spacing: 8) {
                        Button {
                            self.selected = nil
                            self.previewText = nil
                            self.previewURL = nil
                        } label: {
                            Label("文件列表", systemImage: "chevron.left")
                                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(theme.accent)
                        Spacer(minLength: 0)
                        Button {
                            store.addPendingFileRef(selected.path)
                        } label: {
                            Label("引用到对话", systemImage: "at")
                                .font(KSSFont.themed(11, .semibold, theme: theme))
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(theme.accent)
                        .help("作为 @file 引用附进下一轮输入")
                        Button {
                            NSWorkspace.shared.activateFileViewerSelecting([previewURL])
                        } label: {
                            Image(systemName: "arrow.up.forward.app")
                                .font(.system(size: 11, weight: .semibold))
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(theme.textSecondary)
                        .help("在 Finder 中显示")
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(selected.name)
                            .font(KSSFont.themed(12.5, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text(selected.path)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 6)
                    Divider().overlay(theme.hairline)
                    SeesawFileViewerRegistry.view(for: previewURL, text: previewText)
                }
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(hits) { hit in
                            Button {
                                open(hit)
                            } label: {
                                HStack(spacing: 8) {
                                    Image(systemName: iconName(for: hit))
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundStyle(theme.textSecondary)
                                        .frame(width: 18)
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(hit.name)
                                            .font(KSSFont.themed(12, .semibold, theme: theme))
                                            .foregroundStyle(theme.textPrimary)
                                            .lineLimit(1)
                                        Text(hit.directory)
                                            .font(.system(size: 9.5, design: .monospaced))
                                            .foregroundStyle(theme.textSecondary)
                                            .lineLimit(1)
                                    }
                                    Spacer(minLength: 0)
                                }
                                .padding(.horizontal, 12)
                                .padding(.vertical, 7)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            Divider().overlay(theme.hairline.opacity(0.6))
                                .padding(.leading, 38)
                        }
                        if hits.isEmpty {
                            Text(query.isEmpty ? "正在读取最近文件…" : "没有匹配的文件")
                                .font(KSSFont.themed(11.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .padding(14)
                        }
                    }
                }
            }
        }
        .task { await refresh() }
    }

    private func iconName(for hit: WorkspaceFileHit) -> String {
        switch (hit.path as NSString).pathExtension.lowercased() {
        case "md", "markdown": return "doc.richtext"
        case "csv", "tsv": return "tablecells"
        case "png", "jpg", "jpeg", "gif", "webp": return "photo"
        case "py", "swift", "js", "mjs", "ts": return "chevron.left.forwardslash.chevron.right"
        default: return "doc.text"
        }
    }

    private func scheduleSearch() {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 150_000_000)
            guard !Task.isCancelled else { return }
            await refresh()
        }
    }

    private func refresh() async {
        let results = await store.searchWorkspaceFiles(query: query, limit: 40)
        if !Task.isCancelled {
            hits = results
        }
    }

    private func open(_ hit: WorkspaceFileHit) {
        guard let url = store.resolveWorkspaceFileURL(hit.path) else { return }
        selected = hit
        previewURL = url
        let ext = url.pathExtension.lowercased()
        if ["png", "jpg", "jpeg", "gif", "webp"].contains(ext) {
            previewText = nil
        } else {
            previewText = try? String(contentsOf: url, encoding: .utf8)
        }
    }
}

// MARK: - 运行 tab（子代理 / 研究运行）

struct SeesawRunsTab: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if store.isChatStreaming {
                    HStack(spacing: 8) {
                        Image(systemName: "circle.dotted.circle")
                            .foregroundStyle(theme.accent)
                        Text(store.chatToolInProgress.map { "对话代理正在调用 \($0)" } ?? "对话代理正在生成")
                            .font(KSSFont.themed(12, .semibold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    Divider().overlay(theme.hairline)
                }

                if store.researchGoals.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("暂无研究运行")
                            .font(KSSFont.themed(12.5, .semibold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text("Deep Research 的目标与任务会显示在这里；也可在 Runbook 中发起。")
                            .font(KSSFont.themed(11.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                    .padding(14)
                } else {
                    ForEach(store.researchGoals) { goal in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 8) {
                                Circle()
                                    .fill(statusColor(goal.status))
                                    .frame(width: 7, height: 7)
                                Text(goal.objective)
                                    .font(KSSFont.themed(12, .semibold, theme: theme))
                                    .foregroundStyle(theme.textPrimary)
                                    .lineLimit(2)
                            }
                            HStack(spacing: 8) {
                                Text(goal.status)
                                    .font(KSSFont.themed(10.5, .semibold, theme: theme))
                                    .foregroundStyle(statusColor(goal.status))
                                if let progress = goal.progress {
                                    Text(String(format: "%.0f%%", progress * 100))
                                        .font(KSSFont.themed(10.5, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                }
                                Text(goal.profileId)
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundStyle(theme.textSecondary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        Divider().overlay(theme.hairline.opacity(0.6))
                    }
                }

                Button {
                    store.openRunbook(focusingResearch: true)
                } label: {
                    Label("打开 Runbook 工作台", systemImage: "arrow.up.right.square")
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
                .padding(14)
            }
        }
        .task { await store.loadResearchGoals() }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "running", "active": return theme.accent
        case "completed", "succeeded": return .green
        case "failed", "interrupted": return .red
        default: return theme.textSecondary
        }
    }
}

// MARK: - 持久会话左栏（desktop-app 工作台参考）

struct SeesawSessionPane: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme

    @State private var search = ""

    private var sessions: [AgentSession] {
        let active = store.agentSessions.filter { !$0.archived }
        let keyword = search.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !keyword.isEmpty else { return active }
        return active.filter {
            $0.title.localizedCaseInsensitiveContains(keyword)
                || $0.sessionId.localizedCaseInsensitiveContains(keyword)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("会话")
                    .font(KSSFont.themed(13, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer(minLength: 0)
                Button {
                    store.createAgentSession()
                } label: {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 12, weight: .semibold))
                        .frame(width: 26, height: 26)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
                .help("新会话")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)

            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(theme.textSecondary)
                TextField("搜索会话", text: $search)
                    .textFieldStyle(.plain)
                    .font(KSSFont.themed(11.5, theme: theme))
            }
            .padding(.horizontal, 9)
            .frame(height: 28)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, 12)
            .padding(.bottom, 8)

            Divider().overlay(theme.hairline)

            ScrollView {
                LazyVStack(spacing: 1) {
                    ForEach(sessions) { session in
                        Button {
                            store.openAgentSession(session.sessionId)
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(session.title)
                                    .font(KSSFont.themed(
                                        12,
                                        session.sessionId == store.selectedAgentSessionId ? .bold : .medium,
                                        theme: theme
                                    ))
                                    .foregroundStyle(theme.textPrimary)
                                    .lineLimit(1)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)
                            .background(
                                session.sessionId == store.selectedAgentSessionId
                                    ? theme.accentSoft
                                    : .clear,
                                in: RoundedRectangle(cornerRadius: 8)
                            )
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .contextMenu {
                            Button("归档会话") {
                                store.archiveAgentSession(session.sessionId)
                            }
                        }
                    }
                }
                .padding(8)
            }
        }
        .background(theme.surface)
    }
}
