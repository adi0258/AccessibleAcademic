"""
Panopto pilot integration.

Two directions:
  - PULL:  poll a Panopto folder for recordings we haven't ingested yet, download
           each one, and hand it to the normal transcription pipeline
           (see sync_folder / pipeline_service.run_full_pipeline).
  - PUSH:  once a lecture born from Panopto finishes processing, upload our VTT
           as a caption track back onto that same Panopto session
           (see upload_captions, called from pipeline_service).

Verified live against the sandbox (2026-08-17), including a full unattended
round trip: a recording uploaded in Panopto was detected, downloaded,
transcribed, and had our captions pushed back onto it — confirmed from
Panopto's own API rather than only from our database.

Sandbox quirks worth keeping in mind, all found the hard way:
  - Urls.DownloadUrl is only reliably populated by the single-session GET,
    never by either listing endpoint (get_session_details handles this).
  - The folder-listing endpoint intermittently 500s and succeeds on retry.
  - Captions can't be replaced, only added: pushing a second track for a
    language a session already has is a 400.
  - The refresh token rotates on every single use — see get_access_token().
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from sqlalchemy.exc import IntegrityError
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


def _load_token_row() -> Optional[dict]:
    """Snapshot of the stored OAuth state, as a plain dict so it stays usable
    after the session closes."""
    with Session(engine) as db:
        row = db.exec(select(PanoptoToken).order_by(PanoptoToken.id)).first()
        if not row:
            return None
        return {
            "refresh_token": row.refresh_token,
            "access_token": row.access_token,
            "access_token_expires_at": row.access_token_expires_at,
        }


def _save_token_state(
    refresh_token: str,
    access_token: Optional[str] = None,
    access_token_expires_at: Optional[float] = None,
) -> None:
    with Session(engine) as db:
        row = db.exec(select(PanoptoToken).order_by(PanoptoToken.id)).first()
        if not row:
            row = PanoptoToken(refresh_token=refresh_token, updated_at="")
        else:
            row.refresh_token = refresh_token
        if access_token is not None:
            row.access_token = access_token
            row.access_token_expires_at = access_token_expires_at
        row.updated_at = datetime.now(timezone.utc).isoformat()
        db.add(row)
        db.commit()


def _save_refresh_token(token: str) -> None:
    """Bootstrap entry point for /panopto/oauth/callback: store a brand new
    refresh token and drop any cached access token minted from the old one."""
    _save_token_state(token, access_token="", access_token_expires_at=0.0)
    _token_cache["value"] = None
    _token_cache["expires_at"] = 0.0


def _exchange_refresh_token(refresh_token: str) -> dict:
    data = (
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
        if refresh_token
        # No refresh token anywhere: fall back to Client Credentials. That
        # grant has no user identity, so reads work but caption writes get a
        # 401 — see config.py.
        else {"grant_type": "client_credentials", "scope": "api"}
    )
    resp = requests.post(
        f"{PANOPTO_BASE_URL}/Panopto/oauth2/connect/token",
        data=data,
        auth=(PANOPTO_CLIENT_ID, PANOPTO_CLIENT_SECRET),
        timeout=30,
    )
    if not resp.ok:
        raise PanoptoAPIError(f"Token request failed ({resp.status_code}): {resp.text}")
    return resp.json()


def get_access_token(force_refresh: bool = False) -> str:
    """A valid Panopto access token, doing as little token spending as possible.

    Order of preference: this process's memory, then the access token cached
    in the database, and only if both are stale does it spend the refresh
    token to mint a new one.

    That ordering is the whole point. Panopto rotates the refresh token every
    single time it's used, and every serverless invocation starts with an
    empty in-memory cache — so a naive implementation rotates on every poll,
    and any two polls that overlap race to spend the same token. The loser of
    that race is left holding a dead one, and recovering means a human doing
    an interactive re-consent. Reusing the cached access token until it
    actually expires turns "rotate dozens of times an hour" into "rotate
    about once an hour", and _recover_from_lost_race() handles the rare
    overlap that's left.
    """
    _require_config()
    now = time.time()
    if not force_refresh and _token_cache["value"] and now < _token_cache["expires_at"]:
        return _token_cache["value"]

    stored = _load_token_row()
    if not force_refresh and stored:
        cached, expires_at = stored["access_token"], stored["access_token_expires_at"]
        if cached and expires_at and now < expires_at:
            _token_cache["value"] = cached
            _token_cache["expires_at"] = expires_at
            return cached

    refresh_token = (stored["refresh_token"] if stored else "") or PANOPTO_REFRESH_TOKEN
    try:
        payload = _exchange_refresh_token(refresh_token)
    except PanoptoAPIError:
        recovered = _recover_from_lost_race(refresh_token)
        if recovered:
            return recovered
        raise

    access_token = payload["access_token"]
    # 60s of headroom so a token can't expire in flight between this check and
    # the API call that uses it.
    expires_at = now + payload.get("expires_in", 3600) - 60
    _token_cache["value"] = access_token
    _token_cache["expires_at"] = expires_at
    _save_token_state(
        payload.get("refresh_token") or refresh_token,
        access_token=access_token,
        access_token_expires_at=expires_at,
    )
    return access_token


def _recover_from_lost_race(attempted_refresh_token: str) -> Optional[str]:
    """Salvage the case where a concurrent caller rotated the refresh token
    out from under us between our read and our exchange.

    The tell is that the stored refresh token has changed since we read it,
    and there's now a fresh access token sitting next to it — meaning the
    other caller succeeded and we can just use its result. Returns None when
    the failure was anything else (bad credentials, Panopto down, a genuinely
    expired consent), so the caller re-raises the real error.
    """
    current = _load_token_row()
    if not current or current["refresh_token"] == attempted_refresh_token:
        return None
    access_token, expires_at = current["access_token"], current["access_token_expires_at"]
    if not access_token or not expires_at or time.time() >= expires_at:
        return None
    _token_cache["value"] = access_token
    _token_cache["expires_at"] = expires_at
    return access_token


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
    """Cheapest possible round-trip: just get a token. Used by /panopto/status
    so the OAuth client setup can be verified before wiring up anything else.

    Deliberately does NOT force a refresh — a health check that spent (and so
    rotated) the refresh token on every call would be a liability rather than
    a diagnostic. A cached-but-valid token proves the same thing this needs to.
    """
    get_access_token()
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


def diagnostics(db: Session, folder_id: str = "") -> dict:
    """One read-only snapshot of everything that has to be true for the
    automatic pipeline to work: config, OAuth state, what Panopto currently
    shows in the folder, and what we've done with each of those sessions.

    Read-only on purpose — it can be called freely while the poller is
    running without ingesting anything or spending the refresh token.
    """
    folder_id = folder_id or PANOPTO_FOLDER_ID
    now = time.time()
    stored = _load_token_row()

    report = {
        "config": {
            "base_url": PANOPTO_BASE_URL or None,
            "folder_id": folder_id or None,
            "client_id_set": bool(PANOPTO_CLIENT_ID),
            "client_secret_set": bool(PANOPTO_CLIENT_SECRET),
            "caption_language": PANOPTO_CAPTION_LANGUAGE,
        },
        "oauth": {
            "refresh_token_stored": bool(stored and stored["refresh_token"]),
            "access_token_cached": bool(stored and stored["access_token"]),
            "access_token_valid_for_seconds": (
                int(stored["access_token_expires_at"] - now)
                if stored and stored.get("access_token_expires_at")
                else None
            ),
        },
    }

    lectures = db.exec(
        select(Lecture).where(Lecture.panopto_session_id.is_not(None)).order_by(Lecture.id)
    ).all()
    by_session = {lecture.panopto_session_id: lecture for lecture in lectures}

    try:
        sessions = list_recent_sessions(folder_id, limit=25) if folder_id else []
        report["panopto_folder"] = {"reachable": True, "session_count": len(sessions)}
        report["sessions"] = [
            {
                "session_id": _session_id_of(raw),
                "name": _session_name_of(raw),
                "ingested": _session_id_of(raw) in by_session,
            }
            for raw in sessions
        ]
    except Exception as e:
        report["panopto_folder"] = {"reachable": False, "error": str(e)}
        report["sessions"] = []

    report["lectures"] = [
        {
            "lecture_id": lecture.id,
            "title": lecture.title,
            "session_id": lecture.panopto_session_id,
            "status": lecture.status,
            "stage": lecture.processing_stage,
            "progress": lecture.progress_percent,
            "captions_synced_at": lecture.panopto_captions_synced_at,
            "sync_error": lecture.panopto_sync_error,
        }
        for lecture in lectures
    ]
    return report


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
        try:
            db.commit()
        except IntegrityError:
            # Another poller run ingested this session while we were
            # downloading it — the unique index on panopto_session_id is what
            # stops us billing a second transcription for the same recording.
            # Its download is the one that counts; drop ours.
            db.rollback()
            try:
                os.remove(dest)
            except OSError:
                pass
            skipped.append(session_id)
            continue
        db.refresh(lecture)
        # filename (basename) matches the Lecture.filename DB convention; audio_source
        # is the full path run_full_pipeline actually needs (bare filenames resolve
        # against whatever the caller's cwd happens to be, not RECORDINGS_DIR).
        created.append({
            "session_id": session_id, "name": name, "lecture_id": lecture.id,
            "filename": dest.name, "audio_source": str(dest),
        })

    return {"created": created, "skipped": skipped, "failed": failed}
