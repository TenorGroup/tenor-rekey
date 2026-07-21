import SwiftUI
import Observation
import AppKit

/// Observable app state. The decoded / loaded image is the DOCUMENT; the card on the
/// reader is a separate live device state. Heavy work stays on the device bridge
/// actor; this holds @MainActor UI state only.
@MainActor
@Observable
final class AppModel {
    var readerOnline = false
    var info: DeviceInfo?
    var card: PollResult?
    var sectors: [SectorVM] = []
    /// The live NTAG / Ultralight read (page rows + the metadata to re-emulate it). Nil
    /// when there is no NTAG dump on the canvas. `pages` reads its page rows so the page
    /// view and the load-to-slot metadata are one source of truth and never drift.
    var ntagDoc: NtagDocument?
    var pages: [NtagPage] { ntagDoc?.pages ?? [] }   // NTAG / Ultralight page dump (SAK 0x00)
    var selected: Int?                  // selected sector index
    var selectedBlock: Int?             // selected absolute block, for the quick-look
    var decoding = false
    var decodeProgress: DecodeProgress?      // live sector / key-walk progress
    /// When the running decode started, for an honest elapsed-time readout (the walk
    /// has a fixed wall-clock budget, so elapsed seconds is bounded, forward-moving
    /// feedback where the raw auth counter has no meaningful denominator).
    var decodeStart: Date?
    /// A decode finished but recovered NO key: the card's keys are not in the
    /// dictionary. Shown as an honest "no keys" result instead of a fake empty grid.
    var noKeysFound = false
    private var decodeCancelled = false
    var lastError: String?
    var inspectorOpen = true

    // ---- Connect surface (USB now; Bluetooth is a later pass) --------------
    /// The Connect popover (device list + rescan + manual serial connect) is open.
    var showConnect = false
    /// Known readers currently present on the USB bus (the detected-devices list).
    var detectedDevices: [DeviceDescriptor] = []
    /// Every enumerated USB serial port (the manual-connect list).
    var serialPorts: [SerialPortInfo] = []
    /// The Bluetooth LE radio for a wireless Chameleon link. Nested @Observable, so a view
    /// that reads `model.ble.state` / `model.ble.devices` re-renders on its changes. A BLE
    /// link is never auto-detected (it is not on the USB bus); it is opened only by the
    /// explicit `bleConnect` from the Connect surface.
    let ble = BLEManager()

    /// The working DOCUMENT: the image produced by a decode or loaded from a file.
    /// It is what the canvas shows, what Save writes out, and what Write clones onto
    /// the card on the reader. It is independent of whichever card is currently on
    /// the reader and deliberately persists across card swaps, so decoding a source
    /// card and writing it onto a blank needs no save/open dance and never visually
    /// vanishes when the source card is lifted.
    var source: CardDump?
    var cloneSheet = false
    var cloning = false
    /// Per-block write outcome from the last/in-flight clone (block -> ok). Tied to a
    /// specific target card, so it resets when the card on the reader changes.
    var cloneResults: [Int: Bool] = [:]
    /// Per-block reason a write was refused (block -> daemon reason), so a failed clone
    /// can be summarised in card terms (sector + cause) instead of raw block numbers.
    var cloneFailReasons: [Int: String] = [:]
    var formatConfirm = false
    /// The uid snapshot taken when the format confirmation is PRESENTED, so the write is
    /// pinned to the card the user actually authorized (the monitor can swap `card`
    /// while the dialog is open). Shown in the dialog and re-checked before erasing.
    var pendingFormatUID: String?
    var formatting = false

    /// apdu console.
    var apduOpen = false
    var apduLog: [ApduEntry] = []
    var apduBusy = false

    // ---- Chameleon slot library + emulation (gated on capabilities) --------
    /// The 8-slot library, loaded when the slot view opens (empty for a plain reader).
    var slots: [ChameleonSlot] = []
    /// The slot highlighted in the library (its actions apply to it) - not the ACTIVE
    /// slot the device presents.
    var selectedSlot: Int?
    /// A slot op (select / type / enable / rename / save / load / open / emulate toggle)
    /// owns the reader. Folded into `deviceBusy` so no other op races it.
    var slotBusy = false
    /// The Chameleon-only slot library is showing instead of the document canvas.
    var showSlots = false

    // ---- LF 125 kHz (Chameleon-only, gated on capabilities.lf) -------------
    /// The LF panel (read / T5577 write / EM410x emulate) is showing instead of the
    /// document canvas. A Chameleon-only detail area, so it drops on a device swap.
    var showLF = false
    /// The last LF read (em410x / hidprox tag on the reader), shown in the LF panel.
    var lfScanResult: LfScanResult?
    /// The last LF T5577 write outcome (wrote + verified), shown in the LF panel.
    var lfWriteResult: LfWriteResult?
    /// An LF op (read / write / emulate) owns the reader. Folded into `deviceBusy`.
    var lfBusy = false

    // ---- saved-cards library (device-agnostic) -----------------------------
    /// The persistent library of saved card dumps, refreshed when the library view opens
    /// and after any save / import / rename / delete.
    var savedCards: [SavedCard] = []
    /// The library entry highlighted in the view (its actions apply to it).
    var selectedSavedCard: String?
    /// The saved-cards library is showing instead of the document canvas. Unlike the slot
    /// library it is device-agnostic, so it persists across a device swap.
    var showLibrary = false
    /// The device is in tag/emulate mode (presenting the active slot), not reader mode.
    /// While true the status monitor stops polling, since a poll would switch the device
    /// back to reader mode under the emulation.
    var emulating = false

    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
    /// The firmware update sheet is open.
    var flashingSheet = false
    /// The current firmware + latest release, loaded when the sheet opens.
    var dfuStatus: DfuStatus?
    /// A firmware flash is in flight (owns the device; folded into `deviceBusy`, so the
    /// status monitor pauses while the device reboots into and out of the bootloader).
    var flashing = false
    /// The flash phase (download / enter / flash / done) + percent, for the progress UI.
    var flashStage: String?
    var flashPercent: Int?
    /// A finished-successfully flag so the sheet can show a done state without a lingering error.
    var flashDone = false
    /// A flash failure, shown INSIDE the flashing sheet (not only the root banner behind the
    /// modal) with the retry-now recovery path, since a failed flash usually leaves the
    /// device in the bootloader.
    var flashError: String?

    /// The active device bridge, chosen by the registry at connect and swapped on
    /// hot-plug. Created lazily for `descriptor` (the daemon itself starts on its
    /// first request), so there is no bridge before the first connect.
    private var bridge: DeviceBridge?
    /// The descriptor of the device the bridge currently drives. X7 by default, so a
    /// bare machine (or one where detection has not run yet) behaves exactly as the
    /// single-device build did; a detected Chameleon swaps it.
    private var descriptor: DeviceDescriptor = DeviceRegistry.fallback
    /// A device swap (or reconnect that changes the device) is tearing down the old
    /// daemon and bringing up the new one.
    ///
    /// INVARIANT: no device op (decode / clone / format / apdu) or reconnect may run
    /// while `swapping` is true, and the old bridge is unreachable the instant it is
    /// set (the swap detaches `bridge` synchronously before its first await). This is
    /// what makes a hot-swap atomic from the UI's point of view: an op started during
    /// the teardown await cannot grab the just-terminated bridge or the stale card.
    private var swapping = false

    /// A BLE connect is in flight: the radio link + loopback bridge are being brought up,
    /// which can take up to ~15s before `swapDevice` even runs. Reserved for that whole
    /// window so a monitor tick or a second selection cannot interleave and swap the device
    /// out from under the pending connect.
    private var bleConnecting = false

