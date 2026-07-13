import XCTest
@testable import KSSDesktop

final class SparklineRefreshTests: XCTestCase {
    // Covers AE3.
    func testTradingSessionQuoteOnSparklinePiggybacks() {
        let r = RealtimeTimerDecision.evaluate(
            scenePhaseActive: true, isTradingSession: true, isTradeDay: true, authFailed: false)
        XCTAssertTrue(r.quoteTimerOn)
        XCTAssertFalse(r.sparklineTimerOn)   // 随 quote tick 顺带，不需要独立 timer
    }

    // Covers AE3.
    func testPostCloseTradeDayQuoteOffSparklineOn() {
        let r = RealtimeTimerDecision.evaluate(
            scenePhaseActive: true, isTradingSession: false, isTradeDay: true, authFailed: false)
        XCTAssertFalse(r.quoteTimerOn)
        XCTAssertTrue(r.sparklineTimerOn)
    }

    func testNonTradeDayBothOff() {
        let r = RealtimeTimerDecision.evaluate(
            scenePhaseActive: true, isTradingSession: false, isTradeDay: false, authFailed: false)
        XCTAssertFalse(r.quoteTimerOn)
        XCTAssertFalse(r.sparklineTimerOn)
    }

    func testPostCloseAuthFailedSparklineStillOn() {
        let r = RealtimeTimerDecision.evaluate(
            scenePhaseActive: true, isTradingSession: false, isTradeDay: true, authFailed: true)
        XCTAssertFalse(r.quoteTimerOn)
        XCTAssertTrue(r.sparklineTimerOn)   // 盘后 sparkline 走 local 降级，不依赖 Longbridge 鉴权
    }

    func testIntradayAuthFailedFreezesBoth() {
        // 盘中 authFailed 时 sparkline 随 quote 链路冻结（现状，本轮不改）：own timer 本就
        // 不在盘中跑，quote 又因 authFailed 停摆 → 净效果两条链路都停。
        let r = RealtimeTimerDecision.evaluate(
            scenePhaseActive: true, isTradingSession: true, isTradeDay: true, authFailed: true)
        XCTAssertFalse(r.quoteTimerOn)
        XCTAssertFalse(r.sparklineTimerOn)
    }

    func testScenePhaseInactiveBothOff() {
        let r = RealtimeTimerDecision.evaluate(
            scenePhaseActive: false, isTradingSession: true, isTradeDay: true, authFailed: false)
        XCTAssertFalse(r.quoteTimerOn)
        XCTAssertFalse(r.sparklineTimerOn)
    }
}
