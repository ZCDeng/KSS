import XCTest
@testable import KSSDesktop

final class WorkspaceSectionTests: XCTestCase {
    func testSettingsIsHidden() {
        XCTAssertTrue(WorkspaceSection.hidden.contains(.settings))
    }

    func testOrderedNeverIncludesSettings() {
        XCTAssertFalse(WorkspaceSection.ordered(from: "").contains(.settings))
        XCTAssertFalse(WorkspaceSection.ordered(from: "Settings,Dashboard").contains(.settings))
    }

    func testRoundTripThroughOrderedNeverReintroducesSettings() {
        let ordered = WorkspaceSection.ordered(from: "")
        let encoded = WorkspaceSection.encode(ordered)
        XCTAssertFalse(encoded.contains("Settings"))
    }
}
