import Foundation

enum Activity: String, Codable {
    case running, done, error
}

enum RowState: UInt8 {
    case waiting = 1, error = 2, running = 3, done = 4

    var name: String {
        switch self {
        case .waiting: return "waiting"
        case .error: return "error"
        case .running: return "running"
        case .done: return "done"
        }
    }

    var alerts: Bool { self == .waiting || self == .error }
}

enum WaitingKind: String, Codable {
    case permission, plan, notification
}

struct WaitingRecord: Codable {
    var kind: WaitingKind
    var key: String?
    var hook: Int?

    enum CodingKeys: String, CodingKey { case kind, key }
}

struct Session: Codable {
    var row = 0
    var project: String?
    var activity = Activity.running
    var waiting: [WaitingRecord] = []
    var since: Double
    var pid: Int?
    var visible = false

    var shown: RowState {
        guard waiting.isEmpty else { return .waiting }
        switch activity {
        case .running: return .running
        case .done: return .done
        case .error: return .error
        }
    }

    var hooks: [Int] { waiting.compactMap(\.hook) }
}

struct HookEvent {
    let name: String
    let session: String
    var toolName: String?
    var toolKey: String?
    var toolUseID: String?
    var notificationType: String?
    var project: String?
    var pid: Int?
    var hook: Int?
    var now: Double
}

struct Sessions: Codable {
    static let rowIDs = 1...255
    static let planTool = "ExitPlanMode"
    static let permissionPrompt = "permission_prompt"

    private(set) var table: [String: Session] = [:]
    private var nextRow = rowIDs.lowerBound
    private(set) var entered: [String] = []

    enum CodingKeys: String, CodingKey {
        case table = "sessions"
        case nextRow = "next_row"
    }

    mutating func apply(_ event: HookEvent) -> [Int] {
        if event.name == "SessionEnd" {
            return remove(event.session)
        }
        let existing = table[event.session]
        if existing == nil {
            table[event.session] = Session(since: event.now)
        }
        let newRow = event.name == "UserPromptSubmit" && existing?.visible != true ? allocateRow() : nil
        return change(event.session, now: event.now) { session in
            if let project = event.project { session.project = project }
            if let pid = event.pid { session.pid = pid }
            return Sessions.effect(of: event, on: &session, newRow: newRow)
        }
    }

    private static func effect(of event: HookEvent, on session: inout Session, newRow: Int?) -> [Int] {
        switch event.name {
        case "UserPromptSubmit":
            if let newRow {
                session.visible = true
                session.row = newRow
            }
            session.activity = .running
            return clearWaiting(&session)
        case "PreToolUse":
            session.activity = .running
            if event.toolName == planTool, let id = event.toolUseID {
                session.waiting.append(WaitingRecord(kind: .plan, key: id))
            }
        case "PermissionRequest":
            session.waiting.append(WaitingRecord(kind: .permission, key: event.toolKey, hook: event.hook))
        case "Notification":
            if event.notificationType == permissionPrompt, !session.waiting.contains(where: { $0.kind == .notification }) {
                session.waiting.append(WaitingRecord(kind: .notification))
            }
        case "PostToolUse", "PostToolUseFailure":
            session.activity = .running
            return toolFinished(event, &session)
        case "Stop":
            session.activity = .done
            return clearWaiting(&session)
        case "StopFailure":
            session.activity = .error
            return clearWaiting(&session)
        default:
            break
        }
        return []
    }

    private static func clearWaiting(_ session: inout Session) -> [Int] {
        let hooks = session.hooks
        session.waiting.removeAll()
        return hooks
    }

    private static func toolFinished(_ event: HookEvent, _ session: inout Session) -> [Int] {
        var released: [Int] = []
        if let id = event.toolUseID {
            session.waiting.removeAll { $0.kind == .plan && $0.key == id }
        }
        if let key = event.toolKey,
           let index = session.waiting.firstIndex(where: { $0.kind == .permission && $0.key == key }) {
            if let hook = session.waiting[index].hook { released.append(hook) }
            session.waiting.remove(at: index)
        }
        session.waiting.removeAll { $0.kind == .notification }
        return released
    }

    mutating func hungUp(session id: String, hook: Int) {
        _ = change(id, now: Date().timeIntervalSince1970) { session in
            session.waiting.removeAll { $0.hook == hook || $0.kind == .notification }
            return []
        }
    }

    mutating func gaveUp(session id: String, hook: Int) {
        guard var session = table[id], let index = session.waiting.firstIndex(where: { $0.hook == hook }) else { return }
        session.waiting[index].hook = nil
        table[id] = session
    }

    mutating func remove(_ id: String) -> [Int] {
        guard let session = table.removeValue(forKey: id) else { return [] }
        entered.removeAll { $0 == id }
        return session.hooks
    }

    mutating func takeEntered() -> [String] {
        defer { entered.removeAll() }
        return entered
    }

    private mutating func change(_ id: String, now: Double, _ body: (inout Session) -> [Int]) -> [Int] {
        guard var session = table[id] else { return [] }
        let before = session.shown
        let wasVisible = session.visible
        let released = body(&session)
        if session.shown != before {
            session.since = now
            if session.visible, wasVisible, session.shown.alerts { entered.append(id) }
        }
        table[id] = session
        return released
    }

    private static func row(after row: Int) -> Int {
        row == rowIDs.upperBound ? rowIDs.lowerBound : row + 1
    }

    private mutating func allocateRow() -> Int {
        let used = Set(table.values.filter(\.visible).map(\.row))
        var candidate = nextRow
        var tried = 1
        while used.contains(candidate), tried < Sessions.rowIDs.count {
            candidate = Sessions.row(after: candidate)
            tried += 1
        }
        nextRow = Sessions.row(after: candidate)
        return candidate
    }

    var pids: [(String, Int)] {
        table.compactMap { id, session in session.pid.map { (id, $0) } }
    }

    var visible: [(id: String, session: Session)] {
        table.filter { $0.value.visible }.map { (id: $0.key, session: $0.value) }
    }
}