    /// A device op already owns the reader. Reconnect / swap must not replace the
    /// bridge under one, and a second op must not start while one runs. Slot ops are
    /// included so a slot edit and a decode / clone can never overlap on the reader.
    /// `bleConnecting` is folded in so EVERY op that guards `!deviceBusy` (reader ops,
    /// firmware, connect) also refuses during the up-to-15s BLE connect window, where
    /// `deviceOverBLE` still describes the OUTGOING device. `bleConnect` itself is safe:
    /// it guards on `canChangeDevice` BEFORE setting `bleConnecting`, and its `swapDevice`
    /// call guards only on `swapping`, not `deviceBusy`.
    private var deviceBusy: Bool { decoding || cloning || formatting || apduBusy || slotBusy || flashing || lfBusy || bleConnecting }

    /// The active bridge, created lazily for the current descriptor. A prior bridge
    /// for a different device is torn down explicitly on the swap path (which nils
    /// `bridge` before the descriptor changes), so this only ever creates the bridge
    /// that matches - it never silently orphans a running daemon.
    private func activeBridge() -> DeviceBridge {
        if let b = bridge, b.descriptor.id == descriptor.id { return b }
        let b = DeviceBridge(descriptor: descriptor)
        bridge = b
        return b
    }

    /// The connected device's capability manifest, read by the shell to gate
    /// device-specific UI. Prefers the daemon's declared manifest; falls back to the
    /// active descriptor's static defaults before `info` lands or when a daemon
    /// predates the manifest.
    var capabilities: DeviceCapabilities { info?.capabilities ?? descriptor.capabilities }

    /// True when the connected device is a Chameleon sitting in the Nordic bootloader:
    /// it has no command interface (so `readerOnline` is false and card ops are off), but
    /// the firmware flash can still recover it, so the firmware action stays reachable.
    var deviceInDFU: Bool { descriptor.family == "chameleon-dfu" }

    /// The family of the device currently driven, so the Connect surface can mark the
    /// active row. A manual pin keeps its base family (chameleon-ultra), so matching a
    /// detected row by family lights the right one in every case.
    var activeDeviceFamily: String { descriptor.family }

    /// A device swap / (re)connect is in flight, exposed read-only so the Connect
    /// surface can show a spinner and disable Rescan while it runs. A BLE connect is
    /// included: its link-up window precedes the swap and must read as busy too.
    var connecting: Bool { swapping || bleConnecting }

    /// True when the active device is driven over a BLE loopback bridge (its port is pinned
    /// to `tcp:...`). Firmware update over BLE is unsafe (the USB DFU flasher could hit the
    /// wrong physical device), so the firmware UI and its methods gate on this being false.
    var deviceOverBLE: Bool { descriptor.portOverride?.hasPrefix("tcp:") == true }

    /// The manual Connect controls (Rescan, the serial-port rows, the free-text connect)
    /// may act only when no swap or device op owns the reader - the same guard `connect`,
    /// `rescan`, and `connectManual` enforce - so a tap during one is a disabled control,
    /// never a silent no-op that the user reads as the app ignoring them.
    var canChangeDevice: Bool { !swapping && !deviceBusy && !bleConnecting }

    /// The user's editable keys (Settings > Dictionaries), tried before the
    /// daemon's large built-in dictionary.
    let keyStore = KeyStore()
    /// The persistent saved-cards library (device-agnostic; works for X7 dumps too).
    let savedCardStore = SavedCardStore.standard()
    /// Size of the daemon's built-in curated dictionary (shown in Settings).
    var builtinKeyCount = 0
    /// Keys the daemon has learned from real cards and reranks decodes with (Settings).
    var learnedKeyCount = 0

    var selectedSector: SectorVM? {
        guard let s = selected else { return nil }
        return sectors.first { $0.index == s }
    }

    /// What "write" clones onto the card on the reader: the working document. It has
    /// no dependency on a card being present, so the write action is available as soon
    /// as there is something to write; the target card is asked for at write time.
    var cloneSource: CardDump? { source }

    /// Format erases the card on the reader, so it is only offered when the card
    /// present is the one this document was decoded from (same uid): only then do we
    /// hold its recovered keys to auth it, and only then is wiping it unambiguous.
    var canFormat: Bool {
        guard let c = card?.uid, let d = source?.uid else { return false }
        return Self.normUID(c) == Self.normUID(d)
    }

    static func normUID(_ s: String) -> String {
        s.replacingOccurrences(of: " ", with: "").lowercased()
    }

    /// Detect the connected device, then start its daemon + read device info and look
    /// for a card (connect at launch, not lazily). With no device detected we fall
    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
    ///
    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
    /// reconnect can never replace the bridge under a running decode / clone. When the
    /// detected device differs from the current one it routes through `swapDevice` so
    /// the old daemon is torn down (never silently orphaned) under the swap guard.
    func connect() async {
        guard !swapping, !deviceBusy else { return }
        await detectAndOpen()
    }

    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
    /// is torn down, and bring the current one up.
    private func detectAndOpen() async {
        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
        if bridge != nil, found.id != descriptor.id {
            await swapDevice(to: found)
            return
        }
        descriptor = found
        await openCurrentDevice()
    }

    /// Refresh the Connect surface's lists (known present devices + all USB serial
    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
    func refreshConnectLists() {
        detectedDevices = DeviceRegistry.detectAll()
        serialPorts = USBProbe.serialPorts()
    }

    /// True when a manual serial port is pinned (portOverride) AND that exact /dev path is
    /// still enumerated on the USB bus; false when nothing is pinned. Lets rescan / the
    /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
    private func pinnedPortPresent() -> Bool {
        guard let pinned = descriptor.portOverride else { return false }
        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
        // serial-port scan below would report it absent and the monitor would swap away from a
        // live wireless link. It is "present" for as long as the manager holds the link; once
        // the BLE link drops, this reads false and a normal hot-swap is allowed.
        if pinned.hasPrefix("tcp:") { return ble.isConnected }
        return USBProbe.serialPorts().contains { $0.path == pinned }
    }

    /// The Connect surface's Rescan: refresh the lists and re-run detection without waiting
    /// for the 1.5s monitor tick. A PRESENT manual pin is preserved - re-open the SAME pinned
    /// device rather than running auto-detect, which (when it does not recognise the
    /// Chameleon) would fall to the X7 fallback and tear down the working port the user pinned
    /// precisely because auto-detect fails. Only with no pin, or a pinned port that has
    /// disappeared, do we re-detect.
    func rescan() async {
        guard !swapping, !deviceBusy else { return }
        refreshConnectLists()
        if descriptor.portOverride != nil, pinnedPortPresent() {
            await openCurrentDevice()
        } else {
            await detectAndOpen()
        }
        refreshConnectLists()
    }

    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
    func connectManual(port: String) async {
        guard !swapping, !deviceBusy else { return }
        // A port that enumerates as a device in the Nordic bootloader (isDFU) is pinned from
        // the chameleon-dfu descriptor, not chameleonUltra: then deviceInDFU is true and
        // openCurrentDevice takes the DFU branch (firmware recovery reachable) instead of
        // trying the app protocol on a bootloader port. A non-DFU port pins as chameleonUltra.
        let isDFUPort = USBProbe.serialPorts().contains { $0.path == port && $0.isDFU }
        let base = isDFUPort ? DeviceRegistry.chameleonDFU : DeviceRegistry.chameleonUltra
        let manual = DeviceDescriptor(
            id: "chameleon-manual:\(port)",
            family: base.family,
            displayName: base.displayName,
            daemonScript: base.daemonScript,
            probeSubdir: base.probeSubdir,
            usbMatch: base.usbMatch,
            capabilities: base.capabilities,
            portOverride: port)
        await swapDevice(to: manual)
        refreshConnectLists()
    }

