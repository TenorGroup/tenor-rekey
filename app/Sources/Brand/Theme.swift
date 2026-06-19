import SwiftUI

/// Semantic theme tokens, resolved per appearance. Brand-canonical roles come
/// from the generated TenorColor (single source of truth); the in-between
/// chrome greys are app-derived surface tones (not brand palette entries).
///
/// The whole app paints from these tokens so a light/dark switch is an animated
/// crossfade (Codex r5: preferredColorScheme + dynamic system colors SNAP, so we
/// drive surfaces ourselves and animate the token swap).
struct Palette {
    let canvas: Color        // window / content background
    let panel: Color         // raised chrome (titlebar, status bar, inspector)
    let hairline: Color      // 1px dividers / borders
    let textPrimary: Color
    let textSecondary: Color
    let textTertiary: Color
    let tileFill: Color      // a read sector tile
    let tileBorder: Color
    let voidStroke: Color    // an unread / unknown sector (dashed)
    let accent: Color        // signal blue - the one live/active accent
    let accentText: Color

    static let dark = Palette(
        canvas: TenorColor.ink,
        panel: Color(hex: 0x0E0F12),
        hairline: Color(hex: 0x1F2024),
        textPrimary: TenorColor.bone,
        textSecondary: Color(hex: 0x9A9A94),
        textTertiary: TenorColor.concrete,
        tileFill: Color(hex: 0x17181C),
        tileBorder: Color(hex: 0x2B2C30),
        voidStroke: Color(hex: 0x34353A),
        accent: TenorColor.signal,
        accentText: TenorColor.pure
    )

    static let light = Palette(
        canvas: TenorColor.paper,
        panel: TenorColor.pure,
        hairline: Color(hex: 0xE6E5DF),
        textPrimary: TenorColor.ink,
        textSecondary: TenorColor.steel,
        textTertiary: TenorColor.concrete,
        tileFill: TenorColor.bone,
        tileBorder: Color(hex: 0xD9D8D2),
        voidStroke: Color(hex: 0xCFCEC7),
        accent: TenorColor.signal,
        accentText: TenorColor.pure
    )
}

enum Appearance: String, CaseIterable {
    case system, light, dark
}

@MainActor
@Observable
final class Theme {
    var appearance: Appearance = .system
    /// Mirror of the environment colour scheme, set by the root view.
    var systemScheme: ColorScheme = .dark

    var scheme: ColorScheme {
        switch appearance {
        case .system: systemScheme
        case .light: .light
        case .dark: .dark
        }
    }

    var p: Palette { scheme == .dark ? .dark : .light }

    /// Cycle the manual override (system -> light -> dark -> system), animated.
    func toggle() {
        withAnimation(.easeInOut(duration: 0.35)) {
            switch appearance {
            case .system: appearance = scheme == .dark ? .light : .dark
            case .light: appearance = .dark
            case .dark: appearance = .light
            }
        }
    }

    var toggleSymbol: String { scheme == .dark ? "moon" : "sun.max" }
}
