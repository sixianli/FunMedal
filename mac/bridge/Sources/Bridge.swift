import CoreBluetooth
import Darwin
import Foundation
import Speech

let serviceUUID = CBUUID(string: "6D2F1001-6B0E-4A8E-9A4B-46756E4D6564")
let stateUUID = CBUUID(string: "6D2F1002-6B0E-4A8E-9A4B-46756E4D6564")
let promptUUID = CBUUID(string: "6D2F1003-6B0E-4A8E-9A4B-46756E4D6564")
let decisionUUID = CBUUID(string: "6D2F1004-6B0E-4A8E-9A4B-46756E4D6564")
let audioUUID = CBUUID(string: "6D2F1005-6B0E-4A8E-9A4B-46756E4D6564")
let voiceUUID = CBUUID(string: "6D2F1006-6B0E-4A8E-9A4B-46756E4D6564")
let voiceImageUUID = CBUUID(string: "6D2F1007-6B0E-4A8E-9A4B-46756E4D6564")
let cardCharacteristics = [stateUUID, promptUUID, decisionUUID, audioUUID, voiceUUID, voiceImageUUID]

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

final class Approval {
    let id: Int
    let session: String
    let toolKey: String?
    let tool: String
    let summary: String
    let fd: Int32
    var outcome: String?

    init(id: Int, session: String, toolKey: String?, tool: String, summary: String, fd: Int32) {
        self.id = id
        self.session = session
        self.toolKey = toolKey
        self.tool = tool
        self.summary = summary
        self.fd = fd
    }
}

final class Listener {
    let pid: Int
    let fd: Int32
    let reportsResults: Bool

    init(pid: Int, fd: Int32, reportsResults: Bool) {
        self.pid = pid
        self.fd = fd
        self.reportsResults = reportsResults
    }
}

struct CardWrite {
    let uuid: CBUUID
    let data: Data
    let what: [String: Any]
    var retried = false
}

struct Handled {
    var reply: [String: Any]?
    var approval: Approval?
    var listener: Listener?
    var quit = false
}

