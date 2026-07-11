import SwiftUI
import CoreText
import CoreGraphics
import AppKit

// MARK: - U1: CoreText 字形提取 + 子路径拆分

/// 一个字母的所有子路径（外轮廓 + 镂空各自一条），SwiftUI Y 轴朝下坐标系。
struct GlyphSubpaths {
    let letter: Character
    let subpaths: [Path]
}

/// 从系统字体轮廓取字形路径，按字母、按子路径展开——供 SeesawWordmark 逐子路径独立描边动画。
enum GlyphExtractor {
    /// 给定字符串与 `CTFont`，返回按字母分组、按子路径展开的路径数据，坐标系已校正为
    /// SwiftUI Y 轴朝下、整体按输入字符串的联合 bounding box 统一定位（不逐字母各自计算，
    /// 避免基线不齐——见 KTD2）。字体/字号由调用方决定（U4 按主题选字体）。
    static func extractSubpaths(for string: String, font: CTFont) -> [GlyphSubpaths] {
        guard !string.isEmpty else { return [] }

        let attrString = CFAttributedStringCreate(
            nil, string as CFString,
            [kCTFontAttributeName: font] as CFDictionary
        )
        guard let attrString else { return [] }
        let line = CTLineCreateWithAttributedString(attrString)
        guard let runs = CTLineGetGlyphRuns(line) as? [CTRun] else { return [] }

        struct RawGlyph { let character: Character; let path: CGPath }
        var rawGlyphs: [RawGlyph] = []
        var characterIndex = string.startIndex

        for run in runs {
            let glyphCount = CTRunGetGlyphCount(run)
            guard glyphCount > 0 else { continue }
            var glyphs = [CGGlyph](repeating: 0, count: glyphCount)
            var positions = [CGPoint](repeating: .zero, count: glyphCount)
            CTRunGetGlyphs(run, CFRangeMake(0, glyphCount), &glyphs)
            CTRunGetPositions(run, CFRangeMake(0, glyphCount), &positions)

            for i in 0..<glyphCount {
                guard characterIndex < string.endIndex else { break }
                let character = string[characterIndex]
                characterIndex = string.index(after: characterIndex)
                var transform = CGAffineTransform(translationX: positions[i].x, y: positions[i].y)
                if let path = CTFontCreatePathForGlyph(font, glyphs[i], &transform) {
                    rawGlyphs.append(RawGlyph(character: character, path: path))
                }
            }
        }
        guard !rawGlyphs.isEmpty else { return [] }

        var unionRect = CGRect.null
        for g in rawGlyphs { unionRect = unionRect.union(g.path.boundingBoxOfPath) }
        guard !unionRect.isNull, unionRect.width > 0, unionRect.height > 0 else { return [] }

        // 字形坐标系 Y 轴朝上、原点在基线；SwiftUI Path 是 Y 轴朝下——统一按整行 bounding box 翻转+平移。
        var flip = CGAffineTransform(scaleX: 1, y: -1)
            .translatedBy(x: -unionRect.minX, y: -unionRect.maxY)

        return rawGlyphs.map { g in
            let flipped = g.path.copy(using: &flip) ?? g.path
            let subpaths = splitIntoSubpaths(flipped).map { Path($0) }
            return GlyphSubpaths(letter: g.character, subpaths: subpaths)
        }
    }

    /// 按 `CGPathElement` 的 `.moveToPoint` 分段，把一条可能含多个子路径（外轮廓+镂空）的
    /// `CGPath` 拆成独立子路径数组——SwiftUI 的 `.trim()` 把整条路径当一段连续弧长处理，
    /// 不按子路径边界切分（KTD3）；不拆的话，"e"/"a" 这类有镂空的字母，镂空会在累计弧长
    /// 跨过外轮廓总长时突然出现，不是顺着描边出来的。
    static func splitIntoSubpaths(_ path: CGPath) -> [CGPath] {
        var subpaths: [CGPath] = []
        var current = CGMutablePath()
        var hasContent = false

        path.applyWithBlock { elementPtr in
            let element = elementPtr.pointee
            switch element.type {
            case .moveToPoint:
                if hasContent { subpaths.append(current.copy() ?? current) }
                current = CGMutablePath()
                current.move(to: element.points[0])
                hasContent = true
            case .addLineToPoint:
                current.addLine(to: element.points[0])
            case .addQuadCurveToPoint:
                current.addQuadCurve(to: element.points[1], control: element.points[0])
            case .addCurveToPoint:
                current.addCurve(to: element.points[2], control1: element.points[0], control2: element.points[1])
            case .closeSubpath:
                current.closeSubpath()
            @unknown default:
                break
            }
        }
        if hasContent { subpaths.append(current.copy() ?? current) }
        return subpaths
    }
}

