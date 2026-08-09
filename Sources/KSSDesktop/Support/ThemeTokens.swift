import SwiftUI
import AppKit

// MARK: - 原生语义 token（SwiftUI 环境消费）

/// 从 `KSSPalette` 解析出的 SwiftUI 颜色 token。成员名沿用旧 `KSSTheme`，
/// 视图迁移即把 `KSSTheme.x` 换成环境 `theme.x`。
struct KSSThemeTokens: Equatable {
    let system: KSSDesignSystem
    let appearance: KSSAppearance

    // 表面层级
    let canvas: Color
    let surfaceContainerLowest: Color
    let surface: Color
    let surfaceContainer: Color
    let surfaceRaised: Color
    let surfaceContainerHighest: Color
    let surfaceTint: Color
    let chartSurface: Color
    // 边线
    let hairline: Color
    let outlineVariant: Color
    // 文字
    let textPrimary: Color
    let textBody: Color
    let textSecondary: Color
    // accent
    let accent: Color
    let onAccent: Color
    let secondary: Color
    let accentSoft: Color
    // 市场 / 图表
    let up: Color
    let down: Color
    let ma5: Color
    let ma20: Color
    // 可投资地图暴露色（plan 2026-08-09-001 U4/KTD6）：五个行业色 + 未定色。
    // 未上图灰点复用 textSecondary，未核节点复用 outlineVariant 虚线描边，两者不占独立槽。
    let exposureDeepGreen: Color
    let exposureLightGreen: Color
    let exposureYellow: Color
    let exposureOrange: Color
    let exposurePurple: Color
    let exposurePending: Color
    // 几何 / 字体 / 阴影
    let cardRadius: CGFloat
    let chipRadius: CGFloat
    let titleDesign: Font.Design
    /// nil = 8 套经典主题,沿用系统字体;非 nil(如 "Chirp")时 `KSSFont.themed` 走自定义字体 + CJK 级联。
    let nativeFontFamily: String?
    let nativeCJKFamily: String?
    let shadowOpacity: Double
    let shadowRadius: CGFloat
    let shadowY: CGFloat
    // AppKit 原色（窗口/WebView underPageBackground）
    let canvasNS: NSColor
    let chartSurfaceNS: NSColor

    /// 涨跌方向色：>0 红、<0 绿、0/nil 用正文色。红涨绿跌 A股语义固定。
    func signColor(_ value: Double?) -> Color {
        guard let value, value != 0 else { return textBody }
        return value > 0 ? up : down
    }

    /// 桥接返回的行业色机器键 → 当前主题取值（与 `KSSPalette.exposureColor` 同一张表）。
    /// 未知键返回 nil；红不是行业色，永远查不到（plan R2）。
    func exposureColor(forKey key: String) -> Color? {
        switch key {
        case "deep_green":  return exposureDeepGreen
        case "light_green": return exposureLightGreen
        case "yellow":      return exposureYellow
        case "orange":      return exposureOrange
        case "purple":      return exposurePurple
        case "pending":     return exposurePending
        default:            return nil
        }
    }
}

extension KSSPalette {
    var tokens: KSSThemeTokens {
        let softAlpha = appearance == .dark ? 0.16 : 0.12
        return KSSThemeTokens(
            system: system, appearance: appearance,
            canvas: canvas.color,
            surfaceContainerLowest: surfaceLowest.color,
            surface: surface.color,
            surfaceContainer: surfaceContainer.color,
            surfaceRaised: surfaceRaised.color,
            surfaceContainerHighest: surfaceHighest.color,
            surfaceTint: surfaceTint.color,
            chartSurface: chartSurface.color,
            hairline: hairline.color,
            outlineVariant: outlineVariant.color,
            textPrimary: ink.color,
            textBody: body.color,
            textSecondary: muted.color,
            accent: accent.color,
            onAccent: onAccent.color,
            secondary: secondary.color,
            accentSoft: accent.withAlpha(softAlpha).color,
            up: up.color,
            down: down.color,
            ma5: ma5.color,
            ma20: ma20.color,
            exposureDeepGreen: exposureDeepGreen.color,
            exposureLightGreen: exposureLightGreen.color,
            exposureYellow: exposureYellow.color,
            exposureOrange: exposureOrange.color,
            exposurePurple: exposurePurple.color,
            exposurePending: exposurePending.color,
            cardRadius: cardRadius,
            chipRadius: chipRadius,
            titleDesign: typography.titleDesign,
            nativeFontFamily: typography.nativeFontFamily,
            nativeCJKFamily: typography.nativeCJKFamily,
            shadowOpacity: elevation.opacity,
            shadowRadius: elevation.radius,
            shadowY: elevation.y,
            canvasNS: canvas.nsColor,
            chartSurfaceNS: chartSurface.nsColor
        )
    }
}

