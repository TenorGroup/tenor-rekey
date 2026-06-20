import Foundation

/// A whole-card image: the data blocks + the per-sector keys. This is the
/// "document" that clone writes from and that File > Open / Save round-trips,
/// in the same `.mfd` + `.keys.json` format the CLI (x7tool) uses, so dumps
/// interoperate. Blocks are stored as compact lowercase hex (32 chars).
struct SectorKey: Equatable, Sendable {
    var type: String   // "A" / "B"
    var hex: String    // 12-char key
}

struct CardDump: Equatable, Sendable {
    var uid: String                 // display form (may carry spaces, as in .keys.json)
    var sak: Int
    var sectorCount: Int
    var blocks: [Int: String]       // block index -> 32-char hex
    var keys: [Int: SectorKey]      // sector -> key
    var name: String                // file stem, or a live-decode label

    /// Build from a live daemon decode.
    static func from(_ r: DecodeResult, name: String) -> CardDump {
        var blocks: [Int: String] = [:]
        for (k, v) in r.blocks {
            if let v, let i = Int(k) { blocks[i] = v.replacingOccurrences(of: " ", with: "") }
        }
        var keys: [Int: SectorKey] = [:]
        for (k, v) in r.keys {
            if let v, v.count == 2, let i = Int(k) { keys[i] = SectorKey(type: v[0], hex: v[1]) }
        }
        return CardDump(uid: r.uid, sak: r.sak, sectorCount: r.sectors,
                        blocks: blocks, keys: keys, name: name)
    }

    // ---- serialization (matches x7tool.save_mfd) ---------------------------

    /// Flat binary image: block index * 16 bytes; missing blocks left as zero.
    func mfdData() -> Data {
        let size = sak == 0x18 ? 4096 : 1024
        var buf = Data(count: size)
        for (b, hex) in blocks {
            guard (b + 1) * 16 <= size, let bytes = Data(hexString: hex), bytes.count == 16 else { continue }
            buf.replaceSubrange(b * 16 ..< b * 16 + 16, with: bytes)
        }
        return buf
    }

    /// Sidecar: {"uid", "sak", "keys": {sector: [kt, key] | null}}.
    func keysJSON() throws -> Data {
        var keysDict: [String: Any] = [:]
        for s in 0..<sectorCount {
            if let k = keys[s] { keysDict[String(s)] = [k.type, k.hex] }
            else { keysDict[String(s)] = NSNull() }
        }
        let obj: [String: Any] = ["uid": uid, "sak": sak, "keys": keysDict]
        return try JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    }

    /// Load a `.mfd` image + its `<name>.mfd.keys.json` sidecar (if present).
    static func load(mfd url: URL) throws -> CardDump {
        let data = try Data(contentsOf: url)
        let sidecar = url.appendingPathExtension("keys.json")
        var uid = ""
        var sak = data.count > 1024 ? 0x18 : 0x08
        var keys: [Int: SectorKey] = [:]
        if let sjson = try? Data(contentsOf: sidecar),
           let obj = try? JSONSerialization.jsonObject(with: sjson) as? [String: Any] {
            if let u = obj["uid"] as? String { uid = u }
            if let sk = obj["sak"] as? Int { sak = sk }
            if let kd = obj["keys"] as? [String: Any] {
                for (k, v) in kd {
                    if let arr = v as? [String], arr.count == 2, let i = Int(k) {
                        keys[i] = SectorKey(type: arr[0], hex: arr[1])
                    }
                }
            }
        }
        var blocks: [Int: String] = [:]
        for b in 0..<(data.count / 16) {
            blocks[b] = data.subdata(in: b * 16 ..< b * 16 + 16).hexCompact
        }
        // No sidecar (e.g. a Windows nfcPro .dump): recover the uid + keys straight
        // from the raw image. The uid is the first 4 bytes of block 0; each sector's
        // KeyA / KeyB sit in its trailer (bytes 0-5 / 10-15). All-zero means the key
        // was not stored, so skip it.
        if uid.isEmpty, let b0 = blocks[0], let by = Data(hexString: b0), by.count >= 4 {
            uid = by.prefix(4).map { String(format: "%02x", $0) }.joined(separator: " ")
        }
        if keys.isEmpty {
            for s in 0..<sectorsForSak(sak) {
                guard let hex = blocks[trailerBlock(s)], let by = Data(hexString: hex), by.count == 16 else { continue }
                let keyA = Data(by.prefix(6)).hexCompact
                let keyB = Data(by.suffix(6)).hexCompact
                if keyA != "000000000000" { keys[s] = SectorKey(type: "A", hex: keyA) }
                else if keyB != "000000000000" { keys[s] = SectorKey(type: "B", hex: keyB) }
            }
        }
        let stem = url.deletingPathExtension().lastPathComponent
        return CardDump(uid: uid, sak: sak, sectorCount: sectorsForSak(sak),
                        blocks: blocks, keys: keys, name: stem)
    }

    /// Source -> daemon params (string-keyed, the x7d.py write_mfd contract).
    var blockParams: [String: String] {
        blocks.reduce(into: [:]) { $0[String($1.key)] = $1.value }
    }
    var keyParams: [String: [String]] {
        keys.reduce(into: [:]) { $0[String($1.key)] = [$1.value.type, $1.value.hex] }
    }
}

// ---- MIFARE Classic block/sector layout (shared) ---------------------------

func sectorsForSak(_ sak: Int) -> Int { sak == 0x18 ? 40 : 16 }   // 4K vs 1K
func blocksInSector(_ s: Int) -> Int { s < 32 ? 4 : 16 }             // 4K big sectors
func firstBlock(_ s: Int) -> Int { s < 32 ? s * 4 : 128 + (s - 32) * 16 }
func trailerBlock(_ s: Int) -> Int { firstBlock(s) + blocksInSector(s) - 1 }
func blockNumbers(ofSector s: Int) -> [Int] { Array(firstBlock(s)...trailerBlock(s)) }

extension Data {
    init?(hexString: String) {
        let s = hexString.replacingOccurrences(of: " ", with: "")
        guard s.count % 2 == 0 else { return nil }
        var d = Data(capacity: s.count / 2)
        var i = s.startIndex
        while i < s.endIndex {
            let j = s.index(i, offsetBy: 2)
            guard let b = UInt8(s[i..<j], radix: 16) else { return nil }
            d.append(b); i = j
        }
        self = d
    }
    var hexCompact: String { map { String(format: "%02x", $0) }.joined() }
}
