from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from app.core.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from app.core.database import get_session
from app.models import User

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def get_or_create_user(session: Session, userinfo: dict) -> User:
    """Look up the Google account by its stable 'sub' claim, creating it on first login."""
    sub = userinfo["sub"]
    user = session.exec(select(User).where(User.google_sub == sub)).first()
    if user:
        # Keep profile fields fresh (name/picture/email can change on Google's side).
        user.email = userinfo.get("email", user.email)
        user.name = userinfo.get("name", user.name)
        user.picture = userinfo.get("picture", user.picture)
    else:
        user = User(
            google_sub=sub,
            email=userinfo.get("email", ""),
            name=userinfo.get("name"),
            picture=userinfo.get("picture"),
        )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> User:
    """Require a logged-in user; raise 401 otherwise. Use as a route dependency."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = session.get(User, user_id)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_current_user_optional(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    """Same as get_current_user but returns None instead of raising — for endpoints
    that behave differently for signed-in vs. anonymous visitors without blocking."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return session.get(User, user_id)
