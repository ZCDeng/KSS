import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

private extension String {
    var nilIfBlank: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

/// AI 复盘助手聊天面板。所有主题共用 Focus Layout；会话状态归 KSSStore，
/// 视觉切换不重建 Agent runtime。
struct AIChatView: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding private var globalNavigationExpanded: Bool
    @State private var input = ""
    @State private var activeOverlay: SeesawOverlay?
    @State private var sessionSearch = ""
    @State private var skillSearch = ""
    @State private var selectedSkillId: String?
    @State private var skillFilter: SkillFilter = .all
    @State private var memorySearch = ""
    @State private var showMemoryManagement = false
    @State private var pendingQueueClientMessageId: String?
    @State private var loadedQueueInputId: String?
    @State private var showAttachmentImporter = false
    @State private var seesawPage: SeesawPage = .conversation
    @State private var showInspectorDrawer = false
    @State private var expandedInspectorSections: Set<InspectorSection> = Set(InspectorSection.allCases)
    @State private var providerAPIKeyDraft = ""
    @State private var providerBaseURLDraft = ""
    @State private var providerManualModelDraft = ""
    @State private var providerThinkingDraft = "off"
    @State private var providerDetailMessage: String?
    @State private var providerDetailIsSaving = false
    @State private var providerDetailIsTesting = false
    @State private var showProviderAdvanced = false
    @State private var showComposerModelPopover = false
    @State private var customProviderIDDraft = ""
    @State private var customProviderNameDraft = ""
    @State private var customProviderBaseURLDraft = ""
    @State private var customProviderKeyDraft = ""
    @State private var customProviderModelsDraft = ""
    @State private var customProviderMessage: String?
    @State private var isSavingCustomProvider = false
    @State private var atFileResults: [WorkspaceFileHit] = []
    @State private var atFileSelection = 0
    @State private var atFileSearchTask: Task<Void, Never>?
    @State private var workbenchTab = "overview"
    // 实测反馈:默认收起,页面更干净;header 按钮可展开。
    @State private var showSessionPane = false
    @FocusState private var isComposerFocused: Bool
    @State private var hoveredMessageId: UUID?
    @State private var copiedMessageId: UUID?

    private enum SeesawOverlay: Equatable {
        case sessions
        case skills
        case context
    }

    private enum SeesawPage: Equatable {
        case conversation
        case models
        case providerDetail(String)
    }

    private enum InspectorSection: String, CaseIterable, Hashable {
        case progress
        case liveMarket
        case evidence
        case skills
        case context

        var title: String {
            switch self {
            case .progress: return "Progress"
            case .liveMarket: return "实时市场"
            case .evidence: return "Evidence & Artifacts"
            case .skills: return "Skills"
            case .context: return "记忆"
            }
        }
    }

    private enum SkillFilter: String, CaseIterable, Identifiable {
        case all = "全部"
        case enabled = "已启用"
        case inConversation = "本会话"

        var id: String { rawValue }
    }

    private struct SkillStarter: Identifiable {
        let skillId: String
        let icon: String
        let title: String
        let prompt: String

        var id: String { skillId }
    }

    private struct ComposerModelOption {
        let provider: AgentProviderDescriptor
        let model: AgentModelDescriptor

        var routeID: String {
            KSSStore.seesawModelRouteID(providerID: provider.id, modelID: model.id)
        }

        var label: String { "\(provider.displayName) · \(model.name ?? model.id)" }
    }

    private let skillStarters: [SkillStarter] = [
        .init(skillId: "kss-review", icon: "chart.line.uptrend.xyaxis", title: "个股复盘", prompt: "688008 今天为什么动"),
        .init(skillId: "longbridge-realtime", icon: "waveform.path.ecg", title: "今日盘面", prompt: "今天大盘总体怎么样"),
        .init(skillId: "kss-indicator-pipeline", icon: "function", title: "指标研究", prompt: "研究一下 RSI 阈值，先说明可回测的数据与约束"),
        .init(skillId: "kss-orientation", icon: "books.vertical", title: "数据上手", prompt: "这个仓库有哪些数据和可用工具，先帮我上手")
    ]

    init(globalNavigationExpanded: Binding<Bool>) {
        _globalNavigationExpanded = globalNavigationExpanded
    }

    var body: some View {
        GeometryReader { geo in
            focusSeesawShell(size: geo.size)
            .onAppear { Task { await store.loadAgentBootstrap() } }
            .onAppear { applySeesawDestination() }
            .onAppear { globalNavigationExpanded = false }
            .onAppear { consumeComposerPrefill() }
            .onDisappear {
                activeOverlay = nil
                globalNavigationExpanded = false
            }
            .onChange(of: store.chatComposerPrefill) { _, _ in
                consumeComposerPrefill()
            }
            .onChange(of: store.selectedAgentSessionId) { _, _ in
                activeOverlay = nil
                isComposerFocused = true
            }
            .onChange(of: store.seesawDestination) { _, _ in
                applySeesawDestination()
            }
            .onChange(of: store.agentQueueAcknowledgement) { _, acknowledgement in
                guard let acknowledgement,
                      acknowledgement.clientMessageId == pendingQueueClientMessageId
                else { return }
                if KSSStore.shouldClearQueuedEditor(
                    acknowledgement: acknowledgement,
                    pendingClientMessageId: pendingQueueClientMessageId
                ) {
                    input = ""
                    loadedQueueInputId = nil
                }
                pendingQueueClientMessageId = nil
            }
            .onChange(of: store.isChatStreaming) { _, isStreaming in
                if !isStreaming {
                    // A stop or terminal failure may race the queue acknowledgement.
                    // Keep the editor text, but release the local send gate so the
                    // user can explicitly resend a restored or rejected input.
                    pendingQueueClientMessageId = nil
                }
            }
            .fileImporter(
                isPresented: $showAttachmentImporter,
                allowedContentTypes: Self.supportedAttachmentTypes,
                allowsMultipleSelection: true
            ) { result in
                switch result {
                case .success(let urls):
                    Task { await store.importAgentAttachments(urls) }
                case .failure(let error):
                    store.agentAttachmentError = error.localizedDescription
                }
            }
        }
    }

    private static let supportedAttachmentTypes: [UTType] = {
        var types: [UTType] = [.image, .pdf, .plainText, .commaSeparatedText]
        if let markdown = UTType(filenameExtension: "md") {
            types.append(markdown)
        }
        return types
    }()

    // MARK: - Focus Layout

