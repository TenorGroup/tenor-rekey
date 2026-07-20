import Foundation

/// One saved-card entry's metadata: what the library list renders (label, uid, tag
/// geometry, saved date). The card image + keys themselves live next to it on disk as
/// the app's own `.mfd` + `.mfd.keys.json` pair (so a saved card interoperates with
/// Save / Open and external tools), keyed by `id`. This lightweight index is what the
/// library shows without touching every card file.
struct SavedCard: Identifiable, Equatable, Sendable, Codable {
    let id: String        // uuid; the on-disk file stem for its .mfd pair
    var name: String      // the operator's label
    let uid: String       // display uid, mirrored from the saved dump
    let sak: Int          // 0x08 / 0x18 - the tag geometry, shown as a type
    let savedAt: Date
}

/// Errors the store surfaces to the model (shown in the status banner).
enum SavedCardStoreError: Error, LocalizedError {
    case corruptIndex     // the index is present but unreadable - a mutation refuses, so it is not clobbered
    case invalidId        // an id that is not a well-formed uuid (a crafted index cannot escape the cards dir)

    var errorDescription: String? {
        switch self {
        case .corruptIndex: return "the saved-cards library index is damaged; not overwriting it"
        case .invalidId: return "invalid saved-card id"
        }
    }
}

/// A persistent, FLAT library of saved card dumps under Application Support
/// (`~/Library/Application Support/tenor-rekey/cards/`). Each entry is a CardDump
/// serialized as the app's own `.mfd` + `.mfd.keys.json` pair, indexed by a small
/// `index.json` the list renders from. Flat by design: no folders (later scope).
///
/// The index is untrusted on read (it may have been hand-edited or corrupted): its size
/// is checked before the read, every id is validated as a uuid before it is used as a
/// path component (no directory escape), and a CORRUPT index is distinguished from an
/// empty one so a mutation never overwrites a damaged index and orphans every card.
/// Mutations are transaction-ordered: the index is committed atomically, and a delete
/// removes the backing files only AFTER that commit.
struct SavedCardStore {
    let dir: URL

    /// Default location under Application Support. Falls back to a temp directory if the
    /// support directory cannot be resolved, so the store is always usable.
    static func standard() -> SavedCardStore {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return SavedCardStore(dir: base.appendingPathComponent("tenor-rekey/cards", isDirectory: true))
    }

    /// Cap on the index we will read into memory. Even thousands of cards make a tiny index;
    /// anything larger is treated as corrupt rather than read.
    private static let maxIndexBytes = 4 * 1024 * 1024

    private var indexURL: URL { dir.appendingPathComponent("index.json") }
    private func mfdURL(_ id: String) -> URL { dir.appendingPathComponent(id).appendingPathExtension("mfd") }
    private func sidecarURL(_ id: String) -> URL { mfdURL(id).appendingPathExtension("keys.json") }

    // ---- list --------------------------------------------------------------

    /// Every saved card, newest first. Robust: a missing / empty / corrupt / oversize index
    /// all yield an empty list rather than throwing. An entry whose backing `.mfd` file has
    /// vanished, or whose id is not a valid uuid, is skipped, so the list never points at a
    /// card that cannot be loaded (and no crafted id ever reaches the filesystem).
    func list() -> [SavedCard] {
        indexForRead()
            .filter { FileManager.default.fileExists(atPath: mfdURL($0.id).path) }
            .sorted { $0.savedAt > $1.savedAt }
    }

    // ---- save --------------------------------------------------------------

    /// Persist a working document as a new library entry, returning its metadata. Refuses
    /// (throws) when the index is corrupt, so a save never clobbers a damaged index. Writes
    /// the `.mfd` + sidecar pair, then appends the entry to the index atomically; a failure
    /// after the files are written rolls them back so no orphan pair is left behind.
    @discardableResult
    func save(_ dump: CardDump, name: String) throws -> SavedCard {
        var all = try indexForMutation()
        try ensureDir()
        let id = UUID().uuidString                      // always a valid uuid path component
        do {
            try dump.mfdData().write(to: mfdURL(id), options: .atomic)
            try dump.keysJSON().write(to: sidecarURL(id), options: .atomic)
            let entry = SavedCard(id: id, name: Self.resolveName(name, dump: dump),
                                  uid: dump.uid, sak: dump.sak, savedAt: Date())
            all.append(entry)
            try writeIndex(all)
            return entry
        } catch {
            try? FileManager.default.removeItem(at: mfdURL(id))
            try? FileManager.default.removeItem(at: sidecarURL(id))
            throw error
        }
    }

