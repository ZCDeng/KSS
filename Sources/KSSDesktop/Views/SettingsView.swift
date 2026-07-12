import SwiftUI

/// 统一"设置"工作区页面（plan 2026-07-12-005 / U1）：密钥、数据源、任务、日志四分区。
/// 收敛原分散入口——工具栏"网络与凭据"弹窗（NetworkSettingsView）与任务页"定时任务"区块
/// 均并入本页，不保留重复 UI（U5/U10 见 Key Decisions）。
struct SettingsView: View {
    @Environment(\.kssTheme) private var theme

    var body: some View {
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    PageTitle("设置", subtitle: "密钥 / 数据源 / 任务 / 日志的唯一入口")

                    SectionHeader("密钥")
                    SettingsKeysSection()

                    SectionHeader("数据源")
                    SettingsDataSourcesSection()

                    SectionHeader("任务")
                    SettingsPlaceholderCard(text: "定时任务管理（U5）即将上线")

                    SectionHeader("日志")
                    SettingsPlaceholderCard(text: "应用内日志查看器（U7）即将上线")
                }
                .frame(width: w, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
        .background(theme.canvas)
    }
}

/// 占位卡片：后续单元（U4/U5/U7）实现前的分区占位，保证 U1 独立可交付。
private struct SettingsPlaceholderCard: View {
    @Environment(\.kssTheme) private var theme
    var text: String

    var body: some View {
        Text(text)
            .font(KSSFont.themed(13, theme: theme))
            .foregroundStyle(theme.textSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: 14)
    }
}

/// 密钥分区：原样承接 NetworkSettingsView 的 Keychain 读写与"保存后重启 sidecar"语义。
struct SettingsKeysSection: View {
    @Environment(\.kssTheme) private var theme

    @State private var tushareToken = ""
    @State private var telegramBotToken = ""
    @State private var telegramChatId = ""
    @State private var telegramApiUrl = ""
    // AI 复盘助手（#4）：LLM 凭据 + live 写开关。注入 sidecar env，openai_client 据此解析网关。
    @State private var openaiApiKey = ""
    @State private var openaiBaseUrl = ""
    @State private var deepseekApiKey = ""
    @State private var llmModel = ""
    @State private var appLive = false
    // Longbridge（U6 历史编号）：实时行情凭据，注入 sidecar env 供 LongbridgeProvider 读。
    @State private var longbridgeAppKey = ""
    @State private var longbridgeAppSecret = ""
    @State private var longbridgeAccessToken = ""
    @State private var saved = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("凭据存入 macOS Keychain，不写入磁盘明文。留空表示删除该项。")
                .font(KSSFont.themed(12, theme: theme))
                .foregroundStyle(theme.textSecondary)

            field("Tushare Token", text: $tushareToken, secure: true)
            field("Telegram Bot Token", text: $telegramBotToken, secure: true)
            field("Telegram Chat ID", text: $telegramChatId, secure: false)
            field("Telegram API URL（自建中继，可选）", text: $telegramApiUrl, secure: false)

