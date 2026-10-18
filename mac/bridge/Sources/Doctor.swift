import Foundation

enum Doctor {
    static let neededClaude = [2, 1, 292]
    static let pluginID = "funmedal@funmedal"
    static let commandLimitSeconds = 30.0

    enum Mark: String {
        case good = "好"
        case note = "注意"
        case bad = "不好"
        case unknown = "不知道"
    }

    struct Finding {
        let mark: Mark
        let name: String
        let detail: String
        var fix: String? = nil

        var line: String {
            "\(mark.rawValue) \(name)：\(detail)" + (fix.map { "。改法：\($0)" } ?? "")
        }
    }

    struct Ran {
        let code: Int32
        let out: String
        let err: String
    }

    static func run(socket: String) -> Int32 {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        let app = (home as NSString).appendingPathComponent("Applications/FunMedalBridge.app")
        let restart = "再重启桥接程序：先运行 \"\(app)/Contents/MacOS/funmedal-bridge\" quit，"
            + "再运行 open -g \"\(app)\" --args serve"
        let status = UnixSocket.request(socket, ["op": "status"], timeoutSeconds: clientTimeoutSeconds)
        let claude = findClaude()
        let findings = [
            bridgeRunning(status, socket: socket, app: app),
            bluetooth(status, restart: restart),
            speech(status, app: app, restart: restart),
            signature(app: app),
            card(status, restart: restart),
            linkVersion(status, restart: restart),
            plugin(claude),
            hooksSwitch(home: home),
            claudeVersion(claude),
        ]
        for finding in findings {
            print(finding.line)
        }
        return findings.contains { $0.mark == .bad } ? 1 : 0
    }

    static func bridgeRunning(_ status: [String: Any]?, socket: String, app: String) -> Finding {
        guard let status else {
            return Finding(mark: .bad, name: "桥接程序", detail: "没在跑（\(socket) 上没有回应）",
                           fix: "开一个新的 Claude Code 会话，插件会自动启动它；或者运行 open -g \"\(app)\" --args serve")
        }
        return Finding(mark: .good, name: "桥接程序", detail: "在跑（进程 \(status["pid"] as? Int ?? 0)）")
    }

    static func bluetooth(_ status: [String: Any]?, restart: String) -> Finding {
        let name = "蓝牙"
        guard let status else { return Finding(mark: .unknown, name: name, detail: "桥接程序没在跑，查不到") }
        switch status["authorization"] as? String {
        case "allowedAlways":
            break
        case "denied", "restricted":
            return Finding(mark: .bad, name: name, detail: "macOS 不让桥接程序用蓝牙",
                           fix: "在“系统设置 → 隐私与安全性 → 蓝牙”里打开 FunMedalBridge，\(restart)")
        case "notDetermined":
            return Finding(mark: .bad, name: name, detail: "macOS 还在等你回答蓝牙授权框",
                           fix: "找到“FunMedalBridge 想使用蓝牙”的弹框，选允许")
        default:
            return Finding(mark: .unknown, name: name, detail: "桥接程序没报蓝牙授权")
        }
        switch status["bluetooth"] as? String {
        case "poweredOn":
            return Finding(mark: .good, name: name, detail: "允许使用，蓝牙开着")
        case "poweredOff":
            return Finding(mark: .bad, name: name, detail: "Mac 的蓝牙关着", fix: "在控制中心或“系统设置 → 蓝牙”里打开蓝牙")
        case let state:
            return Finding(mark: .bad, name: name, detail: "蓝牙现在是 \(state ?? "未知") 状态",
                           fix: "等几秒再查；一直这样就在“系统设置 → 蓝牙”里关掉再打开蓝牙")
        }
    }

    static func speech(_ status: [String: Any]?, app: String, restart: String) -> Finding {
        let name = "语音识别"
        guard let status else { return Finding(mark: .unknown, name: name, detail: "桥接程序没在跑，查不到") }
        switch status["speech"] as? String {
        case "authorized":
            return Finding(mark: .good, name: name, detail: "允许使用")
        case "denied", "restricted":
            return Finding(mark: .bad, name: name, detail: "macOS 不让桥接程序用语音识别，按住说话用不了",
                           fix: "在“系统设置 → 隐私与安全性 → 语音识别”里打开 FunMedalBridge，\(restart)")
        case "not_determined":
            return Finding(mark: .bad, name: name, detail: "还没问过你，按住说话用不了",
                           fix: "运行 open -n -W \"\(app)\" --args speech-access，在弹框里选允许")
        default:
            return Finding(mark: .unknown, name: name, detail: "桥接程序没报语音识别授权")
        }
    }

