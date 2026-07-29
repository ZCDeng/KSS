import XCTest
@testable import KSSDesktop

final class SeesawMarkdownTests: XCTestCase {
    func testParsesHeadingsMarketTableDividerAndOrderedList() {
        let markdown = """
        ### 核心指数表现
        | 指数 | 涨跌幅 |
        |---|---|
        | 上证指数 | **+1.15%** |
        | 科创50 | -0.20% |

        ---

        1. 结构分化
        2. 关注风险
        """

        XCTAssertEqual(
            SeesawMarkdown.parse(markdown),
            [
                .heading(level: 3, text: "核心指数表现"),
                .table(
                    headers: ["指数", "涨跌幅"],
                    rows: [["上证指数", "**+1.15%**"], ["科创50", "-0.20%"]]
                ),
                .divider,
                .list(ordered: true, items: ["结构分化", "关注风险"]),
            ]
        )
    }

    func testKeepsInlineMarkdownInsideReadableParagraphAndListBlocks() {
        let markdown = """
        结论先行：**市场反弹**，但风险仍在。

        - 主题轮动
        - 数据待复核
        """

        XCTAssertEqual(
            SeesawMarkdown.parse(markdown),
            [
                .paragraph("结论先行：**市场反弹**，但风险仍在。"),
                .list(ordered: false, items: ["主题轮动", "数据待复核"]),
            ]
        )
    }

    func testReadingTypographyStaysCompactAndFiveColumnTablesFitTheFeed() {
        XCTAssertEqual(SeesawMarkdownLayout.bodyFontSize, 15)
        XCTAssertLessThanOrEqual(SeesawMarkdownLayout.headingSize(for: 1), 22)
        XCTAssertLessThanOrEqual(
            SeesawMarkdownLayout.tableContentWidth(columnCount: 5),
            680
        )
        XCTAssertLessThan(SeesawMarkdownLayout.tableFontSize, 13)
    }

    func testKamiFallbackForTablesAndVeryLongBodiesOnly() {
        XCTAssertFalse(SeesawMarkdownLayout.prefersKamiFallback("短句结论。"))
        XCTAssertFalse(SeesawMarkdownLayout.prefersKamiFallback("""
        ### 标题
        - 一项
        - 两项
        """))
        XCTAssertTrue(SeesawMarkdownLayout.prefersKamiFallback("""
        | a | b |
        |---|---|
        | 1 | 2 |
        """))
        let long = String(repeating: "投研结论。", count: 800) // > 3500 chars
        XCTAssertGreaterThanOrEqual(long.count, SeesawMarkdownLayout.kamiFallbackCharacterThreshold)
        XCTAssertTrue(SeesawMarkdownLayout.prefersKamiFallback(long))
    }
}
