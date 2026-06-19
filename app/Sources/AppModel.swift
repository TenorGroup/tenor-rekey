import SwiftUI
import Observation
import AppKit

/// Observable app state. The CARD is the document; reading / decoding act on it.
/// Heavy work stays on the X7Engine actor; this holds @MainActor UI state only.
@MainActor
@Observable
final class AppModel {
    var readerOnline = false
    var info: DeviceInfo?
    var card: PollResult?
    var sectors: [SectorVM] = []
    var selected: Int?
    var decoding = false
    var lastError: String?

    /// The most recent live decode, kept so File > Save can write it out.
    var liveDump: CardDump?
    /// A loaded source dump (File > Open / drag-drop) - the thing clone writes.
    var source: CardDump?
    var cloneSheet = false
    var cloning = false
    /// Per-block write outcome from the last/in-flight clone (block -> ok).
    var cloneResults: [Int: Bool] = [:]

    /// apdu console.
    var apduOpen = false
    var apduLog: [ApduEntry] = []
    var apduBusy = false

    private let engine = X7Engine()
    /// The editable key dictionary that feeds decode (Settings > Dictionaries).
    let keyStore = KeyStore()

    var selectedSector: SectorVM? {
        guard let s = selected else { return nil }
        return sectors.first { $0.index == s }
    }

    /// Start the daemon + read device info, then look for a card (Codex r1:
    /// connect at launch, not lazily).
    func connect() async {
        do {
            info = try await engine.info()
            readerOnline = true
            lastError = nil
            if keyStore.keys.isEmpty, let defs = try? await engine.defaultKeys(), !defs.isEmpty {
                keyStore.seed(defs)
            }
            await refreshCard()
        } catch {
            readerOnline = false
            info = nil
            lastError = "\(error)"
        }
    }

    func refreshCard() async {
        do {
            let p = try await engine.poll()
            withAnimation(.easeInOut(duration: 0.3)) {
                card = p.present ? p : nil
                if !p.present { sectors = []; selected = nil }
            }
            readerOnline = true
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }

    func decode() async {
        guard !decoding else { return }
        decoding = true
        lastError = nil
        cloneResults = [:]
        do {
            let r = try await engine.decode(keys: keyStore.keys)
            let vms = Self.buildSectors(r)
            withAnimation(.easeInOut(duration: 0.3)) {
                card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                sectors = vms
                selected = vms.first?.index
            }
            liveDump = CardDump.from(r, name: r.uid.replacingOccurrences(of: " ", with: ""))
        } catch {
            lastError = "\(error)"
        }
        decoding = false
    }

    static func buildSectors(_ r: DecodeResult) -> [SectorVM] {
        (0..<r.sectors).map { s in
            let key = r.keys[String(s)] ?? nil
            let kt = key?.first
            let kh = (key?.count == 2) ? key?[1] : nil
            let prov: KeyProvenance = kh == nil
                ? .unknown
                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
            let blocks = blockNumbers(ofSector: s).map { b in (r.blocks[String(b)] ?? nil) ?? "?" }
            return SectorVM(index: s, keyType: kt, keyHex: kh, provenance: prov, blocks: blocks)
        }
    }

    // ---- clone / write -----------------------------------------------------

    /// Write the loaded source dump onto the card on the reader. Data blocks
    /// only by default; trailers (keys/access) and block 0 (uid) are opt-in.
    func clone(trailers: Bool, uid: Bool) async {
        guard let src = source, !cloning else { return }
        cloning = true
        cloneResults = [:]
        lastError = nil
        do {
            let r = try await engine.writeMFD(
                blocks: src.blockParams, keys: src.keyParams, trailers: trailers, uid: uid,
                onBlock: { [weak self] b, ok in
                    Task { @MainActor in
                        withAnimation(.easeOut(duration: 0.16)) { self?.cloneResults[b] = ok }
                    }
                })
            // Per-block glyphs in the grid/inspector are the primary failure
            // surface; lastError is the summary for when one is shown.
            if r.present == false {
                lastError = "no card on reader"
            } else if let failed = r.failed, !failed.isEmpty {
                lastError = "\(failed.count) block(s) failed to write: \(failed)"
            }
        } catch {
            lastError = "\(error)"
        }
        cloning = false
    }

    /// Aggregate clone status for one sector tile, from the per-block results.
    func cloneStatus(ofSector s: Int) -> SectorCloneStatus {
        let results = blockNumbers(ofSector: s).compactMap { cloneResults[$0] }
        if results.isEmpty { return .none }
        return results.contains(false) ? .failed : .ok
    }

    // ---- apdu --------------------------------------------------------------

    /// Send a raw APDU to the card on the reader and append the outcome to the
    /// console transcript. Distinguishes a real response, a card that gave no
    /// answer (e.g. a MIFARE Classic, not ISO14443-4), and no card present.
    func sendAPDU(_ hex: String) async {
        let clean = hex.trimmingCharacters(in: .whitespaces).lowercased()
        guard !clean.isEmpty, !apduBusy else { return }
        apduBusy = true
        let id = (apduLog.last?.id ?? 0) + 1
        do {
            let r = try await engine.apdu(clean)
            if !r.present {
                apduLog.append(ApduEntry(id: id, tx: clean, rx: nil, info: "apdu_no_card"))
            } else if let resp = r.resp {
                apduLog.append(ApduEntry(id: id, tx: clean, rx: resp, info: nil))
            } else {
                apduLog.append(ApduEntry(id: id, tx: clean, rx: nil, info: "apdu_no_response"))
            }
        } catch {
            apduLog.append(ApduEntry(id: id, tx: clean, rx: nil, info: "apdu_error"))
            lastError = "\(error)"
        }
        apduBusy = false
    }

    // ---- file dumps --------------------------------------------------------

    func openDumpDialog() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.data]
        panel.allowsOtherFileTypes = true
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url { loadDump(from: url) }
    }

    func saveDumpDialog() {
        guard let dump = liveDump else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(dump.name).mfd"
        panel.allowsOtherFileTypes = true
        if let folder = UserDefaults.standard.string(forKey: "rekey.exportFolder") {
            panel.directoryURL = URL(fileURLWithPath: folder)
        }
        if panel.runModal() == .OK, let url = panel.url { saveDump(dump, to: url) }
    }

    func loadDump(from url: URL) {
        do {
            let dump = try CardDump.load(mfd: url)
            withAnimation(.easeInOut(duration: 0.3)) { source = dump }
            NSDocumentController.shared.noteNewRecentDocumentURL(url)
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }

    private func saveDump(_ dump: CardDump, to url: URL) {
        do {
            try dump.mfdData().write(to: url)
            try dump.keysJSON().write(to: url.appendingPathExtension("keys.json"))
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }
}

enum SectorCloneStatus { case none, ok, failed }
