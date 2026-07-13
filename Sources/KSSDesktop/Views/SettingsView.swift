import SwiftUI

/// 统一"设置"工作区页面（plan 2026-07-12-005 / U1，R2-U4 改 Tab 形态）：密钥、数据源、任务、
/// 日志四个 tab。收敛原分散入口——工具栏"网络与凭据"弹窗（NetworkSettingsView）与任务页
/// "定时任务"区块均并入本页，不保留重复 UI（U5/U10 见 Key Decisions）。
struct SettingsView: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var tab: SettingsTab = .keys
    @State private var dataSourceResults: [String: DataSourceTestResult] = [:]

    private static let tabOptions: [(key: SettingsTab, label: String)] =
        SettingsTab.allCases.map { ($0, $0.label) }

    private var dataSourcesConfigured: [Bool] {
        [
            store.isCredentialConfigured("tushare"),
            store.isCredentialConfigured("longbridge"),
            store.isCredentialConfigured("telegram"),
            store.isCredentialConfigured("llm"),
        ].map { $0 ?? true }   // 尚未自检（nil）时不误判为「未配置」而乱亮点
    }

    private var badgedTabs: Set<SettingsTab> {
        var badged: Set<SettingsTab> = []
        if SettingsTabRouting.dataSourcesNeedsBadge(
            configured: dataSourcesConfigured,
            testsOK: dataSourceResults.values.map(\.ok)
        ) {
            badged.insert(.dataSources)
        }
        if SettingsTabRouting.scheduledTasksNeedsBadge(jobs: store.scheduledJobs) {
            badged.insert(.scheduledTasks)
        }
        return badged
    }

    var body: some View {
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    PageTitle("设置", subtitle: "密钥 / 数据源 / 定时任务 / 日志的唯一入口")
                    SelfCheckStatusStrip()

                    KSSSegmentedControl(
                        options: Self.tabOptions,
                        selection: $tab,
                        badgedKeys: badgedTabs
                    )

                    switch tab {
                    case .keys:
                        SettingsKeysSection()
                    case .dataSources:
                        SettingsDataSourcesSection(results: $dataSourceResults)
                    case .scheduledTasks:
                        SettingsTasksSection()
                    case .logs:
                        SettingsLogsSection()
                    }
                }
                .frame(width: w, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
            }
            .scrollContentBackground(.hidden)
            .background(theme.canvas)
        }
        .background(theme.canvas)
        .onAppear {
            if let target = store.settingsTargetTab {
                tab = target
                store.settingsTargetTab = nil
            }
        }
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
    @EnvironmentObject private var store: KSSStore

    @State private var tushareToken = ""
    @State private var telegramBotToken = ""
    @State private var telegramChatId = ""
    @State private var telegramApiUrl = ""
    // BYOK 端点泛化（U3，plan 2026-07-12-005）：主用/备用两组独立 base_url/key/model，
    // 有序候选——主用先试、备用兜底。openai_client._resolve_credential_candidates() 同源解析。
    @State private var llmPrimaryBaseUrl = ""
    @State private var llmPrimaryKey = ""
    @State private var llmPrimaryModel = ""
    @State private var llmFallbackBaseUrl = ""
    @State private var llmFallbackKey = ""
    @State private var llmFallbackModel = ""
    // 兼容旧配置（新六键全空时才生效，见 _resolve_credential_candidates 的兼容映射）。
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
            Text("主用/备用可各配一套 OpenAI 兼容端点（base_url/key/model），主用失败时自动降级到备用。"
                 + "留空则退回下方兼容旧配置。保存后自动重启 sidecar 生效。")
                .font(KSSFont.themed(11, theme: theme)).foregroundStyle(theme.textSecondary)
            Text("主用").font(KSSFont.themed(11, .semibold, theme: theme)).foregroundStyle(theme.textPrimary)
            field("主用 API Key", text: $llmPrimaryKey, secure: true)
            field("主用 Base URL（网关/oneAPI，可选，留空用官方端点）", text: $llmPrimaryBaseUrl, secure: false)
            field("主用模型 ID（可选）", text: $llmPrimaryModel, secure: false)
            Text("备用").font(KSSFont.themed(11, .semibold, theme: theme)).foregroundStyle(theme.textPrimary)
            field("备用 API Key", text: $llmFallbackKey, secure: true)
            field("备用 Base URL（可选）", text: $llmFallbackBaseUrl, secure: false)
            field("备用模型 ID（可选）", text: $llmFallbackModel, secure: false)
            Text("兼容旧配置（仅上方主用/备用均为空时生效）")
                .font(KSSFont.themed(11, .semibold, theme: theme)).foregroundStyle(theme.textSecondary)
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
        llmPrimaryBaseUrl = KeychainStore.read("KSS_LLM_PRIMARY_BASE_URL") ?? ""
        llmPrimaryKey = KeychainStore.read("KSS_LLM_PRIMARY_KEY") ?? ""
        llmPrimaryModel = KeychainStore.read("KSS_LLM_PRIMARY_MODEL") ?? ""
        llmFallbackBaseUrl = KeychainStore.read("KSS_LLM_FALLBACK_BASE_URL") ?? ""
        llmFallbackKey = KeychainStore.read("KSS_LLM_FALLBACK_KEY") ?? ""
        llmFallbackModel = KeychainStore.read("KSS_LLM_FALLBACK_MODEL") ?? ""
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
        KeychainStore.write("KSS_LLM_PRIMARY_BASE_URL", llmPrimaryBaseUrl)
        KeychainStore.write("KSS_LLM_PRIMARY_KEY", llmPrimaryKey)
        KeychainStore.write("KSS_LLM_PRIMARY_MODEL", llmPrimaryModel)
        KeychainStore.write("KSS_LLM_FALLBACK_BASE_URL", llmFallbackBaseUrl)
        KeychainStore.write("KSS_LLM_FALLBACK_KEY", llmFallbackKey)
        KeychainStore.write("KSS_LLM_FALLBACK_MODEL", llmFallbackModel)
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
        // 保存后刷新两条独立的"已配置"判定源，否则缺凭证卡片/IntelView 的 LLM 门禁要等
        // 手动重跑自检或重启才消失（U8/U9 优雅缺失承诺；self-check 与 hasLLMCredentials
        // 是两条历史上各自维护的判定路径，缺一个都会留旧状态）。
        store.refreshLLMCredentialsStatus()
        Task { await store.runSelfCheck() }
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
/// `results` 由父视图（SettingsView）持有，供 tab 状态点（KTD4：任一测试失败）复用同一份结果。
struct SettingsDataSourcesSection: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @Binding var results: [String: DataSourceTestResult]
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

