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

    # tv_embedded and mweb clients are least likely to trigger bot detection
    base_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["tv_embedded", "mweb", "web", "android"],
            }
        },
    }

    # Production (Vercel): decode cookies from env var
    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64")
    if cookies_b64:
        cookies_path = Path("/tmp/yt_cookies.txt")
        cookies_path.write_bytes(base64.b64decode(cookies_b64))
        base_opts["cookiefile"] = str(cookies_path)
        return _run_download(video_url, base_opts)

    # Local dev: try browser cookies (Safari then Firefox — Chrome blocks external access on macOS)
    if os.environ.get("VERCEL") != "1":
        for browser in ("safari", "firefox"):
            try:
                opts = {**base_opts, "cookiesfrombrowser": (browser, None, None, None)}
                return _run_download(video_url, opts)
            except Exception:
                continue

    # Fallback: no cookies, rely on player_client bypass alone
    return _run_download(video_url, base_opts)


def _run_download(video_url: str, opts: dict) -> str:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return ydl.prepare_filename(info)
