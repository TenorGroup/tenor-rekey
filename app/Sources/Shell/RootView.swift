import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// One unified workspace: the CARD is the document; reading / writing / format /
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
            }
            .onChange(of: systemScheme) { _, s in
                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
            }
            .sheet(isPresented: $model.cloneSheet) {
                CloneSheet().environment(model).environment(theme).environment(l10n)
            }
            .confirmationDialog(l10n.t("format_q"), isPresented: $model.formatConfirm, titleVisibility: .visible) {
                Button(l10n.t("format"), role: .destructive) { Task { await model.format() } }
                Button(l10n.t("cancel"), role: .cancel) {}
            } message: { Text(l10n.t("format_msg")) }
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
    private var ntag: Bool { model.card?.sak == 0x00 }
    private var busy: Bool { model.decoding || model.cloning || model.formatting }

    var body: some View {
        HStack(spacing: 8) {
            ActionButton(title: l.t(ntag ? "read" : "decode"), icon: "square.grid.3x3",
                         prominent: true, enabled: model.card != nil && !busy) { Task { await model.decode() } }
            ActionButton(title: l.t("write"), icon: "square.and.arrow.down.on.square",
                         enabled: model.cloneSource != nil && model.card != nil && !busy) { model.cloneSheet = true }
            // Format requires a prior decode: it auths with the recovered keys,
            // and gating on liveDump means the user has seen the card before wiping.
            ActionButton(title: l.t("format"), icon: "eraser",
                         enabled: model.card != nil && model.liveDump != nil && !busy) { model.formatConfirm = true }
            // Nested / reader key recovery: the crypto + collection are ready and
            // the engine method exists, but it is not yet verified live, so the
            // action stays disabled with a "soon" hint until it is.
            ActionButton(title: l.t("recover"), icon: "key.radiowaves.forward",
                         enabled: false, help: l.t("soon")) { }
            Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
            ActionButton(title: l.t("save_dump"), icon: "arrow.down.doc",
                         enabled: model.liveDump != nil) { model.saveDumpDialog() }
            ActionButton(title: l.t("open_dump"), icon: "folder", enabled: true) { model.openDumpDialog() }
            ActionButton(title: "apdu", icon: "terminal", on: model.apduOpen, enabled: true) { model.apduOpen.toggle() }
            Spacer()
            if model.decoding {
                if let p = model.decodeProgress {
                    Text("\(l.t("sector")) \(min(p.sector + 1, p.total))/\(p.total)")
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

private struct ActionButton: View {
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
            Button { withAnimation(.easeInOut(duration: 0.3)) { model.source = nil } } label: {
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
            if let c = model.card {
                CardHeader(card: c)
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                if c.sak == 0x00 {
                    if model.pages.isEmpty { PreDecode() } else { PageTable() }
                } else if model.sectors.isEmpty {
                    PreDecode()
                } else {
                    SectorGrid()
                }
            } else {
                EmptyState()
            }
        }
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
            metric(l.t("type"), cardType(card.sak), mono: false)
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
                if let p = model.decodeProgress {
                    ProgressView(value: p.fraction).frame(width: 230).tint(theme.p.accent)
                    Text(progressText(p)).font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
                } else {
                    ProgressView().controlSize(.small)
                    Text(l.t("decoding")).font(l.sans(12)).foregroundStyle(theme.p.textSecondary)
                }
                Button(l.t("cancel")) { Task { await model.cancelDecode() } }
                    .buttonStyle(.plain).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
            } else {
                let ntag = model.card?.sak == 0x00
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

    private func progressText(_ p: DecodeProgress) -> String {
        var s = "\(l.t("sector")) \(min(p.sector + 1, p.total))/\(p.total)"
        if let kt = p.keysTotal, let tried = p.keysTried {
            s += "  ·  \(l.t("trying_keys")) \(tried)/\(kt)"
        }
        return s
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

func cardType(_ sak: Int?) -> String {
    switch sak {
    case 0x08: "mifare classic 1k"
    case 0x18: "mifare classic 4k"
    case 0x00: "ultralight / ntag"
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
