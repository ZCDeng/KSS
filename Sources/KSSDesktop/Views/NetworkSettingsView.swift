import SwiftUI

/// U3：网络凭据配置面板。用户在此输入 Tushare / Telegram 凭据，存入 macOS Keychain
/// （不再明文落 .env）。BridgeClient 启动时读出注入子进程 env。
struct NetworkSettingsView: View {
    @Environment(\.kssTheme) private var theme
    @Environment(\.dismiss) private var dismiss

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
    // Longbridge（U6）：实时行情凭据，注入 sidecar env 供 LongbridgeProvider 读。
    @State private var longbridgeAppKey = ""
    @State private var longbridgeAppSecret = ""
    @State private var longbridgeAccessToken = ""
    @State private var saved = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("网络与凭据")
                .font(KSSFont.themed(18, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
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
                Button("关闭") { dismiss() }
                Button {
                    save()
                } label: {
                    Text("保存").fontWeight(.semibold)
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 460)
        .background(theme.surface)
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
