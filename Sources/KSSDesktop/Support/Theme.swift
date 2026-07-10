import SwiftUI
import AppKit
import CoreText

/// 主题几何与动效常量。颜色/卡片半径已迁入 `ThemeCatalog` + 环境 `kssTheme` token；
/// 这里只保留与设计系统无关的形状比例（M3 spacing 标度）与运动曲线。
enum KSSTheme {
    // Geometry — M3 形状比例（corner radius, dp）。卡片默认半径改由主题 token 决定，
    // 这套标度只用于内嵌小元素的固定圆角（chip / 列表行 / 细分隔），跨主题恒定。
    // https://m3.material.io/styles/shape
    static let shapeXS: CGFloat = 4    // 小徽标 / 细分隔
    static let shapeS: CGFloat = 8     // 内嵌小卡 / chip 容器 / 列表行
    static let shapeM: CGFloat = 12    // 中容器
    static let shapeL: CGFloat = 16    // 大容器 / 浮层 / 弹窗条
    static let shapeXL: CGFloat = 28   // 对话框 / 全屏浮层

    // MARK: M3 Motion
    /// emphasized 缓动（M3 标准强调曲线）+ medium 时长 350ms。
    static let motionStandard = Animation.timingCurve(0.2, 0, 0, 1, duration: 0.35)
    /// 页面切换 = M3「fade through」（强调版）：入场淡入 + 92% 放大 + 自下 14pt 上移；
    /// 出场淡出 + 轻微放大 1.03 推开。
    static let fadeThrough = AnyTransition.asymmetric(
        insertion: .opacity
            .combined(with: .scale(scale: 0.92, anchor: .center))
            .combined(with: .offset(y: 14)),
        removal: .opacity.combined(with: .scale(scale: 1.03, anchor: .center))
    )
}

/// 字体 helper。标题字族随设计系统（serif / sans / mono）变化，由 token `titleDesign` 驱动。
enum KSSFont {
    /// 旧名：serif 标题。新主题应优先用 `title(_:_:design:)` 传 token 的 titleDesign。
    static func serif(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }

    /// 主题感知标题字体：设计系统决定 serif / 默认 / 等宽。
    static func title(_ size: CGFloat, _ weight: Font.Weight = .semibold, design: Font.Design) -> Font {
        .system(size: size, weight: weight, design: design)
    }

    /// HarmonyOS Sans SC Bold（打包进 app，AppDelegate 启动时注册）。大数字专用。
    static func harmonyNumber(_ size: CGFloat) -> Font {
        .custom("HarmonyOS_Sans_SC_Bold", size: size)
    }

    /// 主题感知正文/标题字体。8 套经典主题 `nativeFontFamily` 为 nil，行为与 `.system(size:weight:design:)`
    /// 完全一致（零回归）；xcom 模式下按 weight 分桶取 "<family>-<Weight>" PostScript 名，并给中文字形
    /// 挂一个级联到 `nativeCJKFallback` 的 `CTFontDescriptor`，单个 Text 内中英文混排各走各的字体。
    /// `design: .monospaced` 场景（K 线/表格数字对齐）不走自定义字体——等宽是功能性选择，跳过。
    static func themed(_ size: CGFloat, _ weight: Font.Weight = .regular, theme: KSSThemeTokens, design: Font.Design = .default) -> Font {
        guard design != .monospaced, let family = theme.nativeFontFamily, let cjkFallback = theme.nativeCJKFallback else {
            return .system(size: size, weight: weight, design: design)
        }
        let psName = "\(family)-\(weightSuffix(weight))"
        guard let baseDescriptor = CTFontDescriptorCreateWithNameAndSize(psName as CFString, size) as CTFontDescriptor? else {
            return .system(size: size, weight: weight, design: design)
        }
        let cjkDescriptor = CTFontDescriptorCreateWithNameAndSize(cjkFallback as CFString, size)
        let cascaded = CTFontDescriptorCreateCopyWithAttributes(
            baseDescriptor,
            [kCTFontCascadeListAttribute: [cjkDescriptor]] as CFDictionary
        )
        let ctFont = CTFontCreateWithFontDescriptor(cascaded, size, nil)
        return Font(ctFont)
    }

    private static func weightSuffix(_ weight: Font.Weight) -> String {
        switch weight {
        case .black, .heavy: return "Heavy"
        case .bold, .semibold: return "Bold"
        case .medium: return "Medium"
        default: return "Regular"
        }
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
/// - elevated：surface（containerLow）+ 按主题 token 给的柔和高度阴影，无边框。
/// - filled：surfaceContainerHighest，无阴影无边框（嵌在已抬高容器里）。
/// - outlined：surface + outline-variant 细边，无阴影（强调边界 / 平铺）。
enum KSSCardStyle { case elevated, filled, outlined }

/// 主题感知卡片：tonal 表面 + 连续圆角（默认半径取当前主题 token）+ 按型给阴影/边框。
struct KSSCard: ViewModifier {
    @Environment(\.kssTheme) private var theme
    var style: KSSCardStyle = .elevated
    var padding: CGFloat = 16
    /// nil = 用当前主题的 cardRadius；非 nil = 显式覆盖（对齐特定容器）。
    var radiusOverride: CGFloat? = nil

    private var radius: CGFloat { radiusOverride ?? theme.cardRadius }

    private var fill: Color {
        switch style {
        case .elevated: return theme.surface
        case .filled:   return theme.surfaceContainerHighest
        case .outlined: return theme.surface
        }
    }

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(fill, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(theme.outlineVariant, lineWidth: style == .outlined ? 1 : 0)
            )
            // 阴影强度/半径/偏移由主题 token 决定（拟物强、终端/Verge 几乎无）。
            .shadow(color: .black.opacity(style == .elevated ? theme.shadowOpacity : 0),
                    radius: style == .elevated ? theme.shadowRadius : 0,
                    x: 0, y: style == .elevated ? theme.shadowY : 0)
    }
}

extension View {
    func kssCard(_ style: KSSCardStyle = .elevated, padding: CGFloat = 16, radius: CGFloat? = nil) -> some View {
        modifier(KSSCard(style: style, padding: padding, radiusOverride: radius))
    }
}
