# Device compatibility - nfcPro / nfcCopy / wCopy family

Bảng tra cứu mọi máy mà phần mềm gốc (và do đó `tenor/rekey`) có thể nói chuyện được.
Nguồn: disassemble `nfcPro_x64.exe` + `wCopy_2024010501.exe`, GitHub `skylandersNFC/wCopy` + `huntsman95/wCopyRFID`, libnfc #628, manuals + listing thương mại. Research 4-agent 2026-06-19.

## Phát hiện cấu trúc quan trọng (đọc trước)

**USB ID KHÔNG map 1-1 với board.** `nfcPro_x64.exe` có hàm probe (`@0x499e0` và `@0x4b460`) gọi `hid_open` lần lượt 4 ID, fallback theo thứ tự. Sau khi mở được, tên board/model đọc RUNTIME qua opcode hãng `FF 00 68` (model string) + `FF 00 69` (serial), rồi đối chiếu bảng `wCopy <BOARD>-<SUFFIX>` nhúng trong binary để gán nhãn + chọn file firmware update đúng.

Hệ quả: cùng một board vật lý có thể enumerate sau bất kỳ ID nào trong 4 ID, tùy USB-bridge/firmware rev. 4 ID = 2 cặp transport, KHÔNG phải selector.

## USB ID - ĐÃ CHỐT bằng disasm 2 build nfcPro (19/06)

Mổ trực tiếp hàm probe (chuỗi `push serial(0xffffffff), push PID, push VID, call hid_open, test eax, jz→ID kế`):
- `nfcPro_x64.exe` (build wcopy/x7, PE32+): probe `0416:b030`, `0416:b031`, `2518:6022`, `2518:6018`.
- `nfcPro_wbw.exe` (build Reader/wbw, PE32, dialog CDialogWbw): probe `0416:b008`, `0416:b030`, `0416:b029`, `2518:6022`, `2518:6018`, `0416:b058`.

**TẬP HỢP TOÀN BỘ nfcPro probe = 7 ID:**

| VID:PID | VID vendor | Dòng phần cứng |
|---------|-----------|----------------|
| `0416:b008` | Nuvoton | dongle desktop (board/rev) |
| `0416:b029` | Nuvoton | dongle desktop |
| `0416:b030` | Nuvoton | dongle desktop (repo huntsman95/wCopyRFID xác nhận) |
| `0416:b031` | Nuvoton | dongle desktop |
| `0416:b058` | Nuvoton | dongle desktop |
| `2518:6018` | Silicon Labs | X7 handheld rev |
| `2518:6022` | Silicon Labs | XIXEI X7 / X7-P (máy founder) - **verified live** |

