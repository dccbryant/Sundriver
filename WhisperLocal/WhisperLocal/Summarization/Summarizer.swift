import Foundation
import FoundationModels

/// Wraps Apple Intelligence's on-device language model for summarization.
/// `SystemLanguageModel` runs locally; no network round-trip.
actor Summarizer {
    enum SummaryStyle: String, CaseIterable, Identifiable, Sendable {
        case bullets
        case paragraph
        var id: String { rawValue }
        var label: String {
            switch self {
            case .bullets: return "Bullets"
            case .paragraph: return "Paragraph"
            }
        }
    }

    enum SummaryError: Error {
        case modelUnavailable(String)
        case empty
    }

    func summarize(_ transcript: String, style: SummaryStyle) async throws -> String {
        let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw SummaryError.empty }

        let availability = SystemLanguageModel.default.availability
        if case let .unavailable(reason) = availability {
            throw SummaryError.modelUnavailable(String(describing: reason))
        }

        let instructions: String
        let prompt: String
        switch style {
        case .bullets:
            instructions = """
            You summarize transcribed speech into 3 to 7 concise bullet points.
            Capture decisions, action items, and key facts. Use plain text bullets prefixed with "- ".
            Do not invent details that aren't in the transcript.
            """
            prompt = "Summarize the following transcript as bullet points:\n\n\(trimmed)"
        case .paragraph:
            instructions = """
            You summarize transcribed speech into a single tight paragraph of 2 to 4 sentences.
            Capture the gist faithfully. Do not invent details.
            """
            prompt = "Summarize the following transcript as a short paragraph:\n\n\(trimmed)"
        }

        let session = LanguageModelSession(instructions: instructions)
        let response = try await session.respond(to: prompt)
        return response.content.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