    // ---- Bluetooth LE connect ----------------------------------------------

    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
    /// surface drives the radio without importing the manager directly.
    func bleStartScan() { ble.startScan() }
    func bleStopScan() { ble.stopScan() }

    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
    ///
    /// A device advertising the Nordic DFU service is refused here (the view also hides its
    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
    /// be recovered over USB.
    func bleConnect(_ id: String) async {
        guard canChangeDevice else { return }
        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
        // Reserve the whole connect (up to ~15s) so a monitor tick / second selection cannot
        // interleave: bleConnecting gates canChangeDevice, connecting, and the monitor. It does
        // NOT block the swapDevice call below (that guards on its own `swapping`).
        bleConnecting = true
        defer { bleConnecting = false }
        // A BLE-A -> BLE-B switch needs no pre-disconnect here: ble.connect() now serialises
        // internally (teardownAndWait tears down A and waits for its didDisconnect before
        // connecting B), so dropping A here would be redundant.
        do {
            let port = try await ble.connect(id)
            let base = DeviceRegistry.chameleonUltra
            let desc = DeviceDescriptor(
                id: "chameleon-ble:\(id)",
                family: base.family,
                displayName: base.displayName,
                daemonScript: base.daemonScript,
                probeSubdir: base.probeSubdir,
                usbMatch: base.usbMatch,
                capabilities: base.capabilities,
                portOverride: "tcp:127.0.0.1:\(port)")
            await swapDevice(to: desc)
            ble.stopScan()
            refreshConnectLists()
        } catch {
            lastError = "\(error)"
            ble.disconnect()
        }
    }

    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
    /// then sample the reader. Shared by the first connect and a hot-swap, so both
    /// paths land the same state (info, capabilities via `info`, reader/card status).
    private func openCurrentDevice() async {
        // A device in the bootloader has no command interface to query: present a reachable
        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
        if deviceInDFU {
            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
            info = nil
            readerOnline = false
            dfuStatus = nil
            lastError = nil
            return
        }
        let b = activeBridge()
        do {
            info = try await b.info()
            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
            readerOnline = true
            lastError = nil
            await refreshStatus()
        } catch {
            applyReaderGone()
            lastError = "\(error)"
        }
    }

    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
    /// as decodes recover keys).
    func refreshLearnedCount() async {
        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
    }

    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
    func clearLearnedKeys() async {
        try? await activeBridge().clearLearnedKeys()
        await refreshLearnedCount()
    }

