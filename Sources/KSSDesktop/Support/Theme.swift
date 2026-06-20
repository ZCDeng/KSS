import SwiftUI
import AppKit

/// Design system ported from the project's interactive architecture diagram
/// (html-diagram skill output): Anthropic "warm paper / clay" palette, serif
/// titles, mono labels. Every token is appearance-adaptive — light + dark —
/// resolved per the window's effective appearance, so a `.preferredColorScheme`
/// toggle reskins the whole app.
enum KSSTheme {
    // Surfaces — M3 dynamic color：中性表面随层级递增掺入 clay 主色调，
    // 形成 surface-container 色阶（tonal elevation），高层级容器更暖、更亮。
    // https://m3.material.io/styles/color/roles  ·  https://m3.material.io/components/cards
    static let canvas = adaptive(light: 0xFAF9F5, dark: 0x141413)               // surfaceDim / 页面底
    static let surfaceContainerLowest = adaptive(light: 0xFFFFFF, dark: 0x100F0E)
    static let surface = adaptive(light: 0xFFFFFF, dark: 0x1D1B19)              // containerLow（elevated 卡）
    static let surfaceContainer = adaptive(light: 0xF6F4EC, dark: 0x232120)     // container（默认嵌套）
    static let surfaceRaised = adaptive(light: 0xEFEDE4, dark: 0x2C2926)        // containerHigh（嵌套小卡）
    static let surfaceContainerHighest = adaptive(light: 0xE8E4D8, dark: 0x363230) // containerHighest（filled / 顶层嵌套）
    static let surfaceTint = adaptive(light: 0xD97757, dark: 0xE48A6E)          // M3 primary tint（高度叠色）
    static let chartSurface = adaptive(light: 0xFFFFFF, dark: 0x1D1B19)
    static let hairline = adaptive(light: 0xD1CFC5, dark: 0x3D3D3A)             // outline
    static let outlineVariant = adaptive(light: 0xDED9CC, dark: 0x35332F)       // M3 outline-variant（更弱边）

    // Text
    static let textPrimary = adaptive(light: 0x141413, dark: 0xFAF9F5)    // --ink
    static let textBody = adaptive(light: 0x3D3D3A, dark: 0xD1CFC5)       // --body
    static let textSecondary = adaptive(light: 0x87867F, dark: 0x8A8980)  // --muted

    // Accents (clay terracotta is the primary; olive/gold/blue secondary)
    static let accent = adaptive(light: 0xD97757, dark: 0xE48A6E)         // --clay
    static let olive = adaptive(light: 0x788C5D, dark: 0x9DB07C)
    static let gold = adaptive(light: 0xC9A45C, dark: 0xD4B36F)
    static let blue = adaptive(light: 0x5B7E96, dark: 0x7FA3BC)

    // A股 红涨绿跌：用醒目的标准红/绿（识别度高），不蹭 clay
    static let up = adaptive(light: 0xF23645, dark: 0xF6465D)             // 红
    static let down = adaptive(light: 0x089981, dark: 0x2EBD85)           // 绿
    // 图表均线：橙 / 蓝
    static let ma5 = adaptive(light: 0xF5A623, dark: 0xFF9F1C)
    static let ma20 = adaptive(light: 0x2962FF, dark: 0x4C82FB)

    // Geometry — M3 形状比例（corner radius, dp）
    // https://m3.material.io/styles/shape  none=0 / xs=4 / s=8 / m=12 / l=16 / xl=28 / full=胶囊
    static let shapeXS: CGFloat = 4    // 小徽标 / 细分隔
    static let shapeS: CGFloat = 8     // 内嵌小卡 / chip 容器 / 列表行
    static let shapeM: CGFloat = 12    // 卡片（默认）
    static let shapeL: CGFloat = 16    // 大容器 / 浮层 / 弹窗条
    static let shapeXL: CGFloat = 28   // 对话框 / 全屏浮层
    /// 卡片默认圆角（= M3 medium）。保留旧名，统一指向 shapeM。
    static let cardRadius: CGFloat = shapeM

    static func signColor(_ value: Double?) -> Color {
        guard let value, value != 0 else { return textBody }
        return value > 0 ? up : down
    }

    // MARK: M3 Motion
    // https://m3.material.io/styles/motion/transitions/transition-patterns
    /// emphasized 缓动（M3 标准强调曲线）+ medium 时长 350ms（页面切换看得清）。
    static let motionStandard = Animation.timingCurve(0.2, 0, 0, 1, duration: 0.35)
    /// 页面切换 = M3「fade through」(强调版，让切换明显)：
    /// 入场 = 淡入 + 从 92% 放大 + 自下方 14pt 上移；出场 = 淡出 + 轻微放大 1.03 推开。
    static let fadeThrough = AnyTransition.asymmetric(
        insertion: .opacity
            .combined(with: .scale(scale: 0.92, anchor: .center))
            .combined(with: .offset(y: 14)),
        removal: .opacity.combined(with: .scale(scale: 1.03, anchor: .center))
    )

    private static func adaptive(light: UInt, dark: UInt) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return nsColor(hex: isDark ? dark : light)
        })
    }

    private static func nsColor(hex: UInt) -> NSColor {
        NSColor(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}

/// Title font helper — the design system leads with serif for headings.
enum KSSFont {
    static func serif(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }

    /// HarmonyOS Sans SC Bold（打包进 app，AppDelegate 启动时注册）。
    /// 用 PostScript 名以确保解析到 Bold 字面；未注册成功时 Font.custom 回退系统字体。
    static func harmonyNumber(_ size: CGFloat) -> Font {
        .custom("HarmonyOS_Sans_SC_Bold", size: size)
    }
}

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha
        )
    }
}

/// M3 card 三型：
/// - elevated：containerLow 表面 + 柔和高度阴影，无边框（默认，最「卡片」）。
/// - filled：containerHighest 表面，无阴影无边框（嵌在已抬高的容器里用）。
/// - outlined：surface 表面 + outline-variant 细边，无阴影（强调边界 / 平铺）。
enum KSSCardStyle { case elevated, filled, outlined }

/// M3 dynamic-color 卡片：tonal 表面 + 连续圆角 + 按型给阴影/边框。
struct KSSCard: ViewModifier {
    var style: KSSCardStyle = .elevated
    var padding: CGFloat = 16
    var radius: CGFloat = KSSTheme.shapeM

    private var fill: Color {
        switch style {
        case .elevated: return KSSTheme.surface
        case .filled:   return KSSTheme.surfaceContainerHighest
        case .outlined: return KSSTheme.surface
        }
    }

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(fill, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(KSSTheme.outlineVariant, lineWidth: style == .outlined ? 1 : 0)
            )
            // M3 elevation level 1（克制版）：密集卡片下靠 tonal 抬升为主、阴影只留一丝，
            // 避免多卡并排时阴影叠加造成审美疲劳。
            .shadow(color: .black.opacity(style == .elevated ? 0.10 : 0), radius: 3, x: 0, y: 1)
            .shadow(color: .black.opacity(style == .elevated ? 0.04 : 0), radius: 1, x: 0, y: 0.5)
    }
}

extension View {
    func kssCard(_ style: KSSCardStyle = .elevated, padding: CGFloat = 16, radius: CGFloat = KSSTheme.shapeM) -> some View {
        modifier(KSSCard(style: style, padding: padding, radius: radius))
    }
}
