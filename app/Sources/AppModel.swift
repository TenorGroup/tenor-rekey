import SwiftUI
import Observation
import AppKit

/// Observable app state. The decoded / loaded image is the DOCUMENT; the card on the
/// reader is a separate live device state. Heavy work stays on the device bridge
/// actor; this holds @MainActor UI state only.
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
    /// A decode finished but recovered NO key: the card's keys are not in the
    /// dictionary. Shown as an honest "no keys" result instead of a fake empty grid.
    var noKeysFound = false
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

    /// The active device bridge, chosen by the registry at connect and swapped on
    /// hot-plug. Created lazily for `descriptor` (the daemon itself starts on its
    /// first request), so there is no bridge before the first connect.
    private var bridge: DeviceBridge?
    /// The descriptor of the device the bridge currently drives. X7 by default, so a
    /// bare machine (or one where detection has not run yet) behaves exactly as the
    /// single-device build did; a detected Chameleon swaps it.
    private var descriptor: DeviceDescriptor = DeviceRegistry.fallback
    /// A device swap (or reconnect that changes the device) is tearing down the old
    /// daemon and bringing up the new one.
    ///
    /// INVARIANT: no device op (decode / clone / format / apdu) or reconnect may run
    /// while `swapping` is true, and the old bridge is unreachable the instant it is
    /// set (the swap detaches `bridge` synchronously before its first await). This is
    /// what makes a hot-swap atomic from the UI's point of view: an op started during
    /// the teardown await cannot grab the just-terminated bridge or the stale card.
    private var swapping = false

    /// A device op already owns the reader. Reconnect / swap must not replace the
    /// bridge under one, and a second op must not start while one runs.
    private var deviceBusy: Bool { decoding || cloning || formatting || apduBusy }

    /// The active bridge, created lazily for the current descriptor. A prior bridge
    /// for a different device is torn down explicitly on the swap path (which nils
    /// `bridge` before the descriptor changes), so this only ever creates the bridge
    /// that matches - it never silently orphans a running daemon.
    private func activeBridge() -> DeviceBridge {
        if let b = bridge, b.descriptor.id == descriptor.id { return b }
        let b = DeviceBridge(descriptor: descriptor)
        bridge = b
        return b
    }

    /// The connected device's capability manifest, read by the shell to gate
    /// device-specific UI. Prefers the daemon's declared manifest; falls back to the
    /// active descriptor's static defaults before `info` lands or when a daemon
    /// predates the manifest.
    var capabilities: DeviceCapabilities { info?.capabilities ?? descriptor.capabilities }

    /// The user's editable keys (Settings > Dictionaries), tried before the
    /// daemon's large built-in dictionary.
    let keyStore = KeyStore()
    /// Size of the daemon's built-in curated dictionary (shown in Settings).
    var builtinKeyCount = 0
    /// Keys the daemon has learned from real cards and reranks decodes with (Settings).
    var learnedKeyCount = 0

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

    /// Detect the connected device, then start its daemon + read device info and look
    /// for a card (connect at launch, not lazily). With no device detected we fall
    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
    ///
    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
    /// reconnect can never replace the bridge under a running decode / clone. When the
    /// detected device differs from the current one it routes through `swapDevice` so
    /// the old daemon is torn down (never silently orphaned) under the swap guard.
    func connect() async {
        guard !swapping, !deviceBusy else { return }
        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
        if bridge != nil, found.id != descriptor.id {
            await swapDevice(to: found)
            return
        }
        descriptor = found
        await openCurrentDevice()
    }

    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
    /// then sample the reader. Shared by the first connect and a hot-swap, so both
    /// paths land the same state (info, capabilities via `info`, reader/card status).
    private func openCurrentDevice() async {
        let b = activeBridge()
        do {
            info = try await b.info()
            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
            readerOnline = true
            lastError = nil
            await refreshStatus()
        } catch {
            applyReaderGone()
            lastError = "\(error)"
        }
    }

    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
    /// as decodes recover keys).
    func refreshLearnedCount() async {
        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
    }

    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
    func clearLearnedKeys() async {
        try? await activeBridge().clearLearnedKeys()
        await refreshLearnedCount()
    }

    /// Live status: keep the reader / card pill honest when the X7 or a card is
    /// plugged or removed with no user action. Runs until the view's task is
    /// cancelled. Skips polling during an operation that already owns the reader.
    func monitor() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1.5))
            if deviceBusy || swapping { continue }
            // Hot-swap: if a different device is now the best USB match (the old one
            // was unplugged and another plugged in), tear down the current daemon and
            // bring the new one up before the next status sample. Detection is a cheap
            // IORegistry scan. With only the X7 involved, detect() keeps returning the
            // same descriptor, so this path is inert and the poll below is unchanged.
            if let found = DeviceRegistry.detect(), found.id != descriptor.id {
                await swapDevice(to: found)
                continue
            }
            await refreshStatus()
        }
    }

    /// Replace the active device with a freshly detected one. Every synchronous state
    /// change happens BEFORE the first await, so the teardown await (a MainActor
    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
    /// already nil. Only reader-bound state is cleared; the writable document is
    /// device-independent and is deliberately kept across the swap.
    private func swapDevice(to found: DeviceDescriptor) async {
        guard !swapping else { return }
        swapping = true
        defer { swapping = false }              // released however this returns
        let old = bridge
        bridge = nil                            // detach: no path can obtain the old bridge now
        descriptor = found
        withAnimation(.easeInOut(duration: 0.3)) {
            readerOnline = false
            info = nil
            card = nil
            clearCardBound()
        }
        await old?.shutdown()                   // bounded terminate + drain of the old daemon
        await openCurrentDevice()               // creates + brings up the new bridge
    }

    /// Consecutive polls that saw the reader but no card; a seated card that blips
    /// for one cycle should not drop its decoded grid, so we debounce a removal.
    private var cardAbsentStreak = 0

    /// One status sample: detects reader unplug (drops to offline + clears), reader
    /// replug (back online + refetch device info), and card placed / removed.
    private func refreshStatus() async {
        do {
            let p = try await activeBridge().poll(tries: 8)
            if p.reader == false {           // reader unplugged: reflect it at once
                applyReaderGone()
                return
            }
            readerOnline = true
            if info == nil { info = try? await activeBridge().info() }   // refetch until it lands
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
        noKeysFound = false
    }

    /// Drop the working document entirely (the source tag's clear button): the image,
    /// its grid, page dump, and selection. The card on the reader is untouched.
    func clearDocument() {
        withAnimation(.easeInOut(duration: 0.3)) {
            source = nil; sectors = []; pages = []; selected = nil; selectedBlock = nil
            cloneResults = [:]; noKeysFound = false
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
        // Refuse while a swap is tearing the device down, or another device op already
        // owns the reader (deviceBusy includes `decoding`, so this also blocks a
        // double-decode). Serialized, never racing the bridge.
        guard !swapping, !deviceBusy else { return }
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
            noKeysFound = false
        }
        do {
            if card?.isNTAG == true {
                // NTAG / Ultralight: a read-only page view, not a writable document.
                let r = try await activeBridge().readNTAG()
                let pgs = Self.buildPages(r)
                withAnimation(.easeInOut(duration: 0.3)) { pages = pgs; source = nil }
            } else {
                // show the whole grid right away (all pending) so sectors fill in
                // live as each one is searched, instead of a blank wait.
                let count = card?.sak.map { sectorsForSak($0) } ?? 16
                withAnimation(.easeInOut(duration: 0.3)) {
                    sectors = Self.pendingSectors(count: count)
                }
                let r = try await activeBridge().decode(userKeys: keyStore.keys,
                    onProgress: { [weak self] ev in Task { @MainActor in self?.applyDecodeEvent(ev) } })
                if let startUID, Self.normUID(r.uid) != Self.normUID(startUID) {
                    lastError = "card changed during decode"
                    withAnimation(.easeInOut(duration: 0.3)) { sectors = []; source = nil }
                } else if r.recovered == 0 {
                    // No key in the dictionary within the scan budget. Never turn an
                    // all-unread card into a clone-ready document (its blocks would be
                    // zeros); show an honest "no keys" result pointing at recovery.
                    withAnimation(.easeInOut(duration: 0.3)) {
                        card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                        sectors = []; pages = []; selected = nil; source = nil
                        noKeysFound = true
                    }
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
        await activeBridge().cancel()
    }

    /// Fold a decode progress event into `decodeProgress`. The daemon emits a
    /// sector-boundary event (carries `total` = sector count) and a dictionary-walk
    /// event (carries attempts / walk_total) as it searches a sector's key.
    private func applyDecodeEvent(_ ev: EngineEvent) {
        guard decoding, let s = ev.sector else { return }
        let fallbackTotal = card?.sak.map { sectorsForSak($0) } ?? 16
        var p = decodeProgress ?? DecodeProgress(sector: 0, total: fallbackTotal, attempts: nil, walkTotal: nil)
        p.sector = s
        // `attempts` is the scan's GLOBAL, monotonic auth counter, so keep it across
        // sector boundaries - the status line then only ever moves forward. `walkTotal`
        // is the adaptive remaining-work estimate and shrinks as sectors resolve.
        if let t = ev.total { p.total = t }
        if let a = ev.attempts { p.attempts = a; p.walkTotal = ev.walk_total }
        decodeProgress = p

        // per-sector live tile state
        guard sectors.indices.contains(s) else { return }
        if ev.attempts != nil {                           // walk event: this sector is searching
            sectors[s].status = .searching
            sectors[s].searchTried = ev.attempts
            sectors[s].searchTotal = ev.walk_total
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
        // Never clone while a swap is in flight or another device op owns the reader.
        guard !swapping, !deviceBusy else { return }
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
            let r = try await activeBridge().writeMFD(
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
        // Never format while a swap is in flight or another device op owns the reader.
        guard canFormat, !swapping, !deviceBusy else { return }
        formatting = true
        cloneResults = [:]
        lastError = nil
        let target = card?.uid
        do {
            let r = try await activeBridge().formatCard(keys: source?.keyParams ?? [:], targetUID: target)
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
        // Never send while a swap is in flight or another device op owns the reader.
        guard !clean.isEmpty, !swapping, !deviceBusy else { return }
        apduBusy = true
        let id = (apduLog.last?.id ?? 0) + 1
        do {
            let r = try await activeBridge().apdu(clean)
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
