import SwiftUI

/// #4 U4：AI 复盘助手聊天面板。流式气泡 + 工具进度 + 数字未核实样式 + 人在环内写确认。
/// 会话历史归 KSSStore（不放 @State，避免 .id(selectedSection) 销毁）。
struct AIChatView: View {
    @EnvironmentObject private var store: KSSStore
    @Environment(\.kssTheme) private var theme
    @State private var input = ""

    private let examples = ["688008 今天为什么动", "今天北向在买啥板块", "最近哪个主题梯队最强"]

    var body: some View {
        GeometryReader { geo in
            let width = min(geo.size.width - 48, 960)
            VStack(spacing: 0) {
                if store.chatMessages.isEmpty {
                    emptyState(width: width)
                } else {
                    messageList(width: width)
                }
                inputBar(width: width)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(theme.canvas)
        }
        .background(theme.canvas)
    }

    // MARK: 空状态 —— 边界文案 + 示例 chip

    private func emptyState(width: CGFloat) -> some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 40)).foregroundStyle(theme.textSecondary)
            Text("AI 复盘助手")
                .font(.system(size: 20, weight: .bold)).foregroundStyle(theme.textPrimary)
            Text("用中文问盘面，我会调工具取真值、流式复盘。\n我只解释与复盘，不给买卖建议。")
                .multilineTextAlignment(.center).font(.system(size: 13))
                .foregroundStyle(theme.textSecondary)
            HStack(spacing: 10) {
                ForEach(examples, id: \.self) { q in
                    Button { input = q; send() } label: {
                        Text(q).font(.system(size: 12))
                            .padding(.horizontal, 12).padding(.vertical, 7)
                            .background(theme.surfaceRaised, in: Capsule())
                            .overlay(Capsule().stroke(theme.hairline))
                    }
                    .buttonStyle(.plain)
                }
            }
            Spacer()
        }
        .frame(width: width).frame(maxWidth: .infinity)
    }

    // MARK: 消息流

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
                    .font(.system(size: 13)).foregroundStyle(theme.textPrimary)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                if msg.text.isEmpty && store.isChatStreaming {
                    HStack(spacing: 8) { ProgressView().controlSize(.small); Text("思考中…")
                        .font(.system(size: 12)).foregroundStyle(theme.textSecondary) }
                } else {
                    MarkdownWebView(text: msg.text)
                        .frame(minHeight: 24)
                }
                if msg.numbersUnverified && store.isChatStreaming {
                    Label("数字校验中（以工具真值为准）", systemImage: "exclamationmark.triangle")
                        .font(.system(size: 11)).foregroundStyle(theme.textSecondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(msg.isError ? theme.surfaceRaised : theme.canvas,
                        in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
            .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
        }
    }

    private func toolIndicator(_ tool: String) -> some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("正在调用 \(tool) …").font(.system(size: 12)).foregroundStyle(theme.textSecondary)
            Spacer()
        }
        .padding(.horizontal, 14)
    }

    // MARK: 输入栏（pinned，streaming 中禁用）

    private func inputBar(width: CGFloat) -> some View {
        HStack(spacing: 10) {
            TextField("问问盘面…（回车发送）", text: $input)
                .textFieldStyle(.plain)
                .font(.system(size: 13))
                .padding(.horizontal, 14).padding(.vertical, 11)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
                .disabled(store.isChatStreaming)
                .onSubmit(send)
            Button(action: send) {
                Image(systemName: "paperplane.fill").font(.system(size: 14))
                    .padding(10)
                    .background(theme.accent, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
            .disabled(store.isChatStreaming || input.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .frame(width: width)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 14)
        .background(theme.canvas)
        .overlay(Divider(), alignment: .top)
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
                Image(systemName: "exclamationmark.shield").font(.system(size: 18))
                    .foregroundStyle(theme.accent)
                Text("确认写操作").font(.system(size: 16, weight: .bold))
                    .foregroundStyle(theme.textPrimary)
            }
            Text(pending.effect)
                .font(.system(size: 14, weight: .semibold)).foregroundStyle(theme.textPrimary)
            if !pending.argsText.isEmpty && pending.argsText != "{}" {
                VStack(alignment: .leading, spacing: 4) {
                    Text("参数").font(.system(size: 11)).foregroundStyle(theme.textSecondary)
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
                    .font(.system(size: 11)).foregroundStyle(theme.textSecondary)
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
