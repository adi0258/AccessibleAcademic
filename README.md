# Accessible Academic 🎓
**Empowering students through AI-driven lecture accessibility and smart learning.**

Accessible Academic is a cutting-edge platform designed to transform standard academic recordings into high-quality, accessible, and interactive study materials. By combining specialized audio processing with advanced Large Language Models (LLMs), we bridge the accessibility gap for hearing-impaired students and enhance learning efficiency for the entire student body.

## 🚀 Key Features

* **Smart Audio Enhancement:** Automatically detects and boosts low-volume recordings using FFmpeg (3x gain) to ensure clarity before transcription.
* **High-Accuracy Hebrew Transcription:** Leverages **AssemblyAI** for precise speech-to-text with word-level timestamps.
* **AI Transcript Refinement:** A unique post-processing step using **GPT-4o** to fix punctuation, remove filler words, and ensure an academic-grade reading experience.
* **Automated Study Suite:** Generates structured topics, deep-dive summaries, and interactive flashcards directly from the lecture content.
* **Smart Localization:** Automatically applies Hebrew or English UI text and switches between RTL and LTR layouts based on the user's system locale, with Hebrew as the default fallback.
* **Interactive Caption Control:** Lets users show or hide captions without losing synchronization, including seamless switching between the inline overlay and the fullscreen subtitle track.
* **Professional PDF Export:** Clean, RTL-supported PDF summaries designed for Israeli students.

## 🧠 The "Why" - Our Mission
This project was inspired by the personal journey of **Adi Tapiro**, a hearing-impaired Computer Science student. Faced with the daily challenge of following recordings without accurate captions, we set out to build a solution that ensures true academic inclusion for everyone.

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

## 🔗 Panopto Pilot Integration

A pilot loop with the college's Panopto sandbox (`mta-sandbox.cloud.panopto.eu`):
a lecturer uploads a recording into Panopto exactly as normal, our backend picks
it up, runs it through the same transcription pipeline as a manual upload, and
pushes the resulting captions back onto that same Panopto recording.

```
Lecturer uploads in Panopto  →  POST /panopto/sync (pull)
                                     │
                                     ▼
                     download video, create Lecture row
                     (panopto_session_id set), run the normal
                     pipeline in the background — identical to
                     a manual upload from here on
                                     │
                                     ▼
                     on completion, VTT captions are POSTed
                     back onto the same Panopto session (push)
```

Code: [`app/services/panopto_service.py`](app/services/panopto_service.py) (the
Panopto API client + sync orchestration), wired into
[`app/services/pipeline_service.py`](app/services/pipeline_service.py) (push on
completion) and exposed via [`app/api/panopto_routes.py`](app/api/panopto_routes.py).

### One-time setup (needs sandbox admin login — this part is on you)

You need **two things** from Panopto, because reading and writing turned out
to need different levels of identity — see "Why two client types" below if
you want the reasoning, or just follow the steps:

1. **For reading** (finding + downloading recordings) — in Panopto, go to
   **Settings → System → API Clients** and create a client:
   - **Client Type:** Server Application
   - Copy the **Client ID** and **Client Secret** (shown once).
   - Fill into `.env`: `PANOPTO_CLIENT_ID`, `PANOPTO_CLIENT_SECRET`.
   - This alone is enough for `/panopto/sync` to find and download videos —
     skip to step 3 if you only want that working for now.
2. **For writing** (pushing captions back) — Panopto refuses caption edits
   from an identity-less client (401 "User is not authorized"), and there's
   no way to grant one a role either (it doesn't show up as a addable
   person/group in folder sharing). The fix is a *second* API client that
   acts as you:
   - Create another client, **Client Type: Server-side Web Application**.
   - Add `PANOPTO_REDIRECT_URI` (from `.env.example`, default
     `http://127.0.0.1:8000/panopto/oauth/callback`) as an **Allowed
     Redirect URL** on this client.
   - Put *this* client's id/secret in `.env` as `PANOPTO_CLIENT_ID` /
     `PANOPTO_CLIENT_SECRET` (replacing the read-only one from step 1 — one
     pair of credentials is active at a time; see the note below on running
     both).
   - With the app running locally, visit `/panopto/oauth/login`, log in as
     yourself, and click **Allow** on Panopto's consent screen. The callback
     page prints a `PANOPTO_REFRESH_TOKEN` value — paste it into `.env` and
     restart the app.
   - This client now acts as *you* — it can only do what your own Panopto
     account can already do. Deliberately **not** the "User Based Server
     Application" type: that one requires storing your actual login password
     and Panopto's own support discourages it for exactly that reason.
3. On the folder you'll pilot with, confirm **downloading is enabled**
   (Folder → Settings → Downloads → "Download enabled") and that its audience
   (Folder → Share → "Who can access this folder") is broad enough for the
   read-only client to see it.
