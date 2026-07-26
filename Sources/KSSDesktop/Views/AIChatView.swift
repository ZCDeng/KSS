import Foundation
import SwiftUI

/// AI 复盘助手聊天面板。x.com 主题使用 600pt 投研时间线；
/// 经典主题保留原有 hero + 卡片布局。会话状态归 KSSStore，视觉切换不重建 Agent runtime。
struct AIChatView: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme
    @State private var input = ""
    @State private var hovered: String?
    @State private var showSkillDrawer = false
    @State private var showMemoryDrawer = false
    @State private var memorySearch = ""
    @State private var pendingQueueClientMessageId: String?
    @State private var loadedQueueInputId: String?
    /// 会话开场确定性候选建议（plan 2026-07-12-004 U9）；nil = 未加载或无候选，不显示 chip。
    @State private var indicatorSuggestion: IndicatorSuggestion?

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

    private var isXcom: Bool { XcomListChrome.isXcom(theme.system) }

    var body: some View {
        GeometryReader { geo in
            Group {
                if isXcom {
                    xcomSeesawShell(size: geo.size)
                } else {
                    classicSeesawShell(size: geo.size)
                }
            }
            .onAppear { Task { await store.preheatRealtimeContext() } }   // U4: Seesaw 加载时预温实时上下文（R3）
            .onAppear { Task { await loadIndicatorSuggestion() } }        // U9: 空态确定性候选建议 chip
            .onAppear { Task { await store.loadAgentBootstrap() } }
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
                            store.openSettings(category: .llm)
                        }
                        .padding(SeesawXcomChrome.rowHorizontalPadding)
                        Rectangle().fill(theme.hairline).frame(height: 1)
                    }
                }

                if indicatorSuggestion?.family != nil {
                    xcomIndicatorSuggestion
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
                } else {
                    markdownText(message.text)
                        .font(KSSFont.themed(15, theme: theme))
                        .foregroundStyle(message.isError ? Color.red : theme.textPrimary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

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

            HStack(alignment: .bottom, spacing: 10) {
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
                        store.openSettings(category: .llm)
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
            TextField("问问盘面…（回车发送）", text: $input, axis: .vertical)
                .textFieldStyle(.plain)
                .font(KSSFont.themed(15, theme: theme))
                .lineLimit(1...4)
                .onKeyPress(.return, phases: .down, action: handleComposerReturn)
            HStack(spacing: 8) {
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
            && !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
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
                Text(msg.text)
                    .font(KSSFont.themed(13, theme: theme)).foregroundStyle(.white)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
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
                    } else {
                        markdownText(msg.text)
                            .font(KSSFont.themed(13, theme: theme))
                            .foregroundStyle(msg.isError ? theme.up : theme.textPrimary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
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
            HStack(spacing: 10) {
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
        guard !text.isEmpty else { return }
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
