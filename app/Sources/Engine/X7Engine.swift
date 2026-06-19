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

    private let python = URL(fileURLWithPath: "/usr/bin/python3")
    private let workDir: URL
    private let script: URL
    private var process: Process?
    private var stdin: FileHandle?
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var buffer = Data()

    init(probeDir: String = "/Users/tuan/Claude/Tenor/tenor-rekey/probe") {
        self.workDir = URL(fileURLWithPath: probeDir)
        self.script = workDir.appendingPathComponent("x7d.py")
    }

    private func startIfNeeded() throws {
        guard process == nil else { return }
        let p = Process()
        p.executableURL = python
        p.arguments = [script.path]
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
        if obj["event"] != nil { return }   // progress events: wired in a later step
        guard let id = obj["id"] as? Int, let c = pending.removeValue(forKey: id) else { return }
        c.resume(returning: line)
    }

    private func request<T: Decodable>(_ method: String, as _: T.Type) async throws -> T {
        try startIfNeeded()
        let id = nextID; nextID += 1
        let reqData = try JSONEncoder().encode(Req(id: id, method: method))
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

    func info() async throws -> DeviceInfo { try await request("info", as: DeviceInfo.self) }
    func poll() async throws -> PollResult { try await request("poll", as: PollResult.self) }
    func decode() async throws -> DecodeResult { try await request("decode", as: DecodeResult.self) }

    private struct Req: Encodable { let id: Int; let method: String }
    private struct Envelope<T: Decodable>: Decodable { let result: T?; let error: String? }
}
