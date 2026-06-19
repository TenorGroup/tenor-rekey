import SwiftUI

/// The selected sector's detail: key + provenance, a tappable block list, and a
/// Quick-Look of the chosen block (16 bytes laid out 4x4 with an ASCII gutter,
/// plus the decoded access conditions for a trailer). Hex density lives here so
/// the overview grid stays calm (Codex r5).
struct SectorInspector: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        Group {
            if let s = model.selectedSector {
                let blocks = blockNumbers(ofSector: s.index)
                let eff = model.selectedBlock.flatMap { blocks.contains($0) ? $0 : nil } ?? blocks.first
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        sectorHead(s)
                        blockList(s, selected: eff)
                        if let eff {
                            Rectangle().fill(theme.p.hairline).frame(height: 1)
                            BlockQuickLook(sector: s, block: eff)
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                VStack {
                    Spacer()
                    Text(l.t("select_sector")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(theme.p.panel)
    }

    private func sectorHead(_ s: SectorVM) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(l.t("sector")) \(String(format: "%02d", s.index))")
                .font(l.sans(12, .medium))
                .foregroundStyle(theme.p.textPrimary)
            if let kh = s.keyHex {
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(l.t("key")) \(s.keyType?.lowercased() ?? "a") · \(l.t(s.provenance.locKey))")
                        .font(l.sans(9)).foregroundStyle(theme.p.textTertiary)
                    Text(kh).font(Typeface.mono(12))
                        .foregroundStyle(theme.p.accent).textSelection(.enabled)
                }
            }
        }
    }

    /// One tappable row per block; the chosen one carries the accent rail.
    private func blockList(_ s: SectorVM, selected: Int?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(l.t("blocks")).font(l.sans(9)).foregroundStyle(theme.p.textTertiary)
                .padding(.bottom, 2)
            ForEach(Array(s.blocks.enumerated()), id: \.offset) { i, hex in
                let blk = firstBlock(s.index) + i
                let isSel = blk == selected
                HStack(spacing: 6) {
                    Rectangle().fill(isSel ? theme.p.accent : .clear).frame(width: 2, height: 13)
                    Text(String(format: "%3d", blk)).font(Typeface.mono(9))
                        .foregroundStyle(isSel ? theme.p.textPrimary : theme.p.textTertiary)
                    Text(hex).font(Typeface.mono(9))
                        .foregroundStyle(isSel ? theme.p.textSecondary : theme.p.textTertiary)
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    if let ok = model.cloneResults[blk] {
                        Image(systemName: ok ? "checkmark" : "xmark")
                            .font(.system(size: 8, weight: .bold))
                            .foregroundStyle(ok ? theme.p.textSecondary : theme.p.textPrimary)
                    }
                }
                .padding(.vertical, 1)
                .contentShape(Rectangle())
                .onTapGesture { withAnimation(.easeOut(duration: 0.16)) { model.selectedBlock = blk } }
                .contextMenu { Button(l.t("copy_block")) { model.copy(model.blockText(blk, hex: hex)) } }
            }
        }
    }
}

/// Quick-Look of a single block: the 16 bytes grouped 4x4 with an ASCII gutter,
/// the block's role, a copy affordance, and (for a trailer) the decoded access
/// conditions. The byte tint marks the keyA / access / keyB regions of a trailer.
private struct BlockQuickLook: View {
    let sector: SectorVM
    let block: Int
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    private var hex: String {
        let i = block - firstBlock(sector.index)
        return (sector.blocks.indices.contains(i)) ? sector.blocks[i] : "?"
    }
    private var bytes: [UInt8]? {
        let parts = hex.split(separator: " ")
        guard parts.count == 16 else { return nil }
        let b = parts.compactMap { UInt8($0, radix: 16) }
        return b.count == 16 ? b : nil
    }
    private var role: BlockRole { blockRole(absolute: block, sector: sector.index) }
    private var roleKey: String {
        switch role { case .manufacturer: "role_manufacturer"; case .data: "role_data"; case .trailer: "role_trailer" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("\(l.t("block")) \(block)").font(l.sans(11, .medium)).foregroundStyle(theme.p.textPrimary)
                Text(l.t(roleKey)).font(l.sans(9)).foregroundStyle(theme.p.textTertiary)
                Spacer()
                Button { model.copy(model.blockText(block, hex: hex)) } label: {
                    Image(systemName: "doc.on.doc").font(.system(size: 10))
                }
                .buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("copy_block"))
            }

            if let bytes {
                hexGrid(bytes)
                if role == .trailer { accessBlock(bytes) }
            } else {
                Text("-").font(Typeface.mono(11)).foregroundStyle(theme.p.textTertiary)
            }
        }
    }

    /// 16 bytes as four rows of four, each with a byte-offset and ASCII gutter.
    private func hexGrid(_ b: [UInt8]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(0..<4, id: \.self) { r in
                HStack(spacing: 8) {
                    Text(String(format: "%02x", r * 4)).font(Typeface.mono(9)).foregroundStyle(theme.p.textTertiary)
                    HStack(spacing: 5) {
                        ForEach(0..<4, id: \.self) { c in
                            let idx = r * 4 + c
                            Text(String(format: "%02x", b[idx])).font(Typeface.mono(11))
                                .foregroundStyle(byteColor(idx))
                        }
                    }
                    Spacer(minLength: 6)
                    Text(ascii(Array(b[r * 4 ..< r * 4 + 4]))).font(Typeface.mono(11))
                        .foregroundStyle(theme.p.textTertiary)
                }
            }
        }
        .textSelection(.enabled)
    }

    /// keyA (0-5) and keyB (10-15) secondary, access bytes (6-8) accent, GPB (9)
    /// tertiary - only for a trailer; plain blocks read uniformly.
    private func byteColor(_ idx: Int) -> Color {
        guard role == .trailer else { return theme.p.textPrimary }
        switch idx {
        case 6...8: return theme.p.accent
        case 9: return theme.p.textTertiary
        default: return theme.p.textSecondary
        }
    }

    private func ascii(_ b: [UInt8]) -> String {
        String(b.map { (32...126).contains($0) ? Character(UnicodeScalar($0)) : "." })
    }

    /// Decoded access conditions for a trailer: per access group, the C1C2C3
    /// triple and a compact read/write summary.
    @ViewBuilder private func accessBlock(_ b: [UInt8]) -> some View {
        if let ac = AccessConditions.decode(trailer: b) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text(l.t("access")).font(l.sans(9)).foregroundStyle(theme.p.textTertiary)
                    if !ac.valid {
                        Text(l.t("access_invalid")).font(l.sans(9)).foregroundStyle(theme.p.accent)
                    }
                }
                let n = blocksInSector(sector.index)
                ForEach(0..<n, id: \.self) { i in
                    let g = ac.groups[accessGroup(blockInSector: i, blocksInSector: n)]
                    let isTrailer = i == n - 1
                    HStack(spacing: 8) {
                        Text(isTrailer ? "t" : "\(i)").font(Typeface.mono(9)).foregroundStyle(theme.p.textTertiary)
                            .frame(width: 10, alignment: .leading)
                        Text("\(g.c1)\(g.c2)\(g.c3)").font(Typeface.mono(9)).foregroundStyle(theme.p.textSecondary)
                        Text(isTrailer ? trailerAccessSummary(g) : dataAccessSummary(g))
                            .font(Typeface.mono(9)).foregroundStyle(theme.p.textTertiary).lineLimit(1)
                    }
                }
            }
        }
    }
}
