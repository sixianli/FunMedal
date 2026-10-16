import Darwin
import Foundation

enum UnixSocket {
    static func address(_ path: String) -> sockaddr_un? {
        var addr = sockaddr_un()
        let bytes = Array(path.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else { return nil }
        addr.sun_family = sa_family_t(AF_UNIX)
        addr.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &addr.sun_path) { $0.copyBytes(from: bytes) }
        return addr
    }

    static func configure(_ fd: Int32, timeoutSeconds: Int) {
        var timeout = timeval(tv_sec: timeoutSeconds, tv_usec: 0)
        let size = socklen_t(MemoryLayout<timeval>.size)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, size)
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, size)
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
    }

    static func connect(_ path: String, timeoutSeconds: Int) -> Int32? {
        guard var addr = address(path) else { return nil }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        configure(fd, timeoutSeconds: timeoutSeconds)
        let rc = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard rc == 0 else {
            close(fd)
            return nil
        }
        return fd
    }

    static func listen(_ path: String) -> Int32? {
        guard var addr = address(path) else { return nil }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        unlink(path)
        let rc = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard rc == 0, chmod(path, 0o600) == 0, Darwin.listen(fd, 16) == 0 else {
            close(fd)
            return nil
        }
        return fd
    }

    static func writeAll(_ fd: Int32, _ data: Data) -> Bool {
        data.withUnsafeBytes { raw -> Bool in
            guard let base = raw.baseAddress else { return true }
            var offset = 0
            while offset < raw.count {
                let n = write(fd, base + offset, raw.count - offset)
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
    }

    static func readLine(_ fd: Int32, limit: Int) -> Data? {
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 4096)
        while true {
            if let newline = buffer.firstIndex(of: UInt8(ascii: "\n")) {
                return buffer[buffer.startIndex..<newline]
            }
            if buffer.count > limit { return nil }
            let n = read(fd, &chunk, chunk.count)
            if n <= 0 { return buffer }
            buffer.append(contentsOf: chunk[0..<n])
        }
    }

    static func request(_ path: String, _ message: [String: Any], timeoutSeconds: Int) -> [String: Any]? {
        guard let fd = connect(path, timeoutSeconds: timeoutSeconds) else { return nil }
        defer { close(fd) }
        guard var data = try? JSONSerialization.data(withJSONObject: message) else { return nil }
        data.append(UInt8(ascii: "\n"))
        guard writeAll(fd, data) else { return nil }
        shutdown(fd, SHUT_WR)
        guard let reply = readLine(fd, limit: 1 << 20), !reply.isEmpty else { return nil }
        return (try? JSONSerialization.jsonObject(with: reply)) as? [String: Any]
    }
}
