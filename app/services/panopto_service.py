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
from sqlalchemy import text
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
    expected_refresh_token: Optional[str] = None,
) -> bool:
    """Persist OAuth state. Returns False if `expected_refresh_token` was given
    and no longer matches what's stored — i.e. a concurrent caller already
    moved the refresh token on and this write was refused rather than allowed
    to clobber theirs.

    The conditional UPDATE is what makes that safe. Read-modify-write through
    the ORM is not atomic under Postgres' default isolation: two callers can
    both read the same row and both write, and the slower one wins — which
    for a rotating credential means persisting a token that has already been
    superseded and is therefore dead. A single UPDATE ... WHERE refresh_token
    = :expected re-evaluates its predicate against the committed row, so the
    late writer sees the mismatch and affects zero rows instead.

    Row id is pinned to 1: this is a singleton, and pinning it turns a
    simultaneous first-ever bootstrap from "two rows, one silently ignored"
    into a primary-key conflict the loser can retry from.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as db:
        row = db.exec(select(PanoptoToken).order_by(PanoptoToken.id)).first()
        if row is None:
            db.add(
                PanoptoToken(
                    id=1,
                    refresh_token=refresh_token,
                    updated_at=now_iso,
                    access_token=access_token,
                    access_token_expires_at=access_token_expires_at,
                )
            )
            try:
                db.commit()
                return True
            except IntegrityError:
                db.rollback()  # someone else bootstrapped first
                return False

        sql = (
            "UPDATE panoptotoken SET refresh_token = :new_refresh, "
            "updated_at = :updated_at"
        )
        params = {
            "new_refresh": refresh_token,
            "updated_at": now_iso,
            "row_id": row.id,
        }
        if access_token is not None:
            sql += ", access_token = :access_token, access_token_expires_at = :expires_at"
            params["access_token"] = access_token
            params["expires_at"] = access_token_expires_at
        sql += " WHERE id = :row_id"
        if expected_refresh_token is not None:
            sql += " AND refresh_token = :expected"
            params["expected"] = expected_refresh_token

        result = db.execute(text(sql), params)
        db.commit()
        return result.rowcount > 0


def _save_token_state_with_retry(**kwargs) -> bool:
    """_save_token_state, but doesn't give up on a blip.

    Losing this write after a successful exchange is the one failure that
    can't be recovered from in code: the token we just spent is dead, so
    failing to store its replacement means the next refresh has nothing valid
    to present and a human has to re-consent. A transient database error is
    worth a few retries to avoid that.
    """
    last_error = None
    for attempt in range(3):
        try:
            return _save_token_state(**kwargs)
        except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
            last_error = e
            time.sleep(0.3 * (attempt + 1))
    # Deliberately not logging the token value itself: it would land in
    # Vercel's logs, and re-consenting is cheaper than leaking a credential.
    print(
        "Panopto: FAILED to persist rotated refresh token after 3 attempts "
        f"({last_error}). Re-authorize at /panopto/oauth/login if sync starts "
        "failing with invalid_grant."
    )
    return False


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
    stored_ok = _save_token_state_with_retry(
        refresh_token=payload.get("refresh_token") or refresh_token,
        access_token=access_token,
        access_token_expires_at=expires_at,
        # Refuse to overwrite a refresh token that moved on while we were
        # exchanging — see _save_token_state.
        expected_refresh_token=refresh_token,
    )
    if not stored_ok:
        print(
            "Panopto: refresh token moved on while we were exchanging; kept the "
            "stored one. Using our access token for this process only."
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


def _invalidate_cached_access_token() -> None:
    """Drop the cached access token, here and in the database, so the next
    caller mints a fresh one instead of re-presenting a rejected token."""
    _token_cache["value"] = None
    _token_cache["expires_at"] = 0.0
    try:
        with Session(engine) as db:
            row = db.exec(select(PanoptoToken).order_by(PanoptoToken.id)).first()
            if row:
                row.access_token = None
                row.access_token_expires_at = 0.0
                db.add(row)
                db.commit()
    except Exception as e:  # noqa: BLE001 — never let cache cleanup break the call
        print(f"Panopto: could not clear cached access token: {e}")


def _authorized_request(method: str, url: str, build_kwargs) -> requests.Response:
    """Authenticated Panopto call that survives a token being rejected early.

    Caching the access token until its stated expiry assumes Panopto agrees
    about when that is. If it ever disagrees — a revoked session, a
    re-consent elsewhere, clock skew — every call would 401 against a token
    we'd happily keep re-presenting for up to an hour, and the pipeline would
    sit dead with nothing in the logs but 401s. One 401 is therefore treated
    as "this token is stale regardless of what its expiry claims": drop it,
    mint a fresh one, try once more.

    `build_kwargs` is a callable rather than a dict because the retry needs
    its own request body (an upload's file payload can't be replayed from a
    consumed handle).
    """
    resp = requests.request(method, url, headers=_auth_headers(), **build_kwargs())
    if resp.status_code == 401:
        _invalidate_cached_access_token()
        resp = requests.request(method, url, headers=_auth_headers(), **build_kwargs())
    return resp


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
        resp = _authorized_request(
            "GET",
            f"{PANOPTO_BASE_URL}/Panopto/api/v1/folders/{folder_id}/sessions",
            lambda: {
                "params": {"sortField": "Date", "sortOrder": "Desc", "maxResults": limit},
                "timeout": 30,
            },
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
    resp = _authorized_request(
        "GET",
        f"{PANOPTO_BASE_URL}/Panopto/api/v1/sessions/{session_id}",
        lambda: {"timeout": 30},
    )
    if not resp.ok:
        raise PanoptoAPIError(f"Fetching session {session_id} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def download_from_url(download_url: str, dest_path: str, session_id: str = "") -> str:
    """Stream a Panopto download URL (from get_session_details) to dest_path."""
    for attempt in range(2):
        with requests.get(download_url, headers=_auth_headers(), stream=True, timeout=180) as r:
            if r.status_code == 401 and attempt == 0:
                # Same stale-token case _authorized_request handles; written out
                # here because the response has to stay streamed, not buffered.
                _invalidate_cached_access_token()
                continue
            if not r.ok:
                raise PanoptoAPIError(
                    f"Downloading session {session_id or download_url} failed ({r.status_code})"
                )
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
            return dest_path
    raise PanoptoAPIError(f"Downloading session {session_id or download_url} failed (401 after token refresh)")


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
    # Read the payload up front rather than handing over a file object: a
    # retried request needs to send the body again, and a consumed handle
    # would upload zero bytes the second time. Caption files are tiny.
    with open(caption_path, "rb") as f:
        payload = f.read()
    resp = _authorized_request(
        "POST",
        f"{PANOPTO_BASE_URL}/Panopto/api/v1/sessions/{session_id}/captions",
        lambda: {
            "files": {"file": (filename, payload, content_type)},
            "data": {"language": language},
            "timeout": 60,
        },
    )
    if not resp.ok:
        raise PanoptoAPIError(f"Caption upload failed ({resp.status_code}): {resp.text}")
    return resp.json() if resp.content else {"status": "ok"}


def probe_session(session_id: str) -> dict:
    """Read-only check of the one call the ingest path depends on and the
    folder listing can't stand in for: whether a session's details come back
    and actually carry a download URL.

    Worth having separately because a session can look perfectly healthy in
    the listing and still be un-downloadable — Panopto still encoding it, or
    downloads disabled on the folder — and that only surfaces at ingest time,
    as a recording that gets noticed every poll and never progresses. Fetches
    metadata only; downloads nothing.
    """
    try:
        details = get_session_details(session_id)
    except Exception as e:  # noqa: BLE001 — this is a diagnostic, report don't raise
        return {"session_id": session_id, "details_fetched": False, "error": str(e)}
    urls = details.get("Urls") or {}
    download_url = urls.get("DownloadUrl") or urls.get("downloadUrl") or details.get("DownloadUrl")
    return {
        "session_id": session_id,
        "details_fetched": True,
        "name": details.get("Name"),
        "downloadable": bool(download_url),
        # The URL itself is a credentialed link — say whether it's there, not what it is.
        "download_url_host": download_url.split("/")[2] if download_url else None,
        "has_captions_already": bool(urls.get("CaptionDownloadUrl")),
    }


def diagnostics(db: Session, folder_id: str = "", probe_session_id: str = "") -> dict:
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

    if probe_session_id:
        report["probe"] = probe_session(probe_session_id)

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