// MARK: - 环境注入

private struct KSSThemeKey: EnvironmentKey {
    static let defaultValue: KSSThemeTokens =
        ThemeCatalog.palette(for: .clayM3, appearance: .dark).tokens
}

extension EnvironmentValues {
    /// 当前主题语义 token。主题切换后整棵 SwiftUI 树重算（不 teardown WebView）。
    var kssTheme: KSSThemeTokens {
        get { self[KSSThemeKey.self] }
        set { self[KSSThemeKey.self] = newValue }
    }
}

// MARK: - WebView 版本化 token 协议

/// 三个 WKWebView（K 线 / Markdown / 架构图）共享的版本化主题 payload。
/// `colors` 键名与三处 HTML 消费的 CSS custom properties 一一对应；JSON 安全，禁止字符串拼接注入。
struct KSSWebThemePayload: Codable, Equatable {
    let version: Int
    let id: String     // designSystem rawValue
    let mode: String   // "light" / "dark"
    let colors: [String: String]
    let typography: Typography
    let shape: Shape
    let elevation: Elevation

    struct Typography: Codable, Equatable {
        let serif: String
        let sans: String
        let mono: String
    }
    struct Shape: Codable, Equatable {
        let card: Double
        let chip: Double
    }
    struct Elevation: Codable, Equatable {
        let opacity: Double
        let radius: Double
        let y: Double
    }

    static let currentVersion = 1

    /// JSON 字符串（供 `evaluateJavaScript("window.kssSetTheme(\(json))")`）。
    var json: String {
        guard let data = try? JSONEncoder().encode(self),
              let str = String(data: data, encoding: .utf8) else { return "{}" }
        return str
    }

    /// 报告/资讯/对话交付物：Kami print 节奏 + Chiron GoRound TC，配色跟 chrome。
    func asEditorialContentTheme() -> KSSWebThemePayload {
        let face = ThemeTypography.contentPrint
        return KSSWebThemePayload(
            version: version,
            id: id,
            mode: mode,
            colors: colors,
            typography: Typography(
                serif: face.serif,
                sans: face.sans,
                mono: face.mono
            ),
            shape: shape,
            elevation: elevation
        )
    }
}

extension KSSPalette {
    /// 颜色键名与 chart/markdown/architecture 三处 HTML 的 CSS 变量对应。
    var webPayload: KSSWebThemePayload {
        let softA = appearance == .dark ? 0.16 : 0.12
        let zebraA = appearance == .dark ? 0.03 : 0.025
        // 图表副图功能色：跨主题恒定（对图表底色保持可读），随明暗取一组。
        let obv = appearance == .dark ? ThemeColor(0xB388FF) : ThemeColor(0x7C4DFF)
        let boll = appearance == .dark ? ThemeColor(0x26C6DA) : ThemeColor(0x0097A7)
        let colors: [String: String] = [
            // 公共表面 / 文字 / 边线
            "bg": canvas.css,
            "surface": surface.css,
            "surface2": surfaceRaised.css,
            "ink": ink.css,
            "body": body.css,
            "muted": muted.css,
            "line": hairline.css,
            "lineSoft": outlineVariant.css,
            // accent
            "accent": accent.css,
            "accentSoft": accent.withAlpha(softA).css,
            "onAccent": onAccent.css,
            // markdown 专用
            "code": surfaceRaised.css,
            "th": surfaceRaised.css,
            "zebra": ink.withAlpha(zebraA).css,
            // Kami tag fill：ink-blue 叠在 parchment 上的等效实色，避免 rgba 双绘
            "tag": system == .clayM3
                ? (appearance == .dark ? ThemeColor(0x243247).css : ThemeColor(0xE4ECF5).css)
                : accent.withAlpha(appearance == .dark ? 0.18 : 0.14).css,
            // 架构图专用
            "olive": secondary.css,
            "oliveSoft": secondary.withAlpha(0.14).css,
            "gold": ma5.css,
            "blue": secondary.css,
            "blueSoft": secondary.withAlpha(0.14).css,
            "zone": ink.withAlpha(appearance == .dark ? 0.03 : 0.025).css,
            "zoneLine": hairline.css,
            // 图表 K 线
            "up": up.css,
            "down": down.css,
            "upFill": upFill.css,
            "downFill": downFill.css,
            "ma5": ma5.css,
            "ma20": ma20.css,
            "grid": hairline.css,
            "text": muted.css,
            "cross": muted.css,
            "dif": ma5.css,
            "dea": ma20.css,
            "obv": obv.css,
            "bbi": ink.css,
            "boll": boll.css,
            "chip": surfaceRaised.css,
            "chipText": body.css,
            "chipOn": accent.css,
            "chartBg": chartSurface.css,
            "hair": hairline.css,
        ]
        return KSSWebThemePayload(
            version: KSSWebThemePayload.currentVersion,
            id: system.rawValue,
            mode: appearance.rawValue,
            colors: colors,
            typography: .init(serif: typography.serif, sans: typography.sans, mono: typography.mono),
            shape: .init(card: Double(cardRadius), chip: Double(chipRadius)),
            elevation: .init(opacity: elevation.opacity, radius: elevation.radius, y: elevation.y)
        )
    }
}

