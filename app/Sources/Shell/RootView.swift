import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// One unified workspace: the decoded / loaded image is the DOCUMENT on the canvas,
/// the card on the reader is a separate live device; reading / writing / format /
/// save / open are LABELLED actions on a always-visible action bar (so the
/// workflow is discoverable, not hidden behind cryptic toolbar icons). The
/// titlebar is hidden; a custom header carries the brand wordmark + reader
/// status cleanly (no system toolbar wells). Theme + language switch instantly.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l10n
    @Environment(\.colorScheme) private var systemScheme

    var body: some View {
        @Bindable var model = model
        Workspace()
            .background(WindowConfigurator())
            .preferredColorScheme(theme.appearance == .system ? nil : theme.scheme)
            .onAppear {
                theme.systemScheme = systemScheme
                l10n.systemCode = Locale.current.language.languageCode?.identifier ?? "en"
                // Refuse app quit while a firmware flash is writing (a mid-write kill can
                // brick the device); warn and keep the update running.
                AppDelegate.terminationGuard = {
                    guard model.flashing else { return .terminateNow }
                    let alert = NSAlert()
                    alert.messageText = l10n.t("quit_while_flashing_title")
                    alert.informativeText = l10n.t("quit_while_flashing_msg")
                    alert.addButton(withTitle: l10n.t("keep_updating"))
                    alert.runModal()
                    return .terminateCancel
                }
            }
            .onChange(of: systemScheme) { _, s in
                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
            }
            .sheet(isPresented: $model.cloneSheet) {
                CloneSheet().environment(model).environment(theme).environment(l10n)
            }
            .sheet(isPresented: $model.flashingSheet, onDismiss: { model.clearFlashState() }) {
                FlashingView().environment(model).environment(theme).environment(l10n)
            }
            .confirmationDialog(l10n.t("format_q"), isPresented: $model.formatConfirm, titleVisibility: .visible) {
                // Pinned to the uid snapshot taken when the dialog opened, so a card
                // swapped in while it is open is never the one wiped.
                Button(l10n.t("format"), role: .destructive) {
                    Task { await model.format(authorizedUID: model.pendingFormatUID) }
                }
                Button(l10n.t("cancel"), role: .cancel) {}
            } message: {
                Text(l10n.t("format_msg") + (model.pendingFormatUID.map { "\n\n\(l10n.t("card_on_reader")): \($0)" } ?? ""))
            }
            .task { await model.connect(); await model.monitor() }
    }
}

private struct Workspace: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    var body: some View {
        VStack(spacing: 0) {
            HeaderBar()
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ActionBar()
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ErrorBanner()
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    if model.apduOpen {
                        Rectangle().fill(theme.p.hairline).frame(height: 1)
                        ApduConsole()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if model.inspectorOpen {
                    Rectangle().fill(theme.p.hairline).frame(width: 1)
                    SectorInspector().frame(width: 300)
                }
            }
        }
        .background(theme.p.canvas)
        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
            guard let provider = providers.first else { return false }
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                let url = (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) } ?? (item as? URL)
                guard let url else { return }
                Task { @MainActor in model.loadDump(from: url) }
            }
            return true
        }
    }
}

/// A dismissible status line for the last operation error (a clone that hit the
/// wrong card, a write that failed, a decode that was interrupted). Without it those
/// failures were silent - the model recorded them but nothing was ever shown. Glyph +
/// typography carry the signal (instrument discipline: no alarm colour).
private struct ErrorBanner: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        if let err = model.lastError {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                    .foregroundStyle(theme.p.textPrimary)
                Text(err).font(l.sans(11)).foregroundStyle(theme.p.textPrimary).lineLimit(2)
                Spacer()
                Button { withAnimation(.easeInOut(duration: 0.2)) { model.lastError = nil } } label: {
                    Image(systemName: "xmark").font(.system(size: 9))
                }.buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
            }
            .padding(.horizontal, 16).padding(.vertical, 9)
            .background(theme.p.tileFill)
            Rectangle().fill(theme.p.hairline).frame(height: 1)
        }
    }
}

