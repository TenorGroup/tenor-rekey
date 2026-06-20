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
    var pages: [NtagPage] = []          // NTAG / Ultralight page dump (SAK 0x00)
    var selected: Int?                  // selected sector index
    var selectedBlock: Int?             // selected absolute block, for the quick-look
    var decoding = false
    var decodeProgress: DecodeProgress?      // live sector / key-walk progress
    private var decodeCancelled = false
    var lastError: String?
    var inspectorOpen = true

    /// The most recent live decode, kept so File > Save can write it out.
    var liveDump: CardDump?
    /// A loaded source dump (File > Open / drag-drop) - the thing clone writes.
    var source: CardDump?
    var cloneSheet = false
    var cloning = false
    /// Per-block write outcome from the last/in-flight clone (block -> ok).
    var cloneResults: [Int: Bool] = [:]
    var formatConfirm = false
    var formatting = false

    /// apdu console.
    var apduOpen = false
    var apduLog: [ApduEntry] = []
    var apduBusy = false

    private let engine = X7Engine()
    /// The user's editable keys (Settings > Dictionaries), tried before the
    /// daemon's large built-in dictionary.
    let keyStore = KeyStore()
    /// Size of the daemon's built-in curated dictionary (shown in Settings).
    var builtinKeyCount = 0

    var selectedSector: SectorVM? {
        guard let s = selected else { return nil }
        return sectors.first { $0.index == s }
    }

    /// What "write" clones FROM: an explicitly loaded source dump if there is one,
    /// otherwise the card just decoded - so decode then write needs no Save / Open
    /// round-trip (the live decode is already a usable source). The implicit live
    /// decode counts ONLY when it belongs to the card currently on the reader, so a
    /// card swap can never silently make card A's image the source for card B.
    var cloneSource: CardDump? {
        if let source { return source }
        if let d = liveDump, let cuid = card?.uid, Self.normUID(d.uid) == Self.normUID(cuid) { return d }
        return nil
    }
    static func normUID(_ s: String) -> String {
        s.replacingOccurrences(of: " ", with: "").lowercased()
    }

    /// Start the daemon + read device info, then look for a card (Codex r1:
    /// connect at launch, not lazily).
    func connect() async {
        do {
            info = try await engine.info()
            builtinKeyCount = (try? await engine.builtinKeyCount()) ?? 0
            readerOnline = true
            lastError = nil
            await refreshStatus()
        } catch {
            applyReaderGone()
            lastError = "\(error)"
        }
    }

    /// Live status: keep the reader / card pill honest when the X7 or a card is
    /// plugged or removed with no user action. Runs until the view's task is
    /// cancelled. Skips polling during an operation that already owns the reader.
    func monitor() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1.5))
            if decoding || cloning || formatting || apduBusy { continue }
            await refreshStatus()
        }
    }

    /// Consecutive polls that saw the reader but no card; a seated card that blips
    /// for one cycle should not drop its decoded grid, so we debounce a removal.
    private var cardAbsentStreak = 0

    /// One status sample: detects reader unplug (drops to offline + clears), reader
    /// replug (back online + refetch device info), and card placed / removed.
    private func refreshStatus() async {
        do {
            let p = try await engine.poll(tries: 8)
            if p.reader == false {           // reader unplugged: reflect it at once
                applyReaderGone()
                return
            }
            readerOnline = true
            if info == nil { info = try? await engine.info() }   // refetch until it lands
            lastError = nil
            if p.present {
                cardAbsentStreak = 0
                // A different card (or first placement): clear everything bound to
                // the previous card BEFORE swapping in the new one, so its grid,
                // live decode and clone results never bleed onto the new card.
                if card == nil || p.uid != card?.uid {
                    withAnimation(.easeInOut(duration: 0.3)) { clearCardState(); card = p }
                }
            } else {
                cardAbsentStreak += 1
                if card != nil && cardAbsentStreak >= 2 {
                    withAnimation(.easeInOut(duration: 0.3)) { card = nil; clearCardState() }
                }
            }
        } catch {
            applyReaderGone()
        }
    }

    /// Forget everything tied to the card on the reader: the decode grid, page
    /// dump, selection, the live decode used as an implicit clone source, and the
    /// per-block clone results. An explicitly loaded `source` document is kept (it
    /// is a separate file the user opened, not bound to this card). Shared by the
    /// swap, removal, and reader-gone paths so they cannot drift.
    private func clearCardState() {
        sectors = []; pages = []; selected = nil; selectedBlock = nil
        liveDump = nil; cloneResults = [:]
    }

    /// Reader unplugged or the daemon went away: go offline and clear everything
    /// tied to a live reader. No-op when already in that state (avoids churn).
    private func applyReaderGone() {
        cardAbsentStreak = 0
        guard readerOnline || card != nil || info != nil else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            readerOnline = false
            info = nil
            card = nil
            clearCardState()
        }
    }

    func decode() async {
        guard !decoding else { return }
        decoding = true
        decodeCancelled = false
        decodeProgress = nil
        lastError = nil
        cloneResults = [:]
        do {
            if card?.sak == 0x00 {
                // NTAG / Ultralight: a page dump, not a sector/key decode.
                let r = try await engine.readNTAG()
                let pgs = Self.buildPages(r)
                withAnimation(.easeInOut(duration: 0.3)) { sectors = []; selected = nil; pages = pgs }
            } else {
                // show the whole grid right away (all pending) so sectors fill in
                // live as each one is searched, instead of a blank wait.
                let count = card?.sak.map { sectorsForSak($0) } ?? 16
                withAnimation(.easeInOut(duration: 0.3)) {
                    sectors = Self.pendingSectors(count: count); pages = []; selected = nil; selectedBlock = nil
                }
                let r = try await engine.decode(userKeys: keyStore.keys,
                    onProgress: { [weak self] ev in Task { @MainActor in self?.applyDecodeEvent(ev) } })
                let vms = Self.buildSectors(r)
                withAnimation(.easeInOut(duration: 0.3)) {
                    card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                    sectors = vms
                    pages = []
                    selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                }
                liveDump = CardDump.from(r, name: r.uid.replacingOccurrences(of: " ", with: ""))
            }
        } catch {
            // a user cancel kills the daemon, which surfaces as a thrown error - not
            // something to show as a failure.
            if !decodeCancelled { lastError = "\(error)" }
        }
        decoding = false
        decodeCancelled = false
        decodeProgress = nil
    }

    /// Stop a long decode. Kills the daemon (the only way to interrupt a
    /// synchronous dictionary walk mid-flight); the monitor / next op respawns it.
    func cancelDecode() async {
        guard decoding else { return }
        decodeCancelled = true
        await engine.cancel()
    }

    /// Fold a decode progress event into `decodeProgress`. The daemon emits a
    /// sector-boundary event (carries `total` = sector count) and a key-walk event
    /// (carries keys_tried / keys_total) as it searches a sector's key.
    private func applyDecodeEvent(_ ev: EngineEvent) {
        guard decoding, let s = ev.sector else { return }
        let fallbackTotal = card?.sak.map { sectorsForSak($0) } ?? 16
        var p = decodeProgress ?? DecodeProgress(sector: 0, total: fallbackTotal, keysTried: nil, keysTotal: nil)
        p.sector = s
        if let t = ev.total { p.total = t; p.keysTried = nil; p.keysTotal = nil }
        if let kt = ev.keys_total { p.keysTotal = kt; p.keysTried = ev.keys_tried }
        decodeProgress = p

        // per-sector live tile state
        guard sectors.indices.contains(s) else { return }
        if ev.keys_total != nil {                         // key-walk event: this sector is searching
            sectors[s].status = .searching
            sectors[s].searchTried = ev.keys_tried
            sectors[s].searchTotal = ev.keys_total
        } else if ev.total != nil {                       // sector boundary: this sector is done
            sectors[s].searchTried = nil
            sectors[s].searchTotal = nil
            if let kh = ev.key {
                sectors[s].status = .found
                sectors[s].keyType = ev.keytype
                sectors[s].keyHex = kh
                sectors[s].provenance = (kh == "ffffffffffff") ? .dictionary : .nonDefault
            } else {
                sectors[s].status = .failed
            }
        }
    }

    static func buildPages(_ r: NtagResult) -> [NtagPage] {
        guard let pages = r.pages else { return [] }
        return pages.compactMap { k, hex -> NtagPage? in
            guard let i = Int(k) else { return nil }
            return NtagPage(index: i, hex: hex, ascii: asciiOf(hex))
        }.sorted { $0.index < $1.index }
    }

    /// Printable ASCII rendering of a space-separated hex page (non-printable -> '.').
    static func asciiOf(_ hex: String) -> String {
        let bytes = hex.split(separator: " ").compactMap { UInt8($0, radix: 16) }
        return String(bytes.map { (32...126).contains($0) ? Character(UnicodeScalar($0)) : "." })
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
            return SectorVM(index: s, keyType: kt, keyHex: kh, provenance: prov, blocks: blocks,
                            status: kh == nil ? .failed : .found)
        }
    }

    /// The full sector grid, all pending, shown the instant decode starts so the
    /// card's memory map is visible and fills in live sector by sector.
    static func pendingSectors(count: Int) -> [SectorVM] {
        (0..<count).map {
            SectorVM(index: $0, keyType: nil, keyHex: nil, provenance: .unknown, blocks: [], status: .pending)
        }
    }

    // ---- copy (plain text) -------------------------------------------------

    /// Plain-text rendering of a sector: a header line with the key, then one
    /// line per block (absolute block number + hex). Used by ⌘C and the tile
    /// context menu so the grid is a real, copyable instrument.
    func sectorText(_ s: SectorVM) -> String {
        var head = "sector \(s.index)"
        if let kh = s.keyHex { head += "  (key \(s.keyType?.lowercased() ?? "a") \(kh))" }
        let base = firstBlock(s.index)
        let body = s.blocks.enumerated().map { i, hex in String(format: "%3d  %@", base + i, hex) }
        return ([head] + body).joined(separator: "\n")
    }

    /// Plain-text rendering of a single block: absolute block number + hex.
    func blockText(_ blk: Int, hex: String) -> String {
        String(format: "%3d  %@", blk, hex)
    }

    /// Plain text for ⌘C: the selected sector, or the whole NTAG page dump.
    func copySelectionText() -> String? {
        if let s = selectedSector { return sectorText(s) }
        if !pages.isEmpty {
            return pages.map { String(format: "%3d  %@  |%@|", $0.index, $0.hex, $0.ascii) }.joined(separator: "\n")
        }
        return nil
    }

    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    // ---- clone / write -----------------------------------------------------

    /// Write the loaded source dump onto the card on the reader. Data blocks
    /// only by default; trailers (keys/access) and block 0 (uid) are opt-in.
    func clone(trailers: Bool, uid: Bool) async {
        guard let src = cloneSource, !cloning else { return }
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

    /// Factory-reset the card on the reader (zero data + factory trailer). Uses
    /// the keys from the last decode so it can auth; destructive, so the UI gates
    /// it behind a confirm. After a successful format the decode is cleared - the
    /// card is blank and should be re-read to confirm.
    func format() async {
        guard card != nil, !formatting else { return }
        formatting = true
        cloneResults = [:]
        lastError = nil
        do {
            let r = try await engine.formatCard(keys: liveDump?.keyParams ?? [:])
            if r.present == false {
                lastError = "no card on reader"
            } else {
                if let failed = r.failed, !failed.isEmpty {
                    lastError = "\(failed.count) block(s) could not be formatted: \(failed)"
                }
                withAnimation(.easeInOut(duration: 0.3)) { sectors = []; selected = nil; liveDump = nil }
            }
        } catch {
            lastError = "\(error)"
        }
        formatting = false
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

    /// Default save name `yymmdd_tr_<uid>.dump` - sorts next to the Windows nfcPro
    /// dumps in the same folder and stays a plain raw image both tools can open.
    static func defaultDumpFilename(_ dump: CardDump) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyMMdd"
        let uid = dump.uid.replacingOccurrences(of: " ", with: "").lowercased()
        let stem = uid.isEmpty ? dump.name : uid
        return "\(f.string(from: Date()))_tr_\(stem).dump"
    }

    func saveDumpDialog() {
        guard let dump = liveDump else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = Self.defaultDumpFilename(dump)
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
