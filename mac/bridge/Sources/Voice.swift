import AVFoundation
import Foundation
import Speech

enum Voice {
    static let localeIdentifier = "zh-CN"

    static func name(_ status: SFSpeechRecognizerAuthorizationStatus) -> String {
        switch status {
        case .authorized: return "authorized"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not_determined"
        @unknown default: return "unknown"
        }
    }

    static func requestAccess() -> SFSpeechRecognizerAuthorizationStatus {
        let current = SFSpeechRecognizer.authorizationStatus()
        guard current == .notDetermined else { return current }
        let answered = DispatchSemaphore(value: 0)
        var result = current
        SFSpeechRecognizer.requestAuthorization { status in
            result = status
            answered.signal()
        }
        answered.wait()
        return result
    }

    static func access(reportTo path: String?, locale: String) -> Int32 {
        let status = requestAccess()
        let recognizer = SFSpeechRecognizer(locale: Locale(identifier: locale))
        let report: [String: Any] = [
            "speech": name(status),
            "locale": locale,
            "recognizer": recognizer != nil,
            "on_device": recognizer?.supportsOnDeviceRecognition ?? false,
        ]
        var data = (try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])) ?? Data()
        data.append(UInt8(ascii: "\n"))
        if let path {
            FileManager.default.createFile(atPath: path, contents: data)
        } else {
            FileHandle.standardOutput.write(data)
        }
        return status == .authorized ? 0 : 1
    }
}

enum Adpcm {
    static let sampleRate = 16000.0
    static let samplesPerPacket = 320
    static let header = 7
    static let packetSize = header + samplesPerPacket / 2
    static let steps: [Int32] = [
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
        107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
        5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
        27086, 29794, 32767,
    ]
    static let indexSteps = [-1, -1, -1, -1, 2, 4, 6, 8]

    static func decode(_ bytes: [UInt8]) -> [Int16]? {
        guard bytes.count == packetSize, bytes[6] < steps.count else { return nil }
        var predictor = Int32(Int16(bitPattern: UInt16(bytes[4]) | UInt16(bytes[5]) << 8))
        var index = Int(bytes[6])
        var samples: [Int16] = []
        samples.reserveCapacity(samplesPerPacket)
        for byte in bytes[header...] {
            for code in [Int(byte & 0x0F), Int(byte >> 4)] {
                let step = steps[index]
                var diff = step >> 3
                if code & 4 != 0 { diff += step }
                if code & 2 != 0 { diff += step >> 1 }
                if code & 1 != 0 { diff += step >> 2 }
                predictor = max(-32768, min(32767, code & 8 != 0 ? predictor - diff : predictor + diff))
                index = max(0, min(steps.count - 1, index + indexSteps[code & 7]))
                samples.append(Int16(predictor))
            }
        }
        return samples
    }
}

final class Transcription {
    static let finalWaitSeconds = 10.0
    static let packetLimit = 750
    static let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Adpcm.sampleRate, channels: 1,
                                      interleaved: false)!

    let clip: UInt8
    let denied: Bool
    var nextSequence = 0
    var packets = 0
    var missing = 0
    var text = ""
    var recognized = false
    var ended = false
    var reason = "release"
    var concluded = false
    private let request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    init(clip: UInt8, recognizer: SFSpeechRecognizer?, onUpdate: @escaping (Transcription) -> Void) {
        self.clip = clip
        guard let recognizer, SFSpeechRecognizer.authorizationStatus() == .authorized, recognizer.isAvailable,
              recognizer.supportsOnDeviceRecognition else {
            denied = true
            request = nil
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        self.request = request
        denied = false
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result { self.text = result.bestTranscription.formattedString }
            if error != nil || result?.isFinal == true { self.recognized = true }
            onUpdate(self)
        }
    }

    private func append(_ samples: [Int16]) {
        guard let request, !samples.isEmpty,
              let buffer = AVAudioPCMBuffer(pcmFormat: Transcription.format,
                                            frameCapacity: AVAudioFrameCount(samples.count)),
              let channel = buffer.floatChannelData?[0] else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        for (offset, sample) in samples.enumerated() { channel[offset] = Float(sample) / 32768 }
        request.append(buffer)
    }

    func receive(sequence: Int, samples: [Int16]) {
        guard !ended, sequence >= nextSequence, sequence < Transcription.packetLimit else { return }
        let gap = sequence - nextSequence
        if gap > 0 {
            missing += gap
            append([Int16](repeating: 0, count: gap * Adpcm.samplesPerPacket))
        }
        append(samples)
        packets += 1
        nextSequence = sequence + 1
    }

    func end(count: Int, reason: String) {
        guard !ended else { return }
        ended = true
        self.reason = reason
        if count > nextSequence { missing += min(count, Transcription.packetLimit) - nextSequence }
        request?.endAudio()
        if denied { recognized = true }
    }

    func cancel() {
        task?.cancel()
    }
}