// MARK: - 任务分区（U5：承接原 Runbook 页面「定时任务」面板全部能力）

/// 薄包装：把 store 状态/方法接到既有 `ScheduledTasksSection`（组件本身不动，纯搬迁）。
struct SettingsTasksSection: View {
    @EnvironmentObject private var store: KSSStore

    var body: some View {
        ScheduledTasksSection(
            jobs: store.scheduledJobs,
            categoryOrder: store.cronCategoryOrder,
            busy: store.scheduledBusy,
            batchBusy: store.scheduledBatchBusy,
            batchNote: store.scheduledBatchNote,
            onRerun: { label in Task { await store.rerunScheduledJob(label) } },
            onToggle: { label, enabled in Task { await store.toggleScheduledJob(label, enabled: enabled) } },
            onSync: { label in Task { await store.syncScheduledJobs(label) } },
            onCatchUp: { Task { await store.catchUpStaleJobs() } },
            onRerunMany: { labels in Task { await store.rerunScheduledJobs(labels) } },
            onDismissBatchNote: { store.scheduledBatchNote = nil },
            onEditSchedule: { label, updated in
                let suffix = label.replacingOccurrences(of: "com.zcdeng.kss.", with: "")
                Task { await store.editScheduledJob(label, suffix: suffix, scheduleJSON: updated.toScheduleJSON()) }
            }
        )
        .task { await store.loadScheduledJobs() }
    }
}

