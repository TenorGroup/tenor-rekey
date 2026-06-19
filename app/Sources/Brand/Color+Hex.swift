import SwiftUI

extension Color {
    /// Build a Color from a 0xRRGGBB literal. Used only by the generated
    /// TenorBrand tokens so brand hex lives in exactly one place.
    init(hex: UInt32) {
        let r = Double((hex >> 16) & 0xFF) / 255
        let g = Double((hex >> 8) & 0xFF) / 255
        let b = Double(hex & 0xFF) / 255
        self.init(.sRGB, red: r, green: g, blue: b, opacity: 1)
    }
}
