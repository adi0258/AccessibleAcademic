# 🏗️ Technical High-Level Design (HLD)

This folder contains the architectural blueprint of **Accessible Academic**. The system combines a modular FastAPI backend with a lightweight browser client to deliver accessible Hebrew-first lecture playback, AI-generated study materials, and export workflows.

## Included Documents
- **`Technical_HLD.md`**: Editable markdown reference for the current architecture and implementation details.
- **`Technical HLD.docx`**: Packaged document version for submission and sharing.

## Current Architecture Summary
The platform follows a staged processing pipeline:

1. **Audio Normalization (FFmpeg):** Optional gain adjustment improves low-volume source media before transcription.
2. **Transcription (AssemblyAI):** Hebrew speech is converted into transcript text plus word-level timestamps.
3. **Study Material Generation (OpenAI):** The transcript is transformed into topics, summaries, and flashcards.
4. **Presentation Layer:** The web client renders localized UI text, transcript views, exports, and synchronized captions.

## Frontend Highlights
- **Smart localization:** The client loads translations from `assets/translations.json`, detects the preferred locale from browser settings, and applies `lang` and `dir` at runtime for English LTR and Hebrew RTL presentation.
- **Interactive caption control:** The watch page keeps caption timing active on every `timeupdate` and `seeked` event, even when the user hides captions. The toggle changes only the display mode, so captions reappear in sync immediately.

## Data Flow
Raw Media Upload ➔ Audio Boost ➔ AssemblyAI Transcript + Word Timestamps ➔ OpenAI Study Materials ➔ Database Persistence ➔ Localized UI, Caption Playback, and Export Services
