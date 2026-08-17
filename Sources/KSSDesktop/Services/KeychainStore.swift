import Foundation
import Security

/// U3：网络凭据存 macOS Keychain（不再明文落 .env）。
/// 受管键与 bridge `_load_project_env` 的 allowed 对齐；BridgeClient 启动时读出注入子进程 env。
enum KeychainStore {
    static let service = "com.zcdeng.KSSDesktop.credentials"
    private static let providerAccountPrefix = "KSS_PROVIDER_API_KEY."
    private static let providerIndexDefaultsKey = "kss.llm.providerCredentialIds.v1"

    /// 敏感凭据（存 Keychain）。非敏感项（API URL / 模型名 / live 开关）一并管理便于配置注入。
    /// LLM key 用于 app 内 AI 复盘助手（#4）：sidecar 启动时读出注入子进程 env，
    /// openai_client 据 OPENAI_API_KEY / DEEPSEEK_API_KEY 解析网关。KSS_APP_LIVE=1 才允许 loop 真写。
    static let managedKeys = [
        "TUSHARE_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_API_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
        "KSS_LLM_MODEL",
        "KSS_APP_LIVE",
        // BYOK 端点泛化（plan 2026-07-12-005 / U3）：主/备供应商各自 base_url/key/model。
        // 全缺时 openai_client._resolve_credential_candidates 兼容映射到上面的旧四键。
        "KSS_LLM_PRIMARY_KEY",
        "KSS_LLM_PRIMARY_BASE_URL",
        "KSS_LLM_PRIMARY_MODEL",
        "KSS_LLM_FALLBACK_KEY",
        "KSS_LLM_FALLBACK_BASE_URL",
        "KSS_LLM_FALLBACK_MODEL",
        // Longbridge（U6）：实时行情凭据，注入 sidecar env 供 LongbridgeProvider 读。
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
        // yupi-hot-monitor（KSS 托管）：OpenRouter Key + 可选模型覆盖
        "OPENROUTER_API_KEY",
        "KSS_YUPI_MODEL",
        "KSS_YUPI_PORT",
        // 外部研究（evidence-only）：provider + 可选 Jina/Serper Key，注入 sidecar。
        "KSS_RESEARCH_PROVIDER",
        "KSS_RESEARCH_FETCH_PROVIDER",
        "KSS_RESEARCH_FIXTURE_PATH",
        "KSS_COMBOSEARCH_BIN",
        "JINA_API_KEY",
        "SERPER_API_KEY",
    ]

    static func read(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty
        else { return nil }
        return value
    }

