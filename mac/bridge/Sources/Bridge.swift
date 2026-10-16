import CoreBluetooth
import Foundation

let serviceUUID = CBUUID(string: "6D2F1001-6B0E-4A8E-9A4B-46756E4D6564")
let stateUUID = CBUUID(string: "6D2F1002-6B0E-4A8E-9A4B-46756E4D6564")

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

final class Bridge: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let writeRetrySeconds = 1.0

    let queue = DispatchQueue(label: "funmedal.bridge")
    let socketPath: String
    let log: EventLog
    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var stateCharacteristic: CBCharacteristic?
    private var link = "starting"
    private var bluetooth = "unknown"
    private var sessions = Sessions()
    private var desired = "idle"
    private var cardState: String?
    private var inFlight: String?
    private var counts: [String: Int] = [:]

    init(socketPath: String, log: EventLog) {
        self.socketPath = socketPath
        self.log = log
    }

    func start() {
        queue.async {
            self.central = CBCentralManager(delegate: self, queue: self.queue)
        }
    }

    func handle(_ data: Data?) -> (reply: [String: Any], quit: Bool) {
        queue.sync {
            guard let data else { return reject("message too long") }
            guard let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let op = message["op"] as? String else { return reject("not a JSON object with an op") }
            switch op {
            case "event":
                return receive(message)
            case "status":
                return (status(), false)
            case "quit":
                log.write("quit")
                return (["ok": true], true)
            default:
                return reject("unknown op")
            }
        }
    }

    private func reject(_ reason: String) -> (reply: [String: Any], quit: Bool) {
        counts["rejected", default: 0] += 1
        log.write("reject", ["reason": reason])
        return (["ok": false, "error": reason], false)
    }

    private func receive(_ message: [String: Any]) -> (reply: [String: Any], quit: Bool) {
        guard let name = message["event"] as? String, !name.isEmpty,
              let session = message["session"] as? String, !session.isEmpty else {
            return reject("event needs event and session")
        }
        counts["events", default: 0] += 1
        var fields: [String: Any] = ["event": name, "session": session]
        for key in ["tool_name", "tool_key", "source", "reason", "error"] {
            if let value = message[key] as? String { fields[key] = value }
        }
        log.write("event", fields)
        sessions.apply(HookEvent(name: name, session: session, toolKey: message["tool_key"] as? String))
        let shown = sessions.shown
        if shown != desired {
            desired = shown
            log.write("state", ["desired": shown])
        }
        push()
        return (["ok": true], false)
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
            "sessions": sessions.table.mapValues { ["state": $0.shown, "pending": $0.pending.count] },
            "events": counts["events", default: 0],
            "rejected": counts["rejected", default: 0],
            "writes": counts["writes", default: 0],
            "acks": counts["acks", default: 0],
            "connects": counts["connects", default: 0],
        ]
    }

    private func push() {
        guard link == "connected", let peripheral, let characteristic = stateCharacteristic,
              inFlight == nil, cardState != desired else { return }
        inFlight = desired
        counts["writes", default: 0] += 1
        log.write("write", ["value": desired])
        peripheral.writeValue(Data(desired.utf8), for: characteristic, type: .withResponse)
    }

    private func isCurrent(_ candidate: CBPeripheral) -> Bool {
        candidate === peripheral
    }

    private func lost(_ what: String, _ lostPeripheral: CBPeripheral, _ error: Error?) {
        guard isCurrent(lostPeripheral) else {
            log.write("ignored_" + what, ["error": error.map { String(describing: $0) } ?? NSNull()])
            return
        }
        link = "connecting"
        stateCharacteristic = nil
        cardState = nil
        inFlight = nil
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
            stateCharacteristic = nil
            cardState = nil
            inFlight = nil
            return
        }
        link = "scanning"
        central.scanForPeripherals(withServices: [serviceUUID])
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
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
        peripheral.discoverCharacteristics([stateUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard isCurrent(peripheral) else { return }
        guard let characteristic = service.characteristics?.first(where: { $0.uuid == stateUUID }) else {
            log.write("characteristic_missing", ["error": error.map { String(describing: $0) } ?? NSNull()])
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        stateCharacteristic = characteristic
        cardState = nil
        inFlight = nil
        link = "connected"
        log.write("ready")
        push()
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard isCurrent(peripheral), characteristic.uuid == stateUUID else { return }
        let value = inFlight
        inFlight = nil
        if let error {
            cardState = nil
            log.write("write_error", ["value": value ?? NSNull(), "error": String(describing: error)])
            queue.asyncAfter(deadline: .now() + Bridge.writeRetrySeconds) { self.push() }
            return
        }
        cardState = value
        counts["acks", default: 0] += 1
        log.write("ack", ["value": value ?? NSNull()])
        push()
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
