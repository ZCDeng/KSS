import SwiftUI
import AppKit

// MARK: - 设计系统 / 外观

/// 8 套「经典」可切换设计系统 + 1 套「新版」xcom 的稳定 raw id（持久化到 UserDefaults，不可随意改名）。
/// `clayM3` 是旧安装缺失 `designSystemId` 时的迁移默认；`material3` 专指
/// showcase 的成对紫色 M3 token，两者来源独立、不得 alias/fallback 互相派生。
/// `xcom` 是新版设计语言，走独立的顶层「新版/经典版」菜单入口（见 KSSUIGeneration），
/// 不在经典版的 8 项子菜单里出现。
enum KSSDesignSystem: String, CaseIterable, Identifiable, Hashable {
    case clayM3
    case tradingTerminal
    case skeuomorphism
    case material3
    case theVerge
    case airbnb
    case discord
    case binanceUS
    case xcom

    var id: String { rawValue }

    /// 缺失/非法 id 回退 clayM3（保留旧安装当前暖纸视觉）。
    static func normalized(_ raw: String?) -> KSSDesignSystem {
        guard let raw, let system = KSSDesignSystem(rawValue: raw) else { return .clayM3 }
        return system
    }

    /// 经典版工具栏子菜单只展示这 8 套，不含 `xcom`（xcom 走独立顶层入口）。
    static var classicCases: [KSSDesignSystem] { allCases.filter { $0 != .xcom } }

    var displayName: String {
        switch self {
        case .clayM3:          return "KSS 暖纸"
        case .tradingTerminal: return "交易终端"
        case .skeuomorphism:   return "拟物"
        case .material3:       return "Material 3"
        case .theVerge:        return "The Verge"
        case .airbnb:          return "Airbnb"
        case .discord:         return "Discord"
        case .binanceUS:       return "Binance.US"
        case .xcom:            return "x.com"
        }
    }
}

/// 顶层「新版/经典版」模式开关。`xcom` 复用同一个 `KSSDesignSystem` 枚举的第 9 个 case，
/// 但工具栏菜单结构与侧边栏导航视觉按这个更粗的维度分支（见 ThemeController、ContentView、SidebarView）。
enum KSSUIGeneration: String, CaseIterable, Identifiable, Hashable {
    case classic
    case xcom

    var id: String { rawValue }
    var displayName: String { self == .xcom ? "新版 x.com" : "经典版" }

    static func normalized(_ raw: String?) -> KSSUIGeneration {
        guard let raw, let generation = KSSUIGeneration(rawValue: raw) else { return .classic }
        return generation
    }
}

/// 每套设计系统的亮/暗两态；移除了「跟随系统」第三态。
enum KSSAppearance: String, CaseIterable, Identifiable, Hashable {
    case light
    case dark

    var id: String { rawValue }
    var colorScheme: ColorScheme { self == .dark ? .dark : .light }
    var displayName: String { self == .dark ? "暗色" : "亮色" }

    /// 缺失/非法/遗留 `system` 一律规范化为 `dark`（有意行为迁移，见计划 ADR）。
    static func normalized(_ raw: String?) -> KSSAppearance {
        guard let raw, let appearance = KSSAppearance(rawValue: raw) else { return .dark }
        return appearance
    }
}

// MARK: - 原始颜色值

/// sRGB 原始颜色（可审查 hex + alpha）；同时供 SwiftUI、AppKit 与 CSS 消费。
struct ThemeColor: Equatable {
    let r: Double   // 0...1
    let g: Double
    let b: Double
    let a: Double

    init(_ hex: UInt, alpha: Double = 1) {
        self.r = Double((hex >> 16) & 0xFF) / 255
        self.g = Double((hex >> 8) & 0xFF) / 255
        self.b = Double(hex & 0xFF) / 255
        self.a = alpha
    }

    private init(r: Double, g: Double, b: Double, a: Double) {
        self.r = r; self.g = g; self.b = b; self.a = a
    }

