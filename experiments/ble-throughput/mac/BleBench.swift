import CoreBluetooth
import Foundation

let serviceUUID = CBUUID(string: "6D2F0001-6B0E-4A8E-9A4B-46756E4D6564")
let txUUID = CBUUID(string: "6D2F0002-6B0E-4A8E-9A4B-46756E4D6564")
let rxUUID = CBUUID(string: "6D2F0003-6B0E-4A8E-9A4B-46756E4D6564")
let ctrlUUID = CBUUID(string: "6D2F0004-6B0E-4A8E-9A4B-46756E4D6564")

struct Options {
    var seconds: UInt8 = 5
    var rounds = 3
    var echoes = 30
    var intervals: [(UInt8, UInt8)] = [(0, 0), (12, 12)]
    var out: String?
}

func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds }

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

func le32(_ value: UInt32) -> Data { withUnsafeBytes(of: value.littleEndian) { Data($0) } }

func readLE32(_ data: Data, at offset: Int) -> UInt32 {
    var value: UInt32 = 0
    for i in 0..<4 { value |= UInt32(data[data.startIndex + offset + i]) << (8 * i) }
    return value
}

final class Bench: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    let queue = DispatchQueue(label: "funmedal.blebench")
    let cond = NSCondition()
    var central: CBCentralManager!
    var peripheral: CBPeripheral?
    var tx: CBCharacteristic?
    var rx: CBCharacteristic?
    var ctrl: CBCharacteristic?

    var managerState: CBManagerState = .unknown
    var rssi: Int = 0
    var connected = false
    var disconnected = false
    var subscribed = false
    var readValue: Data?
    var writeAcks = 0
    var writeErrors: [String] = []

    var dlBytes = 0
    var dlPackets = 0
    var dlGaps = 0
    var dlNextSeq: UInt32 = 0
    var dlFirstLen = 0
    var dlFirst: UInt64 = 0
    var dlLast: UInt64 = 0
    var summary: Data?
    var echoArrival: [UInt32: UInt64] = [:]

    var upDeadline: UInt64 = 0
    var upChunk = 0
    var upSeq: UInt32 = 0
    var upBytes = 0
    var upStart: UInt64 = 0
    var upEnd: UInt64 = 0
    var upDone = false

    func locked<T>(_ body: () -> T) -> T {
        cond.lock()
        defer { cond.unlock() }
        return body()
    }

    func signal(_ body: () -> Void) {
        cond.lock()
        body()
        cond.broadcast()
        cond.unlock()
    }

    @discardableResult
    func wait(_ seconds: Double, _ predicate: () -> Bool) -> Bool {
        let limit = Date().addingTimeInterval(seconds)
        cond.lock()
        defer { cond.unlock() }
        while !predicate() {
            if !cond.wait(until: limit) { return predicate() }
        }
        return true
    }

    func start() {
        central = CBCentralManager(delegate: self, queue: queue)
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        signal { managerState = central.state }
        if central.state == .poweredOn {
            central.scanForPeripherals(withServices: [serviceUUID], options: nil)
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        central.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        signal { rssi = RSSI.intValue }
        central.connect(peripheral, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        signal { connected = true }
        peripheral.discoverServices([serviceUUID])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        signal { disconnected = true; writeErrors.append("connect: \(String(describing: error))") }
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        signal { disconnected = true }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = peripheral.services?.first(where: { $0.uuid == serviceUUID }) else { return }
        peripheral.discoverCharacteristics([txUUID, rxUUID, ctrlUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        for c in service.characteristics ?? [] {
            if c.uuid == txUUID { tx = c }
            if c.uuid == rxUUID { rx = c }
            if c.uuid == ctrlUUID { ctrl = c }
        }
        if let tx { peripheral.setNotifyValue(true, for: tx) }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic,
                    error: Error?) {
        signal { subscribed = characteristic.isNotifying }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let data = characteristic.value, !data.isEmpty else { return }
        let t = now()
        if characteristic.uuid == ctrlUUID {
            signal { readValue = data }
            return
        }
        switch data[data.startIndex] {
        case UInt8(ascii: "D"):
            cond.lock()
            let seq = data.count >= 5 ? readLE32(data, at: 1) : 0
            if dlPackets == 0 {
                dlFirst = t
                dlFirstLen = data.count
                dlNextSeq = seq
            }
            if seq != dlNextSeq { dlGaps += 1 }
            dlNextSeq = seq &+ 1
            dlPackets += 1
            dlBytes += data.count
            dlLast = t
            cond.unlock()
        case UInt8(ascii: "E"):
            if data.count >= 5 {
                let seq = readLE32(data, at: 1)
                signal { echoArrival[seq] = t }
            }
        case UInt8(ascii: "F"):
            signal { summary = data.dropFirst() }
        default:
            break
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        signal {
            writeAcks += 1
            if let error { writeErrors.append("write \(characteristic.uuid): \(error.localizedDescription)") }
        }
    }

    func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        pumpUplink()
    }

    func pumpUplink() {
        guard let peripheral, let rx else { return }
        var chunk = Data(count: upChunk)
        while peripheral.canSendWriteWithoutResponse {
            let t = now()
            if t >= upDeadline {
                signal { upEnd = t; upDone = true }
                return
            }
            chunk.replaceSubrange(0..<4, with: le32(upSeq))
            peripheral.writeValue(chunk, for: rx, type: .withoutResponse)
            cond.lock()
            if upSeq == 0 { upStart = t }
            upSeq &+= 1
            upBytes += chunk.count
            cond.unlock()
        }
    }

    func writeCtrl(_ bytes: [UInt8]) -> Bool {
        let before = locked { writeAcks }
        queue.async { [self] in peripheral?.writeValue(Data(bytes), for: ctrl!, type: .withResponse) }
        return wait(5) { writeAcks > before }
    }

    func readCtrl() -> [String: Any]? {
        signal { readValue = nil }
        queue.async { [self] in peripheral?.readValue(for: ctrl!) }
        guard wait(5, { readValue != nil }), let data = locked({ readValue }) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func runEcho(count: Int) -> [String: Any] {
        var rtts: [Double] = []
        var lost = 0
        for i in 0..<count {
            let seq = UInt32(i)
            let sent = now()
            var bytes: [UInt8] = [UInt8(ascii: "E")]
            bytes += [UInt8](le32(seq)) + [UInt8](repeating: 0x55, count: 8)
            _ = writeCtrl(bytes)
            if wait(2, { echoArrival[seq] != nil }), let arrived = locked({ echoArrival[seq] }) {
                rtts.append(Double(arrived - sent) / 1e6)
            } else {
                lost += 1
            }
            Thread.sleep(forTimeInterval: 0.037)
        }
        signal { echoArrival.removeAll() }
        let sorted = rtts.sorted()
        func pct(_ p: Double) -> Double { sorted.isEmpty ? 0 : sorted[min(sorted.count - 1, Int(Double(sorted.count) * p))] }
        return ["samples": rtts.count, "lost": lost, "min_ms": sorted.first ?? 0, "median_ms": pct(0.5),
                "p90_ms": pct(0.9), "max_ms": sorted.last ?? 0]
    }

    func runDownlink(seconds: UInt8) -> [String: Any] {
        signal {
            dlBytes = 0; dlPackets = 0; dlGaps = 0; dlFirstLen = 0; dlFirst = 0; dlLast = 0; summary = nil
        }
        guard writeCtrl([UInt8(ascii: "S"), seconds]) else { return ["error": "start write timed out"] }
        guard wait(Double(seconds) + 10, { summary != nil }) else { return ["error": "no summary"] }
        Thread.sleep(forTimeInterval: 0.3)
        return locked {
            let span = Double(dlLast - dlFirst) / 1e9
            let kbps = span > 0 ? Double(dlBytes - dlFirstLen) * 8 / span / 1000 : 0
            let card = summary.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
            return ["mac_bytes": dlBytes, "mac_packets": dlPackets, "mac_seq_gaps": dlGaps, "mac_span_s": span,
                    "mac_kbps": kbps, "card": card]
        }
    }

    func runUplink(seconds: UInt8) -> [String: Any] {
        guard writeCtrl([UInt8(ascii: "Z")]) else { return ["error": "reset write timed out"] }
        let chunk = queue.sync { peripheral!.maximumWriteValueLength(for: .withoutResponse) }
        signal {
            upChunk = chunk; upSeq = 0; upBytes = 0; upStart = 0; upEnd = 0; upDone = false
            upDeadline = now() + UInt64(seconds) * 1_000_000_000
        }
        queue.async { [self] in pumpUplink() }
        guard wait(Double(seconds) + 10, { upDone }) else { return ["error": "uplink did not finish"] }
        Thread.sleep(forTimeInterval: 1.0)
        let card = readCtrl() ?? [:]
        let (sent, packets, macSpan) = locked { (upBytes, Int(upSeq), Double(upEnd - upStart) / 1e9) }
        let cardBytes = (card["rx_bytes"] as? Int) ?? 0
        let firstLen = (card["rx_first_len"] as? Int) ?? 0
        let spanUs = (card["rx_span_us"] as? Int) ?? 0
        let cardKbps = spanUs > 0 ? Double(cardBytes - firstLen) * 8 / Double(spanUs) * 1000 : 0
        return ["chunk": chunk, "mac_sent_bytes": sent, "mac_sent_packets": packets, "mac_span_s": macSpan,
                "card_rx_bytes": cardBytes, "card_rx_packets": card["rx_packets"] ?? 0,
                "card_seq_gaps": card["rx_gaps"] ?? 0, "card_kbps": cardKbps]
    }

    func run(_ options: Options) -> [String: Any] {
        var report: [String: Any] = ["started": ISO8601DateFormatter().string(from: Date()),
                                     "host": ProcessInfo.processInfo.hostName,
                                     "os": ProcessInfo.processInfo.operatingSystemVersionString]
        start()
        guard wait(10, { managerState != .unknown && managerState != .resetting }) else {
            report["error"] = "bluetooth state unknown"; return report
        }
        guard locked({ managerState }) == .poweredOn else {
            report["error"] = "bluetooth state \(locked { managerState.rawValue })"; return report
        }
        log("scanning")
        guard wait(30, { connected || disconnected }), locked({ connected }) else {
            report["error"] = "not connected"; return report
        }
        log("connected rssi \(locked { rssi })")
        guard wait(15, { subscribed }) else { report["error"] = "not subscribed"; return report }
        report["rssi"] = locked { rssi }
        report["max_write_without_response"] = queue.sync { peripheral!.maximumWriteValueLength(for: .withoutResponse) }
        Thread.sleep(forTimeInterval: 2.0)
        report["link_initial"] = readCtrl() ?? [:]
        var configs: [[String: Any]] = []
        for (minItvl, maxItvl) in options.intervals {
            var config: [String: Any] = ["requested_itvl_1250us": [minItvl, maxItvl]]
            if minItvl > 0 {
                config["request_ok"] = writeCtrl([UInt8(ascii: "C"), minItvl, maxItvl])
                Thread.sleep(forTimeInterval: 4.0)
            }
            config["link"] = readCtrl() ?? [:]
            log("config \(minItvl)-\(maxItvl): echo")
            config["echo"] = runEcho(count: options.echoes)
            var downs: [[String: Any]] = []
            var ups: [[String: Any]] = []
            for round in 1...options.rounds {
                log("config \(minItvl)-\(maxItvl): downlink round \(round)")
                downs.append(runDownlink(seconds: options.seconds))
                log("config \(minItvl)-\(maxItvl): uplink round \(round)")
                ups.append(runUplink(seconds: options.seconds))
            }
            config["downlink"] = downs
            config["uplink"] = ups
            config["link_after"] = readCtrl() ?? [:]
            configs.append(config)
        }
        report["configs"] = configs
        report["write_errors"] = locked { writeErrors }
        report["disconnected_during_run"] = locked { disconnected }
        if let peripheral { queue.sync { central.cancelPeripheralConnection(peripheral) } }
        return report
    }
}

var options = Options()
var args = CommandLine.arguments.dropFirst()
while let arg = args.popFirst() {
    switch arg {
    case "--seconds": options.seconds = UInt8(args.popFirst() ?? "5") ?? 5
    case "--rounds": options.rounds = Int(args.popFirst() ?? "3") ?? 3
    case "--echoes": options.echoes = Int(args.popFirst() ?? "30") ?? 30
    case "--out": options.out = args.popFirst()
    default: log("unknown argument \(arg)"); exit(64)
    }
}

let bench = Bench()
let report = bench.run(options)
let json = try! JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
if let out = options.out {
    try! (json + "\n".data(using: .utf8)!).write(to: URL(fileURLWithPath: out))
} else {
    FileHandle.standardOutput.write(json)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}
exit(report["error"] == nil ? 0 : 1)
