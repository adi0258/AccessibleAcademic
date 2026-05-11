# 🏗️ Technical High-Level Design (HLD)

This folder contains the architectural blueprint of **Accessible Academic**. Our system follows a **"Best-of-Breed"** AI strategy to ensure maximum accuracy for Hebrew academic content.

## 🧠 The AI Pipeline Architecture
Our system processes data through four distinct logical stages:

1. **Audio Normalization (FFmpeg):** Boosting audio by 3x to ensure clear input for the STT engine.
2. **Transcription (AssemblyAI):** Converting Hebrew speech to text with precise word-level timestamps.
3. **Logical Refinement (GPT-4o):** An intermediate AI step that adds punctuation, fixes grammatical errors, and removes filler words to create an "Academic Grade" transcript.
4. **Generative Study Suite (GPT-4o):** Extracting structured data for topics, deep-dive summaries, and flashcards.

## 🛠️ Technology Stack
* **Backend:** Python with **FastAPI** for high-performance asynchronous processing.
* **Database:** **SQLModel** (SQLite) for structured storage of lecture data.
* **Integrations:**
    * **AssemblyAI API** for Hebrew Speech-to-Text.
    * **OpenAI API (GPT-4o)** for NLP and content generation.
* **Frontend:** Modern HTML5/JS designed for iFrame integration into external VOD players.

## 📂 Data Flow
Raw Media Upload ➔ Audio Boost ➔ AssemblyAI (Raw Text) ➔ GPT-4o (Refined Text) ➔ GPT-4o (Summarization) ➔ Database Persistence ➔ User Interface & PDF Export.
