import XCTest
@testable import KSSDesktop

/// 启动 gate reducer 的纯单测：完整正常转移、ready 不提前放行、entryAvailable 后才能
/// Web 进入、reduced-motion、fallback、重复 enter、watchdog、navigation/error、未知 payload、终态幂等。
final class LaunchStateTests: XCTestCase {

    // MARK: 正常路径

    func testHappyPathFullTransition() {
        var r = LaunchReducer()
        XCTAssertEqual(r.state, .booting)
        XCTAssertTrue(r.send(.webReady));          XCTAssertEqual(r.state, .animating)
        XCTAssertTrue(r.send(.webEntryAvailable)); XCTAssertEqual(r.state, .awaitingEntry)
        XCTAssertTrue(r.showsEntryButton)
        XCTAssertTrue(r.send(.userEntry));         XCTAssertEqual(r.state, .entering)
        XCTAssertTrue(r.isGateVisible, "entering 期间启动层仍可见（淡出中）")
        XCTAssertTrue(r.send(.enterComplete));     XCTAssertEqual(r.state, .entered)
        XCTAssertFalse(r.isGateVisible)
    }

    func testReadyDoesNotRelease() {
        var r = LaunchReducer()
        r.send(.webReady)
        // 仅有 ready/animating：不显示按钮、不可进入。
        XCTAssertFalse(r.showsEntryButton)
        XCTAssertFalse(r.send(.userEntry), "animating 态 userEntry 必须被忽略")
        XCTAssertEqual(r.state, .animating)
    }

    func testCannotEnterBeforeEntryAvailable() {
        var r = LaunchReducer()
        r.send(.webReady)
        XCTAssertFalse(r.send(.userEntry))
        XCTAssertEqual(r.state, .animating)
        r.send(.webEntryAvailable)
        XCTAssertTrue(r.send(.userEntry))
        XCTAssertEqual(r.state, .entering)
    }

    // MARK: reduced-motion

    func testReducedMotionSkipsAnimatingButStillGates() {
        var r = LaunchReducer()
        XCTAssertTrue(r.send(.reducedMotion))
        XCTAssertEqual(r.state, .awaitingEntry, "reduced-motion 直接到等待态")
        XCTAssertTrue(r.showsEntryButton)
        XCTAssertTrue(r.send(.userEntry))
        XCTAssertEqual(r.state, .entering)
    }

    // MARK: fallback

    func testFailureFromEachNonTerminalLeadsToFallback() {
        for start: LaunchEvent? in [nil, .webReady, .reducedMotion] {
            var r = LaunchReducer()
            if let start { r.send(start) }
            XCTAssertTrue(r.send(.failure(.watchdog)))
            XCTAssertEqual(r.state, .fallback)
            XCTAssertTrue(r.showsEntryButton)
            XCTAssertTrue(r.isFallback)
        }
    }

    func testFallbackButtonEnters() {
        var r = LaunchReducer()
        r.send(.failure(.navigation))
        XCTAssertEqual(r.state, .fallback)
        XCTAssertTrue(r.send(.userEntry))
        XCTAssertEqual(r.state, .entering)
    }

    func testFailureAfterEnteringIsIgnored() {
        var r = LaunchReducer()
        r.send(.webReady); r.send(.webEntryAvailable); r.send(.userEntry)
        XCTAssertEqual(r.state, .entering)
        XCTAssertFalse(r.send(.failure(.script)), "entering 后 failure 不能回退到 fallback")
        XCTAssertEqual(r.state, .entering)
    }

    // MARK: 幂等 / 终态

    func testRepeatedUserEntryIdempotent() {
        var r = LaunchReducer()
        r.send(.webReady); r.send(.webEntryAvailable)
        XCTAssertTrue(r.send(.userEntry))
        XCTAssertFalse(r.send(.userEntry), "重复 userEntry 幂等忽略")
        XCTAssertEqual(r.state, .entering)
    }

    func testEnteredIsTerminal() {
        var r = LaunchReducer()
        r.send(.webReady); r.send(.webEntryAvailable); r.send(.userEntry); r.send(.enterComplete)
        XCTAssertEqual(r.state, .entered)
        for e: LaunchEvent in [.webReady, .reducedMotion, .webEntryAvailable, .userEntry,
                               .failure(.script), .enterComplete] {
            XCTAssertFalse(r.send(e), "entered 是终态，\(e) 应被忽略")
            XCTAssertEqual(r.state, .entered)
        }
    }

    func testDuplicateEntryAvailableIgnored() {
        var r = LaunchReducer()
        r.send(.webReady); r.send(.webEntryAvailable)
        XCTAssertFalse(r.send(.webEntryAvailable), "循环不再二次 entryAvailable")
        XCTAssertEqual(r.state, .awaitingEntry)
    }

    // MARK: 消息路由（未知 payload 忽略）

    func testMessageRouterMapsWhitelistedTypes() {
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "ready"]), .webReady)
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "ready", "reducedMotion": true]), .reducedMotion)
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "entryAvailable"]), .webEntryAvailable)
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "enterRequested"]), .userEntry)
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "error", "category": "resource"]), .failure(.resource))
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "error"]), .failure(.script), "缺类别默认 script")
    }

    func testMessageRouterRejectsUnknownAndMalformed() {
        XCTAssertNil(LaunchMessageRouter.event(from: ["type": "enterNow"]), "非白名单 type")
        XCTAssertNil(LaunchMessageRouter.event(from: ["foo": "bar"]), "缺 type")
        XCTAssertNil(LaunchMessageRouter.event(from: "enterRequested"), "裸字符串非法")
        XCTAssertNil(LaunchMessageRouter.event(from: 42))
        // 非法 category 回落到 script（不暴露任意类别）。
        XCTAssertEqual(LaunchMessageRouter.event(from: ["type": "error", "category": "nonsense"]), .failure(.script))
    }
}