// MARK: - 日志分区（U7）

/// 应用内日志查看器：文件清单（含轮转代）+ 尾部滚动 + 关键词过滤（R9/AE5）。
struct SettingsLogsSection: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var files: [LogFileEntry] = []
    @State private var selected: LogFileEntry?
    @State private var tailLines: [String] = []
    @State private var totalMatched = 0
    @State private var searchText = ""
    @State private var loadingList = false
    @State private var loadingTail = false

    private var sortedFiles: [LogFileEntry] {
        files.sorted { $0.mtime > $1.mtime }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Picker("文件", selection: $selected) {
                    Text("选择日志文件").tag(LogFileEntry?.none)
                    ForEach(sortedFiles) { f in
                        Text("\(f.name) · \(f.sizeLabel)").tag(Optional(f))
                    }
                }
                .frame(maxWidth: 360)
                .onChange(of: selected) { _, _ in Task { await loadTail() } }

                Button {
                    Task { await loadList() }
                } label: {
                    if loadingList { ProgressView().controlSize(.small) }
                    else { Image(systemName: "arrow.clockwise") }
                }
                .buttonStyle(.bordered)

                Spacer()

                TextField("搜索关键词", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 220)
                    .onSubmit { Task { await loadTail() } }
                Button("搜索") { Task { await loadTail() } }
                    .buttonStyle(.bordered)
                    .disabled(selected == nil)
            }

            if selected != nil {
                HStack {
                    Text("共 \(totalMatched) 行匹配 · 显示末尾 \(tailLines.count) 行")
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                    Spacer()
                    if loadingTail { ProgressView().controlSize(.small) }
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(tailLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(theme.textPrimary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        if tailLines.isEmpty && !loadingTail {
                            Text("无匹配内容")
                                .font(KSSFont.themed(12, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                    .padding(10)
                }
                .frame(height: 320)
                .background(theme.canvas, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
                .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeS).stroke(theme.hairline))
            } else {
                Text("选择左上角的日志文件查看内容")
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: 14)
            }
        }
        .task { await loadList() }
    }

    private func loadList() async {
        guard let bridge = store.bridge else { return }
        loadingList = true
        defer { loadingList = false }
        let resp = try? await Task.detached { try bridge.logList() }.value
        files = resp?.logs ?? []
    }

    private func loadTail() async {
        guard let bridge = store.bridge, let selected else { return }
        loadingTail = true
        defer { loadingTail = false }
        let name = selected.name
        let grep = searchText
        let resp = try? await Task.detached { try bridge.logTail(name: name, lines: 500, grep: grep) }.value
        tailLines = resp?.lines ?? []
        totalMatched = resp?.totalMatched ?? 0
    }
}

// MARK: - 自检状态 header strip（U8）

