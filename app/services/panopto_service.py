"""
Panopto pilot integration.

Two directions:
  - PULL:  poll a Panopto folder for recordings we haven't ingested yet, download
           each one, and hand it to the normal transcription pipeline
           (see sync_folder / pipeline_service.run_full_pipeline).
  - PUSH:  once a lecture born from Panopto finishes processing, upload our VTT
           as a caption track back onto that same Panopto session
           (see upload_captions, called from pipeline_service).

Verified live against the sandbox (2026-08-17): OAuth2 token exchange, folder
listing, session search, single-session lookup, and the fact that
Urls.DownloadUrl is only reliably populated by the single-session GET, not by
either listing endpoint (get_session_details / download_session_video handle
this). Caption upload is built and shape-tested but not yet fired for real —
see /panopto/sync and the README before running it against a live session,
since Panopto can't replace an existing caption track via this API.
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from sqlmodel import Session, select

from app.core.config import (
    PANOPTO_BASE_URL,
    PANOPTO_CAPTION_LANGUAGE,
    PANOPTO_CLIENT_ID,
    PANOPTO_CLIENT_SECRET,
    PANOPTO_FOLDER_ID,
    PANOPTO_REDIRECT_URI,
    PANOPTO_REFRESH_TOKEN,
    RECORDINGS_DIR,
)
from app.core.database import engine
from app.models import Lecture, PanoptoToken

_token_cache = {"value": None, "expires_at": 0.0}


class PanoptoNotConfigured(RuntimeError):
    pass


class PanoptoAPIError(RuntimeError):
    pass


def _require_config() -> None:
    if not (PANOPTO_BASE_URL and PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET):
        raise PanoptoNotConfigured(
            "Panopto integration is not configured — set PANOPTO_BASE_URL, "
            "PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET in .env"
        )


def _load_stored_refresh_token() -> Optional[str]:
    with Session(engine) as db:
        row = db.exec(select(PanoptoToken).order_by(PanoptoToken.id.desc())).first()
        return row.refresh_token if row else None


def _save_refresh_token(token: str) -> None:
    with Session(engine) as db:
        row = db.exec(select(PanoptoToken)).first()
        if row:
            row.refresh_token = token
        else:
            row = PanoptoToken(refresh_token=token, updated_at="")
        row.updated_at = datetime.now(timezone.utc).isoformat()
        db.add(row)
        db.commit()


def get_access_token(force_refresh: bool = False) -> str:
    """Access token, cached until ~60s before expiry.

    Uses the refresh-token grant (acts as the admin who completed the
    one-time browser consent — see get_authorize_url / exchange_code) when a
    refresh token is available; falls back to plain Client Credentials (no
    user identity — fine for reads, refused for editing captions) otherwise.
    See config.py for why both grant types exist.

    The refresh token itself comes from the database first (PanoptoToken,
    single row), falling back to PANOPTO_REFRESH_TOKEN only if the database
    has never seen one — that env var is a one-time seed, not the ongoing
    source of truth. This matters because Panopto rotates the refresh token
    on every use: two processes reading the same static env var (e.g. local
    testing and the deployed app) will invalidate each other's copy the
    moment either one calls this. Reading/writing through each environment's
    own database — which local dev and production don't share — keeps each
    one self-consistent regardless of what happens in the other.
    """
    _require_config()
    now = time.time()
    if not force_refresh and _token_cache["value"] and now < _token_cache["expires_at"]:
        return _token_cache["value"]

    refresh_token = _load_stored_refresh_token() or PANOPTO_REFRESH_TOKEN
    if refresh_token:
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    else:
        data = {"grant_type": "client_credentials", "scope": "api"}

    resp = requests.post(
        f"{PANOPTO_BASE_URL}/Panopto/oauth2/connect/token",
        data=data,
        auth=(PANOPTO_CLIENT_ID, PANOPTO_CLIENT_SECRET),
        timeout=30,
    )
    if not resp.ok:
        raise PanoptoAPIError(f"Token request failed ({resp.status_code}): {resp.text}")
    payload = resp.json()
    _token_cache["value"] = payload["access_token"]
    _token_cache["expires_at"] = now + payload.get("expires_in", 3600) - 60

    new_refresh = payload.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        _save_refresh_token(new_refresh)
    return _token_cache["value"]


def get_authorize_url(state: str) -> str:
    """Step 1 of the one-time admin consent: send the browser here. Requires
    a "Server-side Web Application" API client (not Client Credentials) with
    PANOPTO_REDIRECT_URI registered as one of its Allowed Redirect URLs."""
    _require_config()
    from urllib.parse import urlencode

    params = {
        "client_id": PANOPTO_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": PANOPTO_REDIRECT_URI,
        # offline_access is what makes Panopto hand back a refresh_token
        # alongside the access_token on the code exchange below.
        "scope": "openid api offline_access",
        "state": state,
    }
    return f"{PANOPTO_BASE_URL}/Panopto/oauth2/connect/authorize?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Step 2: trade the authorization code Panopto redirected back with for
    an access_token + refresh_token. The refresh_token is the one to save —
    see /panopto/oauth/callback, which is the only caller of this."""
    _require_config()
    resp = requests.post(
        f"{PANOPTO_BASE_URL}/Panopto/oauth2/connect/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": PANOPTO_REDIRECT_URI},
        auth=(PANOPTO_CLIENT_ID, PANOPTO_CLIENT_SECRET),
        timeout=30,
    )
    if not resp.ok:
        raise PanoptoAPIError(f"Code exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}"}


