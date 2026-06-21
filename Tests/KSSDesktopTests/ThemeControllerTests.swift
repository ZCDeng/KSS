import XCTest
@testable import KSSDesktop

final class ThemeControllerTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "kss.theme.tests." + name.filter { $0.isLetter }
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    func testFreshInstallDefaultsToClayM3Dark() {
        let c = ThemeController(defaults: defaults)
        XCTAssertEqual(c.designSystem, .clayM3, "旧安装缺失 designSystemId → clayM3 迁移默认")
        XCTAssertEqual(c.appearance, .dark)
    }

    func testNormalizationPersistsOnFirstRead() {
        // 缺失键首次读取即写回规范化值。
        _ = ThemeController(defaults: defaults)
        XCTAssertEqual(defaults.string(forKey: ThemeController.designSystemKey), "clayM3")
        XCTAssertEqual(defaults.string(forKey: ThemeController.appearanceKey), "dark")
    }

    func testLegacySystemAppearanceMigratesToDark() {
        defaults.set("system", forKey: ThemeController.appearanceKey)
        let c = ThemeController(defaults: defaults)
        XCTAssertEqual(c.appearance, .dark, "遗留 system 第三态 → dark（有意行为迁移）")
        XCTAssertEqual(defaults.string(forKey: ThemeController.appearanceKey), "dark",
                       "规范化结果须写回持久化")
    }

    func testUnknownValuesFallBack() {
        defaults.set("nonsense", forKey: ThemeController.designSystemKey)
        defaults.set("teal", forKey: ThemeController.appearanceKey)
        let c = ThemeController(defaults: defaults)
        XCTAssertEqual(c.designSystem, .clayM3)
        XCTAssertEqual(c.appearance, .dark)
    }

    func testLegacyClayInstallKeepsClayDesignSystem() {
        // 旧安装只有 appearanceMode、无 designSystemId → 仍保持暖纸 clayM3。
        defaults.set("light", forKey: ThemeController.appearanceKey)
        let c = ThemeController(defaults: defaults)
        XCTAssertEqual(c.designSystem, .clayM3)
        XCTAssertEqual(c.appearance, .light, "合法的旧外观值保留")
    }

    func testTwoKeyPersistenceRoundTrip() {
        let c = ThemeController(defaults: defaults)
        c.select(system: .discord)
        c.select(appearance: .light)
        XCTAssertEqual(defaults.string(forKey: ThemeController.designSystemKey), "discord")
        XCTAssertEqual(defaults.string(forKey: ThemeController.appearanceKey), "light")
        // 重启（新实例同 suite）应保留两键。
        let reborn = ThemeController(defaults: defaults)
        XCTAssertEqual(reborn.designSystem, .discord)
        XCTAssertEqual(reborn.appearance, .light)
    }

    func testTokensAndPayloadTrackSelection() {
        let c = ThemeController(defaults: defaults)
        c.select(system: .binanceUS)
        c.select(appearance: .dark)
        XCTAssertEqual(c.tokens.system, .binanceUS)
        XCTAssertEqual(c.tokens.appearance, .dark)
        XCTAssertEqual(c.webPayload.id, "binanceUS")
        XCTAssertEqual(c.colorScheme, .dark)
    }

    func testSelectSameValueIsNoOp() {
        let c = ThemeController(defaults: defaults)
        let before = c.designSystem
        c.select(system: before)
        XCTAssertEqual(c.designSystem, before)
    }
}
