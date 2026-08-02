# Codex sol - rekey decode/write root-cause (2026-07-14)

session id: 019f6048-a586-7b01-8fb4-77e12f3addf0 (read-only analysis)
model: gpt-5.6-sol high

## Verdict

- The decode→swap→write workflow is broken by design conflict introduced in `95121d4`.
- After a detected swap, `liveDump` is deleted. With no previously opened `source`, Write becomes disabled; if invoked anyway, `clone()` silently returns.
- `babe18072016` is most likely the valid authentication key that decoded the founder’s card, not garbage or stale data.
- Normal stationary-card decoding is UID-consistent. A card swap during decoding is not: `x7lib.dump()` can mix cards and return the original UID.
- Unknown-key decode is catastrophically slow: roughly 71,672 auth attempts for 1K and 74,168 for 4K with the founder’s one user key—about 31–32 minutes at the repository’s measured rate, colliding with the 30-minute app timeout.

## A. State-machine trace

1. Card A is placed.

`monitor()` runs every 1.5 seconds and calls `refreshStatus()` when no operation owns the reader ([AppModel.swift:83](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:83>)). A first placement or different UID calls `clearCardState()` before assigning the new `card` ([AppModel.swift:97](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:97>), [AppModel.swift:131](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:131>)).

State becomes approximately:

```text
card = A
sectors = []
liveDump = nil
source = unchanged
```

2. He presses Decode.

The app immediately replaces `sectors` with pending tiles, then calls the daemon with `keyStore.keys` ([AppModel.swift:149](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:149>)). Polling is suspended while `decoding == true`.

On success, the response atomically becomes:

```text
card = response UID
sectors = buildSectors(response)
liveDump = CardDump(response)
```

Evidence: [AppModel.swift:163](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:163>), [AppModel.swift:171](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:171>).

`source` is not changed.

3. He saves.

`saveDumpDialog()` takes only `liveDump`; `saveDump()` writes the raw image and keys sidecar. Neither assigns `source` ([AppModel.swift:406](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:406>), [AppModel.swift:428](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:428>)).

Only File → Open assigns `source` ([AppModel.swift:417](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:417>)).

Therefore after Save:

```text
card = A
liveDump = dump A
source = nil       // assuming no dump was opened earlier
cloneSource = dump A
```

4. He puts card B on the reader.

When the monitor sees B’s different UID, it runs:

```swift
clearCardState()
card = B
```

`clearCardState()` deletes `sectors`, `liveDump`, selections, and clone results, but deliberately preserves `source` ([AppModel.swift:109](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:109>), [AppModel.swift:126](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:126>)).

With no opened source:

```text
card = B
liveDump = nil
source = nil
cloneSource = nil
```

Yes: this confirms the founder’s failure. Write is disabled in the action bar ([RootView.swift:142](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:142>)). If a stale sheet or programmatic path still invokes `clone()`, its guard silently returns ([AppModel.swift:302](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:302>)).

This behavior was deliberately introduced by commit `95121d4`, whose stated purpose was preventing card A’s live data from surviving under card B.

There is one race: if he swaps and presses Write before the next 1.5-second monitor sample, `card` still says A and `liveDump` still contains A. `cloneSource` therefore passes its UID guard ([AppModel.swift:51](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:51>)), while the daemon sees physical card B as the write target. That race can successfully perform the workflow he wants. Once the monitor notices B, it cannot.

5. His expected workflow.

The current comments claim decode→write needs no Save/Open round trip, but the implementation only permits that while the decoded card remains on the reader. That is useless for one-reader source→target cloning.

6. “Write made it decode.”

There is no call path from Write to `decode()`.

- Write opens `CloneSheet` ([RootView.swift:146](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:146>)).
- Confirmation calls `clone()` ([CloneSheet.swift:45](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:45>)).
- `clone()` calls only `writeMFD()` ([AppModel.swift:302](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:302>)).

Write does authenticate the target and emits per-block success glyphs. Those glyphs appear on the sector grid/inspector ([SectorGrid.swift:87](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/SectorGrid.swift:87>), [SectorInspector.swift:63](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/SectorInspector.swift:63>)). That can look like the grid is “decoding,” but it is write progress, not a read/decode.

## B. “Garbage babe” root cause

Under a stationary card, this is not a stale-result bug.

`decode()` replaces the visible grid with pending sectors immediately, then rebuilds `card`, `sectors`, and `liveDump` from the same `DecodeResult` ([AppModel.swift:163](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:163>), [AppModel.swift:244](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:244>), [CardDump.swift:20](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/CardDump.swift:20>)). A properly detected new UID also deletes the previous grid and live dump.

`babe18072016` is valid 12-digit hex. `KeyStore` normalizes it and puts newly added keys first ([KeyStore.swift:18](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/KeyStore.swift:18>)). The daemon prepends all user keys to the 17,553-key built-in dictionary ([x7d.py:112](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:112>)).

`find_key()` reports a key only after the reader returns PN532 authentication success ([x7lib.py:170](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:170>), [x7lib.py:219](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:219>)). The UI then deliberately displays that recovered key ([SectorInspector.swift:47](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/SectorInspector.swift:47>)).