    /// Live status: keep the reader / card pill honest when the X7 or a card is
    /// plugged or removed with no user action. Runs until the view's task is
    /// cancelled. Skips polling during an operation that already owns the reader.
    func monitor() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1.5))
            if deviceBusy || swapping || bleConnecting { continue }
            // Hot-swap detection runs even while emulating: unplugging an emulating
            // Chameleon and attaching another device must still tear down + swap. It is a
            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
            // With only the X7 involved, detect() keeps returning the same descriptor, so
            // this path is inert and the poll below is unchanged.
            if let found = DeviceRegistry.detect() {
                // A manually-pinned device (portOverride) is protected from an auto-swap ONLY
                // while its pinned port is still present - the user chose this port. Once that
                // /dev path disappears (unplugged), the pin no longer refers to anything, so a
                // newly-plugged X7 / auto Chameleon / DFU is allowed to hot-swap in exactly as
                // an unpinned device would. Auto descriptors (no pin) swap on an id change as
                // before.
                if found.id != descriptor.id,
                   descriptor.portOverride == nil || !pinnedPortPresent() {
                    await swapDevice(to: found); continue
                }
            } else if emulating {
                // The emulating device was unplugged with nothing to swap to: the card
                // poll is skipped while emulating, so this is the only place that would
                // notice it is gone. Reflect it (which also clears the emulate state).
                applyReaderGone()
                continue
            }
            // Only the card POLL is skipped while emulating: a poll forces reader mode,
            // which would break the emulation.
            if emulating { continue }
            await refreshStatus()
        }
    }

    /// Replace the active device with a freshly detected one. Every synchronous state
    /// change happens BEFORE the first await, so the teardown await (a MainActor
    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
    /// already nil. Only reader-bound state is cleared; the writable document is
    /// device-independent and is deliberately kept across the swap.
    private func swapDevice(to found: DeviceDescriptor) async {
        guard !swapping else { return }
        swapping = true
        defer { swapping = false }              // released however this returns
        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
        let old = bridge
        bridge = nil                            // detach: no path can obtain the old bridge now
        descriptor = found
        withAnimation(.easeInOut(duration: 0.3)) {
            readerOnline = false
            info = nil
            card = nil
            clearCardBound()
            resetChameleonState()
        }
        await old?.shutdown()                   // bounded terminate + drain of the old daemon
        await openCurrentDevice()               // creates + brings up the new bridge
        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
        // now that the old daemon is gone and the new device is open. Gated on the NEW device
        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
        // the old link and brought up the new one before this swap, so disconnecting here
        // would kill the freshly-connected link. Idempotent.
        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
    }

    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
    /// and the slot view / emulate toggle must not persist across a device that has none.
    private func resetChameleonState() {
        slots = []
        selectedSlot = nil
        showSlots = false
        showLF = false
        lfScanResult = nil
        lfWriteResult = nil
        emulating = false
        dfuStatus = nil
        // A failed flash usually leaves the device in the bootloader, which triggers a
        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
        // still open, KEEP the flash outcome (error / done + progress) so its banner and
        // recovery text do not vanish under the user; clearFlashState() clears them when
        // the sheet is dismissed.
        if !flashingSheet {
            flashStage = nil
            flashPercent = nil
            flashDone = false
            flashError = nil
        }
    }

    /// Clear the flash outcome + progress. Called when the flashing sheet is dismissed, so a
    /// stale error / done state never carries into the next time it is opened.
    func clearFlashState() {
        flashStage = nil
        flashPercent = nil
        flashDone = false
        flashError = nil
    }

    /// Consecutive polls that saw the reader but no card; a seated card that blips
    /// for one cycle should not drop its decoded grid, so we debounce a removal.
    private var cardAbsentStreak = 0

    /// One status sample: detects reader unplug (drops to offline + clears), reader
    /// replug (back online + refetch device info), and card placed / removed.
    private func refreshStatus() async {
        do {
            let p = try await activeBridge().poll(tries: 8)
            if p.reader == false {           // reader unplugged: reflect it at once
                applyReaderGone()
                return
            }
            readerOnline = true
            if info == nil { info = try? await activeBridge().info() }   // refetch until it lands
            // NOTE: do not clear lastError here - the 1.5s poll would wipe a clone /
            // decode / format error banner before the user could read it. Operations
            // clear it when they start; the banner also has a dismiss button.
            if p.present {
                cardAbsentStreak = 0
                // A different card (or first placement): the DOCUMENT stays (it is the
                // working image, not bound to this card); only the previous write's
                // per-block glyphs reset so they never show on the new card.
                if card == nil || p.uid != card?.uid {
                    withAnimation(.easeInOut(duration: 0.3)) { clearCardBound(); card = p }
                }
            } else {
                cardAbsentStreak += 1
                if card != nil && cardAbsentStreak >= 2 {
                    withAnimation(.easeInOut(duration: 0.3)) { card = nil; clearCardBound() }
                }
            }
        } catch {
            applyReaderGone()
        }
    }

    /// Reset state tied to the physical card on the reader: the last clone's per-block
    /// glyphs and the NTAG page view (NTAG has no writable document, so its pages are
    /// bound to the live card). The writable Classic DOCUMENT (sector grid + source
    /// image + selection) is deliberately kept so it survives a card swap, removal, or
    /// reader unplug - the working image is not bound to whatever card is on the
    /// reader. Shared by the swap, removal, and reader-gone paths so they cannot drift.
    private func clearCardBound() {
        cloneResults = [:]
        cloneFailReasons = [:]
        ntagDoc = nil
        noKeysFound = false
    }

    /// Drop the working document entirely (the source tag's clear button): the image,
    /// its grid, page dump, and selection. The card on the reader is untouched.
    func clearDocument() {
        withAnimation(.easeInOut(duration: 0.3)) {
            source = nil; sectors = []; ntagDoc = nil; selected = nil; selectedBlock = nil
            cloneResults = [:]; cloneFailReasons = [:]; noKeysFound = false
        }
    }

    /// Reader unplugged or the daemon went away: go offline and clear everything
    /// tied to a live reader. No-op when already in that state (avoids churn).
    private func applyReaderGone() {
        cardAbsentStreak = 0
        guard readerOnline || card != nil || info != nil else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            readerOnline = false
            info = nil
            card = nil
            clearCardBound()
            resetChameleonState()
        }
    }

    func decode() async {
        // Refuse while a swap is tearing the device down, or another device op already
        // owns the reader (deviceBusy includes `decoding`, so this also blocks a
        // double-decode). Also refuse while emulating: a reader op would force the device
        // back to reader mode under the emulation, leaving the toggle lying. Serialized,
        // never racing the bridge.
        guard !swapping, !deviceBusy, !emulating else { return }
        decoding = true
        decodeCancelled = false
        decodeProgress = nil
        decodeStart = Date()
        lastError = nil
        cloneResults = [:]
        cloneFailReasons = [:]
        // Couple the card under the op's OWN patient retry: the snappy 1.5s status poll
        // (tries=8) can miss a card that is physically seated but slow to first-contact,
        // while a full poll finds it. So a decode is not gated on the status poll having
        // detected the card yet - if `card` is not set, poll now with the full retry.
        var live = card
        if live?.present != true {
            live = try? await activeBridge().poll(tries: 25)
            if let l = live, l.present {
                withAnimation(.easeInOut(duration: 0.3)) { card = l }
            }
        }
        guard let live, live.present else {
            // Nothing coupled: surface it instead of silently spinning, and DO NOT drop
            // the held working document (nothing new was read to replace it with).
            lastError = "no card on reader"
            finishDecode()
            return
        }
        let startUID = live.uid
        // The working DOCUMENT is deliberately NOT dropped up front: a decode that finds
        // no keys, hits a changed card, or is cancelled must leave the held, unsaved
        // image intact - only a genuine new result replaces it (see restoreDocument).
        withAnimation(.easeInOut(duration: 0.3)) { selectedBlock = nil; noKeysFound = false }
        do {
            if live.isNTAG {
                // NTAG / Ultralight: a read-only page view, not a writable document.
                let r = try await activeBridge().readNTAG()
                if r.present == false {
                    lastError = "no card on reader"
                    restoreDocument()
                } else if let s = startUID, let ru = r.uid, Self.normUID(ru) != Self.normUID(s) {
                    lastError = "card changed during read"
                    restoreDocument()
                } else {
                    // Keep the whole read (pages + detected type + its own UID + version /
                    // signature / counters) so the load-to-slot path re-emulates this exact
                    // tag, not a page-count guess against the live card.
                    let doc = Self.buildNtagDoc(r)
                    withAnimation(.easeInOut(duration: 0.3)) {
                        ntagDoc = doc; source = nil; sectors = []; selected = nil
                    }
                }
            } else {
                // show the whole grid right away (all pending) so sectors fill in
                // live as each one is searched, instead of a blank wait.
                let count = live.sak.map { sectorsForSak($0) } ?? 16
                withAnimation(.easeInOut(duration: 0.3)) {
                    sectors = Self.pendingSectors(count: count); ntagDoc = nil; selected = nil
                }
                let r = try await activeBridge().decode(userKeys: keyStore.keys,
                    onProgress: { [weak self] ev in Task { @MainActor in self?.applyDecodeEvent(ev) } })
                if let s = startUID, Self.normUID(r.uid) != Self.normUID(s) {
                    lastError = "card changed during decode"
                    restoreDocument()
                } else if r.recovered == 0 {
                    // A no-RESULT decode must NEVER drop a held, unsaved document - not
                    // even when the read card shares its uid (cloned access cards commonly
                    // do). Keep the image whenever one is held; only when nothing is held
                    // do we show the honest no-keys result (or a clean slate on a cancel).
                    if source != nil {
                        if r.cancelled != true { lastError = "no keys found on the card; the document is unchanged" }
                        restoreDocument()
                    } else if r.cancelled == true {
                        withAnimation(.easeInOut(duration: 0.3)) { sectors = []; ntagDoc = nil; selected = nil }
                    } else {
                        withAnimation(.easeInOut(duration: 0.3)) {
                            card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                            sectors = []; ntagDoc = nil; selected = nil
                            noKeysFound = true
                        }
                    }
                } else {
                    let vms = Self.buildSectors(r)
                    let dump = CardDump.from(r, name: r.uid.replacingOccurrences(of: " ", with: ""))
                    withAnimation(.easeInOut(duration: 0.3)) {
                        card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
                        sectors = vms
                        ntagDoc = nil
                        selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                        source = dump
                    }
                }
            }
        } catch {
            // A hard-kill cancel (the fallback when cooperative cancel does not land)
            // surfaces as a thrown error, not a real failure. Either way, restore the
            // held document rather than leaving a half-filled grid or dropping it.
            if !decodeCancelled { lastError = "\(error)" }
            restoreDocument()
        }
        finishDecode()
    }

    private func finishDecode() {
        decoding = false
        decodeCancelled = false
        decodeProgress = nil
        decodeStart = nil
    }

    /// Put the canvas back on the held working document (its sector grid), or empty it
    /// when nothing is held. Used when a decode produced no new result (no card, card
    /// changed, no keys against a held image, or a cancel) so an unsaved image is never
    /// left dropped or hidden behind a failed read.
    private func restoreDocument() {
        withAnimation(.easeInOut(duration: 0.3)) {
            if let s = source {
                sectors = Self.buildSectors(fromDump: s)
                ntagDoc = nil
                selected = sectors.first(where: { $0.hasKey })?.index ?? sectors.first?.index
            } else {
                sectors = []; ntagDoc = nil; selected = nil
            }
        }
    }

    /// Stop a long decode cooperatively: the daemon trips its cancel flag and returns
    /// the partial image it has gathered (which decode() then shows or discards), so we
    /// no longer kill the daemon on every cancel. A hard-kill fallback inside the bridge
    /// covers a wedged daemon that does not honour the cancel in time.
    func cancelDecode() async {
        guard decoding else { return }
        decodeCancelled = true
        await activeBridge().cancel()
    }

    /// Resolved-sector count (sectors whose key was found), an honest, monotonic
    /// secondary progress readout alongside the raw auth counter.
    var resolvedSectors: Int { sectors.filter { $0.status == .found }.count }

    /// Whole seconds elapsed in the running decode: bounded, forward-moving feedback
    /// where the auth counter has no meaningful denominator. 0 when not decoding.
    var decodeElapsed: Int { decodeStart.map { max(0, Int(Date().timeIntervalSince($0))) } ?? 0 }

    /// Fold a decode progress event into `decodeProgress`. The daemon emits a
    /// sector-boundary event (carries `total` = sector count) and a dictionary-walk
    /// event (carries attempts / walk_total) as it searches a sector's key.
    private func applyDecodeEvent(_ ev: EngineEvent) {
        guard decoding, let s = ev.sector else { return }
        let fallbackTotal = card?.sak.map { sectorsForSak($0) } ?? 16
        var p = decodeProgress ?? DecodeProgress(sector: 0, total: fallbackTotal, attempts: nil, walkTotal: nil)
        p.sector = s
        // `attempts` is the scan's GLOBAL, monotonic auth counter, so keep it across
        // sector boundaries - the status line then only ever moves forward. `walkTotal`
        // is the adaptive remaining-work estimate and shrinks as sectors resolve.
        if let t = ev.total { p.total = t }
        if let a = ev.attempts { p.attempts = a; p.walkTotal = ev.walk_total }
        decodeProgress = p

        // per-sector live tile state
        guard sectors.indices.contains(s) else { return }
        if ev.attempts != nil {                           // walk event: this sector is searching
            sectors[s].status = .searching
            sectors[s].searchTried = ev.attempts
            sectors[s].searchTotal = ev.walk_total
        } else if ev.total != nil {                       // sector boundary: this sector is done
            sectors[s].searchTried = nil
            sectors[s].searchTotal = nil
            if let kh = ev.key {
                sectors[s].status = .found
                sectors[s].keyType = ev.keytype
                sectors[s].keyHex = kh
                sectors[s].provenance = (kh == "ffffffffffff") ? .dictionary : .nonDefault
            } else {
                sectors[s].status = .failed
            }
        }
    }

    static func buildPages(_ r: NtagResult) -> [NtagPage] {
        guard let pages = r.pages else { return [] }
        return pages.compactMap { k, hex -> NtagPage? in
            guard let i = Int(k) else { return nil }
            // A null value is a page that could not be read (password-locked): show it as
            // a locked placeholder rather than dropping it, so the gap is visible.
            guard let hex else { return NtagPage(index: i, hex: "-- -- -- --", ascii: "", locked: true) }
            return NtagPage(index: i, hex: hex, ascii: asciiOf(hex))
        }.sorted { $0.index < $1.index }
    }

    /// The full NTAG working document: the page rows plus the read metadata (detected type,
    /// the tag's own UID, version / signature / counters) that the load-to-slot path needs
    /// to re-emulate the exact tag that was read.
    static func buildNtagDoc(_ r: NtagResult) -> NtagDocument {
        NtagDocument(pages: buildPages(r), uid: r.uid, type: r.type,
                     version: r.version, signature: r.signature, counters: r.counters)
    }

    /// Printable ASCII rendering of a space-separated hex page (non-printable -> '.').
    static func asciiOf(_ hex: String) -> String {
        let bytes = hex.split(separator: " ").compactMap { UInt8($0, radix: 16) }
        return String(bytes.map { (32...126).contains($0) ? Character(UnicodeScalar($0)) : "." })
    }

    static func buildSectors(_ r: DecodeResult) -> [SectorVM] {
        (0..<r.sectors).map { s in
            let key = r.keys[String(s)] ?? nil
            let kt = key?.first
            let kh = (key?.count == 2) ? key?[1] : nil
            let prov: KeyProvenance = kh == nil
                ? .unknown
                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
            let blocks = blockNumbers(ofSector: s).map { b in (r.blocks[String(b)] ?? nil) ?? "?" }
            return SectorVM(index: s, keyType: kt, keyHex: kh, provenance: prov, blocks: blocks,
                            status: kh == nil ? .failed : .found,
                            assumedSlot: r.assumed_keys?[String(s)])
        }
    }

    /// Build the grid from a loaded dump (File > Open), so an opened image renders
    /// its memory map exactly like a fresh decode. Dump block hex is stored without
    /// spaces; re-space it to match the daemon's display form the grid expects.
    static func buildSectors(fromDump d: CardDump) -> [SectorVM] {
        (0..<d.sectorCount).map { s in
            let k = d.keys[s]
            let kh = k?.hex
            let prov: KeyProvenance = kh == nil
                ? .unknown
                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
            let blocks = blockNumbers(ofSector: s).map { b in d.blocks[b].map(spacedHex) ?? "?" }
            return SectorVM(index: s, keyType: k?.type, keyHex: kh, provenance: prov, blocks: blocks,
                            status: kh == nil ? .failed : .found,
                            assumedSlot: d.assumedKeys[s])
        }
    }

    /// "0102..1f" -> "01 02 .. 1f" so dump-loaded blocks render like decode blocks.
    static func spacedHex(_ h: String) -> String {
        stride(from: 0, to: h.count, by: 2).compactMap { i -> String? in
            let start = h.index(h.startIndex, offsetBy: i)
            let end = h.index(start, offsetBy: min(2, h.count - i))
            return String(h[start..<end])
        }.joined(separator: " ")
    }

    /// The full sector grid, all pending, shown the instant decode starts so the
    /// card's memory map is visible and fills in live sector by sector.
    static func pendingSectors(count: Int) -> [SectorVM] {
        (0..<count).map {
            SectorVM(index: $0, keyType: nil, keyHex: nil, provenance: .unknown, blocks: [], status: .pending)
        }
    }

    // ---- copy (plain text) -------------------------------------------------

    /// Plain-text rendering of a sector: a header line with the key, then one
    /// line per block (absolute block number + hex). Used by ⌘C and the tile
    /// context menu so the grid is a real, copyable instrument.
    func sectorText(_ s: SectorVM) -> String {
        var head = "sector \(s.index)"
        if let kh = s.keyHex { head += "  (key \(s.keyType?.lowercased() ?? "a") \(kh))" }
        let base = firstBlock(s.index)
        let body = s.blocks.enumerated().map { i, hex in String(format: "%3d  %@", base + i, hex) }
        return ([head] + body).joined(separator: "\n")
    }

    /// Plain-text rendering of a single block: absolute block number + hex.
    func blockText(_ blk: Int, hex: String) -> String {
        String(format: "%3d  %@", blk, hex)
    }

    /// Plain text for ⌘C: the selected sector, or the whole NTAG page dump.
    func copySelectionText() -> String? {
        if let s = selectedSector { return sectorText(s) }
        if !pages.isEmpty {
            return pages.map { String(format: "%3d  %@  |%@|", $0.index, $0.hex, $0.ascii) }.joined(separator: "\n")
        }
        return nil
    }

    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    // ---- clone / write -----------------------------------------------------

    /// Write the explicit source dump onto the card on the reader. Data blocks
    /// only by default; trailers (keys/access) and block 0 (uid) are opt-in.
    ///
    /// `authorizedUID` is the uid the user saw and authorized when they pressed write /
    /// accepted the confirm. The card can be swapped by the live monitor while a confirm
    /// dialog is open, so we execute ONLY if the card on the reader still equals that
    /// authorization - never write to a card whose uid differs from the one shown.
    ///
    /// `realCard` re-keys a REAL (non-magic) card the owner already holds the keys to: on a
    /// Chameleon it routes to `write_mfd` (known-key auth + the anti-brick trailer guards)
    /// instead of the magic-card `magic_write`. The X7 already has no magic path, so it always
    /// uses write_mfd and the flag is a no-op there.
    func clone(trailers: Bool, uid: Bool, authorizedUID: String?, realCard: Bool = false) async {
        // Never clone while a swap is in flight, another device op owns the reader, or
        // the device is emulating (a reader-mode write would break the emulation).
        guard !swapping, !deviceBusy, !emulating else { return }
        guard let src = cloneSource else {
            lastError = "no clone source"
            return
        }
        // The authorization is bound to a specific card: if the card went absent or a
        // different one was seated (e.g. while the confirm dialog was open), the write
        // is not authorized for whatever is on the reader now. Abort rather than wipe it.
        guard let auth = authorizedUID, let target = card?.uid,
              Self.normUID(target) == Self.normUID(auth) else {
            lastError = "card changed, not written"
            return
        }
        cloning = true
        cloneResults = [:]
        cloneFailReasons = [:]
        lastError = nil
        // Per-block glyph updates stream the same way for both write paths.
        let onBlock: @Sendable (Int, Bool, String?) -> Void = { [weak self] b, ok, reason in
            Task { @MainActor in
                withAnimation(.easeOut(duration: 0.16)) { self?.cloneResults[b] = ok }
                if let reason { self?.cloneFailReasons[b] = reason }
            }
        }
        do {
            // Capability-driven route: a Chameleon clones onto a MAGIC card via `magic_write`,
            // or re-keys a REAL card (realCard) via `write_mfd`; the X7 has no magic path and
            // always uses write_mfd. All three return the same WriteResult, so the outcome
            // handling below is shared.
            let r: WriteResult
            if capabilities.emulate && !realCard {
                r = try await activeBridge().magicWrite(
                    blocks: src.blockParams, keys: src.keyParams, trailers: trailers, uid: uid,
                    targetUID: target, onBlock: onBlock)
            } else {
                r = try await activeBridge().writeMFD(
                    blocks: src.blockParams, keys: src.keyParams, trailers: trailers, uid: uid,
                    targetUID: target, onBlock: onBlock)
            }
            // Per-block glyphs in the grid/inspector are the primary failure surface;
            // lastError is the summary shown in the status banner, phrased in card terms.
            if r.present == false {
                lastError = "no card on reader"
            } else if let e = r.error {
                lastError = e
            } else if let failed = r.failed, !failed.isEmpty {
                lastError = Self.cloneFailureSummary(failed, reasons: cloneFailReasons)
            }
        } catch {
            lastError = "\(error)"
        }
        cloning = false
    }

    /// Snapshot the current card uid and open the format confirmation. The snapshot is
    /// what the dialog shows and what the erase is pinned to, so a card swapped in while
    /// the dialog is open is never the one wiped.
    func requestFormat() {
        pendingFormatUID = card?.uid
        formatConfirm = true
    }

    /// Factory-reset the card on the reader (zero data + factory trailer). Offered for
    /// ANY present card, not only the one just decoded: auth uses the document's
    /// recovered keys when the document IS this card, otherwise a factory-key (FF) wipe,
    /// which is what a blank or freshly-issued card needs. Destructive, so the UI gates
    /// it behind a confirm. The anti-brick guards (trailer written last, per-card uid
    /// pin) stay in the daemon; a card whose keys are unknown simply fails, never bricks.
    func format(authorizedUID: String?) async {
        // Never format while a swap is in flight, another device op owns the reader, or
        // the device is emulating (a reader-mode erase would break the emulation).
        guard !swapping, !deviceBusy, !emulating else { return }
        // Bound to the card the user authorized in the confirm dialog: if it was swapped
        // or lifted while the dialog was open, do not erase whatever is on the reader now.
        guard let auth = authorizedUID, let target = card?.uid,
              Self.normUID(target) == Self.normUID(auth) else {
            lastError = "card changed, not written"
            return
        }
        formatting = true
        cloneResults = [:]
        lastError = nil
        // Only the document's keys help when it IS this card; an unrelated / absent
        // document contributes nothing, so fall back to a factory-key wipe attempt.
        let keys = canFormat ? (source?.keyParams ?? [:]) : [:]
        do {
            let r = try await activeBridge().formatCard(keys: keys, targetUID: target)
            if r.present == false {
                lastError = "no card on reader"
            } else if let e = r.error {
                lastError = e                      // aborted (wrong / swapped card): keep the image
            } else if let failed = r.failed, !failed.isEmpty {
                // A partial or fully failed format did NOT blank the card, so keep the
                // document: it may still be the only copy of the image.
                lastError = Self.formatFailureSummary(failed)
            }
            // On a clean format the document is kept (not dropped): its uid is unchanged
            // (block 0 is left intact), so it stays available to erase/re-issue the next
            // identical card without decoding each one again.
        } catch {
            lastError = "\(error)"
        }
        formatting = false
    }

    /// Aggregate clone status for one sector tile, from the per-block results.
    func cloneStatus(ofSector s: Int) -> SectorCloneStatus {
        let results = blockNumbers(ofSector: s).compactMap { cloneResults[$0] }
        if results.isEmpty { return .none }
        return results.contains(false) ? .failed : .ok
    }

    /// Which sector an absolute block belongs to (4K big-sector layout aware).
    static func sectorOf(_ block: Int) -> Int { block < 128 ? block / 4 : 32 + (block - 128) / 16 }

    /// A clone failure summary in card terms: name the sector and, for a refused
    /// trailer, WHY (the daemon already computed it), instead of raw block indices.
    static func cloneFailureSummary(_ failed: [Int], reasons: [Int: String]) -> String {
        let parts: [String] = failed.sorted().map { b in
            let s = sectorOf(b)
            switch reasons[b] {
            case "access-bits":    return "sector \(s) trailer refused: unsafe access bits"
            case "trailer-lockout": return "sector \(s) trailer refused: would lock its own keys"
            default:               return "sector \(s) block \(b)"
            }
        }
        return "write failed - " + parts.joined(separator: "; ")
    }

    /// A format failure summary in card terms (which sectors could not be wiped).
    static func formatFailureSummary(_ failed: [Int]) -> String {
        let sectors = Set(failed.map { sectorOf($0) }).sorted().map(String.init).joined(separator: ", ")
        return "format failed - sector(s) \(sectors) could not be wiped"
    }

    // ---- apdu --------------------------------------------------------------

    /// Send a raw APDU to the card on the reader and append the outcome to the
    /// console transcript. Distinguishes a real response, a card that gave no
    /// answer (e.g. a MIFARE Classic, not ISO14443-4), and no card present.
    func sendAPDU(_ hex: String) async {
        let clean = hex.trimmingCharacters(in: .whitespaces).lowercased()
        // Never send while a swap is in flight, another device op owns the reader, or the
        // device is emulating (an apdu is a reader op that would break the emulation).
        guard !clean.isEmpty, !swapping, !deviceBusy, !emulating else { return }
        apduBusy = true
        let id = (apduLog.last?.id ?? 0) + 1
        do {
            let r = try await activeBridge().apdu(clean)
            if !r.present {
                apduLog.append(ApduEntry(id: id, tx: clean, rx: nil, info: "apdu_no_card"))
            } else if let resp = r.resp {
                apduLog.append(ApduEntry(id: id, tx: clean, rx: resp, info: nil))
            } else {
                apduLog.append(ApduEntry(id: id, tx: clean, rx: nil, info: "apdu_no_response"))
            }
        } catch {
            apduLog.append(ApduEntry(id: id, tx: clean, rx: nil, info: "apdu_error"))
            lastError = "\(error)"
        }
        apduBusy = false
    }

    // ---- Chameleon slot library + emulation --------------------------------

    /// Load the 8-slot library (opening the slot view or refreshing after an edit).
    func loadSlots() async {
        guard capabilities.slots > 0, !swapping, !deviceBusy else { return }
        do { slots = try await activeBridge().slotsList(); lastError = nil }
        catch { lastError = "\(error)" }
    }

    /// Make a slot the active one (the card the device presents / emulates).
    func selectSlot(_ i: Int) async { await slotOp { try await $0.slotSelect(i) } }

    /// Enable / disable a slot's HF or LF field.
    func enableSlot(_ i: Int, sense: String, enabled: Bool) async {
        await slotOp { try await $0.slotEnable(slot: i, sense: sense, enabled: enabled) }
    }

    /// Clear (reset) a slot's HF or LF field, discarding its emulated content.
    func clearSlotField(_ i: Int, sense: String) async {
        await slotOp { try await $0.slotClear(slot: i, sense: sense) }
    }

    /// Set a slot's emulated tag type (+ seed its default data).
    func setSlotType(_ i: Int, type: String) async {
        await slotOp { try await $0.slotSetType(slot: i, type: type) }
    }

    /// Rename a slot's HF field (its primary label in the library).
    func renameSlot(_ i: Int, sense: String = "hf", name: String) async {
        await slotOp { _ = try await $0.slotNick(slot: i, sense: sense, name: name) }
    }

    /// Persist the whole slot configuration + data to flash.
    func saveSlots() async { await slotOp { try await $0.slotSave() } }

    /// Shared slot-op runner: guards a swap / concurrent op, marks the reader busy, runs
    /// the op, then reloads the library so the grid reflects the change.
    private func slotOp(_ op: (DeviceBridge) async throws -> Void) async {
        guard capabilities.slots > 0, !swapping, !deviceBusy else { return }
        slotBusy = true
        do {
            let b = activeBridge()
            try await op(b)
            slots = try await b.slotsList()
            lastError = nil
        } catch { lastError = "\(error)" }
        slotBusy = false
    }

    /// Read a slot's HF MIFARE Classic emulator content into the working document, so
    /// the existing sector grid / inspector render it (and it becomes savable / cloneable).
    /// Returns to the document canvas. Only 1K / 4K HF Classic slots are openable.
    func openSlotContent(_ i: Int) async {
        guard capabilities.slots > 0, !swapping, !deviceBusy else { return }
        guard let slot = slots.first(where: { $0.index == i }), let geo = slot.hfGeometry else {
            lastError = "slot has no readable MIFARE Classic content"
            return
        }
        slotBusy = true
        do {
            let b = activeBridge()
            try await b.slotSelect(i)
            let raw = try await b.emuRead(count: geo.count)
            var map: [Int: String] = [:]
            for (k, v) in raw { if let blk = Int(k) { map[blk] = v.replacingOccurrences(of: " ", with: "") } }
            let dump = CardDump.fromBlocks(map, sak: geo.sak, name: "slot \(i + 1)")
            let vms = Self.buildSectors(fromDump: dump)
            withAnimation(.easeInOut(duration: 0.3)) {
                source = dump; sectors = vms; ntagDoc = nil
                selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                selectedBlock = nil; showSlots = false
                cloneResults = [:]; cloneFailReasons = [:]; noKeysFound = false
            }
            slots = try await b.slotsList()          // the active flag moved to i
            lastError = nil
        } catch { lastError = "\(error)" }
        slotBusy = false
    }

    /// Load the working document into a chosen slot's HF emulator, so the Chameleon
    /// emulates that card. Needs a held document + an emulation-capable device. Routes a
    /// MIFARE Classic source through the block path, and an NTAG / Ultralight read through
    /// the UL/NTAG page path.
    func loadDocumentToSlot(_ i: Int) async {
        if let src = source {
            await writeDumpToSlot(src, slot: i)
        } else if let doc = ntagDoc {
            await writeNtagToSlot(doc, slot: i)
        }
    }

    /// The UL/NTAG TagSpecificType name to emulate a page dump as, chosen by readable page
    /// count. Only a FALLBACK for a read that did not resolve a type (GET_VERSION absent /
    /// unrecognized storage byte); a read that resolved its type uses that instead.
    static func ntagSlotType(pageCount n: Int) -> String {
        switch n {
        case ...20: return "MF0UL11"        // Ultralight EV1 640-bit / NTAG210 (20 pages)
        case 21...41: return "MF0UL21"      // Ultralight EV1 1312-bit / NTAG212 (41 pages)
        case 42...45: return "NTAG_213"     // 45 pages
        case 46...135: return "NTAG_215"    // 135 pages
        default: return "NTAG_216"          // 231 pages
        }
    }

    /// Write an NTAG / Ultralight read into a chosen slot's HF emulator so the Chameleon
    /// emulates the exact tag that was read: select the slot, enable HF, load the pages as a
    /// UL/NTAG tag using the READ-detected type (page-count fallback only when the read did
    /// not resolve one), the tag's OWN UID (not the live card), and its version / signature /
    /// counters, then save. The Classic writeDumpToSlot path is untouched.
    func writeNtagToSlot(_ doc: NtagDocument, slot i: Int) async {
        guard capabilities.emulate, !swapping, !deviceBusy else { return }
        slotBusy = true
        // Use the detected type; only guess from page count when the read could not resolve one.
        let type = doc.type ?? Self.ntagSlotType(pageCount: doc.pages.count)
        var pageMap: [String: String] = [:]
        for page in doc.pages where !page.locked {
            pageMap[String(page.index)] = page.hex.replacingOccurrences(of: " ", with: "")
        }
        let uid = doc.uid?.replacingOccurrences(of: " ", with: "")
        let version = doc.version?.replacingOccurrences(of: " ", with: "")
        let signature = doc.signature?.replacingOccurrences(of: " ", with: "")
        do {
            let b = activeBridge()
            try await b.slotSelect(i)
            try await b.slotEnable(slot: i, sense: "hf", enabled: true)
            try await b.emulateLoadNtag(pages: pageMap, type: type, uid: uid,
                                        version: version, signature: signature, counters: doc.counters)
            try await b.slotSave()
            slots = try await b.slotsList()
            lastError = nil
        } catch { lastError = "\(error)" }
        slotBusy = false
    }

    /// Write a dump into a chosen slot's HF emulator: select the slot, set its type +
    /// enable HF from the dump, load the blocks, then save. Shared by the working-document
    /// load and the saved-cards library's write-to-slot, so both take the exact same
    /// bridge path (no new daemon verb).
    func writeDumpToSlot(_ dump: CardDump, slot i: Int) async {
        guard capabilities.emulate, !swapping, !deviceBusy else { return }
        slotBusy = true
        let type = dump.sak == 0x18 ? "MIFARE_4096" : "MIFARE_1024"
        do {
            let b = activeBridge()
            try await b.slotSelect(i)
            try await b.slotSetType(slot: i, type: type)
            try await b.slotEnable(slot: i, sense: "hf", enabled: true)
            try await b.emulateLoad(blocks: dump.blockParams)
            try await b.slotSave()
            slots = try await b.slotsList()
            lastError = nil
        } catch { lastError = "\(error)" }
        slotBusy = false
    }

    /// Flip the device between reader mode and tag/emulate mode. While emulating the
    /// status monitor stops polling (a poll forces reader mode), so the toggle holds
    /// until flipped back.
    func toggleEmulate() async {
        guard capabilities.emulate, !swapping, !deviceBusy else { return }
        let target = !emulating
        slotBusy = true
        do {
            try await activeBridge().emulateMode(reader: !target)
            emulating = target
            lastError = nil
        } catch { lastError = "\(error)" }
        slotBusy = false
    }

    // ---- LF 125 kHz (Chameleon-only, gated on capabilities.lf) -------------

    /// Read the LF (125 kHz) tag on the reader (em410x, then hidprox). A reader op, so it
    /// is refused while emulating (a reader op would force the device out of tag mode).
    func lfScan() async {
        guard capabilities.lf, !swapping, !deviceBusy, !emulating else { return }
        lfBusy = true
        lfWriteResult = nil
        lastError = nil
        do { lfScanResult = try await activeBridge().lfScan(); lastError = nil }
        catch { lastError = "\(error)" }
        lfBusy = false
    }

    /// Write an LF id onto a blank T5577 on the reader (the LF clone). Destructive - the LF
    /// panel gates it behind a confirm. `kind` is "em410x" / "hidprox"; the daemon reads the
    /// tag back to verify and the panel shows that outcome. Refused while emulating.
    func lfWrite(kind: String, id: String) async {
        guard capabilities.lf, !swapping, !deviceBusy, !emulating else { return }
        let clean = id.replacingOccurrences(of: " ", with: "")
        guard !clean.isEmpty else { return }
        lfBusy = true
        lfWriteResult = nil
        lastError = nil
        do { lfWriteResult = try await activeBridge().lfWrite(kind: kind, id: clean) }
        catch { lastError = "\(error)" }
        lfBusy = false
    }

    /// Load an EM410x id into a slot for LF emulation: select the slot, set its LF field to
    /// the type matching the id length (5-byte -> EM410X, 13-byte -> EM410X_ELECTRA), enable
    /// it, set the emulation id, then save. The slot type MUST match the id length, or the
    /// command layer rejects the id (a 13-byte id on an EM410X slot expects 5 bytes). EM410x
    /// family only (the v1 LF emulate scope). Reuses the slot bridge path plus lf_emu; no
    /// reader op, so it is not gated on emulate mode (presented once the user flips emulate).
    func loadLFEmu(id: String, slot i: Int) async {
        guard capabilities.lf, capabilities.emulate, !swapping, !deviceBusy else { return }
        let clean = id.replacingOccurrences(of: " ", with: "")
        // 5-byte EM410x or 13-byte Electra; anything else has no emulate path.
        let type: String
        switch clean.count {
        case 10: type = "EM410X"
        case 26: type = "EM410X_ELECTRA"
        default: lastError = "lf emulate id must be a 5-byte EM410x or 13-byte Electra id"; return
        }
        lfBusy = true
        do {
            let b = activeBridge()
            try await b.slotSelect(i)
            try await b.slotSetType(slot: i, type: type)
            try await b.slotEnable(slot: i, sense: "lf", enabled: true)
            _ = try await b.lfEmu(id: clean)
            try await b.slotSave()
            slots = try await b.slotsList()
            lastError = nil
        } catch { lastError = "\(error)" }
        lfBusy = false
    }

    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------

    /// Read the running firmware + the newest published release (opening the flashing
    /// sheet or after a flash). A failed release fetch is not fatal - the daemon returns
    /// the current version with a null latest + a note.
    func checkFirmware() async {
        guard capabilities.dfu, !swapping, !deviceBusy else { return }
        // Firmware update is never offered over a BLE link (the USB DFU flasher could hit the
        // wrong physical device). Defensive: the button is already hidden over BLE.
        guard !deviceOverBLE else { return }
        flashError = nil
        // A device already in the bootloader has no command interface to query: leave the
        // status empty (the sheet shows the Ultra/Lite recovery choice) rather than erroring.
        if deviceInDFU { dfuStatus = nil; return }
        do { dfuStatus = try await activeBridge().dfuCheck(); lastError = nil }
        catch { lastError = "\(error)" }
    }

    /// Flash firmware over DFU (v1 is download-only: the daemon fetches the official
    /// model-specific asset). `model` is nil in normal mode (read off the device) and
    /// "ultra"/"lite" only when recovering a device stuck in DFU whose model cannot be read.
    /// The daemon validates the download (app-only, hash) before writing anything and refuses
    /// a mid-write cancel, so this is a commit-once action. The device reboots into the
    /// bootloader and back; `flashing` pauses the status monitor across that so it does not
    /// mistake the reboot for an unplug.
    func flashFirmware(model: String?) async {
        guard capabilities.dfu, !swapping, !deviceBusy else { return }
        // Never flash over a BLE link (the USB DFU flasher could hit the wrong physical
        // device). Defensive: the firmware button is hidden over BLE.
        guard !deviceOverBLE else { return }
        flashing = true
        flashDone = false
        flashError = nil
        flashStage = nil
        flashPercent = nil
        lastError = nil
        let onProgress: @Sendable (String?, Int?) -> Void = { [weak self] stage, pct in
            Task { @MainActor in
                if let stage { self?.flashStage = stage }
                if let pct { self?.flashPercent = pct }
            }
        }
        do {
            let r = try await activeBridge().dfuFlash(model: model, onProgress: onProgress)
            if r.cancelled == true {
                flashStage = nil
            } else if r.flashed {
                flashStage = "done"
                flashPercent = 100
                flashDone = true
            }
        } catch {
            // Show the failure INSIDE the sheet with the retry path (a failed flash usually
            // leaves the device in the bootloader), not only the root banner behind the modal.
            flashError = "\(error)"
        }
        flashing = false
    }

    // ---- file dumps --------------------------------------------------------

    func openDumpDialog() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.data]
        panel.allowsOtherFileTypes = true
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url { loadDump(from: url) }
    }

    /// Default save name `yymmdd_tr_<uid>.dump` - sorts next to the Windows nfcPro
    /// dumps in the same folder and stays a plain raw image both tools can open.
    static func defaultDumpFilename(_ dump: CardDump) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyMMdd"
        let uid = dump.uid.replacingOccurrences(of: " ", with: "").lowercased()
        let stem = uid.isEmpty ? dump.name : uid
        return "\(f.string(from: Date()))_tr_\(stem).dump"
    }

    func saveDumpDialog() {
        guard let dump = source else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = Self.defaultDumpFilename(dump)
        panel.allowsOtherFileTypes = true
        if let folder = UserDefaults.standard.string(forKey: "rekey.exportFolder") {
            panel.directoryURL = URL(fileURLWithPath: folder)
        }
        if panel.runModal() == .OK, let url = panel.url { saveDump(dump, to: url) }
    }

    /// Open a dump as the working document: it becomes the source AND its memory map
    /// is rendered on the canvas (a loaded image is a document just like a decode), so
    /// the canvas never shows a stale grid under a freshly opened source.
    func loadDump(from url: URL) {
        do {
            let dump = try CardDump.load(mfd: url)
            let vms = Self.buildSectors(fromDump: dump)
            withAnimation(.easeInOut(duration: 0.3)) {
                source = dump
                sectors = vms
                ntagDoc = nil
                selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                selectedBlock = nil
                cloneResults = [:]; cloneFailReasons = [:]
            }
            NSDocumentController.shared.noteNewRecentDocumentURL(url)
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }

    private func saveDump(_ dump: CardDump, to url: URL) {
        do {
            try dump.mfdData().write(to: url)
            try dump.keysJSON().write(to: url.appendingPathExtension("keys.json"))
            lastError = nil
        } catch {
            lastError = "\(error)"
        }
    }

    // ---- saved-cards library -----------------------------------------------

    /// Re-read the library index (opening the library view, or after a mutation).
    func refreshSavedCards() { savedCards = savedCardStore.list() }

    /// Save the current working document into the library and select the new entry. The
    /// label defaults to the document's uid (renamable afterwards). No-op with no document.
    func saveCurrentToLibrary() {
        guard let src = source else { return }
        do {
            let entry = try savedCardStore.save(src, name: "")
            refreshSavedCards()
            selectedSavedCard = entry.id
            lastError = nil
        } catch { lastError = error.localizedDescription }
    }

    /// Load a saved card as the working document: it becomes the source and its memory map
    /// renders on the canvas (like Open), so it can be cloned / emulated. Returns to the
    /// document canvas.
    func loadSavedCard(_ card: SavedCard) {
        do {
            let dump = try savedCardStore.load(card.id)
            let vms = Self.buildSectors(fromDump: dump)
            withAnimation(.easeInOut(duration: 0.3)) {
                source = dump; sectors = vms; ntagDoc = nil
                selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
                selectedBlock = nil; showLibrary = false
                cloneResults = [:]; cloneFailReasons = [:]; noKeysFound = false
            }
            lastError = nil
        } catch { lastError = error.localizedDescription }
    }

    /// Relabel a saved card.
    func renameSavedCard(_ card: SavedCard, name: String) {
        do { try savedCardStore.rename(card.id, to: name); refreshSavedCards(); lastError = nil }
        catch { lastError = error.localizedDescription }
    }

    /// Delete a saved card (and its backing files). The physical card is untouched.
    func deleteSavedCard(_ card: SavedCard) {
        do {
            try savedCardStore.delete(card.id)
            if selectedSavedCard == card.id { selectedSavedCard = nil }
            refreshSavedCards()
            lastError = nil
        } catch { lastError = error.localizedDescription }
    }

    /// Import a dump from another tool (Proxmark3 .eml / .json / .bin, Flipper .nfc) into
    /// the library and select it. `unrecognised` is the localized message shown when the
    /// file matches none of the parsers (the model has no L10n, so the view supplies it).
    func importCardFile(from url: URL, unrecognised: String) {
        let dump: CardDump
        do { dump = try CardDump.importFile(url: url) }
        catch { lastError = unrecognised; return }
        do {
            let entry = try savedCardStore.save(dump, name: dump.name)
            refreshSavedCards()
            selectedSavedCard = entry.id
            lastError = nil
        } catch { lastError = error.localizedDescription }
    }

    /// Write a saved card into a Chameleon slot's HF emulator, reusing the shared
    /// load-to-slot path. Gated on an emulation-capable device (the library UI shows this
    /// only when a Chameleon is connected).
    func writeSavedCardToSlot(_ card: SavedCard, slot i: Int) async {
        do {
            let dump = try savedCardStore.load(card.id)
            await writeDumpToSlot(dump, slot: i)
        } catch { lastError = error.localizedDescription }
    }
}

enum SectorCloneStatus { case none, ok, failed }