    /// 写入；空值删除该项。返回是否成功。
    @discardableResult
    static func write(_ key: String, _ value: String) -> Bool {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            SecItemDelete(base as CFDictionary)
            return true
        }
        let data = Data(trimmed.utf8)
        let status = SecItemUpdate(base as CFDictionary,
                                   [kSecValueData as String: data] as CFDictionary)
        if status == errSecSuccess { return true }
        if status == errSecItemNotFound {
            var add = base
            add[kSecValueData as String] = data
            return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
        }
        return false
    }

    private static let llmSecretKeys: Set<String> = [
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "KSS_LLM_PRIMARY_KEY",
        "KSS_LLM_FALLBACK_KEY",
    ]

    private static let llmCredentialPresenceEnv: [String: String] = [
        "OPENAI_API_KEY": "OPENAI_API_KEY_PRESENT",
        "DEEPSEEK_API_KEY": "DEEPSEEK_API_KEY_PRESENT",
        "KSS_LLM_PRIMARY_KEY": "KSS_LLM_PRIMARY_CREDENTIAL_PRESENT",
        "KSS_LLM_FALLBACK_KEY": "KSS_LLM_FALLBACK_CREDENTIAL_PRESENT",
    ]

    /// 注入子进程的 env 片段（仅含已配置的键）。
    ///
    /// LLM BYOK secrets are excluded for the long-lived agent sidecar. The
    /// sidecar only receives non-secret route metadata and presence flags; the
    /// actual API keys are served to the signed pi-ai helper through a
    /// nonce-bound local Unix socket.
    static func injectedEnvironment(includeLLMSecrets: Bool = true) -> [String: String] {
        var out: [String: String] = [:]
        for key in managedKeys {
            if !includeLLMSecrets && llmSecretKeys.contains(key) {
                continue
            }
            if let value = read(key) { out[key] = value }
        }
        if !includeLLMSecrets {
            for (key, flag) in llmCredentialPresenceEnv where read(key) != nil {
                out[flag] = "1"
            }
        }
        return out
    }

    static func sidecarEnvironment() -> [String: String] {
        injectedEnvironment(includeLLMSecrets: false)
    }

    /// bridge 一次性子进程环境：完整 LLM 密钥 + Seesaw provider 作用域密钥映射。
    /// 资讯改写 / 全景 / digest 读 `LLMClient` env，不走 pi-ai broker。
    static func bridgeEnvironment() -> [String: String] {
        var out = injectedEnvironment(includeLLMSecrets: true)
        // Seesaw 页面写入的是 KSS_PROVIDER_API_KEY.<id>；旧链路读 DEEPSEEK/OPENAI/PRIMARY。
        // 缺省时把 provider 作用域 key 映射过去，避免「Seesaw 已配好但雷达改写仍无凭据」。
        func putIfAbsent(_ envKey: String, _ value: String?) {
            guard out[envKey] == nil || out[envKey]?.isEmpty == true,
                  let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else { return }
            out[envKey] = value
        }
        putIfAbsent("KSS_LLM_PRIMARY_KEY", readProviderAPIKey("kss-primary"))
        putIfAbsent("KSS_LLM_FALLBACK_KEY", readProviderAPIKey("kss-fallback"))
        putIfAbsent("DEEPSEEK_API_KEY", readProviderAPIKey("deepseek") ?? read("DEEPSEEK_API_KEY"))
        putIfAbsent("OPENAI_API_KEY", readProviderAPIKey("openai") ?? read("OPENAI_API_KEY"))
        putIfAbsent("OPENROUTER_API_KEY", readProviderAPIKey("openrouter") ?? read("OPENROUTER_API_KEY"))
        // primary 未写时，用 deepseek/openai 任一已有密钥顶上，保证 rewrite 至少有一个候选
        if out["KSS_LLM_PRIMARY_KEY"] == nil || out["KSS_LLM_PRIMARY_KEY"]?.isEmpty == true {
            putIfAbsent("KSS_LLM_PRIMARY_KEY", out["DEEPSEEK_API_KEY"] ?? out["OPENAI_API_KEY"])
        }
        return out
    }

    static func hasLLMCredentials() -> Bool {
        llmSecretKeys.contains { read($0) != nil } || !providerCredentialIds().isEmpty
    }

    /// The Composer must validate the credential for its selected route, not
    /// merely any LLM key on this Mac. This mirrors the broker's alias rules
    /// without returning the secret to the caller.
    static func hasLLMCredential(forProviderID providerId: String?) -> Bool {
        guard let providerId = providerId?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !providerId.isEmpty
        else { return false }
        if readProviderAPIKey(providerId) != nil { return true }
        switch providerId {
        case "kss-primary": return read("KSS_LLM_PRIMARY_KEY") != nil
        case "kss-fallback": return read("KSS_LLM_FALLBACK_KEY") != nil
        case "deepseek": return read("DEEPSEEK_API_KEY") != nil
        case "openai": return read("OPENAI_API_KEY") != nil
        case "openrouter": return read("OPENROUTER_API_KEY") != nil
        default: return false
        }
    }

    static func readProviderAPIKey(_ providerId: String) -> String? {
        read(providerAccountPrefix + providerId)
    }

    @discardableResult
    static func writeProviderAPIKey(_ providerId: String, _ value: String) -> Bool {
        let trimmedId = providerId.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedId.isEmpty,
              trimmedId.range(of: #"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"#,
                              options: .regularExpression) != nil
        else { return false }
        let saved = write(providerAccountPrefix + trimmedId, value)
        guard saved else { return false }
        var ids = Set(providerCredentialIds())
        if value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            ids.remove(trimmedId)
        } else {
            ids.insert(trimmedId)
        }
        UserDefaults.standard.set(ids.sorted(), forKey: providerIndexDefaultsKey)
        return true
    }

    /// 与 Python kss.agent.harness_settings.custom_provider_env_name 保持一致。
    static func customProviderEnvName(_ providerId: String) -> String {
        let token = providerId.uppercased().map { char -> Character in
            char.isASCII && (("A"..."Z").contains(String(char)) || char.isNumber) ? char : "_"
        }
        return "KSS_PROVIDER_\(String(token))_API_KEY"
    }

    static func providerCredentialIds() -> [String] {
        (UserDefaults.standard.stringArray(forKey: providerIndexDefaultsKey) ?? [])
            .filter {
                readProviderAPIKey($0) != nil
            }
    }

    static func piAICredentialSnapshot() -> [String: Any] {
        var scoped: [String: String] = [:]
        for providerId in providerCredentialIds() {
            if let key = readProviderAPIKey(providerId) {
                scoped[providerId] = key
            }
        }
        var legacy: [String: String] = [:]
        for key in managedKeys where key.contains("LLM") || key.contains("OPENAI") || key.contains("DEEPSEEK") || key.contains("OPENROUTER") {
            if let value = read(key) {
                legacy[key] = value
            }
        }
        return makePiAICredentialSnapshot(scoped: scoped, legacy: legacy)
    }

    /// Pure compatibility resolver used by the broker and regression tests.
    /// Existing KSS LLM keys remain authoritative; aliases only fill a missing
    /// new provider-scoped route and never overwrite an explicit credential.
    static func makePiAICredentialSnapshot(
        scoped: [String: String],
        legacy: [String: String]
    ) -> [String: Any] {
        var credentials: [String: Any] = [:]
        let builtinProviderIds: Set<String> = [
            "kss-primary", "kss-fallback", "deepseek", "openai", "openrouter",
        ]
        for (providerId, key) in scoped {
            if builtinProviderIds.contains(providerId) {
                credentials[providerId] = ["type": "api_key", "key": key]
            } else {
                // 自定义 provider：env 携带 apiKeyEnv 约定（KSS_PROVIDER_<ID>_API_KEY），
                // 供 dsh 内核按 settings.yaml 的引用解析；密钥仍只经内存注入。
                credentials[providerId] = [
                    "type": "api_key",
                    "key": key,
                    "env": [customProviderEnvName(providerId): key],
                ]
            }
        }
        func put(_ providerId: String, _ key: String?) {
            guard credentials[providerId] == nil,
                  let key,
                  !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else { return }
            credentials[providerId] = ["type": "api_key", "key": key]
        }

        put("kss-primary", legacy["KSS_LLM_PRIMARY_KEY"])
        put("kss-fallback", legacy["KSS_LLM_FALLBACK_KEY"])
        if let key = legacy["DEEPSEEK_API_KEY"] {
            credentials["deepseek"] = ["type": "api_key", "key": key]
        }
        if let key = legacy["OPENAI_API_KEY"] {
            credentials["openai"] = ["type": "api_key", "key": key]
        }
        if let key = legacy["OPENROUTER_API_KEY"] {
            credentials["openrouter"] = ["type": "api_key", "key": key]
        }

        let primaryBase = legacy["KSS_LLM_PRIMARY_BASE_URL"]?.lowercased() ?? ""
        let fallbackBase = legacy["KSS_LLM_FALLBACK_BASE_URL"]?.lowercased() ?? ""
        let primaryLegacy = primaryBase.contains("deepseek")
            ? legacy["DEEPSEEK_API_KEY"]
            : legacy["OPENAI_API_KEY"] ?? legacy["DEEPSEEK_API_KEY"]
        let fallbackLegacy = fallbackBase.contains("deepseek")
            ? legacy["DEEPSEEK_API_KEY"]
            : legacy["OPENAI_API_KEY"] ?? legacy["DEEPSEEK_API_KEY"]
        put("kss-primary", primaryLegacy)
        put("kss-fallback", fallbackLegacy)
        return credentials
    }
}
