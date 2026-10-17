import Darwin
import Foundation

enum Channel {
    static let name = "funmedal"
    static let protocolVersion = "2025-06-18"
    static let retrySeconds = 2.0
    static let methodNotFound = -32601
    static let instructions = "Messages from the funmedal channel arrive as <channel source=\"funmedal\"> tags. "
        + "Each one is what the user just said aloud on their FunMedal card, transcribed on this Mac. "
        + "Treat it as the user's own next instruction in this session."

    private static let output = NSLock()

    static func object(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    static func write(_ message: [String: Any]) {
        guard let data = Hook.line(message) else { return }
        output.lock()
        FileHandle.standardOutput.write(data)
        output.unlock()
    }

    static func reply(to request: [String: Any]) -> [String: Any]? {
        guard let id = request["id"], let method = request["method"] as? String else { return nil }
        switch method {
        case "initialize":
            return ["jsonrpc": "2.0", "id": id, "result": [
                "protocolVersion": protocolVersion,
                "capabilities": ["experimental": ["claude/channel": [String: Any]()]],
                "serverInfo": ["name": name, "version": "1"],
                "instructions": instructions,
            ] as [String: Any]]
        case "ping":
            return ["jsonrpc": "2.0", "id": id, "result": [String: Any]()]
        default:
            return ["jsonrpc": "2.0", "id": id,
                    "error": ["code": methodNotFound, "message": "unsupported method \(method)"] as [String: Any]]
        }
    }

    static func push(_ text: String) {
        write(["jsonrpc": "2.0", "method": "notifications/claude/channel",
               "params": ["content": text, "meta": ["chat_id": "card"]] as [String: Any]])
    }

    static func forward(fd: Int32) {
        guard let listen = Hook.line(["op": "listen", "claude_pid": Int(getppid())]),
              UnixSocket.writeAll(fd, listen) else { return }
        let reader = LineReader(fd: fd, limit: messageLimit)
        guard case .line(let ackData) = reader.next(until: Date().addingTimeInterval(Hook.ackSeconds)),
              object(ackData)?["ok"] as? Bool == true else { return }
        while true {
            switch reader.next(until: .distantFuture) {
            case .line(let data):
                if let text = object(data)?["say"] as? String { push(text) }
            case .timeout:
                continue
            case .closed, .tooLong:
                return
            }
        }
    }

    static func relay(socket: String) {
        while true {
            if let fd = UnixSocket.connect(socket, timeoutSeconds: 1) {
                forward(fd: fd)
                close(fd)
            }
            Thread.sleep(forTimeInterval: retrySeconds)
        }
    }

    static func run(socket: String) -> Never {
        var relaying = false
        while let line = readLine(strippingNewline: true) {
            guard let message = object(Data(line.utf8)) else { continue }
            if let response = reply(to: message) { write(response) }
            if !relaying, message["method"] as? String == "notifications/initialized" {
                relaying = true
                Thread { relay(socket: socket) }.start()
            }
        }
        exit(0)
    }
}
