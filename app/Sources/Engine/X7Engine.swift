import Foundation

/// Talks to the verified Python engine (probe/x7d.py) over newline-delimited
/// JSON on a child-process pipe. The actor owns the process, correlates each
/// request id with a continuation, and routes progress events separately.
///
/// Architecture (2026-06-19): A-first hybrid. This bridge is deliberately thin
/// and the daemon contract is narrow so the engine can later be replaced by a
/// native Swift + vendored-C implementation without touching the UI.
actor X7Engine {
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

    private let python: URL
    private let workDir: URL
    private let script: URL
    private var process: Process?
    private var stdin: FileHandle?
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var buffer = Data()
    /// Set for the duration of a streaming op (only one runs at a time, the UI
    /// disables other actions); id-less progress events are routed here.
    private var eventSink: (@Sendable (EngineEvent) -> Void)?

    init() {
        let p = Self.resolvePaths()
        self.python = p.python
        self.workDir = p.probeDir
        self.script = p.probeDir.appendingPathComponent("x7d.py")
    }

    /// Resolve the python interpreter + probe engine, preferring the copies
    /// vendored inside the packaged .app (Contents/Resources/python + /probe),
    /// then environment overrides (X7_PYTHON / X7_PROBE_DIR), then the dev
    /// checkout. Both a shipped app and a dev build work with no configuration.
    /// libhidapi is found by x7hid itself (a bundle-relative candidate inside the
    /// .app, brew outside), so we never touch the child's environment here.
    static func resolvePaths() -> (python: URL, probeDir: URL) {
        let fm = FileManager.default
        let res = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let bundledPython = res.appendingPathComponent("python/bin/python3")
        let bundledProbe = res.appendingPathComponent("probe")
        if fm.fileExists(atPath: bundledPython.path),
           fm.fileExists(atPath: bundledProbe.appendingPathComponent("x7d.py").path) {
            return (bundledPython, bundledProbe)
        }
        let env = ProcessInfo.processInfo.environment
        let python = URL(fileURLWithPath: env["X7_PYTHON"] ?? "/usr/bin/python3")
        let probe = URL(fileURLWithPath: env["X7_PROBE_DIR"] ?? "/Users/tuan/Claude/Tenor/tenor-rekey/probe")
        return (python, probe)
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
        p.terminationHandler = { [weak self] _ in
            guard let self else { return }
            Task { await self.died() }
        }
        try p.run()
        process = p
        stdin = inPipe.fileHandleForWriting
    }

    private func died() {
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
        guard let id = obj["id"] as? Int, let c = pending.removeValue(forKey: id) else { return }
        c.resume(returning: line)
    }

    private func transact<T: Decodable>(id: Int, _ reqData: Data, as _: T.Type) async throws -> T {
        let line: Data = try await withCheckedThrowingContinuation { cont in
            pending[id] = cont
            try? stdin?.write(contentsOf: reqData)
            try? stdin?.write(contentsOf: Data([0x0A]))
        }
        let env = try JSONDecoder().decode(Envelope<T>.self, from: line)
        if let e = env.error { throw EngineError.daemon(e) }
        guard let r = env.result else { throw EngineError.badResponse }
        return r
    }

    private func request<T: Decodable>(_ method: String, as t: T.Type) async throws -> T {
        try startIfNeeded()
        let id = nextID; nextID += 1
        return try await transact(id: id, JSONEncoder().encode(Req(id: id, method: method)), as: t)
    }

    private func request<P: Encodable, T: Decodable>(_ method: String, params: P, as t: T.Type) async throws -> T {
        try startIfNeeded()
        let id = nextID; nextID += 1
        return try await transact(id: id, JSONEncoder().encode(ReqP(id: id, method: method, params: params)), as: t)
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
        eventSink = { ev in if ev.method == "decode" { onProgress(ev) } }
        defer { eventSink = nil }
        if userKeys.isEmpty { return try await request("decode", as: DecodeResult.self) }
        return try await request("decode", params: DecodeParams(user_keys: userKeys), as: DecodeResult.self)
    }

    /// Abort an in-flight operation by killing the daemon: its termination fails
    /// the pending request, and the next call respawns it. Used to cancel a long
    /// decode (a card whose keys are not in the dictionary walks the whole list).
    func cancel() {
        process?.terminate()
    }
    /// Size of the daemon's built-in dictionary (for the Settings "+N built-in" line).
    func builtinKeyCount() async throws -> Int {
        try await request("keys_builtin_count", as: CountResult.self).count
    }
    func readNTAG() async throws -> NtagResult { try await request("read_ntag", as: NtagResult.self) }
    /// Factory-reset the card (zero data + factory trailer). keys from a prior decode.
    func formatCard(keys: [String: [String]]) async throws -> FormatResult {
        try await request("format", params: FormatParams(keys: keys), as: FormatResult.self)
    }
    func apdu(_ hex: String) async throws -> ApduResult {
        try await request("apdu", params: ApduParams(hex: hex), as: ApduResult.self)
    }

    /// Clone a dump onto the card on the reader. Per-block results stream to
    /// `onBlock` as the daemon writes; the final tally is returned.
    func writeMFD(blocks: [String: String], keys: [String: [String]],
                  trailers: Bool, uid: Bool,
                  onBlock: @escaping @Sendable (Int, Bool) -> Void) async throws -> WriteResult {
        eventSink = { ev in
            if ev.method == "write_mfd", let b = ev.block, let ok = ev.ok { onBlock(b, ok) }
        }
        defer { eventSink = nil }
        let params = CloneParams(blocks: blocks, keys: keys, trailers: trailers, uid: uid)
        return try await request("write_mfd", params: params, as: WriteResult.self)
    }

    private struct Req: Encodable { let id: Int; let method: String }
    private struct ReqP<P: Encodable>: Encodable { let id: Int; let method: String; let params: P }
    private struct CloneParams: Encodable {
        let blocks: [String: String]; let keys: [String: [String]]
        let trailers: Bool; let uid: Bool
    }
    private struct ApduParams: Encodable { let hex: String }
    private struct PollParams: Encodable { let tries: Int }
    private struct FormatParams: Encodable { let keys: [String: [String]] }
    private struct DecodeParams: Encodable { let user_keys: [String] }
    private struct CountResult: Decodable { let count: Int }
    private struct Envelope<T: Decodable>: Decodable { let result: T?; let error: String? }
}