Therefore, for UID `fc d9 b6 db`, where the founder says `babe18072016` really is KeyA/KeyB, seeing `babe18072016` is correct. It is the authentication key, not the hotel payload or “decoded room code.”

There are three real edge cases:

1. Mid-decode card swap: real bug.

`dump()` captures `info` once at the start ([x7lib.py:253](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:253>)). During key search and every block read it calls `poll()`, which overwrites `self.uid` with whatever card is currently present ([x7lib.py:150](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:150>), [x7lib.py:283](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:283>)). There is no comparison to the original UID. It finally returns the original `info["uid"]` even if later blocks came from another card ([x7lib.py:317](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:317>)).

The app monitor is suspended during decode, so it cannot catch that swap. A decode can therefore be mixed or attributed to the wrong UID.

2. Failed/cancelled re-decode.

`decode()` does not clear the old `liveDump` when a new decode begins. If the new decode throws, the prior `liveDump` survives ([AppModel.swift:149](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:149>)). Normally the swap monitor later clears it, but there is a short stale-source window.

3. Previously opened source.

`cloneSource` always prefers `source` over a fresh `liveDump` ([AppModel.swift:56](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:56>)). If the user opened an old dump and then decoded another card, Write still clones the opened dump. The grid is fresh; the write source is stale by user expectation.

## C. Slowness, quantified

The built-in dictionary has 17,553 unique entries ([mfc_keys.dic:17553](</Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/mfc_keys.dic:17553>)). The “~4.5k” comment in `x7lib.py` is stale ([x7lib.py:36](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:36>)).

With `babe18072016` as one non-built-in user key, the search pool is 17,554 keys.

For a complete miss, `find_key()` does:

- First 64 keys: A and B.
- Remaining keys: complete A sweep, then complete B sweep.

That is exactly `2N`, not “one auth per key”: 35,108 auth attempts per full sector miss ([x7lib.py:198](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:198>)).

After two full misses, `dump()` switches later sectors to the 52-entry `DEFAULT_KEYS` list ([x7lib.py:245](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:245>), [x7lib.py:263](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:263>)).

Worst-case auth counts:

| Card | Calculation | Auth attempts |
|---|---:|---:|
| 1K, 16 sectors | `2 × 35,108 + 14 × 104` | 71,672 |
| 4K, 40 sectors | `2 × 35,108 + 38 × 104` | 74,168 |

Without the extra user key, subtract four attempts.

Every failed attempt normally performs three USB command round-trips: select, auth, then re-poll ([x7lib.py:219](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:219>)). That is roughly:

- 1K: 215,000 USB exchanges.
- 4K: 222,500 USB exchanges.

Using the repository/history’s measured ~26 ms fast attempt:

- 1K: approximately 1,863 seconds, or 31m 03s.
- 4K: approximately 1,928 seconds, or 32m 08s.
- Additional transport/timeouts can only make that worse.

The Swift engine kills decode after 1,800 seconds ([X7Engine.swift:193](</Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/X7Engine.swift:193>)). An all-unknown card can therefore time out before the algorithm naturally finishes.

Key reuse is wired correctly. A proven key is moved to the front and excluded from its old position ([x7lib.py:260](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:260>), [x7lib.py:278](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:278>)). It is effective after the first success.

`FAST_HEAD` is only per-sector ordering. There is no card-wide priority-prefix pass. The code can spend 15 minutes exhaustively failing sector 0 before discovering that sector 1 uses a user/default key.

For a healthy 1K card where `babe18072016` is first and works as KeyA everywhere:

- 16 discovery auths.
- 64 block-read auths.
- 80 total auth operations.
- Roughly 241 USB command exchanges.

For 4K: about 296 auths and 889 USB exchanges. A KeyB-only card adds one failed A attempt per sector.

A known-key card should therefore take seconds or perhaps tens of seconds, not many minutes. It can still be slower than ideal because every block is separately polled, authenticated, and read, with up to six coupling retries ([x7lib.py:283](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:283>)). I cannot determine actual X7 latency or coupling quality without hardware. If the founder’s babe card is still extremely slow, likely explanations are:

- The key is not actually present in the runtime `KeyStore`.
- It does not work on every sector.
- Some sectors use a different key.
- Coupling failures trigger retries/timeouts.

## D. Minimal safe workflow change

The correct minimal state change is in `AppModel.decode()`:

After a successful, UID-stable decode, build one `CardDump` and assign it to both:

```text
liveDump = dump       // live display/save state
source = dump         // explicit clone source
```

That makes Decode an explicit “select this image as the clone source” action. `clearCardState()` can continue deleting `liveDump` and the grid on card removal/swap while preserving `source`, exactly as it already does.

Condition: promote only after the daemon completes successfully and confirms the card UID stayed equal to the UID pinned at decode start. The current Python implementation cannot provide that guarantee because of the mid-decode mixing bug; add the same target-UID check to `dump()` that write already uses.

Do not fix this by changing `cloneSource` back to `source ?? liveDump` without the UID guard, and do not stop clearing `liveDump` on swaps. That would directly revert the protection from `95121d4`.

Setting `source` inside `saveDump()` would fix only the founder’s exact Save→swap sequence. It would still violate his no-Save expectation. Save should remain an export operation.

