import XCTest
import CoreText
import SwiftUI
@testable import KSSDesktop

final class SeesawWordmarkTests: XCTestCase {

    // MARK: U1 — GlyphExtractor

    private func systemTestFont(size: CGFloat = 64) -> CTFont {
        CTFontCreateUIFontForLanguage(.system, size, nil)!
    }

    func testExtractsOneGroupPerLetter() {
        let glyphs = GlyphExtractor.extractSubpaths(for: "Seesaw", font: systemTestFont())
        XCTAssertEqual(glyphs.count, 6, "\"Seesaw\" 六个字符，每个字符一组")
    }

    func testLetterGroupingFindsBothOccurrencesOfE() {
        // 子路径具体数量随字体/hinting 变化（例如系统 UI 字体的 "e" 在实测中就只有 1 条子路径，
        // 不像有些字体把外轮廓+镂空拆成两条）——按字母数量分组才是稳定可断言的部分，具体子
        // 路径数不写死断言，避免过拟合某一款字体的渲染细节（对应 Risks & Dependencies）。
        let glyphs = GlyphExtractor.extractSubpaths(for: "Seesaw", font: systemTestFont())
        let eGroups = glyphs.filter { $0.letter == "e" }
        XCTAssertEqual(eGroups.count, 2, "\"Seesaw\" 里有两个 e")
        for group in eGroups {
            XCTAssertGreaterThanOrEqual(group.subpaths.count, 1, "\"e\" 至少应有 1 条子路径")
        }
    }

    func testSplitIntoSubpathsSeparatesOuterContourFromCounter() {
        // 手工构造一条含两个 moveTo 的 CGPath（模拟"e"这类字母的外轮廓+镂空），独立于任何
        // 具体字体，直接验证 KTD3 的核心保证：SwiftUI 的 .trim() 不按子路径边界切分弧长，
        // 所以拆分函数必须把每个 moveTo 段拆成独立子路径。
        let combined = CGMutablePath()
        combined.move(to: CGPoint(x: 0, y: 0))
        combined.addLine(to: CGPoint(x: 10, y: 0))
        combined.addLine(to: CGPoint(x: 10, y: 10))
        combined.closeSubpath()
        combined.move(to: CGPoint(x: 3, y: 3))
        combined.addLine(to: CGPoint(x: 7, y: 3))
        combined.addLine(to: CGPoint(x: 7, y: 7))
        combined.closeSubpath()

        let subpaths = GlyphExtractor.splitIntoSubpaths(combined)
        XCTAssertEqual(subpaths.count, 2, "两个 moveTo 段应拆成两条独立子路径")
        XCTAssertEqual(subpaths[0].boundingBoxOfPath, CGRect(x: 0, y: 0, width: 10, height: 10))
        XCTAssertEqual(subpaths[1].boundingBoxOfPath, CGRect(x: 3, y: 3, width: 4, height: 4))
    }

    func testLettersWithoutCounterHaveExactlyOneSubpath() {
        let glyphs = GlyphExtractor.extractSubpaths(for: "Seesaw", font: systemTestFont())
        for letter: Character in ["s", "w"] {
            guard let group = glyphs.first(where: { $0.letter == letter }) else {
                XCTFail("未找到字母 \(letter)")
                continue
            }
            XCTAssertEqual(group.subpaths.count, 1, "\"\(letter)\" 无镂空，子路径数应精确等于 1")
        }
    }

    func testEverySubpathHasNonEmptyBounds() {
        let glyphs = GlyphExtractor.extractSubpaths(for: "Seesaw", font: systemTestFont())
        for group in glyphs {
            for subpath in group.subpaths {
                let bounds = subpath.boundingRect
                XCTAssertGreaterThan(bounds.width, 0, "\(group.letter) 的子路径宽度应非零")
                XCTAssertGreaterThan(bounds.height, 0, "\(group.letter) 的子路径高度应非零")
            }
        }
    }

    func testEmptyStringReturnsEmptyArrayWithoutCrashing() {
        let glyphs = GlyphExtractor.extractSubpaths(for: "", font: systemTestFont())
        XCTAssertTrue(glyphs.isEmpty)
    }

