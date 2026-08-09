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
    /// 中文字形级联回退的字族前缀（如 "HarmonyOS_Sans_SC"）；仅在 nativeFontFamily 非 nil 时生效。
    /// `KSSFont.themed(_:_:theme:)` 按 weight 分桶取对应粗细档，粗细跟随请求的 weight（而非固定死一档），
    /// 避免正文/侧边栏这些非标题场景也被强制显示成粗体中文。
    var nativeCJKFamily: String? = nil

    /// 暖纸 UI 壳仍可用系统/仓耳；**内容阅读皮**用 `contentPrint`。
    static let claySerif = ThemeTypography(
        serif: "\"TsangerJinKai02\", Charter, Georgia, \"Songti SC\", serif",
        sans: "-apple-system, \"SF Pro Text\", \"PingFang SC\", sans-serif",
        mono: "ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .serif
    )
    /// 报告/资讯/对话交付物：Chiron GoRound TC（Google Fonts 同款，离线 ttf）。
    static let contentPrint = ThemeTypography(
        serif: "\"Chiron GoRound TC\", \"PingFang SC\", sans-serif",
        sans: "\"Chiron GoRound TC\", \"PingFang SC\", sans-serif",
        mono: "ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .default
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
    /// x.com 模式:英文走 Chirp(无中文字形),中文级联到 HarmonyOS Sans SC,WebView 侧 CSS 字体栈同顺序级联。
    static let xcomChirp = ThemeTypography(
        // HarmonyOS Sans SC 紧跟 Chirp 之后、系统泛型字体之前:WebKit 的 -apple-system 对
        // 不在 Chirp 覆盖范围内的字符(含中文)会先走自己内部的系统级联(通常落到苹方),
        // 若排在 HarmonyOS Sans SC 之前,CSS 引擎会认为它"已经有这个字形"而不再往后找,
        // 导致中文永远轮不到 HarmonyOS Sans SC。
        serif: "\"Chirp\", \"HarmonyOS Sans SC\", -apple-system, sans-serif",
        sans: "\"Chirp\", \"HarmonyOS Sans SC\", -apple-system, sans-serif",
        mono: "ui-monospace, \"SF Mono\", Menlo, monospace",
        titleDesign: .default,
        nativeFontFamily: "Chirp",
        nativeCJKFamily: "HarmonyOS_Sans_SC"
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
    // 可投资地图暴露色（plan 2026-08-09-001 U4 / KTD6）：五个行业色逐设计系统逐外观定值。
    // 未定色按外观固定；未上图灰点复用 muted，未核节点复用 outlineVariant 做虚线描边
    // ——未核是描边不是填充，否则会盖掉节点主色，泳道按色排序立刻失去依据。
    let exposureDeepGreen: ThemeColor
    let exposureLightGreen: ThemeColor
    let exposureYellow: ThemeColor
    let exposureOrange: ThemeColor
    let exposurePurple: ThemeColor
    let exposurePending: ThemeColor
    /// 个股红区专用红（plan A1：红是个股区位，不是行业色）。
    /// 与涨跌的 `up` 分开：同屏出现「涨」和「红区」两种红会互相冒充。跨设计系统共享、
    /// 只按外观分，语义与 `MarketColors` 同级——它是警示色，不是品牌色。
    let exposureRed: ThemeColor
    // 几何
    let cardRadius: CGFloat
    let chipRadius: CGFloat
    // 字体 / 阴影
    let typography: ThemeTypography
    let elevation: ThemeElevation
}

extension KSSPalette {
    /// 桥接返回的行业色机器键 → 本 palette 的取值。
    ///
    /// 桥接层只给 key（`deep_green` 这种），不给色值——颜色是外观相关的，
    /// 判定与渲染分居两侧（plan KTD2）。未标注个股用 `muted`，未核节点用
    /// `outlineVariant` 做虚线描边，两者都不走这个映射。
    func exposureColor(forKey key: String) -> ThemeColor? {
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

    /// 五个行业色，按色板声明顺序。
    var exposureIndustryColors: [(key: String, color: ThemeColor)] {
        [
            ("deep_green", exposureDeepGreen),
            ("light_green", exposureLightGreen),
            ("yellow", exposureYellow),
            ("orange", exposureOrange),
            ("purple", exposurePurple),
        ]
    }
}

// MARK: - 目录

enum ThemeCatalog {
    /// 16 个组合的唯一入口。
    static func palette(for system: KSSDesignSystem, appearance: KSSAppearance) -> KSSPalette {
        let seed = Self.seed(system, appearance)
        let market = MarketColors.forAppearance(appearance)
        let exposure = ExposureColors.forSystem(system, appearance)
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
            exposureDeepGreen: exposure.deepGreen, exposureLightGreen: exposure.lightGreen,
            exposureYellow: exposure.yellow, exposureOrange: exposure.orange,
            exposurePurple: exposure.purple, exposurePending: exposure.pending,
            exposureRed: ExposureRed.forAppearance(appearance),
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

    // 可投资地图五色（plan U4）。语义固定、取值逐设计系统调，与 MarketColors 的
    // 「外观固定、跨系统共享」不同 —— 那是 KTD6 明确否掉的形状。
    //
    // 定值约束（由 ThemeCatalogTests 机检，不靠手感）：
    // - 同一 palette 内任意两个行业色 CIE76 色差 ≥ 25，堵住深绿对浅绿、黄对橙糊在一起
    // - 每个色对该 palette 的 surface 对比度 ≥ 3:1
    // - 未定色与 muted 色差 ≥ 15，避免「待定色」被看成「未上图」
    /// 个股红区红。跨设计系统共享、只按外观分，同 `MarketColors` 的处理。
    /// 取值按「白字铺在它上面要过 4.5:1，且它自己对 surface 要过 3:1」选，
    /// 直接借 `up`（#F23645 / #F6465D）做填充时白字只有 3.6:1，不够。
    private enum ExposureRed {
        static func forAppearance(_ a: KSSAppearance) -> ThemeColor {
            a == .light ? ThemeColor(0xB3261E) : ThemeColor(0xC0392B)
        }
    }

    private struct ExposureColors {
        let deepGreen, lightGreen, yellow, orange, purple, pending: ThemeColor

        // swiftlint:disable:next function_body_length
        static func forSystem(_ s: KSSDesignSystem, _ a: KSSAppearance) -> ExposureColors {
            let dark = a == .dark
            switch s {
            case .clayM3:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x56A47C), lightGreen: ThemeColor(0xABEDC4), yellow: ThemeColor(0xE0B25A),
                                 orange: ThemeColor(0xF1854A), purple: ThemeColor(0xB79BF0), pending: ThemeColor(0x8FA3B8))
                : ExposureColors(deepGreen: ThemeColor(0x134E2A), lightGreen: ThemeColor(0x37905B), yellow: ThemeColor(0x8F6A00),
                                 orange: ThemeColor(0xB8460F), purple: ThemeColor(0x6B3FA0), pending: ThemeColor(0x7A8CA0))
            case .tradingTerminal:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x3FA88A), lightGreen: ThemeColor(0x5CEFC0), yellow: ThemeColor(0xE4C05A),
                                 orange: ThemeColor(0xFF8A3D), purple: ThemeColor(0xA896F5), pending: ThemeColor(0xD6C8B4))
                : ExposureColors(deepGreen: ThemeColor(0x0F4D35), lightGreen: ThemeColor(0x1E8C63), yellow: ThemeColor(0x8A6A00),
                                 orange: ThemeColor(0xB34700), purple: ThemeColor(0x5B3FA8), pending: ThemeColor(0x7A8CA0))
            case .skeuomorphism:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x57A578), lightGreen: ThemeColor(0xABEEBF), yellow: ThemeColor(0xE8B44A),
                                 orange: ThemeColor(0xFF8438), purple: ThemeColor(0xB292F2), pending: ThemeColor(0x8FA3B8))
                : ExposureColors(deepGreen: ThemeColor(0x144B28), lightGreen: ThemeColor(0x328A50), yellow: ThemeColor(0x946800),
                                 orange: ThemeColor(0xC0410A), purple: ThemeColor(0x6A3B9E), pending: ThemeColor(0x7A8CA0))
            case .material3:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x52A277), lightGreen: ThemeColor(0xA3EBC0), yellow: ThemeColor(0xDDB65E),
                                 orange: ThemeColor(0xF08B4F), purple: ThemeColor(0xAE8CF7), pending: ThemeColor(0xD6C8B4))
                : ExposureColors(deepGreen: ThemeColor(0x104629), lightGreen: ThemeColor(0x33885A), yellow: ThemeColor(0x8C6A00),
                                 orange: ThemeColor(0xB34A12), purple: ThemeColor(0x5B3FA8), pending: ThemeColor(0x7A8CA0))
            case .theVerge:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x46A183), lightGreen: ThemeColor(0x63F2AE), yellow: ThemeColor(0xE6BE4E),
                                 orange: ThemeColor(0xFF8033), purple: ThemeColor(0xB08CFF), pending: ThemeColor(0xD6C8B4))
                : ExposureColors(deepGreen: ThemeColor(0x104527), lightGreen: ThemeColor(0x2A8250), yellow: ThemeColor(0x8F6600),
                                 orange: ThemeColor(0xBE4408), purple: ThemeColor(0x6321D6), pending: ThemeColor(0x7A8CA0))
            case .airbnb:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x5AA87E), lightGreen: ThemeColor(0xB0F0C7), yellow: ThemeColor(0xE7BB63),
                                 orange: ThemeColor(0xFF9155), purple: ThemeColor(0xBC9DF2), pending: ThemeColor(0x7A6A57))
                : ExposureColors(deepGreen: ThemeColor(0x165A36), lightGreen: ThemeColor(0x3D9C62), yellow: ThemeColor(0x8F6520),
                                 orange: ThemeColor(0xC0480F), purple: ThemeColor(0x71399C), pending: ThemeColor(0xA89078))
            case .discord:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x4FA079), lightGreen: ThemeColor(0x74E2A0), yellow: ThemeColor(0xDFB755),
                                 orange: ThemeColor(0xF58A46), purple: ThemeColor(0xA9A0FA), pending: ThemeColor(0xD6C8B4))
                : ExposureColors(deepGreen: ThemeColor(0x104325), lightGreen: ThemeColor(0x2D8250), yellow: ThemeColor(0x8A6800),
                                 orange: ThemeColor(0xB54812), purple: ThemeColor(0x5D45B8), pending: ThemeColor(0x7A8CA0))
            case .binanceUS:
                // 黄压暗以离开金色 accent，避免地图色被当成品牌色
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x4FA478), lightGreen: ThemeColor(0xA5EDC0), yellow: ThemeColor(0xD9AE3C),
                                 orange: ThemeColor(0xFF8B2E), purple: ThemeColor(0xB195F0), pending: ThemeColor(0xD6C8B4))
                : ExposureColors(deepGreen: ThemeColor(0x104527), lightGreen: ThemeColor(0x2C8351), yellow: ThemeColor(0x7E5E00),
                                 orange: ThemeColor(0xBC4A05), purple: ThemeColor(0x64409C), pending: ThemeColor(0xA89078))
            case .xcom:
                return dark
                ? ExposureColors(deepGreen: ThemeColor(0x4CA37B), lightGreen: ThemeColor(0x9FEDC0), yellow: ThemeColor(0xE0B857),
                                 orange: ThemeColor(0xF98B43), purple: ThemeColor(0xAE96F6), pending: ThemeColor(0xD6C8B4))
                : ExposureColors(deepGreen: ThemeColor(0x0D4627), lightGreen: ThemeColor(0x2A8452), yellow: ThemeColor(0x8B6800),
                                 orange: ThemeColor(0xB8470E), purple: ThemeColor(0x6236B4), pending: ThemeColor(0xA89078))
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
                   ink: c(0xE7E9EA), body: c(0xE7E9EA), muted: c(0x8B98A5),
                   accent: c(0x1D9BF0), onAccent: c(0x00151F), secondary: c(0x8B98A5),
                   cardRadius: 16, chipRadius: 999, typography: .xcomChirp,
                   elevation: ThemeElevation(opacity: 0, radius: 0, y: 0))
            : Seed(canvas: c(0xFFFFFF), surfaceLowest: c(0xFFFFFF), surface: c(0xFFFFFF),
                   surfaceContainer: c(0xF2F5F6), surfaceRaised: c(0xE8ECED), surfaceHighest: c(0xDEE3E4),
                   surfaceTint: c(0x1D9BF0), chartSurface: c(0xFFFFFF),
                   hairline: c(0xE1E7E8), outlineVariant: c(0xE1E7E8),
                   ink: c(0x0F1419), body: c(0x0F1419), muted: c(0x536471),
                   accent: c(0x1D9BF0), onAccent: c(0x00151F), secondary: c(0x536471),
                   cardRadius: 16, chipRadius: 999, typography: .xcomChirp,
                   elevation: ThemeElevation(opacity: 0, radius: 0, y: 0))
        }
    }
    // swiftlint:enable function_body_length

    private static func c(_ hex: UInt) -> ThemeColor { ThemeColor(hex) }
}
