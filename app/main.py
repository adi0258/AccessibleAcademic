import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.auth_routes import router as auth_router
from app.api.routes import router as api_router
from app.core.config import ASSETS_DIR, INDEX_FILE, RECORDINGS_DIR, SESSION_SECRET_KEY, WATCH_FILE, ensure_runtime_directories
from app.core.database import create_db_and_tables


load_dotenv()


def create_app() -> FastAPI:
    ensure_runtime_directories()

    app = FastAPI(title="Accessible Academic Backend")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Signs the login session cookie. Falls back to a random per-process key so
    # local dev works with zero setup — every restart just logs users out.
    session_secret = SESSION_SECRET_KEY or secrets.token_urlsafe(32)
    # Vercel is always HTTPS; local dev (http://localhost) is not — mark the
    # cookie Secure only where it will actually be sent over HTTPS.
    is_https_env = os.environ.get("VERCEL") == "1"
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=is_https_env,
    )

    app.mount("/static", StaticFiles(directory=str(RECORDINGS_DIR)), name="static")
    if ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

    app.include_router(auth_router)
    app.include_router(api_router)

    @app.on_event("startup")
    def on_startup() -> None:
        create_db_and_tables()

    @app.get("/", response_class=FileResponse)
    def serve_ui():
        return FileResponse(INDEX_FILE)

    @app.get("/watch/{lecture_id}", response_class=FileResponse)
    def watch_lecture(lecture_id: int):
        return FileResponse(WATCH_FILE)

    return app


app = create_app()
