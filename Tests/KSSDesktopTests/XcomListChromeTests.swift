import XCTest
import SwiftUI
@testable import KSSDesktop

final class XcomListChromeTests: XCTestCase {
    func testIsXcomOnlyForXcomSystem() {
        XCTAssertTrue(XcomListChrome.isXcom(.xcom))
        XCTAssertFalse(XcomListChrome.isXcom(.clayM3))
        XCTAssertFalse(XcomListChrome.isXcom(.material3))
    }

    func testDetailTitleSmallerOnXcom() {
        XCTAssertEqual(XcomListChrome.detailTitlePointSize(.xcom), 18)
        XCTAssertEqual(XcomListChrome.detailTitlePointSize(.clayM3), 22)
    }

    func testListColumnWiderOnXcom() {
        XCTAssertEqual(XcomListChrome.listColumnWidth(.xcom), 320)
        XCTAssertEqual(XcomListChrome.listColumnWidth(.clayM3), 300)
    }

    func testListSelectionFillClassicOnDiffersFromOff() {
        let theme = ThemeCatalog.palette(for: .clayM3, appearance: .light).tokens
        let on = XcomListChrome.listSelectionFill(isOn: true, isHovered: false, theme: theme)
        let off = XcomListChrome.listSelectionFill(isOn: false, isHovered: false, theme: theme)
        // classic: on = accent@0.16, off = clear
        XCTAssertNotEqual(String(describing: on), String(describing: off))
    }

    func testListSelectionFillXcomHoverDiffersFromOff() {
        let theme = ThemeCatalog.palette(for: .xcom, appearance: .light).tokens
        let hovered = XcomListChrome.listSelectionFill(isOn: false, isHovered: true, theme: theme)
        let off = XcomListChrome.listSelectionFill(isOn: false, isHovered: false, theme: theme)
        XCTAssertNotEqual(String(describing: hovered), String(describing: off))
    }

    func testSeesawTimelineGeometryMatchesXcomReference() {
        XCTAssertEqual(SeesawXcomChrome.feedColumnWidth, 600)
        XCTAssertEqual(SeesawXcomChrome.sessionRailWidth, 320)
        XCTAssertEqual(SeesawXcomChrome.headerHeight, 53)
        XCTAssertEqual(SeesawXcomChrome.avatarSize, 40)
        XCTAssertEqual(SeesawXcomChrome.minimumThreeColumnWidth, 1320)
    }
}
