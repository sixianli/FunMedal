import CryptoKit
import Darwin
import Foundation

enum Hook {
    static let passedThrough = ["source", "reason"]
    static let defaultWaitLimit = 590.0
    static let ackSeconds = 2.0
    static let startWaitSeconds = 3.0
    static let startPollSeconds = 0.05

    static func toolKey(name: Any?, input: Any?) -> String? {
        let canonical: [String: Any] = ["tool_name": name ?? NSNull(), "tool_input": input ?? NSNull()]
        guard let data = try? JSONSerialization.data(withJSONObject: canonical, options: [.sortedKeys]) else {
            return nil
        }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func claudeProcess() -> Int {
        if let named = ProcessInfo.processInfo.environment["CLAUDE_PID"], let pid = Int(named), pid > 0 {
            return pid
        }
        return Int(getppid())
    }

    static func message(from input: Data) -> [String: Any]? {
        guard let hook = (try? JSONSerialization.jsonObject(with: input)) as? [String: Any],
              let name = hook["hook_event_name"] as? String, !name.isEmpty,
              let session = hook["session_id"] as? String, !session.isEmpty else { return nil }
        var message: [String: Any] = ["op": name == "PermissionRequest" ? "wait" : "event", "event": name,
                                      "session": session,
                                      "claude_pid": claudeProcess()]
        if let tool = hook["tool_name"] as? String { message["tool_name"] = tool }
        if hook["tool_name"] != nil || hook["tool_input"] != nil {
            message["tool_key"] = toolKey(name: hook["tool_name"], input: hook["tool_input"])
        }
        for field in passedThrough {
            if let value = hook[field] as? String { message[field] = value }
        }
        if name == "StopFailure", let error = hook["error"] as? String { message["error"] = error }
        return message
    }

    static func line(_ object: [String: Any]) -> Data? {
        guard var data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
            return nil
        }
        data.append(UInt8(ascii: "\n"))
        return data
    }

    static func holdUntilAnswered(socket: String, message: [String: Any], waitLimit: Double) {
        let deadline = Date().addingTimeInterval(waitLimit)
        guard let fd = UnixSocket.connect(socket, timeoutSeconds: 1) else { return }
        defer { close(fd) }
        guard let request = line(message), UnixSocket.writeAll(fd, request) else { return }
        let reader = LineReader(fd: fd, limit: messageLimit)
        guard case .line(let ackData) = reader.next(until: Date().addingTimeInterval(ackSeconds)),
              let ack = (try? JSONSerialization.jsonObject(with: ackData)) as? [String: Any],
              ack["ok"] as? Bool == true else { return }
        guard case .timeout = reader.next(until: deadline), let giveUp = line(["op": "give_up"]) else { return }
        _ = UnixSocket.writeAll(fd, giveUp)
    }

    static func startBridge(socket: String, logPath: String?) {
        let opener = Process()
        opener.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        opener.arguments = ["-n", "-g", Bundle.main.bundlePath, "--args", "serve", "--socket", socket]
            + (logPath.map { ["--log", $0] } ?? [])
        opener.standardInput = FileHandle.nullDevice
        opener.standardOutput = FileHandle.nullDevice
        opener.standardError = FileHandle.nullDevice
        try? opener.run()
    }

    static func connectStarting(socket: String, logPath: String?) -> Int32? {
        if let fd = UnixSocket.connect(socket, timeoutSeconds: 1) { return fd }
        startBridge(socket: socket, logPath: logPath)
        let deadline = Date().addingTimeInterval(startWaitSeconds)
        while Date() < deadline {
            Thread.sleep(forTimeInterval: startPollSeconds)
            if let fd = UnixSocket.connect(socket, timeoutSeconds: 1) { return fd }
        }
        return nil
    }

    static func run(socket: String, waitLimit: Double, logPath: String?) {
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let message = message(from: input) else { return }
        if message["event"] as? String == "SessionStart" {
            guard let fd = connectStarting(socket: socket, logPath: logPath) else { return }
            _ = UnixSocket.exchange(fd, message)
            close(fd)
            return
        }
        guard message["op"] as? String == "wait" else {
            _ = UnixSocket.request(socket, message, timeoutSeconds: 1)
            return
        }
        holdUntilAnswered(socket: socket, message: message, waitLimit: waitLimit)
    }
}
