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
    @State private var hovered: String?
    @State private var showSkillDrawer = false
    @State private var showMemoryDrawer = false
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
    /// 会话开场确定性候选建议（plan 2026-07-12-004 U9）；nil = 未加载或无候选，不显示 chip。
    @State private var indicatorSuggestion: IndicatorSuggestion?
    @FocusState private var isComposerFocused: Bool

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
            case .context: return "Context & Memory"
            }
        }
    }

    private enum SkillFilter: String, CaseIterable, Identifiable {
        case all = "全部"
        case enabled = "已启用"
        case pinned = "已置顶"

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

        var label: String { "\(provider.name ?? provider.id) · \(model.name ?? model.id)" }
    }

    /// family → 人话标签（bridge 只给基元族枚举名，不给展示文案）。
    private static let indicatorFamilyLabels: [String: String] = [
        "ma_cross": "均线交叉",
        "rsi_threshold": "RSI 阈值",
        "boll_atr": "布林·ATR 波动",
    ]

    /// 能力卡 = 把可用 Skill/编排剧本列出来让本人一目了然(点击即填问句发送)。
    private struct Capability: Identifiable {
        let id = UUID()
        let icon: String
        let title: String
        let desc: String
        let tag: String        // 背后的剧本/工具名,标在卡底
        let prompt: String
    }

    private let capabilities: [Capability] = [
        .init(icon: "chart.line.uptrend.xyaxis", title: "个股复盘",
              desc: "解释某只今天为什么动:个股 + 板块 + 主题龙头 + 发现命中",
              tag: "explain_stock_today", prompt: "688008 今天为什么动"),
        .init(icon: "square.grid.2x2", title: "板块轮动",
              desc: "板块上下文:轮动快照 + 近期历史 + 主题龙头梯队",
              tag: "sector_context", prompt: "今天哪个板块在轮动，龙头梯队如何"),
        .init(icon: "gauge.with.dots.needle.50percent", title: "今日看盘",
              desc: "大盘指数、推荐股、复盘/回测计数一览",
              tag: "get_snapshot", prompt: "今天大盘总体怎么样"),
        .init(icon: "books.vertical", title: "数据上手",
              desc: "有哪些数据集与可用工具、各自粒度与最近日期",
              tag: "get_orientation", prompt: "这个仓库有哪些数据和可用工具，先帮我上手"),
    ]

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
            .onDisappear {
                activeOverlay = nil
                globalNavigationExpanded = false
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

        return ZStack {
            theme.canvas.ignoresSafeArea()

            VStack(spacing: 0) {
                focusHeader(compact: compact, persistentInspector: persistentInspector)

                switch seesawPage {
                case .conversation:
                    HStack(spacing: 0) {
                        focusConversationWorkspace
                        .frame(maxWidth: SeesawXcomChrome.feedColumnWidth)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                        if persistentInspector {
                            Divider().overlay(theme.hairline)
                            focusInspector
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
                    focusInspector
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

    private func focusHeader(compact: Bool, persistentInspector: Bool) -> some View {
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
        .background(theme.surface.opacity(0.94))
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    // MARK: - OpenWorker-style inspector and Models workspace

    private var hasEvidenceOrAttachments: Bool {
        !store.pendingAgentAttachments.isEmpty || store.chatMessages.contains { $0.evidenceSummary.hasEvidence }
    }

    private var focusInspector: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if showInspectorDrawer {
                    HStack {
                        Text("工作台")
                            .font(KSSFont.themed(15, .bold, theme: theme))
                        Spacer()
                        Button { showInspectorDrawer = false } label: {
                            Label("关闭", systemImage: "xmark")
                                .labelStyle(.iconOnly)
                                .frame(width: 30, height: 30)
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(theme.textSecondary)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 14)
                    .overlay(alignment: .bottom) { Rectangle().fill(theme.hairline).frame(height: 1) }
                }

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
                    Text("\(pinnedSkills.count) 个置顶 · \(enabledSkillCount) 个启用")
                        .foregroundStyle(theme.textSecondary)
                    if !pinnedSkills.isEmpty {
                        ForEach(pinnedSkills) { skill in
                            Text(skill.name)
                                .font(KSSFont.themed(12, .semibold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                        }
                    }
                    Button("浏览 Skills…") { toggleOverlay(.skills) }
                        .buttonStyle(.borderless)
                        .foregroundStyle(theme.accent)
                }

                inspectorSection(.context, systemImage: "circle.dotted.circle", opens: .context) {
                    Text(contextUsageShort)
                        .foregroundStyle(theme.textSecondary)
                    Text(providerComposerLabel)
                        .foregroundStyle(theme.textSecondary)
                    if !store.agentSourceRecalls.isEmpty {
                        Text("本轮召回 \(store.agentSourceRecalls.count) 条记忆")
                            .foregroundStyle(theme.textSecondary)
                    }
                    Button("查看上下文与记忆…") { toggleOverlay(.context) }
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
                            ForEach(store.agentProviders) { provider in
                                providerCatalogCard(provider)
                            }
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 9) {
                    Text("路由规则")
                        .font(KSSFont.themed(14, .bold, theme: theme))
                    Text("全局默认只影响新会话；当前会话的模型选择独立保存。全局备用模型只会在主模型尚未输出正文、Thinking 或工具调用时接管。")
                        .font(KSSFont.themed(12.5, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    HStack(spacing: 12) {
                        routeSummary(title: "全局默认", route: store.agentGlobalPrimaryRoute)
                        routeSummary(title: "全局备用", route: store.agentFallbackRoute)
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
                ForEach(store.agentProviders.filter(providerHasCredential)) { provider in
                    if let models = provider.models, !models.isEmpty {
                        Section(provider.name ?? provider.id) {
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
        let label = [route.providerId, route.modelId]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return label.isEmpty ? "尚未配置" : label
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
                    Text(provider.name ?? provider.id)
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
        .accessibilityLabel("配置 \(provider.name ?? provider.id)")
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
        let title = provider?.name ?? providerID
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

    private func applySeesawDestination() {
        switch store.consumeSeesawDestination() {
        case .models?: seesawPage = .models
        case .conversation?: seesawPage = .conversation
        case nil: break
        }
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
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(theme.accent)
                    Text("今天想研究什么？")
                        .font(KSSFont.themed(25, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                }

                Text("选一个起点，或直接在下方描述你想弄清的市场问题。")
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
                LazyVStack(spacing: 18) {
                    ForEach(store.chatMessages) { message in
                        focusMessageCell(message)
                            .id(message.id)
                    }
                    if let tool = store.chatToolInProgress {
                        focusToolRow(tool)
                            .id("tool-progress")
                    }
                }
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.vertical, 22)
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
            HStack(alignment: .bottom) {
                Spacer(minLength: 52)
                VStack(alignment: .leading, spacing: 8) {
                    if !message.text.isEmpty {
                        markdownText(message.text)
                            .font(KSSFont.themed(15, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    messageAttachmentStrip(message.attachments)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 11)
                .frame(maxWidth: SeesawXcomChrome.feedColumnWidth * 0.78, alignment: .leading)
                .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 18))
                .contextMenu {
                    Button("记住这条消息") { store.proposeAgentMemory(message.text) }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 9) {
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
                }

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
                        .padding(.top, 2)
                }

                if let chart = message.chartAttachment, !chart.bars.isEmpty {
                    ChartWebView(points: [], intradayBars: chart.bars)
                        .frame(height: 300)
                        .background(theme.chartSurface)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contextMenu {
                Button("记住这条消息") { store.proposeAgentMemory(message.text) }
            }
        }
    }

    private func focusToolRow(_ tool: String) -> some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("正在调用 \(tool)…")
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
            Spacer()
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 10))
    }

    private var focusComposer: some View {
        VStack(alignment: .leading, spacing: 9) {
            composerInlineStatus
            queuedInputPanel
            pendingAttachmentStrip
            focusPinnedSkillChips

            TextField(
                store.chatMessages.isEmpty ? "问问盘面、个股或一个研究问题…" : "继续追问…",
                text: $input,
                axis: .vertical
            )
            .textFieldStyle(.plain)
            .font(KSSFont.themed(15, theme: theme))
            .foregroundStyle(theme.textPrimary)
            .focused($isComposerFocused)
            .lineLimit(2...6)
            .onKeyPress(.return, phases: .down, action: handleComposerReturn)

            composerControlBar
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: 20))
        .overlay {
            RoundedRectangle(cornerRadius: 20).stroke(theme.hairline)
        }
        .shadow(color: .black.opacity(theme.appearance == .dark ? 0.16 : 0.08), radius: 18, y: 7)
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private var composerInlineStatus: some View {
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

    private var composerControlBar: some View {
        HStack(spacing: 9) {
            attachmentPickerButton

            Button { toggleOverlay(.skills) } label: {
                Label("Skills", systemImage: "slider.horizontal.3")
                    .font(KSSFont.themed(11.5, .medium, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 8)
                    .frame(height: 28)
                    .background(theme.surfaceContainer, in: Capsule())
            }
            .buttonStyle(.plain)
            .help("浏览和管理 Skills")

            Spacer(minLength: 4)
            composerModelMenu

            if store.isChatStreaming {
                queueShortcutHint
                focusStopButton
            }

            focusSendButton
        }
        .frame(minHeight: 32)
    }

    private var composerModelMenu: some View {
        Menu {
            let visible = visibleProviderModels
            if visible.isEmpty {
                Button("在模型中心启用模型") { seesawPage = .models }
            } else {
                ForEach(visible, id: \.routeID) { item in
                    Button(item.label) {
                        selectSessionRoute(provider: item.provider, model: item.model)
                    }
                    .disabled(store.isChatStreaming)
                }
                Divider()
                Button("管理模型…") { seesawPage = .models }
            }
        } label: {
            Label(providerComposerLabel, systemImage: "cpu")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .lineLimit(1)
        }
        .menuStyle(.borderlessButton)
        .disabled(store.isChatStreaming)
        .help("本会话模型；管理可见模型与 Provider")
    }

    @ViewBuilder
    private var focusPinnedSkillChips: some View {
        if !pinnedSkills.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                ForEach(pinnedSkills) { skill in
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
                        Button("取消置顶") {
                            store.setAgentSkillPinned(skill, pinned: false)
                        }
                    }
                    .help("查看 \(skill.name) 技能详情")
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
        Button { submitInput(mode: "steering") } label: {
            Label(store.isChatStreaming ? "排队" : "发送", systemImage: store.isChatStreaming ? "arrow.turn.down.right" : "arrow.up")
                .labelStyle(.iconOnly)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 32, height: 32)
                .background(Circle().fill(canSend ? theme.accent : theme.accent.opacity(0.4)))
        }
        .buttonStyle(.plain)
        .disabled(!canSend)
        .help(store.isChatStreaming ? "将输入排入本轮" : "发送")
    }

    private var focusStopButton: some View {
        Button { store.stopChatGeneration() } label: {
            Label("停止", systemImage: "stop.fill")
                .labelStyle(.iconOnly)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 32, height: 32)
                .background(Circle().fill(Color.red))
        }
        .buttonStyle(.plain)
        .help("停止生成")
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
                        let selected = session.sessionId == store.selectedAgentSessionId
                        HStack(spacing: 8) {
                            Button {
                                store.openAgentSession(session.sessionId)
                                activeOverlay = nil
                                isComposerFocused = true
                            } label: {
                                Image(systemName: selected ? "bubble.left.and.bubble.right.fill" : "bubble.left")
                                    .foregroundStyle(selected ? theme.accent : theme.textSecondary)
                                    .frame(width: 28, height: 28)
                            }
                            .buttonStyle(.plain)

                            TextField("会话名", text: Binding(
                                get: { store.agentSessions.first(where: { $0.sessionId == session.sessionId })?.title ?? session.title },
                                set: { store.renameAgentSession(session.sessionId, title: $0) }
                            ))
                            .textFieldStyle(.plain)
                            .font(KSSFont.themed(13.5, selected ? .semibold : .regular, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .lineLimit(1)

                            Button { store.archiveAgentSession(session.sessionId) } label: {
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

                    HStack(spacing: SettingsFormStyle.rowHSpacing) {
                        Picker("技能筛选", selection: $skillFilter) {
                            ForEach(SkillFilter.allCases) { filter in
                                Text(filter.rawValue).tag(filter)
                            }
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                        .frame(maxWidth: 280)

                        Spacer(minLength: 0)
                        SettingsStatusCapsule(
                            text: "(enabledSkillCount) 个启用",
                            tint: theme.accent
                        )
                        SettingsStatusCapsule(text: "(pinnedSkills.count) 个置顶")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(.filled, padding: 8)

                    seesawPanelSummary(
                        systemImage: "slider.horizontal.3",
                        title: "技能工作区",
                        detail: "置顶的技能会随本会话注入；启用状态在所有会话中共享。",
                        status: "(filteredSkills.count) 项可见"
                    )

                    if let selected = selectedSkill {
                        Label("当前查看：\(selected.name)", systemImage: "pin.fill")
                            .font(KSSFont.themed(SettingsFormStyle.bodyHint, .semibold, theme: theme))
                            .foregroundStyle(theme.accent)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .kssCard(.info, padding: SettingsFormStyle.bannerPadding)
                    }

                    seesawPanelGroupHeader("技能目录", count: filteredSkills.count, trailing: "来源 · 信任 · 所需工具")

                    LazyVStack(spacing: SettingsFormStyle.groupSpacing) {
                        ForEach(filteredSkills) { skill in
                            focusSkillRow(skill)
                        }
                    }

                    if filteredSkills.isEmpty {
                        SettingsHintText(text: "没有匹配的技能", empty: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .kssCard(padding: SettingsFormStyle.cardPadding)
                    }

                    if !store.agentSkillDiagnostics.isEmpty {
                        seesawPanelGroupHeader("诊断", count: store.agentSkillDiagnostics.count)
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

    private func focusSkillRow(_ skill: AgentSkill) -> some View {
        let isPinned = store.pinnedAgentSkillIds.contains(skill.id)
        let exceedsPinLimit = !isPinned && pinnedSkills.count >= 3

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
                    } else if exceedsPinLimit {
                        Text("每个会话最多置顶 3 个技能")
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

                    Toggle("置顶", isOn: Binding(
                        get: { isPinned },
                        set: { store.setAgentSkillPinned(skill, pinned: $0) }
                    ))
                    .disabled(exceedsPinLimit)
                }
                .toggleStyle(.checkbox)
                .font(KSSFont.themed(SettingsFormStyle.actionLabel, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .frame(width: 84, alignment: .leading)
            }
        }
    }

    private var focusContextPopover: some View {
        VStack(spacing: 0) {
            focusPanelHeader(title: "上下文") {
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

    private var pinnedSkills: [AgentSkill] {
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
                case .pinned: matchesFilter = store.pinnedAgentSkillIds.contains(skill.id)
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
            return "正在读取本机凭据与模型目录…"
        case .configuredUntested:
            // A saved Keychain record is usable even before an explicit test;
            // communicate the state without blocking a normal first send.
            return "模型已配置，建议先运行一次连接测试。"
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

    private func xcomSeesawShell(size: CGSize) -> some View {
        let showsSidebar = size.width >= 980
        let showsUtilityPanel = (showSkillDrawer || showMemoryDrawer)
            && size.width >= SeesawXcomChrome.minimumThreeColumnWidth
        let reserved = (showsSidebar ? SeesawXcomChrome.sessionRailWidth : 0)
            + (showsUtilityPanel ? SeesawXcomChrome.utilityPanelWidth : 0)
        let available = max(420, size.width - reserved)
        let feedWidth = min(SeesawXcomChrome.feedColumnWidth, available)

        return HStack(spacing: 0) {
            if showsSidebar {
                xcomAgentSidebar
                    .frame(width: SeesawXcomChrome.sessionRailWidth)
            }

            VStack(spacing: 0) {
                xcomHeader
                if store.chatMessages.isEmpty {
                    xcomEmptyTimeline
                } else {
                    xcomMessageList
                    xcomConversationComposer
                }
            }
            .frame(width: feedWidth)
            .frame(maxHeight: .infinity)
            .background(theme.surface)
            .overlay(alignment: .leading) {
                Rectangle().fill(theme.hairline).frame(width: 1)
            }
            .overlay(alignment: .trailing) {
                Rectangle().fill(theme.hairline).frame(width: 1)
            }

            if showsUtilityPanel {
                xcomUtilityPanel
                    .frame(width: SeesawXcomChrome.utilityPanelWidth)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .background(theme.canvas.ignoresSafeArea())
        .overlay(alignment: .trailing) {
            if (showSkillDrawer || showMemoryDrawer) && !showsUtilityPanel {
                xcomUtilityPanel
                    .frame(width: min(SeesawXcomChrome.utilityPanelWidth, size.width * 0.88))
                    .overlay(alignment: .leading) {
                        Rectangle().fill(theme.hairline).frame(width: 1)
                    }
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.18), value: showSkillDrawer)
        .animation(.easeOut(duration: 0.18), value: showMemoryDrawer)
    }

    private func classicSeesawShell(size: CGSize) -> some View {
        let showsSidebar = size.width >= 1040
        let width = min(size.width - (showsSidebar ? 336 : 64), 820)

        return HStack(spacing: 0) {
            if showsSidebar {
                agentSidebar
                    .frame(width: 272)
                    .background(theme.surfaceContainer)
                    .overlay(alignment: .trailing) {
                        Rectangle().fill(theme.hairline).frame(width: 1)
                    }
            }
            ZStack {
                theme.canvas.ignoresSafeArea()
                VStack(spacing: 0) {
                    agentTopBar(width: width, compact: !showsSidebar)
                    if store.chatMessages.isEmpty {
                        heroEmptyState(width: width)
                    } else {
                        messageList(width: width)
                        pinnedInputBar(width: width)
                    }
                }
            }
        }
    }

    // MARK: - x.com Seesaw shell

    private var xcomAgentSidebar: some View {
        VStack(spacing: 0) {
            HStack {
                Text("会话")
                    .font(KSSFont.themed(20, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                Button { store.createAgentSession() } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 16, weight: .semibold))
                        .frame(width: 36, height: 36)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(theme.accent)
                .help("新建会话")
            }
            .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
            .frame(height: SeesawXcomChrome.headerHeight)

            Rectangle().fill(theme.hairline).frame(height: 1)

            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(store.agentSessions) { session in
                        xcomSessionRow(session)
                    }
                }
            }

            Rectangle().fill(theme.hairline).frame(height: 1)

            VStack(spacing: 0) {
                xcomSidebarAction(
                    title: "技能",
                    systemImage: "slider.horizontal.3",
                    isSelected: showSkillDrawer
                ) {
                    showMemoryDrawer = false
                    showSkillDrawer.toggle()
                }
                xcomSidebarAction(
                    title: "记忆",
                    systemImage: "tray.full",
                    isSelected: showMemoryDrawer
                ) {
                    showSkillDrawer = false
                    showMemoryDrawer.toggle()
                }
                if store.agentProtocolUnavailable {
                    Label("Agent v1 暂不可用，已回退旧聊天", systemImage: "exclamationmark.triangle")
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                        .padding(.vertical, 10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .background(theme.surface)
        .overlay(alignment: .trailing) {
            Rectangle().fill(theme.hairline).frame(width: 1)
        }
    }

    private func xcomSessionRow(_ session: AgentSession) -> some View {
        let selected = store.selectedAgentSessionId == session.sessionId
        let hoverKey = "session:\(session.sessionId)"

        return HStack(spacing: 12) {
            Button { store.openAgentSession(session.sessionId) } label: {
                Image(systemName: selected ? "bubble.left.and.bubble.right.fill" : "bubble.left")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(selected ? theme.accent : theme.textSecondary)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .help("打开会话")

            TextField("会话名", text: Binding(
                get: { store.agentSessions.first(where: { $0.sessionId == session.sessionId })?.title ?? session.title },
                set: { store.renameAgentSession(session.sessionId, title: $0) }
            ))
            .textFieldStyle(.plain)
            .font(KSSFont.themed(15, selected ? .semibold : .regular, theme: theme))
            .foregroundStyle(theme.textPrimary)
            .lineLimit(1)

            Button { store.archiveAgentSession(session.sessionId) } label: {
                Image(systemName: "archivebox")
                    .font(.system(size: 14))
                    .foregroundStyle(theme.textSecondary)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .help("归档会话")
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .frame(minHeight: 52)
        .background(
            XcomListChrome.listSelectionFill(
                isOn: selected,
                isHovered: hovered == hoverKey,
                theme: theme
            )
        )
        .contentShape(Rectangle())
        .onTapGesture { store.openAgentSession(session.sessionId) }
        .onHover { isHovered in
            hovered = isHovered ? hoverKey : (hovered == hoverKey ? nil : hovered)
        }
    }

    private func xcomSidebarAction(
        title: String,
        systemImage: String,
        isSelected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: systemImage)
                    .font(.system(size: 16, weight: .medium))
                    .frame(width: 32)
                Text(title)
                    .font(KSSFont.themed(15, .semibold, theme: theme))
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(theme.textSecondary)
            }
            .foregroundStyle(isSelected ? theme.accent : theme.textPrimary)
            .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
            .frame(minHeight: 48)
            .contentShape(Rectangle())
            .background(isSelected ? theme.accentSoft : Color.clear)
        }
        .buttonStyle(.plain)
    }

    private var xcomHeader: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(store.agentSessions.first { $0.sessionId == store.selectedAgentSessionId }?.title ?? "Seesaw")
                    .font(KSSFont.themed(20, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1)
                if let usage = store.agentContextUsage {
                    Text(usage.displayText)
                        .font(KSSFont.themed(12, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                if store.agentProvider != nil || store.agentModel != nil {
                    Text([store.agentProvider, store.agentModel]
                        .compactMap { $0 }
                        .joined(separator: " · "))
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 8)

            Button {
                showMemoryDrawer = false
                showSkillDrawer.toggle()
            } label: {
                Label("技能", systemImage: "slider.horizontal.3")
                    .labelStyle(.iconOnly)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 36, height: 36)
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(showSkillDrawer ? theme.accent : theme.textPrimary)
            .help("技能")

            Button {
                showSkillDrawer = false
                showMemoryDrawer.toggle()
            } label: {
                Label("记忆", systemImage: "tray.full")
                    .labelStyle(.iconOnly)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 36, height: 36)
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(showMemoryDrawer ? theme.accent : theme.textPrimary)
            .help("记忆")

            if store.isChatStreaming {
                Button { store.stopChatGeneration() } label: {
                    Label("停止", systemImage: "stop.fill")
                        .labelStyle(.iconOnly)
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 36, height: 36)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.red)
                .help("停止")
            }
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .frame(height: SeesawXcomChrome.headerHeight)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var xcomEmptyTimeline: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                xcomComposer

                if store.isCredentialConfigured("llm") == false {
                    VStack(spacing: 0) {
                        MissingCredentialCard(sourceDisplayName: "LLM key") {
                            store.openSeesawModels()
                        }
                        .padding(SeesawXcomChrome.rowHorizontalPadding)
                        Rectangle().fill(theme.hairline).frame(height: 1)
                    }
                }

                if indicatorSuggestion?.family != nil {
                    xcomIndicatorSuggestion
                }
                if store.researchCandidate != nil {
                    xcomResearchCandidate
                }

                HStack {
                    Text("开始研究")
                        .font(KSSFont.themed(15, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Spacer()
                    Text("选择一个入口，或直接提问")
                        .font(KSSFont.themed(13, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .frame(minHeight: 48)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(theme.hairline).frame(height: 1)
                }

                ForEach(capabilities) { capability in
                    xcomCapabilityRow(capability)
                }
            }
        }
        .background(theme.surface)
    }

    private var xcomComposer: some View {
        VStack(alignment: .leading, spacing: 10) {
            queuedInputPanel
            pendingAttachmentStrip

            HStack(alignment: .top, spacing: 12) {
                xcomIdentityIcon(systemImage: "sparkles", accent: theme.accent)

                VStack(alignment: .leading, spacing: 12) {
                    TextField("问问盘面…", text: $input, axis: .vertical)
                        .textFieldStyle(.plain)
                        .font(KSSFont.themed(15, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .lineLimit(2...6)
                        .onKeyPress(.return, phases: .down, action: handleComposerReturn)

                    HStack(spacing: 8) {
                        attachmentPickerButton
                        Label("只解释 · 不荐买卖", systemImage: "shield.lefthalf.filled")
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                        Spacer()
                        if store.isChatStreaming {
                            queueShortcutHint
                            xcomSendButton
                            xcomStopButton
                        } else {
                            xcomSendButton
                        }
                    }
                }
            }
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
        .frame(minHeight: 116, alignment: .top)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    @ViewBuilder
    private var xcomIndicatorSuggestion: some View {
        if let suggestion = indicatorSuggestion, let family = suggestion.family {
            let label = Self.indicatorFamilyLabels[family] ?? family
            Button {
                let reason = suggestion.reason.map { "：\($0)" } ?? ""
                input = "帮我回测 \(label)\(reason)"
                send()
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    xcomIdentityIcon(systemImage: "sparkles", accent: theme.accent)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 5) {
                            Text("Seesaw")
                                .font(KSSFont.themed(15, .bold, theme: theme))
                            Text("建议")
                                .font(KSSFont.themed(13, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                        Text("研究一下\(label)")
                            .font(KSSFont.themed(15, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        if let reason = suggestion.reason, !reason.isEmpty {
                            Text(reason)
                                .font(KSSFont.themed(13, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.top, 3)
                }
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(store.isChatStreaming)
            .overlay(alignment: .bottom) {
                Rectangle().fill(theme.hairline).frame(height: 1)
            }
        }
    }

    @ViewBuilder
    private var xcomResearchCandidate: some View {
        if let candidate = store.researchCandidate {
            Button {
                store.selectedSection = .runbook
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    xcomIdentityIcon(systemImage: "scope", accent: theme.accent)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("转为深度研究")
                            .font(KSSFont.themed(15, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text(candidate.objective)
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Image(systemName: "arrow.right")
                        .foregroundStyle(theme.textSecondary)
                }
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .overlay(alignment: .bottom) {
                Rectangle().fill(theme.hairline).frame(height: 1)
            }
        }
    }

    private func xcomCapabilityRow(_ capability: Capability) -> some View {
        let hoverKey = "capability:\(capability.tag)"

        return Button {
            input = capability.prompt
            send()
        } label: {
            HStack(alignment: .top, spacing: 12) {
                xcomIdentityIcon(systemImage: capability.icon, accent: theme.accent)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(capability.title)
                            .font(KSSFont.themed(15, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text(capability.tag)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(1)
                    }
                    Text(capability.desc)
                        .font(KSSFont.themed(15, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.top, 4)
            }
            .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
            .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
            .frame(minHeight: 78, alignment: .top)
            .contentShape(Rectangle())
            .background(hovered == hoverKey ? theme.surfaceContainer : Color.clear)
        }
        .buttonStyle(.plain)
        .disabled(store.isChatStreaming)
        .onHover { isHovered in
            hovered = isHovered ? hoverKey : (hovered == hoverKey ? nil : hovered)
        }
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var xcomMessageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(store.chatMessages) { message in
                        xcomMessageCell(message)
                            .id(message.id)
                    }
                    if let tool = store.chatToolInProgress {
                        xcomToolRow(tool)
                            .id("tool-progress")
                    }
                }
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

    private func xcomMessageCell(_ message: ChatMessage) -> some View {
        let isUser = message.role == .user

        return HStack(alignment: .top, spacing: 12) {
            xcomIdentityIcon(
                systemImage: isUser ? "person.fill" : "sparkles",
                accent: isUser ? theme.textSecondary : theme.accent
            )

            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 5) {
                    Text(isUser ? "你" : "Seesaw")
                        .font(KSSFont.themed(15, .bold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    Text(isUser ? "提问" : "研究助手")
                        .font(KSSFont.themed(13, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    Spacer()
                }

                if message.text.isEmpty && store.isChatStreaming {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("思考中…")
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                    }
                } else if !message.text.isEmpty {
                    markdownText(message.text)
                        .font(KSSFont.themed(15, theme: theme))
                        .foregroundStyle(message.isError ? Color.red : theme.textPrimary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if !message.thinkingBlocks.isEmpty {
                    AgentThinkingDisclosure(blocks: message.thinkingBlocks)
                }

                messageAttachmentStrip(message.attachments)

                if message.numbersUnverified && store.isChatStreaming {
                    Label("数字校验中（以工具真值为准）", systemImage: "exclamationmark.triangle")
                        .font(KSSFont.themed(13, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }

                if message.evidenceSummary.hasEvidence || message.evidenceSummary.provider != nil {
                    EvidenceDrawerView(summary: message.evidenceSummary, drawer: message.evidenceDrawer)
                        .padding(.top, 4)
                }

                if let chart = message.chartAttachment, !chart.bars.isEmpty {
                    ChartWebView(points: [], intradayBars: chart.bars)
                        .frame(height: 300)
                        .background(theme.chartSurface)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .padding(.top, 6)
                }
            }
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .contextMenu {
            Button("记住这条消息") { store.proposeAgentMemory(message.text) }
        }
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private func xcomToolRow(_ tool: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            xcomIdentityIcon(systemImage: "wrench.and.screwdriver", accent: theme.accent)
            VStack(alignment: .leading, spacing: 4) {
                Text("工具调用")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在调用 \(tool)…")
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
            }
            Spacer()
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var xcomConversationComposer: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let provider = latestResearchProvider {
                HStack {
                    Label(
                        provider == "disabled" ? "外部研究不可用" : "外部研究 · \(provider)",
                        systemImage: provider == "disabled" ? "wifi.slash" : "link"
                    )
                    .font(KSSFont.themed(12, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    Spacer()
                }
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.top, 8)
            }

            queuedInputPanel
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.top, store.agentQueuedInputs.isEmpty ? 0 : 8)

            pendingAttachmentStrip
                .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                .padding(.top, store.pendingAgentAttachments.isEmpty ? 0 : 8)

            HStack(alignment: .bottom, spacing: 10) {
                attachmentPickerButton
                TextField("继续问…", text: $input, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(KSSFont.themed(15, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1...5)
                    .onKeyPress(.return, phases: .down, action: handleComposerReturn)
                if store.isChatStreaming {
                    xcomStopButton
                }
                xcomSendButton
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 18))
            .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
            .padding(.vertical, 10)

            Text(store.isChatStreaming
                 ? "↩ 引导本轮 · ⌥↩ 排到本轮结束后"
                 : "AI 仅解释与复盘，不给个性化买卖建议")
                .font(KSSFont.themed(11, theme: theme))
                .foregroundStyle(theme.textSecondary)
                .frame(maxWidth: .infinity)
                .padding(.bottom, 8)
        }
        .background(theme.surface)
        .overlay(alignment: .top) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var xcomSendButton: some View {
        Button { submitInput(mode: "steering") } label: {
            Text(store.isChatStreaming ? "排队" : "发送")
                .font(KSSFont.themed(13, .bold, theme: theme))
                .foregroundStyle(.white)
                .padding(.horizontal, 16)
                .frame(height: 32)
                .background(canSend ? theme.accent : theme.accent.opacity(0.4), in: Capsule())
        }
        .buttonStyle(.plain)
        .disabled(!canSend)
    }

    private var xcomStopButton: some View {
        Button { store.stopChatGeneration() } label: {
            Label("停止", systemImage: "stop.fill")
                .font(KSSFont.themed(13, .bold, theme: theme))
                .foregroundStyle(.white)
                .padding(.horizontal, 14)
                .frame(height: 32)
                .background(Color.red, in: Capsule())
        }
        .buttonStyle(.plain)
    }

    private func xcomIdentityIcon(systemImage: String, accent: Color) -> some View {
        Circle()
            .fill(accent.opacity(0.12))
            .frame(width: SeesawXcomChrome.avatarSize, height: SeesawXcomChrome.avatarSize)
            .overlay {
                Image(systemName: systemImage)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(accent)
            }
    }

    @ViewBuilder
    private var xcomUtilityPanel: some View {
        if showSkillDrawer {
            xcomSkillPanel
        } else if showMemoryDrawer {
            xcomMemoryPanel
        }
    }

    private var xcomSkillPanel: some View {
        VStack(spacing: 0) {
            xcomPanelHeader(title: "技能管理", systemImage: "arrow.clockwise") {
                store.reloadAgentSkills()
            } close: {
                showSkillDrawer = false
            }

            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(store.agentSkills) { skill in
                        HStack(alignment: .top, spacing: 12) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(skill.name)
                                    .font(KSSFont.themed(15, .bold, theme: theme))
                                    .foregroundStyle(theme.textPrimary)
                                if let description = skill.description, !description.isEmpty {
                                    Text(description)
                                        .font(KSSFont.themed(13, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                HStack(spacing: 6) {
                                    Text(skill.category ?? "general")
                                    Text(skill.source ?? "project")
                                    if let version = skill.version, version != "unversioned" {
                                        Text(version)
                                    }
                                    if let trust = skill.trust { Text(trust) }
                                    if skill.protected == true {
                                        Image(systemName: "lock.fill")
                                    }
                                }
                                .font(KSSFont.themed(10.5, .medium, theme: theme))
                                .foregroundStyle(theme.textSecondary.opacity(0.82))
                                if skill.available == false {
                                    Text("缺少工具：\((skill.missingRequiredTools ?? []).joined(separator: "、"))")
                                        .font(KSSFont.themed(11, .medium, theme: theme))
                                        .foregroundStyle(.orange)
                                }
                            }

                            Spacer(minLength: 8)

                            VStack(alignment: .leading, spacing: 8) {
                                Toggle("启用", isOn: Binding(
                                    get: { skill.enabled != false },
                                    set: { store.setAgentSkillEnabled(skill, enabled: $0) }
                                ))
                                Toggle("置顶", isOn: Binding(
                                    get: { store.pinnedAgentSkillIds.contains(skill.id) },
                                    set: { store.setAgentSkillPinned(skill, pinned: $0) }
                                ))
                            }
                            .toggleStyle(.checkbox)
                            .font(KSSFont.themed(13, .semibold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .fixedSize()
                        }
                        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                        .padding(.vertical, SeesawXcomChrome.rowVerticalPadding)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(theme.hairline).frame(height: 1)
                        }
                    }

                    if !store.agentSkillDiagnostics.isEmpty {
                        HStack {
                            Text("诊断")
                                .font(KSSFont.themed(15, .bold, theme: theme))
                            Spacer()
                            Text("\(store.agentSkillDiagnostics.count)")
                                .font(KSSFont.themed(13, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                        .frame(minHeight: 48)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(theme.hairline).frame(height: 1)
                        }

                        ForEach(store.agentSkillDiagnostics) { diagnostic in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(diagnostic.code)
                                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                                    .foregroundStyle(Color.red)
                                Text(diagnostic.message)
                                    .font(KSSFont.themed(13, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
                            }
                            .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
                            .padding(.vertical, 10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .overlay(alignment: .bottom) {
                                Rectangle().fill(theme.hairline).frame(height: 1)
                            }
                        }
                    }
                }
            }
        }
        .background(theme.surface)
    }

    private var xcomMemoryPanel: some View {
        VStack(spacing: 0) {
            xcomPanelHeader(title: "记忆", systemImage: "magnifyingglass") {
                Task { await store.loadAgentMemories(query: memorySearch) }
                store.recallAgentSources(query: memorySearch)
            } close: {
                showMemoryDrawer = false
            }

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(theme.textSecondary)
                TextField("搜索记忆或来源", text: $memorySearch)
                    .textFieldStyle(.plain)
                    .font(KSSFont.themed(14, theme: theme))
                    .onSubmit {
                        Task { await store.loadAgentMemories(query: memorySearch) }
                        store.recallAgentSources(query: memorySearch)
                    }
            }
            .padding(.horizontal, 12)
            .frame(height: 38)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 19))
            .padding(SeesawXcomChrome.rowHorizontalPadding)
            .overlay(alignment: .bottom) {
                Rectangle().fill(theme.hairline).frame(height: 1)
            }

            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(store.agentMemoryCandidates) { candidate in
                        VStack(alignment: .leading, spacing: 8) {
                            Text("待确认")
                                .font(KSSFont.themed(13, .bold, theme: theme))
                                .foregroundStyle(theme.accent)
                            Text(candidate.text)
                                .font(KSSFont.themed(15, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                            HStack {
                                Button("记住") { store.resolveMemoryCandidate(candidate, approved: true) }
                                    .buttonStyle(.borderedProminent)
                                Button("忽略") { store.resolveMemoryCandidate(candidate, approved: false) }
                                    .buttonStyle(.borderless)
                            }
                            .font(KSSFont.themed(13, .semibold, theme: theme))
                        }
                        .padding(SeesawXcomChrome.rowHorizontalPadding)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(theme.hairline).frame(height: 1)
                        }
                    }

                    ForEach(store.agentMemories) { memory in
                        HStack(alignment: .top, spacing: 10) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(memory.text)
                                    .font(KSSFont.themed(15, theme: theme))
                                    .foregroundStyle(theme.textPrimary)
                                if memory.reviewRequired == true {
                                    Text("待复核")
                                        .font(KSSFont.themed(11, .semibold, theme: theme))
                                        .foregroundStyle(Color.orange)
                                }
                                if let metadata = memoryMetadata(memory) {
                                    Text(metadata)
                                        .font(KSSFont.themed(12, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                }
                            }
                            Spacer()
                            Button { store.archiveAgentMemory(memory) } label: {
                                Label("归档", systemImage: "archivebox").labelStyle(.iconOnly)
                            }
                            Button { store.deleteAgentMemory(memory) } label: {
                                Label("删除", systemImage: "trash").labelStyle(.iconOnly)
                            }
                            .foregroundStyle(Color.red)
                        }
                        .buttonStyle(.plain)
                        .padding(SeesawXcomChrome.rowHorizontalPadding)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(theme.hairline).frame(height: 1)
                        }
                    }

                    ForEach(store.agentSourceRecalls) { recall in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(recall.title)
                                .font(KSSFont.themed(15, .bold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                            if let metadata = recallMetadata(recall) {
                                Text(metadata)
                                    .font(KSSFont.themed(11, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
                            }
                            if let excerpt = recall.excerpt, !excerpt.isEmpty {
                                Text(excerpt)
                                    .font(KSSFont.themed(13, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
                            }
                        }
                        .padding(SeesawXcomChrome.rowHorizontalPadding)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(theme.hairline).frame(height: 1)
                        }
                    }
                }
            }
        }
        .background(theme.surface)
    }

    private func xcomPanelHeader(
        title: String,
        systemImage: String,
        action: @escaping () -> Void,
        close: @escaping () -> Void
    ) -> some View {
        HStack(spacing: 8) {
            Text(title)
                .font(KSSFont.themed(20, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Spacer()
            Button(action: action) {
                Image(systemName: systemImage)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 36, height: 36)
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.accent)
            Button(action: close) {
                Image(systemName: "xmark")
                    .font(.system(size: 13, weight: .semibold))
                    .frame(width: 36, height: 36)
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.textPrimary)
        }
        .padding(.horizontal, SeesawXcomChrome.rowHorizontalPadding)
        .frame(height: SeesawXcomChrome.headerHeight)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private var agentSidebar: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("会话")
                    .font(KSSFont.themed(13, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Spacer()
                Button { store.createAgentSession() } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("新建会话")
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(store.agentSessions) { session in
                        agentSessionRow(session)
                    }
                }
            }
            Divider().overlay(theme.hairline)
            agentUtilityButtons
        }
        .padding(14)
    }

    private func agentSessionRow(_ session: AgentSession) -> some View {
        HStack(spacing: 8) {
            Button { store.openAgentSession(session.sessionId) } label: {
                Image(systemName: store.selectedAgentSessionId == session.sessionId ? "bubble.left.and.bubble.right.fill" : "bubble.left")
                    .foregroundStyle(store.selectedAgentSessionId == session.sessionId ? theme.accent : theme.textSecondary)
            }
            .buttonStyle(.plain)
            TextField("会话名", text: Binding(
                get: { store.agentSessions.first(where: { $0.sessionId == session.sessionId })?.title ?? session.title },
                set: { store.renameAgentSession(session.sessionId, title: $0) }
            ))
            .textFieldStyle(.plain)
            .font(KSSFont.themed(12.5, .semibold, theme: theme))
            .foregroundStyle(theme.textPrimary)
            Button { store.archiveAgentSession(session.sessionId) } label: {
                Image(systemName: "archivebox")
                    .foregroundStyle(theme.textSecondary)
            }
            .buttonStyle(.plain)
            .help("归档会话")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            store.selectedAgentSessionId == session.sessionId ? theme.accentSoft : theme.surface,
            in: RoundedRectangle(cornerRadius: KSSTheme.shapeS)
        )
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeS).stroke(theme.hairline))
    }

    private var agentUtilityButtons: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { showSkillDrawer.toggle() } label: {
                Label("技能", systemImage: "slider.horizontal.3")
            }
            Button { showMemoryDrawer.toggle() } label: {
                Label("记忆", systemImage: "tray.full")
            }
            if store.agentProtocolUnavailable {
                Label("Agent v1 暂不可用，已回退旧聊天", systemImage: "exclamationmark.triangle")
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
        }
        .font(KSSFont.themed(12, .semibold, theme: theme))
        .buttonStyle(.plain)
        .popover(isPresented: $showSkillDrawer) { skillDrawer.frame(width: 360, height: 420) }
        .popover(isPresented: $showMemoryDrawer) { memoryDrawer.frame(width: 420, height: 520) }
    }

    private func agentTopBar(width: CGFloat, compact: Bool) -> some View {
        VStack(spacing: 8) {
            HStack(spacing: 10) {
                if compact {
                    Picker("会话", selection: Binding(
                        get: { store.selectedAgentSessionId ?? "" },
                        set: { store.openAgentSession($0) }
                    )) {
                        ForEach(store.agentSessions) { session in
                            Text(session.title).tag(session.sessionId)
                        }
                    }
                    .frame(width: min(width * 0.48, 240))
                    Button { store.createAgentSession() } label: { Image(systemName: "plus") }
                        .buttonStyle(.borderless)
                }
                skillChipRow
                Spacer()
                if let usage = store.agentContextUsage {
                    Text(usage.displayText)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
                if store.isChatStreaming {
                    Button { store.stopChatGeneration() } label: {
                        Label("停止", systemImage: "stop.fill")
                    }
                    .buttonStyle(.borderless)
                }
                Button { showMemoryDrawer.toggle() } label: {
                    Image(systemName: "tray.full")
                }
                .buttonStyle(.borderless)
                .help("记忆")
            }
            .frame(width: width)
            if let issue = store.agentSequenceIssue {
                Text(issue)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .frame(width: width, alignment: .leading)
            }
        }
        .padding(.top, 12)
        .popover(isPresented: $showSkillDrawer) { skillDrawer.frame(width: 360, height: 420) }
        .popover(isPresented: $showMemoryDrawer) { memoryDrawer.frame(width: 420, height: 520) }
    }

    private var skillChipRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                Button { showSkillDrawer.toggle() } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .buttonStyle(.borderless)
                ForEach(store.agentSkills.filter { store.pinnedAgentSkillIds.contains($0.id) }.prefix(4)) { skill in
                    Text(skill.name)
                        .font(KSSFont.themed(10.5, .semibold, theme: theme))
                        .foregroundStyle(theme.accent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(theme.accentSoft, in: Capsule())
                }
            }
        }
        .frame(maxWidth: 280)
    }

    private var skillDrawer: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("技能管理")
                    .font(KSSFont.themed(15, .bold, theme: theme))
                Spacer()
                Button { store.reloadAgentSkills() } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(store.agentSkills) { skill in
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(skill.name).font(KSSFont.themed(12.5, .semibold, theme: theme))
                                if let desc = skill.description, !desc.isEmpty {
                                    Text(desc).font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                                }
                                HStack(spacing: 5) {
                                    Text(skill.category ?? "general")
                                    Text(skill.source ?? "project")
                                    if let trust = skill.trust { Text(trust) }
                                    if skill.protected == true {
                                        Image(systemName: "lock.fill")
                                    }
                                }
                                .font(KSSFont.themed(9.5, .medium, theme: theme))
                                .foregroundStyle(theme.textSecondary.opacity(0.82))
                                if skill.available == false {
                                    Text("缺少工具：\((skill.missingRequiredTools ?? []).joined(separator: "、"))")
                                        .font(KSSFont.themed(10.5, .medium, theme: theme))
                                        .foregroundStyle(.orange)
                                }
                            }
                            Spacer()
                            VStack(alignment: .leading, spacing: 6) {
                                Toggle("启用", isOn: Binding(
                                    get: { skill.enabled != false },
                                    set: { store.setAgentSkillEnabled(skill, enabled: $0) }
                                ))
                                Toggle("置顶", isOn: Binding(
                                    get: { store.pinnedAgentSkillIds.contains(skill.id) },
                                    set: { store.setAgentSkillPinned(skill, pinned: $0) }
                                ))
                            }
                            .toggleStyle(.checkbox)
                            .font(KSSFont.themed(10.5, .medium, theme: theme))
                            .fixedSize()
                        }
                    }
                    if !store.agentSkillDiagnostics.isEmpty {
                        Divider()
                        Text("诊断").font(KSSFont.themed(11, .semibold, theme: theme))
                        ForEach(store.agentSkillDiagnostics) { item in
                            Text(item.message)
                                .font(KSSFont.themed(10.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                }
            }
        }
        .padding(16)
        .background(theme.canvas)
    }

    private var memoryDrawer: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("记忆")
                .font(KSSFont.themed(15, .bold, theme: theme))
            HStack {
                TextField("搜索记忆或来源", text: $memorySearch)
                    .textFieldStyle(.roundedBorder)
                Button { Task { await store.loadAgentMemories(query: memorySearch) }; store.recallAgentSources(query: memorySearch) } label: {
                    Image(systemName: "magnifyingglass")
                }
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(store.agentMemoryCandidates) { candidate in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(candidate.text).font(KSSFont.themed(12, theme: theme))
                            HStack {
                                Button("记住") { store.resolveMemoryCandidate(candidate, approved: true) }
                                Button("忽略") { store.resolveMemoryCandidate(candidate, approved: false) }
                            }
                            .font(KSSFont.themed(11, .semibold, theme: theme))
                        }
                        .padding(10)
                        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                    }
                    ForEach(store.agentMemories) { memory in
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(memory.text)
                                    .font(KSSFont.themed(12, theme: theme))
                                    .foregroundStyle(theme.textPrimary)
                                if memory.reviewRequired == true {
                                    Text("待复核")
                                        .font(KSSFont.themed(10, .semibold, theme: theme))
                                        .foregroundStyle(Color.orange)
                                }
                                if let metadata = memoryMetadata(memory) {
                                    Text(metadata)
                                        .font(KSSFont.themed(10.5, theme: theme))
                                        .foregroundStyle(theme.textSecondary)
                                }
                            }
                            Spacer()
                            Button { store.archiveAgentMemory(memory) } label: { Image(systemName: "archivebox") }
                            Button { store.deleteAgentMemory(memory) } label: { Image(systemName: "trash") }
                        }
                        .buttonStyle(.borderless)
                        .padding(10)
                        .background(theme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                    }
                    ForEach(store.agentSourceRecalls) { recall in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(recall.title).font(KSSFont.themed(12, .semibold, theme: theme))
                            if let metadata = recallMetadata(recall) {
                                Text(metadata)
                                    .font(KSSFont.themed(10, theme: theme))
                                    .foregroundStyle(theme.textSecondary)
                            }
                            if let excerpt = recall.excerpt {
                                Text(excerpt).font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                            }
                        }
                        .padding(10)
                        .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                    }
                }
            }
        }
        .padding(16)
        .background(theme.canvas)
    }

    // MARK: - 空态:Cortex 风格 hero

    private func heroEmptyState(width: CGFloat) -> some View {
        ScrollView {
            VStack(spacing: 0) {
                Spacer(minLength: 40)
                SeesawWordmark().padding(.bottom, 22)
                Text("你好")
                    .font(KSSFont.themed(26, .bold, theme: theme))
                    .foregroundStyle(theme.accent)
                Text("今天复盘点什么？")
                    .font(KSSFont.themed(30, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .padding(.bottom, 28)

                // 未配置任何 LLM key 时 Seesaw 完全用不了——明确指引而非让用户输入后才报错（U9/R12）。
                if store.isCredentialConfigured("llm") == false {
                    MissingCredentialCard(sourceDisplayName: "LLM key") {
                        // R3：AIChat 入口落到 Seesaw LLM 分类（经典投影 credentials tab）。
                        store.openSeesawModels()
                    }
                    .frame(width: width)
                    .padding(.bottom, 18)
                }

                heroInputCard.frame(width: width)
                    .padding(.bottom, 26)

                if indicatorSuggestion?.family != nil {
                    indicatorSuggestionChip
                        .frame(width: width)
                        .padding(.bottom, 18)
                }
                if let candidate = store.researchCandidate {
                    Button {
                        store.selectedSection = .runbook
                    } label: {
                        Label("转为深度研究：\(candidate.objective)", systemImage: "scope")
                            .lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(14)
                            .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                    }
                    .buttonStyle(.plain)
                    .frame(width: width)
                    .padding(.bottom, 18)
                }

                capabilityCards(width: width)
                Spacer(minLength: 32)
                Text("AI 仅解释与复盘，不给个性化买卖建议")
                    .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    .padding(.bottom, 18)
            }
            .frame(maxWidth: .infinity)
        }
    }


    /// 突出的圆角输入卡(空态)。
    private var heroInputCard: some View {
        VStack(spacing: 12) {
            queuedInputPanel
            pendingAttachmentStrip
            TextField("问问盘面…（回车发送）", text: $input, axis: .vertical)
                .textFieldStyle(.plain)
                .font(KSSFont.themed(15, theme: theme))
                .lineLimit(1...4)
                .onKeyPress(.return, phases: .down, action: handleComposerReturn)
            HStack(spacing: 8) {
                attachmentPickerButton
                Label("只解释 · 不荐买卖", systemImage: "shield.lefthalf.filled")
                    .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(theme.surface, in: Capsule())
                Spacer()
                if store.isChatStreaming {
                    queueShortcutHint
                    stopButton
                }
                sendButton
            }
        }
        .padding(16)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeL).stroke(theme.hairline))
        .shadow(color: .black.opacity(0.06), radius: 16, y: 6)
    }

    private var sendButton: some View {
        Button { submitInput(mode: "steering") } label: {
            Image(systemName: store.isChatStreaming ? "arrow.turn.down.right" : "arrow.up")
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(Circle().fill(canSend ? theme.accent : theme.accent.opacity(0.4)))
        }
        .buttonStyle(.plain)
        .disabled(!canSend)
    }

    private var stopButton: some View {
        Button(action: { store.stopChatGeneration() }) {
            Image(systemName: "stop.fill")
                .font(KSSFont.themed(13, .bold, theme: theme))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(Circle().fill(theme.up))
        }
        .buttonStyle(.plain)
    }

    private var canSend: Bool {
        pendingQueueClientMessageId == nil
            && store.seesawProviderReadiness.isReadyForComposer
            && (!input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                || (!store.isChatStreaming && !store.pendingAgentAttachments.isEmpty))
    }

    private var queueShortcutHint: some View {
        Text("↩ 引导 · ⌥↩ 后续")
            .font(KSSFont.themed(10.5, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .lineLimit(1)
    }

    /// U9：命令不可用/超时/无候选 → 优雅缺席，不显示 chip、不报错弹窗（KTD8 诚实空态）。
    private func loadIndicatorSuggestion() async {
        guard let bridge = store.bridge else { return }
        let suggestion = try? await Task.detached { try bridge.suggestIndicator() }.value
        guard let suggestion, suggestion.family != nil else { return }
        indicatorSuggestion = suggestion
    }

    @ViewBuilder
    private var indicatorSuggestionChip: some View {
        if let suggestion = indicatorSuggestion, let family = suggestion.family {
            let label = Self.indicatorFamilyLabels[family] ?? family
            Button {
                let reason = suggestion.reason.map { "：\($0)" } ?? ""
                input = "帮我回测 \(label)\(reason)"
                send()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "sparkles")
                        .font(KSSFont.themed(11, .semibold, theme: theme))
                        .foregroundStyle(theme.accent)
                    Text("Seesaw 提议：研究一下\(label)")
                        .font(KSSFont.themed(12.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textPrimary)
                    if let reason = suggestion.reason, !reason.isEmpty {
                        Text(reason)
                            .font(KSSFont.themed(11.5, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.right")
                        .font(KSSFont.themed(10, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
            }
            .buttonStyle(.plain)
            .disabled(store.isChatStreaming)
        } else {
            EmptyView()
        }
    }

    private var latestResearchProvider: String? {
        store.chatMessages.reversed().compactMap { message in
            let provider = message.evidenceSummary.provider?.trimmingCharacters(in: .whitespacesAndNewlines)
            return provider?.isEmpty == false ? provider : nil
        }.first
    }

    private func researchProviderPill(_ provider: String) -> some View {
        Label("外部研究: \(provider)", systemImage: provider == "disabled" ? "wifi.slash" : "link")
            .font(.system(size: 10, weight: .semibold, design: .monospaced))
            .foregroundStyle(theme.textSecondary)
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(theme.surface, in: Capsule())
            .overlay(Capsule().stroke(theme.hairline))
            .help(provider == "disabled" ? "外部研究 provider 当前不可用，不影响本地 KSS 问答" : "当前外部研究 provider")
    }

    // MARK: 能力卡(列出 Skill/剧本)

    private func capabilityCards(width: CGFloat) -> some View {
        FlowLayout(spacing: 12, lineSpacing: 12) {
            ForEach(capabilities) { cap in
                capabilityCard(cap)
            }
        }
        .frame(width: width)
    }

    private func capabilityCard(_ cap: Capability) -> some View {
        Button { input = cap.prompt; send() } label: {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: cap.icon)
                    .font(KSSFont.themed(15, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                    .frame(width: 32, height: 32)
                    .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                Text(cap.title)
                    .font(KSSFont.themed(13, .bold, theme: theme)).foregroundStyle(theme.textPrimary)
                Text(cap.desc)
                    .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
                Text(cap.tag)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(theme.accent)
            }
            .padding(14)
            .frame(width: 234, height: 138, alignment: .topLeading)
            .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                .stroke(hovered == cap.tag ? theme.accent : theme.hairline,
                        lineWidth: hovered == cap.tag ? 1.5 : 1))
            .shadow(color: .black.opacity(hovered == cap.tag ? 0.08 : 0), radius: 10, y: 4)
        }
        .buttonStyle(.plain)
        .disabled(store.isChatStreaming)
        .onHover { hovered = $0 ? cap.tag : (hovered == cap.tag ? nil : hovered) }
        .animation(.easeOut(duration: 0.15), value: hovered)
    }

    // MARK: - 对话流

    private func messageList(width: CGFloat) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(store.chatMessages) { msg in
                        bubble(msg).id(msg.id)
                    }
                    if let tool = store.chatToolInProgress {
                        toolIndicator(tool).id("tool-progress")
                    }
                }
                .frame(width: width)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
            }
            .onChange(of: store.chatMessages.last?.text) { _, _ in
                if let last = store.chatMessages.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
        }
    }

    @ViewBuilder
    private func bubble(_ msg: ChatMessage) -> some View {
        if msg.role == .user {
            HStack {
                Spacer(minLength: 60)
                VStack(alignment: .leading, spacing: 6) {
                    if !msg.text.isEmpty {
                        Text(msg.text)
                            .font(KSSFont.themed(13, theme: theme)).foregroundStyle(.white)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                    }
                    messageAttachmentStrip(msg.attachments)
                }
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(theme.accent, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                    .contextMenu {
                        Button("记住这条消息") { store.proposeAgentMemory(msg.text) }
                    }
            }
        } else {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    if msg.text.isEmpty && store.isChatStreaming {
                        HStack(spacing: 8) { ProgressView().controlSize(.small); Text("思考中…")
                            .font(KSSFont.themed(12, theme: theme)).foregroundStyle(theme.textSecondary) }
                    } else if !msg.text.isEmpty {
                        markdownText(msg.text)
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(msg.isError ? theme.up : theme.textPrimary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if !msg.thinkingBlocks.isEmpty {
                        AgentThinkingDisclosure(blocks: msg.thinkingBlocks)
                    }
                    messageAttachmentStrip(msg.attachments)
                    if msg.numbersUnverified && store.isChatStreaming {
                        Label("数字校验中（以工具真值为准）", systemImage: "exclamationmark.triangle")
                            .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    }
                    if msg.evidenceSummary.hasEvidence || msg.evidenceSummary.provider != nil {
                        EvidenceDrawerView(summary: msg.evidenceSummary, drawer: msg.evidenceDrawer)
                            .padding(.top, 2)
                    }
                    // U4: K 线附件 (R8) — intraday-snapshot 工具返回后渲染 K 线 bubble
                    if let chart = msg.chartAttachment, !chart.bars.isEmpty {
                        ChartWebView(points: [], intradayBars: chart.bars)
                            .frame(height: 300)
                            .background(theme.chartSurface)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .padding(.top, 6)
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 10)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
                .contextMenu {
                    Button("记住这条消息") { store.proposeAgentMemory(msg.text) }
                }
                Spacer(minLength: 40)
            }
        }
    }

    private func markdownText(_ s: String) -> Text {
        if let attr = try? AttributedString(
            markdown: s,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            return Text(attr)
        }
        return Text(s)
    }

    private func toolIndicator(_ tool: String) -> some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("正在调用 \(tool) …").font(KSSFont.themed(12, theme: theme)).foregroundStyle(theme.textSecondary)
            Spacer()
        }
        .padding(.horizontal, 14)
    }

    /// 对话态底部固定输入栏(圆角卡风格,与空态一致)。
    private func pinnedInputBar(width: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let provider = latestResearchProvider {
                researchProviderPill(provider)
            }
            queuedInputPanel
            pendingAttachmentStrip
            HStack(spacing: 10) {
                attachmentPickerButton
                TextField("继续问…（回车发送）", text: $input, axis: .vertical)
                    .textFieldStyle(.plain).font(KSSFont.themed(14, theme: theme)).lineLimit(1...4)
                    .onKeyPress(.return, phases: .down, action: handleComposerReturn)
                if store.isChatStreaming {
                    queueShortcutHint
                    stopButton
                }
                sendButton
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeL).stroke(theme.hairline))
        .shadow(color: .black.opacity(0.05), radius: 10, y: 4)
        .frame(width: width)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 14)
    }

    private func send() {
        submitInput(mode: "steering")
    }

    private func handleComposerReturn(_ keyPress: KeyPress) -> KeyPress.Result {
        let mode = keyPress.modifiers.contains(.option)
            ? "follow_up"
            : "steering"
        submitInput(mode: mode)
        return .handled
    }

    private func submitInput(mode: String) {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || (!store.isChatStreaming && !store.pendingAgentAttachments.isEmpty)
        else { return }
        if store.isChatStreaming {
            pendingQueueClientMessageId = store.enqueueAgentInput(
                text,
                mode: mode,
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
                .frame(width: 30, height: 30)
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