## E. Ranked fix plan

1. Fix the source→target workflow.

In `AppModel.decode()`:

- Capture the starting UID.
- On a successful UID-stable response, construct one dump and assign it to both `liveDump` and explicit `source`.
- Replace any previously opened `source`; a fresh Decode must become the active source.
- Show the existing Source tag and a direct instruction: remove source card, place target, press Write.
- Make `clone()` report “no clone source” instead of silently returning if its guard fails.

Keep `clearCardState()` and the `cloneSource` live UID guard unchanged.

2. Close the wrong-card decode hole.

In `X7Card.dump()`:

- Pin `target = info["uid"]`.
- After every poll used during key search/read, abort if `self.uid != target`.
- Return an explicit “card changed during decode” error; never return partial mixed blocks under the original UID.

In `AppModel.decode()`:

- Reject a response whose UID differs from the captured starting UID.
- Clear the pending/live result on failure rather than retaining the prior same-UID `liveDump`.

The write path already has the correct pattern and must remain intact ([x7d.py:155](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:155>), [x7d.py:224](</Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:224>)).

3. Fix the search order before changing transport behavior.

In `X7Card.dump()`:

- Run a card-wide fast prepass using user keys plus the small priority/default prefix across every sector.
- Promote any successes immediately and reuse them everywhere.
- Only then perform deep dictionary sweeps on unresolved sectors.
- After one exhaustive A+B miss, stop or require an explicit exhaustive/recovery action instead of automatically spending another ~15 minutes.
- Preserve A+B support: removing KeyB search outright would break valid cards.
- Preserve proven-key move-to-front.

This removes the worst ordering failure without weakening authentication correctness.

4. Optimize known-key block reading only after hardware validation.

The per-block poll→auth→read cycle is expensive but was added for reliability. Test whether the X7 maintains the Crypto1 session across sequential block reads; if verified, authenticate once per sector and read its blocks without re-authenticating each one. Do not assume this works from PN532 theory alone.

Do not enable nested recovery yet: the UI explicitly marks it unverified. Do not weaken trailer validation, UID-write confirmation, target-swap aborts, malformed-block rejection, or the self-locking trailer protections in `x7d.py`. Those are correct anti-brick/anti-wrong-card hardening.
---
## Executor result (fix commit ebd8b32)

Implemented all three fixes:

- `app/Sources/AppModel.swift`
  - `decode()` pins the starting UID, rejects swapped-card results, and assigns one fresh dump to both `liveDump` and `source`.
  - `clone()` now reports `"no clone source"` when appropriate.
- `probe/x7lib.py`
  - `X7Card.dump()` now pins the UID, aborts on card swaps, performs the card-wide fast prepass, and stops after the first full dictionary miss.
  - Corrected the dictionary docstring to `~17.5k keys`.
- `probe/x7d.py`
  - `Daemon.decode()` passes user keys separately into `dump()` while preserving the existing user-first merge.
- `probe/test_all.py`
  - Updated early-exit and key-reuse tests.
  - Added the mid-decode card-swap regression test.

Safety guards remain intact: `write_mfd`/`format` target checks, `clearCardState()` clearing `liveDump`, the `cloneSource` UID guard, trailer patching, access-bit validation, trailer lockout checks, and other anti-brick protections.

No git add, commit, or push performed.

Test result: `ALL TESTS PASSED`
---
## UX red-team (2026-07-14, mô hình tài liệu vs đầu đọc) - gpt-5.6-sol high

Verdict: the stable workflow is an intentional, clearly labelled A → B clone. However, there is still a real timing path where the daemon writes a card whose UID was never shown in the sheet. I would not call the wrong-card invariant fully preserved.

## A. Wrong-card write safety

The normal path is sound:

- The sheet shows the document UID on the left and cached reader-card UID on the right at [CloneSheet.swift:25](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:25).
- With no card, the target displays `waiting_card`, and the write button is disabled at [CloneSheet.swift:30](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:30) and [CloneSheet.swift:55](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:55).
- Writing is only initiated by the sheet’s write button or destructive confirmation at [CloneSheet.swift:45](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:45) and [CloneSheet.swift:61](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:61). There are no automatic `clone()` callers.
- The daemon waits for a target, pins `target = i["uid"]`, and checks `c.uid != target` before every block authentication/write attempt at [x7d.py:158](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:158) and [x7d.py:225](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:225). A detected mid-write swap aborts at [x7d.py:244](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:244).

So, with a stable target, this is explicitly an intentional clone with clear labelling. Card A’s image being written to card B is the requested operation, not a silent side effect.

But there is a genuine silent-wrong-target race:

- The UID shown by the sheet comes from the asynchronously polled, cached `model.card`.
- The Swift request does not send that expected target UID; `CloneParams` contains only blocks, keys, trailers, and the block-zero option at [X7Engine.swift:242](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/X7Engine.swift:242).
- The daemon independently chooses whichever card it sees when `wait_for_card()` completes.

Concrete path: the sheet shows source A → target B; B is removed or replaced after display/confirmation but before `write_mfd` pins its initial target; the daemon sees C and writes C. A fast source-to-blank swap can also leave cached `model.card` showing A for up to the next poll while the actual reader already holds B. The user initiated a write, but did not see the UID of the card actually written.