    func withAlpha(_ alpha: Double) -> ThemeColor {
        ThemeColor(r: r, g: g, b: b, a: alpha)
    }

    var color: Color { Color(.sRGB, red: r, green: g, blue: b, opacity: a) }
    var nsColor: NSColor { NSColor(srgbRed: r, green: g, blue: b, alpha: a) }

    /// CSS 安全值：不透明走 `#RRGGBB`，半透明走 `rgba(...)`，供 JSON bridge 直接注入 CSS 变量。
    var css: String {
        let ri = Int((r * 255).rounded()), gi = Int((g * 255).rounded()), bi = Int((b * 255).rounded())
        if a >= 1 {
            return String(format: "#%02X%02X%02X", ri, gi, bi)
        }
        return "rgba(\(ri),\(gi),\(bi),\(String(format: "%.3f", a)))"
    }

    // WCAG 相对亮度（sRGB → 线性）。
    private func lin(_ c: Double) -> Double {
        c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
    }
    var relativeLuminance: Double {
        0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    }

    /// 与另一不透明色的 WCAG 对比度（要求 self.a == other.a == 1 才有意义；
    /// 半透明色须先 `composited(over:)` 再比较）。
    func contrastRatio(against other: ThemeColor) -> Double {
        let l1 = relativeLuminance, l2 = other.relativeLuminance
        let hi = max(l1, l2), lo = min(l1, l2)
        return (hi + 0.05) / (lo + 0.05)
    }

    /// 把半透明 self 合成到不透明背景上，得到等效不透明色（用于审查 soft fill / zebra 的真实对比）。
    func composited(over bg: ThemeColor) -> ThemeColor {
        let outR = r * a + bg.r * (1 - a)
        let outG = g * a + bg.g * (1 - a)
        let outB = b * a + bg.b * (1 - a)
        return ThemeColor(r: outR, g: outG, b: outB, a: 1)
    }
}

// MARK: - 字体策略

/// 受限的字体语义 id（serif / sans / mono），原生与 WebView 共用同一策略。
struct ThemeTypography: Equatable {
    let serif: String   // 标题字体族（CSS font-family）
    let sans: String    // 正文 / UI
    let mono: String    // 数字 / 等宽

    /// 原生标题用的 SwiftUI Font.Design（serif 主题用 serif，等宽主题用 monospaced）。
    var titleDesign: Font.Design

    /// 原生自定义字体家族（PostScript 名前缀，如 "Chirp"）；nil = 8 套经典主题原样走系统字体。
    /// 非 nil 时 `KSSFont.themed(_:_:theme:)` 按 weight 分桶取 "<family>-<Weight>" PostScript 名。
    var nativeFontFamily: String? = nil
    /// 中文字形级联回退的 PostScript 名（如 "TsangerJinKai02-W02"），仅在 nativeFontFamily 非 nil 时生效。
    var nativeCJKFallback: String? = nil

    static let claySerif = ThemeTypography(
        serif: "ui-serif, Georgia, \"Songti SC\", serif",
        sans: "-apple-system, \"SF Pro Text\", \"PingFang SC\", sans-serif",
        mono: "ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .serif
    )
    static let pureSans = ThemeTypography(
        serif: "system-ui, -apple-system, \"PingFang SC\", sans-serif",
        sans: "system-ui, -apple-system, \"PingFang SC\", sans-serif",
        mono: "ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .default
    )
    static let mono = ThemeTypography(
        serif: "\"JetBrains Mono\", ui-monospace, \"SF Mono\", Menlo, monospace",
        sans: "\"JetBrains Mono\", ui-monospace, \"SF Mono\", Menlo, monospace",
        mono: "\"JetBrains Mono\", ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .monospaced
    )
    static let material = ThemeTypography(
        serif: "\"Roboto\", system-ui, sans-serif",
        sans: "\"Roboto\", system-ui, sans-serif",
        mono: "ui-monospace, \"Roboto Mono\", monospace",
        titleDesign: .default
    )
    /// x.com 模式:英文走 Chirp,中文走仓耳今楷,WebView 侧 CSS 字体栈同顺序级联。
    static let xcomChirp = ThemeTypography(
        serif: "\"Chirp\", -apple-system, \"TsangerJinKai02\", sans-serif",
        sans: "\"Chirp\", -apple-system, \"TsangerJinKai02\", sans-serif",
        mono: "ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .default,
        nativeFontFamily: "Chirp",
        nativeCJKFallback: "TsangerJinKai02-W02"
    )
}

