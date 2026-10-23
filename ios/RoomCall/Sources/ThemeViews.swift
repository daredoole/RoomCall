import SwiftUI

/// The screen background: soft colour glows behind glass, a tonal surface for Material.
struct ThemeBackground: View {
    @Environment(\.palette) private var palette

    var body: some View {
        ZStack {
            palette.background
            if palette.style != .system {
                GeometryReader { geometry in
                    let width = geometry.size.width
                    ZStack {
                        Circle().fill(palette.glowA).frame(width: width * 1.1, height: width * 1.1)
                            .position(x: 0, y: geometry.size.height * 0.62)
                        Circle().fill(palette.glowB).frame(width: width * 0.8, height: width * 0.8)
                            .position(x: width, y: geometry.size.height * 0.04)
                    }
                    .blur(radius: 90)
                    .opacity(palette.glowOpacity)
                }
            }
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

/// A rounded card: Liquid Glass on iOS 26, frosted material before it, tonal fill for Material.
struct Surface: ViewModifier {
    @Environment(\.palette) private var palette
    var radius: CGFloat = 24
    /// A solid fill instead of the surface, for selected tiles.
    var fill: Color?

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        if let fill {
            content.background(fill, in: shape)
        } else if palette.style == .glass {
            glass(content, shape)
        } else {
            content.background(palette.card, in: shape)
        }
    }

    @ViewBuilder private func glass(_ content: Content, _ shape: RoundedRectangle) -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            content.glassEffect(.regular, in: shape)
        } else {
            frosted(content, shape)
        }
        #else
        frosted(content, shape)
        #endif
    }

    private func frosted(_ content: Content, _ shape: RoundedRectangle) -> some View {
        content
            .background(palette.card, in: shape)
            .background(.ultraThinMaterial, in: shape)
            .overlay(shape.strokeBorder(palette.stroke, lineWidth: 1))
    }
}

extension View {
    func surface(radius: CGFloat = 24, fill: Color? = nil) -> some View {
        modifier(Surface(radius: radius, fill: fill))
    }

    /// Lists and forms show the theme background behind their rows.
    func themedList() -> some View {
        scrollContentBackground(.hidden).background { ThemeBackground() }
    }

    /// Row fill for lists and forms in the current style.
    func themedRows() -> some View {
        listRowBackground(RowBackground())
    }
}

struct RowBackground: View {
    @Environment(\.palette) private var palette

    var body: some View {
        if palette.style == .glass {
            Rectangle().fill(.ultraThinMaterial).overlay(palette.card)
        } else {
            palette.card
        }
    }
}

/// The colour presets plus a picker for any colour.
struct AccentPicker: View {
    @Binding var hex: String

    var body: some View {
        HStack(spacing: 10) {
            ForEach(ThemeSettings.presets, id: \.hex) { preset in
                let selected = hex.caseInsensitiveCompare(preset.hex) == .orderedSame
                Button { hex = preset.hex } label: {
                    Circle()
                        .fill((RGB(hex: preset.hex) ?? .default).color)
                        .frame(width: 30, height: 30)
                        .overlay {
                            if selected {
                                Image(systemName: "checkmark").font(.caption.weight(.bold))
                                    .foregroundStyle((RGB(hex: preset.hex) ?? .default).onColor.color)
                            }
                        }
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(Text(LocalizedStringKey("ios_color_" + preset.name)))
                .accessibilityAddTraits(selected ? .isSelected : [])
                .accessibilityIdentifier("accent-" + preset.name)
            }
            Spacer(minLength: 0)
            ColorPicker("ios_custom_color", selection: Binding(
                get: { (RGB(hex: hex) ?? .default).color },
                set: { hex = RGB($0).hex }
            ), supportsOpacity: false)
            .labelsHidden()
            .frame(minWidth: 44, minHeight: 44)
            .accessibilityLabel(Text("ios_custom_color"))
        }
    }
}
