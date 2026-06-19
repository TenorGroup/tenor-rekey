import SwiftUI
import CoreText

/// Brand typography (master CLAUDE.md §9): Geist Sans for UI chrome, Geist Mono
/// for every hex / uid / numeric value, Be Vietnam Pro for Vietnamese body. The
/// face names come from the generated TenorFontFace (single source = @tenor/brand),
/// never typed here. CJK glyphs (zh / ja) fall back to the system face per-glyph,
/// which is correct - Geist is a Latin family.
enum Typeface {
    /// Geist Mono - all hex, uid, sak, block data. Language-independent (ASCII).
    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .custom(monoFace(weight), size: size)
    }

    /// UI chrome text. Be Vietnam Pro when the active language is Vietnamese
    /// (brand stack: VN body = Be Vietnam Pro), Geist Sans otherwise.
    static func sans(_ size: CGFloat, _ weight: Font.Weight = .regular, vietnamese: Bool = false) -> Font {
        .custom(vietnamese ? vietFace(weight) : sansFace(weight), size: size)
    }

    /// The tenor wordmark is a fixed Latin brand mark - always Geist Medium,
    /// never localized to Be Vietnam Pro even in Vietnamese.
    static func wordmark(_ size: CGFloat) -> Font {
        .custom(TenorFontFace.sansMedium, size: size)
    }

    private static func sansFace(_ w: Font.Weight) -> String {
        switch w {
        case .bold, .heavy, .black: TenorFontFace.sansBold
        case .semibold: TenorFontFace.sansSemibold
        case .medium: TenorFontFace.sansMedium
        default: TenorFontFace.sansRegular
        }
    }
    private static func monoFace(_ w: Font.Weight) -> String {
        switch w {
        case .medium, .semibold, .bold, .heavy, .black: TenorFontFace.monoMedium
        default: TenorFontFace.monoRegular
        }
    }
    private static func vietFace(_ w: Font.Weight) -> String {
        switch w {
        case .medium, .semibold, .bold, .heavy, .black: TenorFontFace.vietMedium
        default: TenorFontFace.vietRegular
        }
    }

    /// Register the bundled TTFs once at launch, so the faces resolve without an
    /// Info.plist ATSApplicationFontsPath dependency (survives the P7 relocatable
    /// bundle). Idempotent: an already-registered face is a harmless no-op.
    static func registerBundledFonts() {
        for name in TenorFontFace.bundledFiles {
            guard let url = Bundle.main.url(forResource: name, withExtension: "ttf") else { continue }
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }
}
