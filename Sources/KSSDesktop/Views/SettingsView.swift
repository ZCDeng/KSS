import SwiftUI

/// 统一"设置"工作区页面（plan 2026-07-12-005 / U1；R2-U4 Tab 化；R4 合并为两 tab）：
/// 经典：「凭证与数据源」|「任务与日志」长滚动。
/// xcom（plan 2026-07-23-003）：左分类 + 右详情 master-detail。
struct SettingsView: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var tab: SettingsTab = .credentials
    @State private var selectedCategory: SettingsCategory = .selfCheck
    @State private var dataSourceResults: [String: DataSourceTestResult] = [:]
    @State private var dirtySources: Set<String> = []
    @State private var hoveredCategory: SettingsCategory?

    private var isXcom: Bool { theme.system == .xcom }

    private static let tabOptions: [(key: SettingsTab, label: String)] =
        SettingsTab.allCases.map { ($0, $0.label) }

    private var dataSourcesConfigured: [Bool] {
        [
            store.isCredentialConfigured("tushare"),
            store.isCredentialConfigured("longbridge"),
            store.isCredentialConfigured("telegram"),
            store.isCredentialConfigured("research"),
        ].map { $0 ?? true }   // 尚未自检（nil）时不误判为「未配置」而乱亮点
    }

    private var badgedTabs: Set<SettingsTab> {
        var badged: Set<SettingsTab> = []
        if SettingsTabRouting.dataSourcesNeedsBadge(
            configured: dataSourcesConfigured,
            testsOK: dataSourceResults.values.map(\.ok)
        ) {
            badged.insert(.credentials)
        }
        if SettingsTabRouting.scheduledTasksNeedsBadge(jobs: store.scheduledJobs) {
            badged.insert(.operations)
        }
        return badged
    }

    var body: some View {
        Group {
            if isXcom {
                xcomSettingsShell
            } else {
                classicSettingsShell
            }
        }
        .background(theme.canvas)
        .onAppear(perform: consumeDeepLink)
    }

    private func consumeDeepLink() {
        if let cat = store.consumeSettingsDestination() {
            selectedCategory = cat
            tab = cat.tab
        }
    }

    // MARK: - Classic shell（两 Tab 长滚动，零回归）

    private var classicSettingsShell: some View {
        GeometryReader { geo in
            let w = min(geo.size.width - 48, 1080)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    PageTitle("设置", subtitle: "数据源与凭证 / 任务与日志的唯一入口")
                    SelfCheckStatusStrip(onJump: nil)

                    KSSSegmentedControl(
                        options: Self.tabOptions,
                        selection: $tab,
                        badgedKeys: badgedTabs
                    )

                    switch tab {
                    case .credentials:
                        SettingsCredentialsSection(
                            results: $dataSourceResults,
                            dirtySources: $dirtySources,
                            focusSource: nil
                        )
                        SectionHeader("资讯雷达 · yupi（托管安装 + 监控词）")
                        SettingsIntelKeywordsSection()
                    case .operations:
                        SettingsTasksSection()
                        SectionHeader("日志")
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
    }

    // MARK: - xcom shell（左分类 | 右详情）

    private var xcomSettingsShell: some View {
        HStack(spacing: 0) {
            xcomCategoryNav
                .frame(width: 240)
            Divider().overlay(theme.hairline)
            xcomDetailPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var xcomCategoryNav: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("设置")
                .font(KSSFont.themed(20, .bold, theme: theme))
                .foregroundStyle(theme.textPrimary)
                .padding(.horizontal, 16)
                .padding(.top, 20)
                .padding(.bottom, 12)

            Text("App v\(BridgeClient.appVersion)")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .padding(.horizontal, 16)
                .padding(.bottom, 16)

            ScrollView {
                VStack(spacing: 2) {
                    ForEach(SettingsCategory.allCases) { cat in
                        xcomNavRow(cat)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 16)
            }
        }
        .background(theme.canvas)
    }

    private func xcomNavRow(_ cat: SettingsCategory) -> some View {
        let isOn = selectedCategory == cat
        let isHovered = hoveredCategory == cat
        let needsBadge = SettingsTabRouting.categoryNeedsBadge(
            cat,
            isSourceConfigured: { raw in
                SettingsDataSource(rawValue: raw)?.isConfigured ?? true
            },
            testOK: { raw in dataSourceResults[raw]?.ok },
            jobs: store.scheduledJobs
        )
        let isDirty = cat.dataSource.map { dirtySources.contains($0.rawValue) } ?? false
        let hoverOpacity = theme.appearance == .dark ? 0.10 : 0.07

        return Button {
            withAnimation(.easeOut(duration: 0.12)) {
                selectedCategory = cat
                tab = cat.tab
            }
        } label: {
            HStack(spacing: 10) {
                Text(cat.label)
                    .font(KSSFont.themed(15, isOn ? .bold : .regular, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                if isDirty {
                    Text("·")
                        .font(KSSFont.themed(18, .bold, theme: theme))
                        .foregroundStyle(theme.accent)
                        .help("有未保存更改")
                } else if needsBadge {
                    Circle()
                        .fill(theme.ma5)
                        .frame(width: 7, height: 7)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 999, style: .continuous)
                    .fill(
                        isOn
                            ? theme.textPrimary.opacity(theme.appearance == .dark ? 0.14 : 0.08)
                            : (isHovered ? theme.textPrimary.opacity(hoverOpacity) : Color.clear)
                    )
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? .isSelected : [])
        .onHover { hovering in
            hoveredCategory = hovering ? cat : (hoveredCategory == cat ? nil : hoveredCategory)
        }
    }

    @ViewBuilder
    private var xcomDetailPane: some View {
        ScrollView {
            // 间距/标题与「任务」区同令牌（SettingsFormStyle）
            VStack(alignment: .leading, spacing: SettingsFormStyle.blockSpacing) {
                Text(selectedCategory.label)
                    .font(KSSFont.themed(SettingsFormStyle.pageTitle, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)

                switch selectedCategory {
                case .selfCheck:
                    SelfCheckStatusStrip(onJump: { cat in
                        withAnimation(.easeOut(duration: 0.12)) {
                            selectedCategory = cat
                            tab = cat.tab
                        }
                    })
                case .tushare, .longbridge, .telegram, .research:
                    // 固定 id：多源共享同一份 @State，切换分类不丢未保存编辑（plan KTD3）。
                    SettingsCredentialsSection(
                        results: $dataSourceResults,
                        dirtySources: $dirtySources,
                        focusSource: selectedCategory.dataSource
                    )
                    .id("settings-credentials-shared")
                case .yupi:
                    SettingsIntelKeywordsSection()
                case .tasks:
                    SettingsTasksSection()
                case .logs:
                    SettingsLogsSection()
                }
            }
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, SettingsFormStyle.detailHPadding)
            .padding(.vertical, SettingsFormStyle.detailVPadding)
        }
        .scrollContentBackground(.hidden)
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

/// 凭证与数据源（R4 合并）：按源合一卡——每张卡 = 该源的凭证字段 + 连通性测试 + 独立保存。
/// Keychain 读写与「保存后全杀重启 sidecar」语义承袭原密钥分区。
/// `focusSource != nil`（xcom）：只渲染该源详情；`nil`（经典）：各源同屏。
struct SettingsCredentialsSection: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @Binding var results: [String: DataSourceTestResult]
    @Binding var dirtySources: Set<String>
    /// xcom 单源详情；经典传 nil 渲染全部。
    var focusSource: SettingsDataSource?
    @State private var testing: Set<String> = []
    @State private var saving: Set<String> = []

    @State private var tushareToken = ""
    @State private var telegramBotToken = ""
    @State private var telegramChatId = ""
    @State private var telegramApiUrl = ""
    @State private var longbridgeAppKey = ""
    @State private var longbridgeAppSecret = ""
    @State private var longbridgeAccessToken = ""
    @State private var researchProvider = "disabled"
    @State private var researchJinaKey = ""
    @State private var researchSerperKey = ""
    @State private var researchFixturePath = ""
    /// 已保存反馈按卡显示（source.rawValue）。任一字段编辑即清除对应卡的反馈。
    @State private var savedSources: Set<String> = []
    /// Keychain/provider route 回填会触发 SwiftUI `onChange`；回填期间不能被误判成用户编辑。
    @State private var isHydratingValues = true
    @State private var hydrationGeneration = 0

    private var isXcomFlat: Bool { focusSource != nil }
    private var visibleSources: [SettingsDataSource] {
        if let focusSource { return [focusSource] }
        return SettingsDataSource.allCases
    }

    var body: some View {
        VStack(alignment: .leading, spacing: isXcomFlat ? SettingsFormStyle.blockSpacing : 14) {
            Text("凭据仅存于 macOS Keychain。Seesaw 的模型与 API Key 请在 Seesaw 页面中管理。")
                .font(KSSFont.themed(12.5, theme: theme))
                .foregroundStyle(theme.textSecondary)

            ForEach(visibleSources) { source in
                sourceDetail(source)
            }

            if focusSource == nil {
                HStack {
                    Spacer()
                    Text("App v\(BridgeClient.appVersion) · Python 层 v\(BridgeClient.scriptsVersionOnDisk())")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(theme.textSecondary)
                }
            }
        }
        .onAppear(perform: hydrate)
    }

    @ViewBuilder
    private func sourceDetail(_ source: SettingsDataSource) -> some View {
        switch source {
        case .tushare:
            sourceCard(.tushare, note: "日线/财务/日历数据主源。") {
                field("Tushare Token", text: $tushareToken, secure: true, source: .tushare)
            }
        case .longbridge:
            sourceCard(.longbridge, note: "ChinaConnect LV1 实时行情与分钟 K 线（陆股通池，北交所不覆盖）。") {
                field("App Key", text: $longbridgeAppKey, secure: true, source: .longbridge)
                field("App Secret", text: $longbridgeAppSecret, secure: true, source: .longbridge)
                field("Access Token", text: $longbridgeAccessToken, secure: true, source: .longbridge)
            }
        case .telegram:
            sourceCard(.telegram, note: "复盘/告警推送通道（可选自建中继）。") {
                field("Bot Token", text: $telegramBotToken, secure: true, source: .telegram)
                field("Chat ID", text: $telegramChatId, secure: false, source: .telegram)
                field("API URL（自建中继，可选）", text: $telegramApiUrl, secure: false, source: .telegram)
            }
        case .research:
            sourceCard(.research, note: "覆盖与对话可拉取公告/舆情作为 evidence-only 背景，不得覆盖本地盘面，不得写成动作或仓位。") {
                researchProviderPicker
                if researchProvider == "jina" {
                    field("Jina API Key（可选）", text: $researchJinaKey, secure: true, source: .research)
                }
                if researchProvider == "serper" {
                    field("Serper API Key", text: $researchSerperKey, secure: true, source: .research)
                }
                if researchProvider == "fixture" {
                    field("夹具路径（开发，可选）", text: $researchFixturePath, secure: false, source: .research)
                }
            }
        }
    }

    private var researchProviderPicker: some View {
        VStack(alignment: .leading, spacing: isXcomFlat ? SettingsFormStyle.titleMetaSpacing : 5) {
            Text("提供方")
                .font(KSSFont.themed(
                    isXcomFlat ? SettingsFormStyle.fieldLabel : 13,
                    isXcomFlat ? .bold : .semibold,
                    theme: theme
                ))
                .foregroundStyle(theme.textSecondary)
            Picker("", selection: $researchProvider) {
                ForEach(ResearchProviderOption.allCases) { option in
                    Text(option.label).tag(option.rawValue)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .onChange(of: researchProvider) { _, _ in markDirty(.research) }
        }
    }

    private func markDirty(_ source: SettingsDataSource) {
        guard SettingsCredentialChangePolicy.shouldMarkDirty(isHydrating: isHydratingValues) else {
            return
        }
        dirtySources.insert(source.rawValue)
        savedSources.remove(source.rawValue)
    }

    // MARK: 卡片骨架

    @ViewBuilder
    private func sourceCard<Content: View>(
        _ source: SettingsDataSource, note: String, @ViewBuilder content: () -> Content
    ) -> some View {
        let titleSize: CGFloat = isXcomFlat ? SettingsFormStyle.itemTitle : 16
        let noteSize: CGFloat = isXcomFlat ? SettingsFormStyle.bodyHint : 12.5
        let isConfigured = sourceConfigured(source)
        VStack(alignment: .leading, spacing: isXcomFlat ? SettingsFormStyle.cardInnerSpacing : 12) {
            HStack(spacing: isXcomFlat ? SettingsFormStyle.rowHSpacing : 10) {
                Circle()
                    .fill(isConfigured ? theme.accent : theme.textSecondary.opacity(0.4))
                    .frame(width: 8, height: 8)
                Text(source.displayName)
                    // xcom：与任务行标题 14.5 bold 对齐；经典拉丁源名仍 16 光学对齐 CJK
                    .font(KSSFont.themed(titleSize, .bold, theme: theme))
                    .foregroundStyle(theme.textPrimary)
                if isXcomFlat {
                    SettingsStatusCapsule(
                        text: isConfigured ? "已配置" : "未配置",
                        tint: isConfigured ? theme.accent : theme.textSecondary
                    )
                } else {
                    Text(isConfigured ? "已配置" : "未配置")
                        .font(KSSFont.themed(11.5, .semibold, theme: theme))
                        .foregroundStyle(theme.textSecondary)
                        .padding(.horizontal, 7).padding(.vertical, 1.5)
                        .background(theme.textSecondary.opacity(0.12), in: Capsule())
                }
                Spacer()
                if dirtySources.contains(source.rawValue) {
                    SettingsStatusCapsule(text: "未保存", tint: theme.ma5)
                } else if savedSources.contains(source.rawValue) {
                    Label("已保存", systemImage: "checkmark.seal.fill")
                        .font(KSSFont.themed(SettingsFormStyle.actionLabel, .semibold, theme: theme))
                        .foregroundStyle(theme.accent)
                }
                if isXcomFlat {
                    SettingsBorderedAction(
                        title: "测试",
                        systemImage: "antenna.radiowaves.left.and.right",
                        busy: testing.contains(source.rawValue),
                        action: { Task { await runTest(source) } }
                    )
                    SettingsBorderedAction(
                        title: "保存",
                        systemImage: "square.and.arrow.down",
                        busy: saving.contains(source.rawValue),
                        action: { saveAction(source) }
                    )
                } else {
                    Button {
                        Task { await runTest(source) }
                    } label: {
                        if testing.contains(source.rawValue) {
                            ProgressView().controlSize(.small)
                        } else {
                            Text("测试").font(KSSFont.themed(12.5, .semibold, theme: theme))
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(testing.contains(source.rawValue))
                    Button {
                        saveAction(source)
                    } label: {
                        if saving.contains(source.rawValue) {
                            ProgressView().controlSize(.small)
                        } else {
                            Text("保存")
                                .font(KSSFont.themed(12.5, .semibold, theme: theme))
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(saving.contains(source.rawValue))
                }
            }
            Text(note)
                .font(KSSFont.themed(noteSize, theme: theme))
                .foregroundStyle(theme.textSecondary)
            content()
            if let result = results[source.rawValue] {
                resultDetail(result)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // xcom 与任务行一致用 kssCard，不再 flat hairline
        .modifier(SettingsSourceChromeModifier(flat: false, theme: theme, compact: isXcomFlat))
    }

    @ViewBuilder
    private func subHead(_ title: String) -> some View {
        Text(title)
            .font(KSSFont.themed(
                isXcomFlat ? SettingsFormStyle.sectionHeader : 13,
                isXcomFlat ? .bold : .semibold,
                theme: theme
            ))
            .foregroundStyle(isXcomFlat ? theme.textSecondary : theme.textPrimary)
            .padding(.top, 2)
    }

    @ViewBuilder
    private func field(_ label: String, text: Binding<String>, secure: Bool, source: SettingsDataSource) -> some View {
        VStack(alignment: .leading, spacing: isXcomFlat ? SettingsFormStyle.titleMetaSpacing : 5) {
            Text(label)
                .font(KSSFont.themed(
                    isXcomFlat ? SettingsFormStyle.fieldLabel : 13,
                    isXcomFlat ? .bold : .semibold,
                    theme: theme
                ))
                .foregroundStyle(theme.textSecondary)
            Group {
                if secure {
                    SecureField("", text: text)
                } else {
                    TextField("", text: text)
                }
            }
            .kssInput()
            .onChange(of: text.wrappedValue) { _, _ in markDirty(source) }
        }
    }

    private func sourceConfigured(_ source: SettingsDataSource) -> Bool { source.isConfigured }

    @ViewBuilder
    private func resultDetail(_ result: DataSourceTestResult) -> some View {
        if let candidates = result.candidates, !candidates.isEmpty {
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
                .font(KSSFont.themed(12, theme: theme))
            if let role {
                Text(role == "primary" ? "主" : "备")
                    .font(KSSFont.themed(12, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
            }
            if let model, !model.isEmpty {
                Text(model)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if let latencyMs {
                Text(String(format: "%.0fms", latencyMs))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(theme.textSecondary)
            }
            if !ok, let hint, !hint.isEmpty {
                Text(hint)
                    .font(KSSFont.themed(12, theme: theme))
                    .foregroundStyle(theme.up)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
    }

    // MARK: 读写

    private func hydrate() {
        hydrationGeneration += 1
        let generation = hydrationGeneration
        let hydratedSources = Set(visibleSources.map(\.rawValue))
        isHydratingValues = true
        load()

        // `onChange` 在本次 SwiftUI transaction 结算时才执行；下一次主队列 tick
        // 再解除 gate，并清理仅由程序回填产生的 dirty 标记。
        DispatchQueue.main.async {
            guard generation == hydrationGeneration else { return }
            dirtySources.subtract(hydratedSources)
            isHydratingValues = false
        }
    }

    private func load() {
        tushareToken = KeychainStore.read("TUSHARE_TOKEN") ?? ""
        telegramBotToken = KeychainStore.read("TELEGRAM_BOT_TOKEN") ?? ""
        telegramChatId = KeychainStore.read("TELEGRAM_CHAT_ID") ?? ""
        telegramApiUrl = KeychainStore.read("TELEGRAM_API_URL") ?? ""
        longbridgeAppKey = KeychainStore.read("LONGBRIDGE_APP_KEY") ?? ""
        longbridgeAppSecret = KeychainStore.read("LONGBRIDGE_APP_SECRET") ?? ""
        longbridgeAccessToken = KeychainStore.read("LONGBRIDGE_ACCESS_TOKEN") ?? ""
        let provider = (KeychainStore.read("KSS_RESEARCH_PROVIDER") ?? "disabled").lowercased()
        researchProvider = ResearchProviderOption(rawValue: provider)?.rawValue ?? "disabled"
        researchJinaKey = KeychainStore.read("JINA_API_KEY") ?? ""
        researchSerperKey = KeychainStore.read("SERPER_API_KEY") ?? ""
        researchFixturePath = KeychainStore.read("KSS_RESEARCH_FIXTURE_PATH") ?? ""
    }

    /// 按源保存（只写该卡字段）；随后重启 sidecar 并刷新自检。
    private func saveAction(_ source: SettingsDataSource) {
        save(source)
    }

    private func save(_ source: SettingsDataSource) {
        switch source {
        case .tushare:
            KeychainStore.write("TUSHARE_TOKEN", tushareToken)
        case .longbridge:
            KeychainStore.write("LONGBRIDGE_APP_KEY", longbridgeAppKey)
            KeychainStore.write("LONGBRIDGE_APP_SECRET", longbridgeAppSecret)
            KeychainStore.write("LONGBRIDGE_ACCESS_TOKEN", longbridgeAccessToken)
        case .telegram:
            KeychainStore.write("TELEGRAM_BOT_TOKEN", telegramBotToken)
            KeychainStore.write("TELEGRAM_CHAT_ID", telegramChatId)
            KeychainStore.write("TELEGRAM_API_URL", telegramApiUrl)
        case .research:
            KeychainStore.write("KSS_RESEARCH_PROVIDER", researchProvider)
            KeychainStore.write("KSS_RESEARCH_FETCH_PROVIDER", researchProvider)
            KeychainStore.write("JINA_API_KEY", researchJinaKey)
            KeychainStore.write("SERPER_API_KEY", researchSerperKey)
            KeychainStore.write("KSS_RESEARCH_FIXTURE_PATH", researchFixturePath)
        }
        BridgeClient.restartSidecarForEnvChange()
        store.refreshLLMCredentialsStatus()
        Task { await store.runSelfCheck() }
        dirtySources.remove(source.rawValue)
        savedSources.insert(source.rawValue)
    }

    private func runTest(_ source: SettingsDataSource) async {
        guard let bridge = store.bridge else { return }
        testing.insert(source.rawValue)
        defer { testing.remove(source.rawValue) }
        let result: DataSourceTestResult?
        result = try? await Task.detached { try bridge.datasourceTest(source: source.rawValue) }.value
        if let result {
            results[source.rawValue] = result
        }
    }
}

enum SettingsCredentialChangePolicy {
    static func shouldMarkDirty(isHydrating: Bool) -> Bool {
        !isHydrating
    }
}

private enum ResearchProviderOption: String, CaseIterable, Identifiable {
    case disabled, requests, jina, serper, combosearch, fixture
    var id: String { rawValue }

    var label: String {
        switch self {
        case .disabled: return "关闭"
        case .requests: return "HTTP 直连"
        case .jina: return "Jina"
        case .serper: return "Serper"
        case .combosearch: return "本机 comboSearch"
        case .fixture: return "本地夹具（开发）"
        }
    }
}

// MARK: - 数据源定义（配置状态判定，凭证卡与 tab 状态点共用）

enum SettingsDataSource: String, CaseIterable, Identifiable {
    case tushare, longbridge, telegram, research
    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .tushare: return "Tushare"
        case .longbridge: return "Longbridge"
        case .telegram: return "Telegram"
        case .research: return "外部研究"
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
        case .research:
            let provider = (KeychainStore.read("KSS_RESEARCH_PROVIDER") ?? "disabled").lowercased()
            if provider == "serper" {
                return !(KeychainStore.read("SERPER_API_KEY") ?? "").isEmpty
            }
            return ["fixture", "requests", "jina", "combosearch"].contains(provider)
        }
    }

    var settingsCategory: SettingsCategory {
        switch self {
        case .tushare: return .tushare
        case .longbridge: return .longbridge
        case .telegram: return .telegram
        case .research: return .research
        }
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}

extension SettingsCategory {
    var dataSource: SettingsDataSource? {
        switch self {
        case .tushare: return .tushare
        case .longbridge: return .longbridge
        case .telegram: return .telegram
        case .research: return .research
        default: return nil
        }
    }
}

// MARK: - 资讯雷达 yupi 词表（plan 2026-07-21-001 + UX 清单）

/// 运行时检查清单的一行（绿/黄/红 + 文案）。
private struct YupiStatusRow: Identifiable {
    enum Level { case ok, warn, fail, info }
    let id: String
    let label: String
    let level: Level
    let detail: String
}

/// 12 赛道监控词 + KSS 托管 yupi 运行时（安装/启动/OpenRouter）。
/// 状态清单与操作反馈分栏，避免与词表 status 互相覆盖。
struct SettingsIntelKeywordsSection: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    @State private var tracks: [String: [String]] = [:]
    @State private var trackOrder: [String] = []
    @State private var draft: [String: String] = [:]  // key -> comma-separated words
    @State private var keywordNote: String = ""       // 仅词表区
    @State private var actionBanner: String = ""      // 仅安装/启动/凭据操作
    @State private var actionIsError: Bool = false
    @State private var loading = false
    @State private var saving = false
    @State private var openrouterKey: String = ""
    @State private var yupiModel: String = ""
    @State private var ensuring = false
    @State private var runtime: YupiRuntimeStatus?
    @State private var nextStep: String = ""
    @State private var primaryActionTitle: String = "安装并启动"

    private var useTasksStyle: Bool { SettingsFormStyle.usesTasksStandard(theme.system) }

    var body: some View {
        VStack(alignment: .leading, spacing: useTasksStyle ? SettingsFormStyle.blockSpacing : 12) {
            SettingsHintText(text: "KSS 在本机托管 yupi（端口 18765，KeepAlive）。状态按行列出；Seesaw 主/备若 base 为 OpenRouter 会自动复用 Key/模型。")

            // ---- 状态清单（对齐任务健康汇总卡）----
            VStack(alignment: .leading, spacing: useTasksStyle ? SettingsFormStyle.groupSpacing : 6) {
                ForEach(statusRows) { row in
                    HStack(alignment: .top, spacing: useTasksStyle ? SettingsFormStyle.rowHSpacing : 8) {
                        Circle()
                            .fill(dotColor(row.level))
                            .frame(width: 8, height: 8)
                            .padding(.top, useTasksStyle ? 6 : 4)
                        Text(row.label)
                            .font(KSSFont.themed(
                                useTasksStyle ? SettingsFormStyle.sectionHeader : 12,
                                .bold,
                                theme: theme
                            ))
                            .foregroundStyle(theme.textSecondary)
                            .frame(width: 72, alignment: .leading)
                        Text(row.detail)
                            .font(KSSFont.themed(
                                useTasksStyle ? SettingsFormStyle.meta : 12,
                                theme: theme
                            ))
                            .foregroundStyle(theme.textSecondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(padding: useTasksStyle ? SettingsFormStyle.cardPadding : 10)

            if !nextStep.isEmpty {
                Text("下一步：\(nextStep)")
                    .font(KSSFont.themed(
                        useTasksStyle ? SettingsFormStyle.sectionHeader : 12.5,
                        .bold,
                        theme: theme
                    ))
                    .foregroundStyle(theme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if ensuring {
                SettingsInfoBanner(
                    text: "安装/启动中（首次 2–5 分钟，请勿关闭设置页）…",
                    systemImage: "arrow.triangle.2.circlepath"
                )
            } else if !actionBanner.isEmpty {
                SettingsInfoBanner(text: actionBanner, isError: actionIsError)
            }

            VStack(alignment: .leading, spacing: useTasksStyle ? SettingsFormStyle.groupSpacing : 10) {
                SecureField("OpenRouter API Key（可选，优先于 Seesaw 复用）", text: $openrouterKey)
                    .kssInput()
                TextField("模型覆盖（空=默认/复用 Seesaw）", text: $yupiModel)
                    .kssInput()
            }
            .kssCard(padding: useTasksStyle ? SettingsFormStyle.cardPadding : 10)

            HStack(spacing: 10) {
                SettingsBorderedAction(
                    title: "保存 yupi 凭据",
                    systemImage: "square.and.arrow.down",
                    disabled: ensuring,
                    action: { saveYupiCreds() }
                )
                SettingsPrimaryAction(
                    title: ensuring ? "处理中…" : primaryActionTitle,
                    systemImage: "play.circle.fill",
                    busy: ensuring,
                    action: { Task { await ensureYupi() } }
                )
                SettingsBorderedAction(
                    title: "刷新状态",
                    systemImage: "arrow.clockwise",
                    disabled: ensuring,
                    action: { Task { await refreshRuntime() } }
                )
            }

            // 分类头对齐任务 categoryBlock
            HStack {
                Text("赛道监控词")
                    .font(KSSFont.themed(SettingsFormStyle.sectionHeader, .bold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                Text("灌入热议")
                    .font(KSSFont.themed(10.5, .bold, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(theme.textSecondary.opacity(0.12), in: Capsule())
                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .kssCard(.filled, padding: 8)

            if loading {
                ProgressView().controlSize(.small)
            } else {
                ForEach(trackOrder, id: \.self) { key in
                    HStack(alignment: .center, spacing: SettingsFormStyle.rowHSpacing) {
                        Text(key)
                            .font(KSSFont.themed(SettingsFormStyle.itemTitle, .bold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                            .frame(width: 72, alignment: .leading)
                        TextField("词1, 词2, …", text: Binding(
                            get: { draft[key] ?? "" },
                            set: { draft[key] = $0 }
                        ))
                        .kssInput()
                    }
                    .kssCard(padding: 11)
                }
            }

            HStack(spacing: 10) {
                SettingsPrimaryAction(
                    title: "保存词表",
                    systemImage: "square.and.arrow.down",
                    busy: saving,
                    disabled: loading || ensuring,
                    action: { Task { await saveKeywords() } }
                )
                SettingsBorderedAction(
                    title: "立即灌入",
                    systemImage: "arrow.down.circle",
                    disabled: saving || loading || ensuring,
                    action: { Task { await ingest() } }
                )
                SettingsBorderedAction(
                    title: "重新加载",
                    systemImage: "arrow.clockwise",
                    disabled: ensuring,
                    action: { Task { await loadKeywords() } }
                )
            }
            if !keywordNote.isEmpty {
                SettingsHintText(text: keywordNote)
            }
        }
        .task {
            openrouterKey = KeychainStore.read("OPENROUTER_API_KEY") ?? ""
            yupiModel = KeychainStore.read("KSS_YUPI_MODEL") ?? ""
            await loadKeywords()
            await refreshRuntime()
        }
    }

    // MARK: - 状态派生

    private var statusRows: [YupiStatusRow] {
        guard let st = runtime else {
            return [
                YupiStatusRow(id: "svc", label: "服务", level: .info, detail: "尚未读取状态，点「刷新状态」"),
            ]
        }
        var rows: [YupiStatusRow] = []

        // 服务
        if st.healthOk == true {
            rows.append(YupiStatusRow(
                id: "svc", label: "服务", level: .ok,
                detail: "运行中  \(st.baseUrl ?? "http://127.0.0.1:18765")"
            ))
        } else if st.installed == true {
            rows.append(YupiStatusRow(
                id: "svc", label: "服务", level: .warn,
                detail: "已安装但未响应  \(st.baseUrl ?? "") — 点「\(primaryActionTitle)」"
            ))
        } else {
            rows.append(YupiStatusRow(
                id: "svc", label: "服务", level: .fail,
                detail: "未安装 — 点「安装并启动」（首次 2–5 分钟）"
            ))
        }

        // Node
        if st.nodeOk == true {
            rows.append(YupiStatusRow(id: "node", label: "Node", level: .ok, detail: st.node ?? "可用"))
        } else if st.healthOk == true {
            rows.append(YupiStatusRow(
                id: "node", label: "Node", level: .warn,
                detail: "探测失败但服务已在跑（多为 App PATH 窄）。本机可能已装 Node，安装流程会再查 brew 路径。"
            ))
        } else {
            rows.append(YupiStatusRow(
                id: "node", label: "Node", level: .fail,
                detail: st.node ?? "未找到 node/npm。请 brew install node 后重启 App"
            ))
        }

        // OpenRouter
        if st.hasOpenrouterKey == true {
            let src = st.openrouterKeySource ?? "?"
            rows.append(YupiStatusRow(
                id: "or", label: "OpenRouter", level: .ok,
                detail: "已配（\(src)）· model=\(st.model ?? "?")"
            ))
        } else {
            rows.append(YupiStatusRow(
                id: "or", label: "OpenRouter", level: .warn,
                detail: "未配置 — 服务仍可运行，热议 AI 分析会降级。可在此填 Key，或把 Seesaw 主 LLM base 设为 OpenRouter 以自动复用"
            ))
        }

        // 安装 / pin
        if st.installed == true {
            let pin = st.gitHead?.prefix(8) ?? st.gitRef?.prefix(8) ?? "?"
            rows.append(YupiStatusRow(id: "inst", label: "安装", level: .ok, detail: "已安装  pin \(pin)"))
        } else {
            rows.append(YupiStatusRow(id: "inst", label: "安装", level: .fail, detail: "未安装到 Application Support"))
        }

        // KeepAlive
        if st.launchdLoaded == true {
            rows.append(YupiStatusRow(id: "ka", label: "KeepAlive", level: .ok, detail: "launchd 已加载 com.zcdeng.kss.yupi_server"))
        } else {
            rows.append(YupiStatusRow(
                id: "ka", label: "KeepAlive", level: .warn,
                detail: "launchd 未加载 — 设置→任务 同步 cron，或安装后仍可用临时进程"
            ))
        }
        return rows
    }

    private func dotColor(_ level: YupiStatusRow.Level) -> Color {
        switch level {
        case .ok: return Color.green.opacity(0.85)
        case .warn: return Color.orange.opacity(0.9)
        case .fail: return theme.up
        case .info: return theme.textSecondary.opacity(0.5)
        }
    }

    private func applyRuntime(_ st: YupiRuntimeStatus) {
        runtime = st
        // 主按钮语义（D）
        if st.healthOk == true {
            primaryActionTitle = "重启 yupi"
            nextStep = st.hasOpenrouterKey == true
                ? "服务正常。可改监控词后点「立即灌入」。"
                : "服务在跑但 OpenRouter 未配 — 热议 AI 会降级；填 Key 并保存，或配置 Seesaw 主 LLM 为 OpenRouter。"
        } else if st.installed == true {
            primaryActionTitle = "启动 yupi"
            nextStep = "已安装未运行 — 点「启动 yupi」。若反复失败，检查端口 18765 是否被占用。"
        } else {
            primaryActionTitle = "安装并启动"
            nextStep = st.nodeOk == true
                ? "点「安装并启动」（首次需联网 npm，约 2–5 分钟）。"
                : "先安装 Node.js ≥ 18（brew install node），重启 App 后再点「安装并启动」。"
        }
    }

    // MARK: - 操作

    private func saveYupiCreds() {
        KeychainStore.write("OPENROUTER_API_KEY", openrouterKey)
        KeychainStore.write("KSS_YUPI_MODEL", yupiModel)
        BridgeClient.restartSidecarForEnvChange()
        actionIsError = false
        actionBanner = "凭据已保存到 Keychain，并已重启 bridge 以注入环境。"
        Task {
            await store.runSelfCheck()
            await refreshRuntime()
        }
    }

    private func refreshRuntime() async {
        guard let bridge = store.bridge else {
            runtime = nil
            nextStep = "bridge 未就绪，无法读 yupi 状态。"
            return
        }
        do {
            let st = try await Task.detached { try bridge.yupiStatus() }.value
            applyRuntime(st)
        } catch {
            runtime = nil
            nextStep = "状态读取失败：\(error.localizedDescription)"
        }
    }

    private func ensureYupi() async {
        ensuring = true
        actionIsError = false
        actionBanner = ""
        defer { ensuring = false }
        guard let bridge = store.bridge else {
            actionIsError = true
            actionBanner = "bridge 未就绪，无法安装/启动。"
            return
        }
        let title = primaryActionTitle
        actionBanner = "正在\(title)… 首次安装需联网下载依赖，请稍候。"
        do {
            let r = try await Task.detached { try bridge.yupiEnsure(force: false) }.value
            if r.ok == true {
                actionIsError = false
                actionBanner = "成功：\(title) 完成。\(r.baseUrl.map { " 地址 \($0)" } ?? "")"
            } else {
                actionIsError = true
                let detail = r.error
                    ?? r.install?.error
                    ?? "unknown"
                actionBanner = "失败：\(detail)"
            }
            await refreshRuntime()
            await store.runSelfCheck()
        } catch {
            actionIsError = true
            actionBanner = "失败：\(error.localizedDescription)"
            await refreshRuntime()
        }
    }

    private func loadKeywords() async {
        loading = true
        defer { loading = false }
        guard let bridge = store.bridge else {
            keywordNote = "bridge 未就绪"
            return
        }
        do {
            let resp = try await Task.detached { try bridge.intelKeywordsGet() }.value
            let t = resp.tracks ?? [:]
            tracks = t
            trackOrder = t.keys.sorted()
            draft = t.mapValues { $0.joined(separator: ", ") }
            keywordNote = "已加载 \(trackOrder.count) 赛道词表"
        } catch {
            keywordNote = "词表加载失败: \(error.localizedDescription)"
        }
    }

    private func saveKeywords() async {
        saving = true
        defer { saving = false }
        guard let bridge = store.bridge else {
            keywordNote = "bridge 未就绪"
            return
        }
        var out: [String: [String]] = [:]
        for key in trackOrder {
            let parts = (draft[key] ?? "")
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            out[key] = parts
        }
        do {
            let resp = try await Task.detached { try bridge.intelKeywordsSet(tracks: out) }.value
            tracks = resp.tracks ?? out
            draft = tracks.mapValues { $0.joined(separator: ", ") }
            keywordNote = "词表已保存"
        } catch {
            keywordNote = "词表保存失败: \(error.localizedDescription)"
        }
    }

    private func ingest() async {
        keywordNote = "灌入中…"
        guard let bridge = store.bridge else {
            keywordNote = "bridge 未就绪"
            return
        }
        do {
            _ = try await Task.detached { try bridge.intelYupiIngest(force: false) }.value
            keywordNote = "灌入完成（yupi 不可用时仅保留 RSS）"
            await refreshRuntime()
        } catch {
            keywordNote = "灌入失败: \(error.localizedDescription)"
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

    private var useTasksStyle: Bool { SettingsFormStyle.usesTasksStandard(theme.system) }

    var body: some View {
        VStack(alignment: .leading, spacing: useTasksStyle ? SettingsFormStyle.blockSpacing : 10) {
            HStack(spacing: useTasksStyle ? SettingsFormStyle.rowHSpacing : 8) {
                Picker("文件", selection: $selected) {
                    Text("选择日志文件").tag(LogFileEntry?.none)
                    ForEach(sortedFiles) { f in
                        Text("\(f.name) · \(f.sizeLabel)").tag(Optional(f))
                    }
                }
                .frame(maxWidth: 360)
                .onChange(of: selected) { _, _ in Task { await loadTail() } }

                SettingsBorderedAction(
                    title: "刷新",
                    systemImage: "arrow.clockwise",
                    busy: loadingList,
                    action: { Task { await loadList() } }
                )

                Spacer()

                TextField("搜索关键词", text: $searchText)
                    .kssInput()
                    .frame(maxWidth: 220)
                    .onSubmit { Task { await loadTail() } }
                SettingsBorderedAction(
                    title: "搜索",
                    systemImage: "magnifyingglass",
                    disabled: selected == nil,
                    action: { Task { await loadTail() } }
                )
            }
            .kssCard(padding: useTasksStyle ? SettingsFormStyle.cardPadding : 10)

            if selected != nil {
                HStack {
                    Text("共 \(totalMatched) 行匹配 · 显示末尾 \(tailLines.count) 行")
                        .font(KSSFont.themed(
                            useTasksStyle ? SettingsFormStyle.meta : 11,
                            theme: theme
                        ))
                        .foregroundStyle(theme.textSecondary)
                    Spacer()
                    if loadingTail { ProgressView().controlSize(.small) }
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(tailLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: SettingsFormStyle.monoMeta, design: .monospaced))
                                .foregroundStyle(theme.textPrimary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        if tailLines.isEmpty && !loadingTail {
                            SettingsHintText(text: "无匹配内容", empty: true)
                        }
                    }
                    .padding(SettingsFormStyle.cardPadding)
                }
                .frame(height: 320)
                .kssCard(padding: 0)
                .overlay(
                    RoundedRectangle(cornerRadius: KSSTheme.shapeM)
                        .strokeBorder(theme.hairline, lineWidth: 1)
                )
            } else {
                SettingsHintText(text: "选择上方日志文件查看内容", empty: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .kssCard(padding: SettingsFormStyle.cardPadding)
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

/// 设置页自检摘要 + 手动重跑。xcom 详情内可点行跳转分类（`onJump`），样式对齐任务健康卡。
struct SelfCheckStatusStrip: View {
    @Environment(\.kssTheme) private var theme
    @EnvironmentObject private var store: KSSStore
    /// nil = 经典折叠详情；非 nil = xcom 默认可点跳转。
    var onJump: ((SettingsCategory) -> Void)?
    @State private var expanded = false

    private var failCount: Int { store.selfCheckItems.filter(\.isFail).count }
    private var warnCount: Int { store.selfCheckItems.filter(\.isWarn).count }
    private var alwaysExpanded: Bool { onJump != nil }
    private var useTasksStyle: Bool { alwaysExpanded || SettingsFormStyle.usesTasksStandard(theme.system) }

    var body: some View {
        VStack(alignment: .leading, spacing: useTasksStyle ? SettingsFormStyle.blockSpacing : 8) {
            // 汇总条 ≈ 任务 healthSummary 卡
            HStack(spacing: useTasksStyle ? SettingsFormStyle.rowHSpacing : 10) {
                Image(systemName: statusIcon)
                    .font(useTasksStyle ? KSSFont.themed(16, .semibold, theme: theme) : .body)
                    .foregroundStyle(statusTint)
                    .frame(width: useTasksStyle ? 22 : nil)
                Text(summaryText)
                    .font(KSSFont.themed(
                        useTasksStyle ? SettingsFormStyle.itemTitle : 13,
                        useTasksStyle ? .bold : .semibold,
                        theme: theme
                    ))
                    .foregroundStyle(theme.textPrimary)
                if let at = store.selfCheckGeneratedAt {
                    Text("· \(at)")
                        .font(KSSFont.themed(
                            useTasksStyle ? SettingsFormStyle.metaSmall : 11,
                            theme: theme
                        ))
                        .foregroundStyle(theme.textSecondary)
                }
                Spacer()
                if !alwaysExpanded, !store.selfCheckItems.isEmpty {
                    Button(expanded ? "收起" : "详情") { expanded.toggle() }
                        .buttonStyle(.borderless)
                        .controlSize(.small)
                }
                if useTasksStyle {
                    SettingsBorderedAction(
                        title: "重新自检",
                        systemImage: "arrow.clockwise",
                        busy: store.isRunningSelfCheck,
                        action: { Task { await store.runSelfCheck() } }
                    )
                } else {
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
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .modifier(SelfCheckChromeModifier(useTasksStyle: useTasksStyle))

            if alwaysExpanded || expanded {
                VStack(alignment: .leading, spacing: useTasksStyle ? SettingsFormStyle.groupSpacing : 6) {
                    ForEach(store.selfCheckItems) { item in
                        selfCheckItemRow(item)
                    }
                    if store.selfCheckItems.isEmpty {
                        SettingsHintText(text: "尚未跑过自检，点右上角重新自检。", empty: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .kssCard(padding: SettingsFormStyle.cardPadding)
                    }
                }
            }
        }
        .onAppear {
            if alwaysExpanded { expanded = true }
        }
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
        let cat = SettingsTabRouting.targetCategory(forSelfCheckItem: item.item)
        let useTasks = useTasksStyle
        let row = HStack(spacing: useTasks ? SettingsFormStyle.rowHSpacing : 8) {
            Image(systemName: item.isOK ? "checkmark.circle.fill" : (item.isFail ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"))
                .font(KSSFont.themed(useTasks ? 16 : 11.5, .semibold, theme: theme))
                .foregroundStyle(item.isOK ? theme.accent : (item.isFail ? theme.up : theme.ma5))
                .frame(width: useTasks ? 22 : nil)
            VStack(alignment: .leading, spacing: useTasks ? SettingsFormStyle.titleMetaSpacing : 0) {
                Text(item.displayName)
                    .font(KSSFont.themed(
                        useTasks ? SettingsFormStyle.itemTitle : 12,
                        useTasks ? .bold : .semibold,
                        theme: theme
                    ))
                    .foregroundStyle(theme.textPrimary)
                Text(item.detail)
                    .font(KSSFont.themed(
                        useTasks ? SettingsFormStyle.meta : 11.5,
                        theme: theme
                    ))
                    .foregroundStyle(theme.textSecondary)
                    .lineLimit(2)
            }
            Spacer()
            if let hint = item.fixHint, !item.isOK {
                Text(hint)
                    .font(KSSFont.themed(useTasks ? SettingsFormStyle.metaSmall : 11, .semibold, theme: theme))
                    .foregroundStyle(theme.accent)
                    .lineLimit(1)
            }
            if onJump != nil, !item.isOK, cat != .selfCheck {
                Image(systemName: "chevron.right")
                    .font(KSSFont.themed(10, .semibold, theme: theme))
                    .foregroundStyle(theme.textSecondary.opacity(0.7))
            }
        }
        .contentShape(Rectangle())
        .modifier(SelfCheckRowChromeModifier(useTasksStyle: useTasks))

        if item.item == "llm", !item.isOK {
            return AnyView(
                Button { store.openSeesawModels() } label: { row }
                    .buttonStyle(.plain)
                    .help("在 Seesaw 中配置模型")
            )
        }
        if let onJump, !item.isOK {
            return AnyView(
                Button { onJump(cat) } label: { row }
                    .buttonStyle(.plain)
                    .help("前往 \(cat.label)")
            )
        }
        return AnyView(row)
    }
}

private struct SelfCheckChromeModifier: ViewModifier {
    @Environment(\.kssTheme) private var theme
    var useTasksStyle: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if useTasksStyle {
            content.kssCard(padding: SettingsFormStyle.cardPadding)
        } else {
            content
                .padding(.horizontal, 14).padding(.vertical, 10)
                .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        }
    }
}

private struct SelfCheckRowChromeModifier: ViewModifier {
    @Environment(\.kssTheme) private var theme
    var useTasksStyle: Bool

    func body(content: Content) -> some View {
        if useTasksStyle {
            content
                .padding(11)
                .frame(maxWidth: .infinity, alignment: .leading)
                .kssCard(padding: 11)
        } else {
            content
        }
    }
}

// MARK: - 凭证卡 chrome（统一 kssCard；xcom compact 对齐任务行 padding 12）

private struct SettingsSourceChromeModifier: ViewModifier {
    var flat: Bool
    var theme: KSSThemeTokens
    var compact: Bool = false

    func body(content: Content) -> some View {
        if flat {
            content
                .padding(.vertical, 4)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(theme.hairline).frame(height: 1)
                }
        } else {
            content.kssCard(padding: compact ? SettingsFormStyle.cardPadding : 16)
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
