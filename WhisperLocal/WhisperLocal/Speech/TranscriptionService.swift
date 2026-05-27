import AVFoundation
import Foundation
import Speech

/// On-device streaming transcription via iOS 26's SpeechAnalyzer.
/// All audio is processed locally — `SpeechTranscriber` only runs offline.
actor TranscriptionService {
    enum TranscriptionError: Error {
        case localeUnsupported(Locale)
        case modelUnavailable
    }

    private var analyzer: SpeechAnalyzer?
    private var transcriber: SpeechTranscriber?
    private var inputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?

    private(set) var audioFormat: AVAudioFormat?

    func prepare(locale: Locale = .current) async throws -> AVAudioFormat {
        let transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: [.audioTimeRange]
        )

        guard await SpeechTranscriber.supportedLocales.contains(where: { $0.identifier == locale.identifier }) else {
            throw TranscriptionError.localeUnsupported(locale)
        }

        if await !SpeechTranscriber.installedLocales.contains(where: { $0.identifier == locale.identifier }) {
            if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await request.downloadAndInstall()
            }
        }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw TranscriptionError.modelUnavailable
        }

        self.transcriber = transcriber
        self.analyzer = analyzer
        self.audioFormat = format
        return format
    }

    func startStreaming(onUpdate: @escaping @Sendable (String, Bool) -> Void) async throws {
        guard let analyzer, let transcriber else { throw TranscriptionError.modelUnavailable }

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        inputContinuation = continuation

        try await analyzer.start(inputSequence: stream)

        resultsTask = Task { [transcriber] in
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    onUpdate(text, result.isFinal)
                }
            } catch {
                // Stream ended; nothing to do.
            }
        }
    }

    func ingest(_ buffer: AVAudioPCMBuffer) {
        inputContinuation?.yield(AnalyzerInput(buffer: buffer))
    }

    func finish() async {
        inputContinuation?.finish()
        try? await analyzer?.finalizeAndFinish()
        resultsTask?.cancel()
        resultsTask = nil
        inputContinuation = nil
    }
}
