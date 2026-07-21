import Foundation
import CoreBluetooth
import Network

/// A discovered Bluetooth LE advertiser that speaks (or, in the bootloader, would speak)
/// the Chameleon protocol: the NUS UART service, or the Nordic DFU service when the device
/// is sitting in its bootloader.
struct BLEDevice: Identifiable, Equatable, Sendable {
    let id: String        // CBPeripheral.identifier.uuidString
    let name: String      // peripheral.name, else the advertised local name, else "Chameleon"
    let rssi: Int
    let isDFU: Bool       // advertised the FE59 DFU service (in the Nordic bootloader)
}

/// A radio-power / authorization state mirror of CBManagerState, so the UI can show a
/// "Bluetooth off" / "not authorized" state without importing CoreBluetooth.
enum BLEState: Equatable, Sendable { case unknown, unsupported, unauthorized, poweredOff, poweredOn }

/// Owns the Bluetooth LE radio for a Chameleon link. The Swift app process legitimately
/// holds the Bluetooth TCC permission, so the BLE side lives here; the Python daemon speaks
/// the SAME command protocol it uses over USB-CDC serial, so we do not re-implement it.
/// Instead `connect` brings up a loopback TCP server (Network framework) that relays raw
/// bytes to / from the NUS characteristics, and returns the 127.0.0.1 port the daemon opens
/// with its existing `tcp:HOST:PORT` transport.
///
/// The central is created with the MAIN dispatch queue, so every CoreBluetooth delegate
/// callback lands on the main thread and can hop onto this @MainActor with
/// `MainActor.assumeIsolated`. The TCP relay runs on its own queue and hops back via
/// `Task { @MainActor in ... }`. No CoreBluetooth prompt is raised until `startScan` first
/// creates the central, so the app does not ask for Bluetooth at launch.
@MainActor
@Observable
final class BLEManager: NSObject {
    // ---- NUS / DFU UUIDs (from the reference GUI; do not change) -----------
    private static let nusService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
    private static let nusRX = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")  // WRITE to device
    private static let nusTX = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")  // NOTIFY from device
    private static let dfuService = CBUUID(string: "FE59")
    /// Written to RX right after subscribing to TX notify, to bring the link up.
    private static let handshake = Data([0x11, 0xEF, 0x03, 0xFB, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])

    // ---- Public, observable state ------------------------------------------
    /// The radio state, mapped from CBManagerState by the central's delegate.
    private(set) var state: BLEState = .unknown
    /// Discovered NUS / DFU advertisers, deduped by id, sorted by rssi descending.
    private(set) var devices: [BLEDevice] = []
    private(set) var scanning = false
    /// Non-nil while a BLE link + its loopback bridge are up.
    private(set) var connectedDeviceID: String?
    var isConnected: Bool { connectedDeviceID != nil }

    // ---- CoreBluetooth ------------------------------------------------------
    private var central: CBCentralManager?
    /// Every peripheral seen while scanning, keyed by id, so `connect` can find it.
    private var discovered: [String: CBPeripheral] = [:]
    /// A scan was requested before the radio was powered on; begin it once it is.
    private var scanRequested = false
    /// The peripheral of the active (or in-flight) link.
    private var peripheral: CBPeripheral?
    private var rxChar: CBCharacteristic?
    private var txChar: CBCharacteristic?
    /// The write chunk size for the data path (max write length for a no-response write),
    /// falling back to 20 when the peripheral reports 0.
    private var maxWriteLen = 20

    /// The connect() continuation, resolved by the delegate chain or the timeout. Non-nil
    /// only while a connect is in flight; guards every fail / succeed path so there is no
    /// double-resume.
    private var connectContinuation: CheckedContinuation<Int, Error>?
    private var connectTimeoutTask: Task<Void, Never>?
    /// Monotonic connect-attempt generation. Bumped on every new connect and on every
    /// teardown, so a delegate callback / timeout belonging to a superseded attempt (a
    /// BLE-A -> BLE-B swap, a double-tap) is recognised as stale and ignored: it must not
    /// resolve the continuation or tear down the newer link.
    private var attemptGen = 0
    /// True between issuing the handshake `.withResponse` write and its ack: the loopback
    /// bridge is started only once that ack lands (didWriteValueFor), not immediately.
    private var awaitingHandshakeAck = false
    /// Resumed by `didDisconnectPeripheral` (or a bounded fallback), so a new connect can wait
    /// for the prior peripheral to FULLY disconnect before it begins. Without this, a late
    /// callback from an earlier connection to the SAME peripheral (a reconnect / double-tap)
    /// carries an identical identifier, so `isActive` would wrongly accept it into the new
    /// attempt. Serialising teardown closes that same-identifier stale-callback window.
    private var disconnectContinuation: CheckedContinuation<Void, Never>?

