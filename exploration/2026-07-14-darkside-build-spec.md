# Spec build darkside -> nested cho tenor/rekey (2026-07-14)

Nguồn: 2 agent Opus (Chameleon Ultra firmware + darkside-PN532) cross-verify libnfc/mfcuk/Proxmark3. Chameleon repo clone ở `scratchpad/ChameleonUltra/software/src/`. Chờ capture nfcPro crack a7 e5 04 để chốt wire protocol + parity polarity.

## Kiến trúc (giống Chameleon): X7 = thu nonce (RF), Mac = giải khóa (crypto)
Chameleon chip tí hon KHÔNG tự giải - nó thu nonce, đẩy sang máy tính chạy solver (crapto1 cần ~128MB, nRF52 không kham). **X7+Mac của ta y hệt.** Đã có crapto1 → việc thật = viết vòng THU nonce trên X7 + port vài hàm solver.

## Chuỗi end-to-end
`poll → dict walk (đã có) → [trượt hết] → validate_prng_nonce probe → DARKSIDE (khóa đầu) → NESTED (15 sector còn lại, đã có) → dump 16/16 → clone (đã có)`

## PHẦN 1 - Solver (Mac, port từ mfcuk/Proxmark/Chameleon clone)
crapto1.py ĐÃ có: lfsr_recovery32/64, lfsr_rollback_*, crypto1_get_lfsr, prng_successor, filter, evenparity32.
**THIẾU (port pure-Python, dùng đúng các hàm đã có):**
- `fastfwd[2][8]` table (PM3 crapto1.c): `{0,0x4BC53,0xECB1,0x450E2,0x25E29,0x6E27A,0x2B298,0x60ECB}` / `{0,0x1D962,0x4BC53,0x56531,0xECB1,0x135D3,0x450E2,0x58980}`
- `lfsr_prefix_ks(ks[8], isodd)` - ứng viên 21-bit state khớp 8 nibble keystream.
- `check_pfx_parity(pfx, rr, par[8][8], odd, even, sl)` - lọc theo ràng buộc 8×8 parity.
- `lfsr_common_prefix(pfx, rr, ks[8], par[8][8])` - top-level, cross-product odd×even × 64 tops → check_pfx_parity → state list (malloc tới 1<<20 states; Python chạy vài giây).
- `nonce2key(uid, nt, nr, ar, par_info, ks_info)` - pack ks/par, gọi lfsr_common_prefix, rồi lfsr_rollback_word(state, uid^nt, 0) + crypto1_get_lfsr → khóa.
- `validate_prng_nonce(nonce)` + `nonce_distance` - probe thẻ weak/hard.
mfcuk oddparity(x) = evenparity32(x)^1. KHÔNG dùng lfsr_recovery64 cho darkside (đường khác, chỉ để verify).

## PHẦN 2 - Acquisition darkside (X7)
Darkside KHÔNG cần encrypted CIU state (khác nested) - gửi raw bits, đọc 4-bit reply. Né được rủi ro lớn nhất.

**Nguyên lý:** parity bit của mỗi byte bị mã bằng CÙNG keystream bit mã bit data kế → khi reader gửi {nr}{ar} có 8 parity bit "đúng" nhưng ar sai, tag trả **NACK 4-bit mã hóa = keystream ^ 0x5** → lộ 4 keystream bit. ~1/256 lần đoán parity.

**Loop (mfcuk mfcuk_key_recovery_block):** MFCUK_DARKSIDE_START_NR=0xDEADBEEF, START_AR=0xFACECAFE, cần 8 NACK. STAGE1: sweep parity 0..255 tới NACK đầu → fix prefix nr&0xFFFFFF1F. STAGE2: 8 nr-tail (bit 5,6,7) × sweep low-5 parity tới NACK → ks[pos]=nack^0x5. Đủ 8 → lfsr_common_prefix. ~25% fail solver → bump nr retry.
**Fixed-nonce trên USB:** KHÔNG ép nt như Proxmark FPGA. Passive: cache theo từng nt distinct (bsearch), tích 8 NACK trên nt nào tái xuất. Giữ timing loop đều (sleep cố định, không log trong hot loop). ~300-500 auth/nt thành công, ~1-5 phút.

