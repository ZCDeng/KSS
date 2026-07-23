import XCTest
@testable import KSSDesktop

final class IntelXcomChromeTests: XCTestCase {
    func testXcomFlagsOnForXcomOnly() {
        XCTAssertTrue(IntelXcomChrome.isXcom(.xcom))
        XCTAssertTrue(IntelXcomChrome.usesTimelineList(.xcom))
        XCTAssertTrue(IntelXcomChrome.usesUnderlineTabs(.xcom))
        XCTAssertTrue(IntelXcomChrome.usesSlimHeader(.xcom))
        XCTAssertTrue(IntelXcomChrome.demotesPanoramaToEmptyDetail(.xcom))
        XCTAssertTrue(IntelXcomChrome.usesCollapsedDigestChrome(.xcom))
        XCTAssertFalse(IntelXcomChrome.usesEntryCardChrome(.xcom))
        XCTAssertFalse(IntelXcomChrome.showTrackColorDot(.xcom))
        XCTAssertEqual(IntelXcomChrome.selectionChrome(.xcom), .timelineFill)
        XCTAssertEqual(IntelXcomChrome.listRowSpacing(.xcom), 0)
        XCTAssertEqual(IntelXcomChrome.listContentPadding(.xcom), 0)
        XCTAssertEqual(IntelXcomChrome.detailTitlePointSize(.xcom), 18)
    }

    func testClassicFlagsOffForClayAndMaterial() {
        for system in [KSSDesignSystem.clayM3, .material3, .discord] {
            XCTAssertFalse(IntelXcomChrome.isXcom(system), system.rawValue)
            XCTAssertFalse(IntelXcomChrome.usesTimelineList(system), system.rawValue)
            XCTAssertFalse(IntelXcomChrome.usesUnderlineTabs(system), system.rawValue)
            XCTAssertFalse(IntelXcomChrome.usesSlimHeader(system), system.rawValue)
            XCTAssertFalse(IntelXcomChrome.demotesPanoramaToEmptyDetail(system), system.rawValue)
            XCTAssertTrue(IntelXcomChrome.usesEntryCardChrome(system), system.rawValue)
            XCTAssertTrue(IntelXcomChrome.showTrackColorDot(system), system.rawValue)
            XCTAssertEqual(IntelXcomChrome.selectionChrome(system), .entryCard, system.rawValue)
            XCTAssertEqual(IntelXcomChrome.listRowSpacing(system), 8, system.rawValue)
            XCTAssertEqual(IntelXcomChrome.listContentPadding(system), 8, system.rawValue)
            XCTAssertEqual(IntelXcomChrome.detailTitlePointSize(system), 22, system.rawValue)
        }
    }

    func testAllClassicCasesKeepEntryCard() {
        for system in KSSDesignSystem.classicCases {
            XCTAssertEqual(
                IntelXcomChrome.selectionChrome(system),
                .entryCard,
                "classic \(system.rawValue) must keep entry-card chrome"
            )
            XCTAssertFalse(
                IntelXcomChrome.demotesPanoramaToEmptyDetail(system),
                "classic \(system.rawValue) keeps panorama above tracks"
            )
        }
    }

    func testHoverOpacityOnlyWhenXcom() {
        XCTAssertEqual(IntelXcomChrome.hoverOverlayOpacity(appearance: .light, isXcom: false), 0)
        XCTAssertGreaterThan(IntelXcomChrome.hoverOverlayOpacity(appearance: .light, isXcom: true), 0)
        XCTAssertGreaterThan(
            IntelXcomChrome.hoverOverlayOpacity(appearance: .dark, isXcom: true),
            IntelXcomChrome.hoverOverlayOpacity(appearance: .light, isXcom: true)
        )
    }
}
