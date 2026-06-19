// Generate AppIcon.icns from the Tenor master symbol `t/` (brand app-icon
// grammar: ink squircle + paper t/, slash at 30%). Renders with the real Geist
// Sans Medium via CoreText so the mark matches @tenor/brand exactly - no font
// substitution. Run:  swift app/tools/gen_icon.swift
import AppKit
import CoreText

let brandFonts = "/Users/tuan/Claude/Tenor/Tenor Branding/fonts/geist-sans"
let outDir = "/Users/tuan/Claude/Tenor/tenor-rekey/app/Resources"
let iconset = outDir + "/AppIcon.iconset"

CTFontManagerRegisterFontsForURL(URL(fileURLWithPath: brandFonts + "/Geist-Medium.ttf") as CFURL, .process, nil)
try? FileManager.default.createDirectory(atPath: iconset, withIntermediateDirectories: true)

let paper = NSColor(srgbRed: 0xFA/255.0, green: 0xFA/255.0, blue: 0xF7/255.0, alpha: 1)

func render(_ px: Int) -> Data {
    let s = CGFloat(px)
    // Exact-pixel canvas (NSImage lockFocus would render at the display's 2x
    // backing scale and double every output).
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                               isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: px, height: px)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let ctx = NSGraphicsContext.current!.cgContext

    // ink squircle, full-bleed (brand app-icon: rect 0..1024 rx 224 = 0.21875)
    let path = CGPath(roundedRect: CGRect(x: 0, y: 0, width: s, height: s),
                      cornerWidth: s * 0.21875, cornerHeight: s * 0.21875, transform: nil)
    ctx.addPath(path); ctx.setFillColor(NSColor.black.cgColor); ctx.fillPath()

    // t/  (Geist Medium, slash 30%, brand tracking -0.04em)
    let fontSize = s * 0.56
    let font = NSFont(name: "Geist-Medium", size: fontSize) ?? .systemFont(ofSize: fontSize, weight: .medium)
    let kern = -fontSize * 0.04
    let str = NSMutableAttributedString()
    str.append(NSAttributedString(string: "t", attributes: [.font: font, .foregroundColor: paper, .kern: kern]))
    str.append(NSAttributedString(string: "/", attributes: [.font: font, .foregroundColor: paper.withAlphaComponent(0.3), .kern: kern]))

    // center on the actual glyph INK (path bounds), not the line/line-height box,
    // so the mark sits dead-centre regardless of ascent/descent padding
    let line = CTLineCreateWithAttributedString(str)
    let ink = CTLineGetBoundsWithOptions(line, .useGlyphPathBounds)
    ctx.textPosition = CGPoint(x: s / 2 - ink.minX - ink.width / 2,
                               y: s / 2 - ink.minY - ink.height / 2)
    CTLineDraw(line, ctx)

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for (sz, scale) in [(16,1),(16,2),(32,1),(32,2),(128,1),(128,2),(256,1),(256,2),(512,1),(512,2)] {
    let name = scale == 1 ? "icon_\(sz)x\(sz).png" : "icon_\(sz)x\(sz)@2x.png"
    try! render(sz * scale).write(to: URL(fileURLWithPath: iconset + "/" + name))
}
print("wrote \(iconset)")
