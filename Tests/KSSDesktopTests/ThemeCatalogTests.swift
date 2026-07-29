import XCTest
@testable import KSSDesktop

final class ThemeCatalogTests: XCTestCase {

    // MARK: 完整性

    func testAllSixteenCombinationsResolve() {
        var count = 0
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                XCTAssertEqual(p.system, system)
                XCTAssertEqual(p.appearance, appearance)
                count += 1
            }
        }
        XCTAssertEqual(count, 18, "9 设计系统(含 xcom) × 亮/暗 = 18 组合")
        XCTAssertEqual(KSSDesignSystem.allCases.count, 9)
    }

    func testRequiredRolesOpaque() {
        // required 文本/表面/accent role 必须不透明（alpha == 1）。
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                for (name, color) in [
                    ("canvas", p.canvas), ("surface", p.surface), ("ink", p.ink),
                    ("body", p.body), ("muted", p.muted), ("accent", p.accent),
                    ("onAccent", p.onAccent), ("hairline", p.hairline),
                    ("chartSurface", p.chartSurface),
                ] {
                    XCTAssertEqual(color.a, 1, accuracy: 0.0001,
                                   "\(system).\(appearance).\(name) 必须不透明")
                }
            }
        }
    }

    // MARK: provenance baseline（测试专用，不从生产 catalog 派生）

    /// clayM3 baseline = 迁移前 Theme.swift 的暖纸/clay 规范化映射。
    func testClayM3MatchesMigrationBaseline() {
        let dark = ThemeCatalog.palette(for: .clayM3, appearance: .dark)
        XCTAssertEqual(dark.canvas, ThemeColor(0x141413))
        XCTAssertEqual(dark.ink, ThemeColor(0xFAF9F5))
        XCTAssertEqual(dark.surface, ThemeColor(0x1D1B19))
        XCTAssertEqual(dark.surfaceHighest, ThemeColor(0x363230))

        let light = ThemeCatalog.palette(for: .clayM3, appearance: .light)
        XCTAssertEqual(light.canvas, ThemeColor(0xFAF9F5))
        XCTAssertEqual(light.ink, ThemeColor(0x141413))
        XCTAssertEqual(light.surface, ThemeColor(0xFFFFFF))
    }

    /// material3 baseline = showcase 紫色成对 token 的本地转录映射。
    func testMaterial3MatchesShowcaseBaseline() {
        let light = ThemeCatalog.palette(for: .material3, appearance: .light)
        XCTAssertEqual(light.accent, ThemeColor(0x6750A4))   // --md-sys-color-primary
        XCTAssertEqual(light.onAccent, ThemeColor(0xFFFFFF)) // --on-primary
        XCTAssertEqual(light.ink, ThemeColor(0x1D1B20))      // --on-surface

        let dark = ThemeCatalog.palette(for: .material3, appearance: .dark)
        XCTAssertEqual(dark.accent, ThemeColor(0xD0BCFF))
        XCTAssertEqual(dark.onAccent, ThemeColor(0x381E72))
        XCTAssertEqual(dark.surface, ThemeColor(0x1D1B20))   // surface-container-low
    }

    /// clayM3 与 material3 来源独立：accent/surface 不同，且任一不得经 alias 派生为另一套。
    func testClayM3AndMaterial3AreIndependent() {
        for appearance in KSSAppearance.allCases {
            let clay = ThemeCatalog.palette(for: .clayM3, appearance: appearance)
            let m3 = ThemeCatalog.palette(for: .material3, appearance: appearance)
            XCTAssertNotEqual(clay.accent, m3.accent, "clayM3 与 material3 accent 必须不同")
            XCTAssertNotEqual(clay.canvas, m3.canvas)
            XCTAssertNotEqual(clay.system, m3.system)
        }
        XCTAssertNotEqual(KSSDesignSystem.clayM3, KSSDesignSystem.material3)
    }

    // MARK: 市场语义

    func testUpDownDistinctAndFixedAcrossSystems() {
        // 红涨绿跌固定：所有设计系统同一外观共享同一组 up/down，且 up != down。
        var lightUp: ThemeColor?
        for system in KSSDesignSystem.allCases {
            let p = ThemeCatalog.palette(for: system, appearance: .light)
            XCTAssertTrue(ThemeValidation.upDownDistinct(for: p))
            if let first = lightUp { XCTAssertEqual(p.up, first, "up 不随设计系统漂移") }
            lightUp = p.up
        }
        XCTAssertEqual(ThemeCatalog.palette(for: .clayM3, appearance: .light).up, ThemeColor(0xF23645))
        XCTAssertEqual(ThemeCatalog.palette(for: .clayM3, appearance: .light).down, ThemeColor(0x089981))
    }

    // MARK: 对比度（含 alpha 合成）

    func testContrastAAForAllCombinations() {
        let findings = ThemeValidation.allFindings()
        XCTAssertTrue(findings.isEmpty,
                      "对比度未达标：" + findings.map {
                          "\($0.system).\($0.appearance).\($0.role)=\(String(format: "%.2f", $0.ratio))<\($0.required)"
                      }.joined(separator: "; "))
    }

    func testCompositedAlphaContrastIsEvaluated() {
        // accentSoft 是半透明，须合成到背景后比较——验证 composited 工具有效。
        let p = ThemeCatalog.palette(for: .clayM3, appearance: .dark)
        let soft = p.accent.withAlpha(0.16)
        let composited = soft.composited(over: p.surface)
        XCTAssertEqual(composited.a, 1, accuracy: 0.0001)
        XCTAssertGreaterThan(composited.contrastRatio(against: p.surface), 1.0)
    }

    // MARK: Web payload

    func testWebPayloadCoversAllConsumedCSSVars() {
        // 三处 HTML 消费的键全部存在（缺一会让网页落回 fallback 而非主题色）。
        let required = ["bg", "surface", "surface2", "ink", "body", "muted", "line", "lineSoft",
                        "accent", "accentSoft", "onAccent", "code", "th", "zebra", "tag",
                        "olive", "oliveSoft", "gold", "blue", "blueSoft", "zone", "zoneLine",
                        "up", "down", "upFill", "downFill", "ma5", "ma20", "grid", "text",
                        "cross", "dif", "dea", "obv", "bbi", "boll", "chip", "chipText",
                        "chipOn", "chartBg", "hair"]
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let payload = ThemeCatalog.palette(for: system, appearance: appearance).webPayload
                for key in required {
                    XCTAssertNotNil(payload.colors[key], "\(system).\(appearance) 缺 CSS 键 \(key)")
                }
                XCTAssertEqual(payload.version, KSSWebThemePayload.currentVersion)
                XCTAssertEqual(payload.id, system.rawValue)
                XCTAssertEqual(payload.mode, appearance.rawValue)
                XCTAssertFalse(payload.typography.sans.isEmpty)
            }
        }
    }

    func testWebPayloadRoundTripsThroughJSON() {
        let payload = ThemeCatalog.palette(for: .discord, appearance: .dark).webPayload
        let data = payload.json.data(using: .utf8)!
        let decoded = try? JSONDecoder().decode(KSSWebThemePayload.self, from: data)
        XCTAssertEqual(decoded, payload)
    }

    func testWebColorsAreCSSSafe() {
        // 不透明 → #RRGGBB；半透明 → rgba(...)。禁止裸 UInt 或非法值。
        for system in KSSDesignSystem.allCases {
            let payload = ThemeCatalog.palette(for: system, appearance: .dark).webPayload
            for (_, value) in payload.colors {
                let ok = value.hasPrefix("#") || value.hasPrefix("rgba(")
                XCTAssertTrue(ok, "非 CSS 安全颜色值：\(value)")
            }
        }
    }

    func testClaySerifUsesKamiTypographyStack() {
        let light = ThemeCatalog.palette(for: .clayM3, appearance: .light)
        XCTAssertTrue(light.typography.serif.contains("TsangerJinKai02"))
        XCTAssertTrue(light.webPayload.typography.serif.contains("TsangerJinKai02"))
        XCTAssertEqual(light.webPayload.colors["tag"], ThemeColor(0xE4ECF5).css)
        let dark = ThemeCatalog.palette(for: .clayM3, appearance: .dark)
        XCTAssertEqual(dark.webPayload.colors["tag"], ThemeColor(0x243247).css)
    }
}
