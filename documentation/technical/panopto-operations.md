# Panopto Integration — Operations Guide

Everything needed to set up, run, monitor, and reset the Panopto pilot.

The README carries the architecture and the reasoning behind it; this is the
runbook. Most of what follows was learned by hitting the problem in
production, so the "why" is kept alongside the "how".

**Contents**
- [One-time setup](#one-time-setup)
- [Automating it](#automating-it)
- [Monitoring](#monitoring)
- [What runs unattended, and what doesn't](#what-runs-unattended-and-what-doesnt)
- [Resetting for a clean test](#resetting-for-a-clean-test)
- [Panopto API quirks](#panopto-api-quirks)

---

## One-time setup

You need **two things** from Panopto, because reading and writing turned out
to need different levels of identity (see [Panopto API quirks](#panopto-api-quirks)
for why). To just get it working, follow the steps:

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


## Automating it


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


## Monitoring

### Verifying by hand

1. `GET /panopto/status` — confirms the OAuth client works. Do this first: a
   bad client id/secret shows up here immediately rather than failing deeper in.
2. `POST /panopto/sync` — claims any recording that needs work and reports
   what was `created`, `skipped`, `deferred`, `gave_up`, and `reaped`.
3. Watch the lecture in the app; once it reads `completed`, the caption push
   follows automatically. Confirm in Panopto itself: open the session →
   Edit → Captions.

### Diagnostics

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

The two fields to read first:

- **`attention`** — recordings the pipeline has given up on, or whose
  captions never landed after a full retry budget. Empty is the healthy
  state; anything here needs a person.
- **`oauth.refresh_token_stored`** — false means the pilot needs a
  re-consent at `/panopto/oauth/login` and nothing will sync until then.

Per-lecture, `ingest_attempts` / `caption_attempts` show how much retry
budget is left and `last_progress_at` is the heartbeat the reaper uses.

Nothing in the response includes a secret: credentials are reported as
present/absent, and the download URL as its host rather than the
credentialed link itself.

These endpoints are restricted to the pilot owner — the account named by
`PANOPTO_SYNC_OWNER_USER_ID`, or the lowest-id account if that's unset.
Anyone can sign in with a Google account, and these trigger real work and
expose the folder's contents, so being signed in is not on its own enough.


## What runs unattended, and what doesn't


Handled by the code, no human in the loop:

| Failure | What happens |
|---|---|
| Access token expires | Refreshed on demand; cached in the DB so a cold start doesn't re-spend the refresh token |
| Refresh token rotates | Persisted with a compare-and-swap, so a slow writer can't bury a newer one |
| Token rejected before its stated expiry | First 401 invalidates and remints, rather than re-presenting it for an hour |
| Transient refresh failure | Stored credential survives; the next attempt succeeds unaided |
| Two pollers overlap | Atomic claim + unique index — one wins, the other moves on |
| Panopto still encoding the recording | No download URL yet, so it's retried next poll |
| Download / transcription / API outage | Recorded as a retryable failure, retried up to `PANOPTO_MAX_INGEST_ATTEMPTS` |
| Worker killed mid-processing (execution limit, redeploy) | Heartbeat goes quiet, reaper marks it failed, next poll retries it |
| Caption push fails | Retried on later polls, up to `PANOPTO_MAX_CAPTION_ATTEMPTS` |
| Caption uploaded but our DB write failed | The retry gets "already has captions" and records it as done |
| Backlog of many recordings | Drained one per poll instead of all in one invocation |
| `/tmp` filling with media | Cleaned up per lecture, plus a sweep for files a killed worker abandoned |
| AssemblyAI wedged / flaky | Bounded by `TRANSCRIPTION_TIMEOUT_MINUTES`; transient poll errors are absorbed |
| Validator misbehaves | The lecture is kept with unpurified content rather than being thrown away |

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
- **A recording that fails its full retry budget stops being retried.** That's
  deliberate — the alternative is re-downloading a broken recording forever —
  but it means someone has to look. `/panopto/diagnostics` lists these under
  `attention`, and each shows `given_up: true`.
- **Nothing pages you.** Diagnostics makes the state legible, but only when
  someone asks. There is no alerting; a pipeline that has quietly given up on
  every recording looks exactly like an idle one from the outside.
- **Database growth is unbounded.** Word-level timings dominate (~0.5 MB for
  an hour-long lecture) and nothing prunes them. Fine for a pilot, worth a
  retention policy before this runs at course scale — the managed Postgres
  free tier is the first thing that would bite.
- **Continuous polling keeps the database awake.** A poll every two minutes
  means the serverless Postgres never idles into its suspended state, so
  compute is effectively billed around the clock. If usage becomes a
  concern, the lever is the poll interval in the workflow.
- **Very long recordings are unproven.** A 48-minute lecture has been through
  the whole pipeline on production Vercel successfully; something
  substantially longer may run into the serverless execution limit. It now
  fails visibly rather than hanging — the reaper catches it — but it would
  fail repeatedly until it exhausts its retries.
- **Deleting a Panopto-sourced lecture in the UI makes it come back.** The
  recording is still in the folder, so the next poll sees an unknown session
  and ingests it again.


## Resetting for a clean test


**Order matters, and getting it backwards silently undoes the reset.** Delete
the recordings from the Panopto folder *first*, then clear the database. The
poller treats any session it has no row for as new work, so clearing the
database while recordings are still in the folder means the next poll — at
most two minutes later — starts re-ingesting them, re-transcribing at full
cost, and the reset is gone before anyone notices.

1. Delete the test recordings in the Panopto folder.
2. Confirm the folder is empty: `GET /panopto/diagnostics` should report
   `panopto_folder.session_count: 0`.
3. Clear the pilot rows — and only those:
   ```sql
   DELETE FROM lecture WHERE panopto_session_id IS NOT NULL;
   ```
   That covers every piece of per-recording state, because the retry
   counters, caption attempts, heartbeat, and sync errors all live on the
   lecture row. **Leave `panoptotoken` alone** — that's the OAuth grant, and
   dropping it means a human has to re-consent at `/panopto/oauth/login`.
4. Confirm: diagnostics should show no lectures, an empty `attention`, and
   `oauth.refresh_token_stored: true`.

Downloaded media needs no cleanup: it lives in the serverless `/tmp`, which
is discarded when the instance recycles, and anything a killed worker left
behind is swept on a later poll.


## Panopto API quirks


*(All verified live against the sandbox — not guesses.)*

- **Captions can't be replaced via this API.** Pushing a second track for a
  language a session already has is a 400. That's also what a successful
  upload whose bookkeeping failed looks like on retry, so
  `push_captions_for_lecture()` treats "already has captions" as done rather
  than as an error. To genuinely re-caption a recording, delete the existing
  track in the Panopto UI first.
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
  but not instantly), so recordings are claimed atomically before being
  downloaded and `lecture.panopto_session_id` carries a unique index behind
  it. Without that, one recording could be downloaded and transcribed twice —
  billed twice — and the second caption push would then fail as a duplicate.
- **Failures are retried, but not forever.** Anything that fails leaves the
  lecture in a state a later poll can reclaim, up to
  `PANOPTO_MAX_INGEST_ATTEMPTS`; past that it's reported under `attention` in
  diagnostics and left alone, because retrying without a ceiling means
  re-downloading a permanently broken recording indefinitely.
- **The list endpoint omits `words_json`.** It's the largest column by a wide
  margin and the list screen — which polls every five seconds — doesn't read
  it. `GET /lectures/{id}` still returns it, which is what the captions
  player uses.
- Confirmed end-to-end on 2026-08-17 and again on 2026-08-24, twice
  unattended: caption push verified not just via our own DB but
  independently, by re-fetching the session from Panopto's API and seeing a
  populated `CaptionDownloadUrl` for Hebrew that wasn't there before.

