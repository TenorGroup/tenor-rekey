# X7 (XIXEI X7-P) Vendor HID Opcode Map

Companion to `PROTOCOL.md`. This document is the definitive opcode reference for the
XIXEI X7-P NFC reader's vendor HID protocol, reversed from `nfcPro_x64.exe` (nfcCopy).

All command payloads below are the **payload bytes only** (the `[4..]` field of the
64-byte report). They must be wrapped in the transport envelope described in
`PROTOCOL.md`:

```
REQUEST report = 01 | total | seq_lo | seq_hi | <PAYLOAD> | cksum | FE | 00-pad
                 total = len(payload) + 6
                 cksum = (~sum(report[0 .. 3+L])) & 0xFF
```

Every command function reads back exactly **64 bytes** via the RECV wrapper
(`fcn.140044a60`, 0x41-byte buffer, 2000 ms timeout) and mirrors the raw frame into a
global response staging buffer at `0x148718ee1`. Throughout this doc, response byte
offsets are given relative to that buffer: `resp[0]` = `0x148718ee1` (the `0x02`
marker), `resp[1]` = `0x148718ee2` (the `total` field), `resp[4]` = `0x148718ee5`
(first status/length byte of payload), `resp[5]` = `0x148718ee6` (first data byte),
etc. (`total - 6` = payload length.)

> Heads-up on the `0x66` family. Almost all RF/config/magic-card commands share the
> prefix `FF 00 66 00 1E` followed by a 4-byte context/handle and a 1-byte
> **subcommand** byte. The `66` "opcode" is really a family; the subcommand byte at
> payload offset 9 is what actually selects behavior. Where a function takes a
> caller-supplied 4-byte context (written at payload[5..8]), the placeholder
> `<ctx:4 LE>` is used below.

---

## 1. Master opcode table

Legend - Dir: R = read-only, W = write/destructive, R/W = depends on caller data.
Conf = reverse-engineering confidence.