    // ---- Loopback TCP bridge (Network framework) ---------------------------
    private var listener: NWListener?
    private var tcpConnection: NWConnection?
    private let bridgeQueue = DispatchQueue(label: "vn.tenor.rekey.ble-bridge")
    /// The single ordered TCP -> BLE outgoing buffer. Bytes arriving from the daemon are
    /// appended here on the main actor and drained to the RX characteristic in order, only
    /// while the peripheral can accept a no-response write (real backpressure), so nothing
    /// is dropped and the byte order the daemon sent is preserved.
    private var outbox = Data()

    // ---- Scanning ----------------------------------------------------------

    /// Begin scanning for NUS / DFU advertisers. Lazily creates the central on the first
    /// call (this is what raises the Bluetooth permission prompt, not app launch). If the
    /// radio is not powered on yet, the scan begins from `centralManagerDidUpdateState`.
    func startScan() {
        if central == nil {
            central = CBCentralManager(delegate: self, queue: .main)
        }
        scanRequested = true
        if central?.state == .poweredOn { beginScan() }
    }

    /// Stop scanning and clear the pending-scan intent.
    func stopScan() {
        scanRequested = false
        central?.stopScan()
        scanning = false
    }

    private func beginScan() {
        guard let central, central.state == .poweredOn else { return }
        devices = []
        discovered = [:]
        central.scanForPeripherals(withServices: [Self.nusService, Self.dfuService],
                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        scanning = true
    }

    // ---- Connect / disconnect ----------------------------------------------

    /// Connect the peripheral for `id`, discover NUS, subscribe to TX notify, write the
    /// handshake, bring up the loopback TCP bridge, and return the 127.0.0.1 port the daemon
    /// should open. Throws on an unknown id, a radio that is not powered on, a missing NUS
    /// service / characteristic, or a stall past the ~15s overall timeout.
    func connect(_ id: String) async throws -> Int {
        if central == nil {
            central = CBCentralManager(delegate: self, queue: .main)
        }
        guard let central, state == .poweredOn else { throw BLEError.notPoweredOn }
        guard connectContinuation == nil else { throw BLEError.busy }

        let target: CBPeripheral
        if let p = discovered[id] {
            target = p
        } else if let uuid = UUID(uuidString: id),
                  let p = central.retrievePeripherals(withIdentifiers: [uuid]).first {
            target = p
            discovered[id] = p
        } else {
            throw BLEError.unknownDevice
        }

        stopScan()
        // Serialise: tear the prior link down and WAIT for its didDisconnect (bounded) before
        // the new attempt begins, so an earlier connection's late callbacks to the same
        // peripheral cannot be mistaken for this attempt's.
        await teardownAndWait()

        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Int, Error>) in
            self.attemptGen += 1
            let gen = self.attemptGen
            self.connectContinuation = cont
            self.peripheral = target
            self.awaitingHandshakeAck = false
            target.delegate = self
            // One overall deadline: any stalled step (connect, discover, notify, bridge)
            // fails the whole connect rather than orphaning the continuation. Scoped to this
            // attempt: a superseded attempt's timeout is a no-op, and a cancelled sleep
            // (the task was torn down) returns cleanly rather than failing a later attempt.
            self.connectTimeoutTask = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(15)) }
                catch { return }
                guard let self else { return }
                guard gen == self.attemptGen, self.connectContinuation != nil else { return }
                self.failConnect(BLEError.timeout)
            }
            central.connect(target, options: nil)
        }
    }

    /// Whether `p` is the peripheral of the CURRENT attempt / link. A callback from any other
    /// (stale) peripheral must be ignored so it cannot resolve the continuation or tear down
    /// the newer link.
    private func isActive(_ p: CBPeripheral) -> Bool { p.identifier == peripheral?.identifier }

    /// Tear the link down: cancel the peripheral connection, stop + close the listener and
    /// TCP connection, clear `connectedDeviceID`. Idempotent, and if a connect is still in
    /// flight it fails that continuation instead.
    func disconnect() {
        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
        if connectContinuation != nil {
            failConnect(BLEError.disconnected)
            return
        }
        teardownLink()
    }

    /// Resolve the in-flight connect with the bound port. No-op if there is no pending
    /// continuation (already resolved / torn down).
    private func succeedConnect(port: Int) {
        guard let cont = connectContinuation else { return }
        connectContinuation = nil
        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
        connectedDeviceID = peripheral?.identifier.uuidString
        cont.resume(returning: port)
    }

    /// Fail the in-flight connect and tear the partial link down. No-op if there is no
    /// pending continuation, so it is safe to call from any delegate error path.
    private func failConnect(_ error: Error) {
        guard let cont = connectContinuation else { return }
        connectContinuation = nil
        connectTimeoutTask?.cancel(); connectTimeoutTask = nil
        teardownLink()
        cont.resume(throwing: error)
    }

    /// Synchronous teardown of the BLE link + its bridge. Shared by disconnect, a fail, a
    /// radio-off, and a peripheral drop, so they cannot drift.
    private func teardownLink() {
        // Supersede any in-flight attempt: a callback / timeout still queued for the link
        // being torn down now sees a newer generation (or a nil peripheral) and is ignored.
        attemptGen += 1
        awaitingHandshakeAck = false
        teardownBridge()
        if let p = peripheral, let central { central.cancelPeripheralConnection(p) }
        peripheral = nil
        rxChar = nil
        txChar = nil
        connectedDeviceID = nil
    }

    /// Tear the current link down and await its `didDisconnectPeripheral`, so the previous
    /// link's callbacks have all drained before a new connect begins. Returns at once when
    /// there is no live peripheral. Bounded (~2s): a missing didDisconnect (e.g. an already
    /// dropped peripheral where cancelPeripheralConnection reports nothing) resumes via a
    /// fallback timer so a new attempt can never hang forever.
    private func teardownAndWait() async {
        guard peripheral != nil else { return }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            self.disconnectContinuation = cont
            self.teardownLink()   // bumps attemptGen, cancels the connection, nils peripheral
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(2))
                guard let self, let c = self.disconnectContinuation else { return }
                self.disconnectContinuation = nil
                c.resume()
            }
        }
    }

    private func teardownBridge() {
        outbox = Data()
        // Clear the handlers before cancelling so the cancelled Network objects and their
        // closures are released promptly on repeated reconnects (no lingering retain).
        if let conn = tcpConnection {
            conn.stateUpdateHandler = nil
            conn.cancel()
        }
        tcpConnection = nil
        if let l = listener {
            l.stateUpdateHandler = nil
            l.newConnectionHandler = nil
            l.cancel()
        }
        listener = nil
    }

    // ---- Loopback TCP bridge -----------------------------------------------

    /// Bind an NWListener to 127.0.0.1 on an OS-picked ephemeral port and, once it is ready,
    /// report the bound port via `onReady`. Accepts exactly one inbound connection (the
    /// daemon) and relays bytes both ways.
    private func startBridge(onReady: @escaping @MainActor (Result<Int, Error>) -> Void) {
        // Scope this bridge to the attempt that is starting it: if a teardown / newer attempt
        // supersedes this one (attemptGen bumped, or self.listener replaced) before the
        // listener callback lands, it must not resolve the current connect.
        let gen = self.attemptGen
        let params = NWParameters.tcp
        // Bind specifically to loopback on an ephemeral port (port 0 -> OS assigns).
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: 0)
        let listener: NWListener
        do {
            listener = try NWListener(using: params)
        } catch {
            onReady(.failure(error))
            return
        }
        self.listener = listener
        // Capture `listener` WEAKLY (a strong capture inside its own handler is a retain cycle)
        // so the callback can identity-check the listener that fired against the current one.
        listener.stateUpdateHandler = { [weak self, weak listener] st in
            switch st {
            case .ready:
                Task { @MainActor in
                    guard let self, let listener else { return }
                    // Superseded (gen bumped or listener replaced): cancel this orphaned
                    // listener and do NOT resolve the current connect.
                    guard gen == self.attemptGen, listener === self.listener else {
                        listener.cancel(); return
                    }
                    if let raw = listener.port?.rawValue { onReady(.success(Int(raw))) }
                    else { onReady(.failure(BLEError.bridge)) }
                }
            case .failed:
                Task { @MainActor in
                    guard let self, let listener else { return }
                    guard gen == self.attemptGen, listener === self.listener else {
                        listener.cancel(); return
                    }
                    if self.connectContinuation != nil { onReady(.failure(BLEError.bridge)) }
                    else { self.disconnect() }
                }
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] conn in
            Task { @MainActor in self?.acceptConnection(conn) }
        }
        listener.start(queue: bridgeQueue)
    }

    /// Accept exactly one inbound connection (the daemon); cancel any later one. Start the
    /// receive loop that relays daemon -> BLE bytes.
    private func acceptConnection(_ conn: NWConnection) {
        guard tcpConnection == nil else { conn.cancel(); return }
        tcpConnection = conn
        conn.stateUpdateHandler = { [weak self] st in
            switch st {
            case .failed, .cancelled:
                Task { @MainActor in self?.handleTCPClosed(conn) }
            default:
                break
            }
        }
        conn.start(queue: bridgeQueue)
        receiveLoop(conn)
    }

    /// One turn of the daemon -> BLE relay. The receive completion runs on the bridge queue
    /// and hops onto the main actor exactly once, where it appends the bytes to the single
    /// ordered `outbox`, drains what the peripheral can currently accept, and only THEN
    /// re-arms the next receive. Handling one completion fully before arming the next keeps
    /// byte order intact (no concurrent, unordered write vs re-arm tasks).
    private func receiveLoop(_ conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            Task { @MainActor in
                guard let self else { return }
                // A completion queued for a superseded / torn-down connection (a previous link)
                // must NOT append the previous daemon's bytes to the shared outbox, drain them
                // to the NEW device, or re-arm. Identity-gate on the current TCP connection.
                guard conn === self.tcpConnection else { return }
                if let data, !data.isEmpty {
                    self.outbox.append(data)
                    self.drainOutbox()
                }
                if isComplete || error != nil {
                    self.handleTCPClosed(conn)
                } else {
                    self.receiveLoop(conn)
                }
            }
        }
    }

    /// Drain the ordered outbox to the device with real backpressure: while there are bytes
    /// AND the peripheral can accept a no-response write, pop up to the max no-response write
    /// length and write it without response. Stops when the buffer empties or the peripheral
    /// stops accepting; `peripheralIsReady(toSendWriteWithoutResponse:)` resumes it. This is
    /// the NUS reliable-write pattern: nothing is dropped and the order is preserved.
    private func drainOutbox() {
        guard let peripheral, let rx = rxChar else { return }
        let chunk = maxWriteLen > 0 ? maxWriteLen : 20
        while !outbox.isEmpty, peripheral.canSendWriteWithoutResponse {
            let n = min(chunk, outbox.count)
            let piece = Data(outbox.prefix(n))
            peripheral.writeValue(piece, for: rx, type: .withoutResponse)
            outbox.removeFirst(n)
        }
    }

    /// Relay device notify bytes to the daemon over TCP.
    private func sendToTCP(_ data: Data) {
        guard let conn = tcpConnection else { return }
        conn.send(content: data, completion: .contentProcessed { _ in })
    }

    /// The daemon closed / dropped its side: tear the whole link down (the listener only ever
    /// accepts one connection, so a reconnect needs a fresh link, which phase 2 owns).
    private func handleTCPClosed(_ conn: NWConnection) {
        guard conn === tcpConnection else { return }
        disconnect()
    }

    // ---- State mapping ------------------------------------------------------

    private static func mapState(_ s: CBManagerState) -> BLEState {
        switch s {
        case .poweredOn: return .poweredOn
        case .poweredOff: return .poweredOff
        case .unauthorized: return .unauthorized
        case .unsupported: return .unsupported
        default: return .unknown
        }
    }

    enum BLEError: Error, CustomStringConvertible {
        case notPoweredOn, unknownDevice, serviceNotFound, characteristicNotFound
        case connectionFailed, disconnected, timeout, bridge, busy
        var description: String {
            switch self {
            case .notPoweredOn:          return "Bluetooth is not powered on"
            case .unknownDevice:         return "unknown Bluetooth device"
            case .serviceNotFound:       return "the device does not expose the Chameleon UART service"
            case .characteristicNotFound: return "the Chameleon UART characteristics were not found"
            case .connectionFailed:      return "the Bluetooth connection failed"
            case .disconnected:          return "the Bluetooth device disconnected"
            case .timeout:               return "the Bluetooth connection timed out"
            case .bridge:                return "the local bridge could not be started"
            case .busy:                  return "a connection is already in progress"
            }
        }
    }
}

