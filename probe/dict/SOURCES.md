# Bundled MIFARE Classic key dictionary - sources

`mfc_keys.dic` is assembled by `build_dict.py` from these public, curated
dictionaries (fetched 2026-06-20). All are GPL/MIT-family; this repo is
GPL-3.0-or-later, compatible. Keys are public default/vendor keys, not secrets.

| Source | URL | ~keys | License |
|---|---|---|---|
| Proxmark3 Iceman `mfc_default_keys.dic` | github.com/RfidResearchGroup/proxmark3 (client/dictionaries) | 2509 | GPL-3.0 |
| MifareClassicTool `hotel-std.keys` | github.com/ikarus23/MifareClassicTool | 24 | GPL-3.0 |
| MifareClassicTool `extended-std.keys` | github.com/ikarus23/MifareClassicTool | 2477 | GPL-3.0 |
| nbox aggregate `clean_keys.dic` | github.com/nbox/Chameleon-Ultra-Flipper-Zero-key-dictionary | 4481 | MIT |
| Flipper Unleashed `mf_classic_dict.nfc` | github.com/DarkFlippers/unleashed-firmware | 4082 | GPL-3.0 |
| Proxmark3 `mfc_keys_icbmp_sorted.dic` / `mfc_keys_bmp_sorted.dic` | (ordering only, not new keys) | 1000+1000 | GPL-3.0 |

Merged + deduped + validated (12-hex lowercase) -> **~4513 unique keys**.

**Why not "30-40k":** there is no curated MIFARE Classic dictionary that large.
Files of that size (e.g. `will-caruana/RFID-Brute-Force` `6char.dic` = 248k) are
hex-encoded ASCII password wordlists with near-zero real-card hit rate, and some
are the wrong key length. The real public key universe is ~4.5-5k. Cards a
dictionary misses need the nested attack, not a bigger wordlist.

**Hotel coverage** (the Proxmark set labels keys by vendor): Saflok, VingCard,
Onity, KABA, MIWA ALVH, OMNITEC, Salto, ASSA, plus the Vietnam-relevant
**BETECH** (`aac34d9a4e65`) and **TESA** (`90c270f690c2`) and Hanoi BRT - these
are ordered near the front. Note the Marriott "diversified" keys are per-card
(UID-derived), not reusable defaults.

Re-pull periodically (Proxmark is the living upstream): `python3 build_dict.py`.
