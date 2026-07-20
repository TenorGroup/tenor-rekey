import Foundation
import IOKit
import IOKit.hid
import IOKit.serial

/// Describes one device family the app can drive: which daemon speaks for it, where
/// its script lives under the probe root, how to recognise its USB device, and the
/// static capability baseline to assume before the daemon's own `info` manifest
/// lands. The shell picks a descriptor by USB match, spawns its daemon, and gates
/// UI on capabilities - it never hardcodes "if X7 / if Chameleon".
struct DeviceDescriptor: Identifiable, Equatable, Sendable {
    let id: String
    let family: String
    let displayName: String
    let daemonScript: String        // e.g. "x7d.py" / "chameleon_d.py"
    let probeSubdir: String?        // relative subdir under the probe root, nil = root
    let usbMatch: USBMatch
    let capabilities: DeviceCapabilities
}

/// How to recognise a device on the USB bus. `pid == nil` matches any product id
/// under the vendor (the Chameleon re-enumerates to a different pid in bootloader,
/// so it is matched by vendor alone).
struct USBMatch: Equatable, Sendable {
    enum Transport: Sendable { case hid, serial }
    let vid: Int
    let pid: Int?
    let transport: Transport
}

/// The device catalogue + USB detection. `detect()` returns the first present
/// device in priority order, or nil when none is plugged in.
enum DeviceRegistry {
    /// XIXEI X7 (HID PN533 reader) -> x7d.py.
    static let x7 = DeviceDescriptor(
        id: "x7", family: "x7", displayName: "XIXEI X7",
        daemonScript: "x7d.py", probeSubdir: nil,
        usbMatch: USBMatch(vid: 0x2518, pid: 0x6022, transport: .hid),
        capabilities: .x7)

    /// Chameleon Ultra / Lite (USB-CDC serial) -> chameleon_d.py. Matched by vendor
    /// id only; the daemon's `info` reports Ultra vs Lite and the real capabilities.
    static let chameleonUltra = DeviceDescriptor(
        id: "chameleon-ultra", family: "chameleon-ultra", displayName: "Chameleon Ultra",
        daemonScript: "chameleon_d.py", probeSubdir: nil,
        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial),
        capabilities: .chameleonUltra)

    /// A Chameleon sitting in the Nordic bootloader (re-enumerated to VID 0x1915). It
    /// has no command interface (the daemon's `info` cannot query it), but recognising it
    /// as a Chameleon-in-DFU keeps the firmware/flash action reachable so a stuck or
    /// manually-B-buttoned device can be flash-recovered after relaunch, instead of
    /// silently launching into the X7 fallback with DFU hidden. Same daemon (chameleon_d.py).
    static let chameleonDFU = DeviceDescriptor(
        id: "chameleon-dfu", family: "chameleon-dfu", displayName: "Chameleon (DFU)",
        daemonScript: "chameleon_d.py", probeSubdir: nil,
        usbMatch: USBMatch(vid: 0x1915, pid: 0x521f, transport: .serial),
        capabilities: .chameleonDFU)

    /// Every family the app can drive, in match priority order (X7 first, so a
    /// machine with both plugged in keeps driving the X7 until the user unplugs it; the
    /// DFU match is last, only relevant when a Chameleon is stuck in the bootloader).
    static let all: [DeviceDescriptor] = [x7, chameleonUltra, chameleonDFU]

    /// The descriptor to use when nothing is detected: the X7, so a bare machine
    /// starts the X7 daemon and shows "reader offline" exactly as the single-device
    /// build did.
    static let fallback = x7

    /// The first present device by USB match, or nil when none is connected. Cheap
    /// enough to run on the status poll (a bounded IORegistry scan, no I/O).
    static func detect() -> DeviceDescriptor? {
        all.first { USBProbe.isPresent($0.usbMatch) }
    }
}

/// Point-in-time USB presence checks over IOKit. HID devices (the X7) are found via
/// IOHIDManager; CDC serial devices (the Chameleon) via the serial BSD service,
/// walking up to the owning USB device node to read its vendor / product id.
enum USBProbe {
    static func isPresent(_ m: USBMatch) -> Bool {
        switch m.transport {
        case .hid: return hidPresent(vid: m.vid, pid: m.pid)
        case .serial: return serialPresent(vid: m.vid, pid: m.pid)
        }
    }

    private static func hidPresent(vid: Int, pid: Int?) -> Bool {
        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        var match: [String: Any] = [kIOHIDVendorIDKey as String: vid]
        if let pid { match[kIOHIDProductIDKey as String] = pid }
        IOHIDManagerSetDeviceMatching(manager, match as CFDictionary)
        guard let devices = IOHIDManagerCopyDevices(manager) else { return false }
        return CFSetGetCount(devices) > 0
    }

    private static func serialPresent(vid: Int, pid: Int?) -> Bool {
        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return false }
        var iterator: io_iterator_t = 0
        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
            return false
        }
        defer { IOObjectRelease(iterator) }
        var found = false
        var service = IOIteratorNext(iterator)
        while service != 0 {
            if usbAncestorMatches(service, vid: vid, pid: pid) { found = true }
            IOObjectRelease(service)
            if found { break }
            service = IOIteratorNext(iterator)
        }
        return found
    }

    /// Walk up the IOService plane from a leaf (the serial client) to the USB device
    /// node that carries `idVendor` / `idProduct`, matching there. Bounded so a
    /// malformed registry can never loop.
    private static func usbAncestorMatches(_ service: io_object_t, vid: Int, pid: Int?) -> Bool {
        var node = service
        IOObjectRetain(node)
        defer { IOObjectRelease(node) }
        for _ in 0..<10 {
            if intProperty(node, "idVendor") == vid {
                if pid == nil || intProperty(node, "idProduct") == pid { return true }
            }
            var parent: io_registry_entry_t = 0
            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
            guard kr == KERN_SUCCESS, parent != 0 else { return false }
            IOObjectRelease(node)
            node = parent
        }
        return false
    }

    private static func intProperty(_ entry: io_registry_entry_t, _ key: String) -> Int? {
        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
            return nil
        }
        return (cf.takeRetainedValue() as? NSNumber)?.intValue
    }
}