final class Bridge: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let writeRetrySeconds = 1.0
    static let toolLimit = 31
    static let summaryLimit = 240
    static let countLimit = 999
    static let imageChunkHeader = 3
    static let attributeLimit = 512
    static let releasingEvents = ["UserPromptSubmit": "prompt", "Stop": "stop", "StopFailure": "stop_failure",
                                  "SessionEnd": "session_end"]
    static let attentionEvents: Set<String> = ["SessionStart", "UserPromptSubmit", "PermissionRequest", "Stop",
                                               "StopFailure"]
    static let activityLimit = 32
    static let saidResults: Set<String> = ["submitted", "cleared", "stopped", "refused"]

    let queue = DispatchQueue(label: "funmedal.bridge")
    let socketPath: String
    let activityPath: String
    let log: EventLog
    let locale: String
    private var central: CBCentralManager?
    private var peripheral: CBPeripheral?
    private var characteristics: [CBUUID: CBCharacteristic] = [:]
    private var link = "starting"
    private var bluetooth = "unknown"
    private var sessions = Sessions()
    private var desired = "idle"
    private var cardState: String?
    private var approvals: [Approval] = []
    private var nextApprovalID = 1
    private var desiredPrompt = ""
    private var cardPrompt: String?
    private var inFlight: (uuid: CBUUID, value: String)?
    private var counts: [String: Int] = [:]
    private var listeners: [Listener] = []
    private var waiting: [UInt8: Listener] = [:]
    private var lastActive: [Int: Date] = [:]
    private var transcription: Transcription?
    private var voiceWrites: [CardWrite] = []
    private lazy var recognizer: SFSpeechRecognizer? = {
        let recognizer = SFSpeechRecognizer(locale: Locale(identifier: locale))
        let results = OperationQueue()
        results.underlyingQueue = queue
        results.maxConcurrentOperationCount = 1
        recognizer?.queue = results
        return recognizer
    }()

    init(socketPath: String, log: EventLog, locale: String) {
        self.socketPath = socketPath
        activityPath = Bridge.activityPath(for: socketPath)
        self.log = log
        self.locale = locale
        lastActive = Bridge.loadActivity(activityPath, log: log)
    }

    static func activityPath(for socket: String) -> String {
        (socket as NSString).deletingPathExtension + ".activity.json"
    }

    private static func loadActivity(_ path: String, log: EventLog) -> [Int: Date] {
        guard let data = FileManager.default.contents(atPath: path) else { return [:] }
        guard let table = (try? JSONSerialization.jsonObject(with: data)) as? [String: Double] else {
            log.write("activity_unreadable")
            return [:]
        }
        var activity: [Int: Date] = [:]
        for (pid, time) in table {
            if let pid = Int(pid) { activity[pid] = Date(timeIntervalSince1970: time) }
        }
        log.write("activity_loaded", ["processes": activity.count])
        return activity
    }

    private func noteActivity(_ pid: Int) {
        lastActive[pid] = Date()
        if lastActive.count > Bridge.activityLimit, let oldest = lastActive.min(by: { $0.value < $1.value })?.key {
            lastActive.removeValue(forKey: oldest)
        }
        var table: [String: Double] = [:]
        for (pid, time) in lastActive { table[String(pid)] = time.timeIntervalSince1970 }
        let data = try? JSONSerialization.data(withJSONObject: table, options: [.sortedKeys])
        if data == nil || !FileManager.default.createFile(atPath: activityPath, contents: data,
                                                          attributes: [.posixPermissions: 0o600]) {
            log.write("activity_unsaved")
        }
    }

    func start() {
        queue.async {
            self.central = CBCentralManager(delegate: self, queue: self.queue)
        }
    }

    func handle(_ data: Data?, client: Int32) -> Handled {
        queue.sync {
            guard let data else { return Handled(reply: reject("message too long"), approval: nil, quit: false) }
            guard let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let op = message["op"] as? String else {
                return Handled(reply: reject("not a JSON object with an op"), approval: nil, quit: false)
            }
            switch op {
            case "event":
                return Handled(reply: receive(message), approval: nil, quit: false)
            case "approve":
                return approve(message, client: client)
            case "listen":
                return listen(message, client: client)
            case "said":
                return Handled(reply: said(message), approval: nil, quit: false)
            case "status":
                return Handled(reply: status(), approval: nil, quit: false)
            case "quit":
                log.write("quit")
                return Handled(reply: ["ok": true], approval: nil, quit: true)
            default:
                return Handled(reply: reject("unknown op"), approval: nil, quit: false)
            }
        }
    }

    func gaveUp(_ approval: Approval) {
        queue.sync {
            guard approval.outcome == nil else { return }
            finish(approval, as: "gave_up", decision: nil)
            refresh()
        }
    }

    func unlisten(_ listener: Listener) {
        queue.sync {
            listeners.removeAll { $0 === listener }
            log.write("unlisten", ["pid": listener.pid])
            for clip in waiting.filter({ $0.value === listener }).keys.sorted() {
                waiting[clip] = nil
                follow(clip, "no_session", pid: listener.pid)
            }
        }
    }

    func ended(_ approval: Approval) {
        queue.sync {
            if approval.outcome == nil {
                approval.outcome = "hung_up"
                approvals.removeAll { $0 === approval }
                sessions.rejectedInTerminal(session: approval.session, toolKey: approval.toolKey)
                refresh()
            }
            log.write("approval_closed", ["id": approval.id, "how": approval.outcome ?? "hung_up"])
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

    private static func clean(_ text: String, limit: Int) -> String {
        let flat = text.replacingOccurrences(of: "\t", with: " ")
        return Hook.truncated(flat, bytes: limit)
    }

    private func record(_ message: [String: Any], name: String, session: String) {
        counts["events", default: 0] += 1
        var fields: [String: Any] = ["event": name, "session": session]
        for key in ["tool_name", "tool_key", "source", "reason", "error"] {
            if let value = message[key] as? String { fields[key] = value }
        }
        log.write("event", fields)
        if Bridge.attentionEvents.contains(name), let pid = message["claude_pid"] as? Int { noteActivity(pid) }
        let toolKey = message["tool_key"] as? String
        let followsCardAllow = sessions.apply(HookEvent(name: name, session: session, toolKey: toolKey))
        if !followsCardAllow, name == "PostToolUse" || name == "PostToolUseFailure",
           let approval = approvals.first(where: { $0.session == session && $0.toolKey == toolKey }) {
            answer(approval, nil, why: "post_tool_use")
        }
        if let why = Bridge.releasingEvents[name] {
            for approval in approvals.filter({ $0.session == session }) {
                answer(approval, nil, why: why)
            }
        }
    }

    private func receive(_ message: [String: Any]) -> [String: Any] {
        guard let event = Bridge.eventIdentity(message) else { return reject("event needs event and session") }
        record(message, name: event.name, session: event.session)
        refresh()
        return ["ok": true]
    }

    private func approve(_ message: [String: Any], client: Int32) -> Handled {
        guard let event = Bridge.eventIdentity(message), event.name == "PermissionRequest" else {
            return Handled(reply: reject("approve needs a PermissionRequest with a session"), approval: nil,
                           quit: false)
        }
        record(message, name: event.name, session: event.session)
        let tool = Bridge.clean(message["tool_name"] as? String ?? "", limit: Bridge.toolLimit)
            .replacingOccurrences(of: "\n", with: " ")
        let approval = Approval(id: nextApprovalID, session: event.session, toolKey: message["tool_key"] as? String,
                                tool: tool.isEmpty ? "?" : tool,
                                summary: Bridge.clean(message["summary"] as? String ?? "", limit: Bridge.summaryLimit),
                                fd: client)
        nextApprovalID += 1
        approvals.append(approval)
        log.write("approval", ["id": approval.id, "session": approval.session, "tool": approval.tool])
        send(approval, ["ok": true, "id": approval.id])
        refresh()
        return Handled(reply: nil, approval: approval, quit: false)
    }

    private func listen(_ message: [String: Any], client: Int32) -> Handled {
        guard let pid = message["claude_pid"] as? Int, pid > 0 else {
            return Handled(reply: reject("listen needs a claude_pid"))
        }
        let listener = Listener(pid: pid, fd: client, reportsResults: message["reports_results"] as? Bool == true)
        listeners.append(listener)
        log.write("listen", ["pid": pid, "reports_results": listener.reportsResults])
        if let data = Hook.line(["ok": true]) { _ = UnixSocket.writeAll(client, data) }
        return Handled(reply: nil, listener: listener)
    }

    private func said(_ message: [String: Any]) -> [String: Any] {
        guard let number = message["clip"] as? Int, let clip = UInt8(exactly: number),
              let result = message["result"] as? String, Bridge.saidResults.contains(result) else {
            return reject("said needs a clip and one of \(Bridge.saidResults.sorted().joined(separator: ", "))")
        }
        guard let listener = waiting.removeValue(forKey: clip) else {
            return reject("clip \(clip) is not waiting for a result")
        }
        follow(clip, result, pid: listener.pid)
        return ["ok": true]
    }

    private func follow(_ clip: UInt8, _ result: String, pid: Int) {
        voiceWrites.append(CardWrite(uuid: voiceUUID, data: Data("\(clip)\t\(result)".utf8),
                                     what: ["what": "voice", "clip": Int(clip), "result": result]))
        log.write("voice_followed", ["clip": Int(clip), "result": result, "pid": pid])
        push()
    }

    private func target() -> Listener? {
        listeners.max { (lastActive[$0.pid] ?? .distantPast) < (lastActive[$1.pid] ?? .distantPast) }
    }

    private func beginClip(_ clip: UInt8) {
        transcription?.cancel()
        if SFSpeechRecognizer.authorizationStatus() == .notDetermined {
            SFSpeechRecognizer.requestAuthorization { _ in }
        }
        let started = Transcription(clip: clip, recognizer: recognizer) { [weak self] update in
            self?.advance(update)
        }
        transcription = started
        log.write("voice_start", ["clip": Int(clip), "speech": Voice.name(SFSpeechRecognizer.authorizationStatus()),
                                  "ready": !started.denied])
    }

    private func endClip(_ current: Transcription, count: Int, reason: String) {
        current.end(count: count, reason: reason)
        log.write("voice_end", ["clip": Int(current.clip), "packets": current.packets, "missing": current.missing,
                                "reason": reason])
        if current.denied {
            conclude(current, "denied")
        } else if listeners.isEmpty {
            conclude(current, "no_session")
        } else {
            queue.asyncAfter(deadline: .now() + Transcription.finalWaitSeconds) { [weak self] in
                guard let self, current === self.transcription, !current.concluded else { return }
                self.deliver(current)
            }
            advance(current)
        }
    }

    private func hear(_ data: Data) {
        let bytes = [UInt8](data)
        guard bytes.count >= 2 else {
            log.write("voice_bad", ["bytes": bytes.count])
            return
        }
        let clip = bytes[1]
        switch bytes[0] {
        case 1 where bytes.count == 2:
            beginClip(clip)
        case 2 where bytes.count == Adpcm.packetSize:
            guard let current = transcription, current.clip == clip, let samples = Adpcm.decode(bytes) else { return }
            current.receive(sequence: Int(bytes[2]) | Int(bytes[3]) << 8, samples: samples)
        case 3 where bytes.count == 5:
            guard let current = transcription, current.clip == clip, !current.ended else { return }
            endClip(current, count: Int(bytes[2]) | Int(bytes[3]) << 8, reason: bytes[4] == 1 ? "limit" : "release")
        default:
            log.write("voice_bad", ["bytes": bytes.count])
        }
    }

    private func advance(_ current: Transcription) {
        guard current === transcription, current.ended, current.recognized, !current.concluded else { return }
        deliver(current)
    }

    private func deliver(_ current: Transcription) {
        let text = current.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            conclude(current, "empty")
            return
        }
        guard let listener = target(), let line = Hook.line(["say": text, "clip": Int(current.clip)]),
              UnixSocket.writeAll(listener.fd, line) else {
            conclude(current, "no_session", ["chars": text.count])
            return
        }
        var fields: [String: Any] = ["chars": text.count, "pid": listener.pid]
        if let image = TextImage.render(text) {
            queueImage(image.pixels, clip: current.clip)
            fields["ink"] = image.ink
        }
        if listener.reportsResults { waiting[current.clip] = listener }
        conclude(current, "queued", fields)
    }

    private func queueImage(_ pixels: Data, clip: UInt8) {
        guard let peripheral else { return }
        let room = min(peripheral.maximumWriteValueLength(for: .withResponse), Bridge.attributeLimit)
            - Bridge.imageChunkHeader
        guard room > 0 else { return }
        var offset = 0
        while offset < pixels.count {
            let end = min(offset + room, pixels.count)
            var chunk = Data([clip, UInt8(offset & 0xFF), UInt8(offset >> 8)])
            chunk.append(pixels[offset..<end])
            voiceWrites.append(CardWrite(uuid: voiceImageUUID, data: chunk,
                                         what: ["what": "voice_image", "clip": Int(clip), "offset": offset]))
            offset = end
        }
    }

    private func keepFailedVoiceWrite() -> Bool {
        guard let failed = voiceWrites.first else { return false }
        if failed.uuid == voiceUUID, !failed.retried {
            voiceWrites[0].retried = true
            return true
        }
        voiceWrites.removeFirst()
        if failed.uuid == voiceImageUUID, let clip = failed.data.first {
            voiceWrites.removeAll { $0.uuid == voiceImageUUID && $0.data.first == clip }
        }
        return false
    }

    private func conclude(_ current: Transcription, _ result: String, _ extra: [String: Any] = [:]) {
        current.concluded = true
        current.cancel()
        if current === transcription { transcription = nil }
        voiceWrites.append(CardWrite(uuid: voiceUUID, data: Data("\(current.clip)\t\(result)".utf8),
                                     what: ["what": "voice", "clip": Int(current.clip), "result": result]))
        var fields = extra
        fields["clip"] = Int(current.clip)
        fields["result"] = result
        fields["packets"] = current.packets
        fields["missing"] = current.missing
        counts["voice", default: 0] += 1
        log.write("voice_result", fields)
        push()
    }

    private func send(_ approval: Approval, _ object: [String: Any]) {
        if let data = Hook.line(object) { _ = UnixSocket.writeAll(approval.fd, data) }
    }

    private func finish(_ approval: Approval, as outcome: String, decision: String?) {
        approval.outcome = outcome
        approvals.removeAll { $0 === approval }
        send(approval, ["decision": decision ?? NSNull()])
        shutdown(approval.fd, SHUT_WR)
    }

    private func answer(_ approval: Approval, _ decision: String?, why: String) {
        guard approval.outcome == nil else { return }
        finish(approval, as: "answered", decision: decision)
        log.write("answer", ["id": approval.id, "decision": decision ?? "released", "why": why])
    }

    private var cardPromptID: Int? {
        guard let cardPrompt, let first = cardPrompt.split(separator: "\t").first else { return nil }
        return Int(first)
    }

    private func refresh() {
        let shown = sessions.shown
        if shown != desired {
            desired = shown
            log.write("state", ["desired": shown])
        }
        var prompt = ""
        if let head = approvals.first {
            prompt = "\(head.id)\t1\t\(min(approvals.count, Bridge.countLimit))\t\(head.tool)\t\(head.summary)"
        }
        if prompt != desiredPrompt {
            desiredPrompt = prompt
            log.write("prompt", ["id": approvals.first?.id ?? NSNull(), "count": approvals.count])
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
            "prompt": cardPromptID ?? NSNull(),
            "approvals": approvals.map { ["id": $0.id, "session": $0.session, "tool": $0.tool] },
            "sessions": sessions.table.mapValues { ["state": $0.shown, "pending": $0.pendingCount] },
            "events": counts["events", default: 0],
            "rejected": counts["rejected", default: 0],
            "writes": counts["writes", default: 0],
            "acks": counts["acks", default: 0],
            "connects": counts["connects", default: 0],
            "channels": listeners.map(\.pid),
            "waiting": waiting.keys.sorted().map { Int($0) },
            "speech": Voice.name(SFSpeechRecognizer.authorizationStatus()),
            "voice": counts["voice", default: 0],
        ]
    }

    private func push() {
        guard link == "connected", let peripheral, inFlight == nil else { return }
        if cardState != desired, let characteristic = characteristics[stateUUID] {
            write(desired, to: characteristic, of: peripheral)
        } else if cardPrompt != desiredPrompt, let characteristic = characteristics[promptUUID] {
            write(desiredPrompt, to: characteristic, of: peripheral)
        } else if let next = voiceWrites.first, let characteristic = characteristics[next.uuid] {
            inFlight = (next.uuid, "")
            counts["writes", default: 0] += 1
            log.write("write", next.what)
            peripheral.writeValue(next.data, for: characteristic, type: .withResponse)
        }
    }

    private func describe(_ uuid: CBUUID, _ value: String) -> [String: Any] {
        if uuid == stateUUID { return ["what": "state", "value": value] }
        if uuid == promptUUID {
            return ["what": "prompt", "id": value.split(separator: "\t").first.map(String.init) ?? "none"]
        }
        return voiceWrites.first?.what ?? ["what": "voice"]
    }

    private func write(_ value: String, to characteristic: CBCharacteristic, of peripheral: CBPeripheral) {
        inFlight = (characteristic.uuid, value)
        counts["writes", default: 0] += 1
        log.write("write", describe(characteristic.uuid, value))
        peripheral.writeValue(Data(value.utf8), for: characteristic, type: .withResponse)
    }

    private func forgetCard() {
        characteristics = [:]
        cardState = nil
        cardPrompt = nil
        inFlight = nil
        voiceWrites.removeAll()
        transcription?.cancel()
        transcription = nil
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
        peripheral.discoverCharacteristics(cardCharacteristics, for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard isCurrent(peripheral) else { return }
        var found: [CBUUID: CBCharacteristic] = [:]
        for characteristic in service.characteristics ?? [] where cardCharacteristics.contains(characteristic.uuid) {
            found[characteristic.uuid] = characteristic
        }
        guard found.count == cardCharacteristics.count, let decision = found[decisionUUID],
              let audio = found[audioUUID] else {
            log.write("characteristic_missing", ["found": found.keys.map(\.uuidString).sorted(),
                                                 "error": error.map { String(describing: $0) } ?? NSNull()])
            central?.cancelPeripheralConnection(peripheral)
            return
        }
        forgetCard()
        characteristics = found
        link = "connected"
        log.write("ready")
        peripheral.setNotifyValue(true, for: decision)
        peripheral.setNotifyValue(true, for: audio)
        push()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic,
                    error: Error?) {
        guard isCurrent(peripheral) else { return }
        log.write("subscribed", ["what": characteristic.uuid == audioUUID ? "audio" : "decision",
                                 "notifying": characteristic.isNotifying,
                                 "error": error.map { String(describing: $0) } ?? NSNull()])
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard isCurrent(peripheral), let flight = inFlight, characteristic.uuid == flight.uuid else { return }
        inFlight = nil
        if let error {
            var fields = describe(flight.uuid, flight.value)
            fields["error"] = String(describing: error)
            log.write("write_error", fields)
            if flight.uuid == stateUUID {
                cardState = nil
            } else if flight.uuid == promptUUID {
                cardPrompt = nil
            } else if !keepFailedVoiceWrite() {
                push()
                return
            }
            queue.asyncAfter(deadline: .now() + Bridge.writeRetrySeconds) { self.push() }
            return
        }
        let acked = describe(flight.uuid, flight.value)
        if flight.uuid == stateUUID {
            cardState = flight.value
        } else if flight.uuid == promptUUID {
            cardPrompt = flight.value
        } else if !voiceWrites.isEmpty {
            voiceWrites.removeFirst()
        }
        counts["acks", default: 0] += 1
        log.write("ack", acked)
        push()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard isCurrent(peripheral), error == nil, let data = characteristic.value else { return }
        if characteristic.uuid == audioUUID {
            hear(data)
            return
        }
        guard characteristic.uuid == decisionUUID, let text = String(data: data, encoding: .utf8) else { return }
        let parts = text.split(separator: " ").map(String.init)
        guard parts.count == 2, let id = Int(parts[0]), parts[1] == "allow" || parts[1] == "deny" else {
            log.write("decision_bad", ["bytes": data.count])
            return
        }
        guard let head = approvals.first, head.id == id, cardPromptID == id else {
            log.write("decision_ignored", ["id": id])
            return
        }
        sessions.answeredOnCard(session: head.session, toolKey: head.toolKey, allowed: parts[1] == "allow")
        answer(head, parts[1], why: "card")
        refresh()
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
