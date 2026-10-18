import Darwin
import Foundation

enum Listen {
    static let retrySeconds = 2.0
    static let parentCheckSeconds = 1.0

    static func object(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    static func relay(fd: Int32, parent: pid_t) -> Bool {
        guard let hello = Hook.line(["op": "listen", "claude_pid": Int(parent)]),
              UnixSocket.writeAll(fd, hello) else { return true }
        let reader = LineReader(fd: fd, limit: messageLimit)
        guard case .line(let ackData) = reader.next(until: Date().addingTimeInterval(Hook.ackSeconds)),
              object(ackData)?["ok"] as? Bool == true else { return true }
        while getppid() == parent {
            switch reader.next(until: Date().addingTimeInterval(parentCheckSeconds)) {
            case .line(var data):
                data.append(UInt8(ascii: "\n"))
                guard UnixSocket.writeAll(STDOUT_FILENO, data) else { return false }
            case .timeout:
                continue
            case .closed, .tooLong:
                return true
            }
        }
        return false
    }

    static func run(socket: String) -> Never {
        let parent = getppid()
        while getppid() == parent {
            if let fd = UnixSocket.connect(socket, timeoutSeconds: 1) {
                let keepGoing = relay(fd: fd, parent: parent)
                close(fd)
                if !keepGoing { break }
            }
            let retryAt = Date().addingTimeInterval(retrySeconds)
            while getppid() == parent, Date() < retryAt {
                Thread.sleep(forTimeInterval: min(parentCheckSeconds, retryAt.timeIntervalSinceNow))
            }
        }
        exit(0)
    }
}
