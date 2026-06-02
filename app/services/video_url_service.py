import base64
import os
import sys
import yt_dlp

from pathlib import Path
from app.core.config import RECORDINGS_DIR


_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")


def is_youtube_url(url: str) -> bool:
    return any(d in url for d in _YOUTUBE_DOMAINS)


def _find_node_path() -> str | None:
    """Find node binary from nodejs-wheel-binaries or system."""
    import shutil, glob, sysconfig

    candidates = [
        os.path.join(os.path.dirname(sys.executable), "node"),
        os.path.join(sysconfig.get_path("scripts") or "", "node"),
        "/var/task/_vendor/nodejs_wheel/bin/node",   # Vercel runtime location
    ]
    for path in candidates:
        if path and os.path.exists(path):
            print(f"[yt-dlp] Found node at {path}")
            return path

    # Broader glob search (covers varying Vercel paths)
    for pattern in ["/var/**/nodejs_wheel/bin/node", "/usr/**/nodejs_wheel/bin/node"]:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            print(f"[yt-dlp] Found node via glob at {matches[0]}")
            return matches[0]

    system_node = shutil.which("node")
    print(f"[yt-dlp] node={'not found' if not system_node else system_node}")
    return system_node


def download_youtube_audio(video_url: str) -> str:
    """Download best-quality audio from a YouTube URL and return the local file path."""
    output_template = str(RECORDINGS_DIR / "%(title)s_%(id)s.%(ext)s")
    node_path = _find_node_path()

    common_opts = {
        "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": output_template,
        "quiet": False,
        "no_warnings": False,
        "remote_components": ["ejs:github"],
    }

    # Tell yt-dlp exactly where node is — avoids relying on PATH discovery
    if node_path:
        common_opts["js_runtimes"] = {"node": {"path": node_path}}

    is_vercel = os.environ.get("VERCEL") == "1"
    print(f"[yt-dlp] VERCEL={is_vercel} node={node_path}")

    # Local dev: read cookies directly from Firefox/Safari
    if not is_vercel:
        for browser in ("firefox", "safari"):
            try:
                opts = {**common_opts, "cookiesfrombrowser": (browser, None, None, None)}
                return _run(video_url, opts)
            except Exception as e:
                print(f"[yt-dlp] {browser} cookies failed: {e}")
                continue

    # Vercel: use cookies from env var
    cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64", "")
    print(f"[yt-dlp] COOKIES_B64_SET={bool(cookies_b64)} COOKIES_LEN={len(cookies_b64)}")
    if cookies_b64:
        cookies_path = Path("/tmp/yt_cookies.txt")
        cookies_path.write_bytes(base64.b64decode(cookies_b64))
        opts = {**common_opts, "cookiefile": str(cookies_path)}
        return _run(video_url, opts)

    return _run(video_url, common_opts)


def _run(video_url: str, opts: dict) -> str:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        return ydl.prepare_filename(info)
