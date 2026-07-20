import SwiftUI

/// The LF (125 kHz) panel, a Chameleon-only detail area shown when the device advertises
/// lf (capabilities.lf). Three moves, in the calm instrument grammar: read the LF tag on
/// the reader (em410x, then hid prox), write a read/entered id onto a blank T5577 (a clear
/// destructive confirm, like the HF clone), and - for em410x only - load an id into a slot
/// for LF emulation. Only the protocols in scope (em410x + hid prox) are surfaced.
struct LFPanel: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    /// The write/emulate id (hex, no spaces) and its protocol. Seeded from a read, editable
    /// for a manual clone. Kept local so a stray poll cannot disturb a half-typed id.
    @State private var kind = "em410x"
    @State private var idText = ""
    @State private var confirmWrite = false

    private var busy: Bool { model.lfBusy }

    /// The read/write controls are shown only when the device advertises LF read protocols
    /// (respecting the manifest): the Ultra does, the Lite (no reader front-end) sends an
    /// empty lfProtocols, so it gets only the EM410x emulate path.
    private var hasReadWrite: Bool { !model.capabilities.lfProtocols.isEmpty }
    /// EM410x emulate is available on any emulation-capable device (Ultra + Lite) when the
    /// selected protocol is em410x (the only LF emulate type in v1).
    private var canEmulate: Bool { model.capabilities.emulate && kind == "em410x" }

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if hasReadWrite {
                        readSection
                        Rectangle().fill(theme.p.hairline).frame(height: 1)
                        writeSection
                    } else {
                        // Lite: no reader front-end, so no read / T5577 write. Only the
                        // EM410x id entry that feeds the slot emulate below.
                        liteIdEntry
                    }
                    if canEmulate {
                        Rectangle().fill(theme.p.hairline).frame(height: 1)
                        emulateSection
                    }
                }
                .padding(24)
                .frame(maxWidth: 560, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onChange(of: model.lfScanResult) { _, r in seedFromScan(r) }
        .confirmationDialog(l.t("lf_write_q"), isPresented: $confirmWrite, titleVisibility: .visible) {
            Button(l.t("lf_write_t5577"), role: .destructive) {
                Task { await model.lfWrite(kind: kind, id: idText) }
            }
            Button(l.t("cancel"), role: .cancel) {}
        } message: {
            Text(l.t("lf_write_msg"))
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Text(l.t("lf_panel")).font(l.sans(13, .medium)).foregroundStyle(theme.p.textPrimary)
            if busy { ProgressView().controlSize(.small) }
            Spacer()
        }
        .padding(.horizontal, 24).padding(.vertical, 12)
        .background(theme.p.panel)
    }

    // ---- read ----

    private var readSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                ActionButton(title: l.t("lf_read"), icon: "dot.radiowaves.left.and.right",
                             prominent: true,
                             enabled: model.readerOnline && !busy && !model.emulating) {
                    Task { await model.lfScan() }
                }
                if busy { Text(l.t("lf_reading")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary) }
                Spacer()
            }
            if let r = model.lfScanResult {
                if r.present, let id = r.id {
                    scanTile(r, id: id)
                } else {
                    HStack(spacing: 7) {
                        Circle().fill(theme.p.textTertiary).frame(width: 6, height: 6)
                        Text(l.t("lf_no_tag")).font(l.sans(11)).foregroundStyle(theme.p.textSecondary)
                    }
                }
            }
        }
    }

    /// The read result: kind + id, and - for a HID Prox tag - its human fields.
    private func scanTile(_ r: LfScanResult, id: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 24) {
                field(l.t("type"), r.kind ?? "-", mono: false)
                field(l.t("lf_id"), id)
                Spacer()
            }
            if r.kind == "em410x", let tt = r.tagType {
                field(l.t("lf_variant"), tt.replacingOccurrences(of: "_", with: " "), mono: false)
            }
            if r.kind == "hidprox" {
                HStack(alignment: .top, spacing: 24) {
                    if let fn = r.formatName { field(l.t("lf_format"), fn) }
                    if let fc = r.fc { field("fc", String(fc)) }
                    if let cn = r.cn { field("cn", String(cn)) }
                    if let il = r.il, il > 0 { field("il", String(il)) }
                    if let oem = r.oem, oem > 0 { field("oem", String(oem)) }
                    Spacer()
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.md).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.md).strokeBorder(theme.p.hairline, lineWidth: 0.5))
    }

    // ---- write (T5577 clone) ----

    private var writeSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(l.t("lf_write_t5577")).font(l.sans(12, .medium)).foregroundStyle(theme.p.textPrimary)
            HStack(spacing: 10) {
                Picker("", selection: $kind) {
                    Text("em410x").tag("em410x")
                    Text("hid prox").tag("hidprox")
                }
                .pickerStyle(.segmented).labelsHidden().fixedSize()
                TextField(l.t("lf_enter_id"), text: $idText)
                    .textFieldStyle(.roundedBorder).font(Typeface.mono(12)).frame(maxWidth: 240)
                    .disableAutocorrection(true)
                Spacer()
            }
            HStack(spacing: 10) {
                ActionButton(title: l.t("lf_write_t5577"), icon: "square.and.arrow.down.on.square",
                             enabled: validID && model.readerOnline && !busy && !model.emulating) {
                    confirmWrite = true
                }
                if let w = model.lfWriteResult, w.wrote {
                    HStack(spacing: 6) {
                        Image(systemName: w.verified == true ? "checkmark.circle" : "exclamationmark.triangle")
                            .font(.system(size: 11)).foregroundStyle(theme.p.textPrimary)
                        Text(l.t(w.verified == true ? "lf_verified" : "lf_unverified"))
                            .font(l.sans(11)).foregroundStyle(theme.p.textSecondary)
                    }
                }
                Spacer()
            }
        }
    }

    /// The Lite id entry: no reader front-end, so no read / write - just the EM410x id the
    /// emulate section below loads into a slot. The protocol is fixed to em410x here.
    private var liteIdEntry: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("EM410X").font(l.sans(12, .medium)).foregroundStyle(theme.p.textPrimary)
            TextField(l.t("lf_enter_id"), text: $idText)
                .textFieldStyle(.roundedBorder).font(Typeface.mono(12)).frame(maxWidth: 240)
                .disableAutocorrection(true)
        }
    }

    // ---- emulate (em410x only) ----

    private var emulateSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Menu {
                    ForEach(0..<8, id: \.self) { i in
                        Button(slotLabel(i)) { Task { await model.loadLFEmu(id: idText, slot: i) } }
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
                .disabled(!validID || busy || !model.capabilities.emulate)
                Text(l.t("lf_emulate_note")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
                Spacer()
            }
        }
    }

    private func slotLabel(_ i: Int) -> String {
        let base = "\(l.t("slot")) \(i + 1)"
        if let s = model.slots.first(where: { $0.index == i }), !s.lf.nick.isEmpty {
            return "\(base) · \(s.lf.nick)"
        }
        return base
    }

    // ---- helpers ----

    /// A metric cell: a small tracked label over the mono/sans value.
    private func field(_ label: String, _ value: String, mono: Bool = true) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
            Text(value).font(mono ? Typeface.mono(13) : l.sans(13))
                .foregroundStyle(theme.p.textPrimary).textSelection(.enabled)
        }
    }

    /// Seed the write field from a fresh read so the read auto-fills the clone id.
    private func seedFromScan(_ r: LfScanResult?) {
        guard let r, r.present, let id = r.id, let k = r.kind else { return }
        kind = k
        idText = id.replacingOccurrences(of: " ", with: "").lowercased()
    }

    /// A hex id of a length the selected protocol accepts (em410x 5 or 13 bytes; hid 13).
    private var validID: Bool {
        let clean = idText.replacingOccurrences(of: " ", with: "")
        guard !clean.isEmpty, clean.count % 2 == 0,
              clean.allSatisfy({ $0.isHexDigit }) else { return false }
        let bytes = clean.count / 2
        return kind == "em410x" ? (bytes == 5 || bytes == 13) : bytes == 13
    }
}
