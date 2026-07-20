import SwiftUI
import UniformTypeIdentifiers

/// The saved-cards library: a persistent, labelled collection of card dumps, shown as a
/// device-agnostic detail area (reachable for both the X7 and a Chameleon). Each tile
/// reads the same calm grammar as the sector / slot grids - a solid populated tile, the
/// selected one marked. Selecting a card opens its actions below: load into the working
/// document, write into a Chameleon slot (only when a Chameleon is connected), rename,
/// delete. The header saves the current working document into the library and imports a
/// dump from another tool (Proxmark3 .eml / .json / .bin, Flipper .nfc).
struct SavedCardsView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    private let cols = Array(repeating: GridItem(.flexible(), spacing: 12), count: 3)

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            if model.savedCards.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVGrid(columns: cols, spacing: 12) {
                        ForEach(model.savedCards) { card in
                            SavedCardTile(card: card, selected: model.selectedSavedCard == card.id)
                                .onTapGesture {
                                    withAnimation(.easeOut(duration: 0.16)) { model.selectedSavedCard = card.id }
                                }
                        }
                    }
                    .padding(24)
                }
            }
            if let sel = model.selectedSavedCard, let card = model.savedCards.first(where: { $0.id == sel }) {
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                SavedCardDetail(card: card)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task { model.refreshSavedCards() }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Text(l.t("saved_cards")).font(l.sans(13, .medium)).foregroundStyle(theme.p.textPrimary)
            if model.slotBusy { ProgressView().controlSize(.small) }
            Spacer()
            ActionButton(title: l.t("save_current"), icon: "arrow.down.doc",
                         enabled: model.source != nil) { model.saveCurrentToLibrary() }
            ActionButton(title: l.t("import"), icon: "square.and.arrow.down", enabled: true) { importDialog() }
        }
        .padding(.horizontal, 24).padding(.vertical, 12)
        .background(theme.p.panel)
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "tray").font(.system(size: 22)).foregroundStyle(theme.p.textTertiary)
            Text(l.t("no_saved_cards")).font(l.sans(13, .medium)).foregroundStyle(theme.p.textPrimary)
            Text(l.t("saved_cards_hint")).font(l.sans(11)).foregroundStyle(theme.p.textSecondary)
                .multilineTextAlignment(.center).frame(maxWidth: 380).fixedSize(horizontal: false, vertical: true)
            ActionButton(title: l.t("import"), icon: "square.and.arrow.down", enabled: true) { importDialog() }
                .padding(.top, 4)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func importDialog() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.data]
        panel.allowsOtherFileTypes = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            model.importCardFile(from: url, unrecognised: l.t("import_failed"))
        }
    }
}

/// One saved card tile: label, uid, tag type, saved date. Solid tile, marked when selected.
private struct SavedCardTile: View {
    let card: SavedCard
    let selected: Bool
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 7)
                .fill(theme.p.tileFill)
                .overlay(RoundedRectangle(cornerRadius: 7)
                    .strokeBorder(selected ? theme.p.accent : theme.p.tileBorder,
                                  lineWidth: selected ? 1.2 : 0.5))
            VStack(alignment: .leading, spacing: 5) {
                Text(card.name).font(l.sans(12, .medium)).foregroundStyle(theme.p.textPrimary).lineLimit(1)
                Text(card.uid.isEmpty ? "-" : card.uid)
                    .font(Typeface.mono(10)).foregroundStyle(theme.p.textSecondary).lineLimit(1)
                Spacer(minLength: 0)
                HStack(spacing: 6) {
                    Text(cardType(card.sak, isNTAG: false))
                        .font(l.sans(9)).foregroundStyle(theme.p.textTertiary).lineLimit(1)
                    Spacer(minLength: 2)
                    Text(Self.date.string(from: card.savedAt))
                        .font(Typeface.mono(9)).foregroundStyle(theme.p.textTertiary)
                }
            }
            .padding(10)
        }
        .frame(height: 96)
        .contentShape(Rectangle())
    }

    private static let date: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yy-MM-dd"; return f
    }()
}

/// Actions for the selected saved card: load into the working document, write into a
/// Chameleon slot (only when a Chameleon is connected), rename, delete. Kept to the calm
/// action-bar grammar (labelled verbs), mirroring the slot library's detail bar.
private struct SavedCardDetail: View {
    let card: SavedCard
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @State private var name = ""
    @State private var pendingDelete: SavedCard?

    private var busy: Bool { model.slotBusy }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                ActionButton(title: l.t("load_to_document"), icon: "square.grid.3x3",
                             enabled: !busy) { model.loadSavedCard(card) }
                // Also gate on the live connection: after a Chameleon disconnects, `info`
                // is cleared but the descriptor's static 8-slot manifest lingers, so slots>0
                // alone would keep write-to-slot visible with no device to write to.
                if model.readerOnline && model.capabilities.slots > 0 {
                    Menu {
                        ForEach(0..<8, id: \.self) { i in
                            Button(slotMenuLabel(i)) { Task { await model.writeSavedCardToSlot(card, slot: i) } }
                        }
                    } label: {
                        actionLabel(icon: "tray.and.arrow.down", title: l.t("write_to_slot"))
                    }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().disabled(busy)
                }
                Spacer()
                ActionButton(title: l.t("delete"), icon: "trash", enabled: !busy) { pendingDelete = card }
            }
            HStack(spacing: 8) {
                TextField(l.t("card_name"), text: $name)
                    .textFieldStyle(.roundedBorder).font(l.sans(12)).frame(maxWidth: 220)
                    .onSubmit { rename() }
                ActionButton(title: l.t("rename"), icon: "pencil", enabled: !busy) { rename() }
                Spacer()
            }
        }
        .padding(.horizontal, 24).padding(.vertical, 14)
        .background(theme.p.panel)
        .task(id: card.id) { name = card.name }
        .confirmationDialog(l.t("delete_card_q"),
                            isPresented: Binding(get: { pendingDelete != nil },
                                                 set: { if !$0 { pendingDelete = nil } }),
                            titleVisibility: .visible, presenting: pendingDelete) { c in
            Button(l.t("delete"), role: .destructive) { model.deleteSavedCard(c) }
            Button(l.t("cancel"), role: .cancel) {}
        } message: { _ in Text(l.t("delete_card_msg")) }
    }

    private func rename() {
        let n = name.trimmingCharacters(in: .whitespaces)
        guard !n.isEmpty else { return }
        model.renameSavedCard(card, name: n)
    }

    /// A slot menu entry, nicked where the slot library has been loaded (else "slot N").
    private func slotMenuLabel(_ i: Int) -> String {
        let base = "\(l.t("slot")) \(i + 1)"
        if let s = model.slots.first(where: { $0.index == i }), !s.hf.nick.isEmpty {
            return "\(base) · \(s.hf.nick)"
        }
        return base
    }

    /// A menu trigger styled like the action-bar verbs (matches the slot library's type menu).
    private func actionLabel(icon: String, title: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 11))
            Text(title).font(l.sans(12, .medium))
        }
        .padding(.horizontal, 11).frame(height: 30)
        .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(0.6)))
        .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
        .foregroundStyle(theme.p.textPrimary)
    }
}
