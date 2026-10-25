import CoreBluetooth
import Darwin
import Foundation

let serviceUUID = CBUUID(string: "6D2F1001-6B0E-4A8E-9A4B-46756E4D6564")
let versionUUID = CBUUID(string: "6D2F1008-6B0E-4A8E-9A4B-46756E4D6564")
let rowsUUID = CBUUID(string: "6D2F1009-6B0E-4A8E-9A4B-46756E4D6564")

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
    let fd: Int32
    var outcome: String?

    init(id: Int, session: String, fd: Int32) {
        self.id = id
        self.session = session
        self.fd = fd
    }
}

struct Handled {
    var reply: [String: Any]?
    var wait: PermissionWait?
    var quit = false
}

struct RowsWrite {
    let what: String
    let data: Data
    let key: [String]?
    let fresh: [String: Int]
}

final class Bridge: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let writeRetrySeconds = 1.0
    static let heartbeatSeconds = 30.0
    static let pidCheckSeconds = 5.0
    static let agentsCheckSeconds = 15.0
    static let quitDisconnectSeconds = 1.0
    static let linkVersion = 4
    static let pairingAttempts = 3
    static let linkVersions = 1...9999
    static let releasingEvents = ["UserPromptSubmit": "prompt", "Stop": "stop", "StopFailure": "stop_failure",
                                  "SessionEnd": "session_end"]

    let queue = DispatchQueue(label: "funmedal.bridge")
    let socketPath: String
    let savedPath: String
    let log: EventLog
    let linkVersion: Int
    let agentsSeconds: Double
    private var claude: String?
    private let claudeChosen: Bool
    private var checking = false
    private var agentsCheck = "not_yet"
    private var lastEvent: [String: Double] = [:]
    private var answers: [String: (seen: Seen, inARow: Int)] = [:]
    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var characteristics: [CBUUID: CBCharacteristic] = [:]
    private var cardVersion: Int?
    private var link = "starting"
    private var pairingFailures = 0
    private var bluetooth = "unknown"
    private var sessions = Sessions()
    private var savedData: Data?
    private var waits: [PermissionWait] = []
    private var nextWaitID = 1
    private var inFlight: RowsWrite?
    private var ackedKey: [String]?
    private var cardFrame: Data?
    private var pendingFresh: [String: Int] = [:]
    private var freshCount = 0
    private var maxWrite: Int?
    private var nameLimit = Rows.nameLimit
    private var timers: [DispatchSourceTimer] = []
    private var quitting: DispatchSemaphore?
    private var counts: [String: Int] = [:]

    init(socketPath: String, log: EventLog, linkVersion: Int, claude: String?, agentsSeconds: Double) {
        self.socketPath = socketPath
        self.savedPath = socketPath + ".sessions.json"
        self.linkVersion = linkVersion
        self.log = log
        self.claude = claude
        self.claudeChosen = claude != nil
        self.agentsSeconds = agentsSeconds
    }

    func start() {
        queue.async {
            self.load()
            self.checkPids()
            self.timers = [self.every(Bridge.heartbeatSeconds) { self.heartbeat() },
                           self.every(Bridge.pidCheckSeconds) { self.checkPids() },
                           self.every(self.agentsSeconds) { self.checkAgents() }]
            self.central = CBCentralManager(delegate: self, queue: self.queue)
        }
    }

    func stop() {
        let done = DispatchSemaphore(value: 0)
        let waiting: Bool = queue.sync {
            guard let peripheral, let central, peripheral.state != .disconnected else { return false }
            quitting = done
            central.cancelPeripheralConnection(peripheral)
            return true
        }
        if waiting { _ = done.wait(timeout: .now() + Bridge.quitDisconnectSeconds) }
    }

    private func every(_ seconds: Double, _ body: @escaping () -> Void) -> DispatchSourceTimer {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + seconds, repeating: seconds)
        timer.setEventHandler(handler: body)
        timer.resume()
        return timer
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
            sessions.gaveUp(session: wait.session, hook: wait.id)
            answers[wait.session] = nil
            log.write("wait_gave_up", ["id": wait.id])
            refresh()
        }
    }

    func ended(_ wait: PermissionWait) {
        queue.sync {
            if wait.outcome == nil {
                wait.outcome = "hung_up"
                waits.removeAll { $0 === wait }
                sessions.hungUp(session: wait.session, hook: wait.id)
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

    private func record(_ message: [String: Any], name: String, session: String, hook: Int? = nil) {
        counts["events", default: 0] += 1
        var fields: [String: Any] = ["event": name, "session": session]
        for key in ["tool_name", "tool_key", "tool_use_id", "notification_type", "project", "source", "reason",
                    "error"] {
            if let value = message[key] as? String { fields[key] = value }
        }
        let asks = message["asks"] as? Bool == true
        if asks { fields["asks"] = true }
        let projectThread = message["project_thread"] as? Bool == true
        if projectThread { fields["project_thread"] = true }
        log.write("event", fields)
        let event = HookEvent(name: name, session: session, toolName: message["tool_name"] as? String,
                              toolKey: message["tool_key"] as? String, toolUseID: message["tool_use_id"] as? String,
                              notificationType: message["notification_type"] as? String,
                              project: message["project"] as? String, pid: message["claude_pid"] as? Int,
                              hook: hook, now: Date().timeIntervalSince1970, asks: asks,
                              projectThread: projectThread)
        lastEvent[session] = event.now
        answers[session] = nil
        let why = Bridge.releasingEvents[name] ?? "post_tool_use"
        release(sessions.apply(event), why: why)
    }

    private func release(_ hooks: [Int], why: String) {
        for id in hooks {
            if let wait = waits.first(where: { $0.id == id }) { release(wait, why: why) }
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
        let wait = PermissionWait(id: nextWaitID, session: event.session, fd: client)
        nextWaitID += 1
        waits.append(wait)
        record(message, name: event.name, session: event.session, hook: wait.id)
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
        for id in sessions.takeEntered() {
            freshCount += 1
            pendingFresh[id] = freshCount
        }
        save()
        push()
    }

    private static func processGone(_ pid: Int) -> Bool {
        kill(pid_t(pid), 0) != 0 && errno == ESRCH
    }

    private func checkPids() {
        for (id, pid) in sessions.pids where Bridge.processGone(pid) {
            log.write("session_gone", ["session": id, "pid": pid])
            pendingFresh[id] = nil
            release(sessions.remove(id), why: "session_gone")
        }
        refresh()
    }

    private func checkAgents() {
        guard !checking, !sessions.table.isEmpty else { return }
        checking = true
        let began = Date().timeIntervalSince1970
        let known = claude
        DispatchQueue.global().async {
            let program = known ?? Bridge.newestClaude()
            let ran = program.flatMap { Doctor.execute($0, ["agents", "--json"]) }
            self.queue.async { self.agentsAnswered(ran, program: program, began: began) }
        }
    }

    private static func newestClaude() -> String? {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        let terminal = Doctor.findClaude().map { Doctor.ClaudeCopy(program: $0, version: Doctor.readVersion($0)) }
        return Doctor.newest(terminal, Doctor.desktopClaude(home: home))?.program
    }

    static func parseAgents(_ text: String) -> [String: Seen]? {
        guard let list = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [Any] else { return nil }
        var seen: [String: Seen] = [:]
        for item in list {
            guard let entry = item as? [String: Any] else { return nil }
            if let id = entry["sessionId"] as? String, let status = (entry["status"] as? String).flatMap(Seen.init) {
                seen[id] = status
            }
        }
        return seen
    }

    private func agentsAnswered(_ ran: Doctor.Ran?, program: String?, began: Double) {
        checking = false
        if !claudeChosen { claude = ran?.code == 0 ? program : nil }
        answers = answers.filter { sessions.table[$0.key] != nil }
        lastEvent = lastEvent.filter { sessions.table[$0.key] != nil }
        guard let ran, ran.code == 0, let seen = Bridge.parseAgents(ran.out) else {
            agentsCheck = program == nil ? "no_claude" : ran?.code == 0 ? "unreadable" : "failed"
            answers.removeAll()
            log.write("agents", ["result": agentsCheck, "exit": ran.map { Int($0.code) } ?? NSNull()])
            return
        }
        agentsCheck = "ok"
        var logged: [String: String] = [:]
        let now = Date().timeIntervalSince1970
        for id in sessions.table.keys {
            guard let status = seen[id], lastEvent[id].map({ $0 < began }) ?? true else {
                answers[id] = nil
                continue
            }
            let inARow = answers[id].map { $0.seen == status ? $0.inARow + 1 : 1 } ?? 1
            answers[id] = (status, inARow)
            logged[id] = status.rawValue
            sessions.observe(session: id, status, inARow: inARow, now: now)
        }
        log.write("agents", ["result": agentsCheck, "seen": logged])
        refresh()
    }

    private func load() {
        guard let data = FileManager.default.contents(atPath: savedPath) else { return }
        do {
            sessions = try JSONDecoder().decode(Sessions.self, from: data)
            savedData = data
            log.write("loaded", ["sessions": sessions.table.count])
        } catch {
            log.write("load_failed", ["error": String(describing: error)])
        }
    }

    private func save() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(sessions), data != savedData else { return }
        let written = FileManager.default.createFile(atPath: savedPath + ".new", contents: data,
                                                     attributes: [.posixPermissions: 0o600])
        guard written, rename(savedPath + ".new", savedPath) == 0 else {
            log.write("save_failed", ["errno": Int(errno)])
            return
        }
        savedData = data
    }

    private func status() -> [String: Any] {
        let now = Date().timeIntervalSince1970
        let table = Rows.table(sessions)
        return [
            "ok": true,
            "pid": Int(getpid()),
            "socket": socketPath,
            "bluetooth": bluetooth,
            "authorization": Bridge.name(CBManager.authorization),
            "card": link,
            "card_version": cardVersion ?? NSNull(),
            "bridge_version": linkVersion,
            "agents_check": agentsCheck,
            "waits": waits.map { ["id": $0.id, "session": $0.session] },
            "sessions": sessions.table.mapValues { session -> [String: Any] in
                ["state": session.shown.name, "project": session.project ?? NSNull(),
                 "project_thread": session.isProjectThread, "row": session.row,
                 "visible": session.visible, "waiting": session.waiting.count, "pid": session.pid ?? NSNull()]
            },
            "total": table.total,
            "rows": table.lines.map { line -> [String: Any] in
                ["row": line.row, "state": line.state.name, "minutes": line.minutes(at: now), "project": line.project,
                 "project_thread": line.projectThread]
            },
            "synced": link == "connected" && ackedKey == table.key,
            "frame": Rows.hex(Rows.frame(table, fresh: [], nameLimit: nameLimit, now: now)),
            "card_frame": cardFrame.map(Rows.hex) ?? NSNull(),
            "max_write": maxWrite ?? NSNull(),
            "name_limit": nameLimit,
            "events": counts["events", default: 0],
            "rejected": counts["rejected", default: 0],
            "writes": counts["writes", default: 0],
            "acks": counts["acks", default: 0],
            "heartbeats": counts["heartbeats", default: 0],
            "connects": counts["connects", default: 0],
        ]
    }

    private func push() {
        let table = Rows.table(sessions)
        guard table.key != ackedKey else { return }
        let fresh = Set(pendingFresh.keys)
        let data = Rows.frame(table, fresh: fresh, nameLimit: nameLimit, now: Date().timeIntervalSince1970)
        write(RowsWrite(what: "rows", data: data, key: table.key, fresh: pendingFresh))
    }

    private func heartbeat() {
        guard inFlight == nil else { return }
        write(RowsWrite(what: "heartbeat", data: Rows.heartbeat(total: Rows.table(sessions).total), key: nil,
                        fresh: [:]))
    }

    private func write(_ request: RowsWrite) {
        guard link == "connected", let peripheral, inFlight == nil,
              let characteristic = characteristics[rowsUUID] else { return }
        inFlight = request
        counts["writes", default: 0] += 1
        log.write("write", ["what": request.what, "frame": Rows.hex(request.data)])
        peripheral.writeValue(request.data, for: characteristic, type: .withResponse)
    }

    private func written(_ request: RowsWrite, error: Error?) {
        inFlight = nil
        if let error {
            log.write("write_error", ["what": request.what, "frame": Rows.hex(request.data),
                                      "error": String(describing: error)])
            queue.asyncAfter(deadline: .now() + Bridge.writeRetrySeconds) { self.push() }
            return
        }
        counts["acks", default: 0] += 1
        log.write("ack", ["what": request.what, "frame": Rows.hex(request.data)])
        if let key = request.key {
            ackedKey = key
            cardFrame = request.data
            for (id, generation) in request.fresh where pendingFresh[id] == generation {
                pendingFresh[id] = nil
            }
        } else {
            counts["heartbeats", default: 0] += 1
        }
        push()
    }

    private func forgetCard() {
        characteristics = [:]
        ackedKey = nil
        cardFrame = nil
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
        if let quitting {
            forgetCard()
            log.write(what, ["error": error.map { String(describing: $0) } ?? NSNull(), "reconnect": false])
            quitting.signal()
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
        pairingFailures = 0
        if let connected = central.retrieveConnectedPeripherals(withServices: [serviceUUID]).first {
            peripheral = connected
            connected.delegate = self
            link = "connecting"
            log.write("retrieved", ["name": connected.name ?? NSNull()])
            central.connect(connected)
            return
        }
        link = "scanning"
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
        peripheral.discoverCharacteristics([rowsUUID, versionUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didModifyServices invalidatedServices: [CBService]) {
        guard isCurrent(peripheral), invalidatedServices.contains(where: { $0.uuid == serviceUUID }) else { return }
        log.write("services_changed")
        forgetCard()
        link = "discovering"
        peripheral.discoverServices([serviceUUID])
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard isCurrent(peripheral) else { return }
        var found: [CBUUID: CBCharacteristic] = [:]
        for characteristic in service.characteristics ?? []
        where characteristic.uuid == rowsUUID || characteristic.uuid == versionUUID {
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
        guard characteristics[rowsUUID] != nil else {
            log.write("characteristic_missing", ["found": characteristics.keys.map(\.uuidString).sorted()])
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        let limit = peripheral.maximumWriteValueLength(for: .withResponse)
        maxWrite = limit
        nameLimit = Rows.nameLimit(forMaxWrite: limit)
        link = "connected"
        pairingFailures = 0
        pendingFresh.removeAll()
        log.write("ready", ["max_write": limit, "name_limit": nameLimit])
        push()
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if isCurrent(peripheral), characteristic.uuid == versionUUID {
            versionWritten(of: peripheral, error: error)
            return
        }
        guard isCurrent(peripheral), let request = inFlight, characteristic.uuid == rowsUUID else { return }
        written(request, error: error)
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
