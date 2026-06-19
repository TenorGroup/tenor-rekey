import SwiftUI
import Observation

/// Observable app state. The CARD is the document; reading / decoding act on it.
/// Heavy work stays on the X7Engine actor; this holds @MainActor UI state only.
@MainActor
@Observable
final class AppModel {
    var readerOnline = false
    var info: DeviceInfo?
    var card: PollResult?
    var sectors: [SectorVM] = []
    var selected: Int?
    var decoding = false
    var lastError: String?

    private let engine = X7Engine()

    var selectedSector: SectorVM? {
        guard let s = selected else { return nil }
        return sectors.first { $0.index == s }
    }

    /// Start the daemon + read device info, then look for a card (Codex r1:
    /// connect at launch, not lazily).
    func connect() async {
        do {
            info = try await engine.info()
            readerOnline = true
            lastError = nil
            await refreshCard()
        } catch {
            readerOnline = false
            info = nil
            lastError = "\(error)"
        }
    }

    func refreshCard() async {
        do {
            let p = try await engine.poll()
            withAnimation(.easeInOut(duration: 0.3)) {
                card = p.present ? p : nil
                if !p.present { sectors = []; selected = nil }
            }
            readerOnline = true
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }

    func decode() async {
        guard !decoding else { return }
        decoding = true
        lastError = nil
        do {
            let r = try await engine.decode()
            let vms = Self.buildSectors(r)
            withAnimation(.easeInOut(duration: 0.3)) {
                card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                sectors = vms
                selected = vms.first?.index
            }
        } catch {
            lastError = "\(error)"
        }
        decoding = false
    }

    static func buildSectors(_ r: DecodeResult) -> [SectorVM] {
        (0..<r.sectors).map { s in
            let key = r.keys[String(s)] ?? nil
            let kt = key?.first
            let kh = (key?.count == 2) ? key?[1] : nil
            let prov: KeyProvenance = kh == nil
                ? .unknown
                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
            let nums = s < 32
                ? Array((s * 4)...(s * 4 + 3))
                : Array((128 + (s - 32) * 16)...(128 + (s - 32) * 16 + 15))
            let blocks = nums.map { b in (r.blocks[String(b)] ?? nil) ?? "?" }
            return SectorVM(index: s, keyType: kt, keyHex: kh, provenance: prov, blocks: blocks)
        }
    }
}
