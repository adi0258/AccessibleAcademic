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
instead a **GitHub Actions workflow** drives the polling:
[`.github/workflows/panopto-sync.yml`](.github/workflows/panopto-sync.yml).
Since there's no logged-in user for a scheduled job to authenticate as,
`/panopto/sync` accepts an `X-Sync-Secret` header as an alternative to the
normal session cookie — see `PANOPTO_SYNC_SECRET` / `PANOPTO_SYNC_OWNER_USER_ID`
in `.env.example`.

**The workflow polls from inside the job rather than relying on the cron
tick, and that distinction is the difference between this feeling broken and
feeling instant.** GitHub delivers scheduled triggers on a best-effort basis
and drops most of them under load. Measured here over 7 days: 285 runs
against an intended every-5-minutes, with a median gap of 26 minutes and a
worst gap of 111. So each run now polls every 2 minutes for 2 hours on its
own, and a newly delivered tick cancels the previous poller rather than
stacking beside it (`concurrency.cancel-in-progress`). One delivered tick per
two hours is enough to keep detection at ~2 minutes.

Setup, once you've deployed:
1. Generate a secret: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Set `PANOPTO_SYNC_SECRET` to that value in **both** places — the deployed
   app's environment variables (Vercel project settings) and this repo's
   **Settings → Secrets and variables → Actions → New repository secret**.
3. In the same GitHub settings page, add a repository **variable** (not
   secret) `APP_BASE_URL` set to the deployed app's origin, e.g.
   `https://accessible-academic-backend.vercel.app` (no trailing slash).
4. That's it. You can also start a poller immediately from the repo's
   **Actions** tab (`Panopto pilot sync` → **Run workflow**) instead of
   waiting for the next tick.

Even with instant detection, "immediate" captions aren't achievable — the
pipeline itself (transcription + GPT generation) takes several minutes
regardless of how fast the upload is noticed. Polling gets uploads *noticed*
quickly; it doesn't skip the processing time. Budget roughly **2 minutes to
notice + 5–10 minutes to process** for a ~45-minute lecture.

### Leaving it running unattended

What the code now handles by itself, with no human in the loop: access
tokens expiring (refreshed on demand), refresh tokens rotating (persisted
with a compare-and-swap so a slow writer can't bury a newer one), a token
Panopto rejects earlier than its stated expiry (invalidated and reminted on
the first 401 rather than re-presented for an hour), transient refresh
failures (the stored credential survives, the next attempt succeeds),
overlapping pollers (unique index on the session id), a recording that isn't
downloadable yet because Panopto is still encoding it (retried next poll),
and `/tmp` filling up with downloaded media (cleaned up after each lecture,
success or failure).

What still needs a human, eventually:

- **GitHub disables scheduled workflows after 60 days without repository
  activity.** Any commit re-arms them, and GitHub emails the repo owner
  first — but a pilot left completely untouched for two months will go
  quiet, and nothing in this app can tell you that from the inside. Checking
  that the Actions tab still shows recent runs is the cheapest way to notice.
- **If the refresh token is ever genuinely lost** — Panopto-side revocation,
  or the one unrecoverable case where an exchange succeeds but persisting
  the replacement fails three times over — sync starts failing with
  `invalid_grant`, and the only fix is a person visiting
  `/panopto/oauth/login` and re-consenting. `/panopto/diagnostics` shows
  `refresh_token_stored` so this is visible before anyone goes looking
  through logs.
- **A lecture that fails *after* its row exists is not retried.** See the
  caveat below; it shows up in diagnostics as a lecture with an error status.
- **Very long recordings are unproven.** A 48-minute lecture has been through
  the whole pipeline on production Vercel successfully; something
  substantially longer may run into the serverless execution limit, which
  would show as a lecture stuck partway with no error.

### Checking on it

`GET /panopto/diagnostics` (same `X-Sync-Secret` header as `/panopto/sync`)
returns one read-only snapshot of everything that has to be true for the
automation to work: config presence, OAuth state and how long the cached
access token is still good for, what Panopto currently lists in the folder
and whether each session has been ingested, and every Panopto-linked lecture
with its status and caption-sync result. It spends no tokens and ingests
nothing, so it's safe to call at any time, including while a poller is
mid-run.

Add `?probe_session_id=<id>` to also check the one thing the folder listing
can't tell you: whether that session's details actually carry a download
URL. A recording can look healthy in the listing and still be
un-downloadable — Panopto still encoding it, or downloads switched off on
the folder — which otherwise only surfaces as a recording that gets noticed
on every poll and never progresses. The probe fetches metadata only.

Nothing in the response includes a secret: credentials are reported as
present/absent, and the download URL as its host rather than the
credentialed link itself.

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
  periodically.** This is the single most fragile thing about the
  integration, because every serverless invocation is a cold process: spend
  the refresh token on each poll and you rotate dozens of times an hour,
  and any two polls that overlap race to spend the same one. The loser is
  left holding a dead token, and the only way back is a human doing an
  interactive re-consent at `/panopto/oauth/login`. That happened once, on
  2026-08-17. The fix is in `get_access_token()`: the *access* token is
  cached in the database (`PanoptoToken`) and reused until it actually
  expires, so a rotation happens roughly once an hour instead of every
  poll, and `_recover_from_lost_race()` picks up the winner's token on the
  rare overlap that remains.
- **`GET /panopto/status` deliberately does not force a token refresh.** A
  health check that rotated the refresh token every time it was called
  would be a liability rather than a diagnostic. Prefer
  `GET /panopto/diagnostics` for a fuller picture.
- **Two pollers can be in flight at once** (a new tick cancels the old one,
  but not instantly), so `lecture.panopto_session_id` carries a unique index
  and `discover_and_ingest()` treats the resulting `IntegrityError` as
  "someone else got there first". Without it, one recording could be
  downloaded and transcribed twice — billed twice — and the second caption
  push would then fail as a duplicate.
- **A session that fails *after* its lecture row exists is not retried
  automatically**, because the row makes it look already-ingested. A session
  that fails *before* that (e.g. Panopto hasn't finished encoding, so there's
  no download URL yet) is retried on the next poll, which is the common case.
  `GET /panopto/diagnostics` surfaces the former as a lecture with an error
  status.
- Confirmed end-to-end (2026-08-17): caption push verified not just via our
  own DB but independently, by re-fetching the session from Panopto's API
  and seeing a populated `CaptionDownloadUrl` for Hebrew that wasn't there
  before.

## 👥 The Team

We are Computer Science students at **The Academic College of Tel Aviv-Yaffo (MTA)**.

---
*Developed as part of the Software Entrepreneurship Workshop 2025.*
