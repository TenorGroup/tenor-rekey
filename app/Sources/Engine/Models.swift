import Foundation

/// Decoded shapes from the x7d.py daemon contract (probe/x7d.py).

struct DeviceInfo: Codable, Equatable {
    let model: String
    let serial: String
    let hw: String
    /// Device family the daemon reports ("x7" / "chameleon-ultra" / "chameleon-lite").
    /// Optional so an older daemon that omits it decodes to nil rather than failing.
    var family: String? = nil
    /// The device's capability manifest, used to gate device-specific UI. Optional
    /// so a daemon that predates the manifest decodes to nil; the shell then falls
    /// back to the active descriptor's static capabilities.
    var capabilities: DeviceCapabilities? = nil
}

struct PollResult: Codable, Equatable {
    let present: Bool
    let uid: String?
    let atqa: String?
    let sak: Int?
    /// Whether the reader itself is connected (nil from older daemons -> treat as
    /// connected). `present` is a card on the reader; `reader` is the reader.
    var reader: Bool? = nil
    /// Card family from the daemon ("ntag" / "classic"); nil from older daemons,
    /// in which case `isNTAG` falls back to the ATQA/SAK rule.
    var kind: String? = nil

    /// True for a genuine NTAG / Ultralight. Prefers the daemon's `kind`; falls
    /// back to the ATQA/SAK rule (SAK 0x00 AND ATQA 0x0044). A magic/blank Classic
    /// reports SAK 0x00 with ATQA 0x0004 and must NOT be treated as NTAG.
    var isNTAG: Bool {
        if let kind { return kind == "ntag" }
        let a = (atqa ?? "").replacingOccurrences(of: " ", with: "").lowercased()
        return sak == 0x00 && a == "0044"
    }
}

struct DecodeResult: Codable, Equatable {
    let uid: String
    let atqa: String
    let sak: Int
    let sectors: Int
    let recovered: Int
    let attempts: Int?                  // auth attempts the scan spent
    let exhausted: Bool?               // true if the scan budget ran out before a full search
    /// True when the walk stopped on a cooperative cancel rather than finishing; the
    /// blocks/keys below are then whatever partial image was gathered so far. Optional
    /// so an older daemon that omits it decodes to nil.
    let cancelled: Bool?
    /// Sectors whose OTHER key slot (A or B) was not readable and was mirrored from the
    /// recovered slot: sector-index string -> the assumed slot ("A" / "B"). The mirrored
    /// value is a guess, not a read key. Optional (older daemons omit it). JSON key is
    /// `assumed_keys`, matching the daemon contract (like `walk_total` on EngineEvent).
    let assumed_keys: [String: String]?
    let blocks: [String: String?]      // block index -> hex, or null if unreadable
    let keys: [String: [String]?]      // sector -> [keytype, keyhex], or null
}

/// Result of a write_mfd / clone. `present` is false when no card was on the
/// reader; otherwise `wrote` counts blocks written and `failed` lists block
/// indices that could not be written.
struct WriteResult: Codable, Sendable {
    let present: Bool
    let wrote: Int?
    let failed: [Int]?
    let error: String?      // set when the daemon aborted (wrong / swapped target card)
}

/// Result of a format (factory reset). `present` is false when no card.
struct FormatResult: Codable, Sendable {
    let present: Bool
    let formatted: Int?
    let failed: [Int]?
    let error: String?      // set when the daemon aborted (wrong / swapped target card)
}

/// Result of an NTAG / Ultralight (SAK 0x00) page dump.
struct NtagResult: Codable, Sendable {
    let present: Bool
    let uid: String?
    let sak: Int?
    let pages: [String: String]?    // page index -> 4-byte hex
}

/// One NTAG page row for the page table.
struct NtagPage: Identifiable, Equatable {
    let index: Int
    let hex: String
    let ascii: String
    var id: Int { index }
}

/// Result of an apdu passthrough. `present` is false when no card; `resp` is
/// the response hex (space-separated) or nil when the card gave no answer
/// (e.g. a MIFARE Classic that is not ISO14443-4).
struct ApduResult: Codable, Sendable {
    let present: Bool
    let uid: String?
    let sak: Int?
    let resp: String?
}

/// One line of the apdu console transcript.
struct ApduEntry: Identifiable, Equatable {
    let id: Int
    let tx: String          // command hex, lowercased
    let rx: String?         // response hex, or nil for a non-data outcome
    let info: String?       // l10n key for a non-data outcome (no response / no card)
}

/// An id-less progress event emitted by the daemon mid-operation. Fields are
/// optional because each method emits a different subset (write_mfd: block/ok;
/// decode: sector/total/keytype; nested_recover: phase).
struct EngineEvent: Decodable, Sendable {
    let event: String
    let method: String
    let block: Int?
    let ok: Bool?
    let sector: Int?
    let total: Int?
    let keytype: String?
    let key: String?          // decode: the key found for `sector` (nil = not found)
    let phase: String?        // decode walk phase: "hot" (common/hotel) or "dict"
    let attempts: Int?        // decode: cumulative auth attempts spent in the walk
    let walk_total: Int?      // decode: adaptive remaining-work estimate (shrinks)
    let unsafe: String?       // write_mfd: why a trailer was refused ("access-bits" / "trailer-lockout")
}

/// How a sector's key was obtained - drives the provenance dot.
enum KeyProvenance: Equatable {
    case nonDefault   // a known, non-factory key (e.g. a0b1c2d3e4f5)
    case dictionary   // a factory / dictionary key (ffffffffffff)
    case nested       // recovered by the nested attack
    case unknown      // not recovered

    var locKey: String {
        switch self {
        case .nonDefault: "prov_nondefault"
        case .dictionary: "prov_dictionary"
        case .nested: "prov_nested"
        case .unknown: "prov_unknown"
        }
    }
}

/// Live decode progress for the spinner: which sector, and how far into the key
/// dictionary that sector's search has walked (nil when a known key hit at once).
struct DecodeProgress: Equatable {
    var sector: Int
    var total: Int
    var attempts: Int?      // cumulative auth attempts spent walking the dictionary
    var walkTotal: Int?     // adaptive remaining-work estimate (unresolved * dict size)

    /// 0...1 fallback fraction. A normal card resolves via key reuse almost instantly
    /// (no walk), so we show sector progress; once the walk starts reporting attempts
    /// the ratio to the adaptive remaining-work total is a rough measure. The visible
    /// decode bar stays indeterminate, so this never drives a jumpy determinate bar.
    var fraction: Double {
        if let a = attempts, let w = walkTotal, w > 0 { return min(1, Double(a) / Double(w)) }
        guard total > 0 else { return 0 }
        return min(1, Double(sector) / Double(total))
    }
}

/// Live decode state of a sector tile.
enum SectorStatus: Equatable {
    case pending      // not started yet
    case searching    // walking the key dictionary now
    case found        // key recovered
    case failed       // searched, no key in the dictionary
}

/// One sector, as the grid + inspector need it.
struct SectorVM: Identifiable, Equatable {
    let index: Int
    var keyType: String?      // "A" / "B"
    var keyHex: String?
    var provenance: KeyProvenance
    var blocks: [String]      // hex lines for this sector's data blocks ("?" if unreadable)
    var status: SectorStatus = .found
    var searchTried: Int? = nil
    var searchTotal: Int? = nil
    /// The trailer key slot ("A" / "B") that was mirrored from the recovered slot
    /// rather than read (its real value is a guess). nil when both slots are genuine.
    var assumedSlot: String? = nil

    var id: Int { index }
    var hasKey: Bool { keyHex != nil }
}
