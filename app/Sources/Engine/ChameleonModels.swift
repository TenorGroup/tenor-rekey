import Foundation

/// Decoded shapes for the Chameleon-only daemon methods (probe/chameleon_d.py:
/// slots_list / slot_* / emulate_* / emu_read). The shell only ever asks for these
/// when the connected device advertises the matching capability (slots / emulate),
/// so a plain reader like the X7 never decodes them.

/// One field (HF or LF) of a slot: its emulated tag type (a TagSpecificType enum
/// name the shell localises), whether it is enabled, and its nickname.
struct SlotSense: Codable, Equatable, Sendable {
    let type: String
    let enabled: Bool
    let nick: String
}

/// One of the 8 slots: which one, whether it is the active slot, and its HF/LF fields.
struct ChameleonSlot: Codable, Equatable, Sendable, Identifiable {
    let index: Int
    let active: Bool
    let hf: SlotSense
    let lf: SlotSense
    var id: Int { index }

    /// True when the HF field holds a MIFARE Classic image (readable into the sector
    /// grid via emu_read). Only 1K / 4K are opened; other Classic sizes are rare here.
    var hfIsClassic: Bool { hf.type == "MIFARE_1024" || hf.type == "MIFARE_4096" }

    /// The emulator geometry (block count + sak) for an HF Classic slot, or nil when
    /// the HF field is not a Classic image the grid can render.
    var hfGeometry: (count: Int, sak: Int)? {
        switch hf.type {
        case "MIFARE_1024": return (64, 0x08)
        case "MIFARE_4096": return (256, 0x18)
        default: return nil
        }
    }
}

/// slots_list result: the 8-slot library.
struct SlotsResult: Codable, Sendable {
    let slots: [ChameleonSlot]
}

/// dfu_check result: the running firmware (app version + git) and the newest published
/// release tag, so the flashing view can show "update available". `latest` / `note` are
/// optional - an offline release fetch leaves latest nil and puts the reason in note.
struct DfuStatus: Codable, Sendable, Equatable {
    let model: String
    let current: String        // "major.minor" app version
    let git: String            // git description, e.g. "v2.0.0-3-gdeadbee"
    let asset: String          // the app-only asset picked for this model
    let latest: String?        // newest release tag, or nil if the fetch failed
    let updateAvailable: Bool
    let note: String?          // why the release fetch failed (offline), if any
}

/// dfu_flash result: whether the image was flashed (or the op was cancelled before any
/// write). `tag` is the release flashed when the source was "latest".
struct DfuFlashResult: Codable, Sendable {
    let flashed: Bool
    let tag: String?
    let port: String?
    let hash: String?
    let cancelled: Bool?
}

/// emu_read result: the active slot's HF emulator memory as a block-index -> hex map.
struct EmuReadResult: Codable, Sendable {
    let blocks: [String: String]
    let count: Int
}

/// slot_nick result (get or set): the resolved nickname.
struct SlotNickResult: Codable, Sendable {
    let slot: Int
    let sense: String
    let nick: String
}

/// A selectable emulated tag type for the slot library's type picker. `name` is the
/// TagSpecificType enum name the daemon accepts; `label` is the shown text (kept
/// verbatim, not localised - these are product type names).
struct SlotTagType: Identifiable, Equatable {
    let name: String
    let label: String
    var id: String { name }

    /// The curated set offered in the UI: HF types only. The slot library's actions
    /// (enable/disable, set-type, open-content) are wired to the HF field, so an LF type
    /// (EM410X etc.) is intentionally excluded here - LF is later scope.
    static let selectable: [SlotTagType] = [
        SlotTagType(name: "MIFARE_1024", label: "MIFARE Classic 1K"),
        SlotTagType(name: "MIFARE_4096", label: "MIFARE Classic 4K"),
        SlotTagType(name: "MIFARE_Mini", label: "MIFARE Mini"),
        SlotTagType(name: "NTAG_215", label: "NTAG 215"),
        SlotTagType(name: "NTAG_216", label: "NTAG 216"),
    ]
}
