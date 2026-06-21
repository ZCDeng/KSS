import XCTest
@testable import KSSDesktop

/// U5: 加自选 toggle 的纯决策 —— 加入触发即时复盘、取消不触发。
final class WatchlistTriggerTests: XCTestCase {

    func testAddingTriggersGeneration() {
        let r = WatchlistToggle.apply(["688017.SH"], toggling: "688322.SH")
        XCTAssertTrue(r.generate, "加入应触发即时复盘")
        XCTAssertEqual(r.list, ["688017.SH", "688322.SH"])
    }

    func testRemovingDoesNotTrigger() {
        let r = WatchlistToggle.apply(["688017.SH", "688322.SH"], toggling: "688322.SH")
        XCTAssertFalse(r.generate, "取消自选不应触发生成")
        XCTAssertEqual(r.list, ["688017.SH"])
    }

    func testAddingToEmptyList() {
        let r = WatchlistToggle.apply([], toggling: "300750.SZ")
        XCTAssertTrue(r.generate)
        XCTAssertEqual(r.list, ["300750.SZ"])
    }

    func testRemovingLastLeavesEmpty() {
        let r = WatchlistToggle.apply(["688322.SH"], toggling: "688322.SH")
        XCTAssertFalse(r.generate)
        XCTAssertTrue(r.list.isEmpty)
    }

    /// 加入只生成被加的那一只（不是整张表）。
    func testGeneratesOnlyTheAddedSymbol() {
        let current = ["688017.SH", "688322.SH"]
        let r = WatchlistToggle.apply(current, toggling: "688114.SH")
        XCTAssertTrue(r.generate)
        XCTAssertEqual(r.list.last, "688114.SH")
    }
}
