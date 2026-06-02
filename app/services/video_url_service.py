import base64
import os
import yt_dlp

from pathlib import Path
from app.core.config import RECORDINGS_DIR


_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")


def is_youtube_url(url: str) -> bool:
    return any(d in url for d in _YOUTUBE_DOMAINS)


def _cookie_opts() -> dict:
    """Return yt-dlp cookie options for the current environment."""
    is_vercel = os.environ.get("VERCEL") == "1"

    if not is_vercel:
        for browser in ("firefox", "safari", "chrome"):
            try:
                with yt_dlp.YoutubeDL({"cookiesfrombrowser": (browser, None, None, None), "quiet": True}) as ydl:
                    pass
                return {"cookiesfrombrowser": (browser, None, None, None)}
            except Exception:
                continue
        return {}

    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64", "")
    print(f"[yt-dlp] COOKIES_B64_SET={bool(cookies_b64)} COOKIES_LEN={len(cookies_b64)}")
    if cookies_b64:
        cookies_path = Path("/tmp/yt_cookies.txt")
        cookies_path.write_bytes(base64.b64decode(cookies_b64))
        return {"cookiefile": str(cookies_path)}
    return {}


def download_youtube_audio(video_url: str) -> str:
    """Download best-quality audio from a YouTube URL and return the local file path."""
    output_template = str(RECORDINGS_DIR / "%(title)s_%(id)s.%(ext)s")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": False,
        "no_warnings": False,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios"],
            }
        },
        **_cookie_opts(),
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return ydl.prepare_filename(info)
