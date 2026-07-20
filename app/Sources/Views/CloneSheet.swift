import SwiftUI

/// The clone action as a native attached sheet: two-slot source ->
/// target, not a tab). Data blocks copy by default; trailers (keys/access) and
/// block 0 (the uid) are opt-in - block 0 is fenced as a guarded zone when on.
struct CloneSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @Environment(\.dismiss) private var dismiss

    // Default to a full clone: most targets here are CUID (magic) cards, and a
    // data-only copy without the keys/access or the uid is not a usable duplicate.
    // The uid-write warning still shows, so a non-magic card is not a silent trap.
    @State private var trailers = true
    @State private var uid = true
    // Target kind (Chameleon only): a MAGIC card (clone, block 0 writable) vs a REAL card
    // you already hold the keys to (re-key via write_mfd, block 0 factory-locked). On the X7
    // this is never shown - it has only the write_mfd path.
    @State private var realCard = false
    @State private var confirm = false
    // The batch use (one master onto many blanks) keeps the sheet open: after a write
    // the user seats the next blank and writes again without reopening. `batchConfirmed`
    // remembers that the irreversible-write dialog was already accepted for THESE
    // settings, so each blank in the run does not re-prompt; changing a toggle resets it.
    @State private var batchConfirmed = false
    @State private var wrote = false
    // The uid captured the moment write is pressed / the confirm is shown. The write is
    // pinned to it (see AppModel.clone), so a card swapped in while the dialog is open is
    // never the one written. In the batch path it is re-snapshotted per card, so each
    // write still targets exactly the card whose uid is on screen.
    @State private var confirmUID: String?

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
                targetSlot
            }

            // A KeyB (or KeyA) that was mirrored, not read, is written verbatim when
            // trailers are on: warn before the clone so the user knows the slot is a
            // guess and an access reader that authes with the real key may reject it.
            if showAssumedWarning { assumedWarning }

            if model.cloning {
                writingState
            } else if wrote {
                resultState
            } else {
                editState
            }
        }
        .padding(22)
        .frame(width: 460)
        .background(theme.p.panel)
        .onChange(of: trailers) { _, _ in batchConfirmed = false }
        .onChange(of: uid) { _, _ in batchConfirmed = false }
        .confirmationDialog(l.t("clone_q"), isPresented: $confirm, titleVisibility: .visible) {
            Button(l.t("write_to_card"), role: .destructive) {
                batchConfirmed = true
                doWrite(authorized: confirmUID)
            }
            Button(l.t("cancel"), role: .cancel) {}
        } message: {
            Text(l.t("clone_msg") + (confirmUID.map { "\n\n\(l.t("card_on_reader")): \($0)" } ?? ""))
        }
    }

    /// The idle state: write options + the cancel / write buttons.
    private var editState: some View {
        VStack(alignment: .leading, spacing: 18) {
            // On a Chameleon the target can be a magic card (clone) OR a real card you own
            // the keys to (re-key). The X7 has only the write_mfd path, so it is not shown.
            if model.capabilities.emulate { targetKindPicker }
            VStack(alignment: .leading, spacing: 12) {
                option(l.t("write_trailers"), hint: l.t("write_trailers_hint"), isOn: $trailers)
                // A real card's block 0 (uid) is factory-locked, so uid-write is offered only
                // for a magic-card clone; re-keying never touches block 0.
                if !realCard {
                    option(l.t("write_uid"), hint: l.t("write_uid_hint"), isOn: $uid)
                    if uid { guardedZone }
                }
            }
            HStack {
                Spacer()
                Button(l.t("cancel")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(l.t("write_to_card")) {
                    // Snapshot the card being authorized right now (shown in the target
                    // slot); the write is pinned to it. Writing keys/access or the uid is
                    // irreversible (a bad trailer can brick a sector, a uid write can brick
                    // a normal card), so confirm first - unless this batch already
                    // confirmed these settings (the per-card uid pin still applies). A
                    // data-only clone is recoverable, so it writes directly.
                    confirmUID = model.card?.uid
                    if trailers || uid {
                        if batchConfirmed { doWrite(authorized: confirmUID) } else { confirm = true }
                    } else {
                        doWrite(authorized: confirmUID)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent).tint(theme.p.accent)
                .disabled(model.cloneSource == nil || model.card == nil)
            }
        }
    }

    /// While the clone runs: keep the sheet up so the per-block outcome lands in the
    /// source -> target context instead of a sheet that vanished before the result.
    private var writingState: some View {
        HStack(spacing: 10) {
            ProgressView().controlSize(.small)
            Text(l.t("writing")).font(l.sans(12)).foregroundStyle(theme.p.textSecondary)
            Spacer()
        }
    }

    /// After a write: show the outcome, then let the user seat the next blank and write
    /// again (batch) or finish - the sheet never forces a reopen per card.
    private var resultState: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Image(systemName: model.lastError == nil ? "checkmark.circle" : "exclamationmark.triangle")
                    .font(.system(size: 12)).foregroundStyle(theme.p.textPrimary)
                Text(model.lastError ?? l.t("clone_done")).font(l.sans(11))
                    .foregroundStyle(theme.p.textSecondary).lineLimit(2)
                Spacer()
            }
            Text(l.t("place_next")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            HStack {
                Spacer()
                Button(l.t("done")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(l.t("write_another")) { wrote = false }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent).tint(theme.p.accent)
            }
        }
    }

    /// The target slot: the card on the reader, or - while none has coupled yet - an
    /// active "looking for card" spinner so the disabled Write button is not a silent
    /// stuck state (the status poll keeps looking in the background).
    private var targetSlot: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(l.t("card_on_reader")).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            if let c = model.card, let uid = c.uid {
                Text(uid).font(Typeface.mono(13)).foregroundStyle(theme.p.textPrimary)
                Text("\(cardType(c.sak)) · \(sectorsForSak(c.sak ?? 0x08)) \(l.t("sectors"))")
                    .font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
            } else {
                HStack(spacing: 7) {
                    ProgressView().controlSize(.small)
                    Text(l.t("looking_card")).font(l.sans(12)).foregroundStyle(theme.p.textTertiary)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 64, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.md).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.md).strokeBorder(theme.p.hairline, lineWidth: 0.5))
    }

    private var showAssumedWarning: Bool {
        trailers && !(model.cloneSource?.assumedKeys.isEmpty ?? true)
    }

    private var assumedWarning: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                .foregroundStyle(theme.p.textPrimary)
            Text(l.t("assumed_warning")).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.sm).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.sm)
            .strokeBorder(theme.p.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3, 2])))
    }

    /// The magic-card (clone) vs real-card (re-key) target selector, shown only on a
    /// Chameleon. Switching to a real card forces uid-write off (block 0 is factory-locked)
    /// and resets the batch confirmation, so the irreversible-write dialog re-prompts for the
    /// new target kind.
    private var targetKindPicker: some View {
        VStack(alignment: .leading, spacing: 4) {
            Picker("", selection: $realCard) {
                Text(l.t("target_magic")).tag(false)
                Text(l.t("target_real")).tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .onChange(of: realCard) { _, now in
                if now { uid = false }
                batchConfirmed = false
            }
            Text(l.t(realCard ? "target_real_hint" : "target_magic_hint"))
                .font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
        }
    }

    /// Run the clone pinned to the authorized card and stay open on its result (do not
    /// dismiss); the model resets the per-block glyphs when the next card is placed, so
    /// the batch stays clean.
    private func doWrite(authorized: String?) {
        Task {
            await model.clone(trailers: trailers, uid: uid, authorizedUID: authorized, realCard: realCard)
            wrote = true
        }
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