// MARK: - Header (brand + status + utilities), in content so we control the look

private struct HeaderBar: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        // The row sits BELOW the traffic-light band (top padding clears the
        // lights) so the wordmark left-aligns with the action bar margin instead
        // of being indented beside the lights.
        HStack(spacing: 12) {
            Lockup(focal: "rekey", size: 15)
            Spacer()
            ReaderStatusInline()
            Divider().frame(height: 16)
            Menu {
                ForEach(AppLang.allCases) { lang in
                    Button(lang == .system ? l.systemDisplay() : lang.display) { l.lang = lang }
                }
            } label: { Image(systemName: "globe") }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().help(l.t("language"))
            iconButton("sun.max", symbol: theme.toggleSymbol, help: l.t("light_dark")) { theme.toggle() }
            iconButton("sidebar.right", help: l.t("inspector")) { model.inspectorOpen.toggle() }
        }
        .font(l.sans(12))
        .foregroundStyle(theme.p.textSecondary)
        .padding(.leading, 16)
        .padding(.trailing, 14)
        .padding(.top, 30)
        .padding(.bottom, 12)
        .background(theme.p.panel)
    }
    private func iconButton(_ name: String, symbol: String? = nil, help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol ?? name) }
            .buttonStyle(.plain).foregroundStyle(theme.p.textSecondary).help(help)
    }
}

private struct ReaderStatusInline: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary).frame(width: 6, height: 6)
            Text(text).font(model.card?.uid != nil ? Typeface.mono(11) : l.sans(11))
                .foregroundStyle(theme.p.textSecondary)
        }
    }
    private var text: String {
        if model.deviceInDFU { return l.t("in_bootloader") }
        if !model.readerOnline { return l.t("reader_offline") }
        if let uid = model.card?.uid { return "\(l.t("card")) · \(uid)" }
        return l.t("reader_online")
    }
}

// MARK: - Action bar (the discoverable, labelled verbs)

