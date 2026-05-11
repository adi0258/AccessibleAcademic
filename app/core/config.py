from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "database.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"
RECORDINGS_DIR = PROJECT_ROOT / "recordings"
EXPORTS_DIR = PROJECT_ROOT / "exports"
ASSETS_DIR = PROJECT_ROOT / "assets"
WEB_DIR = PROJECT_ROOT / "app" / "web"
INDEX_FILE = WEB_DIR / "index.html"
WATCH_FILE = WEB_DIR / "watch.html"
FONT_FILE = ASSETS_DIR / "fonts" / "Heebo-VariableFont_wght.ttf"


def ensure_runtime_directories() -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