    // ---- load --------------------------------------------------------------

    /// Load a saved card back into a CardDump (reusing the `.mfd` + sidecar loader), with
    /// its stored library label as the name. Throws for an invalid id or a missing file.
    func load(_ id: String) throws -> CardDump {
        guard UUID(uuidString: id) != nil else { throw SavedCardStoreError.invalidId }
        var dump = try CardDump.load(mfd: mfdURL(id))
        if let meta = indexForRead().first(where: { $0.id == id }) { dump.name = meta.name }
        return dump
    }

    // ---- rename / delete ---------------------------------------------------

    /// Relabel an entry. A blank new name is ignored (keeps the old label). Refuses on a
    /// corrupt index (would clobber it).
    func rename(_ id: String, to name: String) throws {
        guard UUID(uuidString: id) != nil else { throw SavedCardStoreError.invalidId }
        var all = try indexForMutation()
        guard let i = all.firstIndex(where: { $0.id == id }) else { return }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        all[i].name = trimmed
        try writeIndex(all)
    }

    /// Remove an entry. Transaction-ordered: commit the index WITHOUT the entry first (so a
    /// failure leaves the entry + its files consistent), THEN best-effort remove the now
    /// unreferenced backing files - a crash between the two only leaves a harmless orphan the
    /// list already ignores. Refuses on a corrupt index.
    func delete(_ id: String) throws {
        guard UUID(uuidString: id) != nil else { throw SavedCardStoreError.invalidId }
        var all = try indexForMutation()
        all.removeAll { $0.id == id }
        try writeIndex(all)
        try? FileManager.default.removeItem(at: mfdURL(id))
        try? FileManager.default.removeItem(at: sidecarURL(id))
    }

    // ---- internals ---------------------------------------------------------

    /// The parsed state of the index, so a genuinely empty / missing index (a fresh store)
    /// is not confused with a corrupt one (present but unreadable / unparseable / oversize).
    private enum IndexState { case entries([SavedCard]); case emptyOrMissing; case corrupt }

    /// Read + classify the index. Size is checked before the read (an oversize index is
    /// corrupt, not loaded). Entries whose id is not a valid uuid are dropped, so a crafted
    /// index can never yield a path-escaping id.
    private func readIndex() -> IndexState {
        guard FileManager.default.fileExists(atPath: indexURL.path) else { return .emptyOrMissing }
        guard let size = try? indexURL.resourceValues(forKeys: [.fileSizeKey]).fileSize else { return .corrupt }
        if size == 0 { return .emptyOrMissing }
        guard size <= Self.maxIndexBytes, let data = try? Data(contentsOf: indexURL),
              let entries = try? Self.decoder.decode([SavedCard].self, from: data) else { return .corrupt }
        return .entries(entries.filter { UUID(uuidString: $0.id) != nil })
    }

    /// The index for a read (list): any failure or corruption yields an empty list.
    private func indexForRead() -> [SavedCard] {
        if case .entries(let e) = readIndex() { return e }
        return []
    }

    /// The index for a mutation: an empty / missing index is a fresh `[]`; a CORRUPT index
    /// throws, so a save / rename / delete refuses rather than overwriting it and orphaning
    /// every existing card.
    private func indexForMutation() throws -> [SavedCard] {
        switch readIndex() {
        case .entries(let e): return e
        case .emptyOrMissing: return []
        case .corrupt: throw SavedCardStoreError.corruptIndex
        }
    }

    private func writeIndex(_ entries: [SavedCard]) throws {
        try ensureDir()
        try Self.encoder.encode(entries).write(to: indexURL, options: .atomic)
    }

    private func ensureDir() throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    /// A label for a save: the trimmed input, or the uid, or the dump name, or "card".
    static func resolveName(_ raw: String, dump: CardDump) -> String {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { return t }
        let uid = dump.uid.replacingOccurrences(of: " ", with: "")
        if !uid.isEmpty { return uid }
        return dump.name.isEmpty ? "card" : dump.name
    }

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        return e
    }()
    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}
