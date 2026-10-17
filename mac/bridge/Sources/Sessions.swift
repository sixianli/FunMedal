enum Activity: String {
    case idle, working, done, error
}

struct Session {
    var activity: Activity = .idle
    var pending: [String: Int] = [:]
    var allowedOnCard: [String: Int] = [:]

    var pendingCount: Int { pending.values.reduce(0, +) }
    var shown: String { pendingCount == 0 ? activity.rawValue : "waiting" }

    static func take(_ key: String?, from counts: inout [String: Int]) -> Bool {
        guard let key, let count = counts[key], count > 0 else { return false }
        counts[key] = count == 1 ? nil : count - 1
        return true
    }

    static func add(_ key: String?, to counts: inout [String: Int]) {
        guard let key else { return }
        counts[key, default: 0] += 1
    }
}

struct HookEvent {
    let name: String
    let session: String
    let toolKey: String?
}

struct Sessions {
    static let urgency = ["waiting", "error", "done", "working", "idle"]

    private(set) var table: [String: Session] = [:]

    mutating func apply(_ event: HookEvent) -> Bool {
        if event.name == "SessionEnd" {
            table[event.session] = nil
            return false
        }
        var session = table[event.session] ?? Session()
        var followsCardAllow = false
        switch event.name {
        case "UserPromptSubmit":
            session.activity = .working
            session.pending.removeAll()
            session.allowedOnCard.removeAll()
        case "PreToolUse":
            session.activity = .working
        case "PermissionRequest":
            session.activity = .working
            Session.add(event.toolKey, to: &session.pending)
        case "PostToolUse", "PostToolUseFailure":
            session.activity = .working
            followsCardAllow = Session.take(event.toolKey, from: &session.allowedOnCard)
            if !followsCardAllow {
                _ = Session.take(event.toolKey, from: &session.pending)
            }
        case "Stop":
            session.activity = .done
            session.pending.removeAll()
            session.allowedOnCard.removeAll()
        case "StopFailure":
            session.activity = .error
            session.pending.removeAll()
            session.allowedOnCard.removeAll()
        default:
            break
        }
        table[event.session] = session
        return followsCardAllow
    }

    mutating func answeredOnCard(session name: String, toolKey: String?, allowed: Bool) {
        guard var session = table[name], Session.take(toolKey, from: &session.pending) else { return }
        if allowed { Session.add(toolKey, to: &session.allowedOnCard) }
        table[name] = session
    }

    mutating func rejectedInTerminal(session name: String, toolKey: String?) {
        guard var session = table[name], Session.take(toolKey, from: &session.pending) else { return }
        if session.pendingCount == 0 { session.activity = .idle }
        table[name] = session
    }

    var shown: String {
        let states = Set(table.values.map(\.shown))
        return Sessions.urgency.first(where: states.contains) ?? "idle"
    }
}
