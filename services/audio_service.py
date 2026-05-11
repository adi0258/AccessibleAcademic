import os
import shutil
import subprocess
from glob import glob


def _resolve_ffmpeg() -> str | None:
    env_path = os.getenv("FFMPEG_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return path_ffmpeg

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        winget_pattern = os.path.join(
            local_app_data,
            "Microsoft",
            "WinGet",
            "Packages",
            "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
            "*",
            "bin",
            "ffmpeg.exe",
        )
        matches = sorted(glob(winget_pattern), reverse=True)
        if matches:
            return matches[0]

    return None


def boost_audio(input_path: str) -> str:
    """
    Boost media audio volume using FFmpeg while copying video codec for speed.
    Returns boosted file path; falls back to original on failure.
    """
    folder = os.path.dirname(input_path)
    base = os.path.basename(input_path)
    output_filename = f"boosted_{base}"
    output_path = os.path.join(folder, output_filename)
    ffmpeg_bin = _resolve_ffmpeg()

    cmd = [
        ffmpeg_bin or "ffmpeg",
        "-i",
        input_path,
        "-af",
        "volume=3.0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-y",
        output_path,
    ]
    try:
        if ffmpeg_bin is None:
            print("Audio boosting skipped: ffmpeg is not installed; using original media file.")
            return input_path
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_path
    except FileNotFoundError:
        print("Audio boosting skipped: ffmpeg is not installed; using original media file.")
        return input_path
    except Exception as e:
        print(f"Audio boosting failed: {e}")
        return input_path
