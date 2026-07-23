import XCTest
@testable import KSSDesktop

final class SettingsFormStyleTests: XCTestCase {
    func testTasksStandardOnlyOnXcom() {
        XCTAssertTrue(SettingsFormStyle.usesTasksStandard(.xcom))
        XCTAssertFalse(SettingsFormStyle.usesTasksStandard(.clayM3))
    }

    /// 与 ScheduledJobRow / health 汇总对齐的硬数字，防止样式漂移。
    func testTokenMatchesTaskRowBaseline() {
        XCTAssertEqual(SettingsFormStyle.itemTitle, 14.5)
        XCTAssertEqual(SettingsFormStyle.sectionHeader, 12.5)
        XCTAssertEqual(SettingsFormStyle.bodyHint, 12.5)
        XCTAssertEqual(SettingsFormStyle.meta, 11.5)
        XCTAssertEqual(SettingsFormStyle.actionLabel, 12)
        XCTAssertEqual(SettingsFormStyle.primaryAction, 13)
        XCTAssertEqual(SettingsFormStyle.blockSpacing, 12)
        XCTAssertEqual(SettingsFormStyle.cardPadding, 12)
        XCTAssertEqual(SettingsFormStyle.pageTitle, SettingsFormStyle.itemTitle)
    }
}
