Reading additional input from stdin...
2026-07-21T04:44:01.575482Z ERROR codex_models_manager::cache: failed to load models cache: missing field `supports_reasoning_summaries` at line 88 column 5
OpenAI Codex v0.144.1
--------
workdir: /Users/tuan/Claude/Tenor/tenor-rekey
model: gpt-5.6-sol
provider: openai
approval: never
sandbox: read-only
reasoning effort: high
reasoning summaries: none
session id: 019f82fc-f151-7393-a77c-ac483a82bca9
--------
user
You reviewed the tenor/rekey Bluetooth-LE change earlier and found 7 issues. They have now been fixed. Verify the FIXES are correctly applied and introduced no new bug. Inspect the current uncommitted diff + files yourself:
  git --no-pager diff -- app probe
  cat app/Sources/Engine/BLEManager.swift
Read app/Sources/AppModel.swift, app/Sources/Shell/RootView.swift, probe/chameleon_d.py, probe/chameleon/chameleon_com.py as needed.

The 7 fixes claimed:
1 (CRITICAL) dfu over BLE: probe/chameleon_d.py dfu_flash raises immediately when self._port starts with "tcp:"; Swift hides the firmware button + guards checkFirmware/flashFirmware via `deviceOverBLE` (descriptor.portOverride hasPrefix "tcp:").
2 (HIGH) BLE delegate callbacks scoped to the active attempt: an `attemptGen` counter + `isActive(peripheral)` guard on every delegate; timeout task guards `gen == attemptGen`; teardownLink bumps attemptGen; cancelled sleep returns cleanly.
3 (HIGH) TCP->BLE lossless: a single ordered `outbox`, `drainOutbox()` writes only while `canSendWriteWithoutResponse`, resumed by `peripheralIsReady(toSendWriteWithoutResponse:)`; the receive loop appends+drains once per completion before re-arming.
4 (HIGH) `bleConnecting` state folded into canChangeDevice/connecting/monitor guard, held across the connect.
5 (MED) bridge starts only after `didWriteValueFor` confirms the handshake write (awaitingHandshakeAck).
6 (MED) chameleon_com.py: recv()==b"" treated as EOF (break), shutdown(SHUT_RDWR) in try + close in finally.
7 (LOW) Network handler retain cycles: weak self, handlers cleared before cancel.

Confirm EACH fix is actually correct and complete. Then specifically hunt for NEW bugs the fixes may have introduced, focusing on:
- attemptGen/isActive: is there any path where a LEGITIMATE callback for the current attempt is now wrongly dropped (e.g. isActive compares identifiers but peripheral was reassigned), or where teardownLink bumping attemptGen breaks a normal disconnect/reconnect; can the continuation still leak or double-resume.
- drainOutbox backpressure: can the outbox stall forever if peripheralIsReady never fires (e.g. after the last write canSendWriteWithoutResponse is already true so no ready callback comes and residual bytes sit); is there any case bytes are left undrained; is maxWriteLen captured correctly.
- handshake gate: if didWriteValueFor never arrives (silent), does the 15s timeout still fail the connect cleanly; is awaitingHandshakeAck cleared on every exit path.
- bleConnecting: is it always cleared (defer) even on the throwing path; does it deadlock the swapDevice that bleConnect itself calls.
- daemon dfu guard + socket EOF: any regression to the USB serial path (the EOF/shutdown change must not affect SERIAL transport); does dfu_check still work over BLE (version read only).