    func testUnresolvableFontNameFallsBackWithoutCrashing() {
        // CTFontCreateWithName 对不存在的名字会静默回退到系统默认字体而不是返回 nil，
        // 所以这里断言的是"不崩溃、仍能拿到某种可用字体的提取结果"，而不是空数组。
        let bogusFont = CTFontCreateWithName("KSS-Nonexistent-Font-Name-XYZ" as CFString, 64, nil)
        let glyphs = GlyphExtractor.extractSubpaths(for: "Seesaw", font: bogusFont)
        XCTAssertEqual(glyphs.count, 6, "字体名解析失败会静默回退到系统字体，仍应正常提取六组字形")
    }

    // MARK: U2 — StaticSubpathShape

    func testPathInRectStaysWithinTargetBoundsAndPreservesAspectRatio() {
        let square = CGMutablePath()
        square.addRect(CGRect(x: 0, y: 0, width: 10, height: 20))
        let sourceBounds = CGRect(x: 0, y: 0, width: 10, height: 20)
        let shape = StaticSubpathShape(sourcePath: Path(square), sourceBounds: sourceBounds)

        let target = CGRect(x: 0, y: 0, width: 100, height: 100)
        let result = shape.path(in: target)
        let resultBounds = result.boundingRect

        XCTAssertLessThanOrEqual(resultBounds.maxX, target.maxX + 0.01)
        XCTAssertLessThanOrEqual(resultBounds.maxY, target.maxY + 0.01)
        XCTAssertGreaterThanOrEqual(resultBounds.minX, target.minX - 0.01)
        XCTAssertGreaterThanOrEqual(resultBounds.minY, target.minY - 0.01)

        // 源 10:20（1:2）比例，缩放后应保持同样比例——不因为目标是正方形而被拉伸变形。
        let sourceAspect = sourceBounds.width / sourceBounds.height
        let resultAspect = resultBounds.width / resultBounds.height
        XCTAssertEqual(sourceAspect, resultAspect, accuracy: 0.01)
    }

    func testPathInRectIsDeterministic() {
        let circle = CGMutablePath()
        circle.addEllipse(in: CGRect(x: 0, y: 0, width: 8, height: 8))
        let shape = StaticSubpathShape(sourcePath: Path(circle), sourceBounds: CGRect(x: 0, y: 0, width: 8, height: 8))
        let rect = CGRect(x: 0, y: 0, width: 50, height: 50)

        // path(in:) 不持有任何隐藏状态、不调用 CoreText（KTD4）——多次调用同一个 rect 应产出
        // 完全一致的结果，本身就是"没有重新提取/没有副作用"的证明，不需要额外的调用计数器。
        let first = shape.path(in: rect)
        let second = shape.path(in: rect)
        XCTAssertEqual(first.boundingRect, second.boundingRect)
    }

    // MARK: U4 — SeesawFont

    func testXcomThemeTargetsChirpHeavy() {
        let theme = ThemeCatalog.palette(for: .xcom, appearance: .light).tokens
        XCTAssertEqual(SeesawFont.targetPostScriptName(for: theme), "Chirp-Heavy")
    }

    func testClassicThemeTargetsSystemRounded() {
        let theme = ThemeCatalog.palette(for: .clayM3, appearance: .light).tokens
        XCTAssertEqual(SeesawFont.targetPostScriptName(for: theme), SeesawFont.systemRoundedLabel)
    }

    func testResolveNeverCrashesRegardlessOfFontRegistration() {
        // swift test 进程不会跑 App 的字体注册逻辑，Chirp 在这里大概率解析不到——
        // resolve() 必须优雅回退，不能崩溃或返回无效字体。
        for system in KSSDesignSystem.allCases {
            let theme = ThemeCatalog.palette(for: system, appearance: .light).tokens
            let font = SeesawFont.resolve(theme: theme, size: 64)
            XCTAssertNotNil(CTFontCopyPostScriptName(font))
        }
    }
}