**⚠️ REGISTER SAI trong x7lib.py (Agent B verify vs libnfc pn53x-internal.h) - PHẢI SỬA:**
| x7lib hiện | Sai | Đúng |
|---|---|---|
| CIU_MFRX=0x6312 (parity) | =CRCResultLSB | **ManualRCV 0x630D bit 0x10** |
| CIU_ERROR=0x6306 | =TxSel | Error 0x6336 |
| CIU_FIFOLEVEL=0x633C | =Control | FIFOLevel 0x633A |
| CIU_BITFRAMING=0x633D | đúng | 0x633D |
Bit: SYMBOL_PARITY_DISABLE=0x10 (ManualRCV bit4), TX/RX_CRC_ENABLE=0x80 (TxMode 0x6302/RxMode 0x6303 bit7), TX_LAST_BITS=0x07 (BitFraming 0x633D), RX_LAST_BITS=0x07 (Control 0x633C).
**⚠️ Parity polarity mơ hồ:** libnfc set bit4=1 để DISABLE; capture nfcPro ghi 0xEF (bit4=0). NGƯỢC nhau. Phải test live, dùng validate_prng_nonce làm oracle.

**Đọc NACK 4-bit (chỗ khó cổ điển):** PN532 không có register parity riêng. Gửi qua InCommunicateThru parity-off (pre-interleave parity vào byte stream: 8 byte → 9 byte = 72 bit, TxLastBits=0). Nhận: đọc RxLastBits = reg_read(Control 0x633C)&0x07 = 4 cho NACK; nackEnc=rx[0]&0xf. Darkside KHÔNG cần unwrap tag parity.

**Wire/attempt (dùng comm_thru/reg_read/reg_write/_ciu_rmw/_crc_a đã có):**
```
setup: reg_write TxMode 0x6302 clear bit7; RxMode 0x6303 clear bit7  (CRC off)
per attempt:
 1. poll()  # re-select, nt mới
 2. parity ON: _ciu_rmw(0x630D, enable, 0x10); comm_thru([0x60|0x61,block]+crc) → nt plaintext
 3. cache theo nt; chọn {nr}{ar}{parity} theo STAGE1/2
 4. parity OFF: _ciu_rmw(0x630D, disable, 0x10); reg_write BitFraming 0x633D TxLastBits=0
    comm_thru(wrap_frame(nr||ar 8B, parity8) = 9B)
 5. rxlast = reg_read(0x633C)&0x07; if rxlast==4: nackEnc=rx[0]&0xf (ks=nack^5)
```
wrap_frame: mirror mỗi byte + chèn parity bit ở ranh giới 9-bit (mirror pn53x_wrap_frame).

## Thất bại + probe trước
validate_prng_nonce(nt): True=weak→darkside OK; False=hardened→KHÔNG darkside (cần hardnested). a7 e5 04 (1K clone, SAK08/ATQA0004) → gần chắc weak. Thẻ never-NACK / static-nonce / magic-always-NACK(par_list==0, dùng parity-zero variant + intersection 2 run) = xử riêng.

## Sau khi có khóa đầu → nested (đã có)
crack_key(known_blk, known_key, target_blk) dùng khóa darkside làm anchor. Nested nhanh (~chục nonce/sector). = pipeline Proxmark hf mf autopwn.

## 2 việc làm trước khi code (Agent B)
1. Sửa register constants sai (bảng trên) - cũng vá đường nested đang hỏng.
2. Chốt parity polarity bằng oracle validate_prng_nonce (capture nfcPro sẽ cho thấy chính xác nfcPro ghi gì).

## Nguồn
mfcuk src/mfcuk.c + crapto1.c; libnfc pn53x.c (transceive_bits L1532, unwrap_frame L432, NP_HANDLE_PARITY L886) + pn53x-internal.h; PM3 mifarehost.c (mf_dark_side L49, detect_classic_prng L1400, nonce2key). Chameleon clone: scratchpad/ChameleonUltra/software/src/{darkside,mfkey,crapto1,nested,nested_util}.c.

---
# ✅ XÁC NHẬN TỪ CAPTURE THẬT (2026-07-15, agent mổ crack_a704e504.pcapng 112k frame)

**DỨT ĐIỂM = DARKSIDE, không phải nested.** Pha "Getting nonces" (f23357-37199, ~18.8s) dùng InCommunicateThru (0x42), **0 InDataExchange auth**. Không có khóa biết trước (cả dict sweep trượt). 68 nonce, 2/(sector-trailer × keytype), mọi sector.

**Phase map:** (a) fingerprint f11317-12703 → UID a704e504/ATQA0004/SAK08. (b) dict sweep f13661-23331 (~3127 khóa, trượt hết, status 0x14). (c) **DARKSIDE** f23357-37199. (d) solve Mac-side idle ~9.5s. (e) validate keys + dict-walk block7 cho sector-1 KeyA (18736 → a5e7091879cb). (f) read/dump.

**Khóa ra (plaintext trong auth sau, validate decode):** KeyA `865501172208` (75 auth), KeyB `050604061320` (15), `a5e7091879cb` (5, sector 1 KeyA từ dict-walk).

