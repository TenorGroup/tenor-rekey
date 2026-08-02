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
