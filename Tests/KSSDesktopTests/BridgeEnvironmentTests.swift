import XCTest
@testable import KSSDesktop

final class BridgeEnvironmentTests: XCTestCase {
    func testSanitizedChildEnvironmentDropsAmbientSecrets() {
        let input = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "LANG": "en_US.UTF-8",
            "OPENAI_API_KEY": "secret",
            "LONGBRIDGE_ACCESS_TOKEN": "secret",
            "SERVICE_CLIENT_SECRET": "secret",
            "DB_PASSWORD": "secret",
            "KSS_PI_AI_CREDENTIAL_NONCE": "secret",
        ]

        let result = BridgeClient.sanitizedChildEnvironment(input)

        XCTAssertEqual(result["PATH"], "/usr/bin:/bin")
        XCTAssertEqual(result["HOME"], "/Users/test")
        XCTAssertEqual(result["LANG"], "en_US.UTF-8")
        XCTAssertNil(result["OPENAI_API_KEY"])
        XCTAssertNil(result["LONGBRIDGE_ACCESS_TOKEN"])
        XCTAssertNil(result["SERVICE_CLIENT_SECRET"])
        XCTAssertNil(result["DB_PASSWORD"])
        XCTAssertNil(result["KSS_PI_AI_CREDENTIAL_NONCE"])
    }
}