The final confirmation dialog neither repeats/fixes the UID nor disables itself if the card disappears after the first button press. The daemon’s pin protects against changes after daemon acquisition, not disagreement between the UI target and the daemon’s initial target.

Therefore: 95121d4’s broad “never carry A into B” behavior is intentionally removed. Its ordinary hazard is replaced with a clearly labelled clone flow, but its silent-wrong-card protection is not fully replaced because the displayed target is not atomically bound to the write.

Theoretical residual: there is also a tiny hardware TOCTOU window between the daemon’s UID check and `auth`/`write_block`. Usually a card swap breaks authentication, but identical-key cards and reader timing should be tested physically.

## B. State-model coherence

### Real: failed/cancelled decode leaves a false document grid

A Classic decode replaces the visible sectors with pending tiles before it succeeds at [AppModel.swift:180](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:180). On failure, cancellation, or UID mismatch, nothing restores the previous sectors or clears the pending/partially updated grid at [AppModel.swift:188](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:188) and [AppModel.swift:202](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:202).

Meanwhile, `source` remains the previous document. `CanvasView` treats any nonempty sectors as a document at [RootView.swift:254](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:254), while `DocHeader` takes its UID from the old `source` at [RootView.swift:283](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:283).

Result: header/write source A, but the grid is a partial attempted decode of B. That is materially misleading.

### Real: opening a dump does not replace the canvas document

`loadDump()` changes only `source` at [AppModel.swift:445](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:445). It does not rebuild `sectors`, clear `pages`, or reset selection.

Consequences:

- If the canvas was showing A, opening B produces header/source B over grid A.
- If the canvas was empty, B becomes writable/saveable but is not shown because the canvas branches on `sectors/pages`, not `source`.

This directly contradicts the new single-document model.

### Real: NTAG becomes an orphaned pseudo-document

The NTAG path sets `pages` but never sets or clears `source`, never updates document identity from `r.uid`, and does not check `r.present` or validate it against `startUID` at [AppModel.swift:174](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:174).

Two bad cases:

- With an existing Classic source A, reading NTAG B shows B’s pages while the document header and write source remain A.
- With no existing source, the pages persist after removal/unplug, but `DocHeader` falls back to the now-absent reader card and shows `document -`, `sak -` at [RootView.swift:283](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:283).

This is exactly the “pages persist and DocHeader becomes `-`” failure you suspected.

### Real limitation: `canFormat` does not prove physical-card identity

For stable, unique UIDs, `canFormat` correctly blocks a visibly different card at [AppModel.swift:63](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:63).

But “same UID” does not mean “the very same card,” especially in an app that writes UIDs to magic cards by default. A cloned card can share both UID and keys with the source. `canFormat` will enable, and format will authenticate with the source document’s keys at [AppModel.swift:358](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:358).

There is also the same cached-target race as clone: `format()` checks cached `model.card`, but the daemon pins whichever physical card it initially sees at [x7d.py:255](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:255). A fast physical swap before daemon acquisition can format a different card if the document keys authenticate it.

### Real: format discards the document even on partial/failed format

Any `present == true` result clears `source`, sectors, and pages, even when `failed` is nonempty—including a total authentication failure at [AppModel.swift:365](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:365). That contradicts the “on success” comment and can destroy the only in-app copy of the pre-format image without successfully formatting the card.

Worse, the daemon returns an `error` field on mid-format swap at [x7d.py:295](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:295), but `FormatResult` has no `error` property at [Models.swift:52](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:52). Swift ignores the abort reason and still clears the document.

### Coherent parts

- `clearDocument()` correctly clears source, sectors, pages, selection, and clone results at [AppModel.swift:145](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:145).
- A successfully decoded Classic document survives removal and reader unplug coherently.
- `clearWriteResults()` correctly limits swap cleanup to target-specific write glyphs.

Low-severity UX issue: after unplug, `ReaderHint` says “place the target card” because it checks only `card == nil`, not `readerOnline`, at [RootView.swift:323](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:323).

## C. Write-without-card UX

Before invocation, the sheet degrades correctly:

- Target shows the waiting placeholder.
- Confirm/write remains disabled until `model.card != nil`.

At the model level, `clone()` is defensive:

- Missing source sets `"no clone source"` at [AppModel.swift:324](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:324).
- No target returned by the daemon sets `"no card on reader"` at [AppModel.swift:343](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:343).
- Thrown errors are caught at [AppModel.swift:348](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:348).

It should not crash.

But user-visible behavior is not graceful after a timing failure. `CloneSheet` dismisses immediately when it launches the task at [CloneSheet.swift:51](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:51) and [CloneSheet.swift:63](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift:63), and `lastError` is never rendered anywhere in the UI. A repository-wide reference check found only assignments in `AppModel`.

So if the card is removed between enablement and execution, the model records an error, but to the user the sheet closes and nothing happens. That is effectively a silent no-op. Successful status polling also clears `lastError` at [AppModel.swift:113](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:113).

Mid-write swap reporting has the same hole: the daemon returns `"error": "card changed during write"`, but `WriteResult` lacks an `error` field at [Models.swift:43](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:43). If all earlier blocks succeeded, `failed` may be empty, so Swift can treat an aborted partial write as having no summary error.

