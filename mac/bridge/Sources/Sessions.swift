enum Activity: String {
    case idle, working, done, error
}

struct Session {
    var activity: Activity = .idle
    var pending: Set<String> = []

    var shown: String { pending.isEmpty ? activity.rawValue : "waiting" }
}

struct HookEvent {
    let name: String
    let session: String
    let toolKey: String?
}

struct Sessions {
    static let urgency = ["waiting", "error", "done", "working", "idle"]

    private(set) var table: [String: Session] = [:]

    mutating func apply(_ event: HookEvent) {
        if event.name == "SessionEnd" {
            table[event.session] = nil
            return
        }
        var session = table[event.session] ?? Session()
        switch event.name {
        case "UserPromptSubmit":
            session.activity = .working
            session.pending.removeAll()
        case "PreToolUse":
            session.activity = .working
        case "PermissionRequest":
            session.activity = .working
            if let key = event.toolKey { session.pending.insert(key) }
        case "PostToolUse", "PostToolUseFailure":
            session.activity = .working
            if let key = event.toolKey { session.pending.remove(key) }
        case "Stop":
            session.activity = .done
            session.pending.removeAll()
        case "StopFailure":
            session.activity = .error
            session.pending.removeAll()
        default:
            break
        }
        table[event.session] = session
    }

    var shown: String {
        let states = Set(table.values.map(\.shown))
        return Sessions.urgency.first(where: states.contains) ?? "idle"
    }
}