    static func signature(app: String) -> Finding {
        let name = "签名"
        let install = "在仓库目录运行 sh install.sh"
        guard FileManager.default.fileExists(atPath: app) else {
            return Finding(mark: .bad, name: name, detail: "\(app) 不存在", fix: install)
        }
        guard let verified = execute("/usr/bin/codesign", ["--verify", app]), verified.code == 0,
              let shown = execute("/usr/bin/codesign", ["-dv", "--verbose=2", app]), shown.code == 0 else {
            return Finding(mark: .bad, name: name, detail: "\(app) 没有签名或签名坏了，macOS 不让它用蓝牙", fix: install)
        }
        let lines = shown.err.split(separator: "\n").map(String.init)
        if lines.contains("Signature=adhoc") {
            return Finding(mark: .note, name: name,
                           detail: "临时签名：每次重新安装后，macOS 都会再问一次蓝牙和语音识别授权")
        }
        let authority = lines.first { $0.hasPrefix("Authority=") }.map { String($0.dropFirst("Authority=".count)) }
        return Finding(mark: .good, name: name, detail: "用证书签名（\(authority ?? "证书名称未知")）")
    }

    static func card(_ status: [String: Any]?, restart: String) -> Finding {
        let name = "卡片"
        guard let status else { return Finding(mark: .unknown, name: name, detail: "桥接程序没在跑，查不到") }
        switch status["card"] as? String {
        case "connected":
            return Finding(mark: .good, name: name, detail: "连着")
        case "mismatch":
            return Finding(mark: .bad, name: name, detail: "连上过，但版本不配，桥接程序已经断开",
                           fix: "照下一行更新，\(restart)")
        case "pairing_failed":
            return Finding(mark: .bad, name: name, detail: "配对连续失败 \(Bridge.pairingAttempts) 次，桥接程序不再连这张卡片",
                           fix: "\(restart)；Mac 弹出配对框后，两边数字相同就点“连接”，再按卡片右侧最上面的键")
        case "off":
            return Finding(mark: .unknown, name: name, detail: "蓝牙不能用，见上面蓝牙那一行")
        case let link:
            return Finding(mark: .bad, name: name, detail: "没连上（\(link ?? "未知")）",
                           fix: "确认卡片开着、就在 Mac 旁边；用原厂固件时按任意功能键唤醒；从没配对过就照 README“安装”的第 4 步配对")
        }
    }

    static func linkVersion(_ status: [String: Any]?, restart: String) -> Finding {
        let name = "固件版本"
        guard let status else { return Finding(mark: .unknown, name: name, detail: "桥接程序没在跑，查不到") }
        let firmware = "照 README“安装”的第 1 步运行 sh flash.sh <端口>，刷上仓库里编译好的固件，\(restart)"
        let install = "在仓库目录运行 sh install.sh，\(restart)"
        guard let bridgeVersion = status["bridge_version"] as? Int else {
            return Finding(mark: .bad, name: name, detail: "正在跑的桥接程序太旧，不报版本", fix: install)
        }
        guard let cardVersion = status["card_version"] as? Int else {
            if status["card"] as? String == "mismatch" {
                return Finding(mark: .bad, name: name, detail: "卡片固件太旧，没有版本号；桥接程序是 \(bridgeVersion)",
                               fix: firmware)
            }
            return Finding(mark: .unknown, name: name, detail: "卡片没连上，读不到")
        }
        if cardVersion == bridgeVersion {
            return Finding(mark: .good, name: name, detail: "卡片和桥接程序都是 \(cardVersion)")
        }
        if cardVersion < bridgeVersion {
            return Finding(mark: .bad, name: name, detail: "卡片 \(cardVersion)，桥接程序 \(bridgeVersion)，卡片固件旧了",
                           fix: firmware)
        }
        return Finding(mark: .bad, name: name, detail: "卡片 \(cardVersion)，桥接程序 \(bridgeVersion)，桥接程序旧了", fix: install)
    }

