import SwiftUI

/// The card's memory map: a grid of sector tiles (Codex r5: calmer + more
/// architectural than a dense hex matrix; hex density lives in the inspector).
struct SectorGrid: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    private let cols = Array(repeating: GridItem(.flexible(), spacing: 12), count: 4)

    var body: some View {
        Group {
            // 1K (<= 16 sectors) fits in four rows: size the tiles to fill the
            // canvas so the memory map reads as one composed matrix, no dead space
            // below. Larger cards (4K) stay in a scroll view.
            if model.sectors.count <= 16 {
                GeometryReader { geo in
                    let rows = max(1, Int(ceil(Double(model.sectors.count) / 4.0)))
                    let pad: CGFloat = 24, gap: CGFloat = 12
                    let avail = geo.size.height - pad * 2 - CGFloat(rows - 1) * gap
                    let tileH = min(92, max(56, avail / CGFloat(rows)))
                    LazyVGrid(columns: cols, spacing: gap) {
                        ForEach(model.sectors) { s in tile(s, height: tileH) }
                    }
                    .padding(pad)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }
            } else {
                ScrollView {
                    LazyVGrid(columns: cols, spacing: 12) {
                        ForEach(model.sectors) { s in tile(s, height: 56) }
                    }
                    .padding(24)
                }
            }
        }
        // ⌘C when the grid is first responder; text fields keep their own copy.
        .onCopyCommand {
            guard let t = model.copySelectionText() else { return [] }
            return [NSItemProvider(object: t as NSString)]
        }
    }

    private func tile(_ s: SectorVM, height: CGFloat) -> some View {
        SectorTile(s: s, selected: model.selected == s.index, height: height)
            .onTapGesture {
                withAnimation(.easeOut(duration: 0.16)) { model.selected = s.index }
            }
            .contextMenu { TileMenu(s: s) }
    }
}

private struct TileMenu: View {
    let s: SectorVM
    @Environment(AppModel.self) private var model
    @Environment(L10n.self) private var l
    var body: some View {
        Button(l.t("copy_sector")) { model.copy(model.sectorText(s)) }
        if let kh = s.keyHex {
            Button(l.t("copy_key")) { model.copy(kh) }
        }
    }
}

private struct SectorTile: View {
    let s: SectorVM
    let selected: Bool
    var height: CGFloat = 56
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    private var live: Bool { s.status == .found || s.status == .searching }

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 7)
                .fill(live ? theme.p.tileFill : Color.clear)
                .overlay(
                    RoundedRectangle(cornerRadius: 7).strokeBorder(
                        border,
                        style: live
                            ? StrokeStyle(lineWidth: selected ? 1.2 : 0.5)
                            : StrokeStyle(lineWidth: 1, dash: [3, 3])
                    )
                )
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 5) {
                    Text(String(format: "s%02d", s.index))
                        .font(Typeface.mono(10))
                        .foregroundStyle(live ? theme.p.textSecondary : theme.p.textTertiary)
                    Spacer()
                    CloneStatusGlyph(status: model.cloneStatus(ofSector: s.index))
                    statusGlyph
                }
                Spacer()
                bottom
            }
            .padding(8)
        }
        .frame(height: height)
        .contentShape(Rectangle())
    }

    private var border: Color {
        if selected { return theme.p.accent }
        switch s.status {
        case .found: return theme.p.tileBorder
        case .searching: return theme.p.accent
        case .pending, .failed: return theme.p.voidStroke
        }
    }

    @ViewBuilder private var statusGlyph: some View {
        switch s.status {
        case .found: Image(systemName: "checkmark").font(.system(size: 8, weight: .bold)).foregroundStyle(theme.p.accent)
        case .searching: ProgressView().controlSize(.mini).scaleEffect(0.55)
        case .pending, .failed: ProvenanceDot(p: s.provenance)
        }
    }

    @ViewBuilder private var bottom: some View {
        switch s.status {
        case .found:
            if let kh = s.keyHex {
                Text(kh).font(Typeface.mono(8)).foregroundStyle(theme.p.accent).lineLimit(1)
            }
        case .searching:
            VStack(alignment: .leading, spacing: 3) {
                Text(l.t("decoding")).font(l.sans(8)).foregroundStyle(theme.p.textSecondary).lineLimit(1)
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        Capsule().fill(theme.p.voidStroke).frame(height: 2)
                        Capsule().fill(theme.p.accent).frame(width: g.size.width * searchFraction, height: 2)
                    }
                }.frame(height: 2)
            }
        case .pending, .failed:
            Text(l.t("not_decoded")).font(l.sans(8)).foregroundStyle(theme.p.textTertiary).lineLimit(1)
        }
    }

    private var searchFraction: Double {
        guard let t = s.searchTotal, t > 0 else { return 0 }
        return min(1, Double(s.searchTried ?? 0) / Double(t))
    }
}

struct ProvenanceDot: View {
    let p: KeyProvenance
    @Environment(Theme.self) private var theme
    var body: some View {
        switch p {
        case .nonDefault: Circle().fill(theme.p.textSecondary).frame(width: 6, height: 6)
        case .dictionary: Circle().strokeBorder(theme.p.textTertiary, lineWidth: 1).frame(width: 6, height: 6)
        case .nested: Circle().fill(theme.p.accent).frame(width: 6, height: 6)
        case .unknown: Text("-").font(Typeface.mono(9)).foregroundStyle(theme.p.textTertiary)
        }
    }
}

/// Sector-level clone outcome, overlaid on the tile as blocks stream back.
struct CloneStatusGlyph: View {
    let status: SectorCloneStatus
    @Environment(Theme.self) private var theme
    var body: some View {
        switch status {
        case .none: EmptyView()
        case .ok: Image(systemName: "checkmark").font(.system(size: 8, weight: .bold))
                .foregroundStyle(theme.p.textSecondary)
        case .failed: Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 8))
                .foregroundStyle(theme.p.textPrimary)
        }
    }
}
