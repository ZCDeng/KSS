import SwiftUI
import AppKit

/// Design system ported from the project's interactive architecture diagram
/// (html-diagram skill output): Anthropic "warm paper / clay" palette, serif
/// titles, mono labels. Every token is appearance-adaptive — light + dark —
/// resolved per the window's effective appearance, so a `.preferredColorScheme`
/// toggle reskins the whole app.
enum KSSTheme {
    // Surfaces
    static let canvas = adaptive(light: 0xFAF9F5, dark: 0x141413)        // --bg
    static let surface = adaptive(light: 0xFFFFFF, dark: 0x1F1F1D)        // --surface (cards)
    static let surfaceRaised = adaptive(light: 0xF0EEE6, dark: 0x2A2A28)  // --surface2
    static let chartSurface = adaptive(light: 0xFFFFFF, dark: 0x1F1F1D)
    static let hairline = adaptive(light: 0xD1CFC5, dark: 0x3D3D3A)       // --line

    // Text
    static let textPrimary = adaptive(light: 0x141413, dark: 0xFAF9F5)    // --ink
    static let textBody = adaptive(light: 0x3D3D3A, dark: 0xD1CFC5)       // --body
    static let textSecondary = adaptive(light: 0x87867F, dark: 0x8A8980)  // --muted

    // Accents (clay terracotta is the primary; olive/gold/blue secondary)
    static let accent = adaptive(light: 0xD97757, dark: 0xE48A6E)         // --clay
    static let olive = adaptive(light: 0x788C5D, dark: 0x9DB07C)
    static let gold = adaptive(light: 0xC9A45C, dark: 0xD4B36F)
    static let blue = adaptive(light: 0x5B7E96, dark: 0x7FA3BC)

    // A股 红涨绿跌 → 暖红(clay) / 橄榄绿(olive)，与设计系统统一
    static let up = adaptive(light: 0xD97757, dark: 0xE48A6E)
    static let down = adaptive(light: 0x788C5D, dark: 0x9DB07C)
    // 图表均线
    static let ma5 = adaptive(light: 0xC9A45C, dark: 0xD4B36F)            // gold
    static let ma20 = adaptive(light: 0x5B7E96, dark: 0x7FA3BC)          // blue

    // Geometry
    static let cardRadius: CGFloat = 12

    static func signColor(_ value: Double?) -> Color {
        guard let value, value != 0 else { return textBody }
        return value > 0 ? up : down
    }

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

/// A raised card surface in the warm-paper design system.
struct KSSCard: ViewModifier {
    var padding: CGFloat = 16
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(KSSTheme.surface)
            .clipShape(RoundedRectangle(cornerRadius: KSSTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: KSSTheme.cardRadius)
                    .stroke(KSSTheme.hairline, lineWidth: 1)
            )
    }
}

extension View {
    func kssCard(padding: CGFloat = 16) -> some View {
        modifier(KSSCard(padding: padding))
    }
}
