import secrets
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.core.config import PANOPTO_BASE_URL, PANOPTO_FOLDER_ID, PANOPTO_SYNC_OWNER_USER_ID, PANOPTO_SYNC_SECRET
from app.core.database import get_session
from app.models import User
from app.services.auth_service import get_current_user, get_current_user_optional
from app.services.panopto_service import (
    PanoptoAPIError,
    PanoptoNotConfigured,
    _save_refresh_token,
    diagnostics,
    discover_and_ingest,
    exchange_code_for_tokens,
    get_authorize_url,
    ingest_and_process,
    retry_caption_pushes,
    test_connection,
)

router = APIRouter(prefix="/panopto", tags=["panopto"])


def _designated_owner(session: Session) -> Optional[User]:
    """Whoever the pilot's Panopto lectures belong to: PANOPTO_SYNC_OWNER_USER_ID
    if set, otherwise the lowest-id account (fine while there's only one real
    user)."""
    if PANOPTO_SYNC_OWNER_USER_ID:
        try:
            owner = session.get(User, int(PANOPTO_SYNC_OWNER_USER_ID))
        except ValueError:
            # Misconfigured to something non-numeric: fall through to the
            # default owner rather than 500-ing every sync from now on.
            print(f"PANOPTO_SYNC_OWNER_USER_ID={PANOPTO_SYNC_OWNER_USER_ID!r} is not an integer")
            owner = None
        if owner:
            return owner
    return session.exec(select(User).order_by(User.id)).first()


def _resolve_sync_owner(session: Session, user: Optional[User], sync_secret: str) -> User:
    """Authorise a call to the Panopto pilot controls and return the account
    the resulting lectures belong to.

    Two ways in: a signed-in browser session, or the shared secret the
    scheduled poller uses (it has no human to be). Signing in is not by itself
    enough — anyone can create an account here with a Google login, and these
    endpoints trigger real work and expose the pilot's Panopto folder
    contents, so a signed-in caller must actually be the pilot owner.
    """
    owner = _designated_owner(session)
    if user is not None:
        if owner is None or user.id != owner.id:
            raise HTTPException(
                status_code=403,
                detail="The Panopto pilot controls are restricted to the pilot owner.",
            )
        return owner

    if not PANOPTO_SYNC_SECRET or not sync_secret or not secrets.compare_digest(
        sync_secret, PANOPTO_SYNC_SECRET
    ):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if owner is None:
        raise HTTPException(status_code=503, detail="No users exist yet to own synced lectures")
    return owner


@router.get("/status")
def panopto_status(user: User = Depends(get_current_user)):
    """Cheapest possible check that the OAuth2 API Client is set up correctly —
    just fetches a token, doesn't touch any sessions. Use this first, before
    trying /panopto/sync, so a bad client id/secret is obvious immediately."""
    if not PANOPTO_BASE_URL:
        return {"configured": False, "detail": "PANOPTO_BASE_URL / PANOPTO_CLIENT_ID / PANOPTO_CLIENT_SECRET not set"}
    try:
        result = test_connection()
    except PanoptoNotConfigured as e:
        return {"configured": False, "detail": str(e)}
    except PanoptoAPIError as e:
        return {"configured": True, "token_ok": False, "detail": str(e)}
    return {"configured": True, "token_ok": True, "base_url": result["base_url"], "default_folder_id": PANOPTO_FOLDER_ID or None}


