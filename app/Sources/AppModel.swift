import SwiftUI
import Observation
import AppKit

/// Observable app state. The decoded / loaded image is the DOCUMENT; the card on the
/// reader is a separate live device state. Heavy work stays on the X7Engine actor;
/// this holds @MainActor UI state only.
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

    /// The working DOCUMENT: the image produced by a decode or loaded from a file.
    /// It is what the canvas shows, what Save writes out, and what Write clones onto
    /// the card on the reader. It is independent of whichever card is currently on
    /// the reader and deliberately persists across card swaps, so decoding a source
    /// card and writing it onto a blank needs no save/open dance and never visually
    /// vanishes when the source card is lifted.
    var source: CardDump?
    var cloneSheet = false
    var cloning = false
    /// Per-block write outcome from the last/in-flight clone (block -> ok). Tied to a
    /// specific target card, so it resets when the card on the reader changes.
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

    /// What "write" clones onto the card on the reader: the working document. It has
    /// no dependency on a card being present, so the write action is available as soon
    /// as there is something to write; the target card is asked for at write time.
    var cloneSource: CardDump? { source }

    /// Format erases the card on the reader, so it is only offered when the card
    /// present is the one this document was decoded from (same uid): only then do we
    /// hold its recovered keys to auth it, and only then is wiping it unambiguous.
    var canFormat: Bool {
        guard let c = card?.uid, let d = source?.uid else { return false }
        return Self.normUID(c) == Self.normUID(d)
    }

    static func normUID(_ s: String) -> String {
        s.replacingOccurrences(of: " ", with: "").lowercased()
    }

    /// Start the daemon + read device info, then look for a card (connect at launch,
    /// not lazily).
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
            // NOTE: do not clear lastError here - the 1.5s poll would wipe a clone /
            // decode / format error banner before the user could read it. Operations
            // clear it when they start; the banner also has a dismiss button.
            if p.present {
                cardAbsentStreak = 0
                // A different card (or first placement): the DOCUMENT stays (it is the
                // working image, not bound to this card); only the previous write's
                // per-block glyphs reset so they never show on the new card.
                if card == nil || p.uid != card?.uid {
                    withAnimation(.easeInOut(duration: 0.3)) { clearCardBound(); card = p }
                }
            } else {
                cardAbsentStreak += 1
                if card != nil && cardAbsentStreak >= 2 {
                    withAnimation(.easeInOut(duration: 0.3)) { card = nil; clearCardBound() }
                }
            }
        } catch {
            applyReaderGone()
        }
    }

    /// Reset state tied to the physical card on the reader: the last clone's per-block
    /// glyphs and the NTAG page view (NTAG has no writable document, so its pages are
    /// bound to the live card). The writable Classic DOCUMENT (sector grid + source
    /// image + selection) is deliberately kept so it survives a card swap, removal, or
    /// reader unplug - the working image is not bound to whatever card is on the
    /// reader. Shared by the swap, removal, and reader-gone paths so they cannot drift.
    private func clearCardBound() {
        cloneResults = [:]
        pages = []
    }

    /// Drop the working document entirely (the source tag's clear button): the image,
    /// its grid, page dump, and selection. The card on the reader is untouched.
    func clearDocument() {
        withAnimation(.easeInOut(duration: 0.3)) {
            source = nil; sectors = []; pages = []; selected = nil; selectedBlock = nil
            cloneResults = [:]
        }
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
            clearCardBound()
        }
    }

    func decode() async {
        let startUID = card?.uid
        guard !decoding else { return }
        decoding = true
        decodeCancelled = false
        decodeProgress = nil
        lastError = nil
        cloneResults = [:]
        // A fresh decode REPLACES the working document, so drop it up front: the
        // canvas then follows the card being read, and a decode that fails does not
        // leave the previous image's header sitting over a half-filled grid.
        withAnimation(.easeInOut(duration: 0.3)) {
            source = nil; sectors = []; pages = []; selected = nil; selectedBlock = nil
        }
        do {
            if card?.isNTAG == true {
                // NTAG / Ultralight: a read-only page view, not a writable document.
                let r = try await engine.readNTAG()
                let pgs = Self.buildPages(r)
                withAnimation(.easeInOut(duration: 0.3)) { pages = pgs; source = nil }
            } else {
                // show the whole grid right away (all pending) so sectors fill in
                // live as each one is searched, instead of a blank wait.
                let count = card?.sak.map { sectorsForSak($0) } ?? 16
                withAnimation(.easeInOut(duration: 0.3)) {
                    sectors = Self.pendingSectors(count: count)
                }
                let r = try await engine.decode(userKeys: keyStore.keys,
                    onProgress: { [weak self] ev in Task { @MainActor in self?.applyDecodeEvent(ev) } })
                if let startUID, Self.normUID(r.uid) != Self.normUID(startUID) {
                    lastError = "card changed during decode"
                    withAnimation(.easeInOut(duration: 0.3)) { sectors = []; source = nil }
                } else {
                    let vms = Self.buildSectors(r)
                    let dump = CardDump.from(r, name: r.uid.replacingOccurrences(of: " ", with: ""))
                    withAnimation(.easeInOut(duration: 0.3)) {
                        card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                        sectors = vms
                        pages = []
                        selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                        source = dump
                    }
                }
            }
        } catch {
            // a user cancel kills the daemon, which surfaces as a thrown error - not
            // something to show as a failure. Either way, do not leave a half-filled
            // grid or a stale image behind.
            if !decodeCancelled { lastError = "\(error)" }
            withAnimation(.easeInOut(duration: 0.3)) {
                sectors = []; pages = []; selected = nil; source = nil
            }
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

    /// Build the grid from a loaded dump (File > Open), so an opened image renders
    /// its memory map exactly like a fresh decode. Dump block hex is stored without
    /// spaces; re-space it to match the daemon's display form the grid expects.
    static func buildSectors(fromDump d: CardDump) -> [SectorVM] {
        (0..<d.sectorCount).map { s in
            let k = d.keys[s]
            let kh = k?.hex
            let prov: KeyProvenance = kh == nil
                ? .unknown
                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
            let blocks = blockNumbers(ofSector: s).map { b in d.blocks[b].map(spacedHex) ?? "?" }
            return SectorVM(index: s, keyType: k?.type, keyHex: kh, provenance: prov, blocks: blocks,
                            status: kh == nil ? .failed : .found)
        }
    }

    /// "0102..1f" -> "01 02 .. 1f" so dump-loaded blocks render like decode blocks.
    static func spacedHex(_ h: String) -> String {
        stride(from: 0, to: h.count, by: 2).compactMap { i -> String? in
            let start = h.index(h.startIndex, offsetBy: i)
            let end = h.index(start, offsetBy: min(2, h.count - i))
            return String(h[start..<end])
        }.joined(separator: " ")
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

    /// Write the explicit source dump onto the card on the reader. Data blocks
    /// only by default; trailers (keys/access) and block 0 (uid) are opt-in.
    func clone(trailers: Bool, uid: Bool) async {
        guard !cloning else { return }
        guard let src = cloneSource else {
            lastError = "no clone source"
            return
        }
        cloning = true
        cloneResults = [:]
        lastError = nil
        // Pin the write to the card the sheet just showed the user; the daemon refuses
        // if a different card slid onto the reader before it acquired one.
        let target = card?.uid
        do {
            let r = try await engine.writeMFD(
                blocks: src.blockParams, keys: src.keyParams, trailers: trailers, uid: uid,
                targetUID: target,
                onBlock: { [weak self] b, ok in
                    Task { @MainActor in
                        withAnimation(.easeOut(duration: 0.16)) { self?.cloneResults[b] = ok }
                    }
                })
            // Per-block glyphs in the grid/inspector are the primary failure surface;
            // lastError is the summary shown in the status banner.
            if r.present == false {
                lastError = "no card on reader"
            } else if let e = r.error {
                lastError = e
            } else if let failed = r.failed, !failed.isEmpty {
                lastError = "\(failed.count) block(s) failed to write: \(failed)"
            }
        } catch {
            lastError = "\(error)"
        }
        cloning = false
    }

    /// Factory-reset the card on the reader (zero data + factory trailer). Auths with
    /// the document's recovered keys; `canFormat` guarantees that document is this very
    /// card. Destructive, so the UI gates it behind a confirm. On success the document
    /// is dropped - the card is blank now, and its old image should not linger.
    func format() async {
        guard canFormat, !formatting else { return }
        formatting = true
        cloneResults = [:]
        lastError = nil
        let target = card?.uid
        do {
            let r = try await engine.formatCard(keys: source?.keyParams ?? [:], targetUID: target)
            if r.present == false {
                lastError = "no card on reader"
            } else if let e = r.error {
                lastError = e                      // aborted (wrong / swapped card): keep the image
            } else if let failed = r.failed, !failed.isEmpty {
                // A partial or fully failed format did NOT blank the card, so keep the
                // document: it may still be the only copy of the image.
                lastError = "\(failed.count) block(s) could not be formatted: \(failed)"
            } else {
                // Clean format: the card is factory now, so drop its stale image.
                withAnimation(.easeInOut(duration: 0.3)) {
                    source = nil; sectors = []; pages = []; selected = nil; selectedBlock = nil
                }
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
        guard let dump = source else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = Self.defaultDumpFilename(dump)
        panel.allowsOtherFileTypes = true
        if let folder = UserDefaults.standard.string(forKey: "rekey.exportFolder") {
            panel.directoryURL = URL(fileURLWithPath: folder)
        }
        if panel.runModal() == .OK, let url = panel.url { saveDump(dump, to: url) }
    }

    /// Open a dump as the working document: it becomes the source AND its memory map
    /// is rendered on the canvas (a loaded image is a document just like a decode), so
    /// the canvas never shows a stale grid under a freshly opened source.
    func loadDump(from url: URL) {
        do {
            let dump = try CardDump.load(mfd: url)
            let vms = Self.buildSectors(fromDump: dump)
            withAnimation(.easeInOut(duration: 0.3)) {
                source = dump
                sectors = vms
                pages = []
                selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                selectedBlock = nil
                cloneResults = [:]
            }
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
