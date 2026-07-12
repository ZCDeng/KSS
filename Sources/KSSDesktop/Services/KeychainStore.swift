import Foundation
import Security

/// U3：网络凭据存 macOS Keychain（不再明文落 .env）。
/// 受管键与 bridge `_load_project_env` 的 allowed 对齐；BridgeClient 启动时读出注入子进程 env。
enum KeychainStore {
    static let service = "com.zcdeng.KSSDesktop.credentials"

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

    /// 注入子进程的 env 片段（仅含已配置的键）。
    static func injectedEnvironment() -> [String: String] {
        var out: [String: String] = [:]
        for key in managedKeys {
            if let value = read(key) { out[key] = value }
        }
        return out
    }
}
