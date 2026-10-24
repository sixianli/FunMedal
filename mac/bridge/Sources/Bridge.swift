import CoreBluetooth
import Darwin
import Foundation

let serviceUUID = CBUUID(string: "6D2F1001-6B0E-4A8E-9A4B-46756E4D6564")
let stateUUID = CBUUID(string: "6D2F1002-6B0E-4A8E-9A4B-46756E4D6564")
let versionUUID = CBUUID(string: "6D2F1008-6B0E-4A8E-9A4B-46756E4D6564")

final class EventLog {
    private let handle: FileHandle?
    private let lock = NSLock()

    init(path: String?) {
        guard let path else {
            handle = nil
            return
        }
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        handle = FileHandle(forWritingAtPath: path)
        handle?.seekToEndOfFile()
    }

    func write(_ kind: String, _ fields: [String: Any] = [:]) {
        guard let handle else { return }
        var entry = fields
        entry["t"] = Date().timeIntervalSince1970
        entry["kind"] = kind
        guard var data = try? JSONSerialization.data(withJSONObject: entry, options: [.sortedKeys]) else { return }
        data.append(UInt8(ascii: "\n"))
        lock.lock()
        handle.write(data)
        lock.unlock()
    }
}

final class PermissionWait {
    let id: Int
    let session: String
    let toolKey: String?
    let fd: Int32
    var outcome: String?

    init(id: Int, session: String, toolKey: String?, fd: Int32) {
        self.id = id
        self.session = session
        self.toolKey = toolKey
        self.fd = fd
    }
}

struct Handled {
    var reply: [String: Any]?
    var wait: PermissionWait?
    var quit = false
}

