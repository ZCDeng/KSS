import SwiftUI

/// Design system aligned to the Discord token set (design-systems/discord):
/// warm near-black canvas, raised card surfaces, blurple accent for chrome.
/// A-share semantics (红涨绿跌) are reserved strictly for price/return values.
enum KSSTheme {
    // Surfaces (Discord: --bg / --surface / elevated / --border)
    static let canvas = Color(hex: 0x1E1F22)         // --bg
    static let surface = Color(hex: 0x2B2D31)        // --surface (cards)
    static let surfaceRaised = Color(hex: 0x313338)  // elevated / nested
    static let chartSurface = Color(hex: 0x2B2D31)
    static let hairline = Color(hex: 0x3F4147)       // --border

    // Text (Discord: --fg / --muted)
    static let textPrimary = Color(hex: 0xDBDEE1)
    static let textSecondary = Color(hex: 0x949BA4)

    // Accents
    static let accent = Color(hex: 0x5865F2)         // Discord blurple
    static let up = Color(hex: 0xF23645)             // 红涨
    static let down = Color(hex: 0x089981)           // 绿跌
    static let ma5 = Color(hex: 0xFF9F1C)
    static let ma20 = Color(hex: 0x5865F2)

    // Geometry
    static let cardRadius: CGFloat = 14

    /// Tint a number by sign using A-share semantics. nil/zero stays neutral.
    static func signColor(_ value: Double?) -> Color {
        guard let value, value != 0 else { return textPrimary }
        return value > 0 ? up : down
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

/// A raised card surface in the trading-dashboard style.
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
