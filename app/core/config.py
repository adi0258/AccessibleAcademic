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


def ensure_runtime_directories() -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
