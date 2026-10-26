import Foundation

struct RowLine {
    let id: String
    let row: Int
    let state: RowState
    let since: Double
    let project: String
    let projectThread: Bool
    var members: [String]

    var count: Int { members.count }

    func minutes(at now: Double) -> Int {
        guard state == .done else { return Rows.hiddenMinutes }
        return min(max(Int((now - since) / 60), 0), Rows.minutesCap)
    }
}

struct RowTable {
    let total: Int
    let urgent: Int
    let lines: [RowLine]

    var key: [String] {
        ["\(total)|\(urgent)"] + lines.map {
            "\($0.row)|\($0.state.rawValue)|\($0.since)|\($0.project)|\($0.projectThread)|\($0.members.joined(separator: ","))"
        }
    }

    var members: Set<String> { Set(lines.flatMap(\.members)) }
}

private struct RowGroup: Hashable {
    let state: RowState
    let project: String
    let projectThread: Bool
}

enum Rows {
    static let format: UInt8 = 2
    static let shownLimit = 6
    static let mergeAbove = 5
    static let countLimit = 255
    static let heartbeatFlag: UInt8 = 0x01
    static let freshBit: UInt8 = 0x80
    static let projectBit: UInt8 = 0x40
    static let hiddenMinutes = 65535
    static let minutesCap = 44640
    static let nameLimit = 32
    static let shortNameLimit = 24
    static let headerBytes = 5
    static let rowHeaderBytes = 6
    static let fullFrameBytes = headerBytes + shownLimit * (rowHeaderBytes + nameLimit)

    static func nameLimit(forMaxWrite maxWrite: Int) -> Int {
        maxWrite >= fullFrameBytes ? nameLimit : shortNameLimit
    }

    static func precedes(_ a: RowLine, _ b: RowLine) -> Bool {
        if a.state != b.state { return a.state.rawValue < b.state.rawValue }
        if a.since != b.since { return a.state == .done ? a.since > b.since : a.since < b.since }
        return a.row < b.row
    }

    static func merged(_ lines: [RowLine]) -> [RowLine] {
        var groups: [RowGroup: Int] = [:]
        var out: [RowLine] = []
        for line in lines {
            guard line.state.alerts else {
                out.append(line)
                continue
            }
            let group = RowGroup(state: line.state, project: line.project, projectThread: line.projectThread)
            if let at = groups[group] {
                out[at].members += line.members
            } else {
                groups[group] = out.count
                out.append(line)
            }
        }
        return out
    }

    static func picked(_ lines: [RowLine], fresh: Set<String>) -> [RowLine] {
        let urgent = lines.filter { $0.state.alerts }
        guard urgent.count > shownLimit else { return Array(lines.prefix(shownLimit)) }
        let flagged = urgent.filter { !fresh.isDisjoint(with: $0.members) }.prefix(shownLimit)
        var kept = Set(flagged.map(\.id))
        for line in urgent where kept.count < shownLimit {
            kept.insert(line.id)
        }
        return urgent.filter { kept.contains($0.id) }
    }

    static func table(_ sessions: Sessions, fresh: Set<String>) -> RowTable {
        let lines = sessions.visible.map { entry in
            RowLine(id: entry.id, row: entry.session.row, state: entry.session.shown, since: entry.session.since,
                    project: entry.session.project ?? "", projectThread: entry.session.isProjectThread,
                    members: [entry.id])
        }.sorted(by: precedes)
        let urgent = lines.filter { $0.state.alerts }.count
        let rows = urgent > mergeAbove ? merged(lines) : lines
        return RowTable(total: min(lines.count, countLimit), urgent: min(urgent, countLimit),
                        lines: picked(rows, fresh: fresh))
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
        var data = Data([format, UInt8(table.total), UInt8(table.lines.count), 0, UInt8(table.urgent)])
        for line in table.lines {
            let minutes = line.minutes(at: now)
            let name = cut(line.project, to: nameLimit)
            let flagged = line.state.alerts && !fresh.isDisjoint(with: line.members)
            let state = line.state.rawValue | (flagged ? freshBit : 0) | (line.projectThread ? projectBit : 0)
            data += [UInt8(line.row), state, UInt8(minutes & 0xff), UInt8(minutes >> 8),
                     UInt8(min(line.count, countLimit)), UInt8(name.count)]
            data += name
        }
        return data
    }

    static func heartbeat(_ table: RowTable) -> Data {
        Data([format, UInt8(table.total), 0, heartbeatFlag, UInt8(table.urgent)])
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
