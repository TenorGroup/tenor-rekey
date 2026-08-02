# X7_AUDIT_FINDINGS.md - confirmed bugs + UX friction on the X7 side

Source: adversarial find+verify workflow 2026-07-20. Owner: 'using X7 on rekey is torture'. Every item below passed an independent adversarial verify (CONFIRMED or NEEDS_HARDWARE). Fix targets: probe/x7.py, probe/x7lib.py, probe/x7d.py, probe/x7hid.py, app/Sources/*.

Confirmed: 21 | Rejected by verify: 0

---

## 1. [HIGH] Card seated but undetected is a dead-end: status monitor polls with tries=8, but decode needs tries=25, and every read verb is gated on model.card != nil

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift`:125 | lens: ux-friction | confidence: likely | verdict: CONFIRMED (lower)

**Failure/pain:** refreshStatus() calls engine.poll(tries: 8) every 1.5s. The codebase itself documents that coupling is 'intermittent on first contact' and the actual decode/write/format paths poll with tries=25 plus per-block retries. So a real hotel card physically sitting on the X7 can fail to couple within the snappy 8-try status poll while it WOULD couple under a 25-try op. When that happens model.card stays nil, CanvasView shows EmptyState 'waiting for card', and the decode/read button is disabled (RootView.swift:173 `enabled: model.card != nil`). The user is looking at a card on the reader with no button to press and no way to force a read - they can only re-seat and wiggle the card and hope the next 1.5s tick couples. This is the single most repeated daily friction: the app refuses to even attempt the operation that would have worked.

**Fix:** Decouple the action gate from the flaky status poll. Either (a) enable the decode/read verb whenever readerOnline (not only when a card is currently detected) and let the decode path's own tries=25 + wait_for_card do the coupling, or (b) add an explicit 'read anyway / retry' affordance in EmptyState that calls a full-retry poll, or (c) raise the monitor's tries and/or add a short exponential re-poll when readerOnline && card==nil. The op already tolerates a missing card gracefully (daemon returns present:false), so gating the button hard on the snappy poll is the wrong tradeoff.

**Verify note:** Fix is sound; prefer option (a)+(b): enable the decode/read verb whenever readerOnline (let the op's own tries=25 + wait_for_card do the coupling), and add a "read anyway" affordance in EmptyState. Suggest lowering high->medium: the continuous 1.5s re-poll means the card gets repeated 8-try attempts, so a true dead-end requires coupling to fail across many consecutive polls while succeeding in one 25-try op - plausible per the "intermittent on first contact" note but not the guaranteed everyday failure the scenario implies. Cheapest partial mitigation if not decoupling the gate: raise the monitor's tries or add a short exponential re-poll when readerOnline && card==nil.

---

## 2. [HIGH] Decoding a card destroys the unsaved held document up front, with no guard, no undo, and no save prompt - and the on-screen hint invites exactly this

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift`:199 | lens: ux-friction | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** The clone workflow is: decode source (source held as the document), lift it, place a blank/target, press Write. But decode() nulls the working document at the very start (lines 199-202 set source=nil, sectors=[], before any hardware call). Meanwhile ReaderHint (RootView.swift:363-364) literally tells the user, when a different card is on the reader, 'the card on the reader differs from the document - decode to read it, or write to copy the document onto it'. If the user follows that hint (or hits Cmd+R, which is bound and always available) with the blank target seated, decode wipes the still-unsaved source document; the blank yields recovered==0, so noKeysFound is set and source stays nil (lines 221-229). The only copy of the decoded image is now gone. There is no unsaved-changes confirmation anywhere.

**Fix:** Do not discard the document until the decode actually produces a new one. Move the source=nil/sectors=[] reset to run only on success (right before assigning the new dump), or guard decode() with a confirm when an unsaved document whose uid differs from the reader card is held. At minimum, don't reset before the hardware read succeeds so a no-keys or card-changed outcome leaves the prior document intact.

**Verify note:** Move the source=nil/sectors=[] reset out of the up-front block (AppModel.swift:200) so it runs only in the success branch right before source=dump (:238). The card-changed (:220) and no-keys (:227) branches already null source explicitly, so they still clean up on genuine failure; only the pre-read wipe should go. Alternatively/additionally, guard decode() with a confirm when an unsaved document whose normalized UID differs from the reader card is held - mirroring the confirm already gating the write/clone path (:434).

---

## 3. [HIGH] Any engine call during a long decode kills the daemon and aborts the decode

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Engine/X7Engine.swift`:169 | lens: swift-shell | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** The daemon (probe/x7d.py run() line 387) is single-threaded: while a decode is walking the dictionary it does NOT read stdin, so any other request queues unread in the pipe. Every request except decode/write/format arms the DEFAULT 30s timeout (request(), line 172/178). AppModel makes several such calls that are NOT gated by the decoding/cloning/formatting flags: refreshLearnedCount() (AppModel:96, fired automatically by DictionarySettings '.task', SettingsView.swift:87), clearLearnedKeys() (AppModel:101), connect()/info() from the Reconnect button (AppModel:80, SettingsView.swift:35), and sendAPDU() (AppModel:476). Concrete case: user starts a decode on a card whose key isn't in the dictionary (the walk takes >30s), then opens Settings > Dictionaries. The auto-fired learned_stats request queues behind the decode; 30s later timeoutRequest() (X7Engine:166) fires and calls process?.terminate() (line 169), killing the shared daemon. died() (X7Engine:103) resumes the decode's continuation with 'daemon exited', so decode() throws, the grid is wiped, and lastError shows a cryptic 'daemon exited'/'daemon timed out'. The user's minutes-long decode dies because they touched Settings.

**Fix:** Serialize UI-initiated engine calls behind the in-flight operation instead of letting them share the process and race the 30s deadline: either queue non-op requests until the current streaming op finishes, or have timeoutRequest() only terminate for the op that actually owns the reader (not for a queued status/APDU request), or gate refreshLearnedCount/clearLearnedKeys/connect/sendAPDU on !(decoding||cloning||formatting||apduBusy) the same way monitor() is. The cleanest fix is a serial gate in X7Engine so a status/APDU request waits for the streaming op rather than timing out against it.

**Verify note:** Proposed fix is sound. Cleanest is a serial gate inside X7Engine so a status/APDU/info request issued while a streaming op (decode/clone/format) holds the reader waits for that op to finish rather than arming its own 30s deadline against the shared process. Alternatively, timeoutRequest() must not terminate the process for a request that isn't the current reader-owning streaming op - only fail that one continuation. The AppModel-side gating (guard on !(decoding||cloning||formatting||apduBusy), mirroring monitor()) is a weaker patch: it plugs the four known callers but leaves the underlying shared-process-vs-30s-deadline race for any future ungated call, so prefer the engine-level serial gate.

---

## 4. [HIGH] Unknown-card path is a hard dead-end while the one recovery verb ('recover keys') sits permanently disabled with a 'soon' tooltip

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift`:186 | lens: ux-friction | confidence: certain | verdict: CONFIRMED (lower)

**Failure/pain:** When a card's keys aren't in the dictionary, decode returns recovered==0 and the app shows NoKeysState telling the user to add the key in Settings and decode again (RootView.swift:442-459). But that only helps users who already possess the key. The 'recover keys' action - the entire point for a card you DON'T have the key to - is hardcoded `enabled: false` with help 'soon'. So for the exact scenario where a user most needs the tool (a hotel card not in the dictionary), the app offers a greyed-out button that dangles a promise and a Settings instruction that presupposes you already have the answer. The primary verb visibly present but inert reads as broken software on every unknown card.

**Fix:** Either hide the 'recover keys' verb entirely until nested/darkside is wired (a disabled primary verb is worse than an absent one), or wire it to the daemon's existing nested_recover for cards that have at least one dictionary key, and make NoKeysState's copy honest about what is and isn't possible rather than implying every unknown card is user-solvable.

**Verify note:** Prefer the "hide the disabled verb until it is wired, and make NoKeysState copy honest about what is/isn't user-solvable" half of the fix. Do NOT rely on the nested_recover half: nested recovery needs at least one already-known key, but this state is reached only when recovered==0, so it cannot help the fully-unknown hotel-card case (that needs an unimplemented darkside/no-key attack). Also note the verb is inert on the Swift side too - X7Engine has no binding for the daemon's nested_recover - so wiring it means adding a Swift engine method, not just flipping enabled to true. Severity is closer to medium than high: it is an honestly-labeled unfinished feature (disabled + "soon"), not deceptive broken software.

---

## 5. [HIGH] Request/response stream has no seq validation - a single late/dropped frame desyncs every later command

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7.py`:52 | lens: daemon-driver | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** X7.transceive() writes a command, then reads up to `reads` times and RETURNS THE FIRST non-empty 64-byte frame - it never checks that the frame's seq echoes the seq it just sent (decode() even extracts `seq` but no caller compares it), and it never flushes the input queue before writing. The firmware sometimes answers slower than the read window: _select() uses reads=2/to=150 (300ms window) and auth() uses reads=4/to=150 (600ms window), and the code's own _select comment admits 'our auths needed slow retries.' When a _select/auth response arrives AFTER that window, transceive gives up, but the frame is still queued in the HID buffer. The NEXT command reads that stale frame as its own answer, and since the reader emits exactly one frame per command, the stream stays permanently one frame behind - self-perpetuating. Concretely inside find_key: a _select response (d5 4f 00) gets consumed as an auth response; _pt anchors on its 0xD5, sees r[1]=0x4f != 0x41, so auth() returns False and a key the card actually has is silently rejected. Result: decode intermittently fails to recover keys it should, the user re-taps, 'sometimes it works, sometimes it doesn't' - the classic X7 torture.

**Fix:** In transceive/cmd, tag each request with its seq and discard any read whose decoded seq != (sent_seq|1), looping until the matching frame or a real timeout; and flush pending input (drain reads until empty) before writing a new command so a stale late frame from a timed-out prior op can never be mis-attributed. At minimum, have _pt reject a frame whose PN532 response byte doesn't match the command it sent.

**Verify note:** Primary fix is correct: drain the HID input queue before each write AND loop reads until the decoded seq matches (sent_seq|1) before accepting a frame - both are needed to resync. Note the 'at minimum' variant (only reject a frame whose PN532 response byte mismatches the command) detects the mis-attribution but does NOT resync the stream: it stays one frame behind, so it converts a silent wrong-answer into a persistent detected-miss rather than actually fixing the desync. The drain+seq-match is the real remedy.

---

## 6. [HIGH] Tapping an NTAG/Ultralight into 'decode' grinds the full 90s dictionary and returns nothing

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py`:131 | lens: daemon-driver | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** decode()/X7Card.dump() never consult card_kind(); they blindly run the MIFARE Classic auth walk regardless of card type. sector_count() returns 16 for any SAK != 0x18, so an NTAG21x/Ultralight (SAK 0x00) is treated as a 16-sector 1K card. Every crypto1 auth against an NTAG fails, so PASS A finds nothing and PASS B walks the entire ~17.5k-key BUILTIN_KEYS dictionary to exhaustion or until the DEFAULT_SCAN_SECONDS=90 watchdog fires. The daemon already has card_kind() and a dedicated read_ntag() path, but the decode button routes to dump() with no guard. User taps a hotel NTAG keycard, hits Decode, waits 90 seconds staring at a progress bar, and gets an all-empty dump with recovered=0.

**Fix:** In decode() (or at the top of dump()) call card_kind(sak, atqa); if it is 'ntag', either return a typed error telling the UI to use read_ntag, or auto-route to read_ntag() - never enter the Classic dictionary walk for a non-Classic card.

**Verify note:** Guard at the top of decode() rather than inside dump(): after the initial poll/wait_for_card, compute card_kind(sak, atqa) and if 'ntag', auto-route to read_ntag() (or return a typed error telling the UI to). Note dump()/decode() re-poll internally via wait_for_card(), which returns sak/atqa but not kind, so recompute card_kind from those. Guarding inside dump() also works but couples the engine to a routing decision the daemon layer already owns; decode() is the better seam.

---

## 7. [HIGH] Reader wedge is only recovered on an OSError; an empty-read wedge makes the daemon report the reader healthy forever

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py`:378 | lens: daemon-driver | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** _drop() (dead-handle reset) fires only inside `except OSError` in poll() and handle(). But the HID layer only raises OSError when hid_read/hid_write returns a negative error code. If the reader firmware wedges or the USB stack half-drops the device such that hid_read_timeout returns 0 (empty, no error) instead of -1, transceive() returns [] with no exception, _pt() returns b'', and poll() returns None. The daemon then answers {'present': False, 'reader': True} - it reports the reader as connected and healthy while every operation silently no-ops. No _drop() ever runs, so the dead handle is reused indefinitely; the UI shows 'reader connected, tap a card', the user taps repeatedly, and nothing ever happens. The only escape is unplug/replug or killing the daemon. The code comment claims a failing hardware op means unplug and 'drop the dead handle so a replug re-opens cleanly,' but that recovery is unreachable when the failure surfaces as empty reads rather than an exception.

**Fix:** Detect a wedge on the empty-read path too: if a command that must answer (e.g. InListPassiveTarget) returns zero frames N times in a row, treat it as a dead handle and _drop()/re-open, or probe the reader with info() and drop on failure. Consider surfacing a distinct 'reader unresponsive' state instead of 'reader: True'.

**Verify note:** Recovery is gated purely on OSError, which negative-code hardware errors raise but empty-read (n==0) wedges do not. Add wedge detection on the empty-read path: track consecutive zero-frame responses from a command that must answer (e.g. InListPassiveTarget in poll) and, past a threshold, _drop() + re-open; or periodically probe via info() and _drop() on failure. Note wait_for_card legitimately returns empty for 'no card present', so the wedge heuristic must key off a command that is expected to always yield a frame, not off card absence. Consider surfacing a 'reader unresponsive' state distinct from reader:True/reader:False so the UI can prompt a replug instead of showing 'tap a card' indefinitely.

---

## 8. [HIGH] Reopening a saved dump zero-fills unread blocks; cloning it then silently overwrites real data on the target

- File: `app/Sources/Engine/CardDump.swift`:90 | lens: safety-regression | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** Decode a card where some blocks were not read (a sector whose key was not found, or a KeyB-only block that failed the read retry): the daemon returns those blocks as null, and CardDump.from() omits them, so a DIRECT decode->clone correctly skips them (write_mfd's `if not v: continue`). But Save writes mfdData(), which starts from `Data(count: size)` (all zeros, line 39) and only overwrites present blocks - the unread blocks become 00..00 in the .mfd. On File>Open, load() reads ALL blocks 0..(count/16) from the file (line 90-91), so those zero blocks are now indistinguishable from genuine zero data. Cloning that reopened document sends '00000000000000000000000000000000' for those blocks (a truthy hex string, so write_mfd does NOT skip it) and overwrites the target's real data with zeros. access_bits_valid protects trailers from this (an all-zero trailer fails the check and is skipped), but DATA blocks have no integrity check, so the loss is silent. The handoff notes this as a known 'partial-clone residual' but it was never fixed.

**Fix:** Distinguish 'unknown' from 'zero' across the save/open boundary: persist which blocks were actually recovered (e.g. omit unread blocks from the mfd via a companion presence bitmap, or record recovered block indices in the .keys.json sidecar) and have CardDump.load() populate blocks[b] only for blocks that were genuinely captured, leaving the rest absent so write_mfd skips them exactly like the direct-clone path does.

**Verify note:** Fix is sound. Concretely: persist recovered-block presence in the sidecar (the .mfd binary format is fixed for x7tool/nfcPro interop, so don't try to omit blocks there). Record which block indices were genuinely captured (e.g. a "present":[...] array or per-sector bitmap in .keys.json), and have CardDump.load() populate blocks[b] ONLY for those indices, leaving unread blocks absent so blockParams omits them and write_mfd's `if not v: continue` skips them exactly like the direct-clone path. Backward-compat: a legacy .mfd with no presence sidecar has no way to distinguish unknown from zero, so treat missing presence metadata conservatively (either assume all-zero blocks are unknown for older files, or surface a warning that reopened dumps may carry zero-filled unread blocks).

---

## 9. [MEDIUM] NTAG 'read' silently does nothing when the card blips or is lifted mid-read

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift`:205 | lens: swift-shell | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** In decode(), the NTAG branch (lines 204-208) calls engine.readNTAG(), then unconditionally does `pages = buildPages(r)` with no check of r.present and no card-change guard - unlike the Classic branch which checks r.recovered and compares startUID (lines 218-221). If the NTAG card couples poorly and the daemon returns present=false (pages=nil), buildPages returns [] (AppModel:300), so pages stays empty, source stays nil, and the canvas falls back to PreDecode: the 'read' button appears to do absolutely nothing, with no error and no feedback. On a flaky reader (the reported pain point) the user taps Read repeatedly and nothing happens. Additionally, swapping NTAG-A for NTAG-B during the read window silently shows B's pages with no indication the card changed.

**Fix:** Mirror the Classic branch: check r.present and surface lastError when the read returned no card/pages, and guard against a uid change during the read (compare startUID to r.uid) before committing pages so a lifted/swapped card produces an honest error rather than a silent no-op.

**Verify note:** Fix as proposed: mirror the write/format branches and the Classic decode branch. In the NTAG branch check r.present (set lastError "no card on reader" and clear when false), and guard against a UID change by comparing startUID to r.uid before committing pages. Note the empty-result and card-absent cases are distinguishable via r.present (buildPages returning [] alone cannot tell "no card" from "card with zero pages"), so key the error on r.present, not on pages.isEmpty.

---

## 10. [MEDIUM] Unknown-card decode grinds up to 90 seconds with a counter that has no denominator and no time bar, even though a fixed 90s deadline is known

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Shell/RootView.swift`:434 | lens: ux-friction | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** For a card whose key is deep or absent, dump() runs to the DEFAULT_SCAN_SECONDS=90 wall-clock watchdog (x7lib.py:25). During that up-to-90s wait the only feedback is decodeStatusLine emitting 'trying keys N' - a bare, ever-increasing integer with intentionally no denominator (the comment explains the sector*dict total would look like it quit at a few percent). The net effect for the user is a spinner and a number climbing for a minute and a half with zero sense of whether it is 5 seconds or 85 seconds from finishing. It feels frozen and is a core part of why daily use is painful.

**Fix:** There IS a concrete, honest denominator available: the 90s deadline. Show elapsed/remaining time (or a determinate bar driven by budget.elapsed()/max_seconds, which the daemon already tracks) so the user sees bounded, forward-moving progress. Optionally surface 'sectors resolved: k/N' alongside the auth counter, since that IS a meaningful fraction that only grows.

**Verify note:** Fix is sound and cheap: the daemon already tracks budget.elapsed() and knows max_seconds (=90), and walk_total already flows to the app (Models.swift:111,137). Two concrete moves: (a) add elapsed/max_seconds to the x7d.py:157-159 progress event and drive a determinate time bar (or show elapsed/remaining seconds) since the deadline is fixed and forward-moving; (b) wire the already-computed but currently unused DecodeProgress.fraction / 'sectors resolved k/N' as an honest, monotonic secondary readout. Note the counter's missing denominator is deliberate design (dict total looks like ~few percent), so the correct denominator to expose is the 90s budget, not sector×dict. Severity medium is fair but scope it to the unknown/first-decode card path, not every decode.

---

## 11. [MEDIUM] APDU send is not disabled while a decode/clone/format is running

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/ApduConsole.swift`:76 | lens: swift-shell | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** The APDU send button is disabled only on `model.card == nil || input empty || model.apduBusy` (ApduConsole:76) and sendAPDU()'s guard is only `!clean.isEmpty, !apduBusy` (AppModel:478) - neither checks decoding/cloning/formatting. During an in-flight decode the APDU input stays live; the user types a command and presses Return. engine.apdu() (default 30s timeout) queues behind the busy decode in the single-threaded daemon, times out after 30s, terminates the daemon (see finding 1), and the decode dies. Even the successful path is misleading: the APDU appears to hang for 30s then logs 'apdu_error'. The Decode action button itself is gated on `busy` but `busy` excludes apduBusy (RootView ActionBar:168), so the reverse is also possible: starting a decode while an APDU is pending.

**Fix:** Include the operation-busy state in the APDU send disable condition and in sendAPDU()'s guard (e.g. also disable when model.decoding || model.cloning || model.formatting), and add apduBusy to ActionBar's `busy` so the two paths cannot interleave on the shared daemon.

**Verify note:** Fix is valid. Gate both the ApduConsole button disable (line 76) and the sendAPDU guard (AppModel:478) on `model.decoding || model.cloning || model.formatting` in addition to apduBusy, and add `model.apduBusy` to ActionBar's `busy` (RootView:168). Cleaner still: serialize at the X7Engine actor by adding a single `busy`/operation guard so no second request can be written to stdin while any request is in flight, since the daemon is strictly sequential and a queued request's shorter timeout will kill the shared daemon.

---

## 12. [MEDIUM] Batch duplication forces reopening the Write sheet for every blank; the sheet also dismisses before the write result is visible in-context

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift`:51 | lens: ux-friction | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** The real business use (hotel key supplier duplicating one master onto many blanks) means writing the same source to N cards in a row. But CloneSheet calls dismiss() immediately after kicking off each clone (lines 51 and 63-64), so the sheet closes on every single write. To do 10 blanks the user must: place blank, open Write sheet, wait for the target to couple, confirm the irreversible-write dialog, watch it close, place next blank, reopen sheet, again... ten times. dismiss() also fires synchronously while clone() is still awaited, so the source->target confirmation panel is gone before the per-block result even lands (results only appear as glyphs on the main grid), removing the write's own context at the moment it matters.

**Fix:** Add a 'write another' / keep-open batch flow: after a successful clone, keep the sheet up (or show an inline success + 'place next blank' state) so the user can seat the next card and write again without re-navigating and re-confirming. Keep the sheet open until the clone finishes so its result shows in the source->target context, then offer continue vs done.

**Verify note:** Fix direction is correct. Note that a full clone always routes through the confirmationDialog (trailers/uid default true), so a keep-open batch flow should avoid forcing re-confirmation of the same source→target on each blank in a batch, or the re-confirm friction persists. Keep the sheet up until clone() completes so per-block glyphs can render in-context (the sheet would need to start reading cloneResults/cloning, which it currently ignores), then present continue-vs-done.

---

## 13. [MEDIUM] Inside the Write sheet the target slot silently sits on 'waiting for card' with the Write button disabled and no retry or polling feedback

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/Views/CloneSheet.swift`:55 | lens: ux-friction | confidence: likely | verdict: CONFIRMED (lower)

**Failure/pain:** The Write button is `.disabled(model.cloneSource == nil || model.card == nil)`. The target card's presence is driven by the same 1.5s status monitor with the flaky tries=8 poll (see finding 1). If the blank doesn't couple on those snappy polls, the sheet's target slot just reads 'waiting for card' and the Write button stays greyed, with no spinner indicating it is polling and no manual retry. The user is stuck in a modal staring at a disabled primary button with a card physically on the reader, and the only escape is Cancel and re-seat.

**Fix:** Show that the sheet is actively polling for the target (a subtle spinner/'looking for card' state), and provide a manual retry that runs a full-retry poll. Better, let Write attempt the operation even if the snappy poll hasn't reflected the card yet - the daemon's write_mfd does its own wait_for_card and target_uid pinning, so a disabled button here is an over-restriction.

**Verify note:** Right fix is fix #1 only: add a subtle 'looking for card' spinner in the target slot while card==nil so the user sees polling is active. Reject fix #3 (writing without card reflected) - it forfeits the target_uid pinning that prevents cloning onto a card whose uid was never shown. Fix #2 (manual retry) is largely redundant because monitor() already re-polls every 1.5s behind the sheet; a full-retry poll (tries=25) button is at most a nicety for flaky hardware. Net: this is a low-severity missing-affordance, not a medium stuck-state, given the continuous auto-poll.

---

## 14. [MEDIUM] No cancel path: a stuck decode/write/format can only be aborted by killing the daemon

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py`:387 | lens: daemon-driver | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** run() reads one JSON line, executes the entire operation synchronously, then emits one response; there is no cancel/abort method in METHODS and the code comment states 'The user cancel path (daemon kill) is separate.' A decode that hits the 90s watchdog, or a write/format that stalls on repeated 5.6s no-response poll windows (each _pt read loop is reads×timeout, up to 8×700ms), blocks the single stdin loop. During that time the UI cannot cancel, cannot poll status, cannot do anything except kill the daemon process - which also throws away any partial dump already collected. Any wedge or long grind therefore presents to the user as a frozen app with kill -9 as the only recourse.

**Fix:** Make long ops interruptible: run the engine on a worker thread and honor a 'cancel' request (or a cancel flag checked in the dump/find_key/read_sector loops and the _Budget), so the UI can abort and still receive the partial result gathered so far.

**Verify note:** The proposed fix is sound and the partial-preservation machinery already exists: dump() already returns whatever was recovered when _Budget.expired() fires (x7lib.py:24,34-49). Wire a cancel flag into that same early-exit path (an is-cancelled check alongside expired() in the walk, and in the write/format block loops) and run the engine on a worker thread so run() can keep reading stdin and accept a new "cancel" method. That lets the UI abort and still receive the partial dump gathered so far, matching the existing watchdog return shape. Note the write/format loops swallow HID read timeouts (no OSError), so a silent reader will not self-abort - the cancel flag is the intended escape there too.

---

## 15. [MEDIUM] write_mfd abandons a good key after a single transient auth miss

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7d.py`:277 | lens: daemon-driver | confidence: likely | verdict: CONFIRMED (lower)

**Failure/pain:** In the per-block write loop, `if not c.auth(trailer_block(s), kk, kt): break` breaks out of the `for _ in range(3)` retry and moves to the NEXT candidate key. The range(3) retry therefore only shields against poll() failures, not against a flaky auth - a single transient auth miss (common on this reader given intermittent coupling and the aggressive 700ms/FAST_TO windows) permanently discards that key for this block. If the source key was correct but auth blipped once, the code falls through to FF, which on a non-magic target also fails, and the block is recorded as failed. On a clone/write the user then sees blocks 'fail' that should have written, forcing a full re-run. Compounded across a 40-sector 4K write this makes writes flaky and slow.

**Fix:** Retry auth within the inner loop (don't break to the next candidate on the first auth failure) - e.g. re-poll and retry the same key a couple of times before giving up on it, mirroring the read path's 6-try re-auth in read_sector.

**Verify note:** The narrow, correct fix: on auth failure, re-poll and retry the SAME candidate a couple of times before advancing (make auth as retry-worthy as poll/write already are), rather than only relying on the (k1,A)->(k1,B) fallthrough. Do NOT justify it as "mirroring read_sector" - read_sector breaks on auth failure exactly like write_mfd; if this retry is worth adding to write, the same gap exists in read_sector and both should change together. Impact is limited to strict single-keytype targets; uniform KeyA==KeyB cards are already effectively retried via the A-then-B candidates.

---

## 16. [MEDIUM] Wall-clock watchdog does not cover PASS A, so decode overruns max_seconds and reports misleading attempts/exhausted

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py`:388 | lens: daemon-driver | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** In dump() PASS A both find_key() calls pass budget=None, and find_key only ticks/checks the _Budget when budget is not None (attempt() line ~266). PASS A retries the growing found_keys list plus the 24-key hot set on EVERY sector. On a diverse or 4K card (up to 40 sectors, keys differing per sector) found_keys grows toward 40, so PASS A does roughly 40×(up-to-40 + 24)×2 auths ≈ several thousand auths, none counted against the deadline and none cancellable, before PASS B even starts. The advertised behavior ('an UNKNOWN card fails fast... in ~90s') is violated - total wall time can be 2-3 minutes. Worse, the returned `attempts` (budget.attempts) and `exhausted` (budget.expired()) only reflect PASS B, so the UI can show a small attempt count and exhausted=False for a decode that actually ran far past max_seconds.

**Fix:** Thread the budget through PASS A as well (tick per auth, honor expired()), or add a separate hard wall-clock cap that covers the whole dump() including PASS A and read_sector; and count PASS A auths so `attempts`/`exhausted` reflect the real work done.

**Verify note:** Fix is sound. Prefer a single hard wall-clock cap covering the entire dump() (PASS A + read_sector + PASS B) plus counting every auth into budget.attempts, so both the fail-fast guarantee and the reported attempts/exhausted become honest. Note the reuse sweeps are intentionally 'always worth trying' per the comment, so if budget-gating PASS A, gate only the hot-set sweep (not the found_keys reuse) to preserve that design intent while still bounding the expensive part.

---

## 17. [MEDIUM] target_uid pin is skipped when the card goes absent while the clone/format confirm dialog is open, allowing a write onto a card whose UID was never shown

- File: `app/Sources/AppModel.swift`:407 | lens: safety-regression | confidence: likely | verdict: CONFIRMED (keep)

**Failure/pain:** clone() captures `let target = card?.uid` at invocation and passes it as target_uid; write_mfd only enforces the pin when `expected` is non-empty (x7d.py line 205-206: `if expected and ...`). The 'write to card' button that sets confirm=true is gated on model.card != nil, but the confirmationDialog's destructive button is NOT re-gated. The monitor loop keeps polling while the dialog is open (it only pauses during cloning/formatting/decoding), and after the card is absent for 2 cycles (~3s) it sets card=nil (clearCardBound). If the user lifts the card while the confirm dialog is up and then confirms, target = card?.uid = nil, so the daemon's UID check is bypassed and write_mfd/format writes onto whatever card is placed on the reader next - a card whose UID was never displayed to the user. The daemon's internal `c.uid != target` check still prevents a mid-operation swap, so the blast radius is 'the card placed after removal', but the UI-shown-UID guarantee the pin was added for is lost. format() (line 441) has the same nil-capture, partially mitigated by canFormat requiring a matching source UID.

**Fix:** Refuse the write when target UID is unknown: make clone()/format() bail (or the daemon reject) if target_uid is nil/empty, and/or dismiss the confirm dialog when `card` becomes nil so a stale dialog cannot fire a write with no pin.

**Verify note:** Proposed fix is sound. Preferred: make clone() bail when `target == nil` (mirror what canFormat already does for format), i.e. `guard let target = card?.uid else { lastError = "no target card"; return }`, and/or have the daemon reject an empty target_uid outright (require the pin rather than treating empty as "no check"). Also dismiss the confirm dialog + sheet when `card` becomes nil so a stale dialog cannot fire a pinless write. Note format() already self-protects via the canFormat re-check, so the core fix is clone()-side.

---

## 18. [LOW] Write/format failures report raw absolute block numbers and drop the actual reason the daemon already computed

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift`:423 | lens: ux-friction | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** On a partial clone the banner says e.g. '3 block(s) failed to write: [12, 24, 36]'. To a lock technician that is opaque: which sector, and why - a wrong key, an unsafe trailer that was refused, or a card swap? The daemon actually knows the reason: for trailers it emits per-block `unsafe: access-bits` or `unsafe: trailer-lockout` (x7d.py:242-249), and distinguishes auth failure from write failure. All of that is discarded; the Swift side only keeps ok/fail booleans (X7Engine writeMFD onBlock passes just (b, ok)), so the user gets bare block indices with no cause and no next step.

**Fix:** Propagate the daemon's per-block failure reason through the write_mfd/format progress events into cloneResults (or a parallel map), and phrase the summary in user terms ('sector 3 trailer refused: unsafe access bits' / 'sector 6: wrong key'). Reference sectors, not just absolute block numbers.

**Verify note:** Add an `unsafe: String?` field to EngineEvent so the daemon's already-emitted trailer reason survives decode, thread it through onBlock into a parallel [Int:String] reason map alongside cloneResults, and phrase the summary in sector terms (e.g. "sector 3 trailer refused: unsafe access bits", "sector 3 trailer refused: would lock its own keys"). Reference sectors, not raw absolute block numbers. Note: "wrong key" phrasing for data blocks is NOT currently derivable - the daemon reports data-block auth/write failures only as ok=False (x7d.py:277-288); surfacing wrong-key vs write-fail would need extra daemon instrumentation, so scope the first fix to the trailer `unsafe` reasons that already exist.

---

## 19. [LOW] You cannot format/erase a card without first successfully decoding that exact card; a file-loaded document blocks format on the physical card entirely

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/app/Sources/AppModel.swift`:69 | lens: ux-friction | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** canFormat requires the document uid to equal the reader card uid. So Format only lights up when the on-reader card is the very one you just decoded. Consequences: (a) to wipe a card you must first run a full decode on it (and if decode can't recover keys, Format is impossible - there's no factory-key-only wipe attempt); (b) after a clean format the document is dropped (lines 453-456), so to format a second identical card you must decode it again first; (c) if you loaded a dump from FILE (its uid differs from the card on the reader), Format is disabled on the physical card even when you legitimately want to erase it. For a supplier who erases/re-issues cards in volume this is repeated forced decoding.

**Fix:** Offer a direct 'format this card' that attempts factory-key (FF) auth plus any keys learned/available, independent of whether the on-reader card matches the current document; keep the destructive confirm. Don't auto-drop the document after format if the workflow is erase-many. Gate purely on readerOnline + a confirm, not on uid identity with the current document.

**Verify note:** Real behavior, low severity. Decouple Format from document-uid identity: gate on readerOnline + destructive confirm, and attempt auth with FF factory keys plus any learned/available keys in the daemon (not just the current document's recovered keys), so unknown-key or file-mismatched cards can still be wiped. For volume erase/re-issue, don't auto-drop the document after a clean format when an erase-many mode is active. Note the engine (X7Engine.formatCard / daemon) must actually support a factory-key wipe attempt for this to work end-to-end; that part is not visible in AppModel.swift and would need hardware/daemon verification.

---

## 20. [LOW] Dump fabricates KeyB = KeyA when KeyB is unreadable, then clones it as a 'recovered' key

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py`:363 | lens: daemon-driver | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** In read_sector, when KeyA authed (kt=='A') and the trailer's KeyB slot reads back all-zero (because access bits hide KeyB), the code sets t[10:16] = KeyA. The same substitution runs in write_mfd's trailer path. This is presented to the user as the card's key material and written verbatim by a clone/format. If the real KeyB differs from KeyA (a card that uses distinct A/B keys with KeyB hidden), the cloned card ends up with KeyB = KeyA - silently wrong. Any access-control reader that authenticates with the original KeyB will reject the clone, and the user has no signal that KeyB was guessed rather than recovered.

**Fix:** Mark substituted key slots explicitly (e.g. return a per-slot 'recovered' vs 'assumed' flag) so the UI can show KeyB as unknown/assumed rather than as a verified recovered key, instead of presenting a fabricated KeyB==KeyA as fact.

**Verify note:** Return a per-slot provenance flag (recovered vs assumed) for each trailer key so the UI/clone can show KeyB as unknown/assumed when it was mirrored from KeyA (x7lib.py:363-364 and x7d.py:254-255). Keep the brick-avoidance substitution at write time, but surface that the byte was guessed rather than read.

---

## 21. [LOW] Card-type detection only distinguishes SAK 0x18; Mini/other Classic variants get 11+ sectors of futile auth per decode

- File: `/Users/tuan/Claude/Tenor/tenor-rekey/probe/x7lib.py`:113 | lens: daemon-driver | confidence: certain | verdict: CONFIRMED (keep)

**Failure/pain:** sector_count() returns 40 only for SAK 0x18 (4K) and 16 for everything else. A MIFARE Mini (SAK 0x09, 5 sectors) is decoded/formatted as if it had 16 sectors: sectors 5-15 don't exist, so every auth against them fails and PASS B walks the full dictionary on each of those 11 phantom sectors, adding a large chunk of the 90s budget for a card that was fully read in its first 5 sectors. Decode of a Mini is needlessly slow and reports 11 'unrecovered' sectors that aren't real.

**Fix:** Derive geometry from SAK properly (e.g. 0x09 Mini = 5 sectors, 0x08/0x88 = 1K/16, 0x18 = 4K/40) instead of the binary 0x18-vs-else split, and stop the walk once all real sectors are resolved.

**Verify note:** Map SAK to sector count explicitly (0x09 Mini=5, 0x08/0x88 1K=16, 0x18 4K=40, 0x19/0x11 2K=32) rather than the 0x18-vs-else split. Note the blocks_in_sector/first_block/trailer_block helpers already handle the 4K big-sector layout correctly, so only sector_count needs the richer mapping; the existing budget short-circuit already caps wasted time, so the walk-stop half of the proposed fix is secondary.

---