private struct ActionBar: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    private var ntag: Bool { model.card?.isNTAG == true }
    private var busy: Bool { model.decoding || model.cloning || model.formatting }

    var body: some View {
        HStack(spacing: 8) {
            // Enabled whenever the reader is online, not only once the snappy status
            // poll has detected a card: the decode does its own patient coupling, so a
            // seated-but-undetected card is no longer a dead button (see AppModel.decode).
            ActionButton(title: l.t(ntag ? "read" : "decode"), icon: "square.grid.3x3",
                         prominent: true, enabled: model.readerOnline && !busy && !model.emulating) { Task { await model.decode() } }
            // Write lights up as soon as there is a document to write; it does NOT
            // require a card on the reader (the target is asked for at write time in
            // the sheet), so lifting the source card to place a blank never darkens it.
            ActionButton(title: l.t("write"), icon: "square.and.arrow.down.on.square",
                         enabled: model.cloneSource != nil && !busy && !model.emulating) { model.cloneSheet = true }
            // Format is destructive but offered for ANY present card (a blank / unknown
            // card can be wiped with factory keys, no prior decode required); the daemon
            // keeps the anti-brick guards. Gated only on a card being present + a confirm.
            ActionButton(title: l.t("format"), icon: "eraser",
                         enabled: model.card != nil && !busy && !model.emulating) { model.requestFormat() }
            Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
            ActionButton(title: l.t("save_dump"), icon: "arrow.down.doc",
                         enabled: model.source != nil) { model.saveDumpDialog() }
            ActionButton(title: l.t("open_dump"), icon: "folder", enabled: true) { model.openDumpDialog() }
            ActionButton(title: "apdu", icon: "terminal", on: model.apduOpen, enabled: true) { model.apduOpen.toggle() }
            // Saved-cards library: device-agnostic (shown for both the X7 and a Chameleon),
            // so it sits with the document verbs, not behind the Chameleon divider. Opening
            // it closes the Chameleon slot library (only one detail area at a time).
            ActionButton(title: l.t("library"), icon: "books.vertical", on: model.showLibrary,
                         enabled: true) {
                let willShow = !model.showLibrary
                withAnimation(.easeInOut(duration: 0.2)) {
                    model.showLibrary = willShow
                    if willShow { model.showSlots = false; model.showLF = false }
                }
                if willShow { model.refreshSavedCards() }
            }
            // Chameleon-only verbs, gated on the connected device's capabilities: the
            // slot library, the reader<->emulate toggle, and loading the working
            // document into a slot for emulation. A plain reader (X7) shows none of them.
            if model.capabilities.slots > 0 || model.capabilities.emulate
                || model.capabilities.lf || model.capabilities.dfu {
                Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
            }
            if model.capabilities.slots > 0 {
                ActionButton(title: l.t("slots"), icon: "square.stack.3d.up", on: model.showSlots,
                             enabled: !model.slotBusy) {
                    let willShow = !model.showSlots
                    withAnimation(.easeInOut(duration: 0.2)) {
                        model.showSlots = willShow
                        if willShow { model.showLibrary = false; model.showLF = false }
                    }
                    if willShow { Task { await model.loadSlots() } }
                }
            }
            // LF (125 kHz) panel, gated on the device advertising lf: read an LF tag,
            // clone it to a T5577, or load an EM410x id into a slot to emulate. A plain
            // reader (X7, lf:false) never shows it.
            if model.capabilities.lf {
                ActionButton(title: "LF", icon: "wifi", on: model.showLF,
                             enabled: !model.lfBusy, help: l.t("lf_hint")) {
                    let willShow = !model.showLF
                    withAnimation(.easeInOut(duration: 0.2)) {
                        model.showLF = willShow
                        if willShow { model.showSlots = false; model.showLibrary = false }
                    }
                }
            }
            if model.capabilities.emulate {
                EmulateToggle()
                if model.source != nil { LoadToSlotMenu() }
            }
            // Firmware update (DFU), gated on the device advertising it: the X7 has
            // dfu:false and never shows it. Opening the sheet reads the current + latest
            // firmware. Disabled while any device op owns the reader.
            if model.capabilities.dfu {
                // Reachable when the reader is online OR the device is stuck in the
                // bootloader (deviceInDFU) - the latter is exactly when flash-recovery is needed.
                ActionButton(title: l.t("firmware"), icon: "arrow.up.circle",
                             enabled: (model.readerOnline || model.deviceInDFU) && !busy
                                 && !model.slotBusy && !model.emulating) {
                    model.flashingSheet = true
                    Task { await model.checkFirmware() }
                }
            }
            Spacer()
            if model.decoding {
                if let p = model.decodeProgress {
                    Text(decodeStatusLine(p, resolved: model.resolvedSectors, elapsed: model.decodeElapsed, l))
                        .font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
                } else {
                    ProgressView().controlSize(.small)
                }
                Button(l.t("cancel")) { Task { await model.cancelDecode() } }
                    .buttonStyle(.plain).font(l.sans(11)).foregroundStyle(theme.p.accent).padding(.leading, 2)
            } else if busy {
                ProgressView().controlSize(.small).padding(.trailing, 4)
            }
            if let src = model.source { SourceTag(src: src) }
        }
        .padding(.horizontal, 16)
        .frame(height: 48)
        .background(theme.p.panel)
    }
}

struct ActionButton: View {
    let title: String
    let icon: String
    var prominent = false
    var on = false
    let enabled: Bool
    var help: String? = nil
    let action: () -> Void
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: icon).font(.system(size: 11))
                Text(title).font(l.sans(12, .medium))
            }
            .padding(.horizontal, 11)
            .frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 7).fill(fill))
            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: prominent ? 0 : 0.5))
            .foregroundStyle(foreground)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .help(help ?? "")
    }
    private var fill: Color {
        if prominent && enabled { return theme.p.accent }
        if on { return theme.p.tileFill }
        return enabled ? theme.p.tileFill.opacity(0.6) : .clear
    }
    private var foreground: Color {
        if !enabled { return theme.p.textTertiary }
        if prominent { return theme.p.accentText }
        return theme.p.textPrimary
    }
}