final class Bridge: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let writeRetrySeconds = 1.0
    static let linkVersion = 2
    static let pairingAttempts = 3
    static let linkVersions = 1...9999
    static let releasingEvents = ["UserPromptSubmit": "prompt", "Stop": "stop", "StopFailure": "stop_failure",
                                  "SessionEnd": "session_end"]

    let queue = DispatchQueue(label: "funmedal.bridge")
    let socketPath: String
    let log: EventLog
    let linkVersion: Int
    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var characteristics: [CBUUID: CBCharacteristic] = [:]
    private var cardVersion: Int?
    private var link = "starting"
    private var pairingFailures = 0
    private var bluetooth = "unknown"
    private var sessions = Sessions()
    private var desired = "idle"
    private var cardState: String?
    private var waits: [PermissionWait] = []
    private var nextWaitID = 1
    private var inFlight: String?
    private var counts: [String: Int] = [:]

    init(socketPath: String, log: EventLog, linkVersion: Int) {
        self.socketPath = socketPath
        self.linkVersion = linkVersion
        self.log = log
    }

    func start() {
        queue.async {
            self.central = CBCentralManager(delegate: self, queue: self.queue)
        }
    }

    func handle(_ data: Data?, client: Int32) -> Handled {
        queue.sync {
            guard let data else { return Handled(reply: reject("message too long")) }
            guard let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let op = message["op"] as? String else {
                return Handled(reply: reject("not a JSON object with an op"))
            }
            switch op {
            case "event":
                return Handled(reply: receive(message))
            case "wait":
                return hold(message, client: client)
            case "status":
                return Handled(reply: status())
            case "quit":
                log.write("quit")
                return Handled(reply: ["ok": true], quit: true)
            default:
                return Handled(reply: reject("unknown op"))
            }
        }
    }

    func gaveUp(_ wait: PermissionWait) {
        queue.sync {
            guard wait.outcome == nil else { return }
            finish(wait, as: "gave_up")
            log.write("wait_gave_up", ["id": wait.id])
        }
    }

    func ended(_ wait: PermissionWait) {
        queue.sync {
            if wait.outcome == nil {
                wait.outcome = "hung_up"
                waits.removeAll { $0 === wait }
                sessions.answeredInTerminal(session: wait.session, toolKey: wait.toolKey)
                refresh()
            }
            log.write("wait_closed", ["id": wait.id, "how": wait.outcome ?? "hung_up"])
        }
    }

    private func reject(_ reason: String) -> [String: Any] {
        counts["rejected", default: 0] += 1
        log.write("reject", ["reason": reason])
        return ["ok": false, "error": reason]
    }

    private static func eventIdentity(_ message: [String: Any]) -> (name: String, session: String)? {
        guard let name = message["event"] as? String, !name.isEmpty,
              let session = message["session"] as? String, !session.isEmpty else { return nil }
        return (name, session)
    }

    private func record(_ message: [String: Any], name: String, session: String) {
        counts["events", default: 0] += 1
        var fields: [String: Any] = ["event": name, "session": session]
        for key in ["tool_name", "tool_key", "source", "reason", "error"] {
            if let value = message[key] as? String { fields[key] = value }
        }
        log.write("event", fields)
        let toolKey = message["tool_key"] as? String
        sessions.apply(HookEvent(name: name, session: session, toolKey: toolKey))
        if name == "PostToolUse" || name == "PostToolUseFailure",
           let wait = waits.first(where: { $0.session == session && $0.toolKey == toolKey }) {
            release(wait, why: "post_tool_use")
        }
        if let why = Bridge.releasingEvents[name] {
            for wait in waits.filter({ $0.session == session }) {
                release(wait, why: why)
            }
        }
    }

    private func receive(_ message: [String: Any]) -> [String: Any] {
        guard let event = Bridge.eventIdentity(message) else { return reject("event needs event and session") }
        record(message, name: event.name, session: event.session)
        refresh()
        return ["ok": true]
    }

    private func hold(_ message: [String: Any], client: Int32) -> Handled {
        guard let event = Bridge.eventIdentity(message), event.name == "PermissionRequest" else {
            return Handled(reply: reject("wait needs a PermissionRequest with a session"))
        }
        record(message, name: event.name, session: event.session)
        let wait = PermissionWait(id: nextWaitID, session: event.session, toolKey: message["tool_key"] as? String,
                                  fd: client)
        nextWaitID += 1
        waits.append(wait)
        log.write("wait", ["id": wait.id, "session": wait.session])
        if let data = Hook.line(["ok": true, "id": wait.id]) { _ = UnixSocket.writeAll(client, data) }
        refresh()
        return Handled(wait: wait)
    }

    private func finish(_ wait: PermissionWait, as outcome: String) {
        wait.outcome = outcome
        waits.removeAll { $0 === wait }
        shutdown(wait.fd, SHUT_WR)
    }

    private func release(_ wait: PermissionWait, why: String) {
        guard wait.outcome == nil else { return }
        finish(wait, as: "released")
        log.write("wait_released", ["id": wait.id, "why": why])
    }

    private func refresh() {
        let shown = sessions.shown
        if shown != desired {
            desired = shown
            log.write("state", ["desired": shown])
        }
        push()
    }

    private func status() -> [String: Any] {
        [
            "ok": true,
            "pid": Int(getpid()),
            "socket": socketPath,
            "bluetooth": bluetooth,
            "authorization": Bridge.name(CBManager.authorization),
            "card": link,
            "desired": desired,
            "card_state": cardState ?? NSNull(),
            "card_version": cardVersion ?? NSNull(),
            "bridge_version": linkVersion,
            "waits": waits.map { ["id": $0.id, "session": $0.session] },
            "sessions": sessions.table.mapValues { ["state": $0.shown, "pending": $0.pendingCount] },
            "events": counts["events", default: 0],
            "rejected": counts["rejected", default: 0],
            "writes": counts["writes", default: 0],
            "acks": counts["acks", default: 0],
            "connects": counts["connects", default: 0],
        ]
    }

    private func push() {
        guard link == "connected", let peripheral, inFlight == nil, cardState != desired,
              let characteristic = characteristics[stateUUID] else { return }
        inFlight = desired
        counts["writes", default: 0] += 1
        log.write("write", ["what": "state", "value": desired])
        peripheral.writeValue(Data(desired.utf8), for: characteristic, type: .withResponse)
    }

    private func forgetCard() {
        characteristics = [:]
        cardState = nil
        inFlight = nil
    }

    private var gaveUp: Bool {
        link == "mismatch" || link == "pairing_failed"
    }

    static func isPairingFailure(_ error: Error?) -> Bool {
        guard let code = (error as? CBATTError)?.code else { return false }
        return code == .insufficientEncryption || code == .insufficientAuthentication
    }

    private func isCurrent(_ candidate: CBPeripheral) -> Bool {
        candidate === peripheral
    }

    private func lost(_ what: String, _ lostPeripheral: CBPeripheral, _ error: Error?) {
        guard isCurrent(lostPeripheral) else {
            log.write("ignored_" + what, ["error": error.map { String(describing: $0) } ?? NSNull()])
            return
        }
        if gaveUp {
            forgetCard()
            log.write(what, ["error": error.map { String(describing: $0) } ?? NSNull(), "reconnect": false])
            return
        }
        link = "connecting"
        forgetCard()
        log.write(what, ["error": error.map { String(describing: $0) } ?? NSNull()])
        guard let central, central.state == .poweredOn else { return }
        if let peripheral { central.connect(peripheral) }
        central.scanForPeripherals(withServices: [serviceUUID])
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        bluetooth = Bridge.name(central.state)
        log.write("bluetooth", ["state": bluetooth, "authorization": Bridge.name(CBManager.authorization)])
        guard central.state == .poweredOn else {
            link = "off"
            peripheral = nil
            forgetCard()
            return
        }
        link = "scanning"
        pairingFailures = 0
        central.scanForPeripherals(withServices: [serviceUUID])
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        if gaveUp { return }
        if let current = self.peripheral {
            if current.identifier == peripheral.identifier || link == "connected" || link == "discovering" { return }
            central.cancelPeripheralConnection(current)
        }
        self.peripheral = peripheral
        peripheral.delegate = self
        link = "connecting"
        log.write("discovered", ["rssi": RSSI.intValue, "name": peripheral.name ?? NSNull()])
        central.connect(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard isCurrent(peripheral) else {
            central.cancelPeripheralConnection(peripheral)
            return
        }
        counts["connects", default: 0] += 1
        link = "discovering"
        cardVersion = nil
        central.stopScan()
        log.write("connected")
        peripheral.discoverServices([serviceUUID])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        lost("connect_failed", peripheral, error)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        lost("disconnected", peripheral, error)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard isCurrent(peripheral) else { return }
        guard let service = peripheral.services?.first(where: { $0.uuid == serviceUUID }) else {
            log.write("service_missing", ["error": error.map { String(describing: $0) } ?? NSNull()])
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        peripheral.discoverCharacteristics([stateUUID, versionUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard isCurrent(peripheral) else { return }
        var found: [CBUUID: CBCharacteristic] = [:]
        for characteristic in service.characteristics ?? []
        where characteristic.uuid == stateUUID || characteristic.uuid == versionUUID {
            found[characteristic.uuid] = characteristic
        }
        forgetCard()
        characteristics = found
        guard let version = found[versionUUID] else {
            mismatch(card: nil, of: peripheral)
            return
        }
        peripheral.readValue(for: version)
    }

    private func mismatch(card: Int?, of peripheral: CBPeripheral) {
        cardVersion = card
        link = "mismatch"
        log.write("version", ["card": card ?? NSNull(), "bridge": linkVersion, "match": false])
        central?.stopScan()
        central?.cancelPeripheralConnection(peripheral)
    }

    private func checkVersion(_ characteristic: CBCharacteristic, of peripheral: CBPeripheral, error: Error?) {
        guard error == nil, let data = characteristic.value, let text = String(data: data, encoding: .utf8),
              let version = Int(text), Bridge.linkVersions.contains(version) else {
            var fields: [String: Any] = ["bytes": characteristic.value?.count ?? 0,
                                         "error": error.map { String(describing: $0) } ?? NSNull()]
            if Bridge.isPairingFailure(error) {
                pairingFailures += 1
                fields["pairing_failures"] = pairingFailures
            }
            log.write("version_unreadable", fields)
            if pairingFailures >= Bridge.pairingAttempts {
                link = "pairing_failed"
                central?.stopScan()
            }
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        cardVersion = version
        peripheral.writeValue(Data(String(linkVersion).utf8), for: characteristic, type: .withResponse)
    }

    private func versionWritten(of peripheral: CBPeripheral, error: Error?) {
        guard let card = cardVersion else { return }
        guard card == linkVersion else {
            mismatch(card: card, of: peripheral)
            return
        }
        if let error {
            log.write("version_write_error", ["error": String(describing: error)])
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        log.write("version", ["card": card, "bridge": linkVersion, "match": true])
        guard characteristics[stateUUID] != nil else {
            log.write("characteristic_missing", ["found": characteristics.keys.map(\.uuidString).sorted()])
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        link = "connected"
        pairingFailures = 0
        log.write("ready")
        push()
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if isCurrent(peripheral), characteristic.uuid == versionUUID {
            versionWritten(of: peripheral, error: error)
            return
        }
        guard isCurrent(peripheral), let value = inFlight, characteristic.uuid == stateUUID else { return }
        inFlight = nil
        if let error {
            log.write("write_error", ["what": "state", "value": value, "error": String(describing: error)])
            cardState = nil
            queue.asyncAfter(deadline: .now() + Bridge.writeRetrySeconds) { self.push() }
            return
        }
        cardState = value
        counts["acks", default: 0] += 1
        log.write("ack", ["what": "state", "value": value])
        push()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if isCurrent(peripheral), characteristic.uuid == versionUUID {
            checkVersion(characteristic, of: peripheral, error: error)
        }
    }

    static func name(_ state: CBManagerState) -> String {
        switch state {
        case .poweredOn: return "poweredOn"
        case .poweredOff: return "poweredOff"
        case .unauthorized: return "unauthorized"
        case .unsupported: return "unsupported"
        case .resetting: return "resetting"
        case .unknown: return "unknown"
        @unknown default: return "state\(state.rawValue)"
        }
    }

    static func name(_ authorization: CBManagerAuthorization) -> String {
        switch authorization {
        case .allowedAlways: return "allowedAlways"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "notDetermined"
        @unknown default: return "authorization\(authorization.rawValue)"
        }
    }
}