// MARK: - 阴影 / 几何

struct ThemeElevation: Equatable {
    let opacity: Double   // 主阴影 opacity（elevated 卡）
    let radius: Double
    let y: Double
}

// MARK: - 调色板（每套主题 × 亮/暗一组）

/// 单组调色板：所有 required role 的不透明原始色 + 几何 + 字体 + 阴影。
/// up/down/ma 等市场/图表功能色由 appearance 固定常量提供，不随设计系统漂移。
struct KSSPalette: Equatable {
    let system: KSSDesignSystem
    let appearance: KSSAppearance

    // 表面层级
    let canvas: ThemeColor
    let surfaceLowest: ThemeColor
    let surface: ThemeColor
    let surfaceContainer: ThemeColor
    let surfaceRaised: ThemeColor
    let surfaceHighest: ThemeColor
    let surfaceTint: ThemeColor
    let chartSurface: ThemeColor
    // 边线
    let hairline: ThemeColor
    let outlineVariant: ThemeColor
    // 文字
    let ink: ThemeColor       // textPrimary
    let body: ThemeColor      // textBody
    let muted: ThemeColor     // textSecondary
    // accent
    let accent: ThemeColor
    let onAccent: ThemeColor
    let secondary: ThemeColor  // 架构图 olive/blue 等次强调
    // 市场 / 图表功能色（appearance 固定）
    let up: ThemeColor
    let down: ThemeColor
    let upFill: ThemeColor
    let downFill: ThemeColor
    let ma5: ThemeColor
    let ma20: ThemeColor
    // 几何
    let cardRadius: CGFloat
    let chipRadius: CGFloat
    // 字体 / 阴影
    let typography: ThemeTypography
    let elevation: ThemeElevation
}

// MARK: - 目录

enum ThemeCatalog {
    /// 16 个组合的唯一入口。
    static func palette(for system: KSSDesignSystem, appearance: KSSAppearance) -> KSSPalette {
        let seed = Self.seed(system, appearance)
        let market = MarketColors.forAppearance(appearance)
        return KSSPalette(
            system: system, appearance: appearance,
            canvas: seed.canvas, surfaceLowest: seed.surfaceLowest, surface: seed.surface,
            surfaceContainer: seed.surfaceContainer, surfaceRaised: seed.surfaceRaised,
            surfaceHighest: seed.surfaceHighest, surfaceTint: seed.surfaceTint,
            chartSurface: seed.chartSurface,
            hairline: seed.hairline, outlineVariant: seed.outlineVariant,
            ink: seed.ink, body: seed.body, muted: seed.muted,
            accent: seed.accent, onAccent: seed.onAccent, secondary: seed.secondary,
            up: market.up, down: market.down, upFill: market.upFill, downFill: market.downFill,
            ma5: market.ma5, ma20: market.ma20,
            cardRadius: seed.cardRadius, chipRadius: seed.chipRadius,
            typography: seed.typography, elevation: seed.elevation
        )
    }