    static func plugin(_ claude: String?) -> Finding {
        let name = "插件"
        guard let claude else { return Finding(mark: .unknown, name: name, detail: "没找到 claude 命令，查不了") }
        guard let listed = execute(claude, ["plugin", "list", "--json"]), listed.code == 0,
              let plugins = (try? JSONSerialization.jsonObject(with: Data(listed.out.utf8))) as? [[String: Any]] else {
            return Finding(mark: .unknown, name: name, detail: "claude plugin list --json 没给出结果")
        }
        guard let mine = plugins.first(where: { $0["id"] as? String == pluginID }) else {
            return Finding(mark: .bad, name: name, detail: "\(pluginID) 没装", fix: "在仓库目录运行 sh install.sh")
        }
        if mine["enabled"] as? Bool == true {
            return Finding(mark: .good, name: name, detail: "\(pluginID) 装着，也开着")
        }
        return Finding(mark: .bad, name: name, detail: "\(pluginID) 装着，但关着",
                       fix: "运行 claude plugin enable \(pluginID)，再开新的会话")
    }

    static func hooksSwitch(home: String) -> Finding {
        let name = "hooks 开关"
        let path = (home as NSString).appendingPathComponent(".claude/settings.json")
        guard let data = FileManager.default.contents(atPath: path) else {
            return Finding(mark: .good, name: name, detail: "没被关掉（\(path) 不存在）")
        }
        guard let settings = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return Finding(mark: .unknown, name: name, detail: "\(path) 不是有效的 JSON")
        }
        if settings["disableAllHooks"] as? Bool == true {
            return Finding(mark: .bad, name: name, detail: "\(path) 里 disableAllHooks 是 true，状态显示、批准和按住说话都不工作",
                           fix: "把 disableAllHooks 删掉或改成 false，再开新的会话")
        }
        return Finding(mark: .good, name: name, detail: "没被关掉（只查了 \(path)）")
    }

    static func claudeVersion(_ claude: String?) -> Finding {
        let name = "Claude Code 版本"
        let needed = neededClaude.map(String.init).joined(separator: ".")
        guard let claude else {
            return Finding(mark: .bad, name: name, detail: "没找到 claude 命令",
                           fix: "安装 Claude Code，并确认终端里能运行 claude")
        }
        guard let ran = execute(claude, ["--version"]), ran.code == 0,
              let found = ran.out.range(of: #"\d+\.\d+\.\d+"#, options: .regularExpression) else {
            return Finding(mark: .unknown, name: name, detail: "claude --version 没给出版本号")
        }
        let version = String(ran.out[found])
        let parts = version.split(separator: ".").compactMap { Int($0) }
        if parts.lexicographicallyPrecedes(neededClaude) {
            return Finding(mark: .bad, name: name, detail: "\(version)，低于 \(needed)", fix: "把 Claude Code 升级到最新版")
        }
        return Finding(mark: .good, name: name, detail: version)
    }

    static func findClaude() -> String? {
        let path = ProcessInfo.processInfo.environment["PATH"] ?? ""
        for directory in path.split(separator: ":") {
            let candidate = (String(directory) as NSString).appendingPathComponent("claude")
            if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
        }
        return nil
    }

    static func execute(_ program: String, _ arguments: [String]) -> Ran? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: program)
        process.arguments = arguments
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        process.standardInput = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let limit = DispatchWorkItem { process.terminate() }
        DispatchQueue.global().asyncAfter(deadline: .now() + commandLimitSeconds, execute: limit)
        var errData = Data()
        let errRead = DispatchGroup()
        DispatchQueue.global().async(group: errRead) { errData = err.fileHandleForReading.readDataToEndOfFile() }
        let outData = out.fileHandleForReading.readDataToEndOfFile()
        errRead.wait()
        process.waitUntilExit()
        limit.cancel()
        return Ran(code: process.terminationStatus, out: String(decoding: outData, as: UTF8.self),
                   err: String(decoding: errData, as: UTF8.self))
    }
}
