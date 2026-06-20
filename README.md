# tenor/rekey

a native **macOS** tool for the **XIXEI X7 / X7-P** USB NFC reader - the open
replacement for **nfcPro** (a.k.a. nfcCopy / wCopy), which is Windows-only.

It talks to the reader directly over USB-HID (no Windows, no driver, no VM) on
Apple Silicon, and gives you read / decode / clone / key-recovery / APDU for
13.56 MHz MIFARE Classic cards.

> Built by [Tenor](https://tenor.vn). The X7 (USB `2518:6022`) is an N32G020 MCU
> that emulates an NXP PN532; nfcPro's vendor HID protocol was reverse-engineered
> and ground-truthed from a USB capture. See `PROTOCOL.md` + `OPCODES.md`.

## what's here

- **`probe/`** - the engine (Python + hidapi via ctypes), and a CLI:
  - `x7tool.py info | read | decode -o card.mfd | write card.mfd | apdu <hex>`
  - `x7lib.py` MIFARE driver, `x7.py` HID envelope, `x7_init.py` captured RF-init,
    `crapto1.py` + `x7crypto.py` the Crypto1 cipher + key recovery,
    `x7d.py` a JSON-over-stdio daemon the app speaks to.
- **`app/`** - the native macOS app (SwiftUI). One unified workspace: the card is
  the document; read / decode / clone / recover / apdu are actions on it. Light +
  dark, four languages (vi / en / zh / ja). Built with XcodeGen; it drives the
  Python engine as a child process over the narrow `x7d.py` contract.

## quick start (CLI)

```bash
brew install hidapi        # one-time
cd probe
python3 x7tool.py info
python3 x7tool.py read     # UID + ATQA + SAK + type
python3 x7tool.py decode -o card.mfd
```

## quick start (app)

```bash
cd app
xcodegen generate
xcodebuild -project tenorrekey.xcodeproj -scheme tenorrekey -configuration Debug build
```

## status

the engine (read / decode / clone / format / apdu) is verified on real hardware
and walks the key dictionary at the reader's full speed (~26 ms/key). the macOS
app is a unified workspace - themes, four languages, live decode, clone with
brick safety (it refuses to write a self-locking trailer or a zeroed key slot),
an apdu console, and an editable key dictionary - and ships as a self-contained,
relocatable `.app` + a drag-to-Applications `.dmg` (ad-hoc signed; Developer-ID
notarization is the remaining distribution step). nested / MFKey32 live
key-recovery is the one in-progress R&D area: the offline Crypto1 + recovery is
ready, but a live attack needs a hardware capture to finish.

example keys and UIDs in the code and tests (`a0b1c2d3e4f5`, `01 02 03 04`) are
placeholders, not real credentials.

## license

GPL-3.0-or-later. `probe/crapto1.py` is a faithful port of the Proxmark3 /
nfc-tools `crypto1.c` + `crapto1.c` (Copyright 2008-2014 bla, GPLv3; algorithm
from Garcia, de Koning Gans et al., "Dismantling MIFARE Classic"), so this work
inherits GPLv3. Use it for legitimate purposes only - card management on systems
you own or are authorized to service.