## D. Other regressions/incoherence

- Real: the menu Write command does not disable during decode or format; it checks only source/cloning at [App.swift:32](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/App.swift:32), unlike the action bar’s `busy` gate at [RootView.swift:140](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:140). During decode this should be rejected by the engine’s streaming-operation guard, invisibly. During format, the daemon can queue the write and execute it afterward using the `src` already captured by `clone()`, potentially writing the old image straight back onto the just-formatted card.
- Real display bug: `DocHeader` takes SAK from the document but the `isNTAG` discriminator from the current reader card at [RootView.swift:283](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:283). A SAK `0x00` magic Classic document can change displayed type depending on which unrelated card is on the reader.
- Comments at [AppModel.swift:5](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:5) and [RootView.swift:5](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:5) still say “the CARD is the document,” which is now specifically false.

Real-hardware tests I would require:

- Swap/remove the target at every point: sheet open, destructive dialog open, immediately after confirmation, and between blocks.
- Perform a fast A → B placement before the 1.5-second monitor refresh and verify which UID the sheet shows versus which card is written.
- Cancel, unplug, and induce decode failure midway with and without an existing document.
- Read an NTAG, remove it, unplug the reader, and repeat while a Classic source is loaded.
- Format a same-UID clone and a same-UID/different-key card.
- Force total and partial format failures and confirm whether the source document is retained.
- Invoke menu Write while decode/format is running.
- Verify the daemon’s within-block UID-check/auth/write behavior under rapid RF-field swaps.

Recent history inspected:

```text
ebd8b32 decode straight into a writable source, and fail fast on unknown cards
0c05b3b point the brand asset tools at the renamed branding folder
5fe6433 read a magic MIFARE Classic that reports SAK 0x00 as Classic, not NTAG
87f86e0 name the app tenor/rekey in the menu bar and app switcher
2291def ship a styled, Developer-ID-signed, notarized installer
```
---
## Speed strategy (2026-07-14, thẻ lạ = ngu) - gpt-5.6-sol high
### Strategy
## Verdict

Yes—the current behavior is dumb for this customer. It is optimized around one captured deployment instead of hotel work, burns 43 seconds before the real search, and then provides misleading progress.

Highest-impact changes, in order:

1. Rebuild the dictionary in hotel-first order.
2. Put a hard global budget on automatic search.
3. Replace the 16-sector fast prepass with anchor-first search and immediate reuse.
4. Report attempts, phase, elapsed time, and budget continuously.
5. Return an explicit “automatic set missed” outcome, not a nominally successful empty dump.
6. Treat partial dumps as partial—not clone-ready successes.

No code was changed.

## Verified timing

The dictionary contains 17,553 unique keys. BETECH is entry 13,056 and TESA 13,057: [mfc_keys.dic:13056](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/mfc_keys.dic:13056).

With the current 64-key paired head followed by complete A and B sweeps ([x7lib.py:217](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:217)):

| Path | Auth attempts | At 26 ms |
|---|---:|---:|
| Silent 52-key pass across 16 sectors | 1,664 | 43.3 sec |
| BETECH as Key A, deep pass only | 13,120 | 5m 41s |
| BETECH as Key A, including silent pass | 14,784 | 6m 24s |
| BETECH as Key B, including silent pass | 32,273 | about 14m |
| Complete dictionary miss, including silent pass | 36,770 | about 15m 56s |

The Key B case is substantially worse than the stated 5–6 minutes because every tail key is first tried as A before the B sweep begins.

## 1. Dictionary order: reorder it immediately

The exact runtime priority should be:

1. User-entered/site-specific keys.
2. Locally learned recent successful keys, ranked by hit count and recency.
3. Universal MIFARE defaults.
4. Complete hotel/vendor manifest, with founder-market priority:
   - BETECH
   - TESA
   - Saflok
   - VingCard
   - Onity
   - MIWA
   - Salto
   - OMNITEC
   - KABA/ASSA
   - remaining labelled hotel/access keys
5. Proxmark frequency-ranked common keys.
6. Remaining curated public dictionary.
7. Device-capture keys with demonstrated real hit frequency.
8. The remaining one-deployment capture, preserving its original order only within this tail.

User keys already precede the built-in list in the daemon ([x7d.py:112](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:112)), and the local key store puts newest entries first ([KeyStore.swift:4](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/KeyStore.swift:4)). Build on that with a local successful-key cache.

The capture’s observed order is not evidence of card prevalence. Do not promote arbitrary captured entries. Promote only keys with actual successful-auth counts from the founder’s fleet.

The required builder merge is:

`DEFAULTS → COMPLETE_HOTEL_MANIFEST → FREQ → CURATED_REMAINDER → PROVEN_CAPTURE_HITS → CAPTURE_TAIL`

The builder already implements the first four tiers ([build_dict.py:92](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/build_dict.py:92)) but does not ingest the device capture at all. The generated file was subsequently changed independently. That is a reproducibility bug: running the documented builder will not reproduce the shipped dictionary.

