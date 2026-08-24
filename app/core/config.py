import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env here (not just in main.py) so module-level env reads below are correct
# regardless of import order — this module gets imported transitively before
# main.py's own load_dotenv() call runs. Safe to call more than once.
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# On Vercel (and other serverless platforms) only /tmp is writable at runtime.
_runtime_root = Path("/tmp") if os.environ.get("VERCEL") == "1" else PROJECT_ROOT

DATABASE_PATH = _runtime_root / "database.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}")
RECORDINGS_DIR = _runtime_root / "recordings"
EXPORTS_DIR = _runtime_root / "exports"
ASSETS_DIR = PROJECT_ROOT / "assets"
WEB_DIR = PROJECT_ROOT / "app" / "web"
INDEX_FILE = WEB_DIR / "index.html"
WATCH_FILE = WEB_DIR / "watch.html"
FONT_FILE = ASSETS_DIR / "fonts" / "Heebo-VariableFont_wght.ttf"

# Google OAuth (see app/services/auth_service.py). SESSION_SECRET_KEY signs the
# session cookie — any long random string; changing it logs everyone out.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "")
# Optional override so the redirect_uri sent to Google exactly matches what's
# registered in Google Cloud Console (avoids scheme mismatches behind a proxy,
# e.g. on Vercel). Example: https://your-app.vercel.app/auth/callback
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")

# Panopto pilot integration (see app/services/panopto_service.py).
# PANOPTO_BASE_URL is the sandbox root, e.g. https://mta-sandbox.cloud.panopto.eu
# — no trailing slash, no /Panopto suffix. Client id/secret come from an OAuth2
# "API Client" created in Panopto under System > API Clients.
#
# Two client types are in play here, deliberately:
#   - "Server Application" (Client Credentials grant) — runs with no user
#     identity at all. Fine for reading/downloading, but Panopto refuses to
#     let it edit captions (401 "User is not authorized"), and it isn't
#     addressable in the folder-sharing UI to grant it a role either.
#   - "Server-side Web Application" (Authorization Code + refresh token) —
#     acts AS the admin who completes a one-time browser consent, so it
#     inherits real permissions. This is what caption push needs, and what
#     Panopto's own support recommends over the password-grant alternative
#     ("User Based Server Application") — that one requires storing the
#     admin's actual login password, which we're deliberately avoiding.
# Set PANOPTO_REFRESH_TOKEN (below) to use the second kind; get_access_token()
# falls back to Client Credentials when it's unset.
PANOPTO_BASE_URL = os.environ.get("PANOPTO_BASE_URL", "").rstrip("/")
PANOPTO_CLIENT_ID = os.environ.get("PANOPTO_CLIENT_ID", "")
PANOPTO_CLIENT_SECRET = os.environ.get("PANOPTO_CLIENT_SECRET", "")
# Obtained once via GET /panopto/oauth/login → complete the browser consent →
# the /panopto/oauth/callback response prints it for you to paste in here.
PANOPTO_REFRESH_TOKEN = os.environ.get("PANOPTO_REFRESH_TOKEN", "")
# Must exactly match an "Allowed Redirect URL" on the Server-side Web
# Application client in Panopto.
PANOPTO_REDIRECT_URI = os.environ.get("PANOPTO_REDIRECT_URI", "http://127.0.0.1:8000/panopto/oauth/callback")
# The Panopto folder to poll for new recordings (the folder's GUID, from its URL
# in the Panopto UI). Used as the default when /panopto/sync is called with no
# folder_id query param.
PANOPTO_FOLDER_ID = os.environ.get("PANOPTO_FOLDER_ID", "")
# Language tag sent with each caption upload — must match one of the values
# Panopto's own captions UI offers (check Edit > Captions > Add a language on
# a session in the sandbox to see the exact accepted strings).
PANOPTO_CAPTION_LANGUAGE = os.environ.get("PANOPTO_CAPTION_LANGUAGE", "Hebrew")
# Lets an automated caller (a scheduled GitHub Action, since Vercel Hobby's
# own Cron is capped at once/day — see README) trigger /panopto/sync without
# a logged-in session, by sending this value in an X-Sync-Secret header.
# Any long random string; leave unset to disable the automated path entirely
# (POST /panopto/sync still works normally for a logged-in human).
PANOPTO_SYNC_SECRET = os.environ.get("PANOPTO_SYNC_SECRET", "")
# Who owns Lecture rows created by an automated (secret-authenticated) sync,
# since there's no logged-in user to attribute them to. Defaults to whichever
# user has the lowest id (fine for a single-user pilot); set explicitly once
# there's more than one real account.
PANOPTO_SYNC_OWNER_USER_ID = os.environ.get("PANOPTO_SYNC_OWNER_USER_ID", "")


def _int_env(name: str, default: int) -> int:
    """Read an int setting without letting a typo take the pipeline down —
    a bad value falls back to the default rather than raising at import."""
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        print(f"config: {name}={raw!r} is not an integer; using {default}")
        return default


# How many not-yet-ingested recordings a single /panopto/sync will take on.
# Deliberately small: each one costs a download plus a full transcription
# inside one serverless invocation, and the poller comes back every two
# minutes, so a backlog drains steadily instead of trying to do everything in
# one call and being killed partway.
PANOPTO_MAX_NEW_PER_SYNC = _int_env("PANOPTO_MAX_NEW_PER_SYNC", 1)
# Give up on a recording after this many attempts. Without a ceiling, one
# permanently broken recording is retried every couple of minutes forever,
# re-downloading it each time.
PANOPTO_MAX_INGEST_ATTEMPTS = _int_env("PANOPTO_MAX_INGEST_ATTEMPTS", 4)
PANOPTO_MAX_CAPTION_ATTEMPTS = _int_env("PANOPTO_MAX_CAPTION_ATTEMPTS", 4)
# A lecture whose progress hasn't moved in this long is presumed dead — the
# invocation was killed by the execution limit, or a redeploy landed
# mid-processing. It gets marked failed so it becomes retryable instead of
# sitting in "processing" forever.
PANOPTO_STALL_MINUTES = _int_env("PANOPTO_STALL_MINUTES", 30)
# Ceiling on how long we'll wait for AssemblyAI on one recording.
TRANSCRIPTION_TIMEOUT_MINUTES = _int_env("TRANSCRIPTION_TIMEOUT_MINUTES", 40)


def ensure_runtime_directories() -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
