import XCTest
@testable import KSSDesktop

/// 自选页档案区分段：哪几段出 Tab、换股后选中项怎么落位、哪几段该挂待办点。
/// 这三件事决定用户会不会被丢在一个空 Tab 上，所以全走纯函数并锁在测试里。
final class StockDossierSectionTests: XCTestCase {

    // MARK: - 可见分段

    func testFullyLoadedStockShowsAllFourInCanonicalOrder() {
        XCTAssertEqual(
            StockDossierSection.available(hasReview: true, hasEnrichment: true),
            [.review, .perilla, .exposure, .questions]
        )
    }

    func testMissingContentDropsItsTab() {
        XCTAssertEqual(
            StockDossierSection.available(hasReview: false, hasEnrichment: true),
            [.perilla, .exposure, .questions]
        )
        XCTAssertEqual(
            StockDossierSection.available(hasReview: true, hasEnrichment: false),
            [.review, .exposure, .questions]
        )
    }

    func testEntrySurfacesSurviveEvenWithNoData() {
        // 地图与 8 问是录入面：没标注、没答题才更需要点进去，不能因为「空」就不给入口。
        let sections = StockDossierSection.available(hasReview: false, hasEnrichment: false)
        XCTAssertEqual(sections, [.exposure, .questions])
    }

    // MARK: - 选中项落位

    func testSelectionIsStickyAcrossStocksWhenStillAvailable() {
        let available = StockDossierSection.available(hasReview: true, hasEnrichment: false)
        XCTAssertEqual(
            StockDossierSection.resolve(selected: .questions, available: available), .questions
        )
    }

    func testSelectionFallsBackToFirstWhenTabDisappears() {
        // 上只票停在「紫苏叶富化」，切到一只没富化的票 → 落到第一个可见段，而不是留在空段。
        let available = StockDossierSection.available(hasReview: true, hasEnrichment: false)
        XCTAssertEqual(
            StockDossierSection.resolve(selected: .perilla, available: available), .review
        )
    }

    func testFallbackWithoutReviewLandsOnExposure() {
        let available = StockDossierSection.available(hasReview: false, hasEnrichment: false)
        XCTAssertEqual(
            StockDossierSection.resolve(selected: .review, available: available), .exposure
        )
    }

    func testEmptyAvailableDefaultsToExposure() {
        XCTAssertEqual(StockDossierSection.resolve(selected: .review, available: []), .exposure)
    }

    // MARK: - 待办点

    func testUnlabelledAndUnansweredBothFlag() {
        XCTAssertEqual(
            StockDossierSection.pending(hasPrimaryNode: false, exposureLoaded: true,
                                        decided: 0, total: 8),
            [.exposure, .questions]
        )
    }

    func testAnsweredAndLabelledFlagsNothing() {
        XCTAssertTrue(
            StockDossierSection.pending(hasPrimaryNode: true, exposureLoaded: true,
                                        decided: 8, total: 8).isEmpty
        )
    }

    func testPartialAnswersStillFlagQuestionsOnly() {
        XCTAssertEqual(
            StockDossierSection.pending(hasPrimaryNode: true, exposureLoaded: true,
                                        decided: 7, total: 8),
            [.questions]
        )
    }

    func testUnloadedDictionaryFlagsNothing() {
        // 标注字典没加载完时 nil 只代表「还不知道」，此刻标黄点是假警报。
        XCTAssertTrue(
            StockDossierSection.pending(hasPrimaryNode: false, exposureLoaded: false,
                                        decided: 0, total: 8).isEmpty
        )
    }

    // MARK: - 标签

    func testLabelsAreStable() {
        XCTAssertEqual(StockDossierSection.review.label, "复盘结论")
        XCTAssertEqual(StockDossierSection.perilla.label, "紫苏叶富化")
        XCTAssertEqual(StockDossierSection.exposure.label, "可投资地图")
        XCTAssertEqual(StockDossierSection.questions.label, "8 问尽调")
    }
}
