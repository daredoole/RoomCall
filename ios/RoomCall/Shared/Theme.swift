import SwiftUI
import UIKit

/// Settings → Appearance. Stored in the app group so the widgets follow the chosen colour.
enum AppStyle: String, CaseIterable, Identifiable {
    case glass, material, system
    var id: String { rawValue }
    var label: LocalizedStringKey { LocalizedStringKey("ios_style_" + rawValue) }
}

enum ColorMode: String, CaseIterable, Identifiable {
    case auto, light, dark
    var id: String { rawValue }
    var label: LocalizedStringKey { LocalizedStringKey("ios_mode_" + rawValue) }
    var scheme: ColorScheme? {
        switch self {
        case .auto: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}

enum ThemeSettings {
    static let styleKey = "appearance_style"
    static let modeKey = "appearance_mode"
    static let accentKey = "appearance_accent"
    static let defaultAccent = "#0A84FF"
    /// Same presets as the dashboard card's colour options.
    static let presets: [(name: String, hex: String)] = [
        ("blue", "#0A84FF"), ("purple", "#7C4DFF"), ("teal", "#00A389"), ("coral", "#FF6B4A"), ("pink", "#E0447F"),
    ]
    static var accent: Color { (RGB(hex: RoomCache.defaults.string(forKey: accentKey) ?? "") ?? .default).color }
}

/// An sRGB colour with the conversions the palette needs (components 0...1).
struct RGB: Equatable {
    var r: Double, g: Double, b: Double

    static let `default` = RGB(hex: ThemeSettings.defaultAccent)!

    init(r: Double, g: Double, b: Double) {
        self.r = min(max(r, 0), 1)
        self.g = min(max(g, 0), 1)
        self.b = min(max(b, 0), 1)
    }

    init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
        self.init(r: Double((value >> 16) & 0xFF) / 255, g: Double((value >> 8) & 0xFF) / 255,
                  b: Double(value & 0xFF) / 255)
    }

    init(_ color: Color) {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        _ = UIColor(color).getRed(&r, green: &g, blue: &b, alpha: &a)
        self.init(r: Double(r), g: Double(g), b: Double(b))
    }

    var hex: String {
        String(format: "#%02X%02X%02X", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded()))
    }

    var color: Color { Color(red: r, green: g, blue: b) }

    /// WCAG relative luminance.
    var luminance: Double {
        func linear(_ c: Double) -> Double { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
    }

    /// Dark text on light colours (yellow, mint) so labels on them stay readable.
    var onColor: RGB { luminance > 0.4 ? RGB(r: 0.063, g: 0.075, b: 0.09) : RGB(r: 1, g: 1, b: 1) }

    /// Hue in degrees, saturation and lightness 0...1.
    var hsl: (h: Double, s: Double, l: Double) {
        let high = max(r, g, b), low = min(r, g, b), l = (high + low) / 2
        guard high != low else { return (0, 0, l) }
        let d = high - low
        let s = l > 0.5 ? d / (2 - high - low) : d / (high + low)
        var h: Double
        if high == r { h = (g - b) / d + (g < b ? 6 : 0) } else if high == g { h = (b - r) / d + 2 } else { h = (r - g) / d + 4 }
        h *= 60
        return (h, s, l)
    }

    static func fromHSL(_ h: Double, _ s: Double, _ l: Double) -> RGB {
        let hue = (h.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360) / 360
        guard s > 0 else { return RGB(r: l, g: l, b: l) }
        let q = l < 0.5 ? l * (1 + s) : l + s - l * s
        let p = 2 * l - q
        func channel(_ t: Double) -> Double {
            var t = t
            if t < 0 { t += 1 }
            if t > 1 { t -= 1 }
            if t < 1 / 6 { return p + (q - p) * 6 * t }
            if t < 1 / 2 { return q }
            if t < 2 / 3 { return p + (q - p) * (2 / 3 - t) * 6 }
            return p
        }
        return RGB(r: channel(hue + 1 / 3), g: channel(hue), b: channel(hue - 1 / 3))
    }
}

/// Material 3-style tones from one seed colour, matching the dashboard card's Material look.
struct Tones: Equatable {
    let primary, onPrimary, tertiary, secondaryContainer, onSecondaryContainer, surface, surfaceHigh, outline: RGB

