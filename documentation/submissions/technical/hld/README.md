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

## Data Flow
Raw Media Upload ➔ Audio Boost ➔ AssemblyAI Transcript + Word Timestamps ➔ OpenAI Study Materials ➔ Database Persistence ➔ Localized UI, Caption Playback, and Export Services
