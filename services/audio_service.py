import os
import subprocess


def boost_audio(input_path: str) -> str:
    """
    Boost media audio volume using FFmpeg while copying video codec for speed.
    Returns boosted file path; falls back to original on failure.
    """
    folder = os.path.dirname(input_path)
    base = os.path.basename(input_path)
    output_filename = f"boosted_{base}"
    output_path = os.path.join(folder, output_filename)

    cmd = [
        "ffmpeg",
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
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_path
    except Exception as e:
        print(f"Audio boosting failed: {e}")
        return input_path