// MARK: - CBCentralManagerDelegate

extension BLEManager: CBCentralManagerDelegate {
    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated {
            self.state = Self.mapState(central.state)
            if central.state == .poweredOn {
                if self.scanRequested { self.beginScan() }
            } else {
                self.scanning = false
                if self.connectContinuation != nil {
                    self.failConnect(BLEError.notPoweredOn)
                } else if self.connectedDeviceID != nil {
                    self.teardownLink()
                }
            }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                                    advertisementData: [String: Any], rssi RSSI: NSNumber) {
        MainActor.assumeIsolated {
            let id = peripheral.identifier.uuidString
            self.discovered[id] = peripheral
            let advName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
            let name = peripheral.name ?? advName ?? "Chameleon"
            var isDFU = false
            if let services = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] {
                isDFU = services.contains(Self.dfuService)
            }
            let device = BLEDevice(id: id, name: name, rssi: RSSI.intValue, isDFU: isDFU)
            if let idx = self.devices.firstIndex(where: { $0.id == id }) {
                self.devices[idx] = device
            } else {
                self.devices.append(device)
            }
            self.devices.sort { $0.rssi > $1.rssi }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral) else { return }   // ignore a stale attempt's connect
            peripheral.discoverServices([Self.nusService])
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            // Only the active attempt's failure fails the current connect; a superseded
            // attempt's late failure must not tear down the newer link.
            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
            self.failConnect(BLEError.connectionFailed)
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            // A teardownAndWait() is parked on this disconnect: resume it so a queued new
            // connect can proceed now that the prior peripheral has fully dropped. Done first,
            // because teardownAndWait's teardownLink() already nil'd `self.peripheral`, so the
            // isActive guard below would otherwise return before this ran.
            if let cont = self.disconnectContinuation {
                self.disconnectContinuation = nil
                cont.resume()
            }
            // A stale peripheral's disconnect (e.g. the prior device dropping after a swap)
            // must neither fail the new connect nor tear down the new link.
            guard self.isActive(peripheral) else { return }
            if self.connectContinuation != nil {
                self.failConnect(BLEError.disconnected)
            } else if self.connectedDeviceID == peripheral.identifier.uuidString {
                self.teardownLink()
            }
        }
    }
}