There is also a coverage-audit problem. `SOURCES.md` claims VingCard, KABA, Salto and ASSA coverage ([SOURCES.md:24](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/SOURCES.md:24)), but the explicit `HOTEL` hot list contains only 11 keys and does not identify those brands ([build_dict.py:44](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/build_dict.py:44)). Because output strips labels, I cannot verify which exact VingCard/Salto/KABA/ASSA keys would fall inside a 3,000-key boundary. Maintain a labelled vendor manifest instead of relying on source-file incidental order.

## 2. Bounded walk: yes, but budget attempts/time, not “keys”

Use a default automatic budget of:

- 90 seconds of authentication search, and
- approximately 3,500 auth attempts as the deterministic backstop.

Stop when either limit is reached. Reading sectors after a successful key should not consume this search budget.

Why not simply “5,000 keys”? Because a complete miss tries both A and B:

| Unique values | Complete A+B miss | Time |
|---:|---:|---:|
| 3,000 | 6,000 attempts | 2m 36s |
| 5,000 | 10,000 attempts | 4m 20s |
| 17,553 | 35,106 attempts | 15m 13s |

A 5,000-value automatic scan is still a multi-minute freeze with better text. It is not fail-fast.

Recommended modes:

- **Automatic hotel scan:** 90 seconds / ~3,500 attempts.
- **Extended dictionary scan:** explicit user action; first 5,000 ranked values, about 4m20 worst-case.
- **Exhaustive scan:** explicit user action with a visible ~15-minute warning.

After proper ordering, put every explicitly classified hotel/vendor key within the first 256 values. Trying those values as both A and B costs at most 512 attempts, about 13 seconds. The rest of the 90-second budget covers common and frequency-ranked keys.

Coverage facts that can be stated:

- The curated public set is approximately 4,513 unique keys ([SOURCES.md:16](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/SOURCES.md:16)).
- A 3,000-value boundary covers 66.5% of those dictionary entries and omits 1,513 entries.
- A 5,000-value boundary can contain all 4,513 curated entries plus roughly 487 capture-only entries.
- If all labelled hotel keys are explicitly front-loaded, neither boundary misses that labelled hotel block.
- The repo contains no card-hit telemetry, so it cannot support a defensible real-world “99% of hotel cards” claim or identify every brand missed by 3,000.

## 3. Algorithm shape: remove the current all-sector prepass

The current pass tries 52 defaults as both A and B on all 16 sectors before deep search ([x7lib.py:259](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:259), [x7lib.py:311](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:311)). On a uniform hotel card with BETECH/TESA/etc., that buys nothing and costs about 43 seconds.

Use an anchor-first strategy:

1. Try user/recent/default/hotel hot keys on sector 0.
2. If sector 0 misses the hot tier, try the same hot tier on sector 1 as the MAD/different-sector-0 escape hatch.
3. Run the bounded ranked walk on one anchor sector.
4. As soon as any key works, try that key as A and B across every sector.
5. Read every sector it opens immediately.
6. For unresolved sectors, try all already-proven keys, then the small hot set.
7. Spend another deep budget only if the user requested comprehensive/extended decoding.

This preserves the important key-reuse mechanism already present ([x7lib.py:265](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:265)) while avoiding sixteen copies of speculative work.

Do not make sector 0 the only possible anchor. If sector 0 has a special MAD key and sector 1–15 share a hotel key, sector-0-only search can miss the useful key. The current algorithm has the opposite flaw: after the first full miss, later sectors receive only previously proven keys ([x7lib.py:326](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:326)). Thus a proprietary sector 0 can prevent a dictionary-resident, non-default key in sector 1 from ever receiving a deep search.

## 4. Feedback: emit immediately and time-throttle

Progress should fire:

- Before the first authentication attempt.
- At every phase transition.
- At least every 250 ms or every 8–16 attempts.
- Immediately on a successful auth.
- When reuse checking begins and after each sector checked.
- While reading blocks, not only after an entire sector finishes.
- On budget exhaustion, dictionary exhaustion, cancellation, or card removal.

Currently, callbacks occur only every 256 entries in the tail’s A sweep ([x7lib.py:227](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:227)). There are no callbacks in the paired head, no B-sweep progress, and the successful-sector event is emitted only after `read_sector()` completes ([x7lib.py:320](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:320)).

Suggested UI copy:

- `Trying hotel and common keys · sector 0`
- `Authentication attempt 1,284 / 3,500 · Key B · 0:34 elapsed`
- `Key found · checking all sectors 7 / 16`
- `Reading sector 7 · block 2 / 4`
- `Extended dictionary scan · 2:14 elapsed · about 2:06 remaining`

Do not call the numerator “keys tried” unless A/B are paired and clearly defined. Auth attempts are the honest unit.

Also replace sector-based overall progress. The current calculation treats the sector number as completed work ([Models.swift:137](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:137)), but the two-pass algorithm jumps from sector 15 back to sector 0. That bar can regress or imply completion falsely. Base search progress on the global attempt/time budget, with separate reuse/read progress.

The action bar currently shows only `sector X/Y` during decoding ([RootView.swift:194](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:194)); that is exactly the frozen-looking presentation the founder dislikes.

## 5. Honest failure: yes, with one wording correction

For a bounded scan, do **not** say “not in the dictionary.” Say:

