import SwiftUI

/// An NTAG / Ultralight (SAK 0x00) page dump: one row per 4-byte page with an
/// ASCII gutter. Same grammar as the sector grid - mono, hairline, calm.
struct PageTable: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 16) {
                    Text(l.t("page")).frame(width: 40, alignment: .leading)
                    Text(l.t("bytes"))
                    Spacer()
                    Text("ascii")
                }
                .font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
                .padding(.horizontal, 24).padding(.bottom, 6)

                ForEach(model.pages) { page in
                    HStack(spacing: 16) {
                        Text(String(format: "%03d", page.index))
                            .font(Typeface.mono(11)).foregroundStyle(theme.p.textTertiary)
                            .frame(width: 40, alignment: .leading)
                        Text(page.hex)
                            .font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
                            .textSelection(.enabled)
                        Spacer()
                        Text(page.ascii)
                            .font(Typeface.mono(11)).foregroundStyle(theme.p.textTertiary)
                            .textSelection(.enabled)
                    }
                    .padding(.horizontal, 24).padding(.vertical, 3)
                }
            }
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
