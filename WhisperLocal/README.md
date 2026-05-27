# WhisperLocal

An iPhone app that **transcribes and summarizes speech entirely on-device**.
No network calls. No iCloud sync. No analytics. Audio never leaves the phone.

- **Transcription**: Apple's iOS 26 `SpeechAnalyzer` + `SpeechTranscriber`
  (the on-device successor to `SFSpeechRecognizer`).
- **Summarization**: Apple Intelligence's on-device `FoundationModels`
  `LanguageModelSession`.
- **Privacy**: no `NSAppTransportSecurity` exceptions, no network entitlements,
  no background uploads.

## Requirements

- macOS with **Xcode 26+**
- iPhone running **iOS 26+** with **Apple Intelligence** enabled
  (iPhone 15 Pro / Pro Max, iPhone 16 family, or later)
- [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`)

## Generate the Xcode project

```bash
cd WhisperLocal
xcodegen generate
open WhisperLocal.xcodeproj
```

Set your **Team** under Signing & Capabilities, then build to a real device
(the simulator does not have Apple Intelligence).

## First-run behavior

1. The app asks for **microphone** and **speech recognition** permission.
2. On first launch, iOS may **download the on-device speech model** for your
   locale (handled by `AssetInventory.assetInstallationRequest`). This is a
   one-time, OS-managed download — not a network call from the app itself.
3. Tap **Record**, speak, then tap **Stop & Summarize**.
4. Toggle the **Bullets / Paragraph** segmented control to regenerate the
   summary in the other style.

## Project layout

```
WhisperLocal/
├── project.yml                  # XcodeGen config (iOS 26 target)
├── WhisperLocal/
│   ├── App/                     # @main entry
│   ├── Views/                   # SwiftUI screens
│   ├── ViewModels/              # @Observable view model
│   ├── Audio/                   # AVAudioEngine capture
│   ├── Speech/                  # SpeechAnalyzer wrapper
│   ├── Summarization/           # FoundationModels wrapper
│   └── Resources/               # Info.plist, Assets.xcassets
```

## Verifying that nothing leaves the device

After signing, you can confirm the privacy claim two ways:

1. **Static check** — the app declares no networking usage description, has no
   `URLSession` calls, and no third-party SDKs.
2. **Live check** — run the app while Charles/Proxyman is configured as a
   system proxy on a sacrificial Wi-Fi network. You should see zero outbound
   requests from the bundle ID `com.specialforcesny.WhisperLocal`.

## Moving this into its own repo

This scaffold currently lives inside the `Sundriver` repo on the
`claude/iphone-transcribe-summarize-fW3by` branch as a starting point. To
extract it into a fresh repo:

```bash
# 1. Create the new empty repo on GitHub (web UI or `gh repo create`).
gh repo create dccbryant/WhisperLocal --private --confirm

# 2. From the Sundriver checkout, copy the folder out and init a new repo.
cp -R WhisperLocal /tmp/WhisperLocal-new
cd /tmp/WhisperLocal-new
git init -b main
git add .
git commit -m "Initial scaffold: on-device transcribe + summarize"
git remote add origin git@github.com:dccbryant/WhisperLocal.git
git push -u origin main
```

## Known sharp edges

- The iOS 26 `SpeechAnalyzer` / `SpeechTranscriber` API surface is new; if
  Apple renames anything in a point release, the `Speech/TranscriptionService.swift`
  wrapper is the only file you'd need to touch.
- `FoundationModels` is gated on Apple Intelligence availability. On a device
  where the user has it disabled, `Summarizer` surfaces a clear error.
- Locale support tracks `SpeechTranscriber.supportedLocales`. The app defaults
  to the user's current locale.
