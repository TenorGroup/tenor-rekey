import Foundation

/// Importing a dump produced by another tool into a `CardDump`, so it can be saved to the
/// library, loaded into the working document, cloned, or emulated. The formats an operator
/// commonly has:
///   - Proxmark3 `.eml`  - one block of 32 hex chars per line (a dash / blank = an unread block).
///   - Proxmark3 `.json` - the pm3 dump json (a `blocks` map + `Card` + `SectorKeys`).
///   - Proxmark3 `.bin` / `.mfd` / `.dump` - a raw flat image (handled by the app's own loader).
///   - Flipper `.nfc`    - the Flipper MIFARE Classic text format (UID / ATQA / SAK / Block N).
///
/// Every parser honours the CardDump `present`-block convention: ONLY blocks genuinely
/// present in the source land in `blocks`. An unread block (an eml dash / blank line, a
/// Flipper `??` byte, a block the source simply omits) is left absent, never fabricated as
/// zeros, so an imported partial dump can never clone a made-up block over real card data.
/// External files are untrusted: block indices are range-checked, the file size is checked
/// BEFORE the read, and a recognised text extension that fails to parse is a clean error,
/// never silently reinterpreted as a raw image.
enum CardImportError: Error { case unrecognised }

extension CardDump {
    /// Cap on a text dump we will read + parse, checked against the file size BEFORE the
    /// read so a multi-GB file cannot exhaust memory. Real MFC text dumps are tens of KB.
    private static let maxTextImport = 1_048_576
    /// Raw flat-image sizes we accept (standard 1K / 4K), for a recognised RAW extension only.
    private static let rawImageSizes: Set<Int> = [1024, 4096]
    /// A MIFARE image is at most 256 blocks (4K). Any block index outside 0..<256 in an
    /// external dump is invalid and dropped, so a hostile / negative index can never reach
    /// the `.mfd` writer's block arithmetic.
    private static let maxBlockCount = 256

    /// The import entry point. Dispatch is by extension, with a content sniff only for an
    /// UNKNOWN extension:
    ///   - recognised RAW ext (.bin/.mfd/.dump): a flat image at a plausible size, reusing
    ///     `CardDump.load` (which reads a sidecar + enforces the 4 KB guard). NOT a fallback
    ///     for other files.
    ///   - recognised TEXT ext (.eml/.json/.nfc): parsed by its one parser; a parse failure
    ///     THROWS, it never falls through to the raw path.
    ///   - unknown ext: sniffed by content against the structural parsers (no raw fallback,
    ///     so an arbitrary 1 KB / 4 KB file cannot masquerade as a card).
    static func importFile(url: URL) throws -> CardDump {
        let ext = url.pathExtension.lowercased()
        let name = url.deletingPathExtension().lastPathComponent
        let size = try fileSize(url)                     // stat first: reject oversize before reading

        // Recognised raw image extensions: only these use the raw loader, and only at a
        // plausible image size.
        if ["bin", "mfd", "dump"].contains(ext) {
            guard rawImageSizes.contains(size), let d = try? CardDump.load(mfd: url) else {
                throw CardImportError.unrecognised
            }
            return d
        }

        // Everything else is treated as text. Reject oversize before the whole-file read.
        guard size <= maxTextImport, let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { throw CardImportError.unrecognised }

        switch ext {
        case "nfc": if let d = fromFlipperNFC(text, name: name) { return d }
        case "json": if let d = fromPM3JSON(data, name: name) { return d }
        case "eml": if let d = fromEML(text, name: name) { return d }
        default:
            // Unknown extension: sniff by content (structural parsers only, no raw fallback).
            let head = String(text.prefix(512))
            if head.contains("Filetype: Flipper") || head.contains("Device type:"),
               let d = fromFlipperNFC(text, name: name) { return d }
            if head.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("{"),
               let d = fromPM3JSON(data, name: name) { return d }
            if looksLikeEML(text), let d = fromEML(text, name: name) { return d }
        }
        // A recognised text ext whose parser returned nil (or an unknown ext that sniffed to
        // nothing) is an unrecognised file - never reinterpreted as a raw image.
        throw CardImportError.unrecognised
    }