/// 设置页顶部：自检结果摘要 + 手动重跑入口。位于四分区之上（不属于任一分区）。
struct SelfCheckStatusStrip: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var expanded = false

    private var failCount: Int { store.selfCheckItems.filter(\.isFail).count }
    private var warnCount: Int { store.selfCheckItems.filter(\.isWarn).count }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: statusIcon)
                    .foregroundStyle(statusTint)
                Text(summaryText)
                    .font(KSSFont.themed(13, .semibold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                if let at = store.selfCheckGeneratedAt {
                    Text("· \(at)")
                        .font(KSSFont.themed(11, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer()
                if !store.selfCheckItems.isEmpty {
                    Button(expanded ? "收起" : "详情") { expanded.toggle() }
                        .buttonStyle(.borderless)
                        .controlSize(.small)
                }
                Button {
                    Task { await store.runSelfCheck() }
                } label: {
                    if store.isRunningSelfCheck {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("重新自检", systemImage: "arrow.clockwise")
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(store.isRunningSelfCheck)
            }
            if expanded {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(store.selfCheckItems) { item in
                        selfCheckItemRow(item)
                    }
                }
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
    }

    private var statusIcon: String {
        if failCount > 0 { return "xmark.octagon.fill" }
        if warnCount > 0 { return "exclamationmark.triangle.fill" }
        return "checkmark.circle.fill"
    }

    private var statusTint: Color {
        if failCount > 0 { return theme.up }
        if warnCount > 0 { return theme.ma5 }
        return theme.accent
    }

    private var summaryText: String {
        if store.selfCheckItems.isEmpty { return "尚未自检" }
        if failCount == 0 && warnCount == 0 { return "自检全绿" }
        var parts: [String] = []
        if failCount > 0 { parts.append("\(failCount) 项异常") }
        if warnCount > 0 { parts.append("\(warnCount) 项未配置") }
        return parts.joined(separator: " · ")
    }

    private func selfCheckItemRow(_ item: SelfCheckItem) -> some View {
        HStack(spacing: 8) {
            Image(systemName: item.isOK ? "checkmark.circle.fill" : (item.isFail ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"))
                .font(.system(size: 11))
                .foregroundStyle(item.isOK ? theme.accent : (item.isFail ? theme.up : theme.ma5))
            Text(item.displayName)
                .font(KSSFont.themed(12, .semibold, theme: theme))
                .foregroundStyle(theme.textPrimary)
            Text(item.detail)
                .font(KSSFont.themed(11.5, theme: theme))
                .foregroundStyle(theme.textSecondary)
            Spacer()
            if let hint = item.fixHint, !item.isOK {
                Text(hint)
                    .font(KSSFont.themed(11, theme: theme))
                    .foregroundStyle(theme.accent)
            }
        }
    }
}

/// 启动自检 fail 横幅（KTD4）：仅存在 fail 项时自动弹，当前会话可关；warn 只在设置页可见。
struct SelfCheckBanner: View {
    @Environment(\.kssTheme) private var theme
    var items: [SelfCheckItem]   // 仅 fail 项
    var isBusy: Bool
    var onDismiss: () -> Void
    /// 目标 tab 由调用方按 fail 项字段名映射（R2-U4，SettingsTabRouting.targetTab）。
    var onOpenSettings: (String) -> Void
    var onReinitRuntime: () -> Void
    @State private var expanded = false

    private var wantsReinit: Bool { items.contains { $0.fixAction == "reinit_runtime" } }
    private var settingsItem: SelfCheckItem? { items.first { $0.fixAction == "open_settings" } }
    private var wantsSettings: Bool { settingsItem != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "xmark.octagon.fill")
                    .foregroundStyle(theme.up)
                Text("\(items.count) 项自检未通过")
                    .font(KSSFont.themed(13, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                Button(expanded ? "收起" : "详情") { expanded.toggle() }
                    .buttonStyle(.borderless)
                    .controlSize(.small)
                Spacer()
                if wantsReinit {
                    Button {
                        onReinitRuntime()
                    } label: {
                        if isBusy { ProgressView().controlSize(.small) }
                        else { Text("重新初始化运行时") }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(isBusy)
                }
                if let settingsItem {
                    Button("去设置") { onOpenSettings(settingsItem.item) }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
                Button {
                    onDismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(theme.textSecondary)
                }
                .buttonStyle(.plain)
            }
            if expanded {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(items) { item in
                        HStack(spacing: 6) {
                            Text(item.displayName)
                                .font(KSSFont.themed(11.5, .semibold, theme: theme))
                            Text(item.detail)
                                .font(KSSFont.themed(11.5, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 11)
        .frame(maxWidth: 640)
        .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeL))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeL).strokeBorder(theme.up.opacity(0.35), lineWidth: 1))
        .shadow(color: .black.opacity(0.18), radius: 12, y: 4)
    }
}
