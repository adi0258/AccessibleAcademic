# Technical High-Level Design

## Overview
Accessible Academic is a FastAPI-based lecture accessibility platform focused on Hebrew academic content. The system processes uploaded media into transcripts, study materials, and downloadable outputs, while the browser client provides a localized interface and synchronized playback experience.

## Backend
The backend is organized into focused modules for API routing, data persistence, AI integration, media processing, and export services.

- **API layer:** FastAPI routes manage uploads, lecture processing, lecture retrieval, watch-page data, deletion, and export endpoints.
- **Persistence layer:** SQLModel stores lecture metadata, transcript text, word-level timestamps, processing status, and generated study material.
- **Processing services:** FFmpeg-based normalization, AssemblyAI transcription, and OpenAI-powered study material generation are orchestrated through the processing pipeline.
- **Exports:** Dedicated services generate PDF, Word, SRT, and VTT outputs from persisted lecture data.

## Frontend
The frontend is implemented as lightweight HTML and JavaScript pages served directly by the backend.

- **Main lecture page:** Handles upload flow, lecture listing, status monitoring, summaries, flashcards, and export actions.
- **Watch page:** Renders media playback, transcript viewing, and the interactive caption toggle.
- **Shared localization layer:** `assets/i18n.js` loads `assets/translations.json`, determines the preferred locale from browser language settings, and applies translated strings to elements marked with `data-i18n` attributes.

## Frontend Localization
The localization design is runtime-driven rather than build-time generated.

- The client inspects `navigator.languages`, `navigator.language`, and `navigator.userLanguage` to choose a locale.
- Hebrew is the default fallback when the browser provides no locale information.
- The selected bundle sets both `document.documentElement.lang` and `document.documentElement.dir`.
- Hebrew uses RTL layout, while English uses LTR layout.
- Static labels and dynamic UI strings are both resolved from the centralized translation bundle, keeping the interface consistent across the upload page and the watch page.

## User Interface Behavior
The user interface is designed to preserve accessibility without adding extra navigation complexity.

### Caption Toggle
The watch page exposes a Show/Hide Captions control that affects presentation only, not timing state.

- Transcript words are grouped into subtitle segments from AssemblyAI word timestamps.
- The page keeps caption synchronization active during playback and seeking by continuously calling the subtitle timing logic on media events.
- When captions are hidden, the overlay is suppressed in inline mode and the native subtitle track is disabled in fullscreen mode.
- When captions are shown again, the UI immediately re-renders the caption that matches the current playback timestamp, so the user does not lose sync.

### Localized Presentation
The upload page and watch page both adapt their reading direction and visible text to the detected locale.

- Hebrew readers receive RTL layout and Hebrew labels by default.
- English readers receive LTR layout and English labels when their system locale indicates English.
- The same translation mechanism applies to captions-related labels, upload states, transcript headings, and lecture actions.

## Data Flow
The end-to-end flow is:

1. Media upload
2. Optional audio normalization
3. AssemblyAI transcription with word timestamps
4. OpenAI generation of structured study material
5. Database persistence
6. Localized presentation in the lecture list and watch interface
7. Export generation for offline use

## Current Scope Notes
- Implemented features include automatic system localization and the interactive caption toggle.