> Automatic key search stopped after 3,500 authentication attempts (1m30s). No key matched the hotel/common set. This card may use a site-specific key or a key in the extended dictionary. New-key recovery is not available in this build.

Actions:

- `Add/import known key`
- `Run extended dictionary scan — up to 4m20`
- `Recover keys — not available in this build`

After genuine full exhaustion:

> All 17,553 dictionary keys were tried as Key A and Key B; none matched. This card cannot be decoded by dictionary search. It requires a key-recovery attack.

Surface Recover Keys contextually in that result. A permanently disabled toolbar button with only a “soon” tooltip ([RootView.swift:183](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:183)) is not a usable next step. Until implemented, clicking it should at least explain the requirement and supported external workflow.

One critical qualification: the nested implementation presently requires a known working block and key ([x7crypto.py:192](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7crypto.py:192), [x7d.py:311](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:311)). Therefore:

- If some sectors decoded, nested can potentially target the remaining sectors.
- If zero sectors decoded, the repo’s current nested path cannot start. That case requires the separate reader/MFKey32 capture workflow, another attack, or an imported known key.

Do not promise that the existing nested button can recover a card with zero known keys.

## 6. Additional things currently making it “dumb”

Most important: a zero-key or partial decode currently becomes a normal document. The daemon returns `recovered`, but `AppModel.decode()` does not inspect it before creating the source ([AppModel.swift:196](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:196)). Once `source` exists, Write becomes enabled ([RootView.swift:177](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift:177)). Missing blocks are also serialized as zeros in an `.mfd` image ([CardDump.swift:34](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/CardDump.swift:34)).

That is dangerous and misleading. Use three outcomes:

- **Complete:** all required sectors read; normal clone/save enabled.
- **Partial:** `X/16 sectors decoded`; save as partial if desired, but clone requires a strong warning or stays disabled.
- **No keys:** no dump document; show the recovery/extended-scan result screen.

Finally, record local success statistics. For a hotel supplier doing this daily, the best dictionary is adaptive: successful site/vendor keys should automatically rise above generic public and capture keys on that machine. That will outperform any permanently hard-coded global ordering.
### Verify (post-impl, found 1 High regression -> fixed)
## Verdict

The global budget and uncapped reuse are implemented correctly. The hotel fast path is genuinely fast. Safety guards remain intact.

However, there is one serious deterministic regression: if the first unresolved sector consumes the budget, later sectors never receive even a cheap/common-key dictionary probe. A card with sector 0 proprietary and sectors 1–15 using `ffffffffffff` now recovers 0/16. The previous suite explicitly tested and recovered 15/16 for that case; that test was removed.

## Findings

1. **High — real regression: one difficult sector starves every later sector.**  
   The first unresolved sector can consume the shared budget at [x7lib.py:371](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:371). Once expired, later sectors only try `found_keys`; if sector 0 found nothing, that list is empty and they receive no authentication attempts at all ([x7lib.py:369](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:369)).  
   This is deterministic, not theoretical. It sacrifices recoverable later sectors to achieve the global cap. The old “sector 0 unknown, 15 sectors common” regression test was deleted. The replacement tests do not cover this topology ([test_all.py:155](/Users/tuan/Claude/Tenor/tenor-rekey/probe/test_all.py:155)).

2. **Medium — real UI bug: progress can jump backward from nearly 100% to 0%.**  
   During an unknown-card walk, `fraction` is `attempts / budget` ([Models.swift:143](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:143)). The sector-completion event then clears attempts and budget ([AppModel.swift:257](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:257)), changing the fraction to `sector / total`; for sector 0 that is zero ([Models.swift:145](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:145)).  
   Thus an unknown card can visibly go from approximately `3490/3500` back to `0/16`, then race through the remaining sector events.

3. **Medium — real test-coverage weakness.**  
   Most new dump/reuse tests use `FakeCard.find_key`, which ignores `_Budget` entirely ([test_all.py:71](/Users/tuan/Claude/Tenor/tenor-rekey/probe/test_all.py:71)). Only the dedicated `Walk` test invokes the real implementation ([test_all.py:181](/Users/tuan/Claude/Tenor/tenor-rekey/probe/test_all.py:181)).  
   The 59 tests pass, but they do not establish heterogeneous-sector behavior or reuse after a genuinely exhausted real budget.

4. **Low — theoretical/hardware-dependent deadline behavior.**  
   The 90-second deadline starts before reading sectors ([x7lib.py:309](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:309)), and `expired()` measures total elapsed wall time ([x7lib.py:39](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:39)). Unbudgeted reuse and block reads therefore consume the dictionary deadline indirectly. On intermittent hardware, slow reads of an early recovered sector could prevent a later dictionary search despite very few budgeted attempts. It can also return `exhausted=true` after a fully recovered but slow decode ([x7lib.py:385](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:385)).

## A. Budget correctness

Confirmed.