**Câu đố `6869:1256` ĐÃ GIẢI:** cả 2 build nfcPro đều KHÔNG probe `6869:1256` (xác nhận: zero immediate ở mọi dạng). Vậy:
- Dongle desktop mà nfcPro hiện hành lái = thiết bị **HID VID Nuvoton `0x0416`** (nhiều PID = nhiều board/firmware rev của NS106/NS122/NSR/WBW/NFC108).
- `6869:1256` (PN533 + mass-storage, `iProduct=wCopy smart reader`, libnfc #628) là **dòng RIÊNG/đời cũ** - raw PN533 over USB chạy với libnfc, KHÔNG thuộc danh sách nfcPro hiện tại. Red herring cho tương thích nfcPro; chỉ đụng nếu cố ý hỗ trợ đồ cổ (transport khác).

**Lưu ý:** model name đọc runtime qua `FF 00 68`, KHÔNG suy từ PID. Route theo model string, không theo VID/PID.

**TODO probe cho tenor/rekey:** match đúng 7 ID trên (5× `0416` + 2× `2518`). Tất cả nói cùng vendor HID `FF 00 xx` như X7 → engine hiện tại nhiều khả năng chạy thẳng, chỉ nới device-match. `6869:1256` = path libnfc/PN533 tùy chọn cho legacy.

## Họ board/firmware (nhúng trong binary)

Naming: `wCopy <BOARD>-<SUFFIX> V<rev>`. Suffix = cấp năng lực:
`-IC` = chỉ HF 13.56; `-IDIC` = HF + LF 125kHz; `-HIDIC` = HF + HID-Prox; `-H` = LF/HID; `-BH/-P/-E/-CD` = biến thể product-class.

**Thực chất chỉ 2 thân máy:** (A) dongle desktop không màn, (B) X7 handheld. Mọi tên khác là nhãn firmware/marketing/SKU nội bộ.

| Board | Retail name | Thân máy | Trạng thái thực tế |
|-------|-------------|----------|--------------------|
| X7 / X7-P | XIXEI X7 | (B) Handheld màn màu, pin, USB-C; N32G020 + PN533 | **Máy thật**, verified live |
| X7 Pro | - | = X7 | **Nhãn marketing**, không phải máy riêng |
| X7V310 / X7-V3.10H | - | = X7 | **Chuỗi version firmware (V3.10)**, không phải máy |
| NSR106 / NS106 (-IC/-IDIC/-HIDIC) | NS106 | (A) dongle desktop | **Máy thật**, flagship, ~22 firmware rev |
| NSR122 / NS122 (-BH/-P/-E/-CD/-H) | NS122 / NSR122-H | (A) dongle desktop, có bản Type-C | **Máy thật** (manual "wCopy NSR122-H V601", teardown 6869:1256) |
| WBW106 (-IDIC) | - | (A) dongle desktop | Nhãn firmware/software (kênh wbw), cùng chassis A |
| NS208 | NS208 | (B) handheld màn màu | = cụm X7 retail (rebadge) |
| NSR102 / NSR108 / NFC108 / NSR121 / NSR107 | - | (A) class | **PHANTOM** - không dấu vết bán lẻ/teardown nào; SKU nội bộ hoặc khai tử, chỉ có trong bảng firmware. Đừng coi là máy thật. |

3 kênh firmware update (nsccn.com): `nfcPro_wcopy_*` (HF), `nfcPro_kgf_*` (LF/HID-prox), `nfcPro_wbw_*` (WBW dual-freq; wbw = 网百网 = OBO HANDS).

## OEM thật

- Phần cứng OEM/ODM: **OBO HANDS** = Shenzhen Wang Bai Wang Tech (网百网). Lý do có 30+ rebrand.
- Phần mềm: Shenzhen Wang'an Tech, nsccn.com (mirror rf-card.com / rfcardwriter.com / nfccopy.com). XIXEI host bản riêng.

## 3 cụm phần cứng vật lý + các hãng rebadge (30+)

1. **Handheld màn màu (NS208 / X7):** XIXEI, OBO HANDS, HACHANLUN, Anruhoo, Tqsbeyah, LUCINE, Bewinner, HERNAS, 5YOA, LEXI, LIBO, KDL, CIFY, FST, JASAG, Walfront, SYWAN, Diydeg, Irfora, Uhppote, Vbestlife, ReaIOKbii...
2. **Dongle USB không màn (NS106/NS122) - CẦN PC software:** OBO HANDS, YiToo, HFeng, Sonew, LEXI, LIBO, HERNAS/HENA, JASAG, CIFY, KDL, Mickcara, ASHATA + no-name AliExpress.
3. **Vỏ 3.2" voice-prompt:** HACHANLUN, Anruhoo, Bewinner (khác vỏ, cùng ruột).

## Ma trận năng lực theo tầng

| Tầng | LF 125k | HF 13.56 | Magic blank | M1 decrypt | Sniff/Monitor | CPU/APDU | FM11RF08S |
|------|---------|----------|-------------|-----------|---------------|----------|-----------|
| LF-only dongle | Read + T5577/EM4305 write | - | - | - | - | - | - |
| Dual-freq USB (NS106/NSR/WBW) | Full LF | M1 1K/4K, UL, NTAG R/clone | UID/CUID/FUID | dict+nested | vài unit | KHÔNG (trần software) | nếu build 2025-03+ |
| Full handheld (X7/X7 Pro) | LF + sweep 125-1000kHz | M1, UL, NTAG203/213/215, F08, ISO14443A/B | UID/CUID/FUID/UFUID | có | "Monitor card" sniff | read/passthrough | có (firmware mới) |

- **Stock nfcPro KHÔNG copy CPU / GDM / GTU.** tenor/rekey port APDU passthrough = vượt trần stock.
- **FM11RF08S** (Fudan hardened, static nonce + backdoor Quarkslab 2024): nfcPro thêm support 2025-03-11. Đây là card access đời 2024-2025, đáng port nhất.
- LF read set thực tế = EM4100/4200 + TK4100 + HID Prox + T5577 sweep. AWID/Indala/Paradox/ISO15693 KHÔNG thấy trong nguồn nào - coi như ngoài envelope tới khi verify trên máy.

## TODO cho tenor/rekey

1. Probe cả 4 HID ID (`0416:b030/b031`, `2518:6022/6018`), không chỉ `2518:6022`.
2. Cân nhắc personality PN533 `6869:1256` nếu muốn phủ reader đời cũ NSCCN (protocol khác, dùng libnfc-style).
3. Đọc model runtime qua `FF 00 68` để gán nhãn/route - không đoán theo USB ID.
4. Tham khảo `skylandersNFC/wCopy` + `huntsman95/wCopyRFID` cho board NS106/NS122.
