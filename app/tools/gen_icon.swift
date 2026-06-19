// Generate AppIcon.icns as the tenor/rekey STACKED sub-brand lockup (brand
// grammar 12-tenor-os-stacked: symbol `t/` on top with the slash at 30%, the
// focal word `rekey` below at full opacity, on an ink squircle in paper). The
// app tile carries the focal, exactly like tenor/os - not the bare master t/.
// Rendered with the real Geist Sans Medium via CoreText so the mark is exact.
// Run:  swift app/tools/gen_icon.swift
import AppKit
import CoreText

let brandFonts = "/Users/tuan/Claude/Tenor/Tenor Branding/fonts/geist-sans"
let outDir = "/Users/tuan/Claude/Tenor/tenor-rekey/app/Resources"
let iconset = outDir + "/AppIcon.iconset"
let focal = "rekey"

CTFontManagerRegisterFontsForURL(URL(fileURLWithPath: brandFonts + "/Geist-Medium.ttf") as CFURL, .process, nil)
try? FileManager.default.createDirectory(atPath: iconset, withIntermediateDirectories: true)

let paper = NSColor(srgbRed: 0xFA/255.0, green: 0xFA/255.0, blue: 0xF7/255.0, alpha: 1)

func geist(_ size: CGFloat) -> NSFont { NSFont(name: "Geist-Medium", size: size) ?? .systemFont(ofSize: size, weight: .medium) }

func symbolLine(_ size: CGFloat) -> CTLine {
    let k = -size * 0.04
    let m = NSMutableAttributedString(string: "t", attributes: [.font: geist(size), .foregroundColor: paper, .kern: k])
    m.append(NSAttributedString(string: "/", attributes: [.font: geist(size), .foregroundColor: paper.withAlphaComponent(0.3), .kern: k]))
    return CTLineCreateWithAttributedString(m)
}
func focalLine(_ size: CGFloat) -> CTLine {
    CTLineCreateWithAttributedString(NSAttributedString(string: focal, attributes: [.font: geist(size), .foregroundColor: paper, .kern: -size * 0.04]))
}
func inkBounds(_ l: CTLine) -> CGRect { CTLineGetBoundsWithOptions(l, .useGlyphPathBounds) }

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
    let sq = CGPath(roundedRect: CGRect(x: 0, y: 0, width: s, height: s),
                    cornerWidth: s * 0.21875, cornerHeight: s * 0.21875, transform: nil)
    ctx.addPath(sq); ctx.setFillColor(NSColor.black.cgColor); ctx.fillPath()

    // symbol t/ (prominent), focal `rekey` sized to span ~0.60 of the tile width
    let symFont = s * 0.40
    let probe = s * 0.20
    let focFont = min(s * 0.24, probe * (s * 0.60 / inkBounds(focalLine(probe)).width))
    let sym = symbolLine(symFont), foc = focalLine(focFont)
    let sb = inkBounds(sym), fb = inkBounds(foc)
    let gap = s * 0.045
    let blockTop = (s - (sb.height + gap + fb.height)) / 2     // from top

    // bottom-left CG origin: convert top-anchored ink positions to text baselines
    ctx.textPosition = CGPoint(x: s / 2 - sb.minX - sb.width / 2,
                               y: s - blockTop - sb.height - sb.minY)
    CTLineDraw(sym, ctx)
    ctx.textPosition = CGPoint(x: s / 2 - fb.minX - fb.width / 2,
                               y: s - blockTop - sb.height - gap - fb.height - fb.minY)
    CTLineDraw(foc, ctx)

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for (sz, scale) in [(16,1),(16,2),(32,1),(32,2),(128,1),(128,2),(256,1),(256,2),(512,1),(512,2)] {
    let name = scale == 1 ? "icon_\(sz)x\(sz).png" : "icon_\(sz)x\(sz)@2x.png"
    try! render(sz * scale).write(to: URL(fileURLWithPath: iconset + "/" + name))
}
print("wrote \(iconset)")
