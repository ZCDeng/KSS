import SwiftUI
import AppKit
import WebKit

struct ResearchWorkbenchView: View {
    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    @State private var showingCreate = false
    @State private var objective = ""
    @State private var profileId = "investment-weekly-v3"
    @State private var selectedArtifact: ResearchArtifact?
    @State private var artifactToPublish: ResearchArtifact?
    @State private var publishDestination: String?

    var body: some View {
        HStack(spacing: 0) {
            goalList
                .frame(width: 280)
            Divider()
            Group {
                if let goal = store.selectedResearchGoal {
                    goalDetail(goal)
                } else if store.isLoadingResearch {
                    ProgressView("正在读取研究目标…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ContentUnavailableView(
                        "尚未选择研究目标",
                        systemImage: "scope",
                        description: Text("新建一个目标，或从左侧打开已有研究。"))
                }
            }
        }
        .background(theme.surface.opacity(0.45), in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(theme.hairline.opacity(0.7), lineWidth: 1)
        )
        .sheet(isPresented: $showingCreate) { createSheet }
        .confirmationDialog(
            "发布研究产物？",
            isPresented: Binding(
                get: { artifactToPublish != nil },
                set: { if !$0 { artifactToPublish = nil } }),
            presenting: artifactToPublish
        ) { artifact in
            Button("确认发布「\(artifact.logicalName)」") {
                let destination = publishDestination
                artifactToPublish = nil
                publishDestination = nil
                if let destination {
                    Task { _ = await store.publishResearchArtifact(artifact, destination: destination) }
                }
            }
            Button("取消", role: .cancel) {
                artifactToPublish = nil
                publishDestination = nil
            }
        } message: { _ in
            Text("发布会写入正式产物记录。请先检查预览与审计状态。")
        }
    }