4. Note the **folder's GUID** from its URL, and set `PANOPTO_FOLDER_ID`.

**Running both directions at once:** the code only reads one `PANOPTO_CLIENT_ID`
/ `PANOPTO_CLIENT_SECRET` pair from `.env`, so pulling and pushing currently
share whichever client is configured. For the pilot, using the Server-side Web
Application client for both is simplest — reads work fine with it too, since
it's strictly more privileged than the read-only one.

### Testing it, step by step

1. `GET /panopto/status` — confirms the OAuth client works (fetches a token,
   touches nothing else). Do this first; a bad client id/secret shows up here
   immediately instead of failing deeper in.
2. `POST /panopto/sync` — finds recordings in the folder we haven't ingested
   yet, downloads each, and starts transcription in the background. Response
   lists what was `created`, `skipped` (already synced), and `failed`.
3. Watch the lecture in Accessible Academic as usual; once it's `completed`,
   the caption push happens automatically. Check `panopto_captions_synced_at`
   / `panopto_sync_error` on the lecture row (or just look in Panopto: open
   the session → Edit → Captions) to confirm it landed.

**Confirmed working end-to-end against the sandbox (2026-08-17):** a real
lecture uploaded to Panopto → downloaded → transcribed (real AssemblyAI +
GPT-4o) → captions pushed back — verified not just via our own DB but by
re-querying the session from Panopto's API and seeing a populated
`CaptionDownloadUrl` for Hebrew that wasn't there before.

### Making it automatic — no manual trigger needed

**Panopto has no outbound webhook for "new session uploaded"** — confirmed,
not a gap on our end; it's a feature Panopto users have been requesting for
years without it shipping. Polling is the only option.

Vercel's Hobby plan caps native Cron Jobs at once/day — far too slow — so
instead a **GitHub Actions workflow** polls `/panopto/sync` every 5 minutes:
[`.github/workflows/panopto-sync.yml`](.github/workflows/panopto-sync.yml).
Since there's no logged-in user for a scheduled job to authenticate as,
`/panopto/sync` accepts an `X-Sync-Secret` header as an alternative to the
normal session cookie — see `PANOPTO_SYNC_SECRET` / `PANOPTO_SYNC_OWNER_USER_ID`
in `.env.example`.

Setup, once you've deployed:
1. Generate a secret: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Set `PANOPTO_SYNC_SECRET` to that value in **both** places — the deployed
   app's environment variables (Vercel project settings) and this repo's
   **Settings → Secrets and variables → Actions → New repository secret**.
3. In the same GitHub settings page, add a repository **variable** (not
   secret) `APP_BASE_URL` set to the deployed app's origin, e.g.
   `https://accessible-academic-backend.vercel.app` (no trailing slash).
4. That's it — the workflow runs every 5 minutes automatically. You can also
   trigger it by hand from the repo's **Actions** tab (`Panopto pilot sync` →
   **Run workflow**) without waiting for the schedule.

Even with instant detection, "immediate" captions aren't achievable — the
pipeline itself (transcription + GPT generation) takes several minutes
regardless of how fast the upload is noticed. Polling gets uploads *noticed*
quickly; it doesn't skip the processing time.

### Things worth knowing going in

*(All verified live against the sandbox — not guesses.)*

- **Captions can't be replaced via this API.** If you re-run a sync against
  the same recording after already pushing captions, Panopto will reject the
  second upload for that language — delete the old caption track in the
  Panopto UI first if you want to re-test.
- **Caption edits need a real user identity, not just folder access.**
  A Client Credentials token gets a clean 401 trying to edit captions,
  regardless of how open the folder's sharing settings are — see the
  two-client setup above.
- **`Urls.DownloadUrl` is only reliable from `GET /api/v1/sessions/{id}`.**
  The listing endpoints (`/sessions/search`, `/folders/{id}/sessions`) return
  the same field as `null` even for fully downloadable sessions —
  `download_session_video()` accounts for this; don't trust that field
  straight off a listing response.
- **`GET /api/v1/folders/{id}/sessions` is flaky.** Failed with a bare 500
  roughly half the time in testing, succeeded on retry with no pattern.
  `list_recent_sessions()` retries 3× before giving up.
- **Panopto rotates the refresh token on every single use, not just
  periodically.** `get_access_token()` keeps using the new one in memory for
  the rest of that process, and prints it so you can update `.env` — but if
  the app restarts before you do, you're back on a stale token and need to
  redo `/panopto/oauth/login`. Confirmed end-to-end (2026-08-17): caption
  push verified not just via our own DB but independently, by re-fetching
  the session from Panopto's API and seeing a populated `CaptionDownloadUrl`
  for Hebrew that wasn't there before.

## 👥 The Team

We are Computer Science students at **The Academic College of Tel Aviv-Yaffo (MTA)**.

---
*Developed as part of the Software Entrepreneurship Workshop 2025.*