| Fn addr | Payload (hex, payload bytes only) | Len | Opcode / sub | Purpose | Dir | Conf |
|---|---|---|---|---|---|---|
| `0x140045980` | `FF 00 68` | 3 | 0x68 | Get device **model** string (e.g. `XIXEI X7-P`) | R | high |
| `0x140045a90` | `FF 00 69` | 3 | 0x69 | Get device **serial** (e.g. `P39705638618`) | R | high |
| `0x140045750` | `FF 00 68` then `FF 00 69` | 3 + 3 | 0x68,0x69 | Get model **and** serial in one call -> `"model - serial"` | R | high |
| `0x140045ba0` | `FF 00 40 50 04 <p> 01 01 01` | 9 | 0x04 | RF field / poll-rate config; `<p>` = arg/10 (min 1) | R | medium |
| `0x140046110` | `FF 00 6B 30` | 4 | 0x6B/0x30 | Card **poll + anticollision/SELECT**; returns ATQA, SAK, UID (4/7/10) | R | high |
| `0x1400463c0` | `FF 00 6B 31 <sel>` | 5 | 0x6B/0x31 | Poll/SELECT variant w/ selector; UID + SAK + 16-byte trailer (ATS) | R | high |
| `0x140046d90` | `FF 00 6A 01 <arg3 LE:2>` | 6 | 0x6A/0x01 | ISO14443A poll / get-UID w/ retry; classifies NTAG / ISO14443-4 | R | high |
| `0x140045c80` | `FF 00 85 01 <uid:4> <key:2 swap> <r8b> <r9b>` | 12 | 0x85/0x01 | MIFARE **authenticated read** of a card into host `.dump` (UID + key) | R | medium |
| `0x140045d70` | `FF 00 85 10 <off:2 LE> <count:2 LE> <key>` | 9 | 0x85/0x10 | MIFARE bulk-read / **load-key preamble** (observed count=0x200, key=0xFF) | R | medium |
| `0x140045e40` | `FF 00 85 12 <off:2> <len:2> <data...>` | 8+N | 0x85/0x12 | **Raw bulk WRITE** of data block to card/buffer (CXixeiTabData) | W | high |
| `0x140046660` | `FF 00 85 32 <a:4 LE> <b:2 LE> <c:2 LE>` | 12 | 0x85/0x32 | Device/RF config **SET** (8 param bytes) | W | medium |
| `0x140046770` | `FF 00 85 34` | 4 | 0x85/0x34 | Device/RF config/status **GET** (read counterpart to 0x85/0x32) | R | medium |
| `0x140046850` | `FF 00 85 33 <p>` | 5 | 0x85/0x33 | Device/RF config **SET**, 1-byte mode/selector (observed 0x06) | W | medium |
| `0x140046a40` | `FF 00 66 00 1E <v:4 LE> 00 <p:2 LE> 00` | 13 | 0x66/0x1E sub00 | RF/timing register SET (init v=0x1E848=125000) | W | medium |
| `0x140046b40` | `FF 00 65 <cl> <r8b> <v:4 LE> <a:2 LE> <b:2 LE>` | 13 | 0x65 | Config set-then-readback (init r8b=0x1E); returns 5-byte status | W | medium |
| `0x140046c80` | `FF 00 66 00 1E <v:4 LE> 01 <d:4 LE> <d5>` | 15 | 0x66/0x1E sub01 | RF/timing config SET w/ 5-byte data block from ptr | W | medium |
| `0x140047e40` | `FF 00 66 00 1E <v:4 LE> 1F <w0:2><w1:2><w2:2><w3:2>` | 18 | 0x66/0x1E sub1F | RF antenna/register tuning (4x 16-bit regs) | W | medium |
| `0x140047f70` | `FF 00 66 00 1E <v:4 LE> 12 <p10> <idx> <d:4 LE>` | 16 | 0x66/0x1E sub12 | Config set-then-readback; init streams `'Tian'` w/ incrementing idx | W | medium |
| `0x1400480a0` | `FF 00 66 00 1E <h:4 LE> 13 <f0> <f1> <d:4 LE> <d2:4 LE>` | 20 | 0x66/0x1E sub13 | Multi-field config / key block write; 5-byte readback | W | medium |
| `0x1400481e0` | `FF 00 66 00 1E 48 E8 01 00 21 <v:4 LE>` | 14 | 0x66 sub21 | Write single 4-byte config value (from ptr) | W | medium |
| `0x1400482d0` | `FF 00 66 00 1E 48 E8 01 00 22 <idx> <v:4 LE>` | 15 | 0x66 sub22 | Write **indexed** 4-byte config slot (idx=cl, obs 2); 5-byte readback | W | medium |
| `0x14004a2c0` | `FF 00 66 00 1E 48 E8 01 00 32 <v:4 LE> 00` | 15 | 0x66 sub32 | **125 kHz (LF)** op; sets 32-bit word (body=125000 Hz) | W | medium |
| `0x14004a3b0` | `FF 00 66 00 1E 48 E8 01 00 33 <flag> <v:4 LE>` | 15 | 0x66 sub33 | **125 kHz (LF)** write; flag+32-bit data (obs flag=0xFE, val=card-ID) | W | medium |
| `0x14004a4b0` | `FF 00 66 00 1E 48 E8 01 00 34 <flag> <v:4 LE>` | 15 | 0x66 sub34 | **125 kHz (LF)** write; flag+32-bit data (obs flag=0x01) | W | medium |
| `0x140048570` | `FF 00 66 00 1E <m:4 LE> 40 00*14 3C B4 64` | 27 | 0x66 sub40 | **13.56 MHz scan / poll-field setup**; primes UID-collect loop | R | medium |
| `0x1400486b0` | `FF 00 66 00 1E <t:4 LE> 47 <len>` | 11 | 0x66 sub47 | Variable-length transceive/READ; returns 2 or 4 bytes by status | R | medium |
| `0x140048800` | `FF 00 66 00 1E <t:4 LE> 42 <v:4 LE>` | 14 | 0x66 sub42 | Write 4-byte value keyed by target (commit; status-only) | W | medium |
| `0x140048900` | `FF 00 66 00 1E <ctx:4 LE> 43 <idx> <v:4 LE>` | 15 | 0x66 sub43 | **Magic-card** category C write (carries `0x9933ddbb` backdoor const) | W | medium |
| `0x140048a10` | `FF 00 66 00 1E <ctx:4 LE> 44 <idx> <v:4 LE>` | 15 | 0x66 sub44 | **Magic-card** category D per-block data write (idx 1/4/5) | W | medium |
| `0x140048b20` | `FF 00 66 00 1E <ctx:4 LE> 41 <idx>` | 11 | 0x66 sub41 | **Magic-card** category A READ (read-back block/UID word) | R | medium |
| `0x140048c50` | `FF 00 66 00 1E <ctx:4 LE> 46` then `... 46 28` | 11 + 11 | 0x66 sub46 | **Magic-card BACKDOOR UNLOCK** handshake (Gen1A `/`+`(` answer) | W | low |
| `0x140049c10` | `FF 00 66 00 1E <ctx:4 LE> 42 <v:4 LE>` | 14 | 0x66 sub42 | Magic-card category B verify; GF(2)/CRC transform (BCC/checksum) | R | low |
| `0x140049e40` | `FF 00 66 00 1E <ctx:4 LE> 43 <idx> <v:4 LE>` | 15 | 0x66 sub43 | Magic-card category C ack-checked write (status pattern only) | W | low |
| `0x140049f50` | `FF 00 66 00 1E <a:4 LE> 44 <flag> <v:4 LE>` | 15 | 0x66 sub44 | Config field SET (selector 1/4/5); status-only | W | medium |
| `0x14004a060` | `FF 00 66 00 1E <a:4 LE> 3F <w:2>x5` | 20 | 0x66 sub3F | Config/encode; 1x 32-bit + 5x 16-bit params (RF/LF shaping) | W | low |
| `0x14004a1a0` | `FF 00 66 00 1E 00000000 4F <w:2>x5` | 20 | 0x66 sub4F | RF **carrier-frequency**/waveform config (125-1000 kHz table) | W | low |
| `0x14004a6c0` | `FF 00 00 00 <n+1> D4 <PN532 cmd bytes>` | var | 0x00 / D4 | **CPU/APDU passthrough** (raw PN532 InDataExchange) | R/W | high |

---

## 2. Commands grouped by function

### 2.1 Device info (read-only)

| Payload | Fn | Returns |
|---|---|---|
| `FF 00 68` | `0x140045980` | model string, e.g. `XIXEI X7-P` |
| `FF 00 69` | `0x140045a90` | serial, e.g. `P39705638618` |
| `FF 00 68` + `FF 00 69` | `0x140045750` | `"model - serial"` combined |

Response parsing: payload length = `resp[1] - 6` (i.e. `total - 6`); model/serial
ASCII starts at `resp[5]` and is memcpy'd into the caller buffer and null-terminated.
These match the `PROTOCOL.md` "verified commands" table.

Also in this bucket: the **INIT handshake** `FF 00 9A 5A A5 54 69 61 6E` (`-> 00 OK`,
where `54 69 61 6E` = ASCII `Tian`) documented in `PROTOCOL.md`; it is built at
`.text 0x140043c44`. Send it once after connect before other commands.

### 2.2 RF field / antenna / register config