/// Load the working document into a Chameleon slot for emulation. A menu of the 8
/// slots (nicked where known); picking one writes the document into that slot's HF
/// emulator and saves it. Shown only when a document is held + the device can emulate.
private struct LoadToSlotMenu: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        Menu {
            ForEach(0..<8, id: \.self) { i in
                Button(slotMenuLabel(i)) { Task { await model.loadDocumentToSlot(i) } }
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "tray.and.arrow.down").font(.system(size: 11))
                Text(l.t("load_to_slot")).font(l.sans(12, .medium))
            }
            .padding(.horizontal, 11).frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(0.6)))
            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
            .foregroundStyle(theme.p.textPrimary)
        }
        .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
        .disabled(model.slotBusy)
    }
    private func slotMenuLabel(_ i: Int) -> String {
        let base = "\(l.t("slot")) \(i + 1)"
        if let s = model.slots.first(where: { $0.index == i }), !s.hf.nick.isEmpty {
            return "\(base) · \(s.hf.nick)"
        }
        return base
    }
}

/// Compact "source loaded" tag with a clear button, in the action bar.
private struct SourceTag: View {
    let src: CardDump
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        HStack(spacing: 7) {
            Text(l.t("source")).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            Text(src.uid.isEmpty ? src.name : src.uid)
                .font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary).lineLimit(1)
            Button { model.clearDocument() } label: {
                Image(systemName: "xmark").font(.system(size: 8))
            }.buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
        }
        .padding(.horizontal, 10).frame(height: 28)
        .background(Capsule().fill(theme.p.tileFill))
        .overlay(Capsule().strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
    }
}

// MARK: - Canvas

private struct CanvasView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    var body: some View {
        VStack(spacing: 0) {
            if model.showLibrary {
                // Saved-cards library, opened from the action bar. Device-agnostic, so it
                // shows for any device; the document flow below is untouched.
                SavedCardsView()
            } else if model.showSlots && model.capabilities.slots > 0 {
                // Chameleon-only slot library, opened from the action bar. The single
                // document flow (below) is untouched; this is a separate detail area.
                SlotLibraryView()
            } else if model.showLF && model.capabilities.lf {
                // Chameleon-only LF (125 kHz) panel: read / T5577 write / EM410x emulate.
                // Another mutually-exclusive detail area; the document flow is untouched.
                LFPanel()
            } else if !model.sectors.isEmpty || !model.pages.isEmpty {
                // A document is loaded (decoding, decoded, or an NTAG page dump): show
                // it. It persists across card swaps, so the working image never
                // vanishes when the source card is lifted to place a target.
                DocHeader()
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                ReaderHint()
                if !model.pages.isEmpty { PageTable() } else { SectorGrid() }
            } else if model.noKeysFound, let c = model.card {
                // Decoded, but no key was in the dictionary: an honest result, not a
                // fake empty grid the user could clone.
                CardHeader(card: c)
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                NoKeysState()
            } else if let c = model.card {
                // A card is on the reader and nothing is decoded yet: offer to read it.
                CardHeader(card: c)
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                PreDecode()
            } else {
                EmptyState()
            }
        }
    }
}

/// Identity of the DOCUMENT on the canvas (the decoded / loaded image), not the live
/// card: its uid is labelled "document" so it reads as a held image independent of
/// whatever card is on the reader. Falls back to the card being decoded before its
/// dump exists.
private struct DocHeader: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        let uid = model.source?.uid ?? model.card?.uid
        let sak = model.source?.sak ?? model.card?.sak
        // NTAG-ness is a property of the DOCUMENT (a page dump), not of whatever card
        // is on the reader: only a page dump populates `pages`.
        let isNTAG = !model.pages.isEmpty
        HStack(alignment: .top, spacing: 28) {
            metric(l.t("document"), uid ?? "-")
            metric("sak", sak.map { String(format: "%02x", $0) } ?? "-")
            metric(l.t("type"), cardType(sak, isNTAG: isNTAG), mono: false)
            Spacer()
        }
        .padding(.horizontal, 24).padding(.vertical, 16)
    }
    private func metric(_ label: String, _ value: String, mono: Bool = true) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            Text(value).font(mono ? Typeface.mono(14) : l.sans(14))
                .foregroundStyle(theme.p.textPrimary).textSelection(.enabled)
        }
    }
}

