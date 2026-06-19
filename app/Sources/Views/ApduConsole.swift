import SwiftUI

/// A bottom-docked APDU console with a true terminal feel: a hex input line, a
/// tx/rx transcript with the status word highlighted, up/down-arrow history, and
/// a context header showing the current card. No send is possible without a card.
struct ApduConsole: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @State private var input = ""
    @State private var historyIdx: Int?

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            transcript
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            inputLine
        }
        .frame(height: 240)
        .background(theme.p.panel)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Text("apdu").font(Typeface.mono(11, .medium))
                .foregroundStyle(theme.p.textPrimary)
            Spacer()
            Text(context).font(Typeface.mono(10)).foregroundStyle(theme.p.textTertiary)
            Button { model.apduOpen = false } label: { Image(systemName: "xmark").font(.system(size: 9)) }
                .buttonStyle(.plain).foregroundStyle(theme.p.textTertiary)
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
    }

    private var context: String {
        guard let c = model.card, let uid = c.uid else { return l.t("apdu_no_card") }
        return "\(uid) · sak \(c.sak.map { String(format: "%02x", $0) } ?? "-")"
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    if model.apduLog.isEmpty {
                        Text(l.t("apdu_empty")).font(Typeface.mono(10))
                            .foregroundStyle(theme.p.textTertiary)
                    }
                    ForEach(model.apduLog) { ApduRow(entry: $0).id($0.id) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16).padding(.vertical, 10)
                .textSelection(.enabled)
            }
            .onChange(of: model.apduLog.count) { _, _ in
                if let last = model.apduLog.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
        }
        .frame(maxHeight: .infinity)
    }

    private var inputLine: some View {
        HStack(spacing: 8) {
            Text("›").font(Typeface.mono(12)).foregroundStyle(theme.p.accent)
            TextField(l.t("apdu_hint"), text: $input)
                .textFieldStyle(.plain)
                .font(Typeface.mono(11))
                .foregroundStyle(theme.p.textPrimary)
                .onSubmit(send)
                .onKeyPress(.upArrow) { recall(-1); return .handled }
                .onKeyPress(.downArrow) { recall(1); return .handled }
            if model.apduBusy { ProgressView().controlSize(.small) }
            Button { send() } label: { Image(systemName: "return").font(.system(size: 10)) }
                .buttonStyle(.plain).foregroundStyle(theme.p.textSecondary)
                .disabled(model.card == nil || input.trimmingCharacters(in: .whitespaces).isEmpty || model.apduBusy)
        }
        .padding(.horizontal, 16).padding(.vertical, 9)
    }

    private func send() {
        let hex = input
        guard model.card != nil, !hex.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        Task { await model.sendAPDU(hex) }
        input = ""
        historyIdx = nil
    }

    /// Step through previously sent commands (up = older, down = newer).
    private func recall(_ dir: Int) {
        let sent = model.apduLog.map(\.tx)
        guard !sent.isEmpty else { return }
        let idx: Int
        if let cur = historyIdx { idx = cur + dir } else { idx = dir < 0 ? sent.count - 1 : sent.count }
        if idx < 0 { historyIdx = 0; input = sent[0] }
        else if idx >= sent.count { historyIdx = nil; input = "" }
        else { historyIdx = idx; input = sent[idx] }
    }
}

private struct ApduRow: View {
    let entry: ApduEntry
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 6) {
                Text("→").font(Typeface.mono(10)).foregroundStyle(theme.p.accent)
                Text(entry.tx).font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
            }
            HStack(spacing: 6) {
                Text("←").font(Typeface.mono(10)).foregroundStyle(theme.p.textTertiary)
                if let rx = entry.rx { response(rx) }
                else { Text(l.t(entry.info ?? "")).font(l.sans(11))
                        .foregroundStyle(theme.p.textTertiary) }
            }
        }
    }

    /// Render the response with the trailing status word emphasised: 90 00 in
    /// the operational accent, any other SW in primary text.
    @ViewBuilder private func response(_ rx: String) -> some View {
        let bytes = rx.split(separator: " ").map(String.init)
        if bytes.count >= 2 {
            let data = bytes.dropLast(2).joined(separator: " ")
            let sw = Array(bytes.suffix(2))
            let ok = sw == ["90", "00"]
            HStack(spacing: 6) {
                if !data.isEmpty {
                    Text(data).font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
                }
                Text(sw.joined(separator: " ")).font(Typeface.mono(11, .medium))
                    .foregroundStyle(ok ? theme.p.accent : theme.p.textPrimary)
            }
        } else {
            Text(rx).font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
        }
    }
}
