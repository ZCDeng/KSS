import XCTest
@testable import KSSDesktop

final class IntradaySourceLabelTests: XCTestCase {

    func testDecodeSourceAndSessionDate() throws {
        let json = """
        {"symbol":"688017.SH","interval_minutes":1,"bars":[{"time":"t","open":1,"high":1,"low":1,"close":1,"volume":0}],"source":"local","session_date":"2026-07-10"}
        """.data(using: .utf8)!
        let bars = try JSONDecoder().decode(IntradayBars.self, from: json)
        XCTAssertEqual(bars.source, "local")
        XCTAssertEqual(bars.sessionDate, "2026-07-10")
        XCTAssertEqual(bars.sourceLabel, "本地 · 2026-07-10")
        XCTAssertTrue(bars.isRenderable)
    }

    func testDecodeLegacyWithoutSourceStillWorks() throws {
        let json = """
        {"bars":[{"time":"t","open":1,"high":1,"low":1,"close":1,"volume":0}]}
        """.data(using: .utf8)!
        let bars = try JSONDecoder().decode(IntradayBars.self, from: json)
        XCTAssertNil(bars.source)
        XCTAssertNil(bars.sourceLabel)
        XCTAssertTrue(bars.isRenderable)
    }

    func testLivePartialLabel() throws {
        let json = """
        {"bars":[{"time":"t","open":1,"high":1,"low":1,"close":1,"volume":0}],"source":"live_partial"}
        """.data(using: .utf8)!
        let bars = try JSONDecoder().decode(IntradayBars.self, from: json)
        XCTAssertEqual(bars.sourceLabel, "源 · 部分")
    }

    func testEmptyBarsNotRenderable() throws {
        let json = """
        {"bars":[],"error":"empty","hint":"无分钟存档"}
        """.data(using: .utf8)!
        let bars = try JSONDecoder().decode(IntradayBars.self, from: json)
        XCTAssertFalse(bars.isRenderable)
    }
}
