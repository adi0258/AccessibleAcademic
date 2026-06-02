import base64
import os
import yt_dlp

from pathlib import Path
from app.core.config import RECORDINGS_DIR


_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")


def is_youtube_url(url: str) -> bool:
    return any(d in url for d in _YOUTUBE_DOMAINS)


def download_youtube_audio(video_url: str) -> str:
    """Download best-quality audio from a YouTube URL and return the local file path."""
    output_template = str(RECORDINGS_DIR / "%(title)s_%(id)s.%(ext)s")

    common_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        # Must be a list — string is iterated char-by-char and silently ignored
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "mweb"],
            }
        },
    }

    # Local dev: read cookies directly from Firefox (most reliable — avoids stale cookie files)
    if os.environ.get("VERCEL") != "1":
        for browser in ("firefox", "safari"):
            try:
                opts = {**common_opts, "cookiesfrombrowser": (browser, None, None, None)}
                return _run_download(video_url, opts)
            except Exception:
                continue

    # Vercel (or local fallback): decode cookies from env var
    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64")
    if cookies_b64:
        cookies_path = Path("/tmp/yt_cookies.txt")
        cookies_path.write_bytes(base64.b64decode(cookies_b64))
        opts = {**common_opts, "cookiefile": str(cookies_path)}
        return _run_download(video_url, opts)

    # Last resort: no cookies
    return _run_download(video_url, common_opts)


def _run_download(video_url: str, opts: dict) -> str:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return ydl.prepare_filename(info)
