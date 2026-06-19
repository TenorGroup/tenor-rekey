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