| Payload | Fn | Dir | Notes |
|---|---|---|---|
| `FF 00 40 50 04 <p> 01 01 01` | `0x140045ba0` | R | poll-rate / field; `<p>` = arg/10 (min 1). Middle bytes need live confirm. |
| `FF 00 85 32 <a:4><b:2><c:2>` | `0x140046660` | W | config SET, 8 param bytes |
| `FF 00 85 34` | `0x140046770` | R | config/status GET (pair of 0x85/0x32) |
| `FF 00 85 33 <p>` | `0x140046850` | W | config SET, 1-byte mode (obs 0x06) |
| `FF 00 66 00 1E <v:4> 00 <p:2> 00` | `0x140046a40` | W | timing register, init v=0x1E848 (125000) |
| `FF 00 66 00 1E <v:4> 01 <d:4><d5>` | `0x140046c80` | W | timing register, 5-byte data variant |
| `FF 00 66 00 1E <v:4> 1F <w:2>x4` | `0x140047e40` | W | antenna/register tuning (4 regs: 280,239,155,390) |
| `FF 00 66 00 1E <v:4> 12 <p10><idx><d:4>` | `0x140047f70` | W | set-register-then-readback; init writes `'Tian'` |
| `FF 00 66 00 1E <h:4> 13 <f0><f1><d:4><d2:4>` | `0x1400480a0` | W | multi-field config / key block |
| `FF 00 66 00 1E 48 E8 01 00 21 <v:4>` | `0x1400481e0` | W | single config value |
| `FF 00 66 00 1E 48 E8 01 00 22 <idx><v:4>` | `0x1400482d0` | W | indexed config slot |
| `FF 00 65 <cl><r8b><v:4><a:2><b:2>` | `0x140046b40` | W | config set-then-readback, returns 5-byte struct |
| `FF 00 66 00 1E <a:4> 44 <flag><v:4>` | `0x140049f50` | W | config field SET (selector 1/4/5) |
| `FF 00 66 00 1E <a:4> 3F <w:2>x5` | `0x14004a060` | W | encode/shaping params |
| `FF 00 66 00 1E 00000000 4F <w:2>x5` | `0x14004a1a0` | W | RF carrier frequency |

For all `0x66` and `0x85`-config commands, response is status-only: success when
`resp[1] == 0x0C` (or `resp[1] ^ 0x0C == 0`) and `resp[4] == 0x05` (or `^0x05 == 0`),
returning 5; else 0. The `set-then-readback` variants additionally copy
`dword resp[5..8] -> out[0..3]` and `byte resp[9] -> out[4]`.

### 2.3 Card poll & UID detection (ISO14443A, 13.56 MHz)

| Payload | Fn | Returns |
|---|---|---|
| `FF 00 6B 30` | `0x140046110` | ATQA[2], SAK, UID (4/7/10 bytes by cascade) |
| `FF 00 6B 31 <sel>` | `0x1400463c0` | as above + 16-byte trailer (ATS/extended select) |
| `FF 00 6A 01 <arg3:2 LE>` | `0x140046d90` | poll w/ retry; SAK/ATQA/UID + NTAG / ISO14443-4 typing |

`0x6B/0x30` response validation: `resp[1]==0x19`, `resp[4]==0x12`, `resp[0x14]==0x90`,
`resp[0x15]==0x00` (`90 00` = success). ATQA at `resp[5..6]`; SAK/length selector at
`resp[8]` (value 4/7/10 picks UID length); UID bytes start at `resp[9]`. Returns the
UID byte count. (Doc offsets above are relative to `0x148718ee1`; the JSON's `ee2`
etc. are the same bytes.)

`0x6B/0x31` uses a different layout: `resp[1]==0x29`, `resp[4]==0x22`,
`resp[0x15]==0x90`, `resp[0x16]==0x00`; select byte at `resp[7]`; SAK/len at `resp[8]`.

`0x6A/0x01` loops up to `cx` times (init wrapper `fcn.140043620` uses cx=5,
arg3=0x0800 -> payload bytes `00 08`), 10 ms Sleep between tries; requires
`resp[1]==0x0E` and `resp[4]==0x07` (card present); then SAK at `resp[5]`,
UID/ATQA dword at `resp[6]`, word at `resp[0x0A]`.

### 2.4 MIFARE Classic (auth + read/write)

| Payload | Fn | Dir | Notes |
|---|---|---|---|
| `FF 00 85 10 <off:2><count:2><key>` | `0x140045d70` | R | bulk-read / load-key preamble (obs off=0, count=0x200, key=0xFF) |
| `FF 00 85 01 <uid:4><key:2 swap><r8b><r9b>` | `0x140045c80` | R | authenticated read of a card into host `.dump` |
| `FF 00 85 12 <off:2><len:2><data...>` | `0x140045e40` | W | raw bulk WRITE of a data block to card |

The high-level dump flow lives in dispatcher `fcn.140033cc0`: it calls `0x85/0x10`
once to set up (count 0x200 = 512 bytes), then `0x85/0x01` (auth read) per card, and
the caller writes the result to `%s\keys\a%08x.dump`. `0x85/0x12` is the write-back
side (CXixeiTabData), sent repeatedly at offsets 0, 0x20, ... pushing 32-byte chunks.

Note: the auth in `0x85/0x01` packs the 2-byte key field **byte-swapped**
(`payload[+0x2c]=arg2[1]`, `payload[+0x2d]=arg2[0]`), and two further key/flag bytes
(`r8b`, `r9b`) come from globals `0x142ed6b7a` / `0x142ed6cb7`. Key A vs Key B
selection is **not yet isolated** to a single payload byte - see gaps in §5.

### 2.5 NTAG21x

