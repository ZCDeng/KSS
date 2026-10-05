import XCTest
@testable import KSSDesktop

/// 比率类指标不带正号；涨跌类保持带符号。
final class KSSFormatTests: XCTestCase {
    func testPercentStaysSignedForReturns() {
        XCTAssertEqual(KSSFormat.percent(0.0084), "+0.84%")
        XCTAssertEqual(KSSFormat.percent(-0.1170), "-11.70%")
    }

    func testRatioPercentHasNoPlusSign() {
        XCTAssertEqual(KSSFormat.ratioPercent(0.5362), "53.62%")
        XCTAssertEqual(KSSFormat.ratioPercent(0.2084), "20.84%")
        XCTAssertEqual(KSSFormat.ratioPercent(nil), "-")
    }

    func testTushareAmountIsThousandYuan() {
        XCTAssertEqual(KSSFormat.amountFromThousandYuan(82_000), "8200万")
        XCTAssertEqual(KSSFormat.compactMoney(52_000), "5.2万")
        XCTAssertEqual(KSSFormat.amountFromThousandYuan(194_402.527), "1.94亿")
        XCTAssertEqual(KSSFormat.amountFromThousandYuan(nil), "-")
    }

    func testReadableBacktestTitleFlattensPythonDict() {
        XCTAssertEqual(
            BacktestReport.readableTitle("ma_cross（{'fast': 5, 'slow': 20, 'kind': 'sma', 'type': 'SMA'}）"),
            "ma_cross · fast 5 / slow 20 / kind sma / type SMA"
        )
        XCTAssertEqual(
            BacktestReport.readableTitle("ma_cross({'fast': 5})"),
            "ma_cross · fast 5"
        )
    }

    func testReadableBacktestTitleKeepsOrdinaryTitles() {
        let title = "Sample Weight A/B —— 概念漂移加权（DDG-DA 轻量版）"
        XCTAssertEqual(BacktestReport.readableTitle(title), title)
        XCTAssertEqual(BacktestReport.readableTitle("KSS Desktop log_mv 反向轻量回测"), "KSS Desktop log_mv 反向轻量回测")
    }

    func testSectorGradeLabelShowsShareFlow() {
        XCTAssertEqual(SectorGradeLabel.flowLabel(grade: "强势确认", divergence: false), "净赎回")
        XCTAssertEqual(SectorGradeLabel.flowLabel(grade: "中性偏多", divergence: false), "小幅赎回")
        XCTAssertEqual(SectorGradeLabel.flowLabel(grade: "偏弱", divergence: false), "净申购")
        XCTAssertEqual(SectorGradeLabel.flowLabel(grade: "强势确认", divergence: true), "见顶预警")
        XCTAssertTrue(SectorGradeLabel.help(grade: "强势确认", divergence: false).contains("强势确认"))
    }
}
