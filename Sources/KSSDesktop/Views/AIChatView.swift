import SwiftUI

/// #4 U4：AI 复盘助手聊天面板。空态走 Cortex 风格 hero(光晕球 + 问候 + 突出输入卡 + 能力卡);
/// 有消息走对话流。会话历史归 KSSStore(不放 @State,避免 .id(selectedSection) 销毁)。
/// 全用主题 token,自适应 8 套主题。
struct AIChatView: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme
    @State private var input = ""
    @State private var hovered: String?
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

    var body: some View {
        GeometryReader { geo in
            let width = min(geo.size.width - 64, 820)
            ZStack {
                theme.canvas.ignoresSafeArea()
                if store.chatMessages.isEmpty {
                    heroEmptyState(width: width)
                } else {
                    VStack(spacing: 0) {
                        messageList(width: width)
                        pinnedInputBar(width: width)
                    }
                }
            }
            .onAppear { Task { await store.preheatRealtimeContext() } }   // U4: Seesaw 加载时预温实时上下文（R3）
            .onAppear { Task { await loadIndicatorSuggestion() } }        // U9: 空态确定性候选建议 chip
        }
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
                        // R3：AIChat 入口落密钥 tab。
                        store.settingsTargetTab = .keys
                        store.selectedSection = .settings
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
            TextField("问问盘面…（回车发送）", text: $input, axis: .vertical)
                .textFieldStyle(.plain)
                .font(KSSFont.themed(15, theme: theme))
                .lineLimit(1...4)
                .disabled(store.isChatStreaming)
                .onSubmit(send)
            HStack(spacing: 8) {
                Label("只解释 · 不荐买卖", systemImage: "shield.lefthalf.filled")
                    .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(theme.surface, in: Capsule())
                Spacer()
                sendButton
            }
        }
        .padding(16)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeL).stroke(theme.hairline))
        .shadow(color: .black.opacity(0.06), radius: 16, y: 6)
    }

    private var sendButton: some View {
        Button(action: send) {
            Image(systemName: store.isChatStreaming ? "ellipsis" : "arrow.up")
                .font(KSSFont.themed(15, .bold, theme: theme))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(Circle().fill(canSend ? theme.accent : theme.accent.opacity(0.4)))
        }
        .buttonStyle(.plain)
        .disabled(!canSend)
    }

    private var canSend: Bool {
        !store.isChatStreaming && !input.trimmingCharacters(in: .whitespaces).isEmpty
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
            .onChange(of: store.chatMessages.last?.text) { _ in
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
                    // U4: K 线附件 (R8) — intraday-snapshot 工具返回 bar 数据后渲染
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
            HStack(spacing: 10) {
                TextField("继续问…（回车发送）", text: $input, axis: .vertical)
                    .textFieldStyle(.plain).font(KSSFont.themed(14, theme: theme)).lineLimit(1...4)
                    .disabled(store.isChatStreaming)
                    .onSubmit(send)
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
        let text = input
        input = ""
        store.sendChat(text)
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