// MARK: - CBPeripheralDelegate

extension BLEManager: CBPeripheralDelegate {
    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
            if error != nil { self.failConnect(BLEError.serviceNotFound); return }
            guard let svc = peripheral.services?.first(where: { $0.uuid == Self.nusService }) else {
                self.failConnect(BLEError.serviceNotFound); return
            }
            peripheral.discoverCharacteristics([Self.nusRX, Self.nusTX], for: svc)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
            let chars = service.characteristics ?? []
            guard let rx = chars.first(where: { $0.uuid == Self.nusRX }),
                  let tx = chars.first(where: { $0.uuid == Self.nusTX }) else {
                self.failConnect(BLEError.characteristicNotFound); return
            }
            self.rxChar = rx
            self.txChar = tx
            let m = peripheral.maximumWriteValueLength(for: .withoutResponse)
            self.maxWriteLen = m > 0 ? m : 20
            peripheral.setNotifyValue(true, for: tx)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral), self.connectContinuation != nil else { return }
            if error != nil { self.failConnect(BLEError.characteristicNotFound); return }
            guard characteristic.uuid == Self.nusTX, characteristic.isNotifying else { return }
            guard let rx = self.rxChar else { self.failConnect(BLEError.characteristicNotFound); return }
            // Bring the link up: write the handshake (with response) and wait for its ack in
            // didWriteValueFor before starting the bridge, so a bridge is never opened over a
            // handshake that failed.
            self.awaitingHandshakeAck = true
            peripheral.writeValue(Self.handshake, for: rx, type: .withResponse)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral) else { return }
            // Only the handshake write (a .withResponse write to RX) is acked here; the data
            // path writes .withoutResponse and never reports. Start the bridge only now, once
            // the handshake is confirmed to have landed.
            guard self.awaitingHandshakeAck, characteristic.uuid == Self.nusRX else { return }
            self.awaitingHandshakeAck = false
            guard self.connectContinuation != nil else { return }
            if error != nil { self.failConnect(BLEError.disconnected); return }
            self.startBridge { result in
                switch result {
                case .success(let port): self.succeedConnect(port: port)
                case .failure:
                    if self.connectContinuation != nil { self.failConnect(BLEError.bridge) }
                    else { self.disconnect() }
                }
            }
        }
    }

    nonisolated func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral) else { return }
            self.drainOutbox()
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            guard self.isActive(peripheral) else { return }
            guard characteristic.uuid == Self.nusTX, let data = characteristic.value, !data.isEmpty else { return }
            self.sendToTCP(data)
        }
    }
}
