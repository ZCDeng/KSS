import XCTest
@testable import KSSDesktop

/// Slash 直连命令解析:位置参数按 order 填,k=v 显式覆盖。
final class SlashCommandTests: XCTestCase {
    func testParsePositionalArgsFollowOrder() {
        let invocation = SlashInvocation.parse(
            "/get_stock 688008.SH",
            order: ["symbol"]
        )
        XCTAssertEqual(invocation?.name, "get_stock")
        XCTAssertEqual(invocation?.args, ["symbol": "688008.SH"])
    }

    func testParseKeyValueOverridesAndMix() {
        let invocation = SlashInvocation.parse(
            "/run_equity_coverage 600519.SH mode=earnings",
            order: ["query", "mode", "format", "assumptions"]
        )
        XCTAssertEqual(invocation?.name, "run_equity_coverage")
        XCTAssertEqual(invocation?.args, ["query": "600519.SH", "mode": "earnings"])
    }

    func testParseNoArgsTool() {
        let invocation = SlashInvocation.parse("/get_orientation", order: [])
        XCTAssertEqual(invocation?.name, "get_orientation")
        XCTAssertEqual(invocation?.args, [:])
    }

    func testParseRejectsNonSlashAndBareSlash() {
        XCTAssertNil(SlashInvocation.parse("今天大盘怎么样", order: []))
        XCTAssertNil(SlashInvocation.parse("/", order: []))
        XCTAssertNil(SlashInvocation.parse("   ", order: []))
    }

    func testParseIgnoresExtraPositionalBeyondOrder() {
        let invocation = SlashInvocation.parse(
            "/get_snapshot extra tokens",
            order: []
        )
        XCTAssertEqual(invocation?.name, "get_snapshot")
        XCTAssertEqual(invocation?.args, [:])
    }
}
