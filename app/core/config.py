import os
from pathlib import Path


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


def ensure_runtime_directories() -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
