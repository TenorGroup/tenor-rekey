Reading additional input from stdin...
2026-07-21T04:27:19.046394Z ERROR codex_models_manager::cache: failed to load models cache: missing field `supports_reasoning_summaries` at line 88 column 5
OpenAI Codex v0.144.1
--------
workdir: /Users/tuan/Claude/Tenor/tenor-rekey
model: gpt-5.6-sol
provider: openai
approval: never
sandbox: read-only
reasoning effort: high
reasoning summaries: none
session id: 019f82ed-a41c-7063-a541-6c434bb43254
--------
user
You are a rigorous macOS/Swift + Python reviewer acting as the founder's proxy gate on an UNCOMMITTED change to the tenor/rekey app. The app drives a Chameleon Ultra (RFID) via a Python daemon (chameleon_d.py) that speaks a framed command protocol over a transport. This change adds Bluetooth LE support.

ARCHITECTURE (verify it is sound, do not assume): the Chameleon speaks the SAME protocol over BLE's Nordic UART Service (NUS) as over USB-CDC. The daemon's transport (probe/chameleon/chameleon_com.py, open()) already supports a `tcp:HOST:PORT` port. So BLE is done as: the Swift app (BLEManager.swift) owns the CoreBluetooth radio (the app process holds Bluetooth TCC), connects NUS, and runs a loopback NWListener TCP bridge that relays bytes between the daemon's TCP socket and the BLE RX(write 6E400002)/TX(notify 6E400003) characteristics. On connect it writes a 10-byte handshake to RX. The daemon is then spawned with CHAMELEON_PORT=tcp:127.0.0.1:<port> (existing portOverride mechanism), so it opens the tcp bridge and its whole protocol stack is reused unchanged.

Inspect the uncommitted diff and the new file yourself:
  git --no-pager diff -- app
  cat app/Sources/Engine/BLEManager.swift
Read context as needed: app/Sources/AppModel.swift (ble, bleConnect, pinnedPortPresent, swapDevice, monitor, connectManual, canChangeDevice), app/Sources/Views/ConnectView.swift (bluetoothSection), app/Sources/Engine/DeviceBridge.swift (CHAMELEON_PORT env), probe/chameleon/chameleon_com.py (open() tcp path, thread_data_receive/transfer), app/project.yml + app/tools/tenorrekey.entitlements (Info.plist key + entitlement).

Give ONLY real, actionable findings (file:line, severity CRITICAL/HIGH/MED/LOW, concrete failure scenario, minimal fix). Skip praise and style nits unless they cause bugs. Bounded questions:
1. BLEManager CoreBluetooth correctness: the central is created with queue: .main and delegates are `nonisolated func` bodies wrapped in `MainActor.assumeIsolated` - is that actually safe (are these callbacks guaranteed on the main thread)? Any connectContinuation double-resume or leak across didConnect/didFail/didDisconnect/timeout/radio-off. Any force-unwrap that can crash on a hardware race.
2. TCP bridge (NWListener/NWConnection): is the byte relay correct and lossless in BOTH directions? MTU chunking of TCP->BLE writes (maximumWriteValueLength, .withoutResponse); does a >MTU daemon frame get split correctly; is there flow-control/backpressure or ordering risk; is exactly one inbound connection handled; are listener/connection torn down without leak or deadlock; can teardown race the receive loop.
3. BLE lifecycle in AppModel: bleConnect (pre-disconnect on switch, build tcp descriptor, swapDevice), the swapDevice teardown `if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }`, and pinnedPortPresent returning ble.isConnected for a tcp: pin. Trace: USB->BLE, BLE->USB, BLE-A->BLE-B, tapping the same BLE device twice, a BLE link that drops mid-session (didDisconnectPeripheral), a connect that throws mid-way. Can a live link be killed, or a dead one leave the app stuck on a BLE descriptor with no recovery?
4. Daemon side: opening tcp:127.0.0.1:<port> - any framing/timeout/threading mismatch vs serial; does the DFU fail-closed guard in _connect wrongly trip on a tcp: dev; does anything require a change that was missed. Confirm the reused protocol path is actually transport-agnostic here.
5. macOS reality: with NSBluetoothAlwaysUsageDescription in Info.plist + com.apple.security.device.bluetooth entitlement, will CoreBluetooth in the APP process get permission (vs a child python being denied)? Is there any residual reason a real Chameleon over BLE would fail to connect through this path (permission, MTU, handshake, the bridge, the daemon open)? Name it precisely.

Be blunt. If sound, say so plainly. If you would bullshit, do not bother.
codex
I’ll trace the diff and the specific lifecycle and transport paths you named, then report only reproducible failure modes with minimal fixes.
exec
/bin/zsh -lc "git status --short && git --no-pager diff -- app && sed -n '1,320p' app/Sources/Engine/BLEManager.swift" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 3307ms:
2026-07-21 11:27:33.137 xcodebuild[45798:3375052]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:33.271 xcodebuild[45798:3375051] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-UZcLyqX8' (errno=Operation not permitted)
2026-07-21 11:27:33.702 xcodebuild[45800:3375062]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:33.833 xcodebuild[45800:3375061] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
 M app/Sources/AppModel.swift
 M app/Sources/Brand/L10n.swift
 M app/Sources/Views/ConnectView.swift
 M app/project.yml
 M app/tools/tenorrekey.entitlements
?? AUDIT_CHAMELEON.md
?? DEVICE_COMPAT.md
?? SPEC_CHAMELEON.md
?? X7_AUDIT_FINDINGS.md
?? app/Sources/Engine/BLEManager.swift
?? exploration/
?? probe/native/darkside/
?? probe/x7_reader.py
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-5ls1e6fx' (errno=Operation not permitted)
2026-07-21 11:27:34.814 xcodebuild[45807:3375103]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:34.943 xcodebuild[45807:3375102] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-KXNcJgDZ' (errno=Operation not permitted)
2026-07-21 11:27:35.376 xcodebuild[45810:3375117]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:35.507 xcodebuild[45810:3375116] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/app/Sources/AppModel.swift b/app/Sources/AppModel.swift
index c2a0610..6f0a8e4 100644
--- a/app/Sources/AppModel.swift
+++ b/app/Sources/AppModel.swift
@@ -39,6 +39,11 @@ final class AppModel {
     var detectedDevices: [DeviceDescriptor] = []
     /// Every enumerated USB serial port (the manual-connect list).
     var serialPorts: [SerialPortInfo] = []
+    /// The Bluetooth LE radio for a wireless Chameleon link. Nested @Observable, so a view
+    /// that reads `model.ble.state` / `model.ble.devices` re-renders on its changes. A BLE
+    /// link is never auto-detected (it is not on the USB bus); it is opened only by the
+    /// explicit `bleConnect` from the Connect surface.
+    let ble = BLEManager()
 
     /// The working DOCUMENT: the image produced by a decode or loaded from a file.
     /// It is what the canvas shows, what Save writes out, and what Write clones onto
@@ -252,6 +257,11 @@ final class AppModel {
     /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
     private func pinnedPortPresent() -> Bool {
         guard let pinned = descriptor.portOverride else { return false }
+        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
+        // serial-port scan below would report it absent and the monitor would swap away from a
+        // live wireless link. It is "present" for as long as the manager holds the link; once
+        // the BLE link drops, this reads false and a normal hot-swap is allowed.
+        if pinned.hasPrefix("tcp:") { return ble.isConnected }
         return USBProbe.serialPorts().contains { $0.path == pinned }
     }
 
@@ -296,6 +306,47 @@ final class AppModel {
         refreshConnectLists()
     }
 
+    // ---- Bluetooth LE connect ----------------------------------------------
+
+    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
+    /// surface drives the radio without importing the manager directly.
+    func bleStartScan() { ble.startScan() }
+    func bleStopScan() { ble.stopScan() }
+
+    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
+    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
+    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
+    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
+    ///
+    /// A device advertising the Nordic DFU service is refused here (the view also hides its
+    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
+    /// be recovered over USB.
+    func bleConnect(_ id: String) async {
+        guard canChangeDevice else { return }
+        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
+        // Clean switch between BLE devices: drop any prior link before opening a new one.
+        if ble.isConnected { ble.disconnect() }
+        do {
+            let port = try await ble.connect(id)
+            let base = DeviceRegistry.chameleonUltra
+            let desc = DeviceDescriptor(
+                id: "chameleon-ble:\(id)",
+                family: base.family,
+                displayName: base.displayName,
+                daemonScript: base.daemonScript,
+                probeSubdir: base.probeSubdir,
+                usbMatch: base.usbMatch,
+                capabilities: base.capabilities,
+                portOverride: "tcp:127.0.0.1:\(port)")
+            await swapDevice(to: desc)
+            ble.stopScan()
+            refreshConnectLists()
+        } catch {
+            lastError = "\(error)"
+            ble.disconnect()
+        }
+    }
+
     /// Bring up the daemon for the active `descriptor`: read device info + key counts,
     /// then sample the reader. Shared by the first connect and a hot-swap, so both
     /// paths land the same state (info, capabilities via `info`, reader/card status).
@@ -384,6 +435,10 @@ final class AppModel {
         guard !swapping else { return }
         swapping = true
         defer { swapping = false }              // released however this returns
+        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
+        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
+        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
+        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
         let old = bridge
         bridge = nil                            // detach: no path can obtain the old bridge now
         descriptor = found
@@ -396,6 +451,12 @@ final class AppModel {
         }
         await old?.shutdown()                   // bounded terminate + drain of the old daemon
         await openCurrentDevice()               // creates + brings up the new bridge
+        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
+        // now that the old daemon is gone and the new device is open. Gated on the NEW device
+        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
+        // the old link and brought up the new one before this swap, so disconnecting here
+        // would kill the freshly-connected link. Idempotent.
+        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
     }
 
     /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
diff --git a/app/Sources/Brand/L10n.swift b/app/Sources/Brand/L10n.swift
index 0ac5470..061766e 100644
--- a/app/Sources/Brand/L10n.swift
+++ b/app/Sources/Brand/L10n.swift
@@ -264,6 +264,12 @@ final class L10n {
         "no_device_hint":     ["vi": "chưa thấy thiết bị nào. kiểm tra cáp là loại truyền dữ liệu (không phải cáp chỉ sạc) và thử cổng usb khác.", "en": "no device detected - check the cable is a data cable, not charge-only, and try another usb port.", "zh": "未检测到设备 - 请确认使用的是数据线而非仅充电线，并换一个 usb 口试试。", "ja": "デバイスが見つかりません - ケーブルが充電専用ではなくデータ用か確認し、別の usb ポートを試してください。"],
         "unrecognized_port_hint": ["vi": "tìm thấy cổng serial nhưng không phải đầu đọc nào được nhận diện. nếu đây là chameleon, hãy kết nối thủ công, hoặc kiểm tra bạn đang dùng cáp truyền dữ liệu.", "en": "found serial ports but none is a recognized reader - if this is your chameleon connect it manually, or check you are using a data cable.", "zh": "找到串口但没有可识别的读卡器 - 若这是你的 chameleon，请手动连接，或确认使用的是数据线。", "ja": "シリアルポートは見つかりましたが認識できる読み取り機がありません - これがお使いの chameleon なら手動で接続するか、データ用ケーブルか確認してください。"],
         "bluetooth":          ["vi": "bluetooth", "en": "bluetooth", "zh": "蓝牙", "ja": "bluetooth"],
-        "bluetooth_later":    ["vi": "sẽ thêm ở bản cập nhật sau", "en": "added in a later update", "zh": "将在后续更新中加入", "ja": "今後のアップデートで追加"],
+        "bt_scan":            ["vi": "quét", "en": "scan", "zh": "扫描", "ja": "スキャン"],
+        "bt_stop":            ["vi": "dừng", "en": "stop", "zh": "停止", "ja": "停止"],
+        "bt_none":            ["vi": "chưa thấy chameleon nào qua bluetooth", "en": "no chameleon found over bluetooth", "zh": "未通过蓝牙发现 chameleon", "ja": "bluetooth で chameleon が見つかりません"],
+        "bt_off":             ["vi": "bluetooth đang tắt - bật lên để kết nối không dây", "en": "bluetooth is off - turn it on to connect wirelessly", "zh": "蓝牙已关闭 - 打开后即可无线连接", "ja": "bluetooth がオフです - オンにすると無線で接続できます"],
+        "bt_unauthorized":    ["vi": "chưa cấp quyền bluetooth - bật trong cài đặt hệ thống > quyền riêng tư & bảo mật > bluetooth", "en": "bluetooth permission is off - enable it in system settings > privacy & security > bluetooth", "zh": "未授予蓝牙权限 - 请在系统设置 > 隐私与安全性 > 蓝牙中启用", "ja": "bluetooth の権限がオフです - システム設定 > プライバシーとセキュリティ > bluetooth で有効にしてください"],
+        "bt_unsupported":     ["vi": "máy mac này không có bluetooth le", "en": "this mac has no bluetooth le", "zh": "此 mac 不支持 bluetooth le", "ja": "この mac には bluetooth le がありません"],
+        "bt_dfu_note":        ["vi": "cập nhật firmware qua usb, không qua bluetooth", "en": "update firmware over usb, not bluetooth", "zh": "请通过 usb 更新固件，而非蓝牙", "ja": "ファームウェアは bluetooth ではなく usb で更新してください"],
     ]
 }
diff --git a/app/Sources/Views/ConnectView.swift b/app/Sources/Views/ConnectView.swift
index 9438c55..db9c4f9 100644
--- a/app/Sources/Views/ConnectView.swift
+++ b/app/Sources/Views/ConnectView.swift
@@ -1,11 +1,11 @@
 import SwiftUI
 
-/// The Connect surface, opened from the header status pill or the empty state. USB
-/// only for now: it lists the known readers present on the bus, lets the user RESCAN,
-/// and manually pin a serial port when auto-detect does not recognise the device (the
-/// Chameleon-over-USB case). Bluetooth is a deliberately DISABLED placeholder for a
-/// later pass. Instrument aesthetic: hairlines, muted tokens, mono for machine
-/// identifiers (port paths), sans for chrome, signal via glyph + weight, no alarm colour.
+/// The Connect surface, opened from the header status pill or the empty state. It lists
+/// the known readers present on the USB bus, lets the user RESCAN, manually pin a serial
+/// port when auto-detect does not recognise the device (the Chameleon-over-USB case), and
+/// scan for + connect a Chameleon over Bluetooth LE. Instrument aesthetic: hairlines, muted
+/// tokens, mono for machine identifiers (port paths, rssi), sans for chrome, signal via
+/// glyph + weight, no alarm colour.
 struct ConnectView: View {
     @Environment(AppModel.self) private var model
     @Environment(Theme.self) private var theme
@@ -24,12 +24,26 @@ struct ConnectView: View {
                 divider
                 diagnostics(hint)
             }
-            divider
-            bluetoothRow
+            bluetoothSection
         }
         .frame(width: 340)
         .background(theme.p.panel)
-        .onAppear { model.refreshConnectLists() }
+        .onAppear {
+            model.refreshConnectLists()
+            // Kick the BLE radio so its state (and any advertisers) become known while the
+            // Connect surface is open. startScan lazily creates the central - which is what
+            // learns the state and raises the one-time permission prompt - and only actually
+            // scans once powered on, so it is safe in every state. Skip it when we already
+            // know the radio is off / unauthorized / unsupported (no point re-probing), and
+            // never while a swap or device op owns the reader.
+            if model.canChangeDevice,
+               model.ble.state != .poweredOff,
+               model.ble.state != .unauthorized,
+               model.ble.state != .unsupported {
+                model.bleStartScan()
+            }
+        }
+        .onDisappear { model.bleStopScan() }
     }
 
     // MARK: - header
@@ -151,7 +165,7 @@ struct ConnectView: View {
         return parts.isEmpty ? nil : parts.joined(separator: " · ")
     }
 
-    // MARK: - diagnostics + bluetooth placeholder
+    // MARK: - diagnostics
 
     private func diagnostics(_ hint: String) -> some View {
         HStack(spacing: 7) {
@@ -170,18 +184,112 @@ struct ConnectView: View {
         return model.serialPorts.isEmpty ? l.t("no_device_hint") : l.t("unrecognized_port_hint")
     }
 
-    private var bluetoothRow: some View {
+    // MARK: - bluetooth le
+
+    /// The BLE section, keyed on the radio state. A live section with a scan control + the
+    /// discovered advertisers when powered on; an honest one-line hint when the radio is off /
+    /// unauthorized / unsupported; nothing at all while the state is not yet known. Each
+    /// non-empty state carries its own leading divider so a trailing hairline never dangles
+    /// under an empty section.
+    @ViewBuilder
+    private var bluetoothSection: some View {
+        switch model.ble.state {
+        case .poweredOn:
+            divider
+            bluetoothOnSection
+        case .unauthorized:
+            divider
+            bluetoothHint(l.t("bt_unauthorized"))
+        case .poweredOff:
+            divider
+            bluetoothHint(l.t("bt_off"))
+        case .unsupported:
+            divider
+            bluetoothHint(l.t("bt_unsupported"))
+        case .unknown:
+            EmptyView()
+        }
+    }
+
+    private var bluetoothOnSection: some View {
+        VStack(alignment: .leading, spacing: 8) {
+            HStack {
+                sectionLabel(l.t("bluetooth"))
+                Spacer()
+                scanControl
+            }
+            if model.ble.devices.isEmpty {
+                Text(l.t("bt_none")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
+                    .fixedSize(horizontal: false, vertical: true)
+            } else {
+                ForEach(model.ble.devices) { bleRow($0) }
+            }
+        }
+        .padding(.horizontal, 16).padding(.vertical, 12)
+        .frame(maxWidth: .infinity, alignment: .leading)
+    }
+
+    /// Toggle the scan: start when idle, stop while scanning (a small spinner marks the live
+    /// scan). Disabled while a swap / device op owns the reader, the same guard bleConnect enforces.
+    private var scanControl: some View {
+        Button {
+            if model.ble.scanning { model.bleStopScan() }
+            else if model.canChangeDevice { model.bleStartScan() }
+        } label: {
+            HStack(spacing: 5) {
+                if model.ble.scanning { ProgressView().controlSize(.small) }
+                Text(model.ble.scanning ? l.t("bt_stop") : l.t("bt_scan")).font(l.sans(11, .medium))
+            }
+            .padding(.horizontal, 9).frame(height: 26)
+            .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
+            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
+            .foregroundStyle(theme.p.textPrimary)
+        }
+        .buttonStyle(.plain).disabled(!model.canChangeDevice)
+    }
+
+    /// One discovered advertiser. A non-DFU row connects (bring up the link, then dismiss); a
+    /// DFU (bootloader) row is disabled with a "use usb for firmware" hint, since firmware over
+    /// BLE is out of scope. The accent dot marks the currently-connected device.
+    private func bleRow(_ d: BLEDevice) -> some View {
+        Button {
+            guard !d.isDFU, model.canChangeDevice else { return }
+            Task { await model.bleConnect(d.id) }
+            dismiss()
+        } label: {
+            HStack(spacing: 9) {
+                Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 12))
+                    .foregroundStyle(theme.p.textSecondary).frame(width: 16)
+                Text(d.name).font(l.sans(12)).foregroundStyle(theme.p.textPrimary).lineLimit(1)
+                if d.isDFU { tag("DFU") }
+                Spacer()
+                Text("\(d.rssi) dBm").font(Typeface.mono(10)).foregroundStyle(theme.p.textTertiary)
+                if model.ble.connectedDeviceID == d.id {
+                    Circle().fill(theme.p.accent).frame(width: 6, height: 6)
+                }
+            }
+            .padding(.horizontal, 10).frame(minHeight: 36)
+            .frame(maxWidth: .infinity, alignment: .leading)
+            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(d.isDFU ? 0.3 : 0.6)))
+            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
+            .contentShape(Rectangle())
+        }
+        .buttonStyle(.plain).disabled(d.isDFU || !model.canChangeDevice)
+        .help(d.isDFU ? l.t("bt_dfu_note") : l.t("manual_connect"))
+    }
+
+    /// A one-line BLE state hint (radio off / unauthorized / unsupported), styled like the
+    /// diagnostics line, so an off or denied radio reads honestly instead of a dead placeholder.
+    private func bluetoothHint(_ text: String) -> some View {
         HStack(spacing: 9) {
             Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 12))
                 .foregroundStyle(theme.p.textTertiary).frame(width: 16)
-            VStack(alignment: .leading, spacing: 2) {
-                Text(l.t("bluetooth")).font(l.sans(12)).foregroundStyle(theme.p.textTertiary)
-                Text(l.t("bluetooth_later")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
-            }
-            Spacer()
+            Text(text).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
+                .fixedSize(horizontal: false, vertical: true)
+            Spacer(minLength: 0)
         }
         .padding(.horizontal, 16).padding(.vertical, 12)
-        .opacity(0.7)
+        .frame(maxWidth: .infinity, alignment: .leading)
     }
 
     // MARK: - shared
diff --git a/app/project.yml b/app/project.yml
index 243e0ad..74af190 100644
--- a/app/project.yml
+++ b/app/project.yml
@@ -28,6 +28,7 @@ targets:
         # it to the brand name "tenor/rekey" before signing.
         INFOPLIST_KEY_LSApplicationCategoryType: "public.app-category.utilities"
         INFOPLIST_KEY_NSHumanReadableCopyright: ""
+        INFOPLIST_KEY_NSBluetoothAlwaysUsageDescription: "tenor/rekey connects to a Chameleon over Bluetooth."
         ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon
         CODE_SIGN_IDENTITY: "-"
         CODE_SIGNING_REQUIRED: "NO"
diff --git a/app/tools/tenorrekey.entitlements b/app/tools/tenorrekey.entitlements
index 052baca..146285a 100644
--- a/app/tools/tenorrekey.entitlements
+++ b/app/tools/tenorrekey.entitlements
@@ -5,7 +5,8 @@
      ctypes, so it needs library-validation disabled (those libraries are not all
      signed by this Team) and unsigned-executable-memory allowed (ctypes / the
      embedded interpreter). No App Sandbox: this is a local engineering tool that
-     opens a USB-HID device, which the sandbox would block. -->
+     opens a USB-HID device, which the sandbox would block, and it uses CoreBluetooth
+     to reach a Chameleon over Bluetooth LE (the device.bluetooth entitlement). -->
 <plist version="1.0">
 <dict>
     <key>com.apple.security.cs.disable-library-validation</key>
@@ -14,5 +15,7 @@
     <true/>
     <key>com.apple.security.cs.allow-dyld-environment-variables</key>
     <true/>
+    <key>com.apple.security.device.bluetooth</key>
+    <true/>
 </dict>
 </plist>
import Foundation
import CoreBluetooth
import Network

/// A discovered Bluetooth LE advertiser that speaks (or, in the bootloader, would speak)
/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
/// is sitting in its bootloader.
struct BLEDevice: Identifiable, Equatable, Sendable {
    let id: String        // CBPeripheral.identifier.uuidString
    let name: String      // peripheral.name, else the advertised local name, else "Chameleon"
    let rssi: Int
    let isDFU: Bool       // advertised the FE59 DFU service (in the Nordic bootloader)
}

/// A radio-power / authorization state mirror of CBManagerState, so the UI can show a
/// "Bluetooth off" / "not authorized" state without importing CoreBluetooth.
enum BLEState: Equatable, Sendable { case unknown, unsupported, unauthorized, poweredOff, poweredOn }

/// Owns the Bluetooth LE radio for a Chameleon link. The Swift app process legitimately
/// holds the Bluetooth TCC permission, so the BLE side lives here; the Python daemon speaks
/// the SAME command protocol it uses over USB-CDC serial, so we do not re-implement it.
/// Instead `connect` brings up a loopback TCP server (Network framework) that relays raw
/// bytes to / from the NUS characteristics, and returns the 127.0.0.1 port the daemon opens
/// with its existing `tcp:HOST:PORT` transport.
///
/// The central is created with the MAIN dispatch queue, so every CoreBluetooth delegate
/// callback lands on the main thread and can hop onto this @MainActor with
/// `MainActor.assumeIsolated`. The TCP relay runs on its own queue and hops back via
/// `Task { @MainActor in ... }`. No CoreBluetooth prompt is raised until `startScan` first
/// creates the central, so the app does not ask for Bluetooth at launch.
@MainActor
@Observable
final class BLEManager: NSObject {
    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
    private static let nusService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
    private static let nusRX = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  // WRITE to device
    private static let nusTX = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  // NOTIFY from device
    private static let dfuService = CBUUID(string: "FE59")
    /// Written to RX right after subscribing to TX notify, to bring the link up.
    private static let handshake = Data([0x11, 0xEF, 0x03, 0xFB, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])

    // ---- Public, observable state ------------------------------------------
    /// The radio state, mapped from CBManagerState by the central's delegate.
    private(set) var state: BLEState = .unknown
    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
    private(set) var devices: [BLEDevice] = []
    private(set) var scanning = false
    /// Non-nil while a BLE link + its loopback bridge are up.
    private(set) var connectedDeviceID: String?
    var isConnected: Bool { connectedDeviceID != nil }

    // ---- CoreBluetooth ------------------------------------------------------
    private var central: CBCentralManager?
    /// Every peripheral seen while scanning, keyed by id, so `connect` can find it.
    private var discovered: [String: CBPeripheral] = [:]
    /// A scan was requested before the radio was powered on; begin it once it is.
    private var scanRequested = false
    /// The peripheral of the active (or in-flight) link.
    private var peripheral: CBPeripheral?
    private var rxChar: CBCharacteristic?
    private var txChar: CBCharacteristic?
    /// The write chunk size for the data path (max write length for a no-response write),
    /// falling back to 20 when the peripheral reports 0.
    private var maxWriteLen = 20

    /// The connect() continuation, resolved by the delegate chain or the timeout. Non-nil
    /// only while a connect is in flight; guards every fail / succeed path so there is no
    /// double-resume.
    private var connectContinuation: CheckedContinuation<Int, Error>?
    private var connectTimeoutTask: Task<Void, Never>?

    // ---- Loopback TCP bridge (Network framework) ---------------------------
    private var listener: NWListener?
    private var tcpConnection: NWConnection?
    private let bridgeQueue = DispatchQueue(label: "vn.tenor.rekey.ble-bridge")

    // ---- Scanning ----------------------------------------------------------

    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
    /// call (this is what raises the Bluetooth permission prompt, not app launch). If the
    /// radio is not powered on yet, the scan begins from `centralManagerDidUpdateState`.
    func startScan() {
        if central == nil {
            central = CBCentralManager(delegate: self, queue: .main)
        }
        scanRequested = true
        if central?.state == .poweredOn { beginScan() }
    }

    /// Stop scanning and clear the pending-scan intent.
    func stopScan() {
        scanRequested = false
        central?.stopScan()
        scanning = false
    }

    private func beginScan() {
        guard let central, central.state == .poweredOn else { return }
        devices = []
        discovered = [:]
        central.scanForPeripherals(withServices: [Self.nusService, Self.dfuService],
                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        scanning = true
    }

    // ---- Connect / disconnect ----------------------------------------------

    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
    /// service / characteristic, or a stall past the ~15s overall timeout.
    func connect(_ id: String) async throws -> Int {
        if central == nil {
            central = CBCentralManager(delegate: self, queue: .main)
        }
        guard let central, state == .poweredOn else { throw BLEError.notPoweredOn }
        guard connectContinuation == nil else { throw BLEError.busy }

        let target: CBPeripheral
        if let p = discovered[id] {
            target = p
        } else if let uuid = UUID(uuidString: id),
                  let p = central.retrievePeripherals(withIdentifiers: [uuid]).first {
            target = p
            discovered[id] = p
        } else {
            throw BLEError.unknownDevice
        }

        stopScan()
        teardownLink()   // drop any prior link before opening a new one

        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Int, Error>) in
            self.connectContinuation = cont
            self.peripheral = target
            target.delegate = self
            // One overall deadline: any stalled step (connect, discover, notify, bridge)
            // fails the whole connect rather than orphaning the continuation.
            self.connectTimeoutTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(15))
                self?.failConnect(BLEError.timeout)
            }
            central.connect(target, options: nil)
        }
    }

    /// Tear the link down: cancel the peripheral connection, stop + close the listener and
    /// TCP connection, clear `connectedDeviceID`. Idempotent, and if a connect is still in
    /// flight it fails that continuation instead.
    func disconnect() {
        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
        if connectContinuation != nil {
            failConnect(BLEError.disconnected)
            return
        }
        teardownLink()
    }

    /// Resolve the in-flight connect with the bound port. No-op if there is no pending
    /// continuation (already resolved / torn down).
    private func succeedConnect(port: Int) {
        guard let cont = connectContinuation else { return }
        connectContinuation = nil
        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
        connectedDeviceID = peripheral?.identifier.uuidString
        cont.resume(returning: port)
    }

    /// Fail the in-flight connect and tear the partial link down. No-op if there is no
    /// pending continuation, so it is safe to call from any delegate error path.
    private func failConnect(_ error: Error) {
        guard let cont = connectContinuation else { return }
        connectContinuation = nil
        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
        teardownLink()
        cont.resume(throwing: error)
    }

    /// Synchronous teardown of the BLE link + its bridge. Shared by disconnect, a fail, a
    /// radio-off, and a peripheral drop, so they cannot drift.
    private func teardownLink() {
        teardownBridge()
        if let p = peripheral, let central { central.cancelPeripheralConnection(p) }
        peripheral = nil
        rxChar = nil
        txChar = nil
        connectedDeviceID = nil
    }

    private func teardownBridge() {
        tcpConnection?.cancel()
        tcpConnection = nil
        listener?.cancel()
        listener = nil
    }

    // ---- Loopback TCP bridge -----------------------------------------------

    /// Bind an NWListener to 127.0.0.1 on an OS-picked ephemeral port and, once it is ready,
    /// report the bound port via `onReady`. Accepts exactly one inbound connection (the
    /// daemon) and relays bytes both ways.
    private func startBridge(onReady: @escaping @MainActor (Result<Int, Error>) -> Void) {
        let params = NWParameters.tcp
        // Bind specifically to loopback on an ephemeral port (port 0 -> OS assigns).
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: 0)
        let listener: NWListener
        do {
            listener = try NWListener(using: params)
        } catch {
            onReady(.failure(error))
            return
        }
        self.listener = listener
        listener.stateUpdateHandler = { [weak self] st in
            switch st {
            case .ready:
                let raw = listener.port?.rawValue
                Task { @MainActor in
                    guard self != nil else { return }
                    if let raw { onReady(.success(Int(raw))) }
                    else { onReady(.failure(BLEError.bridge)) }
                }
            case .failed:
                Task { @MainActor in
                    guard let self else { return }
                    if self.connectContinuation != nil { onReady(.failure(BLEError.bridge)) }
                    else { self.disconnect() }
                }
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] conn in
            Task { @MainActor in self?.acceptConnection(conn) }
        }
        listener.start(queue: bridgeQueue)
    }

    /// Accept exactly one inbound connection (the daemon); cancel any later one. Start the
    /// receive loop that relays daemon -> BLE bytes.
    private func acceptConnection(_ conn: NWConnection) {
        guard tcpConnection == nil else { conn.cancel(); return }
        tcpConnection = conn
        conn.stateUpdateHandler = { [weak self] st in
            switch st {
            case .failed, .cancelled:
                Task { @MainActor in self?.handleTCPClosed(conn) }
            default:
                break
            }
        }
        conn.start(queue: bridgeQueue)
        receiveLoop(conn)
    }

    /// One turn of the daemon -> BLE relay: read whatever bytes arrived and write them to the
    /// BLE RX characteristic, then re-arm. The receive completion runs on the bridge queue and
    /// hops back onto the main actor for the BLE write and the next turn.
    private func receiveLoop(_ conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            if let data, !data.isEmpty {
                Task { @MainActor in self?.writeToBLE(data) }
            }
            if isComplete || error != nil {
                Task { @MainActor in self?.handleTCPClosed(conn) }
            } else {
                Task { @MainActor in self?.receiveLoop(conn) }
            }
        }
    }

    /// Relay daemon bytes to the device: chunk to the peripheral's max no-response write
    /// length and write each chunk without response (the data path after the handshake).
    private func writeToBLE(_ data: Data) {
        guard let peripheral, let rx = rxChar else { return }
        let chunk = maxWriteLen > 0 ? maxWriteLen : 20
        var offset = 0
        while offset < data.count {
            let end = min(offset + chunk, data.count)
            peripheral.writeValue(data.subdata(in: offset..<end), for: rx, type: .withoutResponse)
            offset = end
        }
    }

    /// Relay device notify bytes to the daemon over TCP.
    private func sendToTCP(_ data: Data) {
        guard let conn = tcpConnection else { return }
        conn.send(content: data, completion: .contentProcessed { _ in })
    }

    /// The daemon closed / dropped its side: tear the whole link down (the listener only ever
    /// accepts one connection, so a reconnect needs a fresh link, which phase 2 owns).
    private func handleTCPClosed(_ conn: NWConnection) {
        guard conn === tcpConnection else { return }
        disconnect()
    }

    // ---- State mapping ------------------------------------------------------

    private static func mapState(_ s: CBManagerState) -> BLEState {
        switch s {
        case .poweredOn: return .poweredOn
        case .poweredOff: return .poweredOff
        case .unauthorized: return .unauthorized
        case .unsupported: return .unsupported
        default: return .unknown
        }
    }

    enum BLEError: Error, CustomStringConvertible {
        case notPoweredOn, unknownDevice, serviceNotFound, characteristicNotFound
        case connectionFailed, disconnected, timeout, bridge, busy
        var description: String {
            switch self {
            case .notPoweredOn:          return "Bluetooth is not powered on"
            case .unknownDevice:         return "unknown Bluetooth device"
            case .serviceNotFound:       return "the device does not expose the Chameleon UART service"
            case .characteristicNotFound: return "the Chameleon UART characteristics were not found"
            case .connectionFailed:      return "the Bluetooth connection failed"
            case .disconnected:          return "the Bluetooth device disconnected"

exec
/bin/zsh -lc "nl -ba app/Sources/Engine/BLEManager.swift | sed -n '1,520p'; nl -ba app/Sources/AppModel.swift | sed -n '210,490p'; nl -ba app/Sources/Engine/DeviceBridge.swift | sed -n '1,280p'; nl -ba probe/chameleon/chameleon_com.py | sed -n '1,360p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import Foundation
     2	import CoreBluetooth
     3	import Network
     4	
     5	/// A discovered Bluetooth LE advertiser that speaks (or, in the bootloader, would speak)
     6	/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
     7	/// is sitting in its bootloader.
     8	struct BLEDevice: Identifiable, Equatable, Sendable {
     9	    let id: String        // CBPeripheral.identifier.uuidString
    10	    let name: String      // peripheral.name, else the advertised local name, else "Chameleon"
    11	    let rssi: Int
    12	    let isDFU: Bool       // advertised the FE59 DFU service (in the Nordic bootloader)
    13	}
    14	
    15	/// A radio-power / authorization state mirror of CBManagerState, so the UI can show a
    16	/// "Bluetooth off" / "not authorized" state without importing CoreBluetooth.
    17	enum BLEState: Equatable, Sendable { case unknown, unsupported, unauthorized, poweredOff, poweredOn }
    18	
    19	/// Owns the Bluetooth LE radio for a Chameleon link. The Swift app process legitimately
    20	/// holds the Bluetooth TCC permission, so the BLE side lives here; the Python daemon speaks
    21	/// the SAME command protocol it uses over USB-CDC serial, so we do not re-implement it.
    22	/// Instead `connect` brings up a loopback TCP server (Network framework) that relays raw
    23	/// bytes to / from the NUS characteristics, and returns the 127.0.0.1 port the daemon opens
    24	/// with its existing `tcp:HOST:PORT` transport.
    25	///
    26	/// The central is created with the MAIN dispatch queue, so every CoreBluetooth delegate
    27	/// callback lands on the main thread and can hop onto this @MainActor with
    28	/// `MainActor.assumeIsolated`. The TCP relay runs on its own queue and hops back via
    29	/// `Task { @MainActor in ... }`. No CoreBluetooth prompt is raised until `startScan` first
    30	/// creates the central, so the app does not ask for Bluetooth at launch.
    31	@MainActor
    32	@Observable
    33	final class BLEManager: NSObject {
    34	    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
    35	    private static let nusService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
    36	    private static let nusRX = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  // WRITE to device
    37	    private static let nusTX = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  // NOTIFY from device
    38	    private static let dfuService = CBUUID(string: "FE59")
    39	    /// Written to RX right after subscribing to TX notify, to bring the link up.
    40	    private static let handshake = Data([0x11, 0xEF, 0x03, 0xFB, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])
    41	
    42	    // ---- Public, observable state ------------------------------------------
    43	    /// The radio state, mapped from CBManagerState by the central's delegate.
    44	    private(set) var state: BLEState = .unknown
    45	    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
    46	    private(set) var devices: [BLEDevice] = []
    47	    private(set) var scanning = false
    48	    /// Non-nil while a BLE link + its loopback bridge are up.
    49	    private(set) var connectedDeviceID: String?
    50	    var isConnected: Bool { connectedDeviceID != nil }
    51	
    52	    // ---- CoreBluetooth ------------------------------------------------------
    53	    private var central: CBCentralManager?
    54	    /// Every peripheral seen while scanning, keyed by id, so `connect` can find it.
    55	    private var discovered: [String: CBPeripheral] = [:]
    56	    /// A scan was requested before the radio was powered on; begin it once it is.
    57	    private var scanRequested = false
    58	    /// The peripheral of the active (or in-flight) link.
    59	    private var peripheral: CBPeripheral?
    60	    private var rxChar: CBCharacteristic?
    61	    private var txChar: CBCharacteristic?
    62	    /// The write chunk size for the data path (max write length for a no-response write),
    63	    /// falling back to 20 when the peripheral reports 0.
    64	    private var maxWriteLen = 20
    65	
    66	    /// The connect() continuation, resolved by the delegate chain or the timeout. Non-nil
    67	    /// only while a connect is in flight; guards every fail / succeed path so there is no
    68	    /// double-resume.
    69	    private var connectContinuation: CheckedContinuation<Int, Error>?
    70	    private var connectTimeoutTask: Task<Void, Never>?
    71	
    72	    // ---- Loopback TCP bridge (Network framework) ---------------------------
    73	    private var listener: NWListener?
    74	    private var tcpConnection: NWConnection?
    75	    private let bridgeQueue = DispatchQueue(label: "vn.tenor.rekey.ble-bridge")
    76	
    77	    // ---- Scanning ----------------------------------------------------------
    78	
    79	    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
    80	    /// call (this is what raises the Bluetooth permission prompt, not app launch). If the
    81	    /// radio is not powered on yet, the scan begins from `centralManagerDidUpdateState`.
    82	    func startScan() {
    83	        if central == nil {
    84	            central = CBCentralManager(delegate: self, queue: .main)
    85	        }
    86	        scanRequested = true
    87	        if central?.state == .poweredOn { beginScan() }
    88	    }
    89	
    90	    /// Stop scanning and clear the pending-scan intent.
    91	    func stopScan() {
    92	        scanRequested = false
    93	        central?.stopScan()
    94	        scanning = false
    95	    }
    96	
    97	    private func beginScan() {
    98	        guard let central, central.state == .poweredOn else { return }
    99	        devices = []
   100	        discovered = [:]
   101	        central.scanForPeripherals(withServices: [Self.nusService, Self.dfuService],
   102	                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
   103	        scanning = true
   104	    }
   105	
   106	    // ---- Connect / disconnect ----------------------------------------------
   107	
   108	    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
   109	    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
   110	    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
   111	    /// service / characteristic, or a stall past the ~15s overall timeout.
   112	    func connect(_ id: String) async throws -> Int {
   113	        if central == nil {
   114	            central = CBCentralManager(delegate: self, queue: .main)
   115	        }
   116	        guard let central, state == .poweredOn else { throw BLEError.notPoweredOn }
   117	        guard connectContinuation == nil else { throw BLEError.busy }
   118	
   119	        let target: CBPeripheral
   120	        if let p = discovered[id] {
   121	            target = p
   122	        } else if let uuid = UUID(uuidString: id),
   123	                  let p = central.retrievePeripherals(withIdentifiers: [uuid]).first {
   124	            target = p
   125	            discovered[id] = p
   126	        } else {
   127	            throw BLEError.unknownDevice
   128	        }
   129	
   130	        stopScan()
   131	        teardownLink()   // drop any prior link before opening a new one
   132	
   133	        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Int, Error>) in
   134	            self.connectContinuation = cont
   135	            self.peripheral = target
   136	            target.delegate = self
   137	            // One overall deadline: any stalled step (connect, discover, notify, bridge)
   138	            // fails the whole connect rather than orphaning the continuation.
   139	            self.connectTimeoutTask = Task { [weak self] in
   140	                try? await Task.sleep(for: .seconds(15))
   141	                self?.failConnect(BLEError.timeout)
   142	            }
   143	            central.connect(target, options: nil)
   144	        }
   145	    }
   146	
   147	    /// Tear the link down: cancel the peripheral connection, stop + close the listener and
   148	    /// TCP connection, clear `connectedDeviceID`. Idempotent, and if a connect is still in
   149	    /// flight it fails that continuation instead.
   150	    func disconnect() {
   151	        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
   152	        if connectContinuation != nil {
   153	            failConnect(BLEError.disconnected)
   154	            return
   155	        }
   156	        teardownLink()
   157	    }
   158	
   159	    /// Resolve the in-flight connect with the bound port. No-op if there is no pending
   160	    /// continuation (already resolved / torn down).
   161	    private func succeedConnect(port: Int) {
   162	        guard let cont = connectContinuation else { return }
   163	        connectContinuation = nil
   164	        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
   165	        connectedDeviceID = peripheral?.identifier.uuidString
   166	        cont.resume(returning: port)
   167	    }
   168	
   169	    /// Fail the in-flight connect and tear the partial link down. No-op if there is no
   170	    /// pending continuation, so it is safe to call from any delegate error path.
   171	    private func failConnect(_ error: Error) {
   172	        guard let cont = connectContinuation else { return }
   173	        connectContinuation = nil
   174	        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
   175	        teardownLink()
   176	        cont.resume(throwing: error)
   177	    }
   178	
   179	    /// Synchronous teardown of the BLE link + its bridge. Shared by disconnect, a fail, a
   180	    /// radio-off, and a peripheral drop, so they cannot drift.
   181	    private func teardownLink() {
   182	        teardownBridge()
   183	        if let p = peripheral, let central { central.cancelPeripheralConnection(p) }
   184	        peripheral = nil
   185	        rxChar = nil
   186	        txChar = nil
   187	        connectedDeviceID = nil
   188	    }
   189	
   190	    private func teardownBridge() {
   191	        tcpConnection?.cancel()
   192	        tcpConnection = nil
   193	        listener?.cancel()
   194	        listener = nil
   195	    }
   196	
   197	    // ---- Loopback TCP bridge -----------------------------------------------
   198	
   199	    /// Bind an NWListener to 127.0.0.1 on an OS-picked ephemeral port and, once it is ready,
   200	    /// report the bound port via `onReady`. Accepts exactly one inbound connection (the
   201	    /// daemon) and relays bytes both ways.
   202	    private func startBridge(onReady: @escaping @MainActor (Result<Int, Error>) -> Void) {
   203	        let params = NWParameters.tcp
   204	        // Bind specifically to loopback on an ephemeral port (port 0 -> OS assigns).
   205	        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: 0)
   206	        let listener: NWListener
   207	        do {
   208	            listener = try NWListener(using: params)
   209	        } catch {
   210	            onReady(.failure(error))
   211	            return
   212	        }
   213	        self.listener = listener
   214	        listener.stateUpdateHandler = { [weak self] st in
   215	            switch st {
   216	            case .ready:
   217	                let raw = listener.port?.rawValue
   218	                Task { @MainActor in
   219	                    guard self != nil else { return }
   220	                    if let raw { onReady(.success(Int(raw))) }
   221	                    else { onReady(.failure(BLEError.bridge)) }
   222	                }
   223	            case .failed:
   224	                Task { @MainActor in
   225	                    guard let self else { return }
   226	                    if self.connectContinuation != nil { onReady(.failure(BLEError.bridge)) }
   227	                    else { self.disconnect() }
   228	                }
   229	            default:
   230	                break
   231	            }
   232	        }
   233	        listener.newConnectionHandler = { [weak self] conn in
   234	            Task { @MainActor in self?.acceptConnection(conn) }
   235	        }
   236	        listener.start(queue: bridgeQueue)
   237	    }
   238	
   239	    /// Accept exactly one inbound connection (the daemon); cancel any later one. Start the
   240	    /// receive loop that relays daemon -> BLE bytes.
   241	    private func acceptConnection(_ conn: NWConnection) {
   242	        guard tcpConnection == nil else { conn.cancel(); return }
   243	        tcpConnection = conn
   244	        conn.stateUpdateHandler = { [weak self] st in
   245	            switch st {
   246	            case .failed, .cancelled:
   247	                Task { @MainActor in self?.handleTCPClosed(conn) }
   248	            default:
   249	                break
   250	            }
   251	        }
   252	        conn.start(queue: bridgeQueue)
   253	        receiveLoop(conn)
   254	    }
   255	
   256	    /// One turn of the daemon -> BLE relay: read whatever bytes arrived and write them to the
   257	    /// BLE RX characteristic, then re-arm. The receive completion runs on the bridge queue and
   258	    /// hops back onto the main actor for the BLE write and the next turn.
   259	    private func receiveLoop(_ conn: NWConnection) {
   260	        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
   261	            if let data, !data.isEmpty {
   262	                Task { @MainActor in self?.writeToBLE(data) }
   263	            }
   264	            if isComplete || error != nil {
   265	                Task { @MainActor in self?.handleTCPClosed(conn) }
   266	            } else {
   267	                Task { @MainActor in self?.receiveLoop(conn) }
   268	            }
   269	        }
   270	    }
   271	
   272	    /// Relay daemon bytes to the device: chunk to the peripheral's max no-response write
   273	    /// length and write each chunk without response (the data path after the handshake).
   274	    private func writeToBLE(_ data: Data) {
   275	        guard let peripheral, let rx = rxChar else { return }
   276	        let chunk = maxWriteLen > 0 ? maxWriteLen : 20
   277	        var offset = 0
   278	        while offset < data.count {
   279	            let end = min(offset + chunk, data.count)
   280	            peripheral.writeValue(data.subdata(in: offset..<end), for: rx, type: .withoutResponse)
   281	            offset = end
   282	        }
   283	    }
   284	
   285	    /// Relay device notify bytes to the daemon over TCP.
   286	    private func sendToTCP(_ data: Data) {
   287	        guard let conn = tcpConnection else { return }
   288	        conn.send(content: data, completion: .contentProcessed { _ in })
   289	    }
   290	
   291	    /// The daemon closed / dropped its side: tear the whole link down (the listener only ever
   292	    /// accepts one connection, so a reconnect needs a fresh link, which phase 2 owns).
   293	    private func handleTCPClosed(_ conn: NWConnection) {
   294	        guard conn === tcpConnection else { return }
   295	        disconnect()
   296	    }
   297	
   298	    // ---- State mapping ------------------------------------------------------
   299	
   300	    private static func mapState(_ s: CBManagerState) -> BLEState {
   301	        switch s {
   302	        case .poweredOn: return .poweredOn
   303	        case .poweredOff: return .poweredOff
   304	        case .unauthorized: return .unauthorized
   305	        case .unsupported: return .unsupported
   306	        default: return .unknown
   307	        }
   308	    }
   309	
   310	    enum BLEError: Error, CustomStringConvertible {
   311	        case notPoweredOn, unknownDevice, serviceNotFound, characteristicNotFound
   312	        case connectionFailed, disconnected, timeout, bridge, busy
   313	        var description: String {
   314	            switch self {
   315	            case .notPoweredOn:          return "Bluetooth is not powered on"
   316	            case .unknownDevice:         return "unknown Bluetooth device"
   317	            case .serviceNotFound:       return "the device does not expose the Chameleon UART service"
   318	            case .characteristicNotFound: return "the Chameleon UART characteristics were not found"
   319	            case .connectionFailed:      return "the Bluetooth connection failed"
   320	            case .disconnected:          return "the Bluetooth device disconnected"
   321	            case .timeout:               return "the Bluetooth connection timed out"
   322	            case .bridge:                return "the local bridge could not be started"
   323	            case .busy:                  return "a connection is already in progress"
   324	            }
   325	        }
   326	    }
   327	}
   328	
   329	// MARK: - CBCentralManagerDelegate
   330	
   331	extension BLEManager: CBCentralManagerDelegate {
   332	    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
   333	        MainActor.assumeIsolated {
   334	            self.state = Self.mapState(central.state)
   335	            if central.state == .poweredOn {
   336	                if self.scanRequested { self.beginScan() }
   337	            } else {
   338	                self.scanning = false
   339	                if self.connectContinuation != nil {
   340	                    self.failConnect(BLEError.notPoweredOn)
   341	                } else if self.connectedDeviceID != nil {
   342	                    self.teardownLink()
   343	                }
   344	            }
   345	        }
   346	    }
   347	
   348	    nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
   349	                                    advertisementData: [String: Any], rssi RSSI: NSNumber) {
   350	        MainActor.assumeIsolated {
   351	            let id = peripheral.identifier.uuidString
   352	            self.discovered[id] = peripheral
   353	            let advName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
   354	            let name = peripheral.name ?? advName ?? "Chameleon"
   355	            var isDFU = false
   356	            if let services = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] {
   357	                isDFU = services.contains(Self.dfuService)
   358	            }
   359	            let device = BLEDevice(id: id, name: name, rssi: RSSI.intValue, isDFU: isDFU)
   360	            if let idx = self.devices.firstIndex(where: { $0.id == id }) {
   361	                self.devices[idx] = device
   362	            } else {
   363	                self.devices.append(device)
   364	            }
   365	            self.devices.sort { $0.rssi > $1.rssi }
   366	        }
   367	    }
   368	
   369	    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
   370	        MainActor.assumeIsolated {
   371	            peripheral.discoverServices([Self.nusService])
   372	        }
   373	    }
   374	
   375	    nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
   376	        MainActor.assumeIsolated {
   377	            self.failConnect(BLEError.connectionFailed)
   378	        }
   379	    }
   380	
   381	    nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
   382	        MainActor.assumeIsolated {
   383	            if self.connectContinuation != nil {
   384	                self.failConnect(BLEError.disconnected)
   385	            } else if self.connectedDeviceID == peripheral.identifier.uuidString {
   386	                self.teardownLink()
   387	            }
   388	        }
   389	    }
   390	}
   391	
   392	// MARK: - CBPeripheralDelegate
   393	
   394	extension BLEManager: CBPeripheralDelegate {
   395	    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
   396	        MainActor.assumeIsolated {
   397	            if error != nil { self.failConnect(BLEError.serviceNotFound); return }
   398	            guard let svc = peripheral.services?.first(where: { $0.uuid == Self.nusService }) else {
   399	                self.failConnect(BLEError.serviceNotFound); return
   400	            }
   401	            peripheral.discoverCharacteristics([Self.nusRX, Self.nusTX], for: svc)
   402	        }
   403	    }
   404	
   405	    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
   406	        MainActor.assumeIsolated {
   407	            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
   408	            let chars = service.characteristics ?? []
   409	            guard let rx = chars.first(where: { $0.uuid == Self.nusRX }),
   410	                  let tx = chars.first(where: { $0.uuid == Self.nusTX }) else {
   411	                self.failConnect(BLEError.characteristicNotFound); return
   412	            }
   413	            self.rxChar = rx
   414	            self.txChar = tx
   415	            let m = peripheral.maximumWriteValueLength(for: .withoutResponse)
   416	            self.maxWriteLen = m > 0 ? m : 20
   417	            peripheral.setNotifyValue(true, for: tx)
   418	        }
   419	    }
   420	
   421	    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
   422	        MainActor.assumeIsolated {
   423	            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
   424	            guard characteristic.uuid == Self.nusTX, characteristic.isNotifying else { return }
   425	            guard let rx = self.rxChar else { self.failConnect(BLEError.characteristicNotFound); return }
   426	            // Bring the link up: handshake first (with response), then the loopback bridge.
   427	            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
   428	            self.startBridge { result in
   429	                switch result {
   430	                case .success(let port): self.succeedConnect(port: port)
   431	                case .failure:
   432	                    if self.connectContinuation != nil { self.failConnect(BLEError.bridge) }
   433	                    else { self.disconnect() }
   434	                }
   435	            }
   436	        }
   437	    }
   438	
   439	    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
   440	        MainActor.assumeIsolated {
   441	            guard characteristic.uuid == Self.nusTX, let data = characteristic.value, !data.isEmpty else { return }
   442	            self.sendToTCP(data)
   443	        }
   444	    }
   445	}
   210	    /// Format erases the card on the reader, so it is only offered when the card
   211	    /// present is the one this document was decoded from (same uid): only then do we
   212	    /// hold its recovered keys to auth it, and only then is wiping it unambiguous.
   213	    var canFormat: Bool {
   214	        guard let c = card?.uid, let d = source?.uid else { return false }
   215	        return Self.normUID(c) == Self.normUID(d)
   216	    }
   217	
   218	    static func normUID(_ s: String) -> String {
   219	        s.replacingOccurrences(of: " ", with: "").lowercased()
   220	    }
   221	
   222	    /// Detect the connected device, then start its daemon + read device info and look
   223	    /// for a card (connect at launch, not lazily). With no device detected we fall
   224	    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
   225	    ///
   226	    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
   227	    /// reconnect can never replace the bridge under a running decode / clone. When the
   228	    /// detected device differs from the current one it routes through `swapDevice` so
   229	    /// the old daemon is torn down (never silently orphaned) under the swap guard.
   230	    func connect() async {
   231	        guard !swapping, !deviceBusy else { return }
   232	        await detectAndOpen()
   233	    }
   234	
   235	    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
   236	    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
   237	    /// is torn down, and bring the current one up.
   238	    private func detectAndOpen() async {
   239	        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
   240	        if bridge != nil, found.id != descriptor.id {
   241	            await swapDevice(to: found)
   242	            return
   243	        }
   244	        descriptor = found
   245	        await openCurrentDevice()
   246	    }
   247	
   248	    /// Refresh the Connect surface's lists (known present devices + all USB serial
   249	    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
   250	    func refreshConnectLists() {
   251	        detectedDevices = DeviceRegistry.detectAll()
   252	        serialPorts = USBProbe.serialPorts()
   253	    }
   254	
   255	    /// True when a manual serial port is pinned (portOverride) AND that exact /dev path is
   256	    /// still enumerated on the USB bus; false when nothing is pinned. Lets rescan / the
   257	    /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
   258	    private func pinnedPortPresent() -> Bool {
   259	        guard let pinned = descriptor.portOverride else { return false }
   260	        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
   261	        // serial-port scan below would report it absent and the monitor would swap away from a
   262	        // live wireless link. It is "present" for as long as the manager holds the link; once
   263	        // the BLE link drops, this reads false and a normal hot-swap is allowed.
   264	        if pinned.hasPrefix("tcp:") { return ble.isConnected }
   265	        return USBProbe.serialPorts().contains { $0.path == pinned }
   266	    }
   267	
   268	    /// The Connect surface's Rescan: refresh the lists and re-run detection without waiting
   269	    /// for the 1.5s monitor tick. A PRESENT manual pin is preserved - re-open the SAME pinned
   270	    /// device rather than running auto-detect, which (when it does not recognise the
   271	    /// Chameleon) would fall to the X7 fallback and tear down the working port the user pinned
   272	    /// precisely because auto-detect fails. Only with no pin, or a pinned port that has
   273	    /// disappeared, do we re-detect.
   274	    func rescan() async {
   275	        guard !swapping, !deviceBusy else { return }
   276	        refreshConnectLists()
   277	        if descriptor.portOverride != nil, pinnedPortPresent() {
   278	            await openCurrentDevice()
   279	        } else {
   280	            await detectAndOpen()
   281	        }
   282	        refreshConnectLists()
   283	    }
   284	
   285	    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
   286	    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
   287	    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
   288	    func connectManual(port: String) async {
   289	        guard !swapping, !deviceBusy else { return }
   290	        // A port that enumerates as a device in the Nordic bootloader (isDFU) is pinned from
   291	        // the chameleon-dfu descriptor, not chameleonUltra: then deviceInDFU is true and
   292	        // openCurrentDevice takes the DFU branch (firmware recovery reachable) instead of
   293	        // trying the app protocol on a bootloader port. A non-DFU port pins as chameleonUltra.
   294	        let isDFUPort = USBProbe.serialPorts().contains { $0.path == port && $0.isDFU }
   295	        let base = isDFUPort ? DeviceRegistry.chameleonDFU : DeviceRegistry.chameleonUltra
   296	        let manual = DeviceDescriptor(
   297	            id: "chameleon-manual:\(port)",
   298	            family: base.family,
   299	            displayName: base.displayName,
   300	            daemonScript: base.daemonScript,
   301	            probeSubdir: base.probeSubdir,
   302	            usbMatch: base.usbMatch,
   303	            capabilities: base.capabilities,
   304	            portOverride: port)
   305	        await swapDevice(to: manual)
   306	        refreshConnectLists()
   307	    }
   308	
   309	    // ---- Bluetooth LE connect ----------------------------------------------
   310	
   311	    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
   312	    /// surface drives the radio without importing the manager directly.
   313	    func bleStartScan() { ble.startScan() }
   314	    func bleStopScan() { ble.stopScan() }
   315	
   316	    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
   317	    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
   318	    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
   319	    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
   320	    ///
   321	    /// A device advertising the Nordic DFU service is refused here (the view also hides its
   322	    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
   323	    /// be recovered over USB.
   324	    func bleConnect(_ id: String) async {
   325	        guard canChangeDevice else { return }
   326	        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
   327	        // Clean switch between BLE devices: drop any prior link before opening a new one.
   328	        if ble.isConnected { ble.disconnect() }
   329	        do {
   330	            let port = try await ble.connect(id)
   331	            let base = DeviceRegistry.chameleonUltra
   332	            let desc = DeviceDescriptor(
   333	                id: "chameleon-ble:\(id)",
   334	                family: base.family,
   335	                displayName: base.displayName,
   336	                daemonScript: base.daemonScript,
   337	                probeSubdir: base.probeSubdir,
   338	                usbMatch: base.usbMatch,
   339	                capabilities: base.capabilities,
   340	                portOverride: "tcp:127.0.0.1:\(port)")
   341	            await swapDevice(to: desc)
   342	            ble.stopScan()
   343	            refreshConnectLists()
   344	        } catch {
   345	            lastError = "\(error)"
   346	            ble.disconnect()
   347	        }
   348	    }
   349	
   350	    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
   351	    /// then sample the reader. Shared by the first connect and a hot-swap, so both
   352	    /// paths land the same state (info, capabilities via `info`, reader/card status).
   353	    private func openCurrentDevice() async {
   354	        // A device in the bootloader has no command interface to query: present a reachable
   355	        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
   356	        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
   357	        if deviceInDFU {
   358	            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
   359	            info = nil
   360	            readerOnline = false
   361	            dfuStatus = nil
   362	            lastError = nil
   363	            return
   364	        }
   365	        let b = activeBridge()
   366	        do {
   367	            info = try await b.info()
   368	            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
   369	            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
   370	            readerOnline = true
   371	            lastError = nil
   372	            await refreshStatus()
   373	        } catch {
   374	            applyReaderGone()
   375	            lastError = "\(error)"
   376	        }
   377	    }
   378	
   379	    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
   380	    /// as decodes recover keys).
   381	    func refreshLearnedCount() async {
   382	        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
   383	    }
   384	
   385	    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
   386	    func clearLearnedKeys() async {
   387	        try? await activeBridge().clearLearnedKeys()
   388	        await refreshLearnedCount()
   389	    }
   390	
   391	    /// Live status: keep the reader / card pill honest when the X7 or a card is
   392	    /// plugged or removed with no user action. Runs until the view's task is
   393	    /// cancelled. Skips polling during an operation that already owns the reader.
   394	    func monitor() async {
   395	        while !Task.isCancelled {
   396	            try? await Task.sleep(for: .seconds(1.5))
   397	            if deviceBusy || swapping { continue }
   398	            // Hot-swap detection runs even while emulating: unplugging an emulating
   399	            // Chameleon and attaching another device must still tear down + swap. It is a
   400	            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
   401	            // With only the X7 involved, detect() keeps returning the same descriptor, so
   402	            // this path is inert and the poll below is unchanged.
   403	            if let found = DeviceRegistry.detect() {
   404	                // A manually-pinned device (portOverride) is protected from an auto-swap ONLY
   405	                // while its pinned port is still present - the user chose this port. Once that
   406	                // /dev path disappears (unplugged), the pin no longer refers to anything, so a
   407	                // newly-plugged X7 / auto Chameleon / DFU is allowed to hot-swap in exactly as
   408	                // an unpinned device would. Auto descriptors (no pin) swap on an id change as
   409	                // before.
   410	                if found.id != descriptor.id,
   411	                   descriptor.portOverride == nil || !pinnedPortPresent() {
   412	                    await swapDevice(to: found); continue
   413	                }
   414	            } else if emulating {
   415	                // The emulating device was unplugged with nothing to swap to: the card
   416	                // poll is skipped while emulating, so this is the only place that would
   417	                // notice it is gone. Reflect it (which also clears the emulate state).
   418	                applyReaderGone()
   419	                continue
   420	            }
   421	            // Only the card POLL is skipped while emulating: a poll forces reader mode,
   422	            // which would break the emulation.
   423	            if emulating { continue }
   424	            await refreshStatus()
   425	        }
   426	    }
   427	
   428	    /// Replace the active device with a freshly detected one. Every synchronous state
   429	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   430	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   431	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   432	    /// already nil. Only reader-bound state is cleared; the writable document is
   433	    /// device-independent and is deliberately kept across the swap.
   434	    private func swapDevice(to found: DeviceDescriptor) async {
   435	        guard !swapping else { return }
   436	        swapping = true
   437	        defer { swapping = false }              // released however this returns
   438	        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
   439	        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
   440	        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
   441	        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
   442	        let old = bridge
   443	        bridge = nil                            // detach: no path can obtain the old bridge now
   444	        descriptor = found
   445	        withAnimation(.easeInOut(duration: 0.3)) {
   446	            readerOnline = false
   447	            info = nil
   448	            card = nil
   449	            clearCardBound()
   450	            resetChameleonState()
   451	        }
   452	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   453	        await openCurrentDevice()               // creates + brings up the new bridge
   454	        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
   455	        // now that the old daemon is gone and the new device is open. Gated on the NEW device
   456	        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
   457	        // the old link and brought up the new one before this swap, so disconnecting here
   458	        // would kill the freshly-connected link. Idempotent.
   459	        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
   460	    }
   461	
   462	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   463	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   464	    /// and the slot view / emulate toggle must not persist across a device that has none.
   465	    private func resetChameleonState() {
   466	        slots = []
   467	        selectedSlot = nil
   468	        showSlots = false
   469	        showLF = false
   470	        lfScanResult = nil
   471	        lfWriteResult = nil
   472	        emulating = false
   473	        dfuStatus = nil
   474	        // A failed flash usually leaves the device in the bootloader, which triggers a
   475	        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
   476	        // still open, KEEP the flash outcome (error / done + progress) so its banner and
   477	        // recovery text do not vanish under the user; clearFlashState() clears them when
   478	        // the sheet is dismissed.
   479	        if !flashingSheet {
   480	            flashStage = nil
   481	            flashPercent = nil
   482	            flashDone = false
   483	            flashError = nil
   484	        }
   485	    }
   486	
   487	    /// Clear the flash outcome + progress. Called when the flashing sheet is dismissed, so a
   488	    /// stale error / done state never carries into the next time it is opened.
   489	    func clearFlashState() {
   490	        flashStage = nil
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
   251	                onProgress: @escaping @Sendable (EngineEvent) -> Void) async throws -> DecodeResult {
   252	        // One streaming op at a time: the event slot is shared, so reject a second
   253	        // before it can cross-wire this one's progress (callers also serialize, but
   254	        // the actor is reentrant - this is the real guard).
   255	        guard eventSink == nil else { throw EngineError.daemon("an operation is already in progress") }
   256	        eventSink = { ev in if ev.method == "decode" { onProgress(ev) } }
   257	        opGeneration += 1
   258	        defer { eventSink = nil }
   259	        // A decode can legitimately walk the whole dictionary for minutes; the
   260	        // cancel button is the user's control, so give it a long backstop deadline.
   261	        let dl = Duration.seconds(1800)
   262	        if userKeys.isEmpty { return try await request("decode", timeout: dl, as: DecodeResult.self) }
   263	        return try await request("decode", params: DecodeParams(user_keys: userKeys), timeout: dl, as: DecodeResult.self)
   264	    }
   265	
   266	    /// Cooperatively abort an in-flight streaming op: ask the daemon to stop via its
   267	    /// `cancel` method (it trips a flag its long loops watch and returns the partial
   268	    /// result it has gathered so far) INSTEAD of killing the process. Keeping the daemon
   269	    /// alive preserves its learned-key cache write and needs no respawn. If the op has
   270	    /// not wound down within a short grace window (a genuinely wedged daemon), fall back
   271	    /// to terminating it so the pending op fails and the next call respawns.
   272	    ///
   273	    /// The daemon handles `cancel` inline (off its worker), so it lands while the op is
   274	    /// still running; the op's own continuation then resolves with the partial result.
   275	    /// We do not register a continuation for the cancel line - its id-tagged reply is
   276	    /// simply dropped by route().
   277	    func cancel() {
   278	        guard let p = process else { return }
   279	        let gen = opGeneration                  // the op we are cancelling
   280	        let id = nextID; nextID += 1
     1	import sys
     2	import queue
     3	import struct
     4	import threading
     5	import time
     6	import platform
     7	from typing import Union
     8	from enum import Enum, auto
     9	# serial is imported lazily inside open() so the package imports on a bare
    10	# interpreter; it is only needed to actually open a hardware port.
    11	import socket
    12	
    13	from .chameleon_utils import CR, CG, CC, CY, color_string
    14	from .chameleon_enum import Command, Status
    15	
    16	ANDROID = 'android' in platform.release()
    17	
    18	# each thread is waiting for its data for 100 ms before looping again
    19	THREAD_BLOCKING_TIMEOUT = 0.1
    20	
    21	# TODO: client settings
    22	DEBUG = False
    23	
    24	
    25	class TransportType(Enum):
    26	    NONE = auto()
    27	    SERIAL = auto()
    28	    SOCKET = auto()
    29	
    30	
    31	class NotOpenException(Exception):
    32	    """
    33	        Chameleon err status
    34	    """
    35	
    36	
    37	class OpenFailException(Exception):
    38	    """
    39	        Chameleon open fail(serial port may be error)
    40	    """
    41	
    42	
    43	class CMDInvalidException(Exception):
    44	    """
    45	        CMD invalid(Unsupported)
    46	    """
    47	
    48	
    49	class Response:
    50	    """
    51	        Chameleon Response Data
    52	    """
    53	
    54	    def __init__(self, cmd, status, data=b'', parsed=None):
    55	        self.cmd = cmd
    56	        self.status = status
    57	        self.data: bytes = data
    58	        self.parsed = parsed
    59	
    60	
    61	class ChameleonCom:
    62	    """
    63	        Chameleon device base class
    64	        Communication and Data frame implemented
    65	    """
    66	    data_frame_sof = 0x11
    67	    data_max_length = 4096
    68	    commands = []
    69	
    70	    def __init__(self):
    71	        """
    72	            Create a chameleon device instance
    73	        """
    74	        self.transport: Union[serial.Serial, socket.socket, None] = None
    75	        self.transport_type = TransportType.NONE
    76	        self.send_data_queue = queue.Queue()
    77	        self.wait_response_map = {}
    78	        self.event_closing = threading.Event()
    79	
    80	    def isOpen(self) -> bool:
    81	        """
    82	            Chameleon is connected and init.
    83	
    84	        :return:
    85	        """
    86	        return self.transport is not None and (self.transport_type is TransportType.SOCKET or self.transport.is_open)
    87	
    88	    def open(self, port) -> "ChameleonCom":
    89	        """
    90	            Open chameleon port to communication
    91	            And init some variables
    92	
    93	        :param port: com port, comXXX or ttyXXX
    94	        :return:
    95	        """
    96	        if not self.isOpen():
    97	            error = None
    98	            try:
    99	                # open serial port
   100	                if port.startswith('tcp:'):
   101	                    host, _, port = port[4:].partition(':')
   102	                    if not host or not port:
   103	                        sys.exit(color_string(CR, 'Usage: tcp:127.0.0.1:4321'))
   104	                    self.transport = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
   105	                    print('Connecting to', host, int(port))
   106	                    self.transport.connect((host, int(port)))
   107	                    self.transport_type = TransportType.SOCKET
   108	                else:
   109	                    if ANDROID:
   110	                        sys.exit(color_string(
   111	                            CR, 'COM port is not supported on Android, make a USB-serial to TCP communication bridge'))
   112	                    import serial  # lazy: only needed to open a real hardware port
   113	                    self.transport = serial.Serial(port=port, baudrate=115200)
   114	                    self.transport_type = TransportType.SERIAL
   115	            except Exception as e:
   116	                error = e
   117	            finally:
   118	                if error is not None:
   119	                    raise OpenFailException(error)
   120	            assert self.transport is not None
   121	            assert self.transport_type is not TransportType.NONE
   122	            if self.transport_type is TransportType.SERIAL:
   123	                try:
   124	                    self.transport.dtr = True  # must make dtr enable
   125	                except Exception:
   126	                    # not all serial support dtr, e.g. virtual serial over BLE
   127	                    pass
   128	                self.transport.timeout = THREAD_BLOCKING_TIMEOUT
   129	            else:  # SOCKET
   130	                self.transport.settimeout(THREAD_BLOCKING_TIMEOUT)
   131	            # clear variable
   132	            self.send_data_queue.queue.clear()
   133	            self.wait_response_map.clear()
   134	            # Start a sub thread to process data
   135	            self.event_closing.clear()
   136	            threading.Thread(target=self.thread_data_receive).start()
   137	            threading.Thread(target=self.thread_data_transfer).start()
   138	            threading.Thread(target=self.thread_check_timeout).start()
   139	        return self
   140	
   141	    def check_open(self) -> None:
   142	        """
   143	
   144	        :return:
   145	        """
   146	        if not self.isOpen():
   147	            raise NotOpenException("Please call open() function to start device.")
   148	
   149	    @staticmethod
   150	    def lrc_calc(array: Union[bytearray, bytes]) -> int:
   151	        """
   152	            Calc lrc and auto cut byte.
   153	
   154	        :param array: value array
   155	        :return: u8 result
   156	        """
   157	        # add and cut byte and return
   158	        ret = 0x00
   159	        for b in array:
   160	            ret += b
   161	            ret &= 0xFF
   162	        return (0x100 - ret) & 0xFF
   163	
   164	    def close(self):
   165	        """
   166	            Close chameleon and clear variable.
   167	
   168	        :return:
   169	        """
   170	        self.event_closing.set()
   171	        try:
   172	            assert self.transport is not None
   173	            if self.transport_type is TransportType.SOCKET:
   174	                self.transport.shutdown()
   175	            self.transport.close()
   176	        except Exception:
   177	            pass
   178	        finally:
   179	            self.transport = None
   180	        self.wait_response_map.clear()
   181	        self.send_data_queue.queue.clear()
   182	
   183	    def thread_data_receive(self):
   184	        """
   185	            Sub thread to receive data from chameleon device.
   186	
   187	        :return:
   188	        """
   189	        data_buffer = bytearray()
   190	        data_position = 0
   191	        data_cmd = 0x0000
   192	        data_status = 0x0000
   193	        data_length = 0x0000
   194	
   195	        while self.isOpen():
   196	            # receive
   197	            assert self.transport_type is not TransportType.NONE
   198	            if self.transport_type is TransportType.SERIAL:
   199	                try:
   200	                    assert self.transport is not None
   201	                    data_bytes = bytearray(self.transport.read())
   202	                except Exception as e:
   203	                    if not self.event_closing.is_set():
   204	                        print(f"Serial Error {e}, thread for receiver exit.")
   205	                    self.close()
   206	                    break
   207	            else:  # SOCKET
   208	                try:
   209	                    data_bytes = bytearray(self.transport.recv(1024))
   210	                except socket.timeout:
   211	                    continue
   212	                except OSError:
   213	                    print(color_string(CR, 'socket closed'))
   214	                    self.transport = None
   215	                    break
   216	
   217	            while len(data_bytes) > 0:
   218	                data_byte = data_bytes[0]
   219	                data_bytes = data_bytes[1:]
   220	                data_buffer.append(data_byte)
   221	                if data_position < struct.calcsize('!BB'):  # start of frame + lrc1
   222	                    if data_position == 0:
   223	                        if data_buffer[data_position] != self.data_frame_sof:
   224	                            print("Data frame no sof byte.")
   225	                            data_position = 0
   226	                            data_buffer.clear()
   227	                            continue
   228	                    if data_position == struct.calcsize('!B'):
   229	                        if data_buffer[data_position] != self.lrc_calc(data_buffer[:data_position]):
   230	                            data_position = 0
   231	                            data_buffer.clear()
   232	                            print("Data frame sof lrc error.")
   233	                            continue
   234	                elif data_position == struct.calcsize('!BBHHH'):  # frame head lrc
   235	                    if data_buffer[data_position] != self.lrc_calc(data_buffer[:data_position]):
   236	                        data_position = 0
   237	                        data_buffer.clear()
   238	                        print("Data frame head lrc error.")
   239	                        continue
   240	                    # frame head complete, cache info
   241	                    _, _, data_cmd, data_status, data_length = struct.unpack("!BBHHH", data_buffer[:data_position])
   242	                    if data_length > self.data_max_length:
   243	                        data_position = 0
   244	                        data_buffer.clear()
   245	                        print("Data frame data length larger than max.")
   246	                        continue
   247	                elif data_position > struct.calcsize('!BBHHH'):  # // frame data
   248	                    if data_position == (struct.calcsize(f'!BBHHHB{data_length}s')):
   249	                        if data_buffer[data_position] == self.lrc_calc(data_buffer[:data_position]):
   250	                            # ok, lrc for data is correct.
   251	                            # and we are receive completed
   252	                            # print(f"Buffer data = {data_buffer.hex()}")
   253	                            data_response = bytes(data_buffer[struct.calcsize('!BBHHHB'):
   254	                                                              struct.calcsize(f'!BBHHHB{data_length}s')])
   255	                            if DEBUG:
   256	                                try:
   257	                                    command = Command(data_cmd)
   258	                                    command_string = f"{data_cmd} {command.name}"
   259	                                except ValueError:
   260	                                    command_string = f"{data_cmd} (unknown)"
   261	                                try:
   262	                                    status_string = str(Status(data_status))
   263	                                    if data_status == Status.SUCCESS:
   264	                                        status_string = color_string((CG, status_string.ljust(30)))
   265	                                    else:
   266	                                        status_string = color_string((CR, status_string.ljust(30)))
   267	                                except ValueError:
   268	                                    status_string = f"{data_status:30x}"
   269	                                    response = data_response.hex() if data_response is not None else ""
   270	                                    print(
   271	                                        f"<={color_string((CC, command_string.ljust(40)), (CR, status_string), (CY, response))}")
   272	                            if data_cmd in self.wait_response_map:
   273	                                # call processor
   274	                                if 'callback' in self.wait_response_map[data_cmd]:
   275	                                    fn_call = self.wait_response_map[data_cmd]['callback']
   276	                                else:
   277	                                    fn_call = None
   278	                                if callable(fn_call):
   279	                                    # delete wait task from map
   280	                                    del self.wait_response_map[data_cmd]
   281	                                    fn_call(data_cmd, data_status, data_response)
   282	                                else:
   283	                                    self.wait_response_map[data_cmd]['response'] = Response(data_cmd, data_status,
   284	                                                                                            data_response)
   285	                            else:
   286	                                print(f"No task wait process: ${data_cmd}")
   287	                        else:
   288	                            print("Data frame global lrc error.")
   289	                        data_position = 0
   290	                        data_buffer.clear()
   291	                        continue
   292	                data_position += 1
   293	
   294	    def thread_data_transfer(self):
   295	        """
   296	            Sub thread to transfer data to chameleon device.
   297	
   298	        :return:
   299	        """
   300	        while self.isOpen():
   301	            # get a task from queue(if exists)
   302	            try:
   303	                task = self.send_data_queue.get(block=True, timeout=THREAD_BLOCKING_TIMEOUT)
   304	            except queue.Empty:
   305	                continue
   306	            task_cmd = task['cmd']
   307	            task_timeout = task['timeout']
   308	            task_close = task['close']
   309	            # register to wait map
   310	            if 'callback' in task and callable(task['callback']):
   311	                self.wait_response_map[task_cmd] = {'callback': task['callback']}  # The callback for this task
   312	            else:
   313	                self.wait_response_map[task_cmd] = {'response': None}
   314	            # set start time
   315	            start_time = time.time()
   316	            self.wait_response_map[task_cmd]['start_time'] = start_time
   317	            self.wait_response_map[task_cmd]['end_time'] = start_time + task_timeout
   318	            self.wait_response_map[task_cmd]['is_timeout'] = False
   319	            assert self.transport_type is not TransportType.NONE
   320	            if self.transport_type == TransportType.SERIAL:
   321	                try:
   322	                    assert self.transport is not None
   323	                    # send to device
   324	                    self.transport.write(task['frame'])
   325	                except Exception as e:
   326	                    print(f"Serial Error {e}, thread for transfer exit.")
   327	                    self.close()
   328	                    break
   329	            else:  # SOCKET
   330	                try:
   331	                    assert self.transport is not None
   332	                    self.transport.sendall(task['frame'])
   333	                except OSError as e:
   334	                    self.transport = None
   335	                    print(f'Socket error {e}, thread for transfer exit.')
   336	                    self.close()
   337	                    break
   338	            # update queue status
   339	            self.send_data_queue.task_done()
   340	            # disconnect if DFU command has been sent
   341	            if task_close:
   342	                self.close()
   343	
   344	    def thread_check_timeout(self):
   345	        """
   346	            Check task timeout.
   347	
   348	        :return:
   349	        """
   350	        while self.isOpen():
   351	            for task_cmd in self.wait_response_map.keys():
   352	                if time.time() > self.wait_response_map[task_cmd]['end_time']:
   353	                    if 'callback' in self.wait_response_map[task_cmd]:
   354	                        # not sync, call function to notify timeout.
   355	                        self.wait_response_map[task_cmd]['callback'](task_cmd, None, None)
   356	                    else:
   357	                        # sync mode, set timeout flag
   358	                        self.wait_response_map[task_cmd]['is_timeout'] = True
   359	            time.sleep(THREAD_BLOCKING_TIMEOUT)
   360	

exec
/bin/zsh -lc "nl -ba app/Sources/Engine/BLEManager.swift | sed -n '325,520p'; nl -ba app/Sources/AppModel.swift | sed -n '1,470p'; nl -ba app/Sources/Engine/DeviceBridge.swift | sed -n '1,240p'; rg -n \"def _connect|DFU|port_override|portOverride|CHAMELEON_PORT|tcp:\" probe app/Sources -g '*.py' -g '*.swift'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   325	        }
   326	    }
   327	}
   328	
   329	// MARK: - CBCentralManagerDelegate
   330	
   331	extension BLEManager: CBCentralManagerDelegate {
   332	    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
   333	        MainActor.assumeIsolated {
   334	            self.state = Self.mapState(central.state)
   335	            if central.state == .poweredOn {
   336	                if self.scanRequested { self.beginScan() }
   337	            } else {
   338	                self.scanning = false
   339	                if self.connectContinuation != nil {
   340	                    self.failConnect(BLEError.notPoweredOn)
   341	                } else if self.connectedDeviceID != nil {
   342	                    self.teardownLink()
   343	                }
   344	            }
   345	        }
   346	    }
   347	
   348	    nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
   349	                                    advertisementData: [String: Any], rssi RSSI: NSNumber) {
   350	        MainActor.assumeIsolated {
   351	            let id = peripheral.identifier.uuidString
   352	            self.discovered[id] = peripheral
   353	            let advName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
   354	            let name = peripheral.name ?? advName ?? "Chameleon"
   355	            var isDFU = false
   356	            if let services = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] {
   357	                isDFU = services.contains(Self.dfuService)
   358	            }
   359	            let device = BLEDevice(id: id, name: name, rssi: RSSI.intValue, isDFU: isDFU)
   360	            if let idx = self.devices.firstIndex(where: { $0.id == id }) {
   361	                self.devices[idx] = device
   362	            } else {
   363	                self.devices.append(device)
   364	            }
   365	            self.devices.sort { $0.rssi > $1.rssi }
   366	        }
   367	    }
   368	
   369	    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
   370	        MainActor.assumeIsolated {
   371	            peripheral.discoverServices([Self.nusService])
   372	        }
   373	    }
   374	
   375	    nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
   376	        MainActor.assumeIsolated {
   377	            self.failConnect(BLEError.connectionFailed)
   378	        }
   379	    }
   380	
   381	    nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
   382	        MainActor.assumeIsolated {
   383	            if self.connectContinuation != nil {
   384	                self.failConnect(BLEError.disconnected)
   385	            } else if self.connectedDeviceID == peripheral.identifier.uuidString {
   386	                self.teardownLink()
   387	            }
   388	        }
   389	    }
   390	}
   391	
   392	// MARK: - CBPeripheralDelegate
   393	
   394	extension BLEManager: CBPeripheralDelegate {
   395	    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
   396	        MainActor.assumeIsolated {
   397	            if error != nil { self.failConnect(BLEError.serviceNotFound); return }
   398	            guard let svc = peripheral.services?.first(where: { $0.uuid == Self.nusService }) else {
   399	                self.failConnect(BLEError.serviceNotFound); return
   400	            }
   401	            peripheral.discoverCharacteristics([Self.nusRX, Self.nusTX], for: svc)
   402	        }
   403	    }
   404	
   405	    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
   406	        MainActor.assumeIsolated {
   407	            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
   408	            let chars = service.characteristics ?? []
   409	            guard let rx = chars.first(where: { $0.uuid == Self.nusRX }),
   410	                  let tx = chars.first(where: { $0.uuid == Self.nusTX }) else {
   411	                self.failConnect(BLEError.characteristicNotFound); return
   412	            }
   413	            self.rxChar = rx
   414	            self.txChar = tx
   415	            let m = peripheral.maximumWriteValueLength(for: .withoutResponse)
   416	            self.maxWriteLen = m > 0 ? m : 20
   417	            peripheral.setNotifyValue(true, for: tx)
   418	        }
   419	    }
   420	
   421	    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
   422	        MainActor.assumeIsolated {
   423	            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
   424	            guard characteristic.uuid == Self.nusTX, characteristic.isNotifying else { return }
   425	            guard let rx = self.rxChar else { self.failConnect(BLEError.characteristicNotFound); return }
   426	            // Bring the link up: handshake first (with response), then the loopback bridge.
   427	            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
   428	            self.startBridge { result in
   429	                switch result {
   430	                case .success(let port): self.succeedConnect(port: port)
   431	                case .failure:
   432	                    if self.connectContinuation != nil { self.failConnect(BLEError.bridge) }
   433	                    else { self.disconnect() }
   434	                }
   435	            }
   436	        }
   437	    }
   438	
   439	    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
   440	        MainActor.assumeIsolated {
   441	            guard characteristic.uuid == Self.nusTX, let data = characteristic.value, !data.isEmpty else { return }
   442	            self.sendToTCP(data)
   443	        }
   444	    }
   445	}
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
    42	    /// The Bluetooth LE radio for a wireless Chameleon link. Nested @Observable, so a view
    43	    /// that reads `model.ble.state` / `model.ble.devices` re-renders on its changes. A BLE
    44	    /// link is never auto-detected (it is not on the USB bus); it is opened only by the
    45	    /// explicit `bleConnect` from the Connect surface.
    46	    let ble = BLEManager()
    47	
    48	    /// The working DOCUMENT: the image produced by a decode or loaded from a file.
    49	    /// It is what the canvas shows, what Save writes out, and what Write clones onto
    50	    /// the card on the reader. It is independent of whichever card is currently on
    51	    /// the reader and deliberately persists across card swaps, so decoding a source
    52	    /// card and writing it onto a blank needs no save/open dance and never visually
    53	    /// vanishes when the source card is lifted.
    54	    var source: CardDump?
    55	    var cloneSheet = false
    56	    var cloning = false
    57	    /// Per-block write outcome from the last/in-flight clone (block -> ok). Tied to a
    58	    /// specific target card, so it resets when the card on the reader changes.
    59	    var cloneResults: [Int: Bool] = [:]
    60	    /// Per-block reason a write was refused (block -> daemon reason), so a failed clone
    61	    /// can be summarised in card terms (sector + cause) instead of raw block numbers.
    62	    var cloneFailReasons: [Int: String] = [:]
    63	    var formatConfirm = false
    64	    /// The uid snapshot taken when the format confirmation is PRESENTED, so the write is
    65	    /// pinned to the card the user actually authorized (the monitor can swap `card`
    66	    /// while the dialog is open). Shown in the dialog and re-checked before erasing.
    67	    var pendingFormatUID: String?
    68	    var formatting = false
    69	
    70	    /// apdu console.
    71	    var apduOpen = false
    72	    var apduLog: [ApduEntry] = []
    73	    var apduBusy = false
    74	
    75	    // ---- Chameleon slot library + emulation (gated on capabilities) --------
    76	    /// The 8-slot library, loaded when the slot view opens (empty for a plain reader).
    77	    var slots: [ChameleonSlot] = []
    78	    /// The slot highlighted in the library (its actions apply to it) - not the ACTIVE
    79	    /// slot the device presents.
    80	    var selectedSlot: Int?
    81	    /// A slot op (select / type / enable / rename / save / load / open / emulate toggle)
    82	    /// owns the reader. Folded into `deviceBusy` so no other op races it.
    83	    var slotBusy = false
    84	    /// The Chameleon-only slot library is showing instead of the document canvas.
    85	    var showSlots = false
    86	
    87	    // ---- LF 125 kHz (Chameleon-only, gated on capabilities.lf) -------------
    88	    /// The LF panel (read / T5577 write / EM410x emulate) is showing instead of the
    89	    /// document canvas. A Chameleon-only detail area, so it drops on a device swap.
    90	    var showLF = false
    91	    /// The last LF read (em410x / hidprox tag on the reader), shown in the LF panel.
    92	    var lfScanResult: LfScanResult?
    93	    /// The last LF T5577 write outcome (wrote + verified), shown in the LF panel.
    94	    var lfWriteResult: LfWriteResult?
    95	    /// An LF op (read / write / emulate) owns the reader. Folded into `deviceBusy`.
    96	    var lfBusy = false
    97	
    98	    // ---- saved-cards library (device-agnostic) -----------------------------
    99	    /// The persistent library of saved card dumps, refreshed when the library view opens
   100	    /// and after any save / import / rename / delete.
   101	    var savedCards: [SavedCard] = []
   102	    /// The library entry highlighted in the view (its actions apply to it).
   103	    var selectedSavedCard: String?
   104	    /// The saved-cards library is showing instead of the document canvas. Unlike the slot
   105	    /// library it is device-agnostic, so it persists across a device swap.
   106	    var showLibrary = false
   107	    /// The device is in tag/emulate mode (presenting the active slot), not reader mode.
   108	    /// While true the status monitor stops polling, since a poll would switch the device
   109	    /// back to reader mode under the emulation.
   110	    var emulating = false
   111	
   112	    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
   113	    /// The firmware update sheet is open.
   114	    var flashingSheet = false
   115	    /// The current firmware + latest release, loaded when the sheet opens.
   116	    var dfuStatus: DfuStatus?
   117	    /// A firmware flash is in flight (owns the device; folded into `deviceBusy`, so the
   118	    /// status monitor pauses while the device reboots into and out of the bootloader).
   119	    var flashing = false
   120	    /// The flash phase (download / enter / flash / done) + percent, for the progress UI.
   121	    var flashStage: String?
   122	    var flashPercent: Int?
   123	    /// A finished-successfully flag so the sheet can show a done state without a lingering error.
   124	    var flashDone = false
   125	    /// A flash failure, shown INSIDE the flashing sheet (not only the root banner behind the
   126	    /// modal) with the retry-now recovery path, since a failed flash usually leaves the
   127	    /// device in the bootloader.
   128	    var flashError: String?
   129	
   130	    /// The active device bridge, chosen by the registry at connect and swapped on
   131	    /// hot-plug. Created lazily for `descriptor` (the daemon itself starts on its
   132	    /// first request), so there is no bridge before the first connect.
   133	    private var bridge: DeviceBridge?
   134	    /// The descriptor of the device the bridge currently drives. X7 by default, so a
   135	    /// bare machine (or one where detection has not run yet) behaves exactly as the
   136	    /// single-device build did; a detected Chameleon swaps it.
   137	    private var descriptor: DeviceDescriptor = DeviceRegistry.fallback
   138	    /// A device swap (or reconnect that changes the device) is tearing down the old
   139	    /// daemon and bringing up the new one.
   140	    ///
   141	    /// INVARIANT: no device op (decode / clone / format / apdu) or reconnect may run
   142	    /// while `swapping` is true, and the old bridge is unreachable the instant it is
   143	    /// set (the swap detaches `bridge` synchronously before its first await). This is
   144	    /// what makes a hot-swap atomic from the UI's point of view: an op started during
   145	    /// the teardown await cannot grab the just-terminated bridge or the stale card.
   146	    private var swapping = false
   147	
   148	    /// A device op already owns the reader. Reconnect / swap must not replace the
   149	    /// bridge under one, and a second op must not start while one runs. Slot ops are
   150	    /// included so a slot edit and a decode / clone can never overlap on the reader.
   151	    private var deviceBusy: Bool { decoding || cloning || formatting || apduBusy || slotBusy || flashing || lfBusy }
   152	
   153	    /// The active bridge, created lazily for the current descriptor. A prior bridge
   154	    /// for a different device is torn down explicitly on the swap path (which nils
   155	    /// `bridge` before the descriptor changes), so this only ever creates the bridge
   156	    /// that matches - it never silently orphans a running daemon.
   157	    private func activeBridge() -> DeviceBridge {
   158	        if let b = bridge, b.descriptor.id == descriptor.id { return b }
   159	        let b = DeviceBridge(descriptor: descriptor)
   160	        bridge = b
   161	        return b
   162	    }
   163	
   164	    /// The connected device's capability manifest, read by the shell to gate
   165	    /// device-specific UI. Prefers the daemon's declared manifest; falls back to the
   166	    /// active descriptor's static defaults before `info` lands or when a daemon
   167	    /// predates the manifest.
   168	    var capabilities: DeviceCapabilities { info?.capabilities ?? descriptor.capabilities }
   169	
   170	    /// True when the connected device is a Chameleon sitting in the Nordic bootloader:
   171	    /// it has no command interface (so `readerOnline` is false and card ops are off), but
   172	    /// the firmware flash can still recover it, so the firmware action stays reachable.
   173	    var deviceInDFU: Bool { descriptor.family == "chameleon-dfu" }
   174	
   175	    /// The family of the device currently driven, so the Connect surface can mark the
   176	    /// active row. A manual pin keeps its base family (chameleon-ultra), so matching a
   177	    /// detected row by family lights the right one in every case.
   178	    var activeDeviceFamily: String { descriptor.family }
   179	
   180	    /// A device swap / (re)connect is in flight, exposed read-only so the Connect
   181	    /// surface can show a spinner and disable Rescan while it runs.
   182	    var connecting: Bool { swapping }
   183	
   184	    /// The manual Connect controls (Rescan, the serial-port rows, the free-text connect)
   185	    /// may act only when no swap or device op owns the reader - the same guard `connect`,
   186	    /// `rescan`, and `connectManual` enforce - so a tap during one is a disabled control,
   187	    /// never a silent no-op that the user reads as the app ignoring them.
   188	    var canChangeDevice: Bool { !swapping && !deviceBusy }
   189	
   190	    /// The user's editable keys (Settings > Dictionaries), tried before the
   191	    /// daemon's large built-in dictionary.
   192	    let keyStore = KeyStore()
   193	    /// The persistent saved-cards library (device-agnostic; works for X7 dumps too).
   194	    let savedCardStore = SavedCardStore.standard()
   195	    /// Size of the daemon's built-in curated dictionary (shown in Settings).
   196	    var builtinKeyCount = 0
   197	    /// Keys the daemon has learned from real cards and reranks decodes with (Settings).
   198	    var learnedKeyCount = 0
   199	
   200	    var selectedSector: SectorVM? {
   201	        guard let s = selected else { return nil }
   202	        return sectors.first { $0.index == s }
   203	    }
   204	
   205	    /// What "write" clones onto the card on the reader: the working document. It has
   206	    /// no dependency on a card being present, so the write action is available as soon
   207	    /// as there is something to write; the target card is asked for at write time.
   208	    var cloneSource: CardDump? { source }
   209	
   210	    /// Format erases the card on the reader, so it is only offered when the card
   211	    /// present is the one this document was decoded from (same uid): only then do we
   212	    /// hold its recovered keys to auth it, and only then is wiping it unambiguous.
   213	    var canFormat: Bool {
   214	        guard let c = card?.uid, let d = source?.uid else { return false }
   215	        return Self.normUID(c) == Self.normUID(d)
   216	    }
   217	
   218	    static func normUID(_ s: String) -> String {
   219	        s.replacingOccurrences(of: " ", with: "").lowercased()
   220	    }
   221	
   222	    /// Detect the connected device, then start its daemon + read device info and look
   223	    /// for a card (connect at launch, not lazily). With no device detected we fall
   224	    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
   225	    ///
   226	    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
   227	    /// reconnect can never replace the bridge under a running decode / clone. When the
   228	    /// detected device differs from the current one it routes through `swapDevice` so
   229	    /// the old daemon is torn down (never silently orphaned) under the swap guard.
   230	    func connect() async {
   231	        guard !swapping, !deviceBusy else { return }
   232	        await detectAndOpen()
   233	    }
   234	
   235	    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
   236	    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
   237	    /// is torn down, and bring the current one up.
   238	    private func detectAndOpen() async {
   239	        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
   240	        if bridge != nil, found.id != descriptor.id {
   241	            await swapDevice(to: found)
   242	            return
   243	        }
   244	        descriptor = found
   245	        await openCurrentDevice()
   246	    }
   247	
   248	    /// Refresh the Connect surface's lists (known present devices + all USB serial
   249	    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
   250	    func refreshConnectLists() {
   251	        detectedDevices = DeviceRegistry.detectAll()
   252	        serialPorts = USBProbe.serialPorts()
   253	    }
   254	
   255	    /// True when a manual serial port is pinned (portOverride) AND that exact /dev path is
   256	    /// still enumerated on the USB bus; false when nothing is pinned. Lets rescan / the
   257	    /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
   258	    private func pinnedPortPresent() -> Bool {
   259	        guard let pinned = descriptor.portOverride else { return false }
   260	        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
   261	        // serial-port scan below would report it absent and the monitor would swap away from a
   262	        // live wireless link. It is "present" for as long as the manager holds the link; once
   263	        // the BLE link drops, this reads false and a normal hot-swap is allowed.
   264	        if pinned.hasPrefix("tcp:") { return ble.isConnected }
   265	        return USBProbe.serialPorts().contains { $0.path == pinned }
   266	    }
   267	
   268	    /// The Connect surface's Rescan: refresh the lists and re-run detection without waiting
   269	    /// for the 1.5s monitor tick. A PRESENT manual pin is preserved - re-open the SAME pinned
   270	    /// device rather than running auto-detect, which (when it does not recognise the
   271	    /// Chameleon) would fall to the X7 fallback and tear down the working port the user pinned
   272	    /// precisely because auto-detect fails. Only with no pin, or a pinned port that has
   273	    /// disappeared, do we re-detect.
   274	    func rescan() async {
   275	        guard !swapping, !deviceBusy else { return }
   276	        refreshConnectLists()
   277	        if descriptor.portOverride != nil, pinnedPortPresent() {
   278	            await openCurrentDevice()
   279	        } else {
   280	            await detectAndOpen()
   281	        }
   282	        refreshConnectLists()
   283	    }
   284	
   285	    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
   286	    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
   287	    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
   288	    func connectManual(port: String) async {
   289	        guard !swapping, !deviceBusy else { return }
   290	        // A port that enumerates as a device in the Nordic bootloader (isDFU) is pinned from
   291	        // the chameleon-dfu descriptor, not chameleonUltra: then deviceInDFU is true and
   292	        // openCurrentDevice takes the DFU branch (firmware recovery reachable) instead of
   293	        // trying the app protocol on a bootloader port. A non-DFU port pins as chameleonUltra.
   294	        let isDFUPort = USBProbe.serialPorts().contains { $0.path == port && $0.isDFU }
   295	        let base = isDFUPort ? DeviceRegistry.chameleonDFU : DeviceRegistry.chameleonUltra
   296	        let manual = DeviceDescriptor(
   297	            id: "chameleon-manual:\(port)",
   298	            family: base.family,
   299	            displayName: base.displayName,
   300	            daemonScript: base.daemonScript,
   301	            probeSubdir: base.probeSubdir,
   302	            usbMatch: base.usbMatch,
   303	            capabilities: base.capabilities,
   304	            portOverride: port)
   305	        await swapDevice(to: manual)
   306	        refreshConnectLists()
   307	    }
   308	
   309	    // ---- Bluetooth LE connect ----------------------------------------------
   310	
   311	    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
   312	    /// surface drives the radio without importing the manager directly.
   313	    func bleStartScan() { ble.startScan() }
   314	    func bleStopScan() { ble.stopScan() }
   315	
   316	    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
   317	    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
   318	    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
   319	    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
   320	    ///
   321	    /// A device advertising the Nordic DFU service is refused here (the view also hides its
   322	    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
   323	    /// be recovered over USB.
   324	    func bleConnect(_ id: String) async {
   325	        guard canChangeDevice else { return }
   326	        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
   327	        // Clean switch between BLE devices: drop any prior link before opening a new one.
   328	        if ble.isConnected { ble.disconnect() }
   329	        do {
   330	            let port = try await ble.connect(id)
   331	            let base = DeviceRegistry.chameleonUltra
   332	            let desc = DeviceDescriptor(
   333	                id: "chameleon-ble:\(id)",
   334	                family: base.family,
   335	                displayName: base.displayName,
   336	                daemonScript: base.daemonScript,
   337	                probeSubdir: base.probeSubdir,
   338	                usbMatch: base.usbMatch,
   339	                capabilities: base.capabilities,
   340	                portOverride: "tcp:127.0.0.1:\(port)")
   341	            await swapDevice(to: desc)
   342	            ble.stopScan()
   343	            refreshConnectLists()
   344	        } catch {
   345	            lastError = "\(error)"
   346	            ble.disconnect()
   347	        }
   348	    }
   349	
   350	    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
   351	    /// then sample the reader. Shared by the first connect and a hot-swap, so both
   352	    /// paths land the same state (info, capabilities via `info`, reader/card status).
   353	    private func openCurrentDevice() async {
   354	        // A device in the bootloader has no command interface to query: present a reachable
   355	        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
   356	        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
   357	        if deviceInDFU {
   358	            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
   359	            info = nil
   360	            readerOnline = false
   361	            dfuStatus = nil
   362	            lastError = nil
   363	            return
   364	        }
   365	        let b = activeBridge()
   366	        do {
   367	            info = try await b.info()
   368	            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
   369	            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
   370	            readerOnline = true
   371	            lastError = nil
   372	            await refreshStatus()
   373	        } catch {
   374	            applyReaderGone()
   375	            lastError = "\(error)"
   376	        }
   377	    }
   378	
   379	    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
   380	    /// as decodes recover keys).
   381	    func refreshLearnedCount() async {
   382	        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
   383	    }
   384	
   385	    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
   386	    func clearLearnedKeys() async {
   387	        try? await activeBridge().clearLearnedKeys()
   388	        await refreshLearnedCount()
   389	    }
   390	
   391	    /// Live status: keep the reader / card pill honest when the X7 or a card is
   392	    /// plugged or removed with no user action. Runs until the view's task is
   393	    /// cancelled. Skips polling during an operation that already owns the reader.
   394	    func monitor() async {
   395	        while !Task.isCancelled {
   396	            try? await Task.sleep(for: .seconds(1.5))
   397	            if deviceBusy || swapping { continue }
   398	            // Hot-swap detection runs even while emulating: unplugging an emulating
   399	            // Chameleon and attaching another device must still tear down + swap. It is a
   400	            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
   401	            // With only the X7 involved, detect() keeps returning the same descriptor, so
   402	            // this path is inert and the poll below is unchanged.
   403	            if let found = DeviceRegistry.detect() {
   404	                // A manually-pinned device (portOverride) is protected from an auto-swap ONLY
   405	                // while its pinned port is still present - the user chose this port. Once that
   406	                // /dev path disappears (unplugged), the pin no longer refers to anything, so a
   407	                // newly-plugged X7 / auto Chameleon / DFU is allowed to hot-swap in exactly as
   408	                // an unpinned device would. Auto descriptors (no pin) swap on an id change as
   409	                // before.
   410	                if found.id != descriptor.id,
   411	                   descriptor.portOverride == nil || !pinnedPortPresent() {
   412	                    await swapDevice(to: found); continue
   413	                }
   414	            } else if emulating {
   415	                // The emulating device was unplugged with nothing to swap to: the card
   416	                // poll is skipped while emulating, so this is the only place that would
   417	                // notice it is gone. Reflect it (which also clears the emulate state).
   418	                applyReaderGone()
   419	                continue
   420	            }
   421	            // Only the card POLL is skipped while emulating: a poll forces reader mode,
   422	            // which would break the emulation.
   423	            if emulating { continue }
   424	            await refreshStatus()
   425	        }
   426	    }
   427	
   428	    /// Replace the active device with a freshly detected one. Every synchronous state
   429	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   430	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   431	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   432	    /// already nil. Only reader-bound state is cleared; the writable document is
   433	    /// device-independent and is deliberately kept across the swap.
   434	    private func swapDevice(to found: DeviceDescriptor) async {
   435	        guard !swapping else { return }
   436	        swapping = true
   437	        defer { swapping = false }              // released however this returns
   438	        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
   439	        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
   440	        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
   441	        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
   442	        let old = bridge
   443	        bridge = nil                            // detach: no path can obtain the old bridge now
   444	        descriptor = found
   445	        withAnimation(.easeInOut(duration: 0.3)) {
   446	            readerOnline = false
   447	            info = nil
   448	            card = nil
   449	            clearCardBound()
   450	            resetChameleonState()
   451	        }
   452	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   453	        await openCurrentDevice()               // creates + brings up the new bridge
   454	        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
   455	        // now that the old daemon is gone and the new device is open. Gated on the NEW device
   456	        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
   457	        // the old link and brought up the new one before this swap, so disconnecting here
   458	        // would kill the freshly-connected link. Idempotent.
   459	        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
   460	    }
   461	
   462	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   463	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   464	    /// and the slot view / emulate toggle must not persist across a device that has none.
   465	    private func resetChameleonState() {
   466	        slots = []
   467	        selectedSlot = nil
   468	        showSlots = false
   469	        showLF = false
   470	        lfScanResult = nil
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
app/Sources/Engine/DeviceRegistry.swift:20:    /// the daemon is spawned with CHAMELEON_PORT set to this. nil = auto-detect the
app/Sources/Engine/DeviceRegistry.swift:23:    var portOverride: String? = nil
app/Sources/Engine/DeviceRegistry.swift:63:    /// as a Chameleon-in-DFU keeps the firmware/flash action reachable so a stuck or
app/Sources/Engine/DeviceRegistry.swift:65:    /// silently launching into the X7 fallback with DFU hidden. Same daemon (chameleon_d.py).
app/Sources/Engine/DeviceRegistry.swift:66:    static let chameleonDFU = DeviceDescriptor(
app/Sources/Engine/DeviceRegistry.swift:67:        id: "chameleon-dfu", family: "chameleon-dfu", displayName: "Chameleon (DFU)",
app/Sources/Engine/DeviceRegistry.swift:70:        capabilities: .chameleonDFU)
app/Sources/Engine/DeviceRegistry.swift:74:    /// DFU match is last, only relevant when a Chameleon is stuck in the bootloader).
app/Sources/Engine/DeviceRegistry.swift:75:    static let all: [DeviceDescriptor] = [x7, chameleonUltra, chameleonDFU]
app/Sources/Engine/DeviceRegistry.swift:97:/// hint which port is likely a Chameleon (and which is a device in DFU).
app/Sources/Engine/DeviceRegistry.swift:114:    var isDFU: Bool { vid == 0x1915 }
app/Sources/Engine/DeviceRegistry.swift:169:            // The string fallbacks (vendorName / productContains) only apply to a NON-DFU
app/Sources/Engine/DeviceRegistry.swift:172:            // priority order), or its DFU state would be hidden and the daemon would open the
app/Sources/Engine/DeviceRegistry.swift:173:            // bootloader port as an app port. chameleonDFU still matches by its own exact
app/Sources/Engine/DeviceRegistry.swift:174:            // vid/pid above, so a real DFU device is caught by that descriptor alone.
app/Sources/Engine/DeviceRegistry.swift:196:    /// skipped. Chameleon / DFU ports sort first, then by path.
app/Sources/Engine/DeviceRegistry.swift:216:            let ra = a.isChameleon || a.isDFU, rb = b.isChameleon || b.isDFU
app/Sources/Engine/DeviceBridge.swift:111:        // Connect): the daemon honours CHAMELEON_PORT. We COPY the inherited launchd
app/Sources/Engine/DeviceBridge.swift:115:        if let port = descriptor.portOverride {
app/Sources/Engine/DeviceBridge.swift:117:            env["CHAMELEON_PORT"] = port
app/Sources/Engine/DeviceBridge.swift:462:    /// Flash firmware over Nordic Secure DFU. v1 is DOWNLOAD-ONLY: the daemon always fetches
app/Sources/Engine/DeviceBridge.swift:465:    /// only when recovering a device already stuck in DFU, whose model cannot be read. The
app/Sources/Engine/DeviceCapabilities.swift:42:    /// exposes no card / slot interface - only DFU - so the shell offers ONLY the
app/Sources/Engine/DeviceCapabilities.swift:43:    /// firmware action, which is exactly what recovers a device stuck in DFU.
app/Sources/Engine/DeviceCapabilities.swift:44:    static let chameleonDFU = DeviceCapabilities(
app/Sources/Engine/BLEManager.swift:6:/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
app/Sources/Engine/BLEManager.swift:12:    let isDFU: Bool       // advertised the FE59 DFU service (in the Nordic bootloader)
app/Sources/Engine/BLEManager.swift:24:/// with its existing `tcp:HOST:PORT` transport.
app/Sources/Engine/BLEManager.swift:34:    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
app/Sources/Engine/BLEManager.swift:45:    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
app/Sources/Engine/BLEManager.swift:79:    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
app/Sources/Engine/BLEManager.swift:355:            var isDFU = false
app/Sources/Engine/BLEManager.swift:357:                isDFU = services.contains(Self.dfuService)
app/Sources/Engine/BLEManager.swift:359:            let device = BLEDevice(id: id, name: name, rssi: RSSI.intValue, isDFU: isDFU)
app/Sources/Views/FlashingView.swift:3:/// Firmware update (DFU) as a native attached sheet, shown only when the connected device
app/Sources/Views/FlashingView.swift:17:    /// The model the user picked to recover a stuck-in-DFU device ("ultra"/"lite"); drives
app/Sources/Views/FlashingView.swift:72:                } else if model.flashing || model.deviceInDFU {
app/Sources/Views/FlashingView.swift:113:            if model.deviceInDFU {
app/Sources/Views/FlashingView.swift:217:    /// The manual recovery path if the device will not enter DFU on its own (already
app/Sources/AppModel.swift:173:    var deviceInDFU: Bool { descriptor.family == "chameleon-dfu" }
app/Sources/AppModel.swift:255:    /// True when a manual serial port is pinned (portOverride) AND that exact /dev path is
app/Sources/AppModel.swift:259:        guard let pinned = descriptor.portOverride else { return false }
app/Sources/AppModel.swift:260:        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
app/Sources/AppModel.swift:264:        if pinned.hasPrefix("tcp:") { return ble.isConnected }
app/Sources/AppModel.swift:277:        if descriptor.portOverride != nil, pinnedPortPresent() {
app/Sources/AppModel.swift:287:    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
app/Sources/AppModel.swift:290:        // A port that enumerates as a device in the Nordic bootloader (isDFU) is pinned from
app/Sources/AppModel.swift:291:        // the chameleon-dfu descriptor, not chameleonUltra: then deviceInDFU is true and
app/Sources/AppModel.swift:292:        // openCurrentDevice takes the DFU branch (firmware recovery reachable) instead of
app/Sources/AppModel.swift:293:        // trying the app protocol on a bootloader port. A non-DFU port pins as chameleonUltra.
app/Sources/AppModel.swift:294:        let isDFUPort = USBProbe.serialPorts().contains { $0.path == port && $0.isDFU }
app/Sources/AppModel.swift:295:        let base = isDFUPort ? DeviceRegistry.chameleonDFU : DeviceRegistry.chameleonUltra
app/Sources/AppModel.swift:304:            portOverride: port)
app/Sources/AppModel.swift:318:    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
app/Sources/AppModel.swift:321:    /// A device advertising the Nordic DFU service is refused here (the view also hides its
app/Sources/AppModel.swift:326:        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
app/Sources/AppModel.swift:340:                portOverride: "tcp:127.0.0.1:\(port)")
app/Sources/AppModel.swift:355:        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
app/Sources/AppModel.swift:356:        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
app/Sources/AppModel.swift:357:        if deviceInDFU {
app/Sources/AppModel.swift:358:            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
app/Sources/AppModel.swift:404:                // A manually-pinned device (portOverride) is protected from an auto-swap ONLY
app/Sources/AppModel.swift:407:                // newly-plugged X7 / auto Chameleon / DFU is allowed to hot-swap in exactly as
app/Sources/AppModel.swift:411:                   descriptor.portOverride == nil || !pinnedPortPresent() {
app/Sources/AppModel.swift:441:        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
app/Sources/AppModel.swift:459:        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
app/Sources/AppModel.swift:475:        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
app/Sources/AppModel.swift:1277:        if deviceInDFU { dfuStatus = nil; return }
app/Sources/AppModel.swift:1282:    /// Flash firmware over DFU (v1 is download-only: the daemon fetches the official
app/Sources/AppModel.swift:1284:    /// "ultra"/"lite" only when recovering a device stuck in DFU whose model cannot be read.
app/Sources/Shell/RootView.swift:186:        if model.deviceInDFU { return l.t("in_bootloader") }
app/Sources/Shell/RootView.swift:273:            // Firmware update (DFU), gated on the device advertising it: the X7 has
app/Sources/Shell/RootView.swift:278:                // bootloader (deviceInDFU) - the latter is exactly when flash-recovery is needed.
app/Sources/Shell/RootView.swift:280:                             enabled: (model.readerOnline || model.deviceInDFU) && !busy
probe/chameleon_d.py:67:# Firmware update (Nordic Secure DFU). In normal run the device is CDC VID 0x6868;
probe/chameleon_d.py:72:DFU_VID = 0x1915
probe/chameleon_d.py:73:DFU_PID = 0x521f
probe/chameleon_d.py:74:DFU_ENTER_FRAME = b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"
probe/chameleon_d.py:75:# Seconds to wait for the bootloader port to re-appear after the enter-DFU write.
probe/chameleon_d.py:76:DFU_WAIT_SECONDS = 20
probe/chameleon_d.py:77:# After the first new DFU port appears, keep watching this long for a SECOND one to enumerate
probe/chameleon_d.py:79:# device is in DFU - the target is then ambiguous and the flash is refused).
probe/chameleon_d.py:80:DFU_SETTLE_SECONDS = 2.0
probe/chameleon_d.py:103:# Shown when the running firmware cannot be reached to trigger the reboot-into-DFU.
probe/chameleon_d.py:345:    the same 8 slots + emulation + DFU but no HF reader, so reader-mode attacks do
probe/chameleon_d.py:379:# ---- firmware DFU package validation (brick-safety) ------------------------
probe/chameleon_d.py:478:                           "(fw type %r) - full DFU refused (brick-safety)" % ftype)
probe/chameleon_d.py:481:                           "(sd_size=%r bl_size=%r) - full DFU refused (brick-safety)"
probe/chameleon_d.py:528:        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
probe/chameleon_d.py:532:            port = os.environ.get("CHAMELEON_PORT") or None
probe/chameleon_d.py:563:        bootloader (DFU vid) is never a normal device, so it is excluded."""
probe/chameleon_d.py:564:        if getattr(p, "vid", None) == DFU_VID:
probe/chameleon_d.py:582:    def _connect(self, port=None):
probe/chameleon_d.py:590:            # Fail closed on a port in the bootloader: an explicitly-pinned DFU port (the
probe/chameleon_d.py:594:                raise RuntimeError("refusing to open a port that is in DFU/bootloader mode")
probe/chameleon_d.py:1542:    # ---- firmware update (Nordic Secure DFU, app-only, brick-safe) ----------
probe/chameleon_d.py:1549:        """The app-only DFU asset for the device model. FAIL-CLOSED: 0 -> Ultra, 1 -> Lite,
probe/chameleon_d.py:1591:                if getattr(p, "vid", None) == DFU_VID and getattr(p, "pid", None) == DFU_PID]
probe/chameleon_d.py:1599:        """Open a raw pyserial port at 115200 (used only to write the enter-DFU frame).
probe/chameleon_d.py:1612:            s.write(DFU_ENTER_FRAME)
probe/chameleon_d.py:1616:    def _wait_new_dfu_ports(self, before, timeout=DFU_WAIT_SECONDS, settle=DFU_SETTLE_SECONDS):
probe/chameleon_d.py:1617:        """Return the set of NEW bootloader ports that appear after the enter-DFU write,
probe/chameleon_d.py:1685:        DFU zip. Returns {'tag','url','size','digest','asset_id'} or None if none is
probe/chameleon_d.py:1755:                raise RuntimeError("not a Chameleon application DFU package "
probe/chameleon_d.py:1759:                    raise RuntimeError("this is a FULL DFU package (bootloader + softdevice); "
probe/chameleon_d.py:1774:                                   "full DFU refused (brick-safety)" % ", ".join(extra))
probe/chameleon_d.py:1812:        """The full adafruit-nrfutil DFU serial command line: the PATH-independent flasher head
probe/chameleon_d.py:1925:        """Update the Chameleon firmware over Nordic Secure DFU. v1 is DOWNLOAD-ONLY: the
probe/chameleon_d.py:1928:        only to recover a device already stuck in DFU whose model cannot be read. Flow, in
probe/chameleon_d.py:1930:          1. resolve the target device UNAMBIGUOUSLY. Already in DFU -> require exactly one
probe/chameleon_d.py:1931:             DFU device and an explicit model (never guess). Otherwise bind to exactly one
probe/chameleon_d.py:1932:             connected Chameleon and read its model; SNAPSHOT the DFU ports before rebooting.
probe/chameleon_d.py:1936:          4. reboot the bound device (10-byte enter-DFU frame); wait for EXACTLY ONE NEW DFU
probe/chameleon_d.py:1949:            # arbitrary one), read its model FROM HARDWARE, and resolve the DFU port AFTER the
probe/chameleon_d.py:1951:            # stuck in DFU is never mistaken for the one we just rebooted.
probe/chameleon_d.py:1956:            # only valid for in-DFU recovery (where hardware cannot be read). Reject an override
probe/chameleon_d.py:1961:                                   "device already in DFU")
probe/chameleon_d.py:1968:                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
probe/chameleon_d.py:1973:            # No normal device to reboot -> pure recovery of a device ALREADY in DFU (crashed /
probe/chameleon_d.py:1974:            # manual B-button). Require exactly ONE DFU device, and an EXPLICIT model - its model
probe/chameleon_d.py:1975:            # cannot be read in DFU and we never guess (a wrong guess flashes Ultra onto a Lite).
probe/chameleon_d.py:1977:                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
probe/chameleon_d.py:1979:                raise RuntimeError("more than one Chameleon is in DFU mode; connect only the one "
probe/chameleon_d.py:1984:                raise RuntimeError("the Chameleon is already in DFU mode so its model cannot be "
probe/chameleon_d.py:1993:                raise RuntimeError("no application DFU release found for %s"
probe/chameleon_d.py:2011:                # Accept only a NEW, attributable DFU port (not present before the reboot), and
probe/chameleon_d.py:2015:                    raise RuntimeError("the Chameleon did not re-appear in DFU mode. Manual fallback: "
probe/chameleon_d.py:2018:                    raise RuntimeError("more than one new Chameleon appeared in DFU after the reboot; "
probe/chameleon/chameleon_com.py:100:                if port.startswith('tcp:'):
probe/chameleon/chameleon_com.py:103:                        sys.exit(color_string(CR, 'Usage: tcp:127.0.0.1:4321'))
probe/chameleon/chameleon_com.py:340:            # disconnect if DFU command has been sent
app/Sources/Views/ConnectView.swift:93:            if d.family == "chameleon-dfu" { tag("DFU") }
app/Sources/Views/ConnectView.swift:100:        d.family == model.activeDeviceFamily && (model.readerOnline || model.deviceInDFU)
app/Sources/Views/ConnectView.swift:133:            guard !port.isDFU, model.canChangeDevice else { return }
app/Sources/Views/ConnectView.swift:147:                if port.isDFU {
app/Sources/Views/ConnectView.swift:148:                    tag("DFU")
app/Sources/Views/ConnectView.swift:155:            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(port.isDFU ? 0.3 : 0.6)))
app/Sources/Views/ConnectView.swift:159:        .buttonStyle(.plain).disabled(port.isDFU || !model.canChangeDevice)
app/Sources/Views/ConnectView.swift:160:        .help(port.isDFU ? l.t("dfu_recover_hint") : l.t("manual_connect"))
app/Sources/Views/ConnectView.swift:251:    /// One discovered advertiser. A non-DFU row connects (bring up the link, then dismiss); a
app/Sources/Views/ConnectView.swift:252:    /// DFU (bootloader) row is disabled with a "use usb for firmware" hint, since firmware over
app/Sources/Views/ConnectView.swift:256:            guard !d.isDFU, model.canChangeDevice else { return }
app/Sources/Views/ConnectView.swift:264:                if d.isDFU { tag("DFU") }
app/Sources/Views/ConnectView.swift:273:            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(d.isDFU ? 0.3 : 0.6)))
app/Sources/Views/ConnectView.swift:277:        .buttonStyle(.plain).disabled(d.isDFU || !model.canChangeDevice)
app/Sources/Views/ConnectView.swift:278:        .help(d.isDFU ? l.t("bt_dfu_note") : l.t("manual_connect"))
probe/test_chameleon.py:1777:# Firmware DFU (chameleon_d dfu_check / dfu_flash) - hardware-free.
probe/test_chameleon.py:1779:# REAL validation + enter-DFU + argv + progress-parse logic runs against fakes.
probe/test_chameleon.py:1833:    """Write a DFU zip to a temp file and return its path. `full=True` makes it a FULL
probe/test_chameleon.py:1852:    """Captures the raw bytes written for the enter-DFU frame (stands in for pyserial)."""
probe/test_chameleon.py:1930:    of blocking, so time-dependent polling (the DFU settle window) is tested exactly and fast.
probe/test_chameleon.py:1963:    a stray call cannot reach GitHub. DFU tests override the specific seams they exercise. The
probe/test_chameleon.py:1982:    """The bytes of a well-formed app-only DFU zip (what an official download would yield)."""
probe/test_chameleon.py:2042:# 27b. port discovery: CDC (0x6868) vs DFU (0x1915/0x521f) filtering, and the REAL
probe/test_chameleon.py:2050:        FakePort("/dev/cu.usbmodemDFU", 0x1915, 0x521f),
probe/test_chameleon.py:2055:          d._find_dfu_ports() == ["/dev/cu.usbmodemDFU"], str(d._find_dfu_ports()))
probe/test_chameleon.py:2056:    check("_wait_new_dfu_ports (real) returns a DFU port not in the snapshot",
probe/test_chameleon.py:2057:          d._wait_new_dfu_ports([], timeout=1, settle=0.1) == ["/dev/cu.usbmodemDFU"],
probe/test_chameleon.py:2058:          "did not find the new DFU port")
probe/test_chameleon.py:2059:    check("_wait_new_dfu_ports ignores a DFU port already in the snapshot (times out to [])",
probe/test_chameleon.py:2060:          d._wait_new_dfu_ports(["/dev/cu.usbmodemDFU"], timeout=0, settle=0.1) == [],
probe/test_chameleon.py:2061:          "returned a pre-existing DFU port")
probe/test_chameleon.py:2066:#      product/description (case-insensitive), and never a DFU (bootloader) port.
probe/test_chameleon.py:2101:    # (d) a DFU (bootloader) port is never mis-picked: it is skipped even if only it
probe/test_chameleon.py:2104:        FakePortInfo("/dev/cu.usbmodemDFU", vid=0x1915, pid=0x521f,
probe/test_chameleon.py:2107:    check("_find_port skips a DFU port and returns None when only DFU is present",
probe/test_chameleon.py:2110:        FakePortInfo("/dev/cu.usbmodemDFU", vid=0x1915, pid=0x521f),
probe/test_chameleon.py:2113:    check("_find_port skips a DFU port and returns the real CDC port",
probe/test_chameleon.py:2118:# 27d. CHAMELEON_PORT env pins the port at construction; an explicit port= wins.
probe/test_chameleon.py:2121:    saved = os.environ.get("CHAMELEON_PORT")
probe/test_chameleon.py:2123:        os.environ["CHAMELEON_PORT"] = "/dev/cu.usbmodemPINNED"
probe/test_chameleon.py:2124:        check("__init__ honors CHAMELEON_PORT when no port arg is given",
probe/test_chameleon.py:2127:        check("an explicit port= overrides the CHAMELEON_PORT env",
probe/test_chameleon.py:2130:        os.environ["CHAMELEON_PORT"] = ""
probe/test_chameleon.py:2131:        check("an empty CHAMELEON_PORT leaves _port None (auto-discovery)",
probe/test_chameleon.py:2133:        del os.environ["CHAMELEON_PORT"]
probe/test_chameleon.py:2134:        check("a missing CHAMELEON_PORT leaves _port None (auto-discovery)",
probe/test_chameleon.py:2138:            os.environ.pop("CHAMELEON_PORT", None)
probe/test_chameleon.py:2140:            os.environ["CHAMELEON_PORT"] = saved
probe/test_chameleon.py:2144:# 27e. _connect fails closed on a pinned DFU port: an explicitly-pinned bootloader
probe/test_chameleon.py:2146:#      side routing a DFU port to firmware recovery instead of a normal connect).
probe/test_chameleon.py:2151:        FakePort("/dev/cu.usbmodemDFU", 0x1915, 0x521f),
probe/test_chameleon.py:2156:        d._connect(port="/dev/cu.usbmodemDFU")
probe/test_chameleon.py:2159:    check("_connect refuses to open a pinned DFU/bootloader port",
probe/test_chameleon.py:2160:          raised is not None and "DFU" in str(raised), repr(raised))
probe/test_chameleon.py:2161:    check("_connect leaves the handle unopened when it refuses a DFU port",
probe/test_chameleon.py:2162:          d.cmd is None and d.com is None, "cmd/com set despite the DFU guard")
probe/test_chameleon.py:2173:    check("enter-DFU writes exactly the 10-byte ENTER_BOOTLOADER frame",
probe/test_chameleon.py:2175:    check("enter-DFU raises DTR before writing and closes the port after",
probe/test_chameleon.py:2311:DFU_ARGV_TAIL = ["dfu", "serial", "-pkg", "/tmp/fw.zip", "-p", "/dev/cu.dfu", "-b", "115200"]
probe/test_chameleon.py:2320:          argv[-len(DFU_ARGV_TAIL):] == DFU_ARGV_TAIL and len(argv) > len(DFU_ARGV_TAIL), str(argv))
probe/test_chameleon.py:2333:          captured["argv"][-len(DFU_ARGV_TAIL):] == DFU_ARGV_TAIL, str(captured))
probe/test_chameleon.py:2433:# 32. dfu_flash end-to-end (mocked): validate -> enter-DFU -> wait -> flash.
probe/test_chameleon.py:2444:    d._wait_new_dfu_ports = lambda before, timeout=20: ["/dev/cu.usbmodemDFU"]
probe/test_chameleon.py:2458:    check("dfu_flash wrote the enter-DFU frame before flashing",
probe/test_chameleon.py:2461:          flashed.get("port") == "/dev/cu.usbmodemDFU", str(flashed))
probe/test_chameleon.py:2470:    # a device already in DFU (no CDC to reboot) with an EXPLICIT model flashes straight away
probe/test_chameleon.py:2472:    d2._find_dfu_ports = lambda: ["/dev/cu.usbmodemDFU"]
probe/test_chameleon.py:2480:    check("dfu_flash recovers an already-in-DFU device with an explicit model (no enter-DFU)",
probe/test_chameleon.py:2482:          and already.get("port") == "/dev/cu.usbmodemDFU", str((r2, already)))
probe/test_chameleon.py:2486:    # already in DFU with NO explicit model: refuse (never guess Ultra vs Lite)
probe/test_chameleon.py:2488:    d3._find_dfu_ports = lambda: ["/dev/cu.usbmodemDFU"]
probe/test_chameleon.py:2493:    check("dfu_flash refuses an already-in-DFU device of unknown model (requires an explicit choice)",
probe/test_chameleon.py:2563:    # unreachable device (no CDC port, not already in DFU) -> the manual B-button fallback
probe/test_chameleon.py:2572:    # more than one device in DFU (and no CDC to reboot) -> refuse
probe/test_chameleon.py:2580:    check("dfu_flash refuses when MULTIPLE devices are in DFU (never flashes the wrong one)",
probe/test_chameleon.py:2679:# 33c. device identity binding (finding 6): snapshot the DFU ports BEFORE the reboot, then
probe/test_chameleon.py:2680:#      accept exactly ONE NEW attributable port; a second new DFU device -> refuse. A device
probe/test_chameleon.py:2681:#      already stuck in DFU before the reboot is never mistaken for the rebooted one.
probe/test_chameleon.py:2688:    #     appears, then a SECOND new DFU device enumerates a poll later (inside the settle
probe/test_chameleon.py:2711:    check("dfu_flash refuses when a SECOND new DFU device enumerates in the settle window (real wait)",
probe/test_chameleon.py:2714:    # (b) a device already stuck in DFU before the reboot is EXCLUDED by the snapshot; only the
probe/test_chameleon.py:2721:    seq2 = [["/dev/cu.stuckDFU"], ["/dev/cu.stuckDFU", "/dev/cu.freshDFU"]]
probe/test_chameleon.py:2736:          r2["flashed"] is True and flashed2.get("port") == "/dev/cu.freshDFU", str(flashed2))
probe/test_chameleon.py:2740:# 33d. LATE-FIRST settle boundary (fix 1): the first new DFU port appears NEAR the discovery
app/Sources/Brand/L10n.swift:210:        // ---- firmware update / DFU (P4) ----
probe/chameleon/chameleon_cmd.py:1501:        Reboot into DFU mode (bootloader)

exec
/bin/zsh -lc "nl -ba app/Sources/AppModel.swift | sed -n '220,475p'; nl -ba app/Sources/Engine/DeviceBridge.swift | sed -n '1,235p'; nl -ba probe/chameleon_d.py | sed -n '510,620p'; nl -ba probe/chameleon/chameleon_com.py | sed -n '294,380p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   220	    }
   221	
   222	    /// Detect the connected device, then start its daemon + read device info and look
   223	    /// for a card (connect at launch, not lazily). With no device detected we fall
   224	    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
   225	    ///
   226	    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
   227	    /// reconnect can never replace the bridge under a running decode / clone. When the
   228	    /// detected device differs from the current one it routes through `swapDevice` so
   229	    /// the old daemon is torn down (never silently orphaned) under the swap guard.
   230	    func connect() async {
   231	        guard !swapping, !deviceBusy else { return }
   232	        await detectAndOpen()
   233	    }
   234	
   235	    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
   236	    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
   237	    /// is torn down, and bring the current one up.
   238	    private func detectAndOpen() async {
   239	        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
   240	        if bridge != nil, found.id != descriptor.id {
   241	            await swapDevice(to: found)
   242	            return
   243	        }
   244	        descriptor = found
   245	        await openCurrentDevice()
   246	    }
   247	
   248	    /// Refresh the Connect surface's lists (known present devices + all USB serial
   249	    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
   250	    func refreshConnectLists() {
   251	        detectedDevices = DeviceRegistry.detectAll()
   252	        serialPorts = USBProbe.serialPorts()
   253	    }
   254	
   255	    /// True when a manual serial port is pinned (portOverride) AND that exact /dev path is
   256	    /// still enumerated on the USB bus; false when nothing is pinned. Lets rescan / the
   257	    /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
   258	    private func pinnedPortPresent() -> Bool {
   259	        guard let pinned = descriptor.portOverride else { return false }
   260	        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
   261	        // serial-port scan below would report it absent and the monitor would swap away from a
   262	        // live wireless link. It is "present" for as long as the manager holds the link; once
   263	        // the BLE link drops, this reads false and a normal hot-swap is allowed.
   264	        if pinned.hasPrefix("tcp:") { return ble.isConnected }
   265	        return USBProbe.serialPorts().contains { $0.path == pinned }
   266	    }
   267	
   268	    /// The Connect surface's Rescan: refresh the lists and re-run detection without waiting
   269	    /// for the 1.5s monitor tick. A PRESENT manual pin is preserved - re-open the SAME pinned
   270	    /// device rather than running auto-detect, which (when it does not recognise the
   271	    /// Chameleon) would fall to the X7 fallback and tear down the working port the user pinned
   272	    /// precisely because auto-detect fails. Only with no pin, or a pinned port that has
   273	    /// disappeared, do we re-detect.
   274	    func rescan() async {
   275	        guard !swapping, !deviceBusy else { return }
   276	        refreshConnectLists()
   277	        if descriptor.portOverride != nil, pinnedPortPresent() {
   278	            await openCurrentDevice()
   279	        } else {
   280	            await detectAndOpen()
   281	        }
   282	        refreshConnectLists()
   283	    }
   284	
   285	    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
   286	    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
   287	    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
   288	    func connectManual(port: String) async {
   289	        guard !swapping, !deviceBusy else { return }
   290	        // A port that enumerates as a device in the Nordic bootloader (isDFU) is pinned from
   291	        // the chameleon-dfu descriptor, not chameleonUltra: then deviceInDFU is true and
   292	        // openCurrentDevice takes the DFU branch (firmware recovery reachable) instead of
   293	        // trying the app protocol on a bootloader port. A non-DFU port pins as chameleonUltra.
   294	        let isDFUPort = USBProbe.serialPorts().contains { $0.path == port && $0.isDFU }
   295	        let base = isDFUPort ? DeviceRegistry.chameleonDFU : DeviceRegistry.chameleonUltra
   296	        let manual = DeviceDescriptor(
   297	            id: "chameleon-manual:\(port)",
   298	            family: base.family,
   299	            displayName: base.displayName,
   300	            daemonScript: base.daemonScript,
   301	            probeSubdir: base.probeSubdir,
   302	            usbMatch: base.usbMatch,
   303	            capabilities: base.capabilities,
   304	            portOverride: port)
   305	        await swapDevice(to: manual)
   306	        refreshConnectLists()
   307	    }
   308	
   309	    // ---- Bluetooth LE connect ----------------------------------------------
   310	
   311	    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
   312	    /// surface drives the radio without importing the manager directly.
   313	    func bleStartScan() { ble.startScan() }
   314	    func bleStopScan() { ble.stopScan() }
   315	
   316	    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
   317	    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
   318	    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
   319	    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
   320	    ///
   321	    /// A device advertising the Nordic DFU service is refused here (the view also hides its
   322	    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
   323	    /// be recovered over USB.
   324	    func bleConnect(_ id: String) async {
   325	        guard canChangeDevice else { return }
   326	        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
   327	        // Clean switch between BLE devices: drop any prior link before opening a new one.
   328	        if ble.isConnected { ble.disconnect() }
   329	        do {
   330	            let port = try await ble.connect(id)
   331	            let base = DeviceRegistry.chameleonUltra
   332	            let desc = DeviceDescriptor(
   333	                id: "chameleon-ble:\(id)",
   334	                family: base.family,
   335	                displayName: base.displayName,
   336	                daemonScript: base.daemonScript,
   337	                probeSubdir: base.probeSubdir,
   338	                usbMatch: base.usbMatch,
   339	                capabilities: base.capabilities,
   340	                portOverride: "tcp:127.0.0.1:\(port)")
   341	            await swapDevice(to: desc)
   342	            ble.stopScan()
   343	            refreshConnectLists()
   344	        } catch {
   345	            lastError = "\(error)"
   346	            ble.disconnect()
   347	        }
   348	    }
   349	
   350	    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
   351	    /// then sample the reader. Shared by the first connect and a hot-swap, so both
   352	    /// paths land the same state (info, capabilities via `info`, reader/card status).
   353	    private func openCurrentDevice() async {
   354	        // A device in the bootloader has no command interface to query: present a reachable
   355	        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
   356	        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
   357	        if deviceInDFU {
   358	            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
   359	            info = nil
   360	            readerOnline = false
   361	            dfuStatus = nil
   362	            lastError = nil
   363	            return
   364	        }
   365	        let b = activeBridge()
   366	        do {
   367	            info = try await b.info()
   368	            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
   369	            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
   370	            readerOnline = true
   371	            lastError = nil
   372	            await refreshStatus()
   373	        } catch {
   374	            applyReaderGone()
   375	            lastError = "\(error)"
   376	        }
   377	    }
   378	
   379	    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
   380	    /// as decodes recover keys).
   381	    func refreshLearnedCount() async {
   382	        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
   383	    }
   384	
   385	    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
   386	    func clearLearnedKeys() async {
   387	        try? await activeBridge().clearLearnedKeys()
   388	        await refreshLearnedCount()
   389	    }
   390	
   391	    /// Live status: keep the reader / card pill honest when the X7 or a card is
   392	    /// plugged or removed with no user action. Runs until the view's task is
   393	    /// cancelled. Skips polling during an operation that already owns the reader.
   394	    func monitor() async {
   395	        while !Task.isCancelled {
   396	            try? await Task.sleep(for: .seconds(1.5))
   397	            if deviceBusy || swapping { continue }
   398	            // Hot-swap detection runs even while emulating: unplugging an emulating
   399	            // Chameleon and attaching another device must still tear down + swap. It is a
   400	            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
   401	            // With only the X7 involved, detect() keeps returning the same descriptor, so
   402	            // this path is inert and the poll below is unchanged.
   403	            if let found = DeviceRegistry.detect() {
   404	                // A manually-pinned device (portOverride) is protected from an auto-swap ONLY
   405	                // while its pinned port is still present - the user chose this port. Once that
   406	                // /dev path disappears (unplugged), the pin no longer refers to anything, so a
   407	                // newly-plugged X7 / auto Chameleon / DFU is allowed to hot-swap in exactly as
   408	                // an unpinned device would. Auto descriptors (no pin) swap on an id change as
   409	                // before.
   410	                if found.id != descriptor.id,
   411	                   descriptor.portOverride == nil || !pinnedPortPresent() {
   412	                    await swapDevice(to: found); continue
   413	                }
   414	            } else if emulating {
   415	                // The emulating device was unplugged with nothing to swap to: the card
   416	                // poll is skipped while emulating, so this is the only place that would
   417	                // notice it is gone. Reflect it (which also clears the emulate state).
   418	                applyReaderGone()
   419	                continue
   420	            }
   421	            // Only the card POLL is skipped while emulating: a poll forces reader mode,
   422	            // which would break the emulation.
   423	            if emulating { continue }
   424	            await refreshStatus()
   425	        }
   426	    }
   427	
   428	    /// Replace the active device with a freshly detected one. Every synchronous state
   429	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   430	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   431	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   432	    /// already nil. Only reader-bound state is cleared; the writable document is
   433	    /// device-independent and is deliberately kept across the swap.
   434	    private func swapDevice(to found: DeviceDescriptor) async {
   435	        guard !swapping else { return }
   436	        swapping = true
   437	        defer { swapping = false }              // released however this returns
   438	        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
   439	        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
   440	        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
   441	        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
   442	        let old = bridge
   443	        bridge = nil                            // detach: no path can obtain the old bridge now
   444	        descriptor = found
   445	        withAnimation(.easeInOut(duration: 0.3)) {
   446	            readerOnline = false
   447	            info = nil
   448	            card = nil
   449	            clearCardBound()
   450	            resetChameleonState()
   451	        }
   452	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   453	        await openCurrentDevice()               // creates + brings up the new bridge
   454	        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
   455	        // now that the old daemon is gone and the new device is open. Gated on the NEW device
   456	        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
   457	        // the old link and brought up the new one before this swap, so disconnecting here
   458	        // would kill the freshly-connected link. Idempotent.
   459	        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
   460	    }
   461	
   462	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   463	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   464	    /// and the slot view / emulate toggle must not persist across a device that has none.
   465	    private func resetChameleonState() {
   466	        slots = []
   467	        selectedSlot = nil
   468	        showSlots = false
   469	        showLF = false
   470	        lfScanResult = nil
   471	        lfWriteResult = nil
   472	        emulating = false
   473	        dfuStatus = nil
   474	        // A failed flash usually leaves the device in the bootloader, which triggers a
   475	        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
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
   590	            # Fail closed on a port in the bootloader: an explicitly-pinned DFU port (the
   591	            # Swift side should route it to firmware recovery, not here) must never be opened
   592	            # with the app protocol, regardless of how it was resolved. Defense in depth.
   593	            if dev in self._find_dfu_ports():
   594	                raise RuntimeError("refusing to open a port that is in DFU/bootloader mode")
   595	            com = ChameleonCom()
   596	            com.open(dev)
   597	            self.com = com
   598	            self.cmd = ChameleonCMD(com)
   599	        return self.cmd
   600	
   601	    def _drop(self):
   602	        """Forget the device handle so the next _connect() re-opens it (called when
   603	        a hardware op fails: the cached handle is dead)."""
   604	        if self.com is not None:
   605	            try:
   606	                self.com.close()
   607	            except Exception:
   608	                pass
   609	        self.com = None
   610	        self.cmd = None
   611	        self._reader_mode = None
   612	
   613	    def _ensure_reader(self, c):
   614	        """Reader ops (poll/decode/mf_read_block) need the device in reader mode; a
   615	        card op issued in tag/emulator mode returns DEVICE_MODE_ERROR (mis-read as
   616	        "no card"). Switch once per connection and cache it. The Lite has no reader
   617	        front-end (set reader mode -> NOT_IMPLEMENTED), surfaced as a clean
   618	        RuntimeError so callers report a reader fault, never a phantom no-card."""
   619	        if self._reader_mode:
   620	            return
   294	    def thread_data_transfer(self):
   295	        """
   296	            Sub thread to transfer data to chameleon device.
   297	
   298	        :return:
   299	        """
   300	        while self.isOpen():
   301	            # get a task from queue(if exists)
   302	            try:
   303	                task = self.send_data_queue.get(block=True, timeout=THREAD_BLOCKING_TIMEOUT)
   304	            except queue.Empty:
   305	                continue
   306	            task_cmd = task['cmd']
   307	            task_timeout = task['timeout']
   308	            task_close = task['close']
   309	            # register to wait map
   310	            if 'callback' in task and callable(task['callback']):
   311	                self.wait_response_map[task_cmd] = {'callback': task['callback']}  # The callback for this task
   312	            else:
   313	                self.wait_response_map[task_cmd] = {'response': None}
   314	            # set start time
   315	            start_time = time.time()
   316	            self.wait_response_map[task_cmd]['start_time'] = start_time
   317	            self.wait_response_map[task_cmd]['end_time'] = start_time + task_timeout
   318	            self.wait_response_map[task_cmd]['is_timeout'] = False
   319	            assert self.transport_type is not TransportType.NONE
   320	            if self.transport_type == TransportType.SERIAL:
   321	                try:
   322	                    assert self.transport is not None
   323	                    # send to device
   324	                    self.transport.write(task['frame'])
   325	                except Exception as e:
   326	                    print(f"Serial Error {e}, thread for transfer exit.")
   327	                    self.close()
   328	                    break
   329	            else:  # SOCKET
   330	                try:
   331	                    assert self.transport is not None
   332	                    self.transport.sendall(task['frame'])
   333	                except OSError as e:
   334	                    self.transport = None
   335	                    print(f'Socket error {e}, thread for transfer exit.')
   336	                    self.close()
   337	                    break
   338	            # update queue status
   339	            self.send_data_queue.task_done()
   340	            # disconnect if DFU command has been sent
   341	            if task_close:
   342	                self.close()
   343	
   344	    def thread_check_timeout(self):
   345	        """
   346	            Check task timeout.
   347	
   348	        :return:
   349	        """
   350	        while self.isOpen():
   351	            for task_cmd in self.wait_response_map.keys():
   352	                if time.time() > self.wait_response_map[task_cmd]['end_time']:
   353	                    if 'callback' in self.wait_response_map[task_cmd]:
   354	                        # not sync, call function to notify timeout.
   355	                        self.wait_response_map[task_cmd]['callback'](task_cmd, None, None)
   356	                    else:
   357	                        # sync mode, set timeout flag
   358	                        self.wait_response_map[task_cmd]['is_timeout'] = True
   359	            time.sleep(THREAD_BLOCKING_TIMEOUT)
   360	
   361	    def make_data_frame_bytes(self, cmd: int, data: Union[bytes, None] = None, status: int = 0) -> bytes:
   362	        """
   363	            Make data frame
   364	
   365	        :return: frame
   366	        """
   367	        if data is None:
   368	            data = b''
   369	        frame = bytearray(struct.pack(f'!BBHHHB{len(data)}sB',
   370	                                      self.data_frame_sof, 0x00, cmd, status, len(data), 0x00, data, 0x00))
   371	        # lrc1
   372	        frame[struct.calcsize('!B')] = self.lrc_calc(frame[:struct.calcsize('!B')])
   373	        # lrc2
   374	        frame[struct.calcsize('!BBHHH')] = self.lrc_calc(frame[:struct.calcsize('!BBHHH')])
   375	        # lrc3
   376	        frame[struct.calcsize(f'!BBHHHB{len(data)}s')] = self.lrc_calc(frame[:struct.calcsize(f'!BBHHHB{len(data)}s')])
   377	        return bytes(frame)
   378	
   379	    def send_cmd_auto(self, cmd: int, data: Union[bytes, None] = None, status: int = 0, callback=None, timeout: int = 3,
   380	                      close: bool = False):

exec
/bin/zsh -lc "rg -n \"03.?FB|0x03FB|handshake|6E40000|NUS|canSendWriteWithoutResponse|peripheralIsReady\" . /Users/tuan/Claude/chameleon 2>/dev/null | head -n 240; rg -n \"= 1019|1019\" probe/chameleon -g '*.py'; sed -n '1,260p' SPEC_CHAMELEON.md" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
./PROTOCOL.md:52:| `FF 00 9A 5A A5 54 69 61 6E` | `00`                  | **INIT handshake** -> OK   |
./app/Sources/Engine/BLEManager.swift:6:/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
./app/Sources/Engine/BLEManager.swift:23:/// bytes to / from the NUS characteristics, and returns the 127.0.0.1 port the daemon opens
./app/Sources/Engine/BLEManager.swift:34:    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
./app/Sources/Engine/BLEManager.swift:35:    private static let nusService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
./app/Sources/Engine/BLEManager.swift:36:    private static let nusRX = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  // WRITE to device
./app/Sources/Engine/BLEManager.swift:37:    private static let nusTX = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  // NOTIFY from device
./app/Sources/Engine/BLEManager.swift:40:    private static let handshake = Data([0x11, 0xEF, 0x03, 0xFB, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])
./app/Sources/Engine/BLEManager.swift:45:    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
./app/Sources/Engine/BLEManager.swift:79:    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
./app/Sources/Engine/BLEManager.swift:108:    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
./app/Sources/Engine/BLEManager.swift:109:    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
./app/Sources/Engine/BLEManager.swift:110:    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
./app/Sources/Engine/BLEManager.swift:273:    /// length and write each chunk without response (the data path after the handshake).
./app/Sources/Engine/BLEManager.swift:426:            // Bring the link up: handshake first (with response), then the loopback bridge.
./app/Sources/Engine/BLEManager.swift:427:            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
./probe/x7lib.py:606:            (full software crypto1 handshake).
./OPCODES.md:70:| `0x140048c50` | `FF 00 66 00 1E <ctx:4 LE> 46` then `... 46 28` | 11 + 11 | 0x66 sub46 | **Magic-card BACKDOOR UNLOCK** handshake (Gen1A `/`+`(` answer) | W | low |
./OPCODES.md:94:Also in this bucket: the **INIT handshake** `FF 00 9A 5A A5 54 69 61 6E` (`-> 00 OK`,
./OPCODES.md:178:| `... 46` then `... 46 28` | `0x140048c50` | W | **BACKDOOR UNLOCK** handshake; confirmed by `resp[1]==0x2F` (`/`) & `resp[4]==0x28` (`(`) |
./OPCODES.md:189:the `/`(2F) + `(`(28) handshake are the classic Gen1A backdoor signatures.
./OPCODES.md:233:FF 00 9A 5A A5 54 69 61 6E   INIT handshake (send first; expect 00)
./OPCODES.md:364:  signatures (`0x9933ddbb` const, `/`+`(` handshake) are recognizable and strongly
./OPCODES.md:389:- **[info]** READ-CARD SEQUENCE is plausible and the cited opcodes are all real and correctly read-only/non-card-destructive: (1) INIT 'FF 00 9A 5A A5 54 69 61 6E' -> verified. (2) 'FF 00 6B 30' poll/select returns UID+SAK -> verified read-only (fcn 0x140046110). (3) 'FF 00 85 10 00 00 00 02 FF' bulk-read preamble/load-key -> verified read setup. (4) 'FF 00 85 01 <uid0..3> <key_hi key_lo> <r8b> <r9b>' MIFARE authenticated read (fcn 0x140045c80: header word 0x00FF=FF 00, byte 0x85, sub 0x01, then 4 UID bytes from [rcx], arg2[1] then arg2[0] byte-swapped key, then r8b/r9b, len 12) -> verified; returns 0, caches response for per-block reads. This is a standard ISO14443A flow: anticollision/SELECT to get UID/SAK, then authenticate+read with key. Caveat: 'FF 00 85 01' performs a MIFARE Classic AUTHENTICATE with the supplied key. On the target sector this is a read of card content (non-destructive to the device), but a wrong key on some clone/locked cards can increment a card-side auth failure counter - standard MIFARE behavior, not a tool bug. The sequence does NOT include any of the Gen1A magic-write opcodes (0x66 sub 0x41-0x47 categories / the 0x46 backdoor unlock at fcn 0x140048c50, which expects the '/'(0x2f)+'('(0x28) handshake), so it cannot brick/rewrite a card UID. Correctly scoped as read-only.
./OPCODES.md:390:- **[info]** Destructive classification spot-check is sound: the genuinely dangerous magic-card UID-write path (fcn 0x140048c50 = Gen1A backdoor unlock, category words 0x0046 then 0x2846, handshake answer '/'+'(') and the block-write functions (0x140048900/a10/49e40 category 0x43/0x44, 0x140045e40 raw data write 85 12) are all flagged is_write_or_destructive=true and NONE of them appear on the SAFE list. No false-safe among the write opcodes. Note one labeling nuance: 0x140046660 (85 32) and the 0x66-family config setters are marked destructive='writes device config' - they alter reader RF/timing registers, not card data, so they are recoverable via re-init, but excluding them from the safe list is the correct conservative call.
./OPCODES.md:422:config writes right after the INIT handshake, before the first poll. Reversed from
./OPCODES.md:442:FF 00 9A 5A A5 54 69 61 6E                          ; INIT handshake -> 00
./probe/chameleon_d.py:2022:            # Commit handshake (closes the EOF/dispatch race): announce the uninterruptible
./exploration/external-critique/2026-07-21-codex-ble-review.md:17:ARCHITECTURE (verify it is sound, do not assume): the Chameleon speaks the SAME protocol over BLE's Nordic UART Service (NUS) as over USB-CDC. The daemon's transport (probe/chameleon/chameleon_com.py, open()) already supports a `tcp:HOST:PORT` port. So BLE is done as: the Swift app (BLEManager.swift) owns the CoreBluetooth radio (the app process holds Bluetooth TCC), connects NUS, and runs a loopback NWListener TCP bridge that relays bytes between the daemon's TCP socket and the BLE RX(write 6E400002)/TX(notify 6E400003) characteristics. On connect it writes a 10-byte handshake to RX. The daemon is then spawned with CHAMELEON_PORT=tcp:127.0.0.1:<port> (existing portOverride mechanism), so it opens the tcp bridge and its whole protocol stack is reused unchanged.
./exploration/external-critique/2026-07-21-codex-ble-review.md:29:5. macOS reality: with NSBluetoothAlwaysUsageDescription in Info.plist + com.apple.security.device.bluetooth entitlement, will CoreBluetooth in the APP process get permission (vs a child python being denied)? Is there any residual reason a real Chameleon over BLE would fail to connect through this path (permission, MTU, handshake, the bridge, the daemon open)? Name it precisely.
./exploration/external-critique/2026-07-21-codex-ble-review.md:402:/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
./exploration/external-critique/2026-07-21-codex-ble-review.md:419:/// bytes to / from the NUS characteristics, and returns the 127.0.0.1 port the daemon opens
./exploration/external-critique/2026-07-21-codex-ble-review.md:430:    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
./exploration/external-critique/2026-07-21-codex-ble-review.md:431:    private static let nusService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
./exploration/external-critique/2026-07-21-codex-ble-review.md:432:    private static let nusRX = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  // WRITE to device
./exploration/external-critique/2026-07-21-codex-ble-review.md:433:    private static let nusTX = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  // NOTIFY from device
./exploration/external-critique/2026-07-21-codex-ble-review.md:436:    private static let handshake = Data([0x11, 0xEF, 0x03, 0xFB, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])
./exploration/external-critique/2026-07-21-codex-ble-review.md:441:    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
./exploration/external-critique/2026-07-21-codex-ble-review.md:475:    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
./exploration/external-critique/2026-07-21-codex-ble-review.md:504:    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
./exploration/external-critique/2026-07-21-codex-ble-review.md:505:    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
./exploration/external-critique/2026-07-21-codex-ble-review.md:506:    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
./exploration/external-critique/2026-07-21-codex-ble-review.md:669:    /// length and write each chunk without response (the data path after the handshake).
./exploration/external-critique/2026-07-21-codex-ble-review.md:726:     6	/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
./exploration/external-critique/2026-07-21-codex-ble-review.md:743:    23	/// bytes to / from the NUS characteristics, and returns the 127.0.0.1 port the daemon opens
./exploration/external-critique/2026-07-21-codex-ble-review.md:754:    34	    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
./exploration/external-critique/2026-07-21-codex-ble-review.md:755:    35	    private static let nusService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
./exploration/external-critique/2026-07-21-codex-ble-review.md:756:    36	    private static let nusRX = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  // WRITE to device
./exploration/external-critique/2026-07-21-codex-ble-review.md:757:    37	    private static let nusTX = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  // NOTIFY from device
./exploration/external-critique/2026-07-21-codex-ble-review.md:760:    40	    private static let handshake = Data([0x11, 0xEF, 0x03, 0xFB, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])
./exploration/external-critique/2026-07-21-codex-ble-review.md:765:    45	    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
./exploration/external-critique/2026-07-21-codex-ble-review.md:799:    79	    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
./exploration/external-critique/2026-07-21-codex-ble-review.md:828:   108	    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
./exploration/external-critique/2026-07-21-codex-ble-review.md:829:   109	    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
./exploration/external-critique/2026-07-21-codex-ble-review.md:830:   110	    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
./exploration/external-critique/2026-07-21-codex-ble-review.md:993:   273	    /// length and write each chunk without response (the data path after the handshake).
./exploration/external-critique/2026-07-21-codex-ble-review.md:1146:   426	            // Bring the link up: handshake first (with response), then the loopback bridge.
./exploration/external-critique/2026-07-21-codex-ble-review.md:1147:   427	            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
./exploration/external-critique/2026-07-21-codex-ble-review.md:2192:   426	            // Bring the link up: handshake first (with response), then the loopback bridge.
./exploration/external-critique/2026-07-21-codex-ble-review.md:2193:   427	            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
./exploration/external-critique/2026-07-21-codex-ble-review.md:2947:app/Sources/Engine/BLEManager.swift:6:/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
./exploration/external-critique/2026-07-21-codex-ble-review.md:2950:app/Sources/Engine/BLEManager.swift:34:    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
./exploration/external-critique/2026-07-21-codex-ble-review.md:2951:app/Sources/Engine/BLEManager.swift:45:    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
./exploration/external-critique/2026-07-21-codex-ble-review.md:2952:app/Sources/Engine/BLEManager.swift:79:    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
./probe/native/chameleon/src/hardnested_recovery/pm3/emojis.h:215:    {":handshake:", "\xf0\x9f\xa4\x9d"}, // 🤝
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/ios/Runner.xcodeproj/project.pbxproj:367:				GCC_WARN_UNUSED_FUNCTION = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/ios/Runner.xcodeproj/project.pbxproj:368:				GCC_WARN_UNUSED_VARIABLE = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/ios/Runner.xcodeproj/project.pbxproj:449:				GCC_WARN_UNUSED_FUNCTION = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/ios/Runner.xcodeproj/project.pbxproj:450:				GCC_WARN_UNUSED_VARIABLE = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/ios/Runner.xcodeproj/project.pbxproj:498:				GCC_WARN_UNUSED_FUNCTION = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/ios/Runner.xcodeproj/project.pbxproj:499:				GCC_WARN_UNUSED_VARIABLE = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/macos/Runner.xcodeproj/project.pbxproj:411:				GCC_WARN_UNUSED_FUNCTION = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/macos/Runner.xcodeproj/project.pbxproj:412:				GCC_WARN_UNUSED_VARIABLE = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/macos/Runner.xcodeproj/project.pbxproj:500:				GCC_WARN_UNUSED_FUNCTION = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/macos/Runner.xcodeproj/project.pbxproj:501:				GCC_WARN_UNUSED_VARIABLE = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/macos/Runner.xcodeproj/project.pbxproj:551:				GCC_WARN_UNUSED_FUNCTION = YES;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/macos/Runner.xcodeproj/project.pbxproj:552:				GCC_WARN_UNUSED_VARIABLE = YES;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:544:// <q> BLE_NUS_C_ENABLED  - ble_nus_c - Nordic UART Central Service
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:547:#ifndef BLE_NUS_C_ENABLED
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:548:#define BLE_NUS_C_ENABLED 0
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:551:// <e> BLE_NUS_ENABLED - ble_nus - Nordic UART Service
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:553:#ifndef BLE_NUS_ENABLED
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:554:#define BLE_NUS_ENABLED 1
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:556:// <e> BLE_NUS_CONFIG_LOG_ENABLED - Enables logging in the module.
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:558:#ifndef BLE_NUS_CONFIG_LOG_ENABLED
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:559:#define BLE_NUS_CONFIG_LOG_ENABLED 0
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:561:// <o> BLE_NUS_CONFIG_LOG_LEVEL  - Default Severity level
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:569:#ifndef BLE_NUS_CONFIG_LOG_LEVEL
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:570:#define BLE_NUS_CONFIG_LOG_LEVEL 3
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:573:// <o> BLE_NUS_CONFIG_INFO_COLOR  - ANSI escape code prefix.
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:585:#ifndef BLE_NUS_CONFIG_INFO_COLOR
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:586:#define BLE_NUS_CONFIG_INFO_COLOR 0
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:589:// <o> BLE_NUS_CONFIG_DEBUG_COLOR  - ANSI escape code prefix.
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:601:#ifndef BLE_NUS_CONFIG_DEBUG_COLOR
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:602:#define BLE_NUS_CONFIG_DEBUG_COLOR 0
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:6667:// <i>                Then use X(USBD_STRING_WINUSB, =0xEE, (APP_USBD_STRING_DESC(...)))
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11671:// <o> BLE_NUS_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11674:#ifndef BLE_NUS_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11675:#define BLE_NUS_BLE_OBSERVER_PRIO 2
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11678:// <o> BLE_NUS_C_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11681:#ifndef BLE_NUS_C_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11682:#define BLE_NUS_C_BLE_OBSERVER_PRIO 2
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/bsp/bsp_delay.c:12:// Delay NUS
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/bsp/bsp_delay.c:13:// NUS is the number of US numbers to be delayed.
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/bsp/bsp_time.c:63:    UNUSED_PARAMETER(arg);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:52:#define NUS_SERVICE_UUID_TYPE           BLE_UUID_TYPE_VENDOR_BEGIN                  /**< UUID type for the Nordic UART Service (vendor specific). */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:86:BLE_NUS_DEF(m_nus, NRF_SDH_BLE_TOTAL_LINK_COUNT);                                   /**< BLE NUS service instance. */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:100:    {BLE_UUID_NUS_SERVICE, NUS_SERVICE_UUID_TYPE},
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:184:    if (p_evt->type == BLE_NUS_EVT_RX_DATA) {
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:185:        NRF_LOG_DEBUG("Received data from BLE NUS.");
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:297:    // Initialize NUS.
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:480:            UNUSED_VARIABLE(err_code);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:718:    UNUSED_PARAMETER(p_context);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/usb_main.c:54:            UNUSED_VARIABLE(ret);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/usb_main.c:77:            UNUSED_VARIABLE(ret);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/app_main.c:431:    UNUSED_VARIABLE(ret);
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/emojis.h:215:    {":handshake:", "\xf0\x9f\xa4\x9d"}, // 🤝
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart:10:Uuid nrfUUID = Uuid.parse("6E400001-B5A3-F393-E0A9-E50E24DCCA9E");
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart:11:Uuid uartRX = Uuid.parse("6E400002-B5A3-F393-E0A9-E50E24DCCA9E");
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart:12:Uuid uartTX = Uuid.parse("6E400003-B5A3-F393-E0A9-E50E24DCCA9E");
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:1640:// <i>                Then use X(USBD_STRING_WINUSB, =0xEE, (APP_USBD_STRING_DESC(...)))
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:5595:// <o> BLE_NUS_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:5598:#ifndef BLE_NUS_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:5599:#define BLE_NUS_BLE_OBSERVER_PRIO 2
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:5602:// <o> BLE_NUS_C_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:5605:#ifndef BLE_NUS_C_BLE_OBSERVER_PRIO
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/bootloader/src/sdk_config.h:5606:#define BLE_NUS_C_BLE_OBSERVER_PRIO 2
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_power.c:173:        UNUSED_VARIABLE(err_code); //handle no-debug case
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_power.c:292:        UNUSED_VARIABLE(err_code);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_power.c:363:        UNUSED_VARIABLE(err_code); //handle no-debug case
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_power.c:372:        UNUSED_VARIABLE(err_code); //handle no-debug case
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_rng.c:97:        UNUSED_RETURN_VALUE(nrf_queue_push(&m_rand_pool, &rng_val));
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_clock.c:360:        UNUSED_VARIABLE(sd_clock_hfclk_is_running(&is_running));
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_clock.c:424:    UNUSED_PARAMETER(interval);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_clock.c:425:    UNUSED_PARAMETER(handler);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/integration/nrfx/legacy/nrf_drv_clock.c:483:    UNUSED_PARAMETER(p_is_calibrating);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/doc/nrfx.doxyfile:1222:# If the HTML_DYNAMIC_MENUS tag is set to YES then the generated HTML
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/doc/nrfx.doxyfile:1231:#HTML_DYNAMIC_MENUS     = YES
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/documentation/release_notes.txt:3493:- Cleaned up the use of RX/TX in the Nordic UART Service (NUS) to match
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb.h:106:#ifndef PB_UNUSED
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb.h:107:#define PB_UNUSED(x) (void)(x)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb.h:547:#define PB_SET_ERROR(stream, msg) PB_UNUSED(stream)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_decode.c:480:    PB_UNUSED(wire_type);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_decode.c:481:    PB_UNUSED(iter);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_decode.c:1237:    PB_UNUSED(field);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_decode.c:1243:    PB_UNUSED(field);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf5340_application.svd:1345:              <name>UNUSED0</name>
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf5340_application.svd:1359:              <name>UNUSED1</name>
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf5340_application.svd:1959:              <name>UNUSED0</name>
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf5340_application.svd:1977:              <name>UNUSED1</name>
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf5340_application.svd:2040:              <name>UNUSED0</name>
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf5340_application.svd:2058:              <name>UNUSED1</name>
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_encode.c:336:    PB_UNUSED(field);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_encode.c:627:    PB_UNUSED(field);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nano-pb/pb_encode.c:633:    PB_UNUSED(field);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/external/nrf_cc310_bl/include/ssi_pal_types.h:75:#define SASI_UNUSED_PARAM(prm)  ((void)prm)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf52840.h:2254:  __IM  uint32_t  UNUSED;                       /*!< (@ 0x00000000) Unused.                                                    */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf52840.h:2527:  __IM  uint32_t  UNUSED;                       /*!< (@ 0x00000000) Unused.                                                    */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf52810.h:1739:  __IM  uint32_t  UNUSED;                       /*!< (@ 0x00000000) Unused.                                                    */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf51.h:1045:  __IM  uint32_t  UNUSED;                       /*!< (@ 0x00000000) Unused.                                                    */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:49:static bool nrf91_errata_1(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:50:static bool nrf91_errata_2(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:51:static bool nrf91_errata_4(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:52:static bool nrf91_errata_6(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:53:static bool nrf91_errata_7(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:54:static bool nrf91_errata_8(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:55:static bool nrf91_errata_9(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:56:static bool nrf91_errata_10(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:57:static bool nrf91_errata_12(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:58:static bool nrf91_errata_14(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:59:static bool nrf91_errata_15(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:60:static bool nrf91_errata_16(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:61:static bool nrf91_errata_17(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:62:static bool nrf91_errata_20(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:63:static bool nrf91_errata_21(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:64:static bool nrf91_errata_23(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:65:static bool nrf91_errata_24(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:66:static bool nrf91_errata_26(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:67:static bool nrf91_errata_27(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:68:static bool nrf91_errata_28(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:69:static bool nrf91_errata_29(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:70:static bool nrf91_errata_30(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:71:static bool nrf91_errata_31(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:72:static bool nrf91_errata_32(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf91_erratas.h:73:static bool nrf91_errata_33(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf52840_bitfields.h:15209:#define UART_BAUDRATE_BAUDRATE_Baud230400 (0x03AFB000UL) /*!< 230400 baud (actual rate: 231884) */
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:49:static bool nrf53_errata_1(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:50:static bool nrf53_errata_2(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:51:static bool nrf53_errata_3(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:52:static bool nrf53_errata_4(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:53:static bool nrf53_errata_5(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:54:static bool nrf53_errata_6(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:55:static bool nrf53_errata_7(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:56:static bool nrf53_errata_8(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:57:static bool nrf53_errata_9(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:58:static bool nrf53_errata_10(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:59:static bool nrf53_errata_11(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:60:static bool nrf53_errata_12(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:61:static bool nrf53_errata_13(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:62:static bool nrf53_errata_14(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:63:static bool nrf53_errata_15(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:64:static bool nrf53_errata_16(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:65:static bool nrf53_errata_18(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:66:static bool nrf53_errata_19(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:67:static bool nrf53_errata_20(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:68:static bool nrf53_errata_21(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:69:static bool nrf53_errata_22(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:70:static bool nrf53_errata_23(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:71:static bool nrf53_errata_26(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:72:static bool nrf53_errata_27(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:73:static bool nrf53_errata_28(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:74:static bool nrf53_errata_29(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:75:static bool nrf53_errata_30(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:76:static bool nrf53_errata_31(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:77:static bool nrf53_errata_32(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:78:static bool nrf53_errata_33(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:79:static bool nrf53_errata_34(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:80:static bool nrf53_errata_36(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:81:static bool nrf53_errata_37(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:82:static bool nrf53_errata_42(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:83:static bool nrf53_errata_43(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:84:static bool nrf53_errata_44(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:85:static bool nrf53_errata_45(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:86:static bool nrf53_errata_46(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:87:static bool nrf53_errata_47(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:88:static bool nrf53_errata_49(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:89:static bool nrf53_errata_50(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:90:static bool nrf53_errata_51(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:91:static bool nrf53_errata_52(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:92:static bool nrf53_errata_53(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:93:static bool nrf53_errata_54(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:94:static bool nrf53_errata_55(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:95:static bool nrf53_errata_57(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:96:static bool nrf53_errata_58(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:97:static bool nrf53_errata_59(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:98:static bool nrf53_errata_62(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:99:static bool nrf53_errata_64(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:100:static bool nrf53_errata_65(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:101:static bool nrf53_errata_66(void) __UNUSED;
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/nrf52_sdk/modules/nrfx/mdk/nrf53_erratas.h:102:static bool nrf53_errata_67(void) __UNUSED;
probe/chameleon/chameleon_enum.py:32:    GET_SLOT_INFO = 1019
# SPEC - tenor/rekey mở rộng lái Chameleon Ultra

Trạng thái: DRAFT chờ founder duyệt. Ngày lập: 2026-07-20.
Phạm vi: thêm hỗ trợ **Chameleon Ultra** vào app native `tenor/rekey` hiện có (đang lái đầu đọc XIXEI X7), theo kiến trúc capability-driven, một app lái được nhiều máy.

Nền tảng nghiên cứu: 6 agent đã mổ ChameleonUltraGUI (Flutter, GPLv3), CLI Python chính chủ (RfidResearchGroup/ChameleonUltra, GPLv3), cơ chế DFU, và toàn bộ 20 file Swift + `x7d.py` của rekey. Mọi số liệu trong spec này trích từ source thật, có dẫn file.

---

## 1. Mục tiêu và phi-mục-tiêu

### 1.1 Mục tiêu
- `tenor/rekey` lái được **Chameleon Ultra** đầy đủ như ChameleonUltraGUI, nhưng trong vỏ native SwiftUI đẹp sẵn có.
- **Cắm máy nào, UI tự mọc tính năng của máy đó.** Cắm X7 hiện UI một-thẻ hiện tại; cắm Chameleon mọc thêm thư viện 8 slot, giả lập, LF, nạp firmware.
- Tái dùng tối đa: bộ não protocol + crypto lấy nguyên xi từ CLI Python chính chủ; vỏ SwiftUI (lưới sector, inspector, theme, i18n, APDU console) dùng lại.
- Giữ nguyên cách phát hành: DMG ký Developer-ID + notarize, GPLv3 (như rekey đang làm).

### 1.2 Phi-mục-tiêu (không làm trong bản này)
- KHÔNG fork/port cái Flutter GUI. Vỏ là SwiftUI native.
- KHÔNG lên Mac App Store (chứa code RfidResearchGroup nên Tenor không cấp được exception cho Apple; DMG là đúng kênh).
- KHÔNG hỗ trợ BLE giai đoạn đầu. Chameleon nối qua USB-C CDC serial. (BLE là transport thứ 2, để v2.)
- KHÔNG làm mấy panel phụ của GUI ngay: NDEF editor, dump editor full, HF/LF sniffing nặng, đa ngôn ngữ 25 thứ tiếng. Đưa vào "sau MVP".

### 1.3 Người dùng
Founder (nhà cung cấp khóa khách sạn + kỹ thuật). Công cụ nội bộ Tenor. Chạy trên Mac, cắm thiết bị vật lý.

---

## 2. Kiến trúc - capability-driven, một vỏ nhiều máy

### 2.1 Nguyên tắc lõi
Mỗi thiết bị = một **daemon Python** riêng nói **cùng một giao thức JSON** mà vỏ SwiftUI đã hiểu. Vỏ không hardcode `if X7 / if Chameleon`; nó đọc **bảng năng lực** do daemon khai lúc kết nối rồi bật/tắt module UI tương ứng.

```
                 ┌──────────────────────── SwiftUI shell (vỏ chung) ────────────────────────┐
                 │  RootView · SectorGrid · Inspector · Theme · i18n · APDU · Settings       │
                 │  + panel gated theo capabilities: SlotLibrary · Emulate · LF · Flashing   │
                 └───────────────▲───────────────────────────────────────────────▲──────────┘
                                 │ newline-JSON contract (id/result/event)         │
                    ┌────────────┴────────────┐                       ┌────────────┴────────────┐
                    │  x7d.py  (đang có)       │                       │  chameleon_d.py (mới)   │
                    │  X7Card (HID PN533)      │                       │  ChameleonCMD/com (lib) │
                    └─────────────────────────┘                       └─────────────────────────┘
                            đầu đọc X7                                       Chameleon Ultra
```

### 2.2 Giao thức daemon (đã có, giữ nguyên)
Newline-delimited JSON trên stdin/stdout của tiến trình con (`x7d.py` docstring + `X7Engine.swift`):
- request: `{"id":n, "method":str, "params":{}}`
- response: `{"id":n, "result":{}}` hoặc `{"id":n, "error":str}`
- event: `{"event":"progress", "method":str, ...}` (không id, đẩy giữa chừng)

`chameleon_d.py` nói **đúng giao thức này**, thêm method riêng cho Chameleon. Vỏ không phải học giao thức mới.

### 2.3 Bảng năng lực (capability manifest)
Method `info` trả kèm `capabilities`. Ví dụ:

```jsonc
// X7 khai:
{ "family":"x7", "model":"XIXEI X7-P", "serial":"...", "hw":"...",
  "capabilities": { "slots":0, "emulate":false, "lf":false, "dfu":false,
                    "sniff":false, "attacks":["dict","nested"] } }

// Chameleon Ultra khai:
{ "family":"chameleon-ultra", "model":"Chameleon Ultra", "serial":"...", "hw":"...",
  "capabilities": { "slots":8, "emulate":true, "lf":true, "dfu":true,
                    "sniff":true,
                    "attacks":["dict","nested","staticNested","darkside","hardnested"],
                    "writeModes":["normal","denied","deceive","shadow","shadowReq"] } }
```

Vỏ đọc `capabilities` rồi: `slots>0` bật SlotLibraryView, `emulate` bật EmulateToggle, `lf` bật LFPanel, `dfu` bật FlashingView, `attacks` quyết định menu "khôi phục khóa" hiện những đòn nào.

---

## 3. `chameleon_d.py` - thiết kế daemon (mới)

Sibling của `x7d.py`. Ôm một `ChameleonCom` (transport) + `ChameleonCMD` (lệnh) nhập thẳng từ CLI chính chủ (GPLv3, hợp lệ vì rekey đã GPLv3). Không nhập lớp CLI tương tác (`chameleon_cli_*`, prompt_toolkit) - chỉ nhập `chameleon_com`, `chameleon_cmd`, `chameleon_enum`.

Transport: pyserial 115200, DTR high. Frame LRC-checked (SOF `0x11`, cmd 2B big-endian, status 2B, len 2B, LRC). `send_cmd_sync` là primitive request/response; daemon serial hóa mọi op (một thiết bị, một luồng lệnh).

### 3.1 Method dùng chung với x7d.py (vỏ đã biết)
| method | ánh xạ ChameleonCMD | ghi chú |
|---|---|---|
| `info` | `get_device_model` + `get_app_version` + `get_git_version` + `get_device_chip_id` + `get_device_capabilities` | trả kèm `capabilities` |
| `poll` | `hf14a_scan` | trả `{present, uid, atqa, sak, ats, kind}` |
| `decode` | `mf1_check_keys_of_sectors` (dict, on-device) → `mf1_nested_acquire`/`mf1_darkside_acquire` (+ binary C) → `mf1_read_one_block` dump | flow khôi phục khóa + đọc trọn, đẩy `progress` từng sector |
| `read_ntag` | `mf0_ntag_*` read | thẻ Ultralight/NTAG |
| `apdu` | `hf14a_raw` | passthrough |
| `write_mfd` | `mf1_write_one_block` (+ auth key nguồn → fallback) | ghi lên thẻ thật (reader mode) |
| `format` | `mf1_write_one_block` trailer factory | như x7d |
| `keys_default` / `learned_stats` / `learned_clear` | dùng lại module `learned_keys.py` của rekey | cache khóa dùng chung 2 máy |

### 3.2 Method riêng Chameleon (vỏ thêm panel để gọi)
| method | ánh xạ ChameleonCMD | dùng cho |
|---|---|---|
| `slots_list` | `get_slot_info` + `get_enabled_slots` + `get_all_slot_nicks` + `get_active_slot` | SlotLibraryView |
| `slot_select` | `set_active_slot(SlotNumber)` | chọn slot đang bật |
| `slot_set_type` | `set_slot_tag_type` + `set_slot_data_default` | đặt loại thẻ cho slot |
| `slot_enable` | `set_slot_enable(SlotNumber, TagSenseType, bool)` | bật/tắt HF/LF của slot |
| `slot_nick` | `set_slot_tag_nick` / `get_slot_tag_nick` | đặt tên slot |
| `slot_save` | `slot_data_config_save` | ghi slot xuống flash |
| `emulate_load` | `mf1_write_emu_block_data` (nạp dump vào slot) + `mf1_set_block_anti_coll_mode` | biến 1 dump thành thẻ giả lập |
| `emulate_mode` | `set_device_reader_mode(False/True)` | chuyển reader ↔ tag/emulate |
| `emu_read` | `mf1_read_emu_block_data` | đọc lại nội dung slot đang giả lập |
| `magic_write` | `mf1_set_gen1a_mode`/`mf1_set_gen2_mode` + block writes | clone ra thẻ magic (CUID/gen1a/gen2) |
| `lf_scan` | `em410x_scan` | LFPanel đọc EM410x |
| `lf_write` | `em410x_write_to_t55xx` | ghi EM410x ra T5577 |
| `lf_emu_id` | `em410x_set_emu_id` / `em410x_get_emu_id` | id LF cho slot giả lập |
| `settings_get` / `settings_set` | `get/set_button_press_config`, `get/set_animation_mode`, `get/set_sleep_timeout`, `get_battery_info`, `save_settings` | SettingsView (mục Chameleon) |
| `dfu_flash` | xem mục 4 | FlashingView |

### 3.3 Enum then chốt (chameleon_enum.py, trích đúng)
- `SlotNumber`: `SLOT_1..SLOT_8` (=1..8), wire `to_fw(i)=i-1`.
- `TagSenseType`: `UNDEFINED=0, LF=1, HF=2`.
- `TagSpecificType` HF: `MIFARE_Mini=1000, MIFARE_1024=1001, MIFARE_2048=1002, MIFARE_4096=1003, NTAG_213/215/216=1100/1101/1102, MF0UL11/21=1105/1106...`; LF: `EM410X=100, HIDProx=200, ...`.
- `MfcKeyType`: `A=0x60, B=0x61`.
- `MifareClassicWriteMode`: `NORMAL=0, DENIED=1, DECEIVE=2, SHADOW=3, SHADOW_REQ=4`.
- `MifareClassicDarksideStatus`: `OK=0, CANT_FIX_NT=1, LUCKY_AUTH_OK=2, NO_NAK_SENT=3, TAG_CHANGED=4`.
- `ButtonType`: `A=0x41, B=0x42`; `ButtonPressFunction`: `NONE=0, NEXTSLOT=1, PREVSLOT=2, CLONE=3, BATTERY=4, FIELDGEN=5`.

### 3.4 Binary C cho đòn khóa nặng
Dict check (`mf1_check_keys_of_sectors`) chạy 100% trên firmware, KHÔNG cần binary. Nhưng **nested / static-nested / darkside / hardnested cần binary C biên dịch** (`../src/*.c` build qua CMake: `nested`, `staticnested`, `darkside`, `mfkey32v2`, `mfkey64`, `hardnested`). Đây là kiến trúc giống Proxmark, không tránh được. rekey đã biết đóng gói binary native cho `x7d` (darkside solver đã vendored) - dùng lại quy trình đó: build cho macOS arm64, ký + notarize trong app bundle, `subprocess` từ `chameleon_d.py`, parse stdout thành `progress` events.

---

## 4. Nạp firmware (DFU) - founder yêu cầu, đưa vào

### 4.1 Cơ chế (trích source thật)
Chameleon = Nordic nRF52840. Thường: USB CDC `VID 0x6868 / PID 0x8686`. Vào bootloader: re-enumerate thành `VID 0x1915 / PID 0x521f`.

**Vào DFU** = gửi 1 lệnh reboot vào bootloader. Command `ENTER_BOOTLOADER = 1010` (`chameleon_enum.py:20`). Bytes cứng (từ `resource/tools/enter_dfu.py`):
```
b'\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00'   # SOF, LRC, cmd=0x03F2 (1010), status, len=0, headLRC, LRC
```
Mở port thường 115200 DTR high, ghi 10 byte này, đóng. Fallback tay: tắt máy, giữ nút B khi cắm USB (LED 4+5 nhấp nháy = bootloader).

**Flash** = giao thức Nordic Secure DFU trên cùng cổng serial (giờ ở PID 0x521f), 115200. Firmware là gói `ultra-dfu-app.zip` (Nordic DFU package: `application.dat` = init packet protobuf đã ký + `application.bin` = ảnh app), ký bằng `chameleon.pem` - bootloader từ chối gói không đúng chữ ký, nên **chỉ flash được firmware chính chủ**, không flash ảnh tự chế.

**Nguồn firmware**: GitHub `RfidResearchGroup/ChameleonUltra` - asset prerelease `ultra-dfu-app.zip` (Ultra) hoặc `lite-dfu-app.zip` (Lite). GUI cũng cho chọn file `.zip` tay.

### 4.2 Cách replicate trong `chameleon_d.py` (khuyến nghị)
KHÔNG tự viết lại giao thức DFU (CRC/PRN/SLIP dễ sai, rủi ro). Đường chính chủ + an toàn nhất:
1. `requests.get` GitHub releases API → tải `ultra-dfu-app.zip` (chọn ultra/lite theo `get_device_model`).
2. Ghi 10 byte enter-DFU vào cổng thường, đóng.
3. Poll `serial.tools.list_ports` tới khi thấy `1915:521f` (bootloader lên).
4. `subprocess` `adafruit-nrfutil dfu serial -pkg app.zip -p <port> -b 115200` (pure-Python, cài qua pip, không cần binary Nordic riêng) HOẶC `nrfutil device program --firmware app.zip --traits nordicDfu`. Parse tiến độ → `progress` events.

pip deps thêm: `requests` (đã có pyserial). Cân nhắc bundle `adafruit-nrfutil` vào venv đóng gói.

### 4.3 An toàn chống brick (BẮT BUỘC)
- **CHỈ flash `ultra-dfu-app.zip` (app-only). CẤM daemon flash `*-dfu-full.zip`** (bootloader+softdevice) - fail giữa chừng có thể brick. App-only fail thì bootloader còn sống, retry được.
- Trước khi flash, verify gói: `.dat` là init packet có chữ ký, hash (đảo byte) khớp `.bin` (mirror `validateFiles`), fail nhanh nếu sai.
- Xử lý race re-enumerate trên macOS: sau enter-DFU thiết bị đổi VID/PID và đổi `/dev/cu.usbmodem*`; phải poll chờ `1915:521f`, đừng giả định path cổng cố định.
- Nếu `enter_bootloader` fail (firmware đã crash): hiện hướng dẫn combo nút B + cắm lại, đừng treo.

---

## 5. Danh mục panel UI - MVP vs sau

Mọi panel gated theo `capabilities`; cắm X7 thì ẩn hết mấy cái Chameleon-only.

| Panel | File mới | Gated | Giai đoạn |
|---|---|---|---|
| Chọn thiết bị + bảng năng lực (header device picker) | `Engine/DeviceRegistry.swift`, `Engine/DeviceCapabilities.swift` | luôn | **MVP** |
| Lưới sector / inspector / clone (thẻ HF Classic) | dùng lại `Views/SectorGrid.swift` + `SectorInspector.swift` | HF | **MVP** (transfer thẳng vì slot HF = giả lập Classic) |
| Đọc thẻ + khôi phục khóa (dict → nested → darkside) | dùng lại luồng decode + menu attacks theo `capabilities.attacks` | luôn | **MVP** |
| Thư viện 8 slot (list/active/enable/type/nick/save) | `Views/SlotLibraryView.swift` | `slots>0` | **MVP** |
| Nạp dump vào slot + toggle giả lập/đọc | `Views/EmulateToggle.swift` | `emulate` | **MVP** |
| Ghi ra thẻ magic (gen1a/gen2/CUID) | dùng lại CloneSheet + `magic_write` | luôn | **MVP** |
| LF EM410x (scan/write/emu-id) | `Views/LFPanel.swift` | `lf` | sau MVP |
| Nạp firmware DFU | `Views/FlashingView.swift` | `dfu` | **MVP** (founder cần) |
| Cài đặt Chameleon (nút/LED/sleep/pin) | mở rộng `Views/SettingsView.swift` | luôn | sau MVP |
| Sniffing HF/LF, dump editor, NDEF | - | `sniff` | v2 (không làm) |

---

## 6. Bản đồ file rekey cần sửa (dẫn chứng thật)

### 6.1 Dùng lại nguyên xi (device-neutral)
`App.swift`, `Shell/RootView.swift`, `Brand/*` (6 file theme/i18n/lockup), `Engine/AccessBits.swift`, `Engine/KeyStore.swift`, `Views/SectorGrid.swift`, `Views/SectorInspector.swift`, `Views/PageTable.swift`, `Views/ApduConsole.swift`, `Views/SettingsView.swift`.

### 6.2 Cần tổng quát hóa
| File | Sửa gì |
|---|---|
| `Engine/X7Engine.swift` | Đổi tên `X7Engine` → `DeviceBridge`. Giữ nguyên phần actor (process, pipe, `transact`/`route`/`timeoutRequest`, event routing) - vốn đã là JSON-RPC transport thuần. `resolvePaths()` nhận **tên script daemon + probe subdir từ `DeviceDescriptor`** thay vì hardcode `x7d.py` (đang cứng ở `:40`, `:55`). Method dùng chung giữ; verb Chameleon-only thêm vào, gated bằng capability. |
| `AppModel.swift` | Đang single-document, hardwire `let engine = X7Engine()` (`:47`), hình dạng MIFARE-Classic. Tách thành **shell state chung + model theo thiết bị**: (a) ôm `DeviceBridge` lấy từ registry; (b) publish `capabilities`; (c) tách logic Classic để Chameleon thêm được state thư viện-slot + giả lập. `connect()`/`monitor()` giữ nhưng phải chịu được `info` khác hình dạng. |
| `Engine/Models.swift` | Thêm Codable shapes Chameleon (slot info, emulator config, capabilities). `DeviceInfo` thêm field `family` + `capabilities`. |
| `Engine/CardDump.swift` | Giữ cho Classic; thêm biến thể slot-image nếu expose slot save/load. Sửa nhẹ. |
| env contract | `X7_PYTHON` giữ; `X7_PROBE_DIR` tổng quát thành probe root chứa cả `x7d.py` lẫn `chameleon_d.py`. |

### 6.3 File mới
`Engine/DeviceRegistry.swift` (descriptor + `detect()` enumerate USB), `Engine/DeviceCapabilities.swift`, `Engine/ChameleonModels.swift`, `Views/SlotLibraryView.swift`, `Views/EmulateToggle.swift`, `Views/LFPanel.swift`, `Views/FlashingView.swift`, `probe/chameleon_d.py`.

### 6.4 Định tuyến chọn daemon khi cắm máy
1. `DeviceRegistry.detect()` lúc `AppModel.connect()`: X7 là HID (`2518:6022`, IOKit `IOHIDManager`); Chameleon là USB-CDC serial (`/dev/cu.usbmodem*`, VID `6868`). Match theo VID:PID.
2. Match → `DeviceDescriptor { daemonScript, probeSubdir, capabilities }`.
3. `resolvePaths(for: descriptor)` build `script = probeDir/descriptor.daemonScript`; `startIfNeeded()` giữ nguyên (`python -B <script>`).
4. `capabilities` gate UI (nút `recover` đang disabled ở `RootView.swift:186` là tiền lệ sẵn cho verb gated theo năng lực).
5. Hot-swap: `monitor()` (poll 1.5s, `:109`) chạy lại `detect()` khi reader offline → cắm máy khác thì tear down bridge cũ, spawn daemon đúng. Cắm cả hai thì header cho chọn.

---

## 7. Lệnh (build / test / run)
- Test engine hardware-free: `cd probe && python3 test_all.py` (thêm test cho `chameleon_d.py` bằng FakeChameleon mô phỏng frame LRC, giống FakeCard của x7).
- Build app: `app/tools/package.sh` (thêm `chameleon_d.py` + `chameleon_com/cmd/enum.py` + binary C vào `PROBE_MODULES`, thêm `adafruit-nrfutil` vào venv đóng gói).
- Chạy dev: mở `/Applications/tenor-rekey.app`, cắm Chameleon.
- Verify: trên máy thật (founder cắm), KHÔNG kết luận bằng lý thuyết.

## 8. Code style / quy ước
- Swift: khớp style 20 file sẵn có (naming PascalCase, `Engine/*`, `Views/*`). Không refactor ngoài phạm vi.
- Python: khớp `x7d.py`/`x7lib.py` (daemon class + METHODS tuple + handle dispatch + `_drop()` on OSError).
- Tenor: KHÔNG em-dash (§0), KHÔNG hardcode brand (import `@tenor/brand`), i18n mọi string user-facing.
- Repo public: commit prose tự nhiên KHÔNG nhắc Claude/AI; guard key-leak (grep khóa thật của founder) phải rỗng trước push, dùng hex placeholder trong ví dụ/test; giữ notice GPL + copyright RfidResearchGroup của code nhập vào.

## 9. Chiến lược verify - đối chiếu GUI từng tính năng
Nguyên tắc rekey: **không tuyên bố "y như GUI" bằng miệng, verify trên máy thật rồi mới tick.** Checklist parity (chạy cạnh ChameleonUltraGUI trên cùng con Chameleon + cùng thẻ):
- [ ] Connect + đọc model/serial/firmware/pin khớp GUI.
- [ ] `slots_list` khớp 8 slot GUI hiện (loại + tên + active).
- [ ] Đọc thẻ Classic: UID/SAK/ATQA/PRNG/backdoor khớp Read Card của GUI.
- [ ] Khôi phục khóa: dict trên thẻ FF ra 16/16; nested trên thẻ weak-PRNG; darkside trên thẻ 0-khóa - so key ra với GUI.
- [ ] Dump đầy đủ khớp `.mfd` GUI xuất.
- [ ] Nạp dump vào slot + giả lập → đầu đọc cửa nhận (test thực địa).
- [ ] Clone ra thẻ magic gen1a/gen2 → đọc lại byte-identical.
- [ ] LF EM410x scan/write (sau MVP).
- [ ] **Nạp firmware**: flash `ultra-dfu-app.zip` → firmware version tăng đúng, máy sống, so với GUI flash.

## 10. Ranh giới (always / ask-first / never)
- **Always**: verify trên hardware thật; giữ safety guards sẵn có của rekey (target_uid pin, trailer anti-brick, access-bits validate); GPL notices.
- **Ask-first (hỏi founder)**: đụng `deploy_*`/migrations/.env (không liên quan ở đây); bump version; push + tag; đổi contract JSON đã lock của x7d.
- **Never**: flash `*-dfu-full.zip` (brick); flash firmware không phải chính chủ đã ký; commit khóa thật/UID thật; lên App Store; fork Flutter.

## 11. Lộ trình phase + ước lượng công
| Phase | Nội dung | Ước lượng |
|---|---|---|
| P0 | `chameleon_d.py`: connect + `info`+capabilities + `slots_list` + `poll` + đọc block + dump. FakeChameleon test. | ~1-1.5 tuần |
| P1 | Khôi phục khóa: dict (firmware) + build/bundle binary C nested+darkside + wire `decode` + progress events. | ~1-1.5 tuần |
| P2 | Vỏ: `DeviceRegistry` + `DeviceCapabilities` + tổng quát hóa `X7Engine`→`DeviceBridge` + tách `AppModel`. Cắm máy tự chọn daemon + gate UI. | ~1.5-2.5 tuần |
| P3 | Panel Chameleon: `SlotLibraryView` + `EmulateToggle` + `magic_write` + nạp dump vào slot. | ~1.5-2 tuần |
| P4 | **DFU firmware**: download + enter-DFU + adafruit-nrfutil + `FlashingView` + an toàn brick. | ~1-1.5 tuần |
| P5 | LF panel + settings Chameleon + hoàn thiện + verify parity đối chiếu GUI trên hardware. | ~1-2 tuần |
| **Tổng MVP (P0-P4)** | một app hai máy, có slot/giả lập/clone/firmware | **~6.5-9 tuần-người** |
| Full (P0-P5) | thêm LF + settings + parity đầy đủ | **~7.5-11 tuần** |

Cách chạy: orchestrate như rekey đang làm - Claude ra đề + review + verify + commit; executor (Opus/Codex) viết module tự chứa theo spec; verify hardware mỗi phase mới tick.

## 12. Rủi ro + câu hỏi mở
- **Binary C cross-compile + notarize** cho macOS arm64: rekey đã giải cho darkside solver, tái dùng quy trình. Rủi ro trung bình.
- **adafruit-nrfutil** là thế hệ nrfutil cũ (pure-Python) - verify nó flash được gói `ultra-dfu-app.zip` đời mới trên máy thật; nếu không, chuyển sang Nordic `nrfutil` (cần bundle binary Rust).
- **Mô hình single-document → thư viện slot** là phần rework tốn nhất (P2). Quyết định kiến trúc: giữ document cho X7, thêm chiều "device → 8 slot → nội dung" cho Chameleon.
- **Firmware full-flash**: có bao giờ cần flash bootloader+softdevice không? Mặc định KHÔNG (brick risk). Nếu cần, làm riêng có cảnh báo mạnh + xác nhận founder.
- **Đã chốt (founder 2026-07-20)**: (a) LF để **P5**, MVP tập trung HF + firmware. (b) Giữ tên **tenor/rekey**, "Chameleon Ultra" chỉ là **tên thiết bị** app hỗ trợ - KHÔNG sub-brand, KHÔNG lockup con.

exec
/bin/zsh -lc "nl -ba /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart | sed -n '1,320p'; nl -ba /Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c | sed -n '160,215p'; rg -n \"GET_DEVICE|103?0|1019|GET_.*CAP|03FB\" /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib /Users/tuan/Claude/chameleon/ChameleonUltra/software /Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src -g '*.dart' -g '*.py' -g '*.h' -g '*.c' | head -n 160" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	import 'dart:async';
     2	import 'dart:io';
     3	import 'dart:typed_data';
     4	
     5	import 'package:chameleonultragui/connector/serial_abstract.dart';
     6	import 'package:chameleonultragui/helpers/general.dart';
     7	import 'package:flutter_reactive_ble/flutter_reactive_ble.dart';
     8	
     9	// Regular
    10	Uuid nrfUUID = Uuid.parse("6E400001-B5A3-F393-E0A9-E50E24DCCA9E");
    11	Uuid uartRX = Uuid.parse("6E400002-B5A3-F393-E0A9-E50E24DCCA9E");
    12	Uuid uartTX = Uuid.parse("6E400003-B5A3-F393-E0A9-E50E24DCCA9E");
    13	
    14	// DFU
    15	Uuid dfuUUID = Uuid.parse("FE59");
    16	Uuid dfuControl = Uuid.parse("8EC90001-F315-4F60-9FB8-838830DAEA50");
    17	Uuid dfuFirmware = Uuid.parse("8EC90002-F315-4F60-9FB8-838830DAEA50");
    18	
    19	class BLESerial extends AbstractSerial {
    20	  FlutterReactiveBle flutterReactiveBle = FlutterReactiveBle();
    21	  QualifiedCharacteristic? txCharacteristic;
    22	  QualifiedCharacteristic? rxCharacteristic;
    23	  QualifiedCharacteristic? firmwareCharacteristic;
    24	  Stream<List<int>>? receivedDataStream;
    25	  StreamSubscription<ConnectionStateUpdate>? connection;
    26	  Map<String, Chameleon> chameleonMap = {};
    27	  bool inSearch = false;
    28	
    29	  BLESerial({required super.log});
    30	
    31	  Future<List> availableDevices() async {
    32	    if (inSearch) {
    33	      log.w("Multiple searches in one time not allowed! FIXME");
    34	      return [];
    35	    }
    36	
    37	    List<DiscoveredDevice> foundDevices = [];
    38	    await performDisconnect();
    39	
    40	    Completer<List<DiscoveredDevice>> completer =
    41	        Completer<List<DiscoveredDevice>>();
    42	    StreamSubscription<DiscoveredDevice> subscription;
    43	
    44	    inSearch = true;
    45	    subscription = flutterReactiveBle.scanForDevices(
    46	      withServices: [nrfUUID, dfuUUID],
    47	      scanMode: ScanMode.lowLatency,
    48	    ).listen((device) {
    49	      if (!foundDevices.contains(device)) {
    50	        for (var foundDevice in foundDevices) {
    51	          if (foundDevice.id == device.id) {
    52	            return;
    53	          }
    54	        }
    55	        foundDevices.add(device);
    56	      }
    57	    }, onError: (e) {
    58	      log.e("Got BLE search error: $e");
    59	      inSearch = false;
    60	      if (Platform.isIOS) {
    61	        throw (e); // BLE is primary there, throw exception
    62	      } else {
    63	        completer.complete([]); // Other platforms: we don't care
    64	      }
    65	    });
    66	
    67	    Timer(const Duration(seconds: 2), () {
    68	      subscription.cancel();
    69	      inSearch = false;
    70	      try {
    71	        completer.complete(foundDevices);
    72	        log.d('Found BLE devices: ${foundDevices.length}');
    73	      } catch (_) {}
    74	    });
    75	
    76	    return completer.future;
    77	  }
    78	
    79	  @override
    80	  bool isManualConnectionSupported() {
    81	    return false;
    82	  }
    83	
    84	  @override
    85	  Future<List<Chameleon>> availableChameleons(bool onlyDFU) async {
    86	    List<Chameleon> output = [];
    87	    for (var bleDevice in await availableDevices()) {
    88	      var dfuMode = false;
    89	      if (bleDevice.name.startsWith('ChameleonUltra')) {
    90	        device = ChameleonDevice.ultra;
    91	      } else if (bleDevice.name.startsWith('ChameleonLite')) {
    92	        device = ChameleonDevice.lite;
    93	      } else if (bleDevice.name.startsWith('CU-')) {
    94	        device = ChameleonDevice.ultra;
    95	        dfuMode = true;
    96	      } else if (bleDevice.name.startsWith('CL-')) {
    97	        device = ChameleonDevice.lite;
    98	        dfuMode = true;
    99	      } else {
   100	        // regular nRF device with UART
   101	        continue;
   102	      }
   103	
   104	      connectionType = ConnectionType.ble;
   105	
   106	      log.d("Found Chameleon ${chameleonDeviceName(device)}!");
   107	      if (!onlyDFU || onlyDFU && dfuMode) {
   108	        output.add(Chameleon(
   109	            port: bleDevice.id,
   110	            device: device,
   111	            type: connectionType,
   112	            dfu: dfuMode));
   113	      }
   114	
   115	      chameleonMap[bleDevice.id] = Chameleon(
   116	          port: bleDevice.id,
   117	          device: device,
   118	          type: connectionType,
   119	          dfu: dfuMode);
   120	    }
   121	
   122	    return output;
   123	  }
   124	
   125	  @override
   126	  Future<bool> connectSpecificDevice(dynamic devicePort) async {
   127	    // As BLE is unstable, we try to connect 5 times
   128	    // And fail only then
   129	    bool ret = false;
   130	    for (var i = 0; i < 5; i++) {
   131	      ret = await connectSpecificInternal(devicePort);
   132	      if (ret) {
   133	        break;
   134	      }
   135	    }
   136	
   137	    return ret;
   138	  }
   139	
   140	  Future<bool> connectSpecificInternal(dynamic devicePort) async {
   141	    Completer<bool> completer = Completer<bool>();
   142	    List<Uuid> services = [nrfUUID, uartRX, uartTX];
   143	    if (chameleonMap[devicePort]!.dfu) {
   144	      services = [dfuUUID, dfuControl, dfuFirmware];
   145	    }
   146	
   147	    await performDisconnect();
   148	    pendingConnection = true;
   149	    connection = flutterReactiveBle
   150	        .connectToAdvertisingDevice(
   151	      id: devicePort,
   152	      withServices: services,
   153	      prescanDuration: const Duration(seconds: 5),
   154	    )
   155	        .listen((connectionState) async {
   156	      log.w(connectionState);
   157	      if (connectionState.connectionState == DeviceConnectionState.connected) {
   158	        if (chameleonMap[devicePort]!.dfu) {
   159	          connected = true;
   160	          pendingConnection = false;
   161	          txCharacteristic = QualifiedCharacteristic(
   162	              serviceId: dfuUUID,
   163	              characteristicId: dfuControl,
   164	              deviceId: connectionState.deviceId);
   165	          receivedDataStream =
   166	              flutterReactiveBle.subscribeToCharacteristic(txCharacteristic!);
   167	          receivedDataStream!.listen((data) async {
   168	            if (messageCallback != null) {
   169	              try {
   170	                await messageCallback(Uint8List.fromList(data));
   171	              } catch (_) {
   172	                log.w(
   173	                    "Received unexpected data: ${bytesToHex(Uint8List.fromList(data))}");
   174	              }
   175	            }
   176	          }, onError: (dynamic error) async {
   177	            await performDisconnect();
   178	            log.e(error);
   179	          });
   180	
   181	          rxCharacteristic = QualifiedCharacteristic(
   182	              serviceId: dfuUUID,
   183	              characteristicId: dfuControl,
   184	              deviceId: connectionState.deviceId);
   185	
   186	          firmwareCharacteristic = QualifiedCharacteristic(
   187	              serviceId: dfuUUID,
   188	              characteristicId: dfuFirmware,
   189	              deviceId: connectionState.deviceId);
   190	
   191	          portName = devicePort;
   192	          device = chameleonMap[devicePort]!.device;
   193	          activeDevicePort = devicePort;
   194	
   195	          isDFU = true;
   196	          completer.complete(true);
   197	        } else {
   198	          txCharacteristic = QualifiedCharacteristic(
   199	              serviceId: nrfUUID,
   200	              characteristicId: uartTX,
   201	              deviceId: connectionState.deviceId);
   202	          receivedDataStream =
   203	              flutterReactiveBle.subscribeToCharacteristic(txCharacteristic!);
   204	          receivedDataStream!.listen((data) async {
   205	            if (messageCallback != null) {
   206	              try {
   207	                await messageCallback(Uint8List.fromList(data));
   208	              } catch (_) {
   209	                log.w(
   210	                    "Received unexpected data: ${bytesToHex(Uint8List.fromList(data))}");
   211	              }
   212	            }
   213	          }, onError: (dynamic error) async {
   214	            await performDisconnect();
   215	            log.e(error);
   216	          });
   217	
   218	          rxCharacteristic = QualifiedCharacteristic(
   219	              serviceId: nrfUUID,
   220	              characteristicId: uartRX,
   221	              deviceId: connectionState.deviceId);
   222	
   223	          try {
   224	            await flutterReactiveBle.writeCharacteristicWithResponse(
   225	                rxCharacteristic!,
   226	                value: Uint8List.fromList([
   227	                  0x11,
   228	                  0xef,
   229	                  0x03,
   230	                  0xfb,
   231	                  0x00,
   232	                  0x00,
   233	                  0x00,
   234	                  0x00,
   235	                  0x02,
   236	                  0x00
   237	                ]));
   238	
   239	            connected = true;
   240	            portName = devicePort;
   241	            device = chameleonMap[devicePort]!.device;
   242	            activeDevicePort = devicePort;
   243	
   244	            connectionType = ConnectionType.ble;
   245	            isDFU = false;
   246	
   247	            completer.complete(true);
   248	          } catch (_) {
   249	            try {
   250	              completer.complete(false);
   251	            } catch (_) {}
   252	          }
   253	        }
   254	      } else if (connectionState.connectionState ==
   255	          DeviceConnectionState.disconnected) {
   256	        await performDisconnect();
   257	        try {
   258	          completer.complete(false);
   259	        } catch (_) {}
   260	      }
   261	    }, onError: (Object error) {
   262	      log.e(error);
   263	      completer.complete(false);
   264	    });
   265	
   266	    return completer.future;
   267	  }
   268	
   269	  @override
   270	  Future<bool> performDisconnect() async {
   271	    final hadState = hasConnectionState || connection != null;
   272	    resetConnectionState();
   273	    txCharacteristic = null;
   274	    rxCharacteristic = null;
   275	    firmwareCharacteristic = null;
   276	    receivedDataStream = null;
   277	    if (connection != null) {
   278	      await connection!.cancel();
   279	      connection = null;
   280	      connected = false;
   281	      if (hadState) {
   282	        notifyConnectionStateChanged();
   283	      }
   284	      return true;
   285	    }
   286	    connected = false; // For debug button
   287	    if (hadState) {
   288	      notifyConnectionStateChanged();
   289	    }
   290	    return false;
   291	  }
   292	
   293	  @override
   294	  Future<bool> write(Uint8List command, {bool firmware = false}) async {
   295	    if (firmware) {
   296	      await flutterReactiveBle.writeCharacteristicWithoutResponse(
   297	          firmwareCharacteristic!,
   298	          value: command);
   299	    } else {
   300	      await flutterReactiveBle
   301	          .writeCharacteristicWithResponse(rxCharacteristic!, value: command);
   302	    }
   303	
   304	    return true;
   305	  }
   306	}
   160	 * @param[in] p_evt  Event received from the Battery Service.
   161	 */
   162	static void on_bas_evt(ble_bas_t *p_bas, ble_bas_evt_t *p_evt) {
   163	    switch (p_evt->evt_type) {
   164	        case BLE_BAS_EVT_NOTIFICATION_ENABLED:
   165	            break; // BLE_BAS_EVT_NOTIFICATION_ENABLED
   166	
   167	        case BLE_BAS_EVT_NOTIFICATION_DISABLED:
   168	            break; // BLE_BAS_EVT_NOTIFICATION_DISABLED
   169	
   170	        default:
   171	            // No implementation needed.
   172	            break;
   173	    }
   174	}
   175	
   176	/**@brief Function for handling the data from the Nordic UART Service.
   177	 *
   178	 * @details This function will process the data received from the Nordic UART BLE Service
   179	 *
   180	 * @param[in] p_evt       Nordic UART Service event.
   181	 */
   182	/**@snippet [Handling the data received over BLE] */
   183	static void nus_data_handler(ble_nus_evt_t *p_evt) {
   184	    if (p_evt->type == BLE_NUS_EVT_RX_DATA) {
   185	        NRF_LOG_DEBUG("Received data from BLE NUS.");
   186	        NRF_LOG_HEXDUMP_DEBUG(p_evt->params.rx_data.p_data, p_evt->params.rx_data.length);
   187	        data_frame_receive((uint8_t *)(p_evt->params.rx_data.p_data), p_evt->params.rx_data.length);
   188	    }
   189	}
   190	/**@snippet [Handling the data received over BLE] */
   191	
   192	void nus_data_response(uint8_t *p_data, uint16_t length) {
   193	    NRF_LOG_INFO("BLE nus service response data length: %d", length);
   194	    NRF_LOG_HEXDUMP_DEBUG(p_data, length);
   195	
   196	    ret_code_t err_code;
   197	    uint16_t remain = length;
   198	    uint16_t count = 0;
   199	    do {
   200	        remain = MIN(m_ble_nus_max_data_len, remain);
   201	        err_code = ble_nus_data_send(&m_nus, p_data + count, &remain, m_conn_handle);
   202	        // NRF_LOG_INFO("Data send length(amount): %d", remain);
   203	        if (err_code == NRF_SUCCESS) {
   204	            count += remain;
   205	            remain = length - count;
   206	        }
   207	        // NRF_LOG_INFO("Data send length(count): %d", count);
   208	        if (err_code == NRF_ERROR_BUSY) {
   209	            continue;
   210	        }
   211	        if ((err_code != NRF_ERROR_INVALID_STATE) &&
   212	                (err_code != NRF_ERROR_RESOURCES) &&
   213	                (err_code != NRF_ERROR_NOT_FOUND)) {
   214	            APP_ERROR_CHECK(err_code);
   215	        }
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/nested_util.c:19:#define MEM_CHUNK               10000
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/crypto1.py:51:ref: https://web.archive.org/web/20081010065744/http://sar.informatik.hu-berlin.de/research/publications/SAR-PR-2008-21/SAR-PR-2008-21_.pdf
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util.h:29:# define FILE_PATH_SIZE 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_com.py:17:# each thread is waiting for its data for 100 ms before looping again
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_com.py:160:        return (0x100 - ret) & 0xFF
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.h:26:# define sleep(n) Sleep(1000 *(n))
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:6:    GET_APP_VERSION = 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:7:    CHANGE_DEVICE_MODE = 1001
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:8:    GET_DEVICE_MODE = 1002
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:9:    SET_ACTIVE_SLOT = 1003
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:10:    SET_SLOT_TAG_TYPE = 1004
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:11:    SET_SLOT_DATA_DEFAULT = 1005
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:12:    SET_SLOT_ENABLE = 1006
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:14:    SET_SLOT_TAG_NICK = 1007
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:15:    GET_SLOT_TAG_NICK = 1008
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:18:    SLOT_DATA_CONFIG_SAVE = 1009
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:21:    GET_DEVICE_CHIP_ID = 1011
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:22:    GET_DEVICE_ADDRESS = 1012
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:32:    GET_SLOT_INFO = 1019
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:49:    SET_BLE_PAIRING_KEY = 1030
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:53:    GET_DEVICE_MODEL = 1033
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:55:    GET_DEVICE_SETTINGS = 1034
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:56:    GET_DEVICE_CAPABILITIES = 1035
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:300:    # ASK Tag-Talk-First      100
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:302:    EM410X = 100
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:340:    # MIFARE Classic series  1000
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:341:    MIFARE_Mini = 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:342:    MIFARE_1024 = 1001
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:343:    MIFARE_2048 = 1002
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:344:    MIFARE_4096 = 1003
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:346:    # MFUL / NTAG series     1100
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:347:    NTAG_213 = 1100
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:625:    H10301 = 1
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:640:    H10306 = 16
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:641:    N10002 = 17
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:651:    H10302 = 26
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:652:    H10304 = 27
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:653:    P10004 = 28
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:659:            HIDFormat.H10301: "HID H10301 26-bit",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:674:            HIDFormat.H10306: "HID H10306 34-bit",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:675:            HIDFormat.N10002: "Honeywell/Northern N10002 34-bit",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:679:            HIDFormat.C1K35S: "HID Corporate 1000 35-bit Std",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:685:            HIDFormat.H10302: "HID H10302 37-bit huge ID",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:686:            HIDFormat.H10304: "HID H10304 37-bit",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py:687:            HIDFormat.P10004: "HID P10004 37-bit PCSC",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:38:    timeout.tv_sec = n / 1000000000;
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:39:    timeout.tv_nsec = n % 1000000000;
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:44:    nsleep(1000000 * (uint64_t)n);
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:74:        t->tv_nsec = now.tv_usec * 1000;
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:93:        t->tv_sec = now / 1000000000;
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:94:        t->tv_nsec = now % 1000000000;
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:132:    return (count.QuadPart * 1000) / frequency.QuadPart;
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util_posix.c:136:    return (1000 * (uint64_t)t.tv_sec + t.tv_nsec / 1000000);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:283:#define PM_RA_PROTECTION_REWARD_PERIOD 10000
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:1664:// <100663296=> 32MHz/42
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:2243:// <100663296=> 32MHz/42
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:2562:// <134217728=> 1000k
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:2909:#define NRFX_PWM_DEFAULT_CONFIG_TOP_VALUE 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4097:// <26738688=> 100k
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4349:// <26738688=> 100k
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4479:// <268435456=> 1000000 baud
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4591:// <10309632=> 38400 baud
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4600:// <268435456=> 1000000 baud
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4842:// <134217728=> 1000k
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:4977:#define PWM_DEFAULT_CONFIG_TOP_VALUE 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:5831:// <26738688=> 100k
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:5954:// <268435456=> 1000000 baud
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:6464:#define APP_USBD_CONFIG_MAX_POWER 100
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:6917:// <268435456=> 1000000 baud
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:7226:#define NRF_CSENSE_MAX_VALUE 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:7671:// <q> NRF_CLI_VT100_COLORS_ENABLED  - CLI VT100 colors.
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:7674:#ifndef NRF_CLI_VT100_COLORS_ENABLED
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:7675:#define NRF_CLI_VT100_COLORS_ENABLED 1
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/sdk_config.h:11959:// <3=> NRF_CLOCK_LF_ACCURACY_100_PPM
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util.c:42:    // Calculate milliseconds: (ticks * 1000) / ticks_per_second
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util.c:43:    // We multiply by 1000 first to maintain precision before dividing.
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/util.c:45:    return (uint64_t)((counter.QuadPart * 1000) / frequency.QuadPart);
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/helpers/mifare_classic/dump_analyzer.dart:350:        value = value - 0x100000000;
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/helpers/font.dart:133:          FontVariation('wght', (fontWeight!.index + 1) * 100),
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/tests/test_jablotron_modulator.py:219:    "000103070F",   # synthetic odd zero_count, bit-pattern varies across bytes
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/emojis.h:172:    {":100:", "\xf0\x9f\x92\xaf"}, // 💯
/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/HardnestedRecovery/pm3/emojis.h:969:    {":clock1030:", "\xf0\x9f\x95\xa5"}, // 🕥
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:7://                  Range from 1000 -> 1999
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:10:#define DATA_CMD_GET_APP_VERSION                (1000)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:11:#define DATA_CMD_CHANGE_DEVICE_MODE             (1001)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:12:#define DATA_CMD_GET_DEVICE_MODE                (1002)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:13:#define DATA_CMD_SET_ACTIVE_SLOT                (1003)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:14:#define DATA_CMD_SET_SLOT_TAG_TYPE              (1004)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:15:#define DATA_CMD_SET_SLOT_DATA_DEFAULT          (1005)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:16:#define DATA_CMD_SET_SLOT_ENABLE                (1006)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:17:#define DATA_CMD_SET_SLOT_TAG_NICK              (1007)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:18:#define DATA_CMD_GET_SLOT_TAG_NICK              (1008)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:19:#define DATA_CMD_SLOT_DATA_CONFIG_SAVE          (1009)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:21:#define DATA_CMD_GET_DEVICE_CHIP_ID             (1011)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:22:#define DATA_CMD_GET_DEVICE_ADDRESS             (1012)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:29:#define DATA_CMD_GET_SLOT_INFO                  (1019)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:40:#define DATA_CMD_SET_BLE_PAIRING_KEY            (1030)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:43:#define DATA_CMD_GET_DEVICE_MODEL               (1033)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:44:#define DATA_CMD_GET_DEVICE_SETTINGS            (1034)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:45:#define DATA_CMD_GET_DEVICE_CAPABILITIES        (1035)
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/data_cmd.h:80:#define DATA_CMD_HF14A_SET_FIELD_ON             (2100)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:48:        resp = self.device.send_cmd_sync(Command.GET_DEVICE_CHIP_ID)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:58:        resp = self.device.send_cmd_sync(Command.GET_DEVICE_ADDRESS)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:72:        resp = self.device.send_cmd_sync(Command.GET_DEVICE_MODE)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:336:    def hf14a_raw(self, options, resp_timeout_ms=100, data=[], bitlen=None):
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:376:        resp = self.device.send_cmd_sync(Command.HF14A_RAW, data, timeout=(resp_timeout_ms // 1000) + 1)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:538:        timeout_s = (timeout_ms // 1000) + 5
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:572:        return self.device.send_cmd_sync(Command.HF14A_AUTH_TRACE, payload, timeout=(timeout_ms // 1000) + 3)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:711:        :param timeout_ms: Capture duration in ms (1-10000, default 2000)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:714:        timeout_ms = max(1, min(10000, timeout_ms))
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:716:        timeout_s = (timeout_ms // 1000) + 2
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1664:            resp = self.device.send_cmd_sync(Command.GET_DEVICE_CAPABILITIES)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1667:            return chameleon_com.Response(cmd=Command.GET_DEVICE_CAPABILITIES,
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1682:        resp = self.device.send_cmd_sync(Command.GET_DEVICE_MODEL)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1702:        resp = self.device.send_cmd_sync(Command.GET_DEVICE_SETTINGS)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1855:        resp = cml.hf14a_raw(options=options, resp_timeout_ms=1000, data=[0x40], bitlen=7)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1860:            resp = cml.hf14a_raw(options=options, resp_timeout_ms=1000, data=[0x43])
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py:1873:                    resp = cml.hf14a_raw(options=options, resp_timeout_ms=100, data=cmd_read_gen1a_block)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:190:                    return round((timeit.default_timer() - self.time_start) * 1000, 2)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:546:            HIDFormat.H10301: [0xFF, 0xFFFF, 0, 0],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:561:            HIDFormat.H10306: [0xFFFF, 0xFFFF, 0, 0],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:562:            HIDFormat.N10002: [0xFFFF, 0xFFFF, 0, 0],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:572:            HIDFormat.H10302: [0, 0x7FFFFFFFF, 0, 0],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:573:            HIDFormat.H10304: [0xFFFF, 0x7FFFF, 0, 0],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:574:            HIDFormat.P10004: [0x1FFF, 0x3FFFF, 0, 0],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:600:            format = HIDFormat.H10301.value
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:1392:            msg = f"   [ Time elapsed {process.get_time_distance()/1000:#.1f}s ]\r"
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:2829:                f' - progress of checking keys... {color_string((CY, i))} / {len(keys)} ({color_string((CY, f"{100 * i / len(keys):.1f}"))} %)'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4720:                    options=options, resp_timeout_ms=100, data=struct.pack("!B", 0x60)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4733:                            resp_timeout_ms=100,
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:4780:                        resp_timeout_ms=100,
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5144:            default=1000,
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5145:            help="Number of challenges to collect (default: 1000)",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5240:        # Collect 100 challenges to check for collision
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5242:        challenges_100 = set()
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5254:                if hex_challenge in challenges_100:
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5256:                    challenges["challenge_100"] = hex_challenge
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5259:                    challenges_100.add(hex_challenge)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5350:            2: challenges["challenge_100"],
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:5894:            format = HIDFormat.H10301
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6038:        card_id = card_id * 100 + ((b >> 4) * 10) + (b & 0x0F)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6308:      hid      -f <format> --cn <n>  e.g. -f H10301 --fc 10 --cn 1234
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:6350:            help="HID Prox format, e.g. H10301 (required for hid type)",
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7561:            default=100,
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7659:            help='Capture duration in milliseconds (default: 2000, max: 10000, firmware blocks for full duration)'
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7672:        timeout = max(1, min(10000, args.timeout))
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7685:        duration_ms = n * 8 / 1000
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:7923:  hf 14a auth-trace --blk 0 -k FFFFFFFFFFFF -t 10000   # wait up to 10s for tag
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8120:        0x6100: 'Response bytes available',
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8362:        bytes.fromhex('a0000000181002'): 'Mastercard Debit',
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8558:        major = amount // 100
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:8559:        minor = amount % 100
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9037:        rf_dividers = [8, 16, 32, 40, 50, 64, 100, 128]
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9617:                           'a511bf0c0e610c4f07a000000004101087010190 00'.replace(' ', '')),
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9622:                           '4d6173746572436172648701019f38009000'),
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9783:        timeout_ms = max(1000, min(60000, args.timeout))
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:9793:            deadline = time.monotonic() + (timeout_ms / 1000.0)
/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py:10407:            for i in range(0x10000):
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/rgb_marquee.c:16:#define PWM_MAX 1000 // PWM Maximum
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/rgb_marquee.c:165:        if (!(NO_TIMEOUT_1MS(timer, 100))) {

exec
/bin/zsh -lc "rg -n \"void data_frame_receive|data_frame_receive\\(\" /Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src -g '*.[ch]'; rg -n \"data_frame_receive\" /Users/tuan/Claude/chameleon/ChameleonUltra/firmware -g '*.[ch]' | head -n 40; rg -n \"writeCharacteristicWithResponse|maximumWriteValueLength|withoutResponse\" /Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib -g '*.dart'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:187:        data_frame_receive((uint8_t *)(p_evt->params.rx_data.p_data), p_evt->params.rx_data.length);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/usb_main.c:73:            data_frame_receive(cdc_data_buffer, size);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/utils/dataframe.h:16:void data_frame_receive(uint8_t *data, uint16_t length);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/utils/dataframe.c:94:void data_frame_receive(uint8_t *data, uint16_t length) {
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/app_main.c:1027:    on_data_frame_complete(on_data_frame_received);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/app_cmd.h:17:void on_data_frame_received(uint16_t cmd, uint16_t status, uint16_t length, uint8_t *data);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/app_cmd.c:3170:void on_data_frame_received(uint16_t cmd, uint16_t status, uint16_t length, uint8_t *data) {
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/usb_main.c:73:            data_frame_receive(cdc_data_buffer, size);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c:187:        data_frame_receive((uint8_t *)(p_evt->params.rx_data.p_data), p_evt->params.rx_data.length);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/utils/dataframe.h:16:void data_frame_receive(uint8_t *data, uint16_t length);
/Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/utils/dataframe.c:94:void data_frame_receive(uint8_t *data, uint16_t length) {
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart:224:            await flutterReactiveBle.writeCharacteristicWithResponse(
/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/connector/serial_ble.dart:301:          .writeCharacteristicWithResponse(rxCharacteristic!, value: command);

exec
/bin/zsh -lc "nl -ba /Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/utils/dataframe.c | sed -n '1,180p'; nl -ba /Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c | sed -n '190,235p'; nl -ba /Users/tuan/Claude/chameleon/ChameleonUltra/firmware/application/src/ble_main.c | sed -n '440,495p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	#include "dataframe.h"
     2	#include "netdata.h"
     3	
     4	#define NRF_LOG_MODULE_NAME data_frame
     5	#include "nrf_log.h"
     6	#include "nrf_log_ctrl.h"
     7	#include "nrf_log_default_backends.h"
     8	NRF_LOG_MODULE_REGISTER();
     9	
    10	static netdata_frame_raw_t m_netdata_frame_rx_buf;
    11	static netdata_frame_raw_t m_netdata_frame_tx_buf;
    12	static data_frame_tx_t m_frame_tx_buf_info = {
    13	    .buffer = (uint8_t *) &m_netdata_frame_tx_buf,  // default buffer
    14	};
    15	static uint16_t m_data_rx_position = 0;
    16	static uint16_t m_data_cmd;
    17	static uint16_t m_data_status;
    18	static uint16_t m_data_len;
    19	static uint8_t *m_data_buffer;
    20	static volatile bool m_data_completed = false;
    21	static data_frame_cbk_t m_frame_process_cbk = NULL;
    22	
    23	static uint8_t compute_lrc(uint8_t *buf, uint16_t bufsize) {
    24	    uint8_t lrc = 0x00;
    25	    for (uint16_t i = 0; i < bufsize; i++) {
    26	        lrc += buf[i];
    27	    }
    28	    return 0x100 - lrc;
    29	}
    30	
    31	//
    32	//  !!!!!!!!!!!!!!!!! NRF_LOG_HEXDUMP_INFO() printing long data can cause freezing and needs to be fixed. !!!!!!!!!!!!!!!!!
    33	//  FIXME.
    34	//
    35	
    36	/**
    37	 * @brief: create a packet, put the created data packet into the buffer, and wait for the post to set up a non busy state
    38	 * @param cmd: instructionResponse
    39	 * @param status:responseStatus
    40	 * @param length: answerDataLength
    41	 * @param data: answerData
    42	 */
    43	data_frame_tx_t *data_frame_make(uint16_t cmd, uint16_t status, uint16_t data_length, uint8_t *data) {
    44	    if (data_length > 0 && data == NULL) {
    45	        NRF_LOG_ERROR("data_frame_make error, null pointer.");
    46	        return NULL;
    47	    }
    48	    if (data_length > 4096) {
    49	        NRF_LOG_ERROR("data_frame_make error, too much data.");
    50	        return NULL;
    51	    }
    52	
    53	    // NRF_LOG_INFO("TX Data frame: cmd = 0x%04x (%i), status = 0x%04x, length = %d%s", cmd, cmd, status, data_length, data_length > 0 ? ", data =" : "");
    54	    // if (data_length > 0) {
    55	    //     NRF_LOG_HEXDUMP_INFO(data, data_length);
    56	    // }
    57	
    58	    netdata_frame_postamble_t *tx_post = (netdata_frame_postamble_t *)((uint8_t *)&m_netdata_frame_tx_buf + sizeof(netdata_frame_preamble_t) + data_length);
    59	    // sof
    60	    m_netdata_frame_tx_buf.pre.sof = NETDATA_FRAME_SOF;
    61	    // sof lrc
    62	    m_netdata_frame_tx_buf.pre.lrc1 = compute_lrc((uint8_t *)&m_netdata_frame_tx_buf.pre, offsetof(netdata_frame_preamble_t, lrc1));
    63	    // cmd
    64	    m_netdata_frame_tx_buf.pre.cmd = U16HTONS(cmd);
    65	    // status
    66	    m_netdata_frame_tx_buf.pre.status = U16HTONS(status);
    67	    // data_length
    68	    m_netdata_frame_tx_buf.pre.len = U16HTONS(data_length);
    69	    // head lrc
    70	    m_netdata_frame_tx_buf.pre.lrc2 = compute_lrc((uint8_t *)&m_netdata_frame_tx_buf.pre, offsetof(netdata_frame_preamble_t, lrc2));
    71	    // data
    72	    if (data_length > 0) {
    73	        memcpy(&m_netdata_frame_tx_buf.data, data, data_length);
    74	    }
    75	    // length out.
    76	    m_frame_tx_buf_info.length = (sizeof(netdata_frame_preamble_t) + data_length + sizeof(netdata_frame_postamble_t));
    77	    // data all lrc
    78	    tx_post->lrc3 = compute_lrc((uint8_t *)&m_netdata_frame_tx_buf.data, data_length);
    79	    return (&m_frame_tx_buf_info);
    80	}
    81	
    82	/**
    83	 * @brief Data frame reset
    84	 */
    85	void data_frame_reset(void) {
    86	    m_data_rx_position = 0;
    87	}
    88	
    89	/**
    90	 * @brief Package receiving, which is used to receive the sent from the data packet and perform splicing processing
    91	 * @param data: Receive byte array
    92	 * @param length:The length of the receiving byte array
    93	 */
    94	void data_frame_receive(uint8_t *data, uint16_t length) {
    95	    // buffer wait process
    96	    if (m_data_completed) {
    97	        NRF_LOG_ERROR("Data frame wait process.");
    98	        return;
    99	    }
   100	    // buffer overflow
   101	    if (m_data_rx_position + length > sizeof(m_netdata_frame_rx_buf)) {
   102	        NRF_LOG_ERROR("Data frame wait overflow.");
   103	        data_frame_reset();
   104	        return;
   105	    }
   106	    // frame process
   107	    for (int i = 0; i < length; i++) {
   108	        // copy to buffer
   109	        ((uint8_t *)(&m_netdata_frame_rx_buf))[m_data_rx_position] = data[i];
   110	        if (m_data_rx_position == offsetof(netdata_frame_preamble_t, sof)) {
   111	            if (m_netdata_frame_rx_buf.pre.sof != NETDATA_FRAME_SOF) {
   112	                // not sof byte
   113	                NRF_LOG_ERROR("Data frame no sof byte.");
   114	                data_frame_reset();
   115	                return;
   116	            }
   117	        } else if (m_data_rx_position == offsetof(netdata_frame_preamble_t, lrc1)) {
   118	            if (m_netdata_frame_rx_buf.pre.lrc1 != compute_lrc((uint8_t *)&m_netdata_frame_rx_buf.pre, offsetof(netdata_frame_preamble_t, lrc1))) {
   119	                // not sof lrc byte
   120	                NRF_LOG_ERROR("Data frame sof lrc error.");
   121	                data_frame_reset();
   122	                return;
   123	            }
   124	        } else if (m_data_rx_position == offsetof(netdata_frame_preamble_t, lrc2)) {  // frame head lrc
   125	            if (m_netdata_frame_rx_buf.pre.lrc2 != compute_lrc((uint8_t *)&m_netdata_frame_rx_buf.pre, offsetof(netdata_frame_preamble_t, lrc2))) {
   126	                // frame head lrc error
   127	                NRF_LOG_ERROR("Data frame head lrc error.");
   128	                data_frame_reset();
   129	                return;
   130	            }
   131	            // frame head complete, cache info
   132	            m_data_cmd = U16NTOHS(m_netdata_frame_rx_buf.pre.cmd);
   133	            m_data_status = U16NTOHS(m_netdata_frame_rx_buf.pre.status);
   134	            m_data_len = U16NTOHS(m_netdata_frame_rx_buf.pre.len);
   135	            NRF_LOG_INFO("Data frame data length %d.", m_data_len);
   136	            // check data length
   137	            if (m_data_len > NETDATA_MAX_DATA_LENGTH) {
   138	                NRF_LOG_ERROR("Data frame data length larger than max.");
   139	                data_frame_reset();
   140	                return;
   141	            }
   142	        } else if (m_data_rx_position >= offsetof(netdata_frame_raw_t, data)) {   // frame data
   143	            // check all data ready.
   144	            if (m_data_rx_position == (sizeof(netdata_frame_preamble_t) + m_data_len)) {
   145	                netdata_frame_postamble_t *rx_post = (netdata_frame_postamble_t *)((uint8_t *)&m_netdata_frame_rx_buf + sizeof(netdata_frame_preamble_t) + m_data_len);
   146	                if (rx_post->lrc3 == compute_lrc((uint8_t *)&m_netdata_frame_rx_buf.data, m_data_len)) {
   147	                    // ok, lrc for data is check success.
   148	                    // and we are receive completed
   149	                    m_data_buffer = m_data_len > 0 ? (uint8_t *)&m_netdata_frame_rx_buf.data : NULL;
   150	                    m_data_completed = true;
   151	                    // NRF_LOG_INFO("RX Data frame: cmd = 0x%04x (%i), status = 0x%04x, length = %d%s", m_data_cmd, m_data_cmd, m_data_status, m_data_len, m_data_len > 0 ? ", data =" : "");
   152	                    // if (m_data_len > 0) {
   153	                    //     NRF_LOG_HEXDUMP_INFO(m_data_buffer, m_data_len);
   154	                    // }
   155	                } else {
   156	                    // data frame lrc error
   157	                    NRF_LOG_ERROR("Data frame finally lrc error.");
   158	                    data_frame_reset();
   159	                }
   160	                return;
   161	            }
   162	        }
   163	        // index update
   164	        m_data_rx_position++;
   165	    }
   166	}
   167	
   168	/**
   169	 * @brief After the data packet processing, when the received data forms a complete frame,
   170	 *         This function will be distributed processing tasks through this function, which will be adjusted to notify the data processing of the data
   171	 * If the data processing is time -consuming operation, you need to put this function in the main loop to call
   172	 */
   173	void data_frame_process(void) {
   174	    // check if data frame
   175	    if (m_data_completed) {
   176	        // to process data frame
   177	        if (m_frame_process_cbk != NULL) {
   178	            m_frame_process_cbk(m_data_cmd, m_data_status, m_data_len, m_data_buffer);
   179	        }
   180	        // reset after process data frame.
   190	/**@snippet [Handling the data received over BLE] */
   191	
   192	void nus_data_response(uint8_t *p_data, uint16_t length) {
   193	    NRF_LOG_INFO("BLE nus service response data length: %d", length);
   194	    NRF_LOG_HEXDUMP_DEBUG(p_data, length);
   195	
   196	    ret_code_t err_code;
   197	    uint16_t remain = length;
   198	    uint16_t count = 0;
   199	    do {
   200	        remain = MIN(m_ble_nus_max_data_len, remain);
   201	        err_code = ble_nus_data_send(&m_nus, p_data + count, &remain, m_conn_handle);
   202	        // NRF_LOG_INFO("Data send length(amount): %d", remain);
   203	        if (err_code == NRF_SUCCESS) {
   204	            count += remain;
   205	            remain = length - count;
   206	        }
   207	        // NRF_LOG_INFO("Data send length(count): %d", count);
   208	        if (err_code == NRF_ERROR_BUSY) {
   209	            continue;
   210	        }
   211	        if ((err_code != NRF_ERROR_INVALID_STATE) &&
   212	                (err_code != NRF_ERROR_RESOURCES) &&
   213	                (err_code != NRF_ERROR_NOT_FOUND)) {
   214	            APP_ERROR_CHECK(err_code);
   215	        }
   216	
   217	    } while (count != length && g_is_ble_connected);
   218	}
   219	
   220	bool is_nus_working(void) {
   221	    return g_is_ble_connected;
   222	}
   223	
   224	/**@brief Function for handling Queued Write Module errors.
   225	 *
   226	 * @details A pointer to this function will be passed to each service which may need to inform the
   227	 *          application about an error.
   228	 *
   229	 * @param[in]   nrf_error   Error code containing information about what went wrong.
   230	 */
   231	static void nrf_qwr_error_handler(uint32_t nrf_error) {
   232	    APP_ERROR_HANDLER(nrf_error);
   233	}
   234	
   235	__INLINE uint32_t map(uint32_t x, uint32_t in_min, uint32_t in_max, uint32_t out_min, uint32_t out_max) {
   440	            // Pairing not supported? No, is supported now, hahahaha...
   441	            // But... the pairing is enable?
   442	            if (settings_get_ble_pairing_enable_first_load()) {
   443	                NRF_LOG_DEBUG("Pairing is enable, The BLE_GAP_EVT_SEC_PARAMS_REQUEST event is handled by the pairing manager.");
   444	            } else {
   445	                err_code = sd_ble_gap_sec_params_reply(m_conn_handle, BLE_GAP_SEC_STATUS_PAIRING_NOT_SUPP, NULL, NULL);
   446	                APP_ERROR_CHECK(err_code);
   447	            }
   448	            break;
   449	
   450	        case BLE_GAP_EVT_PASSKEY_DISPLAY: {
   451	            char passkey[BLE_GAP_PASSKEY_LEN + 1];
   452	            memcpy(passkey, p_ble_evt->evt.gap_evt.params.passkey_display.passkey, BLE_GAP_PASSKEY_LEN);
   453	            passkey[BLE_GAP_PASSKEY_LEN] = 0x00;
   454	            NRF_LOG_INFO("=== PASSKEY: %s =====",   nrf_log_push(passkey));
   455	        }
   456	        break;
   457	
   458	        case BLE_GATTS_EVT_SYS_ATTR_MISSING:
   459	            // No system attributes have been stored.
   460	            err_code = sd_ble_gatts_sys_attr_set(m_conn_handle, NULL, 0, 0);
   461	            APP_ERROR_CHECK(err_code);
   462	            break;
   463	
   464	        case BLE_GATTC_EVT_TIMEOUT:
   465	            // Disconnect on GATT Client timeout event.
   466	            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gattc_evt.conn_handle,
   467	                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
   468	            APP_ERROR_CHECK(err_code);
   469	            break;
   470	
   471	        case BLE_GATTS_EVT_TIMEOUT:
   472	            // Disconnect on GATT Server timeout event.
   473	            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gatts_evt.conn_handle,
   474	                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
   475	            APP_ERROR_CHECK(err_code);
   476	            break;
   477	
   478	        default:
   479	            // No implementation needed.
   480	            UNUSED_VARIABLE(err_code);
   481	            break;
   482	    }
   483	}
   484	
   485	
   486	/**@brief Function for the SoftDevice initialization.
   487	 *
   488	 * @details This function initializes the SoftDevice and the BLE event interrupt.
   489	 */
   490	static void ble_stack_init(void) {
   491	    ret_code_t err_code;
   492	
   493	    err_code = nrf_sdh_enable_request();
   494	    APP_ERROR_CHECK(err_code);
   495	

codex
The transport reuse is structurally valid, but the bridge currently has a real loss mechanism: it floods `.withoutResponse` writes without honoring CoreBluetooth readiness. I’m checking the remaining state races and whether the handshake is actually acknowledged before the daemon is admitted.
exec
/bin/zsh -lc "rg -n -A18 -B8 \"canSendWriteWithoutResponse|peripheralIsReadyToSendWriteWithoutResponse|maximumWriteValueLength\" /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers 2>/dev/null; rg -n -A12 -B5 \"func send\\(|contentProcessed|finalMessage\" /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules 2>/dev/null | head -n 160; rg -n \"MACOSX_DEPLOYMENT_TARGET|deploymentTarget|SWIFT_VERSION|strict-concurrency|entitlements|CODE_SIGN_ENTITLEMENTS|GENERATE_INFOPLIST\" app/project.yml app -g '*.yml' -g '*.sh' -g '*.xcconfig'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-87-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-88- *  @property services
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-89- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-90- *  @discussion A list of <code>CBService</code> objects that have been discovered on the peripheral.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-91- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-92-@property(retain, readonly, nullable) NSArray<CBService *> *services;
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-93-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-94-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:95: *  @property canSendWriteWithoutResponse
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-96- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-97- *  @discussion YES if the remote device has space to send a write without response. If this value is NO,
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-98- *				the value will be set to YES after the current writes have been flushed, and
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:99: *				<link>peripheralIsReadyToSendWriteWithoutResponse:</link> will be called.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-100- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:101:@property(readonly) BOOL canSendWriteWithoutResponse NS_AVAILABLE(10_13, 11_0);
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-102-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-103-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-104- *  @property ancsAuthorized
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-105- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-106- *  @discussion YES if the remote device has been authorized to receive data over ANCS (Apple Notification Service Center) protocol.  If this value is NO,
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-107- *                the value will be set to YES after a user authorization occurs and
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-108- *                <link>didUpdateANCSAuthorizationForPeripheral:</link> will be called.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-109- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-110-@property(readonly) BOOL ancsAuthorized NS_AVAILABLE_IOS(13_0);
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-111-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-112-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-113- *  @method readRSSI
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-114- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-115- *  @discussion While connected, retrieves the current RSSI of the link.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-116- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-117- *  @see        peripheral:didReadRSSI:error:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-118- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-119-- (void)readRSSI;
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-163- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-164- *  @discussion				Reads the characteristic value for <i>characteristic</i>.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-165- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-166- *  @see					peripheral:didUpdateValueForCharacteristic:error:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-167- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-168-- (void)readValueForCharacteristic:(CBCharacteristic *)characteristic;
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-169-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-170-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:171: *  @method		maximumWriteValueLengthForType:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-172- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-173- *  @discussion	The maximum amount of data, in bytes, that can be sent to a characteristic in a single write type.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-174- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-175- *  @see		writeValue:forCharacteristic:type:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-176- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:177:- (NSUInteger)maximumWriteValueLengthForType:(CBCharacteristicWriteType)type NS_AVAILABLE(10_12, 9_0);
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-178-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-179-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-180- *  @method writeValue:forCharacteristic:type:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-181- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-182- *  @param data				The value to write.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-183- *  @param characteristic	The characteristic whose characteristic value will be written.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-184- *  @param type				The type of write to be executed.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-185- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-186- *  @discussion				Writes <i>value</i> to <i>characteristic</i>'s characteristic value.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-187- *							If the <code>CBCharacteristicWriteWithResponse</code> type is specified, {@link peripheral:didWriteValueForCharacteristic:error:}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-188- *							is called with the result of the write request.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:189: *							If the <code>CBCharacteristicWriteWithoutResponse</code> type is specified, and canSendWriteWithoutResponse is false, the delivery
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-190- * 							of the data is best-effort and may not be guaranteed.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-191- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-192- *  @see					peripheral:didWriteValueForCharacteristic:error:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:193: *  @see					peripheralIsReadyToSendWriteWithoutResponse:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:194: *	@see					canSendWriteWithoutResponse
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-195- *	@see					CBCharacteristicWriteType
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-196- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-197-- (void)writeValue:(NSData *)data forCharacteristic:(CBCharacteristic *)characteristic type:(CBCharacteristicWriteType)type;
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-198-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-199-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-200- *  @method setNotifyValue:forCharacteristic:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-201- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-202- *  @param enabled			Whether or not notifications/indications should be enabled.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-203- *  @param characteristic	The characteristic containing the client characteristic configuration descriptor.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-204- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-205- *  @discussion				Enables or disables notifications/indications for the characteristic value of <i>characteristic</i>. If <i>characteristic</i>
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-206- *							allows both, notifications will be used.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-207- *                          When notifications/indications are enabled, updates to the characteristic value will be received via delegate method 
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-208- *                          @link peripheral:didUpdateValueForCharacteristic:error: @/link. Since it is the peripheral that chooses when to send an update,
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-209- *                          the application should be prepared to handle them as long as notifications/indications remain enabled.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-210- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-211- *  @see					peripheral:didUpdateNotificationStateForCharacteristic:error:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-212- *  @seealso                CBConnectPeripheralOptionNotifyOnNotificationKey
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-415- *  @param descriptor		A <code>CBDescriptor</code> object.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-416- *	@param error			If an error occurred, the cause of the failure.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-417- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-418- *  @discussion				This method returns the result of a @link writeValue:forDescriptor: @/link call.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-419- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-420-- (void)peripheral:(CBPeripheral *)peripheral didWriteValueForDescriptor:(CBDescriptor *)descriptor error:(nullable NSError *)error;
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-421-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-422-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:423: *  @method peripheralIsReadyToSendWriteWithoutResponse:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-424- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-425- *  @param peripheral   The peripheral providing this update.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-426- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-427- *  @discussion         This method is invoked after a failed call to @link writeValue:forCharacteristic:type: @/link, when <i>peripheral</i> is again
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-428- *                      ready to send characteristic value updates.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-429- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-430- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h:431:- (void)peripheralIsReadyToSendWriteWithoutResponse:(CBPeripheral *)peripheral;
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-432-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-433-/*!
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-434- *  @method peripheral:didOpenL2CAPChannel:error:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-435- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-436- *  @param peripheral		The peripheral providing this information.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-437- *  @param channel			A <code>CBL2CAPChannel</code> object.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-438- *	@param error			If an error occurred, the cause of the failure.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-439- *
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-440- *  @discussion				This method returns the result of a @link openL2CAPChannel: @link call.
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-441- */
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-442-- (void)peripheral:(CBPeripheral *)peripheral didOpenL2CAPChannel:(nullable CBL2CAPChannel *)channel error:(nullable NSError *)error;
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-443-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-444-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-445-@end
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-446-
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h-447-NS_ASSUME_NONNULL_END
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-970-    public func hash(into hasher: inout Swift.Hasher)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-971-    public var hashValue: Swift.Int {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-972-      get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-973-    }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-974-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:975:  @preconcurrency final public func send(content: Foundation.Data, to remoteAddress: Network.NWEthernetChannel.EthernetAddress, vlanTag: Swift.UInt16, completion: @escaping @Sendable (_ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-976-  @preconcurrency final public var receiveHandler: (@Sendable (_ content: Foundation.Data, _ vlanTag: Swift.UInt16, _ localAddress: Network.NWEthernetChannel.EthernetAddress, _ remoteAddress: Network.NWEthernetChannel.EthernetAddress) -> Swift.Void)? {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-977-    get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-978-    set
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-979-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-980-  @objc deinit
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-981-}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-982-@available(macOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-983-@available(iOS, unavailable)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-984-@available(tvOS, unavailable)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-985-@available(watchOS, unavailable)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-986-extension Network.NWEthernetChannel : Swift.Sendable {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-987-}
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1878-  public func send<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1879-  public func receive() async throws -> ApplicationProtocol.Message<Foundation.Data>
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1880-}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1881-@available(macOS 26.0, iOS 26.0, watchOS 26.0, tvOS 26.0, visionOS 26.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1882-extension Network.NetworkChannel where ApplicationProtocol == Network.WebSocket {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:1883:  public func send(_ content: Swift.String, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1884-  public func sendIdempotent(_ content: Swift.String, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]})
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1885-  public func send<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1886-  public func sendIdempotent<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1887-  public func startSend(_ content: Swift.String, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}, handler: (_ send: (_ content: Swift.String, _ isComplete: Swift.Bool) async throws -> Swift.Void) async throws -> Swift.Void) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1888-  public func startSend<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}, handler: (_ send: (_ content: Content, _ isComplete: Swift.Bool) async throws -> Swift.Void) async throws -> Swift.Void) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1889-  public func ping<Content>(_ content: Content? = nil, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1890-  public func pong<Content>(_ content: Content? = nil, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1891-  public func close(code: Network.NWProtocolWebSocket.CloseCode = .protocolCode(.normalClosure), reason: Swift.String? = nil, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1892-  public func receive() async throws -> ApplicationProtocol.Message<Foundation.Data>
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1893-  public func startReceive(_ handler: (_ receive: (_ atLeast: Swift.Int, _ atMost: Swift.Int) async throws -> ApplicationProtocol.Message<Foundation.Data>) async throws -> Swift.Void) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1894-}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-1895-@available(macOS 26.0, iOS 26.0, watchOS 26.0, tvOS 26.0, visionOS 26.0, *)
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2072-      get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2073-    }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2074-    public func protocolMetadata(definition: Network.NWProtocolDefinition) -> Network.NWProtocolMetadata?
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2075-    public init(identifier: Swift.String, expiration: Swift.UInt64 = 0, priority: Swift.Double = 0.5, isFinal: Swift.Bool = false, antecedent: Network.NWConnection.ContentContext? = nil, metadata: [Network.NWProtocolMetadata]? = [])
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2076-    public static let defaultMessage: Network.NWConnection.ContentContext
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:2077:    public static let finalMessage: Network.NWConnection.ContentContext
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2078-    public static let defaultStream: Network.NWConnection.ContentContext
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2079-    @objc deinit
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2080-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2081-  @preconcurrency final public func receive(minimumIncompleteLength: Swift.Int, maximumLength: Swift.Int, completion: @escaping @Sendable (_ content: Foundation.Data?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2082-  @available(macOS 11.0, iOS 14.0, watchOS 7.0, tvOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2083-  @preconcurrency final public func receiveDiscontiguous(minimumIncompleteLength: Swift.Int, maximumLength: Swift.Int, completion: @escaping @Sendable (_ content: Dispatch.DispatchData?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2084-  @preconcurrency final public func receiveMessage(completion: @escaping @Sendable (_ content: Foundation.Data?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2085-  @available(macOS 11.0, iOS 14.0, watchOS 7.0, tvOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2086-  @preconcurrency final public func receiveMessageDiscontiguous(completion: @escaping @Sendable (_ content: Dispatch.DispatchData?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2087-  public enum SendCompletion {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:2088:    @preconcurrency case contentProcessed(@Sendable (_ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2089-    case idempotent
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2090-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:2091:  @preconcurrency final public func send(content: Foundation.Data?, contentContext: Network.NWConnection.ContentContext = .defaultMessage, isComplete: Swift.Bool = true, completion: Network.NWConnection.SendCompletion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2092-  @available(macOS 10.15, iOS 13.0, watchOS 6.0, tvOS 13.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2093-  @usableFromInline
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:2094:  @preconcurrency final internal func send(discontiguousContent: Dispatch.DispatchData, contentContext: Network.NWConnection.ContentContext = .defaultMessage, isComplete: Swift.Bool = true, completion: Network.NWConnection.SendCompletion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2095-  @available(macOS 10.15, iOS 13.0, watchOS 6.0, tvOS 13.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2096-  @preconcurrency @inlinable final public func send<Content>(content: Content?, contentContext: Network.NWConnection.ContentContext = .defaultMessage, isComplete: Swift.Bool = true, completion: Network.NWConnection.SendCompletion) where Content : Foundation.DataProtocol {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2097-		if let dispatchData = content as? DispatchData {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2098-			send(discontiguousContent: dispatchData, contentContext: contentContext, isComplete: isComplete, completion: completion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2099-		} else if let data = content as? Data {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2100-			send(content: data, contentContext: contentContext, isComplete: isComplete, completion: completion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2101-		} else if let content = content {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2102-			send(content: Data(content), contentContext: contentContext, isComplete: isComplete, completion: completion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2103-		} else {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2104-			send(content: nil, contentContext: contentContext, isComplete: isComplete, completion: completion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2105-		}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2106-	}
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2264-    @available(iOS 17.0, tvOS 17.0, watchOS 10.0, macOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2265-    public init(nw: Network.nw_content_context_t)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2266-    @objc deinit
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2267-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2268-  @preconcurrency final public func setReceiveHandler(maximumMessageSize: Swift.Int = Int.max, rejectOversizedMessages: Swift.Bool = true, handler: (@Sendable (_ message: Network.NWConnectionGroup.Message, _ content: Foundation.Data?, _ isComplete: Swift.Bool) -> Swift.Void)?)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface:2269:  @preconcurrency final public func send(content: Foundation.Data?, to: Network.NWEndpoint? = nil, message: Network.NWConnectionGroup.Message = .default, completion: @escaping (_ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2270-  @available(macOS 12.0, iOS 15.0, watchOS 8.0, tvOS 15.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2271-  @preconcurrency final public var newConnectionHandler: (@Sendable (_ connection: Network.NWConnection) -> Swift.Void)? {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2272-    get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2273-    set
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2274-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2275-  @available(macOS 12.0, iOS 15.0, watchOS 8.0, tvOS 15.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2276-  final public func extract(connectionTo: Network.NWEndpoint? = nil, using: Network.NWProtocolOptions? = nil) -> Network.NWConnection?
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2277-  @available(macOS 12.0, iOS 15.0, watchOS 8.0, tvOS 15.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2278-  final public func reinsert(connection: Network.NWConnection) -> Swift.Bool
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2279-  final public let descriptor: any Network.NWGroupDescriptor
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2280-  final public let parameters: Network.NWParameters
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/x86_64-apple-macos.swiftinterface-2281-  public init(with: any Network.NWGroupDescriptor, using: Network.NWParameters)
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-970-    public func hash(into hasher: inout Swift.Hasher)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-971-    public var hashValue: Swift.Int {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-972-      get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-973-    }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-974-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface:975:  @preconcurrency final public func send(content: Foundation.Data, to remoteAddress: Network.NWEthernetChannel.EthernetAddress, vlanTag: Swift.UInt16, completion: @escaping @Sendable (_ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-976-  @preconcurrency final public var receiveHandler: (@Sendable (_ content: Foundation.Data, _ vlanTag: Swift.UInt16, _ localAddress: Network.NWEthernetChannel.EthernetAddress, _ remoteAddress: Network.NWEthernetChannel.EthernetAddress) -> Swift.Void)? {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-977-    get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-978-    set
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-979-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-980-  @objc deinit
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-981-}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-982-@available(macOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-983-@available(iOS, unavailable)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-984-@available(tvOS, unavailable)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-985-@available(watchOS, unavailable)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-986-extension Network.NWEthernetChannel : Swift.Sendable {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-987-}
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1878-  public func send<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1879-  public func receive() async throws -> ApplicationProtocol.Message<Foundation.Data>
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1880-}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1881-@available(macOS 26.0, iOS 26.0, watchOS 26.0, tvOS 26.0, visionOS 26.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1882-extension Network.NetworkChannel where ApplicationProtocol == Network.WebSocket {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface:1883:  public func send(_ content: Swift.String, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1884-  public func sendIdempotent(_ content: Swift.String, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]})
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1885-  public func send<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1886-  public func sendIdempotent<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1887-  public func startSend(_ content: Swift.String, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}, handler: (_ send: (_ content: Swift.String, _ isComplete: Swift.Bool) async throws -> Swift.Void) async throws -> Swift.Void) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1888-  public func startSend<Content>(_ content: Content, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}, handler: (_ send: (_ content: Content, _ isComplete: Swift.Bool) async throws -> Swift.Void) async throws -> Swift.Void) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1889-  public func ping<Content>(_ content: Content? = nil, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1890-  public func pong<Content>(_ content: Content? = nil, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws where Content : Foundation.DataProtocol
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1891-  public func close(code: Network.NWProtocolWebSocket.CloseCode = .protocolCode(.normalClosure), reason: Swift.String? = nil, @Network.ProtocolMetadataBuilder metadata builder: () -> [Network.NWProtocolMetadata] = {[]}) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1892-  public func receive() async throws -> ApplicationProtocol.Message<Foundation.Data>
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1893-  public func startReceive(_ handler: (_ receive: (_ atLeast: Swift.Int, _ atMost: Swift.Int) async throws -> ApplicationProtocol.Message<Foundation.Data>) async throws -> Swift.Void) async throws
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1894-}
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-1895-@available(macOS 26.0, iOS 26.0, watchOS 26.0, tvOS 26.0, visionOS 26.0, *)
--
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2072-      get
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2073-    }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2074-    public func protocolMetadata(definition: Network.NWProtocolDefinition) -> Network.NWProtocolMetadata?
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2075-    public init(identifier: Swift.String, expiration: Swift.UInt64 = 0, priority: Swift.Double = 0.5, isFinal: Swift.Bool = false, antecedent: Network.NWConnection.ContentContext? = nil, metadata: [Network.NWProtocolMetadata]? = [])
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2076-    public static let defaultMessage: Network.NWConnection.ContentContext
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface:2077:    public static let finalMessage: Network.NWConnection.ContentContext
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2078-    public static let defaultStream: Network.NWConnection.ContentContext
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2079-    @objc deinit
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2080-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2081-  @preconcurrency final public func receive(minimumIncompleteLength: Swift.Int, maximumLength: Swift.Int, completion: @escaping @Sendable (_ content: Foundation.Data?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2082-  @available(macOS 11.0, iOS 14.0, watchOS 7.0, tvOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2083-  @preconcurrency final public func receiveDiscontiguous(minimumIncompleteLength: Swift.Int, maximumLength: Swift.Int, completion: @escaping @Sendable (_ content: Dispatch.DispatchData?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2084-  @preconcurrency final public func receiveMessage(completion: @escaping @Sendable (_ content: Foundation.Data?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2085-  @available(macOS 11.0, iOS 14.0, watchOS 7.0, tvOS 14.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2086-  @preconcurrency final public func receiveMessageDiscontiguous(completion: @escaping @Sendable (_ content: Dispatch.DispatchData?, _ contentContext: Network.NWConnection.ContentContext?, _ isComplete: Swift.Bool, _ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2087-  public enum SendCompletion {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface:2088:    @preconcurrency case contentProcessed(@Sendable (_ error: Network.NWError?) -> Swift.Void)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2089-    case idempotent
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2090-  }
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface:2091:  @preconcurrency final public func send(content: Foundation.Data?, contentContext: Network.NWConnection.ContentContext = .defaultMessage, isComplete: Swift.Bool = true, completion: Network.NWConnection.SendCompletion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2092-  @available(macOS 10.15, iOS 13.0, watchOS 6.0, tvOS 13.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2093-  @usableFromInline
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface:2094:  @preconcurrency final internal func send(discontiguousContent: Dispatch.DispatchData, contentContext: Network.NWConnection.ContentContext = .defaultMessage, isComplete: Swift.Bool = true, completion: Network.NWConnection.SendCompletion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2095-  @available(macOS 10.15, iOS 13.0, watchOS 6.0, tvOS 13.0, *)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2096-  @preconcurrency @inlinable final public func send<Content>(content: Content?, contentContext: Network.NWConnection.ContentContext = .defaultMessage, isComplete: Swift.Bool = true, completion: Network.NWConnection.SendCompletion) where Content : Foundation.DataProtocol {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2097-		if let dispatchData = content as? DispatchData {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2098-			send(discontiguousContent: dispatchData, contentContext: contentContext, isComplete: isComplete, completion: completion)
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2099-		} else if let data = content as? Data {
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/Network.framework/Modules/Network.swiftmodule/arm64e-apple-macos.swiftinterface-2100-			send(content: data, contentContext: contentContext, isComplete: isComplete, completion: completion)
app/project.yml:4:  deploymentTarget:
app/project.yml:9:    SWIFT_VERSION: "5.0"
app/project.yml:24:        GENERATE_INFOPLIST_FILE: "YES"
app/project.yml:4:  deploymentTarget:
app/project.yml:9:    SWIFT_VERSION: "5.0"
app/project.yml:24:        GENERATE_INFOPLIST_FILE: "YES"
app/tools/package.sh:7:# Signing: a Developer ID Application cert (hardened runtime + entitlements) is
app/tools/package.sh:137:# runtime + entitlements + secure timestamp); otherwise ad-hoc (runs on this Mac,
app/tools/package.sh:140:ENT="$HERE/tenorrekey.entitlements"
app/tools/package.sh:142:    echo "    Developer ID: $SIGN_ID  (hardened runtime + entitlements)"
app/tools/package.sh:144:    SIGN_ENT=(codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$SIGN_ID")
app/tools/package.sh:151:# interpreter and the app last (the interpreter + app carry the entitlements; the
app/tools/package.sh:156:# (pure compute, no entitlements needed). Sign them so a hardened/notarizable build

exec
/bin/zsh -lc "tail -n 110 probe/chameleon_d.py | nl -ba; rg -n \"sys.stdout|Daemon\\(\" probe/chameleon_d.py | tail -n 40; rg -n \"except OSError|_drop\\(\\)\" probe/chameleon_d.py | head -n 120; nl -ba app/project.yml | sed -n '1,75p'; nl -ba app/tools/package.sh | sed -n '125,175p'; nl -ba app/tools/tenorrekey.entitlements" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
     1	
     2	    # ---- dispatch ----------------------------------------------------------
     3	
     4	    def handle(self, req):
     5	        rid = req.get("id")
     6	        method = req.get("method")
     7	        if method not in self.METHODS:
     8	            return {"id": rid, "error": "unknown method: %r" % method}
     9	        try:
    10	            return {"id": rid, "result": getattr(self, method)(req.get("params") or {})}
    11	        except _DEAD as e:
    12	            # The port died mid-op: drop the dead handle so the NEXT command
    13	            # re-opens a fresh one (mirrors x7d dropping on OSError).
    14	            self._drop()
    15	            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}
    16	        except Exception as e:
    17	            return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}
    18	
    19	    def run(self, stream=None):
    20	        # A worker thread runs requests one at a time (the device is a single command
    21	        # stream, so ops never overlap) while THIS thread keeps reading stdin. A
    22	        # `cancel` arriving mid-decode is handled inline - off the worker - so it
    23	        # trips the flag the attack loop watches and the shell can abort a long
    24	        # recovery without killing the daemon; every other request is serialized.
    25	        if stream is None:
    26	            stream = sys.stdin
    27	        q = queue.Queue()
    28	
    29	        def worker():
    30	            while True:
    31	                req = q.get()
    32	                if req is None:
    33	                    return
    34	                try:
    35	                    self.emit(self.handle(req))
    36	                finally:
    37	                    # Disarm the flash-pending guard only once the dfu_flash op has fully
    38	                    # returned (a result, a cancel, or an error) - so from dispatch until
    39	                    # completion an EOF/shutdown always joins unbounded.
    40	                    if req.get("method") == "dfu_flash":
    41	                        self._flash_pending.clear()
    42	
    43	        t = threading.Thread(target=worker, daemon=True)
    44	        t.start()
    45	        for line in stream:
    46	            line = line.strip()
    47	            if not line:
    48	                continue
    49	            try:
    50	                req = json.loads(line)
    51	            except ValueError as e:
    52	                self.emit({"error": "bad json: %s" % e})
    53	                continue
    54	            method = req.get("method")
    55	            if method == "cancel":
    56	                self.emit(self.handle(req))
    57	            else:
    58	                # Arm the flash-pending guard at DISPATCH, before the worker can start (and
    59	                # before any EOF could choose the bounded join), closing the race where EOF
    60	                # lands between the last cancel check and _flashing being set.
    61	                if method == "dfu_flash":
    62	                    self._flash_pending.set()
    63	                if method in self.CANCELLABLE:
    64	                    # Arm a fresh cancel window for THIS op at dispatch, before any
    65	                    # later cancel line is read - so a cancel that lands before the
    66	                    # worker starts the op still cancels it, and a stale cancel from a
    67	                    # prior op cannot leak in. The op body never clears the flag.
    68	                    self._cancel.clear()
    69	                q.put(req)
    70	        # EOF: abort any in-flight op and join the worker (bounded). Only drop the
    71	        # handle if the worker actually stopped, so close() cannot race a serial
    72	        # read still in flight on the worker thread (matches x7d's EOF guard). If
    73	        # the worker is still blocked, leave the handle for process-exit cleanup.
    74	        # EXCEPTION: a firmware flash that is DISPATCHED or WRITING is joined UNBOUNDED -
    75	        # abandoning it after 5s and letting the process exit would orphan the flasher and
    76	        # can brick the device, so we wait for it to finish before tearing down.
    77	        self._cancel.set()
    78	        q.put(None)
    79	        if self._flashing.is_set() or self._flash_pending.is_set():
    80	            t.join()
    81	        else:
    82	            t.join(timeout=self.EOF_JOIN_TIMEOUT)
    83	        if not t.is_alive():
    84	            self._drop()
    85	
    86	
    87	if __name__ == "__main__":
    88	    # Protect the protocol channel: the daemon captured the real stdout at
    89	    # construction; redirect sys.stdout to stderr so any stray library print()
    90	    # (the vendored ChameleonCom prints on frame errors + verbose logging) goes
    91	    # to stderr and never interleaves with the newline-JSON on stdout.
    92	    import signal
    93	    _daemon = Daemon()
    94	    sys.stdout = sys.stderr
    95	
    96	    def _guarded_signal(signum, frame):
    97	        # Never die while a firmware flash is dispatched or writing: a SIGTERM/SIGINT then
    98	        # would orphan the flasher and can brick the device. Ignore the signal while a flash
    99	        # is in flight; otherwise exit cleanly (running finally-blocks + EOF teardown).
   100	        if _daemon._flashing.is_set() or _daemon._flash_pending.is_set():
   101	            sys.stderr.write("chameleon_d: signal %d ignored during firmware flash\n" % signum)
   102	            sys.stderr.flush()
   103	            return
   104	        raise SystemExit(0)
   105	    try:
   106	        signal.signal(signal.SIGTERM, _guarded_signal)
   107	        signal.signal(signal.SIGINT, _guarded_signal)
   108	    except (ValueError, OSError):            # not main thread / unsupported: best-effort
   109	        pass
   110	    _daemon.run()
546:        # sys.stdout to stderr so vendored-library print() cannot corrupt it.
547:        self._out = sys.stdout
2556:    # construction; redirect sys.stdout to stderr so any stray library print()
2560:    _daemon = Daemon()
2561:    sys.stdout = sys.stderr
662:            self._drop()
2007:                self._drop()                         # free the CMD handle so the raw serial can open the port
2481:            self._drop()
2551:            self._drop()
     1	name: tenorrekey
     2	options:
     3	  bundleIdPrefix: vn.tenor
     4	  deploymentTarget:
     5	    macOS: "14.0"
     6	  createIntermediateGroups: true
     7	settings:
     8	  base:
     9	    SWIFT_VERSION: "5.0"
    10	    MARKETING_VERSION: "1.0.0"
    11	    CURRENT_PROJECT_VERSION: "1"
    12	targets:
    13	  tenorrekey:
    14	    type: application
    15	    platform: macOS
    16	    sources:
    17	      - Sources
    18	      - Assets.xcassets
    19	      - Resources/Fonts
    20	    settings:
    21	      base:
    22	        PRODUCT_BUNDLE_IDENTIFIER: vn.tenor.rekey
    23	        PRODUCT_NAME: tenorrekey
    24	        GENERATE_INFOPLIST_FILE: "YES"
    25	        INFOPLIST_KEY_CFBundleDisplayName: "tenor/rekey"
    26	        # CFBundleName (menu bar / app switcher) is derived by Xcode from
    27	        # PRODUCT_NAME (kept slash-free for the on-disk binary); package.sh patches
    28	        # it to the brand name "tenor/rekey" before signing.
    29	        INFOPLIST_KEY_LSApplicationCategoryType: "public.app-category.utilities"
    30	        INFOPLIST_KEY_NSHumanReadableCopyright: ""
    31	        INFOPLIST_KEY_NSBluetoothAlwaysUsageDescription: "tenor/rekey connects to a Chameleon over Bluetooth."
    32	        ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon
    33	        CODE_SIGN_IDENTITY: "-"
    34	        CODE_SIGNING_REQUIRED: "NO"
    35	        ENABLE_HARDENED_RUNTIME: "NO"
    36	        DEAD_CODE_STRIPPING: "YES"
    37	schemes:
    38	  tenorrekey:
    39	    build:
    40	      targets:
    41	        tenorrekey: all
    42	    run:
    43	      config: Debug
   125	done
   126	[ -n "$HIDAPI_SRC" ] || { echo "libhidapi not found (brew install hidapi)"; exit 1; }
   127	cp "$HIDAPI_SRC" "$FW/libhidapi.dylib"
   128	install_name_tool -id "@rpath/libhidapi.dylib" "$FW/libhidapi.dylib" 2>/dev/null || true
   129	
   130	echo "==> 5/7  pre-compile + sign"
   131	# Pre-generate every .pyc now so they are sealed by the signature; the app also
   132	# launches python with PYTHONDONTWRITEBYTECODE=1 so it never writes one at runtime
   133	# (a code-signed bundle that mutates itself breaks its own seal).
   134	"$RES/python/bin/python3" -m compileall -q "$RES/python/lib" "$RES/probe" >/dev/null 2>&1 || true
   135	
   136	# Use a Developer ID Application cert if one is installed (notarizable: hardened
   137	# runtime + entitlements + secure timestamp); otherwise ad-hoc (runs on this Mac,
   138	# but cannot be notarized). An "Apple Development" cert is NOT a Developer ID cert.
   139	SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Developer ID Application[^"]*\)".*/\1/p' | head -1)"
   140	ENT="$HERE/tenorrekey.entitlements"
   141	if [ -n "$SIGN_ID" ]; then
   142	    echo "    Developer ID: $SIGN_ID  (hardened runtime + entitlements)"
   143	    SIGN=(codesign --force --timestamp --options runtime -s "$SIGN_ID")
   144	    SIGN_ENT=(codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$SIGN_ID")
   145	else
   146	    echo "    no Developer ID Application cert found - ad-hoc signing (NOT notarizable)"
   147	    SIGN=(codesign --force -s -)
   148	    SIGN_ENT=(codesign --force -s -)
   149	fi
   150	# sign inside-out: the dylib + every mach-o the python tree ships, then the
   151	# interpreter and the app last (the interpreter + app carry the entitlements; the
   152	# interpreter is the process that loads the bundled libs via ctypes).
   153	"${SIGN[@]}" "$FW/libhidapi.dylib"
   154	find "$RES/python" \( -name "*.dylib" -o -name "*.so" \) -exec "${SIGN[@]}" {} + 2>/dev/null || true
   155	# The vendored Chameleon crackers are standalone Mach-O helpers spawned as subprocess
   156	# (pure compute, no entitlements needed). Sign them so a hardened/notarizable build
   157	# and a strict verify cover every mach-o in the bundle.
   158	for b in nested staticnested darkside hardnested; do
   159	    "${SIGN[@]}" "$RES/probe/native/chameleon/bin/$b" || { echo "codesign failed: cracker $b"; exit 1; }
   160	    codesign --verify --strict "$RES/probe/native/chameleon/bin/$b" || { echo "codesign verify failed: cracker $b"; exit 1; }
   161	done
   162	find "$RES/python/bin" -type f -perm -111 -exec "${SIGN_ENT[@]}" {} + 2>/dev/null || true
   163	"${SIGN_ENT[@]}" "$STAGE/Contents/MacOS/tenorrekey" 2>/dev/null || true
   164	"${SIGN_ENT[@]}" "$STAGE"
   165	codesign --verify --strict --deep "$STAGE" && echo "    codesign verify OK"
   166	
   167	# Notarize the .app now (before the dmg) so the dmg ships a stapled app that runs
   168	# offline. Needs a Developer ID signature AND a stored notarytool credential -
   169	# create it once (the founder, it needs the Apple ID app-specific password):
   170	#   xcrun notarytool store-credentials tenor-notary \
   171	#       --apple-id <id> --team-id 35ZXMV2YHU --password <app-specific-password>
   172	# then run:  NOTARY_PROFILE=tenor-notary app/tools/package.sh
   173	NOTARIZE=0
   174	if [ -n "$SIGN_ID" ] && [ -n "${NOTARY_PROFILE:-}" ]; then
   175	    NOTARIZE=1
     1	<?xml version="1.0" encoding="UTF-8"?>
     2	<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
     3	<!-- Hardened-runtime entitlements for the notarized build. The app embeds a
     4	     relocatable CPython that loads its own .dylib/.so modules and libhidapi via
     5	     ctypes, so it needs library-validation disabled (those libraries are not all
     6	     signed by this Team) and unsigned-executable-memory allowed (ctypes / the
     7	     embedded interpreter). No App Sandbox: this is a local engineering tool that
     8	     opens a USB-HID device, which the sandbox would block, and it uses CoreBluetooth
     9	     to reach a Chameleon over Bluetooth LE (the device.bluetooth entitlement). -->
    10	<plist version="1.0">
    11	<dict>
    12	    <key>com.apple.security.cs.disable-library-validation</key>
    13	    <true/>
    14	    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    15	    <true/>
    16	    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    17	    <true/>
    18	    <key>com.apple.security.device.bluetooth</key>
    19	    <true/>
    20	</dict>
    21	</plist>

exec
/bin/zsh -lc "rg -n \"func refreshStatus|applyReaderGone|func shutdown\" app/Sources/AppModel.swift app/Sources/Engine/DeviceBridge.swift; nl -ba app/Sources/AppModel.swift | sed -n '485,550p'; nl -ba app/Sources/Engine/DeviceBridge.swift | sed -n '275,370p'; rg -n \"_DEAD\\s*=|OpenFailException|NotOpenException\" probe/chameleon_d.py | head -n 60" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
app/Sources/Engine/DeviceBridge.swift:305:    func shutdown() async {
app/Sources/AppModel.swift:374:            applyReaderGone()
app/Sources/AppModel.swift:418:                applyReaderGone()
app/Sources/AppModel.swift:502:    private func refreshStatus() async {
app/Sources/AppModel.swift:506:                applyReaderGone()
app/Sources/AppModel.swift:529:            applyReaderGone()
app/Sources/AppModel.swift:557:    private func applyReaderGone() {
   485	    }
   486	
   487	    /// Clear the flash outcome + progress. Called when the flashing sheet is dismissed, so a
   488	    /// stale error / done state never carries into the next time it is opened.
   489	    func clearFlashState() {
   490	        flashStage = nil
   491	        flashPercent = nil
   492	        flashDone = false
   493	        flashError = nil
   494	    }
   495	
   496	    /// Consecutive polls that saw the reader but no card; a seated card that blips
   497	    /// for one cycle should not drop its decoded grid, so we debounce a removal.
   498	    private var cardAbsentStreak = 0
   499	
   500	    /// One status sample: detects reader unplug (drops to offline + clears), reader
   501	    /// replug (back online + refetch device info), and card placed / removed.
   502	    private func refreshStatus() async {
   503	        do {
   504	            let p = try await activeBridge().poll(tries: 8)
   505	            if p.reader == false {           // reader unplugged: reflect it at once
   506	                applyReaderGone()
   507	                return
   508	            }
   509	            readerOnline = true
   510	            if info == nil { info = try? await activeBridge().info() }   // refetch until it lands
   511	            // NOTE: do not clear lastError here - the 1.5s poll would wipe a clone /
   512	            // decode / format error banner before the user could read it. Operations
   513	            // clear it when they start; the banner also has a dismiss button.
   514	            if p.present {
   515	                cardAbsentStreak = 0
   516	                // A different card (or first placement): the DOCUMENT stays (it is the
   517	                // working image, not bound to this card); only the previous write's
   518	                // per-block glyphs reset so they never show on the new card.
   519	                if card == nil || p.uid != card?.uid {
   520	                    withAnimation(.easeInOut(duration: 0.3)) { clearCardBound(); card = p }
   521	                }
   522	            } else {
   523	                cardAbsentStreak += 1
   524	                if card != nil && cardAbsentStreak >= 2 {
   525	                    withAnimation(.easeInOut(duration: 0.3)) { card = nil; clearCardBound() }
   526	                }
   527	            }
   528	        } catch {
   529	            applyReaderGone()
   530	        }
   531	    }
   532	
   533	    /// Reset state tied to the physical card on the reader: the last clone's per-block
   534	    /// glyphs and the NTAG page view (NTAG has no writable document, so its pages are
   535	    /// bound to the live card). The writable Classic DOCUMENT (sector grid + source
   536	    /// image + selection) is deliberately kept so it survives a card swap, removal, or
   537	    /// reader unplug - the working image is not bound to whatever card is on the
   538	    /// reader. Shared by the swap, removal, and reader-gone paths so they cannot drift.
   539	    private func clearCardBound() {
   540	        cloneResults = [:]
   541	        cloneFailReasons = [:]
   542	        ntagDoc = nil
   543	        noKeysFound = false
   544	    }
   545	
   546	    /// Drop the working document entirely (the source tag's clear button): the image,
   547	    /// its grid, page dump, and selection. The card on the reader is untouched.
   548	    func clearDocument() {
   549	        withAnimation(.easeInOut(duration: 0.3)) {
   550	            source = nil; sectors = []; ntagDoc = nil; selected = nil; selectedBlock = nil
   275	    /// We do not register a continuation for the cancel line - its id-tagged reply is
   276	    /// simply dropped by route().
   277	    func cancel() {
   278	        guard let p = process else { return }
   279	        let gen = opGeneration                  // the op we are cancelling
   280	        let id = nextID; nextID += 1
   281	        if let data = try? JSONEncoder().encode(Req(id: id, method: "cancel")) {
   282	            try? stdin?.write(contentsOf: data)
   283	            try? stdin?.write(contentsOf: Data([0x0A]))
   284	        }
   285	        Task { [weak self] in
   286	            try? await Task.sleep(for: .seconds(5))
   287	            await self?.hardCancel(generation: gen, process: p)
   288	        }
   289	    }
   290	
   291	    /// Hard-kill fallback for a cancel the daemon did not honour: terminate ONLY if the
   292	    /// SAME op that was cancelled is still in flight after the grace window - same daemon
   293	    /// (`process === p`), same operation (`opGeneration == gen`, so a later op that
   294	    /// reused the daemon is never killed), and still streaming (`eventSink != nil`, so an
   295	    /// op that already wound down is left alone). Any of those failing makes this a no-op.
   296	    private func hardCancel(generation gen: Int, process p: Process) {
   297	        if opGeneration == gen, process === p, eventSink != nil { p.terminate() }
   298	    }
   299	    /// Tear the daemon down (device hot-swap / app teardown) completion-safely: signal
   300	    /// the child, wait a bounded interval for it to actually exit, then synchronously
   301	    /// fail every pending continuation and cancel the pipe readers. We do NOT rely on
   302	    /// the async terminationHandler alone: by the time this returns no stale stdout
   303	    /// line can resolve a continuation and no request is left dangling. A fresh bridge
   304	    /// for the newly detected device respawns the right daemon on its next request.
   305	    func shutdown() async {
   306	        guard let p = process else { died(); return }
   307	        p.terminate()                                   // SIGTERM
   308	        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
   309	        while p.isRunning, ContinuousClock.now < deadline {
   310	            try? await Task.sleep(for: .milliseconds(20))
   311	        }
   312	        if p.isRunning { p.interrupt() }                // still alive: one more signal
   313	        // Our side is torn down regardless of whether the child has fully exited yet:
   314	        // died() cancels the readability handlers, fails every pending request, and
   315	        // drops the handles. It is idempotent with the terminationHandler's later
   316	        // died() call (a second run finds nil handles + empty pending), so there is
   317	        // no double-resume.
   318	        died()
   319	    }
   320	    /// Size of the daemon's built-in dictionary (for the Settings "+N built-in" line).
   321	    func builtinKeyCount() async throws -> Int {
   322	        try await request("keys_builtin_count", as: CountResult.self).count
   323	    }
   324	    /// Number of keys the daemon has learned from real cards (Settings line).
   325	    func learnedKeyCount() async throws -> Int {
   326	        try await request("learned_stats", as: CountResult.self).count
   327	    }
   328	    /// Forget every learned key.
   329	    func clearLearnedKeys() async throws {
   330	        _ = try await request("learned_clear", as: CountResult.self)
   331	    }
   332	    func readNTAG() async throws -> NtagResult {
   333	        try await request("read_ntag", timeout: .seconds(120), as: NtagResult.self)
   334	    }
   335	    /// Factory-reset the card (zero data + factory trailer). keys from a prior decode.
   336	    func formatCard(keys: [String: [String]], targetUID: String?) async throws -> FormatResult {
   337	        try await request("format", params: FormatParams(keys: keys, target_uid: targetUID), timeout: .seconds(300), as: FormatResult.self)
   338	    }
   339	    func apdu(_ hex: String) async throws -> ApduResult {
   340	        try await request("apdu", params: ApduParams(hex: hex), as: ApduResult.self)
   341	    }
   342	
   343	    /// Clone a dump onto the card on the reader. Per-block results stream to
   344	    /// `onBlock` as the daemon writes; the final tally is returned.
   345	    func writeMFD(blocks: [String: String], keys: [String: [String]],
   346	                  trailers: Bool, uid: Bool, targetUID: String?,
   347	                  onBlock: @escaping @Sendable (Int, Bool, String?) -> Void) async throws -> WriteResult {
   348	        guard eventSink == nil else { throw EngineError.daemon("an operation is already in progress") }
   349	        eventSink = { ev in
   350	            // `unsafe` carries WHY a trailer was refused (bad access bits / would lock
   351	            // its own keys), so the UI can name the reason instead of a bare block index.
   352	            if ev.method == "write_mfd", let b = ev.block, let ok = ev.ok { onBlock(b, ok, ev.unsafe) }
   353	        }
   354	        opGeneration += 1
   355	        defer { eventSink = nil }
   356	        let params = CloneParams(blocks: blocks, keys: keys, trailers: trailers, uid: uid, target_uid: targetUID)
   357	        return try await request("write_mfd", params: params, timeout: .seconds(300), as: WriteResult.self)
   358	    }
   359	
   360	    // ---- Chameleon-only verbs (the shell calls these only when the connected
   361	    // device advertises the matching capability; the X7 never does) --------------
   362	
   363	    /// The 8-slot library (type / enabled / nick / active per slot).
   364	    func slotsList() async throws -> [ChameleonSlot] {
   365	        try await request("slots_list", as: SlotsResult.self).slots
   366	    }
   367	    /// Make a slot the active one.
   368	    func slotSelect(_ slot: Int) async throws {
   369	        _ = try await request("slot_select", params: SlotParam(slot: slot), as: SlotSelectResult.self)
   370	    }
37:from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
497:_DEAD = (OSError, NotOpenException, OpenFailException)

exec
/bin/zsh -lc "rg -n \"class .*Exception|def send_cmd_sync|is_timeout|Timeout\" probe/chameleon/chameleon_com.py | head -n 100; nl -ba probe/chameleon/chameleon_com.py | sed -n '379,475p'; nl -ba app/Sources/AppModel.swift | sed -n '548,580p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
31:class NotOpenException(Exception):
37:class OpenFailException(Exception):
43:class CMDInvalidException(Exception):
318:            self.wait_response_map[task_cmd]['is_timeout'] = False
358:                        self.wait_response_map[task_cmd]['is_timeout'] = True
412:    def send_cmd_sync(self, cmd: int, data: Union[bytes, None] = None, status: int = 0,
436:                raise TimeoutError(f"CMD {cmd} start timeout")
443:                raise TimeoutError(f"CMD {cmd} lost (connection closed)")
446:            if entry.get('is_timeout'):
447:                raise TimeoutError(f"CMD {cmd} exec timeout")
   379	    def send_cmd_auto(self, cmd: int, data: Union[bytes, None] = None, status: int = 0, callback=None, timeout: int = 3,
   380	                      close: bool = False):
   381	        """
   382	            Send cmd to device
   383	
   384	        :param cmd: cmd
   385	        :param data: bytes data (optional)
   386	        :param status: status (optional)
   387	        :param callback: call on response
   388	        :param timeout: wait response timeout
   389	        :param close: close connection after executing
   390	        :return:
   391	        """
   392	        self.check_open()
   393	        # delete old task
   394	        if cmd in self.wait_response_map:
   395	            del self.wait_response_map[cmd]
   396	        # make data frame
   397	        if DEBUG:
   398	            try:
   399	                command = Command(cmd)
   400	                command_name = f"{command.name}"
   401	            except ValueError:
   402	                command_name = "(UNKNOWN)"
   403	            cmd_string = f'{cmd:4} {command_name}{f"[{status:04x}]" if status != 0 else ""}'
   404	            hexdata = data.hex() if data is not None else ""
   405	            print(f"<={color_string((CC, cmd_string.ljust(40)), (CY, hexdata))}")
   406	        data_frame = self.make_data_frame_bytes(cmd, data, status)
   407	        task = {'cmd': cmd, 'frame': data_frame, 'timeout': timeout, 'close': close}
   408	        if callable(callback):
   409	            task['callback'] = callback
   410	        self.send_data_queue.put(task)
   411	
   412	    def send_cmd_sync(self, cmd: int, data: Union[bytes, None] = None, status: int = 0,
   413	                      timeout: int = 3) -> Response:
   414	        """
   415	            Send cmd to device, and block receive data.
   416	
   417	        :param cmd: cmd
   418	        :param data: bytes data (optional)
   419	        :param status: status (optional)
   420	        :param timeout: wait response timeout
   421	        :return: response data
   422	        """
   423	        if len(self.commands):
   424	            # check if chameleon can understand this command
   425	            if cmd not in self.commands:
   426	                raise CMDInvalidException(f"This device doesn't declare that it can support this command: {cmd}.\n"
   427	                                          f"Make sure firmware is up to date and matches client")
   428	        # first to send cmd, no callback mode(sync)
   429	        self.send_cmd_auto(cmd, data, status, None, timeout)
   430	        # wait cmd start process (bounded + close-safe: a transport thread may
   431	        # clear wait_response_map via close() during either wait window, which
   432	        # would otherwise spin here forever or KeyError in the loop below)
   433	        start_deadline = time.time() + timeout + 1
   434	        while cmd not in self.wait_response_map:
   435	            if time.time() > start_deadline:
   436	                raise TimeoutError(f"CMD {cmd} start timeout")
   437	            time.sleep(0.01)
   438	        # wait response data set
   439	        while True:
   440	            entry = self.wait_response_map.get(cmd)
   441	            if entry is None:
   442	                # close() cleared the map mid-wait: the port is gone
   443	                raise TimeoutError(f"CMD {cmd} lost (connection closed)")
   444	            if entry['response'] is not None:
   445	                break
   446	            if entry.get('is_timeout'):
   447	                raise TimeoutError(f"CMD {cmd} exec timeout")
   448	            time.sleep(0.01)
   449	        # ok, data received.
   450	        data_response = entry['response']
   451	        self.wait_response_map.pop(cmd, None)
   452	        if data_response.status == Status.INVALID_CMD:
   453	            raise CMDInvalidException(f"Device unsupported cmd: {cmd}")
   454	        return data_response
   455	
   456	
   457	if __name__ == '__main__':
   458	    try:
   459	        cml = ChameleonCom().open('com19')
   460	    except OpenFailException:
   461	        cml = ChameleonCom().open('/dev/ttyACM0')
   462	    resp = cml.send_cmd_sync(0x03E8, None, 0)
   463	    print(resp.status)
   464	    print(resp.data)
   465	    cml.close()
   548	    func clearDocument() {
   549	        withAnimation(.easeInOut(duration: 0.3)) {
   550	            source = nil; sectors = []; ntagDoc = nil; selected = nil; selectedBlock = nil
   551	            cloneResults = [:]; cloneFailReasons = [:]; noKeysFound = false
   552	        }
   553	    }
   554	
   555	    /// Reader unplugged or the daemon went away: go offline and clear everything
   556	    /// tied to a live reader. No-op when already in that state (avoids churn).
   557	    private func applyReaderGone() {
   558	        cardAbsentStreak = 0
   559	        guard readerOnline || card != nil || info != nil else { return }
   560	        withAnimation(.easeInOut(duration: 0.3)) {
   561	            readerOnline = false
   562	            info = nil
   563	            card = nil
   564	            clearCardBound()
   565	            resetChameleonState()
   566	        }
   567	    }
   568	
   569	    func decode() async {
   570	        // Refuse while a swap is tearing the device down, or another device op already
   571	        // owns the reader (deviceBusy includes `decoding`, so this also blocks a
   572	        // double-decode). Also refuse while emulating: a reader op would force the device
   573	        // back to reader mode under the emulation, leaving the toggle lying. Serialized,
   574	        // never racing the bridge.
   575	        guard !swapping, !deviceBusy, !emulating else { return }
   576	        decoding = true
   577	        decodeCancelled = false
   578	        decodeProgress = nil
   579	        decodeStart = Date()
   580	        lastError = nil

exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '1920,2040p'; rg -n \"func dfu|dfuFlash|flashFirmware|capabilities.dfu\" app/Sources/AppModel.swift app/Sources -g '*.swift' | head -n 120; nl -ba app/Sources/AppModel.swift | sed -n '1260,1355p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
  1920	        return {"model": "Chameleon Ultra" if model == 0 else "Chameleon Lite",
  1921	                "current": current, "git": git, "asset": self._dfu_asset_name(model),
  1922	                "latest": latest, "updateAvailable": update, "note": note}
  1923	
  1924	    def dfu_flash(self, p):
  1925	        """Update the Chameleon firmware over Nordic Secure DFU. v1 is DOWNLOAD-ONLY: the
  1926	        source is ALWAYS the model-specific application-only asset from the official releases
  1927	        (no local files, no arbitrary URLs). Params: optional `model` ('ultra'/'lite'), used
  1928	        only to recover a device already stuck in DFU whose model cannot be read. Flow, in
  1929	        strict brick-safe order:
  1930	          1. resolve the target device UNAMBIGUOUSLY. Already in DFU -> require exactly one
  1931	             DFU device and an explicit model (never guess). Otherwise bind to exactly one
  1932	             connected Chameleon and read its model; SNAPSHOT the DFU ports before rebooting.
  1933	          2. resolve the official release ONCE and PIN it; DOWNLOAD the pinned asset into a
  1934	             private per-flash temp dir with a complete-transfer + size + digest check.
  1935	          3. app-only SANITY check the download (reject a full package).
  1936	          4. reboot the bound device (10-byte enter-DFU frame); wait for EXACTLY ONE NEW DFU
  1937	             port (a port not in the pre-reboot snapshot) and flash THAT one.
  1938	          5. flash with adafruit-nrfutil, streaming percent progress.
  1939	        The cooperative cancel is honored ONLY up to the moment the flash write begins; a
  1940	        mid-write abort can brick, so it is never checked past step 5. `dfu_flash` is armed as
  1941	        flash-pending at DISPATCH (run()), so an EOF/shutdown while it is in flight always
  1942	        joins UNBOUNDED and never abandons a flash."""
  1943	        supplied_model = self._norm_model(p.get("model"))
  1944	        dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
  1945	        cdc_ports = self._find_cdc_ports()
  1946	        cdc = None
  1947	        if cdc_ports:
  1948	            # There is a normal Chameleon to reboot. Bind to EXACTLY ONE (never reboot an
  1949	            # arbitrary one), read its model FROM HARDWARE, and resolve the DFU port AFTER the
  1950	            # reboot as the NEW port relative to `dfu_before` - so a device that was already
  1951	            # stuck in DFU is never mistaken for the one we just rebooted.
  1952	            if len(cdc_ports) != 1:
  1953	                raise RuntimeError("more than one Chameleon is connected; connect only the one "
  1954	                                   "to update, then retry")
  1955	            # The model MUST come from the connected device; a caller-supplied model choice is
  1956	            # only valid for in-DFU recovery (where hardware cannot be read). Reject an override
  1957	            # so a live Ultra can never be handed Lite firmware.
  1958	            if supplied_model is not None:
  1959	                raise RuntimeError("the model is read from the connected device and cannot be "
  1960	                                   "overridden; the Ultra/Lite choice is only for recovering a "
  1961	                                   "device already in DFU")
  1962	            dfu_port = None
  1963	            cdc = cdc_ports[0]
  1964	            try:
  1965	                c = self._connect(cdc)
  1966	                raw_model = c.get_device_model()
  1967	            except Exception:
  1968	                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
  1969	            # Validate the HARDWARE model too (fail-closed): an unexpected value (2, future
  1970	            # variants) raises here rather than defaulting to Lite firmware.
  1971	            model = self._norm_model(raw_model)
  1972	        else:
  1973	            # No normal device to reboot -> pure recovery of a device ALREADY in DFU (crashed /
  1974	            # manual B-button). Require exactly ONE DFU device, and an EXPLICIT model - its model
  1975	            # cannot be read in DFU and we never guess (a wrong guess flashes Ultra onto a Lite).
  1976	            if not dfu_before:
  1977	                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
  1978	            if len(dfu_before) != 1:
  1979	                raise RuntimeError("more than one Chameleon is in DFU mode; connect only the one "
  1980	                                   "to update, then retry")
  1981	            dfu_port = dfu_before[0]
  1982	            model = supplied_model
  1983	            if model is None:
  1984	                raise RuntimeError("the Chameleon is already in DFU mode so its model cannot be "
  1985	                                   "read; choose Ultra or Lite to flash-recover it")
  1986	
  1987	        tmp = None
  1988	        try:
  1989	            self.emit({"event": "progress", "method": "dfu_flash", "stage": "prepare", "percent": 0})
  1990	            # Resolve the official release ONCE and PIN it for this whole op (no re-resolve).
  1991	            rel = self._latest_release(model)
  1992	            if not rel or not rel.get("url"):
  1993	                raise RuntimeError("no application DFU release found for %s"
  1994	                                   % self._dfu_asset_name(model))
  1995	            tmp = tempfile.mkdtemp(prefix="cham-dfu-")   # private per-flash dir (mode 0700)
  1996	            os.chmod(tmp, 0o700)
  1997	            pkg = os.path.join(tmp, self._dfu_asset_name(model))
  1998	            self.emit({"event": "progress", "method": "dfu_flash", "stage": "download",
  1999	                       "percent": 0, "tag": rel.get("tag")})
  2000	            self._download_asset(rel, pkg)           # complete + size-capped + digest-checked
  2001	            info = self._validate_dfu_zip(pkg)       # app-only sanity check (reject full)
  2002	            self.emit({"event": "progress", "method": "dfu_flash", "stage": "validated", "percent": 0})
  2003	            if self._cancel.is_set():                # safe cancel: nothing has been written
  2004	                return {"flashed": False, "cancelled": True}
  2005	
  2006	            if dfu_port is None:
  2007	                self._drop()                         # free the CMD handle so the raw serial can open the port
  2008	                self.emit({"event": "progress", "method": "dfu_flash", "stage": "enter", "percent": 0})
  2009	                self._send_enter_dfu(cdc)
  2010	                self.emit({"event": "progress", "method": "dfu_flash", "stage": "wait", "percent": 0})
  2011	                # Accept only a NEW, attributable DFU port (not present before the reboot), and
  2012	                # exactly one - refuse if a second device appears or the target is ambiguous.
  2013	                new = self._wait_new_dfu_ports(dfu_before)
  2014	                if not new:
  2015	                    raise RuntimeError("the Chameleon did not re-appear in DFU mode. Manual fallback: "
  2016	                                       + _MANUAL_FALLBACK)
  2017	                if len(new) != 1:
  2018	                    raise RuntimeError("more than one new Chameleon appeared in DFU after the reboot; "
  2019	                                       "disconnect the others and retry")
  2020	                dfu_port = new[0]
  2021	
  2022	            # Commit handshake (closes the EOF/dispatch race): announce the uninterruptible
  2023	            # write BEFORE the final cancel re-check, so EOF either sees `_flashing` (and joins
  2024	            # unbounded) or the worker still sees the cancel here and aborts before any write.
  2025	            self._flashing.set()
  2026	            try:
  2027	                if self._cancel.is_set():            # last safe cancel point, before any write
  2028	                    self._flashing.clear()
  2029	                    return {"flashed": False, "cancelled": True}
  2030	                # POINT OF NO CANCEL: the bootloader is being written; a mid-write abort can
  2031	                # brick, so the cancel flag is deliberately not checked again.
  2032	                self._run_flash(pkg, dfu_port)
  2033	            finally:
  2034	                self._flashing.clear()
  2035	            return {"flashed": True, "port": dfu_port, "tag": rel.get("tag"),
  2036	                    "hash": info.get("hash_type")}
  2037	        finally:
  2038	            if tmp and os.path.isdir(tmp):
  2039	                shutil.rmtree(tmp, ignore_errors=True)
  2040	
app/Sources/AppModel.swift:112:    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
app/Sources/AppModel.swift:1267:    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
app/Sources/AppModel.swift:1273:        guard capabilities.dfu, !swapping, !deviceBusy else { return }
app/Sources/AppModel.swift:1289:    func flashFirmware(model: String?) async {
app/Sources/AppModel.swift:1290:        guard capabilities.dfu, !swapping, !deviceBusy else { return }
app/Sources/AppModel.swift:1304:            let r = try await activeBridge().dfuFlash(model: model, onProgress: onProgress)
app/Sources/Engine/DeviceBridge.swift:453:    // ---- firmware update (Chameleon-only; gated on capabilities.dfu) ------------
app/Sources/Engine/DeviceBridge.swift:458:    func dfuCheck() async throws -> DfuStatus {
app/Sources/Engine/DeviceBridge.swift:473:    func dfuFlash(model: String?,
app/Sources/Views/FlashingView.swift:4:/// advertises it (capabilities.dfu; the X7 never does). v1 is DOWNLOAD-ONLY: it reads the
app/Sources/Views/FlashingView.swift:48:                Task { await model.flashFirmware(model: m) }
app/Sources/Views/FlashingView.swift:130:                    Button(l.t("update_latest")) { Task { await model.flashFirmware(model: nil) } }
app/Sources/AppModel.swift:112:    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
app/Sources/AppModel.swift:1267:    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
app/Sources/AppModel.swift:1273:        guard capabilities.dfu, !swapping, !deviceBusy else { return }
app/Sources/AppModel.swift:1289:    func flashFirmware(model: String?) async {
app/Sources/AppModel.swift:1290:        guard capabilities.dfu, !swapping, !deviceBusy else { return }
app/Sources/AppModel.swift:1304:            let r = try await activeBridge().dfuFlash(model: model, onProgress: onProgress)
app/Sources/Shell/RootView.swift:240:                || model.capabilities.lf || model.capabilities.dfu {
app/Sources/Shell/RootView.swift:276:            if model.capabilities.dfu {
  1260	            try await b.slotSave()
  1261	            slots = try await b.slotsList()
  1262	            lastError = nil
  1263	        } catch { lastError = "\(error)" }
  1264	        lfBusy = false
  1265	    }
  1266	
  1267	    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
  1268	
  1269	    /// Read the running firmware + the newest published release (opening the flashing
  1270	    /// sheet or after a flash). A failed release fetch is not fatal - the daemon returns
  1271	    /// the current version with a null latest + a note.
  1272	    func checkFirmware() async {
  1273	        guard capabilities.dfu, !swapping, !deviceBusy else { return }
  1274	        flashError = nil
  1275	        // A device already in the bootloader has no command interface to query: leave the
  1276	        // status empty (the sheet shows the Ultra/Lite recovery choice) rather than erroring.
  1277	        if deviceInDFU { dfuStatus = nil; return }
  1278	        do { dfuStatus = try await activeBridge().dfuCheck(); lastError = nil }
  1279	        catch { lastError = "\(error)" }
  1280	    }
  1281	
  1282	    /// Flash firmware over DFU (v1 is download-only: the daemon fetches the official
  1283	    /// model-specific asset). `model` is nil in normal mode (read off the device) and
  1284	    /// "ultra"/"lite" only when recovering a device stuck in DFU whose model cannot be read.
  1285	    /// The daemon validates the download (app-only, hash) before writing anything and refuses
  1286	    /// a mid-write cancel, so this is a commit-once action. The device reboots into the
  1287	    /// bootloader and back; `flashing` pauses the status monitor across that so it does not
  1288	    /// mistake the reboot for an unplug.
  1289	    func flashFirmware(model: String?) async {
  1290	        guard capabilities.dfu, !swapping, !deviceBusy else { return }
  1291	        flashing = true
  1292	        flashDone = false
  1293	        flashError = nil
  1294	        flashStage = nil
  1295	        flashPercent = nil
  1296	        lastError = nil
  1297	        let onProgress: @Sendable (String?, Int?) -> Void = { [weak self] stage, pct in
  1298	            Task { @MainActor in
  1299	                if let stage { self?.flashStage = stage }
  1300	                if let pct { self?.flashPercent = pct }
  1301	            }
  1302	        }
  1303	        do {
  1304	            let r = try await activeBridge().dfuFlash(model: model, onProgress: onProgress)
  1305	            if r.cancelled == true {
  1306	                flashStage = nil
  1307	            } else if r.flashed {
  1308	                flashStage = "done"
  1309	                flashPercent = 100
  1310	                flashDone = true
  1311	            }
  1312	        } catch {
  1313	            // Show the failure INSIDE the sheet with the retry path (a failed flash usually
  1314	            // leaves the device in the bootloader), not only the root banner behind the modal.
  1315	            flashError = "\(error)"
  1316	        }
  1317	        flashing = false
  1318	    }
  1319	
  1320	    // ---- file dumps --------------------------------------------------------
  1321	
  1322	    func openDumpDialog() {
  1323	        let panel = NSOpenPanel()
  1324	        panel.allowedContentTypes = [.data]
  1325	        panel.allowsOtherFileTypes = true
  1326	        panel.canChooseFiles = true
  1327	        if panel.runModal() == .OK, let url = panel.url { loadDump(from: url) }
  1328	    }
  1329	
  1330	    /// Default save name `yymmdd_tr_<uid>.dump` - sorts next to the Windows nfcPro
  1331	    /// dumps in the same folder and stays a plain raw image both tools can open.
  1332	    static func defaultDumpFilename(_ dump: CardDump) -> String {
  1333	        let f = DateFormatter()
  1334	        f.dateFormat = "yyMMdd"
  1335	        let uid = dump.uid.replacingOccurrences(of: " ", with: "").lowercased()
  1336	        let stem = uid.isEmpty ? dump.name : uid
  1337	        return "\(f.string(from: Date()))_tr_\(stem).dump"
  1338	    }
  1339	
  1340	    func saveDumpDialog() {
  1341	        guard let dump = source else { return }
  1342	        let panel = NSSavePanel()
  1343	        panel.nameFieldStringValue = Self.defaultDumpFilename(dump)
  1344	        panel.allowsOtherFileTypes = true
  1345	        if let folder = UserDefaults.standard.string(forKey: "rekey.exportFolder") {
  1346	            panel.directoryURL = URL(fileURLWithPath: folder)
  1347	        }
  1348	        if panel.runModal() == .OK, let url = panel.url { saveDump(dump, to: url) }
  1349	    }
  1350	
  1351	    /// Open a dump as the working document: it becomes the source AND its memory map
  1352	    /// is rendered on the canvas (a loaded image is a document just like a decode), so
  1353	    /// the canvas never shows a stale grid under a freshly opened source.
  1354	    func loadDump(from url: URL) {
  1355	        do {

exec
/bin/zsh -lc "rg -n \"struct DeviceCapabilities|static let chameleonUltra|chameleonUltra =|var capabilities\" app/Sources/Engine app/Sources/AppModel.swift; nl -ba app/Sources/Engine/DeviceRegistry.swift | sed -n '1,150p'; nl -ba app/Sources/Engine/Models.swift | sed -n '1,100p'; nl -ba app/Sources/Shell/RootView.swift | sed -n '265,288p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
app/Sources/AppModel.swift:168:    var capabilities: DeviceCapabilities { info?.capabilities ?? descriptor.capabilities }
app/Sources/Engine/DeviceRegistry.swift:54:    static let chameleonUltra = DeviceDescriptor(
app/Sources/Engine/Models.swift:15:    var capabilities: DeviceCapabilities? = nil
app/Sources/Engine/DeviceCapabilities.swift:10:struct DeviceCapabilities: Codable, Equatable, Sendable {
app/Sources/Engine/DeviceCapabilities.swift:35:    static let chameleonUltra = DeviceCapabilities(
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
     1	import Foundation
     2	
     3	/// Decoded shapes from the x7d.py daemon contract (probe/x7d.py).
     4	
     5	struct DeviceInfo: Codable, Equatable {
     6	    let model: String
     7	    let serial: String
     8	    let hw: String
     9	    /// Device family the daemon reports ("x7" / "chameleon-ultra" / "chameleon-lite").
    10	    /// Optional so an older daemon that omits it decodes to nil rather than failing.
    11	    var family: String? = nil
    12	    /// The device's capability manifest, used to gate device-specific UI. Optional
    13	    /// so a daemon that predates the manifest decodes to nil; the shell then falls
    14	    /// back to the active descriptor's static capabilities.
    15	    var capabilities: DeviceCapabilities? = nil
    16	}
    17	
    18	struct PollResult: Codable, Equatable {
    19	    let present: Bool
    20	    let uid: String?
    21	    let atqa: String?
    22	    let sak: Int?
    23	    /// Whether the reader itself is connected (nil from older daemons -> treat as
    24	    /// connected). `present` is a card on the reader; `reader` is the reader.
    25	    var reader: Bool? = nil
    26	    /// Card family from the daemon ("ntag" / "classic"); nil from older daemons,
    27	    /// in which case `isNTAG` falls back to the ATQA/SAK rule.
    28	    var kind: String? = nil
    29	
    30	    /// True for a genuine NTAG / Ultralight. Prefers the daemon's `kind`; falls
    31	    /// back to the ATQA/SAK rule (SAK 0x00 AND ATQA 0x0044). A magic/blank Classic
    32	    /// reports SAK 0x00 with ATQA 0x0004 and must NOT be treated as NTAG.
    33	    var isNTAG: Bool {
    34	        if let kind { return kind == "ntag" }
    35	        let a = (atqa ?? "").replacingOccurrences(of: " ", with: "").lowercased()
    36	        return sak == 0x00 && a == "0044"
    37	    }
    38	}
    39	
    40	struct DecodeResult: Codable, Equatable {
    41	    let uid: String
    42	    let atqa: String
    43	    let sak: Int
    44	    let sectors: Int
    45	    let recovered: Int
    46	    let attempts: Int?                  // auth attempts the scan spent
    47	    let exhausted: Bool?               // true if the scan budget ran out before a full search
    48	    /// True when the walk stopped on a cooperative cancel rather than finishing; the
    49	    /// blocks/keys below are then whatever partial image was gathered so far. Optional
    50	    /// so an older daemon that omits it decodes to nil.
    51	    let cancelled: Bool?
    52	    /// Sectors whose OTHER key slot (A or B) was not readable and was mirrored from the
    53	    /// recovered slot: sector-index string -> the assumed slot ("A" / "B"). The mirrored
    54	    /// value is a guess, not a read key. Optional (older daemons omit it). JSON key is
    55	    /// `assumed_keys`, matching the daemon contract (like `walk_total` on EngineEvent).
    56	    let assumed_keys: [String: String]?
    57	    let blocks: [String: String?]      // block index -> hex, or null if unreadable
    58	    let keys: [String: [String]?]      // sector -> [keytype, keyhex], or null
    59	}
    60	
    61	/// Result of a write_mfd / clone. `present` is false when no card was on the
    62	/// reader; otherwise `wrote` counts blocks written and `failed` lists block
    63	/// indices that could not be written.
    64	struct WriteResult: Codable, Sendable {
    65	    let present: Bool
    66	    let wrote: Int?
    67	    let failed: [Int]?
    68	    let error: String?      // set when the daemon aborted (wrong / swapped target card)
    69	}
    70	
    71	/// Result of a format (factory reset). `present` is false when no card.
    72	struct FormatResult: Codable, Sendable {
    73	    let present: Bool
    74	    let formatted: Int?
    75	    let failed: [Int]?
    76	    let error: String?      // set when the daemon aborted (wrong / swapped target card)
    77	}
    78	
    79	/// Result of an NTAG / Ultralight (SAK 0x00) page dump. A page value is null when it
    80	/// could not be read (a password-protected page that NAKs) - the readable pages are
    81	/// still returned. `type` is the detected TagSpecificType name (nil when GET_VERSION did
    82	/// not resolve one). `version` / `signature` / `counters` are present only where the chip
    83	/// exposes them (UL EV1 / NTAG21x); the X7 daemon omits type/version/signature/counters.
    84	/// `counters` is keyed by counter index so a failed counter is absent-at-its-index rather
    85	/// than shifting later values.
    86	struct NtagResult: Codable, Sendable {
    87	    let present: Bool
    88	    let uid: String?
    89	    let sak: Int?
    90	    let pages: [String: String?]?   // page index -> 4-byte hex, or null if unreadable
    91	    var type: String? = nil         // detected TagSpecificType name (e.g. "NTAG_215")
    92	    var version: String? = nil      // 8-byte GET_VERSION hex
    93	    var signature: String? = nil    // originality signature hex
    94	    var counters: [String: Int]? = nil   // counter index -> value
    95	}
    96	
    97	/// One NTAG page row for the page table. `locked` marks a page that could not be read
    98	/// (password-protected): its bytes are shown as a placeholder rather than real data.
    99	struct NtagPage: Identifiable, Equatable {
   100	    let index: Int
   265	                }
   266	            }
   267	            if model.capabilities.emulate {
   268	                EmulateToggle()
   269	                // A held Classic document (source) OR a live NTAG / Ultralight page dump
   270	                // can be loaded into a slot to emulate.
   271	                if model.source != nil || !model.pages.isEmpty { LoadToSlotMenu() }
   272	            }
   273	            // Firmware update (DFU), gated on the device advertising it: the X7 has
   274	            // dfu:false and never shows it. Opening the sheet reads the current + latest
   275	            // firmware. Disabled while any device op owns the reader.
   276	            if model.capabilities.dfu {
   277	                // Reachable when the reader is online OR the device is stuck in the
   278	                // bootloader (deviceInDFU) - the latter is exactly when flash-recovery is needed.
   279	                ActionButton(title: l.t("firmware"), icon: "arrow.up.circle",
   280	                             enabled: (model.readerOnline || model.deviceInDFU) && !busy
   281	                                 && !model.slotBusy && !model.emulating) {
   282	                    model.flashingSheet = true
   283	                    Task { await model.checkFirmware() }
   284	                }
   285	            }
   286	            Spacer()
   287	            if model.decoding {
   288	                if let p = model.decodeProgress {

exec
/bin/zsh -lc "sed -n '1,180p' exploration/external-critique/2026-07-21-codex-ble-review.md; rg -n \"CRITICAL|HIGH|MED|LOW|Findings|finding\" exploration/external-critique/2026-07-21-codex-ble-review.md | tail -n 80" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
Reading additional input from stdin...
2026-07-21T04:27:19.046394Z ERROR codex_models_manager::cache: failed to load models cache: missing field `supports_reasoning_summaries` at line 88 column 5
OpenAI Codex v0.144.1
--------
workdir: /Users/tuan/Claude/Tenor/tenor-rekey
model: gpt-5.6-sol
provider: openai
approval: never
sandbox: read-only
reasoning effort: high
reasoning summaries: none
session id: 019f82ed-a41c-7063-a541-6c434bb43254
--------
user
You are a rigorous macOS/Swift + Python reviewer acting as the founder's proxy gate on an UNCOMMITTED change to the tenor/rekey app. The app drives a Chameleon Ultra (RFID) via a Python daemon (chameleon_d.py) that speaks a framed command protocol over a transport. This change adds Bluetooth LE support.

ARCHITECTURE (verify it is sound, do not assume): the Chameleon speaks the SAME protocol over BLE's Nordic UART Service (NUS) as over USB-CDC. The daemon's transport (probe/chameleon/chameleon_com.py, open()) already supports a `tcp:HOST:PORT` port. So BLE is done as: the Swift app (BLEManager.swift) owns the CoreBluetooth radio (the app process holds Bluetooth TCC), connects NUS, and runs a loopback NWListener TCP bridge that relays bytes between the daemon's TCP socket and the BLE RX(write 6E400002)/TX(notify 6E400003) characteristics. On connect it writes a 10-byte handshake to RX. The daemon is then spawned with CHAMELEON_PORT=tcp:127.0.0.1:<port> (existing portOverride mechanism), so it opens the tcp bridge and its whole protocol stack is reused unchanged.

Inspect the uncommitted diff and the new file yourself:
  git --no-pager diff -- app
  cat app/Sources/Engine/BLEManager.swift
Read context as needed: app/Sources/AppModel.swift (ble, bleConnect, pinnedPortPresent, swapDevice, monitor, connectManual, canChangeDevice), app/Sources/Views/ConnectView.swift (bluetoothSection), app/Sources/Engine/DeviceBridge.swift (CHAMELEON_PORT env), probe/chameleon/chameleon_com.py (open() tcp path, thread_data_receive/transfer), app/project.yml + app/tools/tenorrekey.entitlements (Info.plist key + entitlement).

Give ONLY real, actionable findings (file:line, severity CRITICAL/HIGH/MED/LOW, concrete failure scenario, minimal fix). Skip praise and style nits unless they cause bugs. Bounded questions:
1. BLEManager CoreBluetooth correctness: the central is created with queue: .main and delegates are `nonisolated func` bodies wrapped in `MainActor.assumeIsolated` - is that actually safe (are these callbacks guaranteed on the main thread)? Any connectContinuation double-resume or leak across didConnect/didFail/didDisconnect/timeout/radio-off. Any force-unwrap that can crash on a hardware race.
2. TCP bridge (NWListener/NWConnection): is the byte relay correct and lossless in BOTH directions? MTU chunking of TCP->BLE writes (maximumWriteValueLength, .withoutResponse); does a >MTU daemon frame get split correctly; is there flow-control/backpressure or ordering risk; is exactly one inbound connection handled; are listener/connection torn down without leak or deadlock; can teardown race the receive loop.
3. BLE lifecycle in AppModel: bleConnect (pre-disconnect on switch, build tcp descriptor, swapDevice), the swapDevice teardown `if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }`, and pinnedPortPresent returning ble.isConnected for a tcp: pin. Trace: USB->BLE, BLE->USB, BLE-A->BLE-B, tapping the same BLE device twice, a BLE link that drops mid-session (didDisconnectPeripheral), a connect that throws mid-way. Can a live link be killed, or a dead one leave the app stuck on a BLE descriptor with no recovery?
4. Daemon side: opening tcp:127.0.0.1:<port> - any framing/timeout/threading mismatch vs serial; does the DFU fail-closed guard in _connect wrongly trip on a tcp: dev; does anything require a change that was missed. Confirm the reused protocol path is actually transport-agnostic here.
5. macOS reality: with NSBluetoothAlwaysUsageDescription in Info.plist + com.apple.security.device.bluetooth entitlement, will CoreBluetooth in the APP process get permission (vs a child python being denied)? Is there any residual reason a real Chameleon over BLE would fail to connect through this path (permission, MTU, handshake, the bridge, the daemon open)? Name it precisely.

Be blunt. If sound, say so plainly. If you would bullshit, do not bother.
codex
I’ll trace the diff and the specific lifecycle and transport paths you named, then report only reproducible failure modes with minimal fixes.
exec
/bin/zsh -lc "git status --short && git --no-pager diff -- app && sed -n '1,320p' app/Sources/Engine/BLEManager.swift" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 3307ms:
2026-07-21 11:27:33.137 xcodebuild[45798:3375052]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:33.271 xcodebuild[45798:3375051] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-UZcLyqX8' (errno=Operation not permitted)
2026-07-21 11:27:33.702 xcodebuild[45800:3375062]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:33.833 xcodebuild[45800:3375061] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
 M app/Sources/AppModel.swift
 M app/Sources/Brand/L10n.swift
 M app/Sources/Views/ConnectView.swift
 M app/project.yml
 M app/tools/tenorrekey.entitlements
?? AUDIT_CHAMELEON.md
?? DEVICE_COMPAT.md
?? SPEC_CHAMELEON.md
?? X7_AUDIT_FINDINGS.md
?? app/Sources/Engine/BLEManager.swift
?? exploration/
?? probe/native/darkside/
?? probe/x7_reader.py
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-5ls1e6fx' (errno=Operation not permitted)
2026-07-21 11:27:34.814 xcodebuild[45807:3375103]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:34.943 xcodebuild[45807:3375102] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-KXNcJgDZ' (errno=Operation not permitted)
2026-07-21 11:27:35.376 xcodebuild[45810:3375117]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:27:35.507 xcodebuild[45810:3375116] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/app/Sources/AppModel.swift b/app/Sources/AppModel.swift
index c2a0610..6f0a8e4 100644
--- a/app/Sources/AppModel.swift
+++ b/app/Sources/AppModel.swift
@@ -39,6 +39,11 @@ final class AppModel {
     var detectedDevices: [DeviceDescriptor] = []
     /// Every enumerated USB serial port (the manual-connect list).
     var serialPorts: [SerialPortInfo] = []
+    /// The Bluetooth LE radio for a wireless Chameleon link. Nested @Observable, so a view
+    /// that reads `model.ble.state` / `model.ble.devices` re-renders on its changes. A BLE
+    /// link is never auto-detected (it is not on the USB bus); it is opened only by the
+    /// explicit `bleConnect` from the Connect surface.
+    let ble = BLEManager()
 
     /// The working DOCUMENT: the image produced by a decode or loaded from a file.
     /// It is what the canvas shows, what Save writes out, and what Write clones onto
@@ -252,6 +257,11 @@ final class AppModel {
     /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
     private func pinnedPortPresent() -> Bool {
         guard let pinned = descriptor.portOverride else { return false }
+        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
+        // serial-port scan below would report it absent and the monitor would swap away from a
+        // live wireless link. It is "present" for as long as the manager holds the link; once
+        // the BLE link drops, this reads false and a normal hot-swap is allowed.
+        if pinned.hasPrefix("tcp:") { return ble.isConnected }
         return USBProbe.serialPorts().contains { $0.path == pinned }
     }
 
@@ -296,6 +306,47 @@ final class AppModel {
         refreshConnectLists()
     }
 
+    // ---- Bluetooth LE connect ----------------------------------------------
+
+    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
+    /// surface drives the radio without importing the manager directly.
+    func bleStartScan() { ble.startScan() }
+    func bleStopScan() { ble.stopScan() }
+
+    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
+    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
+    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
+    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
+    ///
+    /// A device advertising the Nordic DFU service is refused here (the view also hides its
+    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
+    /// be recovered over USB.
+    func bleConnect(_ id: String) async {
+        guard canChangeDevice else { return }
+        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
+        // Clean switch between BLE devices: drop any prior link before opening a new one.
+        if ble.isConnected { ble.disconnect() }
+        do {
+            let port = try await ble.connect(id)
+            let base = DeviceRegistry.chameleonUltra
+            let desc = DeviceDescriptor(
+                id: "chameleon-ble:\(id)",
+                family: base.family,
+                displayName: base.displayName,
+                daemonScript: base.daemonScript,
+                probeSubdir: base.probeSubdir,
+                usbMatch: base.usbMatch,
+                capabilities: base.capabilities,
+                portOverride: "tcp:127.0.0.1:\(port)")
+            await swapDevice(to: desc)
+            ble.stopScan()
+            refreshConnectLists()
+        } catch {
+            lastError = "\(error)"
+            ble.disconnect()
+        }
+    }
+
     /// Bring up the daemon for the active `descriptor`: read device info + key counts,
     /// then sample the reader. Shared by the first connect and a hot-swap, so both
     /// paths land the same state (info, capabilities via `info`, reader/card status).
@@ -384,6 +435,10 @@ final class AppModel {
         guard !swapping else { return }
         swapping = true
         defer { swapping = false }              // released however this returns
+        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
+        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
+        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
+        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
         let old = bridge
         bridge = nil                            // detach: no path can obtain the old bridge now
         descriptor = found
@@ -396,6 +451,12 @@ final class AppModel {
         }
         await old?.shutdown()                   // bounded terminate + drain of the old daemon
         await openCurrentDevice()               // creates + brings up the new bridge
+        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
+        // now that the old daemon is gone and the new device is open. Gated on the NEW device
+        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
+        // the old link and brought up the new one before this swap, so disconnecting here
+        // would kill the freshly-connected link. Idempotent.
+        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
     }
 
     /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
diff --git a/app/Sources/Brand/L10n.swift b/app/Sources/Brand/L10n.swift
index 0ac5470..061766e 100644
--- a/app/Sources/Brand/L10n.swift
+++ b/app/Sources/Brand/L10n.swift
@@ -264,6 +264,12 @@ final class L10n {
         "no_device_hint":     ["vi": "chưa thấy thiết bị nào. kiểm tra cáp là loại truyền dữ liệu (không phải cáp chỉ sạc) và thử cổng usb khác.", "en": "no device detected - check the cable is a data cable, not charge-only, and try another usb port.", "zh": "未检测到设备 - 请确认使用的是数据线而非仅充电线，并换一个 usb 口试试。", "ja": "デバイスが見つかりません - ケーブルが充電専用ではなくデータ用か確認し、別の usb ポートを試してください。"],
         "unrecognized_port_hint": ["vi": "tìm thấy cổng serial nhưng không phải đầu đọc nào được nhận diện. nếu đây là chameleon, hãy kết nối thủ công, hoặc kiểm tra bạn đang dùng cáp truyền dữ liệu.", "en": "found serial ports but none is a recognized reader - if this is your chameleon connect it manually, or check you are using a data cable.", "zh": "找到串口但没有可识别的读卡器 - 若这是你的 chameleon，请手动连接，或确认使用的是数据线。", "ja": "シリアルポートは見つかりましたが認識できる読み取り機がありません - これがお使いの chameleon なら手動で接続するか、データ用ケーブルか確認してください。"],
         "bluetooth":          ["vi": "bluetooth", "en": "bluetooth", "zh": "蓝牙", "ja": "bluetooth"],
-        "bluetooth_later":    ["vi": "sẽ thêm ở bản cập nhật sau", "en": "added in a later update", "zh": "将在后续更新中加入", "ja": "今後のアップデートで追加"],
+        "bt_scan":            ["vi": "quét", "en": "scan", "zh": "扫描", "ja": "スキャン"],
+        "bt_stop":            ["vi": "dừng", "en": "stop", "zh": "停止", "ja": "停止"],
+        "bt_none":            ["vi": "chưa thấy chameleon nào qua bluetooth", "en": "no chameleon found over bluetooth", "zh": "未通过蓝牙发现 chameleon", "ja": "bluetooth で chameleon が見つかりません"],
+        "bt_off":             ["vi": "bluetooth đang tắt - bật lên để kết nối không dây", "en": "bluetooth is off - turn it on to connect wirelessly", "zh": "蓝牙已关闭 - 打开后即可无线连接", "ja": "bluetooth がオフです - オンにすると無線で接続できます"],
+        "bt_unauthorized":    ["vi": "chưa cấp quyền bluetooth - bật trong cài đặt hệ thống > quyền riêng tư & bảo mật > bluetooth", "en": "bluetooth permission is off - enable it in system settings > privacy & security > bluetooth", "zh": "未授予蓝牙权限 - 请在系统设置 > 隐私与安全性 > 蓝牙中启用", "ja": "bluetooth の権限がオフです - システム設定 > プライバシーとセキュリティ > bluetooth で有効にしてください"],
+        "bt_unsupported":     ["vi": "máy mac này không có bluetooth le", "en": "this mac has no bluetooth le", "zh": "此 mac 不支持 bluetooth le", "ja": "この mac には bluetooth le がありません"],
+        "bt_dfu_note":        ["vi": "cập nhật firmware qua usb, không qua bluetooth", "en": "update firmware over usb, not bluetooth", "zh": "请通过 usb 更新固件，而非蓝牙", "ja": "ファームウェアは bluetooth ではなく usb で更新してください"],
     ]
24:Give ONLY real, actionable findings (file:line, severity CRITICAL/HIGH/MED/LOW, concrete failure scenario, minimal fix). Skip praise and style nits unless they cause bugs. Bounded questions:
3128:probe/test_chameleon.py:2679:# 33c. device identity binding (finding 6): snapshot the DFU ports BEFORE the reboot, then

exec
/bin/zsh -lc "rg -n -A22 -B8 \"initWithDelegate.*queue|queue.*dispatch queue|delegate.*queue\" /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBCentralManager.h | head -n 120; rg -n -A12 -B5 \"@property.*delegate|delegate\" /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/System/Library/Frameworks/CoreBluetooth.framework/Headers/CBPeripheral.h | head -n 80; rg -n \"MainActor.assumeIsolated\" /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift -g '*.swiftinterface' | head -n 30" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
103- *  @discussion     Returns a boolean value representing the support for the provided features.
104- *
105- */
106-+ (BOOL)supportsFeatures:(CBCentralManagerFeature)features CB_CM_API_AVAILABLE NS_SWIFT_NAME(supports(_:));
107-
108-- (instancetype)init;
109-
110-/*!
111: *  @method initWithDelegate:queue:
112- *
113- *  @param delegate The delegate that will receive central role events.
114: *  @param queue    The dispatch queue on which the events will be dispatched.
115- *
116- *  @discussion     The initialization call. The events of the central role will be dispatched on the provided queue.
117- *                  If <i>nil</i>, the main queue will be used.
118- *
119- */
120-- (instancetype)initWithDelegate:(nullable id<CBCentralManagerDelegate>)delegate
121-						   queue:(nullable dispatch_queue_t)queue;
122-
123-/*!
124: *  @method initWithDelegate:queue:options:
125- *
126- *  @param delegate The delegate that will receive central role events.
127: *  @param queue    The dispatch queue on which the events will be dispatched.
128- *  @param options  An optional dictionary specifying options for the manager.
129- *
130- *  @discussion     The initialization call. The events of the central role will be dispatched on the provided queue.
131- *                  If <i>nil</i>, the main queue will be used.
132- *
133- *	@seealso		CBCentralManagerOptionShowPowerAlertKey
134- *	@seealso		CBCentralManagerOptionRestoreIdentifierKey
135- *
136- */
137-- (instancetype)initWithDelegate:(nullable id<CBCentralManagerDelegate>)delegate
138-						   queue:(nullable dispatch_queue_t)queue
139-						 options:(nullable NSDictionary<NSString *, id> *)options NS_AVAILABLE(10_9, 7_0) NS_DESIGNATED_INITIALIZER;
140-
141-/*!
142- *  @method retrievePeripheralsWithIdentifiers:
143- *
144- *  @param identifiers	A list of <code>NSUUID</code> objects.
145- *
146- *  @discussion			Attempts to retrieve the <code>CBPeripheral</code> object(s) with the corresponding <i>identifiers</i>.
147- *
148- *	@return				A list of <code>CBPeripheral</code> objects.
149- *
53- */
54-NS_CLASS_AVAILABLE(10_7, 5_0)
55-CB_EXTERN_CLASS @interface CBPeripheral : CBPeer
56-
57-/*!
58: *  @property delegate
59- *
60: *  @discussion The delegate object that will receive peripheral events.
61- */
62:@property(weak, nonatomic, nullable) id<CBPeripheralDelegate> delegate;
63-
64-/*!
65- *  @property name
66- *
67- *  @discussion The name of the peripheral.
68- */
69-@property(retain, readonly, nullable) NSString *name;
70-
71-/*!
72- *  @property RSSI
73- *
74- *  @discussion The most recently read RSSI, in decibels.
--
202- *  @param enabled			Whether or not notifications/indications should be enabled.
203- *  @param characteristic	The characteristic containing the client characteristic configuration descriptor.
204- *
205- *  @discussion				Enables or disables notifications/indications for the characteristic value of <i>characteristic</i>. If <i>characteristic</i>
206- *							allows both, notifications will be used.
207: *                          When notifications/indications are enabled, updates to the characteristic value will be received via delegate method 
208- *                          @link peripheral:didUpdateValueForCharacteristic:error: @/link. Since it is the peripheral that chooses when to send an update,
209- *                          the application should be prepared to handle them as long as notifications/indications remain enabled.
210- *
211- *  @see					peripheral:didUpdateNotificationStateForCharacteristic:error:
212- *  @seealso                CBConnectPeripheralOptionNotifyOnNotificationKey
213- */
214-- (void)setNotifyValue:(BOOL)enabled forCharacteristic:(CBCharacteristic *)characteristic;
215-
216-/*!
217- *  @method discoverDescriptorsForCharacteristic:
218- *
219- *  @param characteristic	A GATT characteristic.

exec
/bin/zsh -lc "rg -n \"def info\\(\" probe/chameleon_d.py; nl -ba probe/chameleon_d.py | sed -n '650,730p'; rg -n \"def get_slot_info|get_app_version|get_device_model\" probe/chameleon/chameleon_cmd.py | head -n 30" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
635:    def info(self, p):
   650	        # device with no reader mode (Lite) -> reader:false. `_ensure_reader` runs
   651	        # first so hf14a_scan cannot fail with DEVICE_MODE_ERROR and be mistaken
   652	        # for a genuine no-card. Errors here are normal, so we answer, not raise.
   653	        try:
   654	            c = self._connect(p.get("port"))
   655	            self._ensure_reader(c)
   656	            tags = c.hf14a_scan()
   657	        except UnexpectedResponseError:
   658	            return {"present": False, "reader": True}     # field up, no tag
   659	        except (RuntimeError, ImportError):
   660	            return {"present": False, "reader": False}    # no device / no reader mode
   661	        except _DEAD:
   662	            self._drop()
   663	            return {"present": False, "reader": False}    # device gone
   664	        if not tags:
   665	            return {"present": False, "reader": True}
   666	        t = tags[0]
   667	        sak = t["sak"]
   668	        atqa = t["atqa"][::-1]                             # wire (LSB-first) -> semantic
   669	        return {"present": True, "reader": True,
   670	                "uid": hx(t["uid"]), "atqa": hx(atqa),
   671	                "sak": sak[0], "ats": (hx(t["ats"]) if t["ats"] else ""),
   672	                "kind": card_kind(sak[0], atqa)}
   673	
   674	    def slots_list(self, p):
   675	        c = self._connect(p.get("port"))
   676	        info = c.get_slot_info()             # 8 x {'hf': type, 'lf': type}
   677	        enabled = c.get_enabled_slots()      # 8 x {'hf': bool, 'lf': bool}
   678	        nicks = c.get_all_slot_nicks()       # 8 x {'hf': str,  'lf': str}
   679	        active = c.get_active_slot()         # fw index 0..7
   680	        slots = []
   681	        for i in range(8):
   682	            slots.append({
   683	                "index": i,
   684	                "active": i == active,
   685	                "hf": {"type": _type_name(info[i]["hf"]),
   686	                       "enabled": bool(enabled[i]["hf"]),
   687	                       "nick": nicks[i]["hf"]},
   688	                "lf": {"type": _type_name(info[i]["lf"]),
   689	                       "enabled": bool(enabled[i]["lf"]),
   690	                       "nick": nicks[i]["lf"]},
   691	            })
   692	        return {"slots": slots}
   693	
   694	    def slot_select(self, p):
   695	        c = self._connect(p.get("port"))
   696	        slot = int(p["slot"])                # 0-based, matches slots_list index
   697	        c.set_active_slot(SlotNumber.from_fw(slot))
   698	        return {"slot": slot}
   699	
   700	    def mf_read_block(self, p):
   701	        c = self._connect(p.get("port"))
   702	        self._ensure_reader(c)
   703	        block = int(p["block"])
   704	        kt = (p.get("keytype") or "A").upper()
   705	        key = bytes.fromhex(p["key"].replace(" ", ""))
   706	        mkt = MfcKeyType.A if kt == "A" else MfcKeyType.B
   707	        data = c.mf1_read_one_block(block, mkt, key)
   708	        return {"block": block, "data": hx(data)}
   709	
   710	    # ---- NTAG / Ultralight read (reader-mode 14a raw transceive) ----------------
   711	
   712	    # Consecutive NAKs on an UNKNOWN-size tag that mean the READ has walked off the end of
   713	    # memory (not merely a locked page): the fallback stops and drops this terminating run.
   714	    NTAG_OFF_END_MARGIN = 4
   715	    # Hard cap on any page dump so an oddly-behaving tag cannot spin (NTAG216 = 231 pages).
   716	    NTAG_PAGE_CAP = 256
   717	
   718	    @staticmethod
   719	    def _14a_raw(c, cmd, check_crc=True):
   720	        """One reader-mode ISO14443A transceive (auto-select + append CRC), returning the
   721	        tag's response bytes (empty on a NAK / no answer / transient fault). Mirrors the
   722	        reference GUI's send14ARaw defaults; the emulator-independent page read the Ultralight
   723	        scan uses. The undecorated hf14a_raw can raise UnexpectedResponseError (an unsupported
   724	        command / device-mode status), TimeoutError (a slow / mid-read stall), or ValueError
   725	        (a malformed frame) - all are collapsed to empty data so a single unreadable page is
   726	        a null page, not a failed daemon request. A truly dead port raises OSError from the
   727	        transport, which handle() catches to drop + reconnect."""
   728	        opts = {"activate_rf_field": 1, "wait_response": 1, "append_crc": 1,
   729	                "auto_select": 1, "keep_rf_field": 0,
   730	                "check_response_crc": 1 if check_crc else 0}
29:    def get_app_version(self):
841:    def get_slot_info(self):
1675:    def get_device_model(self):
1836:    ver = cml.get_app_version()

exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '625,655p'; nl -ba probe/chameleon/chameleon_cmd.py | sed -n '20,85p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   625	            raise RuntimeError("device has no reader mode (Lite?): %s" % e)
   626	        self._reader_mode = True
   627	
   628	    def emit(self, obj):
   629	        with self._emit_lock:
   630	            self._out.write(json.dumps(obj) + "\n")
   631	            self._out.flush()
   632	
   633	    # ---- methods -----------------------------------------------------------
   634	
   635	    def info(self, p):
   636	        c = self._connect(p.get("port"))
   637	        model = c.get_device_model()             # 0 = Ultra, 1 = Lite
   638	        major, minor = c.get_app_version()
   639	        git = c.get_git_version()
   640	        chip = c.get_device_chip_id()
   641	        family = "chameleon-ultra" if model == 0 else "chameleon-lite"
   642	        name = "Chameleon Ultra" if model == 0 else "Chameleon Lite"
   643	        return {"family": family, "model": name, "serial": chip,
   644	                "hw": "app %d.%d (%s)" % (major, minor, git),
   645	                "capabilities": _capabilities(model, self.crack)}
   646	
   647	    def poll(self, p):
   648	        # `reader` reports whether the Chameleon is connected (vs `present`, a card
   649	        # in the field). No tag -> field is up, so reader:true. A dead port or a
   650	        # device with no reader mode (Lite) -> reader:false. `_ensure_reader` runs
   651	        # first so hf14a_scan cannot fail with DEVICE_MODE_ERROR and be mistaken
   652	        # for a genuine no-card. Errors here are normal, so we answer, not raise.
   653	        try:
   654	            c = self._connect(p.get("port"))
   655	            self._ensure_reader(c)
    20	    """
    21	
    22	    def __init__(self, chameleon: chameleon_com.ChameleonCom):
    23	        """
    24	        :param chameleon: chameleon instance, @see chameleon_device.Chameleon
    25	        """
    26	        self.device = chameleon
    27	
    28	    @expect_response(Status.SUCCESS)
    29	    def get_app_version(self):
    30	        """
    31	            Get firmware version number(application)
    32	        """
    33	        resp = self.device.send_cmd_sync(Command.GET_APP_VERSION)
    34	        if resp.status == Status.SUCCESS:
    35	            resp.parsed = struct.unpack('!BB', resp.data)
    36	        # older protocol, must upgrade!
    37	        if resp.status == 0 and resp.data == b'\x00\x01':
    38	            print("Chameleon does not understand new protocol. Please update firmware")
    39	            return chameleon_com.Response(cmd=Command.GET_APP_VERSION,
    40	                                          status=Status.NOT_IMPLEMENTED)
    41	        return resp
    42	
    43	    @expect_response(Status.SUCCESS)
    44	    def get_device_chip_id(self):
    45	        """
    46	            Get device chip id
    47	        """
    48	        resp = self.device.send_cmd_sync(Command.GET_DEVICE_CHIP_ID)
    49	        if resp.status == Status.SUCCESS:
    50	            resp.parsed = resp.data.hex()
    51	        return resp
    52	
    53	    @expect_response(Status.SUCCESS)
    54	    def get_device_address(self):
    55	        """
    56	            Get device address
    57	        """
    58	        resp = self.device.send_cmd_sync(Command.GET_DEVICE_ADDRESS)
    59	        if resp.status == Status.SUCCESS:
    60	            resp.parsed = resp.data.hex()
    61	        return resp
    62	
    63	    @expect_response(Status.SUCCESS)
    64	    def get_git_version(self):
    65	        resp = self.device.send_cmd_sync(Command.GET_GIT_VERSION)
    66	        if resp.status == Status.SUCCESS:
    67	            resp.parsed = resp.data.decode('utf-8')
    68	        return resp
    69	
    70	    @expect_response(Status.SUCCESS)
    71	    def get_device_mode(self):
    72	        resp = self.device.send_cmd_sync(Command.GET_DEVICE_MODE)
    73	        if resp.status == Status.SUCCESS:
    74	            resp.parsed, = struct.unpack('!?', resp.data)
    75	        return resp
    76	
    77	    def is_device_reader_mode(self) -> bool:
    78	        """
    79	            Get device mode, reader or tag.
    80	
    81	        :return: True is reader mode, else tag mode
    82	        """
    83	        return self.get_device_mode()
    84	
    85	    # Note: Will return NOT_IMPLEMENTED if one tries to set reader mode on Lite

codex
1. **CRITICAL** - [probe/chameleon_d.py:1924](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon_d.py:1924), [RootView.swift:276](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:276): DFU remains exposed over BLE and can flash the wrong physical device. With BLE device A active and USB device B attached, `dfu_flash()` enumerates B, but `_connect(cdc)` returns the cached BLE command object and reads A's model. It then drops A, reboots B, and flashes B with A's package. Minimal fix: fail immediately in `dfu_flash()` when `_port` starts with `tcp:`, before downloading or touching hardware. Also hide and guard firmware actions while the active descriptor is BLE.

2. **HIGH** - [BLEManager.swift:150](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:150), [BLEManager.swift:369](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:369): delegate failures are not scoped to the current peripheral or connection attempt. During BLE-A to BLE-B, canceling A is asynchronous. After B installs its continuation, A's `didDisconnectPeripheral` sees a non-nil continuation and fails B. Tapping A twice has the same race. Stale `didFailToConnect`, discovery, notification, listener-ready, and timeout callbacks can likewise resolve the new attempt. Minimal fix: guard every callback against the active peripheral and an attempt generation. Wait for A's disconnect callback before reconnecting the same peripheral. A canceled timeout task must return on `CancellationError` instead of continuing to `failConnect`.

3. **HIGH** - [BLEManager.swift:259](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:259), [BLEManager.swift:274](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:274): TCP-to-BLE is not lossless. `writeToBLE` emits every MTU chunk using `.withoutResponse` without checking `canSendWriteWithoutResponse`. CoreBluetooth explicitly makes delivery best-effort once that becomes false, so large command frames can lose chunks and fail LRC parsing. Independent `Task` submissions for writing and rearming the TCP receive also do not guarantee byte ordering. Minimal fix: maintain one ordered outgoing buffer, stop reading TCP while BLE is backpressured, drain only while `canSendWriteWithoutResponse`, and resume from `peripheralIsReady(toSendWriteWithoutResponse:)`. Alternatively serialize `.withResponse` chunks and advance from `didWriteValueFor`. Handle each receive completion in one MainActor task.

4. **HIGH** - [AppModel.swift:324](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:324): the 15-second BLE connection phase does not reserve the device-switch state. `canChangeDevice` remains true and the monitor remains active. A second BLE selection returns `busy`, then its catch calls `ble.disconnect()` and aborts the first attempt. A manual or monitor-driven USB swap can complete while BLE is connecting, after which the original BLE task unexpectedly swaps back. Minimal fix: add a `bleConnecting` state, include it in `canChangeDevice`, `connecting`, and the monitor guard, and hold it from before pre-disconnect through `swapDevice`.

5. **MED** - [BLEManager.swift:421](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:421): connection succeeds before the handshake write succeeds. The bridge starts immediately after `writeValue(..., .withResponse)`, but `didWriteValueFor` is not implemented. A rejected or failed GATT write still produces a live TCP descriptor and starts the daemon, which then times out. Minimal fix: start the bridge only after `didWriteValueFor` confirms the RX handshake write completed without error; fail the connection otherwise.

6. **MED** - [chameleon_com.py:164](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon/chameleon_com.py:164), [chameleon_com.py:209](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon/chameleon_com.py:209): socket teardown is incorrect. An orderly TCP close returns `b""`, which the receive thread treats as an empty read and immediately loops forever. Also, `socket.shutdown()` is called without the required argument, so it raises and skips the explicit `close()`. A dropped BLE link can leave a hot-spinning receive thread and cached dead command object. Minimal fix: treat zero-length `recv()` as EOF and close/drop the transport; call `shutdown(socket.SHUT_RDWR)` and put `close()` in a separate `finally`.

7. **LOW** - [BLEManager.swift:190](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:190), [BLEManager.swift:214](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:214), [BLEManager.swift:244](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:244): the listener and connection handlers form retain cycles by capturing their owning `listener` or `conn` strongly. Repeated reconnects can retain canceled Network objects and their closures. Minimal fix: weak-capture those objects or clear all state, new-connection, and receive handlers before cancellation.

No additional finding on the requested fundamentals: CoreBluetooth callbacks are dispatched on the central's supplied `.main` queue, so the `MainActor.assumeIsolated` usage is valid; there are no force unwraps; MTU fragmentation itself is accepted by the firmware parser; `_connect` does not mistake a `tcp:` endpoint for DFU; and app-process CoreBluetooth is the correct macOS TCC boundary. Real hardware behavior remains unverified.
tokens used
137,059
1. **CRITICAL** - [probe/chameleon_d.py:1924](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon_d.py:1924), [RootView.swift:276](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:276): DFU remains exposed over BLE and can flash the wrong physical device. With BLE device A active and USB device B attached, `dfu_flash()` enumerates B, but `_connect(cdc)` returns the cached BLE command object and reads A's model. It then drops A, reboots B, and flashes B with A's package. Minimal fix: fail immediately in `dfu_flash()` when `_port` starts with `tcp:`, before downloading or touching hardware. Also hide and guard firmware actions while the active descriptor is BLE.

2. **HIGH** - [BLEManager.swift:150](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:150), [BLEManager.swift:369](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:369): delegate failures are not scoped to the current peripheral or connection attempt. During BLE-A to BLE-B, canceling A is asynchronous. After B installs its continuation, A's `didDisconnectPeripheral` sees a non-nil continuation and fails B. Tapping A twice has the same race. Stale `didFailToConnect`, discovery, notification, listener-ready, and timeout callbacks can likewise resolve the new attempt. Minimal fix: guard every callback against the active peripheral and an attempt generation. Wait for A's disconnect callback before reconnecting the same peripheral. A canceled timeout task must return on `CancellationError` instead of continuing to `failConnect`.

3. **HIGH** - [BLEManager.swift:259](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:259), [BLEManager.swift:274](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:274): TCP-to-BLE is not lossless. `writeToBLE` emits every MTU chunk using `.withoutResponse` without checking `canSendWriteWithoutResponse`. CoreBluetooth explicitly makes delivery best-effort once that becomes false, so large command frames can lose chunks and fail LRC parsing. Independent `Task` submissions for writing and rearming the TCP receive also do not guarantee byte ordering. Minimal fix: maintain one ordered outgoing buffer, stop reading TCP while BLE is backpressured, drain only while `canSendWriteWithoutResponse`, and resume from `peripheralIsReady(toSendWriteWithoutResponse:)`. Alternatively serialize `.withResponse` chunks and advance from `didWriteValueFor`. Handle each receive completion in one MainActor task.

4. **HIGH** - [AppModel.swift:324](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:324): the 15-second BLE connection phase does not reserve the device-switch state. `canChangeDevice` remains true and the monitor remains active. A second BLE selection returns `busy`, then its catch calls `ble.disconnect()` and aborts the first attempt. A manual or monitor-driven USB swap can complete while BLE is connecting, after which the original BLE task unexpectedly swaps back. Minimal fix: add a `bleConnecting` state, include it in `canChangeDevice`, `connecting`, and the monitor guard, and hold it from before pre-disconnect through `swapDevice`.

5. **MED** - [BLEManager.swift:421](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:421): connection succeeds before the handshake write succeeds. The bridge starts immediately after `writeValue(..., .withResponse)`, but `didWriteValueFor` is not implemented. A rejected or failed GATT write still produces a live TCP descriptor and starts the daemon, which then times out. Minimal fix: start the bridge only after `didWriteValueFor` confirms the RX handshake write completed without error; fail the connection otherwise.

6. **MED** - [chameleon_com.py:164](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon/chameleon_com.py:164), [chameleon_com.py:209](/Users/tuan/Claude/Tenor/tenor-rekey/probe/chameleon/chameleon_com.py:209): socket teardown is incorrect. An orderly TCP close returns `b""`, which the receive thread treats as an empty read and immediately loops forever. Also, `socket.shutdown()` is called without the required argument, so it raises and skips the explicit `close()`. A dropped BLE link can leave a hot-spinning receive thread and cached dead command object. Minimal fix: treat zero-length `recv()` as EOF and close/drop the transport; call `shutdown(socket.SHUT_RDWR)` and put `close()` in a separate `finally`.

7. **LOW** - [BLEManager.swift:190](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:190), [BLEManager.swift:214](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:214), [BLEManager.swift:244](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:244): the listener and connection handlers form retain cycles by capturing their owning `listener` or `conn` strongly. Repeated reconnects can retain canceled Network objects and their closures. Minimal fix: weak-capture those objects or clear all state, new-connection, and receive handlers before cancellation.

No additional finding on the requested fundamentals: CoreBluetooth callbacks are dispatched on the central's supplied `.main` queue, so the `MainActor.assumeIsolated` usage is valid; there are no force unwraps; MTU fragmentation itself is accepted by the firmware parser; `_connect` does not mistake a `tcp:` endpoint for DFU; and app-process CoreBluetooth is the correct macOS TCC boundary. Real hardware behavior remains unverified.
