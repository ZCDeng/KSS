import SwiftUI
import AppKit
import WebKit

struct ResearchWorkbenchView: View {
    @Environment(\.kssTheme) private var theme
    @ObservedObject var store: KSSStore
    @State private var showingCreate = false
    @State private var objective = ""
    @State private var profileId = "investment-weekly-v3"
    @State private var dateRange = ""
    @State private var asOf = ""
    @State private var useMultiAgentPilot = false
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
                    prepareCreateForm()
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
                        Task {
                            await store.createResearchGoal(
                                objective: candidate.objective,
                                profileId: candidate.profileId ?? "investment-weekly-v3",
                                executionMode: "single")
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
                    Button("新建目标") {
                        prepareCreateForm()
                        showingCreate = true
                    }
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
                    HStack(spacing: 8) {
                        Label(
                            goal.executionMode == "multi_agent_pilot"
                                ? "多 Agent 试验"
                                : "单 Agent",
                            systemImage: goal.executionMode == "multi_agent_pilot"
                                ? "person.3.sequence.fill"
                                : "person.fill"
                        )
                        if goal.executionMode == "multi_agent_pilot",
                           !goal.researchAgents.isEmpty {
                            Text("\(goal.researchAgents.count) 个研究角色")
                        }
                    }
                    .font(KSSFont.themed(10.5, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
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
                    if !goal.budget.isEmpty {
                        HStack(spacing: 14) {
                            Label(
                                "\(goal.usage["nodes"] ?? 0)/\(goal.budget["max_nodes"] ?? 0) 节点",
                                systemImage: "square.stack.3d.up")
                            Label(
                                "\(goal.usage["provider_tokens"] ?? 0)/\(goal.budget["max_provider_tokens"] ?? 0) tokens",
                                systemImage: "text.word.spacing")
                            Label(
                                "\(goal.usage["seconds"] ?? 0)/\(goal.budget["max_seconds"] ?? 0) 秒",
                                systemImage: "timer")
                        }
                        .font(KSSFont.themed(10.5, theme: theme))
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
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(task.title)
                                        if let agentId = task.agentId, !agentId.isEmpty {
                                            Text(agentId)
                                                .font(KSSFont.themed(10.5, .medium, theme: theme))
                                                .foregroundStyle(theme.textSecondary)
                                        }
                                    }
                                    Spacer()
                                    if ["failed", "incomplete", "interrupted", "blocked"]
                                        .contains(task.status.lowercased()) {
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

                if goal.executionMode == "multi_agent_pilot",
                   !goal.researchAgents.isEmpty {
                    section("研究角色", systemImage: "person.3.sequence.fill") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(goal.researchAgents) { agent in
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack {
                                        Text(agent.title)
                                            .fontWeight(.semibold)
                                        Text(agent.agentId)
                                            .foregroundStyle(theme.textSecondary)
                                        Spacer()
                                        if let tasks = agent.tasks {
                                            Text("\(agent.succeeded ?? 0)/\(tasks) 任务")
                                                .foregroundStyle(theme.textSecondary)
                                        }
                                    }
                                    if let focus = agent.focus ?? agent.role {
                                        Text(focus)
                                            .font(KSSFont.themed(11.5, theme: theme))
                                            .foregroundStyle(theme.textSecondary)
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
                                        if evidence.verified == true {
                                            Label("已验证", systemImage: "checkmark.seal.fill")
                                                .foregroundStyle(theme.accent)
                                        }
                                        Text(evidence.source ?? "未知来源")
                                            .foregroundStyle(theme.textSecondary)
                                    }
                                    HStack(spacing: 12) {
                                        if let tier = evidence.sourceTier {
                                            Text("来源等级：\(tier)")
                                        }
                                        if let asOf = evidence.dataAsOf {
                                            Text("数据时点：\(asOf)")
                                        }
                                    }
                                    .font(KSSFont.themed(10.5, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
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
            case "interrupted":
                actionButton("继续", icon: "play.fill", action: "resume", prominent: true)
            default:
                EmptyView()
            }
            if goal.status.lowercased() != "running" {
                actionButton(
                    "刷新数据",
                    icon: "arrow.triangle.2.circlepath",
                    action: "refresh_snapshot")
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
                            .disabled(
                                artifact.auditStatus?.lowercased() != "pass"
                                    || store.selectedResearchGoal?.status.lowercased()
                                        != "completed"
                            )
                    }
                }
                if let artifact = selectedArtifact ?? artifacts.first {
                    ResearchArtifactPreview(
                        artifact: artifact,
                        stateRoot: store.bridge?.stateRoot)
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
            if profileId == "investment-weekly-v3" {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                    GridRow {
                        Text("研究区间")
                            .foregroundStyle(theme.textSecondary)
                        TextField("YYYY-MM-DD_to_YYYY-MM-DD", text: $dateRange)
                            .textFieldStyle(.roundedBorder)
                    }
                    GridRow {
                        Text("统一时点")
                            .foregroundStyle(theme.textSecondary)
                        TextField("YYYY-MM-DD", text: $asOf)
                            .textFieldStyle(.roundedBorder)
                    }
                }
            }
            Toggle(isOn: $useMultiAgentPilot) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("启用多 Agent 研究试验")
                        .font(KSSFont.themed(12.5, .semibold, theme: theme))
                    Text("仅改变研究任务分工和后台调度；普通 Seesaw、写确认和金融工具顺序执行不变。")
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            .toggleStyle(.switch)
            HStack {
                Spacer()
                Button("取消") { showingCreate = false }
                Button("创建目标") {
                    let text = objective
                    let inputs = profileId == "investment-weekly-v3"
                        ? ["date_range": dateRange, "as_of": asOf]
                        : [:]
                    objective = ""
                    showingCreate = false
                    Task {
                        await store.createResearchGoal(
                            objective: text,
                            profileId: profileId,
                            executionMode: useMultiAgentPilot
                                ? "multi_agent_pilot"
                                : "single",
                            inputs: inputs)
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(
                    objective.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || (
                            profileId == "investment-weekly-v3"
                                && (
                                    dateRange.trimmingCharacters(
                                        in: .whitespacesAndNewlines
                                    ).isEmpty
                                        || asOf.trimmingCharacters(
                                            in: .whitespacesAndNewlines
                                        ).isEmpty
                                )
                        )
                )
            }
        }
        .padding(22)
        .frame(width: 520)
    }

    private func prepareCreateForm() {
        let defaults = KSSStore.defaultResearchInputs()
        dateRange = defaults["date_range"] ?? ""
        asOf = defaults["as_of"] ?? ""
        useMultiAgentPilot = false
    }

    private func chooseDraftDestination(for artifact: ResearchArtifact) {
        let panel = NSSavePanel()
        panel.title = "导出研究草稿"
        panel.nameFieldStringValue = artifact.logicalName
        panel.canCreateDirectories = true
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            // NSSavePanel already owns the user's replace confirmation.
            Task {
                _ = await store.exportResearchDraft(
                    artifact,
                    destination: url.path,
                    overwrite: true)
            }
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
        case "incomplete", "insufficient_evidence": "证据不足"
        case "blocked": "已阻塞"
        case "interrupted": "已中断"
        case "budget_limited": "预算已用尽"
        case "needs_refresh": "需要刷新"
        case "cancelled", "aborted": "已取消"
        default: status
        }
    }

    private var color: Color {
        switch status.lowercased() {
        case "running", "completed", "succeeded", "met", "passed": theme.accent
        case "failed", "blocked": .red
        case "paused", "pending", "incomplete", "insufficient_evidence",
             "interrupted", "budget_limited", "needs_refresh": .orange
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

enum ResearchArtifactPreviewLoader {
    static func load(relativePath: String?, under root: URL?) -> String? {
        guard let root, let relativePath, !relativePath.isEmpty,
              !relativePath.hasPrefix("/"),
              !relativePath.split(separator: "/").contains("..")
        else { return nil }
        let allowedExtensions = ["html", "htm", "md", "markdown", "txt", "json"]
        guard allowedExtensions.contains(URL(fileURLWithPath: relativePath).pathExtension.lowercased())
        else { return nil }
        let normalizedRoot = root.standardizedFileURL.resolvingSymlinksInPath()
        let candidate = normalizedRoot
            .appendingPathComponent(relativePath)
            .standardizedFileURL
            .resolvingSymlinksInPath()
        guard candidate.path == normalizedRoot.path
                || candidate.path.hasPrefix(normalizedRoot.path + "/"),
              let attributes = try? FileManager.default.attributesOfItem(atPath: candidate.path),
              let size = attributes[.size] as? NSNumber,
              size.intValue <= 2_000_000
        else { return nil }
        return try? String(contentsOf: candidate, encoding: .utf8)
    }
}

private struct ResearchArtifactPreview: NSViewRepresentable {
    let artifact: ResearchArtifact
    let stateRoot: URL?

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.underPageBackgroundColor = .clear
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let body: String
        let loadedContent = artifact.content
            ?? ResearchArtifactPreviewLoader.load(
                relativePath: artifact.relativePath,
                under: stateRoot)
        if artifact.mediaType == "text/html", let content = loadedContent {
            body = content
        } else {
            let preview = loadedContent
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