There is **no dedicated NTAG opcode** in the reversed set. NTAG handling is folded
into the ISO14443A poll path `0x6A/0x01` (`0x140046d90`), which inspects SAK and
classifies NTAG213/215/216 vs ISO14443-4 after detection. NTAG page read/write would
then go through the generic transceive (`0x66 sub47`, `0x1400486b0`) or the CPU/APDU
passthrough (`0x14004a6c0`). This is a gap - see §5.

### 2.6 Magic / Gen1A (UID-writable) card cloning

These all use `FF 00 66 00 1E <ctx:4 LE>` + a category byte. The category letters
match their ASCII (`0x41`=`A` ... `0x46`=`F`).

| Payload | Fn | Dir | Role |
|---|---|---|---|
| `... 46` then `... 46 28` | `0x140048c50` | W | **BACKDOOR UNLOCK** handshake; confirmed by `resp[1]==0x2F` (`/`) & `resp[4]==0x28` (`(`) |
| `... 41 <idx>` | `0x140048b20` | R | category A: read block / UID word (returns 4 bytes) |
| `... 42 <v:4>` | `0x140049c10` | R | category B: UID checksum/BCC verify (GF(2) xor-0x1D transform) |
| `... 43 <idx><v:4>` | `0x140048900` | W | category C: write data word (carries `0x9933ddbb` magic const) |
| `... 43 <idx><v:4>` | `0x140049e40` | W | category C: ack-checked write (status-pattern variant) |
| `... 44 <idx><v:4>` | `0x140048a10` | W | category D: per-block data write (idx 1/4/5) |
| `FF 00 66 00 1E <m:4> 40 00*14 3C B4 64` | `0x140048570` | R | scan/poll-field setup that primes the UID-collect loop |

The clone sequence (driver `fcn.1400497e0`): scan (`sub40`) -> backdoor unlock
(`sub46` x2) -> read-back (`sub41`) / verify (`sub42`) -> stream block writes
(`sub44`, and `sub43` carrying `0x9933ddbb`). The two magic constants `0x9933ddbb` and
the `/`(2F) + `(`(28) handshake are the classic Gen1A backdoor signatures.

### 2.7 CPU / APDU card (PN532 passthrough)

| Payload | Fn | Dir |
|---|---|---|
| `FF 00 00 00 <n+1> D4 <PN532 cmd bytes (n bytes)>` | `0x14004a6c0` | R/W |

This is the only path that wraps a **real PN532 frame** (`0xD4` host->controller
direction byte) - everything else is the vendor opcode set. Used by CXixeiTabCPU to
send APDUs to ISO14443-4 / CPU cards. Read vs write depends entirely on the APDU the
caller supplies. Response validation: `resp_raw[0]==0x02` marker, internal checksum
(sum then NOT), frame trailer `0xFD`, PN532 response direction byte `0xD5`, and an
echoed length check; then the response data is memcpy'd to the caller's output buffer.
Returns data length, or negative (-1..-6) on a framing error.

### 2.8 125 kHz / LF (EM4100 / T5577)

| Payload | Fn | Dir | Notes |
|---|---|---|---|
| `FF 00 66 00 1E 48 E8 01 00 32 <v:4> 00` | `0x14004a2c0` | W | LF set 32-bit word |
| `FF 00 66 00 1E 48 E8 01 00 33 <flag><v:4>` | `0x14004a3b0` | W | LF write (obs flag=0xFE, val=card ID e.g. `0x2135A847`) |
| `FF 00 66 00 1E 48 E8 01 00 34 <flag><v:4>` | `0x14004a4b0` | W | LF write (obs flag=0x01) |
| `FF 00 66 00 1E 00000000 4F <w:2>x5` | `0x14004a1a0` | W | LF/RF carrier frequency select (125-1000 kHz) |

The constant body `48 E8 01 00` = LE `0x0001E848` = 125000 = the 125 kHz carrier.
A full LF program issues `32`, `33`, `34` together (each potentially twice).

### 2.9 Beep / LED / misc

**No beep or LED opcode was identified** in the reversed function range
(`0x140045750 .. 0x14004a6c0`). If the device has audible/visual feedback it is
either firmware-automatic on card detect, or hidden behind a `0x66`/`0x85` subcommand
not yet decompiled. This is a gap - see §5.

---

## 3. SAFE TO TEST (read-only payloads)

These do **not** modify a card or persist device state (they read info, status, or
poll). They are safe to send blind during bring-up. (Replace `<...>` with literal
zeros where a value is needed; UID/context placeholders can be `00 00 00 00` to start.)

```
FF 00 9A 5A A5 54 69 61 6E   INIT handshake (send first; expect 00)
FF 00 68                     get model
FF 00 69                     get serial
FF 00 68 (then FF 00 69)     get model+serial
FF 00 6B 30                  poll + anticollision/SELECT (UID + SAK)
FF 00 6B 31 00               poll/SELECT variant (UID + SAK + ATS)
FF 00 6A 01 00 08            ISO14443A poll / get-UID (retry)
FF 00 85 34                  config/status GET
FF 00 40 50 04 01 01 01 01   RF poll-rate read (middle bytes unverified)
FF 00 66 00 1E 00 00 00 00 47 04          transceive READ (len=4)
FF 00 66 00 1E 00 00 00 00 41 05          magic category-A read-back
FF 00 66 00 1E <m:4> 40 00..00 3C B4 64   13.56 MHz scan/poll setup
FF 00 00 00 <n+1> D4 <PN532 read APDU>    APDU passthrough (read-only APDU only)
```

Caveat: `0x66 sub40/sub41/sub47` and `0x6A` actively energize the RF field and poll;
that is electrically read-only but will wake any card in range. The CPU/APDU passthrough
is only safe if the APDU you pass is itself a read (e.g. `SELECT` / `READ BINARY`).

