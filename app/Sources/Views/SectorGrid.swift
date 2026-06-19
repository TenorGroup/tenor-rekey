import SwiftUI

/// The card's memory map: a grid of sector tiles (Codex r5: calmer + more
/// architectural than a dense hex matrix; hex density lives in the inspector).
struct SectorGrid: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    private let cols = Array(repeating: GridItem(.flexible(), spacing: 12), count: 4)

    var body: some View {
        ScrollView {
            LazyVGrid(columns: cols, spacing: 12) {
                ForEach(model.sectors) { s in
                    SectorTile(s: s, selected: model.selected == s.index)
                        .onTapGesture {
                            withAnimation(.easeOut(duration: 0.16)) { model.selected = s.index }
                        }
                }
            }
            .padding(24)
        }
    }
}

private struct SectorTile: View {
    let s: SectorVM
    let selected: Bool
    @Environment(Theme.self) private var theme

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 7)
                .fill(s.hasKey ? theme.p.tileFill : Color.clear)
                .overlay(
                    RoundedRectangle(cornerRadius: 7).strokeBorder(
                        selected ? theme.p.accent : (s.hasKey ? theme.p.tileBorder : theme.p.voidStroke),
                        style: s.hasKey
                            ? StrokeStyle(lineWidth: selected ? 1.2 : 0.5)
                            : StrokeStyle(lineWidth: 1, dash: [3, 3])
                    )
                )
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text(String(format: "s%02d", s.index))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(s.hasKey ? theme.p.textSecondary : theme.p.textTertiary)
                    Spacer()
                    ProvenanceDot(p: s.provenance)
                }
                Spacer()
                if let kh = s.keyHex {
                    Text(kh).font(.system(size: 8, design: .monospaced))
                        .foregroundStyle(theme.p.textTertiary).lineLimit(1)
                }
            }
            .padding(8)
        }
        .frame(height: 56)
        .contentShape(Rectangle())
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
        case .unknown: Text("—").font(.system(size: 9)).foregroundStyle(theme.p.textTertiary)
        }
    }
}
