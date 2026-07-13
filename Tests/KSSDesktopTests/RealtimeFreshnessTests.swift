import XCTest
@testable import KSSDesktop

final class RealtimeFreshnessTests: XCTestCase {

    private let now = Date(timeIntervalSince1970: 1_752_000_000)  // 固定基准，测试可重复

    private func iso(_ secondsBeforeNow: TimeInterval, fraction: Bool = false) -> String {
        let d = now.addingTimeInterval(-secondsBeforeNow)
        let f = ISO8601DateFormatter()
        f.formatOptions = fraction ? [.withInternetDateTime, .withFractionalSeconds] : [.withInternetDateTime]
        return f.string(from: d)
    }

    func testFreshWithinTwoMinutes() {
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: iso(120), fallbackReceivedAt: nil, now: now),
            .fresh
        )
    }

    func testFreshAtExactlyThreshold() {
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: iso(300), fallbackReceivedAt: nil, now: now),
            .fresh
        )
    }

    func testStaleBeyondThreshold() {
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: iso(600), fallbackReceivedAt: nil, now: now),
            .stale
        )
    }

    func testFractionalSecondsParse() {
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: iso(120, fraction: true), fallbackReceivedAt: nil, now: now),
            .fresh
        )
    }

    func testFallbackUsedWhenSourceAsofTsNil() {
        XCTAssertEqual(
            RealtimeFreshness.status(
                sourceAsofTs: nil,
                fallbackReceivedAt: now.addingTimeInterval(-120),
                now: now
            ),
            .fresh
        )
    }

    func testFallbackUsedWhenSourceAsofTsUnparseable() {
        XCTAssertEqual(
            RealtimeFreshness.status(
                sourceAsofTs: "not-a-date",
                fallbackReceivedAt: now.addingTimeInterval(-120),
                now: now
            ),
            .fresh
        )
    }

    func testMissingWhenBothNil() {
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: nil, fallbackReceivedAt: nil, now: now),
            .missing
        )
    }

    func testFallbackIsIsolatedPerSymbol() {
        // 标的 A 自己的接收时间距今 2 分钟 → fresh，即使调用方误传了另一个更早的时间也不该混用；
        // 这里验证的是函数本身按传入值计算，不做跨标的隐式共享。
        let symbolAReceivedAt = now.addingTimeInterval(-120)
        let symbolBReceivedAt = now.addingTimeInterval(-600)
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: nil, fallbackReceivedAt: symbolAReceivedAt, now: now),
            .fresh
        )
        XCTAssertEqual(
            RealtimeFreshness.status(sourceAsofTs: nil, fallbackReceivedAt: symbolBReceivedAt, now: now),
            .stale
        )
    }
}
