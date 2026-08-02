# AUDIT_CHAMELEON.md - parity-grade behavioral audit

Nguon: ChameleonUltraGUI (Flutter) + CLI Python chinh chu, mo boi 5-agent deep-audit workflow 2026-07-20. Day la ground truth de dung tenor/rekey ho tro Chameleon Ultra. Doc kem SPEC_CHAMELEON.md.

## Muc luc
- [GUI - Core Pages (Home/Connect, Read Card, Slot Manager, Saved Cards, Write Card)](#)
- [GUI - Tools, Settings, Dialogs, Editors, DFU Flashing](#)
- [GUI - Recovery / Write / Emulate Internals](#)
- [CLI - Complete Command Tree](#)
- [Data Formats + External Attack Binaries](#)

---


# GUI - Core Pages (Home/Connect, Read Card, Slot Manager, Saved Cards, Write Card)

I now have comprehensive coverage of all 5 pages plus supporting components. Here is the parity-grade reference.

---

# ChameleonUltraGUI - Core Pages UX Behavior Contract (Native Port Reference)

Root reviewed: `/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib`. All line cites are 1-indexed against the files named. This document is the build spec; do not summarize away detail.

## 0. Global architecture & shared state (`main.dart`)

**App-level state object `ChameleonGUIState extends ChangeNotifier`** (`main.dart:65-138`). This is the single provider every page reads via `context.watch/read<ChameleonGUIState>()`. Fields the pages depend on:

| Field | Type | Meaning |
|---|---|---|
| `connector` | `AbstractSerial?` | The transport. Has `.connected`, `.pendingConnection`, `.isDFU`, `.device` (`ChameleonDevice.ultra`/`.lite`), `.connectionType` (`ConnectionType.ble`/`.usb`), `.portName`, `.activeDevicePort`, `.isManualConnectionSupported()` |
| `communicator` | `ChameleonCommunicator?` | The command layer (all `.getSlotTagTypes()` etc calls). Non-null only while connected |
| `sharedPreferencesProvider` | `SharedPreferencesProvider` | Persistent store: cards, folders, dictionaries, theme, flags |
| `devMode` | `bool` | Shows Debug rail item |
| `progress` | `double?` | DFU flashing progress (0..1), drives bottom progress bar |
| `log` | `Logger?` | logger |

**Key methods:** `changesMade()` → just `notifyListeners()` (`:87`); `disconnect({bool manual=false})` (`:123`) calls `connector.performDisconnect()`, nulls `communicator`+`progress`, notifies; when `manual`, records the port into `_suppressedAutoReconnectPort` so auto-reconnect skips it. `onConnectorStateChanged()` (`:91`) nulls communicator/progress if disconnected then notifies - wired as `connector.connectionStateCallback` (`:209`). Auto-reconnect suppression helpers: `isAutoReconnectSuppressed`, `clearAutoReconnectSuppression`, `syncAutoReconnectSuppression` (`:99-121`).

**Navigation model** - `_MainPageState.selectedIndex` (`main.dart:150`), a `NavigationRail` (`:329-387`). Destinations, in order, with disabled rules:

| idx | Icon | Label key | Disabled when | Page widget |
|---|---|---|---|---|
| 0 | `Icons.home` | `home` | never | Connect / Pending / Home / Flashing (see below) |
| 1 | `Icons.widgets` | `slot_manager` | `!connected` | `SlotManagerPage` |
| 2 | `Icons.auto_awesome_motion` | `saved_cards` | never | `SavedCardsPage` |
| 3 | `Icons.sensors` | `read_card` | `!connected` | `ReadCardPage` |
| 4 | `Icons.system_update_alt` | `write_card` | `!connected` | `WriteCardPage` |
| 5 | `Icons.handyman` | `tools` | never | `ToolsPage` |
| 6 | `Icons.settings` | `settings` | never | `SettingsMainPage` |
| 7 | `Icons.bug_report` | `🐞 debug 🐞` | shown only if `devMode` | `DebugPage` |

**Guard (`main.dart:224-232`):** if `!connected` AND `selectedIndex` is not one of {0,2,5,6,7}, force `selectedIndex=0`. So disconnecting while on Slot/Read/Write kicks you to Home tab. **Index 0 sub-routing (`:236-250`):** if `pendingConnection` → `PendingConnectionPage`; else if `connected` && `isDFU` → `FlashingPage`; else if `connected` → `HomePage`; else → `ConnectPage`.

**Rail visibility:** hidden entirely when in DFU+connected (`main.dart:327`, shows `SizedBox`). Rail `extended` bound to `getSideBarExpanded()`. Auto-expansion (`:212-219`): if `getSideBarAutoExpansion()`, expand when window width ≥600 else collapse. `bottomNavigationBar` = `BottomProgressBar` (`:405-419`): a `LinearProgressIndicator(value: progress)` (grey bg, blue value) shown only when `connected && isDFU`, else `SizedBox`.

**Theme:** Material3, `ColorScheme.fromSeed(getThemeColor())`, light+dark variants, `themeMode` from `getTheme()`. `reassemble()` disconnects on hot-reload (`:160`). `WakelockPlus.toggle(enable: page is FlashingPage)` (`:277`).

**Native-port note:** the "page area" is `Expanded > Container(color: primaryContainer) > page`. Wrap the whole thing in `SafeArea(bottom:true)`.

---

## 1. HOME / CONNECT cluster (tab index 0)

Four mutually-exclusive screens share tab 0. Selection logic above.

### 1A. ConnectPage (`connect.dart`, 480 L)

Shown when not connected, not pending. Stateful; constructor param `autoScanInterval` default `Duration(seconds:3)` (`:20`).

**State fields (`:30-38`):** `_devices` (List<Chameleon>), `_scanTimer`, `_error`, `_isLoading`(init true), `_initialScanCompleted`, `_scanInProgress`, `_connectionInProgress`, `_showedPermissionsSnackbar`, `_lastAutoConnectAttemptPort`.

**Displays:**
- AppBar title `connect` (`:437`).
- Top-right `IconButton(Icons.refresh)` → `_scanNow(manual:true)` (`:445-448`).
- Body center: if `_isLoading && !_initialScanCompleted` → `CircularProgressIndicator` (`:452`); else `_buildDeviceGrid` - a 2-column `GridView`, childAspectRatio 1, spacing 10, padding 20 (`:341-419`).
- Each device tile = `ElevatedButton` (rounded 18) containing: top row with `Icons.bluetooth` (if `type==ble`) else `Icons.usb`, the `port` string, and `dfu` label if in DFU; bold "Chameleon {deviceName}" (fontSize 20); device image (`assets/black-ultra-standing-front.webp` for ultra else `black-lite-standing-front.webp`, `errorBuilder`→empty).
- Bottom-right `IconButton(Icons.add)` shown only if `connector.isManualConnectionSupported()` → opens `ManualConnect` dialog (`:455-474`).
- If `_error != null`: whole body replaced by `ErrorPage(errorMessage)` (`:426-433`).

**Scanning flow `_scanNow({manual})` (`:135-194`):**
1. `initState` fires `_scanNow()` post-frame (`:46`).
2. Guard: bail if `_scanInProgress` or `!_shouldScan` (`_shouldScan` = mounted && !connectionInProgress && !connected && !pendingConnection, `:55-60`).
3. cancel timer; setState `_scanInProgress=true`, clear error, set `_isLoading=true` only if first scan or manual.
4. `devices = _normalizeDevices(await connector.availableChameleons(false))` - dedup by `port|type|dfu` (`:62-74`).
5. `syncAutoReconnectSuppression(ports)`; reset `_lastAutoConnectAttemptPort` if first-connectable-port changed.
6. setState devices, `_isLoading=false`, `_initialScanCompleted=true`.
7. `_showPermissionsWarningIfNeeded` then `_maybeAutoConnect`.
8. On error: `connector.performDisconnect()`, setState `_error`.
9. `finally`: setState `_scanInProgress=false`, `_scheduleNextScan()`.

**`_scheduleNextScan` (`:85-94`):** re-arms `Timer(autoScanInterval, _scanNow)` only if `_shouldScan` && `getAutoScanEnabled()`.

**Android permissions warning (`:96-133`):** only for `AndroidSerial`. If `devices.isEmpty && !hasAllPermissions` and not already shown, show SnackBar `android_ble_permissions_missing` with Close action. Resets flag otherwise.

**Auto-connect `_maybeAutoConnect` (`:196-222`):** only if `_shouldScan` && `getAutoConnectFirstFoundDevice()`. Pick first non-dfu, non-suppressed device; if none, clear attempt port; skip if already attempted that port; else record port and `_connectToDevice(device, fromAutoConnect:true)`.

**Connect `_connectToDevice(device, {fromAutoConnect}) (`:224-285`):**
1. Bail if `_connectionInProgress`.
2. If `device.dfu`: if not auto → `_showDfuDialog`; return (never auto-connects DFU device).
3. Cancel timer; setState `_connectionInProgress=true`.
4. If `type==ble`: set `pendingConnection=true` + `changesMade()` (this flips tab 0 to PendingConnectionPage).
5. `connected = await connector.connectSpecificDevice(device.port)`.
6. If connected: `pendingConnection=false`, clear suppression, **construct** `appState.communicator = ChameleonCommunicator(log, port: connector)` (`:259`). Else `pendingConnection=false`.
7. `changesMade()`.
8. catch → `pendingConnection=false`, changesMade, setState `_error`.
9. finally → `_connectionInProgress=false`; if still not connected, `_scheduleNextScan()`.

**DFU dialog `_showDfuDialog` (`:287-339`):** AlertDialog title `chameleon_is_dfu`, content `firmware_is_corrupted`, actions Cancel / Flash. Flash → pop, changesMade, SnackBar `downloading_fw(deviceName)` (Close hides), `flashFirmware(appState, device, enterDFU:false)`, changesMade, `_scanNow()`.

**Empty state:** grid renders empty (no devices) - no explicit "no devices" text; the Android permission SnackBar is the only empty hint.

### 1B. PendingConnectionPage (`pending_connection.dart`, 47 L)

Pure display. AppBar `connect`. Center column: `CircularProgressIndicator`, spacer 25, `connecting_to_ble`, then - only while `!connector.connected` - bold lines `default_ble_password`, `connection_might_take_some_time`, `ble_need_to_remove_pair`. No controls. Exits when `_connectToDevice` sets `pendingConnection=false` (routing then re-evaluates to Home or back to Connect).

### 1C. HomePage (`home.dart`, 584 L)

Shown when connected, non-DFU. Stateful. State: `selectedSlot=1`, `isLegacyFirmware=false`.

**Data load - `getFutureData()` (`:33-50`)** returns a 5-tuple, driven by a `FutureBuilder` (`:212`):
1. `getBatteryInfo()` → `(Icon, BatteryCharge)`.
2. `getUsedSlotsOut8(slotTypes)` where `slotTypes = communicator.getSlotTagTypes()`.
3. `getVersion()` → `[displayString, commitHash]`.
4. `isReaderDeviceMode()` → bool.
5. `areCapabilitiesSupported()` → bool.

**FutureBuilder states (`:214-231`):** waiting → Scaffold(AppBar `home`) + centered `CircularProgressIndicator`. hasError → `appState.disconnect()` + `ErrorPage`. Else render body.

**Battery (`:80-110`):** `communicator.getBatteryCharge()`; icon chosen by percent thresholds: >98 full, >87 `battery_6_bar`, >75 `_5_bar`, >62 `_4_bar`, >50 `_3_bar`, >37 `_2_bar`, >10 `_1_bar`, >3 `_0_bar`, >0 `battery_alert`, else `battery_unknown`. Rendered as `Tooltip(battery_info(percent, voltage))` wrapping the icon (`:287-292`).

**Used slots (`:112-125`):** if slotTypes empty → localized `unknown`; else count `i in 0..7 where slotTypes[i].notMatch()` (note `notMatch()` = "hf≠unknown OR lf≠unknown", `definitions.dart:408`). Displayed as `used_slots: {n}/8` (`:314`).

**Version (`:127-200`):** `getFirmwareVersion()` → sets `isLegacyFirmware`, formats `numToVerCode`. `getGitCommitHash()` (catch→empty→`outdated_fw`). If `isLegacyFirmware` → immediately shows **non-dismissible** AlertDialog `outdated_protocol` (3 description lines) with Update / Skip. Update → pop, SnackBar `downloading_fw`, `flashFirmware`. Returns `["{ver} ({hash})", hash]`.

**Body layout (`:244-579`):**
- Top-right column: `IconButton(Icons.close)` → `disconnect(manual:true)` (`:257-263`); then a row: ellipsized `connector.portName` (fontSize 20), a `Icons.bluetooth`/`Icons.usb` icon by connectionType, the battery tooltip icon.
- Center: bold "Chameleon {deviceName}" (responsive fontSize = min(w/25, h/20)).
- `used_slots: {n}/8` (fontSize min(w/35,h/20)).
- **`SlotChanger` widget** (see 1D) inside a FittedBox.
- Device image, 40% width (`ultra`/`lite` webp).
- **Firmware row** - only if `portName != "Demo"` (`:337-450`): bold `firmware_version: ` + `fwVersion[0]` + `IconButton(Icons.update)` tooltip `check_updates`. Update button flow (`:359-444`): `latestAvailableCommit(device)` (catch→SnackBar `update_error`); `resolveCommit(fwVersion[1])`; if `latestCommit.startsWith(current)` → SnackBar `up_to_date`; else SnackBar `downloading_fw` + `flashFirmware` (catch→`update_error`).
- **Demo banner** - only if `portName == "Demo"` (`:451-478`): rounded container (complementary color), `Icons.error_outline` + `demo_firmware`.
- **Capabilities banner** - if `!areCapabilitiesSupported && portName != "Demo"` (`:479-531`): `error_outline` + `please_update_firmware` + `Update` TextButton → SnackBar `downloading_fw` + `flashFirmware`.
- **Bottom-right controls (`:532-576`):**
  - Reader/emulator toggle: if `isReaderDeviceMode` → `IconButton(Icons.nfc_sharp)` tooltip `emulator_mode` → `setReaderDeviceMode(false)` + setState + changesMade; else `IconButton(Icons.barcode_reader)` tooltip `reader_mode` → `setReaderDeviceMode(true)`.
  - `IconButton(Icons.settings)` → `ChameleonSettings` dialog (`:564-573`).

**`areCapabilitiesSupported()` (`:52-78`):** reads `getDeviceCapabilities()`. For ultra AND lite, required capability is `ChameleonCommand.setIdteckEmulatorID.value`. If device is ultra/lite and capability missing → false; any exception → false.

### 1D. SlotChanger (`component/slot_changer.dart`, 134 L)

Embedded in Home. Stateful, `selectedSlot=1`. `FutureBuilder(getFutureData)`:
- `getFutureData` → `getSlotIcons(usedSlots=getSlotTagTypes())` (catch→[]).
- `getSlotIcons` (`:35-62`): `selectedSlot = getActiveSlot()+1` (catch→1). If usedSlots empty → single `Icons.warning`. Else 8 icons: index==selectedSlot-1 → `Icons.circle_outlined` **red** (the active slot); else if `usedSlots[i].notMatch()` → filled `Icons.circle`; else `Icons.circle_outlined`.
- **Waiting state:** row of `arrow_back` + 8 placeholder `circle_outlined` (`presold`) + `arrow_forward`, arrows no-op.
- **Loaded:** `IconButton(Icons.arrow_back)` → if `selectedSlot>1`: `activateSlot(selectedSlot-2)` + setState + changesMade. 8 icons. `IconButton(Icons.arrow_forward)` → if `selectedSlot<8`: `activateSlot(selectedSlot)` + setState + changesMade.
- hasError → `performDisconnect()` + `ErrorPage`.

**Cross-page effect:** changing active slot here re-triggers Home's FutureBuilder via changesMade → `getActiveSlot` reflects new slot.

---

## 2. READ CARD (`read_card.dart`, 998 L) - tab index 3

Two stacked `Card` sections: **HF Tag Info** (top) and **LF Tag Info** (bottom), inside a `SingleChildScrollView`. AppBar `read_card`.

### 2.1 State & models

State (`:92-102`): `dumpName`, `hfInfo:HFCardInfo`, `lfInfo:LFCardInfo`, `mfcInfo:MifareClassicInfo`, `mfuInfo:MifareUltralightInfo`, `isContinuousHFScan`, `isContinuousLFScan`, `scanInProgress`, `hfScanTimer`, `lfScanTimer`.

- `HFCardInfo` (`:33-50`): `uid, sak, atqa, tech, ats` (strings), `type:TagType`(default unknown), `cardExist`(default true).
- `LFCardInfo` (`:52-57`): `card:LFCard?`, `cardExist`(true).
- `MifareClassicInfo` (`:59-75`): `isEV1`, `recovery:MifareClassicRecovery?`, `type:MifareClassicType`, `state:MifareClassicState`, `ntLevel:NTLevel?`, `hasBackdoor:bool?`.
- `MifareClassicState` enum (`:21-30`): none, checkKeys, checkKeysOngoing, recovery, recoveryOngoing, dump, dumpOngoing, save.
- `MifareUltralightInfo` (`:77-82`): `version, signature` (Uint8List?).

### 2.2 HF Tag Info card (`:320-725`)

**Displayed fields** (each `buildFieldRow(label, value, fontSize)` = bold "`label: value`", `:287-301`; fontSize 16 small / 20 wide, breakpoint width<800):
- Header `hf_tag_info` (bold 20).
- `uid`, `sak`, `atqa`, `ats`.
- Row `card_tech: {hfInfo.tech}`. If `uid` non-empty, an inline `IconButton(Icons.edit)` tooltip `override_card_type` (see 2.4).
- If `isMifareClassic(hfInfo.type)` (`:457-472`): if `mfcInfo.ntLevel != null` → `prng_type: {mfClassicGetPrngType(...)}`; if `hasBackdoor != null` → `has_backdoor_support: yes/no`.

**Controls - Read / Continuous Scan (`:473-671`):** layout is a `Column` when small screen (full-width stacked buttons) else a `Row` of two `Expanded`. Both use `customCardButtonStyle`.
- **Read button** (`read`): disabled when `scanInProgress` (small-screen variant only sets scanInProgress). Behavior branches on `connector.device`:
  - `ultra` → (small screen: setState scanInProgress=true) `info = await readHFInfo(context, updateMifareClassicRecovery)`; setState hfInfo=info.$1, mfcInfo=info.$2, mfuInfo=info.$3, scanInProgress=false.
  - `lite` → AlertDialog `no_supported` / bold `lite_no_read` / OK. (Lite cannot read.)
  - else → `changesMade()`.
- **Continuous Scan button:** label `continuous_scan`, becomes `cancel` while active. If active → `stopContinuousHFScan()`. Else same ultra/lite branch; ultra → `startContinuousHFScan()`.

**`readHFInfo` (helpers/general.dart:602-652):**
1. Ensure reader mode (`isReaderDeviceMode` else `setReaderDeviceMode(true)`).
2. `card = scan14443aTag()`. If null → `hfInfo.cardExist=false`, return.
3. If `!detectMf1Support()` → `performMifareUltralightScan` (ultralight path). Else → `performMifareClassicScan`.
4. Fill `hfInfo`: uid=`bytesToHexSpace(card.uid)`, sak=2-hex upper, atqa=hexSpace, ats=hexSpace or localized `no`, type. Set `mfcInfo.state = checkKeys if type!=none else none`. `tech = chameleonTagToString(type) + (isEV1?" EV1":"")`.
5. catch → log + `cardExist=false`.

**`performMifareClassicScan` (mifare_classic/general.dart:592-633):** resolves `MifareClassicType` (or from `override`); tests EV1 via `mf1Auth(0x45,0x61,keys[3])`; builds `MifareClassicRecovery`; reads `getMf1NTLevel()` (catch ignore), `mfClassicHasBackdoor()`; populates mfcInfo (recovery, ntLevel, hasBackdoor, isEV1, type). Returns chameleon TagType.

**`performMifareUltralightScan` (mifare_ultralight/general.dart:235-257):** `mfUltralightGetVersion`; type = override or `mfUltralightGetType(version)`; fallback `mfUltralightType`; if version len 8, read signature; store version+signature in mfuInfo.

**Continuous HF scan (`:147-200`):** guard if already scanning. Interval 2 s, max duration 1 min. `Timer.periodic`: on each tick, if elapsed>1min or unmounted → stop; else `readHFInfo`, setState; if a card with non-empty uid found → stop. Also runs one immediate scan. `stopContinuousHFScan` cancels timer, setState flag false. `dispose()` stops both HF+LF (`:246-251`).

**Empty/error states:** if `!hfInfo.cardExist` → `ErrorMessage(no_card_found)` (`:672-675`). 

**Save-UID button (`:676-717`):** shown if `hfInfo.uid != ""`. Label `save_only_uid`. Opens AlertDialog `enter_name_of_card` with a `TextField` (onChanged→`dumpName`), actions OK (`saveHFCard()` then pop) / Cancel (pop).
- `saveHFCard()` (`:253-276`): appends `CardSave(uid, sak=hexToBytes(sak)[0], atqa, name=dumpName, tag = type!=unknown?type:mifare1K, data:[], ats = ats!=no ? bytes : empty, extraData(ultralightSignature/version from mfuInfo, counters:[]))`. Persists via `setCards`.

**Type-specific helper widgets:** if `isMifareClassic(type)` → `MifareClassicHelper(mfcInfo, hfInfo)` (`:718`); if `isMifareUltralight(type)` → `MifareUltralightHelper(hfInfo)` (`:720`). Detailed in 2.6/2.7.

### 2.3 LF Tag Info card (`:727-992`)

**Displayed:** header `lf_tag_info`; `uid: {card.toViewableString() or ''}`; `card_tech: {chameleonTagToString(card.type) or ''}`.

**Controls (same small/wide dual layout):** Read + Continuous Scan.
- Read → ultra: (small: scanInProgress=true) `readLFInfo()`; lite: `no_supported`/`lite_no_read`; else changesMade.
- Continuous → toggles `startContinuousLFScan`/`stopContinuousLFScan`.

**`readLFInfo()` (`:116-145`):** setState reset lfInfo; ensure reader mode; try in order: `readEM410X()` → `readHIDProx()` → `readViking()` → `readPac()` → `readIoProx()` (first non-null wins, `:127-131`). If found → setState card + scanInProgress=false; else `cardExist=false`.

**Continuous LF (`:202-244`):** identical structure to HF (2 s / 1 min), stops on found card (`lfInfo.card != null`).

**Empty state:** `!lfInfo.cardExist` → `ErrorMessage(no_card_found)` (`:942-945`).

**Save button (`:946-987`):** shown if `lfInfo.card != null`. Label `save`. Same name dialog → `saveLFCard()` (`:278-285`): appends `CardSave(uid: card.toString(), name: dumpName, tag: card.type)`.

### 2.4 Override card type dialog (`:356-452`)

Triggered by the edit icon on HF card_tech. AlertDialog `override_card_type`, description `override_card_type_description`, a full-width `DropdownButton<TagType?>` value=`hfInfo.type`, items = `getTagTypesByFrequency(hf)` + `TagType.unknown`. onChanged (`:377-413`): setState type+tech; if `isMifareClassic(newValue)` → `performMifareClassicScan(override:newValue)` then setState mfcInfo; else if `isMifareUltralight` → `performMifareUltralightScan(override)` then setState mfuInfo; pop. Cancel action closes.

### 2.5 MifareClassicHelper (`component/mifare/classic.dart`, 467 L)

Drives the key-recovery/dump state machine off `mfcInfo.recovery` + `mfcInfo.state`. Params: `hfInfo`, `mfcInfo`, `allowSave`(default true).

**On build (`:96-108`):** loads `recovery.dictionaries = getDictionaries(keyLength:12)`, inserts an `empty` dictionary at index 0, defaults `selectedDictionary`. Toggles `WakelockPlus` on during any `*Ongoing` state.

**Displays (`:110-162`):**
- Header `keys` (bold 24).
- `KeyCheckMarks` grid: `checkMarks`, `validKeys`, count=`mfClassicGetSectorCount(type, isEV1)`, per-row 8 (<600px) else 16; user can tap a checkmark to toggle (`onCheckmarkChanged` writes `checkMarks[index]` and calls `recovery.update()`).
- If `recovery.error != ""` → `ErrorMessage`.
- If `recovery.state != ""` → `Text(state)`.
- Progress bars (each shown conditionally): `dumpProgress` (if ≠0), `hardnestedProgress` (if !=null && error==""), `keyCheckProgress` (if !=null).

**Recovery model** (`helpers/mifare_classic/recovery.dart:29-42`): fields `error, state` (Strings), `allKeysExists`, `dictionaries`, `selectedDictionary`, `checkMarks:List<ChameleonKeyCheckmark>` (80), `validKeys:List<Uint8List>` (80), `dumpProgress:double`, `hardnestedProgress:double?`, `keyCheckProgress:double?`. Public ops: `checkKeys({skipDefaultDictionary})` (`:181`), `recoverKeys()` (`:220`), `dumpData()` (`:571`), plus `getKey/setKey` (`:689/708`).

**State-machine buttons:**

- **checkKeys / checkKeysOngoing** (`:236-329`): when `checkKeys`, shows a `CheckboxListTile skip_default_dictionary` (→ `skipDefaultDictionary`), label `additional_key_dict`, a `DropdownButton<String>` of dictionaries ("{name} ({n} keys)") setting `selectedDictionary`. Button `check_keys_dict`: setState `checkKeysOngoing`; `recovery.checkKeys(skipDefaultDictionary)`; if `allKeysExists` → state `dump` else `recovery`. catch → reset any `checking` checkmarks to `none`, set `error=recovery_error_dict`, state back to `checkKeys`.
- **recovery / recoveryOngoing** (`:163-235`): `_ResponsiveButtonGroup` with:
  - `recover_keys`: setState `recoveryOngoing`; `recovery.recoverKeys()`; if error → back to `recovery` else → `dump`.
  - (if allowSave) `dump_partial_data`: setState `dumpOngoing`; `recovery.dumpData()`; success → dumpProgress=0, state `save`; catch → error `recovery_error_dump_data`, state `dump`.
  - `export_to_dictionary`: `exportFoundKeys()` → `DictionaryExportMenu(keys: validKeys)`.
- **dump / dumpOngoing** (if allowSave, `:330-369`): `dump_card` (same as dump_partial_data flow) + `export_to_dictionary`.
- **save** (if allowSave, `:371-422`): `save` → name dialog → `saveCard()`; `save_as(".bin")` → `saveCard(bin:true)`.

**`saveCard({bin, skipDump})` (`:50-83`):** builds `cardDump = mfClassicGetExportBytes(type, recovery.cardData, isEV1)` unless skipDump. If bin → `FilePicker.saveFile({uid}.bin)`. Else append `CardSave(uid, sak, atqa, name=dumpName, tag = skipDump?mifare1K:mfClassicGetChameleonTagType(type), data=recovery.cardData, ats)`.

`_ResponsiveButtonGroup` (`:427-467`): <800px → stretched Column dropping SizedBox spacers; else Row (FittedBox scaleDown, or Center if `centerOnly`).

### 2.6 MifareUltralightHelper (`component/mifare/ultralight.dart`, 272 L)

`MifareUltralightState { none, read, save }`. Params `hfInfo`, `allowSave`(true). State: `keyController`, `state`, `cardData`, `version, signature` (strings), `counters`, `dumpName`, `error`, `progress`.

**none state (`:175-206`):** `Form` with `TextFormField` (label `key`, hint `ultralight_key_prompt`, `hexFormatter`, `validateHex(exactBytes:4)`), then two `TextButton`s: `read_with_key` → `readCard(withPassword:true)`; `read_without_key` → `readCard(withPassword:false)`.

**`readCard({withPassword})` (`:43-123`):** setState reset, state=read. Loop pages `0..mfUltralightGetPagesCount(type)`:
- If withPassword: `send14ARaw([0x1B,...key], keepRfField:true)` → `pack`; if `pack.length<2` → state none, error `invalid_password`, return.
- `send14ARaw([0x30, page])` → append first 4 bytes (or empty).
- setState progress = page/count.
- After loop: if no valid data → progress 0, error `failed_to_read_block`, state none. Else read version+signature (hexSpace); if `mfUltralightHasCounters` → read counters. If a password page exists && withPassword → store key+pack into cardData. setState state=save.

**error != ""** → `ErrorMessage`. **read state** → `LinearProgressIndicator(progress)`. **save state (`:215-268`):** `save` (name dialog → `saveCard()`) + `save_as(".bin")` (`saveCard(bin:true)`).

**`saveCard({bin})` (`:125-165`):** assembles cardDump from cardData (empty page → 4 zero bytes). bin → FilePicker `{uid}.bin`. Else `CardSave(uid, sak, atqa, name, tag=type, data=cardData, extraData(signature, version, counters), ats)`.

---

## 3. SLOT MANAGER (`slot_manager.dart`, 482 L) - tab index 1

The 8-slot device-writing page. AppBar `slot_manager`.

### 3.1 State & load

State (`:28-45`): `usedSlots:List<SlotTypes>(8)`, `enabledSlots:List<EnabledSlotInfo>(8)`, `slotData:List<SlotNames>(8)`, `progress`(-1), `gridPosition`(0), `onlyOneSlot`(false).

**`loadSlotData()` (`:47-63`)** - the FutureBuilder future: if `progress != -1` returns early (don't reload mid-upload). Reads `getSlotTagTypes()`, `getEnabledSlots()`, `getSlotTagNames()`. Blank names → localized `no_name`.

**FutureBuilder (`:348-464`):** waiting OR `progress!=-1` → `CircularProgressIndicator`. hasError → `performDisconnect()` + `ErrorPage`. Else → 8-item grid.

### 3.2 The 8 slot tiles

`AlignedGridView.count`, crossAxisCount 2 if width≥700 else 1, spacing 10, 8 items (`:359-461`). Each tile = `ElevatedButton` (rounded 18), constraints maxHeight 160/minHeight 100. **Tapping the tile:** setState `gridPosition=index`, then `cardSelectDialog(context)` (opens the card picker - see 3.3).

**Tile content (`:388-457`):**
- Row: `Icons.nfc` colored **green** if `enabledSlots[index].any()` else **deepOrange**; "Slot {index+1}".
- `Icons.credit_card` + "`{slotData[index].hf} ({chameleonTagToString(usedSlots[index].hf)})`" (HF line).
- Row: `Icons.wifi` + "`{slotData[index].lf} ({chameleonTagToString(usedSlots[index].lf)})`" (LF line) + `IconButton(Icons.settings)` → `SlotSettings(slot:index, refresh:refreshSlot)` dialog (see 3.5).

**Bottom (`:466-476`):** if `progress != -1`: `uploading_dump` text + `LinearProgressIndicator(progress/100)`.

### 3.3 Card picker → write-to-slot

`cardSelectDialog` (`:318-333`): if `progress != -1` returns `""` (block concurrent uploads). Sorts cards by name; `showSearch(CardSearchDelegate(cards, onTap))`. The delegate (`component/card_list.dart`) lists cards filterable by All/HF/LF; tapping a suggestion calls `onTap(card, close, localizations)`.

**`onTap(card, close, localizations)` (`:81-316`)** - the core write engine. Branches by `card.tag`:

**Mifare Classic (`:85-151`):** `close(context, name)`; `setUploadState(0)`; if EV1 (`chameleonTagSaveCheckForMifareClassicEV1`) bump tag to mifare2K. Then sequence:
1. `setReaderDeviceMode(false)`
2. `enableSlot(gridPosition, hf, true)`
3. `activateSlot(gridPosition)`
4. `setSlotType(gridPosition, tag)`
5. `setDefaultDataToSlot(gridPosition, tag)`
6. `setMf1AntiCollision(CardData(uid, atqa, sak, ats))`
7. Block-upload loop (`:106-142`): accumulate `blockChunk`; flush via `setMf1BlockData(lastSend, chunk)` when next block empty or chunk≥128 bytes; update `setUploadState(round(blockOffset/count*100))`; `asyncSleep(1)` each block. Final flush.
8. `setUploadState(100)`
9. `setSlotTagName(gridPosition, name or no_name, hf)`
10. `saveSlotData()`; `changesMade()`; `refreshSlot()`.

**EM410X (`:152-171`):** close; setReaderDeviceMode(false); enableSlot **lf**; activateSlot; tag = `em410XElectra` if that variant else `em410X`; setSlotType; setDefaultDataToSlot; `setEM410XEmulatorID(hexToBytes(uid))`; setSlotTagName lf; saveSlotData; refresh. (No progress bar - LF is instant.)

**HID Prox (`:172-188`):** as LF above but `setHIDProxEmulatorID(hexToBytes(HIDCard.fromUID(uid).toString()))`.
**Viking (`:189-204`):** `setVikingEmulatorID`. **PAC (`:205-220`):** `setPacEmulatorID`. **IO Prox (`:221-236`):** `setIoProxEmulatorID`. **Idteck (`:237-252`):** `setIdteckEmulatorID`. All identical LF skeleton.

**Mifare Ultralight (`:253-311`):** close; setUploadState(0); setReaderDeviceMode(false); enableSlot hf; activateSlot; setSlotType; setDefaultDataToSlot; setMf1AntiCollision. Page loop `mf0EmulatorWritePages(page, data)` with `setUploadState(page/count*100)` + `asyncSleep(1)`. Then if present: `mf0EmulatorSetVersionData`, `mf0EmulatorSetSignatureData`, per-counter `mf0EmulatorSetCounterData(i, val, true)`; if `mfUltralightHasCounters` → `mf0ResetAuthCount`. setUploadState(100); setSlotTagName hf; saveSlotData; refresh.

**Unsupported (`:312-315`):** log error "Can't write this card type yet." + close.

`setUploadState(n)` (`:72-79`) setState progress + changesMade. `refreshSlot()` (`:65-70`) = `setUploadState(-1)` + changesMade (resets progress, reloads via FutureBuilder).

### 3.4 Data models used

- `SlotTypes{hf, lf: TagType}` - `notMatch({type=unknown})` = `hf!=type || lf!=type` (`definitions.dart:400-413`).
- `EnabledSlotInfo{hf, lf: bool}` - `any()` = `hf||lf` (`:415-424`).
- `SlotNames{hf, lf: String}` (`:300-305`).

### 3.5 SlotSettings dialog (`menu/dialogs/slot/settings.dart`, 300 L)

Opened per-slot from the tile's settings icon. Params `slot:int`, `refresh` callback. State: `enabledSlot`, `slotTypes`, `names`, `exportFrequency`(hf).

**`fetchInfo()` (`:34-66`):** `activateSlot(slot)`; read HF name (`getSlotTagName(slot,hf).trim()` → `empty` if blank), LF name; `enabledSlot = getEnabledSlots()[slot]`; `slotTypes = getSlotTagTypes()[slot]`; setState.

FutureBuilder future = `names.hf.isNotEmpty ? Future.value(null) : fetchInfo()` (fetch once). waiting+empty → AlertDialog `slot_settings` + spinner. hasError → performDisconnect + ErrorPage in dialog.

**Dialog body (`:102-296`):**
- Title row: `slot_settings` + `IconButton(Icons.download)` → `SlotExportMenu(names, enabledSlotInfo, slotTypes)`; enabled only if `slotTypes.notMatch()` (i.e. slot has data).
- **HF group (`:127-210`):** row "HF:" + `IconButton(Icons.edit)` → `SlotEditMenu(name, isEnabled, slotType, frequency:hf, slot, update:updateSlot)`; `IconButton(Icons.clear_rounded)` → optional `ConfirmDeletionMenu` (if `getConfirmDelete()`), then `deleteSlotInfo(slot, hf)` + `setSlotTagName(slot, empty, hf)` + `saveSlotData`; setState clears name+type; `refresh()`; `Switch(enabledSlot.hf)` → `enableSlot(slot, hf, value)` + setState + refresh. Below: full-width disabled `OutlinedButton` showing `names.hf`.
- **LF group (`:212-295`):** identical with `lf`.

`updateSlot(name, frequency, type)` (`:68-80`): writes into `names`/`slotTypes` by frequency, calls `refresh()` + setState. `SlotEditMenu`/`SlotExportMenu` are separate dialogs (`slot/edit.dart`, `slot/export.dart`) - out of the 5-page scope but referenced here.

---

## 4. SAVED CARDS (`saved_cards.dart`, 1835 L) - tab index 2

Two stacked `Card` sections in a Column: **Cards** (top) and **Dictionaries** (bottom), each an independent folder browser. Works offline (tab always enabled). AppBar `saved_cards` with a back-arrow leading icon when inside a card folder.

### 4.1 State & filtering

State (`:39-41`): `selectedType:TagType`, `currentFolderId:String?`, `currentDictionaryFolderId:String?`.

Build reads all cards/folders/dictionaries/dictionaryFolders, then filters current level: `tags = allTags where folderId==currentFolderId`; `folders = allFolders where parentId==currentFolderId` (`:687-699`); same for dictionaries. `currentFolder`/`currentDictionaryFolder` looked up by id. `isCompact = width<700`.

**`_folderTreeIds(rootId, folders)` (`:180-194`):** transitive closure of descendants (used for cascade delete/export and card counts).

### 4.2 Cards section (`:751-1438`)

**Header (`sectionHeader`, `:705-733`):** title = `currentFolder?.name ?? cards`. In compact mode, header row also embeds the action buttons; non-compact shows a separate button bar below.
- Actions: `IconButton(Icons.file_upload)` → `importCard()`; `_createMenuButton` (a `MenuAnchor` with `Icons.add`). Menu items: `card` → `_createCard()` (`CardCreateMenu(folderId)`); `folder` → `_editFolder()`.
- Non-compact button bar (`:763-1267`): a full-width `ElevatedButton(Icons.file_upload)` (same import handler) + `_createMenuButton(elevated:true)`.

**Grid (`:1269-1436`):** `AlignedGridView.count`, cols 2 if ≥700 else 1, itemCount = `folders.length + tags.length`. Folders first, then cards.
- **Folder item** → `ElementButton(icon:folder, iconColor:folder.color, firstLine:name, secondLine:folder_card_count(cardCount from subtree), onPressed → setState currentFolderId=folder.id)`. Trailing icon buttons: `move_folder` (`_moveFolder`), `edit_folder` (`_editFolder(folder)`), `export_folder` (`_exportFolder`), `delete_folder` (`_deleteFolder`).
- **Card item** → `ElementButton(icon: credit_card if hf else wifi, iconColor:tag.color, firstLine: name or "⠀", secondLine: chameleonCardToString(tag))`. onPressed → `CardViewMenu(tagSave:tag, onMove:_moveCard)` dialog. Trailing: `move_card` (`_moveCard`); edit (`CardEditMenu`); download (AlertDialog `select_save_format` → `saveTag(tag,ctx,true)` for .bin / `false` for .json); delete (optional `ConfirmDeletionMenu` if `getConfirmDelete()`, then filter card out by id + `setCards` + changesMade).

**ElementButton** (`component/element_button.dart`, 151 L): elevated card-style button; icon + two ellipsized text lines; trailing action icons that reflow below the text (min height 90→130) when they don't fit (`_shouldMoveIcons` measures text width, `:50-82`).

### 4.3 Card import (`:775-1252`) - the multi-format importer

`FilePicker.pickFile()` → read bytes → try UTF-8 decode → try `jsonDecode`:
1. If JSON map with `format=='chameleon-ultra-gui-folder'` → `_importFolderSource(string)` (folder bundle import).
2. Else detect by content signatures (`:801-819`): `"Created": "proxmark3"` → `pm3JsonToCardSave`; `Filetype: Flipper NFC device` → `flipperNfcToCardSave`; `+Sector: 0` → `mctToCardSave` (Mifare Classic Tool); `Filetype: Flipper RFID key` → `flipperRfidToCardSave`; else `CardSave.fromJson`. Set name from filename (strip extension), folderId=current, add, `setCards`, changesMade.
3. **catch (binary dump)** (`:830-1250`): `selectedType = getTagTypeByDumpSize(length)`; if unknown → abort. For MifareClassic → derive uid4/uid7/sak/atqa from bytes; for Ultralight → uid7 + atqa 00 44. Show **"correct_tag_data"** dialog with (conditionally) 4-byte UID/SAK/ATQA fields, 7-byte UID/SAK/ATQA fields, name, and a HF `TagType` dropdown; all validated by `validateHex`/`validateName`. Actions: `save_as(x_byte_uid(4))` (if hasUid4Support) and `save_as(x_byte_uid(7))` - each slices `contents` into 16- or 4-byte blocks, validates SAK/ATQA lengths (else error dialog `invalid_input`), builds `CardSave`, sets folderId, adds, persists. Cancel closes.

### 4.4 Folder operations (cards)

- `_editFolder([folder])` (`:89-178`): AlertDialog create/edit; `TextField(name)` with a `folder` prefix icon that opens a `ColorPicker` dialog; on save appends/updates `CardFolder(name, color, parentId:currentFolderId)`.
- `_pickFolderDestination({movingFolder})` (`:196-229`): `SimpleDialog move_to_folder` listing root (`saved_cards`) + all folders except the moving-folder subtree. `_moveCard` (`:231-243`) / `_moveFolder` (`:245-257`) set the target's folderId/parentId (`__root__` → null).
- `_deleteFolder` (`:259-290`): confirm dialog `delete_folder_title(name)` / `delete_card_folder_confirmation`; cascade-removes the subtree folders AND their cards.
- `_exportFolder` (`:292-311`): builds `CardFolderBundle(rootFolderId, subtree folders, subtree cards)` → `FilePicker.saveFile({name}.json)`.
- `_importFolderSource` (`:313-349`): remaps all ids to fresh UUIDs, re-parents the root to `currentFolderId`, imports cards with new ids/folderIds; catch → SnackBar `invalid_folder_export`.

### 4.5 Dictionaries section (`:1439-1694`)

Mirror of cards. Header title = `currentDictionaryFolder?.name ?? dictionaries`; extra back-arrow inside a folder. Actions: `IconButton(Icons.upload)` → `importDictionary()`; `_dictionaryCreateMenuButton` (menu: `dictionary`→`_createDictionary()` = `DictionaryEditMenu(new)`; `folder`→`_editDictionaryFolder`).

**Grid:** folders then dictionaries.
- Folder → `ElementButton(folder, folder_dictionary_count)`, onPressed sets currentDictionaryFolderId. Trailing: move/edit/export/delete (dictionary-folder variants `:1580-1604`).
- Dictionary → `ElementButton(icon:key, iconColor:dictionary.color, firstLine:name, secondLine: key_count: {n})`, onPressed → `DictionaryViewMenu(dictionary, onMove:_moveDictionary)`. Trailing: move; edit (`DictionaryEditMenu`); download (`FilePicker.saveFile({name}.dic, dictionary.toFile())`); delete (optional confirm → filter by id → `setDictionaries`).

**Import (`:1483-1530`):** pickFile → UTF-8 (catch→abort); if JSON `format=='chameleon-ultra-gui-dictionary-folder'` → `_importDictionaryFolderSource`; else `Dictionary.fromString(contents, name)`; folderId=current; abort if no keys; add + persist. Folder ops (`_editDictionaryFolder`, `_pickDictionaryFolderDestination`, `_moveDictionary(Folder)`, `_deleteDictionaryFolder`, `_exportDictionaryFolder`, `_importDictionaryFolderSource`) mirror the card equivalents (`:351-614`).

### 4.6 DictMergeDelegate (`:1713-1835`)

A `SearchDelegate` for merging dictionaries (invoked via `dictMergeDialog`, `:1700-1710`). `buildActions`: clear + `Icons.merge`. Merge (`:1734-1767`) concatenates keys of checked dictionaries into `mergeDict`, dedups by `Object.hashAll`, replaces in list, persists, pops. `buildSuggestions/buildResults`: `CheckboxListTile` per dictionary (excludes the merge target itself), subtitle "{n} total keys". Note: `buildResults` checkboxes are no-op (`onChanged:(){}`), only suggestions toggle selection.

---

## 5. WRITE CARD (`write_card.dart`, 416 L) - tab index 4

A 3-step `Stepper` for writing a saved card onto a physical **magic** card. AppBar `write_card`. Body is a scrollable centered Stepper.

### 5.1 State

State (`:22-27`): `step`(0), `progress`(-1), `written`(false), `card:CardSave?`, `baseHelper:AbstractWriteHelper?`, `helper:AbstractWriteHelper?`.

**`AbstractWriteHelper`** (`helpers/write.dart`): abstract write strategy. `getClassByCardType(type,...)` (`:47-74`) returns: MifareClassic → `BaseMifareClassicWriteHelper(recovery)`; Ultralight → `BaseMifareUltralightWriteHelper`; EM410X/HID/Viking/PAC/IOProx/Idteck → `BaseT55XXCardHelper`; else null (unsupported). API: `name`, `autoDetect`, `isMagic(data)`, `isReady()`, `isCompatible(card)`, `getAvailableMethods()`, `getCardType()`, `writeData(card, update)`, `getWriteWidget(ctx,setState)`, `writeWidgetSupported()`, `getFailedBlocks()`, `reset()`. Equality by `name`.

### 5.2 The three steps (`:313-411`)

**Step 0 - `select_saved_card_to_write` (`:314-342`):** a `Card>ListTile` with a `FilterChip`. Chip avatar = credit_card (hf) / wifi (lf) in `card.color` when a card chosen; label = `card.name` or `select_saved_card`. Selecting the chip → `cardSelectDialog` (`showSearch(CardSearchDelegate)`, `:29-39`). 
- `onTap(selectedCard, close, localizations)` (`:41-61`): setState card=selected, `baseHelper = getClassByCardType(tag,...)`; if baseHelper != null, `helper = baseHelper.getAvailableMethods()[0]`; `await helper.getCardType()`; `close(context, name)`.

**Step 1 - `select_magic_card` (`:343-384`):** if `baseHelper != null` → a `Wrap` with `DropdownButton<AbstractWriteHelper>` (value=helper, items from `getAvailableMethods()` labeled via `typeLocalization` map: gen1/gen2/gen3/t55xx, `:289-294`), onChanged sets helper; plus, if `baseHelper.autoDetect`, a `TextButton(auto_detect_magic_card)` → `detectMagicType()`. Else `writing_is_not_yet_supported`.
- `detectMagicType()` (`:63-109`): ensure reader mode; iterate `getAvailableMethods()`, first `magicHelper.isMagic(card)` true → set helper, `getCardType()` (retry once on throw), SnackBar `detected_magic_card_type: {name}`, return. If none → SnackBar `failed_to_detect_magic_card_type`.

**Step 2 - `write_data_to_magic_card` (`:385-411`):** content depends on state:
- If `progress != -1` → `LinearProgressIndicator(progress/100)` (writing in progress).
- Else if `helper.isReady()`:
  - If `getFailedBlocks()` non-empty → text `otp_magic_warning(...) some_blocks_failed_to_write: {joined}`.
  - Else → Column: `otp_magic_warning(...)` + orange bold `keep_stable_warning`.
- Else if `helper.writeWidgetSupported()` → `helper.getWriteWidget(context, setState)` (helper-specific input UI).
- Else → `error`.

### 5.3 Stepper controls (`createButtonsForStep`, `:236-279`)

Controls rebuilt per step:
- If `written`: `write_again` (→`onStepContinue` when progress==-1) + `reset` (→`onStepReset`).
- Else:
  - step 0/1 → `next` button, disabled when (step0 && card==null) or (step1 && baseHelper==null).
  - step 2 → `write_data_to_magic_card` button, enabled only if `helper!=null && helper.isReady() && progress==-1`.
  - step!=0 → `back` (`onStepBack`).

**`onStepContinue()` (`:162-216`):**
1. If device is **lite** → AlertDialog `no_supported`/`lite_no_read`/OK, return (lite can't write).
2. If step != 2 → if step==1 `helper.reset()`; setState step++.
3. Else (step 2, helper ready, progress==-1): `updateProgress(0)`; ensure reader mode is handled in writeCard. If `!helper.isCompatible(card)` → SnackBar `magic_incompatible_card` with action `continue_anyway` → `writeCard()`. Else `writeCard()`. Then `updateProgress(-1)`.

**`writeCard()` (`:123-160`):** `updateProgress(0)`; ensure `setReaderDeviceMode(true)`; `success = helper.writeData(card, updateProgress)`; SnackBar `magic_success_write` or `magic_failed_write` (Close action); setState `written=true`; `updateProgress(-1)`.

**`onStepBack()` (`:218-227`):** setState written=false, step--; if step==1 `helper.reset()`. **`onStepReset()` (`:229-234`):** written=false, step=0. `updateState()` (`:111-115`) and `updateProgress(n)` (`:117-121`) are setState wrappers.

---

## 6. Cross-page data flow (the contract that ties it together)

```
                     SharedPreferencesProvider (persistent)
                     ├─ getCards()/setCards()      ← the CardSave list
                     ├─ getCardFolders()/set...
                     ├─ getDictionaries()/set...
                     └─ flags: confirmDelete, autoScan, autoConnect, sidebar...

 READ CARD ──save(HF/LF/MFC/MFU)──▶ setCards()  (new CardSave appended)
                                        │
 SAVED CARDS ◀──lists/edits/imports/exports──┘  (view/edit/move/delete/format-convert)
                                        │
        ┌───────────────────────────────┴───────────────────────────┐
        ▼                                                            ▼
 SLOT MANAGER  onTap(card)                                    WRITE CARD  onTap(card)
   → writes CardSave into device slot                          → writes CardSave onto a
     via communicator (enableSlot/activateSlot/                  physical magic card via
     setSlotType/setDefaultData/anti-collision/                  AbstractWriteHelper.writeData
     block or page upload/setSlotTagName/saveSlotData)
        │                                                            │
        └── active slot reflected on HOME (SlotChanger) ────────────┘
```

Key invariants for the native port:
1. **`communicator` is the only device I/O surface.** Every device action in Read/Slot/Write/Home routes through `appState.communicator!.<method>`. When disconnected it is null; pages 1/3/4 are rail-disabled and force-routed to Home (`main.dart:224`).
2. **`CardSave` is the universal interchange record** (`sharedprefsprovider.dart:195-272`): `id`(uuid), `uid`(string), `sak`(int), `atqa/ats`(Uint8List), `name`, `tag:TagType`, `data:List<Uint8List>` (blocks/pages), `extraData:CardSaveExtra`(ultralight version/signature/counters), `color`, `folderId`. Read Card produces them; Saved Cards curates them; Slot Manager & Write Card consume them. JSON round-trips via `toJson`/`fromJson`.
3. **Reader vs emulator mode toggling is stateful and side-effectful.** Reading (`readHFInfo`, `readLFInfo`, Write) ensures `setReaderDeviceMode(true)`; writing to slots ensures `setReaderDeviceMode(false)` first. The native port must serialize these - do not read and emulate concurrently.
4. **Slot writes always end with `saveSlotData()` + `changesMade()` + `refreshSlot()`** so Home/SlotManager re-query the device.
5. **`getConfirmDelete()` gates every destructive delete** (cards, dictionaries, slot HF/LF) via `ConfirmDeletionMenu(thingBeingDeleted)`; skip only when the flag is false.
6. **Device capability split (ultra vs lite):** Lite refuses all reads/writes with `no_supported`+`lite_no_read` dialog (Read Card ×4 button branches, Write Card step-continue). Ultra performs the operation. Any other device → `changesMade()` no-op.

## 7. Reusable UI primitives to port

| Component | File | Contract |
|---|---|---|
| `customCardButtonStyle` | `card_button.dart` | ElevatedButton style: bg=`getThemeComplementaryColor()`, radius 18 |
| `ElementButton` | `element_button.dart` | icon+2 lines+trailing actions, reflow actions below text when cramped (90→130px) |
| `ErrorMessage` | `error_message.dart` | inline error banner: light `#FDEDED`/dark `#FF7961` bg, text `#5F2120`, `error_outline` icon, min height 60 |
| `ErrorPage` | `error_page.dart` | centered `error_outline`(80px, colorScheme.error) + `error:` + message |
| `CardSearchDelegate` | `card_list.dart` | search with All/HF/LF dropdown filter; suggestions call `onTap(card, close, localizations)`; leading icon credit_card(hf)/wifi(lf) in card.color |
| `KeyCheckMarks` | `key_check_marks.dart` | sector key grid, tappable checkmarks (`ChameleonKeyCheckmark` states) |

**Responsiveness breakpoints used across pages:** 600 (sidebar auto-expand, checkmark per-row), 700 (grid 1↔2 cols in Saved/Slot; compact Saved Cards header), 800 (Read Card small/wide button layout; `_ResponsiveButtonGroup`).

**Empty/loading/error summary:** every device-reading page uses `FutureBuilder` → `CircularProgressIndicator` (waiting) → `ErrorPage` + `performDisconnect()`/`disconnect()` (error) → content (done). Read Card additionally shows inline `ErrorMessage(no_card_found)` when `!cardExist`, and Slot Manager overlays an upload `LinearProgressIndicator` while `progress != -1` (which also blocks new uploads). Continuous scans self-terminate after 1 minute or on first successful read.

Localization keys are cited verbatim (e.g. `no_supported`, `lite_no_read`, `save_only_uid`, `otp_magic_warning`, `keep_stable_warning`, `uploading_dump`, `used_slots`, `folder_card_count`) - resolve them from `messages/en.json` / `vi.json` for the port's string table.

---


# GUI - Tools, Settings, Dialogs, Editors, DFU Flashing

I now have exhaustive coverage of every target file. Below is the parity-grade build spec.

---

# ChameleonUltraGUI - Parity Reference: Tools, Settings, Editors, Dialogs & DFU Flashing

All paths are absolute under `/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/`. Line numbers cite the exact source. This is a Flutter/Dart app; every "communicator" call is a serial/BLE command to the ChameleonUltra device - command IDs are in `helpers/definitions.dart` (`enum ChameleonCommand`, lines 5-163). Text shown to the user comes from `AppLocalizations` (i18n keys), so labels are given by key; render the English string.

---

## 0. Shared primitives you must build first

### 0.1 App state (`main.dart`, `ChameleonGUIState`)
- `communicator` (`ChameleonCommunicator?`) - device command channel; `null` when disconnected (main.dart:75, cleared on disconnect at :93,:129).
- `connector` - serial/BLE transport; exposes `.device` (`ChameleonDevice.ultra|lite`), `.isDFU`, `.isOpen`, `.connected`, `.connectionType` (`ConnectionType.ble|usb`), `.portName`, `performDisconnect()`, `connectSpecificDevice(port)`, `availableChameleons(bool)`, `connectSpecificDevice`.
- `progress` (`double?`, main.dart:78) - DFU progress 0..1. `setProgressBar(value)` (:134) sets it + `notifyListeners()`.
- `easterEgg` (bool, :81) - 1-in-100 flag set during flashing (see §1.4).
- `changesMade()` (:87) = `notifyListeners()` - call after any state mutation so widgets rebuild.
- `sharedPreferencesProvider` - all persisted settings/cards/dictionaries (see §2, §8).
- Routing (main.dart:234-274): sidebar index → page. Index 0 special-cases: `pendingConnection`→`PendingConnectionPage`; `connected && isDFU`→`FlashingPage`; `connected`→`HomePage`; else `ConnectPage`. `WakelockPlus` enabled while `page is FlashingPage` (:277).
- `BottomProgressBar` (main.dart:405-419): a `LinearProgressIndicator(value: appState.progress)` shown only while `connected && isDFU`, blue on grey.

### 0.2 Validators (`helpers/validators.dart`)
| Function | Rule | Error key |
|---|---|---|
| `validateName` (10) | non-empty, ≤`maxNameLength`=19 (8) | `please_enter_name` / `too_long_name` |
| `validateUid` (22) | strips spaces. HF create+Ultralight→14 chars; HF create→8 or 14; HF edit→8/14/20; LF→`uidSizeForLfTag(type)*2` | `must_be`/`must_or`/`please_enter_something` |
| `validateHex` (59) | valid hex, even length; optional `exactBytes`; `required` flag | `must_be_valid_hex`/`must_be` |
| `validateIntRange` (94) | int in `[min,max]`; `required`; optional `emptyMessage` | `must_be_between` |
| `validateBlePin` (120) | exactly 6 digits | `pin_must_be_6_digits` |
| `hexFormatter` (130) | `FilteringTextInputFormatter.allow(RegExp(r'[0-9A-Fa-f: ]'))` |

### 0.3 Enums (`helpers/definitions.dart`)
- `AnimationSetting`: full(0), minimal(1), none(2), symmetric(3) (204-212).
- `ButtonConfig`: disable(0), cycleForward(1), cycleBackward(2), cloneUID(3), chargeStatus(4) (241-250).
- `ButtonType`: a(65='A'), b(66='B') (233-239).
- `MifareWriteMode`: normal(0), denied(1), deceive(2), shadow(3) (214-222).
- `Mf1PrngType`: static(0), weak(1), hard(2) (224-231).
- `TagFrequency`: unknown(0), lf(1), hf(2) (195-202).
- `DeviceSettings` (448-467): `animation, aPress, bPress, aLongPress, bLongPress, pairingEnabled, key(String), wakeTimeSeconds(int?)`.

---

## 1. FIRMWARE / DFU FLASHING FLOW (the priority path)

Three source files: `helpers/github.dart` (network fetch), `helpers/flash.dart` (orchestration + validation), `bridge/dfu.dart` (Nordic DFU protocol), `gui/page/flashing.dart` (progress screen). Triggers live in `menu/dialogs/chameleon_settings.dart`, `page/home.dart`, `page/connect.dart`, `page/debug.dart`.

### 1.1 Where the user triggers a flash

There are **five distinct entry points**, all ultimately calling `flashFirmware(appState, scaffoldMessenger:...)` or `flashFirmwareZip(...)`:

1. **Device Settings dialog → "Enter DFU"** (`chameleon_settings.dart:76-90`): `ElevatedButton` icon `Icons.medical_services_outlined`, label key `enter_dfu`. Calls `communicator.enterDFUMode()` then `connector.performDisconnect()`, pops dialog, `changesMade()`. This only *reboots into DFU*; the device reconnects as DFU and `main.dart` then shows `FlashingPage`. It does NOT itself download/flash.
2. **Device Settings dialog → "Flash via DFU"** (`chameleon_settings.dart:95-131`): icon `Icons.system_security_update`, label key `flash_via_dfu`. Pops dialog, shows a persistent `SnackBar` with text key `downloading_fw(deviceName)` + a `close` action, then `await flashFirmware(appState, scaffoldMessenger)`. On throw: `hideCurrentSnackBar()` then error SnackBar `update_error: <e>`.
3. **Device Settings dialog → "Flash .zip via DFU"** (`chameleon_settings.dart:136-160`): icon `Icons.system_security_update_good`, label key `flash_zip_dfu`. Calls `flashFirmwareZip(appState, scaffoldMessenger)` (user picks a local zip). Same error handling.
4. **Home page → "Check updates" button** (`home.dart:356-448`): `IconButton(Icons.update)`, tooltip `check_updates`, next to firmware version text. Flow: `latestAvailableCommit(device)` → on error, error SnackBar and return. Then `resolveCommit(fwVersion[1])` to normalize the *current* commit. Logs `Latest commit: X, current commit Y`. If `latestCommit.isEmpty` return silently. If `latestCommit.startsWith(currentCommit)` → SnackBar `up_to_date(deviceName)`. Else → SnackBar `downloading_fw` + `flashFirmware(...)` with `update_error` catch.
5. **Home page → "please update firmware" banner** (`home.dart:479-531`): shown when `!areCapabilitiesSupported && portName != "Demo"`. A `TextButton` label `update` → SnackBar `downloading_fw` → `flashFirmware(...)`.
   - Plus **legacy-protocol forced update** (`home.dart:147-197`): if `isLegacyFirmware`, a non-dismissible `AlertDialog` (`outdated_protocol` + 3 description lines) with `update` / `skip` actions; `update` → `flashFirmware`.
   - `connect.dart:322` and `debug.dart:442,453` also call `flashFirmware` (auto-flash on connect if outdated / debug buttons).

The **"Demo" firmware banner** (`home.dart:451-478`): if `portName == "Demo"` show a static `demo_firmware` warning box (no flash).

### 1.2 `FlashingPage` (`gui/page/flashing.dart`, 57 L)
Shown automatically by `main.dart` when the connected device is in DFU mode (`isDFU`). It is a full-screen `Scaffold`:
- `AppBar` title literal `'Chameleon DFU'`.
- Centered column: `Image.asset(...)` 300×300 - asset chosen by `device==ultra ? (easterEgg? 'assets/black-ultra-standing-front-flashing.webp' : '...-front.webp') : (lite variant)`.
- Title `Text` 24px bold: `easterEgg ? chameleon_flashing_title_easter_egg(name) : chameleon_flashing_title(name)` where name = `chameleonDeviceName(ChameleonDevice.ultra)`.
- Subtitle `Text` 20px: key `please_wait`.
- The actual progress number is rendered by the separate `BottomProgressBar` (main.dart:405) driven by `appState.progress`.

### 1.3 Firmware source selection (`helpers/github.dart`)

`fetchFirmware(device)` (flash.dart:17) tries **GitHub Actions nightly first, then Releases**:

**A. `fetchFirmwareFromActions(device)` (github.dart:121-153)** - nightly/main build:
- GET `https://api.github.com/repos/RfidResearchGroup/ChameleonUltra/actions/artifacts?per_page=100`.
- If JSON has `message` key → throw it (rate-limit/error).
- Iterate `artifacts["artifacts"]`; match `name == "<ultra|lite>-dfu-app"` AND `workflow_run.head_branch == "main"` AND `workflow_run.head_repository_id == 581338100`.
- Download from `https://nightly.link/RfidResearchGroup/ChameleonUltra/suites/<run.id>/artifacts/<artifact.id>` (nightly.link proxies the zip since GitHub artifact download needs auth).
- All wrapped in `try/catch(_){}`; only a `message` error re-throws.

**B. `fetchFirmwareFromReleases(device)` (github.dart:85-119)** - fallback, only if Actions returned empty:
- GET `.../ChameleonUltra/releases`.
- If not a List and has `message` → throw.
- For each release where `prerelease == true`, for each asset, match `name == "<ultra|lite>-dfu-app.zip"`; download `browser_download_url`.

**C. Pick file (`flashFirmwareZip`, flash.dart:101-119)**: `FilePicker.pickFile()`; read bytes; unpack; flash. No network.

Device naming: `ultra` → `"ultra"`, else `"lite"` (github.dart:104). `latestAvailableCommit(device)` (155-203) resolves the newest available commit SHA: first checks Actions artifacts (same match → `head_sha`), else Releases (`author.login == "github-actions[bot]" && prerelease` → `target_commitish`).

`resolveCommit(commitHash)` (205-223): `v2.0.0-1-gXXXXXX` (two dashes) → take `split("-")[2]` strip `g`; `-dirty` prefix → return as-is; no dashes → look up in `/tags` for matching `name`→`commit.sha`.

### 1.4 Unpack + validate (`helpers/flash.dart`)
`unpackFirmware(content)` (27-44): `ZipDecoder().decodeBytes`; extract `application.dat` and `application.bin`. Returns `(dat, bin)` tuple.

`validateFiles(dat, bin)` (52-81):
1. Either empty → throw `"Empty firmware file"`.
2. `Packet.fromBuffer(dat)` (Nordic protobuf `dfu-cc.pb.dart`); require `hasSignedCommand()` else throw `"Package isn't signed"`.
3. `signedCommand.command`; require `hasInit()` else throw `"Package command doesn't have init"`.
4. `hash = command.init.hash`; `expectedHash = hash.hash.reversed` (byte-reversed).
5. Compute actual hash of `bin` by `hash.hashType`: `SHA128→sha1`, `SHA256→sha256`, `SHA512→sha512`; else throw `"Unsupported hash type ..."`.
6. Compare with `IterableEquality`; mismatch → throw `"Hashes don't match! expected:..., actual:..."`.

**Easter egg** (`flashFirmware`→`flashFile`, flash.dart:132-138): `Random().nextInt(100)+1`; if `==1` set `appState.easterEgg=true` (changes the FlashingPage image/title).

### 1.5 `flashFile(...)` orchestration (`flash.dart:121-201`)
1. `validateFiles(dat,bin)`.
2. Easter-egg roll.
3. If `enterDFU` (default true): `connection?.enterDFUMode()` then `connector?.performDisconnect()`.
4. If `connector.isOpen`: `performDisconnect()`.
5. If Android: `asyncSleep(1000)` (BLE re-enumerates before USB).
6. **Wait loop** (156-159): repeat `asyncSleep(250)` then `chameleons = connector.availableChameleons(true)` until non-empty. (No timeout - blocks until a DFU device appears.)
7. `toFlash = chameleons[0]`. Track `{ble:false, usb:false}`; if two of the same `type` seen → throw `"More than one Chameleon in DFU. Please connect only one at a time"` (167-173).
8. If `toFlash.type == ble`, prefer a non-BLE device if present (175-182) - USB is more reliable.
9. `connector.connectSpecificDevice(chameleons[0].port)`.
10. `scaffoldMessenger?.removeCurrentSnackBar()`.
11. Build `DFUCommunicator(log, port: connector, viaBLE: toFlash.type==ble)`.
12. `changesMade()`; `dfu.setPRN()`; `dfu.getMTU()`.
13. `dfu.flashFirmware(0x01, applicationDat, callback)` then `dfu.flashFirmware(0x02, applicationBin, callback)` - object type 1 = init packet, 2 = firmware image. `callback = (progress)=>setProgressBar(progress/100)`.
14. Log `"Firmware flashed!"`; `performDisconnect()`; `asyncSleep(500)` (exit DFU); `changesMade()`.

### 1.6 Nordic DFU protocol (`bridge/dfu.dart`, 367 L)

**Constants**: `baudrate=115200`, `dataFrameSof=0x11`, `dataMaxLength=512`, default `mtu=2051` (fallback), `prn=0`.

**`enum DFUCommand`** (9-24): createObject(0x01), setPRN(0x02), calcChecSum(0x03), execute(0x04), readError(0x05), readObject(0x06), getSerialMTU(0x07), writeObject(0x08), ping(0x09), getHW(0x0a), response(0x60).

**`enum DFUResponseCode`** (26-47): invalidCode(0), success(1), notSupported(2), invalidParameter(3), insufficientResources(4), invalidObject(5), invalidSignature(6), unsupportedType(7), operationNotPermitted(8), operationFailed(0x0A), extendedError(0x0B). `fromValue` defaults to `invalidCode`.

**SLIP framing** (`class Slip`, 49-121) - used for **serial/USB only, not BLE**:
- Bytes: END=0xC0, ESC=0xDB, ESC_END=0xDC, ESC_ESC=0xDD.
- `encode`: replace 0xC0→[0xDB,0xDC], 0xDB→[0xDB,0xDD], append 0xC0.
- `decode`: state machine (decoding/escReceived/clearingInvalidPacket); `decodeAddByte` returns `(finished,state,decoded)`.

**`sendCmd(cmd, data)`** (151-203):
1. `packet = [cmd.value, ...data]`; if `!isBLE` → `Slip.encode(packet)`.
2. Complete any pending completer with `[]`; new `responseCompleter`.
3. Open serial if needed; `registerCallback(completer.complete)`.
4. Log+`write(packet)`; await response bytes.
5. If null/empty → return null.
6. If `!isBLE` → `Slip.decode`.
7. `readBuffer[0] != 0x60` → throw `"DFU sent not response"`.
8. `readBuffer[1] != cmd.value` → throw `DFUTransferError("Received unexpected DFU command")`.
9. `readBuffer[2] == success(1)` → return `sublist(3)`. Else if `extendedError(0x0B)` → throw `"DFU error: <code from byte 3>"`; else throw `"DFU error: <code byte 2>"`.

**Object ops**:
- `selectObject(type)` (205): `readObject` with `[type,0,0,0]`; parse `maxSize`(u32le@0), `offset`(@4), `crc`(@8).
- `createObject(type,size)` (214): `createObject` with `[type, size-as-u32le]`.
- `execute()` (221): `execute` empty.
- `setPRN()` (225): `setPRN` `[0x00]` (PRN disabled → validate only at end).
- `getMTU()` (229): `getSerialMTU`; parse u16le; on any error or 0 → `mtu=2051`.
- `calculateChecksum()` (245): `calcChecSum`; parse `offset`(u32le@0), `crc`(@4).

**`flashFirmware(objectType, bytes, callback)`** (253-290):
- `object = selectObject(type)`; `length = object.maxSize`.
- Loop `offset` in steps of `length`:
  - `tries` up to `(iOS?50:10)`:
    - `createObject(type, min(remaining, length))`.
    - `sendFirmware(chunk, crc, offset)` - on `DFUTransferError`: warn, `object=selectObject`, restore `crc=crcBackup`, continue.
    - `asyncSleep(1)`, `execute()`, `callback(round(offset/len*100))`, `asyncSleep(1)`, break.
  - If exhausted tries → throw `"Unable to recover from DFU"`.

**`sendFirmware(data, crc, offset)`** (292-341):
- Chunk size `(mtu-1)~/2 - 1`. For each chunk: build packet; if `!isBLE` SLIP-encode with `writeObject(0x08)` prefix; `delayedSend`; advance `offset`; `crc = calculateCRC32(chunk, crc) & 0xFFFFFFFF`; when `currentPrn==prn` → checksum + validate. Final checksum + `validateCrc()`.
- `validateCrc()`: throws `DFUTransferError("Offset")` or `("CRC")` on mismatch.

**`delayedSend(packet)`** (343-366): Windows/macOS/BLE send in slices of `offsetSize` (128; 20 for BLE non-macOS). BLE on iOS/macOS: `asyncSleep(250)` after. Other OS: single write. All writes use `write(..., firmware:true)`.

**Platform quirks to preserve**: iOS retries 50× (else 10); BLE chunk size 20 bytes off-macOS; Android 1s pre-scan sleep; Windows byte-slicing.

---

## 2. SETTINGS - App Settings page (`gui/page/settings.dart`, 669 L)

`SettingsMainPage`, a scrolling centered column. Every control writes to `sharedPreferencesProvider` and calls `changesMade()`.

| Control (line) | Widget | Options / values | Persistence method |
|---|---|---|---|
| Sidebar expansion (88-117) | `ToggleButtonsWrapper` | `expand`(0), `auto`(1), `retract`(2) | idx0→`setSideBarExpanded(true)+setSideBarAutoExpansion(false)`; idx2→`setSideBarExpanded(false)+auto(false)`; idx1→`setSideBarAutoExpansion(true)`; always `setSideBarExpandedIndex(index)`; then `updateNavigationRailWidth` post-frame |
| Theme (124-136) | `ToggleButtonsWrapper` | `system`(0),`light`(1),`dark`(2) | `setTheme(ThemeMode.values[index])` |
| Color scheme (143-185) | `DropdownButton` | `def`(0),`purple`(1),`blue`(2),`green`(3),`indigo`(4),`lime`(5),`red`(6),`yellow`(7) | `setThemeColor(value)` |
| Language (192-208) | `DropdownButton` | `AppLocalizations.supportedLocales`, shown as `language_name`, value=`locale.toLanguageTag()` | `setLocale(Locale(value))` |
| Auto scan devices (210-228) | `Switch` | bool | `getAutoScanEnabled`/`setAutoScanEnabled` |
| Auto connect first device (230-248) | `Switch` | bool | `getAutoConnectFirstFoundDevice`/`set...` |
| Confirm deletions (250-268) | `Switch` | bool | `getConfirmDelete`/`setConfirmDelete` (gates all delete confirm dialogs) |
| Export settings (270-359) | `TextButton`+`Icons.upload` | opens dialog `choose_export_method` → **QR** or **JSON file** | see below |
| Import settings (361-445) | `TextButton`+`Icons.download` | dialog `import_settings` → **QR** (mobile only) or **JSON file** | `restoreSettingsFromJson` |
| About (447-535) | `TextButton` | dialog w/ `FutureBuilder(getFutureData)` | read-only |
| Licenses (537-582) | `TextButton` | loads 4 license .md, registers, `showLicensePage` | - |
| Changelog (584-590) | `TextButton` | `ChangelogView` (see §6.4) | - |
| Help translate (592-598) | `TextButton` | `launchUrl(crowdin.com/project/chameleonultragui)` | - |
| Emulate device toggle (600-631) | `TextButton` | confirm dialog `emulate_device_confirmation(activate/deactivate)` → `setEmulatedChameleon(!...)`, `connector=null` | Demo/emulated device |
| Debug mode toggle (633-662) | `TextButton` | confirm dialog `debug_mode_confirmation(...)` → `setDebugMode(!...)` | shows Debug page in sidebar |

**Export settings dialog** (272-358): dialog title `choose_export_method`, content `choose_export_method_description`, actions `cancel` / `qr_code` / `json_file`.
- **QR path** (286-334): `string = dumpSettingsToJson()`. Open `QRCodeSettings` dialog (§6.1) → returns `{splitSize, errorCorrection}` (empty map cancels). `splitStringIntoQrChunks(string, splitSize)`. Build header `{"Info":"Chameleon Ultra GUI Settings","chunks":n,"sha256":<sha256 of utf8(string)>}`, `insert(0, jsonEncode(header))`. Show `QrCodeViewer(qrChunks, errorCorrection)`.
- **JSON path** (335-347): `FilePicker.saveFile(dialogTitle:'<output_file>:', fileName:'ChameleonUltraGUISettings.json', bytes: utf8(dumpSettingsToJson()))`.

**Import settings dialog** (364-435): title `import_settings`, actions `cancel`/`qr_code`/`json_file`.
- QR only on Android/iOS; else shows error dialog `qr_code_import_not_supported_description`. Opens `QrCodeImport` (§6.5) returning json string → `restoreSettingsFromJson`.
- JSON: `FilePicker.pickFile()`, read bytes, utf8 decode → `restoreSettingsFromJson`.

**About dialog** (447-534): `getFutureData` (43-50) = `(fetchOCnames(), fetchContributors(), PackageInfo.fromPlatform())`. Shows: title `Chameleon Ultra GUI`, `about_text`, `version: <ver> (Build <buildNumber>)`, `developed_by:` + `DeveloperList(developers)` (github.dart:7-50, 7 hardcoded devs), `license: GNU General Public License v3.0`, tappable repo URL, OpenCollective link `thanks_for_support`, OC contributor names, `code_contributors:` + `DeveloperList(contributors)` (fetched via `fetchGitHubContributors`, github.dart:54, excludes `github-actions[bot]`,`ChameleonHelper` + core devs), trademark lines `trademarks_mifare/em/hid`. On snapshot error → `performDisconnect()` + `ErrorPage`.

**Licenses** (537-580): loads `assets/licenses/{BSD-3-Clause,GPL3,LGPL3,MIT}.md`, registers 4 `LicenseEntryWithLineBreaks` (chinese_font_library=BSD, FlipperNestedRecovery=LGPL3, proxmark3=GPL3, minlzma=MIT), then `showLicensePage`.

---

## 3. SETTINGS - Device Settings dialog (`chameleon_settings.dart`, 557 L)

`ChameleonSettings` - opened from Home page gear icon (`home.dart:566-572`, `IconButton(Icons.settings)`). This is the on-device configuration. Opens with `FutureBuilder(getSettingsData())`.

`getSettingsData()` (32-39): `communicator.getDeviceSettings()`; on any error returns default `DeviceSettings()`.

**`getDeviceSettings()` wire format** (`bridge/chameleon.dart:1105-1126`): cmd `getDeviceSettings`(1034). Response payload (needs ≥13 bytes else throw `"Invalid settings payload"`): byte[1]=animation, [2]=aPress, [3]=bPress, [4]=aLongPress, [5]=bLongPress, [6]=pairingEnabled(==1), [7..13]=key(utf8, 6 chars), [13]=wakeTimeSeconds (only if len≥14, else null).

Loading state shows `AlertDialog(device_settings)` + `CircularProgressIndicator`. Error → `performDisconnect()` + `ErrorPage`.

The dialog is a scrolling column with these sections:

### 3.1 Firmware management (71-160)
- Header text `firmware_management:`.
- **Enter DFU** button (§1.1 #1).
- **Flash via DFU** button (§1.1 #2).
- **Flash .zip via DFU** button (§1.1 #3).

### 3.2 Animation (162-181)
- Header `animations:`.
- `ToggleButtonsWrapper` items `[full, mini, none, symmetric]`, selectedValue=`settings.animation.value`.
- onChange: `animation = getAnimationModeType(index)`; `setAnimationMode(animation)` → cmd `setAnimationMode`(1015) with `[animation.value]` (chameleon.dart:827); then `saveSettings()` (cmd 1013); update local; `changesMade()`.

### 3.3 Wake time (182-235) - only if `settings.wakeTimeSeconds != null`
- Label `wake_time_after_button_press`.
- `Form` (wakeTimeFormKey) with `TextFormField` (numeric, `digitsOnly`), label `wake_time`, hint `"5-60"`, validator `validateIntRange(min:5,max:60)`.
- `Save` button: validate → `setSleepTimeout(int)` → cmd `setSleepTimeout`(1040) with `[seconds]` (chameleon.dart:990); `saveSettings()`.

### 3.4 Button config (236-368)
Header `button_config:`. Four `ToggleButtonsWrapper`, each items `[disable, forward, backward, clone_uid, charge]` (maps to `ButtonConfig.disable/cycleForward/cycleBackward/cloneUID/chargeStatus`):
- **Button A short press** (label `button_x("A")`): `setButtonConfig(ButtonType.a, mode)` → cmd `setButtonPressConfig`(1027) with `[type.value, mode.value]` (chameleon.dart:969).
- **Button B short press** (`button_x("B")`): `setButtonConfig(ButtonType.b, mode)`.
- Sub-header `long_press`.
- **Button A long press**: `setLongButtonConfig(ButtonType.a, mode)` → cmd `setLongButtonPressConfig`(1029).
- **Button B long press**: `setLongButtonConfig(ButtonType.b, mode)`.
Each also `saveSettings()` + `changesMade()`.

### 3.5 BLE (370-484)
- Header `BLE:`, sub `ble_pairing:`.
- `ToggleButtonsWrapper` `[enabled, disabled]`, selectedValue=`pairingEnabled?0:1`. onChange: `setBLEPairEnabled(index==0)` → cmd `bleSetPairEnable`(1037) `[status?1:0]` (chameleon.dart:1014); `saveSettings()`.
- **If `pairingEnabled`** show:
  - **Clear BLE bonds** button (icon `Icons.settings_bluetooth`, label `clear_ble_bonds`): confirm dialog `clear_ble_bonds_confirmation` (yes/no). Yes → `clearBLEBoundedDevices()` → cmd `bleClearBondedDevices`(1032) `skipReceive:true` (chameleon.dart:995). If current connection is BLE → `performDisconnect()`.
  - **BLE PIN** form (formKey): `TextFormField` maxLength 6, `digitsOnly`, validator `validateBlePin`, label `ble_pin`, hint `enter_pin`. Save → `setBLEConnectKey(text)` → cmd `bleSetConnectKey`(1030) with utf8 bytes (chameleon.dart:1004); `saveSettings()`; pop.

### 3.6 Other (485-551)
- Header `other:`.
- **Reset settings** (icon `Icons.lock_reset`, label `reset_settings`): `resetSettings()` → cmd `resetSettings`(1014) (chameleon.dart:823); pop; `changesMade()`.
- **Factory reset** (icon `Icons.restore_from_trash_outlined`, label `factory_reset`): pops dialog first, then confirm dialog `factory_reset_confirmation` (yes/no). Yes → `factoryReset()` → cmd `factoryReset`(1020) `skipReceive:true` (WARNING: erases all); `performDisconnect()`; `changesMade()`.

---

## 4. SAVED-CARD DIALOGS

Persisted model `CardSave` (`sharedprefsprovider.dart`): `id, uid(String), sak(int), atqa(Uint8List), ats(Uint8List), name, tag(TagType), data(List<Uint8List> blocks), extraData(CardSaveExtra: ultralightVersion, ultralightSignature, ultralightCounters), folderId, color`. Toggle "confirm deletions" from settings gates every delete.

### 4.1 Card View (`menu/dialogs/card/view.dart`, 404 L)
`CardViewMenu(tagSave, onMove)`. Title = card name (max 3 lines). Body rows (each with a copy `IconButton`):
- `uid: <uid>` - for LF, `uid` derived via `getLFCardFromUID(tag,uid).toViewableString()` (view.dart:41-47); copy copies raw `currentSavedCard.uid`.
- `tag_type: <chameleonTagToString(tag)>`.
- **HF only**: `sak: <hex>` (copies `unavailable` if sak==0), `atqa: <hexSpace>` (or `unavailable`).
- **MifareClassic only**: `Export to dictionary` button - enabled iff `mfClassicGetKeysFromDump(data).isNotEmpty`; opens `DictionaryExportMenu(keys)` (§7.2).
- **MifareUltralight only**: `ultralight_version` + `ultralight_signature` rows (hexSpace or `unavailable`), each copyable.

Action bar (`Wrap`, 242-400):
- **Move** (`Icons.drive_file_move_outline`, tooltip `move_card`): `await onMove(card)` then `_refreshCardData()`.
- **Edit** (`Icons.edit`): opens `CardEditMenu(tagSave)` then refresh.
- **Duplicate** (`Icons.copy_all`): `CardSave.fromJson(toJson())`, new `Uuid().v4()` id, name `"<name> (<copy>)"`, add, save, pop.
- **Dump editor** (`Icons.edit_document`) - only if MifareClassic|Ultralight: `Navigator.push(DumpEditor(cardSave, onSave))`. onSave rebuilds `CardSave` with new `data`, replaces in storage, `changesMade()`, pops view (see §5).
- **Download** (`Icons.download_rounded`): dialog `select_save_format`; if Classic → `save_as(".bin")` button (`saveTag(card, ctx, true)`); always `save_as(".json")` (`saveTag(...,false)`).
- **Delete** (`Icons.delete_outline`): if `getConfirmDelete()` → `ConfirmDeletionMenu(name)`; on confirm remove by id, `setCards`, `changesMade`, pop.
- **OK** (`ElevatedButton`): pop.

### 4.2 Card Create (`menu/dialogs/card/create.dart`, 446 L)
`CardCreateMenu(folderId?)`. `AlertDialog(create_card)`. Form (autovalidate on interaction):
- **Name** `TextFormField`, validator `validateName`, with a **color-picker** icon prefix (`Icons.credit_card` for HF, `Icons.wifi` for LF, tinted `currentColor`) → opens `ColorPicker` dialog with `reset_default`(→`Colors.deepOrange`)/`cancel`/`ok`.
- **Tag type** `DropdownButton<TagType>` - items = `getTagTypesByFrequency(hf) + getTagTypesByFrequency(lf)`; default `TagType.mifare1K`; ignores `TagType.unknown`.
- Visible when type≠unknown:
  - **UID** `TextFormField`, `hexFormatter`, validator `validateUid(...,isCreate:true)`.
  - **HF only** (freq≠lf): **SAK** (validateHex exactBytes:1, required), **ATQA** (exactBytes:2, required), **ATS** (validateHex optional).
    - **Ultralight only**: `ultralight_version` (exactBytes:8), `ultralight_signature` (optional).
  - **HID Prox only** (`type==hidProx`): `DropdownButton<int>` 1..30 (`getNameForHIDProxType`), `facility_code` (int 0..4294967295), `issue_level` (0..255), `OEM` (0..65535) - all `digitsOnly`.
- Actions `cancel` / `create`. On create (370-442): validate; build UID string - for HID via `HIDCard(...).toString()`, else `bytesToHexSpace(hexToBytes(uid))`. LF → sak=0, atqa/ats empty, blocks=[]. Ultralight → `generateMifareUltralightBlocks()` (88-107: `mfUltralightGenerateFirstBlocks` + CC block `E1 10 <size/8> 00` + zero pages). Classic → `generateMifareClassicBlocks()` (45-86: zero data blocks + trailer `FF FF FF FF FF FF FF 07 80 69 FF FF FF FF FF FF` per sector, block0 = `mfClassicGenerateFirstBlock(uid,sak,atqa)`). Add to cards, save, pop.

### 4.3 Card Edit (`menu/dialogs/card/edit.dart`, 548 L)
`CardEditMenu(tagSave, isNew=false)`. Same fields as Create, prefilled from `tagSave` in `initState` (52-77). Adds **Ultralight counters**: `ultralightCounterControllers` sized by `mfUltralightGetCounterCount(type)` (146-160); each `TextFormField` validator `validateIntRange(0..16777215, emptyMessage:counter_value_empty)` shown only if `mfUltralightHasCounters`.
- Type dropdown uses `getTagTypes()` (all types); on change re-inits counters.
- HID fields prefilled via `initHIDFields()` (162-169) if `tag==hidProx`.
- **Data-change guard** (79-83, 465-477): tracks `originalUid/Sak/Atqa`. On save, if `hasDataChanged() && canUpdateSavedCardData()` (only Classic/Ultralight with non-empty data) → confirm dialog `update_data_title`/`update_data_message` (no/yes). Yes → `updateSavedCardData` regenerates block0 (Classic: `mfClassicGenerateFirstBlock`) or first blocks (Ultralight: `mfUltralightGenerateFirstBlocks`).
- Save (457-544): validate; build `CardSave` (keeping `id`,`folderId`); LF keeps original sak; write counters into `extraData`; replace by id; save; pop.

---

## 5. DUMP EDITOR (`menu/pages/dump_editor.dart`, 1986 L)

`DumpEditor(cardSave, onSave)` - full-page hex editor for MifareClassic/Ultralight. Pushed as a route from Card View.

**Model & init** (33-115): `isUltralight` from tag; `bytesPerBlock` = 4 (UL) or 16 (Classic); `hexCharsPerBlock` = 8/32. One `TextEditingController` per sector (Classic) or a single controller (Ultralight). Each block rendered as space-separated uppercase hex (`_formatHexData`, 117-125). `initialTexts` snapshot for dirty tracking (`_onDataChanged`, 127-138). Classic layout uses `mfClassicGetSectorCount/BlockCountBySector/FirstBlockCountBySector`.

**Text input engine** (140-424): custom `TextInputFormatter` combining `FilteringTextInputFormatter.allow(RegExp(r'[0-9A-Fa-f\n\s-]'))` + `_handleTextInput`. Two modes toggled by an **Insert mode** `Switch` (in legend, 1776):
- `_handleInsertMode` (149-241): inserts hex chars into the clean line, clamps to `hexCharsPerBlock`, re-spaces, recomputes cursor.
- `_handleOverwriteMode` (243-350): overwrites in place; extends at end up to block limit.
- `_handleDeletion` (352-359) blocks deletions from changing text length (keeps fixed width), only moves cursor.
- `_processTextWithSpacing` (361-424): re-spaces every line, clamps length.

**Rendering** (1391-1555): `_buildAdaptiveEditor` auto-sizes font 16→10px (`_guessOptimalFontSize`) so the longest line fits. `_buildOriginalEditor` overlays a transparent `TextFormField` on top of a highlighted `Text.rich` (block numbers via `_buildBlockNumbers` left gutter `NNN: `, colored bytes via `_buildHighlightedTextOnly` using `MifareClassicDumpHighlighter`/`MifareUltralightDumpHighlighter`). Each sector shows title `sector: N` (or `dump` for UL).

**Save** (`_saveDump`, 471-517): validates each controller (`_validateDataForSave`, 426-452 - UL: 8 hex chars/line; Classic: exact block count per sector, 32 hex chars). Lines of all-dashes (`--...`) are left unchanged (preserve unknown blocks). Errors → `_showErrorDialog(invalid_data_in_block/sector N)`. Calls `widget.onSave(updatedDump)`, clears dirty, pops.

**Cancel/discard** (`_cancelEdit`, 519-545 + `PopScope` 1873): if dirty, dialog `unsaved_changes`/`unsaved_changes_message` with `cancel`/`discard`.

**Toolbar** (bottom, `Wrap`, 1941-1978):
- **NDEF** (`Icons.nfc`, label `ndef`) - only if `_currentNdefContainer()!=null` (Classic/UL with a valid NDEF): opens `NdefEditorPage` (§6.3). onSave writes message back into the dump (`container.writeMessage`) and re-applies to controllers.
- **ASCII** (`ascii`): `_showAsciiView` (644-753) - per-block editable ASCII fields (`0x20-0x7E`, maxLength=`bytesPerBlock`), converts back to hex on save.
- **Classic only - ACL** (`acl`): `_showAccessConditions` (755-1050) - decodes sector trailer access bits (`accessConditionValues`), shows per-block Read/Write/Inc/Dec dropdowns (`A`/`B`/`A/B`/`-`) and trailer KeyA/ACL/KeyB Read/Write dropdowns; validates & re-encodes (`encodeAccessConditions`) into the trailer bytes 6-8.
- **Classic only - VALUE** (`value`): `_showValueBlocks` (1052-1177) - lists detected value blocks (`valueBlockToInt`/`valueBlockAddress`), editable value (−0x80000000..0x7FFFFFFF) + address (0..255), re-encodes via `intToValueBlock`.
- **Compare** (`compare`): `_startCompare` (1329-1381) - pick another card of *same tag* (`no_dumps_to_compare` if none); enters compare mode showing per-byte diffs in red (`_buildCompareView`, 1557-1654). Exit via `exit_comparison` button.

**Color legend** (`_buildColorLegend`, 1698-1797): Classic legend = UID/Value block/Key A/Key B/Access conditions/Block index; UL = UID/BCC/Lock bytes/Password/Block index. Plus Insert-mode switch. Compare mode shows a `Difference` legend.

**AppBar** (1881-1899): title `dump_editor`; leading back + `Icons.close` (both `_cancelEdit`); `Icons.save` (hidden in compare mode).

---

## 6. STANDALONE EDITORS & PAGES

### 6.1 QR Code Settings (`menu/dialogs/qr/settings.dart`, 185 L)
`QRCodeSettings` - returns `{splitSize, errorCorrection}` or empty. Two `Slider`+`TextFormField` pairs:
- **Split size**: slider min 1 max 2048, field validator `validateIntRange(1,2048)`, tooltip `split_size_tooltip`.
- **Error correction**: slider min 0 max 3, field validator `validateIntRange(0,3)`, tooltip `error_correction_tooltip`. Mapping: L=1, M=0(default), Q=3, H=2.
- **Cross-clamping** (43-95, 164-172): EC level caps split size - L/M→2048, Q(2)→1200, H(3)→1600. Both sliders enforce mutually.
- Live **`QrImageView`** preview using dummy data `List.filled(splitSize,"a")`, white bg, 40px pad.
- Actions `cancel` / `ok`.

### 6.2 QR Code Import (`menu/dialogs/qr/import.dart`, 88 L)
`QrCodeImport` - multi-chunk scanner returning assembled JSON. State: `shasum, qrCodeChunks, resultingJson, currentChunk`. Title `qrCodeImport`. Single action button that:
- If `qrCodeChunks == currentChunk` → pop with `resultingJson`.
- Else opens `QrCodeScanner` dialog. If scanned data contains `"Info":"Chameleon Ultra GUI Settings"` → it's the header: parse `sha256`+`chunks`, reset. Else append to `resultingJson`, `currentChunk++`.
- Button label: `startScanning` / `finishImport` / `scan_next_qr_code(current+1, total+1)`. Shows a `Icons.check` tooltip `checksumOk` when `sha256(utf8(resultingJson)) == shasum`.

### 6.3 NDEF Editor (`menu/pages/ndef_editor.dart`, 441 L)
`NdefEditorPage(records, capacity, mappingName, parseWarning?, onSave)`. Full-page list of NDEF records. Uses `NdefCodec`/`NdefRecord` from `helpers/ndef.dart`.
- Header `Card` shows `mappingName`, `size / capacity bytes`, error icon if over capacity.
- If `parseWarning` → warning card "The existing NDEF message could not be parsed."
- Records list: each `Card`/`ListTile` - leading index avatar, title = kind label (`Text/URI/MIME/External/Raw`, `_kindLabel` 50-56), subtitle = display value or type name. Trailing: **move up** (`Icons.arrow_upward`, disabled at 0), **move down** (disabled at last), **delete** (`Icons.delete_outline`). Tap → edit.
- **FAB** `Add record` (`Icons.add`) → `_editRecord()`.
- **Edit-record dialog** (`_editRecord`, 58-279): `DropdownButtonFormField<NdefRecordKind>` (Text/URI/MIME/External/Raw). Fields per kind:
  - Text: `Language code` (maxLength 63, default `en`) + multiline `Text`.
  - URI: single `URI` field.
  - MIME/External: `MIME type`/`External type` (defaults `text/plain` / `example.com:type`) + `Payload`.
  - Raw: `TNF` dropdown 0..7, `Type (hex)`, `Payload (hex)` (hex-filtered).
  - Actions `cancel`/`save` - builds `NdefRecord` via named constructors; dialog error caught & shown. Controllers disposed 300ms after close (comment 264-266).
- **Save** (`_save`, 281-293): `NdefCodec.encodeMessage(records)`; if `> capacity` → inline error; else `onSave(message)`, pop.
- **Discard guard** (`PopScope` + `_confirmDiscard`, 295-323): `unsaved_changes`/`unsaved_changes_message` dialog (`cancel`/`discard`). Dirty tracked by byte-comparing `encodeMessage` vs `initialMessage`.
- AppBar: title literal `NDEF Editor`; `Icons.save` action.

### 6.4 Changelog View (`menu/pages/changelog_view.dart`, 284 L)
`ChangelogView` - `AlertDialog(changelog)`, 400px tall `FutureBuilder(fetchChangelogs(buildNumber))` (github.dart:306). Each entry = `_buildChangelogCard`:
- Version title (orange if `"Unreleased"` + `latest_commits` chip).
- Date (`d/m/y`) or `latest_commits_from_main_branch`.
- Changes list bulleted; the entry matching the running build shows a green `your_version` chip (matched via `commitHashes[change]==currentVersionCommit`).
- Rich text (`_buildRichText`, 205) linkifies URLs and `@mentions` (→ github.com/user).
- Footer `view_commits` / `view_full_release` → `launchUrl(changelog.url)`.
- States: loading spinner; error → `ErrorPage`; empty → `no_changelogs_available`. Action `ok`.
- Data: `fetchChangelogs` (github.dart:306-340) fetches GUI repo releases, prepends an "Unreleased" entry (commits between latest release `target_commitish` and `main` via `/compare`, github.dart:342-420), then published releases parsed by `ChangelogEntry.fromGitHubRelease` (parses `## What's Changed` section, github.dart:261-287). `findCurrentVersionCommit` (422) maps buildNumber→`head_sha` via workflow runs (`publish-app.yml` on Android/macOS/iOS, else `build-app.yml`).

### 6.5 Mfkey32 (`menu/pages/mfkey32.dart`, 244 L)
`Mfkey32Menu` - full-page key recovery from on-device detection nonces. Opened from Slot Edit (§4-slot) when detection has nonces.
- `initState` → `getMf1DetectionStatus()` = `(isMf1DetectionMode(), getMf1DetectionCount())`.
- Title literal `Mfkey32`. Body: header `recover_keys_via("Mfkey32")`; a button labeled `recover_keys_nonce(detectionCount)` (disabled if 0 or while loading).
- **`handleMfkeyCalculation`** (58-128): `getMf1DetectionCount()` then `getMf1DetectionResult(count)`; nested loop over uid→block→keyType→nonce pairs (i,j); builds `Mfkey32Dart(uid,nt0,nt1,nr0Enc,ar0Enc,nr1Enc,ar1Enc)`; `recovery.mfkey32(mfkey)`; collects 6-byte keys; de-dups by `Object.hashAll`; displays `block N key K: <hex>` rows (each copyable) + `outputUid`. Progress via `LinearProgressIndicator` (`progress` 0..100).
- After completion `saveKeys=true` → **Save recovered keys** button opens `DictionaryExportMenu(defaultName:outputUid, keys)` (§7.2).
- Loading spinner + `recovery_in_progress` text while running.

### 6.6 Logs Viewer (`menu/pages/logs_viewer.dart`, 76 L)
`LogsViewerPage` - `Scaffold(logs)`. `ListView.builder` over `sharedPreferencesProvider.getLogLines()`; each line = `SelectableText`, monospace 12px, in a bordered container. Read-only.

---

## 7. DICTIONARY DIALOGS

Model `Dictionary` (`sharedprefsprovider.dart`): `id, name, color, keys(List<Uint8List>), folderId`. `Dictionary.fromString(text, name, color)` parses newline-separated hex; `.toString()` joins; `.toFile()` bytes; `getDictionaries({keyLength})` can filter by key byte-length.

### 7.1 Dictionary Edit (`menu/dialogs/dictionary/edit.dart`, 175 L)
`DictionaryEditMenu(dictionary, isNew=false)`. Title `create_dictionary`/`edit_dictionary`. Form:
- **Name** `TextFormField`, validator `validateName`, prefix `Icons.key` (tinted `currentColor`) → color-picker dialog (`reset_default`→deepOrange / `cancel` / `ok`).
- **Keys** multiline `TextFormField` (RobotoMono 16), label `keys`, hint `enter_dict_keys`.
- Actions `cancel` / `save`. Save (135-171): validate; `Dictionary.fromString(keysText, name, color)`; assign `id` (`Uuid().v4()` if new else keep) + `folderId`; if `keys.isEmpty` abort silently; append (new) or replace by id; `setDictionaries`; pop.

### 7.2 Dictionary Export (`menu/dialogs/dictionary/export.dart`, 242 L)
`DictionaryExportMenu(defaultName, keys)` - used after key recovery (Mfkey32, HF sniff, card view). Title `save_recovered_keys`, content `save_recovered_keys_where`. Three `ElevatedButton` actions:
- **Save to file** (`save_recovered_keys_to_file`): `convertKeysToDictionaryFile` (dedup by hash, uppercase hex lines) → `getDictionaryName()` prompt → `FilePicker.saveFile('<name>.dic')`.
- **Add to existing** (`add_recovered_keys_to_existing_dict`): `dictionarySelectDialog` → `showSearch(DictSearchDelegate)` (171-242) listing dictionaries with key counts; tapping a result appends keys (`dict.keys.addAll`), `setDictionaries`, `changesMade`.
- **Create new** (`create_new_dict_with_recovered_keys`): name prompt → `Dictionary(name, Colors.blue, dedupKeys)` → opens `DictionaryEditMenu(isNew:true)`.
- `getDictionaryName()` (57-92): dialog `enter_name_of_dictionary` with `ok`/`cancel` (cancel clears text).

### 7.3 Dictionary View (`menu/dialogs/dictionary/view.dart`, 205 L)
`DictionaryViewMenu(dictionary, onMove)`. Title = name. Body: a `Card` `key_count: N` (copyable), then a scrollable `SelectableText` of all keys (RobotoMono 16) with a `copy_all_keys` `TextButton.icon`. Actions: **Move** (tooltip `move_dictionary`, `onMove` then refresh), **Edit** (`DictionaryEditMenu`), **Download** (`FilePicker.saveFile('<name>.dic', toFile())`), **Delete** (confirm-gated by `getConfirmDelete` → `ConfirmDeletionMenu`, then remove by id).

---

## 8. TOOLS (launched from `gui/page/tools.dart`)

`ToolsPage` (163 L) is a responsive grid (`AlignedGridView`, 2 cols ≥700px else 1). Tool list (43-70): Dictionary Download, T55XX Password Cleaner (device-required), LF Sniffing, HF Sniffing, Mifare Classic Gen4 (device-required, `onPressed:null` → shows `wip` badge). Each `ElementButton` (icon, name, description) opens its `onPressed` widget via `showDialog`. Device-required tools show a `device_required` badge and are disabled when `!connector.connected`.

### 8.1 Dictionary Download (`menu/tools/dictionary_download.dart`, 159 L)
`DictionaryDownloadMenu` - `AlertDialog(dictionary_download)`. Hardcoded list of 5 remote dictionaries (82-103):
| Name | URL |
|---|---|
| Proxmark3 (Mifare Classic) | `raw.githubusercontent.com/RfidResearchGroup/proxmark3/.../mfc_default_keys.dic` |
| Proxmark3 (T55XX) | `.../t55xx_default_pwds.dic` |
| Proxmark3 (Mifare Ultralight C) | `.../mfulc_default_keys.dic` |
| Proxmark3 (Mifare Plus) | `.../mfp_default_keys.dic` |
| Flipper Zero Unleashed (Mifare Classic) | `raw.githubusercontent.com/DarkFlippers/unleashed-firmware/.../mf_classic_dict.nfc` |

Each row: name + download `ElevatedButton` (spinner while downloading). `_downloadDictionary` (37-75): `http.get`; on 200 → `Dictionary.fromString(body, name)`; abort if `keys.isEmpty`; append + `setDictionaries` + `changesMade`; SnackBar `dictionary_download_success(name)`. Errors swallowed. Action `ok`.

### 8.2 T55XX Password Cleaner (`menu/tools/t55xx_password_cleaner.dart`, 314 L)
`T55XXPasswordCleanerMenu` - device-required. Brute-forces a T5577 password from a dictionary and resets it. Title `t55xx_password_cleaner`.
- Orange warning card `t55xx_password_cleaner_warning`.
- **Dictionary dropdown** - only 8-byte-key dictionaries (`getDictionaries(keyLength:8)`); if none → red card `no_t55xx_dictionaries` + `download_dictionaries` button (opens `DictionaryDownloadMenu`).
- **New password** `TextFormField`, default `"20206666"`, `hexFormatter`, validator `validateHex(exactBytes:4, required)`.
- Progress (while processing): `LinearProgressIndicator(currentKeyIndex/totalKeys)`, `N / M`, `trying_password: <key>`.
- Actions: while processing → `cancel` (sets `isProcessing=false`, cooperative stop); else `cancel`(pop) + `start_password_reset` (enabled iff dict selected and new key is 8 chars).
- **`_startPasswordReset`** (41-111): `targetUID="DE AD BE EF FF"`. For each key: `writeEM410XtoT55XX(targetUID, newKey, [key])` then `readEM410X()`; if read back == targetUID → found: `foundPassword`, success dialog `password_reset_success(password)`. Loop-broken by `isProcessing`. Exceptions per-key `continue`. If exhausted → `password_reset_failed`/`password_reset_no_match`. Fatal error → `ErrorPage`.

### 8.3 LF Sniffing (`menu/tools/lf_sniffing.dart`, 1671 L)
`LfSniffingMenu` - `AlertDialog(lf_sniffing)`, responsive sized (≤92% width, ≤82% height). Uses helpers `helpers/lf_sniff.dart` (`LfSniffCapture`, `decodeLfManchester`, `kLfClockDivisors`, `buildLfHexRows`, `levelGlyph`).

**Capability check** (`_loadCapabilities`, 55-77): `getDeviceCapabilities()` contains `ChameleonCommand.lfSniff.value`(3031)? Sets `_capabilitySupported`. Banner (693-745): if no device → `sniff_device_required_hint`; if unsupported → `lf_sniff_firmware_unsupported`.

**Header controls** (586-666): 
- **Timeout** `TextFormField` default `2000`, `digitsOnly`, validator `validateIntRange(1,10000)`, label `lf_sniff_timeout`, helper `lf_sniff_timeout_help`.
- **Capture** `FilledButton.icon(Icons.sensors, lf_sniff_capture)` - disabled while capturing/unsupported/disconnected.
- **Save to file** (`Icons.download`) → `_exportCapture` (`.bin` of raw samples).
- **Load file** (`Icons.upload_file`, `lf_sniff_load_file`) → `_loadCaptureFromFile`.
- **Copy hex** (`Icons.copy_all`, `lf_sniff_copy_hex`) → clipboard of `_hexPreview` (max 512 bytes).

**Capture** (`_captureLfSamples`, 83-152): validate; `if (!isReaderDeviceMode()) setReaderDeviceMode(true)`; `lfSniff(timeoutMs)` → cmd `lfSniff`(3031) with `[timeoutMs>>8, timeoutMs&0xFF]`, timeout `timeoutMs/1000+2`s (chameleon.dart:694). Response status `0x40`→data, `0x41`→empty, else throw. Empty → `lf_sniff_no_samples`. Else `LfSniffCapture.fromSamples`, decode, status `lf_sniff_capture_done(count)`. Errors: `_isFirmwareUnsupportedError` (contains `0x67`/`0x69`) → mark unsupported; else `_errorMessage`.

**Tabs** (`DefaultTabController(4)`, 502-550):
1. **Summary** (`lf_sniff_summary`, 747-851): cards Samples (count + `lf_sniff_duration_value`), Range (min-max hex + mean), Gaps (count + threshold), and a Modulation panel (type via `_modulationLabel`: none/insufficient/manchester/ask-nrz/biphase/fsk-mixed, dynamic range, nearest clock, half/full period µs).
2. **Waveform** (`lf_sniff_waveform`, 853-934): `_LfWaveformSurface` (CustomPaint `_LfWaveformPainter`, 1578-1671 - draws grid lines, gap shading, mean/threshold reference lines, sample path). Chips mean/threshold/samples. **Expand** button (`Icons.open_in_full`) → `_openWaveformViewer` (461): fullscreen page on narrow screens (`_LfWaveformFullscreenPage`), else `_showWaveformDialog`. Zoom slider 1..32× (`_LfWaveformZoomControls`, 1524).
3. **Decode** (`lf_sniff_decode`, 936-1092): **Clock divisor** field (default `64`, validator = must be in `kLfClockDivisors`), **Invert** `FilterChip`, **Refresh decode**, **Copy bits**. Shows bit count, threshold hex, hex preview, and grouped 64-wide bitstream (`_groupBits`). `decodeLfManchester(samples, clockDivisor, invert)`.
4. **Hex** (`lf_sniff_hex`, 1094-1124): `HexViewer` (max 512 bytes) with per-byte color (`_lfSampleColor` 333-363: warmup/low/carrier/peak/error-gap) and trailing level-glyph column. Legends: color scale + glyph legend (`_ .-+ oO#`).

Action `close`.

### 8.4 HF Sniffing (`menu/tools/hf_sniffing.dart`, 1199 L)
`HfSniffingMenu` - `AlertDialog(hf_sniffing)`. Uses `helpers/hf_sniff.dart` (`HfSniffCapture`, `HfSniffNonceGroup`, `buildMfkey64Command/32Command`, `buildHfSniffRawHexPreview`) and `recovery` (`mfkey64`, `mfkey32`).

**Capability** (68-90): `getDeviceCapabilities()` contains `hf14aSniff`(2020)? Same banners.

**Header** (456-547): **Timeout** default `5000`, validator `validateIntRange(1,30000)`, helper `hf_sniff_timeout_help`. **Capture** `FilledButton(Icons.radar, hf_sniff_capture)`. **Save to file** (`.trace` raw), **Load file**, **Copy hex** (`_rawHexDump`).

**Capture** (`_captureFrames`, 92-157): `if (isReaderDeviceMode()) setReaderDeviceMode(false)` (opposite of LF); `hf14aSniff(timeoutMs)` → cmd `hf14aSniff`(2020) `[timeoutMs>>8, &0xFF]`, timeout `timeoutMs/1000+5`s (chameleon.dart:715). Status `0x68`/`0x00`→data, `0x01`→empty. `HfSniffCapture.fromChameleonBytes`. Empty frames → `hf_sniff_no_decoded_frames`. Load-from-file uses `HfSniffCapture.fromProxmarkTrace`.

**Tabs** (`DefaultTabController(5)`, 404-442):
1. **Summary** (631-714): cards Frames (total + reader/card), UID, Protocol (ISO14443-4 if RATS else -A). Detail panel rows: reader/card frame counts, UID, protocol, auth requests (`keyType block N`), AIDs, ATC, amount (`minor/100`), auth type (ARQC/TC), end (HALT/DESELECT).
2. **Frames** (716-740): transcript bubbles (`_buildFrameTranscriptEntry`, 1015) - reader→card left/primary, card→reader right/tertiary, `#idx route bitLength`, hex, annotation label.
3. **Nonces** (742-823): per `HfSniffNonceGroup`: header `hf_sniff_nonce_group_value(block,keyType,uid)`, each exchange `hf_sniff_nonce_exchange_value(i,nt,nr,ar)`, `buildMfkey64Command`, and (if `canRecover`) `buildMfkey32Command`, plus copy buttons.
4. **Recovery** (825-978): **Recover all** `FilledButton(Icons.key)` (iterates recoverable groups). Per group (`_buildRecoveryGroup`): if not recoverable → `hf_sniff_nonce_single` + mfkey64 command. Else **Recover key** button (`_recoverGroup`, 248-322: tries `mfkey64` first - `Mfkey64Dart(uid,nt,nrEnc,arEnc,atEnc)` - then `mfkey32`; `_kNoKey=0xFFFFFFFFFFFFFFFF` = failure → `hf_sniff_recovery_failed`). On success: shows method + 12-hex key, **Copy key** and **Save recovered keys** (`DictionaryExportMenu`, defaultName `hf-sniff-<uid>`).
5. **Raw** (980-1013): `HexViewer` (max 1024 bytes) + `SelectableText` hex dump.

Action `close`.

---

## 9. SMALL DIALOGS

### 9.1 Confirm Delete (`menu/dialogs/confirm_delete.dart`, 50 L)
`ConfirmDeletionMenu(thingBeingDeleted)` - `AlertDialog(confirm_deletion)`, content `confirm_deletion_text("\"<thing>\"")`, actions `cancel`(pop false) / `delete`(pop true). Callers gate on `getConfirmDelete()` and check `confirm == true`.

### 9.2 Manual Connect (`menu/dialogs/manual_connect.dart`, 66 L)
`ManualConnect` - `AlertDialog(connect_manually)`. A `TextField` (label `port`, hint `port_hint`). Actions `cancel` / `connect`. Connect (48-61): if empty return; `connector.connectSpecificDevice(portText)`; new `ChameleonCommunicator(log, port: connector)`; `connector.pendingConnection = false`; `changesMade()`. Default `type = ChameleonDevice.ultra`.

---

## 10. SLOT DIALOGS

Device slots 0-7, each has HF + LF sub-slots. All operations go straight to the device (no local persistence).

### 10.1 Slot Settings (`menu/dialogs/slot/settings.dart`, 300 L)
`SlotSettings(slot, refresh)`. On open `fetchInfo` (34-66): `activateSlot(slot)`; `getSlotTagName(slot, hf/lf)` (empty→`empty`); `getEnabledSlots()[slot]`; `getSlotTagTypes()[slot]`. Loading spinner; error → `performDisconnect()`+`ErrorPage`.
- Title row `slot_settings` + **Export** `IconButton(Icons.download)` - enabled iff `slotTypes.notMatch()` (slot has data); opens `SlotExportMenu`.
- **HF section** (127-210) and **LF section** (212-295), each with: label `hf:`/`lf:`, **Edit** (`Icons.edit` → `SlotEditMenu`), **Clear** (`Icons.clear_rounded`, confirm-gated → `deleteSlotInfo(slot,freq)` + `setSlotTagName(slot,empty,freq)` + `saveSlotData()`, reset local to `empty`/`unknown`), **Enable** `Switch` (`enableSlot(slot,freq,value)`), and a disabled `OutlinedButton` showing the name.

### 10.2 Slot Edit (`menu/dialogs/slot/edit.dart`, 945 L)
`SlotEditMenu(name, isEnabled, slotType, frequency, slot, update)`. `AlertDialog(edit_slot_data)`. The heaviest slot dialog.
- **Name** field (`validateName`).
- **Type** `DropdownButton<TagType>` = `getTagTypesByFrequency(frequency) + TagType.unknown`.
- **`updateInfo()`** (79-184, run via `FutureBuilder`): when type changes, `activateSlot(slot)` and reads the current emulated data from device per type:
  - EM410X → `getEM410XEmulatorID`; HID → `getHIDProxEmulatorID` (fills hidType/fc/il/oem); Viking/Pac/IoProx/Idteck → their `get...EmulatorID`.
  - Classic/Ultralight → `mf1GetAntiCollData` (uid/sak/atqa/ats). Classic also: `getMf1EmulatorSettings`, detection count (`getMf1DetectionCount` if enabled), `getMf1PrngType` (nullable). Ultralight also: `mf0EmulatorGetVersionData/SignatureData`, counters (`mf0EmulatorGetCounterData`), `mf0NtagGetEmulatorConfig`, `mf0NtagGetDetectionCount`.
- **Common fields** (UID, and for HF: SAK/ATQA/ATS; Ultralight: version/signature/counters) - same validators as Card Edit.
- **Mifare Classic emulator settings** (460-659): Gen1a (yes/no → `setMf1Gen1aMode`), Gen2 (`setMf1Gen2Mode`), PRNG type (`prng_type_static/weak/hard` → `setMf1PrngType`), Use-from-block anti-coll (`setMf1UseFirstBlockColl`), **Collect nonces (Mfkey32)** detection toggle (`setMf1DetectionStatus`) - if enabled & count>0 shows **Recover keys** button → pushes `Mfkey32Menu`; if count==0 → hint `present_cham_reader_keys`; if disabled → `ena_coll_recover_keys`. Write mode (`normal/decline/deceive/shadow` → `setMf1WriteMode`).
- **Mifare Ultralight emulator settings** (660-846): Gen2 (`mf0SetMagicMode`), Password detection (`mf0NtagSetDetectionEnable`) - if enabled shows `passwords_detected: N` + **View passwords** (`mf0NtagGetDetectionLog(0)` → read-only dialog `detected_passwords`). Write mode (`mf0NtagSetWriteMode`).
- **HID Prox fields** (850-915) as in Card Edit.
- Actions `cancel` / `save`. **`save()`** (186-269): `activateSlot(slot)`; if type changed → `setSlotType(slot,type)` and, unless staying within Classic↔Classic or UL↔UL, `setDefaultDataToSlot(slot,type)`. Then write per-type emulator ID / anti-collision (`setMf1AntiCollision`) + UL version/signature/counters. Finally `setSlotTagName(slot,name,frequency)` + `saveSlotData()`; call `update(name,frequency,type)`.

### 10.3 Slot Export (`menu/dialogs/slot/export.dart`, 338 L)
`SlotExportMenu(names, enabledSlotInfo, slotTypes)`. `AlertDialog(export_slot_data)`.
- Frequency toggle (`ToggleButtonsWrapper`) built from available HF/LF (`hf`/`lf`); default first available.
- **`rebuildCardSaveFromSlot(freq)`** (36-177): reads full slot content into a `CardSave`. LF → per-type emulator ID. HF → `mf1GetAntiCollData`; Ultralight → read all pages (`mf0EmulatorReadPages`) + version/signature/counters; Classic → read all blocks (`mf1GetEmulatorBlock` in chunks of 16) into 16-byte blocks.
- Three actions:
  - **Save to file** (`save_to_file`): `FilePicker.saveFile('<name>.json', utf8(cardSave.toJson()))`.
  - **Export to new card** (`export_to_new_card`): name prompt dialog → append new `CardSave` to storage.
  - **Update saved card** (`update_saved_card`): `showSearch(CardSearchDelegate, filter: hf/lf)`; on tap (`onTap`, 179-212) rebuilds from slot and overwrites the matching saved card's uid/tag/sak/atqa/ats/data.

---

## 11. Communicator command reference (settings/DFU/sniff subset)

From `bridge/chameleon.dart`; payload = bytes after command header. `skipReceive` = fire-and-forget.

| Method (line) | Cmd (id) | Payload | Notes |
|---|---|---|---|
| `enterDFUMode` (811) | enterBootloader(1010) | - | skipReceive |
| `factoryReset` (815) | factoryReset(1020) | - | skipReceive, erases all |
| `saveSettings` (819) | saveSettings(1013) | - | persist to flash |
| `resetSettings` (823) | resetSettings(1014) | - | |
| `setAnimationMode` (827) | setAnimationMode(1015) | `[value]` | |
| `setButtonConfig` (969) | setButtonPressConfig(1027) | `[type,mode]` | |
| `setLongButtonConfig` (980) | setLongButtonPressConfig(1029) | `[type,mode]` | |
| `getSleepTimeout` (985) / `setSleepTimeout` (990) | 1039 / 1040 | - / `[seconds]` | wake time 5-60 |
| `clearBLEBoundedDevices` (995) | bleClearBondedDevices(1032) | - | skipReceive |
| `setBLEConnectKey` (1004) | bleSetConnectKey(1030) | utf8(pin) | 6 digits |
| `setBLEPairEnabled` (1014) | bleSetPairEnable(1037) | `[0/1]` | |
| `getDeviceSettings` (1105) | getDeviceSettings(1034) | - | 13-14 byte reply (§3) |
| `getDeviceCapabilities` (1128) | getDeviceCapabilities(1035) | - | u16 list of supported cmd ids |
| `isReaderDeviceMode` (211) / `setReaderDeviceMode` (216) | getDeviceMode(1002) / changeDeviceMode(1001) | - / `[0/1]` | reader vs emulator |
| `lfSniff` (694) | lfSniff(3031) | `[to>>8, to&0xFF]` | status 0x40 data / 0x41 empty |
| `hf14aSniff` (715) | hf14aSniff(2020) | `[to>>8, to&0xFF]` | status 0x68/0x00 data / 0x01 empty |

Firmware-unsupported status bytes surfaced to UI: `0x67`, `0x69` (both sniff tools treat as "firmware doesn't support this").

---

## 12. Native-rebuild checklist (parity gotchas)

1. **DFU is Nordic Secure DFU** over SLIP (USB) or raw (BLE). Reuse exact object-type order: 0x01 init (`application.dat` protobuf), 0x02 image (`application.bin`). Validate signed init + hash-reversed comparison before flashing.
2. **BLE vs USB divergence**: no SLIP on BLE; 20-byte writes off-macOS; 250ms sleeps on Apple BLE; 50 retries on iOS. USB preferred when both present; reject two same-transport devices in DFU.
3. **Firmware source order**: nightly (GitHub Actions via nightly.link, must match `head_repository_id==581338100` on `main`) → prerelease GitHub Releases → user-picked zip. All calls must degrade gracefully (only `message` errors surface).
4. **Progress** is a single `double? 0..1` fed from DFU `callback(progress)/100`; the FlashingPage + BottomProgressBar are the only UI.
5. **Every device settings write is followed by `saveSettings()`** - omitting it loses the change on reboot.
6. **Confirm-delete gating** is global (`getConfirmDelete`); replicate in every delete path (cards, dictionaries, slots).
7. **Sniff mode prerequisite**: LF sniff needs reader mode ON; HF sniff needs reader mode OFF. Set before capture.
8. **Dump editor fixed-width text engine** is bespoke (insert/overwrite modes, dash-preservation of unknown blocks) - not a plain text field. ACL/value-block/ASCII/NDEF sub-editors round-trip through the same controllers.
9. **Capability gating**: tools check `getDeviceCapabilities()` for their command id and show a firmware-unsupported banner rather than failing silently.
10. **i18n**: ~all labels are keys; the DFU page title `'Chameleon DFU'`, `'Mfkey32'`, `'NDEF Editor'` are the few hardcoded literals.

Key source files: `helpers/flash.dart`, `helpers/github.dart`, `bridge/dfu.dart`, `gui/page/flashing.dart`, `gui/page/settings.dart`, `gui/menu/dialogs/chameleon_settings.dart`, `gui/menu/pages/dump_editor.dart`, `gui/menu/tools/{lf_sniffing,hf_sniffing}.dart`, `helpers/definitions.dart`, `bridge/chameleon.dart`.

---


# GUI - Recovery / Write / Emulate Internals

I now have complete coverage. Below is the parity-grade reference.

---

# Chameleon Ultra GUI - Domain Logic Parity Reference

Scope: the domain layer under `lib/helpers/` (Mifare Classic recovery/dump/write, Ultralight, T55xx), the FFI crack library binding in `lib/recovery/`, and the device command layer in `lib/bridge/chameleon.dart`. Every routine below is the exact target to reimplement in the native SwiftUI app + `chameleon_d.py` daemon. Line numbers are 1-indexed against the files as they exist.

All paths are under `/Users/tuan/Claude/chameleon/GUI/chameleonultragui/`.

---

## 0. Architecture & the three layers

| Layer | File | Role |
|---|---|---|
| Device command layer | `lib/bridge/chameleon.dart` (`ChameleonCommunicator`, 1339 L) | Frames/deframes the serial protocol; exposes one `Future` method per device opcode. **All hardware I/O funnels through `sendCmd`.** |
| Domain orchestration | `lib/helpers/mifare_classic/recovery.dart` (`MifareClassicRecovery`, 714 L) + `.../general.dart` | Sequences opcodes into the key-recovery / dump / write algorithms; owns per-sector state. |
| Native crack FFI | `lib/recovery/recovery.dart` + `bindings.dart` | Off-device cryptanalysis (`darkside`, `nested`, `static_nested`, `static_encrypted_nested`, `hardnested`, `mfkey32/64`) via a dynamic library, run inside a helper isolate. |

The daemon (`chameleon_d.py`) must reimplement layers 1 + 2 and either (a) FFI-bind the same `librecovery`, or (b) reimplement the crack routines. The GUI runs the crackers in an isolate (thread) because `hardnested`/`darkside` are CPU-heavy and block.

---

## 1. Wire protocol (`ChameleonCommunicator`)

### 1.1 Frame format - `makeDataFrameBytes` (chameleon.dart:47-65)

Outgoing frame bytes, in order:

| Offset | Field | Value |
|---|---|---|
| 0 | SOF | `0x11` (`dataFrameSof`) |
| 1 | SOF LRC | `lrcCalc([0x11])` |
| 2-3 | command | u16 **big-endian** (`cmd.value`) |
| 4-5 | status | u16 BE (always `0x00` on send) |
| 6-7 | data length | u16 BE |
| 8 | head LRC | `lrcCalc(bytes[2..8])` |
| 9.. | payload | `data` (may be empty) |
| last | frame LRC | `lrcCalc(entire frame so far)` |

`lrcCalc` (chameleon.dart:38-45): `ret = (sum(bytes)) & 0xFF; return (0x100 - ret) & 0xFF`.

### 1.2 Deframe - `onSerialMessage` (chameleon.dart:67-117)

Byte-by-byte state machine keyed on `dataPosition`:
- pos 0 must equal `0x11` else throw `'Data frame no sof byte.'`
- pos 1 must equal `lrcCalc([byte0])` else `'Data frame sof lrc error.'`
- pos 8 = head LRC check; then cache `dataCmd` (BE u16 @2-4), `dataStatus` (@4-6), `dataLength` (@6-8). If `dataLength > dataMaxLength(4096)` throw.
- pos `8 + dataLength + 1` = final LRC over all-but-last byte; on match, slice payload `buffer[9 : 9+dataLength]`, push `ChameleonMessage{command,status,data}` to `messageQueue`, reset buffer.

Note the length is read as a **signed** int16 (`getInt16`) but treated as size; status/command likewise via `_toInt16BE`.

### 1.3 `sendCmd` (chameleon.dart:119-179) - the choke point

Behavior to replicate exactly:
1. Opens the serial port + registers `onSerialMessage` callback lazily on first send.
2. **Per-command mutex via `commandQueue`**: if `commandQueue` already contains `cmd.value`, busy-waits (1 ms `asyncSleep`) until it clears or `2×timeout` elapses → throw `"Timeout waiting for queue for command <v>"`. Then pushes `cmd.value`.
3. `skipReceive:true` → write frame (swallowing write errors) and return `null` immediately. Used by fire-and-forget commands (`enterBootloader`, `factoryReset`, `bleClearBondedDevices`).
4. Else write, then poll `messageQueue` for a message whose `command == cmd.value`; on match remove it + release queue + return. On `timeout`: release queue; if `firstRun` re-invoke once (`firstRun:false`); else throw `"Timeout waiting for response for command <v>"`.
5. Default `timeout` = 5 s. Matching is **by command id only**, not by ordering - the daemon must key responses to their command.

Status semantics: `status == 0` generally means success (`0x68`=STATUS_HF_TAG_OK etc. appear in sniff). Many methods read `resp.data[0]` as a nested status.

### 1.4 Complete command opcode table (`ChameleonCommand`, definitions.dart:5-163)

| Name | id | Name | id |
|---|---|---|---|
| getAppVersion | 1000 | changeDeviceMode | 1001 |
| getDeviceMode | 1002 | setActiveSlot | 1003 |
| setSlotTagType | 1004 | setSlotDataDefault | 1005 |
| setSlotEnable | 1006 | setSlotTagNick | 1007 |
| getSlotTagNick | 1008 | saveSlotNicks | 1009 |
| enterBootloader | 1010 | getDeviceChipID | 1011 |
| getDeviceBLEAddress | 1012 | saveSettings | 1013 |
| resetSettings | 1014 | setAnimationMode | 1015 |
| getAnimationMode | 1016 | getGitVersion | 1017 |
| getActiveSlot | 1018 | getSlotInfo | 1019 |
| factoryReset | 1020 | getEnabledSlots | 1023 |
| deleteSlotInfo | 1024 | getBatteryCharge | 1025 |
| getButtonPressConfig | 1026 | setButtonPressConfig | 1027 |
| getLongButtonPressConfig | 1028 | setLongButtonPressConfig | 1029 |
| bleSetConnectKey | 1030 | bleGetConnectKey | 1031 |
| bleClearBondedDevices | 1032 | getDeviceType | 1033 |
| getDeviceSettings | 1034 | getDeviceCapabilities | 1035 |
| bleGetPairEnable | 1036 | bleSetPairEnable | 1037 |
| getAllSlotNicks | 1038 | getSleepTimeout | 1039 |
| setSleepTimeout | 1040 | | |
| **scan14ATag** | 2000 | **mf1SupportDetect** | 2001 |
| **mf1NTLevelDetect** | 2002 | **mf1StaticNestedAcquire** | 2003 |
| **mf1DarksideAcquire** | 2004 | **mf1NTDistanceDetect** | 2005 |
| **mf1NestedAcquire** | 2006 | **mf1CheckKey** | 2007 |
| **mf1ReadBlock** | 2008 | **mf1WriteBlock** | 2009 |
| hf14ARawCommand | 2010 | mf1ManipulateValueBlock | 2011 |
| mf1CheckKeysOfSectors | 2012 (unused) | **mf1HardNestedAcquire** | 2013 |
| **mf1StaticEncryptedNestedAcquire** | 2014 | **mf1CheckKeysOnBlock** | 2015 |
| hf14aSniff | 2020 | | |
| scanEM410Xtag | 3000 | writeEM410XtoT5577 | 3001 |
| scanHIDProxTag | 3002 | writeHIDProxToT5577 | 3003 |
| scanVikingTag | 3004 | writeVikingToT5577 | 3005 |
| writeEM410XElectraToT5577 | 3006 | scanIoProxTag | 3010 |
| writeIoProxToT5577 | 3011 | scanPacTag | 3014 |
| writePacToT5577 | 3015 | writeIdteckToT5577 | 3018 |
| lfSniff | 3031 | | |
| mf1LoadBlockData | 4000 | mf1SetAntiCollision | 4001 |
| mf1SetDetectionEnable | 4004 | mf1GetDetectionCount | 4005 |
| mf1GetDetectionResult | 4006 | mf1GetDetectionStatus | 4007 |
| mf1GetBlockData | 4008 | mf1GetEmulatorConfig | 4009 |
| mf1GetGen1aMode | 4010 | mf1SetGen1aMode | 4011 |
| mf1GetGen2Mode | 4012 | mf1SetGen2Mode | 4013 |
| mf1GetFirstBlockColl | 4014 | mf1SetFirstBlockColl | 4015 |
| mf1GetWriteMode | 4016 | mf1SetWriteMode | 4017 |
| mf1GetAntiCollData | 4018 | mf0Ntag* (UID magic…) | 4019-4037 |
| mf1GetPrngType | 4040 | mf1SetPrngType | 4041 |
| setEM410XemulatorID | 5000 | getEM410XemulatorID | 5001 |
| setHIDProxEmulatorID | 5002 | getHIDProxEmulatorID | 5003 |
| setVikingEmulatorID | 5004 | getVikingEmulatorID | 5005 |
| setPacEmulatorID | 5006 | getPacEmulatorID | 5007 |
| setIoProxEmulatorID | 5008 | getIoProxEmulatorID | 5009 |
| setIdteckEmulatorID | 5012 | getIdteckEmulatorID | 5013 |

### 1.5 Command payload/response layouts used by recovery

| Method (chameleon.dart) | Cmd | Request payload | Response parse |
|---|---|---|---|
| `isReaderDeviceMode` :211 | getDeviceMode | - | `data[0]==1` |
| `setReaderDeviceMode(b)` :216 | changeDeviceMode | `[b?1:0]` | - |
| `scan14443aTag` :221 | scan14ATag | - | see §5.1; null if empty |
| `detectMf1Support` :239 | mf1SupportDetect | - | `status==0` |
| `getMf1NTLevel` :246 | mf1NTLevelDetect | - | `data[0]`: 0=static,1=weak,2=hard,else unknown |
| `checkMf1Darkside` :260 | mf1DarksideAcquire | `[0x61,0x03,1,2]`, **60 s** | status (or `data[0]` if present): 0=vulnerable,1=cantFixNT,2=luckAuthOK,3=notSendingNACK,4=tagChanged,else fixed |
| `getMf1NTDistance(block,keyType,key)` :285 | mf1NTDistanceDetect | `[keyType, block, ...key(6)]` | `uid=u32(data[0:4])`, `distance=u32(data[4:8])` |
| `getMf1NestedNonces(block,keyType,key,tBlock,tKeyType,{level,slow})` :300 | see below | see below | see below |
| `getMf1Darkside(tBlock,tKeyType,firstRecover,syncMax)` :348 | mf1DarksideAcquire | `[tKeyType,tBlock,firstRecover?1:0,syncMax]`, 60 s | `data[0]!=0`→throw "Not vulnerable"; then strip byte0; `uid,nt1,par,ks1,nr,ar` (see §4.4) |
| `getMf1StaticEncryptedNestedAcquire({sectorCount,startingSector})` :372 | mf1StaticEncryptedNestedAcquire | per backdoor key: `[...key(6), sectorCount, startingSector]` | see §4.7; returns `(uid, aNonces, bNonces, key)` or null |
| `mf1Auth(block,keyType,key)` :403 | mf1CheckKey | `[keyType, block, ...key]` | `status==0` |
| `mf1AuthMultipleKeys(block,keyType,keys)` :413 | mf1CheckKeysOnBlock | `[block, keyType, keys.length, ...keys.flatten]` | `status==0 ? data[1:] (the 6-byte found key) : null` |
| `mf1ReadBlock(block,keyType,key)` :422 | mf1ReadBlock | `[keyType, block, ...key]` | `data` (16 B or empty) |
| `mf1WriteBlock(block,keyType,key,data)` :430 | mf1WriteBlock | `[keyType, block, ...key, ...data(16)]` | `status==0` |
| `send14ARaw` :1164 | hf14ARawCommand | see §5.4 | `data` |

`getMf1NestedNonces` (chameleon.dart:300-346) - the workhorse nonce collector:
- Command select: default `mf1NestedAcquire(2006)`; `level==static` → `mf1StaticNestedAcquire(2003)`; `level==hard` → `mf1HardNestedAcquire(2013)` with `padding=[slow?1:0]` prepended.
- Payload: `[...padding, keyType, block, ...knownKey(6), targetKeyType, targetBlock]`, timeout 30 s.
- Response starts at `i = (level==static)?4:0` (static skips 4 header bytes). Loop:
  - static: record = `nt=u32[i:i+4]`, `ntEnc=u32[i+4:i+8]`, `parity=0`; `i+=8`.
  - weak/hard: `nt`, `ntEnc`, `parity=data[i+8]`; `i+=9`.
- Returns `NestedNonces{ nonces:[NestedNonce{nt,ntEnc,parity}] }`.

`keyType` convention everywhere: **`0x60` = key A, `0x61` = key B**; the code writes `0x60 + keyType` where `keyType ∈ {0,1}`. Backdoor auth uses `0x64` (i.e. `0x60 + 4`).

---

## 2. Core enums & value objects (definitions.dart)

- `NTLevel { static, weak, hard, backdoor, unknown }` (:274). Note **`backdoor` is a GUI-synthesized level**, never returned by the device - set in code when static-encrypted/backdoor path is chosen.
- `DarksideResult { vulnerable, fixed, cantFixNT, luckAuthOK, notSendingNACK, tagChanged }` (:276).
- `MifareClassicType { none, mini, m1k, m2k, m4k }` (general.dart:74).
- `TagType` (:165-193): mifareMini 1000, mifare1K 1001, mifare2K 1002, mifare4K 1003; ultralight/NTAG family 1100-1108; LF: em410X 100, em410XElectra 104, pac 150, viking 170, hidProx 200, ioProx 201, idteck 310.
- `NestedNonce{nt,ntEnc,parity}` (:292). `NestedNonces` (:307) carries `getNoncesInfo()` and `getHardNested()` - see §7.
- `NTDistance{uid,distance}`, `Darkside{uid,nt1,par,ks1,nr,ar}` (:357), `CardData{uid,sak,atqa,ats}` (:252).
- `ChameleonKeyCheckmark { none, found, checking, disabled }` (recovery.dart:27) - per-slot recovery state.

---

## 3. `MifareClassicRecovery` state model (recovery.dart:29-67)

State arrays and their **indexing convention** (critical - replicate exactly):
- `checkMarks : List<ChameleonKeyCheckmark>` length **80**, index = `sector + keyType*40`. So key-A of sector S at index `S`, key-B at `S+40`. `getSectorState/setKeyAsFound/setCheckingSector/setMissingSector/getSectorKey` (recovery.dart:687-713) all use `sector + keyType*40`.
- `validKeys : List<Uint8List>` length **80**, same indexing, each 6-byte key (empty `Uint8List(0)` if unknown).
- `cardData : List<Uint8List>` length **256**, block-indexed, each 16 B (empty until dumped).
- Progress fields: `dumpProgress` (double 0..1), `hardnestedProgress` (double? 0..1), `keyCheckProgress` (double? or null). `update()` is the UI-notify callback.
- `mifareClassicType`, `isMifareClassicEV1`.

State transitions (recovery.dart:687-705):
- `setKeyAsFound(s,kt,key)`: mark index `found`, store key, `update()`.
- `setCheckingSector(s,kt)`: only `none → checking`.
- `setMissingSector(s,kt)`: only `checking → none` (i.e. revert, never overwrite a `found`).

### 3.1 Card geometry helpers (general.dart:193-265) - must be exact

```
sectorCount(type, isEV1): mini=5, 1k=(EV1?18:16), 2k=32, 4k=40
blockCount(type, isEV1):  mini=20, 1k=(EV1?72:64), 2k=128, 4k=256
sectorTrailerBlockBySector(s): s<32 ? s*4+3 : 128 + (s-32)*16 + 15
sectorTrailerBlockInSector(s): s<32 ? 3 : 15
blockCountBySector(s):         s<32 ? 4 : 16
firstBlockCountBySector(s):    s<32 ? s*4 : 128 + (s-32)*16
sectorByBlock(b):              b<128 ? b~/4 : 32 + (b-128)~/16
```
(4K has 32 four-block sectors then 8 sixteen-block sectors.)

### 3.2 Key dictionary (general.dart:16-102)

`gMifareClassicKeysList` is a 48-entry ordered set of u48 keys (default `FFFFFFFFFFFF` first, then MAD/NDEF/EV1-signature/vendor keys, `000000000000` blank, plus common keys). `gMifareClassicKeys` maps each to a big-endian 6-byte `Uint8List`. **Index references used by EV1 seeding:** `[3]=4B791BEA7BCC`, `[4]=5C8FF9990DA2`, `[5]=D01AFEEB890A`, `[6]=75CCB59C9BED`.

`gMifareClassicBackdoorKeysList` (general.dart:67-72): `A396EFA4E24F, A31667A8CEC1, 518B3354E760, 73B9836CF168` (from eprint 2024/1275). `gMifareClassicBackdoorKeys` = 6-byte forms.

---

## 4. KEY RECOVERY PIPELINE - end to end

Two public entry points, always in this order from the UI: **`checkKeys()`** (dictionary) then **`recoverKeys()`** (attacks). `initialize()` prepares device state.

### 4.0 `initialize()` (recovery.dart:125-141)
1. If not reader mode → `setReaderDeviceMode(true)`.
2. `detectMf1Support()`. If true → `mifareClassicType = mfClassicGetType()`, else log "Not Mifare Classic tag!".
3. `isMifareClassicEV1 = mf1Auth(0x45, 0x61, gMifareClassicKeys[3])` - auth block 69 key B with the EV1 sig-17-B key `4B791BEA7BCC`.
4. `initializeEV1()`.

`mfClassicGetType` (general.dart:104-128): probes with raw `[0x60, block]` (auth-A request) via `send14ARaw(checkResponseCrc:false)`; a 4-byte reply means the block exists → block 255→4K, 80→2K, 63→1K, else Mini.

`initializeEV1()` (recovery.dart:172-179): if EV1, pre-mark 4 known signature-sector keys as found: sector16-A=`gKeys[4]`, 16-B=`gKeys[5]`, 17-A=`gKeys[6]`, 17-B=`gKeys[3]`.

### 4.1 STAGE 1 - Dictionary check: `checkKeys({skipDefaultDictionary})` (recovery.dart:181-218)

Preconditions: `mifareClassicType` known, `selectedDictionary` set.

Algorithm:
1. `initializeEV1()` (re-seed).
2. For each `sector` in `[0, sectorCount)`, build `keyList = selectedDictionary.keys ++ (skipDefault ? [] : gMifareClassicKeys not already in dict)`.
3. For `keyType ∈ {0,1}`: `await checkKeysOnSector(keyList, keyType, sector)`.
4. After the sweep, set `allKeysExists = true` iff every (sector,keyType) is `found` or `disabled`.

`checkKeysOnSector(keys, keyType, sector)` (recovery.dart:69-123) - dictionary probe on one slot:
1. `chunkSize = (connectionType==ble) ? 32 : 64`.
2. If the slot is not already `found`/`disabled`: `setCheckingSector`. Partition `keys` into `chunkSize` groups; for each chunk call `mf1AuthMultipleKeys(sectorTrailerBlock, 0x60+keyType, chunk)` (device tests up to `chunkSize` keys against the block in one command, returns the matching 6-byte key or null).
   - On hit: `setKeyAsFound`, then **`recheckKey(key, sector)`** (see 4.1a), return true.
   - Else if `totalChunks>10`: bump `keyCheckProgress += 1/totalChunks`, `update()`.
3. If nothing found and `key==null`: `setMissingSector`.
4. **Key-B inference (recovery.dart:103-118):** if `keyType==0` and A is found but B is unknown and `key!=null`: read the sector trailer with A (`mf1ReadBlock(trailer, 0x60, keyA)`); if 16 B returned, take `block[10:16]`; if it's not all-zero, treat B as recoverable → `recheckKey` + return true. (Note: it does not store that raw B as a validated key here; it relies on recheck/dump to confirm.)
5. Fallthrough → `setMissingSector`, return false.

`recheckKey(key, startingSector)` (recovery.dart:143-170): once any key is found, opportunistically test it on **all remaining (sector,keyType) still `none`** via single `mf1Auth`. This propagates a reused key across the card cheaply before running expensive attacks.

### 4.2 STAGE 2 - Attack orchestration: `recoverKeys()` (recovery.dart:220-569)

This is the master sequencer. Exact order:

**Phase A - reconnaissance (recovery.dart:220-259)**
1. `hasBackdoor = mfClassicHasBackdoor(comm)` (general.dart:130-144): send raw `[0x64,0x00]` (backdoor auth) autoSelect; needs 4-byte reply AND a successful `getMf1StaticEncryptedNestedAcquire(sectorCount:1)`.
2. If `hasBackdoor`: `backdoorInfo = getMf1StaticEncryptedNestedAcquire(sectorCount)` → `(uid, aNonces, bNonces, backdoorKey)`.
3. `hasKey` = does any slot already show `found` (from dictionary stage)?
4. `isStaticEncrypted`: if backdoor, `mfClassicIsStaticEncrypted(comm, 0, 4, backdoorKey)` (general.dart:158-166 - collects hard-nested nonces auth block0 key `0x64` targeting block3 keyB, returns true iff `getNoncesInfo()[1]==1`, i.e. exactly one distinct first-byte → static).
5. `prng = getMf1NTLevel()`.

**Phase B - Darkside (recovery.dart:262-310)** - runs only if `!hasKey && !isStaticEncrypted && prng != static`.
- `state = checking_or_running_darkside`; `setCheckingSector(0,1)`; `darkside = checkMf1Darkside()` (wrapped in try; on throw `setMissingSector(0,1)`).
- If `vulnerable`:
  1. `data = getMf1Darkside(0x03, 0x61, firstRecover:true, syncMax:15)` - target block 3, key B.
  2. Build `DarksideDart(uid, items:[])`. Loop up to **5 tries** until found:
     - Append `DarksideItemDart{nt1, ks1, par, nr, ar}` from `data`.
     - `keys = recovery.darkside(darksideDart)` (FFI).
     - If keys non-empty: `checkKeysOnSector(mfClassicConvertKeys(keys), keyType:1, sector:0)`; on success `found=hasKey=true`, break.
     - Else `data = getMf1Darkside(0x03,0x61,firstRecover:false,15)` to gather another nonce set.
  3. If never found → `setMissingSector(0,1)`.

Darkside accumulates encrypted nonce samples (`nt1,ks1,par,nr,ar`) across iterations and feeds the growing `items` list to the native cracker each round.

**Phase C - Backdoor recovery of non-static-encrypted (recovery.dart:314-344)** - only if `!hasKey && hasBackdoor && prng==weak && !isStaticEncrypted`.
- Loop 3 times: `getMf1NTDistance(0, 0x64, backdoorKey)`; `getMf1NestedNonces(0, 0x64, backdoorKey, targetBlock:0, targetKeyType:0x60, level:weak)`; build `NestedDart` from the two nonces; `keys = recovery.nested(nested)`; if non-empty and `checkKeysOnSector(keys, 0, 0)` succeeds → break. (Uses the backdoor auth as the "known key" to nested-attack sector 0 key A.)

**Phase D - pick anchor key & finalize prng (recovery.dart:346-380)**
- Scan sectors/keyTypes for the first `found` slot → `validKey`, `validKeyBlock = sectorTrailerBlock(sector)`, `validKeyType`. While scanning, if not yet flagged, compute `isStaticEncrypted = mfClassicIsStaticEncrypted(comm, validKeyBlock, validKeyType, validKey)`.
- If `(isStaticEncrypted || (validKeyType==-1 && hasBackdoor)) && backdoorInfo!=null` → **`prng = NTLevel.backdoor`**.
- If `validKeyType==-1 && prng!=backdoor` → set `error = recovery_error_no_keys_darkside`, `state=""`, **return** (give up: no anchor key and no backdoor).
- `tries = (prng ∈ {backdoor, static}) ? 1 : 5`.

**Phase E - main nested loop (recovery.dart:382-552)** - for every `(sector, keyType)` still `none`:
- `attackType` label from `prng`: static→"Static Nested", weak→"Nested", hard→"Hard Nested", backdoor→`has_backdoor_support`, unknown→"".
- `setCheckingSector`.
- If `prng != backdoor`: `distance = getMf1NTDistance(validKeyBlock, 0x60+validKeyType, validKey)` (PRNG sync distance from the anchor key).
- Loop up to `tries` (until `found`):
  - **Nonce acquisition:**
    - `hard`: `nonces = collectHardnestedNonces(...)` (see 4.5). If it returns a `String` (error) → `setMissingSector`, set `error`, **return** (aborts whole recovery).
    - else if `prng != backdoor`: `nonces = getMf1NestedNonces(validKeyBlock, 0x60+validKeyType, validKey, targetBlock=sectorTrailer(sector), targetKeyType=0x60+keyType, level:prng)`.
  - **Crack dispatch:**
    - `weak` → `NestedDart{uid=distance.uid, distance, nt0/nt0Enc/par0 from nonces[0], nt1/nt1Enc/par1 from nonces[1]}` → `recovery.nested`.
    - `static` → `StaticNestedDart{uid=distance.uid, keyType=0x60+validKeyType, nt0/nt0Enc, nt1/nt1Enc}` → `recovery.staticNested`.
    - `hard` → `HardNestedDart{nonces: nonces.getHardNested(distance.uid)}` → `recovery.hardNested`.
    - `backdoor` → **static-encrypted attack** (see 4.6).
  - If `keys` non-empty and `checkKeysOnSector(mfClassicConvertKeys(keys), keyType, sector)` → `found=true`, break; else retry.

**Phase F - completion (recovery.dart:554-568)**: `state=""`; recompute `allKeysExists` (all slots `found`/`disabled`).

### 4.5 Hardnested nonce collection: `collectHardnestedNonces` (recovery.dart:633-685)

Accumulates nonces until the statistical set is complete:
```
loop:
  collected = getMf1NestedNonces(block,keyType,knownKey,targetBlock,targetKeyType, level:hard)
  nonces.addAll(collected)
  [sum, num] = nonces.getNoncesInfo()          // §7.1
  if nonces empty -> return localizations.recovery_old_firmware   // STRING = error sentinel
  hardnestedProgress = num/256; state = hardnested_collecting_nonces(num); update()
  if num == 256:
     if sum ∈ {0,32,56,64,80,96,104,112,120,128,136,144,152,160,176,192,200,224,256}: break  // valid sum table
     else: log "Got wrong sum"; nonces.nonces = []   // discard, recollect
return nonces
```
The valid-sum set is the known set of achievable parity sums for a complete hardnested distribution; a `num==256` set with a sum outside it is corrupt and must be recollected.

### 4.6 Static-encrypted (backdoor) attack (recovery.dart:477-535)

For sector `s` under `prng==backdoor`, using `backdoorInfo=(uid, aNonces, bNonces, key)`:
1. `setCheckingSector(s, 1)`.
2. `possibleAKeys = recovery.staticEncryptedNested(StaticEncryptedNestedDart{uid, nt=aNonces[s].nt, ntEnc=aNonces[s].ntEnc, ntParEnc=aNonces[s].parity})`.
3. `possibleBKeys` = same from `bNonces[s]`.
4. `filtered = StaticEncryptedKeysFilterAsync.filterKeys(possibleAKeys, possibleBKeys, aNonces[s].nt, bNonces[s].nt)` (isolate; §7.2). Returns `(filteredA, filteredB)`.
5. If B-slot not yet resolved and `checkKeysOnSector(convert(filtered.$2.reversed), keyType:1, s)` → mark `checkMarks[s+40]=found`.
6. If A-slot already found/disabled → `found=true`, break. Else try, in order:
   - `checkKeysOnSector(convert(findMatchingKeys(bNonces[s].nt, u64([0,0,...validKeys[s+40]]), aNonces[s].nt, possibleAKeys)), 0, s)` - narrow A candidates using the just-found B key (§7.2 `findMatchingKeys`);
   - else `checkKeysOnSector(convert(filtered.$1.reversed), 0, s)`.
   - On any success `found=true`, break; else `setMissingSector(s,0)`+`setMissingSector(s,1)`.

`mfClassicConvertKeys` (general.dart:168-176): each u64 key → `u64ToBytes(key)[2:8]` (drop top 2 bytes → 6-byte key).

### 4.7 Static-encrypted nonce acquire parsing (chameleon.dart:372-401)

`getMf1StaticEncryptedNestedAcquire` iterates `gMifareClassicBackdoorKeys`; for each sends `[...key(6), sectorCount, startingSector]`. On `status==0`: `uid=u32(data[0:4])`, then per 14-byte record (`i` from 4, step 14):
- A-nonce: `nt=reconstructFullNt(data,i)`, `ntEnc=u32(data[i+3:i+7])`, `parity=parityToInt(data[i+2])`.
- B-nonce: `nt=reconstructFullNt(data,i+7)`, `ntEnc=u32(data[i+10:i+14])`, `parity=parityToInt(data[i+9])`.
Returns `(uid, aNonces, bNonces, key)`. `reconstructFullNt`/`parityToInt` in §7.

### 4.8 Give-up conditions summary

| Condition | Location | Behavior |
|---|---|---|
| No anchor key & no backdoor | :374-378 | set `error`, return (nothing recoverable) |
| Darkside not vulnerable / 5 tries exhausted | :306-308 | `setMissingSector(0,1)`, continue to nested |
| Hardnested nonce collect returns empty (old firmware) | :430-433, :645-647 | set `error=recovery_old_firmware`, return |
| Per-slot `tries` exhausted | :416 loop | slot left `none`; `allKeysExists=false` |

---

## 5. DUMP ASSEMBLY: `dumpData()` (recovery.dart:571-631)

Precondition: keys recovered into `validKeys`. Produces `cardData[256]`.

```
cardData = 256 × empty
for sector in [0, sectorCount(type, isEV1)):
  for block in [0, blockCountBySector(sector)):
    for keyType in {0,1}:                        # key A first, then B fallback
      absBlock = block + firstBlockCountBySector(sector)
      if getSectorKey(sector,keyType).isEmpty: cardData[absBlock]=zeros16; continue
      blockData = mf1ReadBlock(absBlock, 0x60+keyType, key)
      if blockData empty:
         if keyType==1: blockData = zeros16     # both keys failed → zero-fill
         else: continue                          # try key B
      if absBlock == sectorTrailer(sector):      # patch trailer with known keys
         if keyA known: blockData[0:6]  = keyA
         if keyB known: blockData[10:16]= keyB
      cardData[absBlock] = blockData
      dumpProgress = absBlock / blockCount(type,isEV1); update()
      break                                       # stop after first key that read this block
```

Key points to replicate:
- **Key-A preferred; B is fallback.** The `break` at the end means once a block reads with A, B is not tried.
- **Sector trailer key patch:** even if the card returned masked/zeroed key bytes in the trailer, the known recovered keys are written into bytes `[0:6]` (A) and `[10:16]` (B); access-bits bytes `[6:10]` are left as read.
- Missing-key blocks become 16 zero bytes so the image stays block-aligned.

Export helpers (general.dart:309-330): `mfClassicGetExportBlocks` → list of `blockCount` blocks (each 16 B, zero-filled if missing); `mfClassicGetExportBytes` flattens to a contiguous binary dump. `mfClassicGetKeysFromDump` (general.dart:341-353) reads back A=`trailer[0:6]`, B=`trailer[10:16]` per sector.

---

## 6. WRITE STRATEGIES

Class hierarchy: `AbstractWriteHelper` (write.dart:15-94) → `BaseMifareClassicWriteHelper` (write/base.dart) → `Gen1`, `Gen2`, `Gen3(extends Gen2)`. Parallel: `BaseMifareUltralightWriteHelper`, `BaseT55XXCardHelper`.

`AbstractWriteHelper.getClassByCardType` (write.dart:47-74) routes: Mifare Classic → `BaseMifareClassicWriteHelper`; Ultralight/NTAG → `BaseMifareUltralightWriteHelper`; EM410X/HID/Viking/PAC/ioProx/idteck → `BaseT55XXCardHelper`; else null.

### 6.1 Method selection & auto-detect (write/base.dart:36-51)

`getAvailableMethods()` = `[gen1, gen2, gen3]`. `getAvailableMethodsByPriority()` = **`[gen1, gen3, gen2]`** - auto-detect probes gen1 first, then gen3, then gen2. Each helper's `isMagic()` is the detector.

`createBlock0FromSave(card)` (write/base.dart:128-147): `uid ++ (uid.len==4 ? [bcc=XOR(uid)] : []) ++ [sak] ++ atqa.reversed ++ zero-pad to 16`.

### 6.2 Gen1a (write/gen1.dart) - backdoor unlock sequence

`isMagic` (gen1.dart:14-35): reset (`send14ARaw([0x00])`), then unlock:
1. `send14ARaw([0x40], bitLen:7, appendCrc:false, autoSelect:false, checkResponseCrc:false, keepRfField:true)` - 7-bit `0x40`. Expect `data[0]==0x0a` (ACK).
2. `send14ARaw([0x43], appendCrc:false, autoSelect:false, checkResponseCrc:false)`. Magic iff `data[0]==0x0a`.

`writeBlock(block, data)` (gen1.dart:43-75): up to **5 retries**:
1. reset `[0x00]`.
2. `[0x40]` 7-bit (keepRfField).
3. `[0x43]` (keepRfField).
4. `[0xA0, block]` write-command (autoSelect:false, keepRfField, no CRC check).
5. `send14ARaw(data)` (16 B). Success iff reply `[0]==0x0a`.
6. On failure wait 100 ms, retry.

Gen1a bypasses all authentication via the backdoor magic-wakeup, so it can rewrite block 0 (UID) and any block. `isReady()` always true (gen1.dart:38-40).

### 6.3 Gen2 (write/gen2.dart) - authenticated direct write

`isMagic` (gen2.dart:20-32): scan tag; magic assumed iff scanned UID == `cardSave.uid` (Gen2 is indistinguishable from a normal card, so it only confirms the same card is present).

`isReady()` (gen2.dart:35-48): requires **all** (sector,keyType) in `recovery.checkMarks` to be `found` - Gen2 needs the real keys to auth each block.

`writeBlock(block, data, {tryBothKeys, useGenericKey})` (gen2.dart:68-107):
1. `mf1WriteBlock(block, 0x60, useGenericKey ? gKeys[0] : recovery.validKeys[sectorByBlock(block)], data)`. Success → true.
2. If `useGenericKey` and that failed, retry with the real key A.
3. If `tryBothKeys`: same two attempts with `0x61` and `validKeys[40+sectorByBlock(block)]`.

`writeBlockModifier` (gen2.dart:50-65): wraps `writeBlock` with 10 retries, 50 ms pre-delay + 150 ms post-fail delay.

`writeData(card, update)` (gen2.dart:110-182) - three-pass sector-trailer-aware algorithm:
- Ensure `data[0]` is a valid block0 (`createBlock0FromSave` if empty).
- **Pass 1 - trailers first:** for each sector, `writeBlockModifier(trailerBlock, data[trailer], tryBothKeys:true)`. On success mark `cleanSectors[sector]=true` and **immediately update `recovery.validKeys[sector]`/`[40+sector]`** from the just-written trailer (`data[trailer][0:6]` / `[10:16]`) so subsequent data-block writes auth with the new keys.
- **Pass 2 - data blocks:** skip trailer blocks; `writeBlockModifier(block, data[block], useGenericKey: cleanSectors[sector], tryBothKeys:true)`. If write fails AND sector isn't clean AND block≠0 → record in `failedBlocks`.
- **Pass 3 - rewrite trailers with generic key** for clean sectors (the trailer now holds the target keys, so it re-writes using default key `useGenericKey:true`). Failure here → return false ("card is lost").
- Returns `failedBlocks.isEmpty`.

### 6.4 Gen3 (write/gen3.dart) - UID magic, extends Gen2

Only difference vs Gen2 is block-0 handling. `isMagic` (gen3.dart:19-33): `detectMf1Support()` then raw `[0x30, 0x00]` (read block0); magic iff reply length **18** (16 data + 2 CRC).

`writeBlockModifier` override (gen3.dart:52-70): if `block==0` → `writeGen3Block(card,data)`, else normal Gen2 `writeBlock`. Same 10-retry/delay envelope.

`writeGen3Block(dump, data)` (gen3.dart:72-91) - Gen3 UID-set APDUs:
1. Write whole block0: `send14ARaw([0x90,0xFB,0xCC,0xCC,0x10, ...data(16)])`.
2. Write UID only: `send14ARaw([0x90,0xFB,0xCC,0xCC,0x07, ...uidBytes])`.
3. Card reboots silently → wait **500 ms**, `scan14443aTag()`, verify new UID matches `data[0:uidLen]` or `dump.uid`.

### 6.5 Ultralight write (mifare_ultralight/write/base.dart:111-176)

Requires a 4-byte password via UI (`key`; empty string = no key). `isReady` = `key != null`.

`writeData(card, update)`:
1. Ensure reader mode; `scan14443aTag()` (null → false).
2. If `key` non-empty: `send14ARaw([0x1B, ...key(4)], keepRfField:true)` (PWD_AUTH); reply <2 B → false.
3. **Two passes** over all blocks:
   - **Pass 0** (write everything except): for block 2, zero bytes `[2],[3]` (lock bytes) before writing; for block 3, write `Uint8List(4)` (skip OTP content initially). Write via `[0xA2, block, ...blockData(4)]`, `autoSelect: block∈{0,3}`.
   - **Pass 1**: only rewrite blocks 2 and 3 (the real lock/OTP values), skip all others.
   - On write failure (reply empty / `[0]!=0x0A`) OR always for block 2: reset `[0x00]`, re-auth if keyed; if `block>2` record `failedBlocks`.
4. Returns `failedBlocks.isEmpty`. Progress denominator is `totalBlocks+2`.

Geometry (ultralight/general.dart): page counts per type (:58-79), password page (:81-102), counters (:104-130). Type detection `mfUltralightGetType` from GET_VERSION byte[6] (:36-51); fallback `mfUltralightType` probes `[0x30, page]` reply lengths (:132-176).

### 6.6 T55xx write (t55xx/write/base.dart:147-189)

Requires `currentKey` (8 hex) + `newKey` (8 hex); UI defaults both to `"20206666"` when blank. `isReady` = both length 8.

Per LF type, calls the matching `writeXtoT55XX(uid, newKey, [currentKey, zeros4])` command, waits 500 ms, reads the tag back, and verifies `readback.toString() == card.uid`:
- EM410X family → `writeEM410XtoT55XX` (chameleon.dart:618-636: UID len 5 → cmd 3001; len 13 → Electra cmd 3006; else throw). Payload `[...uid, ...newKey, ...(newKey ++ each oldKey)]`.
- hidProx → `writeHIDProxToT5577`; viking → `writeVikingToT5577`; pac → `writePacToT5577` (no readback delay); ioProx → `writeIoProxToT5577`; idteck → `writeIdteckToT5577` then **return true unconditionally** (firmware has no idteck read-back).

---

## 7. Support math (must be bit-exact)

### 7.1 `NestedNonces.getNoncesInfo()` (definitions.dart:310-330)
Returns `[firstByteSum, firstByteNum]`. For each nonce, process both `nt` (parity `parity>>4`) and `ntEnc` (parity `parity&0x0F`). `processNonce(value, parity)`: `key = value>>24`; if that first byte unseen, `firstByteSum += evenParity32((value & 0xFF000000) | (parity & 0x08))`, mark seen, `firstByteNum++`. `evenParity32(n)` (general.dart:468-476) = popcount(n) mod 2.

### 7.2 `getHardNested(uid)` (definitions.dart:332-352)
Serializes nonces for the native hardnested cracker: `[uid(4 BE)][2 pad bytes][ per nonce: nt(4 BE), ntEnc(4 BE), parity(1) ]`. Total `6 + 9*count`.

### 7.3 PRNG / nonce reconstruction (general.dart)
- `prngSuccessor(x, n)` (:75-85): swap-endian, iterate `x = (x>>1) | (((x>>16)^(x>>18)^(x>>19)^(x>>21))<<31)` masked to 32 bits, n times, swap back.
- `reconstructFullNt(data, off)` (:87-91): `nt16 = u16(data[off:off+2]); return (nt16<<16) | prngSuccessor(nt16,16)`.
- `parityToInt(b)` (:60-67): packs low nibble bits into a decimal-digit integer `(b>>3)&1,(b>>2)&1,(b>>1)&1,b&1` joined - quirky, replicate literally.
- `calculateBcc(data)` (:263-269): XOR fold.
- `bytesToU16/32/64`, `u16/32/64ToBytes`: big-endian throughout.

### 7.4 Static-encrypted key filter (general.dart:387-573) - `StaticEncryptedKeysFilter`
Pure-Dart port of Proxmark3 `staticnested_2x1nt_rf08s` (Doegox). Init a 16-bit LFSR table `_iLfsr16/_sLfsr16` once (:392-403). `_computeSeednt16Nt32(nt32,key)` (:415-446) walks the nonce back 14 steps then mixes 48 key bits through substitution tables `a[]`/`b[]` with alternating odd/even nibble handling, stepping the LFSR backward 8 more each round. `filterKeys(keys1,keys2,nt1,nt2)` (:450-488) keeps only keys whose seed-nonces collide across the two nonce sets. `findMatchingKeys(nt1,key1,nt2,keys2)` (:492-506) keeps keys2 entries whose seed matches key1's. Both are run off-thread via the `StaticEncryptedKeysFilterAsync` isolate wrappers (:509-573).

---

## 8. Native crack FFI (`lib/recovery/`)

### 8.1 Library resolution (recovery.dart:200-229)
`resolveDylibPath('recovery', dartDefine/env 'LIBRECOVERY_PATH')`; macOS/iOS fallback `'recovery.framework/recovery'`. Opened once into `Recovery(DynamicLibrary.open(...))`.

### 8.2 Symbols (bindings.dart) - the native ABI the daemon must match

| Dart wrapper | C symbol | Signature | Result |
|---|---|---|---|
| `darkside(ptr, count*)` | `darkside` | `(Darkside*, uint32* keyCount) -> uint64*` | array of `keyCount` u64 keys |
| `nested(ptr, count*)` | `nested` | `(Nested*, uint32*) -> uint64*` | u64 key array |
| `static_nested(ptr, count*)` | `static_nested` | `(StaticNested*, uint32*) -> uint64*` | u64 key array |
| `static_encrypted_nested(ptr, count*)` | `static_encrypted_nested` | `(StaticEncryptedNested*, uint32*) -> uint64*` | u64 candidate-key array |
| `mfkey32(ptr)` | `mfkey32` | `(Mfkey32*) -> uint64` | single key |
| `mfkey64(ptr)` | `mfkey64` | `(Mfkey64*) -> uint64` | single key |
| `hardnested(ptr)` | `hardnested` | `(HardNested*) -> uint64` | single key |

Struct fields (bindings.dart:146-290), all `uint32` unless noted:
- `DarksideItem{ nt1:u32, ks1:u64, par:u64, nr:u32, ar:u32 }`; `Darkside{ uid:u32, items:DarksideItem*, count:u32 }`.
- `Nested{ uid, dist, nt0, nt0_enc, par0, nt1, nt1_enc, par1 }`.
- `StaticNested{ uid, key_type, nt0, nt0_enc, nt1, nt1_enc }`.
- `StaticEncryptedNested{ uid, nt, nt_enc, nt_par_enc }`.
- `Mfkey32{ uid, nt0, nt1, nr0_enc, ar0_enc, nr1_enc, ar1_enc }`; `Mfkey64{ uid, nt, nr_enc, ar_enc, at_enc }`.
- `HardNested{ nonces:char*, length:u32 }`.

### 8.3 Isolate marshaling (recovery.dart:297-467)
A single long-lived helper isolate owns all FFI calls (crackers block). Main→helper messages are `*Request{id, payload}`; helper→main `KeyResponse{id, List<int>}`. Each public `Future` (e.g. `nested(NestedDart)`) allocates a request id, stores a `Completer` in `requests[id]`, and posts to the isolate. In the isolate each request type `calloc`s the matching struct, copies fields, calls `_bindings.<fn>`, reads `keyCount.value` u64s (or single return), and sends back. **The daemon can serialize this as a thread pool / subprocess with the same struct layouts.**

---

## 9. EMULATE / SLOT-LOAD: dump → emulated slot

Two paths write an emulated card. Both target one of 8 slots (`gridPosition`, 0-7).

### 9.1 Full dump upload (slot_manager.dart `onTap`, :81-316)

**Mifare Classic (:85-151):**
1. If dump looks like EV1 (`chameleonTagSaveCheckForMifareClassicEV1`, general.dart:295-307 - tag is 1K but has non-empty blocks 64-71) → override `card.tag = mifare2K` (so 32 sectors of emulator storage are allocated).
2. `setReaderDeviceMode(false)` (emulation mode).
3. `enableSlot(slot, hf, true)` → `activateSlot(slot)` → `setSlotType(slot, tag)` → `setDefaultDataToSlot(slot, tag)`.
4. `setMf1AntiCollision(CardData{uid, atqa, sak, ats})` (chameleon.dart:532-542 payload `[uidLen, ...uid, ...atqa.reversed, sak, atsLen, ...ats]`).
5. **Chunked block upload:** walk blocks `[0, blockCount)`, accumulate 16-byte blocks into `blockChunk`; flush via `setMf1BlockData(lastSend, chunk)` when the next block is empty OR `blockChunk.length >= 128` (i.e. ≥8 blocks). `mf1LoadBlockData` payload = `[startBlock&0xFF, ...blocks]`, auto-incrementing. Final flush after loop.
6. `setSlotTagName(slot, name|no_name, hf)` → `saveSlotData()`.

**Ultralight/NTAG (:253-311):** reader mode off; enable/activate/setType/setDefault; `setMf1AntiCollision`; per page `mf0EmulatorWritePages(page, card.data[page])` (chameleon.dart:1221 payload `[from, len>>2, ...data]`); then set version/signature (`mf0EmulatorSetVersionData/SignatureData`), counters (`mf0EmulatorSetCounterData(i,val,resetTearing:true)`), and `mf0ResetAuthCount()` if the type has counters; name + save.

**LF (EM410X/HID/Viking/PAC/ioProx/idteck, :152-252):** reader mode off; enable(lf)/activate/setType/setDefault; `setXEmulatorID(...)` (HID uses `HIDCard.fromUID(uid).toString()` byte layout; EM410X Electra keeps its subtype); name(lf) + save.

### 9.2 Manual slot edit (slot/edit.dart `save`, :186-269)
`activateSlot`; if type changed `setSlotType` + (unless both old/new are same-family Classic/Ultralight) `setDefaultDataToSlot`; then per-family `setXEmulatorID` or, for Classic/Ultralight, `setMf1AntiCollision` from the UID/SAK/ATQA/ATS fields (+ Ultralight version/signature/counters); `setSlotTagName`; `saveSlotData`. `updateInfo` (:80-184) reads current emulator state back (`mf1GetAntiCollData`, `getMf1EmulatorSettings`, `getMf1PrngType`, `mf0EmulatorGetVersionData/SignatureData/CounterData`, `mf0NtagGetEmulatorConfig`).

### 9.3 Emulator config knobs (chameleon.dart)
`getMf1EmulatorSettings` (:864-884) decodes `[detection, gen1a, gen2, antiColl, writeMode]` where writeMode byte: 1=denied,2=deceive,3/4=shadow,else normal. Individual setters: `setMf1Gen1aMode`/`setMf1Gen2Mode`/`setMf1UseFirstBlockColl`/`setMf1WriteMode`/`setMf1PrngType`/`setMf1DetectionStatus`. Gen1a/Gen2/anti-collision "magic" emulation flags let the emulated slot itself answer magic-card probes.

---

## 10. Pipeline summary (the algorithm for `chameleon_d.py`)

```
initialize(): reader-mode → detectMf1Support → getType → probe EV1 (auth blk69/keyB/4B791BEA7BCC) → seed EV1 sig keys
checkKeys():  per sector×keyType, chunked mf1CheckKeysOnBlock over (dict ++ builtins);
              on hit → recheckKey across remaining slots; infer key B by reading trailer with A
recoverKeys():
  A. hasBackdoor? → static-encrypted acquire (backdoorInfo); isStaticEncrypted?; prng=NTLevelDetect
  B. if no key & not static-enc & prng≠static: Darkside (checkMf1Darkside → 5× getMf1Darkside+FFI darkside)
  C. if no key & backdoor & prng==weak & not static-enc: 3× NTDistance+NestedNonces(0x64)+FFI nested
  D. pick anchor key; recompute static-enc; maybe prng=backdoor; if none & not backdoor → give up
  E. tries=(backdoor|static)?1:5; for each unknown slot:
        acquire nonces (hard=collect-until-256-with-valid-sum; else NestedNonces at level);
        crack via FFI nested/static_nested/hardnested OR static-encrypted filter (backdoor);
        verify with mf1CheckKeysOnBlock
dumpData(): per block, key A then B; read; patch trailer keys; zero-fill misses
write:      gen1a(unlock 0x40/0x43/0xA0) | gen2(auth per block, trailers-first 3-pass) | gen3(0x90FBCCCC UID) | ul(0xA2 2-pass) | t55xx(writeXtoT55XX+readback)
emulate:    reader-off → enable/activate/setType/setDefault → setMf1AntiCollision → chunked mf1LoadBlockData (or mf0EmulatorWritePages) → name → saveSlotData
```

### Files (all absolute)
- `/Users/tuan/Claude/chameleon/GUI/chameleonultragui/lib/helpers/mifare_classic/recovery.dart` - recovery/dump orchestrator
- `.../lib/helpers/mifare_classic/general.dart` - geometry, dictionaries, backdoor detect, static-encrypted filter
- `.../lib/helpers/mifare_classic/write/{base,gen1,gen2,gen3}.dart` - write strategies
- `.../lib/helpers/mifare_ultralight/{general,write/base}.dart`, `.../lib/helpers/t55xx/write/base.dart` - UL & LF write
- `.../lib/helpers/write.dart` - `AbstractWriteHelper` + routing
- `.../lib/helpers/definitions.dart` - enums, `ChameleonCommand` ids, `NestedNonces` math
- `.../lib/helpers/general.dart` - byte/PRNG/parity helpers
- `.../lib/bridge/chameleon.dart` - `ChameleonCommunicator`, frame protocol, every opcode
- `.../lib/recovery/{recovery,bindings}.dart` - crack-lib FFI + isolate marshaling
- `.../lib/gui/page/slot_manager.dart` + `.../lib/gui/menu/dialogs/slot/edit.dart` - dump→slot emulation flow

---


# CLI - Complete Command Tree

I now have complete coverage. Here is the parity-grade reference.

---

# ChameleonUltra CLI - Functional Parity Reference

Source: `software/script/` in the ChameleonUltra repo. Everything below is drawn from `chameleon_cli_main.py` (REPL), `chameleon_cli_unit.py` (command tree, 10557 lines), `chameleon_cmd.py` (device command layer), `chameleon_com.py` (serial/TCP transport + framing), `chameleon_enum.py` (all enums), `chameleon_utils.py` (CLITree, arg parser, helpers). File+line citations are to `chameleon_cli_unit.py` unless another file is named.

Punctuation note for the rebuild spec: this document deliberately uses only plain hyphens (no em/en dashes) per the target project's style lock. The Python source contains em-dashes in some help strings; when you port those strings, normalize them.

---

## 1. Runtime model (REPL + dispatch)

### 1.1 Boot sequence (`chameleon_cli_main.py`)
1. `if __name__ == '__main__'` (line 184): require Python >= 3.9, else raise.
2. `colorama.init(autoreset=True)`.
3. `chameleon_cli_unit.check_tools()` (unit.py:89) - probes `bin/` for optional MIFARE crack binaries and prints a yellow warning listing any missing (see 7.1).
4. `ChameleonCLI().startCLI()`.

`ChameleonCLI.__init__` creates one `chameleon_com.ChameleonCom()` (the transport, shared across every command via `unit.device_com`).

`startCLI()` (main:153):
- Builds a `CustomNestedCompleter.from_clitree(root)` (utils.py:357) for tab completion.
- Creates a `prompt_toolkit.PromptSession` with `FileHistory(~/.chameleon_history)`.
- Prints `BANNER` (yellow ASCII art, main:28).
- Infinite loop: reads a line, splits on CR/LF into multiple commands (so pasting a multiline script runs each line), pops them one at a time, calls `exec_cmd`. `EOFError`/`KeyboardInterrupt` -> treat as `exit`.

Prompt string (`get_prompt`, main:65): `[USB] chameleon --> ` (green "USB") when `device_com.isOpen()`, else `[Offline] chameleon --> ` (red "Offline"). Rendered via `prompt_toolkit.formatted_text.ANSI`.

### 1.2 Command dispatch (`exec_cmd`, main:87)
1. Empty string -> return.
2. Alias exits: `quit`, `q`, `e` -> `exit`.
3. Comment aliases: if first char in `;#%`, rewrite to `rem <rest>`.
4. `argv = cmd_str.split()`.
5. `get_cmd_node(root, argv)` (main:47) walks the CLITree: at each level, if `cmdline[0]` matches a child `.name`, recurse into that child with the tail; when no child matches or tokens exhausted, return `(node, remaining_tokens)`.
6. If the matched node has no `cls` (it is a group), print a dashed header and list children as help lines (`{ help... }` for subgroups, plain help for leaf commands), then return. This is the "type a group name to see its subcommands" behavior.
7. Otherwise instantiate `unit = tree_node.cls()`, set `unit.device_com = self.device_com` (this also builds `unit.cmd = ChameleonCMD(device_com)` via the setter, cmd.py binding at unit.py:124).
8. `args = unit.args_parser()`; set `args.prog = tree_node.fullname`; `args.parse_args(arg_list)`.
   - If `args.help_requested` (set by the custom `print_help`), return silently.
   - `ArgsParserError` -> print help + yellow error message.
   - `ParserExitIntercept` -> return (swallowed; used for `-h`).
9. Execution wrapper:
   - `unit.before_exec(args)` -> if it returns falsy, abort (this is where device-online / reader-mode / slot-switch preconditions live).
   - `unit.on_exec(args)` inside try; capture any exception as `error`.
   - `unit.after_exec(args)` always runs (used by slot units to restore the previously-active slot).
   - Re-raise `error` if set. `UnexpectedResponseError`/`ArgsParserError` -> print red message; any other `Exception` -> print `CLI exception:` + red traceback.

Parity note: `before -> on -> after` with after guaranteed even on failure is load-bearing for slot commands (they switch the active slot then must switch back).

### 1.3 Argument parser (`ArgumentParserNoExit`, utils.py:57)
- Subclass of `argparse.ArgumentParser` with `add_help=False`, `description="Please enter correct parameters"`, and a `help_requested` flag.
- `exit()` raises `ParserExitIntercept` instead of `sys.exit` (so a bad parse never kills the REPL).
- `error()` raises `ArgsParserError('%(prog)s: error: %(message)s')`.
- A module-level `print_help` (utils.py:81) is monkey-attached and does colorized help: red prog, green "usage:"/"positional arguments:"/"options:", cyan description. It sets `help_requested = True` at the end. (`-h` is not a real argparse flag here since `add_help=False`; help is reached through the custom flow / `dump_help`.)

### 1.4 Transport & wire framing (`chameleon_com.py`)
- `data_frame_sof = 0x11`, `data_max_length = 4096`, `commands = []` (capabilities list).
- `open(port)` (com:87): `tcp:host:port` -> TCP socket; otherwise `serial.Serial(port, baudrate=115200)`. Android has no COM support.
- Frame layout (`make_data_frame_bytes`, com:359): `struct.pack('!BBHHHB{n}sB', SOF=0x11, 0x00, cmd, status, len(data), 0x00, data, 0x00)` then three LRC bytes are patched in: LRC1 over the 1-byte prefix, LRC2 over the 8-byte header, LRC3 over header+data. So the frame is `[SOF][LRC1][cmd:2][status:2][len:2][LRC2][data][LRC3]`, all big-endian.
- `send_cmd_sync(cmd, data, status=0, timeout=3)` (com:410): if `commands` is non-empty and `cmd not in commands`, raise `CMDInvalidException`. Enqueue, block on a per-cmd wait map, raise `TimeoutError` on timeout, raise `CMDInvalidException` if the response status is `INVALID_CMD (0x67)`. Returns a `Response(cmd, status, data, parsed)`.
- `send_cmd_auto(..., close=True)` is used only by `enter_bootloader` (DFU): fire-and-close.

### 1.5 `Response` + `expect_response` decorator (utils.py:254)
`ChameleonCMD` methods are wrapped with `@expect_response(accepted_statuses)`. The wrapper calls the method, checks `ret.status in accepted`, else raises `UnexpectedResponseError(str(Status(status)))`, and on success returns `ret.parsed` (the decoded payload, not the raw Response). Methods set `resp.parsed` themselves before returning. A few methods (e.g. `mf1_detect_support`, `mf1_get_prng_type`, `hf14a_sniff`, `hf14a_auth_trace`, `hf14a_4_*`) are NOT decorated and return the raw `Response` so callers can branch on `.status`/`.data`.

---

## 2. Base classes and shared arg mixins (unit.py:113-874)

| Class | Line | Role / `before_exec` behavior |
|---|---|---|
| `BaseCLIUnit` | 113 | Holds `device_com`/`cmd`; `args_parser`/`on_exec` abstract; `before_exec`/`after_exec` return True. Static `sub_process(cmd, cwd=bin/)` runs a shell subprocess in a `ShadowProcess` thread that streams stdout, exposes `is_running`, `get_time_distance`, `get_ret_code`, `get_output_sync`, `wait_process`, `stop_process`. |
| `DeviceRequiredUnit` | 225 | `before_exec`: if `device_com.isOpen()` False, print "Please connect to chameleon device first (use 'hw connect')." and return False. |
| `ReaderRequiredUnit` | 239 | Extends above; if `is_device_reader_mode()` False, calls `set_device_reader_mode(True)` and prints "Switch to { Tag Reader } mode successfully." |
| `SlotIndexArgsUnit` | 256 | `add_slot_args(parser, mandatory=False)`: `-s/--slot` int, choices 1-8, metavar `<1-8>`, "Default: active slot". |
| `SlotIndexArgsAndGoUnit` | 274 | `before_exec`: record `prev_slot_num = active slot`; if `args.slot` given and != current, `set_active_slot`. `after_exec`: restore `prev_slot_num` if it was changed. This is the "operate on slot N then switch back" wrapper used by every `econfig`/`eload`/`esave`/`eview`/`prng`. |
| `SenseTypeArgsUnit` | 292 | `add_sense_type_args`: mutually exclusive **required** `--hf` / `--lf`. |
| `MF1AuthArgsUnit` | 301 | Parser: `--blk/--block` (required int), `-a/-A` vs `-b/-B` (key type, A default), `-k/--key` (required hex). `get_param()` returns `.block`, `.type` (MfcKeyType), `.key` (6-byte, validated by regex `^[a-fA-F0-9]{12}$`, else ArgsParserError). Base for rdbl/wrbl/view. |
| `HF14AAntiCollArgsUnit` | 342 | `add_hf14a_anticoll_args`: `--uid`, `--atqa`, `--sak`, and mutually exclusive `--ats` / `--delete-ats`. `update_hf14a_anticoll()` validates lengths (UID 4/7/10, ATQA 2 bytes, SAK 1 byte), and if anything changed calls `hf14a_set_anti_coll_data(uid,atqa,sak,ats)`. Returns `(change_requested, change_done, uid, atqa, sak, ats)`. |
| `MFUAuthArgsUnit` | 422 | Parser: `-k/--key` (4 or 16 byte hex; 16-byte = "Ultralight-C not supported yet" error), `-l` swap endianness. `get_param()` returns `.key` (possibly byte-reversed). |
| `LFEMIdArgsUnit` | 474 | `--id` hex; `before_exec` requires 10 or 26 hex chars. |
| `LFHIDIdArgsUnit` | 498 | `-f/--format` (choices = HIDFormat names), `--fc`, `--cn`, `--il`, `--oem`. `check_limits()` enforces per-format FC/CN/IL/OEM maxima (big table at 545-577). |
| `LFHIDIdReadArgsUnit` | 614 | Just `-f/--format` as a hint (not required). |
| `LFIOProxIdArgsUnit` | 636 | `--ver`, `--fc` (str, parsed int(base 0)), `--cn`, `--raw8` (16 hex). `checksum5`, `parse_raw8`. |
| `LFIOProxReadArgsUnit` | 697 | `-v/--verbose`. |
| `LFVikingIdArgsUnit` | 704 | `--id` requires 8 hex. |
| `LFJablotronIdArgsUnit` | 726 | `--id` requires 10 hex (5 bytes). |
| `LFIdteckIdArgsUnit` | 804 | `--id`: 16 hex (full frame) or 8 hex (payload, preamble `4944544B` auto-prepended). Warns on bad preamble/checksum via `_idteck_frame_info` (778). |
| `LFPacIdArgsUnit` | 6138 | `--cn` (8 ASCII) XOR `--raw` (32 hex T55xx bitstream, decoded via `pac_decode_raw`). Enforces 7-bit bytes. |
| `TagTypeArgsUnit` | 853 | `-t/--type` required, choices = `TagSpecificType.list()` names. |

---

## 3. Complete command tree

Groups (unit.py:876-902): `root` -> `hw` (-> `slot`, `settings`), `hf` (-> `14a`, `mf`, `mfu`, `des`), `lf` (-> `em` (-> `4x05`, `410x`), `hid` (-> `prox`), `ioprox`, `pac`, `viking`, `jablotron`, `generic`, `idteck`), `data`, `emv`.

| Full command | Line | Unit class | One-line effect |
|---|---|---|---|
| `clear` | 905 | RootClear | `os.system("clear"/"cls")` |
| `rem [comment...]` | 916 | RootRem | print UTC ISO timestamp + "remark: ..." |
| `exit` (`quit`/`q`/`e`) | 933 | RootExit | close port, `sys.exit(996)` |
| `dump_help [-d] [-g]` | 946 | RootDumpHelp | dump entire tree (usage/desc/groups) |
| `hw connect [-p PORT]` | 999 | HWConnect | open serial, load capabilities, print model+fw |
| `hw disconnect` | 1059 | HWDisconnect | close port |
| `hw mode [-r|-e]` | 1070 | HWMode | get/set reader vs emulator mode |
| `hw chipid` | 1097 | HWChipId | `get_device_chip_id` |
| `hw address` | 1108 | HWAddress | `get_device_address` (BLE) |
| `hw version` | 1119 | HWVersion | app version + git + model |
| `hw dfu` | 7088 | HWDFU | reboot into bootloader |
| `hw factory_reset [--force]` | 7229 | HWFactoryReset | `wipe_fds`, closes port |
| `hw battery` | 7255 | HWBatteryInfo | voltage mV + percentage |
| `hw raw -c/-n -d -t` | 7424 | HWRaw | send arbitrary command frame |
| `hw slot list [--short]` | 6525 | HWSlotList | full slot inventory |
| `hw slot change -s` | 6794 | HWSlotSet | set active slot (mandatory `-s`) |
| `hw slot type -s -t` | 6807 | HWSlotType | set slot tag type + default data |
| `hw slot init -s -t` | 6849 | HWSlotInit | reset slot data to default |
| `hw slot enable -s --hf/--lf` | 6868 | HWSlotEnable | enable a sense type |
| `hw slot disable -s --hf/--lf` | 6890 | HWSlotDisable | disable a sense type |
| `hw slot delete -s --hf/--lf` | 6827 | HWDeleteSlotSense | delete a sense type's data |
| `hw slot nick -s --hf/--lf [-n|-d]` | 7006 | HWSlotNick | get/set/delete slot nickname |
| `hw slot prng -s [-t 0|1|2]` | 6757 | HWSlotPrng | get/set emulator PRNG type |
| `hw slot store` | 7045 | HWSlotUpdate | persist slots to flash |
| `hw slot openall` | 7057 | HWSlotOpenAll | init all 8 slots to MFC1k+EM410x |
| `hw settings animation [-m]` | 7108 | HWSettingsAnimation | get/set animation mode |
| `hw settings sleeptimeout [-s]` | 7136 | HWSettingsSleepTimeout | get/set wake timeout 5-60s |
| `hw settings btnpress [-a/-b][-s/-l][-f]` | 7274 | HWButtonSettingsGet | get/set button functions |
| `hw settings blekey [-k]` | 7348 | HWSettingsBLEKey | get/set 6-digit BLE key |
| `hw settings blepair [-e/-d]` | 7383 | HWBlePair | show/enable/disable pairing |
| `hw settings bleclearbonds [--force]` | 7170 | HWSettingsBleClearBonds | delete all BLE bonds |
| `hw settings store` | 7191 | HWSettingsStore | save settings to flash |
| `hw settings reset [--force]` | 7206 | HWSettingsReset | reset settings to defaults |
| `hf 14a scan` | 1228 | HF14AScan | basic 14a scan |
| `hf 14a info` | 1276 / 7743 | HF14AInfo (wins) / HF14ASniff (shadow) | deep scan (see 6.1 quirk) |
| `hf 14a config [--std --bcc --cl2 --cl3 --rats]` | 1134 | HF14AConfig | 14a low-level select overrides |
| `hf 14a raw ...` | 7491 | HF14ARaw | send raw ISO14443A frame |
| `hf 14a sniff [--timeout]` | 7744 | HF14ASniff | sniff reader frames (tag mode) |
| `hf 14a auth-trace --blk -k [-a/-b][-t]` | 7892 | HF14AAuthTrace | full reader-side auth wire trace |
| `hf mf nested ...` | 1289 | HFMFNested | nested key recovery |
| `hf mf darkside` | 1446 | HFMFDarkside | darkside key recovery |
| `hf mf hardnested ...` | 1527 | HFMFHardNested | hardnested key recovery |
| `hf mf senested ...` | 2188 | HFMFStaticEncryptedNested | RF08S static-encrypted (backdoor) |
| `hf mf autopwn [-k]` | 2348 | HFMFAutopwn | full auto key+dump orchestrator |
| `hf mf fchk ...` | 2743 | HFMFFCHK | fast key check on sectors |
| `hf mf rdbl ...` | 2946 | HFMFRDBL | read one block |
| `hf mf wrbl ... -d` | 2959 | HFMFWRBL | write one block |
| `hf mf view [-d|-k]` | 2986 | HFMFView | dump/hexdump from file or tag |
| `hf mf dump -f -d [-t]` | 3078 | HFMFDump | dump 1k tag to file |
| `hf mf clone -f -d [-t -a]` | 3161 | HFMFClone | write dump to magic tag |
| `hf mf value ...` | 3270 | HFMFVALUE | value-block get/set/inc/dec/restore |
| `hf mf elog [--decrypt]` | 3682 | HFMFELog | mfkey32 detection-log crack |
| `hf mf eload -f [-s -t]` | 3781 | HFMFELoad | load dump into emulator |
| `hf mf esave -f [-s -t]` | 3839 | HFMFESave | read emulator memory to file |
| `hf mf eview [-s]` | 3905 | HFMFEView | hexdump emulator memory |
| `hf mf econfig [-s ...]` | 3942 | HFMFEConfig | MFC emulator settings |
| `hf mfu ...` (11 cmds) | 4177-5783 | see 5.4 | Ultralight/NTAG |
| `hf des info` | 10190 | HfDesInfo | DESFire version/UID/AIDs |
| `hf des chk ...` | 10256 | HfDesChk | DESFire key check |
| `lf em 410x read` | 5783 | LFEMRead | scan EM410x |
| `lf em 410x write --id` | 5795 | LFEM410xWriteT55xx | write EM410x/Electra to T55xx |
| `lf em 410x econfig [-s --id]` | 6909 | LFEM410xEconfig | get/set EM410x emu id |
| `lf em 4x05 read` | 7618 | LFEm4x05Read | scan EM4x05/4x69 |
| `lf hid prox read/write/econfig` | 5813/5836/5872 | LFHIDProx* | HID Prox |
| `lf ioprox read/write/econfig` | 5922/5938/5976 | LFIOProx* | ioProx |
| `lf pac read/write/econfig` | 6124/6184/6199 | LFPac* | PAC/Stanley |
| `lf viking read/write/econfig` | 6225/6237/6928 | LFViking* | Viking |
| `lf jablotron read/write/econfig` | 6952/6966/6980 | LFJablotron* | Jablotron |
| `lf idteck write/econfig` | 6251/6265 | LFIdteck* | IDTECK (no separate read) |
| `lf generic adcread` | 6498 | LFADCGenericRead | raw ADC read |
| `lf clone -t ...` | 6299 | LFT55xxClone | universal T55xx clone |
| `lf sniff [--timeout --out --hex]` | 7649 | LFSniff | raw LF ADC capture |
| `data hexsamples [-n]` | 8662 | DataHexsamples | hexdump last LF capture |
| `data plot [--start --len --ascii]` | 8703 | DataPlot | plot last LF capture |
| `data manrawdecode [--clock --invert]` | 8885 | DataManrawdecode | Manchester decode capture |
| `data modulation` | 8974 | DataModulation | guess clock/modulation |
| `emv scan [-f -s]` | 9113 | EMVScan | full EMV read |
| `emv load [-f -s --clear --cmd --resp --defaults]` | 9545 | EMVLoad | load EMV into HF14A_4 slot |
| `emv apdu [--timeout]` | 9758 | EMVApdu | interactive APDU relay |
| `emv debug` | 9522 | EMVDebug | T=CL emulation counters |

Names the task listed that do NOT exist as distinct commands in this build (map accordingly): `hf mf staticnested` -> covered by `hf mf nested` when PRNG=Static (uses the `staticnested` C tool) and by `hf mf senested` for RF08S; `hf mf eread` -> use `hf mf esave` (reads emulator memory back to a file); `hf mf sim` -> emulation is done via slots (`hw slot type -t MIFARE_1024`, `hf mf eload`, `hw mode -e`), there is no `sim` verb.

---

## 4. Priority units - full parity detail

Everything in this section is exactly what the terminal app does: the device commands issued (with `ChameleonCMD` method + `Command` enum id), the external C-tool invocations, output parsing, and edge/error handling.

### 4.1 `hf mf nested` (HFMFNested, 1289)
Args: `--blk/--known-block` (req int), `-a/-A` | `-b/-B` (known key type, A default), `-k/--key` (req 12-hex), `--tblk/--target-block` (req int), `--ta/--tA` | `--tb/--tB` (target type, A default).

`on_exec` (1418): validate key regex; if known == target block+type -> print red "Target key already known" and return; call `recover_a_key`.

`recover_a_key` (1338):
1. `nt_level = cmd.mf1_detect_prng()` -> `MF1_DETECT_PRNG (2002)`, prints "NT vulnerable: StaticNested/Nested/HardNested".
2. If `nt_level == 2` (Hard): print "[!] Use hf mf hardnested", return None.
3. If `nt_level == 0` (Static): `nt_uid_obj = cmd.mf1_static_nested_acquire(block_known,type_known,key_known,block_target,type_target)` -> `MF1_STATIC_NESTED_ACQUIRE (2003)`. Build tool args: `"{uid} {int(type_target)}"` then for each nt: ` {nt} {nt_enc}`. Tool = `staticnested`.
4. Else (Weak): `dist = cmd.mf1_detect_nt_dist(...)` -> `MF1_DETECT_NT_DIST (2005)`; `nts = cmd.mf1_nested_acquire(...)` -> `MF1_NESTED_ACQUIRE (2006)`. Args: `"{uid} {dist}"` then per nt: ` {nt} {nt_enc} {par}`. Tool = `nested`.
5. Command line: `./nested {args}` (or `nested.exe` on win32). Run via `self.sub_process` (streams, prints `[ Time elapsed X.Xs ]` every 0.1s).
6. On ret code 0: scan stdout for any 12-hex substring per line -> candidate keys. Print `[N candidate key(s) found ]`. For each candidate: `cmd.mf1_auth_one_key_block(block_target,type_target,key_bytes)` -> `MF1_AUTH_ONE_KEY_BLOCK (2007)`; return the first that authenticates. Non-zero ret -> return None.

Output: on success `Block {tblk} Type {A/B} Key Found: {key}` (green); else yellow "No key found, you can retry."

### 4.2 `hf mf darkside` (HFMFDarkside, 1446)
No args. `on_exec` hardcodes `recover_key(0x03, MfcKeyType.A)` (block 3, key A).

`recover_key` (1457): loop up to 0xFF retries:
1. `cmd.mf1_darkside_acquire(block_target, type_target, first_recover, sync_max=30)` -> `MF1_DARKSIDE_ACQUIRE (2004)` (timeout `sync_max*10`). `first_recover` True only on first iteration.
2. If `resp[0] != MifareClassicDarksideStatus.OK`: print `Darkside error: {status}` (CANT_FIX_NT / LUCKY_AUTH_OK / NO_NAK_SENT / TAG_CHANGED) and break.
3. If `darkside_obj['par'] != 0` (NXP workaround) clear the accumulated `darkside_list`.
4. Append obj; build args `"{uid}"` + per item ` {nt1} {ks1} {par} {nr} {ar}`.
5. Run `./darkside {args}` via `sub_process`, `wait_process()`.
6. If stdout contains "key not found": print retry message, `retry_count += 1`, continue.
7. Else parse 12-hex candidates, `mf1_auth_one_key_block(0x03, A, key)` each; return first that auths.

Output: "Key Found: {key}" or "Key recover fail."

### 4.3 `hf mf hardnested` (HFMFHardNested, 1527)
Args: `--blk` (req), `-a|-b`, `-k` (req 12-hex), `--tblk` (req), `--ta|--tb`, `--slow` (more nonces), `--keep-nonce-file` (keep `nonces.bin`), `--max-runs` (default 200), `--max-attempts` (default 3).

`recover_key` (1592) - the most complex flow:
- Outer loop over `max_attempts`. Each attempt:
  1. `cmd.hf14a_scan()` -> `HF14A_SCAN (2000)`. Fail/no tag -> retry attempt or fail. Multiple tags -> hard fail.
  2. Extract UID; normalize to 4-byte "uid_for_file": last 4 bytes for 7/10-byte UIDs (slices `[0:4]`, `[3:7]`, `[6:10]`). Unexpected length -> fail.
  3. Build nonce-file header: `uid_for_file + struct.pack('!BB', block_target, type_target & 0x01)`.
  4. Inner loop up to `max_runs`: re-scan to confirm tag still present/same UID (lost -> break); `cmd.mf1_hard_nested_acquire(slow, block_known,type_known,key_known, block_target,type_target)` -> `MF1_HARDNESTED_ACQUIRE (2013)` (timeout 30). Raw nonces are 9-byte records `!IIB` = (nt, nt_enc, par).
  5. MSB tracking: for each record, `msb = (nt_enc>>24)&0xFF`; track 256 unique MSBs. On a new MSB, `parity_bit = hardnested_utils.evenparity32((nt_enc & 0xFF000000)|(par & 0x08))`; accumulate `msb_parity_sum`. Live progress `Unique MSBs: N/256 | Current Sum: S`.
  6. When all 256 MSBs seen: if `msb_parity_sum in hardnested_utils.hardnested_sums` -> success, break; else INVALID -> break to restart attempt.
  - Exceptions handled distinctly: `CMDInvalidException` (firmware too old -> return None), `UnexpectedResponseError`, `TimeoutError`, generic -> break inner loop.
- After success: write `nonces_buffer` to a `tempfile.NamedTemporaryFile(suffix='.bin', prefix='hardnested_nonces_', dir='.')`, close it, then `execute_tool("hardnested", [abspath(nonce_file)])` (utils.py:213 - runs `./hardnested` in `bin/`, captures stdout via temp log).
- Parse output: lines starting with `"Key found: "` -> regex first 12-hex after the prefix -> candidate list.
- Verify: for each candidate, re-scan (tag lost -> abort), `mf1_auth_one_key_block(block_target,type_target,key)`; return first success ("Success!" green).
- `finally`: if `keep_nonce_file`, `os.replace` temp -> `nonces.bin`; else delete temp.

Output: `Key Found: Block {tblk} Type {A/B} Key = {KEY}` or red "HardNested attack failed to recover the key."

### 4.4 `hf mf senested` (HFMFStaticEncryptedNested, 2188) - RF08S static-encrypted / backdoor
Args: `--key/-k` (backdoor key, default `A396EFA4E24F`; other known: `A31667A8CEC1`, `518B3354E760`), `--sectors/-s` (default 16), `--starting-sector` (default 0).

`on_exec` calls `senested(key, starting_sector, sectors, sectors)` then `print_key_table(key_map)` (utils.py:161).

`senested` (2220):
1. `acquire_datas = cmd.mf1_static_encrypted_nested_acquire(bytes.fromhex(key), sectors, starting_sector)` -> `MF1_ENC_NESTED_ACQUIRE (2014)` (timeout 30). Parsed = `{uid, nts:{a:[...], b:[...]}}` where each entry has `nt` (reconstructed full nt), `nt_enc`, `parity` (4-char string). If empty -> print "Failed to collect nonces, is card present and has backdoor?".
2. For each sector in `[starting_sector, stopping_sector)`:
   - `execute_tool("staticnested_1nt", [uid, sector2d, nt_a_8hex, nt_enc_a_8hex, parity_a_4])` and again for the B nonce. These write `.dic` candidate files into `tempfile.gettempdir()`.
   - `execute_tool("staticnested_2x1nt_rf08s", [a_key_dic, b_key_dic])` -> produces `{b_key_dic}_filtered.dic`.
   - Read filtered B candidates; in chunks of 64, `cmd.mf1_check_keys_on_block(sector*4+3, 0x61, keys)` -> `MF1_CHECK_KEYS_ON_BLOCK (2015)` (timeout 10). First hit -> `key_map["B"][sector]`.
   - If a B key found: `execute_tool("staticnested_2x1nt_rf08s_1key", [nt_b_8hex, b_key, a_key_dic])` -> A candidates; `mf1_check_keys_on_block(sector*4+3, 0x60, ...)`. If fast method fails, fall back to the full `{a_key_dic}_filtered.dic` in 64-key chunks. Store into `key_map["A"][sector]`.
3. Cleanup: `os.remove` every `keys_*.dic` in tempdir.
Progress prints estimate "will take up to N seconds for K keys" using `check_speed = 1.95` sec/64 keys; uses `tqdm_if_exists` (tqdm if installed).

### 4.5 `hf mf autopwn` (HFMFAutopwn, 2348) - the orchestrator
Args: `-k/--key` (optional known 12-hex).

`autopwn(key_known)` (2530):
1. `getuid()`/`getsak()` via `hf14a_scan`; `get_mf_size(sak)` maps SAK byte -> (label, sectors): `18`->4k/40, `08`/`01`->1k/16, `09`->mini/5, `10`->2k/32; unknown -> "1k"/16 with a warning.
2. `nt_level = mf1_detect_prng()`.
3. Key dictionary is `current_keys_found: {index -> 6-byte key}` where index `= sector*2 + (0=A,1=B)`.
4. First tries: if `key_known`, `try_key(key, full_mask)`; always `try_key(FFFFFFFFFFFF, mask=all-zero)`. `try_key` = `mf1_check_keys_of_sectors(mask, [key])` -> `MF1_CHECK_KEYS_OF_SECTORS (2012)`. `merge_found_sector_keys` folds the returned `sectorKeys` in.
5. If still nothing: run `HFMFDarkside.recover_key(0x03, A)` (instantiated with `__new__` + shared `_device_cmd`); on success add as key index 0 and reuse-check.
6. If all `total = sectors*2` keys found -> done.
7. Otherwise branch by PRNG:
   - `nt_level == 2` (Hard): for each missing key, pick a random known key (`choose_random_known_key`), run `HFMFHardNested.recover_key(False, block_known, type_known, key, block_target=(idx//2)*4, key_type_target, keep=False, max_runs=200, max_attempts=3)`; reuse-check each found key against remaining sectors. If still missing, fall to `run_senested`.
   - `nt_level == 0` (Static): `run_senested` directly.
   - else (Weak): pick a random known key, run `HFMFNested.recover_a_key(...)` per missing key + reuse-check; then `run_senested` for leftovers.
8. `run_senested` (2465): interactive - asks "proceed? [y/n]", optionally custom backdoor key (validated `[A-F0-9]{12}`), then per missing sector calls `HFMFStaticEncryptedNested.senested(backdoor, sector, sector+1, sectors)` and merges via `try_key` with negated mask.

After `autopwn`: `print_key_table` (box-drawing A/B table), then interactive `save_keys_to_file` (writes `.dic` of unique keys + `.key` binary = per sector KeyA||KeyB, unknown = 6 zero bytes) and `dump_card_to_file` (reads every block with KeyB then KeyA fallback via `mf1_read_one_block`, unreadable -> 16 zero bytes; block layout: sectors <32 are 4 blocks, >=32 are 16 blocks starting at 128).

Mask helpers: `bits_to_10byte_mask`, `mask_from_keys`, `neg_bytes` (bitwise NOT over 10 bytes). Mask semantics: 1 bit per sector-key, bit set = skip.

### 4.6 `hf mf fchk` (HFMFFCHK, 2743) - fast key check
Args: card-size flags `--mini`/`--1k`(default)/`--2k`/`--4k` (set `maxSectors` const 5/16/32/40), positional `keys` (0+ 12-hex), `--key` (`.key` binary file), `--dic` (`.dic` text file, note: `load_dic_file` at unit.py:85 is a stub that returns keys unchanged - a known no-op), `--export-key`/`--export-dic` (OVERWRITE), `-m/--mask` (20-hex, default all zero, 1 bit per sectorKey = skip).

`on_exec`: gather keys from args (regex-validated) + `load_key_file`. Build `mask` bytearray, then force-mask sectors beyond `maxSectors` (`mask[i//4] |= 3<<(6-i%4*2)`). `check_keys` iterates keys in chunks of 20: `mf1_check_keys_of_sectors(mask, chunk)`; on each response OR-in the `found` bitmap into mask and accumulate `sectorKeys`; stop early if status != HF_TAG_OK or "All sectorKey is found or masked". Prints elapsed time and a Sec/Blk/keyA/res/keyB/res table. Exports if requested (`.key` = per-sector A||B with 6 zero bytes for unknown; `.dic` = unique found keys uppercase).

### 4.7 `hf mf rdbl` / `hf mf wrbl`
- `rdbl` (2946, MF1AuthArgsUnit): `mf1_read_one_block(block, type, key)` -> `MF1_READ_ONE_BLOCK (2008)`; print `Data: {hex}`.
- `wrbl` (2959): adds `-d/--data` (req 32-hex). `mf1_write_one_block(block, type, key, data)` -> `MF1_WRITE_ONE_BLOCK (2009)`; print green "Write done." / red "Write fail." based on bool.

### 4.8 `hf mf view` (HFMFView, 2986)
Args: size flags, `-d/--dump-file` (rb) XOR `-k/--key-file` (r). If dump-file: read bytes directly. If key-file: parse `A:B` hex pairs per line (must equal maxSectors else ArgsParserError); for each block, try `mf1_read_one_block` with KeyB then KeyA. Finally `print_mem_dump(data, 16)` (utils.py:144 - blk/data/ascii table). Note the key-file format here is `A:B` colon-separated, distinct from the `.dic` newline format used by dump/clone.

### 4.9 `hf mf dump` (HFMFDump, 3078)
Args: `-t/--dump-file-type` (bin|hex), `-f/--dump-file` (wb, req), `-d/--dic` (r, req). Content type inferred from extension when `-t` omitted: `.bin`->bin, `.eml`->hex, else exception.
Keys loaded from `.dic` (one hex/line, `line[:-1]` strips newline). Fixed 16 sectors (1k only). Per sector: try each key with KeyB then KeyA on block `4*s` to find a working `(key, typ)`; no key -> exception "No key found for sector {s}". Then read all 4 blocks with `mf1_read_one_block`. Buffer as raw bytes (bin) or ascii-hex (hex). Write to file.

### 4.10 `hf mf clone` (HFMFClone, 3161)
Args: `-t` (bin|hex), `-a/--clone-access` (bool, write original ACL - can brick), `-f/--dump-file` (rb, req), `-d/--dic` (r, req). Read dump into buffer; must be 16-byte aligned and <= 256 blocks. Per sector: find KeyA and/or KeyB that read block `4*s` (both keys sought). No key -> exception. Per block: take the 16 bytes from the dump; for the sector-trailer (b==3) if `--clone-access` is NOT set, overwrite access bytes with generic `ff0780` (`block_data[:6] + ff0780 + block_data[9:]`) so the tag stays writable. Write via `mf1_write_one_block` KeyB then KeyA fallback.

### 4.11 `hf mf value` (HFMFVALUE, 3270)
Mutually exclusive ops: `--get`, `--set X`, `--inc X`, `--dec X`, `--res/--cp`. Src: `--blk`, `-a|-b`, `-k`. Dst (for inc/dec/res): `--tblk`, `--ta|--tb`, `--tkey` (all default to src). `get_value` reads block, unpacks `<iiiBBBB` and validates value-block integrity (val1==val3, val1+val2==-1, addr constraints). `set_value` writes a well-formed value block. inc/dec/res call `mf1_manipulate_value_block(src..., operator, operand, dst...)` -> `MF1_MANIPULATE_VALUE_BLOCK (2011)` with operators `DECREMENT=0xC0`/`INCREMENT=0xC1`/`RESTORE=0xC2`, then re-read dst.

### 4.12 Emulator: eload / esave / eview / econfig / elog

- `hf mf eload` (3781, SlotIndexArgsAndGo): `-s`, `-f/--file` (req), `-t` (bin|hex). Type inferred by extension (`.bin`/`.eml`). Buffer must be 16-aligned and <=256 blocks. Writes in chunks of `max_blocks = (data_max_length-1)//16` via `mf1_write_emu_block_data(block, chunk)` -> `MF1_WRITE_EMU_BLOCK_DATA (4000)`. (This is the "eload" the task references.)
- `hf mf esave` (3839): reads emulator memory back to a file. Determines block_count from active slot's HF tag type (Mini=20, 1k=64, 2k=128, 4k=256; else exception "not Mifare Classic/Plus in SL1 mode"). Reads in chunks of `min(count, max_blocks, 32)` via `mf1_read_emu_block_data(index, count)` -> `MF1_READ_EMU_BLOCK_DATA (4008)`. Writes bin or ascii-hex-per-line. (This is the functional "eread".)
- `hf mf eview` (3905): same read logic, then `print_mem_dump`.
- `hf mf econfig` (3942, SlotIndexArgsAndGo + HF14AAntiColl): reads `hf14a_get_anti_coll_data` (`HF14A_GET_ANTI_COLL_DATA 4018`), validates slot is MFC. Toggles via mutually-exclusive pairs: `--enable/--disable-gen1a` (`MF1_SET_GEN1A_MODE 4011`), `--enable/--disable-gen2` (`4013`), `--enable/--disable-block0` (`MF1_SET_BLOCK_ANTI_COLL_MODE 4015`), `--write MODE` (choices from `MifareClassicWriteMode.list()`; `MF1_SET_WRITE_MODE 4017`), `--enable/--disable-log` (`MF1_SET_DETECTION_ENABLE 4004`), `--enable/--disable_field_off_do_reset` (`MF1_SET_FIELD_OFF_DO_RESET 4038`), plus anticoll `--uid/--atqa/--sak/--ats/--delete-ats`. Reads current config via `mf1_get_emulator_config` (`4009`) and `mf1_get_field_off_do_reset` (`4039`). With no change requested, prints the full settings block (Type/UID/ATQA/SAK/ATS/Gen1A/Gen2/block0/Write mode/Log/FIELD_OFF_DO_RESET). Each toggle prints a yellow "already enabled/disabled" if a no-op.
- `hf mf elog` (3682): `--decrypt`. Without flag: `mf1_get_detection_count()` (`MF1_GET_DETECTION_COUNT 4005`). With flag: download all logs via `mf1_get_detection_log(index)` (`MF1_GET_DETECTION_LOG 4006`, each record = block/type/is_nested/uid/nt/nr/ar). Group by uid->block->keytype, then `decrypt_by_list` runs `mfkey32v2` over all nonce pairs using a `multiprocessing.Pool(cpu_count())` and an `ItemGenerator` (3627) that lazily enumerates pairs and prunes pairs whose reader-key is already known (via `Crypto1.mfkey32_is_reader_has_key`). Prints running "K records => P/C combinations. N key(s) found".

### 4.13 Slot commands (`hw slot *`, 6525-7086)
- `list [--short]` (6525): `get_slot_info` (`GET_SLOT_INFO 1019`), `get_active_slot` (`1018`), `get_enabled_slots` (`GET_ENABLED_SLOTS 1023`), `get_all_slot_nicks` (`GET_ALL_SLOT_NICKS 1038`). For each of 8 slots prints HF/LF type + enabled/disabled + nickname. When not `--short` and slot enabled: switches active slot to read per-slot details (anticoll data for HF; for MFC also `mf1_get_emulator_config` + `mf1_get_prng_type`; for LF calls the type-specific `*_get_emu_id`). Restores original active slot at end.
- `change -s` (6794): `set_active_slot` (`SET_ACTIVE_SLOT 1003`). Mandatory `-s`.
- `type -s -t` (6807): `set_slot_tag_type` (`SET_SLOT_TAG_TYPE 1004`) + `set_slot_data_default` (`SET_SLOT_DATA_DEFAULT 1005`).
- `init -s -t` (6849): only `set_slot_data_default`.
- `enable/disable -s --hf/--lf` (6868/6890): `set_slot_enable` (`SET_SLOT_ENABLE 1006`, enabled bool). (Note: `disable` reads `args.slot` directly and does not default to active slot, unlike `enable`.)
- `delete -s --hf/--lf` (6827): `delete_slot_sense_type` (`DELETE_SLOT_SENSE_TYPE 1024`).
- `nick -s --hf/--lf [-n NAME | -d]` (7006): `set_slot_tag_nick`(`1007`) / `delete_slot_tag_nick`(`1021`) / `get_slot_tag_nick`(`1008`).
- `prng -s [-t 0|1|2]` (6757): read `mf1_get_prng_type`(`MF1_GET_PRNG_TYPE 4040`) or set `mf1_set_prng_type`(`4041`).
- `store` (7045): `slot_data_config_save` (`SLOT_DATA_CONFIG_SAVE 1009`).
- `openall` (7057): for all 8 slots set type MFC1k + EM410x, default data both, enable HF+LF, then `slot_data_config_save`.

### 4.14 `hw dfu` (HWDFU, 7088)
`cmd.enter_bootloader()` -> `send_cmd_auto(ENTER_BOOTLOADER=1010, close=True)` (fire-and-forget, closes the port). Prints "Application restarting..." / "Enter success", `time.sleep(0.1)` to let the comm thread flush.

### 4.15 `lf em 410x` (5783-5810, 6909)
- `read` (5783): `em410x_scan()` -> `EM410X_SCAN 3000`, parsed `(tag_type, uid)`; print type + hex uid.
- `write --id` (5795): id must be 10 hex (5-byte EM410x) or 26 hex (13-byte Electra); `em410x_write_to_t55xx(id)` -> `EM410X_WRITE_TO_T55XX 3001` (5-byte) or `EM410X_ELECTRA_WRITE_TO_T55XX 3006` (13-byte). The write payload wraps id + `new_key=20 20 66 66` + `old_keys=[51 24 36 48, 19 92 04 27]` (cmd.py:13, standard T55xx password unlock set).
- `econfig [-s --id]` (6909, SlotIndexArgsAndGo): with `--id` -> `em410x_set_emu_id` (`EM410X_SET_EMU_ID 5000`); without -> `em410x_get_emu_id` (`5001`). The set path validates the active LF slot type is EM410x/Electra (cmd.py:945) to size the id (5 or 13 bytes).

---

## 5. Remaining groups (parity enumeration)

### 5.1 `hw` misc
- `connect [-p PORT]` (999): auto-detect by USB VID `0x6868` (WSL uses PowerShell `Get-PnPDevice` for `VID_6868&PID_8686`); `device_com.open(port)`; load `get_device_capabilities` (`GET_DEVICE_CAPABILITIES 1035`) into `device_com.commands`; print model (`get_device_model 1033`: 0=Ultra,1=Lite) + `get_app_version 1000`.
- `mode [-r|-e]` (1070): `change_device_mode` (`CHANGE_DEVICE_MODE 1001`); no flag prints current mode (`GET_DEVICE_MODE 1002`).
- `chipid`/`address`/`version`: `GET_DEVICE_CHIP_ID 1011` / `GET_DEVICE_ADDRESS 1012` / `GET_APP_VERSION`+`GET_GIT_VERSION 1017`.
- `factory_reset --force` (7229): `wipe_fds` (`WIPE_FDS 1020`, closes port).
- `battery` (7255): `get_battery_info` (`GET_BATTERY_INFO 1025`) -> (mV, %); warns if <30%.
- `raw -c COMMAND | -n NUM -d HEX -t SEC` (7424): `device.send_cmd_sync(cmd, data, status=0, timeout)` directly; `-c` accepts a `Command` name (sorted choices), `-n` accepts any numeric id (for not-yet-known commands). Prints command name, status name+desc, data hex.

### 5.2 `hw settings`
`animation -m MODE` (`SET/GET_ANIMATION_MODE 1015/1016`; modes FULL/MINIMAL/SYMMETRIC/NONE). `sleeptimeout -s SEC` (5-60, warns >=30; `SET/GET_SLEEP_TIMEOUT 1040/1039`). `btnpress [-a/-b] [-s/-l] [-f FUNC]` (`GET/SET_BUTTON_PRESS_CONFIG 1026/1027`, long variants `1028/1029`; functions NONE/NEXTSLOT/PREVSLOT/CLONE/BATTERY/FIELDGEN). `blekey -k KEY` (6 ASCII digits; `SET_BLE_PAIRING_KEY 1030`/`GET 1031`). `blepair -e/-d` (`GET/SET_BLE_PAIRING_ENABLE 1036/1037`). `bleclearbonds --force` (`DELETE_ALL_BLE_BONDS 1032`). `store` (`SAVE_SETTINGS 1013`). `reset --force` (`RESET_SETTINGS 1014`). Every mutating settings command reminds "Do not forget to store your settings in flash!".

### 5.3 `hf 14a`
- `scan` (1228): `hf14a_scan` -> print UID/ATQA(+little-endian hex)/SAK/ATS.
- `info` (1276): `HF14AScan.scan(deep=True)`: adds SAK-guess (`type_id_SAK_dict`, unit.py:58) and, for single MFC tags, `mf1_detect_support` (`MF1_DETECT_SUPPORT 2001`) + `mf1_detect_prng`.
- `config [--std --bcc --cl2 --cl3 --rats]` (1134): `hf14a_get_config`/`hf14a_set_config` (`HF14A_GET/SET_CONFIG 2200/2201`). Each option std/fix|force/ignore|skip (values 0/1/2). Controls BCC handling and forced/skipped CL2/CL3/RATS during select.
- `raw` (7491): flags map to the `hf14a_raw` option bitfield (cmd.py:336, a `ctypes.BigEndianStructure`): `-a` activate_rf, `-s` auto_select, `-c` append_crc, `-r` no wait_response, `-cc` check_response_crc, `-k` keep_rf, `-b BITS` partial-byte, `-d HEX`, `-t MS`. `--bits` and `--crc` are mutually exclusive. `HF14A_RAW 2010`.
- `sniff [--timeout MS]` (7744): `hf14a_sniff` (`HF14A_SNIFF 2020`, 1-30000ms). Parses packed `[bits_be16][data]` frames; bit15 = direction; strips ISO14443-A parity when `szBits%9==0`; decodes MIFARE AUTH/NT/NR||AR context.
- `auth-trace --blk -k [-a/-b][-t MS]` (7892): `hf14a_auth_trace` (`HF14A_AUTH_TRACE 2017`); runs a full reader-side anticoll+select+RATS+AUTH and returns every wire frame with host-side Crypto1 decryption. Status legend HF_TAG_OK/MF_ERR_AUTH/HF_ERR_STAT/HF_TAG_NO.

### 5.4 `hf mfu` (Ultralight / NTAG)
| Cmd | Line | Args | Behavior / device calls |
|---|---|---|---|
| `ercnt -c` | 4177 | counter idx | `mfu_read_emu_counter_data` (`MF0_NTAG_GET_COUNTER_DATA 4027`); prints value + tearing flag |
| `ewcnt -c -v [-t]` | 4196 | idx, 24-bit value, reset-tearing | `mfu_write_emu_counter_data` (`4028`) |
| `rdpg -p [-k -l]` | 4227 | page | optional PWD_AUTH (`0x1B`+key via `hf14a_raw`), then READ `0x30 page`; prints PACK + 4-byte data |
| `wrpg -p -d [-k -l]` | 4295 | page, 4-byte data | optional auth, then WRITE `0xA2 page data`; checks `0x0A` ACK |
| `rcnt -c [-k -l]` | 4573 | counter | READ_CNT `0x39 c`; prints 3-byte value |
| `dump [-p -q -f -t -k -l]` | 4641 | start page, qty, file | scans, verifies ATQA `4400`/SAK `00`; autodetects type via GET_VERSION `0x60` + AUTH probe `0x1A` (size_map for ULEV1/NTAG21x/ULC), then reads pages `0x30 i` until stop; writes bin/eml |
| `version` | 4910 | - | `hf14a_raw` GET_VERSION `0x60`; print 8 bytes |
| `signature` | 4933 | - | READ_SIG `0x3C 00`; print 32 bytes |
| `authnonce` | 4956 | - | AUTHENTICATE `0x1A 00`; print 8-byte nonce (ULC) |
| `ulcg -c -t -j -o` | 5135 | challenges, threads, json, offline | Giantec ULCG / USCUID-UL key recovery; collects challenges (checks AUTH0>=48, lock bit) then cracks; JSON save/load; NOT for NXP |
| `eview` | 4387 | - | `mfu_get_emu_pages_count` (`MF0_NTAG_GET_PAGE_COUNT 4030`) + `mfu_read_emu_page_data` (`4021`); prints `#NN: hex` |
| `eload -f [-t]` | 4405 | file | validates size vs `pages*4`, 4-byte-aligned; `mfu_write_emu_page_data` (`MF0_NTAG_WRITE_EMU_PAGE_DATA 4022`) in <=16-page chunks |
| `esave -f [-t]` | 4492 | file | reads pages (`4021`); .eml adds `# Version:`/`# Signature:` comments (`MF0_NTAG_GET_VERSION/SIGNATURE_DATA 4023/4025`) |
| `econfig [-s ...]` | 5464 | anticoll + magic/write/version/signature/log | `--enable/--disable-uid-magic` (`MF0_NTAG_SET_UID_MAGIC_MODE 4020`), `--write MODE` (`4032`), `--set-version` (`4024`), `--set-signature` (`4026`), `--reset-auth-cnt` (`MF0_NTAG_RESET_AUTH_CNT 4029`), `--enable/--disable-log` (`MF0_NTAG_SET_DETECTION_ENABLE 4033`) |
| `edetect [-s --count --index]` | 5727 | - | password detection logs: `mf0_ntag_get_detection_count` (`4034`) + `get_detection_log` (`4035`) |

MFU auth mechanic (rdpg/wrpg/rcnt/dump): all use `hf14a_raw` directly with an options dict `{activate_rf_field:0, wait_response:1, append_crc:1, auto_select:1, keep_rf_field:0, check_response_crc:1}`; when a key is supplied, first send `0x1B`+key with `keep_rf_field=1`, print PACK, then the operation with `auto_select=0`. Failed auth loses the tag and prints red "Auth failed".

### 5.5 `hf des`
- `info` (10190): selects card (`_des_select` via scan_keep), GET_VERSION (parsed HW/SW major.minor, storage, protocol, UID, batch, prod week/year via `_DESFIRE_*` maps), GetApplicationIDs. All over `hf14a_4_reader_apdu` / raw APDU exchange.
- `chk` (10256): `--aid` (3-byte hex; default PICC master 000000 + all apps), `-n/--keyno` (0-13), `-k/--key` (single 8/16/24-byte), `-f/--file` (dict), `--pattern1b` (all 1-byte patterns DES+AES), `--pattern2b` (2-byte AES), `-t/--timeout`. Tries DES, 2TDEA, AES per key. Built-in `DES_DEFAULTS` (7 keys) and `AES_DEFAULTS` (~20 keys incl. NXP/TI/Gallagher/Gemalto).

### 5.6 `lf` variants (all follow read/write/econfig triad)
Payload framing: every `*_write_to_t55xx` bundles the id + `new_key` + `old_keys` (T55xx password set) and calls the type-specific `Command`.

| Type | read | write | econfig | Commands |
|---|---|---|---|---|
| hid prox | 5813 (scan+format) | 5836 (`>BIBIBH`) | 5872 | `HIDPROX_SCAN 3002`/`WRITE 3003`/`SET_EMU 5002`/`GET_EMU 5003` |
| ioprox | 5922 | 5938 | 5976 | `IOPROX_SCAN 3010`/`WRITE 3011`/`DECODE_RAW 3012`/`COMPOSE_ID 3013`/`SET_EMU 5008`/`GET_EMU 5009` |
| pac | 6124 | 6184 | 6199 | `PAC_SCAN 3014`/`WRITE 3015`/`SET_EMU 5006`/`GET_EMU 5007`; local `pac_encode_raw`/`pac_decode_raw` (7-bit UART framing) |
| viking | 6225 | 6237 | 6928 | `VIKING_SCAN 3004`/`WRITE 3005`/`SET_EMU 5004`/`GET_EMU 5005` |
| jablotron | 6952 | 6966 | 6980 | `JABLOTRON_SCAN 3019`/`WRITE 3020`/`SET_EMU 5010`/`GET_EMU 5011`; `jablotron_card_id` BCD decode |
| idteck | (none) | 6251 | 6265 | `IDTECK_WRITE_TO_T55XX 3018`/`SET_EMU 5012`/`GET_EMU 5013` |
| em 4x05 | 7618 (`em4x05_scan` `EM4X05_SCAN 3030`) | - | - | reads config/UID/EM4x69 64-bit |
| generic | 6498 `adcread` (`ADC_GENERIC_READ 3009`) | - | - | raw ADC array + avg |

`lf clone -t TYPE ...` (6299): universal T55xx writer. `TYPES = em410x, electra, hid, ioprox, pac, viking, idteck`. Requires Chameleon Ultra (`get_device_model()==0`, Lite has no LF writer -> error). Dispatches per type to the same `*_write_to_t55xx` calls with type-specific arg validation (documented inline in the class docstring 6301).

`lf sniff [--timeout --out --hex]` (7649): `lf_sniff` (`LF_SNIFF 3031`, 125kHz 8us/sample). Stores capture in module global `_last_capture`; prints byte count, range, mean, gap detection, optional hexdump with level bars, optional `--out` binary save.

### 5.7 `data` (offline analysis of last `lf sniff`)
All read `_get_capture()` (the `_last_capture` global; error if none). `hexsamples -n` (8662) PM3-style hexdump. `plot --start --len --ascii` (8703) PyQt5/matplotlib waveform, ASCII fallback. `manrawdecode --clock --invert` (8885) Manchester decode. `modulation` (8974) clock/modulation guess.

### 5.8 `emv`
- `scan [-f -s]` (9113): single firmware call `hf14a_4_emv_scan` (`HF14A_4_EMV_SCAN 6005`) runs the full EMV sequence (PPSE, SELECT AID, GPO, READ RECORDs) device-side; parses UID/ATQA/SAK/ATS + APDU pairs; optional PM3-compatible JSON (`-f`), optional load into slot (`-s`).
- `load [-f -s --clear --cmd --resp --defaults]` (9545): populate an HF14A_4 slot's static APDU responses (`hf14a_4_add_static_response` `HF14A_4_STATIC_RESP 6003`); `--clear` clears them; `--defaults` loads Mastercard test data.
- `apdu [--timeout]` (9758): interactive T=CL relay - poll `hf14a_4_apdu_recv` (`6000`), decode the incoming APDU, prompt for a hex response, `hf14a_4_apdu_send` (`6001`). Requires an HF14A_4 slot (SAK=20, ATS) in emulator mode.
- `debug` (9522): sends raw command id `6010`; prints I-block rx/tx counts, last PCB, last static match.

---

## 6. Parity gotchas (must-replicate quirks)

### 6.1 Duplicate `hf 14a info` registration
`@hf_14a.command("info")` appears twice: line 1276 (`HF14AInfo`, deep scan) and line 7743 (`HF14ASniff`, stacked with `sniff`). Because `get_cmd_node` returns the FIRST matching child, **dispatch runs `HF14AInfo`**. But `CustomNestedCompleter.from_clitree` builds an options dict keyed by name, so the completer's `info` entry is overwritten by the LATER one (`HF14ASniff`) - tab-completion for `hf 14a info` suggests sniff's `--timeout`, while execution ignores it. Replicate the dispatch (first-wins) or, better, drop the duplicate.

### 6.2 `.dic` loader is a no-op
`load_dic_file` (unit.py:85) just `return keys` without reading the file. `hf mf fchk --dic` therefore silently loads nothing from a `.dic`. `hf mf dump`/`clone`/`autopwn` read `.dic` files with their own inline `[bytes.fromhex(line[:-1]) ...]` logic (which strips exactly one trailing char - assumes `\n` line endings; a trailing line without newline loses its last hex char).

### 6.3 Key-file formats are inconsistent
Three different formats coexist: `.dic` = one 12-hex key per line (dump/clone/autopwn/fchk-export). `.key` = binary, per-sector KeyA(6)||KeyB(6), unknown = 6 zero bytes (fchk-export, autopwn-save). `hf mf view -k` = ASCII `A:B` colon-separated per line. These are not interchangeable.

### 6.4 Off-by-one / slot indexing
`SlotNumber` is 1-8 in the CLI but `to_fw`/`from_fw` (enum:262) convert to 0-7 for the wire. `slot_info[selected]` in esave/eview uses the raw fw index from `get_active_slot()` (0-based) directly, while econfig uses `SlotNumber.to_fw(self.slot_num)`. `hw slot disable` reads `args.slot` without the active-slot fallback that `enable` has.

### 6.5 External C tools (see 7)
The attack commands shell out to binaries in `bin/`. If missing, `check_tools` only warns at startup; the individual commands fail at runtime. `nested`/`darkside`/`staticnested`/`hardnested` are invoked with positional decimal/hex args and their stdout is scraped with `re.search(r'([a-fA-F0-9]{12})')`. `hardnested` uses a nonce FILE argument, not stdin.

### 6.6 Reader-mode auto-switch side effect
Any `ReaderRequiredUnit` silently flips the device into reader mode (and prints a message) as a precondition. Emulator-only commands use `DeviceRequiredUnit` and do not.

### 6.7 `hw raw` bypasses capability check partially
`hw raw -n <num>` sends unknown command ids with `status=0x0` and the given data; it is the debug escape hatch and intentionally accepts commands not in `device_com.commands`.

---

## 7. External tool integration

### 7.1 `check_tools()` (unit.py:89)
At startup, globs `bin/` for: `staticnested`, `nested`, `darkside`, `mfkey32v2`, `mfkey64`, `staticnested_1nt`, `staticnested_2x1nt_rf08s`, `staticnested_2x1nt_rf08s_1key`. Any missing -> yellow warning "optional Mifare tools not found: ...". (`hardnested` is NOT in this list but is used by `hf mf hardnested`.)

### 7.2 Two invocation paths
- `BaseCLIUnit.sub_process(cmd_string)` (unit.py:166): `subprocess.Popen(shell=True, cwd=bin/)`, threaded stdout reader, used by `nested`/`darkside` with progress polling. Command string is `./tool args` (posix) or `tool.exe args` (win32).
- `execute_tool(name, args_list)` (utils.py:213): `subprocess.Popen([tool_path, *args], cwd=tempfile.gettempdir(), stdout=temp_log)`, waits, raises on non-zero exit, returns log contents. Tool path = `bin/./tool` (posix) or `bin/tool.exe`. Used by `hardnested`, and all `staticnested_*` tools (senested). Note cwd differs: `execute_tool` runs from the temp dir (so `staticnested_1nt` writes `.dic` files into tempdir, which senested then reads back and cleans up).
- `mfkey32v2`/`mfkey64` wrappers (unit.py:3529-3624): `_run_mfkey64(uid,nt,nr,ar,at)`, `_run_mfkey32v2(items)` (multiprocessing), `_run_mfkey32v2_sniff(n0,n1)`. Return sentinels `_TOOL_MISSING`/`_TOOL_BLOCKED`/`_TOOL_NO_KEY` or a 12-hex key. `_sniff_tool_path` checks existence, appends `.exe` on win32.

### 7.3 Tool arg contracts (for reimplementation)
- `nested`: `uid dist [nt nt_enc par]...` -> stdout candidate keys.
- `staticnested`: `uid type [nt nt_enc]...` -> candidates.
- `darkside`: `uid [nt1 ks1 par nr ar]...` -> candidates or "key not found".
- `hardnested <nonce_file>` -> lines "Key found: <12hex>".
- `staticnested_1nt uid sector nt nt_enc parity` -> writes `keys_{uid}_{sector}_{nt}.dic` in cwd(tempdir).
- `staticnested_2x1nt_rf08s a_dic b_dic` -> writes `*_filtered.dic`.
- `staticnested_2x1nt_rf08s_1key nt_b b_key a_dic` -> stdout A-key candidates.
- `mfkey32v2 uid nt0 nr0 ar0 nt1 nr1 ar1` -> key.
- `mfkey64 uid nt nr ar at` -> key.

`hardnested_utils` (imported at unit.py:23) provides `evenparity32` and `hardnested_sums` (the set of valid MSB parity sums used to gate acquisition).

---

## 8. Enum reference (`chameleon_enum.py`) - needed for wire encoding

- `Command` (IntEnum): every device opcode (1000-range = system, 2000 = HF/14a/MFC, 3000 = LF, 4000 = emulator config, 5000 = LF emu id, 6000 = ISO14443-4 T=CL). Full list in section 3/4 citations.
- `Status`: `HF_TAG_OK=0x00`, `HF_TAG_NO=0x01`, `HF_ERR_STAT=0x02`, `HF_ERR_CRC=0x03`, `HF_COLLISION=0x04`, `HF_ERR_BCC=0x05`, `MF_ERR_AUTH=0x06`, `HF_ERR_PARITY=0x07`, `HF_ERR_ATS=0x08`, `LF_TAG_OK=0x40`, `LF_TAG_NO_FOUND=0x41`, `PAR_ERR=0x60`, `DEVICE_MODE_ERROR=0x66`, `INVALID_CMD=0x67`, `SUCCESS=0x68`, `NOT_IMPLEMENTED=0x69`, `FLASH_WRITE_FAIL=0x70`, `FLASH_READ_FAIL=0x71`, `INVALID_SLOT_TYPE=0x72`. `__str__` gives human text.
- `SlotNumber` 1-8 (`to_fw`=value-1, `from_fw`=index+1).
- `TagSenseType`: UNDEFINED=0, LF=1, HF=2.
- `TagSpecificType`: LF 100-310 (EM410X=100, EM410X_ELECTRA=104, PAC=150, Viking=170, Jablotron=180, HIDProx=200, ioProx=201, IDTECK=310); HF 1000+ (MIFARE_Mini=1000, MIFARE_1024=1001, 2048=1002, 4096=1003, NTAG_213=1100..NTAG_212=1108, MF0ICU1=1103 etc.); HF14A_4=3000. `list()`/`list_hf()`/`list_lf()` filter out meta/old types. `__str__` gives display names.
- `MifareClassicWriteMode` / `MifareUltralightWriteMode`: NORMAL=0, DENIED=1, DECEIVE=2, SHADOW=3, SHADOW_REQ=4 (SHADOW_REQ excluded from `list()`).
- `MifareClassicPrngType`: STATIC=0, WEAK=1, HARD=2.
- `MifareClassicDarksideStatus`: OK=0, CANT_FIX_NT=1, LUCKY_AUTH_OK=2, NO_NAK_SENT=3, TAG_CHANGED=4.
- `AnimationMode`: FULL=0, MINIMAL=1, NONE=2, SYMMETRIC=3.
- `ButtonType`: A=ord('A')=65, B=ord('B')=66.
- `MfcKeyType`: A=0x60, B=0x61.
- `ButtonPressFunction`: NONE=0, NEXTSLOT=1, PREVSLOT=2, CLONE=3, BATTERY=4, FIELDGEN=5.
- `MfcValueBlockOperator`: DECREMENT=0xC0, INCREMENT=0xC1, RESTORE=0xC2.
- `HIDFormat`: 31 formats (H10301=1 ... MDI37=30, ACTPHID=42); `check_limits` table at unit.py:545.

---

## 9. Files for the rebuild team (absolute paths)
- REPL/dispatch: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_main.py`
- Command tree (all units): `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cli_unit.py`
- Device command layer: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_cmd.py`
- Transport + framing: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_com.py`
- CLITree / arg parser / helpers: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_utils.py`
- Enums: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/chameleon_enum.py`
- Crypto1 (mfkey32 reader-key test used by elog): `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/crypto1.py`
- Hardnested parity helpers: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/hardnested_utils.py`
- External crack binaries live in `/Users/tuan/Claude/chameleon/ChameleonUltra/software/script/bin/` (globbed by `check_tools`; not present in this checkout unless built).

---


# Data Formats + External Attack Binaries

Investigation complete. Here is the parity-grade reference.

---

# tenor-rekey / ChameleonUltra - File Formats & Crack-Binary Interop Reference

Scope: exact on-disk layouts a native (SwiftUI + Python/Swift daemon) rebuild must read/write to interoperate with existing GUI/CLI files, and the exact subprocess contract for the external C crackers under `ChameleonUltra/software/src`. All paths absolute. Line cites are `file:line`.

Two source trees are involved:
- **tenor-rekey** (`/Users/tuan/Claude/Tenor/tenor-rekey`) - the native macOS clone tool (SwiftUI GUI `app/`, Python daemon+CLI `probe/`). This is what defines `.mfd`/`.keys.json`/`.dic`/export/learned-cache and the `x7d.py` daemon RPC.
- **ChameleonUltra** (`/Users/tuan/Claude/chameleon/ChameleonUltra`) - upstream; source of the crack binaries (`software/src`), the `.eml`/`.bin`/hex slot formats, and the Nordic DFU `.zip`.

---

# PART A - FILE FORMATS

## A.1 `.mfd` / `.bin` / `.dump` - raw MIFARE Classic image

Flat binary, **no header**, block-major. `block_index * 16` bytes. Missing blocks are left as zero.

| Card | SAK | Size | Blocks | Sectors |
|---|---|---|---|---|
| Classic 1K | `0x08` | 1024 | 64 | 16 (4 blocks each) |
| Classic 4K | `0x18` | 4096 | 256 | 40 (32×4-block + 8×16-block) |

Size selects card type on write; on read, `>1024` ⇒ 4K (`0x18`) else 1K (`0x08`).

**Sector/block geometry** (`CardDump.swift:131-135`, mirrored `x7lib`/`x7tool`):
```
sectorsForSak(sak) = (sak==0x18) ? 40 : 16
blocksInSector(s)  = (s < 32) ? 4 : 16
firstBlock(s)      = (s < 32) ? s*4 : 128 + (s-32)*16
trailerBlock(s)    = firstBlock(s) + blocksInSector(s) - 1
```

**Trailer block** (last block of each sector) layout, 16 bytes:
```
[0:6]  KeyA (6 bytes)
[6:9]  access bytes C1/C2/C3 (bytes 6,7,8)
[9]    GPB (user byte, factory 0x69)
[10:16] KeyB (6 bytes)
```

**Block 0** (manufacturer, sector 0): bytes `[0:4]` = UID (4-byte cards), byte `[4]` = BCC, `[5]` = SAK, `[6:8]` = ATQA, rest vendor.

Writers:
- Swift `CardDump.mfdData()` `CardDump.swift:40-48`
- Python `save_mfd()` `probe/x7tool.py:61-71`

Reader (`CardDump.load(mfd:)` `CardDump.swift:60-113`), edge cases the daemon MUST replicate:
- **Reject files > 4096 bytes** (`load` throws `rekey/1` "not a MIFARE image (over 4 KB)") - anti-DoS.
- If a `<name>.mfd.keys.json` sidecar is absent (e.g. a Windows nfcPro `.dump`): recover UID from block-0 first 4 bytes, and recover per-sector keys straight from each trailer (KeyA `[0:6]`, KeyB `[10:16]`); an all-zero key means "not stored" → skip.
- Sidecar cap: only read if `≤ 256 KiB` (`CardDump.swift:74`).

**Default export filename** (`AppModel.defaultDumpFilename` `app/Sources/AppModel.swift:509-513`): `yyMMdd_tr_<uid-no-spaces-lowercase>.dump` (extension `.dump`, a raw image, chosen to sit next to nfcPro dumps). Note this is a deliberate exception to the CLAUDE.md deliverable naming rule.

## A.2 `.keys.json` - key sidecar (interop-critical)

Written next to the image as `<imagepath>.keys.json` (i.e. `foo.mfd` → `foo.mfd.keys.json`). Real sample: `/Users/tuan/Documents/fcd9b6db.mfd.keys.json`.

Schema:
```json
{
  "uid": "fc d9 b6 db",          // string, space-separated lowercase hex (display form)
  "sak": 8,                       // integer (decimal), e.g. 8 or 24 (0x18)
  "keys": {
    "0": ["A", "AABBCCDDEEFF"],   // sector-index string -> [keytype, 12-hex-key] OR null
    "1": ["A", "ffffffffffff"],
    "...": null                   // null = key not recovered for that sector
  }
}
```

Writers: `CardDump.keysJSON()` `CardDump.swift:52-58` (uses `JSONSerialization` with `.prettyPrinted, .sortedKeys`); Python `save_mfd` `probe/x7tool.py:69-70` (`json.dump(..., indent=1)` - matches the sample's 1-space indent). Keys with value `None`/`NSNull` are emitted for every sector `0..<sectorCount`.

Parser validation (treat as untrusted, `CardDump.swift:76-90`): each value must be a 2-element string array; key normalized via `KeyStore.normalized` = **exactly 12 hex chars, lowercased**, else the entry is dropped (not mangled). `keytype` folds to `"B"` only when uppercased == `"B"`, else `"A"`.

## A.3 `.eml` / hex text - slot/tag dump (ChameleonUltra)

Text format. One block/page per line as hex, optional `#` comment lines. Used for MIFARE Classic (`hf mf` eload/clone) and MFU/NTAG (`hf mfu eload/esave`).

- **Load** (`chameleon_cli_unit.py:4430-4443`, `:3112-3118`): file type inferred from extension - `.eml`/`.txt` ⇒ `"hex"`, else `"bin"`. For hex: strip comments with `re.sub("#.*$","",data,flags=MULTILINE)` then `bytes.fromhex(...)` over the whole remaining text (whitespace/newlines tolerated by `fromhex`? - no; here newlines are within the hex run, `fromhex` ignores nothing but the code concatenates lines; the regex leaves newlines which `bytes.fromhex` in py3.7+ tolerates only spaces - in practice MFU path writes 4-byte lines and reads them back joined). Length must be a multiple of 4 (MFU) and ≤ slot size.
- **Save (MFU/NTAG, hex/`.eml`)** (`:4520-4566`): may prepend comment metadata lines before the page rows:
  ```
  # Version: <16 hex chars>       (GET_VERSION, 8 bytes)
  # Signature: <64 hex chars>     (ECC signature, 32 bytes; omitted if all-zero)
  aabbccdd                        (one 4-byte page per line, lowercase hex)
  ...
  ```
- **Save (Classic, hex)** (`:3155-3157`): each 16-byte block appended as `block_data.hex()` bytes; for `bin` the raw 16 bytes.

A native daemon that only needs `.mfd` interop can ignore `.eml`; include it only for ChameleonUltra slot round-tripping.

## A.4 `.dic` - key dictionary

Plain text, **one 12-hex key per line, lowercase**, `#` comment lines allowed. This is both an input to the CLI (`-d/--dic`) and the output of `staticnested_1nt` (see B.3).

- Bundled dict: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/mfc_keys.dic` - 4514 lines, 5-line `#` header, ~4513 unique keys, hotel-brand-first ordering. Provenance: `probe/dict/SOURCES.md`. Generated by `probe/dict/build_dict.py` (do not hand-edit).
- CLI read (`chameleon_cli_unit.py:3121`): `keys = [bytes.fromhex(line[:-1]) for line in dic.readlines()]` - naïve, strips only the trailing newline, **no comment handling** on this path, so a `#`-commented `.dic` fed to `hf mf eload`/`clone` will crash. The `staticnested_1nt`-produced `.dic` files are comment-free, matching this.
- GUI import (`KeyStore.importText` `app/Sources/Engine/KeyStore.swift:44-55`): tolerant - splits on newlines, ignores blank + `#` lines, takes first whitespace token per line, validates via `normalized` (12 hex), dedupes, newest-first. Accepts `.dic`/`.keys`/`.txt`.
- `staticnested` filename convention (parsed by `sscanf`): `keys_<uid:08x>_<sector:02u>_<nt:08x>.dic` and filtered form `..._filtered.dic`. **These names are load-bearing** - `staticnested_2x1nt_rf08s[_1key]` parse UID/sector/nt back out of the filename (B.4).

## A.5 Saved-card export / import (GUI)

There is **no separate "saved_cards" catalog** in either tree (grep-confirmed). "Export a card" == the `.mfd` + `.keys.json` pair from A.1/A.2:
- `AppModel.saveDumpDialog()` `app/Sources/AppModel.swift:516-525` → `NSSavePanel`, default name from A.1, remembers folder in `UserDefaults` key `rekey.exportFolder`.
- `AppModel.saveDump(_:to:)` `:551-559` writes `dump.mfdData()` to the chosen URL and `dump.keysJSON()` to `url + ".keys.json"`.
- Import: `AppModel.loadDump(from:)` `:530-548` → `CardDump.load(mfd:)`, registers a recent-document URL.

So "saved card" interop = produce/consume the A.1+A.2 pair. No proprietary container.

## A.6 `learned_keys.json` - persistent key reranker cache (daemon-owned)

Path: `$X7_LEARNED_PATH` or `~/Library/Application Support/tenor-rekey/learned_keys.json` (`probe/learned_keys.py:40-41`). Written **atomically** (temp file + rename). A missing/corrupt file loads as empty and never raises.

Top-level: a JSON **list** of entry objects. Entry schema (`learned_keys.py:101-105,175-177`):
```json
{
  "key": "a0b1c2d3e4f5",   // 12 lowercase hex, required (entry dropped if invalid)
  "hits": 3,                // int >=1, success count
  "last_used": 1750000000.0,// float epoch seconds
  "first_seen": 1750000000.0,
  "uids": ["fcd9b6db"],     // compact lowercase hex uids, capped 32 (MAX_UIDS_PER_KEY)
  "site": null              // optional label
}
```
Quotas: `MAX_ENTRIES=512`, `DEFAULT_TOP_N=128`, `MAX_UIDS_PER_KEY=32`. Ranking `(-hits, -last_used)`; `top_keys` returns UID-matched entries first, then others (`learned_keys.py:143-148`). A native daemon reimplementing decode ordering should preserve this file's schema so learned state survives the swap.

## A.7 Nordic DFU firmware package (`.zip`)

Built by `nrfutil nrf5sdk-tools pkg generate` (`firmware/build.sh:44-56`). Two variants:

| Package | Contents | Command |
|---|---|---|
| `<type>-dfu-full.zip` | bootloader + application + softdevice | `build.sh:44-50` |
| `<type>-dfu-app.zip` | application only | `build.sh:52-56` |

`<type>` ∈ `ultra` (hw_version 0) / `lite` (hw_version 1). Fixed params: SoftDevice `s140` v`7.2.0`, `sd-req`/`sd-id` = `0x0100`, `application_version=1`, `bootloader_version=1`, signing key `resource/dfu_key/chameleon.pem` (a real private key committed in-repo; treat as public/test).

Internal zip layout (Nordic Secure DFU standard, produced by nrfutil):
```
manifest.json          // describes the images + init packets below
application.bin         // application image (from application.hex)
application.dat         // init packet: signed metadata (fw type/version, hw_version,
                        //   sd_req list, hash, ECDSA-P256 signature over the init cmd)
bootloader.bin/.dat     // (full package only)
softdevice.bin/.dat     // (full package only)
```
`application.dat` is a protobuf-encoded `dfu-cc` init packet (fields: `fw_version`, `hw_version`, `sd_req[]`, `type`, `sd_size`/`bl_size`/`app_size`, `hash{type,hash}`, `is_debug`, plus a `signature` + `signature_type=ECDSA_P256_SHA256`). `manifest.json` maps each `{bin_file, dat_file}` pair under `manifest.application` / `.softdevice_bootloader`.

Flashing (`flash-dfu-app.sh` / `flash-dfu-full.sh`): `nrfutil device program --firmware <pkg>.zip --traits nordicDfu`, after forcing DFU mode. A separate `<type>-binaries.zip` (raw merged `.hex`) is produced for SWD flashing, not DFU.

**Entering DFU** (`resource/tools/enter_dfu.py`): device runs VID:PID `6868:8686` in app mode, `1915:521f` in DFU. Send the 10-byte frame `11 ef 03 f2 00 00 00 00 0b 00` (`DFUCMD`) over the app serial port at 115200 with DTR asserted, then poll for `1915:521f`. (Over the tenor-rekey/CLI protocol this is the `enter_bootloader` command, `chameleon_cli_unit.py:7097`.)

---

# PART B - EXTERNAL CRACK BINARIES (subprocess contract)

All under `/Users/tuan/Claude/chameleon/ChameleonUltra/software/src`, built by `src/CMakeLists.txt`. The daemon drives them as subprocesses; there is **no stdin** to any of them - all input is `argv`, output is **stdout** (a few write `.dic`/temp files to CWD). The Python reference wrappers are `chameleon_utils.execute_tool` and the `_run_mfkey*` helpers.

### ⚠️ Global gotcha - argument radix is NOT uniform

Two different parsers, and getting it wrong silently produces wrong keys:

| Parser | Radix | Used by |
|---|---|---|
| `atoui()` (`common.c:4-13`) - custom, **decimal only**, ignores non-digits | **DECIMAL** | `nested`, `staticnested`, `darkside` |
| `sscanf("%x")` / `strtoul(_,16)` | **HEX** | `mfkey32`, `mfkey32v2`, `mfkey64`, `staticnested_1nt`, `staticnested_2x1nt_rf08s[_1key]` |

So for `nested`/`staticnested`/`darkside` the daemon must pass UID and nonces as **decimal strings**; for the mfkey/1nt/rf08s family as **hex strings**. This is why the CLI builds `nested` params from raw ints (`chameleon_cli_unit.py:1366-1377`) but `staticnested_1nt` params via `format(...,'x')` (`:2242-2246`).

---

## B.1 `nested` - Nested attack (weak-PRNG cards)

`nested.c:8-70`

- **argv:** `nested <uid> <dist> [<nt> <nt_enc> <par>]...`
  - `uid` (decimal), `dist` (decimal, the nonce distance from `mf1_detect_nt_dist`), then **triples** repeated: `nt` (plaintext nonce, decimal), `nt_enc` (encrypted nonce, decimal), `par` (3-bit parity int, decimal). Loop `i=3; i+=3` (`nested.c:19`).
- **stdin:** none.
- **stdout:** for each recovered candidate: `Key <n>... <hexkey> \r\n` (12-hex, space before `\r\n`) - `nested.c:53`. May print zero lines (no candidate).
- **exit:** `EXIT_SUCCESS` (0) always on completion; `EXIT_FAILURE` (1) only on `realloc` failure (`nested.c:75`).
- **Caller parse** (`chameleon_cli_unit.py:1399-1414`): regex `([a-fA-F0-9]{12})` per line → candidate list; each candidate is then **verified by on-card auth** (candidates are not guaranteed unique keys). Ret code 0 required.

## B.2 `staticnested` - Static-nonce nested (static-PRNG cards)

`staticnested.c:8-79`

- **argv:** `staticnested <uid> <type> [<nt> <nt_enc>]...`
  - `uid` (decimal), `type` (target key type: `0x60`=96=KeyA, `0x61`=97=KeyB - passed as `int(type_target)`), then **pairs** `nt nt_enc` (decimal), loop `i=3; i+=2`.
  - Static generation auto-detected from the first `nt`: `0x01200145` ⇒ gen1 (dist 160); `0x009080A2` ⇒ gen2 (dist 161 for KeyB / 160 for KeyA); anything else ⇒ `goto error` (`staticnested.c:26-46`).
- **stdout/exit:** identical to `nested` - `Key n... <hex> \r\n`; exit 0, or 1 on realloc/unknown-gen.
- **Caller** (`chameleon_cli_unit.py:1361-1368`) selects `staticnested` when `mf1_detect_prng()==0`; builds params from `mf1_static_nested_acquire`.

## B.3 `staticnested_1nt` - Backdoored static nested, key-candidate generator (Fudan FM11RF08S)

`staticnested_1nt.c:107-210`. Doegox 2024 (eprint 2024/1275).

- **argv (exactly 6):** `staticnested_1nt <uid:hex> <sector:dec> <nt:hex> <nt_enc:hex> <nt_par_err:bin>`
  - `uid` hex (`strtoul 16`), `sector` decimal (`atoi`), `nt`/`nt_enc` hex, `nt_par_err` a **4-char binary string** (each char `'0'`/`'1'`), e.g. `1110`. Wrong length or non-binary char ⇒ return 1 (`:39-56`).
- **stdout:** diagnostic lines (`uid=... nt=... ks1=...`, `Finding key candidates...`, `Finding phase complete, found N keys`).
- **PRIMARY OUTPUT = a file** written to **CWD**: `keys_<uid:08x>_<sector:02u>_<nt:08x>.dic`, one 12-hex key per line (`:180-193`). Up to `KEY_SPACE_SIZE = 1<<18` = 262144 candidates.
- **exit:** 0 normal; 1 on arg error / bad parity string. Note: a fopen failure only warns to stderr and still returns 0.
- **Caller** (`chameleon_cli_unit.py:2237-2257`) runs it once for KeyA and once for KeyB per sector, using `execute_tool` (CWD = system temp), so the `.dic` files land in `tempfile.gettempdir()`.

The `nt_par_err` bit convention (from the usage text `:141-145`): if trace shows `7b! fc! 7a! 5b`, then `nt_enc=7bfc7a5b` and `nt_par_err=1110`.

## B.4 `staticnested_2x1nt_rf08s` - 2-nonce intersection filter (RF08S)

`staticnested_2x1nt_rf08s.c:81-232`

- **argv (exactly 3):** `staticnested_2x1nt_rf08s <file1.dic> <file2.dic>`
  - Both must be `staticnested_1nt` outputs. **UID + sector + nt are parsed from the filenames** via `sscanf(fn,"keys_%8x_%2u_%8x.dic",...)` (`:88-99`). Same UID, same sector, **different** nt required, else stderr error + return 1.
- **input files:** each `.dic` = list of `%012llx` keys (read with `fscanf`).
- **stdout:** `<file>: N keys loaded` and `<file>: N keys saved`.
- **OUTPUT = two files** in CWD: `keys_<uid>_<sector>_<nt>_filtered.dic` for each input, containing only keys whose `compute_seednt16_nt32` seed collides across the two lists (`:239-286`). Filter narrows KeyA and KeyB candidate sets.
- **exit:** 0 (even on some errors it `goto end` and returns 0).
- **Caller** (`chameleon_cli_unit.py:2259`): runs it, then reads `<B>_filtered.dic` and bulk-checks candidates on-card in batches of 64 (`mf1_check_keys_on_block`, `:2280-2287`).

## B.5 `staticnested_2x1nt_rf08s_1key` - recover the paired key from one known key (RF08S)

`staticnested_2x1nt_rf08s_1key.c:96-190`

- **argv (exactly 4):** `staticnested_2x1nt_rf08s_1key <nt1:hex> <key1:12hex> <file2.dic>`
  - `nt1` hex, `key1` 12-hex (`sscanf %012llx`), `file2` a `.dic` whose name encodes `uid/sector/nt2`. `nt1 != nt2` required.
- **stdout:** every key in `file2` whose seed matches `key1`'s seed, one `%012llx` per line (usually 1). No match ⇒ no output.
- **exit:** 0.
- **Caller** (`chameleon_cli_unit.py:2288-2298`): after KeyB is found on-card, runs this with `(nt_b, keyB, a_key_dic)` to get KeyA candidates, parses stdout lines, verifies on-card.

## B.6 `darkside` - Darkside attack

`darkside.c:18-133`

- **argv:** `darkside <uid> [<nt> <ks> <par> <nr> <ar>]...`
  - `(argc-2) % 5 == 0` required, else "Unexpected param count" + `EXIT_FAILURE` (`:20-23`). `uid` decimal (`atoui`); then **5-tuples**: `nt, ks_list, par_list, nr, ar` all decimal (`:47-53`).
- **stdin:** none.
- **stdout:** `Key<n>: <12 UPPER hex>\r\n` per recovered key (`:99`); if nothing recovered across all tuples, prints exactly `key not found\r\n` (`:114`).
- **exit:** `EXIT_SUCCESS` (0) on normal completion (even when no key - the "not found" is signalled in stdout text, not exit code); `EXIT_FAILURE` on param-count/malloc error.
- **par_list==0 special case** ("parity zero attack", NXP workaround): keylists across successive tuples are intersected (`:70-79`); the caller feeds successive acquisitions until intersection is non-empty.
- **Caller** (`chameleon_cli_unit.py:1483-1516`): builds params from repeated `mf1_darkside_acquire`; on `par!=0` it clears the accumulation list (`:1479-1480`); checks stdout for `"key not found"` to decide retry; regex `[a-fA-F0-9]{12}` for keys; verifies on-card.

## B.7 `mfkey32` / `mfkey32v2` - key from two reader auths (32-bit keystream)

`mfkey32.c:9-72`, `mfkey32v2.c:9-79`. **Radix = HEX** (`sscanf %x`).

- **mfkey32 argv (≥7):** `mfkey32 <uid> <nt> <nr_0> <ar_0> <nr_1> <ar_1>` - same tag nonce `nt` for both auths.
- **mfkey32v2 argv (≥8):** `mfkey32v2 <uid> <nt> <nr_0> <ar_0> <nt1> <nr_1> <ar_1>` - Moebius two-nonce variant (different `nt`/`nt1`); this is the one the daemon should prefer for sniffed pairs.
- **stdin:** none.
- **stdout:** header/diagnostic block, then on success exactly `Found Key: [<12 hex>]` (`mfkey32v2.c:74`). On failure, no `Found Key` line. `argc<N` ⇒ prints `syntax:` and returns 1.
- **exit:** `0` on normal run (found or not - presence of key is signalled by the stdout line, not exit code); `1` only on missing args.
- **Caller** (`chameleon_cli_unit.py:3567-3612`): `_run_mfkey32v2(items)` - hard path via `subprocess.run(check=True)`, greps `_KEY` regex; `_run_mfkey32v2_sniff(n0,n1)` - soft path, 30 s timeout, returns sentinels `MISSING`/`BLOCKED`/`NO_KEY`; treats returncode ∉ {0,1} as `BLOCKED`.

## B.8 `mfkey64` - key from one full auth (64-bit keystream)

`mfkey64.c:9-135`. **Radix = HEX.**

- **argv (≥6):** `mfkey64 <uid> <nt> <{nr}> <{ar}> <{at}> [<enc>...]`
  - First 5 required. Optional trailing `enc` args are hex byte-strings of encrypted traffic to decrypt with the recovered keystream (`:107-124`).
- **stdout:** diagnostics + on success `Found Key: [<12 hex>]` (`:132`). With `enc` args, also prints `{dec<i>}: <hex>` decrypted frames. Always finds a key from one valid auth (no "not found" path - it prints whatever `crypto1_get_lfsr` yields).
- **exit:** `0` normal; `1` on missing args or malloc failure.
- **Caller** (`chameleon_cli_unit.py:3529-3564`): `_run_mfkey64(uid,nt,nr,ar,at)`, 30 s timeout, sentinels as above. Sniff-pair trick: for two consecutive sniffed auths, pass `at = nonce[1].nt` (the CU forwarded it as `{at}`).

## B.9 `hardnested` - Hardnested attack (hard-PRNG, e.g. EV1)

`HardnestedRecovery/hardnested_main.c:88-221`

- **argv (exactly 2):** `hardnested <binary_nonce_file.bin>` - **input is a FILE, not argv nonces.**
- **stdin:** none.
- **stdout:** progress; on success exactly `Key found: <12 hex>` (`:210`) plus a `Details -> UID:... Sector:... Key type:...` line; on failure `Key not found.`.
- **exit:** `0` when key found, `1` otherwise (`:220`) - the only cracker whose exit code reflects success.
- **Caller** (`chameleon_cli_unit.py:2007-2032`): writes nonces to a `.bin` temp file (`prefix=hardnested_nonces_`, in CWD `.`), calls `execute_tool("hardnested",[abs_path])`, greps lines starting with `"Key found: "` then `[a-fA-F0-9]{12}`.
- Internally writes/reads a scratch `temp_nonces.txt` in CWD (text `"%u|%u\n"` = `nt_enc|parity`) and removes it (`:127-190`).

### `hardnested` `.bin` nonce-file layout (Big-Endian header + body)

`hardnested_main.c:100-190`. Reader helpers `read_uint32_be`/`read_uint8`.

```
Header (6 bytes):
  UID        uint32  BIG-ENDIAN
  sector     uint8
  key_type   uint8   (0 = KeyA, 1 = KeyB; else fatal "Invalid key type")

Body: repeated 9-byte chunks until EOF:
  nt_enc1    uint32  BIG-ENDIAN
  nt_enc2    uint32  BIG-ENDIAN
  par_packed uint8   (high nibble = parity of nt_enc1, low nibble = parity of nt_enc2)
```
Parity unpack: `par_enc1 = par_packed >> 4`, `par_enc2 = par_packed & 0x0F` (`:167-169`). Zero chunks ⇒ error "No nonce data". A truncated chunk mid-body ⇒ return 1.

---

## B.10 Summary table

| Binary | argv radix | Input | Success stdout | "not found" signal | Exit (found/not) | Side files (CWD) |
|---|---|---|---|---|---|---|
| `nested` | **dec** | `uid dist (nt nt_enc par)...` | `Key n... <hex> \r\n` | zero key lines | 0 / 0 | - |
| `staticnested` | **dec** | `uid type (nt nt_enc)...` | `Key n... <hex> \r\n` | zero key lines | 0 / 0 | - |
| `staticnested_1nt` | **hex** | `uid sec nt nt_enc par(bin4)` | count lines | - | 0 / 0 | writes `keys_*.dic` |
| `staticnested_2x1nt_rf08s` | **hex (filename)** | `f1.dic f2.dic` | `... keys saved` | - | 0 | writes `*_filtered.dic` |
| `staticnested_2x1nt_rf08s_1key` | **hex** | `nt1 key1 f2.dic` | `<hex>` lines | no output | 0 | - |
| `darkside` | **dec** | `uid (nt ks par nr ar)...` | `Key n: <HEX>\r\n` | `key not found\r\n` | 0 / 0 | - |
| `mfkey32` | **hex** | `uid nt nr0 ar0 nr1 ar1` | `Found Key: [<hex>]` | no line | 0 / 0 | - |
| `mfkey32v2` | **hex** | `uid nt nr0 ar0 nt1 nr1 ar1` | `Found Key: [<hex>]` | no line | 0 / 0 | - |
| `mfkey64` | **hex** | `uid nt nr ar at [enc...]` | `Found Key: [<hex>]` | (always finds) | 0 / - | - |
| `hardnested` | n/a (file) | `<nonces.bin>` | `Key found: <hex>` | `Key not found.` | **0 / 1** | `temp_nonces.txt` |

Key-extraction regex used everywhere: `([a-fA-F0-9]{12})` (case-insensitive 12-hex). Recovered keys from `nested`/`staticnested`/`darkside` are **candidates** and MUST be verified by on-card auth before use.

---

# PART C - LOCATING & BUILDING THE BINARIES

## C.1 How the CLI finds them

- `default_cwd = get_resource_dir("bin")` (`chameleon_utils.py:40`), where `get_resource_dir` (`:27-38`) returns `sys._MEIPASS/bin` under PyInstaller (frozen), else `<script_dir>/bin`. So the binaries live in **`software/script/bin/`** (built output path `EXECUTABLE_OUTPUT_PATH = src/../script/bin` per `CMakeLists.txt`).
- Executable name: `<tool>.exe` on `win32`, else `./<tool>` (`chameleon_utils.py:213-217`; `_sniff_tool_path` `chameleon_cli_unit.py:3522-3527`). A tool is considered "available" if `default_cwd.glob(f"{tool}*")` matches (`chameleon_cli_unit.py:102`).
- `execute_tool` (`chameleon_utils.py:213-243`): runs with `cwd = tempfile.gettempdir()` (so `.dic` scratch files land in temp), stdout+stderr → a temp `.log`, raises on non-zero exit with the log contents.
- `sub_process` (`chameleon_cli_unit.py:166-210`): `shell=True`, `cwd=default_cwd`, threaded stdout reader - used by `nested`/`staticnested`/`darkside` interactive paths.
- `_run_mfkey*` (`chameleon_cli_unit.py:3529-3612`): direct `subprocess.run([str(path), ...])`, `cwd` inherited, 30 s timeout, ASCII decode.

**Native-daemon guidance:** replicate the CWD split - mfkey* can run in any CWD (pure argv→stdout), but `staticnested*`/`hardnested` write scratch/output files to CWD, so run them in a controlled temp dir and read the `keys_*.dic`/`*_filtered.dic` back from there. Filenames are the interop contract for the rf08s chain (A.4).

## C.2 Building for macOS arm64

CMake (`src/CMakeLists.txt`): targets `nested staticnested darkside mfkey32 mfkey32v2 mfkey64 staticnested_1nt staticnested_2x1nt_rf08s staticnested_2x1nt_rf08s_1key mfulc_des_brute hardnested`. Darwin is handled by the `Linux|Android|Darwin` branch: `-O3` release, `-D_GNU_SOURCE`, links `Threads::Threads` and `m`.

Dependencies:
- Most targets: threads + libm only.
- `hardnested`: fetches **xz/liblzma v5.8.1** via `FetchContent` (static, `BUILD_SHARED_LIBS OFF`) and needs the HardnestedRecovery sources; include `-Wall`.
- `mfulc_des_brute` (not in scope but same build): needs `OpenSSL::Crypto`.

Commands:
```bash
cd /Users/tuan/Claude/chameleon/ChameleonUltra/software/src
cmake -B build -DCMAKE_BUILD_TYPE=Release          # add -DCMAKE_OSX_ARCHITECTURES=arm64 to force arm64
cmake --build build -j
# binaries land in software/script/bin/ (EXECUTABLE_OUTPUT_PATH)
```
Notes for arm64: requires CMake ≥3.5 (uses `FetchContent`), a network fetch for xz on first `hardnested` configure (or pre-vendor it / build the other 8 targets without hardnested if offline). For a universal binary use `-DCMAKE_OSX_ARCHITECTURES="arm64;x86_64"`. `find_package(OpenSSL REQUIRED)` (Homebrew `openssl@3`) is only needed if you also build `mfulc_des_brute`. No `.exe` suffix on macOS - the daemon must call `./<tool>`.

---

# PART D - `x7d.py` daemon RPC contract (the native rebuild target)

The tenor-rekey GUI never touches the C binaries or Python internals directly; it speaks a narrow JSON-over-stdio protocol to `probe/x7d.py`, which itself is the thing a native Swift+C daemon would replace. Documented here because "drive the same tools" for tenor-rekey means honoring this contract.

**Wire format** (`probe/x7d.py:10-17`, `:387-401`): newline-delimited JSON on stdin/stdout.
```
request : {"id": <n>, "method": "<name>", "params": {...}}
response: {"id": <n>, "result": {...}}  |  {"id": <n>, "error": "<msg>"}
event   : {"event": "progress", "method": "<name>", ...}   // id-less, unsolicited
```
Hex is lowercase space-separated (`"01 02 03 04"`); keys are 12-char hex. One request per line; blank lines ignored; bad JSON → `{"error": "..."}` event. On `OSError` (reader unplugged) the handler drops the cached reader handle and returns an error so the next call re-opens (`:371-385`).

**Methods** (`METHODS` tuple `x7d.py:47-48`): `info, poll, decode, read_ntag, apdu, write_mfd, format, nested_recover, keys_default, keys_builtin_count, learned_stats, learned_clear`.

Result shapes are the Swift `Codable` structs in `app/Sources/Engine/Models.swift`:
- `poll` → `PollResult{present,uid?,atqa?,sak?,reader?,kind?}` (`x7d.py:93-110`). `kind` ∈ `"classic"`/`"ntag"`; `isNTAG` fallback rule: `SAK==0x00 && ATQA=="0044"` (`Models.swift:26-31`).
- `decode` → `DecodeResult{uid,atqa,sak,sectors,recovered,attempts?,exhausted?,blocks{blk:hex?},keys{sector:[kt,hex]?}}` (`x7d.py:167-174`). Key trial order: **user keys → learned cache (top 64) → builtin dictionary** (`:135-146`); `max_seconds` is only a runaway watchdog. Recovered keys are recorded to the learned cache keyed by UID.
- `write_mfd` → `WriteResult{present,wrote?,failed?,error?}` (`x7d.py:195-294`). Params `{blocks{blk:hex16}, keys{sector:[kt,hex]}, trailers:bool, uid:bool, target_uid?}`. Safety invariants a native daemon MUST keep: (1) if `target_uid` set and the card on the reader differs → refuse (`error`); (2) never write a trailer whose access bits are invalid (`access_bits_valid`) or that locks its own keys (`trailer_locks_keys`); (3) never write an all-zero key slot - substitute recovered key or factory FF; (4) auth tries source key (A/B) then factory FF; (5) abort if the card UID changes mid-write ("card changed during write"). `blockParams`/`keyParams` on the Swift side (`CardDump.swift:130-137`) marshal `{blk:hex}` / `{sector:[kt,hex]}` string-keyed dicts to match.
- `format` → `FormatResult` - factory reset, writes `FACTORY_TRAILER = ffffffffffff ff078069 ffffffffffff` (`x7d.py:38`), trailer last per sector, block 0 untouched.
- `nested_recover` → `{key, target_blk}` - params `{known_blk, known_key, target_blk, known_kt="A", target_kt="A", window=4096, max_samples=8}`; delegates to `x7crypto.nested_recover_key` (`x7d.py:351-368`).
- `read_ntag` → `NtagResult{present,uid?,sak?,pages{idx:hex4}}`; `apdu` → `ApduResult{present,uid?,sak?,resp?}`; `info` → `DeviceInfo{model,serial,hw}` (`x7d.py:82-92`).

Progress events (mid-op, id-less): `decode` emits per-sector `{sector,total,keytype,key}` boundary events plus walk `{sector,attempts,walk_total,phase}` where `phase` ∈ `"hot"`/`"dict"`; `write_mfd`/`format` emit `{block,ok,unsafe?}`; `nested_recover` emits `{phase,n,info}`. Swift decodes these via `EngineEvent` (`Models.swift:113-127`).

---

## Key files (all absolute)

- Crack C sources + build: `/Users/tuan/Claude/chameleon/ChameleonUltra/software/src/{nested,staticnested,staticnested_1nt,staticnested_2x1nt_rf08s,staticnested_2x1nt_rf08s_1key,darkside,mfkey32,mfkey32v2,mfkey64,common}.c`, `src/HardnestedRecovery/hardnested_main.c`, `src/CMakeLists.txt`
- CLI wrappers / formats: `software/script/chameleon_utils.py` (`execute_tool`, `default_cwd`), `software/script/chameleon_cli_unit.py` (nested/darkside/hardnested/rf08s/eml paths, `_run_mfkey*`)
- DFU: `firmware/build.sh`, `firmware/flash-dfu-{app,full}.sh`, `resource/tools/enter_dfu.py`, `resource/dfu_key/chameleon.pem`
- tenor-rekey formats: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/{CardDump,KeyStore,Models,AccessBits}.swift`, `app/Sources/AppModel.swift` (export/import), `probe/{x7d.py,x7tool.py,learned_keys.py}`, `probe/dict/{mfc_keys.dic,SOURCES.md,build_dict.py}`
- Real sample: `/Users/tuan/Documents/fcd9b6db.mfd.keys.json`

---