---

## 4. DO NOT BLIND-TEST (write / destructive payloads)

These persist device config or **write to a card** (including UID/sector-0 cloning).
Do not send without a known-good card you are willing to overwrite and verified
parameters.

```
FF 00 85 12 <off><len><data...>          raw bulk WRITE to card  *** card write ***
FF 00 85 32 <a><b><c>                     device/RF config SET
FF 00 85 33 <p>                           device/RF config SET (1-byte mode)
FF 00 65 <...>                            config set-then-readback
FF 00 66 00 1E <v> 00 <p> 00              RF/timing register SET
FF 00 66 00 1E <v> 01 <d><d5>             RF/timing register SET
FF 00 66 00 1E <v> 1F <w>x4               antenna/register tuning SET
FF 00 66 00 1E <v> 12 <...>               set-register-then-readback
FF 00 66 00 1E <h> 13 <...>               multi-field config / KEY block write
FF 00 66 00 1E 48 E8 01 00 21 <v>         config value write
FF 00 66 00 1E 48 E8 01 00 22 <idx><v>    indexed config slot write
FF 00 66 00 1E <t> 42 <v>                 keyed 4-byte commit write
FF 00 66 00 1E <a> 44/3F/4F <...>         config field / encode / carrier SET
FF 00 66 00 1E 48 E8 01 00 32/33/34 <...> 125 kHz / T5577 LF WRITE  *** tag write ***
FF 00 66 00 1E <ctx> 46  (+ 46 28)        magic-card BACKDOOR UNLOCK  *** enables UID overwrite ***
FF 00 66 00 1E <ctx> 43 <idx><v>          magic-card block WRITE  *** card write ***
FF 00 66 00 1E <ctx> 44 <idx><v>          magic-card block WRITE  *** card write ***
FF 00 00 00 <n+1> D4 <PN532 write APDU>   APDU passthrough w/ a WRITE APDU  *** card write ***
```

Special hazard: `0x140048c50` (`sub46`) puts a magic/Gen1A card into UID-writable
mode and `sub43`/`sub44` then overwrite sector 0 / blocks - this is the irreversible
cloning path. Treat the entire magic-card group as destructive.

---

## 5. Recommended sequence: read a MIFARE Classic card (UID, then a block)

The driver dispatcher is `fcn.140033cc0`. The minimal host sequence is:

1. **INIT** - `FF 00 9A 5A A5 54 69 61 6E`
   verify: response payload `00` (OK).

2. **(optional) RF field setup** - `FF 00 40 50 04 01 01 01 01`
   verify: 64-byte frame returned (function only checks length). Skip if polling works without it.

3. **Poll + anticollision/SELECT (get UID + SAK)** - `FF 00 6B 30`
   verify: `resp[1]==0x19`, `resp[4]==0x12`, `resp[0x14..0x15]==90 00`. Read SAK at
   `resp[8]`; it returns 4/7/10 = UID length; UID bytes at `resp[9..]`.
   (Alternative: `FF 00 6A 01 00 08` which also returns UID and types the card.)
   For a classic MIFARE 1K, SAK is typically `0x08` and UID length 4.

4. **Auth + read setup** - `FF 00 85 10 00 00 00 02 FF`
   (offset 0, count 0x200 = 512 bytes, key byte 0xFF). This is the load-key / bulk-read
   preamble. verify: 64-byte frame returned.

5. **Authenticated read of the card** - `FF 00 85 01 <UID:4> <key:2 byte-swapped> <r8b> <r9b>`
   Supply the UID from step 3. The 2-byte key field is written hi-then-lo
   (`payload[+0x2c]=key[1]`, `payload[+0x2d]=key[0]`). `r8b`/`r9b` are the flag/key-type
   bytes nfcPro pulls from globals `0x142ed6b7a` / `0x142ed6cb7` - **capture their live
   values before relying on this** (see gaps). The function reads the card and the host
   side writes blocks to `...\keys\a<UID>.dump`; per-block data is consumed from the
   cached response frame at `0x148718ee1`.

So, concretely, to read MIFARE Classic UID then block data:
```
FF 00 9A 5A A5 54 69 61 6E        ; init
FF 00 6B 30                       ; -> UID (resp[9..]) + SAK (resp[8])
FF 00 85 10 00 00 00 02 FF        ; load-key / bulk-read preamble (key FF FF FF FF FF FF)
FF 00 85 01 <uid0..3> <k1> <k0> <r8b> <r9b>   ; authenticated read -> .dump image
```

Per-block granularity: nfcPro reads the whole 512+ byte image in one `0x85/0x01`
pass and slices blocks host-side from the cached frame, rather than issuing one
read-block command per block. If you want a single block, the generic transceive
`FF 00 66 00 1E <target:4> 47 04` (`0x1400486b0`, returns 4 bytes) is the closest
single-block read primitive, but the exact target encoding for a specific block must be
captured live.

---

## 6. Gaps and low-confidence inferences (need live verification)

- **Key A vs Key B selection** is not isolated to a single payload byte. In
  `0x85/0x01` the key type appears to ride in `r8b`/`r9b` (from globals
  `0x142ed6b7a` / `0x142ed6cb7`). Capture those globals' live values for both KeyA and
  KeyB reads to map them. The default key `FF FF FF FF FF FF` shows up as the `0xFF`
  byte in `0x85/0x10`.

- **Single read-block / write-block opcodes** are not cleanly separated - nfcPro does
  bulk image transfer (`0x85/0x10` + `0x85/0x01` to read, `0x85/0x12` to write) and
  slices blocks host-side. A per-block command (likely `0x66 sub47` or a `0x85` sub)
  needs a live capture against a single block to confirm offset/length encoding.

