import Foundation

/// Decoded shapes from the x7d.py daemon contract (probe/x7d.py).

struct DeviceInfo: Codable, Equatable {
    let model: String
    let serial: String
    let hw: String
}

struct PollResult: Codable, Equatable {
    let present: Bool
    let uid: String?
    let atqa: String?
    let sak: Int?
    /// Whether the reader itself is connected (nil from older daemons -> treat as
    /// connected). `present` is a card on the reader; `reader` is the reader.
    var reader: Bool? = nil
}

struct DecodeResult: Codable, Equatable {
    let uid: String
    let atqa: String
    let sak: Int
    let sectors: Int
    let recovered: Int
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
}

/// Result of a format (factory reset). `present` is false when no card.
struct FormatResult: Codable, Sendable {
    let present: Bool
    let formatted: Int?
    let failed: [Int]?
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
    let phase: String?
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

/// One sector, as the grid + inspector need it.
struct SectorVM: Identifiable, Equatable {
    let index: Int
    let keyType: String?      // "A" / "B"
    let keyHex: String?
    let provenance: KeyProvenance
    let blocks: [String]      // hex lines for this sector's data blocks ("?" if unreadable)

    var id: Int { index }
    var hasKey: Bool { keyHex != nil }
}
