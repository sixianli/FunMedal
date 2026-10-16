import Darwin
import Foundation

let alreadyRunningExit: Int32 = 3
let setupFailedExit: Int32 = 2
let usageExit: Int32 = 64
let messageLimit = 64 * 1024
let clientTimeoutSeconds = 2

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write(Data("funmedal-bridge: \(message)\n".utf8))
    exit(code)
}

func defaultSocket() -> String {
    let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    return support.appendingPathComponent("FunMedal/bridge.sock").path
}

func serve(socket: String, logPath: String?) -> Never {
    guard UnixSocket.address(socket) != nil else { fail("socket path is too long: \(socket)", setupFailedExit) }
    let directory = (socket as NSString).deletingLastPathComponent
    try? FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true,
                                             attributes: [.posixPermissions: 0o700])
    let lockPath = socket + ".lock"
    let lock = open(lockPath, O_CREAT | O_RDWR, 0o600)
    guard lock >= 0 else { fail("cannot open \(lockPath): \(String(cString: strerror(errno)))", setupFailedExit) }
    guard flock(lock, LOCK_EX | LOCK_NB) == 0 else {
        fail("another bridge is already running on \(socket)", alreadyRunningExit)
    }
    guard let listener = UnixSocket.listen(socket) else {
        fail("cannot listen on \(socket): \(String(cString: strerror(errno)))", setupFailedExit)
    }
    let log = EventLog(path: logPath)
    let bridge = Bridge(socketPath: socket, log: log)
    log.write("start", ["pid": Int(getpid()), "socket": socket])
    let acceptor = Thread {
        while true {
            let client = accept(listener, nil, nil)
            if client < 0 { continue }
            Thread { serveClient(client, bridge: bridge, socket: socket, lockPath: lockPath) }.start()
        }
    }
    acceptor.start()
    bridge.start()
    dispatchMain()
}

func serveClient(_ client: Int32, bridge: Bridge, socket: String, lockPath: String) {
    UnixSocket.configure(client, timeoutSeconds: clientTimeoutSeconds)
    let reader = LineReader(fd: client, limit: messageLimit)
    let first: Data?
    switch reader.next(until: Date().addingTimeInterval(Double(clientTimeoutSeconds))) {
    case .line(let data), .closed(let data): first = data
    case .tooLong: first = nil
    case .timeout: first = Data()
    }
    let handled = bridge.handle(first, client: client)
    if let reply = handled.reply, let data = Hook.line(reply) {
        _ = UnixSocket.writeAll(client, data)
    }
    if let approval = handled.approval {
        follow(approval, reader: reader, bridge: bridge)
    }
    close(client)
    if handled.quit {
        unlink(socket)
        unlink(lockPath)
        exit(0)
    }
}

func follow(_ approval: Approval, reader: LineReader, bridge: Bridge) {
    while true {
        switch reader.next(until: .distantFuture) {
        case .line(let data):
            let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            if message?["op"] as? String == "give_up" { bridge.gaveUp(approval) }
        case .closed, .tooLong:
            bridge.ended(approval)
            return
        case .timeout:
            continue
        }
    }
}

func ask(socket: String, op: String) -> Int32 {
    guard let reply = UnixSocket.request(socket, ["op": op], timeoutSeconds: clientTimeoutSeconds) else {
        FileHandle.standardError.write(Data("funmedal-bridge: no bridge is running on \(socket)\n".utf8))
        return 1
    }
    if var data = try? JSONSerialization.data(withJSONObject: reply, options: [.sortedKeys]) {
        data.append(UInt8(ascii: "\n"))
        FileHandle.standardOutput.write(data)
    }
    return (reply["ok"] as? Bool) == true ? 0 : 1
}

signal(SIGPIPE, SIG_IGN)
var arguments = Array(CommandLine.arguments.dropFirst())
let usage = "usage: funmedal-bridge serve|hook|status|quit [--socket PATH] [--log PATH] [--wait-limit SECONDS]"
guard !arguments.isEmpty else { fail(usage, usageExit) }
let mode = arguments.removeFirst()
var socketPath = defaultSocket()
var logPath: String?
var waitLimit = Hook.defaultWaitLimit
while !arguments.isEmpty {
    let option = arguments.removeFirst()
    guard !arguments.isEmpty else { fail("\(option) needs a value", usageExit) }
    switch option {
    case "--socket": socketPath = arguments.removeFirst()
    case "--log": logPath = arguments.removeFirst()
    case "--wait-limit":
        guard let seconds = Double(arguments.removeFirst()), seconds >= 0, seconds.isFinite else {
            fail("--wait-limit needs a number of seconds", usageExit)
        }
        waitLimit = seconds
    default: fail("unknown option \(option)", usageExit)
    }
}

switch mode {
case "serve":
    serve(socket: socketPath, logPath: logPath)
case "hook":
    Hook.run(socket: socketPath, waitLimit: waitLimit)
    exit(0)
case "status", "quit":
    exit(ask(socket: socketPath, op: mode))
default:
    fail("unknown mode \(mode)", usageExit)
}
