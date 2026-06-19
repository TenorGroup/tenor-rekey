import SwiftUI

/// One unified workspace (Codex r5): the CARD is the document; read/decode/clone
/// /recover/apdu are actions on it, not tabs. Canvas = the card's sector map;
/// a right inspector carries hex density. Theme + language are token-driven so
/// switching either is instant (theme = animated crossfade).
struct RootView: View {
    @State private var model = AppModel()
    @State private var theme = Theme()
    @State private var l10n = L10n()
    @State private var inspectorOpen = true
    @Environment(\.colorScheme) private var systemScheme

    var body: some View {
        Workspace(inspectorOpen: $inspectorOpen)
            .environment(model)
            .environment(theme)
            .environment(l10n)
            .preferredColorScheme(theme.appearance == .system ? nil : theme.scheme)
            .onAppear {
                theme.systemScheme = systemScheme
                l10n.systemCode = Locale.current.language.languageCode?.identifier ?? "en"
            }
            .onChange(of: systemScheme) { _, s in
                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
            }
            .task { await model.connect() }
    }
}

private struct Workspace: View {
    @Binding var inspectorOpen: Bool
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        HStack(spacing: 0) {
            CanvasView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if inspectorOpen {
                Rectangle().fill(theme.p.hairline).frame(width: 1)
                SectorInspector().frame(width: 300)
            }
        }
        .background(theme.p.canvas)
        .toolbar { toolbar }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .navigation) { ReaderStatus() }
        ToolbarItem(placement: .principal) { Lockup(focal: "rekey") }
        ToolbarItemGroup(placement: .primaryAction) {
            Button { Task { await model.decode() } } label: { Image(systemName: "square.grid.3x3") }
                .help(l.t("decode")).disabled(model.card == nil || model.decoding)
            Button {} label: { Image(systemName: "doc.on.doc") }.help("\(l.t("clone")) · \(l.t("soon"))").disabled(true)
            Button {} label: { Image(systemName: "key") }.help("\(l.t("recover")) · \(l.t("soon"))").disabled(true)
            Button {} label: { Image(systemName: "terminal") }.help("apdu · \(l.t("soon"))").disabled(true)
            Menu {
                ForEach(AppLang.allCases) { lang in
                    Button(lang == .system ? l.systemDisplay() : lang.display) { l.lang = lang }
                }
            } label: { Image(systemName: "globe") }
            .help(l.t("language"))
            Button { theme.toggle() } label: { Image(systemName: theme.toggleSymbol) }.help(l.t("light_dark"))
            Button { inspectorOpen.toggle() } label: { Image(systemName: "sidebar.right") }.help(l.t("inspector"))
        }
    }
}

private struct CanvasView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    var body: some View {
        VStack(spacing: 0) {
            if let c = model.card {
                CardHeader(card: c)
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                if model.sectors.isEmpty {
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
            metric("uid", card.uid ?? "—")
            metric("atqa", card.atqa ?? "—")
            metric("sak", card.sak.map { String(format: "%02x", $0) } ?? "—")
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
                Button { Task { await model.decode() } } label: { Text(l.t("decode_card")).font(.system(size: 13)) }
                    .buttonStyle(.borderedProminent).tint(theme.p.accent)
                Text(l.t("read_all")).font(.system(size: 10)).foregroundStyle(theme.p.textTertiary)
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
        VStack(spacing: 18) {
            Spacer()
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
            Text(l.t("waiting_card")).font(.system(size: 12)).foregroundStyle(theme.p.textSecondary)
            Text(model.readerOnline ? (model.info?.model.lowercased() ?? l.t("reader_online")) : l.t("reader_offline"))
                .font(.system(size: 10, design: .monospaced)).foregroundStyle(theme.p.textTertiary)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// tenor/<focal> lockup, locked opacity hierarchy 50 / 30 / 100.
struct Lockup: View {
    let focal: String
    @Environment(Theme.self) private var theme
    var body: some View {
        HStack(spacing: 0) {
            Text("tenor").foregroundStyle(theme.p.textPrimary.opacity(TenorOpacity.namespace))
            Text("/").foregroundStyle(theme.p.textPrimary.opacity(TenorOpacity.syntax))
            Text(focal).foregroundStyle(theme.p.textPrimary.opacity(TenorOpacity.focal))
        }
        .font(.system(size: 13, weight: .medium))
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