    // 红涨绿跌 + 图表均线：A股语义固定，所有主题共享，不蹭设计系统 accent。
    private struct MarketColors {
        let up, down, upFill, downFill, ma5, ma20: ThemeColor
        static func forAppearance(_ a: KSSAppearance) -> MarketColors {
            switch a {
            case .light:
                return MarketColors(
                    up: ThemeColor(0xF23645), down: ThemeColor(0x089981),
                    upFill: ThemeColor(0xF23645, alpha: 0.40), downFill: ThemeColor(0x089981, alpha: 0.40),
                    ma5: ThemeColor(0xF5A623), ma20: ThemeColor(0x2962FF))
            case .dark:
                return MarketColors(
                    up: ThemeColor(0xF6465D), down: ThemeColor(0x2EBD85),
                    upFill: ThemeColor(0xF6465D, alpha: 0.50), downFill: ThemeColor(0x2EBD85, alpha: 0.50),
                    ma5: ThemeColor(0xFF9F1C), ma20: ThemeColor(0x4C82FB))
            }
        }
    }

    private struct Seed {
        var canvas, surfaceLowest, surface, surfaceContainer, surfaceRaised, surfaceHighest, surfaceTint, chartSurface: ThemeColor
        var hairline, outlineVariant, ink, body, muted, accent, onAccent, secondary: ThemeColor
        var cardRadius: CGFloat
        var chipRadius: CGFloat
        var typography: ThemeTypography
        var elevation: ThemeElevation
    }

    // swiftlint:disable function_body_length
    private static func seed(_ system: KSSDesignSystem, _ ap: KSSAppearance) -> Seed {
        let dark = ap == .dark
        switch system {

        // 1. KSS 暖纸 / clay（迁自 Theme.swift，保持现状视觉连续性）
        case .clayM3:
            return dark
            ? Seed(canvas: c(0x141413), surfaceLowest: c(0x100F0E), surface: c(0x1D1B19),
                   surfaceContainer: c(0x232120), surfaceRaised: c(0x2C2926), surfaceHighest: c(0x363230),
                   surfaceTint: c(0xE48A6E), chartSurface: c(0x1D1B19),
                   hairline: c(0x3D3D3A), outlineVariant: c(0x35332F),
                   ink: c(0xFAF9F5), body: c(0xD1CFC5), muted: c(0x8A8980),
                   accent: c(0xE48A6E), onAccent: c(0x141413), secondary: c(0x9DB07C),
                   cardRadius: 12, chipRadius: 7, typography: .claySerif,
                   elevation: ThemeElevation(opacity: 0.10, radius: 3, y: 1))
            : Seed(canvas: c(0xFAF9F5), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF6F4EC), surfaceRaised: c(0xEFEDE4), surfaceHighest: c(0xE8E4D8),
                   surfaceTint: c(0xD97757), chartSurface: c(0xFFFFFF),
                   hairline: c(0xD1CFC5), outlineVariant: c(0xDED9CC),
                   ink: c(0x141413), body: c(0x3D3D3A), muted: c(0x6F6E68),
                   accent: c(0xAF5230), onAccent: c(0xFFFFFF), secondary: c(0x5C7142),
                   cardRadius: 12, chipRadius: 7, typography: .claySerif,
                   elevation: ThemeElevation(opacity: 0.10, radius: 3, y: 1))

        // 2. 交易终端：近黑 + 青绿，等宽，硬朗
        case .tradingTerminal:
            return dark
            ? Seed(canvas: c(0x0A0A0A), surfaceLowest: c(0x000000), surface: c(0x141414),
                   surfaceContainer: c(0x1A1A1A), surfaceRaised: c(0x222222), surfaceHighest: c(0x2A2A2A),
                   surfaceTint: c(0x00D4AA), chartSurface: c(0x141414),
                   hairline: c(0x2A2A2A), outlineVariant: c(0x202020),
                   ink: c(0xF5F5F5), body: c(0xC8C8C8), muted: c(0x8C8C8C),
                   accent: c(0x00D4AA), onAccent: c(0x00140F), secondary: c(0x4C82FB),
                   cardRadius: 4, chipRadius: 4, typography: .mono,
                   elevation: ThemeElevation(opacity: 0, radius: 0, y: 0))
            : Seed(canvas: c(0xF4F5F5), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF0F1F1), surfaceRaised: c(0xE8EAEA), surfaceHighest: c(0xE0E2E2),
                   surfaceTint: c(0x008F73), chartSurface: c(0xFFFFFF),
                   hairline: c(0xCDD0D0), outlineVariant: c(0xDADCDC),
                   ink: c(0x0A0A0A), body: c(0x2A2A2A), muted: c(0x5A5A5A),
                   accent: c(0x00866C), onAccent: c(0xFFFFFF), secondary: c(0x2962FF),
                   cardRadius: 4, chipRadius: 4, typography: .mono,
                   elevation: ThemeElevation(opacity: 0.05, radius: 2, y: 1))

