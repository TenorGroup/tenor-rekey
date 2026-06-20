import SwiftUI

/// The clone action as a native attached sheet (Codex r5: two-slot source ->
/// target, not a tab). Data blocks copy by default; trailers (keys/access) and
/// block 0 (the uid) are opt-in - block 0 is fenced as a guarded zone when on.
struct CloneSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @Environment(\.dismiss) private var dismiss

    @State private var trailers = false
    @State private var uid = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(l.t("clone")).font(l.sans(14, .medium))
                .foregroundStyle(theme.p.textPrimary)

            HStack(spacing: 12) {
                slot(title: l.t("source"),
                     uid: model.cloneSource.map { $0.uid.isEmpty ? $0.name : $0.uid },
                     subtitle: model.cloneSource.map { "\(cardType($0.sak)) · \($0.sectorCount) \(l.t("sectors"))" } ?? "",
                     placeholder: l.t("no_source"))
                Image(systemName: "arrow.right").foregroundStyle(theme.p.textTertiary)
                slot(title: l.t("card_on_reader"),
                     uid: model.card?.uid,
                     subtitle: model.card.map { "\(cardType($0.sak)) · \(sectorsForSak($0.sak ?? 0x08)) \(l.t("sectors"))" } ?? "",
                     placeholder: l.t("waiting_card"))
            }

            VStack(alignment: .leading, spacing: 12) {
                option(l.t("write_trailers"), hint: l.t("write_trailers_hint"), isOn: $trailers)
                option(l.t("write_uid"), hint: l.t("write_uid_hint"), isOn: $uid)
                if uid { guardedZone }
            }

            HStack {
                Spacer()
                Button(l.t("cancel")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(l.t("write_to_card")) {
                    Task { await model.clone(trailers: trailers, uid: uid) }
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent).tint(theme.p.accent)
                .disabled(model.cloneSource == nil || model.card == nil)
            }
        }
        .padding(22)
        .frame(width: 460)
        .background(theme.p.panel)
    }

    /// A write option: the checkbox plus a one-line plain-language explanation, so
    /// "write trailers" / "write block 0" are not opaque jargon.
    private func option(_ title: String, hint: String, isOn: Binding<Bool>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(title, isOn: isOn)
                .toggleStyle(.checkbox).tint(theme.p.accent).font(l.sans(12))
            Text(hint).font(l.sans(10)).foregroundStyle(theme.p.textTertiary).padding(.leading, 20)
        }
    }

    /// One card slot (source, or the target on the reader): uid + a one-line
    /// summary, or a placeholder when empty. Both sides render the same way.
    private func slot(title: String, uid: String?, subtitle: String, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            if let uid {
                Text(uid).font(Typeface.mono(13)).foregroundStyle(theme.p.textPrimary)
                Text(subtitle).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
            } else {
                Text(placeholder).font(l.sans(12)).foregroundStyle(theme.p.textTertiary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 64, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.md).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.md).strokeBorder(theme.p.hairline, lineWidth: 0.5))
    }

    /// Block 0 fenced off when uid-write is enabled: a guarded inset, not an
    /// alarm colour (instrument discipline) - structure + glyph carry the warning.
    private var guardedZone: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                .foregroundStyle(theme.p.textPrimary)
            Text(l.t("uid_warning")).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.sm).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.sm)
            .strokeBorder(theme.p.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3, 2])))
    }
}