def test_connection() -> dict:
    """Cheapest possible round-trip: just fetch a token. Used by /panopto/status
    so the OAuth client setup can be verified before wiring up anything else."""
    get_access_token(force_refresh=True)
    return {"ok": True, "base_url": PANOPTO_BASE_URL}


def list_recent_sessions(folder_id: str, limit: int = 25) -> list:
    """Sessions in a folder, most recent first.

    Verified live against the sandbox: GET /api/v1/folders/{id}/sessions
    returns {"Results": [...]}, each with Id, Name, Folder, FolderDetails,
    and Urls.DownloadUrl (nested, not top-level). Deliberately NOT using
    /api/v1/sessions/search here — that endpoint requires a non-empty
    searchQuery keyword, which doesn't fit "list what's new in this folder".

    One thing this call can't tell you: whether the API client's identity
    actually has view access to folder_id. A folder it can't see returns the
    same empty Results as a folder with nothing new in it — if you expect
    sessions here and get none, check the API client's permissions on that
    folder before suspecting this code.

    This endpoint is also just flaky on the sandbox — verified live to fail
    with a bare 500 on some calls and succeed on an identical retry moments
    later, with no discernible pattern. Retried a few times before giving up.
    """
    last_error = None
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)
        resp = requests.get(
            f"{PANOPTO_BASE_URL}/Panopto/api/v1/folders/{folder_id}/sessions",
            params={"sortField": "Date", "sortOrder": "Desc", "maxResults": limit},
            headers=_auth_headers(),
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            return data.get("Results") or data.get("results") or []
        last_error = f"Listing sessions failed ({resp.status_code}): {resp.text}"
    raise PanoptoAPIError(f"{last_error} (after 3 attempts)")


def get_session_details(session_id: str) -> dict:
    """GET /api/v1/sessions/{id} — verified live to return a fully populated
    Urls.DownloadUrl for a real, user-owned session. Note: this 404'd once in
    testing for one of Panopto's own built-in demo videos (CreatedBy an
    all-zeros system id) — that looks like a quirk of stock content, not
    something to expect for real uploads. This is the endpoint to trust for
    DownloadUrl; the listing endpoints below don't reliably populate it even
    when a session is fully downloadable (see their docstrings)."""
    resp = requests.get(
        f"{PANOPTO_BASE_URL}/Panopto/api/v1/sessions/{session_id}",
        headers=_auth_headers(),
        timeout=30,
    )
    if not resp.ok:
        raise PanoptoAPIError(f"Fetching session {session_id} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def download_from_url(download_url: str, dest_path: str, session_id: str = "") -> str:
    """Stream a Panopto download URL (from get_session_details) to dest_path."""
    with requests.get(download_url, headers=_auth_headers(), stream=True, timeout=180) as r:
        if not r.ok:
            raise PanoptoAPIError(f"Downloading session {session_id or download_url} failed ({r.status_code})")
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest_path


def download_session_video(session_id: str, dest_path: str) -> str:
    """get_session_details(session_id) + download. This is the reliable path
    — verified live — even though list_recent_sessions() already returns a
    Urls block for the same session; that field just comes back null there
    regardless of whether the session is actually downloadable."""
    details = get_session_details(session_id)
    urls = details.get("Urls") or {}
    download_url = urls.get("DownloadUrl") or urls.get("downloadUrl") or details.get("DownloadUrl")
    if not download_url:
        raise PanoptoAPIError(
            f"Session {session_id} has no download URL — check that downloading is "
            "enabled for its folder and that the API client's account can access it"
        )
    return download_from_url(download_url, dest_path, session_id=session_id)


def upload_captions(session_id: str, caption_path: str, language: str = PANOPTO_CAPTION_LANGUAGE) -> dict:
    """Attach a caption track to a Panopto session.

    Panopto's REST API refuses (400) to replace a caption track that already
    exists for a language — if you're re-testing against the same recording,
    delete the old track first in the Panopto UI (Edit > Captions).
    """
    filename = os.path.basename(caption_path)
    content_type = "text/vtt" if filename.lower().endswith(".vtt") else "application/x-subrip"
    with open(caption_path, "rb") as f:
        resp = requests.post(
            f"{PANOPTO_BASE_URL}/Panopto/api/v1/sessions/{session_id}/captions",
            headers=_auth_headers(),
            files={"file": (filename, f, content_type)},
            data={"language": language},
            timeout=60,
        )
    if not resp.ok:
        raise PanoptoAPIError(f"Caption upload failed ({resp.status_code}): {resp.text}")
    return resp.json() if resp.content else {"status": "ok"}


def _session_id_of(raw: dict) -> Optional[str]:
    return raw.get("Id") or raw.get("id") or raw.get("SessionId") or raw.get("sessionId")


def _session_name_of(raw: dict) -> str:
    return raw.get("Name") or raw.get("name") or "Untitled Panopto recording"


def discover_and_ingest(db: Session, user_id: int, folder_id: str = "", limit: int = 25) -> dict:
    """Pull step: find Panopto recordings in a folder that we haven't ingested
    yet, and download each one into a new Lecture row with panopto_session_id
    set, so the pipeline knows to push captions back on completion (see
    pipeline_service). Does NOT start transcription itself — the download is
    done here (synchronously, so a "sync now" call has something concrete to
    show for itself), but the caller is expected to schedule
    pipeline_service.run_full_pipeline(lecture_id, filename) as a background
    task for each item in "created", same as a manual upload would.
    """
    folder_id = folder_id or PANOPTO_FOLDER_ID
    if not folder_id:
        raise PanoptoNotConfigured("No folder_id given and PANOPTO_FOLDER_ID is not set")

    raw_sessions = list_recent_sessions(folder_id, limit=limit)
    known_ids = set(
        db.exec(
            select(Lecture.panopto_session_id).where(Lecture.panopto_session_id.is_not(None))
        ).all()
    )

    created, skipped, failed = [], [], []
    for raw in raw_sessions:
        session_id = _session_id_of(raw)
        if not session_id:
            continue
        if session_id in known_ids:
            skipped.append(session_id)
            continue

        name = _session_name_of(raw)
        try:
            dest = RECORDINGS_DIR / f"panopto_{session_id}_{uuid.uuid4().hex[:8]}.mp4"
            download_session_video(session_id, str(dest))  # fetches its own Urls — see that function's docstring
        except Exception as e:
            failed.append({"session_id": session_id, "name": name, "error": str(e)})
            continue

        lecture = Lecture(
            title=name,
            filename=dest.name,
            status="processing",
            processing_stage="pending",
            progress_percent=0,
            user_id=user_id,
            panopto_session_id=session_id,
        )
        db.add(lecture)
        db.commit()
        db.refresh(lecture)
        # filename (basename) matches the Lecture.filename DB convention; audio_source
        # is the full path run_full_pipeline actually needs (bare filenames resolve
        # against whatever the caller's cwd happens to be, not RECORDINGS_DIR).
        created.append({
            "session_id": session_id, "name": name, "lecture_id": lecture.id,
            "filename": dest.name, "audio_source": str(dest),
        })

    return {"created": created, "skipped": skipped, "failed": failed}
