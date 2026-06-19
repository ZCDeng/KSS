import SwiftUI

/// Dark fintech trading-dashboard design system. Charcoal canvas, raised card
/// surfaces, a single blue accent for chrome, and A-share semantics (红涨绿跌)
/// reserved strictly for price/return values.
enum KSSTheme {
    // Surfaces
    static let canvas = Color(hex: 0x131722)        // app background
    static let surface = Color(hex: 0x1E222D)        // cards
    static let surfaceRaised = Color(hex: 0x252A38)  // hover / nested
    static let chartSurface = Color(hex: 0x1E222D)
    static let hairline = Color(hex: 0x2A2E39)       // borders / dividers

    // Text
    static let textPrimary = Color(hex: 0xD1D4DC)
    static let textSecondary = Color(hex: 0x787B86)

    // Accents
    static let accent = Color(hex: 0x4C82FB)         // selection / links / chrome
    static let up = Color(hex: 0xF23645)             // 红涨
    static let down = Color(hex: 0x089981)           // 绿跌
    static let ma5 = Color(hex: 0xFF9F1C)
    static let ma20 = Color(hex: 0x4C82FB)

    // Geometry
    static let cardRadius: CGFloat = 12

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