@router.get("/diagnostics")
def panopto_diagnostics(
    folder_id: str = "",
    probe_session_id: str = "",
    x_sync_secret: str = Header(default="", alias="X-Sync-Secret"),
    session: Session = Depends(get_session),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Read-only snapshot of the whole automatic pipeline: config, OAuth
    state, what Panopto shows in the folder, and what we've done with each
    session. Same two ways in as /panopto/sync (logged-in human, or the
    shared secret), so it can be checked from outside the browser without
    ingesting anything or spending the refresh token."""
    _resolve_sync_owner(session, user, x_sync_secret)
    return diagnostics(session, folder_id=folder_id, probe_session_id=probe_session_id)


@router.get("/oauth/login")
def panopto_oauth_login(request: Request, user: User = Depends(get_current_user)):
    """One-time admin bootstrap: sends your browser to Panopto's own consent
    screen. Requires the API Client to be type "Server-side Web Application"
    (not Client Credentials) with PANOPTO_REDIRECT_URI registered as an
    Allowed Redirect URL. After you click Allow, Panopto sends you back to
    /panopto/oauth/callback with a code we trade for a refresh token."""
    state = secrets.token_urlsafe(24)
    request.session["panopto_oauth_state"] = state
    try:
        url = get_authorize_url(state)
    except PanoptoNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    return RedirectResponse(url)


@router.get("/oauth/callback")
def panopto_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Panopto redirects here after you approve (or deny) the consent screen.
    Saves the refresh_token straight to this environment's database — see
    PanoptoToken / get_access_token()'s docstring for why that, and not
    PANOPTO_REFRESH_TOKEN in .env, is the ongoing source of truth. Still
    shown in the response too, in case you want it in .env as a backup seed
    for a from-scratch database."""
    if error:
        raise HTTPException(status_code=400, detail=f"Panopto denied the request: {error}")
    expected_state = request.session.pop("panopto_oauth_state", None)
    if not state or state != expected_state:
        raise HTTPException(status_code=400, detail="State mismatch — start over at /panopto/oauth/login")
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code in the callback")

    try:
        tokens = exchange_code_for_tokens(code)
    except PanoptoAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Usually means the "offline_access" scope wasn't granted/consented.
        raise HTTPException(
            status_code=502,
            detail="Panopto didn't return a refresh_token — check the API Client "
            "supports offline_access, and that this is its first authorization "
            "(some IdPs only issue one on first consent).",
        )
    _save_refresh_token(refresh_token)
    return HTMLResponse(
        "<pre style='white-space:pre-wrap;font-family:monospace'>"
        "Success — saved to this environment's database, nothing more to do.\n\n"
        f"(Backup seed for .env's PANOPTO_REFRESH_TOKEN, if you want one: {refresh_token})\n\n"
        "That value is shown once and won't stay valid for long once this app "
        "starts using it — Panopto rotates it on every call. If you ever need "
        "to re-bootstrap, just run /panopto/oauth/login again."
        "</pre>"
    )


@router.post("/sync")
def panopto_sync(
    background_tasks: BackgroundTasks,
    folder_id: str = "",
    limit: int = 25,
    x_sync_secret: str = Header(default="", alias="X-Sync-Secret"),
    session: Session = Depends(get_session),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Pull step. Callable two ways: by a logged-in human (browser/curl with a
    session cookie), or by an automated caller sending X-Sync-Secret — see
    README for the GitHub Actions workflow that polls this every few minutes,
    since Vercel Hobby's own Cron can't run more than once a day. Downloads
    any not-yet-seen recordings in the folder and schedules the normal
    transcription pipeline for each; captions get pushed back to Panopto
    automatically when each one finishes (pipeline_service)."""
    owner = _resolve_sync_owner(session, user, x_sync_secret)
    try:
        result = discover_and_ingest(session, owner.id, folder_id=folder_id, limit=limit)
    except PanoptoNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except PanoptoAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    for item in result["created"]:
        background_tasks.add_task(ingest_and_process, item["lecture_id"], item["session_id"])

    # Finished lectures whose captions never reached Panopto get another go,
    # one per call so this stays a cheap addition to the poll. Only runs when
    # nothing was claimed, so a backlog of new recordings takes priority.
    if not result["created"]:
        try:
            result["caption_retries"] = retry_caption_pushes(session)
        except Exception as e:  # noqa: BLE001 — never fail the sync over a retry
            result["caption_retries"] = [{"error": str(e)}]

    return result