/// The one line that keeps the document-vs-reader flow honest: when a writable
/// document is held it says what to do next given the card on the reader - place a
/// target to write, or (a different card is sitting there) decode it or write over it.
/// Silent when the reader card is the document itself, so nothing nags in the calm case.
private struct ReaderHint: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        if let hint = hintText {
            HStack(spacing: 7) {
                Circle().fill(model.card == nil ? theme.p.textTertiary : theme.p.accent)
                    .frame(width: 6, height: 6)
                Text(hint).font(l.sans(11)).foregroundStyle(theme.p.textSecondary)
                Spacer()
            }
            .padding(.horizontal, 24).padding(.vertical, 9)
            .background(theme.p.panel)
            Rectangle().fill(theme.p.hairline).frame(height: 1)
        }
    }
    private var hintText: String? {
        guard let doc = model.source?.uid else { return nil }   // only for a writable document
        guard model.readerOnline else { return nil }            // reader unplugged: the header already says so
        guard let card = model.card?.uid else { return l.t("place_target") }
        return AppModel.normUID(card) == AppModel.normUID(doc) ? nil : l.t("decode_to_read")
    }
}

private struct CardHeader: View {
    let card: PollResult
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        HStack(alignment: .top, spacing: 28) {
            metric("uid", card.uid ?? "-")
            metric("atqa", card.atqa ?? "-")
            metric("sak", card.sak.map { String(format: "%02x", $0) } ?? "-")
            metric(l.t("type"), cardType(card.sak, isNTAG: card.isNTAG), mono: false)
            Spacer()
        }
        .padding(.horizontal, 24).padding(.vertical, 16)
    }
    private func metric(_ label: String, _ value: String, mono: Bool = true) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            Text(value).font(mono ? Typeface.mono(14) : l.sans(14))
                .foregroundStyle(theme.p.textPrimary).textSelection(.enabled)
        }
    }
}

