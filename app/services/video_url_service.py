import base64
import os
import yt_dlp

from pathlib import Path
from app.core.config import RECORDINGS_DIR


_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")


def is_youtube_url(url: str) -> bool:
    return any(d in url for d in _YOUTUBE_DOMAINS)


def _ensure_node_in_path() -> None:
    """Add nodejs-wheel-binaries Node.js to PATH so yt-dlp can solve n-challenge."""
    try:
        import nodejs.node as _node
        node_dir = os.path.dirname(_node.path)
        if node_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = node_dir + ":" + os.environ.get("PATH", "")
    except Exception:
        pass  # nodejs not installed — yt-dlp will try system node/deno


def download_youtube_audio(video_url: str) -> str:
    """Download best-quality audio from a YouTube URL and return the local file path."""
    _ensure_node_in_path()
    return _download_ytdlp(video_url)


def _download_ytdlp(video_url: str) -> str:
    output_template = str(RECORDINGS_DIR / "%(title)s_%(id)s.%(ext)s")

    common_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "mweb"],
            }
        },
    }

    if os.environ.get("VERCEL") != "1":
        for browser in ("firefox", "safari"):
            try:
                opts = {**common_opts, "cookiesfrombrowser": (browser, None, None, None)}
                return _run_ytdlp(video_url, opts)
            except Exception:
                continue

    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64")
    print(f"[yt-dlp] VERCEL={os.environ.get('VERCEL')} COOKIES_B64_SET={bool(cookies_b64)} COOKIES_LEN={len(cookies_b64 or '')}")
    if cookies_b64:
        cookies_path = Path("/tmp/yt_cookies.txt")
        cookies_path.write_bytes(base64.b64decode(cookies_b64))
        opts = {**common_opts, "cookiefile": str(cookies_path)}
        return _run_ytdlp(video_url, opts)

    return _run_ytdlp(video_url, common_opts)


def _run_ytdlp(video_url: str, opts: dict) -> str:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return ydl.prepare_filename(info)
