import SwiftUI
import Observation

/// The user's editable MIFARE key list (Settings > Dictionaries). These are the
/// keys tried FIRST, before the daemon's large built-in curated dictionary - so
/// it holds only the user's own / site-specific keys, persisted across launches.
/// Order matters - user keys are tried in order, newest at the front.
@MainActor
@Observable
final class KeyStore {
    private static let udKey = "rekey.keyDictionary"
    var keys: [String] = []

    init() {
        if let arr = UserDefaults.standard.array(forKey: Self.udKey) as? [String] { keys = arr }
    }

    /// A valid key is exactly 12 hex chars (6 bytes), case-insensitive. Returns
    /// the lowercased form, or nil - validation, not silent stripping, so junk
    /// with embedded hex is rejected rather than mangled into a wrong key.
    /// `nonisolated` so the same rule can guard non-UI load paths (CardDump).
    nonisolated static func normalized(_ raw: String) -> String? {
        let k = raw.trimmingCharacters(in: .whitespaces).lowercased()
        return (k.count == 12 && k.allSatisfy(\.isHexDigit)) ? k : nil
    }

    @discardableResult
    func add(_ raw: String) -> Bool {
        guard let k = Self.normalized(raw), !keys.contains(k) else { return false }
        keys.insert(k, at: 0)
        save()
        return true
    }

    func remove(at offsets: IndexSet) { keys.remove(atOffsets: offsets); save() }
    func move(from: IndexSet, to: Int) { keys.move(fromOffsets: from, toOffset: to); save() }

    /// Import a .dic / .keys / .txt list: one key per line (first whitespace
    /// token), `#` comments ignored. Returns how many new keys were added.
    @discardableResult
    func importText(_ text: String) -> Int {
        var added = 0
        for line in text.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed.hasPrefix("#") { continue }
            let token = trimmed.split(whereSeparator: \.isWhitespace).first.map(String.init) ?? trimmed
            if add(token) { added += 1 }
        }
        return added
    }

    private func save() { UserDefaults.standard.set(keys, forKey: Self.udKey) }
}