- **`FF 00 04 ...` (`0x140045ba0`)**: the middle bytes
  (`45 01 04 <p> 01 01`) come from a `movabs` template whose little-endian layout the
  analyst flagged as needing re-confirmation against a live capture. Opcode `FF 00 04`
  and the divide-by-10 `<p>` are solid; the exact on-wire order of bytes 3-8 is not.

- **NTAG21x read/write** has no dedicated opcode found; detection is via `0x6A/0x01`.
  Page-level NTAG I/O probably reuses transceive (`0x66 sub47`) or APDU passthrough -
  unverified.

- **No beep / LED / buzzer opcode** was located. Confirm whether feedback is firmware-
  automatic or hidden behind an undecompiled `0x66`/`0x85` subcommand.

- **`0x66` family subcommand semantics (sub3F/sub4F/sub13/sub21/sub22/sub40/sub42)**
  are medium-to-low confidence. The opcode bytes and payload shapes are solid; the
  exact register meanings (which physical RF/timing parameter each touches) are
  inferred from init constants (e.g. `0x1E848`=125000, the `'Tian'` magic, the KHz
  carrier table) and should be confirmed by toggling each and observing device behavior.

- **Magic-card group (`sub41`-`sub46`, `0x140048900`-`0x140049e40`)**: the Gen1A
  signatures (`0x9933ddbb` const, `/`+`(` handshake) are recognizable and strongly
  suggest UID-write cloning, but the per-step state machine in `0x140048c50` /
  `0x1400497e0` is `low` confidence. Verify on a sacrificial magic card only.

- **`0x6B/0x30` vs `0x6B/0x31` response offsets** differ by one byte
  (`90 00` at `resp[0x14..0x15]` vs `resp[0x15..0x16]`) - confirm the exact status
  offset live, since an off-by-one here will look like a failed SELECT.

- **`r8b`/`r9b` global-sourced bytes** in `0x85/0x01` and `0x85/0x10` (and the
  config `0x65`/`0x66` init values like `0x1E`) are read from `.bss`/`.data` globals
  set during init; static analysis can't always resolve their runtime values. Snapshot
  them from a live USB capture of nfcPro performing a successful read/clone.


---

## 7. Adversarial verification (workflow Verify phase)

**Verdict:** SAFE LIST IS MOSTLY CORRECT BUT HAS ONE MIS-DECODE THAT MUST BE FIXED BEFORE TRUSTING IT. No genuinely write/destructive opcode is false-flagged as safe - the dangerous Gen1A magic-write/backdoor-unlock opcodes are all correctly excluded. However one safe-list entry, 'FF 00 04 45 01 04 01 01 01', is a byte-order mis-decode of fcn 0x140045ba0: the real on-wire frame is 'FF 00 40 50 04 <param> 01 01 01' (opcode 0x40, not 0x04). The listed frame is a fabricated opcode-0x04 command with unknown firmware behavior and should be removed (or replaced with the corrected, read-only frame 'FF 00 40 50 04 01 01 01 01'). The read-card sequence is plausible per MIFARE/ISO14443A and all four cited opcodes (INIT 9A, poll 6B 30, bulk-read 85 10, auth-read 85 01) are real and verified read-only. Recommend: drop the bogus 'FF 00 04 ...' payload; treat the 6B 31 selector=00 value as untested. Everything else verified against the binary.

Issues:

- **[high]** FALSE-SAFE / MIS-DECODE on the SAFE list: payload 'FF 00 04 45 01 04 01 01 01' does not correspond to any command the app actually sends. Function 0x140045ba0 builds its 9-byte payload from `movabs 0x01050104504000ff` into var_20h (little-endian bytes = FF 00 40 50 04 01 05 01), then overwrites var_25h with the divide-by-10 param and var_26h=01, var_28h=01. Re-verified on the binary, the true on-wire bytes are: 20:FF 21:00 22:40 23:50 24:04 25:<param> 26:01 27:01 28:01 => 'FF 00 40 50 04 <param> 01 01 01'. The real OPCODE IS 0x40, not 0x04. The raw analysis transposed the movabs bytes and read opcode 0x04 + a phantom '45 01 04' body. Consequence: the proposed safe payload 'FF 00 04 ...' is a fabricated opcode 0x04 frame whose firmware behavior is unknown (PROTOCOL.md already shows unknown low opcodes like FF 00 60 / FF 00 70 returning error 0xFD). It should be REMOVED from the safe list. If the intent was to exercise this function, the correct frame is 'FF 00 40 50 04 01 01 01 01' (param=1). The function itself is read-only (caches the 64B response, no field write), so the corrected frame is safe; the as-listed frame is just wrong/unverified.
- **[low]** 6B 31 ('FF 00 6B 31 00', fcn 0x1400463c0) takes a 1-byte selector parameter (cl). The function is genuinely read-only (validates sentinels resp[1]==0x29, resp[4]==0x22, resp[0x16..17]==90 00, returns UID length + 16-byte trailer; no card/device write). But the specific selector value 0x00 is not observed at any call site in the binary that I verified - 6B 30 is the one driven by the dispatcher. Selector 0 is a plausible 'default/cascade-0' value and the opcode family is read-only, so risk is low, but treat the 00 selector as untested rather than firmware-verified.
- **[info]** Everything else on the SAFE list verified correct against the binary: INIT 'FF 00 9A 5A A5 54 69 61 6E' (fcn 0x140043c44: byte FF, 00, 9A, dword 0x6954A55A LE = 5A A5 54 69, word 0x6E61 = 61 6E = 'Tian', len 9, followed by a RECV) -> exact match; 'FF 00 68' / 'FF 00 69' (model/serial getters, read-only); 'FF 00 6B 30' (fcn 0x140046110, word 0x306b, len 4, poll, read-only); 'FF 00 6A 01 00 08' (fcn 0x140046d90, dword 0x016A00FF LE = FF 00 6A 01 + arg3 0x0800 LE, len 6, poll/anticollision, read-only); 'FF 00 85 34' (fcn 0x140046770, byte FF + word 0x3485 = 85 34, len 4, config GET, read-only - just caches response); 'FF 00 66 00 1E 00 00 00 00 47 04' (fcn 0x1400486b0) and 'FF 00 66 00 1E 00 00 00 00 41 05' (fcn 0x140048b20) - both built from the 0x14008ee60 template 'FF 00 66 00 1E' (confirmed in-binary) + rcx=0 + subopcode + len byte; both read-only (status/length-gated readback, no card write); 'FF 00 85 10 00 00 00 02 FF' (fcn 0x140045d70, qword 0x8500ff LE = FF 00 85 00 + sub 0x10 + rcx=0 + rdx=0x200 LE + r8=0xFF, len 9, bulk-read setup, returns on 64B, no write). The 0x14008edef template is confirmed 'FF 00 85 00' and 0x14008ee60 is confirmed 'FF 00 66 00 1E', validating the whole 0x85 and 0x66 family decodes.
- **[info]** READ-CARD SEQUENCE is plausible and the cited opcodes are all real and correctly read-only/non-card-destructive: (1) INIT 'FF 00 9A 5A A5 54 69 61 6E' -> verified. (2) 'FF 00 6B 30' poll/select returns UID+SAK -> verified read-only (fcn 0x140046110). (3) 'FF 00 85 10 00 00 00 02 FF' bulk-read preamble/load-key -> verified read setup. (4) 'FF 00 85 01 <uid0..3> <key_hi key_lo> <r8b> <r9b>' MIFARE authenticated read (fcn 0x140045c80: header word 0x00FF=FF 00, byte 0x85, sub 0x01, then 4 UID bytes from [rcx], arg2[1] then arg2[0] byte-swapped key, then r8b/r9b, len 12) -> verified; returns 0, caches response for per-block reads. This is a standard ISO14443A flow: anticollision/SELECT to get UID/SAK, then authenticate+read with key. Caveat: 'FF 00 85 01' performs a MIFARE Classic AUTHENTICATE with the supplied key. On the target sector this is a read of card content (non-destructive to the device), but a wrong key on some clone/locked cards can increment a card-side auth failure counter - standard MIFARE behavior, not a tool bug. The sequence does NOT include any of the Gen1A magic-write opcodes (0x66 sub 0x41-0x47 categories / the 0x46 backdoor unlock at fcn 0x140048c50, which expects the '/'(0x2f)+'('(0x28) handshake), so it cannot brick/rewrite a card UID. Correctly scoped as read-only.
- **[info]** Destructive classification spot-check is sound: the genuinely dangerous magic-card UID-write path (fcn 0x140048c50 = Gen1A backdoor unlock, category words 0x0046 then 0x2846, handshake answer '/'+'(') and the block-write functions (0x140048900/a10/49e40 category 0x43/0x44, 0x140045e40 raw data write 85 12) are all flagged is_write_or_destructive=true and NONE of them appear on the SAFE list. No false-safe among the write opcodes. Note one labeling nuance: 0x140046660 (85 32) and the 0x66-family config setters are marked destructive='writes device config' - they alter reader RF/timing registers, not card data, so they are recoverable via re-init, but excluding them from the safe list is the correct conservative call.

**Corrected safe-to-test payloads:**

```
FF 00 9A 5A A5 54 69 61 6E
FF 00 68
FF 00 69
FF 00 6B 30
FF 00 6B 31 00
FF 00 6A 01 00 08
FF 00 85 34
FF 00 40 50 04 01 01 01 01
FF 00 66 00 1E 00 00 00 00 47 04
FF 00 66 00 1E 00 00 00 00 41 05
FF 00 85 10 00 00 00 02 FF
```


---

> **LIVE REPLAY RESULT (2026-06-19):** Mọi lệnh trong corrected_minimal_sequence được
> reader chấp nhận (đều trả payload=00), NHƯNG poll sau đó KHÔNG đổi: 6B30 trả response
> y hệt (resp[8]=0x78), 6A vẫn báo no-card. Kết luận: chuỗi RF-init tĩnh CHƯA đủ để bật
> dò 13.56MHz, HOẶC thẻ test là 125kHz (không phải 13.56MHz). Verifier khuyến nghị: cần
> USB-capture nfcPro để lấy ground-truth (init thật bị GUI/runtime chi phối). Treat chuỗi
> dưới là GIẢ THUYẾT, chưa verified-sufficient.

## 8. RF-init gating sequence: enter 13.56 MHz ISO14443A poll mode

`FF 00 6B 30` / `FF 00 6A 01 00 08` return a clean UID only after the reader's RF
front-end is configured. nfcPro does this with an ordered burst of `0x66`-family
config writes right after the INIT handshake, before the first poll. Reversed from
the power-on init block `.text 0x140011259-0x14001145c` (fcn.140011200).

**Context handle is the fixed constant `48 E8 01 00`** (LE `0x0001E848`) for every
`0x66` command here - confirmed at `0x140011260` (`mov ecx,0x1e848`). It is a device
context token, NOT a carrier frequency. (The 125 kHz LF clone path in fcn.140065000 /
the 13x-sub00 loop at 0x140012351 swaps it for a jump-table value - do **not** replay
that path for card poll; it programs an LF write.)

All payloads below are payload bytes only; wrap each in the
`01|total|seq|...|cksum|FE` envelope from PROTOCOL.md. Sleep ~10-50 ms between writes
(nfcPro uses 50 ms after sub1F/sub13/sub00, 15 ms after sub12).