private struct PreDecode: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        VStack(spacing: 14) {
            Spacer()
            if model.decoding {
                // An indeterminate bar + an honest, always-forward status line (sector
                // while resolving common keys, auth-attempt count once the dictionary
                // walk starts). No determinate bar, so nothing can jump backward.
                ProgressView().controlSize(.small)
                if let p = model.decodeProgress {
                    Text(decodeStatusLine(p, resolved: model.resolvedSectors, elapsed: model.decodeElapsed, l))
                        .font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
                } else {
                    Text(l.t("decoding")).font(l.sans(12)).foregroundStyle(theme.p.textSecondary)
                }
                Button(l.t("cancel")) { Task { await model.cancelDecode() } }
                    .buttonStyle(.plain).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
            } else {
                let ntag = model.card?.isNTAG == true
                Button { Task { await model.decode() } } label: {
                    Text(l.t(ntag ? "read_card" : "decode_card")).font(l.sans(13))
                }
                .buttonStyle(.borderedProminent).tint(theme.p.accent)
                Text(l.t(ntag ? "read_pages" : "read_all")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// One live status line for a running decode. While a card resolves via key reuse
/// there is no walk to report (just the sector); once the dictionary walk starts it
/// reports honest cumulative auth attempts against the adaptive remaining-work total
/// (which shrinks as sectors resolve), so it never looks frozen.
@MainActor
func decodeStatusLine(_ p: DecodeProgress, resolved: Int, elapsed: Int, _ l: L10n) -> String {
    // The raw auth count has no meaningful denominator (sectors x dictionary is tens of
    // thousands and the walk gives up long before that, so "N/huge" reads as if it quit
    // at a few percent). Pair it instead with two honest, bounded, forward-moving
    // readouts: resolved sectors out of the total, and elapsed seconds. The card's
    // memory map fills in live too, so the walk never reads as frozen.
    if let a = p.attempts {
        return "\(l.t("trying_keys")) \(a) · \(resolved)/\(p.total) \(l.t("sectors")) · \(elapsed)s"
    }
    return "\(l.t("sector")) \(min(p.sector + 1, p.total))/\(p.total)"
}

/// Shown when a decode found no key in the dictionary: an honest dead-end with the
/// real next step (key recovery), not a blank grid that looks clone-ready.
private struct NoKeysState: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "key.slash").font(.system(size: 22)).foregroundStyle(theme.p.textTertiary)
            Text(l.t("no_keys_title")).font(l.sans(13, .medium)).foregroundStyle(theme.p.textPrimary)
            Text(l.t("no_keys_msg")).font(l.sans(11)).foregroundStyle(theme.p.textSecondary)
                .multilineTextAlignment(.center).frame(maxWidth: 380).fixedSize(horizontal: false, vertical: true)
            Button { Task { await model.decode() } } label: {
                Text(l.t("decode_card")).font(l.sans(12))
            }.buttonStyle(.bordered).tint(theme.p.accent).padding(.top, 4)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct EmptyState: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Lockup(focal: "rekey", size: 24)
            // a quiet ghost of the memory map, echoing the loaded sector grid
            VStack(spacing: 9) {
                ForEach(0..<4, id: \.self) { _ in
                    HStack(spacing: 9) {
                        ForEach(0..<4, id: \.self) { _ in
                            RoundedRectangle(cornerRadius: 7)
                                .strokeBorder(theme.p.voidStroke.opacity(0.7),
                                              style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                                .frame(width: 60, height: 40)
                        }
                    }
                }
            }
            VStack(spacing: 5) {
                HStack(spacing: 6) {
                    Circle().fill(model.readerOnline ? theme.p.accent : theme.p.textTertiary)
                        .frame(width: 6, height: 6)
                    Text(l.t("waiting_card")).font(l.sans(12)).foregroundStyle(theme.p.textSecondary)
                }
                Text(model.readerOnline ? (model.info?.model.lowercased() ?? l.t("reader_online")) : l.t("reader_offline"))
                    .font(Typeface.mono(10)).foregroundStyle(theme.p.textTertiary)
            }
            // A seated card can miss the snappy status poll (see AppModel.decode): give
            // an explicit "read anyway" that runs the op's own patient coupling, so a
            // card physically on the reader is never a silent dead-end.
            if model.readerOnline {
                Button { Task { await model.decode() } } label: {
                    Text(l.t("read_anyway")).font(l.sans(11))
                }
                .buttonStyle(.plain).foregroundStyle(theme.p.accent)
                .disabled(model.decoding)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// tenor/<focal> lockup, locked opacity hierarchy 50 / 30 / 100 (namespace /
/// syntax / focal). Letter-spacing tracks the brand symbol (-0.04em ~ -size*0.04).
struct Lockup: View {
    let focal: String
    var size: CGFloat = 15
    @Environment(Theme.self) private var theme
    var body: some View {
        HStack(spacing: 0) {
            Text("tenor").foregroundStyle(theme.p.textPrimary.opacity(TenorOpacity.namespace))
            Text("/").foregroundStyle(theme.p.textPrimary.opacity(TenorOpacity.syntax))
            Text(focal).foregroundStyle(theme.p.textPrimary.opacity(TenorOpacity.focal))
        }
        .font(Typeface.wordmark(size))
        .tracking(-size * 0.04)
    }
}

func cardType(_ sak: Int?, isNTAG: Bool = true) -> String {
    switch sak {
    case 0x08: "mifare classic 1k"
    case 0x18: "mifare classic 4k"
    // A magic/blank Classic also reports SAK 0x00 (ATQA 0x0004); only a genuine
    // NTAG (ATQA 0x0044) is labelled ultralight / ntag.
    case 0x00: isNTAG ? "ultralight / ntag" : "mifare classic 1k"
    case 0x20: "desfire / plus"
    default: "unknown"
    }
}

/// Hidden-titlebar window: make it draggable from the background and keep the
/// titlebar transparent so the custom header reads as one surface.
private struct WindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        DispatchQueue.main.async {
            guard let w = v.window else { return }
            w.titlebarAppearsTransparent = true
            w.isMovableByWindowBackground = true
        }
        return v
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