    /// One conversation column for every theme. Session history, skills and memory
    /// are overlays so they never compete with the active prompt for horizontal space.
    private func focusSeesawShell(size: CGSize) -> some View {
        let compact = size.width < SeesawXcomChrome.compactContentWidth
        let persistentInspector = size.width >= 1180
        let persistentSessionPane = size.width >= 1360

        return ZStack {
            theme.canvas.ignoresSafeArea()

            VStack(spacing: 0) {
                focusHeader(
                    compact: compact,
                    persistentInspector: persistentInspector,
                    persistentSessionPane: persistentSessionPane
                )

                switch seesawPage {
                case .conversation:
                    HStack(spacing: 0) {
                        if persistentSessionPane, showSessionPane {
                            SeesawSessionPane()
                                .frame(width: 236)
                                .transition(.move(edge: .leading).combined(with: .opacity))
                            Divider().overlay(theme.hairline)
                        }

                        focusConversationWorkspace
                        .frame(maxWidth: SeesawXcomChrome.feedColumnWidth)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                        if persistentInspector {
                            Divider().overlay(theme.hairline)
                            focusWorkbench(showsClose: false)
                                .frame(width: 340)
                                .transition(inspectorTransition)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .models:
                    seesawModelsPage
                case .providerDetail(let providerID):
                    seesawProviderDetail(providerID)
                }
            }

            if !isInModelsWorkspace, !persistentInspector, showInspectorDrawer {
                HStack(spacing: 0) {
                    Spacer(minLength: 0)
                    focusWorkbench(showsClose: true)
                        .frame(width: min(360, max(300, size.width * 0.82)))
                        .transition(inspectorTransition)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .trailing)
                .background(alignment: .trailing) {
                    Rectangle()
                        .fill(theme.canvas.opacity(0.94))
                        .frame(width: min(360, max(300, size.width * 0.82)))
                        .shadow(color: .black.opacity(0.14), radius: 14, x: -4)
                }
            }

            if !isInModelsWorkspace, let overlay = activeOverlay {
                focusOverlaySurface(overlay, size: size)
                    .zIndex(2)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(reduceMotion ? .easeOut(duration: 0.12) : .spring(response: 0.28, dampingFraction: 0.9), value: showInspectorDrawer)
        .onExitCommand {
            if activeOverlay != nil { activeOverlay = nil }
            else if showInspectorDrawer { showInspectorDrawer = false }
            else if case .providerDetail = seesawPage { seesawPage = .models }
            else if seesawPage == .models { seesawPage = .conversation }
        }
    }

    private func focusOverlaySurface(_ overlay: SeesawOverlay, size: CGSize) -> some View {
        let overlaySize = focusOverlaySize(for: overlay, in: size)
        return ZStack {
            // Keep the Composer visually stable while a utility workspace is open.
            // A transparent hit surface still lets a click outside close the panel,
            // without making the entire conversation appear to fade away.
            Color.clear
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { activeOverlay = nil }

            focusOverlayContent(overlay)
                .frame(width: overlaySize.width, height: overlaySize.height)
                .background(theme.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(theme.hairline)
                }
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                .shadow(color: .black.opacity(theme.appearance == .dark ? 0.30 : 0.18), radius: 28, y: 12)
                .padding(24)
        }
        .accessibilityAddTraits(.isModal)
    }

    private func focusOverlaySize(for overlay: SeesawOverlay, in size: CGSize) -> CGSize {
        let horizontalInset: CGFloat = size.width >= 1180 ? 96 : 24
        let width = min(940, max(360, size.width - horizontalInset))
        let preferredHeight = min(580, max(390, size.height - 140))
        // Skills and Context are desktop workspaces, not tall phone popovers.
        // Keep their height below a 0.68 aspect ratio so the content reads laterally.
        let height = min(preferredHeight, width * 0.68)
        return CGSize(width: width, height: height)
    }

    @ViewBuilder
    private func focusOverlayContent(_ overlay: SeesawOverlay) -> some View {
        switch overlay {
        case .sessions:
            focusSessionPalette
        case .skills:
            focusSkillPalette
        case .context:
            focusContextPopover
        }
    }

    private var inspectorTransition: AnyTransition {
        reduceMotion ? .opacity : .move(edge: .trailing).combined(with: .opacity)
    }

    private var isInModelsWorkspace: Bool {
        if case .conversation = seesawPage { return false }
        return true
    }

    private func focusHeader(
        compact: Bool,
        persistentInspector: Bool,
        persistentSessionPane: Bool = false
    ) -> some View {
        HStack(spacing: 8) {
            Button {
                withAnimation(.easeOut(duration: 0.16)) {
                    globalNavigationExpanded.toggle()
                }
            } label: {
                Label(
                    globalNavigationExpanded ? "收起导航" : "展开导航",
                    systemImage: globalNavigationExpanded ? "sidebar.left" : "sidebar.right"
                )
                .labelStyle(.iconOnly)
                .frame(width: 36, height: 36)
                .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.textSecondary)
            .help(globalNavigationExpanded ? "收起全局导航" : "展开全局导航")

            if persistentSessionPane, !isInModelsWorkspace {
                Button {
                    withAnimation(.easeOut(duration: 0.16)) { showSessionPane.toggle() }
                } label: {
                    Label("会话列表", systemImage: "list.bullet.rectangle")
                        .labelStyle(.iconOnly)
                        .frame(width: 36, height: 36)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(showSessionPane ? theme.accent : theme.textSecondary)
                .help(showSessionPane ? "收起会话列表" : "展开会话列表")
            }

            Button { toggleOverlay(.sessions) } label: {
                HStack(spacing: 6) {
                    Text(store.agentSessions.first { $0.sessionId == store.selectedAgentSessionId }?.title ?? "新会话")
                        .font(KSSFont.themed(16, .bold, theme: theme))
                        .lineLimit(1)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 10, weight: .semibold))
                }
                .foregroundStyle(theme.textPrimary)
                .frame(maxWidth: compact ? 170 : 280, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("会话")

            Spacer(minLength: 8)

            if isInModelsWorkspace {
                Button { seesawPage = .conversation } label: {
                    Label("返回对话", systemImage: "chevron.left")
                        .font(KSSFont.themed(12.5, .semibold, theme: theme))
                        .padding(.horizontal, compact ? 0 : 9)
                        .frame(height: 36)
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
                .help("返回对话")
            } else if !persistentInspector {
                Button { showInspectorDrawer.toggle() } label: {
                    Label("执行面板", systemImage: "rectangle.rightthird.inset.filled")
                        .labelStyle(.iconOnly)
                        .frame(width: 36, height: 36)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(showInspectorDrawer ? theme.accent : theme.textSecondary)
                .help("执行面板")
            }

            if let usage = store.agentContextUsage, !isInModelsWorkspace {
                Text(usage.displayText)
                    .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 8)
                    .frame(height: 24)
                    .background(theme.surfaceContainer, in: Capsule())
                    .help("上下文用量（实时 token 统计）")
            }

            if store.isChatStreaming {
                Button { store.stopChatGeneration() } label: {
                    Label("停止", systemImage: "stop.fill")
                        .labelStyle(.iconOnly)
                        .frame(width: 36, height: 36)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.red)
                .help("停止生成")
            }
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .frame(height: SeesawXcomChrome.headerHeight)
        .background(theme.surface.opacity(0.97))
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline.opacity(0.85)).frame(height: 1)
        }
    }

    // MARK: - OpenWorker-style inspector and Models workspace

    private var hasEvidenceOrAttachments: Bool {
        !store.pendingAgentAttachments.isEmpty || store.chatMessages.contains { $0.evidenceSummary.hasEvidence }
    }

    /// 工作台外壳：注册制 tab（概览 / 文件 / 运行）。新增 tab 只需在
    /// `workbenchTabs` 里追加 spec（better-sidebar registerTab 语义）。
    private func focusWorkbench(showsClose: Bool) -> some View {
        SeesawWorkbenchSidebar(
            tabs: workbenchTabs,
            selection: $workbenchTab,
            showsCloseButton: showsClose,
            onClose: { showInspectorDrawer = false }
        )
    }

    private var workbenchTabs: [SeesawWorkbenchTabSpec] {
        [
            SeesawWorkbenchTabSpec(id: "overview", title: "概览", icon: "rectangle.grid.1x2") {
                focusInspector
            },
            SeesawWorkbenchTabSpec(
                id: "files",
                title: "文件",
                icon: "folder",
                badge: store.pendingFileRefs.isEmpty ? nil : store.pendingFileRefs.count
            ) {
                SeesawFilesTab()
            },
            SeesawWorkbenchTabSpec(
                id: "runs",
                title: "运行",
                icon: "point.3.connected.trianglepath.dotted",
                badge: store.researchGoals.isEmpty ? nil : store.researchGoals.count
            ) {
                SeesawRunsTab()
            },
        ]
    }

    private var focusInspector: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if store.isChatStreaming || store.agentSteeringCount + store.agentFollowUpCount > 0 {
                    inspectorSection(.progress, systemImage: "circle.dotted.circle") {
                        if store.isChatStreaming {
                            Label(store.chatToolInProgress.map { "正在调用 \($0)" } ?? "模型正在生成", systemImage: "circle.dotted.circle")
                                .foregroundStyle(theme.accent)
                        }
                        if store.agentSteeringCount + store.agentFollowUpCount > 0 {
                            Text("队列：\(store.agentSteeringCount) 条补充 · \(store.agentFollowUpCount) 条追问")
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                }

                if !store.agentLiveMarketContexts.isEmpty {
                    inspectorSection(.liveMarket, systemImage: "waveform.path.ecg") {
                        if let live = store.agentLiveMarketContexts.last {
                            Text(live.coverageText)
                                .foregroundStyle(theme.textSecondary)
                            ForEach(live.rows.prefix(4)) { row in
                                HStack(spacing: 8) {
                                    Text(row.symbol)
                                        .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                                    Spacer(minLength: 0)
                                    if let last = row.quote?.lastDone {
                                        Text(String(format: "%.2f", last))
                                            .font(.system(size: 11.5, weight: .semibold, design: .monospaced))
                                    } else {
                                        Text(row.quote?.error ?? "未覆盖")
                                            .font(KSSFont.themed(11, theme: theme))
                                            .foregroundStyle(Color.orange)
                                    }
                                }
                                .foregroundStyle(theme.textPrimary)
                            }
                            if let asOf = live.sourceAsOf {
                                Text("数据时点 · \(asOf)")
                                    .foregroundStyle(theme.textSecondary)
                            }
                            Text("Longbridge 只读 · forward-observed · 北交所不覆盖")
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                }

                if hasEvidenceOrAttachments {
                    inspectorSection(.evidence, systemImage: "paperclip") {
                        if !store.pendingAgentAttachments.isEmpty {
                            Text("附件 \(store.pendingAgentAttachments.count) 个")
                                .foregroundStyle(theme.textSecondary)
                        }
                        let evidenceCount = store.chatMessages.filter { $0.evidenceSummary.hasEvidence }.count
                        if evidenceCount > 0 {
                            Text("\(evidenceCount) 条回复包含可展开的证据来源")
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                }

                inspectorSection(.skills, systemImage: "slider.horizontal.3", opens: .skills) {
                    Text("\(sessionSkills.count) 个本会话技能 · \(enabledSkillCount) 个启用")
                        .foregroundStyle(theme.textSecondary)
                    if !sessionSkills.isEmpty {
                        ForEach(sessionSkills) { skill in
                            Text(skill.name)
                                .font(KSSFont.themed(12, .semibold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                        }
                    }
                    Button("浏览 Skills…") { toggleOverlay(.skills) }
                        .buttonStyle(.borderless)
                        .foregroundStyle(theme.accent)
                }

                inspectorSection(.context, systemImage: "brain", opens: .context) {
                    if !store.agentSourceRecalls.isEmpty {
                        Text("本轮召回 \(store.agentSourceRecalls.count) 条记忆")
                            .foregroundStyle(theme.textSecondary)
                    }
                    Text("长期记忆:跨会话可复用的事实,发送时自动召回进上下文。")
                        .foregroundStyle(theme.textSecondary)
                    Button("管理记忆…") { toggleOverlay(.context) }
                        .buttonStyle(.borderless)
                        .foregroundStyle(theme.accent)
                }
            }
            .padding(.bottom, 12)
        }
        .font(KSSFont.themed(12, theme: theme))
        .background(theme.surface)
    }

    private func toggleInspectorSection(_ section: InspectorSection) {
        if expandedInspectorSections.contains(section) {
            expandedInspectorSections.remove(section)
        } else {
            expandedInspectorSections.insert(section)
        }
    }

    private func inspectorSection<Content: View>(
        _ section: InspectorSection,
        systemImage: String,
        opens overlay: SeesawOverlay? = nil,
        @ViewBuilder content: @escaping () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                if let overlay {
                    showInspectorDrawer = false
                    activeOverlay = overlay
                } else {
                    toggleInspectorSection(section)
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: systemImage)
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 18, alignment: .center)
                    Text(section.title)
                        .font(KSSFont.themed(13, .bold, theme: theme))
                    Spacer(minLength: 0)
                    Image(systemName: overlay == nil
                          ? (expandedInspectorSections.contains(section) ? "chevron.up" : "chevron.down")
                          : "arrow.up.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(theme.textSecondary)
                }
                .foregroundStyle(theme.textPrimary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expandedInspectorSections.contains(section) {
                content()
                    .padding(.leading, 28)
                    .padding(.top, 10)
                    .font(KSSFont.themed(11.5, theme: theme))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.vertical, 15)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var seesawModelsPage: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Models")
                        .font(KSSFont.themed(25, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text("配置只影响 Seesaw。密钥保存在本机 Keychain，当前会话可单独选择模型。")
                        .font(KSSFont.themed(13.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }

                sessionModelCard

                VStack(alignment: .leading, spacing: 10) {
                    Text("可用 Provider")
                        .font(KSSFont.themed(14, .bold, theme: theme))
                    if store.agentProviders.isEmpty {
                        Text("正在读取本机凭据与模型目录…")
                            .font(KSSFont.themed(12.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 205), spacing: 12)], spacing: 12) {
                            ForEach(sortedCatalogProviders) { provider in
                                providerCatalogCard(provider)
                            }
                        }
                    }
                }

                customProviderCard

                VStack(alignment: .leading, spacing: 9) {
                    Text("路由规则")
                        .font(KSSFont.themed(14, .bold, theme: theme))
                    Text("全局默认只影响新会话；当前会话的模型选择独立保存。全局备用模型只会在主模型尚未输出正文、Thinking 或工具调用时接管。视觉模型是独立槽位，仅供图片理解工具（vision_analyze）路由。")
                        .font(KSSFont.themed(12.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    HStack(spacing: 12) {
                        routeSummary(title: "全局默认", route: store.agentGlobalPrimaryRoute)
                        routeSummary(title: "全局备用", route: store.agentFallbackRoute)
                        visionRouteSummary
                    }
                }
                .padding(16)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(theme.hairline))
            }
            .frame(maxWidth: 840, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.horizontal, 32)
            .padding(.vertical, 30)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
        .task { await store.loadAgentProviders(reloadCredentials: true) }
    }

    private var sessionModelCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("本会话模型")
                        .font(KSSFont.themed(14, .bold, theme: theme))
                    Text("新会话继承全局默认；切换仅会在下一次发送时生效。")
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer()
                if store.isChatStreaming {
                    Text("生成中不可切换")
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                        .foregroundStyle(Color.orange)
                }
            }

            Menu {
                ForEach(sortedCatalogProviders.filter(providerHasCredential)) { provider in
                    if let models = provider.models, !models.isEmpty {
                        Section(provider.displayName) {
                            ForEach(models.filter { store.isSeesawModelVisible(providerID: provider.id, modelID: $0.id) }) { model in
                                Button(model.name ?? model.id) {
                                    selectSessionRoute(provider: provider, model: model)
                                }
                                .disabled(store.isChatStreaming)
                            }
                        }
                    }
                }
            } label: {
                HStack {
                    Text(providerComposerLabel.isEmpty ? "选择模型" : providerComposerLabel)
                    Spacer()
                    Image(systemName: "chevron.up.chevron.down")
                }
                .font(KSSFont.themed(13, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .padding(.horizontal, 12)
                .frame(height: 38)
                .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 10))
            }
            .menuStyle(.borderlessButton)
            .disabled(store.isChatStreaming || store.agentProviders.isEmpty)

            sessionThinkingRow

            HStack(spacing: 10) {
                Button("设为新会话默认") {
                    if let route = store.agentPrimaryRoute { store.setAgentGlobalDefaultRoute(route) }
                }
                .buttonStyle(.bordered)
                .disabled(store.isChatStreaming || store.agentPrimaryRoute == nil)
                Button("测试当前连接") { Task { await store.testAgentProviderConnection() } }
                    .buttonStyle(.bordered)
                readinessBadge
            }
        }
        .padding(16)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(theme.hairline))
    }

    private func routeSummary(title: String, route: AgentProviderRoute?) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Text(routeDisplayName(route))
                .font(KSSFont.themed(12.5, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func routeDisplayName(_ route: AgentProviderRoute?) -> String {
        guard let route else { return "尚未配置" }
        let provider = route.providerId?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let friendly = provider.flatMap { id in
            id.isEmpty ? nil : AgentProviderDescriptor.friendlyProviderName(id: id)
        }
        let label = [friendly, route.modelId?.trimmingCharacters(in: .whitespacesAndNewlines)]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return label.isEmpty ? "尚未配置" : label
    }

    /// 目录排序:有凭证在前;DeepSeek 官方 > 自定义 > 其他。
    private var sortedCatalogProviders: [AgentProviderDescriptor] {
        store.agentProviders.sorted { providerSortKey($0) < providerSortKey($1) }
    }

    private func providerSortKey(_ provider: AgentProviderDescriptor) -> (Int, Int, String) {
        let credential = providerHasCredential(provider) ? 0 : 1
        let family: Int
        if provider.id == "deepseek-official" {
            family = 0
        } else if provider.custom == true {
            family = 1
        } else {
            family = 2
        }
        return (credential, family, provider.id)
    }

    private func providerCatalogCard(_ provider: AgentProviderDescriptor) -> some View {
        let isCurrent = provider.id == store.agentPrimaryRoute?.providerId
        return Button {
            seesawPage = .providerDetail(provider.id)
        } label: {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 8) {
                    Circle().fill(provider.authenticated == true ? theme.accent : theme.textSecondary.opacity(0.45))
                        .frame(width: 8, height: 8)
                    Text(provider.displayName)
                        .font(KSSFont.themed(13, .bold, theme: theme))
                    Spacer()
                    if isCurrent { Text("当前") .font(KSSFont.themed(10.5, .semibold, theme: theme)).foregroundStyle(theme.accent) }
                }
                Text(providerConnectionLabel(provider))
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Text("\(provider.models?.count ?? 0) 个可用模型")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            .frame(maxWidth: .infinity, minHeight: 82, alignment: .leading)
            .padding(13)
            .background(isCurrent ? theme.accentSoft : theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(theme.hairline))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("配置 \(provider.displayName)")
    }

    private func providerConnectionLabel(_ provider: AgentProviderDescriptor) -> String {
        if provider.id == store.agentPrimaryRoute?.providerId {
            switch store.seesawProviderReadiness {
            case .ready: return "已连接"
            case .configuredUntested: return "已保存 · 待测试"
            case .brokerLoading: return "读取凭据中"
            case .missingCredential: return "未配置"
            case .missingRoute: return "未选择模型"
            case .failed: return "最近测试失败"
            }
        }
        return provider.authenticated == true ? "已保存凭据" : "未配置"
    }

    private func seesawProviderDetail(_ providerID: String) -> some View {
        let provider = store.agentProviders.first(where: { $0.id == providerID })
        let models = provider?.models ?? []
        let title = provider?.displayName
            ?? AgentProviderDescriptor.friendlyProviderName(id: providerID)
        let isCurrent = providerID == store.agentPrimaryRoute?.providerId

        return ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Button {
                    seesawPage = .models
                } label: {
                    Label("所有 Provider", systemImage: "chevron.left")
                        .font(KSSFont.themed(13, .semibold, theme: theme))
                        .foregroundStyle(theme.accent)
                }
                .buttonStyle(.plain)

                VStack(alignment: .leading, spacing: 7) {
                    Text(title)
                        .font(KSSFont.themed(25, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text(provider?.authenticated == true
                         ? "凭据已载入 Credential Broker；可选择模型后测试。"
                         : "在此安全保存 API Key。密钥只写入 macOS Keychain，不会进入聊天记录、日志或 Python 环境。")
                        .font(KSSFont.themed(13.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("连接")
                        .font(KSSFont.themed(14, .bold, theme: theme))
                    HStack(spacing: 10) {
                        SecureField(
                            KeychainStore.readProviderAPIKey(providerID) == nil ? "API Key" : "已保存的 API Key（输入可替换）",
                            text: $providerAPIKeyDraft
                        )
                        .textFieldStyle(.roundedBorder)
                        Button(providerDetailIsSaving ? "保存中…" : "保存 Key") {
                            saveProviderCredential(providerID)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(providerDetailIsSaving || providerAPIKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                    if let providerDetailMessage {
                        Text(providerDetailMessage)
                            .font(KSSFont.themed(11.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                    DisclosureGroup("高级端点", isExpanded: $showProviderAdvanced) {
                        VStack(alignment: .leading, spacing: 8) {
                            TextField("Base URL（仅自定义 OpenAI-compatible 网关需要）", text: $providerBaseURLDraft)
                                .textFieldStyle(.roundedBorder)
                            Text("默认保持 Provider 目录的端点。修改端点仅改变此 Provider 路由，不会复制或暴露 API Key。")
                                .font(KSSFont.themed(11.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                        .padding(.top, 4)
                    }
                    .font(KSSFont.themed(12.5, .semibold, theme: theme))

                    providerThinkingPicker(providerID)
                }
                .padding(16)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(theme.hairline))

                detailFact("当前状态", value: isCurrent ? "此会话正在使用此 Provider" : "可设为本会话、新会话默认或全局备用")

                VStack(alignment: .leading, spacing: 10) {
                    Text("模型")
                        .font(KSSFont.themed(14, .bold, theme: theme))
                    if models.isEmpty {
                        manualModelRouteControls(providerID: providerID, provider: provider)
                    } else {
                        ForEach(models) { model in
                            providerModelRow(provider: provider, model: model, isCurrent: isCurrent)
                            Divider().overlay(theme.hairline)
                        }

                        manualModelRouteControls(providerID: providerID, provider: provider)
                    }
                }

                HStack(spacing: 10) {
                    Button(providerDetailIsTesting ? "测试中…" : "测试当前路由") {
                        providerDetailIsTesting = true
                        Task {
                            await store.testAgentProviderConnection(route: providerDetailTestRoute(providerID))
                            providerDetailIsTesting = false
                        }
                    }
                        .buttonStyle(.bordered)
                    .disabled(providerDetailIsTesting || store.isChatStreaming || providerDetailTestRoute(providerID) == nil)
                    Button("设为新会话默认") {
                        if let route = routeForProvider(providerID, modelID: store.agentPrimaryRoute?.modelId) {
                            store.setAgentGlobalDefaultRoute(route)
                        }
                    }
                        .buttonStyle(.bordered)
                    .disabled(store.isChatStreaming || !hasSelectableModel(providerID: providerID))
                }

                if KeychainStore.readProviderAPIKey(providerID) != nil {
                    Button(role: .destructive) {
                        _ = KeychainStore.writeProviderAPIKey(providerID, "")
                        Task { await store.loadAgentProviders(reloadCredentials: true) }
                    } label: {
                        Label("移除已保存的 \(title) API Key", systemImage: "trash")
                    }
                    .buttonStyle(.bordered)
                    .help("只移除此 Provider 的 scoped Key；不会删除其他 Provider 或历史 Keychain 项")
                }

                if provider?.custom == true {
                    Button(role: .destructive) {
                        Task {
                            customProviderMessage = await store.removeCustomProvider(providerID)
                            seesawPage = .models
                        }
                    } label: {
                        Label("移除此自定义 Provider", systemImage: "trash.slash")
                    }
                    .buttonStyle(.bordered)
                    .help("从 DSH settings.yaml 移除该 provider 并重启内核；同时清除其 Keychain Key")
                }
            }
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.horizontal, 32)
            .padding(.vertical, 30)
        }
        .scrollContentBackground(.hidden)
        .background(theme.canvas)
        .onAppear { hydrateProviderDetail(providerID, provider: provider) }
    }

    @ViewBuilder
    private func providerModelRow(
        provider: AgentProviderDescriptor?,
        model: AgentModelDescriptor,
        isCurrent: Bool
    ) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(model.name ?? model.id)
                    .font(KSSFont.themed(13, .semibold, theme: theme))
                Text(model.id)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                modelCapabilityBadges(model)
            }
            Spacer()
            if model.id == store.agentPrimaryRoute?.modelId && isCurrent {
                Text("当前")
                    .font(KSSFont.themed(11, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
            }
            Toggle(
                "在 Composer 中显示",
                isOn: Binding(
                    get: {
                        guard let provider else { return false }
                        return store.isSeesawModelVisible(providerID: provider.id, modelID: model.id)
                    },
                    set: { visible in
                        if let provider {
                            store.setSeesawModelVisible(providerID: provider.id, modelID: model.id, visible: visible)
                        }
                    }
                )
            )
            .toggleStyle(.checkbox)
            .font(KSSFont.themed(11.5, theme: theme))
            Menu {
                Button("用于此会话") {
                    if let provider { selectSessionRoute(provider: provider, model: model) }
                }
                Button("设为新会话默认") {
                    if let provider { store.setAgentGlobalDefaultRoute(routeFor(provider: provider, model: model)) }
                }
                Button("设为全局备用") {
                    if let provider { store.setAgentFallbackRoute(routeFor(provider: provider, model: model)) }
                }
                if modelSupportsVision(model) {
                    Button("设为视觉模型") {
                        if let provider { store.setAgentVisionRoute(routeFor(provider: provider, model: model)) }
                    }
                }
            } label: {
                Label("使用", systemImage: "chevron.down")
                    .font(KSSFont.themed(11.5, .semibold, theme: theme))
            }
            .menuStyle(.borderlessButton)
            .disabled(store.isChatStreaming)
        }
        .padding(.vertical, 9)
    }

    @ViewBuilder
    private func manualModelRouteControls(providerID: String, provider: AgentProviderDescriptor?) -> some View {
        HStack(spacing: 10) {
            TextField("手动模型 ID", text: $providerManualModelDraft)
                .textFieldStyle(.roundedBorder)
            Button("用于此会话") {
                if let route = routeForProvider(providerID, modelID: providerManualModelDraft) {
                    store.setAgentSessionProviderRoute(route)
                }
            }
            .buttonStyle(.bordered)
            .disabled(store.isChatStreaming || providerManualModelDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        Text(provider == nil ? "自定义 OpenAI-compatible 端点可填写模型 ID 后保存给当前会话。" : "也可填写目录之外、由该 Provider 明确支持的模型 ID。")
            .font(KSSFont.themed(11.5, theme: theme))
            .foregroundStyle(theme.textSecondary)
    }

    private func routeFor(provider: AgentProviderDescriptor, model: AgentModelDescriptor) -> AgentProviderRoute {
        AgentProviderRoute(
            providerId: provider.id,
            modelId: model.id,
            baseURL: providerBaseURLDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? provider.baseURL : providerBaseURLDraft,
            thinkingLevel: providerThinkingDraft,
            contextWindow: model.contextWindow,
            maxOutputTokens: model.maxOutputTokens,
            supportsImages: model.supportsImages,
            supportsTools: model.supportsTools,
            supportsThinking: model.supportsThinking
        )
    }

    private func providerHasCredential(_ provider: AgentProviderDescriptor) -> Bool {
        provider.authenticated == true
            || KeychainStore.hasLLMCredential(forProviderID: provider.id)
    }

    private func providerDetailTestRoute(_ providerID: String) -> AgentProviderRoute? {
        let selectedModelID = store.agentPrimaryRoute?.providerId == providerID
            ? store.agentPrimaryRoute?.modelId
            : store.agentProviders.first(where: { $0.id == providerID })?.models?.first?.id
        return routeForProvider(providerID, modelID: selectedModelID)
    }

    private func routeForProvider(_ providerID: String, modelID: String?) -> AgentProviderRoute? {
        let resolvedModelID = (modelID ?? providerManualModelDraft).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedModelID.isEmpty else { return nil }
        if let provider = store.agentProviders.first(where: { $0.id == providerID }),
           let model = provider.models?.first(where: { $0.id == resolvedModelID }) {
            return routeFor(provider: provider, model: model)
        }
        return AgentProviderRoute(
            providerId: providerID,
            modelId: resolvedModelID,
            baseURL: providerBaseURLDraft.trimmingCharacters(in: .whitespacesAndNewlines).nilIfBlank,
            thinkingLevel: providerThinkingDraft
        )
    }

    private func hasSelectableModel(providerID: String) -> Bool {
        !(store.agentProviders.first(where: { $0.id == providerID })?.models ?? []).isEmpty
            || !providerManualModelDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func hydrateProviderDetail(_ providerID: String, provider: AgentProviderDescriptor?) {
        providerAPIKeyDraft = ""
        providerBaseURLDraft = store.agentPrimaryRoute?.providerId == providerID
            ? (store.agentPrimaryRoute?.baseURL ?? provider?.baseURL ?? "")
            : (provider?.baseURL ?? "")
        providerManualModelDraft = ""
        providerThinkingDraft = store.agentPrimaryRoute?.providerId == providerID
            ? (store.agentPrimaryRoute?.thinkingLevel ?? "off")
            : "off"
        providerDetailMessage = KeychainStore.readProviderAPIKey(providerID) == nil ? nil : "此 Provider 已保存 Key；重新输入可替换。"
        showProviderAdvanced = false
    }

    private func saveProviderCredential(_ providerID: String) {
        let key = providerAPIKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        providerDetailIsSaving = true
        guard KeychainStore.writeProviderAPIKey(providerID, key) else {
            providerDetailMessage = "无法写入 macOS Keychain。请检查钥匙串访问权限。"
            providerDetailIsSaving = false
            return
        }
        providerAPIKeyDraft = ""
        Task {
            await store.loadAgentProviders(reloadCredentials: true)
            providerDetailIsSaving = false
            providerDetailMessage = "已安全保存到 Keychain；可选择模型并运行连接测试。"
        }
    }

    private func detailFact(_ label: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 78, alignment: .leading)
            Text(value)
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .textSelection(.enabled)
        }
    }

    private var readinessBadge: some View {
        let text: String
        let tint: Color
        switch store.seesawProviderReadiness {
        case .ready: text = "已连接"; tint = theme.accent
        case .configuredUntested: text = "待测试"; tint = theme.textSecondary
        case .brokerLoading: text = "读取中"; tint = theme.textSecondary
        case .missingCredential: text = "缺少密钥"; tint = Color.orange
        case .missingRoute: text = "未选模型"; tint = Color.orange
        case .failed: text = "连接失败"; tint = Color.red
        }
        return Text(text)
            .font(KSSFont.themed(11.5, .semibold, theme: theme))
            .foregroundStyle(tint)
    }

    private func selectSessionRoute(provider: AgentProviderDescriptor, model: AgentModelDescriptor) {
        let isEditingProvider: Bool
        if case let .providerDetail(providerID) = seesawPage {
            isEditingProvider = providerID == provider.id
        } else {
            isEditingProvider = false
        }
        let route = isEditingProvider
            ? routeFor(provider: provider, model: model)
            : AgentProviderRoute(
                providerId: provider.id,
                modelId: model.id,
                baseURL: provider.baseURL,
                thinkingLevel: store.agentPrimaryRoute?.thinkingLevel ?? "off",
                contextWindow: model.contextWindow,
                maxOutputTokens: model.maxOutputTokens,
                supportsImages: model.supportsImages,
                supportsTools: model.supportsTools,
                supportsThinking: model.supportsThinking
            )
        store.setAgentSessionProviderRoute(route)
    }

    // MARK: - 模型能力徽章 / 思考强度 / 视觉槽 / 自定义 Provider

    private var sessionThinkingRow: some View {
        let route = store.agentPrimaryRoute ?? store.agentGlobalPrimaryRoute
        let efforts = store.reasoningEffortOptions(
            providerID: route?.providerId,
            modelID: route?.modelId
        )
        return HStack(spacing: 10) {
            Text("思考强度")
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Picker("思考强度", selection: composerThinkingBinding) {
                ForEach(efforts) { effort in
                    Text(effort.name ?? effort.id).tag(effort.id)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 320)
            .disabled(store.isChatStreaming)
            Spacer(minLength: 0)
        }
    }

    private var visionRouteSummary: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text("视觉模型")
                    .font(KSSFont.themed(11.5, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                if store.agentVisionRoute != nil {
                    Button("清除") { store.setAgentVisionRoute(nil) }
                        .buttonStyle(.plain)
                        .font(KSSFont.themed(10, .semibold, theme: theme))
                        .foregroundStyle(theme.accent)
                        .disabled(store.isChatStreaming)
                }
            }
            Text(store.agentVisionRoute == nil
                 ? "未配置（模型行菜单可设为视觉模型）"
                 : routeDisplayName(store.agentVisionRoute))
                .font(KSSFont.themed(12.5, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func providerThinkingPicker(_ providerID: String) -> some View {
        let referenceModelID = store.agentPrimaryRoute?.providerId == providerID
            ? store.agentPrimaryRoute?.modelId
            : store.agentProviders.first(where: { $0.id == providerID })?.models?.first?.id
        let efforts = store.reasoningEffortOptions(providerID: providerID, modelID: referenceModelID)
        VStack(alignment: .leading, spacing: 6) {
            Text("默认思考强度（保存进此 Provider 的路由）")
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Picker("思考强度", selection: $providerThinkingDraft) {
                ForEach(efforts) { effort in
                    Text(effort.name ?? effort.id).tag(effort.id)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 320)
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private func modelCapabilityBadges(_ model: AgentModelDescriptor) -> some View {
        HStack(spacing: 5) {
            if let window = model.contextWindow, window > 0 {
                capabilityBadge(Self.formatContextWindow(window), icon: "square.stack.3d.up")
            }
            if modelSupportsThinking(model) {
                capabilityBadge("思考", icon: "brain")
            }
            if modelSupportsVision(model) {
                capabilityBadge("视觉", icon: "eye")
            }
        }
    }

    private func capabilityBadge(_ text: String, icon: String) -> some View {
        HStack(spacing: 3) {
            Image(systemName: icon)
                .font(.system(size: 8.5, weight: .semibold))
            Text(text)
        }
        .font(KSSFont.themed(9.5, .semibold, theme: theme))
        .foregroundStyle(theme.textSecondary)
        .padding(.horizontal, 6)
        .padding(.vertical, 2.5)
        .background(theme.surfaceContainer, in: Capsule())
    }

    private func modelSupportsThinking(_ model: AgentModelDescriptor) -> Bool {
        model.supportsThinking == true
            || (model.reasoningEfforts?.contains { $0.id != "off" } ?? false)
    }

    private func modelSupportsVision(_ model: AgentModelDescriptor) -> Bool {
        model.supportsImages == true
            || (model.inputModalities?.contains("image") ?? false)
    }

    static func formatContextWindow(_ tokens: Int) -> String {
        if tokens >= 1_000_000 {
            return String(format: "%.0fM", Double(tokens) / 1_000_000)
        }
        if tokens >= 1_000 {
            return "\(tokens / 1_000)K"
        }
        return "\(tokens)"
    }

    /// DSH 官方「配置模型方法」：写 $DSH_HOME/settings.yaml 的 llm-pi-ai 小节。
    private var customProviderCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("自定义端点（OpenAI-compatible）")
                .font(KSSFont.themed(14, .bold, theme: theme))
            Text("按 DSH 配置方法写入 settings.yaml 的 llm-pi-ai 小节；API Key 只进 macOS Keychain，harness 侧仅引用环境变量名。保存后会重启内核使路由与凭证同时生效。")
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textSecondary)
            HStack(spacing: 10) {
                TextField("Provider ID（如 acme-gateway）", text: $customProviderIDDraft)
                    .textFieldStyle(.roundedBorder)
                TextField("显示名（可选）", text: $customProviderNameDraft)
                    .textFieldStyle(.roundedBorder)
            }
            TextField("Base URL（https://…/v1）", text: $customProviderBaseURLDraft)
                .textFieldStyle(.roundedBorder)
            HStack(spacing: 10) {
                SecureField("API Key（存 Keychain，可稍后再补）", text: $customProviderKeyDraft)
                    .textFieldStyle(.roundedBorder)
                TextField("模型 ID，逗号分隔", text: $customProviderModelsDraft)
                    .textFieldStyle(.roundedBorder)
            }
            HStack(spacing: 10) {
                Button(isSavingCustomProvider ? "保存中…" : "添加 Provider") {
                    submitCustomProvider()
                }
                .buttonStyle(.borderedProminent)
                .disabled(isSavingCustomProvider
                          || customProviderIDDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                          || customProviderBaseURLDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                          || customProviderModelsDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if let customProviderMessage {
                    Text(customProviderMessage)
                        .font(KSSFont.themed(11.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(16)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(theme.hairline))
    }

    private func submitCustomProvider() {
        let id = customProviderIDDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        let models = customProviderModelsDraft
            .split(whereSeparator: { $0 == "," || $0 == "，" || $0.isNewline })
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        isSavingCustomProvider = true
        customProviderMessage = nil
        Task {
            let error = await store.addCustomProvider(
                id: id,
                displayName: customProviderNameDraft,
                baseURL: customProviderBaseURLDraft.trimmingCharacters(in: .whitespacesAndNewlines),
                apiKey: customProviderKeyDraft,
                modelIDs: models
            )
            isSavingCustomProvider = false
            if let error, !error.isEmpty {
                customProviderMessage = error
            } else {
                customProviderMessage = "已写入 settings.yaml 并重启内核；在下方目录中可见。"
                customProviderIDDraft = ""
                customProviderNameDraft = ""
                customProviderBaseURLDraft = ""
                customProviderKeyDraft = ""
                customProviderModelsDraft = ""
            }
        }
    }

    private func applySeesawDestination() {
        switch store.consumeSeesawDestination() {
        case .models?: seesawPage = .models
        case .conversation?: seesawPage = .conversation
        case nil: break
        }
    }

    private func consumeComposerPrefill() {
        guard let prefill = store.chatComposerPrefill?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !prefill.isEmpty else { return }
        input = prefill
        store.chatComposerPrefill = nil
        isComposerFocused = true
        seesawPage = .conversation
    }

    /// The input must have one stable identity while session hydration swaps the
    /// empty transcript for history. Keeping it outside the conditional avoids a
    /// Composer dissolve/flicker after the page has already appeared.
    private var focusConversationWorkspace: some View {
        VStack(spacing: 0) {
            Group {
                if store.chatMessages.isEmpty {
                    focusEmptyConversation
                } else {
                    focusMessageList
                }
            }
            focusComposer
                .frame(maxWidth: SeesawXcomChrome.composerColumnWidth)
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.top, 10)
                .padding(.bottom, 18)
        }
    }

    private var focusEmptyConversation: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Spacer(minLength: 76)

                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: "sparkle")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(theme.accent)
                    Text("今天想研究什么？")
                        .font(KSSFont.themed(26, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                }

                Text("对话已升级为混合节奏：你的问题是聊天气泡，助手答复是印刷体。")
                    .font(KSSFont.themed(14, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.top, 10)

                focusResearchTaskRows
                    .padding(.top, 30)

                if store.researchCandidate != nil {
                    focusResearchCandidate
                        .padding(.top, 14)
                }

                Spacer(minLength: 40)
            }
            .frame(maxWidth: SeesawXcomChrome.composerColumnWidth, alignment: .leading)
            .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }

    private var focusMessageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 16) {
                    ForEach(store.chatMessages) { message in
                        VStack(spacing: 2) {
                            focusMessageCell(message)
                            messageActionRow(message)
                        }
                        .id(message.id)
                        .onHover { hovering in
                            hoveredMessageId = hovering
                                ? message.id
                                : (hoveredMessageId == message.id ? nil : hoveredMessageId)
                        }
                    }
                    if let tool = store.chatToolInProgress {
                        focusToolRow(tool)
                            .id("tool-progress")
                    }
                }
                // 与 composer/空态同一列宽(composerColumnWidth),左右边缘对齐。
                .frame(maxWidth: SeesawXcomChrome.composerColumnWidth)
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.vertical, 22)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .onChange(of: store.chatMessages.last?.text) { _, _ in
                if let last = store.chatMessages.last {
                    withAnimation(.easeOut(duration: 0.12)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func focusMessageCell(_ message: ChatMessage) -> some View {
        let isUser = message.role == .user

        if isUser {
            // Hybrid C: solid accent chat bubble — clearly product, not tool card.
            HStack(alignment: .bottom, spacing: 0) {
                Spacer(minLength: 96)
                VStack(alignment: .trailing, spacing: 6) {
                    if !message.text.isEmpty {
                        markdownText(message.text)
                            .font(KSSFont.themed(14.5, theme: theme))
                            .foregroundStyle(Color.white)
                            .textSelection(.enabled)
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                            .tint(.white)
                    }
                    messageAttachmentStrip(message.attachments)
                }
                .padding(.horizontal, 15)
                .padding(.vertical, 12)
                .frame(maxWidth: SeesawXcomChrome.composerColumnWidth * 0.75, alignment: .trailing)
                .background(
                    theme.accent,
                    in: UnevenRoundedRectangle(
                        topLeadingRadius: 18,
                        bottomLeadingRadius: 18,
                        bottomTrailingRadius: 5,
                        topTrailingRadius: 18
                    )
                )
                .shadow(color: theme.accent.opacity(0.28), radius: 8, y: 3)
                .contextMenu {
                    Button("复制内容", systemImage: "doc.on.doc") {
                        copyMessageText(message.text)
                    }
                    .disabled(message.text.nilIfBlank == nil)
                    Divider()
                    Button("记住这条消息") { store.proposeAgentMemory(message.text) }
                }
            }
            .padding(.vertical, 4)
        } else {
            // Hybrid C: compact print column — height tracks content only.
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1)
                    .fill(Color(red: 0x1B / 255, green: 0x36 / 255, blue: 0x5D / 255).opacity(0.75))
                    .frame(width: 2.5)
                    .frame(minHeight: 18)

                VStack(alignment: .leading, spacing: 6) {
                    if message.text.isEmpty && store.isChatStreaming {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("思考中…")
                                .font(KSSFont.themed(13, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                    } else if !message.text.isEmpty {
                        SeesawMarkdownView(markdown: message.text, errorTint: message.isError ? Color.red : nil)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    streamingTail(for: message)

                    if !message.thinkingBlocks.isEmpty {
                        AgentThinkingDisclosure(blocks: message.thinkingBlocks)
                    }

                    messageAttachmentStrip(message.attachments)

                    if message.numbersUnverified && store.isChatStreaming {
                        Label("数字校验中（以工具真值为准）", systemImage: "exclamationmark.triangle")
                            .font(KSSFont.themed(12, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }

                    if message.evidenceSummary.hasEvidence || message.evidenceSummary.provider != nil {
                        EvidenceDrawerView(summary: message.evidenceSummary, drawer: message.evidenceDrawer)
                    }

                    if let chart = message.chartAttachment, !chart.bars.isEmpty {
                        ChartWebView(points: [], intradayBars: chart.bars)
                            .frame(height: 300)
                            .background(theme.chartSurface)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.trailing, 8)
            .contextMenu {
                Button("复制内容", systemImage: "doc.on.doc") {
                    copyMessageText(message.text)
                }
                .disabled(message.text.nilIfBlank == nil)
                Divider()
                Button("记住这条消息") { store.proposeAgentMemory(message.text) }
            }
            .padding(.vertical, 2)
        }
    }

    @ViewBuilder
    private func streamingTail(for message: ChatMessage) -> some View {
        if store.isChatStreaming,
           message.role == .assistant,
           store.chatMessages.last?.id == message.id,
           !message.text.isEmpty {
            HStack(spacing: 8) {
                ProgressView().controlSize(.mini)
                Text(store.chatToolInProgress.map { "正在调用 \($0)…" } ?? "继续生成中…")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
        }
    }

    /// 悬停出现的复制入口；右键菜单仍在，这里是可发现的显性入口。
    @ViewBuilder
    private func messageActionRow(_ message: ChatMessage) -> some View {
        if !message.text.isEmpty {
            let copied = copiedMessageId == message.id
            Button {
                copyMessageText(message.text)
                copiedMessageId = message.id
                let target = message.id
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                    if copiedMessageId == target { copiedMessageId = nil }
                }
            } label: {
                Label(copied ? "已复制" : "复制", systemImage: copied ? "checkmark" : "doc.on.doc")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(copied ? theme.accent : theme.textSecondary)
            }
            .buttonStyle(.plain)
            .opacity(hoveredMessageId == message.id || copied ? 1 : 0)
            .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
            .frame(height: 14)
            .padding(.leading, message.role == .user ? 0 : 13)
            .accessibilityLabel("复制这条消息")
        }
    }

    private func copyMessageText(_ text: String) {
        guard text.nilIfBlank != nil else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }

    private func focusToolRow(_ tool: String) -> some View {
        HStack(spacing: 6) {
            ProgressView().controlSize(.mini)
            Text(tool)
                .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(theme.surfaceContainer.opacity(0.9), in: Capsule())
        .overlay { Capsule().stroke(theme.hairline.opacity(0.8)) }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityLabel("正在调用 \(tool)")
    }

    /// FlowDown-inspired input: one continuous rounded shell; text + trailing
    /// circular send on the same row (not a stacked control bar).
    private var focusComposer: some View {
        VStack(alignment: .leading, spacing: 8) {
            composerInlineStatus
            queuedInputPanel
            pendingAttachmentStrip
            fileRefChips
            atFileSuggestionPanel
            focusSessionSkillChips

            HStack(alignment: .bottom, spacing: 8) {
                composerSkillMenu

                attachmentPickerButton

                TextField(
                    store.isChatStreaming
                        ? "追问会排队，本轮生成结束后处理…"
                        : (store.chatMessages.isEmpty ? "问问盘面、个股或一个研究问题…" : "继续追问…"),
                    text: $input,
                    axis: .vertical
                )
                .textFieldStyle(.plain)
                .font(KSSFont.themed(15, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .focused($isComposerFocused)
                .lineLimit(1...6)
                .onSubmit {
                    if atFilePanelVisible {
                        applyHighlightedAtFile()
                    } else {
                        submitInput(mode: "steering")
                    }
                }
                .onChange(of: input) { _, _ in scheduleAtFileSearch() }
                .onKeyPress(.downArrow) {
                    guard atFilePanelVisible else { return .ignored }
                    atFileSelection = min(atFileSelection + 1, max(atFileResults.count - 1, 0))
                    return .handled
                }
                .onKeyPress(.upArrow) {
                    guard atFilePanelVisible else { return .ignored }
                    atFileSelection = max(atFileSelection - 1, 0)
                    return .handled
                }
                .onKeyPress(.escape) {
                    guard atFilePanelVisible else { return .ignored }
                    atFileResults = []
                    return .handled
                }
                .onKeyPress(.tab) {
                    guard atFilePanelVisible else { return .ignored }
                    applyHighlightedAtFile()
                    return .handled
                }
                .onPasteCommand(of: [.png, .tiff, .jpeg]) { _ in
                    handleComposerImagePaste()
                }
                // 与 32pt 图标同高:额外的垂直 padding 会让底对齐时文字中心
                // 比图标中心高 4pt(实测反馈的"永远对不齐")。
                .frame(minHeight: 32, alignment: .center)

                composerModelMenu
                focusSendButton
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
            theme.appearance == .dark
                ? theme.surfaceContainer.opacity(0.55)
                : Color.white.opacity(0.92),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(theme.hairline.opacity(isComposerFocused ? 0 : 0.9), lineWidth: 1)
        }
        .overlay {
            if isComposerFocused {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(theme.accent.opacity(0.35), lineWidth: 1)
            }
        }
        .shadow(color: .black.opacity(theme.appearance == .dark ? 0.22 : 0.08), radius: 10, y: 4)
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private var composerInlineStatus: some View {
        if let pending = store.pendingWriteConfirm {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "exclamationmark.shield")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(theme.accent)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 4) {
                    Text("需要你确认才能继续")
                        .font(KSSFont.themed(12, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text(pending.effect.isEmpty ? "允许 \(pending.tool) / \(pending.command)" : pending.effect)
                        .font(KSSFont.themed(11.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(3)
                    if !pending.argsText.isEmpty && pending.argsText != "{}" {
                        Text(pending.argsText)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 8)
                Button("拒绝") { store.resolveWriteConfirm(approved: false) }
                    .buttonStyle(.plain)
                    .foregroundStyle(theme.textSecondary)
                Button("允许") { store.resolveWriteConfirm(approved: true) }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 9))
            .accessibilityLabel("写操作待确认")
        }
        if let issue = providerIssueDescription {
            HStack(spacing: 7) {
                Image(systemName: store.seesawProviderReadiness == .configuredUntested
                      ? "info.circle" : "exclamationmark.triangle")
                    .font(.system(size: 11, weight: .semibold))
                Text(issue)
                    .lineLimit(2)
                Spacer(minLength: 4)
                Button("检查模型") {
                    activeOverlay = nil
                    seesawPage = .models
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
            }
            .font(KSSFont.themed(11.5, .medium, theme: theme))
            .foregroundStyle(store.seesawProviderReadiness == .configuredUntested ? theme.textSecondary : Color.orange)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                store.seesawProviderReadiness == .configuredUntested
                    ? theme.surfaceContainer
                    : Color.orange.opacity(0.08),
                in: RoundedRectangle(cornerRadius: 9)
            )
        }
    }

    /// Codex 风格「模型 + 思考强度」入口（dsh-reasoning-effort 交互复刻）：
    /// 弹层上半是思考强度分段滑块，下半是模型列表，与 Models 页共用路由状态。
    private var composerModelMenu: some View {
        Button {
            showComposerModelPopover.toggle()
        } label: {
            HStack(spacing: 3) {
                Image(systemName: "cpu")
                    .font(.system(size: 13, weight: .semibold))
                if composerThinkingLevel != "off" {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(theme.accent)
                }
            }
            .foregroundStyle(theme.textSecondary)
            .frame(width: composerThinkingLevel == "off" ? 32 : 42, height: 32)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(store.isChatStreaming)
        .popover(isPresented: $showComposerModelPopover, arrowEdge: .top) {
            composerModelPopover
        }
        .help("\(providerComposerLabel) · 思考强度 \(composerThinkingLevel)")
    }

    private var composerThinkingLevel: String {
        let route = store.agentPrimaryRoute ?? store.agentGlobalPrimaryRoute
        if let level = route?.thinkingLevel, !level.isEmpty { return level }
        return store.modelDescriptor(
            providerID: route?.providerId,
            modelID: route?.modelId
        )?.defaultReasoningEffort ?? "off"
    }

    private var composerThinkingBinding: Binding<String> {
        Binding(
            get: { composerThinkingLevel },
            set: { store.setSessionThinkingLevel($0) }
        )
    }

    private var composerModelPopover: some View {
        let route = store.agentPrimaryRoute ?? store.agentGlobalPrimaryRoute
        let efforts = store.reasoningEffortOptions(
            providerID: route?.providerId,
            modelID: route?.modelId
        )
        return VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Text("思考强度")
                        .font(KSSFont.themed(12, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Spacer()
                    Text(routeDisplayName(route))
                        .font(KSSFont.themed(10.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
                Picker("思考强度", selection: composerThinkingBinding) {
                    ForEach(efforts) { effort in
                        Text(effort.name ?? effort.id).tag(effort.id)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .disabled(store.isChatStreaming)
                Text("写入会话路由，下一次发送生效；不支持的档位会按模型能力收敛。")
                    .font(KSSFont.themed(10.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }

            Divider().overlay(theme.hairline)

            VStack(alignment: .leading, spacing: 4) {
                Text("模型")
                    .font(KSSFont.themed(12, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                if visibleProviderModels.isEmpty {
                    Button("在模型中心启用模型") {
                        showComposerModelPopover = false
                        seesawPage = .models
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(theme.accent)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(visibleProviderModels, id: \.routeID) { item in
                                composerModelPopoverRow(item)
                            }
                        }
                    }
                    .frame(maxHeight: 236)
                }
            }

            Divider().overlay(theme.hairline)

            Button {
                showComposerModelPopover = false
                seesawPage = .models
            } label: {
                Label("管理模型…", systemImage: "slider.horizontal.3")
                    .font(KSSFont.themed(12, .semibold, theme: theme))
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.accent)
        }
        .padding(14)
        .frame(width: 324)
    }

    private func composerModelPopoverRow(_ item: ComposerModelOption) -> some View {
        let isCurrent = item.provider.id == store.agentPrimaryRoute?.providerId
            && item.model.id == store.agentPrimaryRoute?.modelId
        return Button {
            selectSessionRoute(provider: item.provider, model: item.model)
        } label: {
            HStack(spacing: 8) {
                Image(systemName: isCurrent ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(isCurrent ? theme.accent : theme.textSecondary.opacity(0.5))
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.label)
                        .font(KSSFont.themed(12.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .lineLimit(1)
                    modelCapabilityBadges(item.model)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(store.isChatStreaming)
        .background(isCurrent ? theme.accentSoft : .clear, in: RoundedRectangle(cornerRadius: 8))
    }

    /// 使用技能(轻量菜单):勾选=已加入本会话,点击即切换;配置走底部"管理技能…"。
    /// 使用与配置分离——菜单不再打开管理面板本身。
    private var composerSkillMenu: some View {
        Menu {
            if usableSkills.isEmpty {
                Button("去启用技能…") { toggleOverlay(.skills) }
            } else {
                ForEach(usableSkills) { skill in
                    let selected = store.pinnedAgentSkillIds.contains(skill.id)
                    Button {
                        store.setAgentSkillInConversation(skill, selected: !selected)
                    } label: {
                        if selected {
                            Label(skill.name, systemImage: "checkmark")
                        } else {
                            Text(skill.name)
                        }
                    }
                    .disabled(store.isChatStreaming
                              || (!selected && sessionSkills.count >= 3))
                }
                Divider()
                Button("管理技能…") { toggleOverlay(.skills) }
            }
        } label: {
            Image(systemName: "slider.horizontal.3")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(sessionSkills.isEmpty ? theme.textSecondary : theme.accent)
                .frame(width: 32, height: 32)
                .contentShape(Circle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .help(sessionSkills.isEmpty
              ? "使用技能(本会话最多 3 个)"
              : "本会话技能 \(sessionSkills.count)/3")
    }

    /// 可直接使用的技能:已启用且可用;排序先本会话再其余。
    private var usableSkills: [AgentSkill] {
        store.agentSkills
            .filter { $0.enabled != false && $0.available != false }
            .sorted { lhs, rhs in
                let lhsIn = store.pinnedAgentSkillIds.contains(lhs.id)
                let rhsIn = store.pinnedAgentSkillIds.contains(rhs.id)
                if lhsIn != rhsIn { return lhsIn }
                return lhs.name.localizedCompare(rhs.name) == .orderedAscending
            }
    }

    @ViewBuilder
    private var focusSessionSkillChips: some View {
        if !sessionSkills.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                ForEach(sessionSkills) { skill in
                    Button {
                        selectedSkillId = skill.id
                        skillSearch = skill.name
                        activeOverlay = .skills
                    } label: {
                        Text(skill.name)
                            .font(KSSFont.themed(11.5, .semibold, theme: theme))
                            .foregroundStyle(theme.accent)
                            .padding(.horizontal, 10)
                            .frame(height: 26)
                            .background(theme.accentSoft, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .contextMenu {
                        Button("移出本会话") {
                            store.setAgentSkillInConversation(skill, selected: false)
                        }
                    }
                    .help("\(skill.name) 已加入本会话；点击查看或移出")
                }
            }
            }
        }
    }

    private var focusResearchTaskRows: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("从这里开始")
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .padding(.bottom, 8)

            VStack(spacing: 0) {
                ForEach(availableSkillStarters) { starter in
                    researchTaskRow(starter)
                    if starter.id != availableSkillStarters.last?.id {
                        Divider().overlay(theme.hairline)
                            .padding(.leading, 53)
                    }
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(theme.hairline))
        }
    }

    private func researchTaskRow(_ starter: SkillStarter) -> some View {
        Button {
            input = starter.prompt
            isComposerFocused = true
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: starter.icon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(theme.accent)
                    .frame(width: 28, height: 28)
                    .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 3) {
                    Text(starter.title)
                        .font(KSSFont.themed(14, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text(researchTaskDescription(for: starter.skillId))
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.top, 4)
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(store.isChatStreaming)
        .help("填入“\(starter.title)”的起始问题")
    }

    private func researchTaskDescription(for skillID: String) -> String {
        switch skillID {
        case "kss-review":
            return "解释一只股票今天为什么动"
        case "longbridge-realtime":
            return "查看指数、热点与盘中结构"
        case "kss-indicator-pipeline":
            return "把指标研究成可回测的问题"
        case "kss-orientation":
            return "先了解可用数据、工具与约束"
        default:
            return "用这个 Skill 开始一项研究"
        }
    }

    @ViewBuilder
    private var focusResearchCandidate: some View {
        if let candidate = store.researchCandidate {
            Button { store.selectedSection = .runbook } label: {
                HStack(spacing: 8) {
                    Image(systemName: "scope")
                        .foregroundStyle(theme.accent)
                    Text("继续深度研究：\(candidate.objective)")
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.right")
                }
                .font(KSSFont.themed(12.5, .medium, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
        }
    }

    private var focusSendButton: some View {
        // 提交/停止同一按钮的两个态：空闲=发送（↑），流式=停止（■）。
        // 流式中排队走回车（占位文案提示"追问会排队"）。
        Button {
            if store.isChatStreaming {
                store.stopChatGeneration()
            } else {
                submitInput(mode: "steering")
            }
        } label: {
            Label(store.isChatStreaming ? "停止" : "发送",
                  systemImage: store.isChatStreaming ? "stop.fill" : "arrow.up")
                .labelStyle(.iconOnly)
                .font(.system(size: store.isChatStreaming ? 12 : 13, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 32, height: 32)
                .background(Circle().fill(
                    store.isChatStreaming
                        ? Color.red
                        : (canSend ? theme.accent : theme.accent.opacity(0.4))))
        }
        .buttonStyle(.plain)
        .disabled(!store.isChatStreaming && !canSend)
        .help(store.isChatStreaming ? "停止生成" : "发送")
    }

    private var focusSessionPalette: some View {
        VStack(spacing: 0) {
            focusPanelHeader(title: "会话") {
                store.createAgentSession()
                activeOverlay = nil
                isComposerFocused = true
            }

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(theme.textSecondary)
                TextField("搜索会话", text: $sessionSearch)
                    .textFieldStyle(.plain)
                    .font(KSSFont.themed(13, theme: theme))
            }
            .padding(.horizontal, 12)
            .frame(height: 38)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 10))
            .padding(12)

            ScrollView {
                LazyVStack(spacing: 4) {
                    ForEach(filteredSessions) { session in
                        focusSessionRow(session)
                    }

                    if filteredSessions.isEmpty {
                        Text("没有匹配的会话")
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .padding(.vertical, 24)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 12)
            }

            if store.agentProtocolUnavailable {
                Label("Agent v1 暂不可用，已回退旧聊天", systemImage: "exclamationmark.triangle")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .overlay(alignment: .top) { Rectangle().fill(theme.hairline).frame(height: 1) }
            }
        }
        .background(theme.surface)
    }

    /// 会话行：整行点击切换；标题默认可点打开（不再用常驻 TextField 吞手势）；重命名走上下文菜单。
    private func focusSessionRow(_ session: AgentSession) -> some View {
        let selected = session.sessionId == store.selectedAgentSessionId
        return HStack(spacing: 8) {
            Image(systemName: selected ? "bubble.left.and.bubble.right.fill" : "bubble.left")
                .foregroundStyle(selected ? theme.accent : theme.textSecondary)
                .frame(width: 28, height: 28)

            Text(store.agentSessions.first(where: { $0.sessionId == session.sessionId })?.title ?? session.title)
                .font(KSSFont.themed(13.5, selected ? .semibold : .regular, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)

            Button {
                store.archiveAgentSession(session.sessionId)
            } label: {
                Label("归档 \(session.title)", systemImage: "archivebox")
                    .labelStyle(.iconOnly)
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.textSecondary)
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 42)
        .background(selected ? theme.accentSoft : Color.clear, in: RoundedRectangle(cornerRadius: 10))
        .contentShape(Rectangle())
        .onTapGesture {
            store.openAgentSession(session.sessionId)
            activeOverlay = nil
            isComposerFocused = true
        }
        .contextMenu {
            Button("打开会话") {
                store.openAgentSession(session.sessionId)
                activeOverlay = nil
                isComposerFocused = true
            }
            Button("重命名…") {
                promptRenameSession(session)
            }
            Divider()
            Button("归档", role: .destructive) {
                store.archiveAgentSession(session.sessionId)
            }
        }
    }

    private func promptRenameSession(_ session: AgentSession) {
        let current = store.agentSessions.first(where: { $0.sessionId == session.sessionId })?.title ?? session.title
        let alert = NSAlert()
        alert.messageText = "重命名会话"
        alert.informativeText = "为会话指定新标题"
        alert.alertStyle = .informational
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        let field = NSTextField(string: current)
        field.frame = NSRect(x: 0, y: 0, width: 280, height: 24)
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        let response = alert.runModal()
        guard response == .alertFirstButtonReturn else { return }
        store.renameAgentSession(session.sessionId, title: field.stringValue)
    }

    private var focusSkillPalette: some View {
        VStack(spacing: 0) {
            focusPanelHeader(title: "技能") {
                store.reloadAgentSkills()
            }

            ScrollView {
                VStack(alignment: .leading, spacing: SettingsFormStyle.blockSpacing) {
                    HStack(spacing: SettingsFormStyle.rowHSpacing) {
                        Image(systemName: "magnifyingglass")
                            .font(KSSFont.themed(14, .semibold, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .frame(width: 22)
                        TextField("搜索名称、分类或来源", text: $skillSearch)
                            .textFieldStyle(.plain)
                            .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                    }
                    .frame(height: 38)
                    .padding(.horizontal, SettingsFormStyle.cardPadding)
                    .kssCard(.filled, padding: 0)

                    focusSkillFilterTabs

                    focusSkillListHeader(
                        title: "技能目录",
                        trailing: "\(filteredSkills.count) 项 · 来源 · 信任 · 所需工具",
                        compact: true
                    )

                    LazyVStack(spacing: SettingsFormStyle.groupSpacing) {
                        ForEach(filteredSkills) { skill in
                            focusSkillRow(skill)
                        }
                    }

                    if filteredSkills.isEmpty {
                        SettingsHintText(text: "没有匹配的技能", empty: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, SettingsFormStyle.cardPadding)
                    }

                    if !store.agentSkillDiagnostics.isEmpty {
                        focusSkillListHeader(
                            title: "诊断",
                            trailing: "\(store.agentSkillDiagnostics.count) 项",
                            compact: true
                        )
                        ForEach(store.agentSkillDiagnostics) { diagnostic in
                            seesawPanelRow {
                                VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                                    Text(diagnostic.code)
                                        .font(.system(size: SettingsFormStyle.monoMeta, weight: .semibold, design: .monospaced))
                                        .foregroundStyle(Color.red)
                                    Text(diagnostic.message)
                                        .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                }
                            }
                        }
                    }
                }
                .padding(SettingsFormStyle.detailHPadding)
                .padding(.bottom, SettingsFormStyle.detailVPadding)
            }
        }
        .background(theme.surface)
    }

    /// Skills use the same x.com underline hierarchy as the information radar.
    /// The search field is the only filled control in this workspace; filters
    /// and list headings stay flat so they do not compete with the skill rows.
    private var focusSkillFilterTabs: some View {
        HStack(alignment: .bottom, spacing: 0) {
            ForEach(SkillFilter.allCases) { filter in
                let isActive = skillFilter == filter
                Button {
                    withAnimation(.easeOut(duration: 0.15)) {
                        skillFilter = filter
                    }
                } label: {
                    VStack(spacing: 0) {
                        Text(filter.rawValue)
                            .font(KSSFont.themed(15, isActive ? .bold : .medium, theme: theme))
                            .foregroundStyle(isActive ? theme.textPrimary : theme.textSecondary)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                        Capsule()
                            .fill(isActive ? theme.accent : Color.clear)
                            .frame(height: 4)
                            .padding(.horizontal, 8)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(isActive ? .isSelected : [])
            }

            Spacer(minLength: 12)

            Text("\(enabledSkillCount) 个启用 · \(sessionSkills.count) 个本会话")
                .font(KSSFont.themed(SettingsFormStyle.meta, .medium, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
                .padding(.bottom, 12)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private func focusSkillListHeader(
        title: String,
        detail: String? = nil,
        trailing: String,
        compact: Bool = false
    ) -> some View {
        HStack(alignment: detail == nil ? .center : .top, spacing: 10) {
            if !compact {
                Image(systemName: "slider.horizontal.3")
                    .font(KSSFont.themed(16, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                    .frame(width: 22)
            }
            VStack(alignment: .leading, spacing: detail == nil ? 0 : 3) {
                Text(title)
                    .font(KSSFont.themed(
                        compact ? 15 : SettingsFormStyle.itemTitle,
                        compact ? .semibold : .bold,
                        theme: theme
                    ))
                    .foregroundStyle(theme.textPrimary)
                if let detail {
                    Text(detail)
                        .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 12)
            Text(trailing)
                .font(KSSFont.themed(SettingsFormStyle.meta, .medium, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .multilineTextAlignment(.trailing)
                .lineLimit(compact ? 1 : 2)
        }
        .padding(.top, compact ? 6 : 10)
        .padding(.bottom, compact ? 7 : 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private func focusSkillRow(_ skill: AgentSkill) -> some View {
        let isInConversation = store.pinnedAgentSkillIds.contains(skill.id)
        let exceedsSessionSkillLimit = !isInConversation && sessionSkills.count >= 3

        return seesawPanelRow {
            HStack(alignment: .top, spacing: SettingsFormStyle.rowHSpacing) {
                Image(systemName: skill.available == false ? "exclamationmark.triangle" : "slider.horizontal.3")
                    .font(KSSFont.themed(16, .semibold, theme: theme))
                    .foregroundStyle(skill.available == false ? theme.ma5 : theme.accent)
                    .frame(width: 22, alignment: .center)

                VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                    HStack(spacing: 8) {
                        Text(skill.name)
                            .font(KSSFont.themed(SettingsFormStyle.itemTitle, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        if skill.protected == true {
                            SettingsStatusCapsule(text: "受保护", tint: theme.accent)
                        }
                    }
                    if let description = skill.description, !description.isEmpty {
                        Text(description)
                            .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text(skillMetadata(skill))
                        .font(KSSFont.themed(SettingsFormStyle.meta, .medium, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    if skill.available == false {
                        Text("缺少工具：\((skill.missingRequiredTools ?? []).joined(separator: "、"))")
                            .font(KSSFont.themed(SettingsFormStyle.meta, .semibold, theme: theme))
                            .foregroundStyle(theme.ma5)
                    } else if exceedsSessionSkillLimit {
                        Text("每个会话最多加入 3 个技能")
                            .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                }

                Spacer(minLength: 12)

                VStack(alignment: .leading, spacing: 8) {
                    Toggle("启用", isOn: Binding(
                        get: { skill.enabled != false },
                        set: { store.setAgentSkillEnabled(skill, enabled: $0) }
                    ))
                    .disabled(skill.available == false)

                    Button(isInConversation ? "移出本会话" : "加入本会话") {
                        store.setAgentSkillInConversation(skill, selected: !isInConversation)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(isInConversation ? theme.textSecondary : theme.accent)
                    .disabled(skill.available == false || exceedsSessionSkillLimit)
                }
                .font(KSSFont.themed(SettingsFormStyle.actionLabel, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 112, alignment: .leading)
            }
        }
    }

    private var focusContextPopover: some View {
        VStack(spacing: 0) {
            focusPanelHeader(title: "记忆与上下文") {
                Task { await store.loadAgentMemories(query: memorySearch) }
                store.recallAgentSources(query: memorySearch)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: SettingsFormStyle.blockSpacing) {
                    seesawPanelSummary(
                        systemImage: "circle.dotted.circle",
                        title: "会话上下文",
                        detail: providerComposerLabel,
                        status: contextUsageShort
                    )

                    if !store.agentSourceRecalls.isEmpty {
                        seesawPanelGroupHeader("本轮召回", count: store.agentSourceRecalls.count, trailing: "真实来源与有效期")
                        ForEach(store.agentSourceRecalls) { recall in
                            seesawPanelRow {
                                VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                                    Text(recall.title)
                                        .font(KSSFont.themed(SettingsFormStyle.itemTitle, .bold, theme: theme))
                                    if let metadata = recallMetadata(recall) {
                                        Text(metadata)
                                            .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                                            .foregroundStyle(recall.reviewRequired == true ? theme.ma5 : theme.textSecondary)
                                    }
                                    if let excerpt = recall.excerpt, !excerpt.isEmpty {
                                        Text(excerpt)
                                            .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                                            .foregroundStyle(theme.textSecondary)
                                            .lineLimit(3)
                                    }
                                }
                            }
                        }
                    }

                    if !store.agentMemoryCandidates.isEmpty {
                        seesawPanelGroupHeader("待确认记忆", count: store.agentMemoryCandidates.count)
                        ForEach(store.agentMemoryCandidates) { candidate in
                            seesawPanelRow {
                                HStack(alignment: .top, spacing: SettingsFormStyle.rowHSpacing) {
                                    VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                                        Text(candidate.text)
                                            .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                                        Text("需由你确认后才会写入长期记忆")
                                            .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                                            .foregroundStyle(theme.textSecondary)
                                    }
                                    Spacer(minLength: 8)
                                    HStack(spacing: 8) {
                                        SettingsPrimaryAction(title: "记住", systemImage: "checkmark") {
                                            store.resolveMemoryCandidate(candidate, approved: true)
                                        }
                                        SettingsBorderedAction(title: "忽略", systemImage: "xmark") {
                                            store.resolveMemoryCandidate(candidate, approved: false)
                                        }
                                    }
                                }
                            }
                        }
                    }

                    DisclosureGroup(isExpanded: $showMemoryManagement) {
                        VStack(alignment: .leading, spacing: SettingsFormStyle.groupSpacing) {
                            HStack(spacing: 8) {
                                TextField("搜索记忆或来源", text: $memorySearch)
                                    .textFieldStyle(.roundedBorder)
                                Button("搜索") {
                                    Task { await store.loadAgentMemories(query: memorySearch) }
                                    store.recallAgentSources(query: memorySearch)
                                }
                            }
                            ForEach(store.agentMemories) { memory in
                                seesawPanelRow {
                                    HStack(alignment: .top, spacing: SettingsFormStyle.rowHSpacing) {
                                        VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                                            Text(memory.text)
                                                .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                                            if let metadata = memoryMetadata(memory) {
                                                Text(metadata)
                                                    .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                                                    .foregroundStyle(memory.reviewRequired == true ? theme.ma5 : theme.textSecondary)
                                            }
                                        }
                                        Spacer(minLength: 2)
                                        Button { store.archiveAgentMemory(memory) } label: {
                                            Label("归档", systemImage: "archivebox").labelStyle(.iconOnly)
                                        }
                                        Button { store.deleteAgentMemory(memory) } label: {
                                            Label("删除", systemImage: "trash").labelStyle(.iconOnly)
                                        }
                                    }
                                }
                            }
                        }
                        .padding(.top, SettingsFormStyle.groupSpacing)
                    } label: {
                        seesawPanelGroupHeader("管理记忆", count: store.agentMemories.count, trailing: "搜索、归档或删除")
                    }
                }
                .padding(SettingsFormStyle.detailHPadding)
                .padding(.bottom, SettingsFormStyle.detailVPadding)
            }
        }
        .background(theme.surface)
        .onAppear {
            Task { await store.loadAgentMemories() }
        }
    }

    private func seesawPanelSummary(
        systemImage: String,
        title: String,
        detail: String,
        status: String
    ) -> some View {
        HStack(spacing: SettingsFormStyle.rowHSpacing) {
            Image(systemName: systemImage)
                .font(KSSFont.themed(16, .semibold, theme: theme))
                .foregroundStyle(theme.accent)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: SettingsFormStyle.titleMetaSpacing) {
                Text(title)
                    .font(KSSFont.themed(SettingsFormStyle.itemTitle, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Text(detail)
                    .font(KSSFont.themed(SettingsFormStyle.bodyHint, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            SettingsStatusCapsule(text: status, tint: theme.accent)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: SettingsFormStyle.cardPadding)
    }

    private func seesawPanelGroupHeader(
        _ title: String,
        count: Int,
        trailing: String? = nil
    ) -> some View {
        HStack(spacing: 8) {
            Text(title)
                .font(KSSFont.themed(SettingsFormStyle.sectionHeader, .bold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            SettingsStatusCapsule(text: "\(count)")
            Spacer(minLength: 8)
            if let trailing {
                Text(trailing)
                    .font(KSSFont.themed(SettingsFormStyle.meta, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(.filled, padding: 8)
    }

    private func seesawPanelRow<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: SettingsFormStyle.cardPadding)
    }

    private func focusPanelHeader(title: String, action: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            Text(title)
                .font(KSSFont.themed(SettingsFormStyle.pageTitle, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Spacer()
            Button(action: action) {
                Label(title == "会话" ? "新建会话" : "刷新", systemImage: title == "会话" ? "plus" : "arrow.clockwise")
                    .labelStyle(.iconOnly)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.accent)
            Button { activeOverlay = nil } label: {
                Label("关闭", systemImage: "xmark")
                    .labelStyle(.iconOnly)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.textSecondary)
        }
        .padding(.horizontal, SettingsFormStyle.detailHPadding)
        .frame(height: SeesawXcomChrome.headerHeight)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var sessionSkills: [AgentSkill] {
        Array(store.agentSkills.filter { store.pinnedAgentSkillIds.contains($0.id) }.prefix(3))
    }

    private var enabledSkillCount: Int {
        store.agentSkills.filter { $0.enabled != false && $0.available != false }.count
    }

    private var availableSkillStarters: [SkillStarter] {
        skillStarters.filter { starter in
            // Bundled Skills use a repository-relative file id (for example
            // `.claude/skills/kss-review/SKILL.md`); the manifest name is the
            // stable public identity used by the starter catalogue.
            guard let skill = store.agentSkills.first(where: {
                $0.id == starter.skillId || $0.name == starter.skillId
            }) else { return false }
            return skill.enabled != false && skill.available != false
        }
    }

    private var visibleProviderModels: [ComposerModelOption] {
        store.agentProviders
            .filter(providerHasCredential)
            .flatMap { provider in
            (provider.models ?? []).compactMap { model in
                store.isSeesawModelVisible(providerID: provider.id, modelID: model.id)
                    ? ComposerModelOption(provider: provider, model: model)
                    : nil
            }
        }
    }

    private var filteredSessions: [AgentSession] {
        let query = sessionSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return store.agentSessions }
        return store.agentSessions.filter {
            $0.title.localizedCaseInsensitiveContains(query)
        }
    }

    private var selectedSkill: AgentSkill? {
        guard let selectedSkillId else { return nil }
        return store.agentSkills.first { $0.id == selectedSkillId }
    }

    private var filteredSkills: [AgentSkill] {
        let query = skillSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        return store.agentSkills
            .filter { skill in
                let matchesFilter: Bool
                switch skillFilter {
                case .all: matchesFilter = true
                case .enabled: matchesFilter = skill.enabled != false
                case .inConversation: matchesFilter = store.pinnedAgentSkillIds.contains(skill.id)
                }
                guard matchesFilter else { return false }
                guard !query.isEmpty else { return true }
                return [skill.name, skill.category ?? "", skill.source ?? ""]
                    .contains { $0.localizedCaseInsensitiveContains(query) }
            }
            .sorted { lhs, rhs in
                if lhs.id == selectedSkillId { return true }
                if rhs.id == selectedSkillId { return false }
                let lhsPinned = store.pinnedAgentSkillIds.contains(lhs.id)
                let rhsPinned = store.pinnedAgentSkillIds.contains(rhs.id)
                if lhsPinned != rhsPinned { return lhsPinned }
                return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
            }
    }

    private var contextUsageShort: String {
        store.agentContextUsage?.displayText ?? "上下文状态"
    }

    private var providerComposerLabel: String {
        let route = [
            store.agentPrimaryRoute?.providerId ?? store.agentProvider,
            store.agentPrimaryRoute?.modelId ?? store.agentModel,
        ]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return route.isEmpty ? "只解释 · 不荐买卖" : route
    }

    private var providerIssueDescription: String? {
        switch store.seesawProviderReadiness {
        case .ready:
            return nil
        case .missingRoute:
            return "先选择一个模型。Seesaw 会为本会话保存该选择。"
        case .missingCredential:
            return "还没有可用的 API Key。打开模型页面安全保存并测试连接。"
        case .brokerLoading:
            // 短暂过渡态,不值得占用 composer 打扰输入。
            return nil
        case .configuredUntested:
            // 已配置即可直接发送;连接测试是可选动作,不再常驻提示
            // (实测反馈:永远出现"检查模型"严重干扰)。
            return nil
        case let .failed(reason):
            return "模型连接失败：\(reason)。打开模型页面查看 Provider、模型与端点。"
        }
    }

    private func skillMetadata(_ skill: AgentSkill) -> String {
        var values = [skill.category ?? "general", skill.source ?? "project"]
        if let version = skill.version, version != "unversioned" { values.append(version) }
        if let trust = skill.trust { values.append(trust) }
        if skill.protected == true { values.append("受保护") }
        return values.joined(separator: " · ")
    }

    private func toggleOverlay(_ overlay: SeesawOverlay) {
        if activeOverlay == overlay {
            activeOverlay = nil
        } else {
            showInspectorDrawer = false
            activeOverlay = overlay
        }
    }




    private var canSend: Bool {
        pendingQueueClientMessageId == nil
            && store.seesawProviderReadiness.isReadyForComposer
            && (!input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                || (!store.isChatStreaming && !store.pendingAgentAttachments.isEmpty))
    }

    @ViewBuilder
    private func markdownText(_ s: String) -> Text {
        if let attr = try? AttributedString(
            markdown: s,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            return Text(attr)
        }
        return Text(s)
    }

    // MARK: - @file 引用（dsh-at-file 复刻）

    /// 输入末尾的 @token（无空白），nil 表示未处于引用输入态。
    private var activeAtFileToken: String? {
        guard let lastToken = input
            .components(separatedBy: .whitespacesAndNewlines)
            .last,
            lastToken.hasPrefix("@"),
            lastToken.count <= 64
        else { return nil }
        return String(lastToken.dropFirst())
    }

    private var atFilePanelVisible: Bool {
        activeAtFileToken != nil && !atFileResults.isEmpty
    }

    private func scheduleAtFileSearch() {
        atFileSearchTask?.cancel()
        guard let token = activeAtFileToken else {
            if !atFileResults.isEmpty { atFileResults = [] }
            return
        }
        atFileSearchTask = Task {
            try? await Task.sleep(nanoseconds: 120_000_000)
            guard !Task.isCancelled else { return }
            let hits = await store.searchWorkspaceFiles(query: token)
            guard !Task.isCancelled, activeAtFileToken == token else { return }
            atFileResults = hits
            atFileSelection = 0
        }
    }

    private func applyHighlightedAtFile() {
        guard atFilePanelVisible,
              atFileResults.indices.contains(atFileSelection)
        else { return }
        applyAtFileSelection(atFileResults[atFileSelection])
    }

    private func applyAtFileSelection(_ hit: WorkspaceFileHit) {
        if let token = activeAtFileToken,
           let range = input.range(of: "@" + token, options: .backwards) {
            input.removeSubrange(range)
            input = input.trimmingCharacters(in: .whitespaces).isEmpty
                ? ""
                : input
        }
        store.addPendingFileRef(hit.path)
        atFileResults = []
        isComposerFocused = true
    }

    @ViewBuilder
    private var atFileSuggestionPanel: some View {
        if atFilePanelVisible {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Label("引用工作区文件", systemImage: "at")
                        .font(KSSFont.themed(10.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    Spacer()
                    Text("↑↓ 选择 · ⏎/Tab 引用 · Esc 关闭")
                        .font(KSSFont.themed(9.5, theme: theme))
                        .foregroundStyle(theme.textSecondary.opacity(0.8))
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                Divider().overlay(theme.hairline)
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(atFileResults.enumerated()), id: \.element.id) { index, hit in
                            Button {
                                applyAtFileSelection(hit)
                            } label: {
                                HStack(spacing: 8) {
                                    Image(systemName: "doc.text")
                                        .font(.system(size: 11, weight: .medium))
                                        .foregroundStyle(theme.textSecondary)
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(hit.name)
                                            .font(KSSFont.themed(12, .semibold, theme: theme))
                                            .foregroundStyle(theme.textPrimary)
                                            .lineLimit(1)
                                        Text(hit.directory)
                                            .font(.system(size: 10, design: .monospaced))
                                            .foregroundStyle(theme.textSecondary)
                                            .lineLimit(1)
                                    }
                                    Spacer(minLength: 0)
                                }
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(
                                    index == atFileSelection ? theme.accentSoft : .clear,
                                    in: RoundedRectangle(cornerRadius: 7)
                                )
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .onHover { hovering in
                                if hovering { atFileSelection = index }
                            }
                        }
                    }
                    .padding(4)
                }
                .frame(maxHeight: 210)
            }
            .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11).stroke(theme.hairline))
        }
    }

    @ViewBuilder
    private var fileRefChips: some View {
        if !store.pendingFileRefs.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(store.pendingFileRefs, id: \.self) { path in
                        HStack(spacing: 5) {
                            Image(systemName: "at")
                                .font(.system(size: 9, weight: .bold))
                            Text((path as NSString).lastPathComponent)
                                .font(KSSFont.themed(11, .semibold, theme: theme))
                                .lineLimit(1)
                            Button {
                                store.removePendingFileRef(path)
                            } label: {
                                Image(systemName: "xmark")
                                    .font(.system(size: 8, weight: .bold))
                            }
                            .buttonStyle(.plain)
                        }
                        .foregroundStyle(theme.accent)
                        .padding(.horizontal, 9)
                        .frame(height: 24)
                        .background(theme.accentSoft, in: Capsule())
                        .help(path)
                    }
                }
            }
        }
    }

    /// modlens 入口之一：把剪贴板截图落成临时 PNG 并走现有附件导入，
    /// agent 侧即可用 vision_analyze 读取。
    private func handleComposerImagePaste() {
        let pasteboard = NSPasteboard.general
        guard let images = pasteboard.readObjects(forClasses: [NSImage.self]) as? [NSImage],
              let image = images.first,
              let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:])
        else { return }
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("kss-paste-\(UUID().uuidString)", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyyMMdd-HHmmss"
            let url = directory.appendingPathComponent("剪贴板图片-\(formatter.string(from: Date())).png")
            try png.write(to: url)
            Task {
                await store.importAgentAttachments([url])
                try? FileManager.default.removeItem(at: directory)
            }
        } catch {
            store.agentAttachmentError = "无法保存剪贴板图片：\(error.localizedDescription)"
        }
    }

    private func submitInput(mode: String) {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || (!store.isChatStreaming && !store.pendingAgentAttachments.isEmpty)
        else { return }
        if store.isChatStreaming {
            let queuedMode = (text.contains("为什么动") || text.contains("为什么涨") || text.contains("为什么跌"))
                ? "follow_up"
                : mode
            pendingQueueClientMessageId = store.enqueueAgentInput(
                text,
                mode: queuedMode,
                sourceQueueId: loadedQueueInputId)
            return
        }
        input = ""
        store.sendChat(text, sourceQueueId: loadedQueueInputId)
        loadedQueueInputId = nil
    }

    private var attachmentPickerButton: some View {
        Button {
            showAttachmentImporter = true
        } label: {
            Label("添加附件", systemImage: "paperclip")
                .labelStyle(.iconOnly)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(theme.textSecondary)
                .frame(width: 32, height: 32)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(store.isChatStreaming || store.isImportingAgentAttachment
                  || store.pendingAgentAttachments.count >= 4)
        .help(store.isChatStreaming ? "请在下一轮添加附件" : "添加图片、PDF 或文本附件")
    }

    @ViewBuilder
    private var pendingAttachmentStrip: some View {
        if store.isImportingAgentAttachment
            || !store.pendingAgentAttachments.isEmpty
            || store.agentAttachmentError != nil {
            VStack(alignment: .leading, spacing: 6) {
                if store.isImportingAgentAttachment {
                    Label("正在导入附件…", systemImage: "arrow.down.doc")
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(store.pendingAgentAttachments) { attachment in
                            HStack(spacing: 5) {
                                Image(systemName: attachment.mimeType?.hasPrefix("image/") == true
                                      ? "photo" : "doc.text")
                                Text(attachment.name).lineLimit(1)
                                Button {
                                    store.removePendingAgentAttachment(attachment)
                                } label: {
                                    Image(systemName: "xmark")
                                }
                                .buttonStyle(.plain)
                                .help("移除附件")
                            }
                            .font(KSSFont.themed(11, .medium, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .padding(.horizontal, 8)
                            .frame(height: 26)
                            .background(theme.surfaceContainer, in: Capsule())
                            .overlay(Capsule().stroke(theme.hairline))
                        }
                    }
                }
                if let error = store.agentAttachmentError, !error.isEmpty {
                    Text(error)
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(Color.red)
                }
            }
        }
    }

    @ViewBuilder
    private func messageAttachmentStrip(_ attachments: [AgentAttachment]) -> some View {
        if !attachments.isEmpty {
            FlowLayout(spacing: 6, lineSpacing: 6) {
                ForEach(attachments) { attachment in
                    HStack(spacing: 5) {
                        Image(systemName: attachment.mimeType?.hasPrefix("image/") == true
                              ? "photo" : "doc.text")
                        Text(attachment.name).lineLimit(1)
                        if let size = attachment.displaySize {
                            Text(size).foregroundStyle(theme.textSecondary)
                        }
                    }
                    .font(KSSFont.themed(11, .medium, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .padding(.horizontal, 8)
                    .frame(height: 26)
                    .background(theme.surfaceContainer, in: Capsule())
                    .overlay(Capsule().stroke(theme.hairline))
                }
            }
        }
    }

    @ViewBuilder
    private var queuedInputPanel: some View {
        if !store.agentQueuedInputs.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    if store.agentSteeringCount > 0 {
                        queueCountChip(label: "引导", count: store.agentSteeringCount)
                    }
                    if store.agentFollowUpCount > 0 {
                        queueCountChip(label: "后续", count: store.agentFollowUpCount)
                    }
                    Spacer()
                    Text("恢复的输入不会自动执行")
                        .font(KSSFont.themed(10.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                ForEach(store.agentQueuedInputs) { item in
                    HStack(spacing: 8) {
                        Text(item.mode == "steering" ? "引导" : "后续")
                            .font(KSSFont.themed(11, .semibold, theme: theme))
                            .foregroundStyle(theme.accent)
                        Text(item.content)
                            .font(KSSFont.themed(12, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .lineLimit(1)
                        Text(item.status == "restored" ? "已恢复" : "待处理")
                            .font(KSSFont.themed(10.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                        Spacer(minLength: 4)
                        Button("载回") {
                            input = item.content
                            loadedQueueInputId = item.id
                        }
                        .help("载入编辑器，确认后再发送")
                        Button {
                            store.discardQueuedInput(item)
                            if loadedQueueInputId == item.id {
                                loadedQueueInputId = nil
                            }
                        } label: {
                            Label("丢弃", systemImage: "xmark").labelStyle(.iconOnly)
                        }
                        .help("丢弃这条排队输入")
                    }
                    .buttonStyle(.borderless)
                    .padding(.horizontal, 10)
                    .frame(minHeight: 32)
                    .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }

    private func queueCountChip(label: String, count: Int) -> some View {
        Text("\(label) \(count)")
            .font(KSSFont.themed(11, .semibold, theme: theme))
            .foregroundStyle(theme.accent)
            .padding(.horizontal, 8)
            .frame(height: 24)
            .background(theme.accentSoft, in: Capsule())
    }

    private func memoryMetadata(_ memory: AgentMemoryRecord) -> String? {
        var parts: [String] = []
        if let kind = memory.kind, !kind.isEmpty { parts.append(kind) }
        let trueSource = [memory.sourceSession, memory.sourceEntry]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        if !trueSource.isEmpty {
            parts.append(trueSource)
        } else if let source = memory.source, !source.isEmpty {
            parts.append(source)
        }
        if let expiresAt = memory.expiresAt {
            parts.append("到期 \(Date(timeIntervalSince1970: expiresAt).formatted(date: .abbreviated, time: .omitted))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: "  ·  ")
    }

    private func recallMetadata(_ recall: AgentSourceRecall) -> String? {
        var parts: [String] = []
        let trueSource = [recall.sourceSession, recall.sourceEntry]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        if !trueSource.isEmpty {
            parts.append(trueSource)
        } else if let source = recall.source, !source.isEmpty {
            parts.append(source)
        }
        if recall.reviewRequired == true { parts.append("待复核") }
        if let score = recall.score { parts.append(String(format: "相关度 %.2f", score)) }
        if let expiresAt = recall.expiresAt {
            parts.append("到期 \(Date(timeIntervalSince1970: expiresAt).formatted(date: .abbreviated, time: .omitted))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: "  ·  ")
    }
}

/// Compact, collapsed-by-default rendering of provider-supplied reasoning.
/// It intentionally never derives "thinking" from the visible answer.
private struct AgentThinkingDisclosure: View {
    @Environment(\.kssTheme) private var theme
    @State private var isExpanded = false
    let blocks: [AgentContentBlock]

    private var visibleText: String {
        blocks.compactMap { block -> String? in
            if block.redacted == true && (block.text ?? "").isEmpty {
                return "（提供商返回了已隐藏的思考内容）"
            }
            return block.text
        }
        .filter { !$0.isEmpty }
        .joined(separator: "\n\n")
    }

    private var metadata: String? {
        let values = blocks
            .flatMap { [$0.provider, $0.model] }
            .compactMap { $0 }
            .filter { !$0.isEmpty }
        let unique = values.reduce(into: [String]()) { result, item in
            if !result.contains(item) { result.append(item) }
        }
        return unique.isEmpty ? nil : unique.joined(separator: " · ")
    }

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            if !visibleText.isEmpty {
                Text(visibleText)
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 4)
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "brain")
                Text("思考过程")
                if let metadata {
                    Text(metadata)
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
            }
            .font(KSSFont.themed(11, .semibold, theme: theme))
            .foregroundStyle(theme.accent)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 6)
        .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 10))
    }
}

/// U5：人在环内写确认 modal。显人话效果 + 参数 + 上下文；默认拒（dismiss=拒）。
struct WriteConfirmView: View {
    @Environment(\.kssTheme) private var theme
    let pending: PendingWriteConfirm
    let onResolve: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.shield").font(KSSFont.themed(18, theme: theme))
                    .foregroundStyle(theme.accent)
                Text("确认写操作").font(KSSFont.themed(16, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
            }
            Text(pending.effect)
                .font(KSSFont.themed(14, .semibold, theme: theme)).foregroundStyle(theme.textPrimary)
            if !pending.truthRows.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("真值预览").font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    ForEach(pending.truthRows) { row in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.label ?? row.title ?? "\(row.op ?? "") \(row.code ?? row.metricId ?? "")")
                                .font(KSSFont.themed(13, .semibold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                            HStack(spacing: 10) {
                                if let code = row.code {
                                    Text(code).font(.system(size: 11, design: .monospaced))
                                        .foregroundStyle(theme.textSecondary)
                                }
                                if let close = row.close {
                                    Text(String(format: "%.2f", close))
                                        .font(KSSFont.harmonyNumber(13))
                                        .foregroundStyle(theme.textPrimary)
                                }
                                if let pct = row.pct {
                                    Text(String(format: "%+.2f%%", pct))
                                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                                        .foregroundStyle(theme.signColor(pct))
                                }
                                if let vt = row.valueText {
                                    Text(vt).font(KSSFont.harmonyNumber(13))
                                        .foregroundStyle(theme.textPrimary)
                                }
                                Spacer(minLength: 0)
                            }
                        }
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                    }
                }
            }
            if !pending.argsText.isEmpty && pending.argsText != "{}" {
                VStack(alignment: .leading, spacing: 4) {
                    Text("参数").font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    Text(pending.argsText).font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(theme.textPrimary)
                        .textSelection(.enabled)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            }
            if !pending.contextLine.isEmpty {
                Text("助手上下文：\(pending.contextLine.suffix(140))")
                    .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    .lineLimit(3)
            }
            Text("命令：\(pending.command)").font(.system(size: 11, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            HStack(spacing: 12) {
                Spacer()
                Button("拒绝") { onResolve(false) }
                    .keyboardShortcut(.cancelAction)
                Button("确认执行") { onResolve(true) }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(22)
        .frame(width: 420)
        .background(theme.canvas)
    }
}
