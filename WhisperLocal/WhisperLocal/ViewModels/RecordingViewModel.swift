import AVFoundation
import Foundation
import Observation

@MainActor
@Observable
final class RecordingViewModel {
    enum Phase: Equatable {
        case idle
        case preparing
        case recording
        case summarizing
        case done
        case error(String)
    }

    var phase: Phase = .idle
    var liveTranscript: String = ""
    var finalTranscript: String = ""
    var summary: String = ""
    var summaryStyle: Summarizer.SummaryStyle = .bullets

    private let capture = AudioCaptureSession()
    private let transcription = TranscriptionService()
    private let summarizer = Summarizer()

    func toggleRecording() async {
        switch phase {
        case .idle, .done, .error:
            await start()
        case .recording:
            await stopAndSummarize()
        default:
            break
        }
    }

    func regenerateSummary() async {
        guard !finalTranscript.isEmpty else { return }
        await runSummary()
    }

    private func start() async {
        phase = .preparing
        liveTranscript = ""
        finalTranscript = ""
        summary = ""

        guard await capture.requestPermission() else {
            phase = .error("Microphone permission denied. Enable it in Settings → WhisperLocal.")
            return
        }

        do {
            let format = try await transcription.prepare()
            try await transcription.startStreaming { [weak self] text, isFinal in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    if isFinal {
                        if !self.finalTranscript.isEmpty { self.finalTranscript += " " }
                        self.finalTranscript += text
                        self.liveTranscript = ""
                    } else {
                        self.liveTranscript = text
                    }
                }
            }
            try await capture.start(targetFormat: format) { [weak self] buffer in
                Task { [weak self] in
                    await self?.transcription.ingest(buffer)
                }
            }
            phase = .recording
        } catch {
            await capture.stop()
            await transcription.finish()
            phase = .error(humanize(error))
        }
    }

    private func stopAndSummarize() async {
        await capture.stop()
        await transcription.finish()
        await runSummary()
    }

    private func runSummary() async {
        phase = .summarizing
        let combined = (finalTranscript + " " + liveTranscript).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !combined.isEmpty else {
            phase = .done
            return
        }
        do {
            summary = try await summarizer.summarize(combined, style: summaryStyle)
            phase = .done
        } catch {
            phase = .error(humanize(error))
        }
    }

    private func humanize(_ error: Error) -> String {
        if let e = error as? Summarizer.SummaryError {
            switch e {
            case .modelUnavailable(let reason):
                return "Apple Intelligence isn't available on this device (\(reason)). Summarization needs a supported iPhone with Apple Intelligence enabled."
            case .empty:
                return "Nothing to summarize yet."
            }
        }
        if let e = error as? TranscriptionService.TranscriptionError {
            switch e {
            case .localeUnsupported(let loc):
                return "On-device transcription isn't supported for \(loc.identifier)."
            case .modelUnavailable:
                return "The on-device speech model isn't available."
            }
        }
        return error.localizedDescription
    }
}
