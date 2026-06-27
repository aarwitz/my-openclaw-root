import SwiftUI

extension Color {
    static let background    = Color(hex: "0D0D0D")
    static let cardSurface   = Color(hex: "1A1A1A")
    static let cardBorder    = Color(hex: "2A2A2A")
    static let signalGreen   = Color(hex: "00D96A")
    static let signalRed     = Color(hex: "FF3B3B")
    static let accent        = Color(hex: "7B61FF")
    static let textPrimary   = Color.white
    static let textSecondary = Color(hex: "8E8E93")
    static let textTertiary  = Color(hex: "48484A")

    init(hex: String) {
        let hex = hex.trimmingCharacters(in: .alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8) & 0xFF) / 255
        let b = Double(int & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}

// Allow dot-shorthand: .foregroundStyle(.signalGreen) — same pattern as SwiftUI's built-in colors
extension ShapeStyle where Self == Color {
    static var background:    Color { Color(hex: "0D0D0D") }
    static var cardSurface:   Color { Color(hex: "1A1A1A") }
    static var cardBorder:    Color { Color(hex: "2A2A2A") }
    static var signalGreen:   Color { Color(hex: "00D96A") }
    static var signalRed:     Color { Color(hex: "FF3B3B") }
    static var accent:        Color { Color(hex: "7B61FF") }
    static var textPrimary:   Color { .white }
    static var textSecondary: Color { Color(hex: "8E8E93") }
    static var textTertiary:  Color { Color(hex: "48484A") }
}

struct CardStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(Color.cardSurface)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.cardBorder, lineWidth: 0.5)
            )
    }
}

extension View {
    func cardStyle() -> some View { modifier(CardStyle()) }
}

struct PriceDelta: View {
    let value: Double
    var showSign: Bool = true
    var suffix: String = "%"

    var body: some View {
        Text(String(format: "%@%.2f%@", showSign && value > 0 ? "+" : "", value, suffix))
            .foregroundStyle(value >= 0 ? .signalGreen : .signalRed)
            .font(.system(.caption, design: .monospaced, weight: .semibold))
    }
}