// MARK: - U2: 单子路径可独立 trim 的 Shape

/// 包一条已经算好的 `Path`，`path(in:)` 只做等比缩放定位，不重新调用 CoreText（KTD4——
/// SwiftUI 在 trim 动画每一帧都会重新求值 `path(in:)`，重跑 CoreText 提取是不必要的重复计算）。
/// 非 private：U2 的测试场景需要直接构造并调用 `path(in:)` 断言纯函数行为。
struct StaticSubpathShape: Shape {
    let sourcePath: Path
    let sourceBounds: CGRect

    func path(in rect: CGRect) -> Path {
        guard sourceBounds.width > 0, sourceBounds.height > 0 else { return Path() }
        let scale = min(rect.width / sourceBounds.width, rect.height / sourceBounds.height)
        let scaledWidth = sourceBounds.width * scale
        let scaledHeight = sourceBounds.height * scale
        let offsetX = rect.minX + (rect.width - scaledWidth) / 2 - sourceBounds.minX * scale
        let offsetY = rect.minY + (rect.height - scaledHeight) / 2 - sourceBounds.minY * scale
        let transform = CGAffineTransform(scaleX: scale, y: scale)
            .concatenating(CGAffineTransform(translationX: offsetX, y: offsetY))
        return sourcePath.applying(transform)
    }
}

// MARK: - U4: 主题 → 字体解析

enum SeesawFont {
    /// 主题 → 目标字体名的纯函数映射（可测，不涉及字体是否已在当前进程注册这个运行时问题）。
    /// xcom 用 "Chirp-Heavy"：固定给出目标粗细的 PostScript 名，不复用 `KSSFont` 内部的私有
    /// 分桶表——这里只需要固定的 Heavy 档，没有 `KSSFont.themed` 那种可变字重的需求。
    static func targetPostScriptName(for theme: KSSThemeTokens) -> String {
        theme.system == .xcom ? "Chirp-Heavy" : systemRoundedLabel
    }

    /// 系统圆体不是通过 PostScript 名解析的（走 `NSFontDescriptor.withDesign(.rounded)`），
    /// 这个字符串只作为 `targetPostScriptName` 的可读标签，不传给 `CTFontCreateWithName`。
    static let systemRoundedLabel = "SF Pro Rounded Heavy"

    /// `CTFontCreateWithName` 找不到目标名字时会静默回退到系统默认字体而不是返回 nil，所以用
    /// `CTFontCopyPostScriptName` 校验命中与否，命不中就退到系统圆体，保证任何环境下都有圆润
    /// 重体观感，不会掉到系统默认无衬线字体（对应 Risks & Dependencies 的缓解方案）。字体是否
    /// 已注册是运行时环境问题（`swift test` 不跑 App 的字体注册逻辑）——这层校验+回退只能靠
    /// 实机验证，`targetPostScriptName` 覆盖可单测的分支选择逻辑。
    static func resolve(theme: KSSThemeTokens, size: CGFloat) -> CTFont {
        if theme.system == .xcom, let chirp = chirpHeavy(size: size) {
            return chirp
        }
        return systemRounded(size: size)
    }

    private static func chirpHeavy(size: CGFloat) -> CTFont? {
        let font = CTFontCreateWithName("Chirp-Heavy" as CFString, size, nil)
        guard let postScriptName = CTFontCopyPostScriptName(font) as String?,
              postScriptName == "Chirp-Heavy" else {
            return nil
        }
        return font
    }

