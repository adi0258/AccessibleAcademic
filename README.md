# Accessible Academic 🎓
**Empowering students through AI-driven lecture accessibility and smart learning.**

Accessible Academic is a cutting-edge platform designed to transform standard academic recordings into high-quality, accessible, and interactive study materials. By combining specialized audio processing with advanced Large Language Models (LLMs), we bridge the accessibility gap for hearing-impaired students and enhance learning efficiency for the entire student body.

## 🚀 Key Features

* **Smart Audio Enhancement:** Automatically detects and boosts low-volume recordings using FFmpeg (3x gain) to ensure clarity before transcription.
* **High-Accuracy Hebrew Transcription:** Leverages **AssemblyAI** for precise speech-to-text with word-level timestamps.
* **AI Transcript Refinement:** A unique post-processing step using **GPT-4o** to fix punctuation, remove filler words, and ensure an academic-grade reading experience.
* [cite_start]**Automated Study Suite:** Generates structured topics, deep-dive summaries, and interactive flashcards directly from the lecture content [cite: 467-469, 476-477].
* **Panopto Automation:** Full integration via Webhooks, including automatic VTT caption upload back to the institutional server.
* **Professional PDF Export:** Clean, RTL-supported PDF summaries designed for Israeli students.

## 🧠 The "Why" - Our Mission
This project was inspired by the personal journey of our co-founder, **Adi Tapiro**, a hearing-impaired Computer Science student. Faced with the daily challenge of following recordings without accurate captions, we set out to build a solution that ensures true academic inclusion for everyone.

## 🛠️ Technology Stack

* **Backend:** Python with **FastAPI** (Asynchronous processing).
* **Database:** **SQLModel** with SQLite for efficient data persistence.
* **Audio Engine:** **FFmpeg** for media manipulation and normalization.
* **AI Engines:**
    * **AssemblyAI:** Specialized Hebrew Speech-to-Text.
    * **OpenAI GPT-4o:** Advanced NLP for transcript refinement and summarization.
* **Frontend:** Modern HTML5/JS interface designed for standalone use or iFrame integration.

## 📂 Project Documentation

Our project documentation is organized into specialized modules to support the external evaluation process:

* **[Business Strategy & Executive Summary](./documentation/business/):** The social mission, market research, and core business model (6-page depth).
* **[Ideation & Concept](./documentation/submissions/ideation/):** The initial research and selection process of our core ideas.
* **[MVP Scope & Roadmap](./documentation/technical/mvp/):** Strategic development plan and milestone tracking.
* **[Technical HLD](./documentation/technical/hld/):** Deep dive into the system architecture, data models, and AI pipeline.

## 👥 The Team

We are Computer Science students at **The Academic College of Tel Aviv-Yaffo (MTA)**.

---
*Developed as part of the Software Entrepreneurship Workshop 2025.*