**PARITY POLARITY DỨT ĐIỂM:** 0x630D chỉ ghi 2 giá trị: **`0x10` (66× = DISABLE parity)** và `0x00` (66× = enable). KHÔNG có 0xEF. → bit4=1 tắt parity, đúng libnfc. Register XÁC NHẬN LIVE: TxMode 0x6302, RxMode 0x6303, ManualRCV 0x630D, BitFraming 0x633D, Control 0x633C (RxLastBits), Error 0x6336, FIFOLevel 0x633A. **Auth opcode: nfcPro dùng 0x64(A)/0x65(B) trong InCommThru** (0x60/0x61 cũng chạy).

**1 darkside unit (verbatim f24181-24289):**
```
32 01 01 (field ON) → 4a 01 00 (select) → 4e 01 00 00 (release)
06 6302→80; 08 6302 00  (TxMode CRC off)
06 6303→80; 08 6303 00  (RxMode CRC off)
42 64 03 <CRC_A>        (InCommThru AUTH-A blk3) → d5 43 00 <nt4> 90 00  (nt plaintext, parity ON)
06 630d→00; 08 630d 10  (*** ManualRCV=0x10 → PARITY OFF ***)
42 <9B = {nr}{ar}+8parity=72bit> → d5 43 00 <4B+4bit> 90 00
06 633c→14              (Control RxLastBits = 0x14&7 = 4 = leak NACK 4-bit)
08 633d 04 → 42 <4-bit follow-up> → 08 633d 00  (BitFraming)
06 6336→00; 06 633a→00  (Error/FIFO clear)
32 01 00 (field OFF, reset trước nonce kế)
```
Thu ≥2 run/(sector,keytype) → feed {uid,nt,{nr}{ar},parity,4-bit reply} vào solver → khóa 48-bit.

**GO/NO-GO = XANH:** capture chứng minh X7 LÀM ĐƯỢC parity-TX + đọc NACK 4-bit (rủi ro #1 sol nêu = giải quyết). Register đã vá trong x7lib.py (2026-07-15): CIU_MANUALRCV 0x630D, CIU_CONTROL 0x633C, CIU_ERROR 0x6336, CIU_FIFOLEVEL 0x633A; _set_parity_raw dùng bit 0x10. uid_to_int x7crypto sửa uid[-4:].

## Chỉnh theo review Codex sol (2026-07-15):
- **NHÚNG solver C đã pin (Proxmark/mfcuk), Python điều phối** - KHÔNG port pure-Python (1<<24 state ~128MB, Python 16M object = vô lý). Pure-Python chỉ để test.
- **Validate nhiều tầng**: differential test vs C pin + fixture ngẫu nhiên + golden-replay capture + thẻ known-answer (865501172208/050604061320) + negative fixture. 1 thẻ known-answer KHÔNG đủ (bug byte-order bù nhau vẫn pass).
- **Learned-key cache RIÊNG** (không nhét user dict): chỉ lưu khóa verify-live trên đúng UID, metadata (hit/site/verified), thử top 64-256, quota riêng, mã hóa Keychain (không plaintext UserDefaults). Con số 18.7k nfcPro có thể lẫn ứng viên tạm.
- **Register-state hygiene**: mọi fail/cancel/kill phải khôi phục CRC/parity/crypto1/framing/RF. Process kế không được giả định process trước đã reset.
- **32 khóa A/B độc lập/thẻ 1K** (không phải 16) - clone đầy đủ phải lấy hết cả A lẫn B mỗi sector.
- Phạm vi thật: darkside chỉ weak-PRNG. Static/hardened/always-NACK cần đòn riêng (parity-zero intersection 2-run cho always-NACK - mà thẻ này chính là always-NACK, đã thấy trong capture).

## Bước build (thứ tự sol khuyên):
1. ✅ Vá register + UID (xong 2026-07-15).
2. RF-capability replay: gửi đúng 1 darkside unit trên X7, xác nhận nhận RxLastBits=4 + nt plaintext hợp lệ (dùng validate_prng_nonce oracle). Đây là gate go/no-go thật trên phần cứng (capture đã chứng minh khả thi).
3. Vendor solver C (mfcuk nonce2key/lfsr_common_prefix hoặc PM3) + Python wrapper, differential-test.
4. Darkside acquire loop (68 nonce, parity-zero 2-run) → solver → khóa đầu.
5. Nested cho phần còn lại (vá path nested hiện có) hoặc dict-walk fallback (như nfcPro làm cho sector-1).
6. Wire vào nút "khôi phục khóa" + learned-key cache + register hygiene.
