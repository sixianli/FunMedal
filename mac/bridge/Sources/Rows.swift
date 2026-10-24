import Foundation

struct RowLine {
    let id: String
    let row: Int
    let state: RowState
    let since: Double
    let project: String

    func minutes(at now: Double) -> Int {
        guard state == .done else { return Rows.hiddenMinutes }
        return min(max(Int((now - since) / 60), 0), Rows.minutesCap)
    }
}

struct RowTable {
    let total: Int
    let lines: [RowLine]

    var key: [String] {
        ["\(total)"] + lines.map { "\($0.row)|\($0.state.rawValue)|\($0.since)|\($0.project)" }
    }
}

enum Rows {
    static let format: UInt8 = 1
    static let shownLimit = 6
    static let countLimit = 255
    static let heartbeatFlag: UInt8 = 0x01
    static let freshBit: UInt8 = 0x80
    static let hiddenMinutes = 65535
    static let minutesCap = 44640
    static let nameLimit = 32
    static let shortNameLimit = 24
    static let fullFrameBytes = 4 + 6 * (5 + nameLimit)

    static func nameLimit(forMaxWrite maxWrite: Int) -> Int {
        maxWrite >= fullFrameBytes ? nameLimit : shortNameLimit
    }

    static func precedes(_ a: RowLine, _ b: RowLine) -> Bool {
        if a.state != b.state { return a.state.rawValue < b.state.rawValue }
        let left = a.project.unicodeScalars.map(\.value)
        let right = b.project.unicodeScalars.map(\.value)
        if left != right { return left.lexicographicallyPrecedes(right) }
        return a.row < b.row
    }

    static func table(_ sessions: Sessions) -> RowTable {
        let lines = sessions.visible.map { entry in
            RowLine(id: entry.id, row: entry.session.row, state: entry.session.shown, since: entry.session.since,
                    project: entry.session.project ?? "")
        }.sorted(by: precedes)
        return RowTable(total: min(lines.count, countLimit), lines: Array(lines.prefix(shownLimit)))
    }

    static func cut(_ name: String, to limit: Int) -> [UInt8] {
        var bytes: [UInt8] = []
        for scalar in name.unicodeScalars {
            let shown = scalar.value < 0x20 || scalar.value == 0x7f ? "?" : String(scalar)
            let encoded = Array(shown.utf8)
            if bytes.count + encoded.count > limit { break }
            bytes += encoded
        }
        return bytes
    }

    static func frame(_ table: RowTable, fresh: Set<String>, nameLimit: Int, now: Double) -> Data {
        var data = Data([format, UInt8(table.total), UInt8(table.lines.count), 0])
        for line in table.lines {
            let minutes = line.minutes(at: now)
            let name = cut(line.project, to: nameLimit)
            let state = line.state.rawValue | (line.state.alerts && fresh.contains(line.id) ? freshBit : 0)
            data += [UInt8(line.row), state, UInt8(minutes & 0xff), UInt8(minutes >> 8), UInt8(name.count)]
            data += name
        }
        return data
    }

    static func heartbeat(total: Int) -> Data {
        Data([format, UInt8(total), 0, heartbeatFlag])
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