Report only real remaining or newly-introduced defects (file:line, severity, scenario, fix). If the fixes are correct and complete with no new bug, say so plainly per fix. Be blunt.
codex
I will audit the exact working-tree diff and trace the BLE state machine, continuations, transport backpressure, and daemon USB/TCP branches. I will report only concrete defects, or explicitly clear each claimed fix.
exec
/bin/zsh -lc "git --no-pager diff -- app probe && sed -n '1,320p' app/Sources/Engine/BLEManager.swift && sed -n '1,320p' app/Sources/AppModel.swift && sed -n '1,260p' app/Sources/Shell/RootView.swift && sed -n '1,360p' probe/chameleon_d.py && sed -n '1,320p' probe/chameleon/chameleon_com.py" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 1692ms:
2026-07-21 11:44:11.574 xcodebuild[47455:3437269]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:44:11.705 xcodebuild[47455:3437268] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-rAAQ8kbu' (errno=Operation not permitted)
2026-07-21 11:44:12.148 xcodebuild[47458:3437285]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:44:12.281 xcodebuild[47458:3437284] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/app/Sources/AppModel.swift b/app/Sources/AppModel.swift
index c2a0610..6083fbe 100644
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
@@ -140,6 +145,12 @@ final class AppModel {
     /// the teardown await cannot grab the just-terminated bridge or the stale card.
     private var swapping = false
 
+    /// A BLE connect is in flight: the radio link + loopback bridge are being brought up,
+    /// which can take up to ~15s before `swapDevice` even runs. Reserved for that whole
+    /// window so a monitor tick or a second selection cannot interleave and swap the device
+    /// out from under the pending connect.
+    private var bleConnecting = false
+
     /// A device op already owns the reader. Reconnect / swap must not replace the
     /// bridge under one, and a second op must not start while one runs. Slot ops are
     /// included so a slot edit and a decode / clone can never overlap on the reader.
@@ -173,14 +184,20 @@ final class AppModel {
     var activeDeviceFamily: String { descriptor.family }
 
     /// A device swap / (re)connect is in flight, exposed read-only so the Connect
-    /// surface can show a spinner and disable Rescan while it runs.
-    var connecting: Bool { swapping }
+    /// surface can show a spinner and disable Rescan while it runs. A BLE connect is
+    /// included: its link-up window precedes the swap and must read as busy too.
+    var connecting: Bool { swapping || bleConnecting }
+
+    /// True when the active device is driven over a BLE loopback bridge (its port is pinned
+    /// to `tcp:...`). Firmware update over BLE is unsafe (the USB DFU flasher could hit the
+    /// wrong physical device), so the firmware UI and its methods gate on this being false.
+    var deviceOverBLE: Bool { descriptor.portOverride?.hasPrefix("tcp:") == true }
 
     /// The manual Connect controls (Rescan, the serial-port rows, the free-text connect)
     /// may act only when no swap or device op owns the reader - the same guard `connect`,
     /// `rescan`, and `connectManual` enforce - so a tap during one is a disabled control,
     /// never a silent no-op that the user reads as the app ignoring them.
-    var canChangeDevice: Bool { !swapping && !deviceBusy }
+    var canChangeDevice: Bool { !swapping && !deviceBusy && !bleConnecting }
 
     /// The user's editable keys (Settings > Dictionaries), tried before the
     /// daemon's large built-in dictionary.
@@ -252,6 +269,11 @@ final class AppModel {
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
 
@@ -296,6 +318,52 @@ final class AppModel {
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
+        // Reserve the whole connect (up to ~15s) so a monitor tick / second selection cannot
+        // interleave: bleConnecting gates canChangeDevice, connecting, and the monitor. It does
+        // NOT block the swapDevice call below (that guards on its own `swapping`).
+        bleConnecting = true
+        defer { bleConnecting = false }
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
@@ -343,7 +411,7 @@ final class AppModel {
     func monitor() async {
         while !Task.isCancelled {
             try? await Task.sleep(for: .seconds(1.5))
-            if deviceBusy || swapping { continue }
+            if deviceBusy || swapping || bleConnecting { continue }
             // Hot-swap detection runs even while emulating: unplugging an emulating
             // Chameleon and attaching another device must still tear down + swap. It is a
             // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
@@ -384,6 +452,10 @@ final class AppModel {
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
@@ -396,6 +468,12 @@ final class AppModel {
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
@@ -1210,6 +1288,9 @@ final class AppModel {
     /// the current version with a null latest + a note.
     func checkFirmware() async {
         guard capabilities.dfu, !swapping, !deviceBusy else { return }
+        // Firmware update is never offered over a BLE link (the USB DFU flasher could hit the
+        // wrong physical device). Defensive: the button is already hidden over BLE.
+        guard !deviceOverBLE else { return }
         flashError = nil
         // A device already in the bootloader has no command interface to query: leave the
         // status empty (the sheet shows the Ultra/Lite recovery choice) rather than erroring.
@@ -1227,6 +1308,9 @@ final class AppModel {
     /// mistake the reboot for an unplug.
     func flashFirmware(model: String?) async {
         guard capabilities.dfu, !swapping, !deviceBusy else { return }
+        // Never flash over a BLE link (the USB DFU flasher could hit the wrong physical
+        // device). Defensive: the firmware button is hidden over BLE.
+        guard !deviceOverBLE else { return }
         flashing = true
         flashDone = false
         flashError = nil
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
diff --git a/app/Sources/Shell/RootView.swift b/app/Sources/Shell/RootView.swift
index 520e3f9..bcd65c5 100644
--- a/app/Sources/Shell/RootView.swift
+++ b/app/Sources/Shell/RootView.swift
@@ -273,7 +273,9 @@ private struct ActionBar: View {
             // Firmware update (DFU), gated on the device advertising it: the X7 has
             // dfu:false and never shows it. Opening the sheet reads the current + latest
             // firmware. Disabled while any device op owns the reader.
-            if model.capabilities.dfu {
+            // Never over a BLE link: the DFU flasher runs over USB and could flash the
+            // wrong physical device, so the firmware action is hidden while connected over BLE.
+            if model.capabilities.dfu && !model.deviceOverBLE {
                 // Reachable when the reader is online OR the device is stuck in the
                 // bootloader (deviceInDFU) - the latter is exactly when flash-recovery is needed.
                 ActionButton(title: l.t("firmware"), icon: "arrow.up.circle",
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
diff --git a/probe/chameleon/chameleon_com.py b/probe/chameleon/chameleon_com.py
index bd6a3d2..87851eb 100644
--- a/probe/chameleon/chameleon_com.py
+++ b/probe/chameleon/chameleon_com.py
@@ -171,7 +171,13 @@ class ChameleonCom:
         try:
             assert self.transport is not None
             if self.transport_type is TransportType.SOCKET:
-                self.transport.shutdown()
+                # LOCAL PATCH (tenor/rekey): shutdown() requires a `how` argument; the bare
+                # call raised TypeError and skipped close() below. Guard it in its own
+                # try/except so a peer-closed socket (OSError) still falls through to close().
+                try:
+                    self.transport.shutdown(socket.SHUT_RDWR)
+                except OSError:
+                    pass
             self.transport.close()
         except Exception:
             pass
@@ -213,6 +219,13 @@ class ChameleonCom:
                     print(color_string(CR, 'socket closed'))
                     self.transport = None
                     break
+                # LOCAL PATCH (tenor/rekey): an orderly TCP close makes recv() return b'',
+                # not raise. Treat zero bytes as EOF and exit like the OSError branch;
+                # otherwise this loop hot-spins on empty reads forever.
+                if len(data_bytes) == 0:
+                    print(color_string(CR, 'socket closed'))
+                    self.transport = None
+                    break
 
             while len(data_bytes) > 0:
                 data_byte = data_bytes[0]
diff --git a/probe/chameleon_d.py b/probe/chameleon_d.py
index af4a89b..990e3e7 100644
--- a/probe/chameleon_d.py
+++ b/probe/chameleon_d.py
@@ -1940,6 +1940,13 @@ class Daemon:
         mid-write abort can brick, so it is never checked past step 5. `dfu_flash` is armed as
         flash-pending at DISPATCH (run()), so an EOF/shutdown while it is in flight always
         joins UNBOUNDED and never abandons a flash."""
+        # Fail closed over BLE: when the active port is the Swift loopback TCP bridge
+        # (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil flashes
+        # over USB serial - it would enter-bootloader / write a DIFFERENT physical device that
+        # happens to be on USB, using this BLE device's model. Refuse before any download,
+        # model read, reboot, or subprocess (a cross-device brick).
+        if isinstance(self._port, str) and self._port.startswith("tcp:"):
+            raise RuntimeError("firmware update over bluetooth is not supported; connect over usb")
         supplied_model = self._norm_model(p.get("model"))
         dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
         cdc_ports = self._find_cdc_ports()
diff --git a/probe/test_chameleon.py b/probe/test_chameleon.py
index c07113a..dbe489c 100644
--- a/probe/test_chameleon.py
+++ b/probe/test_chameleon.py
@@ -32,7 +32,7 @@ os.environ.setdefault("X7_LEARNED_PATH",
                       os.path.join(tempfile.gettempdir(), "rekey-test-learned.json"))
 
 import chameleon_d
-from chameleon.chameleon_com import ChameleonCom
+from chameleon.chameleon_com import ChameleonCom, TransportType, THREAD_BLOCKING_TIMEOUT
 from chameleon.chameleon_enum import (Status, SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
                                       MifareClassicPrngType, MifareClassicDarksideStatus)
 from chameleon.chameleon_utils import UnexpectedResponseError
@@ -3078,6 +3078,66 @@ def test_cham_emulate_load_ntag_gap(check):
           and emu.ntag_emu_counters[0][0] == 7, str(emu.ntag_emu_counters))
 
 
+# --------------------------------------------------------------------------
+# 34. dfu_flash fails closed over the BLE/tcp bridge: when the active port is the Swift
+#     loopback (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil
+#     flashes over USB - it could enter-bootloader / write a DIFFERENT USB device. The guard
+#     is at the very top of dfu_flash: it raises before any download / port scan / reboot /
+#     subprocess, so none of those seams run.
+# --------------------------------------------------------------------------
+def test_cham_dfu_flash_rejects_ble(check):
+    d = _dfu_daemon(FakeChameleon(model=0))
+    d._port = "tcp:127.0.0.1:5000"                     # active connection is the Swift BLE bridge
+    called = {}
+
+    def _mark(key, ret):
+        def _fn(*a, **k):
+            called[key] = True
+            return ret
+        return _fn
+    d._find_dfu_ports = _mark("scan", [])
+    d._find_cdc_ports = _mark("scan", [])
+    d._download_asset = _mark("download", 0)
+    d._send_enter_dfu = _mark("entered", None)
+    d._run_flash = _mark("flashed", None)
+    raised = None
+    try:
+        d.dfu_flash({})
+    except RuntimeError as e:
+        raised = str(e)
+    check("dfu_flash over the BLE/tcp bridge raises (bluetooth/usb message)",
+          raised is not None and "bluetooth" in raised.lower() and "usb" in raised.lower(),
+          repr(raised))
+    check("dfu_flash over BLE runs no seam (no download / port scan / reboot / flash)",
+          not called, str(called))
+
+
+# --------------------------------------------------------------------------
+# 35. socket transport recognises an orderly peer EOF: recv() returning b'' must end the
+#     receive loop (transport dropped), not hot-spin on empty reads. Driven with a real
+#     socketpair so the loop runs against a genuine socket close.
+# --------------------------------------------------------------------------
+def test_cham_socket_eof_exits(check):
+    import socket as _socket
+    a, b = _socket.socketpair()
+    a.settimeout(THREAD_BLOCKING_TIMEOUT)              # bound recv so it can see the EOF quickly
+    c = ChameleonCom()
+    c.transport = a
+    c.transport_type = TransportType.SOCKET
+    c.event_closing.clear()
+    t = threading.Thread(target=c.thread_data_receive)
+    t.start()
+    b.close()                                          # orderly peer close -> recv() returns b''
+    t.join(2)
+    check("thread_data_receive exits promptly on peer EOF (no hot spin)",
+          not t.is_alive() and c.transport is None,
+          "alive=%s transport=%s" % (t.is_alive(), c.transport))
+    try:
+        a.close()
+    except OSError:
+        pass
+
+
 TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
          test_cham_read_block, test_cham_decode, test_cham_decode_partial,
          test_cham_decode_nocard, test_cham_decode_swap, test_cham_decode_user_key,
@@ -3107,7 +3167,8 @@ TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
          test_cham_dfu_flash_safety, test_cham_dfu_download_checks,
          test_cham_dfu_identity_binding, test_cham_dfu_settle_past_deadline,
          test_cham_dfu_cancel_through_run,
-         test_cham_dfu_eof_waits_for_flash, test_cham_dfu_eof_dispatch_race]
+         test_cham_dfu_eof_waits_for_flash, test_cham_dfu_eof_dispatch_race,
+         test_cham_dfu_flash_rejects_ble, test_cham_socket_eof_exits]
 
 
 if __name__ == "__main__":
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
    /// Monotonic connect-attempt generation. Bumped on every new connect and on every
    /// teardown, so a delegate callback / timeout belonging to a superseded attempt (a
    /// BLE-A -> BLE-B swap, a double-tap) is recognised as stale and ignored: it must not
    /// resolve the continuation or tear down the newer link.
    private var attemptGen = 0
    /// True between issuing the handshake `.withResponse` write and its ack: the loopback
    /// bridge is started only once that ack lands (didWriteValueFor), not immediately.
    private var awaitingHandshakeAck = false

    // ---- Loopback TCP bridge (Network framework) ---------------------------
    private var listener: NWListener?
    private var tcpConnection: NWConnection?
    private let bridgeQueue = DispatchQueue(label: "vn.tenor.rekey.ble-bridge")
    /// The single ordered TCP -> BLE outgoing buffer. Bytes arriving from the daemon are
    /// appended here on the main actor and drained to the RX characteristic in order, only
    /// while the peripheral can accept a no-response write (real backpressure), so nothing
    /// is dropped and the byte order the daemon sent is preserved.
    private var outbox = Data()

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
            self.attemptGen += 1
            let gen = self.attemptGen
            self.connectContinuation = cont
            self.peripheral = target
            self.awaitingHandshakeAck = false
            target.delegate = self
            // One overall deadline: any stalled step (connect, discover, notify, bridge)
            // fails the whole connect rather than orphaning the continuation. Scoped to this
            // attempt: a superseded attempt's timeout is a no-op, and a cancelled sleep
            // (the task was torn down) returns cleanly rather than failing a later attempt.
            self.connectTimeoutTask = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(15)) }
                catch { return }
                guard let self else { return }
                guard gen == self.attemptGen, self.connectContinuation != nil else { return }
                self.failConnect(BLEError.timeout)
            }
            central.connect(target, options: nil)
        }
    }

    /// Whether `p` is the peripheral of the CURRENT attempt / link. A callback from any other
    /// (stale) peripheral must be ignored so it cannot resolve the continuation or tear down
    /// the newer link.
    private func isActive(_ p: CBPeripheral) -> Bool { p.identifier == peripheral?.identifier }

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
        // Supersede any in-flight attempt: a callback / timeout still queued for the link
        // being torn down now sees a newer generation (or a nil peripheral) and is ignored.
        attemptGen += 1
        awaitingHandshakeAck = false
        teardownBridge()
        if let p = peripheral, let central { central.cancelPeripheralConnection(p) }
        peripheral = nil
        rxChar = nil
        txChar = nil
        connectedDeviceID = nil
    }

    private func teardownBridge() {
        outbox = Data()
        // Clear the handlers before cancelling so the cancelled Network objects and their
        // closures are released promptly on repeated reconnects (no lingering retain).
        if let conn = tcpConnection {
            conn.stateUpdateHandler = nil
            conn.cancel()
        }
        tcpConnection = nil
        if let l = listener {
            l.stateUpdateHandler = nil
            l.newConnectionHandler = nil
            l.cancel()
        }
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
        // Do NOT capture `listener` strongly inside its own handler (that is a retain cycle):
        // read the bound port back off `self.listener`, which is this same listener.
        listener.stateUpdateHandler = { [weak self] st in
            switch st {
            case .ready:
                Task { @MainActor in
                    guard let self else { return }
                    if let raw = self.listener?.port?.rawValue { onReady(.success(Int(raw))) }
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

    /// One turn of the daemon -> BLE relay. The receive completion runs on the bridge queue
    /// and hops onto the main actor exactly once, where it appends the bytes to the single
    /// ordered `outbox`, drains what the peripheral can currently accept, and only THEN
    /// re-arms the next receive. Handling one completion fully before arming the next keeps
    /// byte order intact (no concurrent, unordered write vs re-arm tasks).
    private func receiveLoop(_ conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            Task { @MainActor in
                guard let self else { return }
                if let data, !data.isEmpty {
                    self.outbox.append(data)
                    self.drainOutbox()
                }
                if isComplete || error != nil {
                    self.handleTCPClosed(conn)
                } else {
                    self.receiveLoop(conn)
                }
            }
        }
    }

    /// Drain the ordered outbox to the device with real backpressure: while there are bytes
    /// AND the peripheral can accept a no-response write, pop up to the max no-response write
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

import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// One unified workspace: the decoded / loaded image is the DOCUMENT on the canvas,
/// the card on the reader is a separate live device; reading / writing / format /
/// save / open are LABELLED actions on a always-visible action bar (so the
/// workflow is discoverable, not hidden behind cryptic toolbar icons). The
/// titlebar is hidden; a custom header carries the brand wordmark + reader
/// status cleanly (no system toolbar wells). Theme + language switch instantly.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l10n
    @Environment(\.colorScheme) private var systemScheme

    var body: some View {
        @Bindable var model = model
        Workspace()
            .background(WindowConfigurator())
            .preferredColorScheme(theme.appearance == .system ? nil : theme.scheme)
            .onAppear {
                theme.systemScheme = systemScheme
                l10n.systemCode = Locale.current.language.languageCode?.identifier ?? "en"
                // Refuse app quit while a firmware flash is writing (a mid-write kill can
                // brick the device); warn and keep the update running.
                AppDelegate.terminationGuard = {
                    guard model.flashing else { return .terminateNow }
                    let alert = NSAlert()
                    alert.messageText = l10n.t("quit_while_flashing_title")
                    alert.informativeText = l10n.t("quit_while_flashing_msg")
                    alert.addButton(withTitle: l10n.t("keep_updating"))
                    alert.runModal()
                    return .terminateCancel
                }
            }
            .onChange(of: systemScheme) { _, s in
                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
            }
            .sheet(isPresented: $model.cloneSheet) {
                CloneSheet().environment(model).environment(theme).environment(l10n)
            }
            .sheet(isPresented: $model.flashingSheet, onDismiss: { model.clearFlashState() }) {
                FlashingView().environment(model).environment(theme).environment(l10n)
            }
            .confirmationDialog(l10n.t("format_q"), isPresented: $model.formatConfirm, titleVisibility: .visible) {
                // Pinned to the uid snapshot taken when the dialog opened, so a card
                // swapped in while it is open is never the one wiped.
                Button(l10n.t("format"), role: .destructive) {
                    Task { await model.format(authorizedUID: model.pendingFormatUID) }
                }
                Button(l10n.t("cancel"), role: .cancel) {}
            } message: {
                Text(l10n.t("format_msg") + (model.pendingFormatUID.map { "\n\n\(l10n.t("card_on_reader")): \($0)" } ?? ""))
            }
            .task { await model.connect(); await model.monitor() }
    }
}

private struct Workspace: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    var body: some View {
        VStack(spacing: 0) {
            HeaderBar()
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ActionBar()
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ErrorBanner()
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    if model.apduOpen {
                        Rectangle().fill(theme.p.hairline).frame(height: 1)
                        ApduConsole()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if model.inspectorOpen {
                    Rectangle().fill(theme.p.hairline).frame(width: 1)
                    SectorInspector().frame(width: 300)
                }
            }
        }
        .background(theme.p.canvas)
        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
            guard let provider = providers.first else { return false }
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                let url = (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) } ?? (item as? URL)
                guard let url else { return }
                Task { @MainActor in model.loadDump(from: url) }
            }
            return true
        }
    }
}

/// A dismissible status line for the last operation error (a clone that hit the
/// wrong card, a write that failed, a decode that was interrupted). Without it those
/// failures were silent - the model recorded them but nothing was ever shown. Glyph +
/// typography carry the signal (instrument discipline: no alarm colour).
private struct ErrorBanner: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        if let err = model.lastError {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                    .foregroundStyle(theme.p.textPrimary)
                Text(err).font(l.sans(11)).foregroundStyle(theme.p.textPrimary).lineLimit(2)
                Spacer()
                Button { withAnimation(.easeInOut(duration: 0.2)) { model.lastError = nil } } label: {
                    Image(systemName: "xmark").font(.system(size: 9))
                }.buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
            }
            .padding(.horizontal, 16).padding(.vertical, 9)
            .background(theme.p.tileFill)
            Rectangle().fill(theme.p.hairline).frame(height: 1)
        }
    }
}

// MARK: - Header (brand + status + utilities), in content so we control the look

private struct HeaderBar: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        // The row sits BELOW the traffic-light band (top padding clears the
        // lights) so the wordmark left-aligns with the action bar margin instead
        // of being indented beside the lights.
        HStack(spacing: 12) {
            Lockup(focal: "rekey", size: 15)
            Spacer()
            ReaderStatusInline()
            Divider().frame(height: 16)
            Menu {
                ForEach(AppLang.allCases) { lang in
                    Button(lang == .system ? l.systemDisplay() : lang.display) { l.lang = lang }
                }
            } label: { Image(systemName: "globe") }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().help(l.t("language"))
            iconButton("sun.max", symbol: theme.toggleSymbol, help: l.t("light_dark")) { theme.toggle() }
            iconButton("sidebar.right", help: l.t("inspector")) { model.inspectorOpen.toggle() }
        }
        .font(l.sans(12))
        .foregroundStyle(theme.p.textSecondary)
        .padding(.leading, 16)
        .padding(.trailing, 14)
        .padding(.top, 30)
        .padding(.bottom, 12)
        .background(theme.p.panel)
    }
    private func iconButton(_ name: String, symbol: String? = nil, help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol ?? name) }
            .buttonStyle(.plain).foregroundStyle(theme.p.textSecondary).help(help)
    }
}

private struct ReaderStatusInline: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        @Bindable var model = model
        // The status pill is the Connect affordance: tapping it opens the device list /
        // rescan / manual-connect popover. A chevron marks it as tappable.
        Button { model.showConnect.toggle() } label: {
            HStack(spacing: 6) {
                Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary).frame(width: 6, height: 6)
                Text(text).font(model.card?.uid != nil ? Typeface.mono(11) : l.sans(11))
                    .foregroundStyle(theme.p.textSecondary)
                Image(systemName: "chevron.down").font(.system(size: 8)).foregroundStyle(theme.p.textTertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(l.t("connect"))
        .popover(isPresented: $model.showConnect, arrowEdge: .bottom) {
            ConnectView().environment(model).environment(theme).environment(l)
        }
    }
    private var text: String {
        if model.deviceInDFU { return l.t("in_bootloader") }
        if !model.readerOnline { return l.t("reader_offline") }
        if let uid = model.card?.uid { return "\(l.t("card")) · \(uid)" }
        return l.t("reader_online")
    }
}

// MARK: - Action bar (the discoverable, labelled verbs)

private struct ActionBar: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    private var ntag: Bool { model.card?.isNTAG == true }
    private var busy: Bool { model.decoding || model.cloning || model.formatting }

    var body: some View {
        HStack(spacing: 8) {
            // Enabled whenever the reader is online, not only once the snappy status
            // poll has detected a card: the decode does its own patient coupling, so a
            // seated-but-undetected card is no longer a dead button (see AppModel.decode).
            ActionButton(title: l.t(ntag ? "read" : "decode"), icon: "square.grid.3x3",
                         prominent: true, enabled: model.readerOnline && !busy && !model.emulating) { Task { await model.decode() } }
            // Write lights up as soon as there is a document to write; it does NOT
            // require a card on the reader (the target is asked for at write time in
            // the sheet), so lifting the source card to place a blank never darkens it.
            ActionButton(title: l.t("write"), icon: "square.and.arrow.down.on.square",
                         enabled: model.cloneSource != nil && !busy && !model.emulating) { model.cloneSheet = true }
            // Format is destructive but offered for ANY present card (a blank / unknown
            // card can be wiped with factory keys, no prior decode required); the daemon
            // keeps the anti-brick guards. Gated only on a card being present + a confirm.
            ActionButton(title: l.t("format"), icon: "eraser",
                         enabled: model.card != nil && !busy && !model.emulating) { model.requestFormat() }
            Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
            ActionButton(title: l.t("save_dump"), icon: "arrow.down.doc",
                         enabled: model.source != nil) { model.saveDumpDialog() }
            ActionButton(title: l.t("open_dump"), icon: "folder", enabled: true) { model.openDumpDialog() }
            ActionButton(title: "apdu", icon: "terminal", on: model.apduOpen, enabled: true) { model.apduOpen.toggle() }
            // Saved-cards library: device-agnostic (shown for both the X7 and a Chameleon),
            // so it sits with the document verbs, not behind the Chameleon divider. Opening
            // it closes the Chameleon slot library (only one detail area at a time).
            ActionButton(title: l.t("library"), icon: "books.vertical", on: model.showLibrary,
                         enabled: true) {
                let willShow = !model.showLibrary
                withAnimation(.easeInOut(duration: 0.2)) {
                    model.showLibrary = willShow
                    if willShow { model.showSlots = false; model.showLF = false }
                }
                if willShow { model.refreshSavedCards() }
            }
            // Chameleon-only verbs, gated on the connected device's capabilities: the
            // slot library, the reader<->emulate toggle, and loading the working
            // document into a slot for emulation. A plain reader (X7) shows none of them.
            if model.capabilities.slots > 0 || model.capabilities.emulate
                || model.capabilities.lf || model.capabilities.dfu {
                Rectangle().fill(theme.p.hairline).frame(width: 1, height: 18).padding(.horizontal, 3)
            }
            if model.capabilities.slots > 0 {
                ActionButton(title: l.t("slots"), icon: "square.stack.3d.up", on: model.showSlots,
                             enabled: !model.slotBusy) {
                    let willShow = !model.showSlots
                    withAnimation(.easeInOut(duration: 0.2)) {
                        model.showSlots = willShow
                        if willShow { model.showLibrary = false; model.showLF = false }
                    }
                    if willShow { Task { await model.loadSlots() } }
                }
            }
            // LF (125 kHz) panel, gated on the device advertising lf: read an LF tag,
            // clone it to a T5577, or load an EM410x id into a slot to emulate. A plain
            // reader (X7, lf:false) never shows it.
            if model.capabilities.lf {
                ActionButton(title: "LF", icon: "wifi", on: model.showLF,
                             enabled: !model.lfBusy, help: l.t("lf_hint")) {
                    let willShow = !model.showLF
#!/usr/bin/env python3
"""chameleon_d - JSON-over-stdio daemon wrapping the Chameleon Ultra CLI engine.

Sibling of x7d.py. The native macOS (SwiftUI) front-end speaks the SAME contract
for every device it drives, so the shell never learns a new protocol: it reads the
`capabilities` manifest returned by `info` and lights up the panels the device
supports. This daemon holds one vendored ChameleonCom (transport) + ChameleonCMD
(command layer) and serialises every device op (one device, one command stream).

Wire format - newline-delimited JSON on stdin/stdout (identical to x7d.py):
  request : {"id": <n>, "method": "<name>", "params": {...}}
  response: {"id": <n>, "result": {...}}  |  {"id": <n>, "error": "<msg>"}
  event   : {"event": "progress", "method": "<name>", ...}   (id-less, unsolicited)

Methods (P0 surface): info, poll, slots_list, slot_select, mf_read_block, decode.
Hex at the JSON boundary is lowercase space-separated ("01 02 03 04"); keys are
12-char hex. SAK is an int (matching x7d, so the shell's parser is unchanged).
"""
import sys
import os
import re
import json
import time
import queue
import struct
import shutil
import hashlib
import zipfile
import tempfile
import threading
import subprocess
import collections
import importlib.util

# Vendored upstream engine (GPLv3, RfidResearchGroup/ChameleonUltra). Imports on a
# bare interpreter - serial/colorama/prompt_toolkit are optional in the package.
from chameleon.chameleon_com import ChameleonCom, NotOpenException, OpenFailException
from chameleon.chameleon_cmd import ChameleonCMD
from chameleon.chameleon_enum import (SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
                                      Status, MifareClassicPrngType, MifareClassicDarksideStatus,
                                      HIDFormat)
from chameleon.chameleon_utils import UnexpectedResponseError

# Host-side crackers over the vendored C tools (firmware acquires nonces, host
# cracks them). Optional: if the binaries are not built, decode degrades to the
# on-device dictionary check and never crashes.
try:
    import chameleon_crack as crack
except Exception:                        # pragma: no cover - import guard
    crack = None

# The learned-key reranker + the curated dictionary are SHARED with x7d (one cache
# and one dictionary across both readers). Both are optional so the daemon still
# runs if they are unavailable.
try:
    from learned_keys import LearnedKeyCache
except Exception:                        # pragma: no cover - import guard
    LearnedKeyCache = None
try:
    from x7lib import BUILTIN_KEYS as _X7_BUILTIN
except Exception:                        # pragma: no cover - import guard
    _X7_BUILTIN = []

# USB CDC vendor id of the Chameleon Ultra / Lite (VID 0x6868, PID 0x8686).
CHAMELEON_VID = 0x6868

# Firmware update (Nordic Secure DFU). In normal run the device is CDC VID 0x6868;
# once rebooted into the bootloader it re-enumerates as VID 0x1915 / PID 0x521f. The
# exact 10-byte ENTER_BOOTLOADER frame (cmd 0x03f2 = 1010) is taken verbatim from the
# upstream resource/tools/enter_dfu.py: SOF 0x11, header-LRC 0xef, cmd 0x03 0xf2,
# status 0x00 0x00, length 0x00 0x00, header-LRC 0x0b, data-LRC 0x00.
DFU_VID = 0x1915
DFU_PID = 0x521f
DFU_ENTER_FRAME = b"\x11\xef\x03\xf2\x00\x00\x00\x00\x0b\x00"
# Seconds to wait for the bootloader port to re-appear after the enter-DFU write.
DFU_WAIT_SECONDS = 20
# After the first new DFU port appears, keep watching this long for a SECOND one to enumerate
# (USB re-enum is sub-second, so a second appearing within this settle window means another
# device is in DFU - the target is then ambiguous and the flash is refused).
DFU_SETTLE_SECONDS = 2.0
# Firmware source (v1 is DOWNLOAD-ONLY): the model-specific application-only asset from the
# official RfidResearchGroup/ChameleonUltra RELEASES. No local files, no nightlies, no CI
# workflow artifacts, no arbitrary URLs - only the official release asset for the model.
GITHUB_RELEASES = "https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/releases"
# Per-asset API endpoint. Downloading through the pinned asset ID (with Accept:
# application/octet-stream) fetches the EXACT asset resolved during this op, so deleting +
# replacing an asset under the same tag/filename between resolve and download cannot swap
# the bytes (browser_download_url would follow the new asset).
GITHUB_ASSET = "https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/releases/assets/%s"
# Hard cap on the firmware download (the app image is well under 1 MB; this bounds a
# truncated/oversized/hostile response). A download that exceeds it is refused.
MAX_FIRMWARE_BYTES = 16 * 1024 * 1024
# Nordic dfu-cc Hash.hash_type enum -> hashlib algorithm (mirrors the GUI validateFiles:
# SHA128 -> sha1, SHA256 -> sha256, SHA512 -> sha512). CRC / NO_HASH are not accepted.
_HASH_ALGO = {2: "sha1", 3: "sha256", 4: "sha512"}
# Nordic dfu-cc FwType.APPLICATION - the only image type this tool will flash (a
# SOFTDEVICE / BOOTLOADER / SOFTDEVICE_BOOTLOADER declared type is a full package).
_FWTYPE_APPLICATION = 0
# Filenames that mark a FULL package (bootloader + softdevice). An app-only package
# ships exactly the application pair (+ manifest); anything naming a bootloader or
# softdevice image is a full-dfu zip, which can BRICK on a mid-flash failure.
_FULL_MARKERS = ("bootloader", "softdevice", "sd_bl")
# Shown when the running firmware cannot be reached to trigger the reboot-into-DFU.
_MANUAL_FALLBACK = ("power the device off, hold button B while plugging in USB "
                    "(LEDs 4 and 5 blink = bootloader), then run the update again.")

# How many learned keys to try (after user keys, before the dictionary). Mirrors
# x7d so the two readers rerank identically.
LEARNED_TOP_N = 64

# Attack-stage budget. The on-device dictionary check is fast; the nonce-cracking
# attacks (nested/darkside) are the slow part, so the wall-clock budget guards them
# (overridable per-call via params.max_seconds). Mirrors x7d's runaway watchdog.
DEFAULT_ATTACK_SECONDS = 120
# Darkside collects one acquisition per round and retries until the crack yields a
# key or the parity-zero intersection converges; bounded by rounds AND the budget.
DARKSIDE_MAX_ROUNDS = 24
DARKSIDE_SYNC_MAX = 30                    # firmware sync attempts per round (CLI default)
DARKSIDE_TARGET_BLOCK = 3                # sector 0 trailer, KeyA - the autopwn foothold

# Hardnested (hard-PRNG / MFC Ev1) collects encrypted nonces on-device until the full
# 256-value nt_enc first-byte (MSB) distribution is seen - the coverage the host cracker
# needs. Each acquire is slow, so the loop is bounded by this run cap AND the wall-clock
# budget + cooperative cancel (mirrors the CLI's max_runs, but the budget is the real
# guard). The crack subprocess is itself capped by the remaining budget.
HARDNESTED_MAX_RUNS = 200                 # CLI default max_runs
HARDNESTED_MSB_TARGET = 256               # unique nt_enc MSBs = a complete distribution

# Public, well-known MIFARE Classic default keys (documented defaults, never card
# secrets - safe in a public repo). The named set comes first so a factory card
# resolves in the first check-keys chunk; probe's curated dictionary (loaded by
# x7lib from probe/dict) is appended when present.
_DEFAULT_KEYS = [
    "ffffffffffff", "000000000000", "a0a1a2a3a4a5", "d3f7d3f7d3f7",
    "a0b0c0d0e0f0", "b0b1b2b3b4b5", "4d3a99c351dd", "1a982c7e459a",
    "aabbccddeeff", "714c5c886e97", "587ee5f9350f", "a0478cc39091",
    "533cb6c723f6", "8fd0a4f256e9",
]
BUILTIN_KEYS = list(_DEFAULT_KEYS)
_seen = set(_DEFAULT_KEYS)
for _k in _X7_BUILTIN:
    if _k not in _seen:
        BUILTIN_KEYS.append(_k)
        _seen.add(_k)


def hx(b):
    """Lowercase space-separated hex, identical to x7d/x7 hx()."""
    return " ".join("%02x" % x for x in b)


def _valid_key_hex(k):
    """A key must be exactly 12 hex chars, else it cannot be used for auth."""
    return isinstance(k, str) and len(k) == 12 and all(c in "0123456789abcdefABCDEF" for c in k)


def _is_hex(s, n):
    """True when `s` is exactly `n` lowercase/uppercase hex chars (used to validate a
    sha256 digest string from the releases API before trusting it)."""
    return isinstance(s, str) and len(s) == n and all(c in "0123456789abcdefABCDEF" for c in s)


# ---- MIFARE Classic geometry (kept local so this daemon does not pull in the X7
# hidapi stack just for these pure helpers). Mirrors x7lib's definitions. --------

def sector_count(sak):
    return 40 if sak == 0x18 else 16            # 4K vs 1K


def blocks_in_sector(s):
    return 4 if s < 32 else 16                   # 4K big sectors


def first_block(s):
    return s * 4 if s < 32 else 128 + (s - 32) * 16


def trailer_block(s):
    return first_block(s) + blocks_in_sector(s) - 1


def _sector_of(b):
    return b // 4 if b < 128 else 32 + (b - 128) // 16


def access_bits_valid(trailer):
    """True if a MIFARE Classic trailer's access bytes (6, 7, 8) pass the standard
    inverted-complement integrity check. A trailer whose access bytes are corrupt must
    NEVER be written: the wrong condition bits can lock a sector permanently (no key can
    rewrite the trailer again). Kept local (like the geometry helpers) so this daemon does
    not pull in the X7 hidapi stack; mirrors x7lib.access_bits_valid EXACTLY. Layout:
    byte6 low nibble = ~C1, high = ~C2; byte7 low = ~C3, high = C1; byte8 low = C2, high = C3."""
    if len(trailer) < 9:
        return False
    b6, b7, b8 = trailer[6], trailer[7], trailer[8]
    bit = lambda v, n: (v >> n) & 1
    for i in range(4):
        c1, c2, c3 = bit(b7, 4 + i), bit(b8, i), bit(b8, 4 + i)
        if bit(b6, i) == c1 or bit(b6, 4 + i) == c2 or bit(b7, i) == c3:
            return False
    return True


def trailer_locks_keys(trailer):
    """True if a trailer's access bits leave the sector in a state where NEITHER key can
    ever rewrite the trailer again (keys permanently frozen). Writing such a trailer onto a
    normal card bricks the sector, so the re-key write path refuses it. The trailer group
    (group 3) C1C2C3 in {010, 110, 101, 111} = "keys locked" / "fully locked". The factory
    trailer (ff 07 80) is group-3 001 = "a writes keys+access", so it is not affected.
    Mirrors x7lib.trailer_locks_keys EXACTLY."""
    if len(trailer) < 9:
        return True                              # can't tell -> treat as unsafe
    b7, b8 = trailer[7], trailer[8]
    bit = lambda v, n: (v >> n) & 1
    c = (bit(b7, 7), bit(b8, 3), bit(b8, 7))     # group 3 = bits index 4+3, 3, 4+3
    return c in {(0, 1, 0), (1, 1, 0), (1, 0, 1), (1, 1, 1)}


def card_kind(sak, atqa):
    """NTAG/Ultralight report SAK 0x00 AND ATQA 0x0044; a magic/blank Classic also
    reports SAK 0x00 but ATQA 0x0004. atqa: 2 bytes or int."""
    a = int.from_bytes(atqa, "big") if isinstance(atqa, (bytes, bytearray)) else int(atqa)
    return "ntag" if (sak == 0x00 and a == 0x0044) else "classic"


def _type_name(t):
    """TagSpecificType value -> stable enum name (the shell localises it)."""
    try:
        return TagSpecificType(t).name
    except ValueError:
        return "UNKNOWN_%d" % t


def _resolve_type(t):
    """A tag type given as an enum NAME ('MIFARE_1024') or an int -> TagSpecificType.
    Raises (KeyError / ValueError, surfaced as a clean error envelope) on an unknown
    type rather than guessing one."""
    if isinstance(t, str):
        return TagSpecificType[t]
    return TagSpecificType(int(t))


def _sense(s):
    """The slot field a method targets: 'lf' -> TagSenseType.LF, anything else -> HF
    (the common case; slot_* callers pass an explicit 'hf'/'lf')."""
    return TagSenseType.LF if str(s).lower() == "lf" else TagSenseType.HF


# ---- Ultralight / NTAG geometry (kept local, mirrors the reference GUI's
# mifare_ultralight/general.dart) so read_ntag can size a dump per chip type and the
# slot-emulate path can refuse a non-UL/NTAG type. -----------------------------------

# The UL/NTAG HF TagSpecificType set (>= 1100). A slot may only be UL/NTAG-emulated
# with one of these; any other HF type is refused by emulate_load_ntag.
_NTAG_UL_TYPES = frozenset({
    TagSpecificType.NTAG_210, TagSpecificType.NTAG_212, TagSpecificType.NTAG_213,
    TagSpecificType.NTAG_215, TagSpecificType.NTAG_216, TagSpecificType.MF0ICU1,
    TagSpecificType.MF0ICU2, TagSpecificType.MF0UL11, TagSpecificType.MF0UL21,
})

# Page count per chip type (4-byte pages). Values from the NXP datasheets, matching
# the GUI's mfUltralightGetPagesCount.
_UL_PAGE_COUNT = {
    TagSpecificType.MF0ICU1: 16,             # Mifare Ultralight
    TagSpecificType.MF0ICU2: 48,             # Mifare Ultralight C
    TagSpecificType.MF0UL11: 20,             # Ultralight EV1 (640 bit)
    TagSpecificType.MF0UL21: 41,             # Ultralight EV1 (1312 bit)
    TagSpecificType.NTAG_210: 20,
    TagSpecificType.NTAG_212: 41,
    TagSpecificType.NTAG_213: 45,
    TagSpecificType.NTAG_215: 135,
    TagSpecificType.NTAG_216: 231,
}


def _ultralight_type(version):
    """UL/NTAG TagSpecificType inferred from the 8-byte GET_VERSION (byte 6 = storage size),
    mirroring the GUI's mfUltralightGetType. A short/empty version (a plain UL that NAKs
    GET_VERSION) -> UNDEFINED so the caller reads via the bounded fallback. An UNRECOGNIZED
    storage byte ALSO -> UNDEFINED (bounded fallback) rather than being forced to a 16-page
    plain Ultralight: a real 135-page tag with an unexpected storage byte must dump fully,
    not truncate to 16 pages."""
    if len(version) < 7:
        return TagSpecificType.UNDEFINED
    b6 = version[6]
    if b6 in (0x0B, 0x00):
        return TagSpecificType.MF0UL11
    if b6 == 0x0E:
        return TagSpecificType.MF0UL21
    if b6 == 0x0F:
        return TagSpecificType.NTAG_213
    if b6 == 0x11:
        return TagSpecificType.NTAG_215
    if b6 == 0x13:
        return TagSpecificType.NTAG_216
    return TagSpecificType.UNDEFINED          # unrecognized storage byte: bounded fallback, never truncate


def _ultralight_pages(tt):
    """Readable-page count for a UL/NTAG type, or 0 when the type is unknown (the caller
    then reads until the tag rolls over / NAKs)."""
    return _UL_PAGE_COUNT.get(tt, 0)


def _ultralight_counters(tt):
    """Number of one-way NFC counters the type exposes (UL EV1 = 3, NTAG21x = 1, else 0)."""
    if tt in (TagSpecificType.MF0UL11, TagSpecificType.MF0UL21):
        return 3
    if tt in (TagSpecificType.NTAG_210, TagSpecificType.NTAG_212, TagSpecificType.NTAG_213,
              TagSpecificType.NTAG_215, TagSpecificType.NTAG_216):
        return 1
    return 0


# LF (125 kHz) slot types with an emulation path in v1: em410x_set_emu_id supports
# EXACTLY these two (5-byte EM410x, 13-byte Electra). Any OTHER LF-sense type (Viking /
# PAC / ioProx / Idteck / Jablotron / HID Prox / the EM410x ASK sub-variants with no
# emu-id support) is out of the v1 LF scope AND has no emulation path, so slot_set_type
# refuses to configure it - it would strand a slot the shell can never fill.
_LF_EMU_TYPES = frozenset({TagSpecificType.EM410X, TagSpecificType.EM410X_ELECTRA})

# The human status string the CLI's @expect_response raises for an empty LF field
# (LF_TAG_NO_FOUND). lf_scan treats ONLY this as "no tag"; any other status (PAR_ERR /
# device-mode / NOT_IMPLEMENTED / INVALID_CMD) surfaces as a real error, not a phantom
# absent. Computed from the enum so it stays in sync with the vendored status strings.
_LF_NO_TAG_MSG = str(Status.LF_TAG_NO_FOUND)


def _is_lf_type(tt):
    """True when a TagSpecificType is an LF-sense type (0 < value < TAG_TYPES_LF_END),
    so HF types (>= 1000) are left unrestricted by the LF-scope gate."""
    return TagSpecificType.UNDEFINED < tt < TagSpecificType.TAG_TYPES_LF_END


def _is_lf_no_tag(exc):
    """True only when an UnexpectedResponseError is the LF_TAG_NO_FOUND status. The
    vendored @expect_response collapses the status to its human string, so we compare
    against that string (there is no status attribute on the exception)."""
    return str(exc) == _LF_NO_TAG_MSG


def _capabilities(model, cracker=None):
    """The capability manifest the shell reads to gate panels (SPEC 2.3). Built
    from the device model, not hardcoded: model 0 = Ultra, 1 = Lite. The Lite has
    the same 8 slots + emulation + DFU but no HF reader, so reader-mode attacks do
    not apply (P0 default; confirm the Lite on hardware)."""
    # hardnested (hard-PRNG / MFC Ev1) IS wired into decode() now, but it can only run
    # when its host cracker binary is actually built. Advertise it DYNAMICALLY - appended
    # to the base attacks only when the cracker reports it present - so the shell never
    # lights up an attack the daemon cannot deliver (mirrors the graceful degrade of the
    # other crackers). `lf` is now TRUE: this daemon exposes lf_scan / lf_write / lf_emu,
    # but ONLY for the protocols listed in lfProtocols (EM410x + HID Prox read, T5577
    # write, EM410x emulate) - the daemon can drive more LF protocols but only these are
    # surfaced, so the shell never lights up an LF protocol without live acceptance. `sniff`
    # stays FALSE until this daemon exposes a sniffer.
    attacks = ["dict", "nested", "staticNested", "darkside"]
    if cracker is not None:
        try:
            if cracker.available("hardnested"):
                attacks.append("hardnested")
import sys
import queue
import struct
import threading
import time
import platform
from typing import Union
from enum import Enum, auto
# serial is imported lazily inside open() so the package imports on a bare
# interpreter; it is only needed to actually open a hardware port.
import socket

from .chameleon_utils import CR, CG, CC, CY, color_string
from .chameleon_enum import Command, Status

ANDROID = 'android' in platform.release()

# each thread is waiting for its data for 100 ms before looping again
THREAD_BLOCKING_TIMEOUT = 0.1

# TODO: client settings
DEBUG = False


class TransportType(Enum):
    NONE = auto()
    SERIAL = auto()
    SOCKET = auto()


class NotOpenException(Exception):
    """
        Chameleon err status
    """


class OpenFailException(Exception):
    """
        Chameleon open fail(serial port may be error)
    """


class CMDInvalidException(Exception):
    """
        CMD invalid(Unsupported)
    """


class Response:
    """
        Chameleon Response Data
    """

    def __init__(self, cmd, status, data=b'', parsed=None):
        self.cmd = cmd
        self.status = status
        self.data: bytes = data
        self.parsed = parsed


class ChameleonCom:
    """
        Chameleon device base class
        Communication and Data frame implemented
    """
    data_frame_sof = 0x11
    data_max_length = 4096
    commands = []

    def __init__(self):
        """
            Create a chameleon device instance
        """
        self.transport: Union[serial.Serial, socket.socket, None] = None
        self.transport_type = TransportType.NONE
        self.send_data_queue = queue.Queue()
        self.wait_response_map = {}
        self.event_closing = threading.Event()

    def isOpen(self) -> bool:
        """
            Chameleon is connected and init.

        :return:
        """
        return self.transport is not None and (self.transport_type is TransportType.SOCKET or self.transport.is_open)

    def open(self, port) -> "ChameleonCom":
        """
            Open chameleon port to communication
            And init some variables

        :param port: com port, comXXX or ttyXXX
        :return:
        """
        if not self.isOpen():
            error = None
            try:
                # open serial port
                if port.startswith('tcp:'):
                    host, _, port = port[4:].partition(':')
                    if not host or not port:
                        sys.exit(color_string(CR, 'Usage: tcp:127.0.0.1:4321'))
                    self.transport = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    print('Connecting to', host, int(port))
                    self.transport.connect((host, int(port)))
                    self.transport_type = TransportType.SOCKET
                else:
                    if ANDROID:
                        sys.exit(color_string(
                            CR, 'COM port is not supported on Android, make a USB-serial to TCP communication bridge'))
                    import serial  # lazy: only needed to open a real hardware port
                    self.transport = serial.Serial(port=port, baudrate=115200)
                    self.transport_type = TransportType.SERIAL
            except Exception as e:
                error = e
            finally:
                if error is not None:
                    raise OpenFailException(error)
            assert self.transport is not None
            assert self.transport_type is not TransportType.NONE
            if self.transport_type is TransportType.SERIAL:
                try:
                    self.transport.dtr = True  # must make dtr enable
                except Exception:
                    # not all serial support dtr, e.g. virtual serial over BLE
                    pass
                self.transport.timeout = THREAD_BLOCKING_TIMEOUT
            else:  # SOCKET
                self.transport.settimeout(THREAD_BLOCKING_TIMEOUT)
            # clear variable
            self.send_data_queue.queue.clear()
            self.wait_response_map.clear()
            # Start a sub thread to process data
            self.event_closing.clear()
            threading.Thread(target=self.thread_data_receive).start()
            threading.Thread(target=self.thread_data_transfer).start()
            threading.Thread(target=self.thread_check_timeout).start()
        return self

    def check_open(self) -> None:
        """

        :return:
        """
        if not self.isOpen():
            raise NotOpenException("Please call open() function to start device.")

    @staticmethod
    def lrc_calc(array: Union[bytearray, bytes]) -> int:
        """
            Calc lrc and auto cut byte.

        :param array: value array
        :return: u8 result
        """
        # add and cut byte and return
        ret = 0x00
        for b in array:
            ret += b
            ret &= 0xFF
        return (0x100 - ret) & 0xFF

    def close(self):
        """
            Close chameleon and clear variable.

        :return:
        """
        self.event_closing.set()
        try:
            assert self.transport is not None
            if self.transport_type is TransportType.SOCKET:
                # LOCAL PATCH (tenor/rekey): shutdown() requires a `how` argument; the bare
                # call raised TypeError and skipped close() below. Guard it in its own
                # try/except so a peer-closed socket (OSError) still falls through to close().
                try:
                    self.transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.transport.close()
        except Exception:
            pass
        finally:
            self.transport = None
        self.wait_response_map.clear()
        self.send_data_queue.queue.clear()

    def thread_data_receive(self):
        """
            Sub thread to receive data from chameleon device.

        :return:
        """
        data_buffer = bytearray()
        data_position = 0
        data_cmd = 0x0000
        data_status = 0x0000
        data_length = 0x0000

        while self.isOpen():
            # receive
            assert self.transport_type is not TransportType.NONE
            if self.transport_type is TransportType.SERIAL:
                try:
                    assert self.transport is not None
                    data_bytes = bytearray(self.transport.read())
                except Exception as e:
                    if not self.event_closing.is_set():
                        print(f"Serial Error {e}, thread for receiver exit.")
                    self.close()
                    break
            else:  # SOCKET
                try:
                    data_bytes = bytearray(self.transport.recv(1024))
                except socket.timeout:
                    continue
                except OSError:
                    print(color_string(CR, 'socket closed'))
                    self.transport = None
                    break
                # LOCAL PATCH (tenor/rekey): an orderly TCP close makes recv() return b'',
                # not raise. Treat zero bytes as EOF and exit like the OSError branch;
                # otherwise this loop hot-spins on empty reads forever.
                if len(data_bytes) == 0:
                    print(color_string(CR, 'socket closed'))
                    self.transport = None
                    break

            while len(data_bytes) > 0:
                data_byte = data_bytes[0]
                data_bytes = data_bytes[1:]
                data_buffer.append(data_byte)
                if data_position < struct.calcsize('!BB'):  # start of frame + lrc1
                    if data_position == 0:
                        if data_buffer[data_position] != self.data_frame_sof:
                            print("Data frame no sof byte.")
                            data_position = 0
                            data_buffer.clear()
                            continue
                    if data_position == struct.calcsize('!B'):
                        if data_buffer[data_position] != self.lrc_calc(data_buffer[:data_position]):
                            data_position = 0
                            data_buffer.clear()
                            print("Data frame sof lrc error.")
                            continue
                elif data_position == struct.calcsize('!BBHHH'):  # frame head lrc
                    if data_buffer[data_position] != self.lrc_calc(data_buffer[:data_position]):
                        data_position = 0
                        data_buffer.clear()
                        print("Data frame head lrc error.")
                        continue
                    # frame head complete, cache info
                    _, _, data_cmd, data_status, data_length = struct.unpack("!BBHHH", data_buffer[:data_position])
                    if data_length > self.data_max_length:
                        data_position = 0
                        data_buffer.clear()
                        print("Data frame data length larger than max.")
                        continue
                elif data_position > struct.calcsize('!BBHHH'):  # // frame data
                    if data_position == (struct.calcsize(f'!BBHHHB{data_length}s')):
                        if data_buffer[data_position] == self.lrc_calc(data_buffer[:data_position]):
                            # ok, lrc for data is correct.
                            # and we are receive completed
                            # print(f"Buffer data = {data_buffer.hex()}")
                            data_response = bytes(data_buffer[struct.calcsize('!BBHHHB'):
                                                              struct.calcsize(f'!BBHHHB{data_length}s')])
                            if DEBUG:
                                try:
                                    command = Command(data_cmd)
                                    command_string = f"{data_cmd} {command.name}"
                                except ValueError:
                                    command_string = f"{data_cmd} (unknown)"
                                try:
                                    status_string = str(Status(data_status))
                                    if data_status == Status.SUCCESS:
                                        status_string = color_string((CG, status_string.ljust(30)))
                                    else:
                                        status_string = color_string((CR, status_string.ljust(30)))
                                except ValueError:
                                    status_string = f"{data_status:30x}"
                                    response = data_response.hex() if data_response is not None else ""
                                    print(
                                        f"<={color_string((CC, command_string.ljust(40)), (CR, status_string), (CY, response))}")
                            if data_cmd in self.wait_response_map:
                                # call processor
                                if 'callback' in self.wait_response_map[data_cmd]:
                                    fn_call = self.wait_response_map[data_cmd]['callback']
                                else:
                                    fn_call = None
                                if callable(fn_call):
                                    # delete wait task from map
                                    del self.wait_response_map[data_cmd]
                                    fn_call(data_cmd, data_status, data_response)
                                else:
                                    self.wait_response_map[data_cmd]['response'] = Response(data_cmd, data_status,
                                                                                            data_response)
                            else:
                                print(f"No task wait process: ${data_cmd}")
                        else:
                            print("Data frame global lrc error.")
                        data_position = 0
                        data_buffer.clear()
                        continue
                data_position += 1

    def thread_data_transfer(self):
        """
            Sub thread to transfer data to chameleon device.

        :return:
        """
        while self.isOpen():
            # get a task from queue(if exists)
            try:
                task = self.send_data_queue.get(block=True, timeout=THREAD_BLOCKING_TIMEOUT)
            except queue.Empty:
                continue
            task_cmd = task['cmd']
            task_timeout = task['timeout']

exec
/bin/zsh -lc 'git --no-pager diff -- app/Sources/Engine/BLEManager.swift app/Sources/AppModel.swift app/Sources/Shell/RootView.swift probe/chameleon_d.py probe/chameleon/chameleon_com.py' in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 1655ms:
2026-07-21 11:44:17.589 xcodebuild[47474:3437413]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:44:17.721 xcodebuild[47474:3437411] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-nP6jVtTK' (errno=Operation not permitted)
2026-07-21 11:44:18.156 xcodebuild[47477:3437428]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:44:18.286 xcodebuild[47477:3437426] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/app/Sources/AppModel.swift b/app/Sources/AppModel.swift
index c2a0610..6083fbe 100644
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
@@ -140,6 +145,12 @@ final class AppModel {
     /// the teardown await cannot grab the just-terminated bridge or the stale card.
     private var swapping = false
 
+    /// A BLE connect is in flight: the radio link + loopback bridge are being brought up,
+    /// which can take up to ~15s before `swapDevice` even runs. Reserved for that whole
+    /// window so a monitor tick or a second selection cannot interleave and swap the device
+    /// out from under the pending connect.
+    private var bleConnecting = false
+
     /// A device op already owns the reader. Reconnect / swap must not replace the
     /// bridge under one, and a second op must not start while one runs. Slot ops are
     /// included so a slot edit and a decode / clone can never overlap on the reader.
@@ -173,14 +184,20 @@ final class AppModel {
     var activeDeviceFamily: String { descriptor.family }
 
     /// A device swap / (re)connect is in flight, exposed read-only so the Connect
-    /// surface can show a spinner and disable Rescan while it runs.
-    var connecting: Bool { swapping }
+    /// surface can show a spinner and disable Rescan while it runs. A BLE connect is
+    /// included: its link-up window precedes the swap and must read as busy too.
+    var connecting: Bool { swapping || bleConnecting }
+
+    /// True when the active device is driven over a BLE loopback bridge (its port is pinned
+    /// to `tcp:...`). Firmware update over BLE is unsafe (the USB DFU flasher could hit the
+    /// wrong physical device), so the firmware UI and its methods gate on this being false.
+    var deviceOverBLE: Bool { descriptor.portOverride?.hasPrefix("tcp:") == true }
 
     /// The manual Connect controls (Rescan, the serial-port rows, the free-text connect)
     /// may act only when no swap or device op owns the reader - the same guard `connect`,
     /// `rescan`, and `connectManual` enforce - so a tap during one is a disabled control,
     /// never a silent no-op that the user reads as the app ignoring them.
-    var canChangeDevice: Bool { !swapping && !deviceBusy }
+    var canChangeDevice: Bool { !swapping && !deviceBusy && !bleConnecting }
 
     /// The user's editable keys (Settings > Dictionaries), tried before the
     /// daemon's large built-in dictionary.
@@ -252,6 +269,11 @@ final class AppModel {
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
 
@@ -296,6 +318,52 @@ final class AppModel {
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
+        // Reserve the whole connect (up to ~15s) so a monitor tick / second selection cannot
+        // interleave: bleConnecting gates canChangeDevice, connecting, and the monitor. It does
+        // NOT block the swapDevice call below (that guards on its own `swapping`).
+        bleConnecting = true
+        defer { bleConnecting = false }
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
@@ -343,7 +411,7 @@ final class AppModel {
     func monitor() async {
         while !Task.isCancelled {
             try? await Task.sleep(for: .seconds(1.5))
-            if deviceBusy || swapping { continue }
+            if deviceBusy || swapping || bleConnecting { continue }
             // Hot-swap detection runs even while emulating: unplugging an emulating
             // Chameleon and attaching another device must still tear down + swap. It is a
             // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
@@ -384,6 +452,10 @@ final class AppModel {
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
@@ -396,6 +468,12 @@ final class AppModel {
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
@@ -1210,6 +1288,9 @@ final class AppModel {
     /// the current version with a null latest + a note.
     func checkFirmware() async {
         guard capabilities.dfu, !swapping, !deviceBusy else { return }
+        // Firmware update is never offered over a BLE link (the USB DFU flasher could hit the
+        // wrong physical device). Defensive: the button is already hidden over BLE.
+        guard !deviceOverBLE else { return }
         flashError = nil
         // A device already in the bootloader has no command interface to query: leave the
         // status empty (the sheet shows the Ultra/Lite recovery choice) rather than erroring.
@@ -1227,6 +1308,9 @@ final class AppModel {
     /// mistake the reboot for an unplug.
     func flashFirmware(model: String?) async {
         guard capabilities.dfu, !swapping, !deviceBusy else { return }
+        // Never flash over a BLE link (the USB DFU flasher could hit the wrong physical
+        // device). Defensive: the firmware button is hidden over BLE.
+        guard !deviceOverBLE else { return }
         flashing = true
         flashDone = false
         flashError = nil
diff --git a/app/Sources/Shell/RootView.swift b/app/Sources/Shell/RootView.swift
index 520e3f9..bcd65c5 100644
--- a/app/Sources/Shell/RootView.swift
+++ b/app/Sources/Shell/RootView.swift
@@ -273,7 +273,9 @@ private struct ActionBar: View {
             // Firmware update (DFU), gated on the device advertising it: the X7 has
             // dfu:false and never shows it. Opening the sheet reads the current + latest
             // firmware. Disabled while any device op owns the reader.
-            if model.capabilities.dfu {
+            // Never over a BLE link: the DFU flasher runs over USB and could flash the
+            // wrong physical device, so the firmware action is hidden while connected over BLE.
+            if model.capabilities.dfu && !model.deviceOverBLE {
                 // Reachable when the reader is online OR the device is stuck in the
                 // bootloader (deviceInDFU) - the latter is exactly when flash-recovery is needed.
                 ActionButton(title: l.t("firmware"), icon: "arrow.up.circle",
diff --git a/probe/chameleon/chameleon_com.py b/probe/chameleon/chameleon_com.py
index bd6a3d2..87851eb 100644
--- a/probe/chameleon/chameleon_com.py
+++ b/probe/chameleon/chameleon_com.py
@@ -171,7 +171,13 @@ class ChameleonCom:
         try:
             assert self.transport is not None
             if self.transport_type is TransportType.SOCKET:
-                self.transport.shutdown()
+                # LOCAL PATCH (tenor/rekey): shutdown() requires a `how` argument; the bare
+                # call raised TypeError and skipped close() below. Guard it in its own
+                # try/except so a peer-closed socket (OSError) still falls through to close().
+                try:
+                    self.transport.shutdown(socket.SHUT_RDWR)
+                except OSError:
+                    pass
             self.transport.close()
         except Exception:
             pass
@@ -213,6 +219,13 @@ class ChameleonCom:
                     print(color_string(CR, 'socket closed'))
                     self.transport = None
                     break
+                # LOCAL PATCH (tenor/rekey): an orderly TCP close makes recv() return b'',
+                # not raise. Treat zero bytes as EOF and exit like the OSError branch;
+                # otherwise this loop hot-spins on empty reads forever.
+                if len(data_bytes) == 0:
+                    print(color_string(CR, 'socket closed'))
+                    self.transport = None
+                    break
 
             while len(data_bytes) > 0:
                 data_byte = data_bytes[0]
diff --git a/probe/chameleon_d.py b/probe/chameleon_d.py
index af4a89b..990e3e7 100644
--- a/probe/chameleon_d.py
+++ b/probe/chameleon_d.py
@@ -1940,6 +1940,13 @@ class Daemon:
         mid-write abort can brick, so it is never checked past step 5. `dfu_flash` is armed as
         flash-pending at DISPATCH (run()), so an EOF/shutdown while it is in flight always
         joins UNBOUNDED and never abandons a flash."""
+        # Fail closed over BLE: when the active port is the Swift loopback TCP bridge
+        # (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil flashes
+        # over USB serial - it would enter-bootloader / write a DIFFERENT physical device that
+        # happens to be on USB, using this BLE device's model. Refuse before any download,
+        # model read, reboot, or subprocess (a cross-device brick).
+        if isinstance(self._port, str) and self._port.startswith("tcp:"):
+            raise RuntimeError("firmware update over bluetooth is not supported; connect over usb")
         supplied_model = self._norm_model(p.get("model"))
         dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
         cdc_ports = self._find_cdc_ports()

exec
/bin/zsh -lc "git status --short -- app probe && nl -ba app/Sources/Engine/BLEManager.swift | sed -n '1,420p' && rg -n \"BLEManager|attemptGen|awaitingHandshakeAck|outbox|drainOutbox|canSendWrite|teardownLink|continuation|NWConnection|stateUpdateHandler|viabilityUpdateHandler|betterPathUpdateHandler\" app/Sources/Engine/BLEManager.swift app/Sources/AppModel.swift app/Sources/Shell/RootView.swift probe/chameleon_d.py probe/chameleon/chameleon_com.py" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 1674ms:
2026-07-21 11:44:25.858 xcodebuild[47490:3437544]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:44:25.992 xcodebuild[47490:3437543] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-xwikqNWR' (errno=Operation not permitted)
2026-07-21 11:44:26.424 xcodebuild[47494:3437563]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:44:26.556 xcodebuild[47494:3437562] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
 M app/Sources/AppModel.swift
 M app/Sources/Brand/L10n.swift
 M app/Sources/Shell/RootView.swift
 M app/Sources/Views/ConnectView.swift
 M app/project.yml
 M app/tools/tenorrekey.entitlements
 M probe/chameleon/chameleon_com.py
 M probe/chameleon_d.py
 M probe/test_chameleon.py
?? app/Sources/Engine/BLEManager.swift
?? probe/native/darkside/
?? probe/x7_reader.py
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
    71	    /// Monotonic connect-attempt generation. Bumped on every new connect and on every
    72	    /// teardown, so a delegate callback / timeout belonging to a superseded attempt (a
    73	    /// BLE-A -> BLE-B swap, a double-tap) is recognised as stale and ignored: it must not
    74	    /// resolve the continuation or tear down the newer link.
    75	    private var attemptGen = 0
    76	    /// True between issuing the handshake `.withResponse` write and its ack: the loopback
    77	    /// bridge is started only once that ack lands (didWriteValueFor), not immediately.
    78	    private var awaitingHandshakeAck = false
    79	
    80	    // ---- Loopback TCP bridge (Network framework) ---------------------------
    81	    private var listener: NWListener?
    82	    private var tcpConnection: NWConnection?
    83	    private let bridgeQueue = DispatchQueue(label: "vn.tenor.rekey.ble-bridge")
    84	    /// The single ordered TCP -> BLE outgoing buffer. Bytes arriving from the daemon are
    85	    /// appended here on the main actor and drained to the RX characteristic in order, only
    86	    /// while the peripheral can accept a no-response write (real backpressure), so nothing
    87	    /// is dropped and the byte order the daemon sent is preserved.
    88	    private var outbox = Data()
    89	
    90	    // ---- Scanning ----------------------------------------------------------
    91	
    92	    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
    93	    /// call (this is what raises the Bluetooth permission prompt, not app launch). If the
    94	    /// radio is not powered on yet, the scan begins from `centralManagerDidUpdateState`.
    95	    func startScan() {
    96	        if central == nil {
    97	            central = CBCentralManager(delegate: self, queue: .main)
    98	        }
    99	        scanRequested = true
   100	        if central?.state == .poweredOn { beginScan() }
   101	    }
   102	
   103	    /// Stop scanning and clear the pending-scan intent.
   104	    func stopScan() {
   105	        scanRequested = false
   106	        central?.stopScan()
   107	        scanning = false
   108	    }
   109	
   110	    private func beginScan() {
   111	        guard let central, central.state == .poweredOn else { return }
   112	        devices = []
   113	        discovered = [:]
   114	        central.scanForPeripherals(withServices: [Self.nusService, Self.dfuService],
   115	                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
   116	        scanning = true
   117	    }
   118	
   119	    // ---- Connect / disconnect ----------------------------------------------
   120	
   121	    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
   122	    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
   123	    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
   124	    /// service / characteristic, or a stall past the ~15s overall timeout.
   125	    func connect(_ id: String) async throws -> Int {
   126	        if central == nil {
   127	            central = CBCentralManager(delegate: self, queue: .main)
   128	        }
   129	        guard let central, state == .poweredOn else { throw BLEError.notPoweredOn }
   130	        guard connectContinuation == nil else { throw BLEError.busy }
   131	
   132	        let target: CBPeripheral
   133	        if let p = discovered[id] {
   134	            target = p
   135	        } else if let uuid = UUID(uuidString: id),
   136	                  let p = central.retrievePeripherals(withIdentifiers: [uuid]).first {
   137	            target = p
   138	            discovered[id] = p
   139	        } else {
   140	            throw BLEError.unknownDevice
   141	        }
   142	
   143	        stopScan()
   144	        teardownLink()   // drop any prior link before opening a new one
   145	
   146	        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Int, Error>) in
   147	            self.attemptGen += 1
   148	            let gen = self.attemptGen
   149	            self.connectContinuation = cont
   150	            self.peripheral = target
   151	            self.awaitingHandshakeAck = false
   152	            target.delegate = self
   153	            // One overall deadline: any stalled step (connect, discover, notify, bridge)
   154	            // fails the whole connect rather than orphaning the continuation. Scoped to this
   155	            // attempt: a superseded attempt's timeout is a no-op, and a cancelled sleep
   156	            // (the task was torn down) returns cleanly rather than failing a later attempt.
   157	            self.connectTimeoutTask = Task { [weak self] in
   158	                do { try await Task.sleep(for: .seconds(15)) }
   159	                catch { return }
   160	                guard let self else { return }
   161	                guard gen == self.attemptGen, self.connectContinuation != nil else { return }
   162	                self.failConnect(BLEError.timeout)
   163	            }
   164	            central.connect(target, options: nil)
   165	        }
   166	    }
   167	
   168	    /// Whether `p` is the peripheral of the CURRENT attempt / link. A callback from any other
   169	    /// (stale) peripheral must be ignored so it cannot resolve the continuation or tear down
   170	    /// the newer link.
   171	    private func isActive(_ p: CBPeripheral) -> Bool { p.identifier == peripheral?.identifier }
   172	
   173	    /// Tear the link down: cancel the peripheral connection, stop + close the listener and
   174	    /// TCP connection, clear `connectedDeviceID`. Idempotent, and if a connect is still in
   175	    /// flight it fails that continuation instead.
   176	    func disconnect() {
   177	        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
   178	        if connectContinuation != nil {
   179	            failConnect(BLEError.disconnected)
   180	            return
   181	        }
   182	        teardownLink()
   183	    }
   184	
   185	    /// Resolve the in-flight connect with the bound port. No-op if there is no pending
   186	    /// continuation (already resolved / torn down).
   187	    private func succeedConnect(port: Int) {
   188	        guard let cont = connectContinuation else { return }
   189	        connectContinuation = nil
   190	        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
   191	        connectedDeviceID = peripheral?.identifier.uuidString
   192	        cont.resume(returning: port)
   193	    }
   194	
   195	    /// Fail the in-flight connect and tear the partial link down. No-op if there is no
   196	    /// pending continuation, so it is safe to call from any delegate error path.
   197	    private func failConnect(_ error: Error) {
   198	        guard let cont = connectContinuation else { return }
   199	        connectContinuation = nil
   200	        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
   201	        teardownLink()
   202	        cont.resume(throwing: error)
   203	    }
   204	
   205	    /// Synchronous teardown of the BLE link + its bridge. Shared by disconnect, a fail, a
   206	    /// radio-off, and a peripheral drop, so they cannot drift.
   207	    private func teardownLink() {
   208	        // Supersede any in-flight attempt: a callback / timeout still queued for the link
   209	        // being torn down now sees a newer generation (or a nil peripheral) and is ignored.
   210	        attemptGen += 1
   211	        awaitingHandshakeAck = false
   212	        teardownBridge()
   213	        if let p = peripheral, let central { central.cancelPeripheralConnection(p) }
   214	        peripheral = nil
   215	        rxChar = nil
   216	        txChar = nil
   217	        connectedDeviceID = nil
   218	    }
   219	
   220	    private func teardownBridge() {
   221	        outbox = Data()
   222	        // Clear the handlers before cancelling so the cancelled Network objects and their
   223	        // closures are released promptly on repeated reconnects (no lingering retain).
   224	        if let conn = tcpConnection {
   225	            conn.stateUpdateHandler = nil
   226	            conn.cancel()
   227	        }
   228	        tcpConnection = nil
   229	        if let l = listener {
   230	            l.stateUpdateHandler = nil
   231	            l.newConnectionHandler = nil
   232	            l.cancel()
   233	        }
   234	        listener = nil
   235	    }
   236	
   237	    // ---- Loopback TCP bridge -----------------------------------------------
   238	
   239	    /// Bind an NWListener to 127.0.0.1 on an OS-picked ephemeral port and, once it is ready,
   240	    /// report the bound port via `onReady`. Accepts exactly one inbound connection (the
   241	    /// daemon) and relays bytes both ways.
   242	    private func startBridge(onReady: @escaping @MainActor (Result<Int, Error>) -> Void) {
   243	        let params = NWParameters.tcp
   244	        // Bind specifically to loopback on an ephemeral port (port 0 -> OS assigns).
   245	        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: 0)
   246	        let listener: NWListener
   247	        do {
   248	            listener = try NWListener(using: params)
   249	        } catch {
   250	            onReady(.failure(error))
   251	            return
   252	        }
   253	        self.listener = listener
   254	        // Do NOT capture `listener` strongly inside its own handler (that is a retain cycle):
   255	        // read the bound port back off `self.listener`, which is this same listener.
   256	        listener.stateUpdateHandler = { [weak self] st in
   257	            switch st {
   258	            case .ready:
   259	                Task { @MainActor in
   260	                    guard let self else { return }
   261	                    if let raw = self.listener?.port?.rawValue { onReady(.success(Int(raw))) }
   262	                    else { onReady(.failure(BLEError.bridge)) }
   263	                }
   264	            case .failed:
   265	                Task { @MainActor in
   266	                    guard let self else { return }
   267	                    if self.connectContinuation != nil { onReady(.failure(BLEError.bridge)) }
   268	                    else { self.disconnect() }
   269	                }
   270	            default:
   271	                break
   272	            }
   273	        }
   274	        listener.newConnectionHandler = { [weak self] conn in
   275	            Task { @MainActor in self?.acceptConnection(conn) }
   276	        }
   277	        listener.start(queue: bridgeQueue)
   278	    }
   279	
   280	    /// Accept exactly one inbound connection (the daemon); cancel any later one. Start the
   281	    /// receive loop that relays daemon -> BLE bytes.
   282	    private func acceptConnection(_ conn: NWConnection) {
   283	        guard tcpConnection == nil else { conn.cancel(); return }
   284	        tcpConnection = conn
   285	        conn.stateUpdateHandler = { [weak self] st in
   286	            switch st {
   287	            case .failed, .cancelled:
   288	                Task { @MainActor in self?.handleTCPClosed(conn) }
   289	            default:
   290	                break
   291	            }
   292	        }
   293	        conn.start(queue: bridgeQueue)
   294	        receiveLoop(conn)
   295	    }
   296	
   297	    /// One turn of the daemon -> BLE relay. The receive completion runs on the bridge queue
   298	    /// and hops onto the main actor exactly once, where it appends the bytes to the single
   299	    /// ordered `outbox`, drains what the peripheral can currently accept, and only THEN
   300	    /// re-arms the next receive. Handling one completion fully before arming the next keeps
   301	    /// byte order intact (no concurrent, unordered write vs re-arm tasks).
   302	    private func receiveLoop(_ conn: NWConnection) {
   303	        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
   304	            Task { @MainActor in
   305	                guard let self else { return }
   306	                if let data, !data.isEmpty {
   307	                    self.outbox.append(data)
   308	                    self.drainOutbox()
   309	                }
   310	                if isComplete || error != nil {
   311	                    self.handleTCPClosed(conn)
   312	                } else {
   313	                    self.receiveLoop(conn)
   314	                }
   315	            }
   316	        }
   317	    }
   318	
   319	    /// Drain the ordered outbox to the device with real backpressure: while there are bytes
   320	    /// AND the peripheral can accept a no-response write, pop up to the max no-response write
   321	    /// length and write it without response. Stops when the buffer empties or the peripheral
   322	    /// stops accepting; `peripheralIsReady(toSendWriteWithoutResponse:)` resumes it. This is
   323	    /// the NUS reliable-write pattern: nothing is dropped and the order is preserved.
   324	    private func drainOutbox() {
   325	        guard let peripheral, let rx = rxChar else { return }
   326	        let chunk = maxWriteLen > 0 ? maxWriteLen : 20
   327	        while !outbox.isEmpty, peripheral.canSendWriteWithoutResponse {
   328	            let n = min(chunk, outbox.count)
   329	            let piece = Data(outbox.prefix(n))
   330	            peripheral.writeValue(piece, for: rx, type: .withoutResponse)
   331	            outbox.removeFirst(n)
   332	        }
   333	    }
   334	
   335	    /// Relay device notify bytes to the daemon over TCP.
   336	    private func sendToTCP(_ data: Data) {
   337	        guard let conn = tcpConnection else { return }
   338	        conn.send(content: data, completion: .contentProcessed { _ in })
   339	    }
   340	
   341	    /// The daemon closed / dropped its side: tear the whole link down (the listener only ever
   342	    /// accepts one connection, so a reconnect needs a fresh link, which phase 2 owns).
   343	    private func handleTCPClosed(_ conn: NWConnection) {
   344	        guard conn === tcpConnection else { return }
   345	        disconnect()
   346	    }
   347	
   348	    // ---- State mapping ------------------------------------------------------
   349	
   350	    private static func mapState(_ s: CBManagerState) -> BLEState {
   351	        switch s {
   352	        case .poweredOn: return .poweredOn
   353	        case .poweredOff: return .poweredOff
   354	        case .unauthorized: return .unauthorized
   355	        case .unsupported: return .unsupported
   356	        default: return .unknown
   357	        }
   358	    }
   359	
   360	    enum BLEError: Error, CustomStringConvertible {
   361	        case notPoweredOn, unknownDevice, serviceNotFound, characteristicNotFound
   362	        case connectionFailed, disconnected, timeout, bridge, busy
   363	        var description: String {
   364	            switch self {
   365	            case .notPoweredOn:          return "Bluetooth is not powered on"
   366	            case .unknownDevice:         return "unknown Bluetooth device"
   367	            case .serviceNotFound:       return "the device does not expose the Chameleon UART service"
   368	            case .characteristicNotFound: return "the Chameleon UART characteristics were not found"
   369	            case .connectionFailed:      return "the Bluetooth connection failed"
   370	            case .disconnected:          return "the Bluetooth device disconnected"
   371	            case .timeout:               return "the Bluetooth connection timed out"
   372	            case .bridge:                return "the local bridge could not be started"
   373	            case .busy:                  return "a connection is already in progress"
   374	            }
   375	        }
   376	    }
   377	}
   378	
   379	// MARK: - CBCentralManagerDelegate
   380	
   381	extension BLEManager: CBCentralManagerDelegate {
   382	    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
   383	        MainActor.assumeIsolated {
   384	            self.state = Self.mapState(central.state)
   385	            if central.state == .poweredOn {
   386	                if self.scanRequested { self.beginScan() }
   387	            } else {
   388	                self.scanning = false
   389	                if self.connectContinuation != nil {
   390	                    self.failConnect(BLEError.notPoweredOn)
   391	                } else if self.connectedDeviceID != nil {
   392	                    self.teardownLink()
   393	                }
   394	            }
   395	        }
   396	    }
   397	
   398	    nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
   399	                                    advertisementData: [String: Any], rssi RSSI: NSNumber) {
   400	        MainActor.assumeIsolated {
   401	            let id = peripheral.identifier.uuidString
   402	            self.discovered[id] = peripheral
   403	            let advName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
   404	            let name = peripheral.name ?? advName ?? "Chameleon"
   405	            var isDFU = false
   406	            if let services = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] {
   407	                isDFU = services.contains(Self.dfuService)
   408	            }
   409	            let device = BLEDevice(id: id, name: name, rssi: RSSI.intValue, isDFU: isDFU)
   410	            if let idx = self.devices.firstIndex(where: { $0.id == id }) {
   411	                self.devices[idx] = device
   412	            } else {
   413	                self.devices.append(device)
   414	            }
   415	            self.devices.sort { $0.rssi > $1.rssi }
   416	        }
   417	    }
   418	
   419	    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
   420	        MainActor.assumeIsolated {
app/Sources/Engine/BLEManager.swift:33:final class BLEManager: NSObject {
app/Sources/Engine/BLEManager.swift:66:    /// The connect() continuation, resolved by the delegate chain or the timeout. Non-nil
app/Sources/Engine/BLEManager.swift:74:    /// resolve the continuation or tear down the newer link.
app/Sources/Engine/BLEManager.swift:75:    private var attemptGen = 0
app/Sources/Engine/BLEManager.swift:78:    private var awaitingHandshakeAck = false
app/Sources/Engine/BLEManager.swift:82:    private var tcpConnection: NWConnection?
app/Sources/Engine/BLEManager.swift:88:    private var outbox = Data()
app/Sources/Engine/BLEManager.swift:144:        teardownLink()   // drop any prior link before opening a new one
app/Sources/Engine/BLEManager.swift:147:            self.attemptGen += 1
app/Sources/Engine/BLEManager.swift:148:            let gen = self.attemptGen
app/Sources/Engine/BLEManager.swift:151:            self.awaitingHandshakeAck = false
app/Sources/Engine/BLEManager.swift:154:            // fails the whole connect rather than orphaning the continuation. Scoped to this
app/Sources/Engine/BLEManager.swift:161:                guard gen == self.attemptGen, self.connectContinuation != nil else { return }
app/Sources/Engine/BLEManager.swift:169:    /// (stale) peripheral must be ignored so it cannot resolve the continuation or tear down
app/Sources/Engine/BLEManager.swift:175:    /// flight it fails that continuation instead.
app/Sources/Engine/BLEManager.swift:182:        teardownLink()
app/Sources/Engine/BLEManager.swift:186:    /// continuation (already resolved / torn down).
app/Sources/Engine/BLEManager.swift:196:    /// pending continuation, so it is safe to call from any delegate error path.
app/Sources/Engine/BLEManager.swift:201:        teardownLink()
app/Sources/Engine/BLEManager.swift:207:    private func teardownLink() {
app/Sources/Engine/BLEManager.swift:210:        attemptGen += 1
app/Sources/Engine/BLEManager.swift:211:        awaitingHandshakeAck = false
app/Sources/Engine/BLEManager.swift:221:        outbox = Data()
app/Sources/Engine/BLEManager.swift:225:            conn.stateUpdateHandler = nil
app/Sources/Engine/BLEManager.swift:230:            l.stateUpdateHandler = nil
app/Sources/Engine/BLEManager.swift:256:        listener.stateUpdateHandler = { [weak self] st in
app/Sources/Engine/BLEManager.swift:282:    private func acceptConnection(_ conn: NWConnection) {
app/Sources/Engine/BLEManager.swift:285:        conn.stateUpdateHandler = { [weak self] st in
app/Sources/Engine/BLEManager.swift:299:    /// ordered `outbox`, drains what the peripheral can currently accept, and only THEN
app/Sources/Engine/BLEManager.swift:302:    private func receiveLoop(_ conn: NWConnection) {
app/Sources/Engine/BLEManager.swift:307:                    self.outbox.append(data)
app/Sources/Engine/BLEManager.swift:308:                    self.drainOutbox()
app/Sources/Engine/BLEManager.swift:319:    /// Drain the ordered outbox to the device with real backpressure: while there are bytes
app/Sources/Engine/BLEManager.swift:324:    private func drainOutbox() {
app/Sources/Engine/BLEManager.swift:327:        while !outbox.isEmpty, peripheral.canSendWriteWithoutResponse {
app/Sources/Engine/BLEManager.swift:328:            let n = min(chunk, outbox.count)
app/Sources/Engine/BLEManager.swift:329:            let piece = Data(outbox.prefix(n))
app/Sources/Engine/BLEManager.swift:331:            outbox.removeFirst(n)
app/Sources/Engine/BLEManager.swift:343:    private func handleTCPClosed(_ conn: NWConnection) {
app/Sources/Engine/BLEManager.swift:381:extension BLEManager: CBCentralManagerDelegate {
app/Sources/Engine/BLEManager.swift:392:                    self.teardownLink()
app/Sources/Engine/BLEManager.swift:443:                self.teardownLink()
app/Sources/Engine/BLEManager.swift:451:extension BLEManager: CBPeripheralDelegate {
app/Sources/Engine/BLEManager.swift:489:            self.awaitingHandshakeAck = true
app/Sources/Engine/BLEManager.swift:500:            guard self.awaitingHandshakeAck, characteristic.uuid == Self.nusRX else { return }
app/Sources/Engine/BLEManager.swift:501:            self.awaitingHandshakeAck = false
app/Sources/Engine/BLEManager.swift:518:            self.drainOutbox()
app/Sources/AppModel.swift:46:    let ble = BLEManager()

exec
/bin/zsh -lc "nl -ba app/Sources/Engine/BLEManager.swift | sed -n '410,560p'; nl -ba app/Sources/AppModel.swift | sed -n '235,500p'; nl -ba app/Sources/AppModel.swift | sed -n '1260,1355p'; nl -ba probe/chameleon_d.py | sed -n '1880,1995p'; nl -ba probe/chameleon/chameleon_com.py | sed -n '145,245p'; rg -n \"def dfu_check|def dfu_flash|_port|CHAMELEON_PORT|dfu_check|dfu_flash\" probe/chameleon_d.py probe/test_chameleon.py" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
   410	            if let idx = self.devices.firstIndex(where: { $0.id == id }) {
   411	                self.devices[idx] = device
   412	            } else {
   413	                self.devices.append(device)
   414	            }
   415	            self.devices.sort { $0.rssi > $1.rssi }
   416	        }
   417	    }
   418	
   419	    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
   420	        MainActor.assumeIsolated {
   421	            guard self.isActive(peripheral) else { return }   // ignore a stale attempt's connect
   422	            peripheral.discoverServices([Self.nusService])
   423	        }
   424	    }
   425	
   426	    nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
   427	        MainActor.assumeIsolated {
   428	            // Only the active attempt's failure fails the current connect; a superseded
   429	            // attempt's late failure must not tear down the newer link.
   430	            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
   431	            self.failConnect(BLEError.connectionFailed)
   432	        }
   433	    }
   434	
   435	    nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
   436	        MainActor.assumeIsolated {
   437	            // A stale peripheral's disconnect (e.g. the prior device dropping after a swap)
   438	            // must neither fail the new connect nor tear down the new link.
   439	            guard self.isActive(peripheral) else { return }
   440	            if self.connectContinuation != nil {
   441	                self.failConnect(BLEError.disconnected)
   442	            } else if self.connectedDeviceID == peripheral.identifier.uuidString {
   443	                self.teardownLink()
   444	            }
   445	        }
   446	    }
   447	}
   448	
   449	// MARK: - CBPeripheralDelegate
   450	
   451	extension BLEManager: CBPeripheralDelegate {
   452	    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
   453	        MainActor.assumeIsolated {
   454	            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
   455	            if error != nil { self.failConnect(BLEError.serviceNotFound); return }
   456	            guard let svc = peripheral.services?.first(where: { $0.uuid == Self.nusService }) else {
   457	                self.failConnect(BLEError.serviceNotFound); return
   458	            }
   459	            peripheral.discoverCharacteristics([Self.nusRX, Self.nusTX], for: svc)
   460	        }
   461	    }
   462	
   463	    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
   464	        MainActor.assumeIsolated {
   465	            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
   466	            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
   467	            let chars = service.characteristics ?? []
   468	            guard let rx = chars.first(where: { $0.uuid == Self.nusRX }),
   469	                  let tx = chars.first(where: { $0.uuid == Self.nusTX }) else {
   470	                self.failConnect(BLEError.characteristicNotFound); return
   471	            }
   472	            self.rxChar = rx
   473	            self.txChar = tx
   474	            let m = peripheral.maximumWriteValueLength(for: .withoutResponse)
   475	            self.maxWriteLen = m > 0 ? m : 20
   476	            peripheral.setNotifyValue(true, for: tx)
   477	        }
   478	    }
   479	
   480	    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
   481	        MainActor.assumeIsolated {
   482	            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
   483	            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
   484	            guard characteristic.uuid == Self.nusTX, characteristic.isNotifying else { return }
   485	            guard let rx = self.rxChar else { self.failConnect(BLEError.characteristicNotFound); return }
   486	            // Bring the link up: write the handshake (with response) and wait for its ack in
   487	            // didWriteValueFor before starting the bridge, so a bridge is never opened over a
   488	            // handshake that failed.
   489	            self.awaitingHandshakeAck = true
   490	            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
   491	        }
   492	    }
   493	
   494	    nonisolated func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
   495	        MainActor.assumeIsolated {
   496	            guard self.isActive(peripheral) else { return }
   497	            // Only the handshake write (a .withResponse write to RX) is acked here; the data
   498	            // path writes .withoutResponse and never reports. Start the bridge only now, once
   499	            // the handshake is confirmed to have landed.
   500	            guard self.awaitingHandshakeAck, characteristic.uuid == Self.nusRX else { return }
   501	            self.awaitingHandshakeAck = false
   502	            guard self.connectContinuation != nil else { return }
   503	            if error != nil { self.failConnect(BLEError.disconnected); return }
   504	            self.startBridge { result in
   505	                switch result {
   506	                case .success(let port): self.succeedConnect(port: port)
   507	                case .failure:
   508	                    if self.connectContinuation != nil { self.failConnect(BLEError.bridge) }
   509	                    else { self.disconnect() }
   510	                }
   511	            }
   512	        }
   513	    }
   514	
   515	    nonisolated func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
   516	        MainActor.assumeIsolated {
   517	            guard self.isActive(peripheral) else { return }
   518	            self.drainOutbox()
   519	        }
   520	    }
   521	
   522	    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
   523	        MainActor.assumeIsolated {
   524	            guard self.isActive(peripheral) else { return }
   525	            guard characteristic.uuid == Self.nusTX, let data = characteristic.value, !data.isEmpty else { return }
   526	            self.sendToTCP(data)
   527	        }
   528	    }
   529	}
   235	    /// for a card (connect at launch, not lazily). With no device detected we fall
   236	    /// back to the X7 so a bare machine shows "reader offline" exactly as before.
   237	    ///
   238	    /// Refuses while a swap is in flight or a device op owns the reader, so a Settings
   239	    /// reconnect can never replace the bridge under a running decode / clone. When the
   240	    /// detected device differs from the current one it routes through `swapDevice` so
   241	    /// the old daemon is torn down (never silently orphaned) under the swap guard.
   242	    func connect() async {
   243	        guard !swapping, !deviceBusy else { return }
   244	        await detectAndOpen()
   245	    }
   246	
   247	    /// The shared detect-and-open body of `connect()` and `rescan()`: pick the detected
   248	    /// device (or the X7 fallback), route a change through `swapDevice` so the old daemon
   249	    /// is torn down, and bring the current one up.
   250	    private func detectAndOpen() async {
   251	        let found = DeviceRegistry.detect() ?? DeviceRegistry.fallback
   252	        if bridge != nil, found.id != descriptor.id {
   253	            await swapDevice(to: found)
   254	            return
   255	        }
   256	        descriptor = found
   257	        await openCurrentDevice()
   258	    }
   259	
   260	    /// Refresh the Connect surface's lists (known present devices + all USB serial
   261	    /// ports). Synchronous IORegistry scans, cheap enough to run when the popover opens.
   262	    func refreshConnectLists() {
   263	        detectedDevices = DeviceRegistry.detectAll()
   264	        serialPorts = USBProbe.serialPorts()
   265	    }
   266	
   267	    /// True when a manual serial port is pinned (portOverride) AND that exact /dev path is
   268	    /// still enumerated on the USB bus; false when nothing is pinned. Lets rescan / the
   269	    /// monitor tell a still-present pin (leave it) from one whose device was unplugged.
   270	    private func pinnedPortPresent() -> Bool {
   271	        guard let pinned = descriptor.portOverride else { return false }
   272	        // A BLE link pins a "tcp:127.0.0.1:<port>" bridge, not a serial /dev path, so the
   273	        // serial-port scan below would report it absent and the monitor would swap away from a
   274	        // live wireless link. It is "present" for as long as the manager holds the link; once
   275	        // the BLE link drops, this reads false and a normal hot-swap is allowed.
   276	        if pinned.hasPrefix("tcp:") { return ble.isConnected }
   277	        return USBProbe.serialPorts().contains { $0.path == pinned }
   278	    }
   279	
   280	    /// The Connect surface's Rescan: refresh the lists and re-run detection without waiting
   281	    /// for the 1.5s monitor tick. A PRESENT manual pin is preserved - re-open the SAME pinned
   282	    /// device rather than running auto-detect, which (when it does not recognise the
   283	    /// Chameleon) would fall to the X7 fallback and tear down the working port the user pinned
   284	    /// precisely because auto-detect fails. Only with no pin, or a pinned port that has
   285	    /// disappeared, do we re-detect.
   286	    func rescan() async {
   287	        guard !swapping, !deviceBusy else { return }
   288	        refreshConnectLists()
   289	        if descriptor.portOverride != nil, pinnedPortPresent() {
   290	            await openCurrentDevice()
   291	        } else {
   292	            await detectAndOpen()
   293	        }
   294	        refreshConnectLists()
   295	    }
   296	
   297	    /// Manually connect to a chosen serial port: build a Chameleon-based descriptor with
   298	    /// a distinct id + the port pinned, and route through `swapDevice` so the old daemon
   299	    /// is torn down and the new one spawns with CHAMELEON_PORT set to this port.
   300	    func connectManual(port: String) async {
   301	        guard !swapping, !deviceBusy else { return }
   302	        // A port that enumerates as a device in the Nordic bootloader (isDFU) is pinned from
   303	        // the chameleon-dfu descriptor, not chameleonUltra: then deviceInDFU is true and
   304	        // openCurrentDevice takes the DFU branch (firmware recovery reachable) instead of
   305	        // trying the app protocol on a bootloader port. A non-DFU port pins as chameleonUltra.
   306	        let isDFUPort = USBProbe.serialPorts().contains { $0.path == port && $0.isDFU }
   307	        let base = isDFUPort ? DeviceRegistry.chameleonDFU : DeviceRegistry.chameleonUltra
   308	        let manual = DeviceDescriptor(
   309	            id: "chameleon-manual:\(port)",
   310	            family: base.family,
   311	            displayName: base.displayName,
   312	            daemonScript: base.daemonScript,
   313	            probeSubdir: base.probeSubdir,
   314	            usbMatch: base.usbMatch,
   315	            capabilities: base.capabilities,
   316	            portOverride: port)
   317	        await swapDevice(to: manual)
   318	        refreshConnectLists()
   319	    }
   320	
   321	    // ---- Bluetooth LE connect ----------------------------------------------
   322	
   323	    /// Begin / end a BLE scan for Chameleon advertisers. Thin pass-throughs so the Connect
   324	    /// surface drives the radio without importing the manager directly.
   325	    func bleStartScan() { ble.startScan() }
   326	    func bleStopScan() { ble.stopScan() }
   327	
   328	    /// Connect a scanned BLE Chameleon: bring up the radio link + its loopback TCP bridge,
   329	    /// then model it as a Chameleon-Ultra descriptor whose port is pinned to that bridge
   330	    /// (`tcp:127.0.0.1:<port>`), so the daemon speaks the SAME protocol it uses over USB.
   331	    /// Routes through `swapDevice` so the old daemon is torn down (never orphaned).
   332	    ///
   333	    /// A device advertising the Nordic DFU service is refused here (the view also hides its
   334	    /// connect action): firmware update over BLE is out of scope, so a bootloader device must
   335	    /// be recovered over USB.
   336	    func bleConnect(_ id: String) async {
   337	        guard canChangeDevice else { return }
   338	        guard let dev = ble.devices.first(where: { $0.id == id }), !dev.isDFU else { return }
   339	        // Reserve the whole connect (up to ~15s) so a monitor tick / second selection cannot
   340	        // interleave: bleConnecting gates canChangeDevice, connecting, and the monitor. It does
   341	        // NOT block the swapDevice call below (that guards on its own `swapping`).
   342	        bleConnecting = true
   343	        defer { bleConnecting = false }
   344	        // Clean switch between BLE devices: drop any prior link before opening a new one.
   345	        if ble.isConnected { ble.disconnect() }
   346	        do {
   347	            let port = try await ble.connect(id)
   348	            let base = DeviceRegistry.chameleonUltra
   349	            let desc = DeviceDescriptor(
   350	                id: "chameleon-ble:\(id)",
   351	                family: base.family,
   352	                displayName: base.displayName,
   353	                daemonScript: base.daemonScript,
   354	                probeSubdir: base.probeSubdir,
   355	                usbMatch: base.usbMatch,
   356	                capabilities: base.capabilities,
   357	                portOverride: "tcp:127.0.0.1:\(port)")
   358	            await swapDevice(to: desc)
   359	            ble.stopScan()
   360	            refreshConnectLists()
   361	        } catch {
   362	            lastError = "\(error)"
   363	            ble.disconnect()
   364	        }
   365	    }
   366	
   367	    /// Bring up the daemon for the active `descriptor`: read device info + key counts,
   368	    /// then sample the reader. Shared by the first connect and a hot-swap, so both
   369	    /// paths land the same state (info, capabilities via `info`, reader/card status).
   370	    private func openCurrentDevice() async {
   371	        // A device in the bootloader has no command interface to query: present a reachable
   372	        // "in DFU" state (readerOnline false, but the firmware action stays enabled) so the
   373	        // user can flash-recover it, instead of a dead "reader offline" with DFU hidden.
   374	        if deviceInDFU {
   375	            _ = activeBridge()          // spawn the daemon so the flash path can find the DFU port
   376	            info = nil
   377	            readerOnline = false
   378	            dfuStatus = nil
   379	            lastError = nil
   380	            return
   381	        }
   382	        let b = activeBridge()
   383	        do {
   384	            info = try await b.info()
   385	            builtinKeyCount = (try? await b.builtinKeyCount()) ?? 0
   386	            learnedKeyCount = (try? await b.learnedKeyCount()) ?? 0
   387	            readerOnline = true
   388	            lastError = nil
   389	            await refreshStatus()
   390	        } catch {
   391	            applyReaderGone()
   392	            lastError = "\(error)"
   393	        }
   394	    }
   395	
   396	    /// Re-read how many keys the daemon has learned (Settings shows this; it grows
   397	    /// as decodes recover keys).
   398	    func refreshLearnedCount() async {
   399	        learnedKeyCount = (try? await activeBridge().learnedKeyCount()) ?? learnedKeyCount
   400	    }
   401	
   402	    /// Forget every learned key. The cache reranks decodes; clearing it resets that.
   403	    func clearLearnedKeys() async {
   404	        try? await activeBridge().clearLearnedKeys()
   405	        await refreshLearnedCount()
   406	    }
   407	
   408	    /// Live status: keep the reader / card pill honest when the X7 or a card is
   409	    /// plugged or removed with no user action. Runs until the view's task is
   410	    /// cancelled. Skips polling during an operation that already owns the reader.
   411	    func monitor() async {
   412	        while !Task.isCancelled {
   413	            try? await Task.sleep(for: .seconds(1.5))
   414	            if deviceBusy || swapping || bleConnecting { continue }
   415	            // Hot-swap detection runs even while emulating: unplugging an emulating
   416	            // Chameleon and attaching another device must still tear down + swap. It is a
   417	            // cheap IORegistry presence scan (no device I/O), so it is safe in tag mode.
   418	            // With only the X7 involved, detect() keeps returning the same descriptor, so
   419	            // this path is inert and the poll below is unchanged.
   420	            if let found = DeviceRegistry.detect() {
   421	                // A manually-pinned device (portOverride) is protected from an auto-swap ONLY
   422	                // while its pinned port is still present - the user chose this port. Once that
   423	                // /dev path disappears (unplugged), the pin no longer refers to anything, so a
   424	                // newly-plugged X7 / auto Chameleon / DFU is allowed to hot-swap in exactly as
   425	                // an unpinned device would. Auto descriptors (no pin) swap on an id change as
   426	                // before.
   427	                if found.id != descriptor.id,
   428	                   descriptor.portOverride == nil || !pinnedPortPresent() {
   429	                    await swapDevice(to: found); continue
   430	                }
   431	            } else if emulating {
   432	                // The emulating device was unplugged with nothing to swap to: the card
   433	                // poll is skipped while emulating, so this is the only place that would
   434	                // notice it is gone. Reflect it (which also clears the emulate state).
   435	                applyReaderGone()
   436	                continue
   437	            }
   438	            // Only the card POLL is skipped while emulating: a poll forces reader mode,
   439	            // which would break the emulation.
   440	            if emulating { continue }
   441	            await refreshStatus()
   442	        }
   443	    }
   444	
   445	    /// Replace the active device with a freshly detected one. Every synchronous state
   446	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   447	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   448	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   449	    /// already nil. Only reader-bound state is cleared; the writable document is
   450	    /// device-independent and is deliberately kept across the swap.
   451	    private func swapDevice(to found: DeviceDescriptor) async {
   452	        guard !swapping else { return }
   453	        swapping = true
   454	        defer { swapping = false }              // released however this returns
   455	        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
   456	        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
   457	        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
   458	        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
   459	        let old = bridge
   460	        bridge = nil                            // detach: no path can obtain the old bridge now
   461	        descriptor = found
   462	        withAnimation(.easeInOut(duration: 0.3)) {
   463	            readerOnline = false
   464	            info = nil
   465	            card = nil
   466	            clearCardBound()
   467	            resetChameleonState()
   468	        }
   469	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   470	        await openCurrentDevice()               // creates + brings up the new bridge
   471	        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
   472	        // now that the old daemon is gone and the new device is open. Gated on the NEW device
   473	        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
   474	        // the old link and brought up the new one before this swap, so disconnecting here
   475	        // would kill the freshly-connected link. Idempotent.
   476	        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
   477	    }
   478	
   479	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   480	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   481	    /// and the slot view / emulate toggle must not persist across a device that has none.
   482	    private func resetChameleonState() {
   483	        slots = []
   484	        selectedSlot = nil
   485	        showSlots = false
   486	        showLF = false
   487	        lfScanResult = nil
   488	        lfWriteResult = nil
   489	        emulating = false
   490	        dfuStatus = nil
   491	        // A failed flash usually leaves the device in the bootloader, which triggers a
   492	        // normal->DFU descriptor swap one monitor cycle later. If the flashing sheet is
   493	        // still open, KEEP the flash outcome (error / done + progress) so its banner and
   494	        // recovery text do not vanish under the user; clearFlashState() clears them when
   495	        // the sheet is dismissed.
   496	        if !flashingSheet {
   497	            flashStage = nil
   498	            flashPercent = nil
   499	            flashDone = false
   500	            flashError = nil
  1260	    func loadLFEmu(id: String, slot i: Int) async {
  1261	        guard capabilities.lf, capabilities.emulate, !swapping, !deviceBusy else { return }
  1262	        let clean = id.replacingOccurrences(of: " ", with: "")
  1263	        // 5-byte EM410x or 13-byte Electra; anything else has no emulate path.
  1264	        let type: String
  1265	        switch clean.count {
  1266	        case 10: type = "EM410X"
  1267	        case 26: type = "EM410X_ELECTRA"
  1268	        default: lastError = "lf emulate id must be a 5-byte EM410x or 13-byte Electra id"; return
  1269	        }
  1270	        lfBusy = true
  1271	        do {
  1272	            let b = activeBridge()
  1273	            try await b.slotSelect(i)
  1274	            try await b.slotSetType(slot: i, type: type)
  1275	            try await b.slotEnable(slot: i, sense: "lf", enabled: true)
  1276	            _ = try await b.lfEmu(id: clean)
  1277	            try await b.slotSave()
  1278	            slots = try await b.slotsList()
  1279	            lastError = nil
  1280	        } catch { lastError = "\(error)" }
  1281	        lfBusy = false
  1282	    }
  1283	
  1284	    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
  1285	
  1286	    /// Read the running firmware + the newest published release (opening the flashing
  1287	    /// sheet or after a flash). A failed release fetch is not fatal - the daemon returns
  1288	    /// the current version with a null latest + a note.
  1289	    func checkFirmware() async {
  1290	        guard capabilities.dfu, !swapping, !deviceBusy else { return }
  1291	        // Firmware update is never offered over a BLE link (the USB DFU flasher could hit the
  1292	        // wrong physical device). Defensive: the button is already hidden over BLE.
  1293	        guard !deviceOverBLE else { return }
  1294	        flashError = nil
  1295	        // A device already in the bootloader has no command interface to query: leave the
  1296	        // status empty (the sheet shows the Ultra/Lite recovery choice) rather than erroring.
  1297	        if deviceInDFU { dfuStatus = nil; return }
  1298	        do { dfuStatus = try await activeBridge().dfuCheck(); lastError = nil }
  1299	        catch { lastError = "\(error)" }
  1300	    }
  1301	
  1302	    /// Flash firmware over DFU (v1 is download-only: the daemon fetches the official
  1303	    /// model-specific asset). `model` is nil in normal mode (read off the device) and
  1304	    /// "ultra"/"lite" only when recovering a device stuck in DFU whose model cannot be read.
  1305	    /// The daemon validates the download (app-only, hash) before writing anything and refuses
  1306	    /// a mid-write cancel, so this is a commit-once action. The device reboots into the
  1307	    /// bootloader and back; `flashing` pauses the status monitor across that so it does not
  1308	    /// mistake the reboot for an unplug.
  1309	    func flashFirmware(model: String?) async {
  1310	        guard capabilities.dfu, !swapping, !deviceBusy else { return }
  1311	        // Never flash over a BLE link (the USB DFU flasher could hit the wrong physical
  1312	        // device). Defensive: the firmware button is hidden over BLE.
  1313	        guard !deviceOverBLE else { return }
  1314	        flashing = true
  1315	        flashDone = false
  1316	        flashError = nil
  1317	        flashStage = nil
  1318	        flashPercent = nil
  1319	        lastError = nil
  1320	        let onProgress: @Sendable (String?, Int?) -> Void = { [weak self] stage, pct in
  1321	            Task { @MainActor in
  1322	                if let stage { self?.flashStage = stage }
  1323	                if let pct { self?.flashPercent = pct }
  1324	            }
  1325	        }
  1326	        do {
  1327	            let r = try await activeBridge().dfuFlash(model: model, onProgress: onProgress)
  1328	            if r.cancelled == true {
  1329	                flashStage = nil
  1330	            } else if r.flashed {
  1331	                flashStage = "done"
  1332	                flashPercent = 100
  1333	                flashDone = true
  1334	            }
  1335	        } catch {
  1336	            // Show the failure INSIDE the sheet with the retry path (a failed flash usually
  1337	            // leaves the device in the bootloader), not only the root banner behind the modal.
  1338	            flashError = "\(error)"
  1339	        }
  1340	        flashing = false
  1341	    }
  1342	
  1343	    // ---- file dumps --------------------------------------------------------
  1344	
  1345	    func openDumpDialog() {
  1346	        let panel = NSOpenPanel()
  1347	        panel.allowedContentTypes = [.data]
  1348	        panel.allowsOtherFileTypes = true
  1349	        panel.canChooseFiles = true
  1350	        if panel.runModal() == .OK, let url = panel.url { loadDump(from: url) }
  1351	    }
  1352	
  1353	    /// Default save name `yymmdd_tr_<uid>.dump` - sorts next to the Windows nfcPro
  1354	    /// dumps in the same folder and stays a plain raw image both tools can open.
  1355	    static func defaultDumpFilename(_ dump: CardDump) -> String {
  1880	                except Exception:
  1881	                    time.sleep(0.05)
  1882	            if code is None:
  1883	                code = proc.poll()          # the child has now exited; take its real code
  1884	        if code is None:
  1885	            raise RuntimeError("firmware flash was interrupted before the flasher reported")
  1886	        if code != 0:
  1887	            detail = " | ".join(t for t in tail if t)[-300:]
  1888	            raise RuntimeError("firmware flash failed (adafruit-nrfutil exit %d): %s"
  1889	                               % (code, detail))
  1890	        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
  1891	                                "stage": "done", "percent": 100})
  1892	
  1893	    def _emit_best_effort(self, obj):
  1894	        """emit() that swallows a broken protocol pipe: during a flash the protocol channel
  1895	        may be gone, but we must keep draining the flasher rather than abort mid-write."""
  1896	        try:
  1897	            self.emit(obj)
  1898	        except Exception:
  1899	            pass
  1900	
  1901	    def dfu_check(self, p):
  1902	        """Report the CURRENT firmware (app version + git hash) and the LATEST published
  1903	        release tag, so the shell can show "update available". A failed release fetch
  1904	        (offline) is not fatal: latest is null and `note` carries the reason."""
  1905	        c = self._connect(p.get("port"))
  1906	        model = c.get_device_model()
  1907	        major, minor = c.get_app_version()
  1908	        git = c.get_git_version()
  1909	        current = "%d.%d" % (major, minor)
  1910	        latest, note = None, None
  1911	        try:
  1912	            rel = self._latest_release(model)
  1913	            latest = rel["tag"] if rel else None
  1914	        except Exception as e:
  1915	            note = str(e)
  1916	        # Heuristic: an update is offered when the latest tag is not already embedded in
  1917	        # the running git description (which is e.g. "v2.0.0-3-gdeadbee"). The shell shows
  1918	        # both versions plainly, so a false negative just means the user flashes manually.
  1919	        update = bool(latest) and (latest not in (git or ""))
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
  1943	        # Fail closed over BLE: when the active port is the Swift loopback TCP bridge
  1944	        # (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil flashes
  1945	        # over USB serial - it would enter-bootloader / write a DIFFERENT physical device that
  1946	        # happens to be on USB, using this BLE device's model. Refuse before any download,
  1947	        # model read, reboot, or subprocess (a cross-device brick).
  1948	        if isinstance(self._port, str) and self._port.startswith("tcp:"):
  1949	            raise RuntimeError("firmware update over bluetooth is not supported; connect over usb")
  1950	        supplied_model = self._norm_model(p.get("model"))
  1951	        dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
  1952	        cdc_ports = self._find_cdc_ports()
  1953	        cdc = None
  1954	        if cdc_ports:
  1955	            # There is a normal Chameleon to reboot. Bind to EXACTLY ONE (never reboot an
  1956	            # arbitrary one), read its model FROM HARDWARE, and resolve the DFU port AFTER the
  1957	            # reboot as the NEW port relative to `dfu_before` - so a device that was already
  1958	            # stuck in DFU is never mistaken for the one we just rebooted.
  1959	            if len(cdc_ports) != 1:
  1960	                raise RuntimeError("more than one Chameleon is connected; connect only the one "
  1961	                                   "to update, then retry")
  1962	            # The model MUST come from the connected device; a caller-supplied model choice is
  1963	            # only valid for in-DFU recovery (where hardware cannot be read). Reject an override
  1964	            # so a live Ultra can never be handed Lite firmware.
  1965	            if supplied_model is not None:
  1966	                raise RuntimeError("the model is read from the connected device and cannot be "
  1967	                                   "overridden; the Ultra/Lite choice is only for recovering a "
  1968	                                   "device already in DFU")
  1969	            dfu_port = None
  1970	            cdc = cdc_ports[0]
  1971	            try:
  1972	                c = self._connect(cdc)
  1973	                raw_model = c.get_device_model()
  1974	            except Exception:
  1975	                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
  1976	            # Validate the HARDWARE model too (fail-closed): an unexpected value (2, future
  1977	            # variants) raises here rather than defaulting to Lite firmware.
  1978	            model = self._norm_model(raw_model)
  1979	        else:
  1980	            # No normal device to reboot -> pure recovery of a device ALREADY in DFU (crashed /
  1981	            # manual B-button). Require exactly ONE DFU device, and an EXPLICIT model - its model
  1982	            # cannot be read in DFU and we never guess (a wrong guess flashes Ultra onto a Lite).
  1983	            if not dfu_before:
  1984	                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
  1985	            if len(dfu_before) != 1:
  1986	                raise RuntimeError("more than one Chameleon is in DFU mode; connect only the one "
  1987	                                   "to update, then retry")
  1988	            dfu_port = dfu_before[0]
  1989	            model = supplied_model
  1990	            if model is None:
  1991	                raise RuntimeError("the Chameleon is already in DFU mode so its model cannot be "
  1992	                                   "read; choose Ultra or Lite to flash-recover it")
  1993	
  1994	        tmp = None
  1995	        try:
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
   174	                # LOCAL PATCH (tenor/rekey): shutdown() requires a `how` argument; the bare
   175	                # call raised TypeError and skipped close() below. Guard it in its own
   176	                # try/except so a peer-closed socket (OSError) still falls through to close().
   177	                try:
   178	                    self.transport.shutdown(socket.SHUT_RDWR)
   179	                except OSError:
   180	                    pass
   181	            self.transport.close()
   182	        except Exception:
   183	            pass
   184	        finally:
   185	            self.transport = None
   186	        self.wait_response_map.clear()
   187	        self.send_data_queue.queue.clear()
   188	
   189	    def thread_data_receive(self):
   190	        """
   191	            Sub thread to receive data from chameleon device.
   192	
   193	        :return:
   194	        """
   195	        data_buffer = bytearray()
   196	        data_position = 0
   197	        data_cmd = 0x0000
   198	        data_status = 0x0000
   199	        data_length = 0x0000
   200	
   201	        while self.isOpen():
   202	            # receive
   203	            assert self.transport_type is not TransportType.NONE
   204	            if self.transport_type is TransportType.SERIAL:
   205	                try:
   206	                    assert self.transport is not None
   207	                    data_bytes = bytearray(self.transport.read())
   208	                except Exception as e:
   209	                    if not self.event_closing.is_set():
   210	                        print(f"Serial Error {e}, thread for receiver exit.")
   211	                    self.close()
   212	                    break
   213	            else:  # SOCKET
   214	                try:
   215	                    data_bytes = bytearray(self.transport.recv(1024))
   216	                except socket.timeout:
   217	                    continue
   218	                except OSError:
   219	                    print(color_string(CR, 'socket closed'))
   220	                    self.transport = None
   221	                    break
   222	                # LOCAL PATCH (tenor/rekey): an orderly TCP close makes recv() return b'',
   223	                # not raise. Treat zero bytes as EOF and exit like the OSError branch;
   224	                # otherwise this loop hot-spins on empty reads forever.
   225	                if len(data_bytes) == 0:
   226	                    print(color_string(CR, 'socket closed'))
   227	                    self.transport = None
   228	                    break
   229	
   230	            while len(data_bytes) > 0:
   231	                data_byte = data_bytes[0]
   232	                data_bytes = data_bytes[1:]
   233	                data_buffer.append(data_byte)
   234	                if data_position < struct.calcsize('!BB'):  # start of frame + lrc1
   235	                    if data_position == 0:
   236	                        if data_buffer[data_position] != self.data_frame_sof:
   237	                            print("Data frame no sof byte.")
   238	                            data_position = 0
   239	                            data_buffer.clear()
   240	                            continue
   241	                    if data_position == struct.calcsize('!B'):
   242	                        if data_buffer[data_position] != self.lrc_calc(data_buffer[:data_position]):
   243	                            data_position = 0
   244	                            data_buffer.clear()
   245	                            print("Data frame sof lrc error.")
probe/chameleon_d.py:507:               "dfu_check", "dfu_flash")
probe/chameleon_d.py:511:    # cancel from a prior op cannot leak in (mirrors x7d). dfu_flash honors the flag
probe/chameleon_d.py:513:    CANCELLABLE = ("decode", "dfu_flash")
probe/chameleon_d.py:528:        # An explicit port= (used by tests) wins; otherwise honor CHAMELEON_PORT from the
probe/chameleon_d.py:530:        # missing/empty env var leaves _port None (auto-discovery via _find_port()).
probe/chameleon_d.py:532:            port = os.environ.get("CHAMELEON_PORT") or None
probe/chameleon_d.py:533:        self._port = port
probe/chameleon_d.py:538:        # `_flash_pending` is armed at DISPATCH of dfu_flash (before any EOF/timeout could
probe/chameleon_d.py:575:    def _find_port(self):
probe/chameleon_d.py:577:        for p in self._list_ports():
probe/chameleon_d.py:587:            dev = port or self._port or self._find_port()
probe/chameleon_d.py:593:            if dev in self._find_dfu_ports():
probe/chameleon_d.py:1579:    def _list_ports(self):
probe/chameleon_d.py:1583:            from serial.tools import list_ports
probe/chameleon_d.py:1586:        return list(list_ports.comports())
probe/chameleon_d.py:1588:    def _find_dfu_ports(self):
probe/chameleon_d.py:1590:        return [p.device for p in self._list_ports()
probe/chameleon_d.py:1593:    def _find_cdc_ports(self):
probe/chameleon_d.py:1595:        return [p.device for p in self._list_ports()
probe/chameleon_d.py:1616:    def _wait_new_dfu_ports(self, before, timeout=DFU_WAIT_SECONDS, settle=DFU_SETTLE_SECONDS):
probe/chameleon_d.py:1639:            new = set(self._find_dfu_ports()) - before
probe/chameleon_d.py:1843:        """Run adafruit-nrfutil and stream percent progress as `dfu_flash` events. Raises
probe/chameleon_d.py:1851:        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
probe/chameleon_d.py:1863:                        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
probe/chameleon_d.py:1890:        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
probe/chameleon_d.py:1901:    def dfu_check(self, p):
probe/chameleon_d.py:1924:    def dfu_flash(self, p):
probe/chameleon_d.py:1940:        mid-write abort can brick, so it is never checked past step 5. `dfu_flash` is armed as
probe/chameleon_d.py:1944:        # (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil flashes
probe/chameleon_d.py:1948:        if isinstance(self._port, str) and self._port.startswith("tcp:"):
probe/chameleon_d.py:1951:        dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
probe/chameleon_d.py:1952:        cdc_ports = self._find_cdc_ports()
probe/chameleon_d.py:1954:        if cdc_ports:
probe/chameleon_d.py:1959:            if len(cdc_ports) != 1:
probe/chameleon_d.py:1969:            dfu_port = None
probe/chameleon_d.py:1970:            cdc = cdc_ports[0]
probe/chameleon_d.py:1988:            dfu_port = dfu_before[0]
probe/chameleon_d.py:1996:            self.emit({"event": "progress", "method": "dfu_flash", "stage": "prepare", "percent": 0})
probe/chameleon_d.py:2005:            self.emit({"event": "progress", "method": "dfu_flash", "stage": "download",
probe/chameleon_d.py:2009:            self.emit({"event": "progress", "method": "dfu_flash", "stage": "validated", "percent": 0})
probe/chameleon_d.py:2013:            if dfu_port is None:
probe/chameleon_d.py:2015:                self.emit({"event": "progress", "method": "dfu_flash", "stage": "enter", "percent": 0})
probe/chameleon_d.py:2017:                self.emit({"event": "progress", "method": "dfu_flash", "stage": "wait", "percent": 0})
probe/chameleon_d.py:2020:                new = self._wait_new_dfu_ports(dfu_before)
probe/chameleon_d.py:2027:                dfu_port = new[0]
probe/chameleon_d.py:2039:                self._run_flash(pkg, dfu_port)
probe/chameleon_d.py:2042:            return {"flashed": True, "port": dfu_port, "tag": rel.get("tag"),
probe/chameleon_d.py:2511:                    # Disarm the flash-pending guard only once the dfu_flash op has fully
probe/chameleon_d.py:2514:                    if req.get("method") == "dfu_flash":
probe/chameleon_d.py:2535:                if method == "dfu_flash":
probe/test_chameleon.py:1777:# Firmware DFU (chameleon_d dfu_check / dfu_flash) - hardware-free.
probe/test_chameleon.py:1876:    description fields _find_port() matches on when the OS did not surface a vid."""
probe/test_chameleon.py:1966:    exercised by test_cham_dfu_flasher_resolve."""
probe/test_chameleon.py:2043:#      _wait_new_dfu_ports over _list_ports (a NEW port relative to a pre-reboot snapshot).
probe/test_chameleon.py:2045:def test_cham_dfu_port_discovery(check):
probe/test_chameleon.py:2047:    d._list_ports = lambda: [
probe/test_chameleon.py:2052:    check("_find_cdc_ports returns only the Chameleon CDC (VID 0x6868) ports",
probe/test_chameleon.py:2053:          d._find_cdc_ports() == ["/dev/cu.usbmodem6868"], str(d._find_cdc_ports()))
probe/test_chameleon.py:2054:    check("_find_dfu_ports returns only the bootloader (0x1915/0x521f) ports",
probe/test_chameleon.py:2055:          d._find_dfu_ports() == ["/dev/cu.usbmodemDFU"], str(d._find_dfu_ports()))
probe/test_chameleon.py:2056:    check("_wait_new_dfu_ports (real) returns a DFU port not in the snapshot",
probe/test_chameleon.py:2057:          d._wait_new_dfu_ports([], timeout=1, settle=0.1) == ["/dev/cu.usbmodemDFU"],
probe/test_chameleon.py:2059:    check("_wait_new_dfu_ports ignores a DFU port already in the snapshot (times out to [])",
probe/test_chameleon.py:2060:          d._wait_new_dfu_ports(["/dev/cu.usbmodemDFU"], timeout=0, settle=0.1) == [],
probe/test_chameleon.py:2065:# 27c. _find_port matching: vid, Proxgrind manufacturer, or 'chameleon' in the
probe/test_chameleon.py:2068:def test_cham_find_port_matching(check):
probe/test_chameleon.py:2072:    d._list_ports = lambda: [
probe/test_chameleon.py:2077:    check("_find_port matches a Chameleon by vid",
probe/test_chameleon.py:2078:          d._find_port() == "/dev/cu.usbmodem6868", str(d._find_port()))
probe/test_chameleon.py:2081:    d._list_ports = lambda: [
probe/test_chameleon.py:2085:    check("_find_port matches by manufacturer == Proxgrind when vid is None",
probe/test_chameleon.py:2086:          d._find_port() == "/dev/cu.usbmodemX", str(d._find_port()))
probe/test_chameleon.py:2090:    d._list_ports = lambda: [
probe/test_chameleon.py:2093:    check("_find_port matches by 'chameleon' in product (case-insensitive)",
probe/test_chameleon.py:2094:          d._find_port() == "/dev/cu.byProduct", str(d._find_port()))
probe/test_chameleon.py:2095:    d._list_ports = lambda: [
probe/test_chameleon.py:2098:    check("_find_port matches by 'chameleon' in description (case-insensitive)",
probe/test_chameleon.py:2099:          d._find_port() == "/dev/cu.byDesc", str(d._find_port()))
probe/test_chameleon.py:2103:    d._list_ports = lambda: [
probe/test_chameleon.py:2107:    check("_find_port skips a DFU port and returns None when only DFU is present",
probe/test_chameleon.py:2108:          d._find_port() is None, str(d._find_port()))
probe/test_chameleon.py:2109:    d._list_ports = lambda: [
probe/test_chameleon.py:2113:    check("_find_port skips a DFU port and returns the real CDC port",
probe/test_chameleon.py:2114:          d._find_port() == "/dev/cu.usbmodem6868", str(d._find_port()))
probe/test_chameleon.py:2118:# 27d. CHAMELEON_PORT env pins the port at construction; an explicit port= wins.
probe/test_chameleon.py:2120:def test_cham_env_port_pin(check):
probe/test_chameleon.py:2121:    saved = os.environ.get("CHAMELEON_PORT")
probe/test_chameleon.py:2123:        os.environ["CHAMELEON_PORT"] = "/dev/cu.usbmodemPINNED"
probe/test_chameleon.py:2124:        check("__init__ honors CHAMELEON_PORT when no port arg is given",
probe/test_chameleon.py:2125:              chameleon_d.Daemon()._port == "/dev/cu.usbmodemPINNED",
probe/test_chameleon.py:2126:              str(chameleon_d.Daemon()._port))
probe/test_chameleon.py:2127:        check("an explicit port= overrides the CHAMELEON_PORT env",
probe/test_chameleon.py:2128:              chameleon_d.Daemon(port="/dev/cu.explicit")._port == "/dev/cu.explicit",
probe/test_chameleon.py:2129:              str(chameleon_d.Daemon(port="/dev/cu.explicit")._port))
probe/test_chameleon.py:2130:        os.environ["CHAMELEON_PORT"] = ""
probe/test_chameleon.py:2131:        check("an empty CHAMELEON_PORT leaves _port None (auto-discovery)",
probe/test_chameleon.py:2132:              chameleon_d.Daemon()._port is None, str(chameleon_d.Daemon()._port))
probe/test_chameleon.py:2133:        del os.environ["CHAMELEON_PORT"]
probe/test_chameleon.py:2134:        check("a missing CHAMELEON_PORT leaves _port None (auto-discovery)",
probe/test_chameleon.py:2135:              chameleon_d.Daemon()._port is None, str(chameleon_d.Daemon()._port))
probe/test_chameleon.py:2138:            os.environ.pop("CHAMELEON_PORT", None)
probe/test_chameleon.py:2140:            os.environ["CHAMELEON_PORT"] = saved
probe/test_chameleon.py:2148:def test_cham_connect_refuses_dfu_port(check):
probe/test_chameleon.py:2150:    d._list_ports = lambda: [
probe/test_chameleon.py:2279:# 30. dfu_check: reports the current firmware (app + git) and the latest release tag.
probe/test_chameleon.py:2281:def test_cham_dfu_check(check):
probe/test_chameleon.py:2284:    r = d.dfu_check({})
probe/test_chameleon.py:2285:    check("dfu_check reports the current firmware version + git",
probe/test_chameleon.py:2287:    check("dfu_check reports the latest release tag + the model asset",
probe/test_chameleon.py:2289:    check("dfu_check flags an update when the latest tag is newer than the running git",
probe/test_chameleon.py:2294:    r2 = _dfu_daemon(fake2).dfu_check({})
probe/test_chameleon.py:2295:    check("dfu_check reports no update when the running firmware already is the latest",
probe/test_chameleon.py:2302:    r3 = d3.dfu_check({})
probe/test_chameleon.py:2303:    check("dfu_check survives an offline release fetch (latest null, note set)",
probe/test_chameleon.py:2314:def test_cham_dfu_flash_runner(check):
probe/test_chameleon.py:2317:    # the argv is the resolved flasher HEAD (tested for real in test_cham_dfu_flasher_resolve)
probe/test_chameleon.py:2334:    pcts = [e["percent"] for e in emitted if e.get("method") == "dfu_flash" and "percent" in e]
probe/test_chameleon.py:2372:def test_cham_dfu_flasher_resolve(check):
probe/test_chameleon.py:2433:# 32. dfu_flash end-to-end (mocked): validate -> enter-DFU -> wait -> flash.
probe/test_chameleon.py:2435:def test_cham_dfu_flash_e2e(check):
probe/test_chameleon.py:2442:    d._find_dfu_ports = lambda: []                     # snapshot empty; a NEW port appears after reboot
probe/test_chameleon.py:2443:    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]  # exactly one CDC device
probe/test_chameleon.py:2444:    d._wait_new_dfu_ports = lambda before, timeout=20: ["/dev/cu.usbmodemDFU"]
probe/test_chameleon.py:2450:        d.emit({"event": "progress", "method": "dfu_flash", "stage": "flash", "percent": 50})
probe/test_chameleon.py:2455:    r = d.dfu_flash({})                                # NO zip_path: model read from the device
probe/test_chameleon.py:2456:    check("dfu_flash download-only path succeeds end to end",
probe/test_chameleon.py:2458:    check("dfu_flash wrote the enter-DFU frame before flashing",
probe/test_chameleon.py:2460:    check("dfu_flash flashes the NEW bootloader port, not the stale CDC one",
probe/test_chameleon.py:2462:    check("dfu_flash flashes the downloaded model-specific asset from a private temp dir",
probe/test_chameleon.py:2465:    stages = [e.get("stage") for e in emitted if e.get("method") == "dfu_flash"]
probe/test_chameleon.py:2466:    check("dfu_flash streams prepare -> download -> validated -> enter -> wait -> flash",
probe/test_chameleon.py:2472:    d2._find_dfu_ports = lambda: ["/dev/cu.usbmodemDFU"]
probe/test_chameleon.py:2473:    d2._find_cdc_ports = lambda: []                    # pure recovery: nothing to reboot
probe/test_chameleon.py:2479:    r2 = d2.dfu_flash({"model": "lite"})               # explicit model choice for recovery
probe/test_chameleon.py:2480:    check("dfu_flash recovers an already-in-DFU device with an explicit model (no enter-DFU)",
probe/test_chameleon.py:2483:    check("dfu_flash recovery flashes the CHOSEN model's asset (lite-dfu-app.zip)",
probe/test_chameleon.py:2488:    d3._find_dfu_ports = lambda: ["/dev/cu.usbmodemDFU"]
probe/test_chameleon.py:2489:    d3._find_cdc_ports = lambda: []
probe/test_chameleon.py:2492:    err3 = d3.handle({"id": 1, "method": "dfu_flash", "params": {}})
probe/test_chameleon.py:2493:    check("dfu_flash refuses an already-in-DFU device of unknown model (requires an explicit choice)",
probe/test_chameleon.py:2500:    d4._find_dfu_ports = lambda: []
probe/test_chameleon.py:2501:    d4._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2506:    err4 = d4.handle({"id": 2, "method": "dfu_flash", "params": {"model": "lite"}})
probe/test_chameleon.py:2507:    check("dfu_flash rejects a caller-supplied model on a live device (reads model from hardware)",
probe/test_chameleon.py:2514:    d5._find_dfu_ports = lambda: []
probe/test_chameleon.py:2515:    d5._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2520:    err5 = d5.handle({"id": 3, "method": "dfu_flash", "params": {}})
probe/test_chameleon.py:2521:    check("dfu_flash refuses a live device of unexpected hardware model (never picks Lite)",
probe/test_chameleon.py:2527:# 33. dfu_flash brick-safety: a full package is refused before any hardware touch;
probe/test_chameleon.py:2530:def test_cham_dfu_flash_safety(check):
probe/test_chameleon.py:2538:    d._find_dfu_ports = lambda: []
probe/test_chameleon.py:2539:    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2545:    err = d.handle({"id": 1, "method": "dfu_flash", "params": {}})
probe/test_chameleon.py:2546:    check("dfu_flash refuses a FULL asset (sanity check) BEFORE touching hardware (no enter, no flash)",
probe/test_chameleon.py:2551:    d2._find_dfu_ports = lambda: []
probe/test_chameleon.py:2552:    d2._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2559:    r = d2.dfu_flash({})
probe/test_chameleon.py:2560:    check("a pre-cancelled dfu_flash stops before the flash write (never bricks a cancel)",
probe/test_chameleon.py:2565:    d3._find_dfu_ports = lambda: []
probe/test_chameleon.py:2566:    d3._find_cdc_ports = lambda: []
probe/test_chameleon.py:2568:    err3 = d3.handle({"id": 2, "method": "dfu_flash", "params": {}})
probe/test_chameleon.py:2569:    check("dfu_flash on an unreachable device returns the manual B-button fallback",
probe/test_chameleon.py:2574:    d4._find_dfu_ports = lambda: ["/dev/cu.dfu1", "/dev/cu.dfu2"]
probe/test_chameleon.py:2575:    d4._find_cdc_ports = lambda: []
probe/test_chameleon.py:2579:    err4 = d4.handle({"id": 3, "method": "dfu_flash", "params": {"model": "ultra"}})
probe/test_chameleon.py:2580:    check("dfu_flash refuses when MULTIPLE devices are in DFU (never flashes the wrong one)",
probe/test_chameleon.py:2585:    d5._find_dfu_ports = lambda: []
probe/test_chameleon.py:2586:    d5._find_cdc_ports = lambda: ["/dev/cu.a", "/dev/cu.b"]
probe/test_chameleon.py:2590:    err5 = d5.handle({"id": 4, "method": "dfu_flash", "params": {}})
probe/test_chameleon.py:2591:    check("dfu_flash refuses to reboot when MULTIPLE Chameleons are connected (no arbitrary enter)",
probe/test_chameleon.py:2687:    # (a) DELAYED second enumeration exercised through the REAL _wait_new_dfu_ports: the target
probe/test_chameleon.py:2693:    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2703:    d._find_dfu_ports = find_dfu
probe/test_chameleon.py:2705:    real_wait = d._wait_new_dfu_ports
probe/test_chameleon.py:2706:    d._wait_new_dfu_ports = lambda before, timeout=2, settle=0.4: real_wait(before, timeout, settle)
probe/test_chameleon.py:2710:    err = d.handle({"id": 1, "method": "dfu_flash", "params": {}})
probe/test_chameleon.py:2711:    check("dfu_flash refuses when a SECOND new DFU device enumerates in the settle window (real wait)",
probe/test_chameleon.py:2715:    #     new attributable port is flashed. Real _wait_new_dfu_ports over a stateful port list.
probe/test_chameleon.py:2717:    d2._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2728:    d2._find_dfu_ports = find_dfu2
probe/test_chameleon.py:2729:    real_wait2 = d2._wait_new_dfu_ports
probe/test_chameleon.py:2730:    d2._wait_new_dfu_ports = lambda before, timeout=2, settle=0.4: real_wait2(before, timeout, settle)
probe/test_chameleon.py:2734:    r2 = d2.dfu_flash({})
probe/test_chameleon.py:2735:    check("dfu_flash flashes ONLY the new attributable port, not the pre-existing stuck one",
probe/test_chameleon.py:2756:    d._find_dfu_ports = find_dfu
probe/test_chameleon.py:2759:        r = d._wait_new_dfu_ports([], timeout=2.0, settle=1.0)
probe/test_chameleon.py:2771:    d2._find_dfu_ports = find_one
probe/test_chameleon.py:2774:        r2 = d2._wait_new_dfu_ports([], timeout=2.0, settle=1.0)
probe/test_chameleon.py:2789:    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2790:    d._find_dfu_ports = lambda: []
probe/test_chameleon.py:2793:    d._wait_new_dfu_ports = lambda before, timeout=20: ["/dev/cu.dfu"]
probe/test_chameleon.py:2804:    stream = _GatedStream(['{"id": 1, "method": "dfu_flash", "params": {}}\n'], started)
probe/test_chameleon.py:2818:# 38. EOF-during-DISPATCH race (finding 2c): EOF lands while dfu_flash is in its PRE-flash
probe/test_chameleon.py:2827:    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2828:    d._find_dfu_ports = lambda: []
probe/test_chameleon.py:2836:    d._wait_new_dfu_ports = blocking_wait
probe/test_chameleon.py:2841:    stream = _GatedStream(['{"id": 1, "method": "dfu_flash", "params": {}}\n'], at_wait)
probe/test_chameleon.py:2860:# 35. cancel THROUGH run(): a cancel line delivered while a dfu_flash op is queued must
probe/test_chameleon.py:2865:    d._find_cdc_ports = lambda: ["/dev/cu.usbmodem6868"]
probe/test_chameleon.py:2866:    d._find_dfu_ports = lambda: []
probe/test_chameleon.py:2876:    d._wait_new_dfu_ports = wait_new
probe/test_chameleon.py:2881:    d.run(io.StringIO('{"id": 1, "method": "dfu_flash", "params": {}}\n'
probe/test_chameleon.py:2884:    check("a cancel routed THROUGH run() stops a queued dfu_flash before the flash write",
probe/test_chameleon.py:3082:# 34. dfu_flash fails closed over the BLE/tcp bridge: when the active port is the Swift
probe/test_chameleon.py:3083:#     loopback (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil
probe/test_chameleon.py:3085:#     is at the very top of dfu_flash: it raises before any download / port scan / reboot /
probe/test_chameleon.py:3088:def test_cham_dfu_flash_rejects_ble(check):
probe/test_chameleon.py:3090:    d._port = "tcp:127.0.0.1:5000"                     # active connection is the Swift BLE bridge
probe/test_chameleon.py:3098:    d._find_dfu_ports = _mark("scan", [])
probe/test_chameleon.py:3099:    d._find_cdc_ports = _mark("scan", [])
probe/test_chameleon.py:3105:        d.dfu_flash({})
probe/test_chameleon.py:3108:    check("dfu_flash over the BLE/tcp bridge raises (bluetooth/usb message)",
probe/test_chameleon.py:3111:    check("dfu_flash over BLE runs no seam (no download / port scan / reboot / flash)",
probe/test_chameleon.py:3161:         test_cham_dfu_asset, test_cham_dfu_norm_model, test_cham_dfu_port_discovery,
probe/test_chameleon.py:3162:         test_cham_find_port_matching, test_cham_env_port_pin,
probe/test_chameleon.py:3163:         test_cham_connect_refuses_dfu_port,
probe/test_chameleon.py:3165:         test_cham_dfu_check, test_cham_dfu_flash_runner, test_cham_dfu_flasher_resolve,
probe/test_chameleon.py:3166:         test_cham_dfu_flash_e2e,
probe/test_chameleon.py:3167:         test_cham_dfu_flash_safety, test_cham_dfu_download_checks,
probe/test_chameleon.py:3171:         test_cham_dfu_flash_rejects_ble, test_cham_socket_eof_exits]

codex
The claimed scoping deserves closer scrutiny: the generation is checked by the timeout, but CoreBluetooth callbacks themselves only compare the peripheral identifier. I am tracing same-device reconnects and queued Network-framework callbacks, where identifiers alone may not distinguish attempts.
exec
/bin/zsh -lc "nl -ba probe/chameleon_d.py | sed -n '500,630p'; nl -ba probe/chameleon_d.py | sed -n '1890,2055p'; nl -ba probe/test_chameleon.py | sed -n '3070,3150p'; nl -ba app/Sources/AppModel.swift | sed -n '445,490p'; nl -ba app/Sources/AppModel.swift | sed -n '1275,1345p'; nl -ba app/Sources/Shell/RootView.swift | sed -n '250,305p'; rg -n \"func (connect|rescan|connectManual)|canChangeDevice|bleConnecting|swapDevice\\(\" app/Sources app/Tests || true; rg -n \"\\.close\\(\\)|transport_type|thread_data_receive|SOCKET|SERIAL\" probe/test_chameleon.py | tail -n 80" in /Users/tuan/Claude/Tenor/tenor-rekey
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
   621	        try:
   622	            if not c.is_device_reader_mode():
   623	                c.set_device_reader_mode(True)
   624	        except UnexpectedResponseError as e:
   625	            raise RuntimeError("device has no reader mode (Lite?): %s" % e)
   626	        self._reader_mode = True
   627	
   628	    def emit(self, obj):
   629	        with self._emit_lock:
   630	            self._out.write(json.dumps(obj) + "\n")
  1890	        self._emit_best_effort({"event": "progress", "method": "dfu_flash",
  1891	                                "stage": "done", "percent": 100})
  1892	
  1893	    def _emit_best_effort(self, obj):
  1894	        """emit() that swallows a broken protocol pipe: during a flash the protocol channel
  1895	        may be gone, but we must keep draining the flasher rather than abort mid-write."""
  1896	        try:
  1897	            self.emit(obj)
  1898	        except Exception:
  1899	            pass
  1900	
  1901	    def dfu_check(self, p):
  1902	        """Report the CURRENT firmware (app version + git hash) and the LATEST published
  1903	        release tag, so the shell can show "update available". A failed release fetch
  1904	        (offline) is not fatal: latest is null and `note` carries the reason."""
  1905	        c = self._connect(p.get("port"))
  1906	        model = c.get_device_model()
  1907	        major, minor = c.get_app_version()
  1908	        git = c.get_git_version()
  1909	        current = "%d.%d" % (major, minor)
  1910	        latest, note = None, None
  1911	        try:
  1912	            rel = self._latest_release(model)
  1913	            latest = rel["tag"] if rel else None
  1914	        except Exception as e:
  1915	            note = str(e)
  1916	        # Heuristic: an update is offered when the latest tag is not already embedded in
  1917	        # the running git description (which is e.g. "v2.0.0-3-gdeadbee"). The shell shows
  1918	        # both versions plainly, so a false negative just means the user flashes manually.
  1919	        update = bool(latest) and (latest not in (git or ""))
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
  1943	        # Fail closed over BLE: when the active port is the Swift loopback TCP bridge
  1944	        # (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil flashes
  1945	        # over USB serial - it would enter-bootloader / write a DIFFERENT physical device that
  1946	        # happens to be on USB, using this BLE device's model. Refuse before any download,
  1947	        # model read, reboot, or subprocess (a cross-device brick).
  1948	        if isinstance(self._port, str) and self._port.startswith("tcp:"):
  1949	            raise RuntimeError("firmware update over bluetooth is not supported; connect over usb")
  1950	        supplied_model = self._norm_model(p.get("model"))
  1951	        dfu_before = self._find_dfu_ports()          # SNAPSHOT before any reboot (identity binding)
  1952	        cdc_ports = self._find_cdc_ports()
  1953	        cdc = None
  1954	        if cdc_ports:
  1955	            # There is a normal Chameleon to reboot. Bind to EXACTLY ONE (never reboot an
  1956	            # arbitrary one), read its model FROM HARDWARE, and resolve the DFU port AFTER the
  1957	            # reboot as the NEW port relative to `dfu_before` - so a device that was already
  1958	            # stuck in DFU is never mistaken for the one we just rebooted.
  1959	            if len(cdc_ports) != 1:
  1960	                raise RuntimeError("more than one Chameleon is connected; connect only the one "
  1961	                                   "to update, then retry")
  1962	            # The model MUST come from the connected device; a caller-supplied model choice is
  1963	            # only valid for in-DFU recovery (where hardware cannot be read). Reject an override
  1964	            # so a live Ultra can never be handed Lite firmware.
  1965	            if supplied_model is not None:
  1966	                raise RuntimeError("the model is read from the connected device and cannot be "
  1967	                                   "overridden; the Ultra/Lite choice is only for recovering a "
  1968	                                   "device already in DFU")
  1969	            dfu_port = None
  1970	            cdc = cdc_ports[0]
  1971	            try:
  1972	                c = self._connect(cdc)
  1973	                raw_model = c.get_device_model()
  1974	            except Exception:
  1975	                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
  1976	            # Validate the HARDWARE model too (fail-closed): an unexpected value (2, future
  1977	            # variants) raises here rather than defaulting to Lite firmware.
  1978	            model = self._norm_model(raw_model)
  1979	        else:
  1980	            # No normal device to reboot -> pure recovery of a device ALREADY in DFU (crashed /
  1981	            # manual B-button). Require exactly ONE DFU device, and an EXPLICIT model - its model
  1982	            # cannot be read in DFU and we never guess (a wrong guess flashes Ultra onto a Lite).
  1983	            if not dfu_before:
  1984	                raise RuntimeError("the Chameleon is not reachable to enter DFU: " + _MANUAL_FALLBACK)
  1985	            if len(dfu_before) != 1:
  1986	                raise RuntimeError("more than one Chameleon is in DFU mode; connect only the one "
  1987	                                   "to update, then retry")
  1988	            dfu_port = dfu_before[0]
  1989	            model = supplied_model
  1990	            if model is None:
  1991	                raise RuntimeError("the Chameleon is already in DFU mode so its model cannot be "
  1992	                                   "read; choose Ultra or Lite to flash-recover it")
  1993	
  1994	        tmp = None
  1995	        try:
  1996	            self.emit({"event": "progress", "method": "dfu_flash", "stage": "prepare", "percent": 0})
  1997	            # Resolve the official release ONCE and PIN it for this whole op (no re-resolve).
  1998	            rel = self._latest_release(model)
  1999	            if not rel or not rel.get("url"):
  2000	                raise RuntimeError("no application DFU release found for %s"
  2001	                                   % self._dfu_asset_name(model))
  2002	            tmp = tempfile.mkdtemp(prefix="cham-dfu-")   # private per-flash dir (mode 0700)
  2003	            os.chmod(tmp, 0o700)
  2004	            pkg = os.path.join(tmp, self._dfu_asset_name(model))
  2005	            self.emit({"event": "progress", "method": "dfu_flash", "stage": "download",
  2006	                       "percent": 0, "tag": rel.get("tag")})
  2007	            self._download_asset(rel, pkg)           # complete + size-capped + digest-checked
  2008	            info = self._validate_dfu_zip(pkg)       # app-only sanity check (reject full)
  2009	            self.emit({"event": "progress", "method": "dfu_flash", "stage": "validated", "percent": 0})
  2010	            if self._cancel.is_set():                # safe cancel: nothing has been written
  2011	                return {"flashed": False, "cancelled": True}
  2012	
  2013	            if dfu_port is None:
  2014	                self._drop()                         # free the CMD handle so the raw serial can open the port
  2015	                self.emit({"event": "progress", "method": "dfu_flash", "stage": "enter", "percent": 0})
  2016	                self._send_enter_dfu(cdc)
  2017	                self.emit({"event": "progress", "method": "dfu_flash", "stage": "wait", "percent": 0})
  2018	                # Accept only a NEW, attributable DFU port (not present before the reboot), and
  2019	                # exactly one - refuse if a second device appears or the target is ambiguous.
  2020	                new = self._wait_new_dfu_ports(dfu_before)
  2021	                if not new:
  2022	                    raise RuntimeError("the Chameleon did not re-appear in DFU mode. Manual fallback: "
  2023	                                       + _MANUAL_FALLBACK)
  2024	                if len(new) != 1:
  2025	                    raise RuntimeError("more than one new Chameleon appeared in DFU after the reboot; "
  2026	                                       "disconnect the others and retry")
  2027	                dfu_port = new[0]
  2028	
  2029	            # Commit handshake (closes the EOF/dispatch race): announce the uninterruptible
  2030	            # write BEFORE the final cancel re-check, so EOF either sees `_flashing` (and joins
  2031	            # unbounded) or the worker still sees the cancel here and aborts before any write.
  2032	            self._flashing.set()
  2033	            try:
  2034	                if self._cancel.is_set():            # last safe cancel point, before any write
  2035	                    self._flashing.clear()
  2036	                    return {"flashed": False, "cancelled": True}
  2037	                # POINT OF NO CANCEL: the bootloader is being written; a mid-write abort can
  2038	                # brick, so the cancel flag is deliberately not checked again.
  2039	                self._run_flash(pkg, dfu_port)
  2040	            finally:
  2041	                self._flashing.clear()
  2042	            return {"flashed": True, "port": dfu_port, "tag": rel.get("tag"),
  2043	                    "hash": info.get("hash_type")}
  2044	        finally:
  2045	            if tmp and os.path.isdir(tmp):
  2046	                shutil.rmtree(tmp, ignore_errors=True)
  2047	
  2048	    # ---- decode: dict-only key recovery + full read (nested/darkside is P1) --
  2049	
  2050	    def _check_keys(self, c, n_sectors, key_bytes):
  2051	        """On-device dictionary check across n_sectors, mirroring the CLI. The
  2052	        80-bit mask (10 bytes, MSB-first) masks a sector-key when its bit is set:
  2053	        bit 2*s = KeyA of sector s, bit 2*s+1 = KeyB. Sectors past the card are
  2054	        pre-masked; found bits are accumulated so later chunks skip them. Returns
  2055	        ({index: 6-byte key} keyed 0..79, keys_tried) - index k -> sector k//2,
  3070	          emu.type_sets[-1] == (0, TagSpecificType.NTAG_215)
  3071	          and len(emu.emu_pages) == 130 and 130 not in emu.emu_pages and 129 in emu.emu_pages,
  3072	          str((emu.type_sets[-1], len(emu.emu_pages))))
  3073	    check("round-trip emulate uses the tag's OWN read UID (not a live-card or page-count guess)",
  3074	          emu.anti_coll is not None and emu.anti_coll[0] == bytes.fromhex("04112233445566"),
  3075	          str(emu.anti_coll))
  3076	    check("round-trip emulate carries the read version + counter metadata (not emulator defaults)",
  3077	          emu.ntag_emu_version == bytes.fromhex(read["version"].replace(" ", ""))
  3078	          and emu.ntag_emu_counters[0][0] == 7, str(emu.ntag_emu_counters))
  3079	
  3080	
  3081	# --------------------------------------------------------------------------
  3082	# 34. dfu_flash fails closed over the BLE/tcp bridge: when the active port is the Swift
  3083	#     loopback (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil
  3084	#     flashes over USB - it could enter-bootloader / write a DIFFERENT USB device. The guard
  3085	#     is at the very top of dfu_flash: it raises before any download / port scan / reboot /
  3086	#     subprocess, so none of those seams run.
  3087	# --------------------------------------------------------------------------
  3088	def test_cham_dfu_flash_rejects_ble(check):
  3089	    d = _dfu_daemon(FakeChameleon(model=0))
  3090	    d._port = "tcp:127.0.0.1:5000"                     # active connection is the Swift BLE bridge
  3091	    called = {}
  3092	
  3093	    def _mark(key, ret):
  3094	        def _fn(*a, **k):
  3095	            called[key] = True
  3096	            return ret
  3097	        return _fn
  3098	    d._find_dfu_ports = _mark("scan", [])
  3099	    d._find_cdc_ports = _mark("scan", [])
  3100	    d._download_asset = _mark("download", 0)
  3101	    d._send_enter_dfu = _mark("entered", None)
  3102	    d._run_flash = _mark("flashed", None)
  3103	    raised = None
  3104	    try:
  3105	        d.dfu_flash({})
  3106	    except RuntimeError as e:
  3107	        raised = str(e)
  3108	    check("dfu_flash over the BLE/tcp bridge raises (bluetooth/usb message)",
  3109	          raised is not None and "bluetooth" in raised.lower() and "usb" in raised.lower(),
  3110	          repr(raised))
  3111	    check("dfu_flash over BLE runs no seam (no download / port scan / reboot / flash)",
  3112	          not called, str(called))
  3113	
  3114	
  3115	# --------------------------------------------------------------------------
  3116	# 35. socket transport recognises an orderly peer EOF: recv() returning b'' must end the
  3117	#     receive loop (transport dropped), not hot-spin on empty reads. Driven with a real
  3118	#     socketpair so the loop runs against a genuine socket close.
  3119	# --------------------------------------------------------------------------
  3120	def test_cham_socket_eof_exits(check):
  3121	    import socket as _socket
  3122	    a, b = _socket.socketpair()
  3123	    a.settimeout(THREAD_BLOCKING_TIMEOUT)              # bound recv so it can see the EOF quickly
  3124	    c = ChameleonCom()
  3125	    c.transport = a
  3126	    c.transport_type = TransportType.SOCKET
  3127	    c.event_closing.clear()
  3128	    t = threading.Thread(target=c.thread_data_receive)
  3129	    t.start()
  3130	    b.close()                                          # orderly peer close -> recv() returns b''
  3131	    t.join(2)
  3132	    check("thread_data_receive exits promptly on peer EOF (no hot spin)",
  3133	          not t.is_alive() and c.transport is None,
  3134	          "alive=%s transport=%s" % (t.is_alive(), c.transport))
  3135	    try:
  3136	        a.close()
  3137	    except OSError:
  3138	        pass
  3139	
  3140	
  3141	TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
  3142	         test_cham_read_block, test_cham_decode, test_cham_decode_partial,
  3143	         test_cham_decode_nocard, test_cham_decode_swap, test_cham_decode_user_key,
  3144	         test_cham_transport_wedge, test_cham_dispatch,
  3145	         test_cham_decode_nested_chain, test_cham_decode_darkside_chain,
  3146	         test_cham_decode_static_chain, test_cham_decode_hard_prng,
  3147	         test_cham_decode_cancel, test_cham_attack_budget_guard,
  3148	         test_cham_decode_hardnested_chain, test_cham_decode_hardnested_no_anchor,
  3149	         test_cham_capability_hardnested,
  3150	         test_cham_read_ntag, test_cham_read_ntag_locked, test_cham_read_ntag_unknown_size,
   445	    /// Replace the active device with a freshly detected one. Every synchronous state
   446	    /// change happens BEFORE the first await, so the teardown await (a MainActor
   447	    /// reentrancy point) cannot expose the old bridge or the stale card to a decode /
   448	    /// clone / reconnect started in that window: the swap guard is up and `bridge` is
   449	    /// already nil. Only reader-bound state is cleared; the writable document is
   450	    /// device-independent and is deliberately kept across the swap.
   451	    private func swapDevice(to found: DeviceDescriptor) async {
   452	        guard !swapping else { return }
   453	        swapping = true
   454	        defer { swapping = false }              // released however this returns
   455	        // Whether the OUTGOING device is a BLE link, captured BEFORE `descriptor` is
   456	        // reassigned. Swapping INTO a BLE device leaves this false (the outgoing device is
   457	        // USB/X7), so its radio teardown below fires only when LEAVING a BLE device.
   458	        let oldWasBLE = descriptor.portOverride?.hasPrefix("tcp:") == true
   459	        let old = bridge
   460	        bridge = nil                            // detach: no path can obtain the old bridge now
   461	        descriptor = found
   462	        withAnimation(.easeInOut(duration: 0.3)) {
   463	            readerOnline = false
   464	            info = nil
   465	            card = nil
   466	            clearCardBound()
   467	            resetChameleonState()
   468	        }
   469	        await old?.shutdown()                   // bounded terminate + drain of the old daemon
   470	        await openCurrentDevice()               // creates + brings up the new bridge
   471	        // Leaving a BLE device for a NON-BLE one: close the radio + its loopback TCP bridge
   472	        // now that the old daemon is gone and the new device is open. Gated on the NEW device
   473	        // not being BLE too: a BLE->BLE switch is driven by bleConnect, which already dropped
   474	        // the old link and brought up the new one before this swap, so disconnecting here
   475	        // would kill the freshly-connected link. Idempotent.
   476	        if oldWasBLE, found.portOverride?.hasPrefix("tcp:") != true { ble.disconnect() }
   477	    }
   478	
   479	    /// Drop the Chameleon-scoped slot library + emulation state on a device swap or a
   480	    /// reader-gone: the next device (or the same one reconnected) reloads its own slots,
   481	    /// and the slot view / emulate toggle must not persist across a device that has none.
   482	    private func resetChameleonState() {
   483	        slots = []
   484	        selectedSlot = nil
   485	        showSlots = false
   486	        showLF = false
   487	        lfScanResult = nil
   488	        lfWriteResult = nil
   489	        emulating = false
   490	        dfuStatus = nil
  1275	            try await b.slotEnable(slot: i, sense: "lf", enabled: true)
  1276	            _ = try await b.lfEmu(id: clean)
  1277	            try await b.slotSave()
  1278	            slots = try await b.slotsList()
  1279	            lastError = nil
  1280	        } catch { lastError = "\(error)" }
  1281	        lfBusy = false
  1282	    }
  1283	
  1284	    // ---- firmware update (Chameleon-only, gated on capabilities.dfu) -------
  1285	
  1286	    /// Read the running firmware + the newest published release (opening the flashing
  1287	    /// sheet or after a flash). A failed release fetch is not fatal - the daemon returns
  1288	    /// the current version with a null latest + a note.
  1289	    func checkFirmware() async {
  1290	        guard capabilities.dfu, !swapping, !deviceBusy else { return }
  1291	        // Firmware update is never offered over a BLE link (the USB DFU flasher could hit the
  1292	        // wrong physical device). Defensive: the button is already hidden over BLE.
  1293	        guard !deviceOverBLE else { return }
  1294	        flashError = nil
  1295	        // A device already in the bootloader has no command interface to query: leave the
  1296	        // status empty (the sheet shows the Ultra/Lite recovery choice) rather than erroring.
  1297	        if deviceInDFU { dfuStatus = nil; return }
  1298	        do { dfuStatus = try await activeBridge().dfuCheck(); lastError = nil }
  1299	        catch { lastError = "\(error)" }
  1300	    }
  1301	
  1302	    /// Flash firmware over DFU (v1 is download-only: the daemon fetches the official
  1303	    /// model-specific asset). `model` is nil in normal mode (read off the device) and
  1304	    /// "ultra"/"lite" only when recovering a device stuck in DFU whose model cannot be read.
  1305	    /// The daemon validates the download (app-only, hash) before writing anything and refuses
  1306	    /// a mid-write cancel, so this is a commit-once action. The device reboots into the
  1307	    /// bootloader and back; `flashing` pauses the status monitor across that so it does not
  1308	    /// mistake the reboot for an unplug.
  1309	    func flashFirmware(model: String?) async {
  1310	        guard capabilities.dfu, !swapping, !deviceBusy else { return }
  1311	        // Never flash over a BLE link (the USB DFU flasher could hit the wrong physical
  1312	        // device). Defensive: the firmware button is hidden over BLE.
  1313	        guard !deviceOverBLE else { return }
  1314	        flashing = true
  1315	        flashDone = false
  1316	        flashError = nil
  1317	        flashStage = nil
  1318	        flashPercent = nil
  1319	        lastError = nil
  1320	        let onProgress: @Sendable (String?, Int?) -> Void = { [weak self] stage, pct in
  1321	            Task { @MainActor in
  1322	                if let stage { self?.flashStage = stage }
  1323	                if let pct { self?.flashPercent = pct }
  1324	            }
  1325	        }
  1326	        do {
  1327	            let r = try await activeBridge().dfuFlash(model: model, onProgress: onProgress)
  1328	            if r.cancelled == true {
  1329	                flashStage = nil
  1330	            } else if r.flashed {
  1331	                flashStage = "done"
  1332	                flashPercent = 100
  1333	                flashDone = true
  1334	            }
  1335	        } catch {
  1336	            // Show the failure INSIDE the sheet with the retry path (a failed flash usually
  1337	            // leaves the device in the bootloader), not only the root banner behind the modal.
  1338	            flashError = "\(error)"
  1339	        }
  1340	        flashing = false
  1341	    }
  1342	
  1343	    // ---- file dumps --------------------------------------------------------
  1344	
  1345	    func openDumpDialog() {
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
   261	                    withAnimation(.easeInOut(duration: 0.2)) {
   262	                        model.showLF = willShow
   263	                        if willShow { model.showSlots = false; model.showLibrary = false }
   264	                    }
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
   276	            // Never over a BLE link: the DFU flasher runs over USB and could flash the
   277	            // wrong physical device, so the firmware action is hidden while connected over BLE.
   278	            if model.capabilities.dfu && !model.deviceOverBLE {
   279	                // Reachable when the reader is online OR the device is stuck in the
   280	                // bootloader (deviceInDFU) - the latter is exactly when flash-recovery is needed.
   281	                ActionButton(title: l.t("firmware"), icon: "arrow.up.circle",
   282	                             enabled: (model.readerOnline || model.deviceInDFU) && !busy
   283	                                 && !model.slotBusy && !model.emulating) {
   284	                    model.flashingSheet = true
   285	                    Task { await model.checkFirmware() }
   286	                }
   287	            }
   288	            Spacer()
   289	            if model.decoding {
   290	                if let p = model.decodeProgress {
   291	                    Text(decodeStatusLine(p, resolved: model.resolvedSectors, elapsed: model.decodeElapsed, l))
   292	                        .font(Typeface.mono(11)).foregroundStyle(theme.p.textSecondary)
   293	                } else {
   294	                    ProgressView().controlSize(.small)
   295	                }
   296	                Button(l.t("cancel")) { Task { await model.cancelDecode() } }
   297	                    .buttonStyle(.plain).font(l.sans(11)).foregroundStyle(theme.p.accent).padding(.leading, 2)
   298	            } else if busy {
   299	                ProgressView().controlSize(.small).padding(.trailing, 4)
   300	            }
   301	            if let src = model.source { SourceTag(src: src) }
   302	        }
   303	        .padding(.horizontal, 16)
   304	        .frame(height: 48)
   305	        .background(theme.p.panel)
rg: app/Tests: No such file or directory (os error 2)
app/Sources/Engine/BLEManager.swift:125:    func connect(_ id: String) async throws -> Int {
app/Sources/Views/ConnectView.swift:39:            if model.canChangeDevice,
app/Sources/Views/ConnectView.swift:66:            .buttonStyle(.plain).disabled(model.connecting || !model.canChangeDevice).help(l.t("rescan"))
app/Sources/Views/ConnectView.swift:124:                    .disabled(manualTrimmed.isEmpty || !model.canChangeDevice)
app/Sources/Views/ConnectView.swift:133:            guard !port.isDFU, model.canChangeDevice else { return }
app/Sources/Views/ConnectView.swift:159:        .buttonStyle(.plain).disabled(port.isDFU || !model.canChangeDevice)
app/Sources/Views/ConnectView.swift:237:            else if model.canChangeDevice { model.bleStartScan() }
app/Sources/Views/ConnectView.swift:248:        .buttonStyle(.plain).disabled(!model.canChangeDevice)
app/Sources/Views/ConnectView.swift:256:            guard !d.isDFU, model.canChangeDevice else { return }
app/Sources/Views/ConnectView.swift:277:        .buttonStyle(.plain).disabled(d.isDFU || !model.canChangeDevice)
app/Sources/Views/ConnectView.swift:314:        guard !p.isEmpty, model.canChangeDevice else { return }
app/Sources/AppModel.swift:152:    private var bleConnecting = false
app/Sources/AppModel.swift:189:    var connecting: Bool { swapping || bleConnecting }
app/Sources/AppModel.swift:200:    var canChangeDevice: Bool { !swapping && !deviceBusy && !bleConnecting }
app/Sources/AppModel.swift:242:    func connect() async {
app/Sources/AppModel.swift:253:            await swapDevice(to: found)
app/Sources/AppModel.swift:286:    func rescan() async {
app/Sources/AppModel.swift:300:    func connectManual(port: String) async {
app/Sources/AppModel.swift:317:        await swapDevice(to: manual)
app/Sources/AppModel.swift:337:        guard canChangeDevice else { return }
app/Sources/AppModel.swift:340:        // interleave: bleConnecting gates canChangeDevice, connecting, and the monitor. It does
app/Sources/AppModel.swift:342:        bleConnecting = true
app/Sources/AppModel.swift:343:        defer { bleConnecting = false }
app/Sources/AppModel.swift:358:            await swapDevice(to: desc)
app/Sources/AppModel.swift:414:            if deviceBusy || swapping || bleConnecting { continue }
app/Sources/AppModel.swift:429:                    await swapDevice(to: found); continue
app/Sources/AppModel.swift:451:    private func swapDevice(to found: DeviceDescriptor) async {
3126:    c.transport_type = TransportType.SOCKET
3128:    t = threading.Thread(target=c.thread_data_receive)
3130:    b.close()                                          # orderly peer close -> recv() returns b''
3132:    check("thread_data_receive exits promptly on peer EOF (no hot spin)",
3136:        a.close()

2026-07-21T04:45:18.762149Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc 'PYTHONDONTWRITEBYTECODE=1 python3 test_all.py' in /Users/tuan/Claude/Tenor/tenor-rekey/probe
2026-07-21T04:45:35.603656Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
 exited 1 in 1709ms:
2026-07-21 11:45:34.039 xcodebuild[47620:3438945]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:45:34.168 xcodebuild[47620:3438944] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
python3: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
python3: error: couldn't create cache file '/tmp/xcrun_db-qyXy0Uqs' (errno=Operation not permitted)
2026-07-21 11:45:34.610 xcodebuild[47623:3438967]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:45:34.744 xcodebuild[47623:3438966] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
Traceback (most recent call last):
  File "/Users/tuan/Claude/Tenor/tenor-rekey/probe/test_all.py", line 27, in <module>
    import test_chameleon
  File "/Users/tuan/Claude/Tenor/tenor-rekey/probe/test_chameleon.py", line 32, in <module>
    os.path.join(tempfile.gettempdir(), "rekey-test-learned.json"))
  File "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/tempfile.py", line 287, in gettempdir
    tempdir = _get_default_tempdir()
  File "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/tempfile.py", line 219, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/var/folders/w4/s8fyh51n4x12q3c5kf4lm75h0000gn/T/', '/tmp', '/var/tmp', '/usr/tmp', '/Users/tuan/Claude/Tenor/tenor-rekey/probe']

exec
/bin/zsh -lc "rg -n \"Bluetooth|BLE|bridge|NUS|DFU|tcp:\" SPEC_CHAMELEON.md AUDIT_CHAMELEON.md 2>/dev/null | head -n 240; sed -n '1,220p' app/project.yml; git --no-pager diff --stat -- app probe; git --no-pager diff --check -- app probe" in /Users/tuan/Claude/Tenor/tenor-rekey
2026-07-21T04:45:44.677095Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
 succeeded in 3343ms:
2026-07-21 11:45:41.486 xcodebuild[47639:3439115]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:45:41.620 xcodebuild[47639:3439114] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-AUlNqmX8' (errno=Operation not permitted)
2026-07-21 11:45:42.058 xcodebuild[47642:3439132]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:45:42.190 xcodebuild[47642:3439131] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
 app/Sources/AppModel.swift          |  92 ++++++++++++++++++++++-
 app/Sources/Brand/L10n.swift        |   8 +-
 app/Sources/Shell/RootView.swift    |   4 +-
 app/Sources/Views/ConnectView.swift | 142 +++++++++++++++++++++++++++++++-----
 app/project.yml                     |   1 +
 app/tools/tenorrekey.entitlements   |   5 +-
 probe/chameleon/chameleon_com.py    |  15 +++-
 probe/chameleon_d.py                |   7 ++
 probe/test_chameleon.py             |  65 ++++++++++++++++-
 9 files changed, 312 insertions(+), 27 deletions(-)
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-d4TFxbnE' (errno=Operation not permitted)
2026-07-21 11:45:43.161 xcodebuild[47647:3439165]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:45:43.291 xcodebuild[47647:3439164] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-6FF6MeRI' (errno=Operation not permitted)
2026-07-21 11:45:43.726 xcodebuild[47653:3439182]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:45:43.855 xcodebuild[47653:3439181] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.

exec
/bin/zsh -lc "rg -n \"model\\.(connect|rescan|connectManual|bleConnect)\\(|await (connect|rescan|connectManual|bleConnect)\\(\" app/Sources | sort; rg -n \"func activeBridge|func applyReaderGone|bridge =|descriptor =\" app/Sources/AppModel.swift | head -n 100; sed -n '1,180p' app/Sources/Shell/RootView.swift; sed -n '1,190p' app/Sources/Views/SettingsView.swift 2>/dev/null || true; rg -n \"connect\\(\\)\" app/Sources/Views app/Sources/Shell | head -n 100" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
app/Sources/Shell/RootView.swift:56:            .task { await model.connect(); await model.monitor() }
app/Sources/Views/ConnectView.swift:134:            Task { await model.connectManual(port: port.path) }
app/Sources/Views/ConnectView.swift:257:            Task { await model.bleConnect(d.id) }
app/Sources/Views/ConnectView.swift:315:        Task { await model.connectManual(port: p) }
app/Sources/Views/ConnectView.swift:56:            Button { Task { await model.rescan() } } label: {
app/Sources/Views/SettingsView.swift:35:            Button(l.t("reconnect")) { Task { await model.connect() } }
163:    private func activeBridge() -> DeviceBridge {
166:        bridge = b
256:        descriptor = found
460:        bridge = nil                            // detach: no path can obtain the old bridge now
461:        descriptor = found
574:    private func applyReaderGone() {
import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// One unified workspace: the decoded / loaded image is the DOCUMENT on the canvas,
/// the card on the reader is a separate live device; reading / writing / format /
/// save / open are LABELLED actions on a always-visible action bar (so the
/// workflow is discoverable, not hidden behind cryptic toolbar icons). The
/// titlebar is hidden; a custom header carries the brand wordmark + reader
/// status cleanly (no system toolbar wells). Theme + language switch instantly.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l10n
    @Environment(\.colorScheme) private var systemScheme

    var body: some View {
        @Bindable var model = model
        Workspace()
            .background(WindowConfigurator())
            .preferredColorScheme(theme.appearance == .system ? nil : theme.scheme)
            .onAppear {
                theme.systemScheme = systemScheme
                l10n.systemCode = Locale.current.language.languageCode?.identifier ?? "en"
                // Refuse app quit while a firmware flash is writing (a mid-write kill can
                // brick the device); warn and keep the update running.
                AppDelegate.terminationGuard = {
                    guard model.flashing else { return .terminateNow }
                    let alert = NSAlert()
                    alert.messageText = l10n.t("quit_while_flashing_title")
                    alert.informativeText = l10n.t("quit_while_flashing_msg")
                    alert.addButton(withTitle: l10n.t("keep_updating"))
                    alert.runModal()
                    return .terminateCancel
                }
            }
            .onChange(of: systemScheme) { _, s in
                withAnimation(.easeInOut(duration: 0.35)) { theme.systemScheme = s }
            }
            .sheet(isPresented: $model.cloneSheet) {
                CloneSheet().environment(model).environment(theme).environment(l10n)
            }
            .sheet(isPresented: $model.flashingSheet, onDismiss: { model.clearFlashState() }) {
                FlashingView().environment(model).environment(theme).environment(l10n)
            }
            .confirmationDialog(l10n.t("format_q"), isPresented: $model.formatConfirm, titleVisibility: .visible) {
                // Pinned to the uid snapshot taken when the dialog opened, so a card
                // swapped in while it is open is never the one wiped.
                Button(l10n.t("format"), role: .destructive) {
                    Task { await model.format(authorizedUID: model.pendingFormatUID) }
                }
                Button(l10n.t("cancel"), role: .cancel) {}
            } message: {
                Text(l10n.t("format_msg") + (model.pendingFormatUID.map { "\n\n\(l10n.t("card_on_reader")): \($0)" } ?? ""))
            }
            .task { await model.connect(); await model.monitor() }
    }
}

private struct Workspace: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme

    var body: some View {
        VStack(spacing: 0) {
            HeaderBar()
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ActionBar()
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ErrorBanner()
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    if model.apduOpen {
                        Rectangle().fill(theme.p.hairline).frame(height: 1)
                        ApduConsole()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if model.inspectorOpen {
                    Rectangle().fill(theme.p.hairline).frame(width: 1)
                    SectorInspector().frame(width: 300)
                }
            }
        }
        .background(theme.p.canvas)
        .onDrop(of: [UTType.fileURL], isTargeted: nil) { providers in
            guard let provider = providers.first else { return false }
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                let url = (item as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) } ?? (item as? URL)
                guard let url else { return }
                Task { @MainActor in model.loadDump(from: url) }
            }
            return true
        }
    }
}

/// A dismissible status line for the last operation error (a clone that hit the
/// wrong card, a write that failed, a decode that was interrupted). Without it those
/// failures were silent - the model recorded them but nothing was ever shown. Glyph +
/// typography carry the signal (instrument discipline: no alarm colour).
private struct ErrorBanner: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        if let err = model.lastError {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                    .foregroundStyle(theme.p.textPrimary)
                Text(err).font(l.sans(11)).foregroundStyle(theme.p.textPrimary).lineLimit(2)
                Spacer()
                Button { withAnimation(.easeInOut(duration: 0.2)) { model.lastError = nil } } label: {
                    Image(systemName: "xmark").font(.system(size: 9))
                }.buttonStyle(.plain).foregroundStyle(theme.p.textTertiary).help(l.t("cancel"))
            }
            .padding(.horizontal, 16).padding(.vertical, 9)
            .background(theme.p.tileFill)
            Rectangle().fill(theme.p.hairline).frame(height: 1)
        }
    }
}

// MARK: - Header (brand + status + utilities), in content so we control the look

private struct HeaderBar: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        // The row sits BELOW the traffic-light band (top padding clears the
        // lights) so the wordmark left-aligns with the action bar margin instead
        // of being indented beside the lights.
        HStack(spacing: 12) {
            Lockup(focal: "rekey", size: 15)
            Spacer()
            ReaderStatusInline()
            Divider().frame(height: 16)
            Menu {
                ForEach(AppLang.allCases) { lang in
                    Button(lang == .system ? l.systemDisplay() : lang.display) { l.lang = lang }
                }
            } label: { Image(systemName: "globe") }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().help(l.t("language"))
            iconButton("sun.max", symbol: theme.toggleSymbol, help: l.t("light_dark")) { theme.toggle() }
            iconButton("sidebar.right", help: l.t("inspector")) { model.inspectorOpen.toggle() }
        }
        .font(l.sans(12))
        .foregroundStyle(theme.p.textSecondary)
        .padding(.leading, 16)
        .padding(.trailing, 14)
        .padding(.top, 30)
        .padding(.bottom, 12)
        .background(theme.p.panel)
    }
    private func iconButton(_ name: String, symbol: String? = nil, help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol ?? name) }
            .buttonStyle(.plain).foregroundStyle(theme.p.textSecondary).help(help)
    }
}

private struct ReaderStatusInline: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    var body: some View {
        @Bindable var model = model
        // The status pill is the Connect affordance: tapping it opens the device list /
        // rescan / manual-connect popover. A chevron marks it as tappable.
        Button { model.showConnect.toggle() } label: {
            HStack(spacing: 6) {
                Circle().fill(model.card != nil ? theme.p.accent : theme.p.textTertiary).frame(width: 6, height: 6)
                Text(text).font(model.card?.uid != nil ? Typeface.mono(11) : l.sans(11))
                    .foregroundStyle(theme.p.textSecondary)
                Image(systemName: "chevron.down").font(.system(size: 8)).foregroundStyle(theme.p.textTertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(l.t("connect"))
import SwiftUI
import UniformTypeIdentifiers

/// The Settings scene (⌘,). Native Form/List here (Form only at
/// settings) - the instrument canvas styling stays in the main window.
struct SettingsView: View {
    @Environment(L10n.self) private var l

    var body: some View {
        TabView {
            DeviceSettings().tabItem { Label(l.t("device"), systemImage: "cpu") }
            DictionarySettings().tabItem { Label(l.t("dictionaries"), systemImage: "key") }
            GeneralSettings().tabItem { Label(l.t("general"), systemImage: "gearshape") }
        }
        // Brand chrome font for the native settings form too; explicit mono /
        // sans sites below still override where data wants it.
        .font(l.sans(13))
        .frame(width: 470, height: 400)
    }
}

private struct DeviceSettings: View {
    @Environment(AppModel.self) private var model
    @Environment(L10n.self) private var l
    var body: some View {
        Form {
            Section {
                spec(l.t("model"), model.info?.model)
                spec(l.t("serial"), model.info?.serial)
                spec("mcu", model.info?.hw)
                LabeledContent(l.t("status")) {
                    Text(model.readerOnline ? l.t("reader_online") : l.t("reader_offline"))
                }
            }
            Button(l.t("reconnect")) { Task { await model.connect() } }
        }
        .formStyle(.grouped)
    }
    private func spec(_ label: String, _ value: String?) -> some View {
        LabeledContent(label) {
            Text(value ?? "-").font(Typeface.mono(13)).textSelection(.enabled)
        }
    }
}

private struct DictionarySettings: View {
    @Environment(AppModel.self) private var model
    @Environment(L10n.self) private var l
    @State private var newKey = ""
    @State private var selection = Set<String>()

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField(l.t("key_hint"), text: $newKey)
                    .font(Typeface.mono(13))
                    .onSubmit(addKey)
                Button(l.t("add"), action: addKey)
                    .disabled(KeyStore.normalized(newKey) == nil)
                Button(l.t("import")) { importDialog() }
            }
            .padding(12)
            List(selection: $selection) {
                ForEach(model.keyStore.keys, id: \.self) { key in
                    Text(key).font(Typeface.mono(13))
                }
                .onDelete { model.keyStore.remove(at: $0) }
                .onMove { model.keyStore.move(from: $0, to: $1) }
            }
            HStack {
                Text("\(model.keyStore.keys.count) \(l.t("user_keys"))  ·  +\(model.builtinKeyCount) \(l.t("builtin_keys"))")
                    .font(l.sans(11)).foregroundStyle(.secondary)
                Spacer()
                Button(l.t("remove")) { removeSelected() }.disabled(selection.isEmpty)
            }
            .padding(.horizontal, 12).padding(.top, 12)
            Divider()
            HStack {
                Text("\(model.learnedKeyCount) \(l.t("learned_keys"))")
                    .font(l.sans(11)).foregroundStyle(.secondary)
                Spacer()
                Button(l.t("clear_learned")) { Task { await model.clearLearnedKeys() } }
                    .disabled(model.learnedKeyCount == 0)
            }
            .padding(12)
        }
        .task { await model.refreshLearnedCount() }
    }

    private func addKey() {
        if model.keyStore.add(newKey) { newKey = "" }
    }
    private func removeSelected() {
        let idx = IndexSet(model.keyStore.keys.enumerated().filter { selection.contains($0.element) }.map(\.offset))
        model.keyStore.remove(at: idx)
        selection.removeAll()
    }
    private func importDialog() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.plainText, .text, .data]
        panel.allowsOtherFileTypes = true
        if panel.runModal() == .OK, let url = panel.url, let text = try? String(contentsOf: url, encoding: .utf8) {
            model.keyStore.importText(text)
        }
    }
}

private struct GeneralSettings: View {
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @State private var exportFolder = UserDefaults.standard.string(forKey: "rekey.exportFolder") ?? ""

    var body: some View {
        @Bindable var theme = theme
        @Bindable var l = l
        Form {
            Picker(l.t("appearance"), selection: $theme.appearance) {
                Text(l.t("lang_system")).tag(Appearance.system)
                Text(l.t("light")).tag(Appearance.light)
                Text(l.t("dark")).tag(Appearance.dark)
            }
            Picker(l.t("language"), selection: $l.lang) {
                ForEach(AppLang.allCases) { lang in
                    Text(lang == .system ? l.systemDisplay() : lang.display).tag(lang)
                }
            }
            LabeledContent(l.t("export_folder")) {
                HStack {
                    Text(exportFolder.isEmpty ? l.t("export_default") : (exportFolder as NSString).lastPathComponent)
                        .foregroundStyle(.secondary).lineLimit(1)
                    Button(l.t("choose")) { chooseFolder() }
                }
            }
        }
        .formStyle(.grouped)
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        if panel.runModal() == .OK, let url = panel.url {
            exportFolder = url.path
            UserDefaults.standard.set(url.path, forKey: "rekey.exportFolder")
        }
    }
}
app/Sources/Shell/RootView.swift:56:            .task { await model.connect(); await model.monitor() }
app/Sources/Views/SettingsView.swift:35:            Button(l.t("reconnect")) { Task { await model.connect() } }

2026-07-21T04:45:54.336517Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "nl -ba app/Sources/Shell/RootView.swift | sed -n '205,252p'; rg -n \"var busy|let busy|connecting\" app/Sources/Shell/RootView.swift app/Sources/Views/FlashingView.swift app/Sources/AppModel.swift; nl -ba app/Sources/Views/ConnectView.swift | sed -n '215,290p'; nl -ba app/Sources/AppModel.swift | sed -n '150,210p'; git --no-pager diff -- probe/test_chameleon.py | tail -n 150" in /Users/tuan/Claude/Tenor/tenor-rekey
2026-07-21T04:47:01.914929Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
 succeeded in 1883ms:
2026-07-21 11:47:00.228 xcodebuild[47780:3441444]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:47:00.384 xcodebuild[47780:3441442] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
git: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
git: error: couldn't create cache file '/tmp/xcrun_db-Mmf0EhnQ' (errno=Operation not permitted)
2026-07-21 11:47:00.885 xcodebuild[47782:3441456]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:47:01.026 xcodebuild[47782:3441455] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
diff --git a/probe/test_chameleon.py b/probe/test_chameleon.py
index c07113a..dbe489c 100644
--- a/probe/test_chameleon.py
+++ b/probe/test_chameleon.py
@@ -32,7 +32,7 @@ os.environ.setdefault("X7_LEARNED_PATH",
                       os.path.join(tempfile.gettempdir(), "rekey-test-learned.json"))
 
 import chameleon_d
-from chameleon.chameleon_com import ChameleonCom
+from chameleon.chameleon_com import ChameleonCom, TransportType, THREAD_BLOCKING_TIMEOUT
 from chameleon.chameleon_enum import (Status, SlotNumber, TagSpecificType, TagSenseType, MfcKeyType,
                                       MifareClassicPrngType, MifareClassicDarksideStatus)
 from chameleon.chameleon_utils import UnexpectedResponseError
@@ -3078,6 +3078,66 @@ def test_cham_emulate_load_ntag_gap(check):
           and emu.ntag_emu_counters[0][0] == 7, str(emu.ntag_emu_counters))
 
 
+# --------------------------------------------------------------------------
+# 34. dfu_flash fails closed over the BLE/tcp bridge: when the active port is the Swift
+#     loopback (CHAMELEON_PORT=tcp:...), the device is on Bluetooth but adafruit-nrfutil
+#     flashes over USB - it could enter-bootloader / write a DIFFERENT USB device. The guard
+#     is at the very top of dfu_flash: it raises before any download / port scan / reboot /
+#     subprocess, so none of those seams run.
+# --------------------------------------------------------------------------
+def test_cham_dfu_flash_rejects_ble(check):
+    d = _dfu_daemon(FakeChameleon(model=0))
+    d._port = "tcp:127.0.0.1:5000"                     # active connection is the Swift BLE bridge
+    called = {}
+
+    def _mark(key, ret):
+        def _fn(*a, **k):
+            called[key] = True
+            return ret
+        return _fn
+    d._find_dfu_ports = _mark("scan", [])
+    d._find_cdc_ports = _mark("scan", [])
+    d._download_asset = _mark("download", 0)
+    d._send_enter_dfu = _mark("entered", None)
+    d._run_flash = _mark("flashed", None)
+    raised = None
+    try:
+        d.dfu_flash({})
+    except RuntimeError as e:
+        raised = str(e)
+    check("dfu_flash over the BLE/tcp bridge raises (bluetooth/usb message)",
+          raised is not None and "bluetooth" in raised.lower() and "usb" in raised.lower(),
+          repr(raised))
+    check("dfu_flash over BLE runs no seam (no download / port scan / reboot / flash)",
+          not called, str(called))
+
+
+# --------------------------------------------------------------------------
+# 35. socket transport recognises an orderly peer EOF: recv() returning b'' must end the
+#     receive loop (transport dropped), not hot-spin on empty reads. Driven with a real
+#     socketpair so the loop runs against a genuine socket close.
+# --------------------------------------------------------------------------
+def test_cham_socket_eof_exits(check):
+    import socket as _socket
+    a, b = _socket.socketpair()
+    a.settimeout(THREAD_BLOCKING_TIMEOUT)              # bound recv so it can see the EOF quickly
+    c = ChameleonCom()
+    c.transport = a
+    c.transport_type = TransportType.SOCKET
+    c.event_closing.clear()
+    t = threading.Thread(target=c.thread_data_receive)
+    t.start()
+    b.close()                                          # orderly peer close -> recv() returns b''
+    t.join(2)
+    check("thread_data_receive exits promptly on peer EOF (no hot spin)",
+          not t.is_alive() and c.transport is None,
+          "alive=%s transport=%s" % (t.is_alive(), c.transport))
+    try:
+        a.close()
+    except OSError:
+        pass
+
+
 TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
          test_cham_read_block, test_cham_decode, test_cham_decode_partial,
          test_cham_decode_nocard, test_cham_decode_swap, test_cham_decode_user_key,
@@ -3107,7 +3167,8 @@ TESTS = [test_cham_info, test_cham_slots, test_cham_slot_select, test_cham_poll,
          test_cham_dfu_flash_safety, test_cham_dfu_download_checks,
          test_cham_dfu_identity_binding, test_cham_dfu_settle_past_deadline,
          test_cham_dfu_cancel_through_run,
-         test_cham_dfu_eof_waits_for_flash, test_cham_dfu_eof_dispatch_race]
+         test_cham_dfu_eof_waits_for_flash, test_cham_dfu_eof_dispatch_race,
+         test_cham_dfu_flash_rejects_ble, test_cham_socket_eof_exits]
 
 
 if __name__ == "__main__":

exec
/bin/zsh -lc "rg -n \"deviceBusy|begin.*Op|guard .*swapping|decoding = true|cloning = true|formatting = true|apduBusy = true|slotBusy = true|lfBusy = true\" app/Sources/AppModel.swift | sed -n '1,260p'; nl -ba app/Sources/AppModel.swift | sed -n '555,760p'; nl -ba app/Sources/AppModel.swift | sed -n '800,1040p'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
82:    /// owns the reader. Folded into `deviceBusy` so no other op races it.
95:    /// An LF op (read / write / emulate) owns the reader. Folded into `deviceBusy`.
117:    /// A firmware flash is in flight (owns the device; folded into `deviceBusy`, so the
157:    private var deviceBusy: Bool { decoding || cloning || formatting || apduBusy || slotBusy || flashing || lfBusy }
200:    var canChangeDevice: Bool { !swapping && !deviceBusy && !bleConnecting }
243:        guard !swapping, !deviceBusy else { return }
287:        guard !swapping, !deviceBusy else { return }
301:        guard !swapping, !deviceBusy else { return }
414:            if deviceBusy || swapping || bleConnecting { continue }
452:        guard !swapping else { return }
588:        // owns the reader (deviceBusy includes `decoding`, so this also blocks a
592:        guard !swapping, !deviceBusy, !emulating else { return }
593:        decoding = true
892:        guard !swapping, !deviceBusy, !emulating else { return }
905:        cloning = true
963:        guard !swapping, !deviceBusy, !emulating else { return }
971:        formatting = true
1036:        guard !clean.isEmpty, !swapping, !deviceBusy, !emulating else { return }
1037:        apduBusy = true
1059:        guard capabilities.slots > 0, !swapping, !deviceBusy else { return }
1093:        guard capabilities.slots > 0, !swapping, !deviceBusy else { return }
1094:        slotBusy = true
1108:        guard capabilities.slots > 0, !swapping, !deviceBusy else { return }
1113:        slotBusy = true
1165:        guard capabilities.emulate, !swapping, !deviceBusy else { return }
1166:        slotBusy = true
1194:        guard capabilities.emulate, !swapping, !deviceBusy else { return }
1195:        slotBusy = true
1214:        guard capabilities.emulate, !swapping, !deviceBusy else { return }
1216:        slotBusy = true
1230:        guard capabilities.lf, !swapping, !deviceBusy, !emulating else { return }
1231:        lfBusy = true
1243:        guard capabilities.lf, !swapping, !deviceBusy, !emulating else { return }
1246:        lfBusy = true
1261:        guard capabilities.lf, capabilities.emulate, !swapping, !deviceBusy else { return }
1270:        lfBusy = true
1290:        guard capabilities.dfu, !swapping, !deviceBusy else { return }
1310:        guard capabilities.dfu, !swapping, !deviceBusy else { return }
   555	    /// reader. Shared by the swap, removal, and reader-gone paths so they cannot drift.
   556	    private func clearCardBound() {
   557	        cloneResults = [:]
   558	        cloneFailReasons = [:]
   559	        ntagDoc = nil
   560	        noKeysFound = false
   561	    }
   562	
   563	    /// Drop the working document entirely (the source tag's clear button): the image,
   564	    /// its grid, page dump, and selection. The card on the reader is untouched.
   565	    func clearDocument() {
   566	        withAnimation(.easeInOut(duration: 0.3)) {
   567	            source = nil; sectors = []; ntagDoc = nil; selected = nil; selectedBlock = nil
   568	            cloneResults = [:]; cloneFailReasons = [:]; noKeysFound = false
   569	        }
   570	    }
   571	
   572	    /// Reader unplugged or the daemon went away: go offline and clear everything
   573	    /// tied to a live reader. No-op when already in that state (avoids churn).
   574	    private func applyReaderGone() {
   575	        cardAbsentStreak = 0
   576	        guard readerOnline || card != nil || info != nil else { return }
   577	        withAnimation(.easeInOut(duration: 0.3)) {
   578	            readerOnline = false
   579	            info = nil
   580	            card = nil
   581	            clearCardBound()
   582	            resetChameleonState()
   583	        }
   584	    }
   585	
   586	    func decode() async {
   587	        // Refuse while a swap is tearing the device down, or another device op already
   588	        // owns the reader (deviceBusy includes `decoding`, so this also blocks a
   589	        // double-decode). Also refuse while emulating: a reader op would force the device
   590	        // back to reader mode under the emulation, leaving the toggle lying. Serialized,
   591	        // never racing the bridge.
   592	        guard !swapping, !deviceBusy, !emulating else { return }
   593	        decoding = true
   594	        decodeCancelled = false
   595	        decodeProgress = nil
   596	        decodeStart = Date()
   597	        lastError = nil
   598	        cloneResults = [:]
   599	        cloneFailReasons = [:]
   600	        // Couple the card under the op's OWN patient retry: the snappy 1.5s status poll
   601	        // (tries=8) can miss a card that is physically seated but slow to first-contact,
   602	        // while a full poll finds it. So a decode is not gated on the status poll having
   603	        // detected the card yet - if `card` is not set, poll now with the full retry.
   604	        var live = card
   605	        if live?.present != true {
   606	            live = try? await activeBridge().poll(tries: 25)
   607	            if let l = live, l.present {
   608	                withAnimation(.easeInOut(duration: 0.3)) { card = l }
   609	            }
   610	        }
   611	        guard let live, live.present else {
   612	            // Nothing coupled: surface it instead of silently spinning, and DO NOT drop
   613	            // the held working document (nothing new was read to replace it with).
   614	            lastError = "no card on reader"
   615	            finishDecode()
   616	            return
   617	        }
   618	        let startUID = live.uid
   619	        // The working DOCUMENT is deliberately NOT dropped up front: a decode that finds
   620	        // no keys, hits a changed card, or is cancelled must leave the held, unsaved
   621	        // image intact - only a genuine new result replaces it (see restoreDocument).
   622	        withAnimation(.easeInOut(duration: 0.3)) { selectedBlock = nil; noKeysFound = false }
   623	        do {
   624	            if live.isNTAG {
   625	                // NTAG / Ultralight: a read-only page view, not a writable document.
   626	                let r = try await activeBridge().readNTAG()
   627	                if r.present == false {
   628	                    lastError = "no card on reader"
   629	                    restoreDocument()
   630	                } else if let s = startUID, let ru = r.uid, Self.normUID(ru) != Self.normUID(s) {
   631	                    lastError = "card changed during read"
   632	                    restoreDocument()
   633	                } else {
   634	                    // Keep the whole read (pages + detected type + its own UID + version /
   635	                    // signature / counters) so the load-to-slot path re-emulates this exact
   636	                    // tag, not a page-count guess against the live card.
   637	                    let doc = Self.buildNtagDoc(r)
   638	                    withAnimation(.easeInOut(duration: 0.3)) {
   639	                        ntagDoc = doc; source = nil; sectors = []; selected = nil
   640	                    }
   641	                }
   642	            } else {
   643	                // show the whole grid right away (all pending) so sectors fill in
   644	                // live as each one is searched, instead of a blank wait.
   645	                let count = live.sak.map { sectorsForSak($0) } ?? 16
   646	                withAnimation(.easeInOut(duration: 0.3)) {
   647	                    sectors = Self.pendingSectors(count: count); ntagDoc = nil; selected = nil
   648	                }
   649	                let r = try await activeBridge().decode(userKeys: keyStore.keys,
   650	                    onProgress: { [weak self] ev in Task { @MainActor in self?.applyDecodeEvent(ev) } })
   651	                if let s = startUID, Self.normUID(r.uid) != Self.normUID(s) {
   652	                    lastError = "card changed during decode"
   653	                    restoreDocument()
   654	                } else if r.recovered == 0 {
   655	                    // A no-RESULT decode must NEVER drop a held, unsaved document - not
   656	                    // even when the read card shares its uid (cloned access cards commonly
   657	                    // do). Keep the image whenever one is held; only when nothing is held
   658	                    // do we show the honest no-keys result (or a clean slate on a cancel).
   659	                    if source != nil {
   660	                        if r.cancelled != true { lastError = "no keys found on the card; the document is unchanged" }
   661	                        restoreDocument()
   662	                    } else if r.cancelled == true {
   663	                        withAnimation(.easeInOut(duration: 0.3)) { sectors = []; ntagDoc = nil; selected = nil }
   664	                    } else {
   665	                        withAnimation(.easeInOut(duration: 0.3)) {
   666	                            card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
   667	                            sectors = []; ntagDoc = nil; selected = nil
   668	                            noKeysFound = true
   669	                        }
   670	                    }
   671	                } else {
   672	                    let vms = Self.buildSectors(r)
   673	                    let dump = CardDump.from(r, name: r.uid.replacingOccurrences(of: " ", with: ""))
   674	                    withAnimation(.easeInOut(duration: 0.3)) {
   675	                        card = PollResult(present: true, uid: r.uid, atqa: r.atqa, sak: r.sak)
   676	                        sectors = vms
   677	                        ntagDoc = nil
   678	                        selected = vms.first(where: { $0.hasKey })?.index ?? vms.first?.index
   679	                        source = dump
   680	                    }
   681	                }
   682	            }
   683	        } catch {
   684	            // A hard-kill cancel (the fallback when cooperative cancel does not land)
   685	            // surfaces as a thrown error, not a real failure. Either way, restore the
   686	            // held document rather than leaving a half-filled grid or dropping it.
   687	            if !decodeCancelled { lastError = "\(error)" }
   688	            restoreDocument()
   689	        }
   690	        finishDecode()
   691	    }
   692	
   693	    private func finishDecode() {
   694	        decoding = false
   695	        decodeCancelled = false
   696	        decodeProgress = nil
   697	        decodeStart = nil
   698	    }
   699	
   700	    /// Put the canvas back on the held working document (its sector grid), or empty it
   701	    /// when nothing is held. Used when a decode produced no new result (no card, card
   702	    /// changed, no keys against a held image, or a cancel) so an unsaved image is never
   703	    /// left dropped or hidden behind a failed read.
   704	    private func restoreDocument() {
   705	        withAnimation(.easeInOut(duration: 0.3)) {
   706	            if let s = source {
   707	                sectors = Self.buildSectors(fromDump: s)
   708	                ntagDoc = nil
   709	                selected = sectors.first(where: { $0.hasKey })?.index ?? sectors.first?.index
   710	            } else {
   711	                sectors = []; ntagDoc = nil; selected = nil
   712	            }
   713	        }
   714	    }
   715	
   716	    /// Stop a long decode cooperatively: the daemon trips its cancel flag and returns
   717	    /// the partial image it has gathered (which decode() then shows or discards), so we
   718	    /// no longer kill the daemon on every cancel. A hard-kill fallback inside the bridge
   719	    /// covers a wedged daemon that does not honour the cancel in time.
   720	    func cancelDecode() async {
   721	        guard decoding else { return }
   722	        decodeCancelled = true
   723	        await activeBridge().cancel()
   724	    }
   725	
   726	    /// Resolved-sector count (sectors whose key was found), an honest, monotonic
   727	    /// secondary progress readout alongside the raw auth counter.
   728	    var resolvedSectors: Int { sectors.filter { $0.status == .found }.count }
   729	
   730	    /// Whole seconds elapsed in the running decode: bounded, forward-moving feedback
   731	    /// where the auth counter has no meaningful denominator. 0 when not decoding.
   732	    var decodeElapsed: Int { decodeStart.map { max(0, Int(Date().timeIntervalSince($0))) } ?? 0 }
   733	
   734	    /// Fold a decode progress event into `decodeProgress`. The daemon emits a
   735	    /// sector-boundary event (carries `total` = sector count) and a dictionary-walk
   736	    /// event (carries attempts / walk_total) as it searches a sector's key.
   737	    private func applyDecodeEvent(_ ev: EngineEvent) {
   738	        guard decoding, let s = ev.sector else { return }
   739	        let fallbackTotal = card?.sak.map { sectorsForSak($0) } ?? 16
   740	        var p = decodeProgress ?? DecodeProgress(sector: 0, total: fallbackTotal, attempts: nil, walkTotal: nil)
   741	        p.sector = s
   742	        // `attempts` is the scan's GLOBAL, monotonic auth counter, so keep it across
   743	        // sector boundaries - the status line then only ever moves forward. `walkTotal`
   744	        // is the adaptive remaining-work estimate and shrinks as sectors resolve.
   745	        if let t = ev.total { p.total = t }
   746	        if let a = ev.attempts { p.attempts = a; p.walkTotal = ev.walk_total }
   747	        decodeProgress = p
   748	
   749	        // per-sector live tile state
   750	        guard sectors.indices.contains(s) else { return }
   751	        if ev.attempts != nil {                           // walk event: this sector is searching
   752	            sectors[s].status = .searching
   753	            sectors[s].searchTried = ev.attempts
   754	            sectors[s].searchTotal = ev.walk_total
   755	        } else if ev.total != nil {                       // sector boundary: this sector is done
   756	            sectors[s].searchTried = nil
   757	            sectors[s].searchTotal = nil
   758	            if let kh = ev.key {
   759	                sectors[s].status = .found
   760	                sectors[s].keyType = ev.keytype
   800	                ? .unknown
   801	                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
   802	            let blocks = blockNumbers(ofSector: s).map { b in (r.blocks[String(b)] ?? nil) ?? "?" }
   803	            return SectorVM(index: s, keyType: kt, keyHex: kh, provenance: prov, blocks: blocks,
   804	                            status: kh == nil ? .failed : .found,
   805	                            assumedSlot: r.assumed_keys?[String(s)])
   806	        }
   807	    }
   808	
   809	    /// Build the grid from a loaded dump (File > Open), so an opened image renders
   810	    /// its memory map exactly like a fresh decode. Dump block hex is stored without
   811	    /// spaces; re-space it to match the daemon's display form the grid expects.
   812	    static func buildSectors(fromDump d: CardDump) -> [SectorVM] {
   813	        (0..<d.sectorCount).map { s in
   814	            let k = d.keys[s]
   815	            let kh = k?.hex
   816	            let prov: KeyProvenance = kh == nil
   817	                ? .unknown
   818	                : (kh == "ffffffffffff" ? .dictionary : .nonDefault)
   819	            let blocks = blockNumbers(ofSector: s).map { b in d.blocks[b].map(spacedHex) ?? "?" }
   820	            return SectorVM(index: s, keyType: k?.type, keyHex: kh, provenance: prov, blocks: blocks,
   821	                            status: kh == nil ? .failed : .found,
   822	                            assumedSlot: d.assumedKeys[s])
   823	        }
   824	    }
   825	
   826	    /// "0102..1f" -> "01 02 .. 1f" so dump-loaded blocks render like decode blocks.
   827	    static func spacedHex(_ h: String) -> String {
   828	        stride(from: 0, to: h.count, by: 2).compactMap { i -> String? in
   829	            let start = h.index(h.startIndex, offsetBy: i)
   830	            let end = h.index(start, offsetBy: min(2, h.count - i))
   831	            return String(h[start..<end])
   832	        }.joined(separator: " ")
   833	    }
   834	
   835	    /// The full sector grid, all pending, shown the instant decode starts so the
   836	    /// card's memory map is visible and fills in live sector by sector.
   837	    static func pendingSectors(count: Int) -> [SectorVM] {
   838	        (0..<count).map {
   839	            SectorVM(index: $0, keyType: nil, keyHex: nil, provenance: .unknown, blocks: [], status: .pending)
   840	        }
   841	    }
   842	
   843	    // ---- copy (plain text) -------------------------------------------------
   844	
   845	    /// Plain-text rendering of a sector: a header line with the key, then one
   846	    /// line per block (absolute block number + hex). Used by ⌘C and the tile
   847	    /// context menu so the grid is a real, copyable instrument.
   848	    func sectorText(_ s: SectorVM) -> String {
   849	        var head = "sector \(s.index)"
   850	        if let kh = s.keyHex { head += "  (key \(s.keyType?.lowercased() ?? "a") \(kh))" }
   851	        let base = firstBlock(s.index)
   852	        let body = s.blocks.enumerated().map { i, hex in String(format: "%3d  %@", base + i, hex) }
   853	        return ([head] + body).joined(separator: "\n")
   854	    }
   855	
   856	    /// Plain-text rendering of a single block: absolute block number + hex.
   857	    func blockText(_ blk: Int, hex: String) -> String {
   858	        String(format: "%3d  %@", blk, hex)
   859	    }
   860	
   861	    /// Plain text for ⌘C: the selected sector, or the whole NTAG page dump.
   862	    func copySelectionText() -> String? {
   863	        if let s = selectedSector { return sectorText(s) }
   864	        if !pages.isEmpty {
   865	            return pages.map { String(format: "%3d  %@  |%@|", $0.index, $0.hex, $0.ascii) }.joined(separator: "\n")
   866	        }
   867	        return nil
   868	    }
   869	
   870	    func copy(_ text: String) {
   871	        NSPasteboard.general.clearContents()
   872	        NSPasteboard.general.setString(text, forType: .string)
   873	    }
   874	
   875	    // ---- clone / write -----------------------------------------------------
   876	
   877	    /// Write the explicit source dump onto the card on the reader. Data blocks
   878	    /// only by default; trailers (keys/access) and block 0 (uid) are opt-in.
   879	    ///
   880	    /// `authorizedUID` is the uid the user saw and authorized when they pressed write /
   881	    /// accepted the confirm. The card can be swapped by the live monitor while a confirm
   882	    /// dialog is open, so we execute ONLY if the card on the reader still equals that
   883	    /// authorization - never write to a card whose uid differs from the one shown.
   884	    ///
   885	    /// `realCard` re-keys a REAL (non-magic) card the owner already holds the keys to: on a
   886	    /// Chameleon it routes to `write_mfd` (known-key auth + the anti-brick trailer guards)
   887	    /// instead of the magic-card `magic_write`. The X7 already has no magic path, so it always
   888	    /// uses write_mfd and the flag is a no-op there.
   889	    func clone(trailers: Bool, uid: Bool, authorizedUID: String?, realCard: Bool = false) async {
   890	        // Never clone while a swap is in flight, another device op owns the reader, or
   891	        // the device is emulating (a reader-mode write would break the emulation).
   892	        guard !swapping, !deviceBusy, !emulating else { return }
   893	        guard let src = cloneSource else {
   894	            lastError = "no clone source"
   895	            return
   896	        }
   897	        // The authorization is bound to a specific card: if the card went absent or a
   898	        // different one was seated (e.g. while the confirm dialog was open), the write
   899	        // is not authorized for whatever is on the reader now. Abort rather than wipe it.
   900	        guard let auth = authorizedUID, let target = card?.uid,
   901	              Self.normUID(target) == Self.normUID(auth) else {
   902	            lastError = "card changed, not written"
   903	            return
   904	        }
   905	        cloning = true
   906	        cloneResults = [:]
   907	        cloneFailReasons = [:]
   908	        lastError = nil
   909	        // Per-block glyph updates stream the same way for both write paths.
   910	        let onBlock: @Sendable (Int, Bool, String?) -> Void = { [weak self] b, ok, reason in
   911	            Task { @MainActor in
   912	                withAnimation(.easeOut(duration: 0.16)) { self?.cloneResults[b] = ok }
   913	                if let reason { self?.cloneFailReasons[b] = reason }
   914	            }
   915	        }
   916	        do {
   917	            // Capability-driven route: a Chameleon clones onto a MAGIC card via `magic_write`,
   918	            // or re-keys a REAL card (realCard) via `write_mfd`; the X7 has no magic path and
   919	            // always uses write_mfd. All three return the same WriteResult, so the outcome
   920	            // handling below is shared.
   921	            let r: WriteResult
   922	            if capabilities.emulate && !realCard {
   923	                r = try await activeBridge().magicWrite(
   924	                    blocks: src.blockParams, keys: src.keyParams, trailers: trailers, uid: uid,
   925	                    targetUID: target, onBlock: onBlock)
   926	            } else {
   927	                r = try await activeBridge().writeMFD(
   928	                    blocks: src.blockParams, keys: src.keyParams, trailers: trailers, uid: uid,
   929	                    targetUID: target, onBlock: onBlock)
   930	            }
   931	            // Per-block glyphs in the grid/inspector are the primary failure surface;
   932	            // lastError is the summary shown in the status banner, phrased in card terms.
   933	            if r.present == false {
   934	                lastError = "no card on reader"
   935	            } else if let e = r.error {
   936	                lastError = e
   937	            } else if let failed = r.failed, !failed.isEmpty {
   938	                lastError = Self.cloneFailureSummary(failed, reasons: cloneFailReasons)
   939	            }
   940	        } catch {
   941	            lastError = "\(error)"
   942	        }
   943	        cloning = false
   944	    }
   945	
   946	    /// Snapshot the current card uid and open the format confirmation. The snapshot is
   947	    /// what the dialog shows and what the erase is pinned to, so a card swapped in while
   948	    /// the dialog is open is never the one wiped.
   949	    func requestFormat() {
   950	        pendingFormatUID = card?.uid
   951	        formatConfirm = true
   952	    }
   953	
   954	    /// Factory-reset the card on the reader (zero data + factory trailer). Offered for
   955	    /// ANY present card, not only the one just decoded: auth uses the document's
   956	    /// recovered keys when the document IS this card, otherwise a factory-key (FF) wipe,
   957	    /// which is what a blank or freshly-issued card needs. Destructive, so the UI gates
   958	    /// it behind a confirm. The anti-brick guards (trailer written last, per-card uid
   959	    /// pin) stay in the daemon; a card whose keys are unknown simply fails, never bricks.
   960	    func format(authorizedUID: String?) async {
   961	        // Never format while a swap is in flight, another device op owns the reader, or
   962	        // the device is emulating (a reader-mode erase would break the emulation).
   963	        guard !swapping, !deviceBusy, !emulating else { return }
   964	        // Bound to the card the user authorized in the confirm dialog: if it was swapped
   965	        // or lifted while the dialog was open, do not erase whatever is on the reader now.
   966	        guard let auth = authorizedUID, let target = card?.uid,
   967	              Self.normUID(target) == Self.normUID(auth) else {
   968	            lastError = "card changed, not written"
   969	            return
   970	        }
   971	        formatting = true
   972	        cloneResults = [:]
   973	        lastError = nil
   974	        // Only the document's keys help when it IS this card; an unrelated / absent
   975	        // document contributes nothing, so fall back to a factory-key wipe attempt.
   976	        let keys = canFormat ? (source?.keyParams ?? [:]) : [:]
   977	        do {
   978	            let r = try await activeBridge().formatCard(keys: keys, targetUID: target)
   979	            if r.present == false {
   980	                lastError = "no card on reader"
   981	            } else if let e = r.error {
   982	                lastError = e                      // aborted (wrong / swapped card): keep the image
   983	            } else if let failed = r.failed, !failed.isEmpty {
   984	                // A partial or fully failed format did NOT blank the card, so keep the
   985	                // document: it may still be the only copy of the image.
   986	                lastError = Self.formatFailureSummary(failed)
   987	            }
   988	            // On a clean format the document is kept (not dropped): its uid is unchanged
   989	            // (block 0 is left intact), so it stays available to erase/re-issue the next
   990	            // identical card without decoding each one again.
   991	        } catch {
   992	            lastError = "\(error)"
   993	        }
   994	        formatting = false
   995	    }
   996	
   997	    /// Aggregate clone status for one sector tile, from the per-block results.
   998	    func cloneStatus(ofSector s: Int) -> SectorCloneStatus {
   999	        let results = blockNumbers(ofSector: s).compactMap { cloneResults[$0] }
  1000	        if results.isEmpty { return .none }
  1001	        return results.contains(false) ? .failed : .ok
  1002	    }
  1003	
  1004	    /// Which sector an absolute block belongs to (4K big-sector layout aware).
  1005	    static func sectorOf(_ block: Int) -> Int { block < 128 ? block / 4 : 32 + (block - 128) / 16 }
  1006	
  1007	    /// A clone failure summary in card terms: name the sector and, for a refused
  1008	    /// trailer, WHY (the daemon already computed it), instead of raw block indices.
  1009	    static func cloneFailureSummary(_ failed: [Int], reasons: [Int: String]) -> String {
  1010	        let parts: [String] = failed.sorted().map { b in
  1011	            let s = sectorOf(b)
  1012	            switch reasons[b] {
  1013	            case "access-bits":    return "sector \(s) trailer refused: unsafe access bits"
  1014	            case "trailer-lockout": return "sector \(s) trailer refused: would lock its own keys"
  1015	            default:               return "sector \(s) block \(b)"
  1016	            }
  1017	        }
  1018	        return "write failed - " + parts.joined(separator: "; ")
  1019	    }
  1020	
  1021	    /// A format failure summary in card terms (which sectors could not be wiped).
  1022	    static func formatFailureSummary(_ failed: [Int]) -> String {
  1023	        let sectors = Set(failed.map { sectorOf($0) }).sorted().map(String.init).joined(separator: ", ")
  1024	        return "format failed - sector(s) \(sectors) could not be wiped"
  1025	    }
  1026	
  1027	    // ---- apdu --------------------------------------------------------------
  1028	
  1029	    /// Send a raw APDU to the card on the reader and append the outcome to the
  1030	    /// console transcript. Distinguishes a real response, a card that gave no
  1031	    /// answer (e.g. a MIFARE Classic, not ISO14443-4), and no card present.
  1032	    func sendAPDU(_ hex: String) async {
  1033	        let clean = hex.trimmingCharacters(in: .whitespaces).lowercased()
  1034	        // Never send while a swap is in flight, another device op owns the reader, or the
  1035	        // device is emulating (an apdu is a reader op that would break the emulation).
  1036	        guard !clean.isEmpty, !swapping, !deviceBusy, !emulating else { return }
  1037	        apduBusy = true
  1038	        let id = (apduLog.last?.id ?? 0) + 1
  1039	        do {
  1040	            let r = try await activeBridge().apdu(clean)

exec
/bin/zsh -lc "head -n 20 app/Sources/AppModel.swift; rg -n \"@Observable\" app/Sources | cut -d: -f1 | sort -u | xargs -I{} sh -c 'echo {}; head -n 8 {}'" in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 0ms:
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
app/Sources/AppModel.swift
import SwiftUI
import Observation
import AppKit

/// Observable app state. The decoded / loaded image is the DOCUMENT; the card on the
/// reader is a separate live device state. Heavy work stays on the device bridge
/// actor; this holds @MainActor UI state only.
@MainActor
app/Sources/Brand/L10n.swift
import SwiftUI
import Observation

/// Lightweight in-app localization (vi / en / zh / ja) with instant runtime
/// switching - the manager is observable, so reading t() in a view body
/// re-renders when the language changes. Technical tokens (uid/atqa/sak, hex,
/// product names) stay verbatim; only readable chrome is translated.
enum AppLang: String, CaseIterable, Identifiable {
app/Sources/Brand/Theme.swift
import SwiftUI

/// Semantic theme tokens, resolved per appearance. Brand-canonical roles come
/// from the generated TenorColor (single source of truth); the in-between
/// chrome greys are app-derived surface tones (not brand palette entries).
///
/// The whole app paints from these tokens so a light/dark switch is an animated
/// crossfade (preferredColorScheme + dynamic system colors SNAP, so we
app/Sources/Engine/BLEManager.swift
import Foundation
import CoreBluetooth
import Network

/// A discovered Bluetooth LE advertiser that speaks (or, in the bootloader, would speak)
/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
/// is sitting in its bootloader.
struct BLEDevice: Identifiable, Equatable, Sendable {
app/Sources/Engine/KeyStore.swift
import SwiftUI
import Observation

/// The user's editable MIFARE key list (Settings > Dictionaries). These are the
/// keys tried FIRST, before the daemon's large built-in curated dictionary - so
/// it holds only the user's own / site-specific keys, persisted across launches.
/// Order matters - user keys are tried in order, newest at the front.
@MainActor

exec
/bin/zsh -lc 'SDK_PATH=$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true); echo "$SDK_PATH"; rg -n "exported import Observation|@_exported import Observation|import Observation" "$SDK_PATH"/System/Library/Frameworks/Foundation.framework/Modules "$SDK_PATH"/System/Library/Frameworks/CoreBluetooth.framework/Modules "$SDK_PATH"/System/Library/Frameworks/Network.framework/Modules 2>/dev/null | head -n 50; rg -n "macro Observable|public macro Observable" "$SDK_PATH"/System/Library/Frameworks/Observation.framework/Modules 2>/dev/null | head -n 20' in /Users/tuan/Claude/Tenor/tenor-rekey
 succeeded in 2072ms:
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk/System/Library/Frameworks/Foundation.framework/Modules/Foundation.swiftmodule/arm64e-apple-macos.swiftinterface:12:@_exported import Observation
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk/System/Library/Frameworks/Foundation.framework/Modules/Foundation.swiftmodule/x86_64-apple-macos.swiftinterface:12:@_exported import Observation
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk/System/Library/Frameworks/Foundation.framework/Modules/Foundation.swiftmodule/x86_64-apple-ios-macabi.swiftinterface:12:@_exported import Observation
/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk/System/Library/Frameworks/Foundation.framework/Modules/Foundation.swiftmodule/arm64e-apple-ios-macabi.swiftinterface:12:@_exported import Observation

2026-07-21T04:48:15.682759Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
exec
/bin/zsh -lc "PYTHONDONTWRITEBYTECODE=1 X7_LEARNED_PATH=/dev/null python3 -c 'import tempfile; tempfile.tempdir=\"/tmp\"; import test_chameleon; failures=[]; check=lambda name,ok,detail=\"\": (print((\"PASS \" if ok else \"FAIL \")+name), failures.append((name,detail)) if not ok else None); test_chameleon.test_cham_dfu_flash_rejects_ble(check); test_chameleon.test_cham_socket_eof_exits(check); raise_code=bool(failures); raise SystemExit(raise_code)'" in /Users/tuan/Claude/Tenor/tenor-rekey/probe
2026-07-21T04:48:23.985182Z ERROR codex_models_manager::manager: failed to renew cache TTL: missing field `supports_reasoning_summaries` at line 88 column 5
 succeeded in 2012ms:
2026-07-21 11:48:22.146 xcodebuild[47993:3443656]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:48:22.300 xcodebuild[47993:3443655] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
python3: warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead
python3: error: couldn't create cache file '/tmp/xcrun_db-pvkJhgMa' (errno=Operation not permitted)
2026-07-21 11:48:22.814 xcodebuild[47996:3443668]  DVTFilePathFSEvents: Failed to start fs event stream.
2026-07-21 11:48:22.952 xcodebuild[47996:3443667] [MT] DVTDeveloperPaths: Failed to get length of DARWIN_USER_CACHE_DIR from confstr(3), error = Error Domain=NSPOSIXErrorDomain Code=5 "Input/output error". Using NSCachesDirectory instead.
PASS dfu_flash over the BLE/tcp bridge raises (bluetooth/usb message)
PASS dfu_flash over BLE runs no seam (no download / port scan / reboot / flash)
[so[0m
PASS thread_data_receive exits promptly on peer EOF (no hot spin)

codex
Three real defects remain.

1. HIGH - attempt generation does not actually scope CoreBluetooth callbacks  
   [BLEManager.swift:171](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:171)

   `attemptGen` only protects the timeout. `isActive` compares identifiers, so a late disconnect, failure, handshake acknowledgement, or notification from an earlier connection to the same peripheral is accepted by the new attempt. It can fail or prematurely advance the current continuation.

   Fix: serialize reconnecting the same peripheral until cancellation completes, or attach a per-attempt token through delegate proxies/state and validate it in every callback. Identifier equality is insufficient.

2. HIGH - queued old bridge callbacks can affect a new link  
   [BLEManager.swift:256](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:256), [BLEManager.swift:303](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:303)

   Clearing handlers does not cancel callbacks or `Task` blocks already queued. An old listener callback can resolve or fail the current `connectContinuation`. More seriously, an old TCP receive completion can append bytes to the new global `outbox`, causing commands from the previous daemon to be written to the new BLE device.

   Fix: capture and validate `attemptGen` plus listener identity before calling `onReady`. In the receive task, require `conn === tcpConnection` before appending, draining, or rearming.

3. HIGH - `bleConnecting` does not prevent device operations or Settings reconnect  
   [AppModel.swift:242](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:242), [AppModel.swift:336](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:336), [AppModel.swift:1290](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:1290)

   While BLE is connecting, the Settings reconnect button can call `connect()` because its guard ignores `bleConnecting`. Reader operations and firmware methods also ignore it. During this window, `deviceOverBLE` still describes the outgoing device, so firmware flashing can begin on an old USB Chameleon. When BLE finishes, `swapDevice` proceeds despite the newly-started operation.

   Fix: include `!bleConnecting` in every device-operation entry guard, including `connect`, `rescan`, `connectManual`, `checkFirmware`, and `flashFirmware`. Disable Settings reconnect during `connecting`, and defensively recheck ownership before the post-connect swap.

Fix status:

1. Incomplete because of the transition window above. The daemon's immediate TCP rejection and established-BLE UI/method gates are correct. `dfu_check` remains usable over TCP at the daemon level.

2. Incomplete. Continuations are protected from double-resume, but same-identifier and queued bridge callbacks are not attempt-scoped.

3. The within-link drain logic is correct. It will not stall merely because a ready callback is absent: every receive calls `drainOutbox`, and CoreBluetooth calls the ready delegate after backpressure. `maxWriteLen` is captured correctly. Cross-link stale receives remain defective.

4. Incomplete due to unguarded operation and Settings paths. The `defer` clears `bleConnecting` correctly, and `swapDevice` itself does not deadlock.

5. Correct. A silent handshake acknowledgement times out cleanly after 15 seconds, and `awaitingHandshakeAck` is cleared on success and teardown paths.

6. Correct. EOF and shutdown changes are socket-only; serial behavior is unchanged. Both targeted Python tests pass.

7. Correct for teardown retention: handlers use weak manager captures and are cleared before cancellation.

The full Python suite could not run because the read-only environment provides no writable temporary directory. The two new targeted daemon tests passed.
tokens used
104,032
Three real defects remain.

1. HIGH - attempt generation does not actually scope CoreBluetooth callbacks  
   [BLEManager.swift:171](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:171)

   `attemptGen` only protects the timeout. `isActive` compares identifiers, so a late disconnect, failure, handshake acknowledgement, or notification from an earlier connection to the same peripheral is accepted by the new attempt. It can fail or prematurely advance the current continuation.

   Fix: serialize reconnecting the same peripheral until cancellation completes, or attach a per-attempt token through delegate proxies/state and validate it in every callback. Identifier equality is insufficient.

2. HIGH - queued old bridge callbacks can affect a new link  
   [BLEManager.swift:256](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:256), [BLEManager.swift:303](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/BLEManager.swift:303)

   Clearing handlers does not cancel callbacks or `Task` blocks already queued. An old listener callback can resolve or fail the current `connectContinuation`. More seriously, an old TCP receive completion can append bytes to the new global `outbox`, causing commands from the previous daemon to be written to the new BLE device.

   Fix: capture and validate `attemptGen` plus listener identity before calling `onReady`. In the receive task, require `conn === tcpConnection` before appending, draining, or rearming.

3. HIGH - `bleConnecting` does not prevent device operations or Settings reconnect  
   [AppModel.swift:242](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:242), [AppModel.swift:336](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:336), [AppModel.swift:1290](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:1290)

   While BLE is connecting, the Settings reconnect button can call `connect()` because its guard ignores `bleConnecting`. Reader operations and firmware methods also ignore it. During this window, `deviceOverBLE` still describes the outgoing device, so firmware flashing can begin on an old USB Chameleon. When BLE finishes, `swapDevice` proceeds despite the newly-started operation.

   Fix: include `!bleConnecting` in every device-operation entry guard, including `connect`, `rescan`, `connectManual`, `checkFirmware`, and `flashFirmware`. Disable Settings reconnect during `connecting`, and defensively recheck ownership before the post-connect swap.

Fix status:

1. Incomplete because of the transition window above. The daemon's immediate TCP rejection and established-BLE UI/method gates are correct. `dfu_check` remains usable over TCP at the daemon level.

2. Incomplete. Continuations are protected from double-resume, but same-identifier and queued bridge callbacks are not attempt-scoped.

3. The within-link drain logic is correct. It will not stall merely because a ready callback is absent: every receive calls `drainOutbox`, and CoreBluetooth calls the ready delegate after backpressure. `maxWriteLen` is captured correctly. Cross-link stale receives remain defective.

4. Incomplete due to unguarded operation and Settings paths. The `defer` clears `bleConnecting` correctly, and `swapDevice` itself does not deadlock.

5. Correct. A silent handshake acknowledgement times out cleanly after 15 seconds, and `awaitingHandshakeAck` is cleared on success and teardown paths.

6. Correct. EOF and shutdown changes are socket-only; serial behavior is unchanged. Both targeted Python tests pass.

7. Correct for teardown retention: handlers use weak manager captures and are cleared before cancellation.

The full Python suite could not run because the read-only environment provides no writable temporary directory. The two new targeted daemon tests passed.