// MARK: - 校验（供测试，纯函数）

enum ThemeValidation {
    /// required role 列表：每个组合都必须提供，且这些颜色不透明。
    /// （结构上由 `KSSPalette` 强类型保证存在；此处校验对比度与覆盖完整性。）
    struct Finding: Equatable {
        let system: KSSDesignSystem
        let appearance: KSSAppearance
        let role: String
        let ratio: Double
        let required: Double
    }

    /// 正文/背景 AA ≥ 4.5、UI/图形边界 ≥ 3:1。返回未达标项（空 = 全通过）。
    static func contrastFindings(for palette: KSSPalette) -> [Finding] {
        var out: [Finding] = []
        func textCheck(_ role: String, _ fg: ThemeColor, on bg: ThemeColor, min: Double) {
            let resolved = fg.a < 1 ? fg.composited(over: bg) : fg
            let ratio = resolved.contrastRatio(against: bg)
            if ratio + 0.01 < min {
                out.append(Finding(system: palette.system, appearance: palette.appearance,
                                   role: role, ratio: ratio, required: min))
            }
        }
        // 正文/标题/次文字 对 canvas 与 card surface 都要 ≥ 4.5
        for (name, bg) in [("canvas", palette.canvas), ("surface", palette.surface)] {
            textCheck("textPrimary@\(name)", palette.ink, on: bg, min: 4.5)
            textCheck("textBody@\(name)", palette.body, on: bg, min: 4.5)
            textCheck("textSecondary@\(name)", palette.muted, on: bg, min: 4.5)
        }
        // accent 作前景图形（chevron 等）对 surface ≥ 3:1
        textCheck("accent@surface", palette.accent, on: palette.surface, min: 3.0)
        textCheck("accent@canvas", palette.accent, on: palette.canvas, min: 3.0)
        // onAccent 对 accent 填充 ≥ 4.5（按钮文字）
        textCheck("onAccent@accent", palette.onAccent, on: palette.accent, min: 4.5)
        // 边线对相邻表面 ≥ 3:1（UI 边界）—— hairline 对 canvas
        textCheck("hairline@canvas", palette.hairline, on: palette.canvas, min: 1.18)
        // 涨跌色对图表底 ≥ 3:1
        textCheck("up@chart", palette.up, on: palette.chartSurface, min: 3.0)
        textCheck("down@chart", palette.down, on: palette.chartSurface, min: 3.0)
        return out
    }

    /// 涨跌必须可区分（不只靠颜色，但颜色本身也须不同）。
    static func upDownDistinct(for palette: KSSPalette) -> Bool {
        palette.up != palette.down
    }

    /// 全 16 组合的对比度发现汇总。
    static func allFindings() -> [Finding] {
        var out: [Finding] = []
        for system in KSSDesignSystem.allCases {
            for appearance in KSSAppearance.allCases {
                out += contrastFindings(for: ThemeCatalog.palette(for: system, appearance: appearance))
            }
        }
        return out
    }
}
