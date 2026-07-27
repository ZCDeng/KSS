import XCTest
@testable import KSSDesktop

final class ProviderCredentialMigrationTests: XCTestCase {
    func testLegacyDeepSeekConfigurationFillsKSSPrimaryAlias() throws {
        let snapshot = KeychainStore.makePiAICredentialSnapshot(
            scoped: [:],
            legacy: [
                "DEEPSEEK_API_KEY": "legacy-deepseek",
                "KSS_LLM_PRIMARY_BASE_URL": "https://api.deepseek.com",
            ]
        )

        let primary = try XCTUnwrap(snapshot["kss-primary"] as? [String: String])
        XCTAssertEqual(primary["key"], "legacy-deepseek")
        XCTAssertNotNil(snapshot["deepseek"])
    }

    func testProviderScopedCredentialWinsOverLegacyAlias() throws {
        let snapshot = KeychainStore.makePiAICredentialSnapshot(
            scoped: ["kss-primary": "scoped-key"],
            legacy: [
                "KSS_LLM_PRIMARY_KEY": "old-primary",
                "DEEPSEEK_API_KEY": "legacy-deepseek",
            ]
        )

        let primary = try XCTUnwrap(snapshot["kss-primary"] as? [String: String])
        XCTAssertEqual(primary["key"], "scoped-key")
    }
}
