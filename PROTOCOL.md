# X7 (XIXEI Smart Reader) USB-HID protocol - reverse-engineered

Device: **XIXEI X7-P** (mfr "NSCCN"), serial P39705638618.
USB HID **VID 0x2518 / PID 0x6022**, single interface, no report ID.
HID reports: **64-byte** Input (interrupt IN) + 64-byte Output (interrupt OUT).
Internally a **PN533** chip, but the device does NOT accept raw PN533 (D4/D5)
frames - it speaks a vendor opcode set wrapped in the envelope below.

Source of truth: reversed from `nfcPro_x64.exe` (a.k.a. nfcCopy) with radare2.
Verified live against the physical reader on macOS (Apple Silicon) via hidapi.

## Transport envelope

All numbers are bytes. The device sees a 64-byte report starting at the `0x01`
marker. On macOS hidapi `hid_write` we prepend one `0x00` report-id byte, so the
write buffer is 65 bytes: `00 | <64-byte report>`.

```
REQUEST  (host -> reader), 64-byte report:
  [0] 0x01            marker (constant for commands)
  [1] total           = len(payload) + 6   (also = index of the 0xFE trailer + 1)
  [2] seq_lo          16-bit sequence, little-endian
  [3] seq_hi
  [4 .. 4+L-1] payload   command bytes, L <= 48
  [4+L] cksum         = (~sum(report[0 .. 3+L])) & 0xFF     (marker..last payload)
  [5+L] 0xFE          trailer
  [6+L ..63] 0x00     zero padding

RESPONSE (reader -> host), 64-byte report:
  [0] 0x02            marker (response)
  [1] total           = len(payload) + 6
  [2] seq_lo          echoes request seq, low bit set (req seq 0 -> resp 1)
  [3] seq_hi
  [4 .. ] payload     response data / status
  [..] cksum          same algorithm
  [..] 0xFD           trailer (note: 0xFD on responses, 0xFE on requests)
```

Sequence counter: nfcPro keeps a global 16-bit counter (`.bss 0x148718f72`) and
**increments it by 2 after every command**. The device echoes `req_seq | 1`.
Starting at 0 works; the device does not appear to reject arbitrary seq values.

Checksum verified: e.g. response `02 07 01 00 00 f5 fd` ->
sum(02+07+01+00+00)=0x0A, ~0x0A & 0xFF = 0xF5. ✓

## Verified commands

Payload format for device-info commands is `FF 00 <opcode>`.

| Payload (hex)              | Response payload         | Meaning                    |
|---------------------------|--------------------------|----------------------------|
| `FF 00 9A 5A A5 54 69 61 6E` | `00`                  | **INIT handshake** -> OK   |
| `FF 00 68`                | ascii `XIXEI X7-P`       | get model string           |
| `FF 00 69`                | ascii `P39705638618`     | get serial number          |
| `FF 00 66`                | `00`                     | (OK / needs arg - TBD)     |
| `FF 00 6A`                | `00`                     | (OK / needs arg - TBD)     |
| `FF 00 60`, `FF 00 70`    | `fd`                     | unsupported/error status   |

Status bytes seen: `0x00` = OK, `0xFD`/`0xFE` = error/unsupported.

## Reverse-engineering map (for next session)

radare2 project saved: `re/nfcpro.r2proj` (load: `r2 -q -c 'Po <path>' nfcPro_x64.exe`).

Core transport functions in nfcPro_x64.exe:
- `fcn.1400453f0` - SEND wrapper (builds envelope, only caller of `hid_write`).
  Args: (this, rdx=payload ptr, r8=payload len). Clamps len to 0x30 (48).
- `fcn.140045330` - RECV wrapper (reads 64B, 2000 ms timeout; calls `hid_read_timeout`).
- `fcn.140045210` - CONNECT (hid_enumerate by VID/PID/iface, then hid_open_path).
- Startup INIT command: `.text 0x140043c44` builds the 9-byte init payload.

~40 command functions call the send wrapper (`axt @ 0x1400453f0`), spanning
`fcn.140045750 .. fcn.14004a6c0`. These build the higher opcodes (card scan,
MIFARE auth/read/write, NTAG, CPU card, 125 kHz). Each can be decompiled and
verified live. `fcn.140045750` sends `FF 00 68` then `FF 00 69` (model+serial).

## Still to map (the bulk of full-parity work)

- Card detection / poll / get UID (13.56 MHz ISO14443A).
- MIFARE Classic: auth (key A/B), read block, write block.
- Magic/Gen1 (UID-writable) card write path (for cloning).
- NTAG21x read/write. CPU card (APDU). 125 kHz EM/T5577 path.
- Key recovery (nested/hardnested) - upper layer; open-source, port separately.