            Divider().padding(.vertical, 2)
            Text("Seesaw")
                .font(KSSFont.themed(13, .bold, theme: theme)).foregroundStyle(theme.textPrimary)
            Text("二选一填 key（优先 DeepSeek；都填以 DeepSeek 为准）。保存后自动重启 sidecar 生效。")
                .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
            field("DeepSeek API Key", text: $deepseekApiKey, secure: true)
            field("OpenAI API Key（fallback）", text: $openaiApiKey, secure: true)
            field("OpenAI Base URL（网关/oneAPI，可选）", text: $openaiBaseUrl, secure: false)
            field("模型 ID（KSS_LLM_MODEL，可选，默认 deepseek-v4-flash）", text: $llmModel, secure: false)
            Toggle(isOn: $appLive) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("允许 AI 执行写操作（live）")
                        .font(KSSFont.themed(12, .semibold, theme: theme)).foregroundStyle(theme.textPrimary)
                    Text("关：写操作弹窗确认后仍被拒（只读安全）。开：本人逐次 tap 确认后真执行。")
                        .font(KSSFont.themed(10, theme: theme)).foregroundStyle(theme.textSecondary)
                }
            }
            .onChange(of: appLive) { _, _ in saved = false }

            Divider().padding(.vertical, 2)
            Text("Longbridge 实时行情")
                .font(KSSFont.themed(13, .bold, theme: theme)).foregroundStyle(theme.textPrimary)
            Text("ChinaConnect LV1 实时（陆股通池），注入 sidecar 供 LongbridgeProvider 使用。")
                .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
            field("Longbridge App Key", text: $longbridgeAppKey, secure: true)
            field("Longbridge App Secret", text: $longbridgeAppSecret, secure: true)
            field("Longbridge Access Token", text: $longbridgeAccessToken, secure: true)

            HStack {
                if saved {
                    Label("已保存到 Keychain", systemImage: "checkmark.seal.fill")
                        .font(KSSFont.themed(12, .semibold, theme: theme))
                        .foregroundStyle(theme.up)
                }
                Spacer()
                Text("App v\(BridgeClient.appVersion) · Python 层 v\(BridgeClient.scriptsVersionOnDisk())")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
                Button {
                    save()
                } label: {
                    Text("保存").fontWeight(.semibold)
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 16)
        .onAppear(perform: load)
    }

    @ViewBuilder
    private func field(_ label: String, text: Binding<String>, secure: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(KSSFont.themed(11, .semibold, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Group {
                if secure {
                    SecureField("", text: text)
                } else {
                    TextField("", text: text)
                }
            }
            .textFieldStyle(.roundedBorder)
            .onChange(of: text.wrappedValue) { _, _ in saved = false }
        }
    }

    private func load() {
        tushareToken = KeychainStore.read("TUSHARE_TOKEN") ?? ""
        telegramBotToken = KeychainStore.read("TELEGRAM_BOT_TOKEN") ?? ""
        telegramChatId = KeychainStore.read("TELEGRAM_CHAT_ID") ?? ""
        telegramApiUrl = KeychainStore.read("TELEGRAM_API_URL") ?? ""
        openaiApiKey = KeychainStore.read("OPENAI_API_KEY") ?? ""
        openaiBaseUrl = KeychainStore.read("OPENAI_BASE_URL") ?? ""
        deepseekApiKey = KeychainStore.read("DEEPSEEK_API_KEY") ?? ""
        llmModel = KeychainStore.read("KSS_LLM_MODEL") ?? ""
        appLive = KeychainStore.read("KSS_APP_LIVE") == "1"
        longbridgeAppKey = KeychainStore.read("LONGBRIDGE_APP_KEY") ?? ""
        longbridgeAppSecret = KeychainStore.read("LONGBRIDGE_APP_SECRET") ?? ""
        longbridgeAccessToken = KeychainStore.read("LONGBRIDGE_ACCESS_TOKEN") ?? ""
    }

    private func save() {
        KeychainStore.write("TUSHARE_TOKEN", tushareToken)
        KeychainStore.write("TELEGRAM_BOT_TOKEN", telegramBotToken)
        KeychainStore.write("TELEGRAM_CHAT_ID", telegramChatId)
        KeychainStore.write("TELEGRAM_API_URL", telegramApiUrl)
        KeychainStore.write("OPENAI_API_KEY", openaiApiKey)
        KeychainStore.write("OPENAI_BASE_URL", openaiBaseUrl)
        KeychainStore.write("DEEPSEEK_API_KEY", deepseekApiKey)
        KeychainStore.write("KSS_LLM_MODEL", llmModel)
        KeychainStore.write("KSS_APP_LIVE", appLive ? "1" : "")
        KeychainStore.write("LONGBRIDGE_APP_KEY", longbridgeAppKey)
        KeychainStore.write("LONGBRIDGE_APP_SECRET", longbridgeAppSecret)
        KeychainStore.write("LONGBRIDGE_ACCESS_TOKEN", longbridgeAccessToken)
        // 凭据/开关变更后重启常驻 sidecar，使新 env 生效（SIGHUP re-exec 留旧 env，须全杀重启）。
        BridgeClient.restartSidecarForEnvChange()
        saved = true
    }
}