        // 3. 拟物：橙色 + 实体层次 + 明显高光阴影
        case .skeuomorphism:
            return dark
            ? Seed(canvas: c(0x1C1714), surfaceLowest: c(0x141010), surface: c(0x241D18),
                   surfaceContainer: c(0x2A221C), surfaceRaised: c(0x332A22), surfaceHighest: c(0x3D332A),
                   surfaceTint: c(0xFF6A3D), chartSurface: c(0x241D18),
                   hairline: c(0x3D332A), outlineVariant: c(0x33291F),
                   ink: c(0xF7EFE9), body: c(0xD8CBBF), muted: c(0xA89A8C),
                   accent: c(0xFF6A3D), onAccent: c(0x1C1714), secondary: c(0xF0A321),
                   cardRadius: 14, chipRadius: 9, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.42, radius: 9, y: 4))
            : Seed(canvas: c(0xF2EFEA), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF7F4EF), surfaceRaised: c(0xEFEBE3), surfaceHighest: c(0xE7E2D8),
                   surfaceTint: c(0xFA3C00), chartSurface: c(0xFFFFFF),
                   hairline: c(0xD8D2C6), outlineVariant: c(0xE2DDD2),
                   ink: c(0x2B1D16), body: c(0x4A3C33), muted: c(0x756357),
                   accent: c(0xD83400), onAccent: c(0xFFFFFF), secondary: c(0xB45E0A),
                   cardRadius: 14, chipRadius: 9, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.18, radius: 10, y: 4))

        // 4. Material 3：showcase 紫色成对 token
        case .material3:
            return dark
            ? Seed(canvas: c(0x141218), surfaceLowest: c(0x0F0D13), surface: c(0x1D1B20),
                   surfaceContainer: c(0x211F26), surfaceRaised: c(0x2B2930), surfaceHighest: c(0x36343B),
                   surfaceTint: c(0xD0BCFF), chartSurface: c(0x141218),
                   hairline: c(0x49454F), outlineVariant: c(0x49454F),
                   ink: c(0xE6E0E9), body: c(0xCAC4D0), muted: c(0x938F99),
                   accent: c(0xD0BCFF), onAccent: c(0x381E72), secondary: c(0xCCC2DC),
                   cardRadius: 12, chipRadius: 8, typography: .material,
                   elevation: ThemeElevation(opacity: 0.30, radius: 3, y: 1))
            : Seed(canvas: c(0xFEF7FF), surfaceLowest: c(0xFFFFFF), surface: c(0xF7F2FA),
                   surfaceContainer: c(0xF3EDF7), surfaceRaised: c(0xECE6F0), surfaceHighest: c(0xE6E0E9),
                   surfaceTint: c(0x6750A4), chartSurface: c(0xFFFFFF),
                   hairline: c(0xCAC4D0), outlineVariant: c(0xC9C5D0),
                   ink: c(0x1D1B20), body: c(0x49454F), muted: c(0x615C68),
                   accent: c(0x6750A4), onAccent: c(0xFFFFFF), secondary: c(0x625B71),
                   cardRadius: 12, chipRadius: 8, typography: .material,
                   elevation: ThemeElevation(opacity: 0.18, radius: 3, y: 1))

        // 5. The Verge：深灰 + 酸性 mint + 紫色边线。亮色用紫做 accent（mint 在白底失对比）。
        case .theVerge:
            return dark
            ? Seed(canvas: c(0x1B1B1B), surfaceLowest: c(0x101010), surface: c(0x232323),
                   surfaceContainer: c(0x282828), surfaceRaised: c(0x2F2F2F), surfaceHighest: c(0x383838),
                   surfaceTint: c(0x3CFFD0), chartSurface: c(0x232323),
                   hairline: c(0x4324A8), outlineVariant: c(0x2F2F2F),
                   ink: c(0xFFFFFF), body: c(0xC8C8C8), muted: c(0x949494),
                   accent: c(0x3CFFD0), onAccent: c(0x0A0A0A), secondary: c(0x9A7BFF),
                   cardRadius: 6, chipRadius: 6, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0, radius: 0, y: 0))
            : Seed(canvas: c(0xF4F4F5), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF0F0F2), surfaceRaised: c(0xE9E9EC), surfaceHighest: c(0xE1E1E6),
                   surfaceTint: c(0x5200FF), chartSurface: c(0xFFFFFF),
                   hairline: c(0xD3D0E2), outlineVariant: c(0xDEDCEC),
                   ink: c(0x131313), body: c(0x2E2E2E), muted: c(0x5C5C5C),
                   accent: c(0x5200FF), onAccent: c(0xFFFFFF), secondary: c(0x009E80),
                   cardRadius: 6, chipRadius: 6, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.10, radius: 4, y: 2))

        // 6. Airbnb：通透中性 + 酒红/珊瑚，充足留白，柔和大圆角
        case .airbnb:
            return dark
            ? Seed(canvas: c(0x1A1A1A), surfaceLowest: c(0x121212), surface: c(0x212121),
                   surfaceContainer: c(0x262626), surfaceRaised: c(0x2E2E2E), surfaceHighest: c(0x363636),
                   surfaceTint: c(0xFF5A7A), chartSurface: c(0x212121),
                   hairline: c(0x383838), outlineVariant: c(0x2E2E2E),
                   ink: c(0xF7F7F7), body: c(0xD0D0D0), muted: c(0xA0A0A0),
                   accent: c(0xFF5A7A), onAccent: c(0x1A1A1A), secondary: c(0xE07A99),
                   cardRadius: 16, chipRadius: 10, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.22, radius: 10, y: 3))
            : Seed(canvas: c(0xFFFFFF), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF7F7F7), surfaceRaised: c(0xF0F0F0), surfaceHighest: c(0xEBEBEB),
                   surfaceTint: c(0xE00B41), chartSurface: c(0xFFFFFF),
                   hairline: c(0xDDDDDD), outlineVariant: c(0xEBEBEB),
                   ink: c(0x222222), body: c(0x484848), muted: c(0x6F6F6F),
                   accent: c(0xC2185B), onAccent: c(0xFFFFFF), secondary: c(0x8B1A47),
                   cardRadius: 16, chipRadius: 10, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.10, radius: 8, y: 2))

        // 7. Discord：石墨 + blurple + 社区式分层容器
        case .discord:
            return dark
            ? Seed(canvas: c(0x1E1F22), surfaceLowest: c(0x141517), surface: c(0x2B2D31),
                   surfaceContainer: c(0x313338), surfaceRaised: c(0x383A40), surfaceHighest: c(0x404249),
                   surfaceTint: c(0x5865F2), chartSurface: c(0x2B2D31),
                   hairline: c(0x3F4147), outlineVariant: c(0x35373C),
                   ink: c(0xF2F3F5), body: c(0xDBDEE1), muted: c(0x949BA4),
                   accent: c(0x5865F2), onAccent: c(0xFFFFFF), secondary: c(0x3BA55D),
                   cardRadius: 8, chipRadius: 6, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.20, radius: 4, y: 1))
            : Seed(canvas: c(0xF2F3F5), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xEBEDEF), surfaceRaised: c(0xE3E5E8), surfaceHighest: c(0xDBDDE1),
                   surfaceTint: c(0x5865F2), chartSurface: c(0xFFFFFF),
                   hairline: c(0xDCDEE1), outlineVariant: c(0xE3E5E8),
                   ink: c(0x060607), body: c(0x2E3338), muted: c(0x4E5058),
                   accent: c(0x4752C4), onAccent: c(0xFFFFFF), secondary: c(0x1A7A3F),
                   cardRadius: 8, chipRadius: 6, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.10, radius: 4, y: 1))

        // 8. Binance.US：交易所黄 + 石墨。黄仅作 fill；亮色 accent 用深琥珀承担对比。
        case .binanceUS:
            return dark
            ? Seed(canvas: c(0x0B0E11), surfaceLowest: c(0x000000), surface: c(0x1E2026),
                   surfaceContainer: c(0x252830), surfaceRaised: c(0x2B3139), surfaceHighest: c(0x363C46),
                   surfaceTint: c(0xF0B90B), chartSurface: c(0x1E2026),
                   hairline: c(0x2B3139), outlineVariant: c(0x252830),
                   ink: c(0xEAECEF), body: c(0xB7BDC6), muted: c(0x848E9C),
                   accent: c(0xF0B90B), onAccent: c(0x1E2026), secondary: c(0x848E9C),
                   cardRadius: 6, chipRadius: 6, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.16, radius: 4, y: 1))
            : Seed(canvas: c(0xFAFAFA), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF5F5F5), surfaceRaised: c(0xEEEEEE), surfaceHighest: c(0xE6E8EA),
                   surfaceTint: c(0xF0B90B), chartSurface: c(0xFFFFFF),
                   hairline: c(0xE6E8EA), outlineVariant: c(0xEAECEF),
                   ink: c(0x1E2026), body: c(0x474D57), muted: c(0x636B78),
                   accent: c(0x8A6300), onAccent: c(0xFFFFFF), secondary: c(0x5E6673),
                   cardRadius: 6, chipRadius: 6, typography: .pureSans,
                   elevation: ThemeElevation(opacity: 0.08, radius: 3, y: 1))

        // 9. x.com「新版」：近纯黑/白画布 + 单一品牌蓝 + hairline,零阴影(flat by design)。
        //    up/down/ma5/ma20 不在此覆盖,走下面共享的 MarketColors(与其余 8 套一致)。
        case .xcom:
            return dark
            ? Seed(canvas: c(0x000000), surfaceLowest: c(0x000000), surface: c(0x16181C),
                   surfaceContainer: c(0x16181C), surfaceRaised: c(0x1E2732), surfaceHighest: c(0x273340),
                   surfaceTint: c(0x1D9BF0), chartSurface: c(0x000000),
                   hairline: c(0x2F3336), outlineVariant: c(0x2F3336),
                   ink: c(0xE7E9EA), body: c(0xE7E9EA), muted: c(0x71767B),
                   accent: c(0x1D9BF0), onAccent: c(0xFFFFFF), secondary: c(0x8B98A5),
                   cardRadius: 16, chipRadius: 999, typography: .xcomChirp,
                   elevation: ThemeElevation(opacity: 0, radius: 0, y: 0))
            : Seed(canvas: c(0xFFFFFF), surfaceLowest: c(0xFFFFFF), surface: c(0xF7F9F9),
                   surfaceContainer: c(0xF7F9F9), surfaceRaised: c(0xEFF3F4), surfaceHighest: c(0xE5E8E8),
                   surfaceTint: c(0x1D9BF0), chartSurface: c(0xFFFFFF),
                   hairline: c(0xEFF3F4), outlineVariant: c(0xEFF3F4),
                   ink: c(0x0F1419), body: c(0x0F1419), muted: c(0x536471),
                   accent: c(0x1D9BF0), onAccent: c(0xFFFFFF), secondary: c(0x536471),
                   cardRadius: 16, chipRadius: 999, typography: .xcomChirp,
                   elevation: ThemeElevation(opacity: 0, radius: 0, y: 0))
        }
    }
    // swiftlint:enable function_body_length

    private static func c(_ hex: UInt) -> ThemeColor { ThemeColor(hex) }
}
