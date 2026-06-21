import XCTest
@testable import KSSDesktop

/// WebView 同步 reducer 的无 WebKit 单测：navigation identity / generation /
/// synchronization+content revision 失效，stale didFinish，theme→content 串行。
final class WebSyncStateTests: XCTestCase {

    func testInitialNotLoaded() {
        let s = WebSyncState()
        XCTAssertFalse(s.isLoaded)
        XCTAssertTrue(s.needsContent, "lastApplied(-1) != contentRevision(0)")
    }

    func testDidFinishMarksLoaded() {
        var s = WebSyncState()
        s.didStartNavigation(1)
        XCTAssertTrue(s.didFinish(1))
        XCTAssertTrue(s.isLoaded)
    }

    func testStaleDidFinishIgnoredAfterNewNavigation() {
        var s = WebSyncState()
        s.didStartNavigation(1)      // nav A
        s.didStartNavigation(2)      // nav B（reload/导航）
        XCTAssertFalse(s.didFinish(1), "旧 navigation A 的 didFinish 必须被丢弃")
        XCTAssertFalse(s.isLoaded)
        XCTAssertTrue(s.didFinish(2), "当前 navigation B 的 didFinish 接受")
        XCTAssertTrue(s.isLoaded)
    }

    func testGenerationIncrementsPerNavigation() {
        var s = WebSyncState()
        s.didStartNavigation(1)
        let g1 = s.generation
        s.didStartNavigation(2)
        XCTAssertEqual(s.generation, g1 + 1)
    }

    func testSyncTokenInvalidatedByNewSync() {
        var s = WebSyncState()
        s.didStartNavigation(1); _ = s.didFinish(1)
        let t1 = s.beginSync()
        XCTAssertTrue(s.isCurrent(t1))
        let t2 = s.beginSync()              // 新一轮 synchronize
        XCTAssertFalse(s.isCurrent(t1), "旧 sync 的 completion 必须失效")
        XCTAssertTrue(s.isCurrent(t2))
    }

    func testSyncTokenInvalidatedByNewNavigation() {
        var s = WebSyncState()
        s.didStartNavigation(1); _ = s.didFinish(1)
        let t1 = s.beginSync()
        s.didStartNavigation(2)             // 新页面开始
        XCTAssertFalse(s.isCurrent(t1), "新导航开始后旧 completion 失效")
    }

    func testContentRevisionGatesContentPush() {
        var s = WebSyncState()
        s.didStartNavigation(1); _ = s.didFinish(1)
        s.bumpContent()                     // 有新数据
        XCTAssertTrue(s.needsContent)
        let t = s.beginSync()
        s.markContentApplied(t.contentRevision)
        XCTAssertFalse(s.needsContent, "应用后内容已最新")
    }

    func testThemeOnlyPushWhenContentUnchanged() {
        var s = WebSyncState()
        s.didStartNavigation(1); _ = s.didFinish(1)
        s.bumpContent()
        let t = s.beginSync()
        s.markContentApplied(t.contentRevision)
        // 主题再变（无新数据）：needsContent=false → 只推主题。
        XCTAssertFalse(s.needsContent)
        let t2 = s.beginSync()
        XCTAssertTrue(s.isCurrent(t2))
        XCTAssertEqual(t2.contentRevision, t.contentRevision)
    }

    func testContentBumpAfterApplyReopensContentPush() {
        var s = WebSyncState()
        s.didStartNavigation(1); _ = s.didFinish(1)
        let t = s.beginSync()
        s.markContentApplied(t.contentRevision)
        XCTAssertFalse(s.needsContent)
        s.bumpContent()                     // 又来新数据
        XCTAssertTrue(s.needsContent)
    }
}
