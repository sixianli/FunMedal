import CryptoKit
import Foundation

enum Hook {
    static let passedThrough = ["source", "reason"]

    static func toolKey(name: Any?, input: Any?) -> String? {
        let canonical: [String: Any] = ["tool_name": name ?? NSNull(), "tool_input": input ?? NSNull()]
        guard let data = try? JSONSerialization.data(withJSONObject: canonical, options: [.sortedKeys]) else {
            return nil
        }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func message(from input: Data) -> [String: Any]? {
        guard let hook = (try? JSONSerialization.jsonObject(with: input)) as? [String: Any],
              let name = hook["hook_event_name"] as? String, !name.isEmpty,
              let session = hook["session_id"] as? String, !session.isEmpty else { return nil }
        var message: [String: Any] = ["op": "event", "event": name, "session": session]
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

    static func run(socket: String) {
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let message = message(from: input) else { return }
        _ = UnixSocket.request(socket, message, timeoutSeconds: 1)
    }
}