    /// The file size read via a stat, so an oversize file is rejected before it is read into
    /// memory. Throws for a missing / unreadable file.
    private static func fileSize(_ url: URL) throws -> Int {
        guard let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize else {
            throw CardImportError.unrecognised
        }
        return size
    }

    // ---- Proxmark3 .eml ----------------------------------------------------

    /// One block per line, 32 hex chars. The block address IS the line index, so empty
    /// lines are NOT dropped - a blank (or dash, or junk) line means that block is ABSENT.
    /// Dropping it would shift every later block down one address and clone real data into
    /// the wrong physical blocks. Capped at 256 block positions. sak is chosen to fit every
    /// present block; uid + keys are recovered from block 0 / the sector trailers.
    static func fromEML(_ text: String, name: String) -> CardDump? {
        let lines = text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline)
        var blocks: [Int: String] = [:]
        for (i, raw) in lines.enumerated() {
            if i >= maxBlockCount { break }
            let clean = raw.trimmingCharacters(in: .whitespaces)
                .replacingOccurrences(of: " ", with: "").lowercased()
            guard clean.count == 32, clean.allSatisfy(\.isHexDigit) else { continue }   // blank / dash / junk -> absent
            blocks[i] = clean
        }
        guard !blocks.isEmpty else { return nil }
        let sak = fittingSak(statedSak: 0, maxBlock: blocks.keys.max() ?? 0)
        return CardDump.fromBlocks(blocks, sak: sak, name: name)
    }

    /// A quick sniff for an extension-less eml: the first non-empty line is 32 chars of
    /// hex (or dash placeholders).
    static func looksLikeEML(_ text: String) -> Bool {
        guard let first = text.split(whereSeparator: \.isNewline)
            .map({ $0.trimmingCharacters(in: .whitespaces) })
            .first(where: { !$0.isEmpty }) else { return false }
        let clean = first.replacingOccurrences(of: " ", with: "")
        return clean.count == 32 && clean.allSatisfy { $0.isHexDigit || $0 == "-" }
    }

    // ---- Proxmark3 .json ---------------------------------------------------

    /// The pm3 dump json: a `blocks` map (`"0": "32hex"`), a `Card` object (UID / SAK), and
    /// `SectorKeys` (KeyA / KeyB per sector). Only blocks present in the map AND within the
    /// 0..<256 range are kept (a hostile index is dropped). Explicit SectorKeys override the
    /// trailer-recovered keys where present. uid prefers `Card.UID`.
    static func fromPM3JSON(_ data: Data, name: String) -> CardDump? {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let blocksObj = obj["blocks"] as? [String: Any] else { return nil }
        var blocks: [Int: String] = [:]
        for (k, v) in blocksObj {
            guard let i = Int(k), i >= 0, i < maxBlockCount, let hex = v as? String else { continue }
            let clean = hex.replacingOccurrences(of: " ", with: "").lowercased()
            if clean.count == 32, clean.allSatisfy(\.isHexDigit) { blocks[i] = clean }
        }
        guard !blocks.isEmpty else { return nil }

        let card = obj["Card"] as? [String: Any]
        var statedSak = 0
        if let s = card?["SAK"] as? String, let v = Int(s.replacingOccurrences(of: " ", with: ""), radix: 16) { statedSak = v }
        let sak = fittingSak(statedSak: statedSak, maxBlock: blocks.keys.max() ?? 0)

        var dump = CardDump.fromBlocks(blocks, sak: sak, name: name)
        if let u = card?["UID"] as? String {
            let clean = u.replacingOccurrences(of: " ", with: "").lowercased()
            if !clean.isEmpty, clean.count % 2 == 0, clean.allSatisfy(\.isHexDigit) { dump.uid = spacedHexPairs(clean) }
        }
        if let sk = obj["SectorKeys"] as? [String: Any] {
            var keys = dump.keys
            for (k, v) in sk {
                guard let s = Int(k), s >= 0, s < sectorsForSak(sak), let d = v as? [String: Any] else { continue }
                if let ka = d["KeyA"] as? String, let n = KeyStore.normalized(ka), n != "000000000000" {
                    keys[s] = SectorKey(type: "A", hex: n)
                } else if let kb = d["KeyB"] as? String, let n = KeyStore.normalized(kb), n != "000000000000" {
                    keys[s] = SectorKey(type: "B", hex: n)
                }
            }
            dump.keys = keys
        }
        return dump
    }

    // ---- Flipper .nfc ------------------------------------------------------

    /// The Flipper MIFARE Classic text format: `UID:` / `ATQA:` / `SAK:` header lines and
    /// `Block N: hh hh ...` data lines (16 space-separated bytes). A block number outside
    /// 0..<256, or a line carrying a `??` unknown byte (or otherwise malformed), is skipped,
    /// so a partly-recovered Flipper dump imports only its genuinely read blocks. Keys come
    /// from the sector trailers.
    static func fromFlipperNFC(_ text: String, name: String) -> CardDump? {
        var blocks: [Int: String] = [:]
        var headerUID: String? = nil
        var statedSak = 0
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("UID:") {
                let clean = line.dropFirst(4).trimmingCharacters(in: .whitespaces)
                    .replacingOccurrences(of: " ", with: "").lowercased()
                if !clean.isEmpty, clean.count % 2 == 0, clean.allSatisfy(\.isHexDigit) { headerUID = spacedHexPairs(clean) }
            } else if line.hasPrefix("SAK:") {
                let v = line.dropFirst(4).trimmingCharacters(in: .whitespaces).replacingOccurrences(of: " ", with: "")
                if let s = Int(v, radix: 16) { statedSak = s }
            } else if line.hasPrefix("Block ") {
                guard let colon = line.firstIndex(of: ":"),
                      let n = Int(line[line.index(line.startIndex, offsetBy: 6)..<colon].trimmingCharacters(in: .whitespaces)),
                      n >= 0, n < maxBlockCount else { continue }
                let tokens = line[line.index(after: colon)...].split(whereSeparator: \.isWhitespace).map(String.init)
                guard tokens.count == 16, tokens.allSatisfy({ $0.count == 2 && $0.allSatisfy(\.isHexDigit) }) else { continue }   // ?? -> skip
                blocks[n] = tokens.joined().lowercased()
            }
        }
        guard !blocks.isEmpty else { return nil }
        let sak = fittingSak(statedSak: statedSak, maxBlock: blocks.keys.max() ?? 0)
        var dump = CardDump.fromBlocks(blocks, sak: sak, name: name)
        if let headerUID { dump.uid = headerUID }
        return dump
    }

    // ---- shared ------------------------------------------------------------

    /// The card geometry (1K vs 4K) that FITS every present block: 4K when the stated sak
    /// says 4K OR any block sits beyond the 1K range (block index >= 64), else 1K. This
    /// guarantees no present block is dropped by the 1K/4K block-count when the image is
    /// written back out (all indices are already < 256, so they all fit 4K).
    static func fittingSak(statedSak: Int, maxBlock: Int) -> Int {
        (statedSak == 0x18 || maxBlock >= 64) ? 0x18 : 0x08
    }

    /// "aabbccdd" -> "aa bb cc dd" (lowercased), the spaced display uid form the app uses.
    static func spacedHexPairs(_ compact: String) -> String {
        let clean = compact.replacingOccurrences(of: " ", with: "").lowercased()
        return stride(from: 0, to: clean.count, by: 2).compactMap { i -> String? in
            let s = clean.index(clean.startIndex, offsetBy: i)
            let e = clean.index(s, offsetBy: min(2, clean.count - i))
            return String(clean[s..<e])
        }.joined(separator: " ")
    }
}
