import SwiftUI

struct ContentView: View {
    @State private var vm = RecordingViewModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    statusBadge
                    transcriptCard
                    summarySection
                }
                .padding()
            }
            .navigationTitle("WhisperLocal")
            .safeAreaInset(edge: .bottom) {
                recordButton.padding()
            }
        }
    }

    private var statusBadge: some View {
        HStack(spacing: 8) {
            Image(systemName: "lock.shield.fill")
            Text("On-device · No network")
                .font(.footnote.weight(.medium))
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(.thinMaterial, in: Capsule())
    }

    private var transcriptCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Transcript").font(.headline)
            let combined = (vm.finalTranscript + " " + vm.liveTranscript)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            Text(combined.isEmpty ? placeholderText : combined)
                .foregroundStyle(combined.isEmpty ? .secondary : .primary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
    }

    private var placeholderText: String {
        switch vm.phase {
        case .idle: return "Tap record to start. Audio is processed on your device only."
        case .preparing: return "Preparing on-device model…"
        case .recording: return "Listening…"
        default: return "—"
        }
    }

    @ViewBuilder
    private var summarySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Summary").font(.headline)
                Spacer()
                Picker("Style", selection: $vm.summaryStyle) {
                    ForEach(Summarizer.SummaryStyle.allCases) { style in
                        Text(style.label).tag(style)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 200)
                .onChange(of: vm.summaryStyle) { _, _ in
                    Task { await vm.regenerateSummary() }
                }
            }

            switch vm.phase {
            case .summarizing:
                HStack { ProgressView(); Text("Summarizing on device…").foregroundStyle(.secondary) }
            case .error(let message):
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.leading)
            default:
                if vm.summary.isEmpty {
                    Text("Stop recording to generate a summary.")
                        .foregroundStyle(.secondary)
                } else {
                    Text(vm.summary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
    }

    private var recordButton: some View {
        Button {
            Task { await vm.toggleRecording() }
        } label: {
            Label(buttonLabel, systemImage: buttonIcon)
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
        }
        .buttonStyle(.borderedProminent)
        .tint(vm.phase == .recording ? .red : .accentColor)
        .disabled(vm.phase == .preparing || vm.phase == .summarizing)
    }

    private var buttonLabel: String {
        switch vm.phase {
        case .recording: return "Stop & Summarize"
        case .preparing: return "Preparing…"
        case .summarizing: return "Summarizing…"
        default: return "Record"
        }
    }

    private var buttonIcon: String {
        vm.phase == .recording ? "stop.circle.fill" : "mic.circle.fill"
    }
}

#Preview {
    ContentView()
}