// MARK: - 数据源分区（U4）

private enum SettingsDataSource: String, CaseIterable, Identifiable {
    case tushare, longbridge, telegram, llm
    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .tushare: return "Tushare"
        case .longbridge: return "Longbridge"
        case .telegram: return "Telegram"
        case .llm: return "LLM 端点"
        }
    }

    /// 本地 Keychain 配置状态（不经 bridge 往返）——独立于「测试」按钮的实时连通性结果。
    var isConfigured: Bool {
        switch self {
        case .tushare:
            return !(KeychainStore.read("TUSHARE_TOKEN") ?? "").isEmpty
        case .longbridge:
            return ["LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"]
                .allSatisfy { !(KeychainStore.read($0) ?? "").isEmpty }
        case .telegram:
            return !(KeychainStore.read("TELEGRAM_BOT_TOKEN") ?? "").isEmpty
        case .llm:
            let newKeyed = !(KeychainStore.read("KSS_LLM_PRIMARY_KEY") ?? "").isEmpty
            let legacy = !(KeychainStore.read("DEEPSEEK_API_KEY") ?? "").isEmpty
                || !(KeychainStore.read("OPENAI_API_KEY") ?? "").isEmpty
            return newKeyed || legacy
        }
    }
}

/// 数据源分区：逐源展示配置状态（Keychain 本地读取）+ 连通性测试按钮（R7）。
struct SettingsDataSourcesSection: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var results: [String: DataSourceTestResult] = [:]
    @State private var testing: Set<String> = []

    var body: some View {
        VStack(spacing: 10) {
            ForEach(SettingsDataSource.allCases) { source in
                row(for: source)
            }
        }
    }

    @ViewBuilder
    private func row(for source: SettingsDataSource) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Circle()
                    .fill(source.isConfigured ? theme.accent : theme.textSecondary.opacity(0.4))
                    .frame(width: 8, height: 8)
                Text(source.displayName)
                    .font(KSSFont.themed(14, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Text(source.isConfigured ? "已配置" : "未配置")
                    .font(KSSFont.themed(11.5, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Spacer()
                Button {
                    Task { await runTest(source) }
                } label: {
                    if testing.contains(source.rawValue) {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("测试").font(KSSFont.themed(12, .semibold, theme: theme))
                    }
                }
                .buttonStyle(.bordered)
                .disabled(testing.contains(source.rawValue))
            }
            if let result = results[source.rawValue] {
                resultDetail(result)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .kssCard(padding: 12)
    }

    @ViewBuilder
    private func resultDetail(_ result: DataSourceTestResult) -> some View {
        if let candidates = result.candidates, !candidates.isEmpty {
            // LLM 源：主/备各一行。
            VStack(alignment: .leading, spacing: 4) {
                ForEach(candidates) { c in
                    candidateLine(role: c.role, model: c.model, ok: c.ok,
                                   latencyMs: c.latencyMs, hint: c.hint)
                }
            }
        } else {
            candidateLine(role: nil, model: nil, ok: result.ok,
                          latencyMs: result.latencyMs, hint: result.hint)
        }
    }

    @ViewBuilder
    private func candidateLine(role: String?, model: String?, ok: Bool, latencyMs: Double?, hint: String?) -> some View {
        HStack(spacing: 6) {
            Image(systemName: ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                .foregroundStyle(ok ? theme.accent : theme.up)
                .font(.system(size: 11))
            if let role {
                Text(role == "primary" ? "主" : "备")
                    .font(KSSFont.themed(11, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            if let model, !model.isEmpty {
                Text(model)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if let latencyMs {
                Text(String(format: "%.0fms", latencyMs))
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if !ok, let hint, !hint.isEmpty {
                Text(hint)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.up)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
    }

    private func runTest(_ source: SettingsDataSource) async {
        guard let bridge = store.bridge else { return }
        testing.insert(source.rawValue)
        defer { testing.remove(source.rawValue) }
        let result = try? await Task.detached { try bridge.datasourceTest(source: source.rawValue) }.value
        if let result {
            results[source.rawValue] = result
        }
    }
}