    private var goalList: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("研究目标")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                Spacer()
                Button {
                    Task { await store.loadResearchGoals() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("刷新研究目标")
                Button {
                    showingCreate = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("新建研究目标")
            }
            .padding(14)

            Divider()
            if let candidate = store.researchCandidate {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Seesaw 建议")
                        .font(KSSFont.themed(11, .bold, theme: theme))
                        .foregroundStyle(theme.accent)
                    Text(candidate.objective)
                        .font(KSSFont.themed(12.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .lineLimit(4)
                    Button("创建为研究目标") {
                        store.researchCandidate = nil
                        Task {
                            await store.createResearchGoal(
                                objective: candidate.objective,
                                profileId: candidate.profileId ?? "investment-weekly-v3")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                .padding(12)
                Divider()
            }
            if store.researchGoals.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "scope")
                        .font(.system(size: 24))
                        .foregroundStyle(theme.textSecondary)
                    Text("暂无深度研究")
                        .font(KSSFont.themed(13, .semibold, theme: theme))
                    Button("新建目标") { showingCreate = true }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 4) {
                        ForEach(store.researchGoals) { goal in
                            Button {
                                selectedArtifact = nil
                                Task { await store.openResearchGoal(goal.goalId) }
                            } label: {
                                VStack(alignment: .leading, spacing: 7) {
                                    Text(goal.objective)
                                        .font(KSSFont.themed(13, .semibold, theme: theme))
                                        .foregroundStyle(theme.textPrimary)
                                        .lineLimit(3)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    HStack {
                                        ResearchStatusLabel(status: goal.status)
                                        Spacer()
                                        if let progress = goal.progress {
                                            Text("\(Int(progress * 100))%")
                                                .font(KSSFont.themed(11, .medium, theme: theme))
                                                .foregroundStyle(theme.textSecondary)
                                        }
                                    }
                                }
                                .padding(10)
                                .background(
                                    store.selectedResearchGoalId == goal.goalId
                                        ? theme.accent.opacity(0.12)
                                        : Color.clear,
                                    in: RoundedRectangle(cornerRadius: 9)
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(8)
                }
            }
        }
    }

    private func goalDetail(_ goal: ResearchGoalDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 6) {
                            ResearchStatusLabel(status: goal.status)
                            Text(goal.objective)
                                .font(KSSFont.themed(22, .bold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                                .textSelection(.enabled)
                        }
                        Spacer()
                        controls(for: goal)
                    }
                    if let progress = goal.progress {
                        ProgressView(value: progress)
                            .tint(theme.accent)
                    }
                    if let snapshot = goal.snapshot {
                        HStack(spacing: 12) {
                            Label(
                                "数据截至 \(snapshot.asOf ?? "未标注")",
                                systemImage: "calendar.badge.clock")
                            if let snapshotId = snapshot.snapshotId {
                                Text(snapshotId)
                                    .font(KSSFont.themed(10.5, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
                                    .textSelection(.enabled)
                            }
                        }
                        .font(KSSFont.themed(11.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    }
                    if let reason = goal.terminalReason, !reason.isEmpty {
                        Text(reason)
                            .font(KSSFont.themed(12.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                }

                if !goal.criteria.isEmpty {
                    section("验收条件", systemImage: "checklist") {
                        VStack(spacing: 8) {
                            ForEach(goal.criteria) { criterion in
                                HStack(alignment: .top, spacing: 9) {
                                    Image(systemName: criterion.status == "met"
                                          ? "checkmark.circle.fill" : "circle")
                                        .foregroundStyle(criterion.status == "met"
                                                         ? theme.accent : theme.textSecondary)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(criterion.title)
                                        if let detail = criterion.detail {
                                            Text(detail)
                                                .font(KSSFont.themed(11.5, theme: theme))
                                                .foregroundStyle(theme.textSecondary)
                                        }
                                    }
                                    Spacer()
                                }
                            }
                        }
                    }
                }

                if !goal.tasks.isEmpty {
                    section("任务", systemImage: "square.stack.3d.up") {
                        VStack(spacing: 8) {
                            ForEach(goal.tasks) { task in
                                HStack {
                                    ResearchStatusLabel(status: task.status)
                                    Text(task.title)
                                    Spacer()
                                    if task.status == "failed" {
                                        Button("重试") {
                                            Task { await store.performResearchAction("retry_task", taskId: task.taskId) }
                                        }
                                        .buttonStyle(.bordered)
                                        .controlSize(.small)
                                    }
                                }
                            }
                        }
                    }
                }

                timeline(goalId: goal.goalId)

                if !goal.evidence.isEmpty {
                    section("证据", systemImage: "doc.text.magnifyingglass") {
                        VStack(spacing: 9) {
                            ForEach(goal.evidence) { evidence in
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack {
                                        Text(evidence.title).fontWeight(.semibold)
                                        Spacer()
                                        Text(evidence.source ?? "未知来源")
                                            .foregroundStyle(theme.textSecondary)
                                    }
                                    if let snippet = evidence.snippet {
                                        Text(snippet)
                                            .font(KSSFont.themed(12, theme: theme))
                                            .foregroundStyle(theme.textSecondary)
                                            .lineLimit(3)
                                    }
                                    if let url = evidence.url {
                                        Text(url)
                                            .font(KSSFont.themed(10.5, theme: theme))
                                            .foregroundStyle(theme.accent)
                                            .textSelection(.enabled)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                        }
                    }
                }

                if !goal.artifacts.isEmpty {
                    artifacts(goal.artifacts)
                }

                if !goal.audit.isEmpty {
                    section("审计", systemImage: "checkmark.shield") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(goal.audit) { entry in
                                HStack(alignment: .top) {
                                    Text(entry.timestamp ?? "—")
                                        .foregroundStyle(theme.textSecondary)
                                        .frame(width: 150, alignment: .leading)
                                    Text(entry.message ?? entry.type)
                                    Spacer()
                                    if let status = entry.status {
                                        ResearchStatusLabel(status: status)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .padding(18)
        }
    }

    @ViewBuilder
    private func controls(for goal: ResearchGoalDetail) -> some View {
        HStack(spacing: 8) {
            switch goal.status.lowercased() {
            case "draft", "created", "pending", "queued":
                actionButton("开始", icon: "play.fill", action: "start", prominent: true)
            case "running":
                actionButton("暂停", icon: "pause.fill", action: "pause")
                actionButton("取消", icon: "xmark", action: "cancel")
            case "paused":
                actionButton("继续", icon: "play.fill", action: "resume", prominent: true)
                actionButton("取消", icon: "xmark", action: "cancel")
            case "failed", "interrupted":
                actionButton("重新开始", icon: "arrow.clockwise", action: "start", prominent: true)
            default:
                EmptyView()
            }
            actionButton("审计", icon: "checkmark.shield", action: "audit")
        }
        .disabled(store.isLoadingResearch)
    }

    private func actionButton(
        _ title: String,
        icon: String,
        action: String,
        prominent: Bool = false
    ) -> some View {
        Button {
            Task { await store.performResearchAction(action) }
        } label: {
            Label(title, systemImage: icon)
        }
        .buttonStyle(.bordered)
        .tint(prominent ? theme.accent : nil)
        .controlSize(.small)
    }

    private func timeline(goalId: String) -> some View {
        let events = store.researchEventsByGoal[goalId] ?? []
        return section("时间线", systemImage: "clock.arrow.circlepath") {
            VStack(alignment: .leading, spacing: 9) {
                if let issue = store.researchSequenceIssues[goalId] {
                    Label(issue, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                }
                if events.isEmpty {
                    Text("暂无研究事件")
                        .foregroundStyle(theme.textSecondary)
                } else {
                    ForEach(events) { event in
                        HStack(alignment: .top, spacing: 10) {
                            Text("#\(event.sequence)")
                                .font(KSSFont.themed(10.5, .bold, theme: theme))
                                .foregroundStyle(theme.accent)
                                .frame(width: 38, alignment: .leading)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(event.type)
                                    .fontWeight(.semibold)
                                Text(event.displayMessage)
                                    .foregroundStyle(theme.textSecondary)
                            }
                            Spacer()
                            Text(event.timestamp)
                                .font(KSSFont.themed(10.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                }
            }
        }
    }

    private func artifacts(_ artifacts: [ResearchArtifact]) -> some View {
        section("研究产物", systemImage: "doc.richtext") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(artifacts) { artifact in
                    HStack {
                        Button {
                            selectedArtifact = artifact
                        } label: {
                            Label(artifact.logicalName, systemImage: "doc")
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                        if let audit = artifact.auditStatus {
                            ResearchStatusLabel(status: audit)
                        }
                        Button("导出草稿") { chooseDraftDestination(for: artifact) }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                        Button("发布") { choosePublishDestination(for: artifact) }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)
                    }
                }
                if let artifact = selectedArtifact ?? artifacts.first {
                    ResearchArtifactPreview(artifact: artifact)
                        .frame(minHeight: 260)
                        .clipShape(RoundedRectangle(cornerRadius: 9))
                        .overlay(
                            RoundedRectangle(cornerRadius: 9)
                                .stroke(theme.hairline, lineWidth: 1)
                        )
                }
            }
        }
    }

    private func section<Content: View>(
        _ title: String,
        systemImage: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(KSSFont.themed(14, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            content()
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 14)
    }

    private var createSheet: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("新建深度研究")
                .font(KSSFont.themed(20, .bold, theme: theme))
            Text("目标会先创建为草稿，不会自动开始。")
                .foregroundStyle(theme.textSecondary)
            TextEditor(text: $objective)
                .font(KSSFont.themed(14, theme: theme))
                .frame(minHeight: 120)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(theme.hairline))
            Picker("研究配置", selection: $profileId) {
                if store.researchProfiles.isEmpty {
                    Text("投资周报 v3").tag("investment-weekly-v3")
                } else {
                    ForEach(store.researchProfiles) { profile in
                        Text(profile.name).tag(profile.profileId)
                    }
                }
            }
            HStack {
                Spacer()
                Button("取消") { showingCreate = false }
                Button("创建目标") {
                    let text = objective
                    objective = ""
                    showingCreate = false
                    Task { await store.createResearchGoal(objective: text, profileId: profileId) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(objective.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(22)
        .frame(width: 520)
    }

    private func chooseDraftDestination(for artifact: ResearchArtifact) {
        let panel = NSSavePanel()
        panel.title = "导出研究草稿"
        panel.nameFieldStringValue = artifact.logicalName
        panel.canCreateDirectories = true
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            Task { _ = await store.exportResearchDraft(artifact, destination: url.path) }
        }
    }

    private func choosePublishDestination(for artifact: ResearchArtifact) {
        let panel = NSSavePanel()
        panel.title = "正式发布研究产物"
        panel.nameFieldStringValue = artifact.logicalName
        panel.canCreateDirectories = true
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            publishDestination = url.path
            artifactToPublish = artifact
        }
    }
}

private struct ResearchStatusLabel: View {
    @Environment(\.kssTheme) private var theme
    let status: String

    var body: some View {
        Text(label)
            .font(KSSFont.themed(10.5, .bold, theme: theme))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(0.12), in: Capsule())
    }

    private var label: String {
        switch status.lowercased() {
        case "draft", "created": "待开始"
        case "pending": "等待中"
        case "running": "研究中"
        case "paused": "已暂停"
        case "completed", "succeeded", "met", "passed": "已完成"
        case "failed": "失败"
        case "cancelled", "aborted": "已取消"
        default: status
        }
    }

    private var color: Color {
        switch status.lowercased() {
        case "running", "completed", "succeeded", "met", "passed": theme.accent
        case "failed": .red
        case "paused", "pending": .orange
        default: theme.textSecondary
        }
    }
}

enum ResearchArtifactNavigationPolicy {
    static func allows(_ url: URL?) -> Bool {
        guard let url else { return true }
        return url.scheme?.lowercased() == "about" && url.absoluteString == "about:blank"
    }
}

private struct ResearchArtifactPreview: NSViewRepresentable {
    let artifact: ResearchArtifact

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground")
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let body: String
        if artifact.mediaType == "text/html", let content = artifact.content {
            body = content
        } else {
            let preview = artifact.content
                ?? "尚无内嵌预览。\n\n文件：\(artifact.logicalName)\n类型：\(artifact.mediaType ?? artifact.kind)\n路径：\(artifact.relativePath ?? "由后端管理")"
            body = "<pre>\(Self.escape(preview))</pre>"
        }
        webView.loadHTMLString("""
        <!doctype html><html><head><meta charset="utf-8">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
        <style>body{font:13px -apple-system;margin:18px;color:#25313c;background:transparent;line-height:1.55}
        pre{white-space:pre-wrap;word-break:break-word;font:13px ui-monospace,monospace}</style>
        </head><body>\(body)</body></html>
        """, baseURL: nil)
    }

    private static func escape(_ text: String) -> String {
        text.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            decisionHandler(
                ResearchArtifactNavigationPolicy.allows(navigationAction.request.url)
                    ? .allow : .cancel)
        }
    }
}
