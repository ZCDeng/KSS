import XCTest
@testable import KSSDesktop

/// 图表视窗保持：用户缩放/平移后不该被行情 tick 或主题重推打回原比例。
/// 两道闸——Swift 侧主题去重（不重建 chart）+ chart.html 侧只在换周期时 fitContent。
final class ChartViewportTests: XCTestCase {

    private func chartHTML() throws -> String {
        let url = try XCTUnwrap(
            Bundle.module.url(forResource: "chart", withExtension: "html"),
            "chart.html 必须打进 Bundle.module"
        )
        return try String(contentsOf: url, encoding: .utf8)
    }

    // MARK: - Swift 侧：主题重推判定

    private func palette(_ system: KSSDesignSystem, _ appearance: KSSAppearance) -> KSSWebThemePayload {
        ThemeCatalog.palette(for: system, appearance: appearance).webPayload
    }

    func testUnchangedThemeSkippedWhenSubclassOptsIn() {
        let theme = palette(.clayM3, .dark)
        XCTAssertFalse(
            WebThemePush.shouldPush(latest: theme, lastApplied: theme, skipsUnchanged: true),
            "同一 payload 重推 = chart 重建 = 视窗被打回，图表侧必须跳过"
        )
    }

    func testChangedThemeAlwaysPushed() {
        XCTAssertTrue(WebThemePush.shouldPush(
            latest: palette(.clayM3, .light),
            lastApplied: palette(.clayM3, .dark),
            skipsUnchanged: true
        ))
    }

    func testFirstPushHasNoBaselineSoItGoesThrough() {
        XCTAssertTrue(WebThemePush.shouldPush(
            latest: palette(.clayM3, .dark), lastApplied: nil, skipsUnchanged: true
        ))
    }

    func testMarkdownStyleCoordinatorStillPushesEveryTime() {
        // Markdown 把 overflowScript 挂在 themeScript 上，去重会漏掉 fitsContent 切换。
        let theme = palette(.clayM3, .dark)
        XCTAssertTrue(
            WebThemePush.shouldPush(latest: theme, lastApplied: theme, skipsUnchanged: false)
        )
    }

    func testNoThemeYieldsNoPush() {
        XCTAssertFalse(
            WebThemePush.shouldPush(latest: nil, lastApplied: nil, skipsUnchanged: false)
        )
    }

    func testChartCoordinatorOptsIntoThemeDedupe() {
        let coord = ChartWebView.Coordinator(onSelectMode: nil)
        XCTAssertTrue(coord.skipsUnchangedTheme)
        XCTAssertNil(coord.lastAppliedTheme, "未推过主题前无基线")
    }

    // MARK: - JS 侧：视窗保持

    func testChartHTMLOnlyRefitsThroughTheGuardedHelper() throws {
        let html = try chartHTML()
        XCTAssertTrue(html.contains("function restoreOrFit()"))
        XCTAssertTrue(html.contains("getVisibleLogicalRange"))
        XCTAssertTrue(html.contains("setVisibleLogicalRange"))
        // fitContent 只允许出现在 restoreOrFit 内（正常分支 + catch 兜底），
        // 任何渲染路径直接 fitContent 都会把用户的缩放平移打回去。
        XCTAssertEqual(
            html.components(separatedBy: "fitContent()").count - 1, 2,
            "fitContent 只应出现在 restoreOrFit 的两条分支里"
        )
    }

    func testChartHTMLRemembersRangeBeforeEveryRedraw() throws {
        let html = try chartHTML()
        // 三处：日线渲染前、日内渲染前、主题重建拆 chart 前。
        XCTAssertEqual(
            html.components(separatedBy: "rememberRange();").count - 1, 3,
            "renderTF / renderIntraday / applyThemePayload 三处都要先存视窗"
        )
    }

    func testIntradayViewKeySeparates1mFrom5m() throws {
        let html = try chartHTML()
        // 1m 与 5m 的 currentTF 都停在 "D"，视窗键必须带上分钟档才不会互相套用区间。
        XCTAssertTrue(html.contains(#""I:" + (intradayTF || "?")"#))
    }

    func testThemeRepushDoesNotRebuildChart() throws {
        let html = try chartHTML()
        XCTAssertTrue(html.contains("if (chart && sig === lastThemeSig) return;"))
    }

    func testIntradayTimeScaleOptionsAppliedOncePerChart() throws {
        let html = try chartHTML()
        // rightOffset 是「应用即滚动」的选项，每帧重推会把平移过的位置拽回右端。
        XCTAssertTrue(html.contains(#"if (timeScaleMode !== "I")"#))
        XCTAssertEqual(
            html.components(separatedBy: "rightOffset: 4").count - 1, 2,
            "rightOffset 只在 buildChart 初始配置与日内形态各出现一次"
        )
    }
}
