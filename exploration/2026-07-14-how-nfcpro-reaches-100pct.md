# Làm sao nfcPro đạt ~100% giải mã MIFARE Classic - nghiên cứu 2026-07-14

Fleet: 4 agent Opus 4.8 (2 đục exe, 1 lý thuyết, 1 audit crypto) + Codex sol (bị OpenAI cyber-filter chặn giữa chừng, chỉ kịp quét mật độ khóa = 0 bảng khóa thẳng hàng). Founder = nhà cung cấp khóa khách sạn, crack thẻ/thiết bị của chính mình. Việc hợp pháp.

## Câu hỏi founder: exe có chứa đủ mẫu khóa ẩn không?

**KHÔNG.** Bằng chứng (agent #1, cross-check Codex):
- Chỉ **14 khóa default** ở offset `0x84d36` (đúng bộ mfoc/hardnested default). 20/30 khóa công khai phổ biến VẮNG.
- Blob lớn CÓ THẬT (~736MB giải nén, 351 luồng XZ ở `0xc01f0`-`0x1808f0`) nhưng là **bảng tấn công hardnested** (mảng bit 2^24, entropy 0.87 toàn 0xff, 0 khóa) - verbatim proxmark3 `hardnested.c`: "Using %d precalculated bitflip state tables".
- **13.055 khóa `re/nfcpro_keys.txt` = RÁC** carve từ bảng hardnested giải nén (nibble gần đều, 2/13055 có cấu trúc, không có trong exe). ĐÃ XÓA khỏi dict tenor/rekey (v0.5.1).
- Khóa nfcPro thật = 14 default + **live recovery** (nested/hardnested/darkside) + **cache theo thẻ** `keys\a<uid>.dump`/`b`/`t`.

## Vì sao nfcPro không bao giờ trượt = CHUỖI TẤN CÔNG đầy đủ (agent #2, function map)

Dispatcher `fcn.1400306c0`, getopt `htTDCi:I:o:O:V:R:S:s:v:M:U:d:n:P:p:F:`. Nhúng nguyên bộ:
| Tấn công | Cần gì | Chuỗi |
|---|---|---|
| Dictionary | 0 | 14 default + user file |
| **Darkside/mfcuk** (`fcn.140033cc0`) | **0 khóa biết** | "Dark-Side Attack to reover at least 1 key ... where NO keys are known" |
| **Nested/mfoc** | 1 khóa biết | "Nested attack %d", "Getting nonces..." |
| **Hardnested** (`fcn.14001e740`) | 1 khóa + PRNG cứng | "Hardnested Attack Sector", bitflip tables; brand GUI "流星锤 Meteor Hammer" |

Escalation: dict trượt ("No sector encrypted with the default key has been found") → **darkside lấy khóa đầu** → nested lấy phần còn lại → hardnested nếu PRNG cứng. **Dict của nfcPro (13k, còn nhỏ hơn ta) KHÔNG phải cái tạo ra 100% - crypto attack mới là.**

## Thẻ khóa lạ (a7 e5 04, 0 khóa): nfcPro làm gì
1. Fingerprint (ATQA/SAK/RATS/PRNG probe). 2. Dict sweep → trượt. 3. **Darkside** (parity-disable qua reg ManualRCV, thu parity leak + NACK, crapto1 rollback `0xEC57E80A`) → khóa đầu. 4. **Nested** phần còn lại. 5. **Hardnested** nếu cứng. 6. Dump + clone.

## Phần cứng X7 LÀM ĐƯỢC hết (agent #2, capture crack_traffic.txt)
Capture chứng minh X7 phơi đủ primitive software-Crypto1: `ReadRegister/WriteRegister` CIU, `InCommunicateThru` (raw frame), `InDataExchange`. Registers nfcPro dùng: **TxMode 0x6302/RxMode 0x6303** (CRC off), **ManualRCV 0x630d bit4 = parity disable (ghi 0xef)**, **BitFraming 0x633d** (7-bit `07`), Control 0x633c, Error 0x6336, FIFO 0x6339/0x633a. => KHÔNG cần phần cứng khác, chỉ cần SOFTWARE port mfcuk+mfoc+hardnested.

## Trạng thái crypto tenor/rekey (agent #4, code-grounded)
- **Crypto1 (crapto1.py) SOLID**, pass C-verified self-test (rec32=39822 cand, rec64 exact). Không phải vấn đề.
- **Nested: hiểu lầm cũ đã sửa** - KHÔNG bí vì "lfsr chậm" (đã 6.6s). Bí vì **thiếu mỏ neo khoảng-cách nonce** → hiện quét cả 65535 nonce × 6.6s = 5 ngày. Fix: bắt nonce sector-biết qua **software-Crypto1 first-auth** (thay InDataExchange) → lộ plaintext nonce → calibrate PRNG distance → đoán nonce đích trong ~vài trăm candidate. Logic đã prototype ở `nested_calib.py`. KHÔNG bắt buộc parity.
- **Test nested cũ FAIL vì oracle hỏng**: dùng thẻ magic/blank có nonce KHÔNG phải LFSR thật. Phải calibrate trên thẻ Classic weak-PRNG THẬT.
- **Parity register SAI**: code ta probe `0x6312` (MfRxReg); nfcPro dùng **`0x630d` (ManualRCV) bit4**. Sửa register này mở đường parity-based attack.
- **Darkside: chưa có solver**, khó nhất (cần parity I/O). Không nên làm trước.
- **MFKey32 (đánh từ ổ khóa)**: crypto DONE + near-instant, KHÔNG cần parity. Thiếu: card-emulation (TgInitAsTarget d4 8c / TgGetData / TgResponseToInitiator) + wrapper mfkey32 mỏng. Đúng mục tiêu #1 founder.

## Kết luận: lộ trình 99%
Dict lớn KHÔNG phải đáp án. 99% = chuỗi crypto attack (như nfcPro). Ưu tiên:
1. **Nested (mfoc port)** - workhorse, cho thẻ có ≥1 khóa dict (đa số hotel card). Jump lớn nhất từ dict-only. Cần: distance-anchor fix + calibrate trên thẻ thật + sửa register parity.
2. **FM11RF08S backdoor** - ĐÃ THÊM v0.5.1 (khóa a396efa4e24f/a31667a8cec1 @ vị trí 10). Mở thẻ static-nonce hotel bằng dict auth thường.
3. **MFKey32 (reader attack)** - cho thẻ 0-khóa + có ổ khóa vật lý. Crypto sẵn, cần card-emulation.
4. **Darkside** - cho thẻ 0-khóa không ổ. Khó, làm sau/bỏ.
5. **Hardnested** - thẻ cứng EV1/Plus. Nặng nhất (cần bảng bitflip).

Khóa thật của founder (từ dump của chính anh): `babe18072016` (vendor), `1b6bb3ed89fb` (PeaceHome master) → vào từ điển USER (KHÔNG commit public).