### Minimal sequence (cold device - no live capture needed)

This is the unconditional subset: on a cold device nfcPro itself skips the three
runtime sub12 calibration writes (binary branch `je 0x140011632` when global
`0x140183bc0==0`). Send these in order, then poll:

```
FF 00 9A 5A A5 54 69 61 6E                          ; INIT handshake -> 00
FF 00 6A 01 00 08                                   ; prime poll (also fills calib globals if card present)
FF 00 66 00 1E 48 E8 01 00 1F 18 01 EF 00 9B 00 86 01   ; sub1F antenna tune (regs 280,239,155,390)
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E C0 F5 00 9A   ; sub13 ('Tian' + 0x9A00F5C0)
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E 00 00 00 00   ; sub13 (+ 0)
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E 19 92 04 27   ; sub13 (+ 0x27049219)
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E 1C 0B 58 48   ; sub13 (+ 0x48580B1C)
FF 00 66 00 1E 48 E8 01 00 12 00 00 00 10 70 60     ; sub12 idx0 const (0x60701000)
FF 00 66 00 1E 48 E8 01 00 00 64 00 00              ; sub00 timing (param=100) - LAST config
FF 00 6B 30                                         ; poll + anticollision/SELECT -> UID
```

`sub1F` (antenna) and `sub00` (timing) are the load-bearing RF writes and are fully
constant. The four `sub13` 'Tian' writes and the `sub12 idx0` write are also constant.

### Full sequence (warm device - matches nfcPro exactly)

Adds the three device-calibration `sub12`/`sub13` idx=2/3 pairs whose 4-byte data
field is RUNTIME (`<reg1>`/`<reg2>`). These come from a prior `0x6A` poll response
(globals `0x140183bc1`/`bc5` -> `[rbp+0x360..0x368]`). Capture them by USB-sniffing
nfcPro's `... 12 00 0X <dword>` frames, or skip this list and use the minimal one.

```
FF 00 9A 5A A5 54 69 61 6E
FF 00 6A 01 00 08
FF 00 66 00 1E 48 E8 01 00 1F 18 01 EF 00 9B 00 86 01
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E C0 F5 00 9A
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E 00 00 00 00
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E 19 92 04 27
FF 00 66 00 1E 48 E8 01 00 13 00 07 54 69 61 6E 1C 0B 58 48
FF 00 66 00 1E 48 E8 01 00 12 00 01 54 69 61 6E
FF 00 66 00 1E 48 E8 01 00 13 00 01 54 69 61 6E 1C 0B 58 48
FF 00 66 00 1E 48 E8 01 00 12 00 02 <reg1:4 LE>     ; RUNTIME calib
FF 00 66 00 1E 48 E8 01 00 13 00 02 <reg1:4 LE> 1C 0B 58 48
FF 00 66 00 1E 48 E8 01 00 12 00 03 <reg2:4 LE>     ; RUNTIME calib
FF 00 66 00 1E 48 E8 01 00 13 00 03 <reg2:4 LE> 1C 0B 58 48
FF 00 66 00 1E 48 E8 01 00 12 00 00 00 10 70 60
FF 00 66 00 1E 48 E8 01 00 13 00 00 00 10 70 60 1C 0B 58 48
FF 00 66 00 1E 48 E8 01 00 00 64 00 00
FF 00 6B 30
FF 00 6A 01 00 08
```

### Why `6B30` returned `resp[8]=0x78` before

Without the `sub1F` antenna tune and `sub00` timing register, the PN533 front-end
polls with default (wrong) RF parameters; the SELECT/anticollision returns garbage in
the length selector (`resp[8]`) instead of 4/7/10. The two constant writes (`sub1F` +
`sub00`) are the most likely fix; the `sub13`/`sub12` writes load the rest of the
register file. Test incrementally: send the minimal list, poll, and if `resp[8]` is
still not 4/7/10, the runtime calibration dwords are needed - capture them live.

### Builder reference (payload byte layout)

| Sub | Builder fn | Layout |
|---|---|---|
| `1F` antenna | fcn.140047e40 | `FF 00 66 00 1E 48 E8 01 00 1F <w0:2><w1:2><w2:2><w3:2>` (280,239,155,390) |
| `13` multi-field | fcn.1400480a0 | `FF 00 66 00 1E 48 E8 01 00 13 <f0=00><f1><d:4><d2:4>` (d = 'Tian'/data, d2 = scratch dword) |
| `12` set+readback | fcn.140047f70 | `FF 00 66 00 1E 48 E8 01 00 12 <00><idx><d:4>` (5-byte readback into out) |
| `00` timing | fcn.140046a40 | `FF 00 66 00 1E 48 E8 01 00 00 <p:2><00>` (p=0x64=100) |

`'Tian'` = `54 69 61 6E`. Response for all `0x66` config is status-only: success when
`resp[1]==0x0C` and `resp[4]==0x05`. set-then-readback variants additionally copy
`resp[5..8]->out[0..3]`, `resp[9]->out[4]`.

### Do NOT replay (these are the 125 kHz LF clone path, not RF init)

The `sub21`/`sub22` indexed writes, the `sub4F` carrier, the `sub32/33/34` LF writes,
and the 13x-`sub00` loop at `0x140012351` all belong to fcn.140011200's LF
card-WRITE/clone tail (it streams card-ID `0x2135A847` and the Gen1A `0x9933DDBB`
backdoor). Replaying them programs a 125 kHz tag write - irrelevant and destructive
for a 13.56 MHz poll. The two no-send helpers fcn.140043500 (serial-port object, null
on USB-HID) and fcn.140043710 (host string format) emit no HID command - skip both.