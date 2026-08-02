Reading additional input from stdin...
2026-07-21T03:38:26.684820Z ERROR codex_models_manager::cache: failed to load models cache: missing field `supports_reasoning_summaries` at line 88 column 5
OpenAI Codex v0.144.1
--------
workdir: /Users/tuan/Claude/Tenor/tenor-rekey
model: gpt-5.6-sol
provider: openai
approval: never
sandbox: read-only
reasoning effort: high
reasoning summaries: none
session id: 019f82c0-e4aa-72a1-84fa-3a058e9b5cae
--------
user
You are a rigorous macOS/Swift + Python reviewer acting as the founder's proxy gate on an UNCOMMITTED change to the tenor/rekey app (a native macOS app that drives RFID readers: XIXEI X7 over USB-HID and a Chameleon Ultra over USB-CDC-serial, via per-device Python daemons over JSON on a pipe).

CONTEXT: The founder reported "plugging in the Chameleon over USB does not connect, and there is no button to reconnect or rescan bluetooth." This change adds a USB "Connect" surface (device list + Rescan + manual serial-port pin + honest diagnostics + a disabled Bluetooth placeholder), makes Chameleon USB auto-detection more robust (match by vendor-name/product-string as well as vid), and lets the daemon honor an explicit CHAMELEON_PORT env. Bluetooth/BLE is intentionally NOT implemented yet.

Inspect the full uncommitted diff yourself and read the new file:
  git --no-pager diff -- app probe
  git --no-pager status --short
  cat app/Sources/Views/ConnectView.swift
Read surrounding context in these files as needed: app/Sources/AppModel.swift, app/Sources/Engine/DeviceRegistry.swift, app/Sources/Engine/DeviceBridge.swift, app/Sources/Shell/RootView.swift, probe/chameleon_d.py.

Give me ONLY real, actionable findings on these bounded questions (skip praise, skip style nits unless they cause bugs):
1. CORRECTNESS of the manual-pin monitor logic in AppModel: can a manually-pinned device (portOverride) get stuck, or can the normal auto-detect/hot-swap path (X7 <-> Chameleon) regress? Consider: user pins a port, then unplugs it; user pins then plugs an X7; rescan after a pin.
2. IOKit correctness in DeviceRegistry (USBProbe.serialPorts / usbAncestorInfo / usbAncestorMatches / stringProperty): any io_object retain/release leak or over-release, unbounded walk, wrong registry property key ("USB Vendor Name"/"USB Product Name"/"IOCalloutDevice"/"idVendor"), or case where a real Chameleon still would NOT be detected.
3. connectManual / swapDevice / DeviceBridge CHAMELEON_PORT env: any race with the 1.5s monitor or deviceBusy guard, any regression to the auto-detect spawn (env replacement), any way the pinned daemon opens the WRONG port.
4. Daemon chameleon_d.py: _looks_like_chameleon / _find_port / __init__ CHAMELEON_PORT handling - any way it now mis-picks a DFU device or a non-Chameleon serial port, or fails to honor the env.
5. Does this change actually make the reported "Chameleon USB does not connect" case RECOVERABLE for the user? If there is a residual gap where a plugged-in Chameleon still cannot be connected through this UI, name it precisely.

For each finding: file:line, severity (CRITICAL/HIGH/MED/LOW), the concrete failure scenario, and the minimal fix. Be blunt. If you would bullshit, do not bother. If the change is sound, say so plainly.
codex
I’ll inspect the exact working-tree diff and trace the monitor, registry, bridge, and daemon paths end to end. I’ll report only concrete defects tied to the five questions.
exec
/bin/zsh -lc "git --no-pager diff -- app probe && git --no-pager status --short && sed -n '1,260p' app/Sources/Views/ConnectView.swift" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 3514ms:
2026-07-21 10:38:36.832 xcodebuild[41332:3238019]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:38:36.970 xcodebuild[41332:3238018] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-vYEn22UO' (errno=Operation not permitted)
2026-07-21 10:38:37.430 xcodebuild[41334:3238028]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:38:37.565 xcodebuild[41334:3238027] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/app/Sources/AppModel.swift b/app/Sources/AppModel.swift
index 2940bda..015155c 100644
--- a/app/Sources/AppModel.swift
+++ b/app/Sources/AppModel.swift
@@ -32,6 +32,14 @@ final class AppModel {
     var lastError: String?
     var inspectorOpen = true
 
+    // ---- Connect surface (USB now; Bluetooth is a later pass) --------------
+    /// The Connect popover (device list + rescan + manual serial connect) is open.
+    var showConnect = false
+    /// Known readers currently present on the USB bus (the detected-devices list).
+    var detectedDevices: [DeviceDescriptor] = []
+    /// Every enumerated USB serial port (the manual-connect list).
+    var serialPorts: [SerialPortInfo] = []
+
     /// The working DOCUMENT: the image produced by a decode or loaded from a file.
     /// It is what the canvas shows, what Save writes out, and what Write clones onto
     /// the card on the reader. It is independent of whichever card is currently on
@@ -159,6 +167,15 @@ final class AppModel {
     /// the firmware flash can still recover it, so the firmware action stays reachable.
     var deviceInDFU: Bool { descriptor.family == "chameleon-dfu" }
 
+    /// The family of the device currently driven, so the Connect surface can mark the
+    /// active row. A manual pin keeps its base family (chameleon-ultra), so matching a
+    /// detected row by family lights the right one in every case.
+    var activeDeviceFamily: String { descriptor.family }
+
+    /// A device swap / (re)connect is in flight, exposed read-only so the Connect
+    /// surface can show a spinner and disable Rescan while it runs.
+    var connecting: Bool { swapping }
+
     /// The user's editable keys (Settings > Dictionaries), tried before the
     /// daemon's large built-in dictionary.
     let keyStore = KeyStore()
@@ -201,6 +218,13 @@ final class AppModel {
     /// the old daemon is torn down (never silently orphaned) under the swap guard.
     func connect() async {
         guard !swapping, !deviceBusy else { return }
+        await detectAndOpen()
+    }
+
+    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
+    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
+    /// is torn down, and bring the current one up.
+    private func detectAndOpen() async {
         let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
         if bridge != nil, found.id != descriptor.id {
             await swapDevice(to: found)
@@ -210,6 +234,41 @@ final class AppModel {
         await openCurrentDevice()
     }
 
+    /// Refresh the Connect surface's lists (known present devices + all USB serial
+    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
+    func refreshConnectLists() {
+        detectedDevices = DeviceRegistry.detectAll()
+        serialPorts = USBProbe.serialPorts()
+    }
+
+    /// The Connect surface's Rescan: refresh the lists and immediately re-run detection
+    /// (the same path as `connect()`), without waiting for the 1.5s monitor tick.
+    func rescan() async {
+        guard !swapping, !deviceBusy else { return }
+        refreshConnectLists()
+        await detectAndOpen()
+        refreshConnectLists()
+    }
+
+    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
+    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
+    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
+    func connectManual(port: String) async {
+        guard !swapping, !deviceBusy else { return }
+        let base = DeviceRegistry.chameleonUltra
+        let manual = DeviceDescriptor(
+            id: "chameleon-manual:\(port)",
+            family: base.family,
+            displayName: base.displayName,
+            daemonScript: base.daemonScript,
+            probeSubdir: base.probeSubdir,
+            usbMatch: base.usbMatch,
+            capabilities: base.capabilities,
+            portOverride: port)
+        await swapDevice(to: manual)
+        refreshConnectLists()
+    }
+
     /// Bring up the daemon for the active `descriptor`: read device info + key counts,
     /// then sample the reader. Shared by the first connect and a hot-swap, so both
     /// paths land the same state (info, capabilities via `info`, reader/card status).
@@ -264,7 +323,14 @@ final class AppModel {
             // With only the X7 involved, detect() keeps returning the same descriptor, so
             // this path is inert and the poll below is unchanged.
             if let found = DeviceRegistry.detect() {
-                if found.id != descriptor.id { await swapDevice(to: found); continue }
+                // A manually-pinned device (portOverride) is NOT auto-swapped away just
+                // because detect() returns the non-pinned auto variant with a different
+                // id - the user chose this port. If a pinned device truly disappears, the
+                // poll below sees reader:false and applyReaderGone reflects it; the user
+                // can then rescan. Auto descriptors swap on an id change exactly as before.
+                if descriptor.portOverride == nil, found.id != descriptor.id {
+                    await swapDevice(to: found); continue
+                }
             } else if emulating {
                 // The emulating device was unplugged with nothing to swap to: the card
                 // poll is skipped while emulating, so this is the only place that would
diff --git a/app/Sources/Brand/L10n.swift b/app/Sources/Brand/L10n.swift
index c4779f5..0ac5470 100644
--- a/app/Sources/Brand/L10n.swift
+++ b/app/Sources/Brand/L10n.swift
@@ -249,5 +249,21 @@ final class L10n {
         "import_failed":     ["vi": "không nhận dạng được tệp dump", "en": "unrecognised dump file", "zh": "无法识别的转储文件", "ja": "認識できないダンプファイルです"],
         "delete_card_q":     ["vi": "xóa thẻ đã lưu này?", "en": "delete this saved card?", "zh": "删除这张已保存的卡片？", "ja": "この保存済みカードを削除しますか？"],
         "delete_card_msg":   ["vi": "xóa khỏi thư viện và không lấy lại được. thẻ thật không bị ảnh hưởng.", "en": "removes it from the library and cannot be undone. the physical card is unaffected.", "zh": "将其从卡库中移除，不可撤销。实体卡片不受影响。", "ja": "ライブラリから削除します。元に戻せません。実体カードには影響しません。"],
+        // ---- Connect surface (USB now; Bluetooth is a later pass) ----
+        "connect":            ["vi": "kết nối", "en": "connect", "zh": "连接", "ja": "接続"],
+        "rescan":             ["vi": "quét lại", "en": "rescan", "zh": "重新扫描", "ja": "再スキャン"],
+        "connect_device":     ["vi": "kết nối thiết bị", "en": "connect device", "zh": "连接设备", "ja": "デバイスを接続"],
+        "manual_connect":     ["vi": "kết nối thủ công", "en": "manual connect", "zh": "手动连接", "ja": "手動接続"],
+        "serial_ports":       ["vi": "cổng serial", "en": "serial ports", "zh": "串口", "ja": "シリアルポート"],
+        "no_reader_detected": ["vi": "chưa nhận diện đầu đọc nào", "en": "no recognized reader", "zh": "没有可识别的读卡器", "ja": "認識できる読み取り機なし"],
+        "no_ports":           ["vi": "không thấy cổng serial nào", "en": "no serial ports", "zh": "没有串口", "ja": "シリアルポートなし"],
+        "likely_chameleon":   ["vi": "có thể là chameleon", "en": "likely chameleon", "zh": "可能是 chameleon", "ja": "chameleon の可能性"],
+        "auto_connect_note":  ["vi": "ứng dụng tự kết nối thiết bị nhận diện được", "en": "the app auto-connects a recognized device", "zh": "应用会自动连接已识别的设备", "ja": "認識したデバイスは自動で接続します"],
+        "enter_port":         ["vi": "nhập đường dẫn cổng", "en": "enter a port path", "zh": "输入端口路径", "ja": "ポートのパスを入力"],
+        "connect_action":     ["vi": "kết nối", "en": "connect", "zh": "连接", "ja": "接続"],
+        "no_device_hint":     ["vi": "chưa thấy thiết bị nào. kiểm tra cáp là loại truyền dữ liệu (không phải cáp chỉ sạc) và thử cổng usb khác.", "en": "no device detected - check the cable is a data cable, not charge-only, and try another usb port.", "zh": "未检测到设备 - 请确认使用的是数据线而非仅充电线，并换一个 usb 口试试。", "ja": "デバイスが見つかりません - ケーブルが充電専用ではなくデータ用か確認し、別の usb ポートを試してください。"],
+        "unrecognized_port_hint": ["vi": "tìm thấy cổng serial nhưng không phải đầu đọc nào được nhận diện. nếu đây là chameleon, hãy kết nối thủ công, hoặc kiểm tra bạn đang dùng cáp truyền dữ liệu.", "en": "found serial ports but none is a recognized reader - if this is your chameleon connect it manually, or check you are using a data cable.", "zh": "找到串口但没有可识别的读卡器 - 若这是你的 chameleon，请手动连接，或确认使用的是数据线。", "ja": "シリアルポートは見つかりましたが認識できる読み取り機がありません - これがお使いの chameleon なら手動で接続するか、データ用ケーブルか確認してください。"],
+        "bluetooth":          ["vi": "bluetooth", "en": "bluetooth", "zh": "蓝牙", "ja": "bluetooth"],
+        "bluetooth_later":    ["vi": "sẽ thêm ở bản cập nhật sau", "en": "added in a later update", "zh": "将在后续更新中加入", "ja": "今後のアップデートで追加"],
     ]
 }
diff --git a/app/Sources/Engine/DeviceBridge.swift b/app/Sources/Engine/DeviceBridge.swift
index 0201815..7161760 100644
--- a/app/Sources/Engine/DeviceBridge.swift
+++ b/app/Sources/Engine/DeviceBridge.swift
@@ -107,6 +107,16 @@ actor DeviceBridge {
         // the spawn under the GUI session.
         p.arguments = ["-B", script.path]
         p.currentDirectoryURL = workDir
+        // Pin the user-chosen serial port when this descriptor carries one (a manual
+        // Connect): the daemon honours CHAMELEON_PORT. We COPY the inherited launchd
+        // environment and add only that one key - never replace the child environment
+        // (a bare replacement broke the spawn under the GUI session), so libhidapi /
+        // PATH and the rest are preserved. Auto-detect descriptors leave it untouched.
+        if let port = descriptor.portOverride {
+            var env = ProcessInfo.processInfo.environment
+            env["CHAMELEON_PORT"] = port
+            p.environment = env
+        }
         let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
         p.standardInput = inPipe
         p.standardOutput = outPipe
diff --git a/app/Sources/Engine/DeviceRegistry.swift b/app/Sources/Engine/DeviceRegistry.swift
index 9694e0f..8b7d45a 100644
--- a/app/Sources/Engine/DeviceRegistry.swift
+++ b/app/Sources/Engine/DeviceRegistry.swift
@@ -16,6 +16,11 @@ struct DeviceDescriptor: Identifiable, Equatable, Sendable {
     let probeSubdir: String?        // relative subdir under the probe root, nil = root
     let usbMatch: USBMatch
     let capabilities: DeviceCapabilities
+    /// A serial port to pin explicitly (a manual Connect to a chosen /dev/cu.* path):
+    /// the daemon is spawned with CHAMELEON_PORT set to this. nil = auto-detect the
+    /// port as before. A distinct `id` per pinned port makes the swap logic treat it
+    /// as a different device and re-open the daemon.
+    var portOverride: String? = nil
 }
 
 /// How to recognise a device on the USB bus. `pid == nil` matches any product id
@@ -26,6 +31,12 @@ struct USBMatch: Equatable, Sendable {
     let vid: Int
     let pid: Int?
     let transport: Transport
+    /// Optional string matchers for a serial device whose vid can vary (a Chameleon
+    /// clone / re-enumeration). Matched against the USB ancestor's "USB Vendor Name" /
+    /// "USB Product Name", in ADDITION to the vid/pid check, so a genuine Chameleon is
+    /// still recognised when only its reported strings identify it.
+    var vendorName: String? = nil
+    var productContains: String? = nil
 }
 
 /// The device catalogue + USB detection. `detect()` returns the first present
@@ -43,7 +54,8 @@ enum DeviceRegistry {
     static let chameleonUltra = DeviceDescriptor(
         id: "chameleon-ultra", family: "chameleon-ultra", displayName: "Chameleon Ultra",
         daemonScript: "chameleon_d.py", probeSubdir: nil,
-        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial),
+        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial,
+                           vendorName: "Proxgrind", productContains: "chameleon"),
         capabilities: .chameleonUltra)
 
     /// A Chameleon sitting in the Nordic bootloader (re-enumerated to VID 0x1915). It
@@ -72,6 +84,34 @@ enum DeviceRegistry {
     static func detect() -> DeviceDescriptor? {
         all.first { USBProbe.isPresent($0.usbMatch) }
     }
+
+    /// Every present known device (not just the first), in the same priority order.
+    /// Feeds the Connect surface's detected-devices list.
+    static func detectAll() -> [DeviceDescriptor] {
+        all.filter { USBProbe.isPresent($0.usbMatch) }
+    }
+}
+
+/// One enumerated USB serial (CDC) port, for the Connect surface's manual-connect list.
+/// Carries the callout path plus the owning USB device's vid/pid/strings so the UI can
+/// hint which port is likely a Chameleon (and which is a device in DFU).
+struct SerialPortInfo: Identifiable, Equatable, Sendable {
+    let path: String
+    let vid: Int?
+    let pid: Int?
+    let vendorName: String?
+    let productName: String?
+    var id: String { path }
+    /// Likely a Chameleon: its vid, its reported vendor name, or a product name that
+    /// contains "chameleon" (case-insensitive), covering clones / re-enumerations.
+    var isChameleon: Bool {
+        vid == 0x6868
+            || vendorName?.caseInsensitiveCompare("Proxgrind") == .orderedSame
+            || (productName?.range(of: "chameleon", options: .caseInsensitive) != nil)
+    }
+    /// A device sitting in the Nordic bootloader (re-enumerated to VID 0x1915): shown
+    /// but not manually connectable (it has no serial command interface, only flash).
+    var isDFU: Bool { vid == 0x1915 }
 }
 
 /// Point-in-time USB presence checks over IOKit. HID devices (the X7) are found via
@@ -81,7 +121,7 @@ enum USBProbe {
     static func isPresent(_ m: USBMatch) -> Bool {
         switch m.transport {
         case .hid: return hidPresent(vid: m.vid, pid: m.pid)
-        case .serial: return serialPresent(vid: m.vid, pid: m.pid)
+        case .serial: return serialPresent(m)
         }
     }
 
@@ -94,7 +134,7 @@ enum USBProbe {
         return CFSetGetCount(devices) > 0
     }
 
-    private static func serialPresent(vid: Int, pid: Int?) -> Bool {
+    private static func serialPresent(_ m: USBMatch) -> Bool {
         guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return false }
         var iterator: io_iterator_t = 0
         guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
@@ -104,7 +144,7 @@ enum USBProbe {
         var found = false
         var service = IOIteratorNext(iterator)
         while service != 0 {
-            if usbAncestorMatches(service, vid: vid, pid: pid) { found = true }
+            if usbAncestorMatches(service, m) { found = true }
             IOObjectRelease(service)
             if found { break }
             service = IOIteratorNext(iterator)
@@ -113,16 +153,25 @@ enum USBProbe {
     }
 
     /// Walk up the IOService plane from a leaf (the serial client) to the USB device
-    /// node that carries `idVendor` / `idProduct`, matching there. Bounded so a
-    /// malformed registry can never loop.
-    private static func usbAncestorMatches(_ service: io_object_t, vid: Int, pid: Int?) -> Bool {
+    /// node that carries `idVendor` / `idProduct`, matching there. As well as the
+    /// vid/pid check, an ancestor whose "USB Vendor Name" equals the match's
+    /// `vendorName`, or whose "USB Product Name" contains `productContains`
+    /// (case-insensitive), also matches - so a Chameleon is recognised even when only
+    /// its reported strings identify it. Bounded so a malformed registry can never loop.
+    private static func usbAncestorMatches(_ service: io_object_t, _ m: USBMatch) -> Bool {
         var node = service
         IOObjectRetain(node)
         defer { IOObjectRelease(node) }
         for _ in 0..<10 {
-            if intProperty(node, "idVendor") == vid {
-                if pid == nil || intProperty(node, "idProduct") == pid { return true }
+            if intProperty(node, "idVendor") == m.vid {
+                if m.pid == nil || intProperty(node, "idProduct") == m.pid { return true }
             }
+            if let want = m.vendorName,
+               let got = stringProperty(node, "USB Vendor Name"),
+               got.caseInsensitiveCompare(want) == .orderedSame { return true }
+            if let want = m.productContains,
+               let got = stringProperty(node, "USB Product Name"),
+               got.range(of: want, options: .caseInsensitive) != nil { return true }
             var parent: io_registry_entry_t = 0
             let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
             guard kr == KERN_SUCCESS, parent != 0 else { return false }
@@ -132,10 +181,68 @@ enum USBProbe {
         return false
     }
 
+    /// Enumerate every USB serial (CDC) callout port for the Connect surface. Reads
+    /// each service's "IOCalloutDevice" as the path and walks to the owning USB device
+    /// for its vid/pid/vendor/product. Ports with NO USB ancestor carrying an idVendor
+    /// (pure-Bluetooth / virtual ports like /dev/cu.Bluetooth-Incoming-Port) are
+    /// skipped. Chameleon / DFU ports sort first, then by path.
+    static func serialPorts() -> [SerialPortInfo] {
+        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return [] }
+        var iterator: io_iterator_t = 0
+        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
+            return []
+        }
+        defer { IOObjectRelease(iterator) }
+        var ports: [SerialPortInfo] = []
+        var service = IOIteratorNext(iterator)
+        while service != 0 {
+            if let path = stringProperty(service, "IOCalloutDevice"),
+               let a = usbAncestorInfo(service), let vid = a.vid {
+                ports.append(SerialPortInfo(path: path, vid: vid, pid: a.pid,
+                                            vendorName: a.vendorName, productName: a.productName))
+            }
+            IOObjectRelease(service)
+            service = IOIteratorNext(iterator)
+        }
+        return ports.sorted { a, b in
+            let ra = a.isChameleon || a.isDFU, rb = b.isChameleon || b.isDFU
+            if ra != rb { return ra }
+            return a.path < b.path
+        }
+    }
+
+    /// vid/pid + vendor/product strings of the first USB ancestor carrying an idVendor,
+    /// or nil when the leaf has no USB device above it. Bounded 10-hop walk.
+    private static func usbAncestorInfo(_ service: io_object_t) -> (vid: Int?, pid: Int?, vendorName: String?, productName: String?)? {
+        var node = service
+        IOObjectRetain(node)
+        defer { IOObjectRelease(node) }
+        for _ in 0..<10 {
+            if let vid = intProperty(node, "idVendor") {
+                return (vid, intProperty(node, "idProduct"),
+                        stringProperty(node, "USB Vendor Name"),
+                        stringProperty(node, "USB Product Name"))
+            }
+            var parent: io_registry_entry_t = 0
+            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
+            guard kr == KERN_SUCCESS, parent != 0 else { return nil }
+            IOObjectRelease(node)
+            node = parent
+        }
+        return nil
+    }
+
     private static func intProperty(_ entry: io_registry_entry_t, _ key: String) -> Int? {
         guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
             return nil
         }
         return (cf.takeRetainedValue() as? NSNumber)?.intValue
     }
+
+    private static func stringProperty(_ entry: io_registry_entry_t, _ key: String) -> String? {
+        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
+            return nil
+        }
+        return cf.takeRetainedValue() as? String
+    }
 }
diff --git a/app/Sources/Shell/RootView.swift b/app/Sources/Shell/RootView.swift
index fa59433..520e3f9 100644
--- a/app/Sources/Shell/RootView.swift
+++ b/app/Sources/Shell/RootView.swift
@@ -165,10 +165,21 @@ private struct ReaderStatusInline: View {
     @Environment(Theme.self) private var theme
     @Environment(L10n.self) private var l
     var body: some View {
-        HStack(spacing: 6) {
-            Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary).frame(width: 6, height: 6)
-            Text(text).font(model.card?.uid != nil ? Typeface.mono(11) : l.sans(11))
-                .foregroundStyle(theme.p.textSecondary)
+        @Bindable var model = model
+        // The status pill is the Connect affordance: tapping it opens the device list /
+        // rescan / manual-connect popover. A chevron marks it as tappable.
+        Button { model.showConnect.toggle() } label: {
+            HStack(spacing: 6) {
+                Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary).frame(width: 6, height: 6)
+                Text(text).font(model.card?.uid != nil ? Typeface.mono(11) : l.sans(11))
+                    .foregroundStyle(theme.p.textSecondary)
+                Image(systemName: "chevron.down").font(.system(size: 8)).foregroundStyle(theme.p.textTertiary)
+            }
+            .contentShape(Rectangle())
+        }
+        .buttonStyle(.plain).help(l.t("connect"))
+        .popover(isPresented: $model.showConnect, arrowEdge: .bottom) {
+            ConnectView().environment(model).environment(theme).environment(l)
         }
     }
     private var text: String {
@@ -621,13 +632,23 @@ private struct EmptyState: View {
             }
             // A seated card can miss the snappy status poll (see AppModel.decode): give
             // an explicit "read anyway" that runs the op's own patient coupling, so a
-            // card physically on the reader is never a silent dead-end.
+            // card physically on the reader is never a silent dead-end. When the reader
+            // is offline, offer Connect (open the device list / rescan / manual-connect)
+            // plus an honest hint, so a plugged-in-but-undetected device has a way in.
             if model.readerOnline {
                 Button { Task { await model.decode() } } label: {
                     Text(l.t("read_anyway")).font(l.sans(11))
                 }
                 .buttonStyle(.plain).foregroundStyle(theme.p.accent)
                 .disabled(model.decoding)
+            } else {
+                Button { model.showConnect = true } label: {
+                    Text(l.t("connect_device")).font(l.sans(11))
+                }
+                .buttonStyle(.plain).foregroundStyle(theme.p.accent)
+                Text(l.t("no_device_hint")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
+                    .multilineTextAlignment(.center).frame(maxWidth: 320)
+                    .fixedSize(horizontal: false, vertical: true)
             }
             Spacer()
         }
diff --git a/probe/chameleon_d.py b/probe/chameleon_d.py
index 172a759..2874f80 100644
--- a/probe/chameleon_d.py
+++ b/probe/chameleon_d.py
@@ -525,6 +525,11 @@ class Daemon:
     def __init__(self, learned=None, port=None, cracker=crack):
         self.com = None
         self.cmd = None                  # the ChameleonCMD command layer (or a fake)
+        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
+        # environment so the app can pin a user-chosen serial port when it spawns us. A
+        # missing/empty env var leaves _port None (auto-discovery via _find_port()).
+        if port is None:
+            port = os.environ.get("CHAMELEON_PORT") or None
         self._port = port
         self._reader_mode = None         # cached: True once the device is in reader mode
         self.crack = cracker             # host-side crackers (injectable for tests)
@@ -550,14 +555,27 @@ class Daemon:
 
     # ---- connection --------------------------------------------------------
 
+    def _looks_like_chameleon(self, p):
+        """True if a serial port looks like a Chameleon in normal (CDC) mode. Mirrors the
+        official Chameleon GUI's matching so a genuine device is still found when the OS did
+        not surface its vid (or a Lite reports differently): match on vid, on the Proxgrind
+        manufacturer, or on 'chameleon' in the product/description. A port already in the
+        bootloader (DFU vid) is never a normal device, so it is excluded."""
+        if getattr(p, "vid", None) == DFU_VID:
+            return False
+        if getattr(p, "vid", None) == CHAMELEON_VID:
+            return True
+        if getattr(p, "manufacturer", None) == "Proxgrind":
+            return True
+        for text in (getattr(p, "product", None), getattr(p, "description", None)):
+            if text and "chameleon" in text.lower():
+                return True
+        return False
+
     def _find_port(self):
-        """First serial port whose USB vendor id is the Chameleon's, or None."""
-        try:
-            from serial.tools import list_ports
-        except ImportError:
-            return None
-        for p in list_ports.comports():
-            if getattr(p, "vid", None) == CHAMELEON_VID:
+        """First serial port that looks like a Chameleon in normal (CDC) mode, or None."""
+        for p in self._list_ports():
+            if self._looks_like_chameleon(p):
                 return p.device
         return None
 
diff --git a/probe/test_chameleon.py b/probe/test_chameleon.py
index d7183d3..8a034bf 100644
--- a/probe/test_chameleon.py
+++ b/probe/test_chameleon.py
@@ -1871,6 +1871,19 @@ class FakePort:
         self.device, self.vid, self.pid = device, vid, pid
 
 
+class FakePortInfo:
+    """A richer ListPortInfo stand-in that also carries the manufacturer / product /
+    description fields _find_port() matches on when the OS did not surface a vid."""
+    def __init__(self, device, vid=None, pid=None, manufacturer=None,
+                 product=None, description=None):
+        self.device = device
+        self.vid = vid
+        self.pid = pid
+        self.manufacturer = manufacturer
+        self.product = product
+        self.description = description
+
+
 class FakePopen:
     """Stands in for the adafruit-nrfutil subprocess: yields canned output lines then a
     chosen exit code, so the daemon's progress parse + non-zero handling are exercised.
@@ -2048,6 +2061,85 @@ def test_cham_dfu_port_discovery(check):
           "returned a pre-existing DFU port")
 
 
+# --------------------------------------------------------------------------
+# 27c. _find_port matching: vid, Proxgrind manufacturer, or 'chameleon' in the
+#      product/description (case-insensitive), and never a DFU (bootloader) port.
+# --------------------------------------------------------------------------
+def test_cham_find_port_matching(check):
+    d = _dfu_daemon(FakeChameleon())
+
+    # (a) vid match (the existing behavior) still works, unrelated ports are skipped.
+    d._list_ports = lambda: [
+        FakePortInfo("/dev/cu.usbserial", vid=0x0403, pid=0x6001),   # an unrelated FTDI
+        FakePortInfo("/dev/cu.usbmodem6868", vid=0x6868, pid=0x8686,
+                     manufacturer="Proxgrind", product="ChameleonUltra"),
+    ]
+    check("_find_port matches a Chameleon by vid",
+          d._find_port() == "/dev/cu.usbmodem6868", str(d._find_port()))
+
+    # (b) vid not surfaced by the OS -> match on the Proxgrind manufacturer.
+    d._list_ports = lambda: [
+        FakePortInfo("/dev/cu.usbserial", vid=0x0403, pid=0x6001),
+        FakePortInfo("/dev/cu.usbmodemX", vid=None, manufacturer="Proxgrind"),
+    ]
+    check("_find_port matches by manufacturer == Proxgrind when vid is None",
+          d._find_port() == "/dev/cu.usbmodemX", str(d._find_port()))
+
+    # (c) no vid, not Proxgrind -> match on 'chameleon' in product or description,
+    #     case-insensitively.
+    d._list_ports = lambda: [
+        FakePortInfo("/dev/cu.byProduct", vid=None, product="ChameleonUltra"),
+    ]
+    check("_find_port matches by 'chameleon' in product (case-insensitive)",
+          d._find_port() == "/dev/cu.byProduct", str(d._find_port()))
+    d._list_ports = lambda: [
+        FakePortInfo("/dev/cu.byDesc", vid=None, description="Some CHAMELEON Lite device"),
+    ]
+    check("_find_port matches by 'chameleon' in description (case-insensitive)",
+          d._find_port() == "/dev/cu.byDesc", str(d._find_port()))
+
+    # (d) a DFU (bootloader) port is never mis-picked: it is skipped even if only it
+    #     is present (-> None), and a real CDC port is preferred when both are present.
+    d._list_ports = lambda: [
+        FakePortInfo("/dev/cu.usbmodemDFU", vid=0x1915, pid=0x521f,
+                     product="ChameleonUltra"),
+    ]
+    check("_find_port skips a DFU port and returns None when only DFU is present",
+          d._find_port() is None, str(d._find_port()))
+    d._list_ports = lambda: [
+        FakePortInfo("/dev/cu.usbmodemDFU", vid=0x1915, pid=0x521f),
+        FakePortInfo("/dev/cu.usbmodem6868", vid=0x6868, pid=0x8686),
+    ]
+    check("_find_port skips a DFU port and returns the real CDC port",
+          d._find_port() == "/dev/cu.usbmodem6868", str(d._find_port()))
+
+
+# --------------------------------------------------------------------------
+# 27d. CHAMELEON_PORT env pins the port at construction; an explicit port= wins.
+# --------------------------------------------------------------------------
+def test_cham_env_port_pin(check):
+    saved = os.environ.get("CHAMELEON_PORT")
+    try:
+        os.environ["CHAMELEON_PORT"] = "/dev/cu.usbmodemPINNED"
+        check("__init__ honors CHAMELEON_PORT when no port arg is given",
+              chameleon_d.Daemon()._port == "/dev/cu.usbmodemPINNED",
+              str(chameleon_d.Daemon()._port))
+        check("an explicit port= overrides the CHAMELEON_PORT env",
+              chameleon_d.Daemon(port="/dev/cu.explicit")._port == "/dev/cu.explicit",
+              str(chameleon_d.Daemon(port="/dev/cu.explicit")._port))
+        os.environ["CHAMELEON_PORT"] = ""
+        check("an empty CHAMELEON_PORT leaves _port None (auto-discovery)",
+              chameleon_d.Daemon()._port is None, str(chameleon_d.Daemon()._port))
+        del os.environ["CHAMELEON_PORT"]
+        check("a missing CHAMELEON_PORT leaves _port None (auto-discovery)",
+              chameleon_d.Daemon()._port is None, str(chameleon_d.Daemon()._port))
+    finally:
+        if saved is None:
+            os.environ.pop("CHAMELEON_PORT", None)
+        else:
+            os.environ["CHAMELEON_PORT"] = saved
+
+
 # --------------------------------------------------------------------------
 # 28. enter-bootloader writes the EXACT 10-byte frame, DTR high, then closes.
 # --------------------------------------------------------------------------
@@ -2985,6 +3077,7 @@ TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
          test_cham_magic_write_midswap, test_cham_magic_write_trailer_keys,
          test_cham_magic_write_gen1a, test_cham_write_mfd, test_cham_write_mfd_guards,
          test_cham_dfu_asset, test_cham_dfu_norm_model, test_cham_dfu_port_discovery,
+         test_cham_find_port_matching, test_cham_env_port_pin,
          test_cham_dfu_enter_bootloader, test_cham_dfu_validate,
          test_cham_dfu_check, test_cham_dfu_flash_runner, test_cham_dfu_flasher_resolve,
          test_cham_dfu_flash_e2e,
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-wFzIBFXz' (errno=Operation not permitted)
2026-07-21 10:38:38.592 xcodebuild[41340:3238086]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:38:38.728 xcodebuild[41340:3238085] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-hmeRiEGA' (errno=Operation not permitted)
2026-07-21 10:38:39.179 xcodebuild[41343:3238102]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:38:39.315 xcodebuild[41343:3238101] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
 M app/Sources/AppModel.swift
 M app/Sources/Brand/L10n.swift
 M app/Sources/Engine/DeviceBridge.swift
 M app/Sources/Engine/DeviceRegistry.swift
 M app/Sources/Shell/RootView.swift
 M probe/chameleon_d.py
 M probe/test_chameleon.py
?? AUDIT_CHAMELEON.md
?? DEVICE_COMPAT.md
?? SPEC_CHAMELEON.md
?? X7_AUDIT_FINDINGS.md
?? app/Sources/Views/ConnectView.swift
?? exploration/
?? probe/native/darkside/
?? probe/x7_reader.py
import SwiftUI

/// The Connect surface, opened from the header status pill or the empty state. USB
/// only for now: it lists the known readers present on the bus, lets the user RESCAN,
/// and manually pin a serial port when auto-detect does not recognise the device (the
/// Chameleon-over-USB case). Bluetooth is a deliberately DISABLED placeholder for a
/// later pass. Instrument aesthetic: hairlines, muted tokens, mono for machine
/// identifiers (port paths), sans for chrome, signal via glyph + weight, no alarm colour.
struct ConnectView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @Environment(\.dismiss) private var dismiss
    @State private var manualPort = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            divider
            detectedSection
            divider
            manualSection
            if let hint = diagnosticHint {
                divider
                diagnostics(hint)
            }
            divider
            bluetoothRow
        }
        .frame(width: 340)
        .background(theme.p.panel)
        .onAppear { model.refreshConnectLists() }
    }

    // MARK: - header

    private var header: some View {
        HStack(spacing: 8) {
            Text(l.t("connect")).font(l.sans(13, .semibold)).foregroundStyle(theme.p.textPrimary)
            Spacer()
            if model.connecting { ProgressView().controlSize(.small) }
            Button { Task { await model.rescan() } } label: {
                HStack(spacing: 5) {
                    Image(systemName: "arrow.clockwise").font(.system(size: 10))
                    Text(l.t("rescan")).font(l.sans(11, .medium))
                }
                .padding(.horizontal, 9).frame(height: 26)
                .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
                .foregroundStyle(theme.p.textPrimary)
            }
            .buttonStyle(.plain).disabled(model.connecting).help(l.t("rescan"))
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
    }

    // MARK: - detected devices

    private var detectedSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel(l.t("device"))
            if model.detectedDevices.isEmpty {
                Text(l.t("no_reader_detected")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
            } else {
                ForEach(model.detectedDevices) { deviceRow($0) }
                Text(l.t("auto_connect_note")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func deviceRow(_ d: DeviceDescriptor) -> some View {
        HStack(spacing: 9) {
            Image(systemName: "cable.connector").font(.system(size: 12))
                .foregroundStyle(theme.p.textSecondary).frame(width: 16)
            Text(d.displayName).font(l.sans(12)).foregroundStyle(theme.p.textPrimary)
            if d.family == "chameleon-dfu" { tag("DFU") }
            Spacer()
            if isActive(d) { Circle().fill(theme.p.accent).frame(width: 6, height: 6) }
        }
    }

    private func isActive(_ d: DeviceDescriptor) -> Bool {
        d.family == model.activeDeviceFamily && (model.readerOnline || model.deviceInDFU)
    }

    // MARK: - manual connect (serial ports)

    private var manualSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel(l.t("serial_ports"))
            if model.serialPorts.isEmpty {
                Text(l.t("no_ports")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
            } else {
                ForEach(model.serialPorts) { portRow($0) }
            }
            HStack(spacing: 6) {
                TextField(l.t("enter_port"), text: $manualPort)
                    .textFieldStyle(.plain).font(Typeface.mono(11))
                    .foregroundStyle(theme.p.textPrimary)
                    .padding(.horizontal, 8).frame(height: 26)
                    .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
                    .onSubmit(submitManual)
                Button(l.t("connect_action"), action: submitManual)
                    .buttonStyle(.plain).font(l.sans(11, .medium))
                    .foregroundStyle(manualTrimmed.isEmpty ? theme.p.textTertiary : theme.p.accent)
                    .disabled(manualTrimmed.isEmpty)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func portRow(_ port: SerialPortInfo) -> some View {
        Button {
            guard !port.isDFU else { return }
            Task { await model.connectManual(port: port.path) }
            dismiss()
        } label: {
            HStack(spacing: 9) {
                Image(systemName: "cable.connector").font(.system(size: 12))
                    .foregroundStyle(theme.p.textSecondary).frame(width: 16)
                VStack(alignment: .leading, spacing: 2) {
                    Text(port.path).font(Typeface.mono(11)).foregroundStyle(theme.p.textPrimary).lineLimit(1)
                    if let sub = portSubtitle(port) {
                        Text(sub).font(l.sans(10)).foregroundStyle(theme.p.textTertiary).lineLimit(1)
                    }
                }
                Spacer()
                if port.isDFU {
                    tag("DFU")
                } else if port.isChameleon {
                    Text(l.t("likely_chameleon")).font(l.sans(9)).foregroundStyle(theme.p.accent)
                }
            }
            .padding(.horizontal, 10).frame(minHeight: 36)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(port.isDFU ? 0.3 : 0.6)))
            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(port.isDFU)
        .help(port.isDFU ? l.t("dfu_recover_hint") : l.t("manual_connect"))
    }

    private func portSubtitle(_ port: SerialPortInfo) -> String? {
        let parts = [port.vendorName, port.productName].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: - diagnostics + bluetooth placeholder

    private func diagnostics(_ hint: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: "info.circle").font(.system(size: 10)).foregroundStyle(theme.p.textTertiary)
            Text(hint).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
    }

    /// Honest guidance below the lists: recognisable readers absent but serial ports
    /// present -> connect one manually; nothing at all -> check the cable / usb port.
    private var diagnosticHint: String? {
        guard model.detectedDevices.isEmpty else { return nil }
        return model.serialPorts.isEmpty ? l.t("no_device_hint") : l.t("unrecognized_port_hint")
    }

    private var bluetoothRow: some View {
        HStack(spacing: 9) {
            Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 12))
                .foregroundStyle(theme.p.textTertiary).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(l.t("bluetooth")).font(l.sans(12)).foregroundStyle(theme.p.textTertiary)
                Text(l.t("bluetooth_later")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            }
            Spacer()
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .opacity(0.7)
    }

    // MARK: - shared

    private var divider: some View { Rectangle().fill(theme.p.hairline).frame(height: 1) }

    private func sectionLabel(_ text: String) -> some View {
        Text(text).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
    }

    private func tag(_ text: String) -> some View {
        Text(text).font(Typeface.mono(9)).foregroundStyle(theme.p.textSecondary)
            .padding(.horizontal, 5).padding(.vertical, 1)
            .background(RoundedRectangle(cornerRadius: 4).fill(theme.p.tileFill))
            .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
    }

    private var manualTrimmed: String { manualPort.trimmingCharacters(in: .whitespaces) }

    private func submitManual() {
        let p = manualTrimmed
        guard !p.isEmpty else { return }
        Task { await model.connectManual(port: p) }
        manualPort = ""
        dismiss()
    }
}

codex
The diff shows one area that needs especially careful tracing: manual pins are deliberately exempted from monitor swaps. I’m checking every exit path from that state, including unplug, X7 arrival, and Rescan, plus IOKit ownership at each ancestor hop.
exec
/bin/zsh -lc "nl -ba app/Sources/Engine/DeviceRegistry.swift | sed -n '1,320p'" in /Users/tuan/Claude/Tenor/tenor-rekey
exec
/bin/zsh -lc "nl -ba app/Sources/Engine/DeviceBridge.swift | sed -n '1,250p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import Foundation
     2	import IOKit
     3	import IOKit.hid
     4	import IOKit.serial
     5	
     6	/// Describes one device family the app can drive: which daemon speaks for it, where
     7	/// its script lives under the probe root, how to recognise its USB device, and the
     8	/// static capability baseline to assume before the daemon's own `info` manifest
     9	/// lands. The shell picks a descriptor by USB match, spawns its daemon, and gates
    10	/// UI on capabilities - it never hardcodes "if X7 / if Chameleon".
    11	struct DeviceDescriptor: Identifiable, Equatable, Sendable {
    12	    let id: String
    13	    let family: String
    14	    let displayName: String
    15	    let daemonScript: String        // e.g. "x7d.py" / "chameleon_d.py"
    16	    let probeSubdir: String?        // relative subdir under the probe root, nil = root
    17	    let usbMatch: USBMatch
    18	    let capabilities: DeviceCapabilities
    19	    /// A serial port to pin explicitly (a manual Connect to a chosen /dev/cu.* path):
    20	    /// the daemon is spawned with CHAMELEON_PORT set to this. nil = auto-detect the
    21	    /// port as before. A distinct `id` per pinned port makes the swap logic treat it
    22	    /// as a different device and re-open the daemon.
    23	    var portOverride: String? = nil
    24	}
    25	
    26	/// How to recognise a device on the USB bus. `pid == nil` matches any product id
    27	/// under the vendor (the Chameleon re-enumerates to a different pid in bootloader,
    28	/// so it is matched by vendor alone).
    29	struct USBMatch: Equatable, Sendable {
    30	    enum Transport: Sendable { case hid, serial }
    31	    let vid: Int
    32	    let pid: Int?
    33	    let transport: Transport
    34	    /// Optional string matchers for a serial device whose vid can vary (a Chameleon
    35	    /// clone / re-enumeration). Matched against the USB ancestor's "USB Vendor Name" /
    36	    /// "USB Product Name", in ADDITION to the vid/pid check, so a genuine Chameleon is
    37	    /// still recognised when only its reported strings identify it.
    38	    var vendorName: String? = nil
    39	    var productContains: String? = nil
    40	}
    41	
    42	/// The device catalogue + USB detection. `detect()` returns the first present
    43	/// device in priority order, or nil when none is plugged in.
    44	enum DeviceRegistry {
    45	    /// XIXEI X7 (HID PN533 reader) -> x7d.py.
    46	    static let x7 = DeviceDescriptor(
    47	        id: "x7", family: "x7", displayName: "XIXEI X7",
    48	        daemonScript: "x7d.py", probeSubdir: nil,
    49	        usbMatch: USBMatch(vid: 0x2518, pid: 0x6022, transport: .hid),
    50	        capabilities: .x7)
    51	
    52	    /// Chameleon Ultra / Lite (USB-CDC serial) -> chameleon_d.py. Matched by vendor
    53	    /// id only; the daemon's `info` reports Ultra vs Lite and the real capabilities.
    54	    static let chameleonUltra = DeviceDescriptor(
    55	        id: "chameleon-ultra", family: "chameleon-ultra", displayName: "Chameleon Ultra",
    56	        daemonScript: "chameleon_d.py", probeSubdir: nil,
    57	        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial,
    58	                           vendorName: "Proxgrind", productContains: "chameleon"),
    59	        capabilities: .chameleonUltra)
    60	
    61	    /// A Chameleon sitting in the Nordic bootloader (re-enumerated to VID 0x1915). It
    62	    /// has no command interface (the daemon's `info` cannot query it), but recognising it
    63	    /// as a Chameleon-in-DFU keeps the firmware/flash action reachable so a stuck or
    64	    /// manually-B-buttoned device can be flash-recovered after relaunch, instead of
    65	    /// silently launching into the X7 fallback with DFU hidden. Same daemon (chameleon_d.py).
    66	    static let chameleonDFU = DeviceDescriptor(
    67	        id: "chameleon-dfu", family: "chameleon-dfu", displayName: "Chameleon (DFU)",
    68	        daemonScript: "chameleon_d.py", probeSubdir: nil,
    69	        usbMatch: USBMatch(vid: 0x1915, pid: 0x521f, transport: .serial),
    70	        capabilities: .chameleonDFU)
    71	
    72	    /// Every family the app can drive, in match priority order (X7 first, so a
    73	    /// machine with both plugged in keeps driving the X7 until the user unplugs it; the
    74	    /// DFU match is last, only relevant when a Chameleon is stuck in the bootloader).
    75	    static let all: [DeviceDescriptor] = [x7, chameleonUltra, chameleonDFU]
    76	
    77	    /// The descriptor to use when nothing is detected: the X7, so a bare machine
    78	    /// starts the X7 daemon and shows "reader offline" exactly as the single-device
    79	    /// build did.
    80	    static let fallback = x7
    81	
    82	    /// The first present device by USB match, or nil when none is connected. Cheap
    83	    /// enough to run on the status poll (a bounded IORegistry scan, no I/O).
    84	    static func detect() -> DeviceDescriptor? {
    85	        all.first { USBProbe.isPresent($0.usbMatch) }
    86	    }
    87	
    88	    /// Every present known device (not just the first), in the same priority order.
    89	    /// Feeds the Connect surface's detected-devices list.
    90	    static func detectAll() -> [DeviceDescriptor] {
    91	        all.filter { USBProbe.isPresent($0.usbMatch) }
    92	    }
    93	}
    94	
    95	/// One enumerated USB serial (CDC) port, for the Connect surface's manual-connect list.
    96	/// Carries the callout path plus the owning USB device's vid/pid/strings so the UI can
    97	/// hint which port is likely a Chameleon (and which is a device in DFU).
    98	struct SerialPortInfo: Identifiable, Equatable, Sendable {
    99	    let path: String
   100	    let vid: Int?
   101	    let pid: Int?
   102	    let vendorName: String?
   103	    let productName: String?
   104	    var id: String { path }
   105	    /// Likely a Chameleon: its vid, its reported vendor name, or a product name that
   106	    /// contains "chameleon" (case-insensitive), covering clones / re-enumerations.
   107	    var isChameleon: Bool {
   108	        vid == 0x6868
   109	            || vendorName?.caseInsensitiveCompare("Proxgrind") == .orderedSame
   110	            || (productName?.range(of: "chameleon", options: .caseInsensitive) != nil)
   111	    }
   112	    /// A device sitting in the Nordic bootloader (re-enumerated to VID 0x1915): shown
   113	    /// but not manually connectable (it has no serial command interface, only flash).
   114	    var isDFU: Bool { vid == 0x1915 }
   115	}
   116	
   117	/// Point-in-time USB presence checks over IOKit. HID devices (the X7) are found via
   118	/// IOHIDManager; CDC serial devices (the Chameleon) via the serial BSD service,
   119	/// walking up to the owning USB device node to read its vendor / product id.
   120	enum USBProbe {
   121	    static func isPresent(_ m: USBMatch) -> Bool {
   122	        switch m.transport {
   123	        case .hid: return hidPresent(vid: m.vid, pid: m.pid)
   124	        case .serial: return serialPresent(m)
   125	        }
   126	    }
   127	
   128	    private static func hidPresent(vid: Int, pid: Int?) -> Bool {
   129	        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
   130	        var match: [String: Any] = [kIOHIDVendorIDKey as String: vid]
   131	        if let pid { match[kIOHIDProductIDKey as String] = pid }
   132	        IOHIDManagerSetDeviceMatching(manager, match as CFDictionary)
   133	        guard let devices = IOHIDManagerCopyDevices(manager) else { return false }
   134	        return CFSetGetCount(devices) > 0
   135	    }
   136	
   137	    private static func serialPresent(_ m: USBMatch) -> Bool {
   138	        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return false }
   139	        var iterator: io_iterator_t = 0
   140	        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
   141	            return false
   142	        }
   143	        defer { IOObjectRelease(iterator) }
   144	        var found = false
   145	        var service = IOIteratorNext(iterator)
   146	        while service != 0 {
   147	            if usbAncestorMatches(service, m) { found = true }
   148	            IOObjectRelease(service)
   149	            if found { break }
   150	            service = IOIteratorNext(iterator)
   151	        }
   152	        return found
   153	    }
   154	
   155	    /// Walk up the IOService plane from a leaf (the serial client) to the USB device
   156	    /// node that carries `idVendor` / `idProduct`, matching there. As well as the
   157	    /// vid/pid check, an ancestor whose "USB Vendor Name" equals the match's
   158	    /// `vendorName`, or whose "USB Product Name" contains `productContains`
   159	    /// (case-insensitive), also matches - so a Chameleon is recognised even when only
   160	    /// its reported strings identify it. Bounded so a malformed registry can never loop.
   161	    private static func usbAncestorMatches(_ service: io_object_t, _ m: USBMatch) -> Bool {
   162	        var node = service
   163	        IOObjectRetain(node)
   164	        defer { IOObjectRelease(node) }
   165	        for _ in 0..<10 {
   166	            if intProperty(node, "idVendor") == m.vid {
   167	                if m.pid == nil || intProperty(node, "idProduct") == m.pid { return true }
   168	            }
   169	            if let want = m.vendorName,
   170	               let got = stringProperty(node, "USB Vendor Name"),
   171	               got.caseInsensitiveCompare(want) == .orderedSame { return true }
   172	            if let want = m.productContains,
   173	               let got = stringProperty(node, "USB Product Name"),
   174	               got.range(of: want, options: .caseInsensitive) != nil { return true }
   175	            var parent: io_registry_entry_t = 0
   176	            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
   177	            guard kr == KERN_SUCCESS, parent != 0 else { return false }
   178	            IOObjectRelease(node)
   179	            node = parent
   180	        }
   181	        return false
   182	    }
   183	
   184	    /// Enumerate every USB serial (CDC) callout port for the Connect surface. Reads
   185	    /// each service's "IOCalloutDevice" as the path and walks to the owning USB device
   186	    /// for its vid/pid/vendor/product. Ports with NO USB ancestor carrying an idVendor
   187	    /// (pure-Bluetooth / virtual ports like /dev/cu.Bluetooth-Incoming-Port) are
   188	    /// skipped. Chameleon / DFU ports sort first, then by path.
   189	    static func serialPorts() -> [SerialPortInfo] {
   190	        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return [] }
   191	        var iterator: io_iterator_t = 0
   192	        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
   193	            return []
   194	        }
   195	        defer { IOObjectRelease(iterator) }
   196	        var ports: [SerialPortInfo] = []
   197	        var service = IOIteratorNext(iterator)
   198	        while service != 0 {
   199	            if let path = stringProperty(service, "IOCalloutDevice"),
   200	               let a = usbAncestorInfo(service), let vid = a.vid {
   201	                ports.append(SerialPortInfo(path: path, vid: vid, pid: a.pid,
   202	                                            vendorName: a.vendorName, productName: a.productName))
   203	            }
   204	            IOObjectRelease(service)
   205	            service = IOIteratorNext(iterator)
   206	        }
   207	        return ports.sorted { a, b in
   208	            let ra = a.isChameleon || a.isDFU, rb = b.isChameleon || b.isDFU
   209	            if ra != rb { return ra }
   210	            return a.path < b.path
   211	        }
   212	    }
   213	
   214	    /// vid/pid + vendor/product strings of the first USB ancestor carrying an idVendor,
   215	    /// or nil when the leaf has no USB device above it. Bounded 10-hop walk.
   216	    private static func usbAncestorInfo(_ service: io_object_t) -> (vid: Int?, pid: Int?, vendorName: String?, productName: String?)? {
   217	        var node = service
   218	        IOObjectRetain(node)
   219	        defer { IOObjectRelease(node) }
   220	        for _ in 0..<10 {
   221	            if let vid = intProperty(node, "idVendor") {
   222	                return (vid, intProperty(node, "idProduct"),
   223	                        stringProperty(node, "USB Vendor Name"),
   224	                        stringProperty(node, "USB Product Name"))
   225	            }
   226	            var parent: io_registry_entry_t = 0
   227	            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
   228	            guard kr == KERN_SUCCESS, parent != 0 else { return nil }
   229	            IOObjectRelease(node)
   230	            node = parent
   231	        }
   232	        return nil
   233	    }
   234	
   235	    private static func intProperty(_ entry: io_registry_entry_t, _ key: String) -> Int? {
   236	        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
   237	            return nil
   238	        }
   239	        return (cf.takeRetainedValue() as? NSNumber)?.intValue
   240	    }
   241	
   242	    private static func stringProperty(_ entry: io_registry_entry_t, _ key: String) -> String? {
   243	        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
   244	            return nil
   245	        }
   246	        return cf.takeRetainedValue() as? String
   247	    }
   248	}

 succeeded in 0ms:
     1	import Foundation
     2	
     3	/// Talks to a verified Python daemon (probe/<daemonScript>) over newline-delimited
     4	/// JSON on a child-process pipe. The actor owns the process, correlates each request
     5	/// id with a continuation, and routes progress events separately. It is device
     6	/// neutral: the daemon to spawn, and the probe subdir it lives in, come from a
     7	/// `DeviceDescriptor`, so the same transport drives the X7 (x7d.py) and the
     8	/// Chameleon (chameleon_d.py) - both speak the identical contract.
     9	///
    10	/// Architecture (2026-06-19): A-first hybrid. This bridge is deliberately thin and
    11	/// the daemon contract is narrow so the engine can later be replaced by a native
    12	/// Swift + vendored-C implementation without touching the UI.
    13	actor DeviceBridge {
    14	    enum EngineError: Error, CustomStringConvertible {
    15	        case daemon(String)
    16	        case badResponse
    17	        var description: String {
    18	            switch self {
    19	            case .daemon(let m): return m
    20	            case .badResponse: return "bad daemon response"
    21	            }
    22	        }
    23	    }
    24	
    25	    /// The device this bridge drives. Immutable + Sendable, so callers on other
    26	    /// actors can read it without hopping onto this actor (used to tell whether a
    27	    /// live bridge already matches the detected device before spawning a new one).
    28	    nonisolated let descriptor: DeviceDescriptor
    29	
    30	    private let python: URL
    31	    private let workDir: URL
    32	    private let script: URL
    33	    private var process: Process?
    34	    private var stdin: FileHandle?
    35	    private var outReader: FileHandle?
    36	    private var errReader: FileHandle?
    37	    private var nextID = 1
    38	    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    39	    private var buffer = Data()
    40	    /// Set for the duration of a streaming op (only one runs at a time, the UI
    41	    /// disables other actions); id-less progress events are routed here.
    42	    private var eventSink: (@Sendable (EngineEvent) -> Void)?
    43	    /// Bumped each time a streaming op (decode / write) starts. A cancel's hard-kill
    44	    /// fallback captures the generation of the op it is cancelling and terminates only
    45	    /// if THAT same op is still in flight when the grace window elapses, so it can never
    46	    /// kill a later, unrelated op that reused the shared daemon.
    47	    private var opGeneration = 0
    48	
    49	    init(descriptor: DeviceDescriptor) {
    50	        self.descriptor = descriptor
    51	        let p = Self.resolvePaths(for: descriptor)
    52	        self.python = p.python
    53	        self.workDir = p.workDir
    54	        self.script = p.script
    55	    }
    56	
    57	    /// Resolve the python interpreter + daemon script, preferring the copies
    58	    /// vendored inside the packaged .app (Contents/Resources/python + /probe), then
    59	    /// environment overrides (X7_PYTHON / X7_PROBE_DIR), then the dev checkout. Both
    60	    /// a shipped app and a dev build work with no configuration. The working dir is
    61	    /// always the probe root so the daemons' shared imports (x7lib, learned_keys,
    62	    /// the vendored chameleon package) resolve regardless of which script is spawned.
    63	    /// libhidapi is found by x7hid itself (a bundle-relative candidate inside the
    64	    /// .app, brew outside), so we never touch the child's environment here.
    65	    static func resolvePaths(for descriptor: DeviceDescriptor) -> (python: URL, workDir: URL, script: URL) {
    66	        let fm = FileManager.default
    67	        let res = Bundle.main.resourceURL ?? Bundle.main.bundleURL
    68	        let bundledPython = res.appendingPathComponent("python/bin/python3")
    69	        let bundledProbe = res.appendingPathComponent("probe")
    70	        let bundledScript = scriptURL(probeRoot: bundledProbe, descriptor: descriptor)
    71	        if fm.fileExists(atPath: bundledPython.path),
    72	           fm.fileExists(atPath: bundledScript.path) {
    73	            return (bundledPython, bundledProbe, bundledScript)
    74	        }
    75	        let env = ProcessInfo.processInfo.environment
    76	        let python = URL(fileURLWithPath: env["X7_PYTHON"] ?? "/usr/bin/python3")
    77	        let probe = URL(fileURLWithPath: env["X7_PROBE_DIR"] ?? Self.devProbeDir)
    78	        return (python, probe, scriptURL(probeRoot: probe, descriptor: descriptor))
    79	    }
    80	
    81	    /// Last-resort dev-checkout probe dir, computed from THIS source file's location at
    82	    /// compile time (.../app/Sources/Engine/DeviceBridge.swift -> repo root -> /probe) so
    83	    /// a from-source run still finds probe/ without baking in an absolute home path. A
    84	    /// shipped app never reaches this (the bundled Resources copy wins first), nor does a
    85	    /// run that sets X7_PROBE_DIR.
    86	    private static var devProbeDir: String {
    87	        URL(fileURLWithPath: #filePath)   // .../app/Sources/Engine/DeviceBridge.swift
    88	            .deletingLastPathComponent()  // Engine
    89	            .deletingLastPathComponent()  // Sources
    90	            .deletingLastPathComponent()  // app
    91	            .deletingLastPathComponent()  // repo root
    92	            .appendingPathComponent("probe").path
    93	    }
    94	
    95	    private static func scriptURL(probeRoot: URL, descriptor: DeviceDescriptor) -> URL {
    96	        let dir = descriptor.probeSubdir.map { probeRoot.appendingPathComponent($0) } ?? probeRoot
    97	        return dir.appendingPathComponent(descriptor.daemonScript)
    98	    }
    99	
   100	    private func startIfNeeded() throws {
   101	        guard process == nil else { return }
   102	        let p = Process()
   103	        p.executableURL = python
   104	        // -B: never write .pyc into the bundle (a code-signed .app that mutates
   105	        // itself breaks its own seal). Passed as a flag, not an env var, so we
   106	        // leave the inherited launchd environment untouched - replacing it broke
   107	        // the spawn under the GUI session.
   108	        p.arguments = ["-B", script.path]
   109	        p.currentDirectoryURL = workDir
   110	        // Pin the user-chosen serial port when this descriptor carries one (a manual
   111	        // Connect): the daemon honours CHAMELEON_PORT. We COPY the inherited launchd
   112	        // environment and add only that one key - never replace the child environment
   113	        // (a bare replacement broke the spawn under the GUI session), so libhidapi /
   114	        // PATH and the rest are preserved. Auto-detect descriptors leave it untouched.
   115	        if let port = descriptor.portOverride {
   116	            var env = ProcessInfo.processInfo.environment
   117	            env["CHAMELEON_PORT"] = port
   118	            p.environment = env
   119	        }
   120	        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
   121	        p.standardInput = inPipe
   122	        p.standardOutput = outPipe
   123	        p.standardError = errPipe
   124	        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
   125	            let d = h.availableData
   126	            guard !d.isEmpty, let self else { return }
   127	            Task { await self.ingest(d) }
   128	        }
   129	        // Drain the daemon's stderr. Without this the OS pipe buffer (~64KB) fills
   130	        // the first time the engine prints a traceback or a flood of warnings, and
   131	        // the daemon then BLOCKS on its next stderr write, hanging every request.
   132	        // We forward it to the app's own stderr so it is visible in Console / a
   133	        // terminal launch for diagnosis.
   134	        errPipe.fileHandleForReading.readabilityHandler = { h in
   135	            let d = h.availableData
   136	            if !d.isEmpty { FileHandle.standardError.write(d) }
   137	        }
   138	        p.terminationHandler = { [weak self] _ in
   139	            guard let self else { return }
   140	            Task { await self.died() }
   141	        }
   142	        try p.run()
   143	        process = p
   144	        stdin = inPipe.fileHandleForWriting
   145	        outReader = outPipe.fileHandleForReading
   146	        errReader = errPipe.fileHandleForReading
   147	    }
   148	
   149	    private func died() {
   150	        outReader?.readabilityHandler = nil
   151	        errReader?.readabilityHandler = nil
   152	        outReader = nil
   153	        errReader = nil
   154	        process = nil
   155	        stdin = nil
   156	        for (_, c) in pending { c.resume(throwing: EngineError.daemon("daemon exited")) }
   157	        pending.removeAll()
   158	        buffer.removeAll()
   159	    }
   160	
   161	    private func ingest(_ d: Data) {
   162	        buffer.append(d)
   163	        while let nl = buffer.firstIndex(of: 0x0A) {
   164	            let line = Data(buffer[buffer.startIndex..<nl])
   165	            buffer.removeSubrange(buffer.startIndex...nl)
   166	            route(line)
   167	        }
   168	    }
   169	
   170	    private func route(_ line: Data) {
   171	        guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { return }
   172	        if obj["event"] != nil {
   173	            if let ev = try? JSONDecoder().decode(EngineEvent.self, from: line) { eventSink?(ev) }
   174	            return
   175	        }
   176	        guard let id = obj["id"] as? Int else {
   177	            // An id-less line (the daemon's bad-json reply): if exactly one request
   178	            // is outstanding, fail it rather than orphan its continuation.
   179	            if let err = obj["error"] as? String, pending.count == 1,
   180	               let only = pending.keys.first, let c = pending.removeValue(forKey: only) {
   181	                c.resume(throwing: EngineError.daemon(err))
   182	            }
   183	            return
   184	        }
   185	        guard let c = pending.removeValue(forKey: id) else { return }
   186	        c.resume(returning: line)
   187	    }
   188	
   189	    private func transact<T: Decodable>(id: Int, _ reqData: Data, timeout: Duration?, as _: T.Type) async throws -> T {
   190	        let line: Data = try await withCheckedThrowingContinuation { cont in
   191	            pending[id] = cont
   192	            try? stdin?.write(contentsOf: reqData)
   193	            try? stdin?.write(contentsOf: Data([0x0A]))
   194	            // Arm a deadline: a daemon that is alive but WEDGED (stuck on a hardware
   195	            // read that never returns) would otherwise orphan this continuation
   196	            // forever - freezing the live-status poll and every later op. On the
   197	            // deadline we fail this request and kill the daemon so it respawns.
   198	            //
   199	            // A nil timeout means NO deadline: this is used ONLY for the firmware flash,
   200	            // where a timeout-driven resolve-failed + terminate() would be a mid-write kill
   201	            // (brick). The daemon holds the flash uninterruptible on its side, so the flash
   202	            // request is allowed to take as long as it takes and resolves only on the real
   203	            // result (or a genuine daemon exit).
   204	            if let timeout {
   205	                Task { [weak self] in
   206	                    try? await Task.sleep(for: timeout)
   207	                    await self?.timeoutRequest(id: id)
   208	                }
   209	            }
   210	        }
   211	        let env = try JSONDecoder().decode(Envelope<T>.self, from: line)
   212	        if let e = env.error { throw EngineError.daemon(e) }
   213	        guard let r = env.result else { throw EngineError.badResponse }
   214	        return r
   215	    }
   216	
   217	    /// Fail a still-pending request whose deadline passed and terminate the wedged
   218	    /// daemon (the next request respawns it). A no-op if the response already
   219	    /// arrived - route() removed it from pending first, so there is no double-resume.
   220	    private func timeoutRequest(id: Int) {
   221	        guard let c = pending.removeValue(forKey: id) else { return }
   222	        c.resume(throwing: EngineError.daemon("daemon timed out"))
   223	        process?.terminate()
   224	    }
   225	
   226	    private func request<T: Decodable>(_ method: String, timeout: Duration? = .seconds(30), as t: T.Type) async throws -> T {
   227	        try startIfNeeded()
   228	        let id = nextID; nextID += 1
   229	        return try await transact(id: id, JSONEncoder().encode(Req(id: id, method: method)), timeout: timeout, as: t)
   230	    }
   231	
   232	    private func request<P: Encodable, T: Decodable>(_ method: String, params: P, timeout: Duration? = .seconds(30), as t: T.Type) async throws -> T {
   233	        try startIfNeeded()
   234	        let id = nextID; nextID += 1
   235	        return try await transact(id: id, JSONEncoder().encode(ReqP(id: id, method: method, params: params)), timeout: timeout, as: t)
   236	    }
   237	
   238	    func info() async throws -> DeviceInfo { try await request("info", as: DeviceInfo.self) }
   239	    /// Poll for a card. `tries` bounds the coupling-retry count: the live status
   240	    /// monitor passes a small value to stay snappy; a decode wants the default.
   241	    func poll(tries: Int? = nil) async throws -> PollResult {
   242	        if let tries {
   243	            return try await request("poll", params: PollParams(tries: tries), as: PollResult.self)
   244	        }
   245	        return try await request("poll", as: PollResult.self)
   246	    }
   247	    /// Decode. `userKeys` are the user's editable keys, tried FIRST; the daemon
   248	    /// appends its large built-in curated dictionary. Empty -> built-in only.
   249	    /// `onProgress` receives the per-sector / per-key-walk progress events.
   250	    func decode(userKeys: [String] = [],

exec
/bin/zsh -lc "nl -ba app/Sources/Views/ConnectView.swift | sed -n '1,280p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import SwiftUI
     2	
     3	/// The Connect surface, opened from the header status pill or the empty state. USB
     4	/// only for now: it lists the known readers present on the bus, lets the user RESCAN,
     5	/// and manually pin a serial port when auto-detect does not recognise the device (the
     6	/// Chameleon-over-USB case). Bluetooth is a deliberately DISABLED placeholder for a
     7	/// later pass. Instrument aesthetic: hairlines, muted tokens, mono for machine
     8	/// identifiers (port paths), sans for chrome, signal via glyph + weight, no alarm colour.
     9	struct ConnectView: View {
    10	    @Environment(AppModel.self) private var model
    11	    @Environment(Theme.self) private var theme
    12	    @Environment(L10n.self) private var l
    13	    @Environment(\.dismiss) private var dismiss
    14	    @State private var manualPort = ""
    15	
    16	    var body: some View {
    17	        VStack(alignment: .leading, spacing: 0) {
    18	            header
    19	            divider
    20	            detectedSection
    21	            divider
    22	            manualSection
    23	            if let hint = diagnosticHint {
    24	                divider
    25	                diagnostics(hint)
    26	            }
    27	            divider
    28	            bluetoothRow
    29	        }
    30	        .frame(width: 340)
    31	        .background(theme.p.panel)
    32	        .onAppear { model.refreshConnectLists() }
    33	    }
    34	
    35	    // MARK: - header
    36	
    37	    private var header: some View {
    38	        HStack(spacing: 8) {
    39	            Text(l.t("connect")).font(l.sans(13, .semibold)).foregroundStyle(theme.p.textPrimary)
    40	            Spacer()
    41	            if model.connecting { ProgressView().controlSize(.small) }
    42	            Button { Task { await model.rescan() } } label: {
    43	                HStack(spacing: 5) {
    44	                    Image(systemName: "arrow.clockwise").font(.system(size: 10))
    45	                    Text(l.t("rescan")).font(l.sans(11, .medium))
    46	                }
    47	                .padding(.horizontal, 9).frame(height: 26)
    48	                .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
    49	                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
    50	                .foregroundStyle(theme.p.textPrimary)
    51	            }
    52	            .buttonStyle(.plain).disabled(model.connecting).help(l.t("rescan"))
    53	        }
    54	        .padding(.horizontal, 16).padding(.vertical, 12)
    55	    }
    56	
    57	    // MARK: - detected devices
    58	
    59	    private var detectedSection: some View {
    60	        VStack(alignment: .leading, spacing: 8) {
    61	            sectionLabel(l.t("device"))
    62	            if model.detectedDevices.isEmpty {
    63	                Text(l.t("no_reader_detected")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
    64	            } else {
    65	                ForEach(model.detectedDevices) { deviceRow($0) }
    66	                Text(l.t("auto_connect_note")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
    67	                    .fixedSize(horizontal: false, vertical: true)
    68	            }
    69	        }
    70	        .padding(.horizontal, 16).padding(.vertical, 12)
    71	        .frame(maxWidth: .infinity, alignment: .leading)
    72	    }
    73	
    74	    private func deviceRow(_ d: DeviceDescriptor) -> some View {
    75	        HStack(spacing: 9) {
    76	            Image(systemName: "cable.connector").font(.system(size: 12))
    77	                .foregroundStyle(theme.p.textSecondary).frame(width: 16)
    78	            Text(d.displayName).font(l.sans(12)).foregroundStyle(theme.p.textPrimary)
    79	            if d.family == "chameleon-dfu" { tag("DFU") }
    80	            Spacer()
    81	            if isActive(d) { Circle().fill(theme.p.accent).frame(width: 6, height: 6) }
    82	        }
    83	    }
    84	
    85	    private func isActive(_ d: DeviceDescriptor) -> Bool {
    86	        d.family == model.activeDeviceFamily && (model.readerOnline || model.deviceInDFU)
    87	    }
    88	
    89	    // MARK: - manual connect (serial ports)
    90	
    91	    private var manualSection: some View {
    92	        VStack(alignment: .leading, spacing: 8) {
    93	            sectionLabel(l.t("serial_ports"))
    94	            if model.serialPorts.isEmpty {
    95	                Text(l.t("no_ports")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
    96	            } else {
    97	                ForEach(model.serialPorts) { portRow($0) }
    98	            }
    99	            HStack(spacing: 6) {
   100	                TextField(l.t("enter_port"), text: $manualPort)
   101	                    .textFieldStyle(.plain).font(Typeface.mono(11))
   102	                    .foregroundStyle(theme.p.textPrimary)
   103	                    .padding(.horizontal, 8).frame(height: 26)
   104	                    .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
   105	                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
   106	                    .onSubmit(submitManual)
   107	                Button(l.t("connect_action"), action: submitManual)
   108	                    .buttonStyle(.plain).font(l.sans(11, .medium))
   109	                    .foregroundStyle(manualTrimmed.isEmpty ? theme.p.textTertiary : theme.p.accent)
   110	                    .disabled(manualTrimmed.isEmpty)
   111	            }
   112	        }
   113	        .padding(.horizontal, 16).padding(.vertical, 12)
   114	        .frame(maxWidth: .infinity, alignment: .leading)
   115	    }
   116	
   117	    private func portRow(_ port: SerialPortInfo) -> some View {
   118	        Button {
   119	            guard !port.isDFU else { return }
   120	            Task { await model.connectManual(port: port.path) }
   121	            dismiss()
   122	        } label: {
   123	            HStack(spacing: 9) {
   124	                Image(systemName: "cable.connector").font(.system(size: 12))
   125	                    .foregroundStyle(theme.p.textSecondary).frame(width: 16)
   126	                VStack(alignment: .leading, spacing: 2) {
   127	                    Text(port.path).font(Typeface.mono(11)).foregroundStyle(theme.p.textPrimary).lineLimit(1)
   128	                    if let sub = portSubtitle(port) {
   129	                        Text(sub).font(l.sans(10)).foregroundStyle(theme.p.textTertiary).lineLimit(1)
   130	                    }
   131	                }
   132	                Spacer()
   133	                if port.isDFU {
   134	                    tag("DFU")
   135	                } else if port.isChameleon {
   136	                    Text(l.t("likely_chameleon")).font(l.sans(9)).foregroundStyle(theme.p.accent)
   137	                }
   138	            }
   139	            .padding(.horizontal, 10).frame(minHeight: 36)
   140	            .frame(maxWidth: .infinity, alignment: .leading)
   141	            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(port.isDFU ? 0.3 : 0.6)))
   142	            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
   143	            .contentShape(Rectangle())
   144	        }
   145	        .buttonStyle(.plain).disabled(port.isDFU)
   146	        .help(port.isDFU ? l.t("dfu_recover_hint") : l.t("manual_connect"))
   147	    }
   148	
   149	    private func portSubtitle(_ port: SerialPortInfo) -> String? {
   150	        let parts = [port.vendorName, port.productName].compactMap { $0 }.filter { !$0.isEmpty }
   151	        return parts.isEmpty ? nil : parts.joined(separator: " · ")
   152	    }
   153	
   154	    // MARK: - diagnostics + bluetooth placeholder
   155	
   156	    private func diagnostics(_ hint: String) -> some View {
   157	        HStack(spacing: 7) {
   158	            Image(systemName: "info.circle").font(.system(size: 10)).foregroundStyle(theme.p.textTertiary)
   159	            Text(hint).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
   160	                .fixedSize(horizontal: false, vertical: true)
   161	            Spacer(minLength: 0)
   162	        }
   163	        .padding(.horizontal, 16).padding(.vertical, 10)
   164	    }
   165	
   166	    /// Honest guidance below the lists: recognisable readers absent but serial ports
   167	    /// present -> connect one manually; nothing at all -> check the cable / usb port.
   168	    private var diagnosticHint: String? {
   169	        guard model.detectedDevices.isEmpty else { return nil }
   170	        return model.serialPorts.isEmpty ? l.t("no_device_hint") : l.t("unrecognized_port_hint")
   171	    }
   172	
   173	    private var bluetoothRow: some View {
   174	        HStack(spacing: 9) {
   175	            Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 12))
   176	                .foregroundStyle(theme.p.textTertiary).frame(width: 16)
   177	            VStack(alignment: .leading, spacing: 2) {
   178	                Text(l.t("bluetooth")).font(l.sans(12)).foregroundStyle(theme.p.textTertiary)
   179	                Text(l.t("bluetooth_later")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
   180	            }
   181	            Spacer()
   182	        }
   183	        .padding(.horizontal, 16).padding(.vertical, 12)
   184	        .opacity(0.7)
   185	    }
   186	
   187	    // MARK: - shared
   188	
   189	    private var divider: some View { Rectangle().fill(theme.p.hairline).frame(height: 1) }
   190	
   191	    private func sectionLabel(_ text: String) -> some View {
   192	        Text(text).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
   193	    }
   194	
   195	    private func tag(_ text: String) -> some View {
   196	        Text(text).font(Typeface.mono(9)).foregroundStyle(theme.p.textSecondary)
   197	            .padding(.horizontal, 5).padding(.vertical, 1)
   198	            .background(RoundedRectangle(cornerRadius: 4).fill(theme.p.tileFill))
   199	            .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
   200	    }
   201	
   202	    private var manualTrimmed: String { manualPort.trimmingCharacters(in: .whitespaces) }
   203	
   204	    private func submitManual() {
   205	        let p = manualTrimmed
   206	        guard !p.isEmpty else { return }
   207	        Task { await model.connectManual(port: p) }
   208	        manualPort = ""
   209	        dismiss()
   210	    }
   211	}

exec
/bin/zsh -lc "nl -ba app/Sources/AppModel.swift | sed -n '1,430p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import SwiftUI
     2	import Observation
     3	import AppKit
     4	
     5	/// Observable app state. The decoded / loaded image is the DOCUMENT; the card on the
     6	/// reader is a separate live device state. Heavy work stays on the device bridge
     7	/// actor; this holds @MainActor UI state only.
     8	@MainActor
     9	@Observable
    10	final class AppModel {
    11	    var readerOnline = false
    12	    var info: DeviceInfo?
    13	    var card: PollResult?
    14	    var sectors: [SectorVM] = []
    15	    /// The live NTAG / Ultralight read (page rows + the metadata to re-emulate it). Nil
    16	    /// when there is no NTAG dump on the canvas. `pages` reads its page rows so the page
    17	    /// view and the load-to-slot metadata are one source of truth and never drift.
    18	    var ntagDoc: NtagDocument?
    19	    var pages: [NtagPage] { ntagDoc?.pages ?? [] }   // NTAG / Ultralight page dump (SAK 0x00)
    20	    var selected: Int?                  // selected sector index
    21	    var selectedBlock: Int?             // selected absolute block, for the quick-look
    22	    var decoding = false
    23	    var decodeProgress: DecodeProgress?      // live sector / key-walk progress
    24	    /// When the running decode started, for an honest elapsed-time readout (the walk
    25	    /// has a fixed wall-clock budget, so elapsed seconds is bounded, forward-moving
    26	    /// feedback where the raw auth counter has no meaningful denominator).
    27	    var decodeStart: Date?
    28	    /// A decode finished but recovered NO key: the card's keys are not in the
    29	    /// dictionary. Shown as an honest "no keys" result instead of a fake empty grid.
    30	    var noKeysFound = false
    31	    private var decodeCancelled = false
    32	    var lastError: String?
    33	    var inspectorOpen = true
    34	
    35	    // ---- Connect surface (USB now; Bluetooth is a later pass) --------------
    36	    /// The Connect popover (device list + rescan + manual serial connect) is open.
    37	    var showConnect = false
    38	    /// Known readers currently present on the USB bus (the detected-devices list).
    39	    var detectedDevices: [DeviceDescriptor] = []
    40	    /// Every enumerated USB serial port (the manual-connect list).
    41	    var serialPorts: [SerialPortInfo] = []
    42	
    43	    /// The working DOCUMENT: the image produced by a decode or loaded from a file.
    44	    /// It is what the canvas shows, what Save writes out, and what Write clones onto
    45	    /// the card on the reader. It is independent of whichever card is currently on
    46	    /// the reader and deliberately persists across card swaps, so decoding a source
    47	    /// card and writing it onto a blank needs no save/open dance and never visually
    48	    /// vanishes when the source card is lifted.
    49	    var source: CardDump?
    50	    var cloneSheet = false
    51	    var cloning = false
    52	    /// Per-block write outcome from the last/in-flight clone (block -> ok). Tied to a
    53	    /// specific target card, so it resets when the card on the reader changes.
    54	    var cloneResults: [Int: Bool] = [:]
    55	    /// Per-block reason a write was refused (block -> daemon reason), so a failed clone
    56	    /// can be summarised in card terms (sector + cause) instead of raw block numbers.
    57	    var cloneFailReasons: [Int: String] = [:]
    58	    var formatConfirm = false
    59	    /// The uid snapshot taken when the format confirmation is PRESENTED, so the write is
    60	    /// pinned to the card the user actually authorized (the monitor can swap `card`
    61	    /// while the dialog is open). Shown in the dialog and re-checked before erasing.
    62	    var pendingFormatUID: String?
    63	    var formatting = false
    64	
    65	    /// apdu console.
    66	    var apduOpen = false
    67	    var apduLog: [ApduEntry] = []
    68	    var apduBusy = false
    69	
    70	    // ---- Chameleon slot library + emulation (gated on capabilities) --------
    71	    /// The 8-slot library, loaded when the slot view opens (empty for a plain reader).
    72	    var slots: [ChameleonSlot] = []
    73	    /// The slot highlighted in the library (its actions apply to it) - not the ACTIVE
    74	    /// slot the device presents.
    75	    var selectedSlot: Int?
    76	    /// A slot op (select / type / enable / rename / save / load / open / emulate toggle)
    77	    /// owns the reader. Folded into `deviceBusy` so no other op races it.
    78	    var slotBusy = false
    79	    /// The Chameleon-only slot library is showing instead of the document canvas.
    80	    var showSlots = false
    81	
    82	    // ---- LF 125 kHz (Chameleon-only, gated on capabilities.lf) -------------
    83	    /// The LF panel (read / T5577 write / EM410x emulate) is showing instead of the
    84	    /// document canvas. A Chameleon-only detail area, so it drops on a device swap.
    85	    var showLF = false
    86	    /// The last LF read (em410x / hidprox tag on the reader), shown in the LF panel.
    87	    var lfScanResult: LfScanResult?
    88	    /// The last LF T5577 write outcome (wrote + verified), shown in the LF panel.
    89	    var lfWriteResult: LfWriteResult?
    90	    /// An LF op (read / write / emulate) owns the reader. Folded into `deviceBusy`.
    91	    var lfBusy = false
    92	
    93	    // ---- saved-cards library (device-agnostic) -----------------------------
    94	    /// The persistent library of saved card dumps, refreshed when the library view opens
    95	    /// and after any save / import / rename / delete.
    96	    var savedCards: [SavedCard] = []
    97	    /// The library entry highlighted in the view (its actions apply to it).
    98	    var selectedSavedCard: String?
    99	    /// The saved-cards library is showing instead of the document canvas. Unlike the slot
   100	    /// library it is device-agnostic, so it persists across a device swap.
   101	    var showLibrary = false
   102	    /// The device is in tag/emulate mode (presenting the active slot), not reader mode.
   103	    /// While true the status monitor stops polling, since a poll would switch the device
   104	    /// back to reader mode under the emulation.
   105	    var emulating = false
   106	
   107	    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
   108	    /// The firmware update sheet is open.
   109	    var flashingSheet = false
   110	    /// The current firmware + latest release, loaded when the sheet opens.
   111	    var dfuStatus: DfuStatus?
   112	    /// A firmware flash is in flight (owns the device; folded into `deviceBusy`, so the
   113	    /// status monitor pauses while the device reboots into and out of the bootloader).
   114	    var flashing = false
   115	    /// The flash phase (download / enter / flash / done) + percent, for the progress UI.
   116	    var flashStage: String?
   117	    var flashPercent: Int?
   118	    /// A finished-successfully flag so the sheet can show a done state without a lingering error.
   119	    var flashDone = false
   120	    /// A flash failure, shown INSIDE the flashing sheet (not only the root banner behind the
   121	    /// modal) with the retry-now recovery path, since a failed flash usually leaves the
   122	    /// device in the bootloader.
   123	    var flashError: String?
   124	
   125	    /// The active device bridge, chosen by the registry at connect and swapped on
   126	    /// hot-plug. Created lazily for `descriptor` (the daemon itself starts on its
   127	    /// first request), so there is no bridge before the first connect.
   128	    private var bridge: DeviceBridge?
   129	    /// The descriptor of the device the bridge currently drives. X7 by default, so a
   130	    /// bare machine (or one where detection has not run yet) behaves exactly as the
   131	    /// single-device build did; a detected Chameleon swaps it.
   132	    private var descriptor: DeviceDescriptor = DeviceRegistry.fallback
   133	    /// A device swap (or reconnect that changes the device) is tearing down the old
   134	    /// daemon and bringing up the new one.
   135	    ///
   136	    /// INVARIANT: no device op (decode / clone / format / apdu) or reconnect may run
   137	    /// while `swapping` is true, and the old bridge is unreachable the instant it is
   138	    /// set (the swap detaches `bridge` synchronously before its first await). This is
   139	    /// what makes a hot-swap atomic from the UI's point of view: an op started during
   140	    /// the teardown await cannot grab the just-terminated bridge or the stale card.
   141	    private var swapping = false
   142	
   143	    /// A device op already owns the reader. Reconnect / swap must not replace the
   144	    /// bridge under one, and a second op must not start while one runs. Slot ops are
   145	    /// included so a slot edit and a decode / clone can never overlap on the reader.
   146	    private var deviceBusy: Bool { decoding || cloning || formatting || apduBusy || slotBusy || flashing || lfBusy }
   147	
   148	    /// The active bridge, created lazily for the current descriptor. A prior bridge
   149	    /// for a different device is torn down explicitly on the swap path (which nils
   150	    /// `bridge` before the descriptor changes), so this only ever creates the bridge
   151	    /// that matches - it never silently orphans a running daemon.
   152	    private func activeBridge() -> DeviceBridge {
   153	        if let b = bridge, b.descriptor.id == descriptor.id { return b }
   154	        let b = DeviceBridge(descriptor: descriptor)
   155	        bridge = b
   156	        return b
   157	    }
   158	
   159	    /// The connected device's capability manifest, read by the shell to gate
   160	    /// device-specific UI. Prefers the daemon's declared manifest; falls back to the
   161	    /// active descriptor's static defaults before `info` lands or when a daemon
   162	    /// predates the manifest.
   163	    var capabilities: DeviceCapabilities { info?.capabilities ?? descriptor.capabilities }
   164	
   165	    /// True when the connected device is a Chameleon sitting in the Nordic bootloader:
   166	    /// it has no command interface (so `readerOnline` is false and card ops are off), but
   167	    /// the firmware flash can still recover it, so the firmware action stays reachable.
   168	    var deviceInDFU: Bool { descriptor.family == "chameleon-dfu" }
   169	
   170	    /// The family of the device currently driven, so the Connect surface can mark the
   171	    /// active row. A manual pin keeps its base family (chameleon-ultra), so matching a
   172	    /// detected row by family lights the right one in every case.
   173	    var activeDeviceFamily: String { descriptor.family }
   174	
   175	    /// A device swap / (re)connect is in flight, exposed read-only so the Connect
   176	    /// surface can show a spinner and disable Rescan while it runs.
   177	    var connecting: Bool { swapping }
   178	
   179	    /// The user's editable keys (Settings > Dictionaries), tried before the
   180	    /// daemon's large built-in dictionary.
   181	    let keyStore = KeyStore()
   182	    /// The persistent saved-cards library (device-agnostic; works for X7 dumps too).
   183	    let savedCardStore = SavedCardStore.standard()
   184	    /// Size of the daemon's built-in curated dictionary (shown in Settings).
   185	    var builtinKeyCount = 0
   186	    /// Keys the daemon has learned from real cards and reranks decodes with (Settings).
   187	    var learnedKeyCount = 0
   188	
   189	    var selectedSector: SectorVM? {
   190	        guard let s = selected else { return nil }
   191	        return sectors.first { $0.index == s }
   192	    }
   193	
   194	    /// What "write" clones onto the card on the reader: the working document. It has
   195	    /// no dependency on a card being present, so the write action is available as soon
   196	    /// as there is something to write; the target card is asked for at write time.
   197	    var cloneSource: CardDump? { source }
   198	
   199	    /// Format erases the card on the reader, so it is only offered when the card
   200	    /// present is the one this document was decoded from (same uid): only then do we
   201	    /// hold its recovered keys to auth it, and only then is wiping it unambiguous.
   202	    var canFormat: Bool {
   203	        guard let c = card?.uid, let d = source?.uid else { return false }
   204	        return Self.normUID(c) == Self.normUID(d)
   205	    }
   206	
   207	    static func normUID(_ s: String) -> String {
   208	        s.replacingOccurrences(of: " ", with: "").lowercased()
   209	    }
   210	
   211	    /// Detect the connected device, then start its daemon + read device info and look
   212	    /// for a card (connect at launch, not lazily). With no device detected we fall
   213	    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
   214	    ///
   215	    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
   216	    /// reconnect can never replace the bridge under a running decode / clone. When the
   217	    /// detected device differs from the current one it routes through `swapDevice` so
   218	    /// the old daemon is torn down (never silently orphaned) under the swap guard.
   219	    func connect() async {
   220	        guard !swapping, !deviceBusy else { return }
   221	        await detectAndOpen()
   222	    }
   223	
   224	    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
   225	    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
   226	    /// is torn down, and bring the current one up.
   227	    private func detectAndOpen() async {
   228	        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
   229	        if bridge != nil, found.id != descriptor.id {
   230	            await swapDevice(to: found)
   231	            return
   232	        }
   233	        descriptor = found
   234	        await openCurrentDevice()
   235	    }
   236	
   237	    /// Refresh the Connect surface's lists (known present devices + all USB serial
   238	    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
   239	    func refreshConnectLists() {
   240	        detectedDevices = DeviceRegistry.detectAll()
   241	        serialPorts = USBProbe.serialPorts()
   242	    }
   243	
   244	    /// The Connect surface's Rescan: refresh the lists and immediately re-run detection
   245	    /// (the same path as `connect()`), without waiting for the 1.5s monitor tick.
   246	    func rescan() async {
   247	        guard !swapping, !deviceBusy else { return }
   248	        refreshConnectLists()
   249	        await detectAndOpen()
   250	        refreshConnectLists()
   251	    }
   252	
   253	    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
   254	    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
   255	    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
   256	    func connectManual(port: String) async {
   257	        guard !swapping, !deviceBusy else { return }
   258	        let base = DeviceRegistry.chameleonUltra
   259	        let manual = DeviceDescriptor(
   260	            id: "chameleon-manual:\(port)",
   261	            family: base.family,
   262	            displayName: base.displayName,
   263	            daemonScript: base.daemonScript,
   264	            probeSubdir: base.probeSubdir,
   265	            usbMatch: base.usbMatch,
   266	            capabilities: base.capabilities,
   267	            portOverride: port)
   268	        await swapDevice(to: manual)
   269	        refreshConnectLists()
   270	    }
   271	
   272	    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
   273	    /// then sample the reader. Shared by the first connect and a hot-swap, so both
   274	    /// paths land the same state (info, capabilities via `info`, reader/card status).
   275	    private func openCurrentDevice() async {
   276	        // A device in the bootloader has no command interface to query: present a reachable
   277	        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
   278	        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
   279	        if deviceInDFU {
   280	            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
   281	            info = nil
   282	            readerOnline = false
   283	            dfuStatus = nil
   284	            lastError = nil
   285	            return
   286	        }
   287	        let b = activeBridge()
   288	        do {
   289	            info = try await b.info()
   290	            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
   291	            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
   292	            readerOnline = true
   293	            lastError = nil
   294	            await refreshStatus()
   295	        } catch {
   296	            applyReaderGone()
   297	            lastError = "\(error)"
   298	        }
   299	    }
   300	
   301	    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
   302	    /// as decodes recover keys).
   303	    func refreshLearnedCount() async {
   304	        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
   305	    }
   306	
   307	    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
   308	    func clearLearnedKeys() async {
   309	        try? await activeBridge().clearLearnedKeys()
   310	        await refreshLearnedCount()
   311	    }
   312	
   313	    /// Live status: keep the reader / card pill honest when the X7 or a card is
   314	    /// plugged or removed with no user action. Runs until the view's task is
   315	    /// cancelled. Skips polling during an operation that already owns the reader.
   316	    func monitor() async {
   317	        while !Task.isCancelled {
   318	            try? await Task.sleep(for: .seconds(1.5))
   319	            if deviceBusy || swapping { continue }
   320	            // Hot-swap detection runs even while emulating: unplugging an emulating
   321	            // Chameleon and attaching another device must still tear down + swap. It is a
   322	            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
   323	            // With only the X7 involved, detect() keeps returning the same descriptor, so
   324	            // this path is inert and the poll below is unchanged.
   325	            if let found = DeviceRegistry.detect() {
   326	                // A manually-pinned device (portOverride) is NOT auto-swapped away just
   327	                // because detect() returns the non-pinned auto variant with a different
   328	                // id - the user chose this port. If a pinned device truly disappears, the
   329	                // poll below sees reader:false and applyReaderGone reflects it; the user
   330	                // can then rescan. Auto descriptors swap on an id change exactly as before.
   331	                if descriptor.portOverride == nil, found.id != descriptor.id {
   332	                    await swapDevice(to: found); continue
   333	                }
   334	            } else if emulating {
   335	                // The emulating device was unplugged with nothing to swap to: the card
   336	                // poll is skipped while emulating, so this is the only place that would
   337	                // notice it is gone. Reflect it (which also clears the emulate state).
   338	                applyReaderGone()
   339	                continue
   340	            }
   341	            // Only the card POLL is skipped while emulating: a poll forces reader mode,
   342	            // which would break the emulation.
   343	            if emulating { continue }
   344	            await refreshStatus()
   345	        }
   346	    }
   347	
   348	    /// Replace the active device with a freshly detected one. Every synchronous state
   349	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   350	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   351	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   352	    /// already nil. Only reader-bound state is cleared; the writable document is
   353	    /// device-independent and is deliberately kept across the swap.
   354	    private func swapDevice(to found: DeviceDescriptor) async {
   355	        guard !swapping else { return }
   356	        swapping = true
   357	        defer { swapping = false }              // released however this returns
   358	        let old = bridge
   359	        bridge = nil                            // detach: no path can obtain the old bridge now
   360	        descriptor = found
   361	        withAnimation(.easeInOut(duration: 0.3)) {
   362	            readerOnline = false
   363	            info = nil
   364	            card = nil
   365	            clearCardBound()
   366	            resetChameleonState()
   367	        }
   368	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   369	        await openCurrentDevice()               // creates + brings up the new bridge
   370	    }
   371	
   372	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   373	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   374	    /// and the slot view / emulate toggle must not persist across a device that has none.
   375	    private func resetChameleonState() {
   376	        slots = []
   377	        selectedSlot = nil
   378	        showSlots = false
   379	        showLF = false
   380	        lfScanResult = nil
   381	        lfWriteResult = nil
   382	        emulating = false
   383	        dfuStatus = nil
   384	        // A failed flash usually leaves the device in the bootloader, which triggers a
   385	        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
   386	        // still open, KEEP the flash outcome (error / done + progress) so its banner and
   387	        // recovery text do not vanish under the user; clearFlashState() clears them when
   388	        // the sheet is dismissed.
   389	        if !flashingSheet {
   390	            flashStage = nil
   391	            flashPercent = nil
   392	            flashDone = false
   393	            flashError = nil
   394	        }
   395	    }
   396	
   397	    /// Clear the flash outcome + progress. Called when the flashing sheet is dismissed, so a
   398	    /// stale error / done state never carries into the next time it is opened.
   399	    func clearFlashState() {
   400	        flashStage = nil
   401	        flashPercent = nil
   402	        flashDone = false
   403	        flashError = nil
   404	    }
   405	
   406	    /// Consecutive polls that saw the reader but no card; a seated card that blips
   407	    /// for one cycle should not drop its decoded grid, so we debounce a removal.
   408	    private var cardAbsentStreak = 0
   409	
   410	    /// One status sample: detects reader unplug (drops to offline + clears), reader
   411	    /// replug (back online + refetch device info), and card placed / removed.
   412	    private func refreshStatus() async {
   413	        do {
   414	            let p = try await activeBridge().poll(tries: 8)
   415	            if p.reader == false {           // reader unplugged: reflect it at once
   416	                applyReaderGone()
   417	                return
   418	            }
   419	            readerOnline = true
   420	            if info == nil { info = try? await activeBridge().info() }   // refetch until it lands
   421	            // NOTE: do not clear lastError here - the 1.5s poll would wipe a clone /
   422	            // decode / format error banner before the user could read it. Operations
   423	            // clear it when they start; the banner also has a dismiss button.
   424	            if p.present {
   425	                cardAbsentStreak = 0
   426	                // A different card (or first placement): the DOCUMENT stays (it is the
   427	                // working image, not bound to this card); only the previous write's
   428	                // per-block glyphs reset so they never show on the new card.
   429	                if card == nil || p.uid != card?.uid {
   430	                    withAnimation(.easeInOut(duration: 0.3)) { clearCardBound(); card = p }

exec
/bin/zsh -lc "nl -ba app/Sources/Shell/RootView.swift | sed -n '1,260p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import SwiftUI
     2	import AppKit
     3	import UniformTypeIdentifiers
     4	
     5	/// One unified workspace: the decoded / loaded image is the DOCUMENT on the canvas,
     6	/// the card on the reader is a separate live device; reading / writing / format /
     7	/// save / open are LABELLED actions on a always-visible action bar (so the
     8	/// workflow is discoverable, not hidden behind cryptic toolbar icons). The
     9	/// titlebar is hidden; a custom header carries the brand wordmark + reader
    10	/// status cleanly (no system toolbar wells). Theme + language switch instantly.
    11	struct RootView: View {
    12	    @Environment(AppModel.self) private var model
    13	    @Environment(Theme.self) private var theme
    14	    @Environment(L10n.self) private var l10n
    15	    @Environment(\.colorScheme) private var systemScheme
    16	
    17	    var body: some View {
    18	        @Bindable var model = model
    19	        Workspace()
    20	            .background(WindowConfigurator())
    21	            .preferredColorScheme(theme.appearance == .system ? nil : theme.scheme)
    22	            .onAppear {
    23	                theme.systemScheme = systemScheme
    24	                l10n.systemCode = Locale.current.language.languageCode?.identifier ?? "en"
    25	                // Refuse app quit while a firmware flash is writing (a mid-write kill can
    26	                // brick the device); warn and keep the update running.
    27	                AppDelegate.terminationGuard = {
    28	                    guard model.flashing else { return .terminateNow }
    29	                    let alert = NSAlert()
    30	                    alert.messageText = l10n.t("quit_while_flashing_title")
    31	                    alert.informativeText = l10n.t("quit_while_flashing_msg")
    32	                    alert.addButton(withTitle: l10n.t("keep_updating"))
    33	                    alert.runModal()
    34	                    return .terminateCancel
    35	                }
    36	            }
    37	            .onChange(of: systemScheme) { _, s in
    38	                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
    39	            }
    40	            .sheet(isPresented: $model.cloneSheet) {
    41	                CloneSheet().environment(model).environment(theme).environment(l10n)
    42	            }
    43	            .sheet(isPresented: $model.flashingSheet, onDismiss: { model.clearFlashState() }) {
    44	                FlashingView().environment(model).environment(theme).environment(l10n)
    45	            }
    46	            .confirmationDialog(l10n.t("format_q"), isPresented: $model.formatConfirm, titleVisibility: .visible) {
    47	                // Pinned to the uid snapshot taken when the dialog opened, so a card
    48	                // swapped in while it is open is never the one wiped.
    49	                Button(l10n.t("format"), role: .destructive) {
    50	                    Task { await model.format(authorizedUID: model.pendingFormatUID) }
    51	                }
    52	                Button(l10n.t("cancel"), role: .cancel) {}
    53	            } message: {
    54	                Text(l10n.t("format_msg") + (model.pendingFormatUID.map { "\n\n\(l10n.t("card_on_reader")): \($0)" } ?? ""))
    55	            }
    56	            .task { await model.connect(); await model.monitor() }
    57	    }
    58	}
    59	
    60	private struct Workspace: View {
    61	    @Environment(AppModel.self) private var model
    62	    @Environment(Theme.self) private var theme
    63	
    64	    var body: some View {
    65	        VStack(spacing: 0) {
    66	            HeaderBar()
    67	            Rectangle().fill(theme.p.hairline).frame(height: 1)
    68	            ActionBar()
    69	            Rectangle().fill(theme.p.hairline).frame(height: 1)
    70	            ErrorBanner()
    71	            HStack(spacing: 0) {
    72	                VStack(spacing: 0) {
    73	                    CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
    74	                    if model.apduOpen {
    75	                        Rectangle().fill(theme.p.hairline).frame(height: 1)
    76	                        ApduConsole()
    77	                    }
    78	                }
    79	                .frame(maxWidth: .infinity, maxHeight: .infinity)
    80	                if model.inspectorOpen {
    81	                    Rectangle().fill(theme.p.hairline).frame(width: 1)
    82	                    SectorInspector().frame(width: 300)
    83	                }
    84	            }
    85	        }
    86	        .background(theme.p.canvas)
    87	        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
    88	            guard let provider = providers.first else { return false }
    89	            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
    90	                let url = (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) } ?? (item as? URL)
    91	                guard let url else { return }
    92	                Task { @MainActor in model.loadDump(from: url) }
    93	            }
    94	            return true
    95	        }
    96	    }
    97	}
    98	
    99	/// A dismissible status line for the last operation error (a clone that hit the
   100	/// wrong card, a write that failed, a decode that was interrupted). Without it those
   101	/// failures were silent - the model recorded them but nothing was ever shown. Glyph +
   102	/// typography carry the signal (instrument discipline: no alarm colour).
   103	private struct ErrorBanner: View {
   104	    @Environment(AppModel.self) private var model
   105	    @Environment(Theme.self) private var theme
   106	    @Environment(L10n.self) private var l
   107	    var body: some View {
   108	        if let err = model.lastError {
   109	            HStack(spacing: 8) {
   110	                Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
   111	                    .foregroundStyle(theme.p.textPrimary)
   112	                Text(err).font(l.sans(11)).foregroundStyle(theme.p.textPrimary).lineLimit(2)
   113	                Spacer()
   114	                Button { withAnimation(.easeInOut(duration: 0.2)) { model.lastError = nil } } label: {
   115	                    Image(systemName: "xmark").font(.system(size: 9))
   116	                }.buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
   117	            }
   118	            .padding(.horizontal, 16).padding(.vertical, 9)
   119	            .background(theme.p.tileFill)
   120	            Rectangle().fill(theme.p.hairline).frame(height: 1)
   121	        }
   122	    }
   123	}
   124	
   125	// MARK: - Header (brand + status + utilities), in content so we control the look
   126	
   127	private struct HeaderBar: View {
   128	    @Environment(AppModel.self) private var model
   129	    @Environment(Theme.self) private var theme
   130	    @Environment(L10n.self) private var l
   131	    var body: some View {
   132	        // The row sits BELOW the traffic-light band (top padding clears the
   133	        // lights) so the wordmark left-aligns with the action bar margin instead
   134	        // of being indented beside the lights.
   135	        HStack(spacing: 12) {
   136	            Lockup(focal: "rekey", size: 15)
   137	            Spacer()
   138	            ReaderStatusInline()
   139	            Divider().frame(height: 16)
   140	            Menu {
   141	                ForEach(AppLang.allCases) { lang in
   142	                    Button(lang == .system ? l.systemDisplay() : lang.display) { l.lang = lang }
   143	                }
   144	            } label: { Image(systemName: "globe") }
   145	                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().help(l.t("language"))
   146	            iconButton("sun.max", symbol: theme.toggleSymbol, help: l.t("light_dark")) { theme.toggle() }
   147	            iconButton("sidebar.right", help: l.t("inspector")) { model.inspectorOpen.toggle() }
   148	        }
   149	        .font(l.sans(12))
   150	        .foregroundStyle(theme.p.textSecondary)
   151	        .padding(.leading, 16)
   152	        .padding(.trailing, 14)
   153	        .padding(.top, 30)
   154	        .padding(.bottom, 12)
   155	        .background(theme.p.panel)
   156	    }
   157	    private func iconButton(_ name: String, symbol: String? = nil, help: String, _ action: @escaping () -> Void) -> some View {
   158	        Button(action: action) { Image(systemName: symbol ?? name) }
   159	            .buttonStyle(.plain).foregroundStyle(theme.p.textSecondary).help(help)
   160	    }
   161	}
   162	
   163	private struct ReaderStatusInline: View {
   164	    @Environment(AppModel.self) private var model
   165	    @Environment(Theme.self) private var theme
   166	    @Environment(L10n.self) private var l
   167	    var body: some View {
   168	        @Bindable var model = model
   169	        // The status pill is the Connect affordance: tapping it opens the device list /
   170	        // rescan / manual-connect popover. A chevron marks it as tappable.
   171	        Button { model.showConnect.toggle() } label: {
   172	            HStack(spacing: 6) {
   173	                Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary).frame(width: 6, height: 6)
   174	                Text(text).font(model.card?.uid != nil ? Typeface.mono(11) : l.sans(11))
   175	                    .foregroundStyle(theme.p.textSecondary)
   176	                Image(systemName: "chevron.down").font(.system(size: 8)).foregroundStyle(theme.p.textTertiary)
   177	            }
   178	            .contentShape(Rectangle())
   179	        }
   180	        .buttonStyle(.plain).help(l.t("connect"))
   181	        .popover(isPresented: $model.showConnect, arrowEdge: .bottom) {
   182	            ConnectView().environment(model).environment(theme).environment(l)
   183	        }
   184	    }
   185	    private var text: String {
   186	        if model.deviceInDFU { return l.t("in_bootloader") }
   187	        if !model.readerOnline { return l.t("reader_offline") }
   188	        if let uid = model.card?.uid { return "\(l.t("card")) · \(uid)" }
   189	        return l.t("reader_online")
   190	    }
   191	}
   192	
   193	// MARK: - Action bar (the discoverable, labelled verbs)
   194	
   195	private struct ActionBar: View {
   196	    @Environment(AppModel.self) private var model
   197	    @Environment(Theme.self) private var theme
   198	    @Environment(L10n.self) private var l
   199	    private var ntag: Bool { model.card?.isNTAG == true }
   200	    private var busy: Bool { model.decoding || model.cloning || model.formatting }
   201	
   202	    var body: some View {
   203	        HStack(spacing: 8) {
   204	            // Enabled whenever the reader is online, not only once the snappy status
   205	            // poll has detected a card: the decode does its own patient coupling, so a
   206	            // seated-but-undetected card is no longer a dead button (see AppModel.decode).
   207	            ActionButton(title: l.t(ntag ? "read" : "decode"), icon: "square.grid.3x3",
   208	                         prominent: true, enabled: model.readerOnline && !busy && !model.emulating) { Task { await model.decode() } }
   209	            // Write lights up as soon as there is a document to write; it does NOT
   210	            // require a card on the reader (the target is asked for at write time in
   211	            // the sheet), so lifting the source card to place a blank never darkens it.
   212	            ActionButton(title: l.t("write"), icon: "square.and.arrow.down.on.square",
   213	                         enabled: model.cloneSource != nil && !busy && !model.emulating) { model.cloneSheet = true }
   214	            // Format is destructive but offered for ANY present card (a blank / unknown
   215	            // card can be wiped with factory keys, no prior decode required); the daemon
   216	            // keeps the anti-brick guards. Gated only on a card being present + a confirm.
   217	            ActionButton(title: l.t("format"), icon: "eraser",
   218	                         enabled: model.card != nil && !busy && !model.emulating) { model.requestFormat() }
   219	            Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
   220	            ActionButton(title: l.t("save_dump"), icon: "arrow.down.doc",
   221	                         enabled: model.source != nil) { model.saveDumpDialog() }
   222	            ActionButton(title: l.t("open_dump"), icon: "folder", enabled: true) { model.openDumpDialog() }
   223	            ActionButton(title: "apdu", icon: "terminal", on: model.apduOpen, enabled: true) { model.apduOpen.toggle() }
   224	            // Saved-cards library: device-agnostic (shown for both the X7 and a Chameleon),
   225	            // so it sits with the document verbs, not behind the Chameleon divider. Opening
   226	            // it closes the Chameleon slot library (only one detail area at a time).
   227	            ActionButton(title: l.t("library"), icon: "books.vertical", on: model.showLibrary,
   228	                         enabled: true) {
   229	                let willShow = !model.showLibrary
   230	                withAnimation(.easeInOut(duration: 0.2)) {
   231	                    model.showLibrary = willShow
   232	                    if willShow { model.showSlots = false; model.showLF = false }
   233	                }
   234	                if willShow { model.refreshSavedCards() }
   235	            }
   236	            // Chameleon-only verbs, gated on the connected device's capabilities: the
   237	            // slot library, the reader<->emulate toggle, and loading the working
   238	            // document into a slot for emulation. A plain reader (X7) shows none of them.
   239	            if model.capabilities.slots > 0 || model.capabilities.emulate
   240	                || model.capabilities.lf || model.capabilities.dfu {
   241	                Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
   242	            }
   243	            if model.capabilities.slots > 0 {
   244	                ActionButton(title: l.t("slots"), icon: "square.stack.3d.up", on: model.showSlots,
   245	                             enabled: !model.slotBusy) {
   246	                    let willShow = !model.showSlots
   247	                    withAnimation(.easeInOut(duration: 0.2)) {
   248	                        model.showSlots = willShow
   249	                        if willShow { model.showLibrary = false; model.showLF = false }
   250	                    }
   251	                    if willShow { Task { await model.loadSlots() } }
   252	                }
   253	            }
   254	            // LF (125 kHz) panel, gated on the device advertising lf: read an LF tag,
   255	            // clone it to a T5577, or load an EM410x id into a slot to emulate. A plain
   256	            // reader (X7, lf:false) never shows it.
   257	            if model.capabilities.lf {
   258	                ActionButton(title: "LF", icon: "wifi", on: model.showLF,
   259	                             enabled: !model.lfBusy, help: l.t("lf_hint")) {
   260	                    let willShow = !model.showLF

exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '1,180p;480,640p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	#!/usr/bin/env python3
     2	"""chameleon_d - JSON-over-stdio daemon wrapping the Chameleon Ultra CLI engine.
     3	
     4	Sibling of x7d.py. The native macOS (SwiftUI) front-end speaks the SAME contract
     5	for every device it drives, so the shell never learns a new protocol: it reads the
     6	`capabilities` manifest returned by `info` and lights up the panels the device
     7	supports. This daemon holds one vendored ChameleonCom (transport) + ChameleonCMD
     8	(command layer) and serialises every device op (one device, one command stream).
     9	
    10	Wire format - newline-delimited JSON on stdin/stdout (identical to x7d.py):
    11	  request : {"id": <n>, "method": "<name>", "params": {...}}
    12	  response: {"id": <n>, "result": {...}}  |  {"id": <n>, "error": "<msg>"}
    13	  event   : {"event": "progress", "method": "<name>", ...}   (id-less, unsolicited)
    14	
    15	Methods (P0 surface): info, poll, slots_list, slot_select, mf_read_block, decode.
    16	Hex at the JSON boundary is lowercase space-separated ("01 02 03 04"); keys are
    17	12-char hex. SAK is an int (matching x7d, so the shell's parser is unchanged).
    18	"""
    19	import sys
    20	import os
    21	import re
    22	import json
    23	import time
    24	import queue
    25	import struct
    26	import shutil
    27	import hashlib
    28	import zipfile
    29	import tempfile
    30	import threading
    31	import subprocess
    32	import collections
    33	import importlib.util
    34	
    35	# Vendored upstream engine (GPLv3, RfidResearchGroup/ChameleonUltra). Imports on a
    36	# bare interpreter - serial/colorama/prompt_toolkit are optional in the package.
    37	from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
    38	from chameleon.chameleon_cmd import ChameleonCMD
    39	from chameleon.chameleon_enum import (SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
    40	                                      Status, MifareClassicPrngType, MifareClassicDarksideStatus,
    41	                                      HIDFormat)
    42	from chameleon.chameleon_utils import UnexpectedResponseError
    43	
    44	# Host-side crackers over the vendored C tools (firmware acquires nonces, host
    45	# cracks them). Optional: if the binaries are not built, decode degrades to the
    46	# on-device dictionary check and never crashes.
    47	try:
    48	    import chameleon_crack as crack
    49	except Exception:                        # pragma: no cover - import guard
    50	    crack = None
    51	
    52	# The learned-key reranker + the curated dictionary are SHARED with x7d (one cache
    53	# and one dictionary across both readers). Both are optional so the daemon still
    54	# runs if they are unavailable.
    55	try:
    56	    from learned_keys import LearnedKeyCache
    57	except Exception:                        # pragma: no cover - import guard
    58	    LearnedKeyCache = None
    59	try:
    60	    from x7lib import BUILTIN_KEYS as _X7_BUILTIN
    61	except Exception:                        # pragma: no cover - import guard
    62	    _X7_BUILTIN = []
    63	
    64	# USB CDC vendor id of the Chameleon Ultra / Lite (VID 0x6868, PID 0x8686).
    65	CHAMELEON_VID = 0x6868
    66	
    67	# Firmware update (Nordic Secure DFU). In normal run the device is CDC VID 0x6868;
    68	# once rebooted into the bootloader it re-enumerates as VID 0x1915 / PID 0x521f. The
    69	# exact 10-byte ENTER_BOOTLOADER frame (cmd 0x03f2 = 1010) is taken verbatim from the
    70	# upstream resource/tools/enter_dfu.py: SOF 0x11, header-LRC 0xef, cmd 0x03 0xf2,
    71	# status 0x00 0x00, length 0x00 0x00, header-LRC 0x0b, data-LRC 0x00.
    72	DFU_VID = 0x1915
    73	DFU_PID = 0x521f
    74	DFU_ENTER_FRAME = b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"
    75	# Seconds to wait for the bootloader port to re-appear after the enter-DFU write.
    76	DFU_WAIT_SECONDS = 20
    77	# After the first new DFU port appears, keep watching this long for a SECOND one to enumerate
    78	# (USB re-enum is sub-second, so a second appearing within this settle window means another
    79	# device is in DFU - the target is then ambiguous and the flash is refused).
    80	DFU_SETTLE_SECONDS = 2.0
    81	# Firmware source (v1 is DOWNLOAD-ONLY): the model-specific application-only asset from the
    82	# official RfidResearchGroup/ChameleonUltra RELEASES. No local files, no nightlies, no CI
    83	# workflow artifacts, no arbitrary URLs - only the official release asset for the model.
    84	GITHUB_RELEASES = "https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/releases"
    85	# Per-asset API endpoint. Downloading through the pinned asset ID (with Accept:
    86	# application/octet-stream) fetches the EXACT asset resolved during this op, so deleting +
    87	# replacing an asset under the same tag/filename between resolve and download cannot swap
    88	# the bytes (browser_download_url would follow the new asset).
    89	GITHUB_ASSET = "https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/releases/assets/%s"
    90	# Hard cap on the firmware download (the app image is well under 1 MB; this bounds a
    91	# truncated/oversized/hostile response). A download that exceeds it is refused.
    92	MAX_FIRMWARE_BYTES = 16 * 1024 * 1024
    93	# Nordic dfu-cc Hash.hash_type enum -> hashlib algorithm (mirrors the GUI validateFiles:
    94	# SHA128 -> sha1, SHA256 -> sha256, SHA512 -> sha512). CRC / NO_HASH are not accepted.
    95	_HASH_ALGO = {2: "sha1", 3: "sha256", 4: "sha512"}
    96	# Nordic dfu-cc FwType.APPLICATION - the only image type this tool will flash (a
    97	# SOFTDEVICE / BOOTLOADER / SOFTDEVICE_BOOTLOADER declared type is a full package).
    98	_FWTYPE_APPLICATION = 0
    99	# Filenames that mark a FULL package (bootloader + softdevice). An app-only package
   100	# ships exactly the application pair (+ manifest); anything naming a bootloader or
   101	# softdevice image is a full-dfu zip, which can BRICK on a mid-flash failure.
   102	_FULL_MARKERS = ("bootloader", "softdevice", "sd_bl")
   103	# Shown when the running firmware cannot be reached to trigger the reboot-into-DFU.
   104	_MANUAL_FALLBACK = ("power the device off, hold button B while plugging in USB "
   105	                    "(LEDs 4 and 5 blink = bootloader), then run the update again.")
   106	
   107	# How many learned keys to try (after user keys, before the dictionary). Mirrors
   108	# x7d so the two readers rerank identically.
   109	LEARNED_TOP_N = 64
   110	
   111	# Attack-stage budget. The on-device dictionary check is fast; the nonce-cracking
   112	# attacks (nested/darkside) are the slow part, so the wall-clock budget guards them
   113	# (overridable per-call via params.max_seconds). Mirrors x7d's runaway watchdog.
   114	DEFAULT_ATTACK_SECONDS = 120
   115	# Darkside collects one acquisition per round and retries until the crack yields a
   116	# key or the parity-zero intersection converges; bounded by rounds AND the budget.
   117	DARKSIDE_MAX_ROUNDS = 24
   118	DARKSIDE_SYNC_MAX = 30                    # firmware sync attempts per round (CLI default)
   119	DARKSIDE_TARGET_BLOCK = 3                # sector 0 trailer, KeyA - the autopwn foothold
   120	
   121	# Hardnested (hard-PRNG / MFC Ev1) collects encrypted nonces on-device until the full
   122	# 256-value nt_enc first-byte (MSB) distribution is seen - the coverage the host cracker
   123	# needs. Each acquire is slow, so the loop is bounded by this run cap AND the wall-clock
   124	# budget + cooperative cancel (mirrors the CLI's max_runs, but the budget is the real
   125	# guard). The crack subprocess is itself capped by the remaining budget.
   126	HARDNESTED_MAX_RUNS = 200                 # CLI default max_runs
   127	HARDNESTED_MSB_TARGET = 256               # unique nt_enc MSBs = a complete distribution
   128	
   129	# Public, well-known MIFARE Classic default keys (documented defaults, never card
   130	# secrets - safe in a public repo). The named set comes first so a factory card
   131	# resolves in the first check-keys chunk; probe's curated dictionary (loaded by
   132	# x7lib from probe/dict) is appended when present.
   133	_DEFAULT_KEYS = [
   134	    "ffffffffffff", "000000000000", "a0a1a2a3a4a5", "d3f7d3f7d3f7",
   135	    "a0b0c0d0e0f0", "b0b1b2b3b4b5", "4d3a99c351dd", "1a982c7e459a",
   136	    "aabbccddeeff", "714c5c886e97", "587ee5f9350f", "a0478cc39091",
   137	    "533cb6c723f6", "8fd0a4f256e9",
   138	]
   139	BUILTIN_KEYS = list(_DEFAULT_KEYS)
   140	_seen = set(_DEFAULT_KEYS)
   141	for _k in _X7_BUILTIN:
   142	    if _k not in _seen:
   143	        BUILTIN_KEYS.append(_k)
   144	        _seen.add(_k)
   145	
   146	
   147	def hx(b):
   148	    """Lowercase space-separated hex, identical to x7d/x7 hx()."""
   149	    return " ".join("%02x" % x for x in b)
   150	
   151	
   152	def _valid_key_hex(k):
   153	    """A key must be exactly 12 hex chars, else it cannot be used for auth."""
   154	    return isinstance(k, str) and len(k) == 12 and all(c in "0123456789abcdefABCDEF" for c in k)
   155	
   156	
   157	def _is_hex(s, n):
   158	    """True when `s` is exactly `n` lowercase/uppercase hex chars (used to validate a
   159	    sha256 digest string from the releases API before trusting it)."""
   160	    return isinstance(s, str) and len(s) == n and all(c in "0123456789abcdefABCDEF" for c in s)
   161	
   162	
   163	# ---- MIFARE Classic geometry (kept local so this daemon does not pull in the X7
   164	# hidapi stack just for these pure helpers). Mirrors x7lib's definitions. --------
   165	
   166	def sector_count(sak):
   167	    return 40 if sak == 0x18 else 16            # 4K vs 1K
   168	
   169	
   170	def blocks_in_sector(s):
   171	    return 4 if s < 32 else 16                   # 4K big sectors
   172	
   173	
   174	def first_block(s):
   175	    return s * 4 if s < 32 else 128 + (s - 32) * 16
   176	
   177	
   178	def trailer_block(s):
   179	    return first_block(s) + blocks_in_sector(s) - 1
   180	
   480	        raise RuntimeError("firmware init packet declares a softdevice/bootloader image "
   481	                           "(sd_size=%r bl_size=%r) - full DFU refused (brick-safety)"
   482	                           % (sd_size, bl_size))
   483	    algo = _HASH_ALGO.get(htype)
   484	    if algo is None or not hbytes:
   485	        raise RuntimeError("firmware init packet uses an unsupported hash type %r" % htype)
   486	    expected = bytes(reversed(hbytes))             # stored hash is byte-reversed
   487	    actual = hashlib.new(algo, bin_data).digest()
   488	    if expected != actual:
   489	        raise RuntimeError("firmware image hash does not match the init packet")
   490	    return {"hash_type": algo, "fw_type": ftype,
   491	            "sd_size": sd_size or 0, "bl_size": bl_size or 0}
   492	
   493	
   494	# Transport-dead exceptions: like x7d dropping the handle on OSError, drop the
   495	# Chameleon handle when the port is gone so the next command reconnects cleanly.
   496	# (TimeoutError from send_cmd_sync is an OSError subclass.)
   497	_DEAD = (OSError, NotOpenException, OpenFailException)
   498	
   499	
   500	class Daemon:
   501	    METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block",
   502	               "decode", "read_ntag", "cancel",
   503	               "slot_set_type", "slot_enable", "slot_clear", "slot_nick", "slot_save",
   504	               "emulate_mode", "emulate_load", "emulate_load_ntag", "emu_read",
   505	               "magic_write", "write_mfd",
   506	               "lf_scan", "lf_write", "lf_emu",
   507	               "dfu_check", "dfu_flash")
   508	
   509	    # Long ops whose cancel window is armed (the flag cleared) at DISPATCH, so a
   510	    # cancel that lands before the worker starts the op still targets it and a stale
   511	    # cancel from a prior op cannot leak in (mirrors x7d). dfu_flash honors the flag
   512	    # only BEFORE the flash write begins (never mid-write - that can brick).
   513	    CANCELLABLE = ("decode", "dfu_flash")
   514	
   515	    # Bounded join for a NON-flash in-flight op on stdin EOF (a flash in flight is joined
   516	    # unbounded instead). A class attribute so tests can shrink it to prove the flash path
   517	    # takes the unbounded branch rather than this one.
   518	    EOF_JOIN_TIMEOUT = 5.0
   519	
   520	    # Settle delay before the LF write read-back verify: a freshly written T5577 needs a
   521	    # moment to re-power and answer, so an immediate re-scan can miss it and report a false
   522	    # unverified. Matches the reference GUI's 500 ms. A class attribute so tests set it to 0.
   523	    LF_SETTLE_SECONDS = 0.5
   524	
   525	    def __init__(self, learned=None, port=None, cracker=crack):
   526	        self.com = None
   527	        self.cmd = None                  # the ChameleonCMD command layer (or a fake)
   528	        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
   529	        # environment so the app can pin a user-chosen serial port when it spawns us. A
   530	        # missing/empty env var leaves _port None (auto-discovery via _find_port()).
   531	        if port is None:
   532	            port = os.environ.get("CHAMELEON_PORT") or None
   533	        self._port = port
   534	        self._reader_mode = None         # cached: True once the device is in reader mode
   535	        self.crack = cracker             # host-side crackers (injectable for tests)
   536	        self._cancel = threading.Event()  # cooperative abort for the long decode
   537	        # Two-stage firmware-flash guard, both preventing a mid-flash teardown (brick):
   538	        # `_flash_pending` is armed at DISPATCH of dfu_flash (before any EOF/timeout could
   539	        # pick the bounded join, closing the dispatch race), and `_flashing` marks the
   540	        # committed, uninterruptible write. Either set => EOF/shutdown join UNBOUNDED and
   541	        # SIGTERM/SIGINT are ignored, so the flasher subprocess is never abandoned.
   542	        self._flash_pending = threading.Event()
   543	        self._flashing = threading.Event()
   544	        self._emit_lock = threading.Lock()  # serialize stdout across worker + reader
   545	        # The protocol channel, captured at construction. __main__ redirects
   546	        # sys.stdout to stderr so vendored-library print() cannot corrupt it.
   547	        self._out = sys.stdout
   548	        # Shared verified-key reranker (injectable for tests). None if unavailable.
   549	        if learned is not None:
   550	            self.learned = learned
   551	        elif LearnedKeyCache is not None:
   552	            self.learned = LearnedKeyCache()
   553	        else:
   554	            self.learned = None
   555	
   556	    # ---- connection --------------------------------------------------------
   557	
   558	    def _looks_like_chameleon(self, p):
   559	        """True if a serial port looks like a Chameleon in normal (CDC) mode. Mirrors the
   560	        official Chameleon GUI's matching so a genuine device is still found when the OS did
   561	        not surface its vid (or a Lite reports differently): match on vid, on the Proxgrind
   562	        manufacturer, or on 'chameleon' in the product/description. A port already in the
   563	        bootloader (DFU vid) is never a normal device, so it is excluded."""
   564	        if getattr(p, "vid", None) == DFU_VID:
   565	            return False
   566	        if getattr(p, "vid", None) == CHAMELEON_VID:
   567	            return True
   568	        if getattr(p, "manufacturer", None) == "Proxgrind":
   569	            return True
   570	        for text in (getattr(p, "product", None), getattr(p, "description", None)):
   571	            if text and "chameleon" in text.lower():
   572	                return True
   573	        return False
   574	
   575	    def _find_port(self):
   576	        """First serial port that looks like a Chameleon in normal (CDC) mode, or None."""
   577	        for p in self._list_ports():
   578	            if self._looks_like_chameleon(p):
   579	                return p.device
   580	        return None
   581	
   582	    def _connect(self, port=None):
   583	        """Open the device once and reuse it. ChameleonCom.open sets 115200 + DTR
   584	        high. Raises when no device is found so `info` returns a clean error
   585	        envelope instead of crashing on a hardware-free machine."""
   586	        if self.cmd is None:
   587	            dev = port or self._port or self._find_port()
   588	            if not dev:
   589	                raise RuntimeError("no Chameleon device found")
   590	            com = ChameleonCom()
   591	            com.open(dev)
   592	            self.com = com
   593	            self.cmd = ChameleonCMD(com)
   594	        return self.cmd
   595	
   596	    def _drop(self):
   597	        """Forget the device handle so the next _connect() re-opens it (called when
   598	        a hardware op fails: the cached handle is dead)."""
   599	        if self.com is not None:
   600	            try:
   601	                self.com.close()
   602	            except Exception:
   603	                pass
   604	        self.com = None
   605	        self.cmd = None
   606	        self._reader_mode = None
   607	
   608	    def _ensure_reader(self, c):
   609	        """Reader ops (poll/decode/mf_read_block) need the device in reader mode; a
   610	        card op issued in tag/emulator mode returns DEVICE_MODE_ERROR (mis-read as
   611	        "no card"). Switch once per connection and cache it. The Lite has no reader
   612	        front-end (set reader mode -> NOT_IMPLEMENTED), surfaced as a clean
   613	        RuntimeError so callers report a reader fault, never a phantom no-card."""
   614	        if self._reader_mode:
   615	            return
   616	        try:
   617	            if not c.is_device_reader_mode():
   618	                c.set_device_reader_mode(True)
   619	        except UnexpectedResponseError as e:
   620	            raise RuntimeError("device has no reader mode (Lite?): %s" % e)
   621	        self._reader_mode = True
   622	
   623	    def emit(self, obj):
   624	        with self._emit_lock:
   625	            self._out.write(json.dumps(obj) + "\n")
   626	            self._out.flush()
   627	
   628	    # ---- methods -----------------------------------------------------------
   629	
   630	    def info(self, p):
   631	        c = self._connect(p.get("port"))
   632	        model = c.get_device_model()             # 0 = Ultra, 1 = Lite
   633	        major, minor = c.get_app_version()
   634	        git = c.get_git_version()
   635	        chip = c.get_device_chip_id()
   636	        family = "chameleon-ultra" if model == 0 else "chameleon-lite"
   637	        name = "Chameleon Ultra" if model == 0 else "Chameleon Lite"
   638	        return {"family": family, "model": name, "serial": chip,
   639	                "hw": "app %d.%d (%s)" % (major, minor, git),
   640	                "capabilities": _capabilities(model, self.crack)}

2026-07-21T03:38:50.574966Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba app/Sources/Engine/DeviceRegistry.swift | sed -n '1,300p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import Foundation
     2	import IOKit
     3	import IOKit.hid
     4	import IOKit.serial
     5	
     6	/// Describes one device family the app can drive: which daemon speaks for it, where
     7	/// its script lives under the probe root, how to recognise its USB device, and the
     8	/// static capability baseline to assume before the daemon's own `info` manifest
     9	/// lands. The shell picks a descriptor by USB match, spawns its daemon, and gates
    10	/// UI on capabilities - it never hardcodes "if X7 / if Chameleon".
    11	struct DeviceDescriptor: Identifiable, Equatable, Sendable {
    12	    let id: String
    13	    let family: String
    14	    let displayName: String
    15	    let daemonScript: String        // e.g. "x7d.py" / "chameleon_d.py"
    16	    let probeSubdir: String?        // relative subdir under the probe root, nil = root
    17	    let usbMatch: USBMatch
    18	    let capabilities: DeviceCapabilities
    19	    /// A serial port to pin explicitly (a manual Connect to a chosen /dev/cu.* path):
    20	    /// the daemon is spawned with CHAMELEON_PORT set to this. nil = auto-detect the
    21	    /// port as before. A distinct `id` per pinned port makes the swap logic treat it
    22	    /// as a different device and re-open the daemon.
    23	    var portOverride: String? = nil
    24	}
    25	
    26	/// How to recognise a device on the USB bus. `pid == nil` matches any product id
    27	/// under the vendor (the Chameleon re-enumerates to a different pid in bootloader,
    28	/// so it is matched by vendor alone).
    29	struct USBMatch: Equatable, Sendable {
    30	    enum Transport: Sendable { case hid, serial }
    31	    let vid: Int
    32	    let pid: Int?
    33	    let transport: Transport
    34	    /// Optional string matchers for a serial device whose vid can vary (a Chameleon
    35	    /// clone / re-enumeration). Matched against the USB ancestor's "USB Vendor Name" /
    36	    /// "USB Product Name", in ADDITION to the vid/pid check, so a genuine Chameleon is
    37	    /// still recognised when only its reported strings identify it.
    38	    var vendorName: String? = nil
    39	    var productContains: String? = nil
    40	}
    41	
    42	/// The device catalogue + USB detection. `detect()` returns the first present
    43	/// device in priority order, or nil when none is plugged in.
    44	enum DeviceRegistry {
    45	    /// XIXEI X7 (HID PN533 reader) -> x7d.py.
    46	    static let x7 = DeviceDescriptor(
    47	        id: "x7", family: "x7", displayName: "XIXEI X7",
    48	        daemonScript: "x7d.py", probeSubdir: nil,
    49	        usbMatch: USBMatch(vid: 0x2518, pid: 0x6022, transport: .hid),
    50	        capabilities: .x7)
    51	
    52	    /// Chameleon Ultra / Lite (USB-CDC serial) -> chameleon_d.py. Matched by vendor
    53	    /// id only; the daemon's `info` reports Ultra vs Lite and the real capabilities.
    54	    static let chameleonUltra = DeviceDescriptor(
    55	        id: "chameleon-ultra", family: "chameleon-ultra", displayName: "Chameleon Ultra",
    56	        daemonScript: "chameleon_d.py", probeSubdir: nil,
    57	        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial,
    58	                           vendorName: "Proxgrind", productContains: "chameleon"),
    59	        capabilities: .chameleonUltra)
    60	
    61	    /// A Chameleon sitting in the Nordic bootloader (re-enumerated to VID 0x1915). It
    62	    /// has no command interface (the daemon's `info` cannot query it), but recognising it
    63	    /// as a Chameleon-in-DFU keeps the firmware/flash action reachable so a stuck or
    64	    /// manually-B-buttoned device can be flash-recovered after relaunch, instead of
    65	    /// silently launching into the X7 fallback with DFU hidden. Same daemon (chameleon_d.py).
    66	    static let chameleonDFU = DeviceDescriptor(
    67	        id: "chameleon-dfu", family: "chameleon-dfu", displayName: "Chameleon (DFU)",
    68	        daemonScript: "chameleon_d.py", probeSubdir: nil,
    69	        usbMatch: USBMatch(vid: 0x1915, pid: 0x521f, transport: .serial),
    70	        capabilities: .chameleonDFU)
    71	
    72	    /// Every family the app can drive, in match priority order (X7 first, so a
    73	    /// machine with both plugged in keeps driving the X7 until the user unplugs it; the
    74	    /// DFU match is last, only relevant when a Chameleon is stuck in the bootloader).
    75	    static let all: [DeviceDescriptor] = [x7, chameleonUltra, chameleonDFU]
    76	
    77	    /// The descriptor to use when nothing is detected: the X7, so a bare machine
    78	    /// starts the X7 daemon and shows "reader offline" exactly as the single-device
    79	    /// build did.
    80	    static let fallback = x7
    81	
    82	    /// The first present device by USB match, or nil when none is connected. Cheap
    83	    /// enough to run on the status poll (a bounded IORegistry scan, no I/O).
    84	    static func detect() -> DeviceDescriptor? {
    85	        all.first { USBProbe.isPresent($0.usbMatch) }
    86	    }
    87	
    88	    /// Every present known device (not just the first), in the same priority order.
    89	    /// Feeds the Connect surface's detected-devices list.
    90	    static func detectAll() -> [DeviceDescriptor] {
    91	        all.filter { USBProbe.isPresent($0.usbMatch) }
    92	    }
    93	}
    94	
    95	/// One enumerated USB serial (CDC) port, for the Connect surface's manual-connect list.
    96	/// Carries the callout path plus the owning USB device's vid/pid/strings so the UI can
    97	/// hint which port is likely a Chameleon (and which is a device in DFU).
    98	struct SerialPortInfo: Identifiable, Equatable, Sendable {
    99	    let path: String
   100	    let vid: Int?
   101	    let pid: Int?
   102	    let vendorName: String?
   103	    let productName: String?
   104	    var id: String { path }
   105	    /// Likely a Chameleon: its vid, its reported vendor name, or a product name that
   106	    /// contains "chameleon" (case-insensitive), covering clones / re-enumerations.
   107	    var isChameleon: Bool {
   108	        vid == 0x6868
   109	            || vendorName?.caseInsensitiveCompare("Proxgrind") == .orderedSame
   110	            || (productName?.range(of: "chameleon", options: .caseInsensitive) != nil)
   111	    }
   112	    /// A device sitting in the Nordic bootloader (re-enumerated to VID 0x1915): shown
   113	    /// but not manually connectable (it has no serial command interface, only flash).
   114	    var isDFU: Bool { vid == 0x1915 }
   115	}
   116	
   117	/// Point-in-time USB presence checks over IOKit. HID devices (the X7) are found via
   118	/// IOHIDManager; CDC serial devices (the Chameleon) via the serial BSD service,
   119	/// walking up to the owning USB device node to read its vendor / product id.
   120	enum USBProbe {
   121	    static func isPresent(_ m: USBMatch) -> Bool {
   122	        switch m.transport {
   123	        case .hid: return hidPresent(vid: m.vid, pid: m.pid)
   124	        case .serial: return serialPresent(m)
   125	        }
   126	    }
   127	
   128	    private static func hidPresent(vid: Int, pid: Int?) -> Bool {
   129	        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
   130	        var match: [String: Any] = [kIOHIDVendorIDKey as String: vid]
   131	        if let pid { match[kIOHIDProductIDKey as String] = pid }
   132	        IOHIDManagerSetDeviceMatching(manager, match as CFDictionary)
   133	        guard let devices = IOHIDManagerCopyDevices(manager) else { return false }
   134	        return CFSetGetCount(devices) > 0
   135	    }
   136	
   137	    private static func serialPresent(_ m: USBMatch) -> Bool {
   138	        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return false }
   139	        var iterator: io_iterator_t = 0
   140	        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
   141	            return false
   142	        }
   143	        defer { IOObjectRelease(iterator) }
   144	        var found = false
   145	        var service = IOIteratorNext(iterator)
   146	        while service != 0 {
   147	            if usbAncestorMatches(service, m) { found = true }
   148	            IOObjectRelease(service)
   149	            if found { break }
   150	            service = IOIteratorNext(iterator)
   151	        }
   152	        return found
   153	    }
   154	
   155	    /// Walk up the IOService plane from a leaf (the serial client) to the USB device
   156	    /// node that carries `idVendor` / `idProduct`, matching there. As well as the
   157	    /// vid/pid check, an ancestor whose "USB Vendor Name" equals the match's
   158	    /// `vendorName`, or whose "USB Product Name" contains `productContains`
   159	    /// (case-insensitive), also matches - so a Chameleon is recognised even when only
   160	    /// its reported strings identify it. Bounded so a malformed registry can never loop.
   161	    private static func usbAncestorMatches(_ service: io_object_t, _ m: USBMatch) -> Bool {
   162	        var node = service
   163	        IOObjectRetain(node)
   164	        defer { IOObjectRelease(node) }
   165	        for _ in 0..<10 {
   166	            if intProperty(node, "idVendor") == m.vid {
   167	                if m.pid == nil || intProperty(node, "idProduct") == m.pid { return true }
   168	            }
   169	            if let want = m.vendorName,
   170	               let got = stringProperty(node, "USB Vendor Name"),
   171	               got.caseInsensitiveCompare(want) == .orderedSame { return true }
   172	            if let want = m.productContains,
   173	               let got = stringProperty(node, "USB Product Name"),
   174	               got.range(of: want, options: .caseInsensitive) != nil { return true }
   175	            var parent: io_registry_entry_t = 0
   176	            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
   177	            guard kr == KERN_SUCCESS, parent != 0 else { return false }
   178	            IOObjectRelease(node)
   179	            node = parent
   180	        }
   181	        return false
   182	    }
   183	
   184	    /// Enumerate every USB serial (CDC) callout port for the Connect surface. Reads
   185	    /// each service's "IOCalloutDevice" as the path and walks to the owning USB device
   186	    /// for its vid/pid/vendor/product. Ports with NO USB ancestor carrying an idVendor
   187	    /// (pure-Bluetooth / virtual ports like /dev/cu.Bluetooth-Incoming-Port) are
   188	    /// skipped. Chameleon / DFU ports sort first, then by path.
   189	    static func serialPorts() -> [SerialPortInfo] {
   190	        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return [] }
   191	        var iterator: io_iterator_t = 0
   192	        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
   193	            return []
   194	        }
   195	        defer { IOObjectRelease(iterator) }
   196	        var ports: [SerialPortInfo] = []
   197	        var service = IOIteratorNext(iterator)
   198	        while service != 0 {
   199	            if let path = stringProperty(service, "IOCalloutDevice"),
   200	               let a = usbAncestorInfo(service), let vid = a.vid {
   201	                ports.append(SerialPortInfo(path: path, vid: vid, pid: a.pid,
   202	                                            vendorName: a.vendorName, productName: a.productName))
   203	            }
   204	            IOObjectRelease(service)
   205	            service = IOIteratorNext(iterator)
   206	        }
   207	        return ports.sorted { a, b in
   208	            let ra = a.isChameleon || a.isDFU, rb = b.isChameleon || b.isDFU
   209	            if ra != rb { return ra }
   210	            return a.path < b.path
   211	        }
   212	    }
   213	
   214	    /// vid/pid + vendor/product strings of the first USB ancestor carrying an idVendor,
   215	    /// or nil when the leaf has no USB device above it. Bounded 10-hop walk.
   216	    private static func usbAncestorInfo(_ service: io_object_t) -> (vid: Int?, pid: Int?, vendorName: String?, productName: String?)? {
   217	        var node = service
   218	        IOObjectRetain(node)
   219	        defer { IOObjectRelease(node) }
   220	        for _ in 0..<10 {
   221	            if let vid = intProperty(node, "idVendor") {
   222	                return (vid, intProperty(node, "idProduct"),
   223	                        stringProperty(node, "USB Vendor Name"),
   224	                        stringProperty(node, "USB Product Name"))
   225	            }
   226	            var parent: io_registry_entry_t = 0
   227	            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
   228	            guard kr == KERN_SUCCESS, parent != 0 else { return nil }
   229	            IOObjectRelease(node)
   230	            node = parent
   231	        }
   232	        return nil
   233	    }
   234	
   235	    private static func intProperty(_ entry: io_registry_entry_t, _ key: String) -> Int? {
   236	        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
   237	            return nil
   238	        }
   239	        return (cf.takeRetainedValue() as? NSNumber)?.intValue
   240	    }
   241	
   242	    private static func stringProperty(_ entry: io_registry_entry_t, _ key: String) -> String? {
   243	        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
   244	            return nil
   245	        }
   246	        return cf.takeRetainedValue() as? String
   247	    }
   248	}

2026-07-21T03:38:55.726126Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba app/Sources/AppModel.swift | sed -n '210,410p'; rg -n \"func swapDevice|swapDevice\\(|func applyReaderGone|applyReaderGone|func refreshStatus|portOverride|monitor\\(\\)\" app/Sources/AppModel.swift" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   210	
   211	    /// Detect the connected device, then start its daemon + read device info and look
   212	    /// for a card (connect at launch, not lazily). With no device detected we fall
   213	    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
   214	    ///
   215	    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
   216	    /// reconnect can never replace the bridge under a running decode / clone. When the
   217	    /// detected device differs from the current one it routes through `swapDevice` so
   218	    /// the old daemon is torn down (never silently orphaned) under the swap guard.
   219	    func connect() async {
   220	        guard !swapping, !deviceBusy else { return }
   221	        await detectAndOpen()
   222	    }
   223	
   224	    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
   225	    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
   226	    /// is torn down, and bring the current one up.
   227	    private func detectAndOpen() async {
   228	        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
   229	        if bridge != nil, found.id != descriptor.id {
   230	            await swapDevice(to: found)
   231	            return
   232	        }
   233	        descriptor = found
   234	        await openCurrentDevice()
   235	    }
   236	
   237	    /// Refresh the Connect surface's lists (known present devices + all USB serial
   238	    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
   239	    func refreshConnectLists() {
   240	        detectedDevices = DeviceRegistry.detectAll()
   241	        serialPorts = USBProbe.serialPorts()
   242	    }
   243	
   244	    /// The Connect surface's Rescan: refresh the lists and immediately re-run detection
   245	    /// (the same path as `connect()`), without waiting for the 1.5s monitor tick.
   246	    func rescan() async {
   247	        guard !swapping, !deviceBusy else { return }
   248	        refreshConnectLists()
   249	        await detectAndOpen()
   250	        refreshConnectLists()
   251	    }
   252	
   253	    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
   254	    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
   255	    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
   256	    func connectManual(port: String) async {
   257	        guard !swapping, !deviceBusy else { return }
   258	        let base = DeviceRegistry.chameleonUltra
   259	        let manual = DeviceDescriptor(
   260	            id: "chameleon-manual:\(port)",
   261	            family: base.family,
   262	            displayName: base.displayName,
   263	            daemonScript: base.daemonScript,
   264	            probeSubdir: base.probeSubdir,
   265	            usbMatch: base.usbMatch,
   266	            capabilities: base.capabilities,
   267	            portOverride: port)
   268	        await swapDevice(to: manual)
   269	        refreshConnectLists()
   270	    }
   271	
   272	    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
   273	    /// then sample the reader. Shared by the first connect and a hot-swap, so both
   274	    /// paths land the same state (info, capabilities via `info`, reader/card status).
   275	    private func openCurrentDevice() async {
   276	        // A device in the bootloader has no command interface to query: present a reachable
   277	        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
   278	        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
   279	        if deviceInDFU {
   280	            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
   281	            info = nil
   282	            readerOnline = false
   283	            dfuStatus = nil
   284	            lastError = nil
   285	            return
   286	        }
   287	        let b = activeBridge()
   288	        do {
   289	            info = try await b.info()
   290	            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
   291	            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
   292	            readerOnline = true
   293	            lastError = nil
   294	            await refreshStatus()
   295	        } catch {
   296	            applyReaderGone()
   297	            lastError = "\(error)"
   298	        }
   299	    }
   300	
   301	    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
   302	    /// as decodes recover keys).
   303	    func refreshLearnedCount() async {
   304	        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
   305	    }
   306	
   307	    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
   308	    func clearLearnedKeys() async {
   309	        try? await activeBridge().clearLearnedKeys()
   310	        await refreshLearnedCount()
   311	    }
   312	
   313	    /// Live status: keep the reader / card pill honest when the X7 or a card is
   314	    /// plugged or removed with no user action. Runs until the view's task is
   315	    /// cancelled. Skips polling during an operation that already owns the reader.
   316	    func monitor() async {
   317	        while !Task.isCancelled {
   318	            try? await Task.sleep(for: .seconds(1.5))
   319	            if deviceBusy || swapping { continue }
   320	            // Hot-swap detection runs even while emulating: unplugging an emulating
   321	            // Chameleon and attaching another device must still tear down + swap. It is a
   322	            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
   323	            // With only the X7 involved, detect() keeps returning the same descriptor, so
   324	            // this path is inert and the poll below is unchanged.
   325	            if let found = DeviceRegistry.detect() {
   326	                // A manually-pinned device (portOverride) is NOT auto-swapped away just
   327	                // because detect() returns the non-pinned auto variant with a different
   328	                // id - the user chose this port. If a pinned device truly disappears, the
   329	                // poll below sees reader:false and applyReaderGone reflects it; the user
   330	                // can then rescan. Auto descriptors swap on an id change exactly as before.
   331	                if descriptor.portOverride == nil, found.id != descriptor.id {
   332	                    await swapDevice(to: found); continue
   333	                }
   334	            } else if emulating {
   335	                // The emulating device was unplugged with nothing to swap to: the card
   336	                // poll is skipped while emulating, so this is the only place that would
   337	                // notice it is gone. Reflect it (which also clears the emulate state).
   338	                applyReaderGone()
   339	                continue
   340	            }
   341	            // Only the card POLL is skipped while emulating: a poll forces reader mode,
   342	            // which would break the emulation.
   343	            if emulating { continue }
   344	            await refreshStatus()
   345	        }
   346	    }
   347	
   348	    /// Replace the active device with a freshly detected one. Every synchronous state
   349	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   350	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   351	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   352	    /// already nil. Only reader-bound state is cleared; the writable document is
   353	    /// device-independent and is deliberately kept across the swap.
   354	    private func swapDevice(to found: DeviceDescriptor) async {
   355	        guard !swapping else { return }
   356	        swapping = true
   357	        defer { swapping = false }              // released however this returns
   358	        let old = bridge
   359	        bridge = nil                            // detach: no path can obtain the old bridge now
   360	        descriptor = found
   361	        withAnimation(.easeInOut(duration: 0.3)) {
   362	            readerOnline = false
   363	            info = nil
   364	            card = nil
   365	            clearCardBound()
   366	            resetChameleonState()
   367	        }
   368	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   369	        await openCurrentDevice()               // creates + brings up the new bridge
   370	    }
   371	
   372	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   373	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   374	    /// and the slot view / emulate toggle must not persist across a device that has none.
   375	    private func resetChameleonState() {
   376	        slots = []
   377	        selectedSlot = nil
   378	        showSlots = false
   379	        showLF = false
   380	        lfScanResult = nil
   381	        lfWriteResult = nil
   382	        emulating = false
   383	        dfuStatus = nil
   384	        // A failed flash usually leaves the device in the bootloader, which triggers a
   385	        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
   386	        // still open, KEEP the flash outcome (error / done + progress) so its banner and
   387	        // recovery text do not vanish under the user; clearFlashState() clears them when
   388	        // the sheet is dismissed.
   389	        if !flashingSheet {
   390	            flashStage = nil
   391	            flashPercent = nil
   392	            flashDone = false
   393	            flashError = nil
   394	        }
   395	    }
   396	
   397	    /// Clear the flash outcome + progress. Called when the flashing sheet is dismissed, so a
   398	    /// stale error / done state never carries into the next time it is opened.
   399	    func clearFlashState() {
   400	        flashStage = nil
   401	        flashPercent = nil
   402	        flashDone = false
   403	        flashError = nil
   404	    }
   405	
   406	    /// Consecutive polls that saw the reader but no card; a seated card that blips
   407	    /// for one cycle should not drop its decoded grid, so we debounce a removal.
   408	    private var cardAbsentStreak = 0
   409	
   410	    /// One status sample: detects reader unplug (drops to offline + clears), reader
230:            await swapDevice(to: found)
267:            portOverride: port)
268:        await swapDevice(to: manual)
296:            applyReaderGone()
316:    func monitor() async {
326:                // A manually-pinned device (portOverride) is NOT auto-swapped away just
329:                // poll below sees reader:false and applyReaderGone reflects it; the user
331:                if descriptor.portOverride == nil, found.id != descriptor.id {
332:                    await swapDevice(to: found); continue
338:                applyReaderGone()
354:    private func swapDevice(to found: DeviceDescriptor) async {
412:    private func refreshStatus() async {
416:                applyReaderGone()
439:            applyReaderGone()
467:    private func applyReaderGone() {

2026-07-21T03:39:11.761054Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba app/Sources/AppModel.swift | sed -n '406,490p'; nl -ba app/Sources/Engine/DeviceBridge.swift | sed -n '1,230p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   406	    /// Consecutive polls that saw the reader but no card; a seated card that blips
   407	    /// for one cycle should not drop its decoded grid, so we debounce a removal.
   408	    private var cardAbsentStreak = 0
   409	
   410	    /// One status sample: detects reader unplug (drops to offline + clears), reader
   411	    /// replug (back online + refetch device info), and card placed / removed.
   412	    private func refreshStatus() async {
   413	        do {
   414	            let p = try await activeBridge().poll(tries: 8)
   415	            if p.reader == false {           // reader unplugged: reflect it at once
   416	                applyReaderGone()
   417	                return
   418	            }
   419	            readerOnline = true
   420	            if info == nil { info = try? await activeBridge().info() }   // refetch until it lands
   421	            // NOTE: do not clear lastError here - the 1.5s poll would wipe a clone /
   422	            // decode / format error banner before the user could read it. Operations
   423	            // clear it when they start; the banner also has a dismiss button.
   424	            if p.present {
   425	                cardAbsentStreak = 0
   426	                // A different card (or first placement): the DOCUMENT stays (it is the
   427	                // working image, not bound to this card); only the previous write's
   428	                // per-block glyphs reset so they never show on the new card.
   429	                if card == nil || p.uid != card?.uid {
   430	                    withAnimation(.easeInOut(duration: 0.3)) { clearCardBound(); card = p }
   431	                }
   432	            } else {
   433	                cardAbsentStreak += 1
   434	                if card != nil && cardAbsentStreak >= 2 {
   435	                    withAnimation(.easeInOut(duration: 0.3)) { card = nil; clearCardBound() }
   436	                }
   437	            }
   438	        } catch {
   439	            applyReaderGone()
   440	        }
   441	    }
   442	
   443	    /// Reset state tied to the physical card on the reader: the last clone's per-block
   444	    /// glyphs and the NTAG page view (NTAG has no writable document, so its pages are
   445	    /// bound to the live card). The writable Classic DOCUMENT (sector grid + source
   446	    /// image + selection) is deliberately kept so it survives a card swap, removal, or
   447	    /// reader unplug - the working image is not bound to whatever card is on the
   448	    /// reader. Shared by the swap, removal, and reader-gone paths so they cannot drift.
   449	    private func clearCardBound() {
   450	        cloneResults = [:]
   451	        cloneFailReasons = [:]
   452	        ntagDoc = nil
   453	        noKeysFound = false
   454	    }
   455	
   456	    /// Drop the working document entirely (the source tag's clear button): the image,
   457	    /// its grid, page dump, and selection. The card on the reader is untouched.
   458	    func clearDocument() {
   459	        withAnimation(.easeInOut(duration: 0.3)) {
   460	            source = nil; sectors = []; ntagDoc = nil; selected = nil; selectedBlock = nil
   461	            cloneResults = [:]; cloneFailReasons = [:]; noKeysFound = false
   462	        }
   463	    }
   464	
   465	    /// Reader unplugged or the daemon went away: go offline and clear everything
   466	    /// tied to a live reader. No-op when already in that state (avoids churn).
   467	    private func applyReaderGone() {
   468	        cardAbsentStreak = 0
   469	        guard readerOnline || card != nil || info != nil else { return }
   470	        withAnimation(.easeInOut(duration: 0.3)) {
   471	            readerOnline = false
   472	            info = nil
   473	            card = nil
   474	            clearCardBound()
   475	            resetChameleonState()
   476	        }
   477	    }
   478	
   479	    func decode() async {
   480	        // Refuse while a swap is tearing the device down, or another device op already
   481	        // owns the reader (deviceBusy includes `decoding`, so this also blocks a
   482	        // double-decode). Also refuse while emulating: a reader op would force the device
   483	        // back to reader mode under the emulation, leaving the toggle lying. Serialized,
   484	        // never racing the bridge.
   485	        guard !swapping, !deviceBusy, !emulating else { return }
   486	        decoding = true
   487	        decodeCancelled = false
   488	        decodeProgress = nil
   489	        decodeStart = Date()
   490	        lastError = nil
     1	import Foundation
     2	
     3	/// Talks to a verified Python daemon (probe/<daemonScript>) over newline-delimited
     4	/// JSON on a child-process pipe. The actor owns the process, correlates each request
     5	/// id with a continuation, and routes progress events separately. It is device
     6	/// neutral: the daemon to spawn, and the probe subdir it lives in, come from a
     7	/// `DeviceDescriptor`, so the same transport drives the X7 (x7d.py) and the
     8	/// Chameleon (chameleon_d.py) - both speak the identical contract.
     9	///
    10	/// Architecture (2026-06-19): A-first hybrid. This bridge is deliberately thin and
    11	/// the daemon contract is narrow so the engine can later be replaced by a native
    12	/// Swift + vendored-C implementation without touching the UI.
    13	actor DeviceBridge {
    14	    enum EngineError: Error, CustomStringConvertible {
    15	        case daemon(String)
    16	        case badResponse
    17	        var description: String {
    18	            switch self {
    19	            case .daemon(let m): return m
    20	            case .badResponse: return "bad daemon response"
    21	            }
    22	        }
    23	    }
    24	
    25	    /// The device this bridge drives. Immutable + Sendable, so callers on other
    26	    /// actors can read it without hopping onto this actor (used to tell whether a
    27	    /// live bridge already matches the detected device before spawning a new one).
    28	    nonisolated let descriptor: DeviceDescriptor
    29	
    30	    private let python: URL
    31	    private let workDir: URL
    32	    private let script: URL
    33	    private var process: Process?
    34	    private var stdin: FileHandle?
    35	    private var outReader: FileHandle?
    36	    private var errReader: FileHandle?
    37	    private var nextID = 1
    38	    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    39	    private var buffer = Data()
    40	    /// Set for the duration of a streaming op (only one runs at a time, the UI
    41	    /// disables other actions); id-less progress events are routed here.
    42	    private var eventSink: (@Sendable (EngineEvent) -> Void)?
    43	    /// Bumped each time a streaming op (decode / write) starts. A cancel's hard-kill
    44	    /// fallback captures the generation of the op it is cancelling and terminates only
    45	    /// if THAT same op is still in flight when the grace window elapses, so it can never
    46	    /// kill a later, unrelated op that reused the shared daemon.
    47	    private var opGeneration = 0
    48	
    49	    init(descriptor: DeviceDescriptor) {
    50	        self.descriptor = descriptor
    51	        let p = Self.resolvePaths(for: descriptor)
    52	        self.python = p.python
    53	        self.workDir = p.workDir
    54	        self.script = p.script
    55	    }
    56	
    57	    /// Resolve the python interpreter + daemon script, preferring the copies
    58	    /// vendored inside the packaged .app (Contents/Resources/python + /probe), then
    59	    /// environment overrides (X7_PYTHON / X7_PROBE_DIR), then the dev checkout. Both
    60	    /// a shipped app and a dev build work with no configuration. The working dir is
    61	    /// always the probe root so the daemons' shared imports (x7lib, learned_keys,
    62	    /// the vendored chameleon package) resolve regardless of which script is spawned.
    63	    /// libhidapi is found by x7hid itself (a bundle-relative candidate inside the
    64	    /// .app, brew outside), so we never touch the child's environment here.
    65	    static func resolvePaths(for descriptor: DeviceDescriptor) -> (python: URL, workDir: URL, script: URL) {
    66	        let fm = FileManager.default
    67	        let res = Bundle.main.resourceURL ?? Bundle.main.bundleURL
    68	        let bundledPython = res.appendingPathComponent("python/bin/python3")
    69	        let bundledProbe = res.appendingPathComponent("probe")
    70	        let bundledScript = scriptURL(probeRoot: bundledProbe, descriptor: descriptor)
    71	        if fm.fileExists(atPath: bundledPython.path),
    72	           fm.fileExists(atPath: bundledScript.path) {
    73	            return (bundledPython, bundledProbe, bundledScript)
    74	        }
    75	        let env = ProcessInfo.processInfo.environment
    76	        let python = URL(fileURLWithPath: env["X7_PYTHON"] ?? "/usr/bin/python3")
    77	        let probe = URL(fileURLWithPath: env["X7_PROBE_DIR"] ?? Self.devProbeDir)
    78	        return (python, probe, scriptURL(probeRoot: probe, descriptor: descriptor))
    79	    }
    80	
    81	    /// Last-resort dev-checkout probe dir, computed from THIS source file's location at
    82	    /// compile time (.../app/Sources/Engine/DeviceBridge.swift -> repo root -> /probe) so
    83	    /// a from-source run still finds probe/ without baking in an absolute home path. A
    84	    /// shipped app never reaches this (the bundled Resources copy wins first), nor does a
    85	    /// run that sets X7_PROBE_DIR.
    86	    private static var devProbeDir: String {
    87	        URL(fileURLWithPath: #filePath)   // .../app/Sources/Engine/DeviceBridge.swift
    88	            .deletingLastPathComponent()  // Engine
    89	            .deletingLastPathComponent()  // Sources
    90	            .deletingLastPathComponent()  // app
    91	            .deletingLastPathComponent()  // repo root
    92	            .appendingPathComponent("probe").path
    93	    }
    94	
    95	    private static func scriptURL(probeRoot: URL, descriptor: DeviceDescriptor) -> URL {
    96	        let dir = descriptor.probeSubdir.map { probeRoot.appendingPathComponent($0) } ?? probeRoot
    97	        return dir.appendingPathComponent(descriptor.daemonScript)
    98	    }
    99	
   100	    private func startIfNeeded() throws {
   101	        guard process == nil else { return }
   102	        let p = Process()
   103	        p.executableURL = python
   104	        // -B: never write .pyc into the bundle (a code-signed .app that mutates
   105	        // itself breaks its own seal). Passed as a flag, not an env var, so we
   106	        // leave the inherited launchd environment untouched - replacing it broke
   107	        // the spawn under the GUI session.
   108	        p.arguments = ["-B", script.path]
   109	        p.currentDirectoryURL = workDir
   110	        // Pin the user-chosen serial port when this descriptor carries one (a manual
   111	        // Connect): the daemon honours CHAMELEON_PORT. We COPY the inherited launchd
   112	        // environment and add only that one key - never replace the child environment
   113	        // (a bare replacement broke the spawn under the GUI session), so libhidapi /
   114	        // PATH and the rest are preserved. Auto-detect descriptors leave it untouched.
   115	        if let port = descriptor.portOverride {
   116	            var env = ProcessInfo.processInfo.environment
   117	            env["CHAMELEON_PORT"] = port
   118	            p.environment = env
   119	        }
   120	        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
   121	        p.standardInput = inPipe
   122	        p.standardOutput = outPipe
   123	        p.standardError = errPipe
   124	        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
   125	            let d = h.availableData
   126	            guard !d.isEmpty, let self else { return }
   127	            Task { await self.ingest(d) }
   128	        }
   129	        // Drain the daemon's stderr. Without this the OS pipe buffer (~64KB) fills
   130	        // the first time the engine prints a traceback or a flood of warnings, and
   131	        // the daemon then BLOCKS on its next stderr write, hanging every request.
   132	        // We forward it to the app's own stderr so it is visible in Console / a
   133	        // terminal launch for diagnosis.
   134	        errPipe.fileHandleForReading.readabilityHandler = { h in
   135	            let d = h.availableData
   136	            if !d.isEmpty { FileHandle.standardError.write(d) }
   137	        }
   138	        p.terminationHandler = { [weak self] _ in
   139	            guard let self else { return }
   140	            Task { await self.died() }
   141	        }
   142	        try p.run()
   143	        process = p
   144	        stdin = inPipe.fileHandleForWriting
   145	        outReader = outPipe.fileHandleForReading
   146	        errReader = errPipe.fileHandleForReading
   147	    }
   148	
   149	    private func died() {
   150	        outReader?.readabilityHandler = nil
   151	        errReader?.readabilityHandler = nil
   152	        outReader = nil
   153	        errReader = nil
   154	        process = nil
   155	        stdin = nil
   156	        for (_, c) in pending { c.resume(throwing: EngineError.daemon("daemon exited")) }
   157	        pending.removeAll()
   158	        buffer.removeAll()
   159	    }
   160	
   161	    private func ingest(_ d: Data) {
   162	        buffer.append(d)
   163	        while let nl = buffer.firstIndex(of: 0x0A) {
   164	            let line = Data(buffer[buffer.startIndex..<nl])
   165	            buffer.removeSubrange(buffer.startIndex...nl)
   166	            route(line)
   167	        }
   168	    }
   169	
   170	    private func route(_ line: Data) {
   171	        guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { return }
   172	        if obj["event"] != nil {
   173	            if let ev = try? JSONDecoder().decode(EngineEvent.self, from: line) { eventSink?(ev) }
   174	            return
   175	        }
   176	        guard let id = obj["id"] as? Int else {
   177	            // An id-less line (the daemon's bad-json reply): if exactly one request
   178	            // is outstanding, fail it rather than orphan its continuation.
   179	            if let err = obj["error"] as? String, pending.count == 1,
   180	               let only = pending.keys.first, let c = pending.removeValue(forKey: only) {
   181	                c.resume(throwing: EngineError.daemon(err))
   182	            }
   183	            return
   184	        }
   185	        guard let c = pending.removeValue(forKey: id) else { return }
   186	        c.resume(returning: line)
   187	    }
   188	
   189	    private func transact<T: Decodable>(id: Int, _ reqData: Data, timeout: Duration?, as _: T.Type) async throws -> T {
   190	        let line: Data = try await withCheckedThrowingContinuation { cont in
   191	            pending[id] = cont
   192	            try? stdin?.write(contentsOf: reqData)
   193	            try? stdin?.write(contentsOf: Data([0x0A]))
   194	            // Arm a deadline: a daemon that is alive but WEDGED (stuck on a hardware
   195	            // read that never returns) would otherwise orphan this continuation
   196	            // forever - freezing the live-status poll and every later op. On the
   197	            // deadline we fail this request and kill the daemon so it respawns.
   198	            //
   199	            // A nil timeout means NO deadline: this is used ONLY for the firmware flash,
   200	            // where a timeout-driven resolve-failed + terminate() would be a mid-write kill
   201	            // (brick). The daemon holds the flash uninterruptible on its side, so the flash
   202	            // request is allowed to take as long as it takes and resolves only on the real
   203	            // result (or a genuine daemon exit).
   204	            if let timeout {
   205	                Task { [weak self] in
   206	                    try? await Task.sleep(for: timeout)
   207	                    await self?.timeoutRequest(id: id)
   208	                }
   209	            }
   210	        }
   211	        let env = try JSONDecoder().decode(Envelope<T>.self, from: line)
   212	        if let e = env.error { throw EngineError.daemon(e) }
   213	        guard let r = env.result else { throw EngineError.badResponse }
   214	        return r
   215	    }
   216	
   217	    /// Fail a still-pending request whose deadline passed and terminate the wedged
   218	    /// daemon (the next request respawns it). A no-op if the response already
   219	    /// arrived - route() removed it from pending first, so there is no double-resume.
   220	    private func timeoutRequest(id: Int) {
   221	        guard let c = pending.removeValue(forKey: id) else { return }
   222	        c.resume(throwing: EngineError.daemon("daemon timed out"))
   223	        process?.terminate()
   224	    }
   225	
   226	    private func request<T: Decodable>(_ method: String, timeout: Duration? = .seconds(30), as t: T.Type) async throws -> T {
   227	        try startIfNeeded()
   228	        let id = nextID; nextID += 1
   229	        return try await transact(id: id, JSONEncoder().encode(Req(id: id, method: method)), timeout: timeout, as: t)
   230	    }

2026-07-21T03:39:20.249596Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '500,625p'; rg -n \"def _list_ports|def _open|self\\._port|serial\\.Serial|ChameleonCom|CHAMELEON_PORT|DFU_VID|CHAMELEON_VID\" probe/chameleon_d.py" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   500	class Daemon:
   501	    METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block",
   502	               "decode", "read_ntag", "cancel",
   503	               "slot_set_type", "slot_enable", "slot_clear", "slot_nick", "slot_save",
   504	               "emulate_mode", "emulate_load", "emulate_load_ntag", "emu_read",
   505	               "magic_write", "write_mfd",
   506	               "lf_scan", "lf_write", "lf_emu",
   507	               "dfu_check", "dfu_flash")
   508	
   509	    # Long ops whose cancel window is armed (the flag cleared) at DISPATCH, so a
   510	    # cancel that lands before the worker starts the op still targets it and a stale
   511	    # cancel from a prior op cannot leak in (mirrors x7d). dfu_flash honors the flag
   512	    # only BEFORE the flash write begins (never mid-write - that can brick).
   513	    CANCELLABLE = ("decode", "dfu_flash")
   514	
   515	    # Bounded join for a NON-flash in-flight op on stdin EOF (a flash in flight is joined
   516	    # unbounded instead). A class attribute so tests can shrink it to prove the flash path
   517	    # takes the unbounded branch rather than this one.
   518	    EOF_JOIN_TIMEOUT = 5.0
   519	
   520	    # Settle delay before the LF write read-back verify: a freshly written T5577 needs a
   521	    # moment to re-power and answer, so an immediate re-scan can miss it and report a false
   522	    # unverified. Matches the reference GUI's 500 ms. A class attribute so tests set it to 0.
   523	    LF_SETTLE_SECONDS = 0.5
   524	
   525	    def __init__(self, learned=None, port=None, cracker=crack):
   526	        self.com = None
   527	        self.cmd = None                  # the ChameleonCMD command layer (or a fake)
   528	        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
   529	        # environment so the app can pin a user-chosen serial port when it spawns us. A
   530	        # missing/empty env var leaves _port None (auto-discovery via _find_port()).
   531	        if port is None:
   532	            port = os.environ.get("CHAMELEON_PORT") or None
   533	        self._port = port
   534	        self._reader_mode = None         # cached: True once the device is in reader mode
   535	        self.crack = cracker             # host-side crackers (injectable for tests)
   536	        self._cancel = threading.Event()  # cooperative abort for the long decode
   537	        # Two-stage firmware-flash guard, both preventing a mid-flash teardown (brick):
   538	        # `_flash_pending` is armed at DISPATCH of dfu_flash (before any EOF/timeout could
   539	        # pick the bounded join, closing the dispatch race), and `_flashing` marks the
   540	        # committed, uninterruptible write. Either set => EOF/shutdown join UNBOUNDED and
   541	        # SIGTERM/SIGINT are ignored, so the flasher subprocess is never abandoned.
   542	        self._flash_pending = threading.Event()
   543	        self._flashing = threading.Event()
   544	        self._emit_lock = threading.Lock()  # serialize stdout across worker + reader
   545	        # The protocol channel, captured at construction. __main__ redirects
   546	        # sys.stdout to stderr so vendored-library print() cannot corrupt it.
   547	        self._out = sys.stdout
   548	        # Shared verified-key reranker (injectable for tests). None if unavailable.
   549	        if learned is not None:
   550	            self.learned = learned
   551	        elif LearnedKeyCache is not None:
   552	            self.learned = LearnedKeyCache()
   553	        else:
   554	            self.learned = None
   555	
   556	    # ---- connection --------------------------------------------------------
   557	
   558	    def _looks_like_chameleon(self, p):
   559	        """True if a serial port looks like a Chameleon in normal (CDC) mode. Mirrors the
   560	        official Chameleon GUI's matching so a genuine device is still found when the OS did
   561	        not surface its vid (or a Lite reports differently): match on vid, on the Proxgrind
   562	        manufacturer, or on 'chameleon' in the product/description. A port already in the
   563	        bootloader (DFU vid) is never a normal device, so it is excluded."""
   564	        if getattr(p, "vid", None) == DFU_VID:
   565	            return False
   566	        if getattr(p, "vid", None) == CHAMELEON_VID:
   567	            return True
   568	        if getattr(p, "manufacturer", None) == "Proxgrind":
   569	            return True
   570	        for text in (getattr(p, "product", None), getattr(p, "description", None)):
   571	            if text and "chameleon" in text.lower():
   572	                return True
   573	        return False
   574	
   575	    def _find_port(self):
   576	        """First serial port that looks like a Chameleon in normal (CDC) mode, or None."""
   577	        for p in self._list_ports():
   578	            if self._looks_like_chameleon(p):
   579	                return p.device
   580	        return None
   581	
   582	    def _connect(self, port=None):
   583	        """Open the device once and reuse it. ChameleonCom.open sets 115200 + DTR
   584	        high. Raises when no device is found so `info` returns a clean error
   585	        envelope instead of crashing on a hardware-free machine."""
   586	        if self.cmd is None:
   587	            dev = port or self._port or self._find_port()
   588	            if not dev:
   589	                raise RuntimeError("no Chameleon device found")
   590	            com = ChameleonCom()
   591	            com.open(dev)
   592	            self.com = com
   593	            self.cmd = ChameleonCMD(com)
   594	        return self.cmd
   595	
   596	    def _drop(self):
   597	        """Forget the device handle so the next _connect() re-opens it (called when
   598	        a hardware op fails: the cached handle is dead)."""
   599	        if self.com is not None:
   600	            try:
   601	                self.com.close()
   602	            except Exception:
   603	                pass
   604	        self.com = None
   605	        self.cmd = None
   606	        self._reader_mode = None
   607	
   608	    def _ensure_reader(self, c):
   609	        """Reader ops (poll/decode/mf_read_block) need the device in reader mode; a
   610	        card op issued in tag/emulator mode returns DEVICE_MODE_ERROR (mis-read as
   611	        "no card"). Switch once per connection and cache it. The Lite has no reader
   612	        front-end (set reader mode -> NOT_IMPLEMENTED), surfaced as a clean
   613	        RuntimeError so callers report a reader fault, never a phantom no-card."""
   614	        if self._reader_mode:
   615	            return
   616	        try:
   617	            if not c.is_device_reader_mode():
   618	                c.set_device_reader_mode(True)
   619	        except UnexpectedResponseError as e:
   620	            raise RuntimeError("device has no reader mode (Lite?): %s" % e)
   621	        self._reader_mode = True
   622	
   623	    def emit(self, obj):
   624	        with self._emit_lock:
   625	            self._out.write(json.dumps(obj) + "\n")
7:supports. This daemon holds one vendored ChameleonCom (transport) + ChameleonCMD
37:from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
65:CHAMELEON_VID = 0x6868
72:DFU_VID = 0x1915
528:        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
532:            port = os.environ.get("CHAMELEON_PORT") or None
533:        self._port = port
564:        if getattr(p, "vid", None) == DFU_VID:
566:        if getattr(p, "vid", None) == CHAMELEON_VID:
583:        """Open the device once and reuse it. ChameleonCom.open sets 115200 + DTR
587:            dev = port or self._port or self._find_port()
590:            com = ChameleonCom()
1574:    def _list_ports(self):
1586:                if getattr(p, "vid", None) == DFU_VID and getattr(p, "pid", None) == DFU_PID]
1591:                if getattr(p, "vid", None) == CHAMELEON_VID]
1597:        return serial.Serial(port=port, baudrate=115200)
2552:    # (the vendored ChameleonCom prints on frame errors + verbose logging) goes

2026-07-21T03:39:48.121146Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '1540,1615p'; rg -n \"except .*OSError|_drop\\(\\)|except \\(.*Open|OpenFail|NotOpen|serial\" probe/chameleon_d.py | sed -n '1,180p'; rg -n \"def handle|METHODS|except Exception\" probe/chameleon_d.py | tail -30" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
  1540	    # Popen + a stubbed release fetch); the real flash is hardware-gated by the owner.
  1541	
  1542	    @staticmethod
  1543	    def _dfu_asset_name(model):
  1544	        """The app-only DFU asset for the device model. FAIL-CLOSED: 0 -> Ultra, 1 -> Lite,
  1545	        anything else raises - an unexpected / future / wrong-compatible model must never be
  1546	        coerced to a firmware (e.g. mapping 'not 0' to Lite would flash Lite onto it)."""
  1547	        if model == 0:
  1548	            return "ultra-dfu-app.zip"
  1549	        if model == 1:
  1550	            return "lite-dfu-app.zip"
  1551	        raise RuntimeError("unsupported device model %r - refusing to pick firmware" % model)
  1552	
  1553	    @staticmethod
  1554	    def _norm_model(m):
  1555	        """Normalise a model choice to EXACTLY 0 (Ultra) / 1 (Lite), or None if unspecified.
  1556	        Accepts only the exact int 0/1 or the string 'ultra'/'lite' (or '0'/'1'). Anything
  1557	        else - 2, -1, 0.5, True/False, other strings - RAISES rather than being coerced, so a
  1558	        stray value can never silently pick a firmware (we never guess Ultra vs Lite)."""
  1559	        if m is None:
  1560	            return None
  1561	        if isinstance(m, bool):                  # bool is an int subclass - reject explicitly
  1562	            raise RuntimeError("unknown model %r (expected 'ultra' or 'lite')" % m)
  1563	        if isinstance(m, str):
  1564	            s = m.strip().lower()
  1565	            if s in ("ultra", "0"):
  1566	                return 0
  1567	            if s in ("lite", "1"):
  1568	                return 1
  1569	            raise RuntimeError("unknown model %r (expected 'ultra' or 'lite')" % m)
  1570	        if isinstance(m, int) and m in (0, 1):
  1571	            return m
  1572	        raise RuntimeError("unknown model %r (expected 'ultra' or 'lite')" % m)
  1573	
  1574	    def _list_ports(self):
  1575	        """All serial ports (pyserial is imported lazily so the daemon still loads on a
  1576	        bare interpreter). Overridden in tests."""
  1577	        try:
  1578	            from serial.tools import list_ports
  1579	        except ImportError:
  1580	            return []
  1581	        return list(list_ports.comports())
  1582	
  1583	    def _find_dfu_ports(self):
  1584	        """Every serial port of a device in the bootloader (VID 0x1915 / PID 0x521f)."""
  1585	        return [p.device for p in self._list_ports()
  1586	                if getattr(p, "vid", None) == DFU_VID and getattr(p, "pid", None) == DFU_PID]
  1587	
  1588	    def _find_cdc_ports(self):
  1589	        """Every serial port of a Chameleon in normal (CDC) mode (VID 0x6868)."""
  1590	        return [p.device for p in self._list_ports()
  1591	                if getattr(p, "vid", None) == CHAMELEON_VID]
  1592	
  1593	    def _serial(self, port):
  1594	        """Open a raw pyserial port at 115200 (used only to write the enter-DFU frame).
  1595	        Lazy import + overridable in tests."""
  1596	        import serial
  1597	        return serial.Serial(port=port, baudrate=115200)
  1598	
  1599	    def _send_enter_dfu(self, port):
  1600	        """Reboot the running firmware into the Nordic bootloader: open the normal CDC
  1601	        port, raise DTR, write the exact 10-byte ENTER_BOOTLOADER frame, close (mirrors
  1602	        resource/tools/enter_dfu.py). No response is expected - the device re-enumerates."""
  1603	        s = self._serial(port)
  1604	        try:
  1605	            s.dtr = 1
  1606	            s.timeout = 0
  1607	            s.write(DFU_ENTER_FRAME)
  1608	        finally:
  1609	            s.close()
  1610	
  1611	    def _wait_new_dfu_ports(self, before, timeout=DFU_WAIT_SECONDS, settle=DFU_SETTLE_SECONDS):
  1612	        """Return the set of NEW bootloader ports that appear after the enter-DFU write,
  1613	        relative to the snapshot `before` taken BEFORE the reboot (macOS renames the /dev node
  1614	        on re-enumeration, so identity is 'a port that was not present before'). We do NOT
  1615	        return on the first new port: once one appears at time t, we observe the FULL settle
8:(command layer) and serialises every device op (one device, one command stream).
36:# bare interpreter - serial/colorama/prompt_toolkit are optional in the package.
37:from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
497:_DEAD = (OSError, NotOpenException, OpenFailException)
529:        # environment so the app can pin a user-chosen serial port when it spawns us. A
544:        self._emit_lock = threading.Lock()  # serialize stdout across worker + reader
559:        """True if a serial port looks like a Chameleon in normal (CDC) mode. Mirrors the
576:        """First serial port that looks like a Chameleon in normal (CDC) mode, or None."""
638:        return {"family": family, "model": name, "serial": chip,
657:            self._drop()
1538:    # The port / serial / subprocess / network seams are small overridable methods so
1575:        """All serial ports (pyserial is imported lazily so the daemon still loads on a
1578:            from serial.tools import list_ports
1584:        """Every serial port of a device in the bootloader (VID 0x1915 / PID 0x521f)."""
1589:        """Every serial port of a Chameleon in normal (CDC) mode (VID 0x6868)."""
1593:    def _serial(self, port):
1594:        """Open a raw pyserial port at 115200 (used only to write the enter-DFU frame).
1596:        import serial
1597:        return serial.Serial(port=port, baudrate=115200)
1603:        s = self._serial(port)
1807:        """The full adafruit-nrfutil DFU serial command line: the PATH-independent flasher head
1809:        return self._flasher_head() + ["dfu", "serial",
2002:                self._drop()                         # free the CMD handle so the raw serial can open the port
2476:            self._drop()
2486:        # recovery without killing the daemon; every other request is serialized.
2533:        # handle if the worker actually stopped, so close() cannot race a serial
2546:            self._drop()
2570:    except (ValueError, OSError):            # not main thread / unsupported: best-effort
49:except Exception:                        # pragma: no cover - import guard
57:except Exception:                        # pragma: no cover - import guard
61:except Exception:                        # pragma: no cover - import guard
361:        except Exception:                # a cracker with no available() probe: omit it
501:    METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block",
602:            except Exception:
1861:        except Exception:
1875:                except Exception:
1893:        except Exception:
1909:        except Exception as e:
1962:            except Exception:
2136:            except Exception:
2211:                except Exception:
2250:        except Exception:
2396:            except Exception:
2466:    def handle(self, req):
2469:        if method not in self.METHODS:
2478:        except Exception as e:

2026-07-21T03:39:53.593885Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '630,670p;2450,2500p'; nl -ba app/Sources/Shell/RootView.swift | sed -n '1,125p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   630	    def info(self, p):
   631	        c = self._connect(p.get("port"))
   632	        model = c.get_device_model()             # 0 = Ultra, 1 = Lite
   633	        major, minor = c.get_app_version()
   634	        git = c.get_git_version()
   635	        chip = c.get_device_chip_id()
   636	        family = "chameleon-ultra" if model == 0 else "chameleon-lite"
   637	        name = "Chameleon Ultra" if model == 0 else "Chameleon Lite"
   638	        return {"family": family, "model": name, "serial": chip,
   639	                "hw": "app %d.%d (%s)" % (major, minor, git),
   640	                "capabilities": _capabilities(model, self.crack)}
   641	
   642	    def poll(self, p):
   643	        # `reader` reports whether the Chameleon is connected (vs `present`, a card
   644	        # in the field). No tag -> field is up, so reader:true. A dead port or a
   645	        # device with no reader mode (Lite) -> reader:false. `_ensure_reader` runs
   646	        # first so hf14a_scan cannot fail with DEVICE_MODE_ERROR and be mistaken
   647	        # for a genuine no-card. Errors here are normal, so we answer, not raise.
   648	        try:
   649	            c = self._connect(p.get("port"))
   650	            self._ensure_reader(c)
   651	            tags = c.hf14a_scan()
   652	        except UnexpectedResponseError:
   653	            return {"present": False, "reader": True}     # field up, no tag
   654	        except (RuntimeError, ImportError):
   655	            return {"present": False, "reader": False}    # no device / no reader mode
   656	        except _DEAD:
   657	            self._drop()
   658	            return {"present": False, "reader": False}    # device gone
   659	        if not tags:
   660	            return {"present": False, "reader": True}
   661	        t = tags[0]
   662	        sak = t["sak"]
   663	        atqa = t["atqa"][::-1]                             # wire (LSB-first) -> semantic
   664	        return {"present": True, "reader": True,
   665	                "uid": hx(t["uid"]), "atqa": hx(atqa),
   666	                "sak": sak[0], "ats": (hx(t["ats"]) if t["ats"] else ""),
   667	                "kind": card_kind(sak[0], atqa)}
   668	
   669	    def slots_list(self, p):
   670	        c = self._connect(p.get("port"))
  2450	                cands = []                       # acquisition faulted: treat as a miss
  2451	            key, _kt = self._verify_candidates(c, target_blk, cands, ("A", "B"))
  2452	            if key:
  2453	                attempts += self._absorb_key(c, n, sk, pool, key_bytes, key)
  2454	        return attempts, cancelled
  2455	
  2456	    def cancel(self, p):
  2457	        """Cooperative abort: trip the flag the decode attack loop watches so the
  2458	        shell can stop a long recovery WITHOUT killing the daemon; decode then
  2459	        returns whatever it recovered so far. Handled inline by run() (off the
  2460	        worker) so it lands while decode is still running."""
  2461	        self._cancel.set()
  2462	        return {"cancelled": True}
  2463	
  2464	    # ---- dispatch ----------------------------------------------------------
  2465	
  2466	    def handle(self, req):
  2467	        rid = req.get("id")
  2468	        method = req.get("method")
  2469	        if method not in self.METHODS:
  2470	            return {"id": rid, "error": "unknown method: %r" % method}
  2471	        try:
  2472	            return {"id": rid, "result": getattr(self, method)(req.get("params") or {})}
  2473	        except _DEAD as e:
  2474	            # The port died mid-op: drop the dead handle so the NEXT command
  2475	            # re-opens a fresh one (mirrors x7d dropping on OSError).
  2476	            self._drop()
  2477	            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}
  2478	        except Exception as e:
  2479	            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}
  2480	
  2481	    def run(self, stream=None):
  2482	        # A worker thread runs requests one at a time (the device is a single command
  2483	        # stream, so ops never overlap) while THIS thread keeps reading stdin. A
  2484	        # `cancel` arriving mid-decode is handled inline - off the worker - so it
  2485	        # trips the flag the attack loop watches and the shell can abort a long
  2486	        # recovery without killing the daemon; every other request is serialized.
  2487	        if stream is None:
  2488	            stream = sys.stdin
  2489	        q = queue.Queue()
  2490	
  2491	        def worker():
  2492	            while True:
  2493	                req = q.get()
  2494	                if req is None:
  2495	                    return
  2496	                try:
  2497	                    self.emit(self.handle(req))
  2498	                finally:
  2499	                    # Disarm the flash-pending guard only once the dfu_flash op has fully
  2500	                    # returned (a result, a cancel, or an error) - so from dispatch until
     1	import SwiftUI
     2	import AppKit
     3	import UniformTypeIdentifiers
     4	
     5	/// One unified workspace: the decoded / loaded image is the DOCUMENT on the canvas,
     6	/// the card on the reader is a separate live device; reading / writing / format /
     7	/// save / open are LABELLED actions on a always-visible action bar (so the
     8	/// workflow is discoverable, not hidden behind cryptic toolbar icons). The
     9	/// titlebar is hidden; a custom header carries the brand wordmark + reader
    10	/// status cleanly (no system toolbar wells). Theme + language switch instantly.
    11	struct RootView: View {
    12	    @Environment(AppModel.self) private var model
    13	    @Environment(Theme.self) private var theme
    14	    @Environment(L10n.self) private var l10n
    15	    @Environment(\.colorScheme) private var systemScheme
    16	
    17	    var body: some View {
    18	        @Bindable var model = model
    19	        Workspace()
    20	            .background(WindowConfigurator())
    21	            .preferredColorScheme(theme.appearance == .system ? nil : theme.scheme)
    22	            .onAppear {
    23	                theme.systemScheme = systemScheme
    24	                l10n.systemCode = Locale.current.language.languageCode?.identifier ?? "en"
    25	                // Refuse app quit while a firmware flash is writing (a mid-write kill can
    26	                // brick the device); warn and keep the update running.
    27	                AppDelegate.terminationGuard = {
    28	                    guard model.flashing else { return .terminateNow }
    29	                    let alert = NSAlert()
    30	                    alert.messageText = l10n.t("quit_while_flashing_title")
    31	                    alert.informativeText = l10n.t("quit_while_flashing_msg")
    32	                    alert.addButton(withTitle: l10n.t("keep_updating"))
    33	                    alert.runModal()
    34	                    return .terminateCancel
    35	                }
    36	            }
    37	            .onChange(of: systemScheme) { _, s in
    38	                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
    39	            }
    40	            .sheet(isPresented: $model.cloneSheet) {
    41	                CloneSheet().environment(model).environment(theme).environment(l10n)
    42	            }
    43	            .sheet(isPresented: $model.flashingSheet, onDismiss: { model.clearFlashState() }) {
    44	                FlashingView().environment(model).environment(theme).environment(l10n)
    45	            }
    46	            .confirmationDialog(l10n.t("format_q"), isPresented: $model.formatConfirm, titleVisibility: .visible) {
    47	                // Pinned to the uid snapshot taken when the dialog opened, so a card
    48	                // swapped in while it is open is never the one wiped.
    49	                Button(l10n.t("format"), role: .destructive) {
    50	                    Task { await model.format(authorizedUID: model.pendingFormatUID) }
    51	                }
    52	                Button(l10n.t("cancel"), role: .cancel) {}
    53	            } message: {
    54	                Text(l10n.t("format_msg") + (model.pendingFormatUID.map { "\n\n\(l10n.t("card_on_reader")): \($0)" } ?? ""))
    55	            }
    56	            .task { await model.connect(); await model.monitor() }
    57	    }
    58	}
    59	
    60	private struct Workspace: View {
    61	    @Environment(AppModel.self) private var model
    62	    @Environment(Theme.self) private var theme
    63	
    64	    var body: some View {
    65	        VStack(spacing: 0) {
    66	            HeaderBar()
    67	            Rectangle().fill(theme.p.hairline).frame(height: 1)
    68	            ActionBar()
    69	            Rectangle().fill(theme.p.hairline).frame(height: 1)
    70	            ErrorBanner()
    71	            HStack(spacing: 0) {
    72	                VStack(spacing: 0) {
    73	                    CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
    74	                    if model.apduOpen {
    75	                        Rectangle().fill(theme.p.hairline).frame(height: 1)
    76	                        ApduConsole()
    77	                    }
    78	                }
    79	                .frame(maxWidth: .infinity, maxHeight: .infinity)
    80	                if model.inspectorOpen {
    81	                    Rectangle().fill(theme.p.hairline).frame(width: 1)
    82	                    SectorInspector().frame(width: 300)
    83	                }
    84	            }
    85	        }
    86	        .background(theme.p.canvas)
    87	        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
    88	            guard let provider = providers.first else { return false }
    89	            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
    90	                let url = (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) } ?? (item as? URL)
    91	                guard let url else { return }
    92	                Task { @MainActor in model.loadDump(from: url) }
    93	            }
    94	            return true
    95	        }
    96	    }
    97	}
    98	
    99	/// A dismissible status line for the last operation error (a clone that hit the
   100	/// wrong card, a write that failed, a decode that was interrupted). Without it those
   101	/// failures were silent - the model recorded them but nothing was ever shown. Glyph +
   102	/// typography carry the signal (instrument discipline: no alarm colour).
   103	private struct ErrorBanner: View {
   104	    @Environment(AppModel.self) private var model
   105	    @Environment(Theme.self) private var theme
   106	    @Environment(L10n.self) private var l
   107	    var body: some View {
   108	        if let err = model.lastError {
   109	            HStack(spacing: 8) {
   110	                Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
   111	                    .foregroundStyle(theme.p.textPrimary)
   112	                Text(err).font(l.sans(11)).foregroundStyle(theme.p.textPrimary).lineLimit(2)
   113	                Spacer()
   114	                Button { withAnimation(.easeInOut(duration: 0.2)) { model.lastError = nil } } label: {
   115	                    Image(systemName: "xmark").font(.system(size: 9))
   116	                }.buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
   117	            }
   118	            .padding(.horizontal, 16).padding(.vertical, 9)
   119	            .background(theme.p.tileFill)
   120	            Rectangle().fill(theme.p.hairline).frame(height: 1)
   121	        }
   122	    }
   123	}
   124	
   125	// MARK: - Header (brand + status + utilities), in content so we control the look

2026-07-21T03:39:58.806265Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "rg -n 'port' app/Sources/Engine app/Sources/AppModel.swift | sed -n '1,220p'; git --no-pager diff --unified=80 -- app/Sources/AppModel.swift app/Sources/Engine/DeviceRegistry.swift app/Sources/Engine/DeviceBridge.swift probe/chameleon_d.py | sed -n '1,1100p'" in /Users/tuan/Claude/Tenor/tenor-rekey
2026-07-21T03:41:09.902236Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
 succeeded in 1748ms:
2026-07-21 10:41:08.322 xcodebuild[41583:3240485]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:41:08.461 xcodebuild[41583:3240484] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-ii7tkQxe' (errno=Operation not permitted)
2026-07-21 10:41:08.915 xcodebuild[41585:3240523]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:41:09.053 xcodebuild[41585:3240522] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/app/Sources/AppModel.swift b/app/Sources/AppModel.swift
index 2940bda..015155c 100644
--- a/app/Sources/AppModel.swift
+++ b/app/Sources/AppModel.swift
@@ -1,347 +1,413 @@
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
 
+    // ---- Connect surface (USB now; Bluetooth is a later pass) --------------
+    /// The Connect popover (device list + rescan + manual serial connect) is open.
+    var showConnect = false
+    /// Known readers currently present on the USB bus (the detected-devices list).
+    var detectedDevices: [DeviceDescriptor] = []
+    /// Every enumerated USB serial port (the manual-connect list).
+    var serialPorts: [SerialPortInfo] = []
+
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
 
     /// A device op already owns the reader. Reconnect / swap must not replace the
     /// bridge under one, and a second op must not start while one runs. Slot ops are
     /// included so a slot edit and a decode / clone can never overlap on the reader.
     private var deviceBusy: Bool { decoding || cloning || formatting || apduBusy || slotBusy || flashing || lfBusy }
 
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
 
+    /// The family of the device currently driven, so the Connect surface can mark the
+    /// active row. A manual pin keeps its base family (chameleon-ultra), so matching a
+    /// detected row by family lights the right one in every case.
+    var activeDeviceFamily: String { descriptor.family }
+
+    /// A device swap / (re)connect is in flight, exposed read-only so the Connect
+    /// surface can show a spinner and disable Rescan while it runs.
+    var connecting: Bool { swapping }
+
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
+        await detectAndOpen()
+    }
+
+    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
+    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
+    /// is torn down, and bring the current one up.
+    private func detectAndOpen() async {
         let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
         if bridge != nil, found.id != descriptor.id {
             await swapDevice(to: found)
             return
         }
         descriptor = found
         await openCurrentDevice()
     }
 
+    /// Refresh the Connect surface's lists (known present devices + all USB serial
+    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
+    func refreshConnectLists() {
+        detectedDevices = DeviceRegistry.detectAll()
+        serialPorts = USBProbe.serialPorts()
+    }
+
+    /// The Connect surface's Rescan: refresh the lists and immediately re-run detection
+    /// (the same path as `connect()`), without waiting for the 1.5s monitor tick.
+    func rescan() async {
+        guard !swapping, !deviceBusy else { return }
+        refreshConnectLists()
+        await detectAndOpen()
+        refreshConnectLists()
+    }
+
+    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
+    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
+    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
+    func connectManual(port: String) async {
+        guard !swapping, !deviceBusy else { return }
+        let base = DeviceRegistry.chameleonUltra
+        let manual = DeviceDescriptor(
+            id: "chameleon-manual:\(port)",
+            family: base.family,
+            displayName: base.displayName,
+            daemonScript: base.daemonScript,
+            probeSubdir: base.probeSubdir,
+            usbMatch: base.usbMatch,
+            capabilities: base.capabilities,
+            portOverride: port)
+        await swapDevice(to: manual)
+        refreshConnectLists()
+    }
+
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
             if deviceBusy || swapping { continue }
             // Hot-swap detection runs even while emulating: unplugging an emulating
             // Chameleon and attaching another device must still tear down + swap. It is a
             // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
             // With only the X7 involved, detect() keeps returning the same descriptor, so
             // this path is inert and the poll below is unchanged.
             if let found = DeviceRegistry.detect() {
-                if found.id != descriptor.id { await swapDevice(to: found); continue }
+                // A manually-pinned device (portOverride) is NOT auto-swapped away just
+                // because detect() returns the non-pinned auto variant with a different
+                // id - the user chose this port. If a pinned device truly disappears, the
+                // poll below sees reader:false and applyReaderGone reflects it; the user
+                // can then rescan. Auto descriptors swap on an id change exactly as before.
+                if descriptor.portOverride == nil, found.id != descriptor.id {
+                    await swapDevice(to: found); continue
+                }
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
diff --git a/app/Sources/Engine/DeviceBridge.swift b/app/Sources/Engine/DeviceBridge.swift
index 0201815..7161760 100644
--- a/app/Sources/Engine/DeviceBridge.swift
+++ b/app/Sources/Engine/DeviceBridge.swift
@@ -30,160 +30,170 @@ actor DeviceBridge {
     private let python: URL
     private let workDir: URL
     private let script: URL
     private var process: Process?
     private var stdin: FileHandle?
     private var outReader: FileHandle?
     private var errReader: FileHandle?
     private var nextID = 1
     private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
     private var buffer = Data()
     /// Set for the duration of a streaming op (only one runs at a time, the UI
     /// disables other actions); id-less progress events are routed here.
     private var eventSink: (@Sendable (EngineEvent) -> Void)?
     /// Bumped each time a streaming op (decode / write) starts. A cancel's hard-kill
     /// fallback captures the generation of the op it is cancelling and terminates only
     /// if THAT same op is still in flight when the grace window elapses, so it can never
     /// kill a later, unrelated op that reused the shared daemon.
     private var opGeneration = 0
 
     init(descriptor: DeviceDescriptor) {
         self.descriptor = descriptor
         let p = Self.resolvePaths(for: descriptor)
         self.python = p.python
         self.workDir = p.workDir
         self.script = p.script
     }
 
     /// Resolve the python interpreter + daemon script, preferring the copies
     /// vendored inside the packaged .app (Contents/Resources/python + /probe), then
     /// environment overrides (X7_PYTHON / X7_PROBE_DIR), then the dev checkout. Both
     /// a shipped app and a dev build work with no configuration. The working dir is
     /// always the probe root so the daemons' shared imports (x7lib, learned_keys,
     /// the vendored chameleon package) resolve regardless of which script is spawned.
     /// libhidapi is found by x7hid itself (a bundle-relative candidate inside the
     /// .app, brew outside), so we never touch the child's environment here.
     static func resolvePaths(for descriptor: DeviceDescriptor) -> (python: URL, workDir: URL, script: URL) {
         let fm = FileManager.default
         let res = Bundle.main.resourceURL ?? Bundle.main.bundleURL
         let bundledPython = res.appendingPathComponent("python/bin/python3")
         let bundledProbe = res.appendingPathComponent("probe")
         let bundledScript = scriptURL(probeRoot: bundledProbe, descriptor: descriptor)
         if fm.fileExists(atPath: bundledPython.path),
            fm.fileExists(atPath: bundledScript.path) {
             return (bundledPython, bundledProbe, bundledScript)
         }
         let env = ProcessInfo.processInfo.environment
         let python = URL(fileURLWithPath: env["X7_PYTHON"] ?? "/usr/bin/python3")
         let probe = URL(fileURLWithPath: env["X7_PROBE_DIR"] ?? Self.devProbeDir)
         return (python, probe, scriptURL(probeRoot: probe, descriptor: descriptor))
     }
 
     /// Last-resort dev-checkout probe dir, computed from THIS source file's location at
     /// compile time (.../app/Sources/Engine/DeviceBridge.swift -> repo root -> /probe) so
     /// a from-source run still finds probe/ without baking in an absolute home path. A
     /// shipped app never reaches this (the bundled Resources copy wins first), nor does a
     /// run that sets X7_PROBE_DIR.
     private static var devProbeDir: String {
         URL(fileURLWithPath: #filePath)   // .../app/Sources/Engine/DeviceBridge.swift
             .deletingLastPathComponent()  // Engine
             .deletingLastPathComponent()  // Sources
             .deletingLastPathComponent()  // app
             .deletingLastPathComponent()  // repo root
             .appendingPathComponent("probe").path
     }
 
     private static func scriptURL(probeRoot: URL, descriptor: DeviceDescriptor) -> URL {
         let dir = descriptor.probeSubdir.map { probeRoot.appendingPathComponent($0) } ?? probeRoot
         return dir.appendingPathComponent(descriptor.daemonScript)
     }
 
     private func startIfNeeded() throws {
         guard process == nil else { return }
         let p = Process()
         p.executableURL = python
         // -B: never write .pyc into the bundle (a code-signed .app that mutates
         // itself breaks its own seal). Passed as a flag, not an env var, so we
         // leave the inherited launchd environment untouched - replacing it broke
         // the spawn under the GUI session.
         p.arguments = ["-B", script.path]
         p.currentDirectoryURL = workDir
+        // Pin the user-chosen serial port when this descriptor carries one (a manual
+        // Connect): the daemon honours CHAMELEON_PORT. We COPY the inherited launchd
+        // environment and add only that one key - never replace the child environment
+        // (a bare replacement broke the spawn under the GUI session), so libhidapi /
+        // PATH and the rest are preserved. Auto-detect descriptors leave it untouched.
+        if let port = descriptor.portOverride {
+            var env = ProcessInfo.processInfo.environment
+            env["CHAMELEON_PORT"] = port
+            p.environment = env
+        }
         let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
         p.standardInput = inPipe
         p.standardOutput = outPipe
         p.standardError = errPipe
         outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
             let d = h.availableData
             guard !d.isEmpty, let self else { return }
             Task { await self.ingest(d) }
         }
         // Drain the daemon's stderr. Without this the OS pipe buffer (~64KB) fills
         // the first time the engine prints a traceback or a flood of warnings, and
         // the daemon then BLOCKS on its next stderr write, hanging every request.
         // We forward it to the app's own stderr so it is visible in Console / a
         // terminal launch for diagnosis.
         errPipe.fileHandleForReading.readabilityHandler = { h in
             let d = h.availableData
             if !d.isEmpty { FileHandle.standardError.write(d) }
         }
         p.terminationHandler = { [weak self] _ in
             guard let self else { return }
             Task { await self.died() }
         }
         try p.run()
         process = p
         stdin = inPipe.fileHandleForWriting
         outReader = outPipe.fileHandleForReading
         errReader = errPipe.fileHandleForReading
     }
 
     private func died() {
         outReader?.readabilityHandler = nil
         errReader?.readabilityHandler = nil
         outReader = nil
         errReader = nil
         process = nil
         stdin = nil
         for (_, c) in pending { c.resume(throwing: EngineError.daemon("daemon exited")) }
         pending.removeAll()
         buffer.removeAll()
     }
 
     private func ingest(_ d: Data) {
         buffer.append(d)
         while let nl = buffer.firstIndex(of: 0x0A) {
             let line = Data(buffer[buffer.startIndex..<nl])
             buffer.removeSubrange(buffer.startIndex...nl)
             route(line)
         }
     }
 
     private func route(_ line: Data) {
         guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { return }
         if obj["event"] != nil {
             if let ev = try? JSONDecoder().decode(EngineEvent.self, from: line) { eventSink?(ev) }
             return
         }
         guard let id = obj["id"] as? Int else {
             // An id-less line (the daemon's bad-json reply): if exactly one request
             // is outstanding, fail it rather than orphan its continuation.
             if let err = obj["error"] as? String, pending.count == 1,
                let only = pending.keys.first, let c = pending.removeValue(forKey: only) {
                 c.resume(throwing: EngineError.daemon(err))
             }
             return
         }
         guard let c = pending.removeValue(forKey: id) else { return }
         c.resume(returning: line)
     }
 
     private func transact<T: Decodable>(id: Int, _ reqData: Data, timeout: Duration?, as _: T.Type) async throws -> T {
         let line: Data = try await withCheckedThrowingContinuation { cont in
             pending[id] = cont
             try? stdin?.write(contentsOf: reqData)
             try? stdin?.write(contentsOf: Data([0x0A]))
             // Arm a deadline: a daemon that is alive but WEDGED (stuck on a hardware
             // read that never returns) would otherwise orphan this continuation
             // forever - freezing the live-status poll and every later op. On the
             // deadline we fail this request and kill the daemon so it respawns.
             //
             // A nil timeout means NO deadline: this is used ONLY for the firmware flash,
diff --git a/app/Sources/Engine/DeviceRegistry.swift b/app/Sources/Engine/DeviceRegistry.swift
index 9694e0f..8b7d45a 100644
--- a/app/Sources/Engine/DeviceRegistry.swift
+++ b/app/Sources/Engine/DeviceRegistry.swift
@@ -1,141 +1,248 @@
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
+    /// A serial port to pin explicitly (a manual Connect to a chosen /dev/cu.* path):
+    /// the daemon is spawned with CHAMELEON_PORT set to this. nil = auto-detect the
+    /// port as before. A distinct `id` per pinned port makes the swap logic treat it
+    /// as a different device and re-open the daemon.
+    var portOverride: String? = nil
 }
 
 /// How to recognise a device on the USB bus. `pid == nil` matches any product id
 /// under the vendor (the Chameleon re-enumerates to a different pid in bootloader,
 /// so it is matched by vendor alone).
 struct USBMatch: Equatable, Sendable {
     enum Transport: Sendable { case hid, serial }
     let vid: Int
     let pid: Int?
     let transport: Transport
+    /// Optional string matchers for a serial device whose vid can vary (a Chameleon
+    /// clone / re-enumeration). Matched against the USB ancestor's "USB Vendor Name" /
+    /// "USB Product Name", in ADDITION to the vid/pid check, so a genuine Chameleon is
+    /// still recognised when only its reported strings identify it.
+    var vendorName: String? = nil
+    var productContains: String? = nil
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
-        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial),
+        usbMatch: USBMatch(vid: 0x6868, pid: nil, transport: .serial,
+                           vendorName: "Proxgrind", productContains: "chameleon"),
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
+
+    /// Every present known device (not just the first), in the same priority order.
+    /// Feeds the Connect surface's detected-devices list.
+    static func detectAll() -> [DeviceDescriptor] {
+        all.filter { USBProbe.isPresent($0.usbMatch) }
+    }
+}
+
+/// One enumerated USB serial (CDC) port, for the Connect surface's manual-connect list.
+/// Carries the callout path plus the owning USB device's vid/pid/strings so the UI can
+/// hint which port is likely a Chameleon (and which is a device in DFU).
+struct SerialPortInfo: Identifiable, Equatable, Sendable {
+    let path: String
+    let vid: Int?
+    let pid: Int?
+    let vendorName: String?
+    let productName: String?
+    var id: String { path }
+    /// Likely a Chameleon: its vid, its reported vendor name, or a product name that
+    /// contains "chameleon" (case-insensitive), covering clones / re-enumerations.
+    var isChameleon: Bool {
+        vid == 0x6868
+            || vendorName?.caseInsensitiveCompare("Proxgrind") == .orderedSame
+            || (productName?.range(of: "chameleon", options: .caseInsensitive) != nil)
+    }
+    /// A device sitting in the Nordic bootloader (re-enumerated to VID 0x1915): shown
+    /// but not manually connectable (it has no serial command interface, only flash).
+    var isDFU: Bool { vid == 0x1915 }
 }
 
 /// Point-in-time USB presence checks over IOKit. HID devices (the X7) are found via
 /// IOHIDManager; CDC serial devices (the Chameleon) via the serial BSD service,
 /// walking up to the owning USB device node to read its vendor / product id.
 enum USBProbe {
     static func isPresent(_ m: USBMatch) -> Bool {
         switch m.transport {
         case .hid: return hidPresent(vid: m.vid, pid: m.pid)
-        case .serial: return serialPresent(vid: m.vid, pid: m.pid)
+        case .serial: return serialPresent(m)
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
 
-    private static func serialPresent(vid: Int, pid: Int?) -> Bool {
+    private static func serialPresent(_ m: USBMatch) -> Bool {
         guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return false }
         var iterator: io_iterator_t = 0
         guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
             return false
         }
         defer { IOObjectRelease(iterator) }
         var found = false
         var service = IOIteratorNext(iterator)
         while service != 0 {
-            if usbAncestorMatches(service, vid: vid, pid: pid) { found = true }
+            if usbAncestorMatches(service, m) { found = true }
             IOObjectRelease(service)
             if found { break }
             service = IOIteratorNext(iterator)
         }
         return found
     }
 
     /// Walk up the IOService plane from a leaf (the serial client) to the USB device
-    /// node that carries `idVendor` / `idProduct`, matching there. Bounded so a
-    /// malformed registry can never loop.
-    private static func usbAncestorMatches(_ service: io_object_t, vid: Int, pid: Int?) -> Bool {
+    /// node that carries `idVendor` / `idProduct`, matching there. As well as the
+    /// vid/pid check, an ancestor whose "USB Vendor Name" equals the match's
+    /// `vendorName`, or whose "USB Product Name" contains `productContains`
+    /// (case-insensitive), also matches - so a Chameleon is recognised even when only
+    /// its reported strings identify it. Bounded so a malformed registry can never loop.
+    private static func usbAncestorMatches(_ service: io_object_t, _ m: USBMatch) -> Bool {
         var node = service
         IOObjectRetain(node)
         defer { IOObjectRelease(node) }
         for _ in 0..<10 {
-            if intProperty(node, "idVendor") == vid {
-                if pid == nil || intProperty(node, "idProduct") == pid { return true }
+            if intProperty(node, "idVendor") == m.vid {
+                if m.pid == nil || intProperty(node, "idProduct") == m.pid { return true }
             }
+            if let want = m.vendorName,
+               let got = stringProperty(node, "USB Vendor Name"),
+               got.caseInsensitiveCompare(want) == .orderedSame { return true }
+            if let want = m.productContains,
+               let got = stringProperty(node, "USB Product Name"),
+               got.range(of: want, options: .caseInsensitive) != nil { return true }
             var parent: io_registry_entry_t = 0
             let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
             guard kr == KERN_SUCCESS, parent != 0 else { return false }
             IOObjectRelease(node)
             node = parent
         }
         return false
     }
 
+    /// Enumerate every USB serial (CDC) callout port for the Connect surface. Reads
+    /// each service's "IOCalloutDevice" as the path and walks to the owning USB device
+    /// for its vid/pid/vendor/product. Ports with NO USB ancestor carrying an idVendor
+    /// (pure-Bluetooth / virtual ports like /dev/cu.Bluetooth-Incoming-Port) are
+    /// skipped. Chameleon / DFU ports sort first, then by path.
+    static func serialPorts() -> [SerialPortInfo] {
+        guard let matching = IOServiceMatching(kIOSerialBSDServiceValue) else { return [] }
+        var iterator: io_iterator_t = 0
+        guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else {
+            return []
+        }
+        defer { IOObjectRelease(iterator) }
+        var ports: [SerialPortInfo] = []
+        var service = IOIteratorNext(iterator)
+        while service != 0 {
+            if let path = stringProperty(service, "IOCalloutDevice"),
+               let a = usbAncestorInfo(service), let vid = a.vid {
+                ports.append(SerialPortInfo(path: path, vid: vid, pid: a.pid,
+                                            vendorName: a.vendorName, productName: a.productName))
+            }
+            IOObjectRelease(service)
+            service = IOIteratorNext(iterator)
+        }
+        return ports.sorted { a, b in
+            let ra = a.isChameleon || a.isDFU, rb = b.isChameleon || b.isDFU
+            if ra != rb { return ra }
+            return a.path < b.path
+        }
+    }
+
+    /// vid/pid + vendor/product strings of the first USB ancestor carrying an idVendor,
+    /// or nil when the leaf has no USB device above it. Bounded 10-hop walk.
+    private static func usbAncestorInfo(_ service: io_object_t) -> (vid: Int?, pid: Int?, vendorName: String?, productName: String?)? {
+        var node = service
+        IOObjectRetain(node)
+        defer { IOObjectRelease(node) }
+        for _ in 0..<10 {
+            if let vid = intProperty(node, "idVendor") {
+                return (vid, intProperty(node, "idProduct"),
+                        stringProperty(node, "USB Vendor Name"),
+                        stringProperty(node, "USB Product Name"))
+            }
+            var parent: io_registry_entry_t = 0
+            let kr = IORegistryEntryGetParentEntry(node, kIOServicePlane, &parent)
+            guard kr == KERN_SUCCESS, parent != 0 else { return nil }
+            IOObjectRelease(node)
+            node = parent
+        }
+        return nil
+    }
+
     private static func intProperty(_ entry: io_registry_entry_t, _ key: String) -> Int? {
         guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
             return nil
         }
         return (cf.takeRetainedValue() as? NSNumber)?.intValue
     }
+
+    private static func stringProperty(_ entry: io_registry_entry_t, _ key: String) -> String? {
+        guard let cf = IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0) else {
+            return nil
+        }
+        return cf.takeRetainedValue() as? String
+    }
 }
diff --git a/probe/chameleon_d.py b/probe/chameleon_d.py
index 172a759..2874f80 100644
--- a/probe/chameleon_d.py
+++ b/probe/chameleon_d.py
@@ -448,193 +448,211 @@ def _validate_init_packet(dat, bin_data):
     Raises RuntimeError with a clear reason on anything else; returns {'hash_type', 'fw_type',
     'sd_size', 'bl_size'} on success. NEVER accepts a full / mismatched / untyped image."""
     try:
         _, signed = _pb_single(dat, 2)             # Packet.signed_command
         if signed is None:
             raise RuntimeError("firmware package is not an official signed release "
                                "(no signed init packet)")
         _, command = _pb_single(signed, 1)         # SignedCommand.command
         if command is None:
             raise RuntimeError("firmware init packet has no command")
         _, init = _pb_single(command, 2)           # Command.init
         if init is None:
             raise RuntimeError("firmware package command has no init")
         _, ftype = _pb_single(init, 4)             # InitCommand.type (FwType)
         _, sd_size = _pb_single(init, 5)           # InitCommand.sd_size
         _, bl_size = _pb_single(init, 6)           # InitCommand.bl_size
         _, hashmsg = _pb_single(init, 8)           # InitCommand.hash
         if hashmsg is None:
             raise RuntimeError("firmware init packet carries no image hash")
         _, htype = _pb_single(hashmsg, 1)          # Hash.hash_type
         _, hbytes = _pb_single(hashmsg, 2)         # Hash.hash
     except ValueError as e:                        # duplicate field / truncated / non-protobuf
         raise RuntimeError("firmware init packet is not a valid dfu-cc packet: %s" % e)
     # DECLARED type gate (brick-safety): the type MUST be present AND APPLICATION. A missing
     # type is refused (fail closed - we do not lean on the proto default), as is any nonzero
     # softdevice / bootloader size, so a full package cannot pass by omitting its type.
     if ftype is None:
         raise RuntimeError("firmware init packet declares no image type - refused (brick-safety)")
     if ftype != _FWTYPE_APPLICATION:
         raise RuntimeError("firmware init packet declares a non-application image "
                            "(fw type %r) - full DFU refused (brick-safety)" % ftype)
     if (sd_size or 0) != 0 or (bl_size or 0) != 0:
         raise RuntimeError("firmware init packet declares a softdevice/bootloader image "
                            "(sd_size=%r bl_size=%r) - full DFU refused (brick-safety)"
                            % (sd_size, bl_size))
     algo = _HASH_ALGO.get(htype)
     if algo is None or not hbytes:
         raise RuntimeError("firmware init packet uses an unsupported hash type %r" % htype)
     expected = bytes(reversed(hbytes))             # stored hash is byte-reversed
     actual = hashlib.new(algo, bin_data).digest()
     if expected != actual:
         raise RuntimeError("firmware image hash does not match the init packet")
     return {"hash_type": algo, "fw_type": ftype,
             "sd_size": sd_size or 0, "bl_size": bl_size or 0}
 
 
 # Transport-dead exceptions: like x7d dropping the handle on OSError, drop the
 # Chameleon handle when the port is gone so the next command reconnects cleanly.
 # (TimeoutError from send_cmd_sync is an OSError subclass.)
 _DEAD = (OSError, NotOpenException, OpenFailException)
 
 
 class Daemon:
     METHODS = ("info", "poll", "slots_list", "slot_select", "mf_read_block",
                "decode", "read_ntag", "cancel",
                "slot_set_type", "slot_enable", "slot_clear", "slot_nick", "slot_save",
                "emulate_mode", "emulate_load", "emulate_load_ntag", "emu_read",
                "magic_write", "write_mfd",
                "lf_scan", "lf_write", "lf_emu",
                "dfu_check", "dfu_flash")
 
     # Long ops whose cancel window is armed (the flag cleared) at DISPATCH, so a
     # cancel that lands before the worker starts the op still targets it and a stale
     # cancel from a prior op cannot leak in (mirrors x7d). dfu_flash honors the flag
     # only BEFORE the flash write begins (never mid-write - that can brick).
     CANCELLABLE = ("decode", "dfu_flash")
 
     # Bounded join for a NON-flash in-flight op on stdin EOF (a flash in flight is joined
     # unbounded instead). A class attribute so tests can shrink it to prove the flash path
     # takes the unbounded branch rather than this one.
     EOF_JOIN_TIMEOUT = 5.0
 
     # Settle delay before the LF write read-back verify: a freshly written T5577 needs a
     # moment to re-power and answer, so an immediate re-scan can miss it and report a false
     # unverified. Matches the reference GUI's 500 ms. A class attribute so tests set it to 0.
     LF_SETTLE_SECONDS = 0.5
 
     def __init__(self, learned=None, port=None, cracker=crack):
         self.com = None
         self.cmd = None                  # the ChameleonCMD command layer (or a fake)
+        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
+        # environment so the app can pin a user-chosen serial port when it spawns us. A
+        # missing/empty env var leaves _port None (auto-discovery via _find_port()).
+        if port is None:
+            port = os.environ.get("CHAMELEON_PORT") or None
         self._port = port
         self._reader_mode = None         # cached: True once the device is in reader mode
         self.crack = cracker             # host-side crackers (injectable for tests)
         self._cancel = threading.Event()  # cooperative abort for the long decode
         # Two-stage firmware-flash guard, both preventing a mid-flash teardown (brick):
         # `_flash_pending` is armed at DISPATCH of dfu_flash (before any EOF/timeout could
         # pick the bounded join, closing the dispatch race), and `_flashing` marks the
         # committed, uninterruptible write. Either set => EOF/shutdown join UNBOUNDED and
         # SIGTERM/SIGINT are ignored, so the flasher subprocess is never abandoned.
         self._flash_pending = threading.Event()
         self._flashing = threading.Event()
         self._emit_lock = threading.Lock()  # serialize stdout across worker + reader
         # The protocol channel, captured at construction. __main__ redirects
         # sys.stdout to stderr so vendored-library print() cannot corrupt it.
         self._out = sys.stdout
         # Shared verified-key reranker (injectable for tests). None if unavailable.
         if learned is not None:
             self.learned = learned
         elif LearnedKeyCache is not None:
             self.learned = LearnedKeyCache()
         else:
             self.learned = None
 
     # ---- connection --------------------------------------------------------
 
+    def _looks_like_chameleon(self, p):
+        """True if a serial port looks like a Chameleon in normal (CDC) mode. Mirrors the
+        official Chameleon GUI's matching so a genuine device is still found when the OS did
+        not surface its vid (or a Lite reports differently): match on vid, on the Proxgrind
+        manufacturer, or on 'chameleon' in the product/description. A port already in the
+        bootloader (DFU vid) is never a normal device, so it is excluded."""
+        if getattr(p, "vid", None) == DFU_VID:
+            return False
+        if getattr(p, "vid", None) == CHAMELEON_VID:
+            return True
+        if getattr(p, "manufacturer", None) == "Proxgrind":
+            return True
+        for text in (getattr(p, "product", None), getattr(p, "description", None)):
+            if text and "chameleon" in text.lower():
+                return True
+        return False
+
     def _find_port(self):
-        """First serial port whose USB vendor id is the Chameleon's, or None."""
-        try:
-            from serial.tools import list_ports
-        except ImportError:
-            return None
-        for p in list_ports.comports():
-            if getattr(p, "vid", None) == CHAMELEON_VID:
+        """First serial port that looks like a Chameleon in normal (CDC) mode, or None."""
+        for p in self._list_ports():
+            if self._looks_like_chameleon(p):
                 return p.device
         return None
 
     def _connect(self, port=None):
         """Open the device once and reuse it. ChameleonCom.open sets 115200 + DTR
         high. Raises when no device is found so `info` returns a clean error
         envelope instead of crashing on a hardware-free machine."""
         if self.cmd is None:
             dev = port or self._port or self._find_port()
             if not dev:
                 raise RuntimeError("no Chameleon device found")
             com = ChameleonCom()
             com.open(dev)
             self.com = com
             self.cmd = ChameleonCMD(com)
         return self.cmd
 
     def _drop(self):
         """Forget the device handle so the next _connect() re-opens it (called when
         a hardware op fails: the cached handle is dead)."""
         if self.com is not None:
             try:
                 self.com.close()
             except Exception:
                 pass
         self.com = None
         self.cmd = None
         self._reader_mode = None
 
     def _ensure_reader(self, c):
         """Reader ops (poll/decode/mf_read_block) need the device in reader mode; a
         card op issued in tag/emulator mode returns DEVICE_MODE_ERROR (mis-read as
         "no card"). Switch once per connection and cache it. The Lite has no reader
         front-end (set reader mode -> NOT_IMPLEMENTED), surfaced as a clean
         RuntimeError so callers report a reader fault, never a phantom no-card."""
         if self._reader_mode:
             return
         try:
             if not c.is_device_reader_mode():
                 c.set_device_reader_mode(True)
         except UnexpectedResponseError as e:
             raise RuntimeError("device has no reader mode (Lite?): %s" % e)
         self._reader_mode = True
 
     def emit(self, obj):
         with self._emit_lock:
             self._out.write(json.dumps(obj) + "\n")
             self._out.flush()
 
     # ---- methods -----------------------------------------------------------
 
     def info(self, p):
         c = self._connect(p.get("port"))
         model = c.get_device_model()             # 0 = Ultra, 1 = Lite
         major, minor = c.get_app_version()
         git = c.get_git_version()
         chip = c.get_device_chip_id()
         family = "chameleon-ultra" if model == 0 else "chameleon-lite"
         name = "Chameleon Ultra" if model == 0 else "Chameleon Lite"
         return {"family": family, "model": name, "serial": chip,
                 "hw": "app %d.%d (%s)" % (major, minor, git),
                 "capabilities": _capabilities(model, self.crack)}
 
     def poll(self, p):
         # `reader` reports whether the Chameleon is connected (vs `present`, a card
         # in the field). No tag -> field is up, so reader:true. A dead port or a
         # device with no reader mode (Lite) -> reader:false. `_ensure_reader` runs
         # first so hf14a_scan cannot fail with DEVICE_MODE_ERROR and be mistaken
         # for a genuine no-card. Errors here are normal, so we answer, not raise.
         try:
             c = self._connect(p.get("port"))
             self._ensure_reader(c)
             tags = c.hf14a_scan()
         except UnexpectedResponseError:
             return {"present": False, "reader": True}     # field up, no tag
         except (RuntimeError, ImportError):
             return {"present": False, "reader": False}    # no device / no reader mode
         except _DEAD:
             self._drop()
             return {"present": False, "reader": False}    # device gone

exec
/bin/zsh -lc "nl -ba app/Sources/Engine/ChameleonModels.swift | sed -n '1,95p'; rg -n \"Dfu.*port|port:\" app/Sources/Engine/DeviceBridge.swift app/Sources/AppModel.swift app/Sources/Engine/ChameleonModels.swift" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import Foundation
     2	
     3	/// Decoded shapes for the Chameleon-only daemon methods (probe/chameleon_d.py:
     4	/// slots_list / slot_* / emulate_* / emu_read). The shell only ever asks for these
     5	/// when the connected device advertises the matching capability (slots / emulate),
     6	/// so a plain reader like the X7 never decodes them.
     7	
     8	/// One field (HF or LF) of a slot: its emulated tag type (a TagSpecificType enum
     9	/// name the shell localises), whether it is enabled, and its nickname.
    10	struct SlotSense: Codable, Equatable, Sendable {
    11	    let type: String
    12	    let enabled: Bool
    13	    let nick: String
    14	}
    15	
    16	/// One of the 8 slots: which one, whether it is the active slot, and its HF/LF fields.
    17	struct ChameleonSlot: Codable, Equatable, Sendable, Identifiable {
    18	    let index: Int
    19	    let active: Bool
    20	    let hf: SlotSense
    21	    let lf: SlotSense
    22	    var id: Int { index }
    23	
    24	    /// True when the HF field holds a MIFARE Classic image (readable into the sector
    25	    /// grid via emu_read). Only 1K / 4K are opened; other Classic sizes are rare here.
    26	    var hfIsClassic: Bool { hf.type == "MIFARE_1024" || hf.type == "MIFARE_4096" }
    27	
    28	    /// The emulator geometry (block count + sak) for an HF Classic slot, or nil when
    29	    /// the HF field is not a Classic image the grid can render.
    30	    var hfGeometry: (count: Int, sak: Int)? {
    31	        switch hf.type {
    32	        case "MIFARE_1024": return (64, 0x08)
    33	        case "MIFARE_4096": return (256, 0x18)
    34	        default: return nil
    35	        }
    36	    }
    37	}
    38	
    39	/// slots_list result: the 8-slot library.
    40	struct SlotsResult: Codable, Sendable {
    41	    let slots: [ChameleonSlot]
    42	}
    43	
    44	/// dfu_check result: the running firmware (app version + git) and the newest published
    45	/// release tag, so the flashing view can show "update available". `latest` / `note` are
    46	/// optional - an offline release fetch leaves latest nil and puts the reason in note.
    47	struct DfuStatus: Codable, Sendable, Equatable {
    48	    let model: String
    49	    let current: String        // "major.minor" app version
    50	    let git: String            // git description, e.g. "v2.0.0-3-gdeadbee"
    51	    let asset: String          // the app-only asset picked for this model
    52	    let latest: String?        // newest release tag, or nil if the fetch failed
    53	    let updateAvailable: Bool
    54	    let note: String?          // why the release fetch failed (offline), if any
    55	}
    56	
    57	/// dfu_flash result: whether the image was flashed (or the op was cancelled before any
    58	/// write). `tag` is the release flashed when the source was "latest".
    59	struct DfuFlashResult: Codable, Sendable {
    60	    let flashed: Bool
    61	    let tag: String?
    62	    let port: String?
    63	    let hash: String?
    64	    let cancelled: Bool?
    65	}
    66	
    67	/// emu_read result: the active slot's HF emulator memory as a block-index -> hex map.
    68	struct EmuReadResult: Codable, Sendable {
    69	    let blocks: [String: String]
    70	    let count: Int
    71	}
    72	
    73	/// slot_nick result (get or set): the resolved nickname.
    74	struct SlotNickResult: Codable, Sendable {
    75	    let slot: Int
    76	    let sense: String
    77	    let nick: String
    78	}
    79	
    80	/// A selectable emulated tag type for the slot library's type picker. `name` is the
    81	/// TagSpecificType enum name the daemon accepts; `label` is the shown text (kept
    82	/// verbatim, not localised - these are product type names); `sense` is the slot field
    83	/// the type lives in ("hf" / "lf"), so the picker wires enable/emulate to the right one.
    84	struct SlotTagType: Identifiable, Equatable {
    85	    let name: String
    86	    let label: String
    87	    var sense: String = "hf"
    88	    var id: String { name }
    89	
    90	    /// The HF types offered in the slot library's type picker.
    91	    static let hf: [SlotTagType] = [
    92	        SlotTagType(name: "MIFARE_1024", label: "MIFARE Classic 1K"),
    93	        SlotTagType(name: "MIFARE_4096", label: "MIFARE Classic 4K"),
    94	        SlotTagType(name: "MIFARE_Mini", label: "MIFARE Mini"),
    95	        SlotTagType(name: "NTAG_213", label: "NTAG 213"),
app/Sources/Engine/ChameleonModels.swift:62:    let port: String?
app/Sources/AppModel.swift:253:    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
app/Sources/AppModel.swift:256:    func connectManual(port: String) async {

2026-07-21T03:41:15.119222Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "rg -n -i \"connect|serial|usb|bluetooth|detect|port|hot.swap|rescan|manual\" SPEC_CHAMELEON.md AUDIT_CHAMELEON.md DEVICE_COMPAT.md | sed -n '1,280p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
DEVICE_COMPAT.md:4:Nguồn: disassemble `nfcPro_x64.exe` + `wCopy_2024010501.exe`, GitHub `skylandersNFC/wCopy` + `huntsman95/wCopyRFID`, libnfc #628, manuals + listing thương mại. Research 4-agent 2026-06-19.
DEVICE_COMPAT.md:8:**USB ID KHÔNG map 1-1 với board.** `nfcPro_x64.exe` có hàm probe (`@0x499e0` và `@0x4b460`) gọi `hid_open` lần lượt 4 ID, fallback theo thứ tự. Sau khi mở được, tên board/model đọc RUNTIME qua opcode hãng `FF 00 68` (model string) + `FF 00 69` (serial), rồi đối chiếu bảng `wCopy <BOARD>-<SUFFIX>` nhúng trong binary để gán nhãn + chọn file firmware update đúng.
DEVICE_COMPAT.md:10:Hệ quả: cùng một board vật lý có thể enumerate sau bất kỳ ID nào trong 4 ID, tùy USB-bridge/firmware rev. 4 ID = 2 cặp transport, KHÔNG phải selector.
DEVICE_COMPAT.md:12:## USB ID - ĐÃ CHỐT bằng disasm 2 build nfcPro (19/06)
DEVICE_COMPAT.md:14:Mổ trực tiếp hàm probe (chuỗi `push serial(0xffffffff), push PID, push VID, call hid_open, test eax, jz→ID kế`):
DEVICE_COMPAT.md:32:- `6869:1256` (PN533 + mass-storage, `iProduct=wCopy smart reader`, libnfc #628) là **dòng RIÊNG/đời cũ** - raw PN533 over USB chạy với libnfc, KHÔNG thuộc danh sách nfcPro hiện tại. Red herring cho tương thích nfcPro; chỉ đụng nếu cố ý hỗ trợ đồ cổ (transport khác).
DEVICE_COMPAT.md:47:| X7 / X7-P | XIXEI X7 | (B) Handheld màn màu, pin, USB-C; N32G020 + PN533 | **Máy thật**, verified live |
DEVICE_COMPAT.md:51:| NSR122 / NS122 (-BH/-P/-E/-CD/-H) | NS122 / NSR122-H | (A) dongle desktop, có bản Type-C | **Máy thật** (manual "wCopy NSR122-H V601", teardown 6869:1256) |
DEVICE_COMPAT.md:66:2. **Dongle USB không màn (NS106/NS122) - CẦN PC software:** OBO HANDS, YiToo, HFeng, Sonew, LEXI, LIBO, HERNAS/HENA, JASAG, CIFY, KDL, Mickcara, ASHATA + no-name AliExpress.
DEVICE_COMPAT.md:74:| Dual-freq USB (NS106/NSR/WBW) | Full LF | M1 1K/4K, UL, NTAG R/clone | UID/CUID/FUID | dict+nested | vài unit | KHÔNG (trần software) | nếu build 2025-03+ |
DEVICE_COMPAT.md:77:- **Stock nfcPro KHÔNG copy CPU / GDM / GTU.** tenor/rekey port APDU passthrough = vượt trần stock.
DEVICE_COMPAT.md:78:- **FM11RF08S** (Fudan hardened, static nonce + backdoor Quarkslab 2024): nfcPro thêm support 2025-03-11. Đây là card access đời 2024-2025, đáng port nhất.
DEVICE_COMPAT.md:85:3. Đọc model runtime qua `FF 00 68` để gán nhãn/route - không đoán theo USB ID.
SPEC_CHAMELEON.md:19:- KHÔNG fork/port cái Flutter GUI. Vỏ là SwiftUI native.
SPEC_CHAMELEON.md:21:- KHÔNG hỗ trợ BLE giai đoạn đầu. Chameleon nối qua USB-C CDC serial. (BLE là transport thứ 2, để v2.)
SPEC_CHAMELEON.md:60:{ "family":"x7", "model":"XIXEI X7-P", "serial":"...", "hw":"...",
SPEC_CHAMELEON.md:65:{ "family":"chameleon-ultra", "model":"Chameleon Ultra", "serial":"...", "hw":"...",
SPEC_CHAMELEON.md:78:Sibling của `x7d.py`. Ôm một `ChameleonCom` (transport) + `ChameleonCMD` (lệnh) nhập thẳng từ CLI chính chủ (GPLv3, hợp lệ vì rekey đã GPLv3). Không nhập lớp CLI tương tác (`chameleon_cli_*`, prompt_toolkit) - chỉ nhập `chameleon_com`, `chameleon_cmd`, `chameleon_enum`.
SPEC_CHAMELEON.md:80:Transport: pyserial 115200, DTR high. Frame LRC-checked (SOF `0x11`, cmd 2B big-endian, status 2B, len 2B, LRC). `send_cmd_sync` là primitive request/response; daemon serial hóa mọi op (một thiết bị, một luồng lệnh).
SPEC_CHAMELEON.md:130:Chameleon = Nordic nRF52840. Thường: USB CDC `VID 0x6868 / PID 0x8686`. Vào bootloader: re-enumerate thành `VID 0x1915 / PID 0x521f`.
SPEC_CHAMELEON.md:136:Mở port thường 115200 DTR high, ghi 10 byte này, đóng. Fallback tay: tắt máy, giữ nút B khi cắm USB (LED 4+5 nhấp nháy = bootloader).
SPEC_CHAMELEON.md:138:**Flash** = giao thức Nordic Secure DFU trên cùng cổng serial (giờ ở PID 0x521f), 115200. Firmware là gói `ultra-dfu-app.zip` (Nordic DFU package: `application.dat` = init packet protobuf đã ký + `application.bin` = ảnh app), ký bằng `chameleon.pem` - bootloader từ chối gói không đúng chữ ký, nên **chỉ flash được firmware chính chủ**, không flash ảnh tự chế.
SPEC_CHAMELEON.md:146:3. Poll `serial.tools.list_ports` tới khi thấy `1915:521f` (bootloader lên).
SPEC_CHAMELEON.md:147:4. `subprocess` `adafruit-nrfutil dfu serial -pkg app.zip -p <port> -b 115200` (pure-Python, cài qua pip, không cần binary Nordic riêng) HOẶC `nrfutil device program --firmware app.zip --traits nordicDfu`. Parse tiến độ → `progress` events.
SPEC_CHAMELEON.md:149:pip deps thêm: `requests` (đã có pyserial). Cân nhắc bundle `adafruit-nrfutil` vào venv đóng gói.
SPEC_CHAMELEON.md:154:- Xử lý race re-enumerate trên macOS: sau enter-DFU thiết bị đổi VID/PID và đổi `/dev/cu.usbmodem*`; phải poll chờ `1915:521f`, đừng giả định path cổng cố định.
SPEC_CHAMELEON.md:186:| `Engine/X7Engine.swift` | Đổi tên `X7Engine` → `DeviceBridge`. Giữ nguyên phần actor (process, pipe, `transact`/`route`/`timeoutRequest`, event routing) - vốn đã là JSON-RPC transport thuần. `resolvePaths()` nhận **tên script daemon + probe subdir từ `DeviceDescriptor`** thay vì hardcode `x7d.py` (đang cứng ở `:40`, `:55`). Method dùng chung giữ; verb Chameleon-only thêm vào, gated bằng capability. |
SPEC_CHAMELEON.md:187:| `AppModel.swift` | Đang single-document, hardwire `let engine = X7Engine()` (`:47`), hình dạng MIFARE-Classic. Tách thành **shell state chung + model theo thiết bị**: (a) ôm `DeviceBridge` lấy từ registry; (b) publish `capabilities`; (c) tách logic Classic để Chameleon thêm được state thư viện-slot + giả lập. `connect()`/`monitor()` giữ nhưng phải chịu được `info` khác hình dạng. |
SPEC_CHAMELEON.md:193:`Engine/DeviceRegistry.swift` (descriptor + `detect()` enumerate USB), `Engine/DeviceCapabilities.swift`, `Engine/ChameleonModels.swift`, `Views/SlotLibraryView.swift`, `Views/EmulateToggle.swift`, `Views/LFPanel.swift`, `Views/FlashingView.swift`, `probe/chameleon_d.py`.
SPEC_CHAMELEON.md:196:1. `DeviceRegistry.detect()` lúc `AppModel.connect()`: X7 là HID (`2518:6022`, IOKit `IOHIDManager`); Chameleon là USB-CDC serial (`/dev/cu.usbmodem*`, VID `6868`). Match theo VID:PID.
SPEC_CHAMELEON.md:200:5. Hot-swap: `monitor()` (poll 1.5s, `:109`) chạy lại `detect()` khi reader offline → cắm máy khác thì tear down bridge cũ, spawn daemon đúng. Cắm cả hai thì header cho chọn.
SPEC_CHAMELEON.md:213:- Tenor: KHÔNG em-dash (§0), KHÔNG hardcode brand (import `@tenor/brand`), i18n mọi string user-facing.
SPEC_CHAMELEON.md:218:- [ ] Connect + đọc model/serial/firmware/pin khớp GUI.
SPEC_CHAMELEON.md:236:| P0 | `chameleon_d.py`: connect + `info`+capabilities + `slots_list` + `poll` + đọc block + dump. FakeChameleon test. | ~1-1.5 tuần |
AUDIT_CHAMELEON.md:6:- [GUI - Core Pages (Home/Connect, Read Card, Slot Manager, Saved Cards, Write Card)](#)
AUDIT_CHAMELEON.md:15:# GUI - Core Pages (Home/Connect, Read Card, Slot Manager, Saved Cards, Write Card)
AUDIT_CHAMELEON.md:17:I now have comprehensive coverage of all 5 pages plus supporting components. Here is the parity-grade reference.
AUDIT_CHAMELEON.md:21:# ChameleonUltraGUI - Core Pages UX Behavior Contract (Native Port Reference)
AUDIT_CHAMELEON.md:31:| `connector` | `AbstractSerial?` | The transport. Has `.connected`, `.pendingConnection`, `.isDFU`, `.device` (`ChameleonDevice.ultra`/`.lite`), `.connectionType` (`ConnectionType.ble`/`.usb`), `.portName`, `.activeDevicePort`, `.isManualConnectionSupported()` |
AUDIT_CHAMELEON.md:32:| `communicator` | `ChameleonCommunicator?` | The command layer (all `.getSlotTagTypes()` etc calls). Non-null only while connected |
AUDIT_CHAMELEON.md:38:**Key methods:** `changesMade()` → just `notifyListeners()` (`:87`); `disconnect({bool manual=false})` (`:123`) calls `connector.performDisconnect()`, nulls `communicator`+`progress`, notifies; when `manual`, records the port into `_suppressedAutoReconnectPort` so auto-reconnect skips it. `onConnectorStateChanged()` (`:91`) nulls communicator/progress if disconnected then notifies - wired as `connector.connectionStateCallback` (`:209`). Auto-reconnect suppression helpers: `isAutoReconnectSuppressed`, `clearAutoReconnectSuppression`, `syncAutoReconnectSuppression` (`:99-121`).
AUDIT_CHAMELEON.md:44:| 0 | `Icons.home` | `home` | never | Connect / Pending / Home / Flashing (see below) |
AUDIT_CHAMELEON.md:45:| 1 | `Icons.widgets` | `slot_manager` | `!connected` | `SlotManagerPage` |
AUDIT_CHAMELEON.md:47:| 3 | `Icons.sensors` | `read_card` | `!connected` | `ReadCardPage` |
AUDIT_CHAMELEON.md:48:| 4 | `Icons.system_update_alt` | `write_card` | `!connected` | `WriteCardPage` |
AUDIT_CHAMELEON.md:51:| 7 | `Icons.bug_report` | `🐞 debug 🐞` | shown only if `devMode` | `DebugPage` |
AUDIT_CHAMELEON.md:53:**Guard (`main.dart:224-232`):** if `!connected` AND `selectedIndex` is not one of {0,2,5,6,7}, force `selectedIndex=0`. So disconnecting while on Slot/Read/Write kicks you to Home tab. **Index 0 sub-routing (`:236-250`):** if `pendingConnection` → `PendingConnectionPage`; else if `connected` && `isDFU` → `FlashingPage`; else if `connected` → `HomePage`; else → `ConnectPage`.
AUDIT_CHAMELEON.md:55:**Rail visibility:** hidden entirely when in DFU+connected (`main.dart:327`, shows `SizedBox`). Rail `extended` bound to `getSideBarExpanded()`. Auto-expansion (`:212-219`): if `getSideBarAutoExpansion()`, expand when window width ≥600 else collapse. `bottomNavigationBar` = `BottomProgressBar` (`:405-419`): a `LinearProgressIndicator(value: progress)` (grey bg, blue value) shown only when `connected && isDFU`, else `SizedBox`.
AUDIT_CHAMELEON.md:57:**Theme:** Material3, `ColorScheme.fromSeed(getThemeColor())`, light+dark variants, `themeMode` from `getTheme()`. `reassemble()` disconnects on hot-reload (`:160`). `WakelockPlus.toggle(enable: page is FlashingPage)` (`:277`).
AUDIT_CHAMELEON.md:59:**Native-port note:** the "page area" is `Expanded > Container(color: primaryContainer) > page`. Wrap the whole thing in `SafeArea(bottom:true)`.
AUDIT_CHAMELEON.md:63:## 1. HOME / CONNECT cluster (tab index 0)
AUDIT_CHAMELEON.md:67:### 1A. ConnectPage (`connect.dart`, 480 L)
AUDIT_CHAMELEON.md:69:Shown when not connected, not pending. Stateful; constructor param `autoScanInterval` default `Duration(seconds:3)` (`:20`).
AUDIT_CHAMELEON.md:71:**State fields (`:30-38`):** `_devices` (List<Chameleon>), `_scanTimer`, `_error`, `_isLoading`(init true), `_initialScanCompleted`, `_scanInProgress`, `_connectionInProgress`, `_showedPermissionsSnackbar`, `_lastAutoConnectAttemptPort`.
AUDIT_CHAMELEON.md:74:- AppBar title `connect` (`:437`).
AUDIT_CHAMELEON.md:75:- Top-right `IconButton(Icons.refresh)` → `_scanNow(manual:true)` (`:445-448`).
AUDIT_CHAMELEON.md:77:- Each device tile = `ElevatedButton` (rounded 18) containing: top row with `Icons.bluetooth` (if `type==ble`) else `Icons.usb`, the `port` string, and `dfu` label if in DFU; bold "Chameleon {deviceName}" (fontSize 20); device image (`assets/black-ultra-standing-front.webp` for ultra else `black-lite-standing-front.webp`, `errorBuilder`→empty).
AUDIT_CHAMELEON.md:78:- Bottom-right `IconButton(Icons.add)` shown only if `connector.isManualConnectionSupported()` → opens `ManualConnect` dialog (`:455-474`).
AUDIT_CHAMELEON.md:81:**Scanning flow `_scanNow({manual})` (`:135-194`):**
AUDIT_CHAMELEON.md:83:2. Guard: bail if `_scanInProgress` or `!_shouldScan` (`_shouldScan` = mounted && !connectionInProgress && !connected && !pendingConnection, `:55-60`).
AUDIT_CHAMELEON.md:84:3. cancel timer; setState `_scanInProgress=true`, clear error, set `_isLoading=true` only if first scan or manual.
AUDIT_CHAMELEON.md:85:4. `devices = _normalizeDevices(await connector.availableChameleons(false))` - dedup by `port|type|dfu` (`:62-74`).
AUDIT_CHAMELEON.md:86:5. `syncAutoReconnectSuppression(ports)`; reset `_lastAutoConnectAttemptPort` if first-connectable-port changed.
AUDIT_CHAMELEON.md:88:7. `_showPermissionsWarningIfNeeded` then `_maybeAutoConnect`.
AUDIT_CHAMELEON.md:89:8. On error: `connector.performDisconnect()`, setState `_error`.
AUDIT_CHAMELEON.md:94:**Android permissions warning (`:96-133`):** only for `AndroidSerial`. If `devices.isEmpty && !hasAllPermissions` and not already shown, show SnackBar `android_ble_permissions_missing` with Close action. Resets flag otherwise.
AUDIT_CHAMELEON.md:96:**Auto-connect `_maybeAutoConnect` (`:196-222`):** only if `_shouldScan` && `getAutoConnectFirstFoundDevice()`. Pick first non-dfu, non-suppressed device; if none, clear attempt port; skip if already attempted that port; else record port and `_connectToDevice(device, fromAutoConnect:true)`.
AUDIT_CHAMELEON.md:98:**Connect `_connectToDevice(device, {fromAutoConnect}) (`:224-285`):**
AUDIT_CHAMELEON.md:99:1. Bail if `_connectionInProgress`.
AUDIT_CHAMELEON.md:100:2. If `device.dfu`: if not auto → `_showDfuDialog`; return (never auto-connects DFU device).
AUDIT_CHAMELEON.md:101:3. Cancel timer; setState `_connectionInProgress=true`.
AUDIT_CHAMELEON.md:102:4. If `type==ble`: set `pendingConnection=true` + `changesMade()` (this flips tab 0 to PendingConnectionPage).
AUDIT_CHAMELEON.md:103:5. `connected = await connector.connectSpecificDevice(device.port)`.
AUDIT_CHAMELEON.md:104:6. If connected: `pendingConnection=false`, clear suppression, **construct** `appState.communicator = ChameleonCommunicator(log, port: connector)` (`:259`). Else `pendingConnection=false`.
AUDIT_CHAMELEON.md:106:8. catch → `pendingConnection=false`, changesMade, setState `_error`.
AUDIT_CHAMELEON.md:107:9. finally → `_connectionInProgress=false`; if still not connected, `_scheduleNextScan()`.
AUDIT_CHAMELEON.md:113:### 1B. PendingConnectionPage (`pending_connection.dart`, 47 L)
AUDIT_CHAMELEON.md:115:Pure display. AppBar `connect`. Center column: `CircularProgressIndicator`, spacer 25, `connecting_to_ble`, then - only while `!connector.connected` - bold lines `default_ble_password`, `connection_might_take_some_time`, `ble_need_to_remove_pair`. No controls. Exits when `_connectToDevice` sets `pendingConnection=false` (routing then re-evaluates to Home or back to Connect).
AUDIT_CHAMELEON.md:119:Shown when connected, non-DFU. Stateful. State: `selectedSlot=1`, `isLegacyFirmware=false`.
AUDIT_CHAMELEON.md:126:5. `areCapabilitiesSupported()` → bool.
AUDIT_CHAMELEON.md:128:**FutureBuilder states (`:214-231`):** waiting → Scaffold(AppBar `home`) + centered `CircularProgressIndicator`. hasError → `appState.disconnect()` + `ErrorPage`. Else render body.
AUDIT_CHAMELEON.md:137:- Top-right column: `IconButton(Icons.close)` → `disconnect(manual:true)` (`:257-263`); then a row: ellipsized `connector.portName` (fontSize 20), a `Icons.bluetooth`/`Icons.usb` icon by connectionType, the battery tooltip icon.
AUDIT_CHAMELEON.md:142:- **Firmware row** - only if `portName != "Demo"` (`:337-450`): bold `firmware_version: ` + `fwVersion[0]` + `IconButton(Icons.update)` tooltip `check_updates`. Update button flow (`:359-444`): `latestAvailableCommit(device)` (catch→SnackBar `update_error`); `resolveCommit(fwVersion[1])`; if `latestCommit.startsWith(current)` → SnackBar `up_to_date`; else SnackBar `downloading_fw` + `flashFirmware` (catch→`update_error`).
AUDIT_CHAMELEON.md:143:- **Demo banner** - only if `portName == "Demo"` (`:451-478`): rounded container (complementary color), `Icons.error_outline` + `demo_firmware`.
AUDIT_CHAMELEON.md:144:- **Capabilities banner** - if `!areCapabilitiesSupported && portName != "Demo"` (`:479-531`): `error_outline` + `please_update_firmware` + `Update` TextButton → SnackBar `downloading_fw` + `flashFirmware`.
AUDIT_CHAMELEON.md:149:**`areCapabilitiesSupported()` (`:52-78`):** reads `getDeviceCapabilities()`. For ultra AND lite, required capability is `ChameleonCommand.setIdteckEmulatorID.value`. If device is ultra/lite and capability missing → false; any exception → false.
AUDIT_CHAMELEON.md:158:- hasError → `performDisconnect()` + `ErrorPage`.
AUDIT_CHAMELEON.md:184:- If `isMifareClassic(hfInfo.type)` (`:457-472`): if `mfcInfo.ntLevel != null` → `prng_type: {mfClassicGetPrngType(...)}`; if `hasBackdoor != null` → `has_backdoor_support: yes/no`.
AUDIT_CHAMELEON.md:187:- **Read button** (`read`): disabled when `scanInProgress` (small-screen variant only sets scanInProgress). Behavior branches on `connector.device`:
AUDIT_CHAMELEON.md:189:  - `lite` → AlertDialog `no_supported` / bold `lite_no_read` / OK. (Lite cannot read.)
AUDIT_CHAMELEON.md:196:3. If `!detectMf1Support()` → `performMifareUltralightScan` (ultralight path). Else → `performMifareClassicScan`.
AUDIT_CHAMELEON.md:218:- Read → ultra: (small: scanInProgress=true) `readLFInfo()`; lite: `no_supported`/`lite_no_read`; else changesMade.
AUDIT_CHAMELEON.md:254:  - `export_to_dictionary`: `exportFoundKeys()` → `DictionaryExportMenu(keys: validKeys)`.
AUDIT_CHAMELEON.md:255:- **dump / dumpOngoing** (if allowSave, `:330-369`): `dump_card` (same as dump_partial_data flow) + `export_to_dictionary`.
AUDIT_CHAMELEON.md:258:**`saveCard({bin, skipDump})` (`:50-83`):** builds `cardDump = mfClassicGetExportBytes(type, recovery.cardData, isEV1)` unless skipDump. If bin → `FilePicker.saveFile({uid}.bin)`. Else append `CardSave(uid, sak, atqa, name=dumpName, tag = skipDump?mifare1K:mfClassicGetChameleonTagType(type), data=recovery.cardData, ats)`.
AUDIT_CHAMELEON.md:290:**FutureBuilder (`:348-464`):** waiting OR `progress!=-1` → `CircularProgressIndicator`. hasError → `performDisconnect()` + `ErrorPage`. Else → 8-item grid.
AUDIT_CHAMELEON.md:328:**Unsupported (`:312-315`):** log error "Can't write this card type yet." + close.
AUDIT_CHAMELEON.md:340:Opened per-slot from the tile's settings icon. Params `slot:int`, `refresh` callback. State: `enabledSlot`, `slotTypes`, `names`, `exportFrequency`(hf).
AUDIT_CHAMELEON.md:344:FutureBuilder future = `names.hf.isNotEmpty ? Future.value(null) : fetchInfo()` (fetch once). waiting+empty → AlertDialog `slot_settings` + spinner. hasError → performDisconnect + ErrorPage in dialog.
AUDIT_CHAMELEON.md:347:- Title row: `slot_settings` + `IconButton(Icons.download)` → `SlotExportMenu(names, enabledSlotInfo, slotTypes)`; enabled only if `slotTypes.notMatch()` (i.e. slot has data).
AUDIT_CHAMELEON.md:351:`updateSlot(name, frequency, type)` (`:68-80`): writes into `names`/`slotTypes` by frequency, calls `refresh()` + setState. `SlotEditMenu`/`SlotExportMenu` are separate dialogs (`slot/edit.dart`, `slot/export.dart`) - out of the 5-page scope but referenced here.
AUDIT_CHAMELEON.md:365:**`_folderTreeIds(rootId, folders)` (`:180-194`):** transitive closure of descendants (used for cascade delete/export and card counts).
AUDIT_CHAMELEON.md:370:- Actions: `IconButton(Icons.file_upload)` → `importCard()`; `_createMenuButton` (a `MenuAnchor` with `Icons.add`). Menu items: `card` → `_createCard()` (`CardCreateMenu(folderId)`); `folder` → `_editFolder()`.
AUDIT_CHAMELEON.md:371:- Non-compact button bar (`:763-1267`): a full-width `ElevatedButton(Icons.file_upload)` (same import handler) + `_createMenuButton(elevated:true)`.
AUDIT_CHAMELEON.md:374:- **Folder item** → `ElementButton(icon:folder, iconColor:folder.color, firstLine:name, secondLine:folder_card_count(cardCount from subtree), onPressed → setState currentFolderId=folder.id)`. Trailing icon buttons: `move_folder` (`_moveFolder`), `edit_folder` (`_editFolder(folder)`), `export_folder` (`_exportFolder`), `delete_folder` (`_deleteFolder`).
AUDIT_CHAMELEON.md:379:### 4.3 Card import (`:775-1252`) - the multi-format importer
AUDIT_CHAMELEON.md:382:1. If JSON map with `format=='chameleon-ultra-gui-folder'` → `_importFolderSource(string)` (folder bundle import).
AUDIT_CHAMELEON.md:383:2. Else detect by content signatures (`:801-819`): `"Created": "proxmark3"` → `pm3JsonToCardSave`; `Filetype: Flipper NFC device` → `flipperNfcToCardSave`; `+Sector: 0` → `mctToCardSave` (Mifare Classic Tool); `Filetype: Flipper RFID key` → `flipperRfidToCardSave`; else `CardSave.fromJson`. Set name from filename (strip extension), folderId=current, add, `setCards`, changesMade.
AUDIT_CHAMELEON.md:384:3. **catch (binary dump)** (`:830-1250`): `selectedType = getTagTypeByDumpSize(length)`; if unknown → abort. For MifareClassic → derive uid4/uid7/sak/atqa from bytes; for Ultralight → uid7 + atqa 00 44. Show **"correct_tag_data"** dialog with (conditionally) 4-byte UID/SAK/ATQA fields, 7-byte UID/SAK/ATQA fields, name, and a HF `TagType` dropdown; all validated by `validateHex`/`validateName`. Actions: `save_as(x_byte_uid(4))` (if hasUid4Support) and `save_as(x_byte_uid(7))` - each slices `contents` into 16- or 4-byte blocks, validates SAK/ATQA lengths (else error dialog `invalid_input`), builds `CardSave`, sets folderId, adds, persists. Cancel closes.
AUDIT_CHAMELEON.md:391:- `_exportFolder` (`:292-311`): builds `CardFolderBundle(rootFolderId, subtree folders, subtree cards)` → `FilePicker.saveFile({name}.json)`.
AUDIT_CHAMELEON.md:392:- `_importFolderSource` (`:313-349`): remaps all ids to fresh UUIDs, re-parents the root to `currentFolderId`, imports cards with new ids/folderIds; catch → SnackBar `invalid_folder_export`.
AUDIT_CHAMELEON.md:396:Mirror of cards. Header title = `currentDictionaryFolder?.name ?? dictionaries`; extra back-arrow inside a folder. Actions: `IconButton(Icons.upload)` → `importDictionary()`; `_dictionaryCreateMenuButton` (menu: `dictionary`→`_createDictionary()` = `DictionaryEditMenu(new)`; `folder`→`_editDictionaryFolder`).
AUDIT_CHAMELEON.md:399:- Folder → `ElementButton(folder, folder_dictionary_count)`, onPressed sets currentDictionaryFolderId. Trailing: move/edit/export/delete (dictionary-folder variants `:1580-1604`).
AUDIT_CHAMELEON.md:402:**Import (`:1483-1530`):** pickFile → UTF-8 (catch→abort); if JSON `format=='chameleon-ultra-gui-dictionary-folder'` → `_importDictionaryFolderSource`; else `Dictionary.fromString(contents, name)`; folderId=current; abort if no keys; add + persist. Folder ops (`_editDictionaryFolder`, `_pickDictionaryFolderDestination`, `_moveDictionary(Folder)`, `_deleteDictionaryFolder`, `_exportDictionaryFolder`, `_importDictionaryFolderSource`) mirror the card equivalents (`:351-614`).
AUDIT_CHAMELEON.md:418:**`AbstractWriteHelper`** (`helpers/write.dart`): abstract write strategy. `getClassByCardType(type,...)` (`:47-74`) returns: MifareClassic → `BaseMifareClassicWriteHelper(recovery)`; Ultralight → `BaseMifareUltralightWriteHelper`; EM410X/HID/Viking/PAC/IOProx/Idteck → `BaseT55XXCardHelper`; else null (unsupported). API: `name`, `autoDetect`, `isMagic(data)`, `isReady()`, `isCompatible(card)`, `getAvailableMethods()`, `getCardType()`, `writeData(card, update)`, `getWriteWidget(ctx,setState)`, `writeWidgetSupported()`, `getFailedBlocks()`, `reset()`. Equality by `name`.
AUDIT_CHAMELEON.md:425:**Step 1 - `select_magic_card` (`:343-384`):** if `baseHelper != null` → a `Wrap` with `DropdownButton<AbstractWriteHelper>` (value=helper, items from `getAvailableMethods()` labeled via `typeLocalization` map: gen1/gen2/gen3/t55xx, `:289-294`), onChanged sets helper; plus, if `baseHelper.autoDetect`, a `TextButton(auto_detect_magic_card)` → `detectMagicType()`. Else `writing_is_not_yet_supported`.
AUDIT_CHAMELEON.md:426:- `detectMagicType()` (`:63-109`): ensure reader mode; iterate `getAvailableMethods()`, first `magicHelper.isMagic(card)` true → set helper, `getCardType()` (retry once on throw), SnackBar `detected_magic_card_type: {name}`, return. If none → SnackBar `failed_to_detect_magic_card_type`.
AUDIT_CHAMELEON.md:433:- Else if `helper.writeWidgetSupported()` → `helper.getWriteWidget(context, setState)` (helper-specific input UI).
AUDIT_CHAMELEON.md:446:1. If device is **lite** → AlertDialog `no_supported`/`lite_no_read`/OK, return (lite can't write).
AUDIT_CHAMELEON.md:463:                     └─ flags: confirmDelete, autoScan, autoConnect, sidebar...
AUDIT_CHAMELEON.md:467: SAVED CARDS ◀──lists/edits/imports/exports──┘  (view/edit/move/delete/format-convert)
AUDIT_CHAMELEON.md:480:Key invariants for the native port:
AUDIT_CHAMELEON.md:481:1. **`communicator` is the only device I/O surface.** Every device action in Read/Slot/Write/Home routes through `appState.communicator!.<method>`. When disconnected it is null; pages 1/3/4 are rail-disabled and force-routed to Home (`main.dart:224`).
AUDIT_CHAMELEON.md:483:3. **Reader vs emulator mode toggling is stateful and side-effectful.** Reading (`readHFInfo`, `readLFInfo`, Write) ensures `setReaderDeviceMode(true)`; writing to slots ensures `setReaderDeviceMode(false)` first. The native port must serialize these - do not read and emulate concurrently.
AUDIT_CHAMELEON.md:486:6. **Device capability split (ultra vs lite):** Lite refuses all reads/writes with `no_supported`+`lite_no_read` dialog (Read Card ×4 button branches, Write Card step-continue). Ultra performs the operation. Any other device → `changesMade()` no-op.
AUDIT_CHAMELEON.md:488:## 7. Reusable UI primitives to port
AUDIT_CHAMELEON.md:501:**Empty/loading/error summary:** every device-reading page uses `FutureBuilder` → `CircularProgressIndicator` (waiting) → `ErrorPage` + `performDisconnect()`/`disconnect()` (error) → content (done). Read Card additionally shows inline `ErrorMessage(no_card_found)` when `!cardExist`, and Slot Manager overlays an upload `LinearProgressIndicator` while `progress != -1` (which also blocks new uploads). Continuous scans self-terminate after 1 minute or on first successful read.
AUDIT_CHAMELEON.md:503:Localization keys are cited verbatim (e.g. `no_supported`, `lite_no_read`, `save_only_uid`, `otp_magic_warning`, `keep_stable_warning`, `uploading_dump`, `used_slots`, `folder_card_count`) - resolve them from `messages/en.json` / `vi.json` for the port's string table.
AUDIT_CHAMELEON.md:516:All paths are absolute under `/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/`. Line numbers cite the exact source. This is a Flutter/Dart app; every "communicator" call is a serial/BLE command to the ChameleonUltra device - command IDs are in `helpers/definitions.dart` (`enum ChameleonCommand`, lines 5-163). Text shown to the user comes from `AppLocalizations` (i18n keys), so labels are given by key; render the English string.
AUDIT_CHAMELEON.md:523:- `communicator` (`ChameleonCommunicator?`) - device command channel; `null` when disconnected (main.dart:75, cleared on disconnect at :93,:129).
AUDIT_CHAMELEON.md:524:- `connector` - serial/BLE transport; exposes `.device` (`ChameleonDevice.ultra|lite`), `.isDFU`, `.isOpen`, `.connected`, `.connectionType` (`ConnectionType.ble|usb`), `.portName`, `performDisconnect()`, `connectSpecificDevice(port)`, `availableChameleons(bool)`, `connectSpecificDevice`.
AUDIT_CHAMELEON.md:529:- Routing (main.dart:234-274): sidebar index → page. Index 0 special-cases: `pendingConnection`→`PendingConnectionPage`; `connected && isDFU`→`FlashingPage`; `connected`→`HomePage`; else `ConnectPage`. `WakelockPlus` enabled while `page is FlashingPage` (:277).
AUDIT_CHAMELEON.md:530:- `BottomProgressBar` (main.dart:405-419): a `LinearProgressIndicator(value: appState.progress)` shown only while `connected && isDFU`, blue on grey.
AUDIT_CHAMELEON.md:555:Three source files: `helpers/github.dart` (network fetch), `helpers/flash.dart` (orchestration + validation), `bridge/dfu.dart` (Nordic DFU protocol), `gui/page/flashing.dart` (progress screen). Triggers live in `menu/dialogs/chameleon_settings.dart`, `page/home.dart`, `page/connect.dart`, `page/debug.dart`.
AUDIT_CHAMELEON.md:561:1. **Device Settings dialog → "Enter DFU"** (`chameleon_settings.dart:76-90`): `ElevatedButton` icon `Icons.medical_services_outlined`, label key `enter_dfu`. Calls `communicator.enterDFUMode()` then `connector.performDisconnect()`, pops dialog, `changesMade()`. This only *reboots into DFU*; the device reconnects as DFU and `main.dart` then shows `FlashingPage`. It does NOT itself download/flash.
AUDIT_CHAMELEON.md:565:5. **Home page → "please update firmware" banner** (`home.dart:479-531`): shown when `!areCapabilitiesSupported && portName != "Demo"`. A `TextButton` label `update` → SnackBar `downloading_fw` → `flashFirmware(...)`.
AUDIT_CHAMELEON.md:567:   - `connect.dart:322` and `debug.dart:442,453` also call `flashFirmware` (auto-flash on connect if outdated / debug buttons).
AUDIT_CHAMELEON.md:569:The **"Demo" firmware banner** (`home.dart:451-478`): if `portName == "Demo"` show a static `demo_firmware` warning box (no flash).
AUDIT_CHAMELEON.md:572:Shown automatically by `main.dart` when the connected device is in DFU mode (`isDFU`). It is a full-screen `Scaffold`:
AUDIT_CHAMELEON.md:609:5. Compute actual hash of `bin` by `hash.hashType`: `SHA128→sha1`, `SHA256→sha256`, `SHA512→sha512`; else throw `"Unsupported hash type ..."`.
AUDIT_CHAMELEON.md:617:3. If `enterDFU` (default true): `connection?.enterDFUMode()` then `connector?.performDisconnect()`.
AUDIT_CHAMELEON.md:618:4. If `connector.isOpen`: `performDisconnect()`.
AUDIT_CHAMELEON.md:619:5. If Android: `asyncSleep(1000)` (BLE re-enumerates before USB).
AUDIT_CHAMELEON.md:620:6. **Wait loop** (156-159): repeat `asyncSleep(250)` then `chameleons = connector.availableChameleons(true)` until non-empty. (No timeout - blocks until a DFU device appears.)
AUDIT_CHAMELEON.md:621:7. `toFlash = chameleons[0]`. Track `{ble:false, usb:false}`; if two of the same `type` seen → throw `"More than one Chameleon in DFU. Please connect only one at a time"` (167-173).
AUDIT_CHAMELEON.md:622:8. If `toFlash.type == ble`, prefer a non-BLE device if present (175-182) - USB is more reliable.
AUDIT_CHAMELEON.md:623:9. `connector.connectSpecificDevice(chameleons[0].port)`.
AUDIT_CHAMELEON.md:625:11. Build `DFUCommunicator(log, port: connector, viaBLE: toFlash.type==ble)`.
AUDIT_CHAMELEON.md:628:14. Log `"Firmware flashed!"`; `performDisconnect()`; `asyncSleep(500)` (exit DFU); `changesMade()`.
AUDIT_CHAMELEON.md:634:**`enum DFUCommand`** (9-24): createObject(0x01), setPRN(0x02), calcChecSum(0x03), execute(0x04), readError(0x05), readObject(0x06), getSerialMTU(0x07), writeObject(0x08), ping(0x09), getHW(0x0a), response(0x60).
AUDIT_CHAMELEON.md:636:**`enum DFUResponseCode`** (26-47): invalidCode(0), success(1), notSupported(2), invalidParameter(3), insufficientResources(4), invalidObject(5), invalidSignature(6), unsupportedType(7), operationNotPermitted(8), operationFailed(0x0A), extendedError(0x0B). `fromValue` defaults to `invalidCode`.
AUDIT_CHAMELEON.md:638:**SLIP framing** (`class Slip`, 49-121) - used for **serial/USB only, not BLE**:
AUDIT_CHAMELEON.md:646:3. Open serial if needed; `registerCallback(completer.complete)`.
AUDIT_CHAMELEON.md:659:- `getMTU()` (229): `getSerialMTU`; parse u16le; on any error or 0 → `mtu=2051`.
AUDIT_CHAMELEON.md:690:| Language (192-208) | `DropdownButton` | `AppLocalizations.supportedLocales`, shown as `language_name`, value=`locale.toLanguageTag()` | `setLocale(Locale(value))` |
AUDIT_CHAMELEON.md:692:| Auto connect first device (230-248) | `Switch` | bool | `getAutoConnectFirstFoundDevice`/`set...` |
AUDIT_CHAMELEON.md:694:| Export settings (270-359) | `TextButton`+`Icons.upload` | opens dialog `choose_export_method` → **QR** or **JSON file** | see below |
AUDIT_CHAMELEON.md:695:| Import settings (361-445) | `TextButton`+`Icons.download` | dialog `import_settings` → **QR** (mobile only) or **JSON file** | `restoreSettingsFromJson` |
AUDIT_CHAMELEON.md:700:| Emulate device toggle (600-631) | `TextButton` | confirm dialog `emulate_device_confirmation(activate/deactivate)` → `setEmulatedChameleon(!...)`, `connector=null` | Demo/emulated device |
AUDIT_CHAMELEON.md:703:**Export settings dialog** (272-358): dialog title `choose_export_method`, content `choose_export_method_description`, actions `cancel` / `qr_code` / `json_file`.
AUDIT_CHAMELEON.md:707:**Import settings dialog** (364-435): title `import_settings`, actions `cancel`/`qr_code`/`json_file`.
AUDIT_CHAMELEON.md:708:- QR only on Android/iOS; else shows error dialog `qr_code_import_not_supported_description`. Opens `QrCodeImport` (§6.5) returning json string → `restoreSettingsFromJson`.
AUDIT_CHAMELEON.md:711:**About dialog** (447-534): `getFutureData` (43-50) = `(fetchOCnames(), fetchContributors(), PackageInfo.fromPlatform())`. Shows: title `Chameleon Ultra GUI`, `about_text`, `version: <ver> (Build <buildNumber>)`, `developed_by:` + `DeveloperList(developers)` (github.dart:7-50, 7 hardcoded devs), `license: GNU General Public License v3.0`, tappable repo URL, OpenCollective link `thanks_for_support`, OC contributor names, `code_contributors:` + `DeveloperList(contributors)` (fetched via `fetchGitHubContributors`, github.dart:54, excludes `github-actions[bot]`,`ChameleonHelper` + core devs), trademark lines `trademarks_mifare/em/hid`. On snapshot error → `performDisconnect()` + `ErrorPage`.
AUDIT_CHAMELEON.md:725:Loading state shows `AlertDialog(device_settings)` + `CircularProgressIndicator`. Error → `performDisconnect()` + `ErrorPage`.
AUDIT_CHAMELEON.md:758:  - **Clear BLE bonds** button (icon `Icons.settings_bluetooth`, label `clear_ble_bonds`): confirm dialog `clear_ble_bonds_confirmation` (yes/no). Yes → `clearBLEBoundedDevices()` → cmd `bleClearBondedDevices`(1032) `skipReceive:true` (chameleon.dart:995). If current connection is BLE → `performDisconnect()`.
AUDIT_CHAMELEON.md:759:  - **BLE PIN** form (formKey): `TextFormField` maxLength 6, `digitsOnly`, validator `validateBlePin`, label `ble_pin`, hint `enter_pin`. Save → `setBLEConnectKey(text)` → cmd `bleSetConnectKey`(1030) with utf8 bytes (chameleon.dart:1004); `saveSettings()`; pop.
AUDIT_CHAMELEON.md:764:- **Factory reset** (icon `Icons.restore_from_trash_outlined`, label `factory_reset`): pops dialog first, then confirm dialog `factory_reset_confirmation` (yes/no). Yes → `factoryReset()` → cmd `factoryReset`(1020) `skipReceive:true` (WARNING: erases all); `performDisconnect()`; `changesMade()`.
AUDIT_CHAMELEON.md:777:- **MifareClassic only**: `Export to dictionary` button - enabled iff `mfClassicGetKeysFromDump(data).isNotEmpty`; opens `DictionaryExportMenu(keys)` (§7.2).
AUDIT_CHAMELEON.md:831:- **Classic only - VALUE** (`value`): `_showValueBlocks` (1052-1177) - lists detected value blocks (`valueBlockToInt`/`valueBlockAddress`), editable value (−0x80000000..0x7FFFFFFF) + address (0..255), re-encodes via `intToValueBlock`.
AUDIT_CHAMELEON.md:850:### 6.2 QR Code Import (`menu/dialogs/qr/import.dart`, 88 L)
AUDIT_CHAMELEON.md:851:`QrCodeImport` - multi-chunk scanner returning assembled JSON. State: `shasum, qrCodeChunks, resultingJson, currentChunk`. Title `qrCodeImport`. Single action button that:
AUDIT_CHAMELEON.md:854:- Button label: `startScanning` / `finishImport` / `scan_next_qr_code(current+1, total+1)`. Shows a `Icons.check` tooltip `checksumOk` when `sha256(utf8(resultingJson)) == shasum`.
AUDIT_CHAMELEON.md:883:`Mfkey32Menu` - full-page key recovery from on-device detection nonces. Opened from Slot Edit (§4-slot) when detection has nonces.
AUDIT_CHAMELEON.md:884:- `initState` → `getMf1DetectionStatus()` = `(isMf1DetectionMode(), getMf1DetectionCount())`.
AUDIT_CHAMELEON.md:885:- Title literal `Mfkey32`. Body: header `recover_keys_via("Mfkey32")`; a button labeled `recover_keys_nonce(detectionCount)` (disabled if 0 or while loading).
AUDIT_CHAMELEON.md:886:- **`handleMfkeyCalculation`** (58-128): `getMf1DetectionCount()` then `getMf1DetectionResult(count)`; nested loop over uid→block→keyType→nonce pairs (i,j); builds `Mfkey32Dart(uid,nt0,nt1,nr0Enc,ar0Enc,nr1Enc,ar1Enc)`; `recovery.mfkey32(mfkey)`; collects 6-byte keys; de-dups by `Object.hashAll`; displays `block N key K: <hex>` rows (each copyable) + `outputUid`. Progress via `LinearProgressIndicator` (`progress` 0..100).
AUDIT_CHAMELEON.md:887:- After completion `saveKeys=true` → **Save recovered keys** button opens `DictionaryExportMenu(defaultName:outputUid, keys)` (§7.2).
AUDIT_CHAMELEON.md:905:### 7.2 Dictionary Export (`menu/dialogs/dictionary/export.dart`, 242 L)
AUDIT_CHAMELEON.md:906:`DictionaryExportMenu(defaultName, keys)` - used after key recovery (Mfkey32, HF sniff, card view). Title `save_recovered_keys`, content `save_recovered_keys_where`. Three `ElevatedButton` actions:
AUDIT_CHAMELEON.md:919:`ToolsPage` (163 L) is a responsive grid (`AlignedGridView`, 2 cols ≥700px else 1). Tool list (43-70): Dictionary Download, T55XX Password Cleaner (device-required), LF Sniffing, HF Sniffing, Mifare Classic Gen4 (device-required, `onPressed:null` → shows `wip` badge). Each `ElementButton` (icon, name, description) opens its `onPressed` widget via `showDialog`. Device-required tools show a `device_required` badge and are disabled when `!connector.connected`.
AUDIT_CHAMELEON.md:945:**Capability check** (`_loadCapabilities`, 55-77): `getDeviceCapabilities()` contains `ChameleonCommand.lfSniff.value`(3031)? Sets `_capabilitySupported`. Banner (693-745): if no device → `sniff_device_required_hint`; if unsupported → `lf_sniff_firmware_unsupported`.
AUDIT_CHAMELEON.md:949:- **Capture** `FilledButton.icon(Icons.sensors, lf_sniff_capture)` - disabled while capturing/unsupported/disconnected.
AUDIT_CHAMELEON.md:950:- **Save to file** (`Icons.download`) → `_exportCapture` (`.bin` of raw samples).
AUDIT_CHAMELEON.md:954:**Capture** (`_captureLfSamples`, 83-152): validate; `if (!isReaderDeviceMode()) setReaderDeviceMode(true)`; `lfSniff(timeoutMs)` → cmd `lfSniff`(3031) with `[timeoutMs>>8, timeoutMs&0xFF]`, timeout `timeoutMs/1000+2`s (chameleon.dart:694). Response status `0x40`→data, `0x41`→empty, else throw. Empty → `lf_sniff_no_samples`. Else `LfSniffCapture.fromSamples`, decode, status `lf_sniff_capture_done(count)`. Errors: `_isFirmwareUnsupportedError` (contains `0x67`/`0x69`) → mark unsupported; else `_errorMessage`.
AUDIT_CHAMELEON.md:977:4. **Recovery** (825-978): **Recover all** `FilledButton(Icons.key)` (iterates recoverable groups). Per group (`_buildRecoveryGroup`): if not recoverable → `hf_sniff_nonce_single` + mfkey64 command. Else **Recover key** button (`_recoverGroup`, 248-322: tries `mfkey64` first - `Mfkey64Dart(uid,nt,nrEnc,arEnc,atEnc)` - then `mfkey32`; `_kNoKey=0xFFFFFFFFFFFFFFFF` = failure → `hf_sniff_recovery_failed`). On success: shows method + 12-hex key, **Copy key** and **Save recovered keys** (`DictionaryExportMenu`, defaultName `hf-sniff-<uid>`).
AUDIT_CHAMELEON.md:989:### 9.2 Manual Connect (`menu/dialogs/manual_connect.dart`, 66 L)
AUDIT_CHAMELEON.md:990:`ManualConnect` - `AlertDialog(connect_manually)`. A `TextField` (label `port`, hint `port_hint`). Actions `cancel` / `connect`. Connect (48-61): if empty return; `connector.connectSpecificDevice(portText)`; new `ChameleonCommunicator(log, port: connector)`; `connector.pendingConnection = false`; `changesMade()`. Default `type = ChameleonDevice.ultra`.
AUDIT_CHAMELEON.md:999:`SlotSettings(slot, refresh)`. On open `fetchInfo` (34-66): `activateSlot(slot)`; `getSlotTagName(slot, hf/lf)` (empty→`empty`); `getEnabledSlots()[slot]`; `getSlotTagTypes()[slot]`. Loading spinner; error → `performDisconnect()`+`ErrorPage`.
AUDIT_CHAMELEON.md:1000:- Title row `slot_settings` + **Export** `IconButton(Icons.download)` - enabled iff `slotTypes.notMatch()` (slot has data); opens `SlotExportMenu`.
AUDIT_CHAMELEON.md:1009:  - Classic/Ultralight → `mf1GetAntiCollData` (uid/sak/atqa/ats). Classic also: `getMf1EmulatorSettings`, detection count (`getMf1DetectionCount` if enabled), `getMf1PrngType` (nullable). Ultralight also: `mf0EmulatorGetVersionData/SignatureData`, counters (`mf0EmulatorGetCounterData`), `mf0NtagGetEmulatorConfig`, `mf0NtagGetDetectionCount`.
AUDIT_CHAMELEON.md:1011:- **Mifare Classic emulator settings** (460-659): Gen1a (yes/no → `setMf1Gen1aMode`), Gen2 (`setMf1Gen2Mode`), PRNG type (`prng_type_static/weak/hard` → `setMf1PrngType`), Use-from-block anti-coll (`setMf1UseFirstBlockColl`), **Collect nonces (Mfkey32)** detection toggle (`setMf1DetectionStatus`) - if enabled & count>0 shows **Recover keys** button → pushes `Mfkey32Menu`; if count==0 → hint `present_cham_reader_keys`; if disabled → `ena_coll_recover_keys`. Write mode (`normal/decline/deceive/shadow` → `setMf1WriteMode`).
AUDIT_CHAMELEON.md:1012:- **Mifare Ultralight emulator settings** (660-846): Gen2 (`mf0SetMagicMode`), Password detection (`mf0NtagSetDetectionEnable`) - if enabled shows `passwords_detected: N` + **View passwords** (`mf0NtagGetDetectionLog(0)` → read-only dialog `detected_passwords`). Write mode (`mf0NtagSetWriteMode`).
AUDIT_CHAMELEON.md:1016:### 10.3 Slot Export (`menu/dialogs/slot/export.dart`, 338 L)
AUDIT_CHAMELEON.md:1017:`SlotExportMenu(names, enabledSlotInfo, slotTypes)`. `AlertDialog(export_slot_data)`.
AUDIT_CHAMELEON.md:1022:  - **Export to new card** (`export_to_new_card`): name prompt dialog → append new `CardSave` to storage.
AUDIT_CHAMELEON.md:1042:| `setBLEConnectKey` (1004) | bleSetConnectKey(1030) | utf8(pin) | 6 digits |
AUDIT_CHAMELEON.md:1045:| `getDeviceCapabilities` (1128) | getDeviceCapabilities(1035) | - | u16 list of supported cmd ids |
AUDIT_CHAMELEON.md:1050:Firmware-unsupported status bytes surfaced to UI: `0x67`, `0x69` (both sniff tools treat as "firmware doesn't support this").
AUDIT_CHAMELEON.md:1056:1. **DFU is Nordic Secure DFU** over SLIP (USB) or raw (BLE). Reuse exact object-type order: 0x01 init (`application.dat` protobuf), 0x02 image (`application.bin`). Validate signed init + hash-reversed comparison before flashing.
AUDIT_CHAMELEON.md:1057:2. **BLE vs USB divergence**: no SLIP on BLE; 20-byte writes off-macOS; 250ms sleeps on Apple BLE; 50 retries on iOS. USB preferred when both present; reject two same-transport devices in DFU.
AUDIT_CHAMELEON.md:1064:9. **Capability gating**: tools check `getDeviceCapabilities()` for their command id and show a firmware-unsupported banner rather than failing silently.
AUDIT_CHAMELEON.md:1090:| Device command layer | `lib/bridge/chameleon.dart` (`ChameleonCommunicator`, 1339 L) | Frames/deframes the serial protocol; exposes one `Future` method per device opcode. **All hardware I/O funnels through `sendCmd`.** |
AUDIT_CHAMELEON.md:1117:### 1.2 Deframe - `onSerialMessage` (chameleon.dart:67-117)
AUDIT_CHAMELEON.md:1130:1. Opens the serial port + registers `onSerialMessage` callback lazily on first send.
AUDIT_CHAMELEON.md:1156:| bleSetConnectKey | 1030 | bleGetConnectKey | 1031 |
AUDIT_CHAMELEON.md:1162:| **scan14ATag** | 2000 | **mf1SupportDetect** | 2001 |
AUDIT_CHAMELEON.md:1163:| **mf1NTLevelDetect** | 2002 | **mf1StaticNestedAcquire** | 2003 |
AUDIT_CHAMELEON.md:1164:| **mf1DarksideAcquire** | 2004 | **mf1NTDistanceDetect** | 2005 |
AUDIT_CHAMELEON.md:1179:| mf1SetDetectionEnable | 4004 | mf1GetDetectionCount | 4005 |
AUDIT_CHAMELEON.md:1180:| mf1GetDetectionResult | 4006 | mf1GetDetectionStatus | 4007 |
AUDIT_CHAMELEON.md:1202:| `detectMf1Support` :239 | mf1SupportDetect | - | `status==0` |
AUDIT_CHAMELEON.md:1203:| `getMf1NTLevel` :246 | mf1NTLevelDetect | - | `data[0]`: 0=static,1=weak,2=hard,else unknown |
AUDIT_CHAMELEON.md:1205:| `getMf1NTDistance(block,keyType,key)` :285 | mf1NTDistanceDetect | `[keyType, block, ...key(6)]` | `uid=u32(data[0:4])`, `distance=u32(data[4:8])` |
AUDIT_CHAMELEON.md:1280:2. `detectMf1Support()`. If true → `mifareClassicType = mfClassicGetType()`, else log "Not Mifare Classic tag!".
AUDIT_CHAMELEON.md:1299:1. `chunkSize = (connectionType==ble) ? 32 : 64`.
AUDIT_CHAMELEON.md:1307:`recheckKey(key, startingSector)` (recovery.dart:143-170): once any key is found, opportunistically test it on **all remaining (sector,keyType) still `none`** via single `mf1Auth`. This propagates a reused key across the card cheaply before running expensive attacks.
AUDIT_CHAMELEON.md:1343:- `attackType` label from `prng`: static→"Static Nested", weak→"Nested", hard→"Hard Nested", backdoor→`has_backdoor_support`, unknown→"".
AUDIT_CHAMELEON.md:1437:Export helpers (general.dart:309-330): `mfClassicGetExportBlocks` → list of `blockCount` blocks (each 16 B, zero-filled if missing); `mfClassicGetExportBytes` flattens to a contiguous binary dump. `mfClassicGetKeysFromDump` (general.dart:341-353) reads back A=`trailer[0:6]`, B=`trailer[10:16]` per sector.
AUDIT_CHAMELEON.md:1447:### 6.1 Method selection & auto-detect (write/base.dart:36-51)
AUDIT_CHAMELEON.md:1449:`getAvailableMethods()` = `[gen1, gen2, gen3]`. `getAvailableMethodsByPriority()` = **`[gen1, gen3, gen2]`** - auto-detect probes gen1 first, then gen3, then gen2. Each helper's `isMagic()` is the detector.
AUDIT_CHAMELEON.md:1491:Only difference vs Gen2 is block-0 handling. `isMagic` (gen3.dart:19-33): `detectMf1Support()` then raw `[0x30, 0x00]` (read block0); magic iff reply length **18** (16 data + 2 CRC).
AUDIT_CHAMELEON.md:1513:Geometry (ultralight/general.dart): page counts per type (:58-79), password page (:81-102), counters (:104-130). Type detection `mfUltralightGetType` from GET_VERSION byte[6] (:36-51); fallback `mfUltralightType` probes `[0x30, page]` reply lengths (:132-176).
AUDIT_CHAMELEON.md:1525:## 7. Support math (must be bit-exact)
AUDIT_CHAMELEON.md:1531:Serializes nonces for the native hardnested cracker: `[uid(4 BE)][2 pad bytes][ per nonce: nt(4 BE), ntEnc(4 BE), parity(1) ]`. Total `6 + 9*count`.
AUDIT_CHAMELEON.md:1541:Pure-Dart port of Proxmark3 `staticnested_2x1nt_rf08s` (Doegox). Init a 16-bit LFSR table `_iLfsr16/_sLfsr16` once (:392-403). `_computeSeednt16Nt32(nt32,key)` (:415-446) walks the nonce back 14 steps then mixes 48 key bits through substitution tables `a[]`/`b[]` with alternating odd/even nibble handling, stepping the LFSR backward 8 more each round. `filterKeys(keys1,keys2,nt1,nt2)` (:450-488) keeps only keys whose seed-nonces collide across the two nonce sets. `findMatchingKeys(nt1,key1,nt2,keys2)` (:492-506) keeps keys2 entries whose seed matches key1's. Both are run off-thread via the `StaticEncryptedKeysFilterAsync` isolate wrappers (:509-573).
AUDIT_CHAMELEON.md:1571:A single long-lived helper isolate owns all FFI calls (crackers block). Main→helper messages are `*Request{id, payload}`; helper→main `KeyResponse{id, List<int>}`. Each public `Future` (e.g. `nested(NestedDart)`) allocates a request id, stores a `Completer` in `requests[id]`, and posts to the isolate. In the isolate each request type `calloc`s the matching struct, copies fields, calls `_bindings.<fn>`, reads `keyCount.value` u64s (or single return), and sends back. **The daemon can serialize this as a thread pool / subprocess with the same struct layouts.**
AUDIT_CHAMELEON.md:1593:### 9.2 Manual slot edit (slot/edit.dart `save`, :186-269)
AUDIT_CHAMELEON.md:1597:`getMf1EmulatorSettings` (:864-884) decodes `[detection, gen1a, gen2, antiColl, writeMode]` where writeMode byte: 1=denied,2=deceive,3/4=shadow,else normal. Individual setters: `setMf1Gen1aMode`/`setMf1Gen2Mode`/`setMf1UseFirstBlockColl`/`setMf1WriteMode`/`setMf1PrngType`/`setMf1DetectionStatus`. Gen1a/Gen2/anti-collision "magic" emulation flags let the emulated slot itself answer magic-card probes.
AUDIT_CHAMELEON.md:1604:initialize(): reader-mode → detectMf1Support → getType → probe EV1 (auth blk69/keyB/4B791BEA7BCC) → seed EV1 sig keys
AUDIT_CHAMELEON.md:1608:  A. hasBackdoor? → static-encrypted acquire (backdoorInfo); isStaticEncrypted?; prng=NTLevelDetect
AUDIT_CHAMELEON.md:1623:- `.../lib/helpers/mifare_classic/general.dart` - geometry, dictionaries, backdoor detect, static-encrypted filter
AUDIT_CHAMELEON.md:1644:Source: `software/script/` in the ChameleonUltra repo. Everything below is drawn from `chameleon_cli_main.py` (REPL), `chameleon_cli_unit.py` (command tree, 10557 lines), `chameleon_cmd.py` (device command layer), `chameleon_com.py` (serial/TCP transport + framing), `chameleon_enum.py` (all enums), `chameleon_utils.py` (CLITree, arg parser, helpers). File+line citations are to `chameleon_cli_unit.py` unless another file is named.
AUDIT_CHAMELEON.md:1646:Punctuation note for the rebuild spec: this document deliberately uses only plain hyphens (no em/en dashes) per the target project's style lock. The Python source contains em-dashes in some help strings; when you port those strings, normalize them.
AUDIT_CHAMELEON.md:1658:`ChameleonCLI.__init__` creates one `chameleon_com.ChameleonCom()` (the transport, shared across every command via `unit.device_com`).
AUDIT_CHAMELEON.md:1666:Prompt string (`get_prompt`, main:65): `[USB] chameleon --> ` (green "USB") when `device_com.isOpen()`, else `[Offline] chameleon --> ` (red "Offline"). Rendered via `prompt_toolkit.formatted_text.ANSI`.
AUDIT_CHAMELEON.md:1694:### 1.4 Transport & wire framing (`chameleon_com.py`)
AUDIT_CHAMELEON.md:1696:- `open(port)` (com:87): `tcp:host:port` -> TCP socket; otherwise `serial.Serial(port, baudrate=115200)`. Android has no COM support.
AUDIT_CHAMELEON.md:1702:`ChameleonCMD` methods are wrapped with `@expect_response(accepted_statuses)`. The wrapper calls the method, checks `ret.status in accepted`, else raises `UnexpectedResponseError(str(Status(status)))`, and on success returns `ret.parsed` (the decoded payload, not the raw Response). Methods set `resp.parsed` themselves before returning. A few methods (e.g. `mf1_detect_support`, `mf1_get_prng_type`, `hf14a_sniff`, `hf14a_auth_trace`, `hf14a_4_*`) are NOT decorated and return the raw `Response` so callers can branch on `.status`/`.data`.
AUDIT_CHAMELEON.md:1711:| `DeviceRequiredUnit` | 225 | `before_exec`: if `device_com.isOpen()` False, print "Please connect to chameleon device first (use 'hw connect')." and return False. |
AUDIT_CHAMELEON.md:1718:| `MFUAuthArgsUnit` | 422 | Parser: `-k/--key` (4 or 16 byte hex; 16-byte = "Ultralight-C not supported yet" error), `-l` swap endianness. `get_param()` returns `.key` (possibly byte-reversed). |
AUDIT_CHAMELEON.md:1740:| `exit` (`quit`/`q`/`e`) | 933 | RootExit | close port, `sys.exit(996)` |
AUDIT_CHAMELEON.md:1742:| `hw connect [-p PORT]` | 999 | HWConnect | open serial, load capabilities, print model+fw |
AUDIT_CHAMELEON.md:1743:| `hw disconnect` | 1059 | HWDisconnect | close port |
AUDIT_CHAMELEON.md:1749:| `hw factory_reset [--force]` | 7229 | HWFactoryReset | `wipe_fds`, closes port |
AUDIT_CHAMELEON.md:1789:| `hf mf elog [--decrypt]` | 3682 | HFMFELog | mfkey32 detection-log crack |
AUDIT_CHAMELEON.md:1833:1. `nt_level = cmd.mf1_detect_prng()` -> `MF1_DETECT_PRNG (2002)`, prints "NT vulnerable: StaticNested/Nested/HardNested".
AUDIT_CHAMELEON.md:1836:4. Else (Weak): `dist = cmd.mf1_detect_nt_dist(...)` -> `MF1_DETECT_NT_DIST (2005)`; `nts = cmd.mf1_nested_acquire(...)` -> `MF1_NESTED_ACQUIRE (2006)`. Args: `"{uid} {dist}"` then per nt: ` {nt} {nt_enc} {par}`. Tool = `nested`.
AUDIT_CHAMELEON.md:1895:2. `nt_level = mf1_detect_prng()`.
AUDIT_CHAMELEON.md:1911:Args: card-size flags `--mini`/`--1k`(default)/`--2k`/`--4k` (set `maxSectors` const 5/16/32/40), positional `keys` (0+ 12-hex), `--key` (`.key` binary file), `--dic` (`.dic` text file, note: `load_dic_file` at unit.py:85 is a stub that returns keys unchanged - a known no-op), `--export-key`/`--export-dic` (OVERWRITE), `-m/--mask` (20-hex, default all zero, 1 bit per sectorKey = skip).
AUDIT_CHAMELEON.md:1913:`on_exec`: gather keys from args (regex-validated) + `load_key_file`. Build `mask` bytearray, then force-mask sectors beyond `maxSectors` (`mask[i//4] |= 3<<(6-i%4*2)`). `check_keys` iterates keys in chunks of 20: `mf1_check_keys_of_sectors(mask, chunk)`; on each response OR-in the `found` bitmap into mask and accumulate `sectorKeys`; stop early if status != HF_TAG_OK or "All sectorKey is found or masked". Prints elapsed time and a Sec/Blk/keyA/res/keyB/res table. Exports if requested (`.key` = per-sector A||B with 6 zero bytes for unknown; `.dic` = unique found keys uppercase).
AUDIT_CHAMELEON.md:1937:- `hf mf econfig` (3942, SlotIndexArgsAndGo + HF14AAntiColl): reads `hf14a_get_anti_coll_data` (`HF14A_GET_ANTI_COLL_DATA 4018`), validates slot is MFC. Toggles via mutually-exclusive pairs: `--enable/--disable-gen1a` (`MF1_SET_GEN1A_MODE 4011`), `--enable/--disable-gen2` (`4013`), `--enable/--disable-block0` (`MF1_SET_BLOCK_ANTI_COLL_MODE 4015`), `--write MODE` (choices from `MifareClassicWriteMode.list()`; `MF1_SET_WRITE_MODE 4017`), `--enable/--disable-log` (`MF1_SET_DETECTION_ENABLE 4004`), `--enable/--disable_field_off_do_reset` (`MF1_SET_FIELD_OFF_DO_RESET 4038`), plus anticoll `--uid/--atqa/--sak/--ats/--delete-ats`. Reads current config via `mf1_get_emulator_config` (`4009`) and `mf1_get_field_off_do_reset` (`4039`). With no change requested, prints the full settings block (Type/UID/ATQA/SAK/ATS/Gen1A/Gen2/block0/Write mode/Log/FIELD_OFF_DO_RESET). Each toggle prints a yellow "already enabled/disabled" if a no-op.
AUDIT_CHAMELEON.md:1938:- `hf mf elog` (3682): `--decrypt`. Without flag: `mf1_get_detection_count()` (`MF1_GET_DETECTION_COUNT 4005`). With flag: download all logs via `mf1_get_detection_log(index)` (`MF1_GET_DETECTION_LOG 4006`, each record = block/type/is_nested/uid/nt/nr/ar). Group by uid->block->keytype, then `decrypt_by_list` runs `mfkey32v2` over all nonce pairs using a `multiprocessing.Pool(cpu_count())` and an `ItemGenerator` (3627) that lazily enumerates pairs and prunes pairs whose reader-key is already known (via `Crypto1.mfkey32_is_reader_has_key`). Prints running "K records => P/C combinations. N key(s) found".
AUDIT_CHAMELEON.md:1953:`cmd.enter_bootloader()` -> `send_cmd_auto(ENTER_BOOTLOADER=1010, close=True)` (fire-and-forget, closes the port). Prints "Application restarting..." / "Enter success", `time.sleep(0.1)` to let the comm thread flush.
AUDIT_CHAMELEON.md:1965:- `connect [-p PORT]` (999): auto-detect by USB VID `0x6868` (WSL uses PowerShell `Get-PnPDevice` for `VID_6868&PID_8686`); `device_com.open(port)`; load `get_device_capabilities` (`GET_DEVICE_CAPABILITIES 1035`) into `device_com.commands`; print model (`get_device_model 1033`: 0=Ultra,1=Lite) + `get_app_version 1000`.
AUDIT_CHAMELEON.md:1968:- `factory_reset --force` (7229): `wipe_fds` (`WIPE_FDS 1020`, closes port).
AUDIT_CHAMELEON.md:1977:- `info` (1276): `HF14AScan.scan(deep=True)`: adds SAK-guess (`type_id_SAK_dict`, unit.py:58) and, for single MFC tags, `mf1_detect_support` (`MF1_DETECT_SUPPORT 2001`) + `mf1_detect_prng`.
AUDIT_CHAMELEON.md:1991:| `dump [-p -q -f -t -k -l]` | 4641 | start page, qty, file | scans, verifies ATQA `4400`/SAK `00`; autodetects type via GET_VERSION `0x60` + AUTH probe `0x1A` (size_map for ULEV1/NTAG21x/ULC), then reads pages `0x30 i` until stop; writes bin/eml |
AUDIT_CHAMELEON.md:1999:| `econfig [-s ...]` | 5464 | anticoll + magic/write/version/signature/log | `--enable/--disable-uid-magic` (`MF0_NTAG_SET_UID_MAGIC_MODE 4020`), `--write MODE` (`4032`), `--set-version` (`4024`), `--set-signature` (`4026`), `--reset-auth-cnt` (`MF0_NTAG_RESET_AUTH_CNT 4029`), `--enable/--disable-log` (`MF0_NTAG_SET_DETECTION_ENABLE 4033`) |
AUDIT_CHAMELEON.md:2000:| `edetect [-s --count --index]` | 5727 | - | password detection logs: `mf0_ntag_get_detection_count` (`4034`) + `get_detection_log` (`4035`) |
AUDIT_CHAMELEON.md:2024:`lf sniff [--timeout --out --hex]` (7649): `lf_sniff` (`LF_SNIFF 3031`, 125kHz 8us/sample). Stores capture in module global `_last_capture`; prints byte count, range, mean, gap detection, optional hexdump with level bars, optional `--out` binary save.
AUDIT_CHAMELEON.md:2046:Three different formats coexist: `.dic` = one 12-hex key per line (dump/clone/autopwn/fchk-export). `.key` = binary, per-sector KeyA(6)||KeyB(6), unknown = 6 zero bytes (fchk-export, autopwn-save). `hf mf view -k` = ASCII `A:B` colon-separated per line. These are not interchangeable.
AUDIT_CHAMELEON.md:2083:`hardnested_utils` (imported at unit.py:23) provides `evenparity32` and `hardnested_sums` (the set of valid MSB parity sums used to gate acquisition).
AUDIT_CHAMELEON.md:2110:- Transport + framing: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_com.py`
AUDIT_CHAMELEON.md:2131:- **tenor-rekey** (`/Users/tuan/Claude/Tenor/tenor-rekey`) - the native macOS clone tool (SwiftUI GUI `app/`, Python daemon+CLI `probe/`). This is what defines `.mfd`/`.keys.json`/`.dic`/export/learned-cache and the `x7d.py` daemon RPC.
AUDIT_CHAMELEON.md:2176:**Default export filename** (`AppModel.defaultDumpFilename` `app/Sources/AppModel.swift:509-513`): `yyMMdd_tr_<uid-no-spaces-lowercase>.dump` (extension `.dump`, a raw image, chosen to sit next to nfcPro dumps). Note this is a deliberate exception to the CLAUDE.md deliverable naming rule.
AUDIT_CHAMELEON.md:2195:Writers: `CardDump.keysJSON()` `CardDump.swift:52-58` (uses `JSONSerialization` with `.prettyPrinted, .sortedKeys`); Python `save_mfd` `probe/x7tool.py:69-70` (`json.dump(..., indent=1)` - matches the sample's 1-space indent). Keys with value `None`/`NSNull` are emitted for every sector `0..<sectorCount`.
AUDIT_CHAMELEON.md:2221:- GUI import (`KeyStore.importText` `app/Sources/Engine/KeyStore.swift:44-55`): tolerant - splits on newlines, ignores blank + `#` lines, takes first whitespace token per line, validates via `normalized` (12 hex), dedupes, newest-first. Accepts `.dic`/`.keys`/`.txt`.
AUDIT_CHAMELEON.md:2224:## A.5 Saved-card export / import (GUI)
AUDIT_CHAMELEON.md:2226:There is **no separate "saved_cards" catalog** in either tree (grep-confirmed). "Export a card" == the `.mfd` + `.keys.json` pair from A.1/A.2:
AUDIT_CHAMELEON.md:2227:- `AppModel.saveDumpDialog()` `app/Sources/AppModel.swift:516-525` → `NSSavePanel`, default name from A.1, remembers folder in `UserDefaults` key `rekey.exportFolder`.
AUDIT_CHAMELEON.md:2229:- Import: `AppModel.loadDump(from:)` `:530-548` → `CardDump.load(mfd:)`, registers a recent-document URL.
AUDIT_CHAMELEON.md:2235:Path: `$X7_LEARNED_PATH` or `~/Library/Application Support/tenor-rekey/learned_keys.json` (`probe/learned_keys.py:40-41`). Written **atomically** (temp file + rename). A missing/corrupt file loads as empty and never raises.
AUDIT_CHAMELEON.md:2274:**Entering DFU** (`resource/tools/enter_dfu.py`): device runs VID:PID `6868:8686` in app mode, `1915:521f` in DFU. Send the 10-byte frame `11 ef 03 f2 00 00 00 00 0b 00` (`DFUCMD`) over the app serial port at 115200 with DTR asserted, then poll for `1915:521f`. (Over the tenor-rekey/CLI protocol this is the `enter_bootloader` command, `chameleon_cli_unit.py:7097`.)
AUDIT_CHAMELEON.md:2300:  - `uid` (decimal), `dist` (decimal, the nonce distance from `mf1_detect_nt_dist`), then **triples** repeated: `nt` (plaintext nonce, decimal), `nt_enc` (encrypted nonce, decimal), `par` (3-bit parity int, decimal). Loop `i=3; i+=3` (`nested.c:19`).
AUDIT_CHAMELEON.md:2312:  - Static generation auto-detected from the first `nt`: `0x01200145` ⇒ gen1 (dist 160); `0x009080A2` ⇒ gen2 (dist 161 for KeyB / 160 for KeyA); anything else ⇒ `goto error` (`staticnested.c:26-46`).
AUDIT_CHAMELEON.md:2314:- **Caller** (`chameleon_cli_unit.py:1361-1368`) selects `staticnested` when `mf1_detect_prng()==0`; builds params from `mf1_static_nested_acquire`.
AUDIT_CHAMELEON.md:2485:- `read_ntag` → `NtagResult{present,uid?,sak?,pages{idx:hex4}}`; `apdu` → `ApduResult{present,uid?,sak?,resp?}`; `info` → `DeviceInfo{model,serial,hw}` (`x7d.py:82-92`).
AUDIT_CHAMELEON.md:2496:- tenor-rekey formats: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/{CardDump,KeyStore,Models,AccessBits}.swift`, `app/Sources/AppModel.swift` (export/import), `probe/{x7d.py,x7tool.py,learned_keys.py}`, `probe/dict/{mfc_keys.dic,SOURCES.md,build_dict.py}`

2026-07-21T03:41:20.771172Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
codex
Two concrete state-machine failures are confirmed: Rescan discards a live manual pin, and the monitor suppresses every device swap while any pin exists, even after that port disappears. I’m now validating DFU classification and the remaining recovery path so severities and fixes are precise.
exec
/bin/zsh -lc "rg -n \"Proxgrind|ChameleonUltra|manufacturer|description|list_ports|0x6868|0x1915|521f|USB Vendor Name|USB Product Name\" /Users/tuan/Claude/chameleon/ChameleonUltra/software /Users/tuan/Claude/chameleon/ChameleonUltraGUI/lib 2>/dev/null | sed -n '1,320p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
/Users/tuan/Claude/chameleon/ChameleonUltra/software/pyproject.toml:4:description = "The official CLI for the Chameleon Ultra"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_utils.py:66:        self.description = "Please enter correct parameters"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_utils.py:120:    # Print description if available
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_utils.py:310:            self.help_text = parser.description
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_utils.py:344:    supports meta_dict for descriptions
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:658:        descriptions = {
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:691:        if self in descriptions:
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:692:            return descriptions[self]
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:708:        The ChameleonUltra samples the LF antenna at 125kHz (8µs/sample).
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:12:import serial.tools.list_ports
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:909:        parser.description = "Clear screen"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:920:        parser.description = "Timestamped comment"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:937:        parser.description = "Exit client"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:950:        parser.description = "Dump available commands"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:955:            help="Dump full command description",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:966:    def dump_help(cmd_node, depth=0, dump_cmd_groups=False, dump_description=False):
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:972:            if dump_description:
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:982:                if dump_description:
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:990:                    child, depth + 1, dump_cmd_groups, dump_description
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:995:            root, dump_cmd_groups=args.show_groups, dump_description=args.show_desc
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1003:        parser.description = "Connect to chameleon by serial port"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1039:                    for port in serial.tools.list_ports.comports():
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1040:                        if port.vid == 0x6868:
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1063:        parser.description = "Disconnect chameleon"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1074:        parser.description = "Get or change device mode: tag reader or tag emulator"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1101:        parser.description = "Get device chipset ID"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1112:        parser.description = "Get device address (used with Bluetooth)"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1123:        parser.description = "Get current device firmware version"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1179:        parser.description = "Configure 14a settings (use with caution)"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1232:        parser.description = "Scan 14a tag, and print basic information"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1280:        parser.description = "Scan 14a tag, and print detail information"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1293:        parser.description = "Mifare Classic nested recover key"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1454:        parser.description = "Mifare Classic darkside recover key"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1531:        parser.description = "Mifare Classic hardnested recover key "
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2192:        parser.description = "Mifare Classic static encrypted recover key via backdoor"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2352:        parser.description = "Mifare Classic auto recovery tool"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2747:        parser.description = "Mifare Classic fast key check on sectors"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2950:        parser.description = "Mifare Classic read one block"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2963:        parser.description = "Mifare Classic write one block"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2990:        parser.description = "Display content from tag memory or dump file"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3082:        parser.description = "Mifare Classic dump tag"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3165:        parser.description = "Mifare Classic clone tag from dump"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3274:        parser.description = "MIFARE Classic value block commands"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3688:        parser.description = "MF1 Detection log count/decrypt"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3785:        parser.description = "Load data to emulator memory"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3843:        parser.description = "Read data from emulator memory"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3909:        parser.description = "View data from emulator memory"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:3946:        parser.description = "Settings of Mifare Classic emulator"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4181:        parser.description = "Read MIFARE Ultralight / NTAG counter value."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4200:        parser.description = "Write MIFARE Ultralight / NTAG counter value."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4231:        parser.description = "MIFARE Ultralight / NTAG read one page"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4299:        parser.description = "MIFARE Ultralight / NTAG write one page"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4391:        parser.description = "MIFARE Ultralight / NTAG view emulator data"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4409:        parser.description = "MIFARE Ultralight / NTAG load emulator data"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4496:        parser.description = "MIFARE Ultralight / NTAG save emulator data"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4577:        parser.description = "MIFARE Ultralight / NTAG read counter"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4645:        parser.description = "MIFARE Ultralight dump pages"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4914:        parser.description = "Request MIFARE Ultralight / NTAG version data."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4937:        parser.description = "Request MIFARE Ultralight / NTAG ECC signature data."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4960:        parser.description = "Get authentication nonce from MIFARE Ultralight C tag."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5139:        parser.description = "Key recovery for Giantec ULCG and USCUID-UL cards (won't work on NXP cards!)"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5468:        parser.description = "Settings of Mifare Ultralight / NTAG emulator"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5731:        parser.description = "Get Mifare Ultralight / NTAG emulator detection logs"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5787:        parser.description = "Scan em410x tag and print id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5799:        parser.description = "Write em410x id to t55xx"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5817:        parser.description = "Scan hid prox tag and print card format, facility code, card number, issue level and OEM code"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5840:        parser.description = "Write hidprox card data to t55xx"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5876:        parser.description = "Set emulated hidprox card id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5926:        parser.description = "Scan ioProx tag and print version, facility, card number and raw"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5942:        parser.description = "Write ioProx card data to t55xx"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5980:        parser.description = "Set/Get emulated ioProx card id (stored in slot)"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6128:        parser.description = 'Scan PAC/Stanley tag and print card ID'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6188:        parser.description = 'Write PAC/Stanley id to T55xx'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6203:        parser.description = 'Set emulated PAC/Stanley card ID'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6229:        parser.description = "Scan Viking tag and print id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6241:        parser.description = "Write Viking id to t55xx"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6255:        parser.description = "Clone an IDTECK PSK1 frame onto a T55xx tag."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6269:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6322:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6502:        parser.description = "Read ADC and return the array"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6529:        parser.description = "Get information about slots"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6761:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6798:        parser.description = "Set emulation tag slot activated"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6811:        parser.description = "Set emulation tag type"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6831:        parser.description = "Delete sense type data for a specific slot"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6853:        parser.description = "Set emulation tag data to default"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6872:        parser.description = "Enable tag slot"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6894:        parser.description = "Disable tag slot"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6913:        parser.description = "Set emulated em410x card id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6932:        parser.description = "Set emulated Viking card id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6956:        parser.description = "Scan Jablotron tag and print id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6970:        parser.description = "Write Jablotron id to t55xx"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6984:        parser.description = "Set emulated Jablotron card id"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7010:        parser.description = "Get/Set/Delete tag nick name for slot"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7049:        parser.description = "Store slots config & data to device flash"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7061:        parser.description = "Open all slot and set to default data"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7092:        parser.description = "Restart application to bootloader/DFU mode"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7112:        parser.description = "Get or change current animation mode value"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7140:        parser.description = "Get or set the wake timeout after a button press (5-60 seconds)"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7175:        parser.description = "Clear all BLE bindings. Warning: effect is immediate!"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7195:        parser.description = "Store current settings to flash"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7210:        parser.description = "Reset settings to default values"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7233:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7262:        parser.description = "Get battery information, voltage and level"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7279:        parser.description = "Get or set button press function of Button A and Button B"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7353:        parser.description = "Get or set the ble connect key"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7388:        parser.description = "Show or configure BLE pairing"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7429:        parser.description = "Send raw command"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7500:        parser.description = "Send raw command"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7622:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7653:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7748:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7897:        parser.description = (
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8169:    """Return (description, colour) for a 14A frame."""
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8666:        parser.description = 'Dump last LF sniff capture as hex bytes (PM3 style)'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8707:        parser.description = 'Graphical waveform plot of last LF sniff capture (PyQt5 or matplotlib)'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8841:    win = pg.GraphicsLayoutWidget(title='ChameleonUltra — LF Sniff')
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8889:        parser.description = 'Manchester decode the last LF sniff capture'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8978:        parser.description = 'Detect clock rate and modulation type in last LF capture'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9092:    """Return a brief human-readable description of a command APDU."""
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9131:        parser.description = 'EMV contactless card scan (reader mode) — like PM3 emv scan -at'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9528:        parser.description = 'Show T=CL emulation debug counters'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9563:        parser.description = 'Load EMV APDU responses into HF14A_4 slot for autonomous emulation'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9775:        parser.description = 'ISO14443-4 T=CL interactive APDU relay (manual response mode)'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:10194:        parser.description = "Get MIFARE DESFire card information (version, UID, AIDs)."
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:10260:        parser.description = (

exec
/bin/zsh -lc "find /Users/tuan/Claude/chameleon -maxdepth 3 -type d -iname '*gui*' -print; rg -n \"availableChameleons|Proxgrind|6868|1915|521f\" /Users/tuan/Claude/chameleon --glob '*.dart' | sed -n '1,260p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
/Users/tuan/Claude/chameleon/GUI
/Users/tuan/Claude/chameleon/GUI/chameleonultragui
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/test/recovery_test.dart:63:            '41a56e2600017da88a97ce29023bb43c57ec307bc61cc86ee24bb334f24ca0bfe060088516a7f6e905c7baea628092b49e56f719f83ec684c426e8e93461edcc44eef1e7bafab9d68ce05635e54d3d5b90a4b1ef4773494c236adc0ccebf9e6a7f10b45f404c2dee171686ef59f1cbbacc26ae7eb40a09b5b7ae60b9b3e615f64f2a060fcc7e59132b785fcbada80486a228ed9b42818ba917a174f1980da5c919693c19d7eabb1a8314a1ff0d6ce39c39fcf53e926d55d04f3115a51e04b025552a0dc020281dbf27e15af076950dd27aee2e13639058ba9e008c1383eca5be504e073c2be6f04398c25293183a796b7e710f43a2bd30a57258c5893aeec04b043f1b2c7283b9a9a2f954ee664fb3020c99a4e6516d4f989240f570a1171ce92a6e1c94f9ed8bc8cb62bab2de93be941de4ff416839761094d519b95ebcfd204511bf96cc8273b26448ccad73d82f1327e7e0d41187263ac049a6cf2ba36d4c813b54abab07e41d717bba45b73b6a03df617c6a21d0af3f039063f65bc3528d02fb68afbe8969775e4cce56537bf9a4e67c8c761f6c2a05f12a51ee541a8115666fb190ca05acd65fec1ae689d82012317fa82135c21d3c854abbab13851cb0aef002a0d834f0d598b566861404962e283f55fb6c88026347eba5a13f370e945d6fe66e3aa25ada90ea3cf2d8566cadd408efaefaeef74c6e90d7e500a811eb620810e999f53606a1323afa9bc32ee31208fc9e270f1420ed013a24e91669a18ed536813226f51b02d58eea113d36e69c8d90d293664665f06ddfe8d663e2132ebed790cef986cb3e5cfd9d353c60526d4cdf716c65015845fc5cd39bdb1d0640bd2d86c7f0a824668473654b887066157c50f17ffcd973514b4f0262199be8417725bd25e749253c8ea56e6afb658abc06d2867431ac1fd9c74f12b7e2a37e12e3851ad7744b8effc14ed891e030ccdf3f448bb5bf7861ada1a10b64188025ee3914f7a2f6bb4b529f8121439c49b4faaca4a164d4404ad52eb70b465c1f6508466b28fc6defe04e99cfc13f210eb5bbac29a46244e1894e485192a7bdff2586bd8d75a9806a25a5485302ca4d08eae9d84735c7ffffe1b78bc856619bfa636f487b05556e4ddf2ca940e8570f79f01deaa34c03fd04d81a9693bca149b1b04ff9c1051f4d7fc9b9e4401b50d9d66a91a41738ed48181e3e38fb44da04db3a811aac9495037518fb950ce1160c3c0b937a0e41507e35e0cc599459dd46265686457325133509b0954d12eb92677fae2436b1e5639453fe150a6f3491cb9cfaa9e0c65649b21bb81040f3c093e7533f026cf305f1838ee4ca857b015f0491ec6726c93b458f995c03fc66448f2af1ff8946a610798451f9008ade0b6f04f306a7c8c3980d334797e7566bdcd861e43f5144d7a873497e18d53d1726dda74b879d265242221a39fd544ba9089051a9f53daf0d70e2e7f286db8ab628a9e88a10bff966d18bf73feef205c488ae2c8839db639fc8385b15331c343efa57fb4a45800a8e0dc59e0fa49254efeb543efc2c5a53a120169d2be1c48c5dda4859483f0f20629bd2b1083d7cf356f61eea528b9bf6dbcbdf6b2b98a9d480efcf6efbd8daf6d8b5719b4b78b5d95bd5d6ff481e824569069378608bbfdb93cc7b847ff848efabbe269aad9b0e3ddfe0481d552639bedbbc0b6f262e950ca981a26d7f6d724d9b1a5b7a5cbb43951961be64b9ea868165544bcf1992c734c59620d7043d74b1a449816a5d4d3de0682416c4dc16d48194c817c589450fc728d196e980e51156cdb419e2d006b37f461eb4910076cf861900f970d34d59cf8fe933ed08d06b6be5647772e06819a0f5ab1f1bb6859221475099fd24b9ecf313d659ded5462bcd187f0873772f18c1790342d698b6d9cc034af90307062cbea6dee4eabeca61c619804d399d5c70470e5c367cac9e52f49b644cbb6324011a93adaecd69fb9d0880b1a300592d07667517a98f921aaf3c7190498bebed096d2a9692cab7b75f1d07e102f4fbcd91b0f87de7a0693d94c5d161078cc773206364946eadfc9d4fff2412c8e638f7e2c58045bc4e2f6e5b2d328951b0b675fef7798138e986f6eda8e081f8c2357e9464a355f84a6df71b57e6b74e6d01eec3a84094aa395bc43e07d22391d0674996f7d164e366c70bbd5e55f45cb20fc4369fbc3225e404913fbb48649ae637723066d4ebba4aa0f530768eef54419283c6b10b2c12b77ffd818af6b3b2f3a473bf0a55d9a70f0a3276ee9096a7986814c4c34e35071fe1afc2f0a889e7e6547219ab1a8c0bc356aafa462f62a1181824d4e5ba9602a7b468d7f88b97891dec4a6783b32c04f4c12d362df0d2088cd62db786b5d1d8354727003c0286840fcdf1dfab6ccd821fbd10c02004f042ccf1c80474f9b5a3b46fe2f0d6c93667a17ce6febd1e7f6ba69ecacc0a37beb446afed76c136221071d8d435b5a5b84527d77cd038d61f331a705885a510352541a958357443d7e92b2f2e05b33a1549981a47e390f939e228d103cba5da43bf22802ad0bd3aeaa9a9ed6c0a5df05a9f9056f5971cb3bc6708b3f20411eef3c27c0e1aad29f407e86854aedbf4f4553df619332d3a8d6f1980c9ab1d320fc03e6240a27f7dd26e488518582f790927b4c9536e91513964ea3a7e8f4b2c642ed38f0d6ddbd9ed9374007c28a93bbf0e61dcc8ad604d22622fa8232a26305e3376838e13f8617e2496163fe784ff014f5aecfdaf517a1d2c790a5a8d8af4bf14ce74368472d40b1357296588bff1d9e8a99881a513a1af094a6fc859cf4ecc30eb776cb085ddbac1fa2e59c786b3f045a905d3a2b06c8dc3a68c01288e543cddc7914b31c11a86fd037f47174c2d05bef1d42bab64f80928cbf64fac00c451183146ad881011a2fccbbb65b4407a804254274039857ce7c87489ff1bae6c5f80fb1eaccfdfa1761db423b5238eaa229345c0f32997532a3dd5ff146dd59e3abee46b30971fd6888bc8198a3dc5fef09781c64d5d719ebd64cbe5a37504edbc332619cdd2e96e00499ba6bdc37639156f1311e08a6a923ce860be80788ae7515cd10f9ba5e8cb51887c6e99ed96c4c7ab0f3c6f1ae12b9a50e8f2cd75e62425f726d7cc90be59cacb30a15e77c8f75e5e95d6677817224891c571f4549706a14cf492c92722f729bfc94ded98e913cd4be933942e6f9e827a51efb33731bc7611f872534e83378d0686d02c4949977bbc3e64c5f7df5c9ac4207d1de47a390e4c5379fa8405757a9f1372979bf748c5afe5383234e1fb9ccec0c5fc4052edd7479d858737ce0170822c7997bf58fa15a6f7c3674dffa02a2841b158665842837e6d7967eb76cb89dea2a20fff1f01e7136c682237559d953db1755799faa14efbfa31a37feb669ad3439dae1c51ed6e00dcae07512a32d1ab265ca43373c7650cd8f489406f17dddc3096d40fef3b1b4f1a553cd08c498913b2ee1e0f1b688d5fd7123dbf86ef3f32af4c58d927f9e396bbfa592a36a171c04fdd90ccc88a6b97675103ced4f8e15eefec4d9579c13295a73b61445c4b2aa18474a983ea420289e5631e8fe0e4802d0bf56ed036fc326aeb0979237adf26ba1135d22ed730ebb46e451c3e1662509ae56e93c9b9294faab4c084de37b1f0ed1de1d1c3cdaa83607d2b41acbb8fe0d82fb8fca5d1a81f33bbcc4ad3879e03611f07caf8d7eaf879749c179bc4d47f277754f9ff98a9836df64445ba6cbd28e406ee901e95620b04ed65de29e7ef8596ed2b105aa173fe9054fcdad833ac69733c5aeadd7e1a083505af33e19be32bee1b4cb07055d3a282b9e25a463153c42c13c7c3bd0530500dfc7c321e73b265f5d26f5e6d70f091d382d0d20578815537bbad504a2f13503f5adc3a3614018235993d8ee27464ea4291267edd99fe27c5f03bfd99220afb1c4c080b507e4216c0bd3d2edc52a48d9c0d2a43f50f9b8256868f76bd6578e0af260c3cb1b07dabbc40ac339584f4699dd50235052bc9fa85e80b738f750cb2ec99bfb30f81a5e6aa2fb88e80dfd89314ba0b2255de641296f95ecce8cdda4407e41cc420769bb3e94d5da1d9750a0f9cdad1b25585ae9caaef9877a66cd0885fe9cc3e0402df9895122b7e028c8131d2b20b69f15478a36c61b61e7234007af3c18687b4b36127762e48e1e3b6a6031a07b0d7be5150300814b091b46c9f00b5e0efb0111460fbc321239838268ab0f8452b4e9254a2178577b7c468502318465093e8dae0611e573db41e87adbf0c7dc3f5f8f8b13031316e98c2389e88bfe7aea08d1c4be6198f2765af954cd18829c74de798218e5e15b430e697774b651caebb4033d690bc1cd39aeef45b65de75ddc2a9a9067bb31061b2e0c25216c51aab3b0bf460110313f25cdd10400ca8a99b29688cccf9abf80007237fd547cc943c6de2947e64dcdf733ae45414ea8b9951d997ee0df01d0dec2b164471227730b9699289bb196e64398aa0ac8d2e801842434d90a061f91e2a65e1a51f38fdec24ec52aa82ceeb84d316fce502ef1b4f16ecddd7560689ed90ee2e0b451d32d9cdd3155d74ac873d0bfeaad4f266dd285afdaa5d1ef8c2166b6a698dc3d9436ef1906c1af50857b57f069bd44ca8894848b9a05121d1a94aab7068281a3e88fe4b0db44009a6ef7fab0bf7ab3f6efaa170d0bd7625c204b8ec5af6a75c62cb480f5e89a07f41d0f343bfda16aea9004b427f6953f21b593912553fe62ccfcee1cab92b21c976d9a89f007c95f670ac657396e7a37ab820e8aaacf1977fd03282730530d9219e64c7b17ca011635cebf8cb541a1d2606b1b4a7f576d0a04401a0376d394c773b5b7d0bf7db4119287e1a75368f97096334a51120aac0c13d2420727630a5b319358097bf5f31400558dc83cc193588ac449375824983937e9f06ca32a0917e6b6bb27efe2d06486ba6ed5e985bf343521b53fa73f9f07bf624840e4bb34c56f2946278202c33b33c4d31779b2d88290667d1cb9c004bd399ed477730ba87f959abd43bf935133db84de0490974cc5596a975fef0dfbf3ce7e142eb6afd363fab0eaff28c2244956c1b49266c94bb9039ec4da050112bb44f35c1e1eca9cbf92abfc25f2d915589804609f1232992bf79f0005ee907f3864476e40750418f69ce61122e87c6cdd526a2730de9a29da4e94361cfd7c7d62616a884d2d310901e3bc64517242bfefb52925a2f98d657084c5658b18e5ce800b9dc15a993752e0e84cee1e34014942d07ad984745a3d5ae446be6d07e93cda62109d60d2e8ec7d2a567fcf32b155cafa6f3cfcee11c0d419e6c83386448b023e47a7a6f12c06f55ddf60be44c76f06c88a31483c43b9606a85752500de9bec92837c6b9a87644766d4249718f35c8af81759cbcff543ae85d6b84be3caf1ad7f2e084ca3e276d20bfaafc01ee251648942c03819c6e9da3719dcc8c12c2b66922474536bd9b5b311ecf0af9ac5292bbf796b8dc41ac140084ba21cff3a0f6b16e104e39214e847fc9134b7ccc731f0c9c20e2dbfb6aa25932f0e3f3aef713dcff1948b6b798ae779ba8f655b50939b2b17a6113d5604a6c26950de7e4e55b762c75d43c045e82151e5f71023e1078cc8988f2f134c2ea1cc9c3647ece7cb687bfc0a29cfb74ebee89f720fa426c02231a0b5726b6d83d19fa7a99ca1ca4d6b7e7d8367d30165d40c6980ca1f222e2474914e276f656c7d3eb72773ba28246ab6ab97f984034ef891cbb12ea3f16716cb2ca48e04951e838738b1dc6d05218fd412ad4604d12fb4931b83784000d2008e2553cb70e636869c0bd753d8e5ba7ccee305a1eb99d77db9bebfcebb00b6bb9e0d43277ececb52fd152a0b5acfc69035b9b2b0a1db6c4dc5bb57f19e945235ccbd3d9ffef411ee2c3f37663635c579ebededa366f6f87f7ceaf0e80852037d4fe916eaaceafb760f2dede1985bd1648df3d6dea19b069ade3466caaf24ba8375737fe274a0017b2a17e48928eb6da1c703f27eeebcbf338162493c2b07667ee7fcccb4f0c01b36262ff7a1591eda45e321a24d98e7549c4f99a8fc6799cad424eddce26784e2acc84d1d9aba89bb2a6fe006a05d42099bd49dce1f458b7c39546812085b3c20afe24af28a6e4247da2f1d047cd131c7086aabaf2734978a583e6b6e457201bf51e81e17cd818af5915f0e519e4a9f3e035d661423f3f1ffb420b2e32d2bc60eb8e9589f8305fafff9f6f58caf0a928f774d52445aa9e6170b5ed47242922ad2c075318f77771bdf3330f83c253da401b71ceeb4d416e198c4116baa54b970b2a756ca8819496fa0a040c9b5c10aa1e81b0d616050f806a46dc950fed46711e668f4bc2abf5097068ac97f916e0cf828078f708dc2427c84876cb3e9355624f3561577b2cbbe39ad97c6f0ba5c9ab0a5c93c5a7fbcefa0e2c302150e745c68c1392f7e5f232db3d4db38da7460c2f2b983f6877608e63fc37f0ee615db7fafd0c322e363eb1af1b2573a1f894d00e1972e5b1f72aa8bef41b5c4e494aad70e9286ee3e7d182993bb30aae807912751cd9d942d30993dec7cb22e2294baef67b4e7f4133ae7ed509e43790f2df1517404d8363fad3a968e5556c7cb048e0d83a974f30e1d62ba2eec187e26ebe59dfc24d5276c5a529d1173568b5cfca13ee7bb588f4ad613d5456117a90bd93b5082ea9ee186c3963fa30351ae9750d27c03bfa33aef9c517996c3d8aabd678d84dd4ce2c00a684e8d00472addda1eb826a7ec3dde404af0b0f5b55b8433d779614415d116eaf3a20ff328f2047bdcb95015fbf12b128ac7b4245207ade68655098e00f2bd451f3b097407a019758c809da61080d3f10a3a4dd495f97fb7cef184275440346244f9916a385d51bb90d6b00a9c9ba4a866751b984adfb0ef0cc8574ea7c7d6eb37feffd6758b55bbbfc02e845b414a55a7bf954392e0347da71f70ad4c0a3b5369f9956cd6cf8ef0bf44aa644c07cdfcfc09fbf95102b9d64cb4ebf5d9d56ac370934d8fbb362cf9ad48e2354275f212970e21247d7faaf3746ec7d65a8aaf01f997a77d6aff2b3d26ffb5c966d2c991f957b7aa7b573a729113f359dd55acaa27c6082e5621a3aab2355631e4421b791684c263491f311133ce61619d96d3636e19966ecc5d195eae0c097b25b58e388fc10c5d241a31bbd9c29fe5b1361dccf45d110f13d11a013b0929422fbc730f2bdf8057b6a684306e13be80bb1c5fc47a0007c26f60808b81306778ba2e269481b03a2a5db4b69230bc3edabeccbfb43b2e3748b110914c6e7474c1d206bbae0cf530319dcb57e44a8781f3f0a566ef1b4bcf7aed2fc661df90518067ebfe82dca30811e73cab6be30680d794fb486c3949135182286e19d803739ef23e2651cdb66b8b9d6568ed08425a403cdc684b406af0a0acbfd3ee4539d571ecd0f0bcc1ad1d7a6c528be0fa6ed1046758931a5241d9389725943086b19a5c0b4046b5fd185fe0e6769b5571af91433806a6a4453f5ebda079a62ed93610fd719c8bf051b262f7f6477978318acc270912825241352aa11cd5f42408326d0ddfc25381d64b7cca6a0f23ef1d6c27b86d28925268ae2b51b3cc08dda9a206f5123d885ecd017d6cf332b0cad1db8f9270fd3aef843b1bbc0624d0d18236f3a8cb02d81e72ae2907204e49fa2221074fdae5513557119a5a484b5db67c3edad77e0db3ef69d87258d0f8617633648b376da3b46a27cd2de25e22bf5f2d1bc9687720cfd306f521d85d6e71f9cc78c8903cefec4f344f9946a8ea20b330a965439ae33edf8b42e2935d94cd0865577509108648d458f2f611a2a98e894ba96e0f74a6f190695465ec854d7d222697fb263d4b4f7ec68b6fdb564d77996c8f7778dc6e5aa630816dea109bc20931fa287d235dc62bed323959bf1c169c35dd66cd838857cba1a424ab308b82822612769cefcc23ac0e4aeeccf226b8b80ec4d9feffd33adcee3916231b0a2b1f76367faeb108fb37611b97211c4881c181fdbe74dc778d47e4bbc8e3f10edb3655c57f1d5b4f4371774ade85fe4100711ac5cf69ea53fb923cae140dadc8ff34582fb74a5732dbd669a4d3247fbe03eadc2d4280851f10c3667e79c3aa0297366b25c43212eedb1e1105ba47f9cf94643488beeeee1427bc9b391d10513d2211a80a017f42c2012b14fc76566a1d7bc96b96f9ef4f6a75f478cf058e7e580d623774848903bb44c9b81c55ddf4f021dcac6f2aa1998664c0332db4d4d843b403966f66153aaf2a4fff421227381381a2e6d9fafb80e9329f6156c1dda02be975479481666b6319921dee103fa65ca0dfc1d8e534db48fea2cbb042b50f3c3b9fcca719216109424'));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_android.dart:34:  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_android.dart:37:    output.addAll(await mobileSerial.availableChameleons(onlyDFU));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_android.dart:40:      output.addAll(await bleSerial.availableChameleons(onlyDFU));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_macos.dart:30:  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_macos.dart:33:    output.addAll(await nativeSerial.availableChameleons(onlyDFU));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_macos.dart:34:    output.addAll(await bleSerial.availableChameleons(onlyDFU));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_abstract.dart:48:  Future<List<Chameleon>> availableChameleons(bool onlyDFU);
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_emulator.dart:30:  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/gui/page/connect.dart:153:          await appState.connector!.availableChameleons(false));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_mobile.dart:56:  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_mobile.dart:59:      if (deviceMap[deviceName]!.manufacturerName == "Proxgrind") {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_mobile.dart:68:        var dfuMode = deviceMap[deviceName]!.vid == 0x1915;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_mobile.dart:140:      isDFU = deviceMap[devicePort]!.vid == 0x1915;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/gui/page/debug.dart:376:                          '41a56e2600017da88a97ce29023bb43c57ec307bc61cc86ee24bb334f24ca0bfe060088516a7f6e905c7baea628092b49e56f719f83ec684c426e8e93461edcc44eef1e7bafab9d68ce05635e54d3d5b90a4b1ef4773494c236adc0ccebf9e6a7f10b45f404c2dee171686ef59f1cbbacc26ae7eb40a09b5b7ae60b9b3e615f64f2a060fcc7e59132b785fcbada80486a228ed9b42818ba917a174f1980da5c919693c19d7eabb1a8314a1ff0d6ce39c39fcf53e926d55d04f3115a51e04b025552a0dc020281dbf27e15af076950dd27aee2e13639058ba9e008c1383eca5be504e073c2be6f04398c25293183a796b7e710f43a2bd30a57258c5893aeec04b043f1b2c7283b9a9a2f954ee664fb3020c99a4e6516d4f989240f570a1171ce92a6e1c94f9ed8bc8cb62bab2de93be941de4ff416839761094d519b95ebcfd204511bf96cc8273b26448ccad73d82f1327e7e0d41187263ac049a6cf2ba36d4c813b54abab07e41d717bba45b73b6a03df617c6a21d0af3f039063f65bc3528d02fb68afbe8969775e4cce56537bf9a4e67c8c761f6c2a05f12a51ee541a8115666fb190ca05acd65fec1ae689d82012317fa82135c21d3c854abbab13851cb0aef002a0d834f0d598b566861404962e283f55fb6c88026347eba5a13f370e945d6fe66e3aa25ada90ea3cf2d8566cadd408efaefaeef74c6e90d7e500a811eb620810e999f53606a1323afa9bc32ee31208fc9e270f1420ed013a24e91669a18ed536813226f51b02d58eea113d36e69c8d90d293664665f06ddfe8d663e2132ebed790cef986cb3e5cfd9d353c60526d4cdf716c65015845fc5cd39bdb1d0640bd2d86c7f0a824668473654b887066157c50f17ffcd973514b4f0262199be8417725bd25e749253c8ea56e6afb658abc06d2867431ac1fd9c74f12b7e2a37e12e3851ad7744b8effc14ed891e030ccdf3f448bb5bf7861ada1a10b64188025ee3914f7a2f6bb4b529f8121439c49b4faaca4a164d4404ad52eb70b465c1f6508466b28fc6defe04e99cfc13f210eb5bbac29a46244e1894e485192a7bdff2586bd8d75a9806a25a5485302ca4d08eae9d84735c7ffffe1b78bc856619bfa636f487b05556e4ddf2ca940e8570f79f01deaa34c03fd04d81a9693bca149b1b04ff9c1051f4d7fc9b9e4401b50d9d66a91a41738ed48181e3e38fb44da04db3a811aac9495037518fb950ce1160c3c0b937a0e41507e35e0cc599459dd46265686457325133509b0954d12eb92677fae2436b1e5639453fe150a6f3491cb9cfaa9e0c65649b21bb81040f3c093e7533f026cf305f1838ee4ca857b015f0491ec6726c93b458f995c03fc66448f2af1ff8946a610798451f9008ade0b6f04f306a7c8c3980d334797e7566bdcd861e43f5144d7a873497e18d53d1726dda74b879d265242221a39fd544ba9089051a9f53daf0d70e2e7f286db8ab628a9e88a10bff966d18bf73feef205c488ae2c8839db639fc8385b15331c343efa57fb4a45800a8e0dc59e0fa49254efeb543efc2c5a53a120169d2be1c48c5dda4859483f0f20629bd2b1083d7cf356f61eea528b9bf6dbcbdf6b2b98a9d480efcf6efbd8daf6d8b5719b4b78b5d95bd5d6ff481e824569069378608bbfdb93cc7b847ff848efabbe269aad9b0e3ddfe0481d552639bedbbc0b6f262e950ca981a26d7f6d724d9b1a5b7a5cbb43951961be64b9ea868165544bcf1992c734c59620d7043d74b1a449816a5d4d3de0682416c4dc16d48194c817c589450fc728d196e980e51156cdb419e2d006b37f461eb4910076cf861900f970d34d59cf8fe933ed08d06b6be5647772e06819a0f5ab1f1bb6859221475099fd24b9ecf313d659ded5462bcd187f0873772f18c1790342d698b6d9cc034af90307062cbea6dee4eabeca61c619804d399d5c70470e5c367cac9e52f49b644cbb6324011a93adaecd69fb9d0880b1a300592d07667517a98f921aaf3c7190498bebed096d2a9692cab7b75f1d07e102f4fbcd91b0f87de7a0693d94c5d161078cc773206364946eadfc9d4fff2412c8e638f7e2c58045bc4e2f6e5b2d328951b0b675fef7798138e986f6eda8e081f8c2357e9464a355f84a6df71b57e6b74e6d01eec3a84094aa395bc43e07d22391d0674996f7d164e366c70bbd5e55f45cb20fc4369fbc3225e404913fbb48649ae637723066d4ebba4aa0f530768eef54419283c6b10b2c12b77ffd818af6b3b2f3a473bf0a55d9a70f0a3276ee9096a7986814c4c34e35071fe1afc2f0a889e7e6547219ab1a8c0bc356aafa462f62a1181824d4e5ba9602a7b468d7f88b97891dec4a6783b32c04f4c12d362df0d2088cd62db786b5d1d8354727003c0286840fcdf1dfab6ccd821fbd10c02004f042ccf1c80474f9b5a3b46fe2f0d6c93667a17ce6febd1e7f6ba69ecacc0a37beb446afed76c136221071d8d435b5a5b84527d77cd038d61f331a705885a510352541a958357443d7e92b2f2e05b33a1549981a47e390f939e228d103cba5da43bf22802ad0bd3aeaa9a9ed6c0a5df05a9f9056f5971cb3bc6708b3f20411eef3c27c0e1aad29f407e86854aedbf4f4553df619332d3a8d6f1980c9ab1d320fc03e6240a27f7dd26e488518582f790927b4c9536e91513964ea3a7e8f4b2c642ed38f0d6ddbd9ed9374007c28a93bbf0e61dcc8ad604d22622fa8232a26305e3376838e13f8617e2496163fe784ff014f5aecfdaf517a1d2c790a5a8d8af4bf14ce74368472d40b1357296588bff1d9e8a99881a513a1af094a6fc859cf4ecc30eb776cb085ddbac1fa2e59c786b3f045a905d3a2b06c8dc3a68c01288e543cddc7914b31c11a86fd037f47174c2d05bef1d42bab64f80928cbf64fac00c451183146ad881011a2fccbbb65b4407a804254274039857ce7c87489ff1bae6c5f80fb1eaccfdfa1761db423b5238eaa229345c0f32997532a3dd5ff146dd59e3abee46b30971fd6888bc8198a3dc5fef09781c64d5d719ebd64cbe5a37504edbc332619cdd2e96e00499ba6bdc37639156f1311e08a6a923ce860be80788ae7515cd10f9ba5e8cb51887c6e99ed96c4c7ab0f3c6f1ae12b9a50e8f2cd75e62425f726d7cc90be59cacb30a15e77c8f75e5e95d6677817224891c571f4549706a14cf492c92722f729bfc94ded98e913cd4be933942e6f9e827a51efb33731bc7611f872534e83378d0686d02c4949977bbc3e64c5f7df5c9ac4207d1de47a390e4c5379fa8405757a9f1372979bf748c5afe5383234e1fb9ccec0c5fc4052edd7479d858737ce0170822c7997bf58fa15a6f7c3674dffa02a2841b158665842837e6d7967eb76cb89dea2a20fff1f01e7136c682237559d953db1755799faa14efbfa31a37feb669ad3439dae1c51ed6e00dcae07512a32d1ab265ca43373c7650cd8f489406f17dddc3096d40fef3b1b4f1a553cd08c498913b2ee1e0f1b688d5fd7123dbf86ef3f32af4c58d927f9e396bbfa592a36a171c04fdd90ccc88a6b97675103ced4f8e15eefec4d9579c13295a73b61445c4b2aa18474a983ea420289e5631e8fe0e4802d0bf56ed036fc326aeb0979237adf26ba1135d22ed730ebb46e451c3e1662509ae56e93c9b9294faab4c084de37b1f0ed1de1d1c3cdaa83607d2b41acbb8fe0d82fb8fca5d1a81f33bbcc4ad3879e03611f07caf8d7eaf879749c179bc4d47f277754f9ff98a9836df64445ba6cbd28e406ee901e95620b04ed65de29e7ef8596ed2b105aa173fe9054fcdad833ac69733c5aeadd7e1a083505af33e19be32bee1b4cb07055d3a282b9e25a463153c42c13c7c3bd0530500dfc7c321e73b265f5d26f5e6d70f091d382d0d20578815537bbad504a2f13503f5adc3a3614018235993d8ee27464ea4291267edd99fe27c5f03bfd99220afb1c4c080b507e4216c0bd3d2edc52a48d9c0d2a43f50f9b8256868f76bd6578e0af260c3cb1b07dabbc40ac339584f4699dd50235052bc9fa85e80b738f750cb2ec99bfb30f81a5e6aa2fb88e80dfd89314ba0b2255de641296f95ecce8cdda4407e41cc420769bb3e94d5da1d9750a0f9cdad1b25585ae9caaef9877a66cd0885fe9cc3e0402df9895122b7e028c8131d2b20b69f15478a36c61b61e7234007af3c18687b4b36127762e48e1e3b6a6031a07b0d7be5150300814b091b46c9f00b5e0efb0111460fbc321239838268ab0f8452b4e9254a2178577b7c468502318465093e8dae0611e573db41e87adbf0c7dc3f5f8f8b13031316e98c2389e88bfe7aea08d1c4be6198f2765af954cd18829c74de798218e5e15b430e697774b651caebb4033d690bc1cd39aeef45b65de75ddc2a9a9067bb31061b2e0c25216c51aab3b0bf460110313f25cdd10400ca8a99b29688cccf9abf80007237fd547cc943c6de2947e64dcdf733ae45414ea8b9951d997ee0df01d0dec2b164471227730b9699289bb196e64398aa0ac8d2e801842434d90a061f91e2a65e1a51f38fdec24ec52aa82ceeb84d316fce502ef1b4f16ecddd7560689ed90ee2e0b451d32d9cdd3155d74ac873d0bfeaad4f266dd285afdaa5d1ef8c2166b6a698dc3d9436ef1906c1af50857b57f069bd44ca8894848b9a05121d1a94aab7068281a3e88fe4b0db44009a6ef7fab0bf7ab3f6efaa170d0bd7625c204b8ec5af6a75c62cb480f5e89a07f41d0f343bfda16aea9004b427f6953f21b593912553fe62ccfcee1cab92b21c976d9a89f007c95f670ac657396e7a37ab820e8aaacf1977fd03282730530d9219e64c7b17ca011635cebf8cb541a1d2606b1b4a7f576d0a04401a0376d394c773b5b7d0bf7db4119287e1a75368f97096334a51120aac0c13d2420727630a5b319358097bf5f31400558dc83cc193588ac449375824983937e9f06ca32a0917e6b6bb27efe2d06486ba6ed5e985bf343521b53fa73f9f07bf624840e4bb34c56f2946278202c33b33c4d31779b2d88290667d1cb9c004bd399ed477730ba87f959abd43bf935133db84de0490974cc5596a975fef0dfbf3ce7e142eb6afd363fab0eaff28c2244956c1b49266c94bb9039ec4da050112bb44f35c1e1eca9cbf92abfc25f2d915589804609f1232992bf79f0005ee907f3864476e40750418f69ce61122e87c6cdd526a2730de9a29da4e94361cfd7c7d62616a884d2d310901e3bc64517242bfefb52925a2f98d657084c5658b18e5ce800b9dc15a993752e0e84cee1e34014942d07ad984745a3d5ae446be6d07e93cda62109d60d2e8ec7d2a567fcf32b155cafa6f3cfcee11c0d419e6c83386448b023e47a7a6f12c06f55ddf60be44c76f06c88a31483c43b9606a85752500de9bec92837c6b9a87644766d4249718f35c8af81759cbcff543ae85d6b84be3caf1ad7f2e084ca3e276d20bfaafc01ee251648942c03819c6e9da3719dcc8c12c2b66922474536bd9b5b311ecf0af9ac5292bbf796b8dc41ac140084ba21cff3a0f6b16e104e39214e847fc9134b7ccc731f0c9c20e2dbfb6aa25932f0e3f3aef713dcff1948b6b798ae779ba8f655b50939b2b17a6113d5604a6c26950de7e4e55b762c75d43c045e82151e5f71023e1078cc8988f2f134c2ea1cc9c3647ece7cb687bfc0a29cfb74ebee89f720fa426c02231a0b5726b6d83d19fa7a99ca1ca4d6b7e7d8367d30165d40c6980ca1f222e2474914e276f656c7d3eb72773ba28246ab6ab97f984034ef891cbb12ea3f16716cb2ca48e04951e838738b1dc6d05218fd412ad4604d12fb4931b83784000d2008e2553cb70e636869c0bd753d8e5ba7ccee305a1eb99d77db9bebfcebb00b6bb9e0d43277ececb52fd152a0b5acfc69035b9b2b0a1db6c4dc5bb57f19e945235ccbd3d9ffef411ee2c3f37663635c579ebededa366f6f87f7ceaf0e80852037d4fe916eaaceafb760f2dede1985bd1648df3d6dea19b069ade3466caaf24ba8375737fe274a0017b2a17e48928eb6da1c703f27eeebcbf338162493c2b07667ee7fcccb4f0c01b36262ff7a1591eda45e321a24d98e7549c4f99a8fc6799cad424eddce26784e2acc84d1d9aba89bb2a6fe006a05d42099bd49dce1f458b7c39546812085b3c20afe24af28a6e4247da2f1d047cd131c7086aabaf2734978a583e6b6e457201bf51e81e17cd818af5915f0e519e4a9f3e035d661423f3f1ffb420b2e32d2bc60eb8e9589f8305fafff9f6f58caf0a928f774d52445aa9e6170b5ed47242922ad2c075318f77771bdf3330f83c253da401b71ceeb4d416e198c4116baa54b970b2a756ca8819496fa0a040c9b5c10aa1e81b0d616050f806a46dc950fed46711e668f4bc2abf5097068ac97f916e0cf828078f708dc2427c84876cb3e9355624f3561577b2cbbe39ad97c6f0ba5c9ab0a5c93c5a7fbcefa0e2c302150e745c68c1392f7e5f232db3d4db38da7460c2f2b983f6877608e63fc37f0ee615db7fafd0c322e363eb1af1b2573a1f894d00e1972e5b1f72aa8bef41b5c4e494aad70e9286ee3e7d182993bb30aae807912751cd9d942d30993dec7cb22e2294baef67b4e7f4133ae7ed509e43790f2df1517404d8363fad3a968e5556c7cb048e0d83a974f30e1d62ba2eec187e26ebe59dfc24d5276c5a529d1173568b5cfca13ee7bb588f4ad613d5456117a90bd93b5082ea9ee186c3963fa30351ae9750d27c03bfa33aef9c517996c3d8aabd678d84dd4ce2c00a684e8d00472addda1eb826a7ec3dde404af0b0f5b55b8433d779614415d116eaf3a20ff328f2047bdcb95015fbf12b128ac7b4245207ade68655098e00f2bd451f3b097407a019758c809da61080d3f10a3a4dd495f97fb7cef184275440346244f9916a385d51bb90d6b00a9c9ba4a866751b984adfb0ef0cc8574ea7c7d6eb37feffd6758b55bbbfc02e845b414a55a7bf954392e0347da71f70ad4c0a3b5369f9956cd6cf8ef0bf44aa644c07cdfcfc09fbf95102b9d64cb4ebf5d9d56ac370934d8fbb362cf9ad48e2354275f212970e21247d7faaf3746ec7d65a8aaf01f997a77d6aff2b3d26ffb5c966d2c991f957b7aa7b573a729113f359dd55acaa27c6082e5621a3aab2355631e4421b791684c263491f311133ce61619d96d3636e19966ecc5d195eae0c097b25b58e388fc10c5d241a31bbd9c29fe5b1361dccf45d110f13d11a013b0929422fbc730f2bdf8057b6a684306e13be80bb1c5fc47a0007c26f60808b81306778ba2e269481b03a2a5db4b69230bc3edabeccbfb43b2e3748b110914c6e7474c1d206bbae0cf530319dcb57e44a8781f3f0a566ef1b4bcf7aed2fc661df90518067ebfe82dca30811e73cab6be30680d794fb486c3949135182286e19d803739ef23e2651cdb66b8b9d6568ed08425a403cdc684b406af0a0acbfd3ee4539d571ecd0f0bcc1ad1d7a6c528be0fa6ed1046758931a5241d9389725943086b19a5c0b4046b5fd185fe0e6769b5571af91433806a6a4453f5ebda079a62ed93610fd719c8bf051b262f7f6477978318acc270912825241352aa11cd5f42408326d0ddfc25381d64b7cca6a0f23ef1d6c27b86d28925268ae2b51b3cc08dda9a206f5123d885ecd017d6cf332b0cad1db8f9270fd3aef843b1bbc0624d0d18236f3a8cb02d81e72ae2907204e49fa2221074fdae5513557119a5a484b5db67c3edad77e0db3ef69d87258d0f8617633648b376da3b46a27cd2de25e22bf5f2d1bc9687720cfd306f521d85d6e71f9cc78c8903cefec4f344f9946a8ea20b330a965439ae33edf8b42e2935d94cd0865577509108648d458f2f611a2a98e894ba96e0f74a6f190695465ec854d7d222697fb263d4b4f7ec68b6fdb564d77996c8f7778dc6e5aa630816dea109bc20931fa287d235dc62bed323959bf1c169c35dd66cd838857cba1a424ab308b82822612769cefcc23ac0e4aeeccf226b8b80ec4d9feffd33adcee3916231b0a2b1f76367faeb108fb37611b97211c4881c181fdbe74dc778d47e4bbc8e3f10edb3655c57f1d5b4f4371774ade85fe4100711ac5cf69ea53fb923cae140dadc8ff34582fb74a5732dbd669a4d3247fbe03eadc2d4280851f10c3667e79c3aa0297366b25c43212eedb1e1105ba47f9cf94643488beeeee1427bc9b391d10513d2211a80a017f42c2012b14fc76566a1d7bc96b96f9ef4f6a75f478cf058e7e580d623774848903bb44c9b81c55ddf4f021dcac6f2aa1998664c0332db4d4d843b403966f66153aaf2a4fff421227381381a2e6d9fafb80e9329f6156c1dda02be975479481666b6319921dee103fa65ca0dfc1d8e534db48fea2cbb042b50f3c3b9fcca719216109424'));
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_native.dart:57:  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_native.dart:114:      if (checkPort!.manufacturer == "Proxgrind" ||
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_native.dart:137:        checkDFU = checkPort!.vendorId == 0x1915;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart:85:  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/helpers/flash.dart:158:    chameleons = await appState.connector!.availableChameleons(true);

exec
/bin/zsh -lc "nl -ba /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_native.dart | sed -n '45,155p'; nl -ba /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_macos.dart | sed -n '1,90p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
    45	      if (hadState) {
    46	        notifyConnectionStateChanged();
    47	      }
    48	      return true;
    49	    }
    50	    if (hadState) {
    51	      notifyConnectionStateChanged();
    52	    }
    53	    return false;
    54	  }
    55	
    56	  @override
    57	  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
    58	    List<Chameleon> output = [];
    59	    for (final port in await availableDevices()) {
    60	      if (await connectDevice(port, false)) {
    61	        if (onlyDFU) {
    62	          if (checkDFU) {
    63	            output.add(Chameleon(
    64	                port: port,
    65	                device: device,
    66	                type: connectionType,
    67	                dfu: checkDFU));
    68	          }
    69	        } else {
    70	          output.add(Chameleon(
    71	              port: port, device: device, type: connectionType, dfu: checkDFU));
    72	        }
    73	      }
    74	    }
    75	
    76	    return output;
    77	  }
    78	
    79	  @override
    80	  Future<bool> connectSpecificDevice(dynamic devicePort) async {
    81	    if (await connectDevice(devicePort, true)) {
    82	      portName = devicePort;
    83	      connected = true;
    84	      activeDevicePort = devicePort;
    85	      return true;
    86	    }
    87	    return false;
    88	  }
    89	
    90	  Future<bool> connectDevice(String address, bool setPort) async {
    91	    if (port != null && port!.isOpen && !setPort) {
    92	      log.d("Chameleon is connected now");
    93	    }
    94	
    95	    log.d("Connecting to $address");
    96	    try {
    97	      checkPort = SerialPort(address);
    98	      checkPort!.openReadWrite();
    99	      checkPort!.config = SerialPortConfig()
   100	        ..baudRate = 115200
   101	        ..bits = 8
   102	        ..stopBits = 1
   103	        ..parity = SerialPortParity.none
   104	        ..rts = SerialPortRts.flowControl
   105	        ..cts = SerialPortCts.flowControl
   106	        ..dsr = SerialPortDsr.flowControl
   107	        ..dtr = SerialPortDtr.flowControl
   108	        ..setFlowControl(SerialPortFlowControl.rtsCts);
   109	      log.d("Connected to $address");
   110	      log.d("Manufacturer: ${checkPort!.manufacturer}");
   111	      log.d("Product: ${checkPort!.productName}");
   112	      
   113	      bool isChameleon = false;
   114	      if (checkPort!.manufacturer == "Proxgrind" ||
   115	          (checkPort!.description != null &&
   116	              checkPort!.description!.toLowerCase().contains("chameleon"))) {
   117	        isChameleon = true;
   118	        if (checkPort!.productName != null &&
   119	            checkPort!.productName!.contains('ChameleonUltra')) {
   120	          device = ChameleonDevice.ultra;
   121	        } else if (checkPort!.description != null &&
   122	            checkPort!.description!.toLowerCase().contains('ultra')) {
   123	          device = ChameleonDevice.ultra;
   124	        } else {
   125	          device = ChameleonDevice.lite;
   126	        }
   127	      } else if (setPort) {
   128	        isChameleon = true;
   129	        device = ChameleonDevice.ultra;
   130	      }
   131	
   132	      if (isChameleon) {
   133	        log.d("Found Chameleon ${chameleonDeviceName(device)}!");
   134	
   135	        connectionType = ConnectionType.usb;
   136	
   137	        checkDFU = checkPort!.vendorId == 0x1915;
   138	
   139	        checkPort!.close();
   140	
   141	        if (setPort) {
   142	          port = checkPort;
   143	          isDFU = checkDFU;
   144	        }
   145	
   146	        return true;
   147	      }
   148	
   149	      checkPort!.close();
   150	      return false;
   151	    } on SerialPortError catch (e) {
   152	      log.e(e);
   153	      try {
   154	        checkPort?.close();
   155	      } catch (_) {}
     1	import 'dart:async';
     2	import 'dart:typed_data';
     3	import 'package:chameleonultragui/connector/serial_abstract.dart';
     4	import 'package:chameleonultragui/connector/serial_ble.dart';
     5	import 'package:chameleonultragui/connector/serial_native.dart';
     6	
     7	// Class combines macOS Native Serial and BLE serial
     8	class MacOSSerial extends AbstractSerial {
     9	  late BLESerial bleSerial = BLESerial(log: log);
    10	  late NativeSerial nativeSerial = NativeSerial(log: log);
    11	
    12	  MacOSSerial({required super.log}) {
    13	    bleSerial.connectionStateCallback = notifyConnectionStateChanged;
    14	    nativeSerial.connectionStateCallback = notifyConnectionStateChanged;
    15	  }
    16	
    17	  @override
    18	  Future<bool> performDisconnect() async {
    19	    bool ble = await bleSerial.performDisconnect();
    20	    bool native = await nativeSerial.performDisconnect();
    21	    return (ble || native);
    22	  }
    23	
    24	  @override
    25	  bool isManualConnectionSupported() {
    26	    return nativeSerial.isManualConnectionSupported();
    27	  }
    28	
    29	  @override
    30	  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
    31	    List<Chameleon> output = [];
    32	
    33	    output.addAll(await nativeSerial.availableChameleons(onlyDFU));
    34	    output.addAll(await bleSerial.availableChameleons(onlyDFU));
    35	
    36	    return output;
    37	  }
    38	
    39	  @override
    40	  Future<bool> connectSpecificDevice(dynamic devicePort) async {
    41	    if (devicePort.contains("/dev")) {
    42	      return await nativeSerial.connectSpecificDevice(devicePort);
    43	    } else {
    44	      return await bleSerial.connectSpecificDevice(devicePort);
    45	    }
    46	  }
    47	
    48	  @override
    49	  Future<bool> write(Uint8List command, {bool firmware = false}) async {
    50	    if (bleSerial.connected) {
    51	      return await bleSerial.write(command, firmware: firmware);
    52	    } else {
    53	      return await nativeSerial.write(command, firmware: firmware);
    54	    }
    55	  }
    56	
    57	  @override
    58	  Future<void> registerCallback(dynamic callback) async {
    59	    await bleSerial.registerCallback(callback);
    60	    await nativeSerial.registerCallback(callback);
    61	  }
    62	
    63	  @override
    64	  dynamic get activeDevicePort => (bleSerial.connected)
    65	      ? bleSerial.activeDevicePort
    66	      : nativeSerial.activeDevicePort;
    67	
    68	  @override
    69	  ChameleonDevice get device =>
    70	      (bleSerial.connected) ? bleSerial.device : nativeSerial.device;
    71	
    72	  @override
    73	  bool get connected => (bleSerial.connected || nativeSerial.connected);
    74	
    75	  @override
    76	  String get portName =>
    77	      (bleSerial.connected) ? bleSerial.portName : nativeSerial.portName;
    78	
    79	  @override
    80	  ConnectionType get connectionType => (bleSerial.connected)
    81	      ? bleSerial.connectionType
    82	      : nativeSerial.connectionType;
    83	
    84	  @override
    85	  bool get isOpen => (bleSerial.isOpen || nativeSerial.isOpen);
    86	
    87	  @override
    88	  set isOpen(open) => {bleSerial.isOpen = nativeSerial.isOpen = open};
    89	
    90	  @override

2026-07-21T03:41:45.976264Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "rg -n \"availableDevices\" /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_native.dart; nl -ba /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_native.dart | sed -n '1,48p;155,210p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
20:  Future<List> availableDevices() async {
26:    for (final port in await availableDevices()) {
59:    for (final port in await availableDevices()) {
     1	import 'package:chameleonultragui/helpers/general.dart';
     2	import 'package:flutter/foundation.dart';
     3	import 'package:flutter_libserialport/flutter_libserialport.dart';
     4	import 'serial_abstract.dart';
     5	
     6	class NativeSerial extends AbstractSerial {
     7	  // Class for PC Serial Communication
     8	  SerialPort? port;
     9	  SerialPort? checkPort;
    10	  bool checkDFU = true;
    11	  SerialPortReader? reader;
    12	
    13	  NativeSerial({required super.log});
    14	
    15	  @override
    16	  bool isManualConnectionSupported() {
    17	    return true;
    18	  }
    19	
    20	  Future<List> availableDevices() async {
    21	    return SerialPort.availablePorts;
    22	  }
    23	
    24	  @override
    25	  Future<bool> performConnect() async {
    26	    for (final port in await availableDevices()) {
    27	      if (await connectDevice(port, true)) {
    28	        portName = port;
    29	        connected = true;
    30	        return true;
    31	      }
    32	    }
    33	    return false;
    34	  }
    35	
    36	  @override
    37	  Future<bool> performDisconnect() async {
    38	    final hadState = hasConnectionState || port != null || reader != null;
    39	    resetConnectionState();
    40	    if (port != null) {
    41	      reader?.close();
    42	      port?.close();
    43	      reader = null;
    44	      port = null;
    45	      if (hadState) {
    46	        notifyConnectionStateChanged();
    47	      }
    48	      return true;
   155	      } catch (_) {}
   156	      return false;
   157	    }
   158	  }
   159	
   160	  @override
   161	  Future<void> open() async {
   162	    port!.openReadWrite();
   163	    reader = SerialPortReader(port!, timeout: 2500);
   164	    reader?.stream.listen((data) async {
   165	      try {
   166	        await messageCallback(data);
   167	      } catch (_) {
   168	        log.w("Received unexpected data: ${bytesToHex(data)}");
   169	      }
   170	    }, onDone: () async {
   171	      await performDisconnect();
   172	    }, onError: (_) async {
   173	      await performDisconnect();
   174	    });
   175	  }
   176	
   177	  @override
   178	  Future<bool> write(Uint8List command, {bool firmware = false}) async {
   179	    port!.write(command);
   180	    port!.drain();
   181	    return true;
   182	  }
   183	}

2026-07-21T03:41:53.867175Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "ioreg -r -c IOUSBHostDevice -l 2>/dev/null | rg -m 40 'USB Vendor Name|USB Product Name|idVendor|idProduct|kUSB' || true; ioreg -r -c IOSerialBSDClient -l 2>/dev/null | rg -m 40 'IOCalloutDevice|IODialinDevice|idVendor|USB Vendor Name|USB Product Name' || true" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
  |   "kUSBSerialNumberString" = "7423J07"
  |   "kUSBHubPowerSupply" = 6400
  |   "USB Vendor Name" = "Apple"
  |   "kUSBProductString" = "USB3 Gen2 Hub"
  |   "kUSBVendorString" = "Apple"
  |   "USB Product Name" = "USB3 Gen2 Hub"
  |   "idVendor" = 1452
  |   "kUSBCurrentConfiguration" = 1
  |   "idProduct" = 32778
  |   "kUSBContainerID" = "05100856-66ea-48a8-bece-606c63c4bc27"
  |   "kUSBWakePortCurrentLimit" = 1600
  |   "kUSBAddress" = 1
  |   "kUSBSleepPortCurrentLimit" = 1600
  |   "kUSBHubIdlePolicy" = 1000
  | |   "kUSBSleepPortCurrentLimit" = 1600
  | |   "kUSBWakePortCurrentLimit" = 1600
  | | |   "kUSBSleepPortCurrentLimit" = 1600
  | | |   "kUSBWakePortCurrentLimit" = 1600
  | | |   "kUSBBusCurrentAllocation" = 896
  | |   |   "kUSBSerialNumberString" = "000000123ADA"
  | |   |   "USB Vendor Name" = "VLI Manufacture String"
  | |   |   "kUSBProductString" = "VLI Product String"
  | |   |   "kUSBVendorString" = "VLI Manufacture String"
  | |   |   "USB Product Name" = "VLI Product String"
  | |   |   "idVendor" = 8457
  | |   |   "kUSBCurrentConfiguration" = 1
  | |   |   "idProduct" = 1813
  | |   |   "kUSBContainerID" = "b9ec2655-ea0a-4406-adf6-bb8ed0ecf78e"
  | |   |   "kUSBAddress" = 3
  | |   | |   "idProduct" = 1813
  | |   | |   "USB Product Name" = "VLI Product String"
  | |   | |   "USB Vendor Name" = "VLI Manufacture String"
  | |   | |   "idVendor" = 8457
  | |   |   |   "idProduct" = 1813
  | |   |   |   "USB Product Name" = "VLI Product String"
  | |   |   |   "USB Device Info" = {"bcdDevice"=305,"idProduct"=1813,"bInterfaceSubClass"=6,"locationID"=135331840,"bConfigurationValue"=1,"kUSBVendorString"="VLI Manufacture String","kUSBSerialNumberString"="000000123ADA","USB Product Name"="VLI Product String","kUSBProductString"="VLI Product String","idVendor"=8457,"bInterfaceProtocol"=80,"bInterfaceNumber"=0,"bInterfaceClass"=8,"bNumConfigurations"=1}
  | |   |   |   "kUSBSerialNumberString" = "000000123ADA"
  | |   |   |   "idVendor" = 8457
  | |   |   |   "kUSBProductString" = "VLI Product String"
  | |   |   |   "kUSBVendorString" = "VLI Manufacture String"
      "IOCalloutDevice" = "/dev/cu.usbmodem5AA60600T8023"
      "IODialinDevice" = "/dev/tty.usbmodem5AA60600T8023"
      "IOCalloutDevice" = "/dev/cu.debug-console"
      "IODialinDevice" = "/dev/tty.debug-console"
      "IOCalloutDevice" = "/dev/cu.Bluetooth-Incoming-Port"
      "IODialinDevice" = "/dev/tty.Bluetooth-Incoming-Port"
      "IOCalloutDevice" = "/dev/cu.PT-D610BT1654"
      "IODialinDevice" = "/dev/tty.PT-D610BT1654"

2026-07-21T03:42:00.212030Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "find app -maxdepth 3 -type f \\( -name 'Package.swift' -o -name 'project.pbxproj' -o -name '*.yml' \\) -print; sed -n '1,180p' app/Package.swift 2>/dev/null || true; rg -n \"ConnectView\" app" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
app/project.yml
app/tenorrekey.xcodeproj/project.pbxproj
app/Sources/Views/ConnectView.swift:9:struct ConnectView: View {
app/Sources/Shell/RootView.swift:182:            ConnectView().environment(model).environment(theme).environment(l)

2026-07-21T03:42:55.012387Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "sed -n '1,220p' app/project.yml; rg -n \"ConnectView|Views/.*swift|Sources/Views\" app/tenorrekey.xcodeproj/project.pbxproj | sed -n '1,120p'; sed -n '1,220p' app/tools/package.sh" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
name: tenorrekey
options:
  bundleIdPrefix: vn.tenor
  deploymentTarget:
    macOS: "14.0"
  createIntermediateGroups: true
settings:
  base:
    SWIFT_VERSION: "5.0"
    MARKETING_VERSION: "1.0.0"
    CURRENT_PROJECT_VERSION: "1"
targets:
  tenorrekey:
    type: application
    platform: macOS
    sources:
      - Sources
      - Assets.xcassets
      - Resources/Fonts
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: vn.tenor.rekey
        PRODUCT_NAME: tenorrekey
        GENERATE_INFOPLIST_FILE: "YES"
        INFOPLIST_KEY_CFBundleDisplayName: "tenor/rekey"
        # CFBundleName (menu bar / app switcher) is derived by Xcode from
        # PRODUCT_NAME (kept slash-free for the on-disk binary); package.sh patches
        # it to the brand name "tenor/rekey" before signing.
        INFOPLIST_KEY_LSApplicationCategoryType: "public.app-category.utilities"
        INFOPLIST_KEY_NSHumanReadableCopyright: ""
        ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon
        CODE_SIGN_IDENTITY: "-"
        CODE_SIGNING_REQUIRED: "NO"
        ENABLE_HARDENED_RUNTIME: "NO"
        DEAD_CODE_STRIPPING: "YES"
schemes:
  tenorrekey:
    build:
      targets:
        tenorrekey: all
    run:
      config: Debug
13:		142CFC99D523E71ACD14E2AE /* ConnectView.swift in Sources */ = {isa = PBXBuildFile; fileRef = F7085E6DAAE21E389B70A262 /* ConnectView.swift */; };
91:		F7085E6DAAE21E389B70A262 /* ConnectView.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = ConnectView.swift; sourceTree = "<group>"; };
145:				F7085E6DAAE21E389B70A262 /* ConnectView.swift */,
289:				142CFC99D523E71ACD14E2AE /* ConnectView.swift in Sources */,
#!/usr/bin/env bash
# Package tenor/rekey into a self-contained, relocatable .app + a drag-to-
# Applications .dmg. The shipped bundle carries its own python runtime, the
# probe engine + key dictionary, and libhidapi - so it runs on a clean macOS
# account with no Homebrew, no Command Line Tools, nothing on PATH.
#
# Signing: a Developer ID Application cert (hardened runtime + entitlements) is
# used automatically if one is installed, else ad-hoc (runs on this Mac, not
# distributable). With a Developer ID cert + a stored notarytool credential the
# build also notarizes + staples the app and the dmg.
#
#   usage:  app/tools/package.sh                       # ad-hoc or Developer-ID-signed
#           NOTARY_PROFILE=tenor-notary app/tools/package.sh   # also notarize + staple
#   (one-time, by the founder, since it needs the Apple ID app-specific password:
#    xcrun notarytool store-credentials tenor-notary \
#        --apple-id <id> --team-id 35ZXMV2YHU --password <app-specific-password>)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$HERE/.." && pwd)"          # .../app
REPO="$(cd "$APP_DIR/.." && pwd)"          # repo root
PROBE="$REPO/probe"
DIST="$APP_DIR/dist"
CACHE="$DIST/.cache"
BUILD="$APP_DIR/build"

# Relocatable CPython (python-build-standalone, install_only = full stdlib + ctypes).
PY_VER="3.12.13"
PY_TAG="20260610"
PY_TARBALL="cpython-${PY_VER}+${PY_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_TARBALL}"
# Pinned to the GitHub release asset digest - this exact runtime gets baked into a
# signed bundle, so a swapped tarball would be silently shipped. Verified each run,
# including the cache, so a poisoned cache cannot survive.
PY_SHA256="f0a7fa7decc75df2b1a789329a44f657c4a15c0a683f197ce46a5cb621bc6ef4"

# Runtime engine modules (each daemon + its import graph) and the dictionary. Two
# device stacks share the bundle: x7 (X7 reader) and chameleon (Chameleon Ultra/Lite).
# learned_keys + dict are shared by both. The chameleon daemon additionally needs the
# vendored chameleon/ package + the built crackers, copied as trees in step 3.
PROBE_MODULES=(x7d.py x7lib.py x7.py x7hid.py x7_init.py x7crypto.py crapto1.py learned_keys.py
               chameleon_d.py chameleon_crack.py)

echo "==> 1/7  build release .app"
cd "$APP_DIR"
xcodegen generate >/dev/null
xcodebuild -project tenorrekey.xcodeproj -scheme tenorrekey -configuration Release \
    -derivedDataPath build CODE_SIGNING_ALLOWED=NO build >/dev/null
APP="$BUILD/Build/Products/Release/tenorrekey.app"
[ -d "$APP" ] || { echo "build produced no app at $APP"; exit 1; }

STAGE="$DIST/tenor-rekey.app"
rm -rf "$STAGE"; mkdir -p "$DIST"
cp -R "$APP" "$STAGE"
# Brand the menu-bar / app-switcher name to "tenor/rekey" (the slash displays
# fine; the on-disk binary stays PRODUCT_NAME). Done before signing so the
# signature covers it.
/usr/libexec/PlistBuddy -c "Set :CFBundleName tenor/rekey" "$STAGE/Contents/Info.plist"
RES="$STAGE/Contents/Resources"
FW="$STAGE/Contents/Frameworks"
mkdir -p "$FW"

echo "==> 2/7  vendor python runtime"
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$PY_TARBALL" ]; then
    echo "    downloading $PY_TARBALL"
    curl -fsSL "$PY_URL" -o "$CACHE/$PY_TARBALL"
fi
echo "$PY_SHA256  $CACHE/$PY_TARBALL" | shasum -a 256 -c - >/dev/null \
    || { echo "python runtime checksum mismatch - refusing to bundle"; rm -f "$CACHE/$PY_TARBALL"; exit 1; }
rm -rf "$RES/python"
tar -xzf "$CACHE/$PY_TARBALL" -C "$RES"        # extracts a 'python' dir
[ -x "$RES/python/bin/python3" ] || { echo "python/bin/python3 missing after extract"; exit 1; }
# trim test suites + caches to keep the bundle lean (engine never imports them)
find "$RES/python/lib" -type d -name "test" -prune -exec rm -rf {} + 2>/dev/null || true
find "$RES/python/lib" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

# Chameleon daemon runtime deps into the bundled runtime: pyserial (the serial
# transport, imported lazily by the daemon + chameleon package) and adafruit-nrfutil
# (the Nordic Secure DFU firmware flasher, invoked as the `adafruit-nrfutil` console
# entry point). Both are pure-python; pip drops them into the relocatable runtime's
# site-packages and the step-5 .pyc precompile seals them alongside the stdlib.
echo "    pip install chameleon deps (pyserial, adafruit-nrfutil)"
"$RES/python/bin/python3" -m pip install --no-cache-dir --no-warn-script-location \
    --disable-pip-version-check pyserial adafruit-nrfutil >/dev/null
# pip writes the adafruit-nrfutil console script with an ABSOLUTE shebang baked to the
# build-time interpreter path, which no longer exists once the .app is relocated (drag
# to /Applications, or run from the dmg). Replace it with a relocatable POSIX wrapper
# that execs the bundled python sitting next to it, so the flasher runs under the
# bundled runtime from any install location. The daemon calls it by name
# (subprocess ["adafruit-nrfutil", ...]); this keeps that name pointing at the entry
# point (nordicsemi.__main__:cli) regardless of where the bundle lands.
ANR="$RES/python/bin/adafruit-nrfutil"
[ -f "$ANR" ] || { echo "adafruit-nrfutil entry point missing after pip install"; exit 1; }
printf '#!/bin/sh\nexec "$(dirname "$0")/python3" -m nordicsemi "$@"\n' > "$ANR"
chmod +x "$ANR"

echo "==> 3/7  vendor probe engine + dictionary"
rm -rf "$RES/probe"; mkdir -p "$RES/probe/dict"
for m in "${PROBE_MODULES[@]}"; do cp "$PROBE/$m" "$RES/probe/"; done
cp "$PROBE/dict/mfc_keys.dic" "$RES/probe/dict/"
# Chameleon stack: the vendored upstream engine package (chameleon_d.py imports
# chameleon.chameleon_com / _cmd / _enum / _utils) + the built host-side crackers.
# chameleon_crack.py resolves the binaries at native/chameleon/bin RELATIVE to itself
# (CHAMELEON_BIN overrides), so mirror that exact layout under the bundled probe.
cp -R "$PROBE/chameleon" "$RES/probe/chameleon"
find "$RES/probe/chameleon" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$RES/probe/native/chameleon/bin"
# bin/ is gitignored, so a clean checkout has no cracker binaries: build them from the
# tracked sources first, then require all four to exist before bundling.
bash "$PROBE/native/chameleon/build.sh" || { echo "cracker build failed"; exit 1; }
for b in nested staticnested darkside hardnested; do
    src="$PROBE/native/chameleon/bin/$b"
    [ -x "$src" ] || { echo "cracker $b not built ($src)"; exit 1; }
    cp "$src" "$RES/probe/native/chameleon/bin/$b"
    chmod +x "$RES/probe/native/chameleon/bin/$b"          # keep the executable bit
done
# liblzma: hardnested links the macOS SYSTEM /usr/lib/liblzma.5.dylib (always present
# on macOS); the other three crackers link only libSystem. Nothing to bundle for it.

echo "==> 4/7  vendor libhidapi"
HIDAPI_SRC=""
for c in /opt/homebrew/lib/libhidapi.dylib /usr/local/lib/libhidapi.dylib; do
    [ -e "$c" ] && { HIDAPI_SRC="$(readlink -f "$c" 2>/dev/null || python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$c")"; break; }
done
[ -n "$HIDAPI_SRC" ] || { echo "libhidapi not found (brew install hidapi)"; exit 1; }
cp "$HIDAPI_SRC" "$FW/libhidapi.dylib"
install_name_tool -id "@rpath/libhidapi.dylib" "$FW/libhidapi.dylib" 2>/dev/null || true

echo "==> 5/7  pre-compile + sign"
# Pre-generate every .pyc now so they are sealed by the signature; the app also
# launches python with PYTHONDONTWRITEBYTECODE=1 so it never writes one at runtime
# (a code-signed bundle that mutates itself breaks its own seal).
"$RES/python/bin/python3" -m compileall -q "$RES/python/lib" "$RES/probe" >/dev/null 2>&1 || true

# Use a Developer ID Application cert if one is installed (notarizable: hardened
# runtime + entitlements + secure timestamp); otherwise ad-hoc (runs on this Mac,
# but cannot be notarized). An "Apple Development" cert is NOT a Developer ID cert.
SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Developer ID Application[^"]*\)".*/\1/p' | head -1)"
ENT="$HERE/tenorrekey.entitlements"
if [ -n "$SIGN_ID" ]; then
    echo "    Developer ID: $SIGN_ID  (hardened runtime + entitlements)"
    SIGN=(codesign --force --timestamp --options runtime -s "$SIGN_ID")
    SIGN_ENT=(codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$SIGN_ID")
else
    echo "    no Developer ID Application cert found - ad-hoc signing (NOT notarizable)"
    SIGN=(codesign --force -s -)
    SIGN_ENT=(codesign --force -s -)
fi
# sign inside-out: the dylib + every mach-o the python tree ships, then the
# interpreter and the app last (the interpreter + app carry the entitlements; the
# interpreter is the process that loads the bundled libs via ctypes).
"${SIGN[@]}" "$FW/libhidapi.dylib"
find "$RES/python" \( -name "*.dylib" -o -name "*.so" \) -exec "${SIGN[@]}" {} + 2>/dev/null || true
# The vendored Chameleon crackers are standalone Mach-O helpers spawned as subprocess
# (pure compute, no entitlements needed). Sign them so a hardened/notarizable build
# and a strict verify cover every mach-o in the bundle.
for b in nested staticnested darkside hardnested; do
    "${SIGN[@]}" "$RES/probe/native/chameleon/bin/$b" || { echo "codesign failed: cracker $b"; exit 1; }
    codesign --verify --strict "$RES/probe/native/chameleon/bin/$b" || { echo "codesign verify failed: cracker $b"; exit 1; }
done
find "$RES/python/bin" -type f -perm -111 -exec "${SIGN_ENT[@]}" {} + 2>/dev/null || true
"${SIGN_ENT[@]}" "$STAGE/Contents/MacOS/tenorrekey" 2>/dev/null || true
"${SIGN_ENT[@]}" "$STAGE"
codesign --verify --strict --deep "$STAGE" && echo "    codesign verify OK"

# Notarize the .app now (before the dmg) so the dmg ships a stapled app that runs
# offline. Needs a Developer ID signature AND a stored notarytool credential -
# create it once (the founder, it needs the Apple ID app-specific password):
#   xcrun notarytool store-credentials tenor-notary \
#       --apple-id <id> --team-id 35ZXMV2YHU --password <app-specific-password>
# then run:  NOTARY_PROFILE=tenor-notary app/tools/package.sh
NOTARIZE=0
if [ -n "$SIGN_ID" ] && [ -n "${NOTARY_PROFILE:-}" ]; then
    NOTARIZE=1
    echo "==> 6/7  notarize + staple app  (profile: $NOTARY_PROFILE)"
    APPZIP="$DIST/.notarize-app.zip"
    ditto -c -k --keepParent "$STAGE" "$APPZIP"
    xcrun notarytool submit "$APPZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$STAGE"
    rm -f "$APPZIP"
else
    echo "==> 6/7  notarize  SKIPPED (need a Developer ID cert + NOTARY_PROFILE=<profile>)"
fi

echo "==> 7/7  build dmg (styled drag-to-Applications)"
DMG="$DIST/tenor-rekey.dmg"
VOL="tenor rekey"
DSTAGE="$DIST/.dmgstage"; RW="$DIST/.rw.dmg"
python3 "$HERE/dmg_background.py" >/dev/null     # writes $DIST/.dmgbg/background.tiff
rm -rf "$DSTAGE" "$RW" "$DMG"; mkdir -p "$DSTAGE/.background"
cp -R "$STAGE" "$DSTAGE/tenor-rekey.app"
ln -s /Applications "$DSTAGE/Applications"
cp "$DIST/.dmgbg/background.tiff" "$DSTAGE/.background/background.tiff"
ICNS="$STAGE/Contents/Resources/AppIcon.icns"
[ -f "$ICNS" ] && cp "$ICNS" "$DSTAGE/.VolumeIcon.icns"
# writable image -> lay it out in Finder (background + icon slots) -> compress.
hdiutil create -srcfolder "$DSTAGE" -volname "$VOL" -fs HFS+ -format UDRW -ov "$RW" >/dev/null
DEV="$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | egrep '^/dev/' | head -1 | awk '{print $1}')"
sleep 1
osascript <<APPLESCRIPT >/dev/null 2>&1 || echo "    (Finder layout skipped - automation not permitted; dmg still valid)"
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 860, 562}
    set vo to the icon view options of container window
    set arrangement of vo to not arranged
    set icon size of vo to 128
    set text size of vo to 12
    set background picture of vo to file ".background:background.tiff"
    set position of item "tenor-rekey.app" of container window to {175, 205}
    set position of item "Applications" of container window to {485, 205}
    update without registering applications
    delay 1
    close
  end tell
end tell

2026-07-21T03:43:00.803059Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "git --no-pager diff -- app probe | LC_ALL=C rg -n '[–—]' || true; LC_ALL=C rg -n '[–—]' app/Sources/Views/ConnectView.swift || true" in /Users/tuan/Claude/Tenor/tenor-rekey
2026-07-21T03:43:14.002324Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
 succeeded in 1743ms:
2026-07-21 10:43:12.415 xcodebuild[41778:3242788]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:43:12.551 xcodebuild[41778:3242787] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-cvdQTb66' (errno=Operation not permitted)
2026-07-21 10:43:13.004 xcodebuild[41781:3242804]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 10:43:13.142 xcodebuild[41781:3242802] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.

codex
1. [DeviceRegistry.swift:169](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/DeviceRegistry.swift:169) - HIGH  
   A DFU device reporting vendor `Proxgrind` or a product containing `Chameleon` matches the normal `chameleonUltra` descriptor before `chameleonDFU` is evaluated. `detect()` therefore selects the normal daemon path, `detectAll()` can show both descriptors for one device, and firmware recovery remains hidden because `deviceInDFU` is false. The daemon then rejects the DFU port and connection fails.  
   Minimal fix: exclude VID:PID `1915:521f` from the normal Chameleon string fallback. Reordering descriptors alone does not fix the duplicate `detectAll()` result.

2. [AppModel.swift:246](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:246) - MED  
   Rescan unconditionally discards a working manual pin. If auto-detection originally failed and the user successfully pinned `/dev/cu.usbmodemX`, pressing Rescan produces the X7 fallback and tears down the working Chameleon connection. With multiple readers it may switch to a different device.  
   Minimal fix: preserve and reconnect the current pin while that port still exists. Provide a separate explicit action for returning to automatic selection.

3. [AppModel.swift:325](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:325) - MED  
   Once `portOverride` is set, every detected-device swap is suppressed. If the pinned Chameleon is unplugged and an X7 is plugged in, the monitor keeps polling the dead pinned daemon forever instead of hot-swapping to the X7. Rescan recovers it, but the normal 1.5-second hot-swap path has regressed.  
   Minimal fix: suppress auto-swaps only while the pinned `/dev` path is still present. Once it disappears, allow normal detection to select the X7, a renamed Chameleon port, or DFU.

4. [ConnectView.swift:120](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/ConnectView.swift:120) - LOW  
   Manual-connect controls remain enabled during a device operation. `connectManual()` then returns at its `deviceBusy` guard, but the popover is dismissed immediately, making the attempted connection silently do nothing. Rescan similarly remains clickable but becomes a no-op.  
   Minimal fix: expose whether device changes are allowed and disable these controls, or make `connectManual` report whether it accepted the request and dismiss only on success.

5. [ConnectView.swift:204](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/ConnectView.swift:204) - MED  
   The DFU row is disabled, but the same DFU path can be entered manually. That creates a normal Chameleon descriptor; `CHAMELEON_PORT` then bypasses `_find_port()` and [chameleon_d.py:587](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon_d.py:587) opens the DFU port as an application-protocol port. Combined with the pinned-monitor rule, it remains misclassified until Rescan.  
   Minimal fix: have `connectManual` recognize a listed `1915:521f` port and route it to `chameleonDFU`, or reject it. The daemon should also fail closed when an explicit port is known to be DFU.

Gate verdict: not sound yet. A normal Chameleon that exposes a USB serial callout is recoverable through the manual port row, but the DFU misclassification and sticky-pin state failures are real blockers. If macOS exposes no `/dev/cu.*` callout at all, this UI still cannot connect it.

The IOKit ancestor walks are bounded and their retain/release ownership is balanced. The queried property keys are valid macOS registry keys.
tokens used
131,441
1. [DeviceRegistry.swift:169](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/DeviceRegistry.swift:169) - HIGH  
   A DFU device reporting vendor `Proxgrind` or a product containing `Chameleon` matches the normal `chameleonUltra` descriptor before `chameleonDFU` is evaluated. `detect()` therefore selects the normal daemon path, `detectAll()` can show both descriptors for one device, and firmware recovery remains hidden because `deviceInDFU` is false. The daemon then rejects the DFU port and connection fails.  
   Minimal fix: exclude VID:PID `1915:521f` from the normal Chameleon string fallback. Reordering descriptors alone does not fix the duplicate `detectAll()` result.

2. [AppModel.swift:246](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:246) - MED  
   Rescan unconditionally discards a working manual pin. If auto-detection originally failed and the user successfully pinned `/dev/cu.usbmodemX`, pressing Rescan produces the X7 fallback and tears down the working Chameleon connection. With multiple readers it may switch to a different device.  
   Minimal fix: preserve and reconnect the current pin while that port still exists. Provide a separate explicit action for returning to automatic selection.

3. [AppModel.swift:325](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:325) - MED  
   Once `portOverride` is set, every detected-device swap is suppressed. If the pinned Chameleon is unplugged and an X7 is plugged in, the monitor keeps polling the dead pinned daemon forever instead of hot-swapping to the X7. Rescan recovers it, but the normal 1.5-second hot-swap path has regressed.  
   Minimal fix: suppress auto-swaps only while the pinned `/dev` path is still present. Once it disappears, allow normal detection to select the X7, a renamed Chameleon port, or DFU.

4. [ConnectView.swift:120](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/ConnectView.swift:120) - LOW  
   Manual-connect controls remain enabled during a device operation. `connectManual()` then returns at its `deviceBusy` guard, but the popover is dismissed immediately, making the attempted connection silently do nothing. Rescan similarly remains clickable but becomes a no-op.  
   Minimal fix: expose whether device changes are allowed and disable these controls, or make `connectManual` report whether it accepted the request and dismiss only on success.

5. [ConnectView.swift:204](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/ConnectView.swift:204) - MED  
   The DFU row is disabled, but the same DFU path can be entered manually. That creates a normal Chameleon descriptor; `CHAMELEON_PORT` then bypasses `_find_port()` and [chameleon_d.py:587](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon_d.py:587) opens the DFU port as an application-protocol port. Combined with the pinned-monitor rule, it remains misclassified until Rescan.  
   Minimal fix: have `connectManual` recognize a listed `1915:521f` port and route it to `chameleonDFU`, or reject it. The daemon should also fail closed when an explicit port is known to be DFU.

Gate verdict: not sound yet. A normal Chameleon that exposes a USB serial callout is recoverable through the manual port row, but the DFU misclassification and sticky-pin state failures are real blockers. If macOS exposes no `/dev/cu.*` callout at all, this UI still cannot connect it.

The IOKit ancestor walks are bounded and their retain/release ownership is balanced. The queried property keys are valid macOS registry keys.
