import SwiftUI

/// The selected sector's detail: key + provenance + block hex. Hex density lives
/// here so the overview grid stays calm (Codex r5).
struct SectorInspector: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        Group {
            if let s = model.selectedSector {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
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

                        VStack(alignment: .leading, spacing: 5) {
                            Text(l.t("blocks")).font(l.sans(9)).foregroundStyle(theme.p.textTertiary)
                            ForEach(Array(s.blocks.enumerated()), id: \.offset) { i, hex in
                                let blk = firstBlock(s.index) + i
                                HStack(spacing: 6) {
                                    Text("\(blk)  \(hex)")
                                        .font(Typeface.mono(8.5))
                                        .foregroundStyle(theme.p.textSecondary)
                                        .textSelection(.enabled).lineLimit(1)
                                    Spacer(minLength: 4)
                                    if let ok = model.cloneResults[blk] {
                                        Image(systemName: ok ? "checkmark" : "xmark")
                                            .font(.system(size: 8, weight: .bold))
                                            .foregroundStyle(ok ? theme.p.textSecondary : theme.p.textPrimary)
                                    }
                                }
                            }
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
}
