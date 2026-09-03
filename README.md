# Accessible Academic 🎓
**Empowering students through AI-driven lecture accessibility and smart learning.**

Accessible Academic is a cutting-edge platform designed to transform standard academic recordings into high-quality, accessible, and interactive study materials. By combining specialized audio processing with advanced Large Language Models (LLMs), we bridge the accessibility gap for hearing-impaired students and enhance learning efficiency for the entire student body.

## 🚀 Key Features

* **Smart Audio Enhancement:** Automatically detects and boosts low-volume recordings using FFmpeg (3x gain) to ensure clarity before transcription.
* **High-Accuracy Hebrew Transcription:** Leverages **AssemblyAI** for precise speech-to-text with word-level timestamps.
* **Grounded AI, Not Hallucinated:** A four-agent validation pass checks every generated summary and flashcard back against the transcript, rewriting or removing anything the lecture doesn't actually support — so study material can't drift into the model's own knowledge.
* **Automated Study Suite:** Generates structured topics, deep-dive summaries, and interactive flashcards directly from the lecture content.
* **Smart Localization:** Automatically applies Hebrew or English UI text and switches between RTL and LTR layouts based on the user's system locale, with Hebrew as the default fallback.
* **Interactive Caption Control:** Lets users show or hide captions without losing synchronization, including seamless switching between the inline overlay and the fullscreen subtitle track.
* **Professional PDF Export:** Clean, RTL-supported PDF summaries designed for Israeli students.

## 🧠 The "Why" - Our Mission
This project was inspired by the personal journey of **Adi Tapiro**, a hearing-impaired Computer Science student. Faced with the daily challenge of following recordings without accurate captions, we set out to build a solution that ensures true academic inclusion for everyone.

## 🛠️ Technology Stack

* **Backend:** Python with **FastAPI** (Asynchronous processing).
* **Database:** **SQLModel** over PostgreSQL in production (SQLite for local development).
* **Audio Engine:** **FFmpeg** for media manipulation and normalization.
* **AI Engines:**
    * **AssemblyAI:** Specialized Hebrew Speech-to-Text.
    * **OpenAI GPT-4 class models:** Study-material generation and grounding validation, with automatic fallback across models.
* **Frontend:** Modern HTML5/JS interface designed for standalone use or iFrame integration.
* **Deployment:** Serverless on **Vercel**, with a scheduled **GitHub Actions** poller driving the Panopto integration.

## 📂 Project Documentation

Our project documentation is organized into specialized modules to support the external evaluation process:

* **[Business Strategy & Executive Summary](./documentation/submissions/business/):** The social mission, market research, and go-to-market strategy.
* **[Ideation & Concept](./documentation/submissions/ideation/):** The initial research and selection process of our core ideas.
* **[MVP Scope & Roadmap](./documentation/submissions/technical/mvp/):** Strategic development plan and milestone tracking.
* **[Technical HLD](./documentation/submissions/technical/hld/):** Deep dive into the system architecture, data models, and AI pipeline.

## 🔗 Panopto Pilot Integration

A pilot loop with the college's Panopto sandbox (`mta-sandbox.cloud.panopto.eu`):
a lecturer uploads a recording into Panopto exactly as normal, our backend picks
it up, runs it through the same transcription pipeline as a manual upload, and
pushes the resulting captions back onto that same Panopto recording.

```
 GitHub Actions poller ──every 2 min──▶ POST /panopto/sync
                                              │
                    ┌─────────────────────────┼──────────────────────────┐
                    ▼                         ▼                          ▼
            reap stalled lectures     sweep abandoned /tmp        list the folder
            (dead workers become        downloads                        │
             retryable failures)                                         ▼
                                                          claim ONE recording that
                                                          needs work (atomic, so two
                                                          pollers can't both take it)
                                                                         │
                                          returns immediately ───────────┤
                                                                         ▼
                                                            background: download →
                                                            boost audio → AssemblyAI →
                                                            GPT study material →
                                                            4-agent validation →
                                                            push VTT captions to Panopto
```

Everything is driven by polling because **Panopto has no outbound webhook for
"new session uploaded"** — a long-standing, still-unshipped request on their
side, not a gap in this setup.

**The hard parts, and why the design looks like this:**

- **Recordings are claimed atomically, one at a time.** Two pollers overlap by
  design, and a download takes minutes — claiming afterwards meant both could
  take the same recording and bill two transcriptions for it.
- **The request returns before the work begins.** Downloading inside the
  request meant a burst of new recordings could exhaust the serverless time
  limit, leaving lectures created but never processed.
- **Every failure is retryable, up to a ceiling.** A download, transcription,
  or API outage leaves the lecture in a state a later poll reclaims — bounded,
  so one broken recording can't be re-downloaded forever.
- **Dead workers are detected.** A heartbeat distinguishes a long-running job
  from one whose serverless invocation was killed, so work is never silently
  abandoned mid-pipeline.

**Proven end-to-end.** A lecturer uploads to Panopto exactly as they normally
would; minutes later the recording carries our Hebrew captions. Verified
unattended three times against the college sandbox (17 and 24 August 2026),
confirmed from Panopto's own API rather than only our database.

**Operational detail** — setup, monitoring, failure handling, recovery:
[Panopto Operations Guide](./documentation/technical/panopto-operations.md)

Code: [`panopto_service.py`](app/services/panopto_service.py) (API client,
OAuth, claim/reap/retry) · [`pipeline_service.py`](app/services/pipeline_service.py)
(transcription + caption push) · [`panopto_routes.py`](app/api/panopto_routes.py)
· [`tests/test_panopto_pipeline.py`](tests/test_panopto_pipeline.py) (18-section
regression suite — no framework or network required)

## 👥 The Team

We are Computer Science students at **The Academic College of Tel Aviv-Yaffo (MTA)**.

---
*Developed as part of the Software Entrepreneurship Workshop 2025.*
