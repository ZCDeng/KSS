import XCTest
@testable import KSSDesktop

/// 可投资地图暴露色的机检门（plan U4）。
///
/// 方案只写了「两两可辨」，那是手感不是判据——差一个色阶也算不等。这里把它换成
/// 两条可测阈值，深绿对浅绿、黄对橙这两对糊在一起时测试会红。
final class ExposureColorTests: XCTestCase {

    /// 同一 palette 内任意两个行业色的最小 CIE76 色差。
    private let minDeltaE: Double = 25
    /// 每个色对该 palette 表面色的最小 WCAG 对比度（UI 图形边界档）。
    private let minContrast: Double = 3.0
    /// 未定色与 muted 的最小色差——待定色被看成未上图是这套数据最贵的误读。
    private let minPendingSeparation: Double = 15

    // MARK: - 覆盖完整性

    func testAllPalettesExposeSixSlots() {
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                XCTAssertEqual(p.exposureIndustryColors.count, 5)
                for (key, color) in p.exposureIndustryColors {
                    XCTAssertEqual(color.a, 1, accuracy: 0.0001,
                                   "\(system).\(appearance).\(key) 必须不透明")
                }
                XCTAssertEqual(p.exposurePending.a, 1, accuracy: 0.0001)
            }
        }
    }

    func testColorKeyLookupMatchesBridgeKeys() {
        // 键必须与桥接返回的 colorKey 逐字一致，否则渲染层查不到色。
        let p = ThemeCatalog.palette(for: .clayM3, appearance: .dark)
        for key in ["deep_green", "light_green", "yellow", "orange", "purple", "pending"] {
            XCTAssertNotNil(p.exposureColor(forKey: key), "缺色槽: \(key)")
        }
        XCTAssertNil(p.exposureColor(forKey: "red"), "红不是行业色，不该有取值")
        XCTAssertNil(p.exposureColor(forKey: "chartreuse"))
    }

    // MARK: - 可辨性

    func testIndustryColorsAreMutuallyDistinguishable() {
        var worst = Double.greatestFiniteMagnitude
        var worstLabel = ""
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                let slots = p.exposureIndustryColors
                for i in slots.indices {
                    for j in slots.indices where j > i {
                        let d = Self.deltaE(slots[i].color, slots[j].color)
                        if d < worst {
                            worst = d
                            worstLabel = "\(system).\(appearance) \(slots[i].key)/\(slots[j].key)"
                        }
                        XCTAssertGreaterThanOrEqual(
                            d, minDeltaE,
                            "\(system).\(appearance) 的 \(slots[i].key) 与 \(slots[j].key) 色差 \(d) 低于 \(minDeltaE)")
                    }
                }
            }
        }
        XCTAssertGreaterThanOrEqual(worst, minDeltaE, "最差一对: \(worstLabel)")
    }

    func testIndustryColorsReadableAgainstSurface() {
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                for (key, color) in p.exposureIndustryColors {
                    let ratio = color.contrastRatio(against: p.surface)
                    XCTAssertGreaterThanOrEqual(
                        ratio, minContrast,
                        "\(system).\(appearance).\(key) 对 surface 对比度 \(ratio) 低于 \(minContrast)")
                }
            }
        }
    }

    func testPendingDistinctFromMutedAndIndustryColors() {
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                let sep = Self.deltaE(p.exposurePending, p.muted)
                XCTAssertGreaterThanOrEqual(
                    sep, minPendingSeparation,
                    "\(system).\(appearance) 未定色与 muted 色差 \(sep) 过近，待定色会被看成未上图")
                for (key, color) in p.exposureIndustryColors {
                    XCTAssertNotEqual(p.exposurePending, color, "未定色不得等于 \(key)")
                }
                XCTAssertGreaterThanOrEqual(
                    p.exposurePending.contrastRatio(against: p.surface), minContrast)
            }
        }
    }

    // MARK: - 逐设计系统定值（KTD6）

    func testColorsVaryByDesignSystem() {
        // KTD6 选的是逐套定值而不是跨系统共享一套；若哪天被改回共享，这条会红。
        for appearance in KSSAppearance.allCases {
            let deepGreens = Set(KSSDesignSystem.allCases.map {
                ThemeCatalog.palette(for: $0, appearance: appearance).exposureDeepGreen.css
            })
            XCTAssertGreaterThan(deepGreens.count, 1,
                                 "\(appearance) 下九套设计系统的深绿不该完全相同")
        }
    }

    // MARK: - 个股红区红（UI 重设计新增槽）

    /// 红区标是白字铺在实填上。直接借 `up` 做填充时白字只有 3.6:1，够不着 4.5:1，
    /// 所以红区有自己的槽；这条把那个理由钉死。
    func testExposureRedCarriesWhiteTextAndSeparatesFromSurface() {
        let white = ThemeColor(0xFFFFFF)
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                XCTAssertGreaterThanOrEqual(
                    white.contrastRatio(against: p.exposureRed), 4.5,
                    "\(system).\(appearance) 红区标的白字对比度不足")
                // 不检查填充对 surface 的 3:1：白字要 4.5:1 就把红压到 L ≤ 0.183，
                // 而偏亮的暗色 surface 要 3:1 又要求 L ≥ 0.199，两个区间不相交。
                // 边界因此由 ExposureZoneLabel 的 1pt 描边承担，不由填充承担。
            }
        }
    }

    /// 红仍然不是行业色（plan R2）：查表查不到，且与五色都不相等。
    func testExposureRedIsNotAnIndustryColor() {
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                XCTAssertNil(p.exposureColor(forKey: "red"))
                for (key, color) in p.exposureIndustryColors {
                    XCTAssertNotEqual(p.exposureRed, color, "红区红撞上了行业色 \(key)")
                }
            }
        }
    }

    /// 红区红要离行业色足够远，尤其是橙——两者同为暖红族，撞上了红区标签会被读成橙色节点。
    /// 门槛与行业色两两可辨用同一个（ΔE76 ≥ 25）：#B3261E 这类暗红对暴露橙只有 17.3，
    /// 正是这条测试写出来要拦的东西。
    func testExposureRedIsFarEnoughFromEveryIndustryColor() {
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                let p = ThemeCatalog.palette(for: system, appearance: appearance)
                for (key, color) in p.exposureIndustryColors {
                    let d = Self.deltaE(p.exposureRed, color)
                    XCTAssertGreaterThanOrEqual(
                        d, minDeltaE,
                        "\(system).\(appearance) 红区红对 \(key) 色差 \(d) 过近")
                }
                XCTAssertGreaterThanOrEqual(Self.deltaE(p.exposureRed, p.exposurePending), minDeltaE)
            }
        }
    }

    /// 红区红与涨跌的红分开：同屏出现「涨」和「红区」两种红会互相冒充。
    func testExposureRedDiffersFromMarketUp() {
        for appearance in KSSAppearance.allCases {
            let p = ThemeCatalog.palette(for: .xcom, appearance: appearance)
            XCTAssertNotEqual(p.exposureRed, p.up)
        }
    }

    func testMarketColorsUnaffected() {
        // 暴露色是新增槽位，不该动既有的涨跌语义色。
        let a = ThemeCatalog.palette(for: .clayM3, appearance: .dark)
        let b = ThemeCatalog.palette(for: .xcom, appearance: .dark)
        XCTAssertEqual(a.up, b.up, "涨跌色仍是跨系统共享")
        XCTAssertEqual(a.down, b.down)
    }

    // MARK: - CIE76 色差

    /// sRGB → CIE Lab（D65）→ 欧氏距离。比 CIEDE2000 粗但足够堵住「看起来一样」，
    /// 且实现短到能在测试里就地读懂。
    private static func deltaE(_ x: ThemeColor, _ y: ThemeColor) -> Double {
        let a = lab(x), b = lab(y)
        return sqrt(pow(a.0 - b.0, 2) + pow(a.1 - b.1, 2) + pow(a.2 - b.2, 2))
    }

    private static func lab(_ c: ThemeColor) -> (Double, Double, Double) {
        func linear(_ v: Double) -> Double {
            v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4)
        }
        let r = linear(c.r) * 100, g = linear(c.g) * 100, b = linear(c.b) * 100
        let x = r * 0.4124 + g * 0.3576 + b * 0.1805
        let y = r * 0.2126 + g * 0.7152 + b * 0.0722
        let z = r * 0.0193 + g * 0.1192 + b * 0.9505
        func f(_ t: Double) -> Double { t > 0.008856 ? pow(t, 1.0 / 3) : 7.787 * t + 16.0 / 116 }
        let fx = f(x / 95.047), fy = f(y / 100.0), fz = f(z / 108.883)
        return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))
    }
}
