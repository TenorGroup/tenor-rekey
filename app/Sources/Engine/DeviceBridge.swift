import Foundation

/// Talks to a verified Python daemon (probe/<daemonScript>) over newline-delimited
/// JSON on a child-process pipe. The actor owns the process, correlates each request
/// id with a continuation, and routes progress events separately. It is device
/// neutral: the daemon to spawn, and the probe subdir it lives in, come from a
/// `DeviceDescriptor`, so the same transport drives the X7 (x7d.py) and the
/// Chameleon (chameleon_d.py) - both speak the identical contract.
///
/// Architecture (2026-06-19): A-first hybrid. This bridge is deliberately thin and
/// the daemon contract is narrow so the engine can later be replaced by a native
/// Swift + vendored-C implementation without touching the UI.
actor DeviceBridge {
    enum EngineError: Error, CustomStringConvertible {
        case daemon(String)
        case badResponse
        var description: String {
            switch self {
            case .daemon(let m): return m
            case .badResponse: return "bad daemon response"
            }
        }
    }

    /// The device this bridge drives. Immutable + Sendable, so callers on other
    /// actors can read it without hopping onto this actor (used to tell whether a
    /// live bridge already matches the detected device before spawning a new one).
    nonisolated let descriptor: DeviceDescriptor

    private let python: URL
    private let workDir: URL
    private let script: URL
    private var process: Process?
    private var stdin: FileHandle?
    private var outReader: FileHandle?
    private var errReader: FileHandle?
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var buffer = Data()
    /// Set for the duration of a streaming op (only one runs at a time, the UI
    /// disables other actions); id-less progress events are routed here.
    private var eventSink: (@Sendable (EngineEvent) -> Void)?
    /// Bumped each time a streaming op (decode / write) starts. A cancel's hard-kill
    /// fallback captures the generation of the op it is cancelling and terminates only
    /// if THAT same op is still in flight when the grace window elapses, so it can never
    /// kill a later, unrelated op that reused the shared daemon.
    private var opGeneration = 0

    init(descriptor: DeviceDescriptor) {
        self.descriptor = descriptor
        let p = Self.resolvePaths(for: descriptor)
        self.python = p.python
        self.workDir = p.workDir
        self.script = p.script
    }

    /// Resolve the python interpreter + daemon script, preferring the copies
    /// vendored inside the packaged .app (Contents/Resources/python + /probe), then
    /// environment overrides (X7_PYTHON / X7_PROBE_DIR), then the dev checkout. Both
    /// a shipped app and a dev build work with no configuration. The working dir is
    /// always the probe root so the daemons' shared imports (x7lib, learned_keys,
    /// the vendored chameleon package) resolve regardless of which script is spawned.
    /// libhidapi is found by x7hid itself (a bundle-relative candidate inside the
    /// .app, brew outside), so we never touch the child's environment here.
    static func resolvePaths(for descriptor: DeviceDescriptor) -> (python: URL, workDir: URL, script: URL) {
        let fm = FileManager.default
        let res = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let bundledPython = res.appendingPathComponent("python/bin/python3")
        let bundledProbe = res.appendingPathComponent("probe")
        let bundledScript = scriptURL(probeRoot: bundledProbe, descriptor: descriptor)
        if fm.fileExists(atPath: bundledPython.path),
           fm.fileExists(atPath: bundledScript.path) {
            return (bundledPython, bundledProbe, bundledScript)
        }
        let env = ProcessInfo.processInfo.environment
        let python = URL(fileURLWithPath: env["X7_PYTHON"] ?? "/usr/bin/python3")
        let probe = URL(fileURLWithPath: env["X7_PROBE_DIR"] ?? "/Users/tuan/Claude/Tenor/tenor-rekey/probe")
        return (python, probe, scriptURL(probeRoot: probe, descriptor: descriptor))
    }

    private static func scriptURL(probeRoot: URL, descriptor: DeviceDescriptor) -> URL {
        let dir = descriptor.probeSubdir.map { probeRoot.appendingPathComponent($0) } ?? probeRoot
        return dir.appendingPathComponent(descriptor.daemonScript)
    }

    private func startIfNeeded() throws {
        guard process == nil else { return }
        let p = Process()
        p.executableURL = python
        // -B: never write .pyc into the bundle (a code-signed .app that mutates
        // itself breaks its own seal). Passed as a flag, not an env var, so we
        // leave the inherited launchd environment untouched - replacing it broke
        // the spawn under the GUI session.
        p.arguments = ["-B", script.path]
        p.currentDirectoryURL = workDir
        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = errPipe
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard !d.isEmpty, let self else { return }
            Task { await self.ingest(d) }
        }
        // Drain the daemon's stderr. Without this the OS pipe buffer (~64KB) fills
        // the first time the engine prints a traceback or a flood of warnings, and
        // the daemon then BLOCKS on its next stderr write, hanging every request.
        // We forward it to the app's own stderr so it is visible in Console / a
        // terminal launch for diagnosis.
        errPipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if !d.isEmpty { FileHandle.standardError.write(d) }
        }
        p.terminationHandler = { [weak self] _ in
            guard let self else { return }
            Task { await self.died() }
        }
        try p.run()
        process = p
        stdin = inPipe.fileHandleForWriting
        outReader = outPipe.fileHandleForReading
        errReader = errPipe.fileHandleForReading
    }

    private func died() {
        outReader?.readabilityHandler = nil
        errReader?.readabilityHandler = nil
        outReader = nil
        errReader = nil
        process = nil
        stdin = nil
        for (_, c) in pending { c.resume(throwing: EngineError.daemon("daemon exited")) }
        pending.removeAll()
        buffer.removeAll()
    }

    private func ingest(_ d: Data) {
        buffer.append(d)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = Data(buffer[buffer.startIndex..<nl])
            buffer.removeSubrange(buffer.startIndex...nl)
            route(line)
        }
    }

    private func route(_ line: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { return }
        if obj["event"] != nil {
            if let ev = try? JSONDecoder().decode(EngineEvent.self, from: line) { eventSink?(ev) }
            return
        }
        guard let id = obj["id"] as? Int else {
            // An id-less line (the daemon's bad-json reply): if exactly one request
            // is outstanding, fail it rather than orphan its continuation.
            if let err = obj["error"] as? String, pending.count == 1,
               let only = pending.keys.first, let c = pending.removeValue(forKey: only) {
                c.resume(throwing: EngineError.daemon(err))
            }
            return
        }
        guard let c = pending.removeValue(forKey: id) else { return }
        c.resume(returning: line)
    }

    private func transact<T: Decodable>(id: Int, _ reqData: Data, timeout: Duration, as _: T.Type) async throws -> T {
        let line: Data = try await withCheckedThrowingContinuation { cont in
            pending[id] = cont
            try? stdin?.write(contentsOf: reqData)
            try? stdin?.write(contentsOf: Data([0x0A]))
            // Arm a deadline: a daemon that is alive but WEDGED (stuck on a hardware
            // read that never returns) would otherwise orphan this continuation
            // forever - freezing the live-status poll and every later op. On the
            // deadline we fail this request and kill the daemon so it respawns.
            Task { [weak self] in
                try? await Task.sleep(for: timeout)
                await self?.timeoutRequest(id: id)
            }
        }
        let env = try JSONDecoder().decode(Envelope<T>.self, from: line)
        if let e = env.error { throw EngineError.daemon(e) }
        guard let r = env.result else { throw EngineError.badResponse }
        return r
    }

    /// Fail a still-pending request whose deadline passed and terminate the wedged
    /// daemon (the next request respawns it). A no-op if the response already
    /// arrived - route() removed it from pending first, so there is no double-resume.
    private func timeoutRequest(id: Int) {
        guard let c = pending.removeValue(forKey: id) else { return }
        c.resume(throwing: EngineError.daemon("daemon timed out"))
        process?.terminate()
    }

    private func request<T: Decodable>(_ method: String, timeout: Duration = .seconds(30), as t: T.Type) async throws -> T {
        try startIfNeeded()
        let id = nextID; nextID += 1
        return try await transact(id: id, JSONEncoder().encode(Req(id: id, method: method)), timeout: timeout, as: t)
    }

    private func request<P: Encodable, T: Decodable>(_ method: String, params: P, timeout: Duration = .seconds(30), as t: T.Type) async throws -> T {
        try startIfNeeded()
        let id = nextID; nextID += 1
        return try await transact(id: id, JSONEncoder().encode(ReqP(id: id, method: method, params: params)), timeout: timeout, as: t)
    }

    func info() async throws -> DeviceInfo { try await request("info", as: DeviceInfo.self) }
    /// Poll for a card. `tries` bounds the coupling-retry count: the live status
    /// monitor passes a small value to stay snappy; a decode wants the default.
    func poll(tries: Int? = nil) async throws -> PollResult {
        if let tries {
            return try await request("poll", params: PollParams(tries: tries), as: PollResult.self)
        }
        return try await request("poll", as: PollResult.self)
    }
    /// Decode. `userKeys` are the user's editable keys, tried FIRST; the daemon
    /// appends its large built-in curated dictionary. Empty -> built-in only.
    /// `onProgress` receives the per-sector / per-key-walk progress events.
    func decode(userKeys: [String] = [],
                onProgress: @escaping @Sendable (EngineEvent) -> Void) async throws -> DecodeResult {
        // One streaming op at a time: the event slot is shared, so reject a second
        // before it can cross-wire this one's progress (callers also serialize, but
        // the actor is reentrant - this is the real guard).
        guard eventSink == nil else { throw EngineError.daemon("an operation is already in progress") }
        eventSink = { ev in if ev.method == "decode" { onProgress(ev) } }
        opGeneration += 1
        defer { eventSink = nil }
        // A decode can legitimately walk the whole dictionary for minutes; the
        // cancel button is the user's control, so give it a long backstop deadline.
        let dl = Duration.seconds(1800)
        if userKeys.isEmpty { return try await request("decode", timeout: dl, as: DecodeResult.self) }
        return try await request("decode", params: DecodeParams(user_keys: userKeys), timeout: dl, as: DecodeResult.self)
    }

    /// Cooperatively abort an in-flight streaming op: ask the daemon to stop via its
    /// `cancel` method (it trips a flag its long loops watch and returns the partial
    /// result it has gathered so far) INSTEAD of killing the process. Keeping the daemon
    /// alive preserves its learned-key cache write and needs no respawn. If the op has
    /// not wound down within a short grace window (a genuinely wedged daemon), fall back
    /// to terminating it so the pending op fails and the next call respawns.
    ///
    /// The daemon handles `cancel` inline (off its worker), so it lands while the op is
    /// still running; the op's own continuation then resolves with the partial result.
    /// We do not register a continuation for the cancel line - its id-tagged reply is
    /// simply dropped by route().
    func cancel() {
        guard let p = process else { return }
        let gen = opGeneration                  // the op we are cancelling
        let id = nextID; nextID += 1
        if let data = try? JSONEncoder().encode(Req(id: id, method: "cancel")) {
            try? stdin?.write(contentsOf: data)
            try? stdin?.write(contentsOf: Data([0x0A]))
        }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(5))
            await self?.hardCancel(generation: gen, process: p)
        }
    }

    /// Hard-kill fallback for a cancel the daemon did not honour: terminate ONLY if the
    /// SAME op that was cancelled is still in flight after the grace window - same daemon
    /// (`process === p`), same operation (`opGeneration == gen`, so a later op that
    /// reused the daemon is never killed), and still streaming (`eventSink != nil`, so an
    /// op that already wound down is left alone). Any of those failing makes this a no-op.
    private func hardCancel(generation gen: Int, process p: Process) {
        if opGeneration == gen, process === p, eventSink != nil { p.terminate() }
    }
    /// Tear the daemon down (device hot-swap / app teardown) completion-safely: signal
    /// the child, wait a bounded interval for it to actually exit, then synchronously
    /// fail every pending continuation and cancel the pipe readers. We do NOT rely on
    /// the async terminationHandler alone: by the time this returns no stale stdout
    /// line can resolve a continuation and no request is left dangling. A fresh bridge
    /// for the newly detected device respawns the right daemon on its next request.
    func shutdown() async {
        guard let p = process else { died(); return }
        p.terminate()                                   // SIGTERM
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while p.isRunning, ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(20))
        }
        if p.isRunning { p.interrupt() }                // still alive: one more signal
        // Our side is torn down regardless of whether the child has fully exited yet:
        // died() cancels the readability handlers, fails every pending request, and
        // drops the handles. It is idempotent with the terminationHandler's later
        // died() call (a second run finds nil handles + empty pending), so there is
        // no double-resume.
        died()
    }
    /// Size of the daemon's built-in dictionary (for the Settings "+N built-in" line).
    func builtinKeyCount() async throws -> Int {
        try await request("keys_builtin_count", as: CountResult.self).count
    }
    /// Number of keys the daemon has learned from real cards (Settings line).
    func learnedKeyCount() async throws -> Int {
        try await request("learned_stats", as: CountResult.self).count
    }
    /// Forget every learned key.
    func clearLearnedKeys() async throws {
        _ = try await request("learned_clear", as: CountResult.self)
    }
    func readNTAG() async throws -> NtagResult {
        try await request("read_ntag", timeout: .seconds(120), as: NtagResult.self)
    }
    /// Factory-reset the card (zero data + factory trailer). keys from a prior decode.
    func formatCard(keys: [String: [String]], targetUID: String?) async throws -> FormatResult {
        try await request("format", params: FormatParams(keys: keys, target_uid: targetUID), timeout: .seconds(300), as: FormatResult.self)
    }
    func apdu(_ hex: String) async throws -> ApduResult {
        try await request("apdu", params: ApduParams(hex: hex), as: ApduResult.self)
    }

    /// Clone a dump onto the card on the reader. Per-block results stream to
    /// `onBlock` as the daemon writes; the final tally is returned.
    func writeMFD(blocks: [String: String], keys: [String: [String]],
                  trailers: Bool, uid: Bool, targetUID: String?,
                  onBlock: @escaping @Sendable (Int, Bool, String?) -> Void) async throws -> WriteResult {
        guard eventSink == nil else { throw EngineError.daemon("an operation is already in progress") }
        eventSink = { ev in
            // `unsafe` carries WHY a trailer was refused (bad access bits / would lock
            // its own keys), so the UI can name the reason instead of a bare block index.
            if ev.method == "write_mfd", let b = ev.block, let ok = ev.ok { onBlock(b, ok, ev.unsafe) }
        }
        opGeneration += 1
        defer { eventSink = nil }
        let params = CloneParams(blocks: blocks, keys: keys, trailers: trailers, uid: uid, target_uid: targetUID)
        return try await request("write_mfd", params: params, timeout: .seconds(300), as: WriteResult.self)
    }

    private struct Req: Encodable { let id: Int; let method: String }
    private struct ReqP<P: Encodable>: Encodable { let id: Int; let method: String; let params: P }
    private struct CloneParams: Encodable {
        let blocks: [String: String]; let keys: [String: [String]]
        let trailers: Bool; let uid: Bool; let target_uid: String?
    }
    private struct ApduParams: Encodable { let hex: String }
    private struct PollParams: Encodable { let tries: Int }
    private struct FormatParams: Encodable { let keys: [String: [String]]; let target_uid: String? }
    private struct DecodeParams: Encodable { let user_keys: [String] }
    private struct CountResult: Decodable { let count: Int }
    private struct Envelope<T: Decodable>: Decodable { let result: T?; let error: String? }
}
