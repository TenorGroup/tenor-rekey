import SwiftUI
import UniformTypeIdentifiers

/// One unified workspace (Codex r5): the CARD is the document; read/decode/clone
/// /recover/apdu are actions on it, not tabs. Canvas = the card's sector map;
/// a right inspector carries hex density. Theme + language are token-driven so
/// switching either is instant (theme = animated crossfade).
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l10n
    @Environment(\.colorScheme) private var systemScheme

    var body: some View {
        @Bindable var model = model
        Workspace()
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
            .task { await model.connect() }
    }
}

private struct Workspace: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                CanvasView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
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
        .background(theme.p.canvas)
        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
            guard let provider = providers.first else { return false }
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                let url = (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) }
                    ?? (item as? URL)
                guard let url else { return }
                Task { @MainActor in model.loadDump(from: url) }
            }
            return true
        }
        .toolbar { toolbar }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .navigation) { ReaderStatus() }
        ToolbarItemGroup(placement: .primaryAction) {
            Button { Task { await model.decode() } } label: { Image(systemName: "square.grid.3x3") }
                .help(l.t("decode")).disabled(model.card == nil || model.decoding)
            Button { model.cloneSheet = true } label: { Image(systemName: "doc.on.doc") }
                .help(l.t("clone")).disabled(model.source == nil || model.card == nil || model.cloning)
            Button {} label: { Image(systemName: "key") }.help("\(l.t("recover")) · \(l.t("soon"))").disabled(true)
            Button { model.apduOpen.toggle() } label: { Image(systemName: "terminal") }.help(l.t("apdu"))
            Menu {
                ForEach(AppLang.allCases) { lang in
                    Button(lang == .system ? l.systemDisplay() : lang.display) { l.lang = lang }
                }
            } label: { Image(systemName: "globe") }
            .help(l.t("language"))
            Button { theme.toggle() } label: { Image(systemName: theme.toggleSymbol) }.help(l.t("light_dark"))
            Button { model.inspectorOpen.toggle() } label: { Image(systemName: "sidebar.right") }.help(l.t("inspector"))
        }
    }
}

private struct CanvasView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    var body: some View {
        VStack(spacing: 0) {
            if let src = model.source {
                SourceWell(src: src)
                Rectangle().fill(theme.p.hairline).frame(height: 1)
            }
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

/// A loaded clone source, shown as a slim well above the canvas.
private struct SourceWell: View {
    let src: CardDump
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        HStack(spacing: 10) {
            Text(l.t("source")).font(.system(size: 9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            Text(src.uid.isEmpty ? src.name : "\(src.uid) · \(src.name)")
                .font(.system(size: 11, design: .monospaced)).foregroundStyle(theme.p.textSecondary)
            Text("\(src.sectorCount) \(l.t("sectors"))")
                .font(.system(size: 10)).foregroundStyle(theme.p.textTertiary)
            Spacer()
            Button { withAnimation(.easeInOut(duration: 0.3)) { model.source = nil } } label: {
                Image(systemName: "xmark").font(.system(size: 9))
            }
            .buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
        }
        .padding(.horizontal, 24).padding(.vertical, 8)
        .background(theme.p.panel)
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
            Text(label).font(.system(size: 9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            Text(value).font(.system(size: 14, design: mono ? .monospaced : .default))
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
                ProgressView().controlSize(.small)
                Text(l.t("decoding")).font(.system(size: 12)).foregroundStyle(theme.p.textSecondary)
            } else {
                let ntag = model.card?.sak == 0x00
                Button { Task { await model.decode() } } label: {
                    Text(l.t(ntag ? "read_card" : "decode_card")).font(.system(size: 13))
                }
                .buttonStyle(.borderedProminent).tint(theme.p.accent)
                Text(l.t(ntag ? "read_pages" : "read_all")).font(.system(size: 10)).foregroundStyle(theme.p.textTertiary)
            }
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
        VStack(spacing: 22) {
            Spacer()
            Lockup(focal: "rekey", size: 26)
            VStack(spacing: 8) {
                ForEach(0..<4, id: \.self) { _ in
                    HStack(spacing: 8) {
                        ForEach(0..<4, id: \.self) { _ in
                            RoundedRectangle(cornerRadius: 7)
                                .strokeBorder(theme.p.voidStroke, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                                .frame(width: 56, height: 38)
                        }
                    }
                }
            }
            VStack(spacing: 4) {
                Text(l.t("waiting_card")).font(.system(size: 12)).foregroundStyle(theme.p.textSecondary)
                Text(model.readerOnline ? (model.info?.model.lowercased() ?? l.t("reader_online")) : l.t("reader_offline"))
                    .font(.system(size: 10, design: .monospaced)).foregroundStyle(theme.p.textTertiary)
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
        .font(.system(size: size, weight: .medium))
        .tracking(-size * 0.04)
    }
}

struct ReaderStatus: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary)
                .frame(width: 6, height: 6)
            Text(text).font(.system(size: 11)).foregroundStyle(theme.p.textSecondary)
        }
    }
    private var text: String {
        if !model.readerOnline { return l.t("reader_offline") }
        if let uid = model.card?.uid { return "\(l.t("card")) · \(uid)" }
        return l.t("reader_online")
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
