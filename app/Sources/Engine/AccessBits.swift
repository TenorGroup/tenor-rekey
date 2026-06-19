import Foundation

/// MIFARE Classic sector-trailer access conditions, decoded from access bytes
/// 6, 7, 8. Each sector has four access groups (C1, C2, C3). For a 1K (4-block)
/// sector the groups map 1:1 to data blocks 0, 1, 2 and the trailer; for a 4K
/// big sector (16 blocks) a group covers five blocks.
///
/// Bit layout (verified against the factory trailer ff 07 80):
///   byte6 low nibble = ~C1 , high nibble = ~C2
///   byte7 low nibble = ~C3 , high nibble =  C1
///   byte8 low nibble =  C2 , high nibble =  C3
struct AccessConditions: Equatable {
    var groups: [Triple]            // index 0..3, group 3 is the trailer
    var valid: Bool                 // inverted-complement integrity check held

    struct Triple: Equatable { var c1: Int; var c2: Int; var c3: Int }

    static func decode(trailer: [UInt8]) -> AccessConditions? {
        guard trailer.count == 16 else { return nil }
        let b6 = Int(trailer[6]), b7 = Int(trailer[7]), b8 = Int(trailer[8])
        func bit(_ v: Int, _ n: Int) -> Int { (v >> n) & 1 }
        var groups: [Triple] = []
        var valid = true
        for i in 0..<4 {
            let c1 = bit(b7, 4 + i), c2 = bit(b8, i), c3 = bit(b8, 4 + i)
            groups.append(Triple(c1: c1, c2: c2, c3: c3))
            // the inverted copies in b6 / b7 low nibble must be the complement
            if bit(b6, i) == c1 || bit(b6, 4 + i) == c2 || bit(b7, i) == c3 { valid = false }
        }
        return AccessConditions(groups: groups, valid: valid)
    }
}

/// Which access group a block belongs to inside its sector. A 1K sector's four
/// blocks map 1:1 to the four groups; a 4K big sector's 15 data blocks split into
/// three groups of five, with the trailer (block 15) in group 3.
func accessGroup(blockInSector i: Int, blocksInSector n: Int) -> Int {
    n <= 4 ? i : min(3, i / 5)
}

enum BlockRole { case manufacturer, data, trailer }

/// Block 0 of sector 0 is the read-only manufacturer block (uid + bcc); the last
/// block of every sector is its trailer; the rest are data.
func blockRole(absolute b: Int, sector s: Int) -> BlockRole {
    if b == 0 { return .manufacturer }
    if b == trailerBlock(s) { return .trailer }
    return .data
}

/// Compact read/write summary for a DATA block given its (C1,C2,C3) group.
/// A technical notation (kept verbatim, like uid/sak), not translated chrome.
func dataAccessSummary(_ c: AccessConditions.Triple) -> String {
    switch (c.c1, c.c2, c.c3) {
    case (0, 0, 0): return "r a|b  w a|b"
    case (0, 1, 0): return "r a|b  w -"
    case (1, 0, 0): return "r a|b  w b"
    case (1, 1, 0): return "r a|b  w b  value"
    case (0, 0, 1): return "r a|b  w -  value"
    case (0, 1, 1): return "r b  w b"
    case (1, 0, 1): return "r b  w -"
    case (1, 1, 1): return "no access"
    default: return "?"
    }
}

/// Compact summary for the TRAILER group: who writes the keys/access, and
/// whether keyB is readable (which means keyB is data, not a usable auth key).
func trailerAccessSummary(_ c: AccessConditions.Triple) -> String {
    switch (c.c1, c.c2, c.c3) {
    case (0, 0, 0): return "a writes keys  ·  keyB readable"
    case (0, 1, 0): return "keys locked  ·  keyB readable"
    case (1, 0, 0): return "b writes keys  ·  keyB is a key"
    case (1, 1, 0): return "keys locked"
    case (0, 0, 1): return "a writes keys+access  ·  keyB readable"
    case (0, 1, 1): return "b writes keys+access"
    case (1, 0, 1): return "b writes access  ·  keys locked"
    case (1, 1, 1): return "fully locked"
    default: return "?"
    }
}