    private static func systemRounded(size: CGFloat) -> CTFont {
        let base = NSFont.systemFont(ofSize: size, weight: .heavy)
        let descriptor = base.fontDescriptor.withDesign(.rounded) ?? base.fontDescriptor
        let nsFont = NSFont(descriptor: descriptor, size: size) ?? base
        return nsFont as CTFont
    }
}

// MARK: - U3/U5: SeesawWordmark 视图（描边动画生命周期 + 集成入口）

/// Seesaw 空态页顶部的线条描边字标：逐字母、逐子路径独立描边动画进入，播完停顿后循环重播。
/// 替换原来的 `orb` 光晕球（见 `AIChatView.heroEmptyState`）。
struct SeesawWordmark: View {
    @Environment(\.kssTheme) private var theme

    private let text = "Seesaw"
    private let displayWidth: CGFloat = 220
    /// 提取用固定基准字号；渲染时 `StaticSubpathShape` 按 `.frame` 尺寸等比缩放，基准字号本身不影响最终显示大小。
    private let extractionSize: CGFloat = 64

    private struct SubpathItem: Identifiable {
        let id: Int
        let path: Path
    }

    @State private var items: [SubpathItem] = []
    @State private var bounds: CGRect = .zero
    @State private var progress: [Double] = []
    @State private var loopTask: Task<Void, Never>?

    private var displayHeight: CGFloat {
        guard bounds.width > 0 else { return displayWidth * 0.37 }
        return displayWidth * bounds.height / bounds.width
    }

    var body: some View {
        ZStack {
            ForEach(items) { item in
                StaticSubpathShape(sourcePath: item.path, sourceBounds: bounds)
                    .trim(from: 0, to: progress.indices.contains(item.id) ? progress[item.id] : 0)
                    .stroke(theme.textPrimary, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }
        }
        .frame(width: displayWidth, height: displayHeight)
        .onAppear { restart() }
        .onDisappear { cancelLoop() }
        .onChange(of: theme.system) { _, _ in restart() }
    }

    private func extract() {
        let font = SeesawFont.resolve(theme: theme, size: extractionSize)
        let glyphs = GlyphExtractor.extractSubpaths(for: text, font: font)

        var union = CGRect.null
        var flatItems: [SubpathItem] = []
        var nextID = 0
        for glyph in glyphs {
            for subpath in glyph.subpaths {
                union = union.union(subpath.boundingRect)
                flatItems.append(SubpathItem(id: nextID, path: subpath))
                nextID += 1
            }
        }
        bounds = union.isNull ? .zero : union
        items = flatItems
        progress = Array(repeating: 0, count: flatItems.count)
    }

    /// 主题切换（KTD7 扩展，对应 doc review 的主题切换重置发现）或首次出现时，取消旧循环、
    /// 重新提取字形、从进度 0 开始新一轮循环——不残留上一套字体/进度状态。
    private func restart() {
        loopTask?.cancel()
        extract()
        startLoop()
    }

    private func cancelLoop() {
        loopTask?.cancel()
        loopTask = nil
        progress = Array(repeating: 0, count: progress.count)
    }

    /// 描边动画时序（KTD6 起始参数）：每条子路径 trim 0→1 用 0.8s、easeInOut；子路径间交错
    /// 80ms；一轮描边完成后停顿 4.5s（保持完整字标观感），再重置归零、开始下一轮。
    private func startLoop() {
        let strokeDuration = 0.8
        let stagger = 0.08
        let pause = 4.5

        loopTask = Task {
            while !Task.isCancelled {
                for i in progress.indices {
                    withAnimation(.easeInOut(duration: strokeDuration).delay(Double(i) * stagger)) {
                        progress[i] = 1
                    }
                }
                let totalDrawTime = Double(max(progress.count - 1, 0)) * stagger + strokeDuration
                try? await Task.sleep(nanoseconds: UInt64((totalDrawTime + pause) * 1_000_000_000))
                guard !Task.isCancelled else { return }
                for i in progress.indices { progress[i] = 0 }
            }
        }
    }
}
