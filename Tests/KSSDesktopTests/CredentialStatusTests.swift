import XCTest
@testable import KSSDesktop

/// U9（plan 2026-07-12-005 / R12）：`KSSStore.isCredentialConfigured` 是缺凭证优雅降级
/// 的单一真源查询点——self-check 结果到达前返回 nil（未知，不误判），ok/warn 映射为 true/false。
@MainActor
final class CredentialStatusTests: XCTestCase {
    func testReturnsNilBeforeSelfCheckRuns() {
        let store = KSSStore()
        XCTAssertNil(store.isCredentialConfigured("tushare"))
    }

    func testReturnsTrueForOKItem() {
        let store = KSSStore()
        store.selfCheckItems = [
            SelfCheckItem(item: "tushare", status: "ok", detail: "已配置", fixHint: nil, fixAction: nil)
        ]
        XCTAssertEqual(store.isCredentialConfigured("tushare"), true)
    }

    func testReturnsFalseForWarnItem() {
        let store = KSSStore()
        store.selfCheckItems = [
            SelfCheckItem(item: "llm", status: "warn", detail: "未配置", fixHint: "去设置", fixAction: "open_settings")
        ]
        XCTAssertEqual(store.isCredentialConfigured("llm"), false)
    }

    func testReturnsNilForUnknownSource() {
        let store = KSSStore()
        store.selfCheckItems = [
            SelfCheckItem(item: "tushare", status: "ok", detail: "已配置", fixHint: nil, fixAction: nil)
        ]
        XCTAssertNil(store.isCredentialConfigured("longbridge"))
    }

    func testSelfCheckHasFailAndShowBanner() {
        let store = KSSStore()
        store.selfCheckItems = [
            SelfCheckItem(item: "venv", status: "fail", detail: "坏了", fixHint: "重装", fixAction: "reinit_runtime"),
            SelfCheckItem(item: "llm", status: "warn", detail: "未配置", fixHint: nil, fixAction: "open_settings"),
        ]
        XCTAssertTrue(store.selfCheckHasFail)
        XCTAssertTrue(store.selfCheckHasWarn)
        XCTAssertTrue(store.showSelfCheckBanner)

        store.dismissSelfCheckBanner()
        XCTAssertFalse(store.showSelfCheckBanner)
    }

    func testAllOKHidesBanner() {
        let store = KSSStore()
        store.selfCheckItems = [
            SelfCheckItem(item: "venv", status: "ok", detail: "正常", fixHint: nil, fixAction: nil)
        ]
        XCTAssertFalse(store.selfCheckHasFail)
        XCTAssertFalse(store.showSelfCheckBanner)
    }
}