- Exactly one `_Budget` is created per `dump()` at [x7lib.py:309](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:309).
- That same object is passed to every dictionary walk at [x7lib.py:373](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:373).
- Each budgeted auth checks expiration, then increments once immediately before auth ([x7lib.py:256](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:256)).
- It cannot exceed 3,500 attempts: after attempt 3,500, the next check stops. The 90-second threshold is checked between attempts, so one in-flight attempt may overrun it slightly.
- After sector 0 exhausts an all-unknown card, sectors 1–15 perform only the outer poll/UID guard and completion event. They do not recreate or bypass the dictionary budget.

Total dictionary work is one budget, not 16 budgets. The additional 15 polls mean “near” rather than a hard 90-second whole-function ceiling.

## B. Reuse never capped

Confirmed.

Reuse explicitly calls:

`find_key(..., budget=None)` at [x7lib.py:370](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:370).

A uniform card therefore continues resolving later sectors after the dictionary budget expires.

Promotion/deduplication is correct: an existing key is removed, otherwise inserted into the set, then placed at index zero ([x7lib.py:314](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:314)). No duplicate values accumulate. Strictly speaking, the same key is moved to the front after every successful sector—not “promoted exactly once”—but the invariant is sound.

## C. Hotel fast path

Confirmed, with a line-number clarification.

BETECH is the 10th key but physical file line 15 because of five comment lines; TESA is the 11th key/file line 16 ([mfc_keys.dic:15](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/mfc_keys.dic:15)). They were physical lines 13056/13057 previously.

Both are inside `FAST_HEAD = 64` ([x7lib.py:101](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:101)):

- BETECH Key A: attempt 19.
- BETECH Key B-only: attempt 20.
- Remaining 15 sectors: one reuse auth each for A, or two each for B-only.
- Total key-discovery/reuse auths: approximately 34 for A or 50 for B-only.
- Including the minimum one successful re-auth per block on a 1K card: approximately 98 or 114 total auth calls.

Only 19–20 of 3,500 budgeted attempts are spent—about 0.6%. Based on the measured ~26 ms probe cycle, discovery should be sub-second and the whole read seconds, not minutes. Actual total read time still needs hardware confirmation.

The capture tail ordering is also real: builder sequencing is defaults → hotel → frequency → curated → capture at [build_dict.py:122](/Users/tuan/Claude/Tenor/tenor-rekey/probe/dict/build_dict.py:122).

## D. Safety preserved

Confirmed.

- Outer per-sector UID guard: [x7lib.py:364](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:364).
- UID guard inside every block-read retry: [x7lib.py:327](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:327).
- Same-key, opposite-key-type read fallback: [x7lib.py:319](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:319).
- Trailer recovered-slot patching and zero other-slot mirroring: [x7lib.py:339](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:339).
- `write_mfd` initial target pin and mid-write UID check remain at [x7d.py:161](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:161) and [x7d.py:238](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:238).
- Format target pin and mid-format guard remain at [x7d.py:262](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:262) and [x7d.py:292](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:292).

## E. Honest failure

Confirmed.

`recovered == 0` clears sectors/pages/source and sets `noKeysFound` ([AppModel.swift:206](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:206)). With `source == nil`, `cloneSource` is nil and Write cannot clone it.

A partial result still builds a document at [AppModel.swift:215](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:215).

Important nuance: a direct partial clone does **not** write zeros for unread blocks. `CardDump.from` omits nil blocks ([CardDump.swift:21](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/CardDump.swift:21)), so the daemon receives only recovered blocks. However, saving a partial dump materializes missing blocks as zeros ([CardDump.swift:36](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/CardDump.swift:36)); reopening that file loses the distinction and can later clone those zeros. That residual risk predates this diff.

`noKeysFound` resets on:

- A new decode: [AppModel.swift:184](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:184).
- Card change/removal through `clearCardBound`: [AppModel.swift:125](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:125), [AppModel.swift:145](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:145).
- Explicit document clearing: [AppModel.swift:153](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:153).

## F. Progress/events

Schema migration is consistent:

- Produced by daemon: [x7d.py:129](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py:129).
- Decoded in `EngineEvent`: [Models.swift:100](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:100).
- Applied in AppModel: [AppModel.swift:252](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift:252).
- Used by `DecodeProgress.fraction`: [Models.swift:133](/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/Models.swift:133).

Repository-wide search found no remaining `keys_tried`, `keys_total`, `keysTried`, or `keysTotal` references.

Walk progress emits immediately on the first attempt and then approximately every 0.25 seconds ([x7lib.py:45](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:45), [x7lib.py:260](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:260)). Reuse itself emits no attempt events, but every completed sector emits a boundary event ([x7lib.py:379](/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py:379)), so fast cards still show sector movement.

The backward-progress defect above is the exception.

## G. Hardware tests that matter

At minimum:

- Sector 0 proprietary/unknown, sectors 1–15 common: this should expose the starvation regression immediately.
- Uniform BETECH Key A and a Key-B-only variant: measure discovery, full dump time, and actual auth reliability.
- Mixed keys across several sectors, including a new key after an unknown sector.
- Intermittent coupling during reads to test whether read time burns the 90-second deadline.
- KeyA-authenticated sector with data readable only through KeyB.
- Card swap during dictionary walk and during block reads.
- Partial decode → direct clone versus partial decode → save/reopen → clone.

`probe/test_all.py` does pass all 59 tests, and `git diff --check` is clean. That does not clear the sector-starvation regression.