    init(seed: RGB, dark: Bool) {
        let (h, s, _) = seed.hsl
        let tertiaryHue = h + 60, secondaryHue = h + 20
        if dark {
            primary = .fromHSL(h, min(s, 0.85), 0.70)
            onPrimary = .fromHSL(h, 0.60, 0.14)
            tertiary = .fromHSL(tertiaryHue, 0.55, 0.68)
            secondaryContainer = .fromHSL(secondaryHue, 0.24, 0.26)
            onSecondaryContainer = .fromHSL(secondaryHue, 0.40, 0.90)
            surface = .fromHSL(h, 0.14, 0.08)
            surfaceHigh = .fromHSL(h, 0.12, 0.14)
            outline = .fromHSL(h, 0.08, 0.42)
        } else {
            primary = .fromHSL(h, min(s, 0.80), 0.42)
            onPrimary = RGB(r: 1, g: 1, b: 1)
            tertiary = .fromHSL(tertiaryHue, 0.60, 0.50)
            secondaryContainer = .fromHSL(secondaryHue, 0.55, 0.88)
            onSecondaryContainer = .fromHSL(secondaryHue, 0.55, 0.16)
            surface = .fromHSL(h, 0.30, 0.98)
            surfaceHigh = .fromHSL(h, 0.22, 0.93)
            outline = .fromHSL(h, 0.08, 0.62)
        }
    }
}

/// Every colour a screen needs for the chosen style, colour and light/dark mode.
struct Palette {
    let style: AppStyle
    let dark: Bool
    let background: Color
    let glowA: Color, glowB: Color, glowOpacity: Double
    /// Card and row fill (drawn over frosted material for glass).
    let card: Color
    let stroke: Color
    let selected: Color, onSelected: Color
    let primary: Color, onPrimary: Color
    /// Second stop of the talk button's gradient (Material); equals primary otherwise.
    let gradientEnd: Color

    init(style: AppStyle, dark: Bool, seed: RGB) {
        self.style = style
        self.dark = dark
        let indigo = Color(red: 0.369, green: 0.361, blue: 0.902)
        switch style {
        case .glass:
            background = dark ? Color(red: 0.02, green: 0.027, blue: 0.043) : Color(red: 0.933, green: 0.945, blue: 0.965)
            glowA = seed.color
            glowB = indigo
            glowOpacity = dark ? 0.38 : 0.24
            card = dark ? .white.opacity(0.06) : .white.opacity(0.45)
            stroke = dark ? .white.opacity(0.14) : .white.opacity(0.9)
            selected = seed.color
            onSelected = seed.onColor.color
            primary = seed.color
            onPrimary = seed.onColor.color
            gradientEnd = seed.color
        case .material:
            let tones = Tones(seed: seed, dark: dark)
            background = tones.surface.color
            glowA = tones.primary.color
            glowB = tones.tertiary.color
            glowOpacity = dark ? 0.22 : 0.16
            card = tones.surfaceHigh.color
            stroke = .clear
            selected = tones.secondaryContainer.color
            onSelected = tones.onSecondaryContainer.color
            primary = tones.primary.color
            onPrimary = tones.onPrimary.color
            gradientEnd = tones.tertiary.color
        case .system:
            background = Color(.systemGroupedBackground)
            glowA = .clear
            glowB = .clear
            glowOpacity = 0
            card = Color(.secondarySystemGroupedBackground)
            stroke = .clear
            selected = seed.color
            onSelected = seed.onColor.color
            primary = seed.color
            onPrimary = seed.onColor.color
            gradientEnd = seed.color
        }
    }
}

private struct PaletteKey: EnvironmentKey {
    static let defaultValue = Palette(style: .glass, dark: false, seed: .default)
}

extension EnvironmentValues {
    var palette: Palette {
        get { self[PaletteKey.self] }
        set { self[PaletteKey.self] = newValue }
    }
}
