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
        XCTAssertLessThanOrEqual(SeesawMarkdownLayout.headingSize(for: 1), 20)
        XCTAssertLessThanOrEqual(
            SeesawMarkdownLayout.tableContentWidth(columnCount: 5),
            680
        )
        XCTAssertLessThan(SeesawMarkdownLayout.tableFontSize, 13)
    }

    func testUnbalancedInlineMarkupIsRejectedDuringStreaming() {
        XCTAssertTrue(SeesawMarkdown.inlineMarkupIsBalanced("上手信息已拉齐。这是 **KSS Desktop**。"))
        XCTAssertFalse(SeesawMarkdown.inlineMarkupIsBalanced("上手信息已拉齐。这是 **KSS Desktop"))
        XCTAssertFalse(SeesawMarkdown.inlineMarkupIsBalanced("路径 `/Applications/KSSDesktop.app"))
        XCTAssertTrue(SeesawMarkdown.inlineMarkupIsBalanced("路径 `/tmp` 可用"))
    }

    func testTranscriptTablesStayNativeWithoutWebViewFallback() {
        // Tables must parse natively so assistant height tracks content.
        let blocks = SeesawMarkdown.parse("""
        | a | b |
        |---|---|
        | 1 | 2 |
        """)
        XCTAssertEqual(blocks.count, 1)
        if case .table(let headers, let rows) = blocks[0] {
            XCTAssertEqual(headers, ["a", "b"])
            XCTAssertEqual(rows, [["1", "2"]])
        } else {
            XCTFail("expected table block")
        }
    }

    func testNumericColumnsRightAlignAndSignedChangeColumnsKeepSign() {
        let kinds = SeesawMarkdownLayout.columnKinds(
            headers: ["项", "8/14 值", "涨跌幅"],
            rows: [
                ["开盘", "215.00（高开约 +2.3%）", "**+1.15%**"],
                ["收盘", "211.91（+0.80%）", "-0.20%"],
                ["说明", "利好冲高回落", "—"],
            ]
        )
        XCTAssertEqual(kinds, [.text, .number, .signedChange])
        XCTAssertEqual(SeesawMarkdownLayout.signedDirection("**+1.15%**"), 1)
        XCTAssertEqual(SeesawMarkdownLayout.signedDirection("-0.20%"), -1)
        XCTAssertEqual(SeesawMarkdownLayout.signedDirection("215.00（高开约 +2.3%）"), 1)
        XCTAssertEqual(SeesawMarkdownLayout.signedDirection("约 67.3 亿"), 0)
    }
}
