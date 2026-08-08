from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.core.config import GOOGLE_CLIENT_ID, GOOGLE_REDIRECT_URI
from app.core.database import get_session
from app.services.auth_service import get_current_user, get_or_create_user, oauth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on this server "
            "(missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).",
        )
    redirect_uri = GOOGLE_REDIRECT_URI or str(request.url_for("auth_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request, session: Session = Depends(get_session)):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await oauth.google.parse_id_token(request, token)
    user = get_or_create_user(session, userinfo)
    request.session["user_id"] = user.id
    # 303 (not the default 307) so the browser treats this as a fresh GET
    # navigation, and no-store so no cache layer replays a pre-login copy of
    # this redirect — either can make the first sign-in appear not to stick.
    response = RedirectResponse(url="/", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out"}


@router.get("/me")
def me(response: Response, user=Depends(get_current_user)):
    # Never cache auth state — a cached pre-login 401 would make a freshly
    # signed-in user still look signed out.
    response.headers["Cache-Control"] = "no-store"
    return {"id": user.id, "email": user.email, "name": user.name, "picture": user.picture}
