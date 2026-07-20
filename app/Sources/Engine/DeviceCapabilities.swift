import Foundation

/// The capability manifest a daemon declares in its `info` reply (the shell reads
/// it to gate device-specific UI). `slots > 0` enables the slot library, `emulate`
/// the emulate toggle, `lf` the LF panel, `dfu` firmware flashing, and `attacks`
/// decides which key-recovery moves the recover verb can offer. A plain reader like
/// the X7 declares everything false / zero; a Chameleon Ultra declares the full set.
/// Every field is decoded leniently so a daemon that omits one falls back to the
/// conservative "plain reader" value rather than failing the whole `info`.
struct DeviceCapabilities: Codable, Equatable, Sendable {
    var slots: Int = 0
    var emulate: Bool = false
    var lf: Bool = false
    var dfu: Bool = false
    var sniff: Bool = false
    var attacks: [String] = []
    var writeModes: [String] = []

    /// The XIXEI X7 baseline: an HF reader with a dictionary + nested recovery and
    /// nothing else. Mirrors the manifest x7d.py declares, so the descriptor default
    /// and the daemon's own report agree.
    static let x7 = DeviceCapabilities(
        slots: 0, emulate: false, lf: false, dfu: false, sniff: false,
        attacks: ["dict", "nested"], writeModes: [])

    /// The Chameleon Ultra baseline. Used as the descriptor default until the
    /// device's own `info` manifest lands (which is authoritative, so an Ultra vs a
    /// Lite still gates correctly once connected).
    static let chameleonUltra = DeviceCapabilities(
        slots: 8, emulate: true, lf: true, dfu: true, sniff: true,
        attacks: ["dict", "nested", "staticNested", "darkside"],
        writeModes: ["normal", "denied", "deceive", "shadow", "shadowReq"])
}

extension DeviceCapabilities {
    /// Decode each field with a default so a daemon that predates a field (or omits
    /// it) still yields a usable manifest instead of throwing. The memberwise
    /// initializer stays synthesized because this lives in an extension.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slots = try c.decodeIfPresent(Int.self, forKey: .slots) ?? 0
        emulate = try c.decodeIfPresent(Bool.self, forKey: .emulate) ?? false
        lf = try c.decodeIfPresent(Bool.self, forKey: .lf) ?? false
        dfu = try c.decodeIfPresent(Bool.self, forKey: .dfu) ?? false
        sniff = try c.decodeIfPresent(Bool.self, forKey: .sniff) ?? false
        attacks = try c.decodeIfPresent([String].self, forKey: .attacks) ?? []
        writeModes = try c.decodeIfPresent([String].self, forKey: .writeModes) ?? []
    }
}
