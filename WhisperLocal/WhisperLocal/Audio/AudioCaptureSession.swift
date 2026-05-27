import AVFoundation
import Foundation

actor AudioCaptureSession {
    enum CaptureError: Error {
        case permissionDenied
        case engineFailedToStart(Error)
    }

    private let engine = AVAudioEngine()
    private var isRunning = false

    func requestPermission() async -> Bool {
        await withCheckedContinuation { cont in
            AVAudioApplication.requestRecordPermission { granted in
                cont.resume(returning: granted)
            }
        }
    }

    func start(targetFormat: AVAudioFormat,
               onBuffer: @escaping @Sendable (AVAudioPCMBuffer) -> Void) async throws {
        guard !isRunning else { return }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.duckOthers, .defaultToSpeaker])
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let input = engine.inputNode
        let hwFormat = input.outputFormat(forBus: 0)

        let converter = AVAudioConverter(from: hwFormat, to: targetFormat)

        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 4096, format: hwFormat) { buffer, _ in
            guard let converter else {
                onBuffer(buffer)
                return
            }
            let ratio = targetFormat.sampleRate / hwFormat.sampleRate
            let outFrames = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
            guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outFrames) else { return }

            var error: NSError?
            let status = converter.convert(to: out, error: &error) { _, inputStatus in
                inputStatus.pointee = .haveData
                return buffer
            }
            if status == .haveData || status == .inputRanDry {
                onBuffer(out)
            }
        }

        engine.prepare()
        do {
            try engine.start()
            isRunning = true
        } catch {
            throw CaptureError.engineFailedToStart(error)
        }
    }

    func stop() {
        guard isRunning else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        isRunning = false
    }
}
