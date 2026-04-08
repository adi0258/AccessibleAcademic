import json
import os
import re

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from models.lecture import Lecture


MAX_WORDS_PER_SEGMENT = 15
SENTENCE_END = re.compile(r"[.?!。．？！]\s*$")


def _ms_to_srt_timestamp(ms: int) -> str:
    total = max(0, int(ms))
    hours = total // 3600000
    minutes = (total % 3600000) // 60000
    seconds = (total % 60000) // 1000
    millis = total % 1000
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _ms_to_vtt_timestamp(ms: int) -> str:
    total = max(0, int(ms))
    hours = total // 3600000
    minutes = (total % 3600000) // 60000
    seconds = (total % 60000) // 1000
    millis = total % 1000
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def _build_segments(words):
    if not words:
        return []
    segments = []
    chunk = []
    for word in words:
        text = (word.get("text") or "").strip()
        chunk.append({"start": word.get("start"), "end": word.get("end"), "text": text})
        end_of_sentence = bool(text and SENTENCE_END.search(text))
        at_max_words = len(chunk) >= MAX_WORDS_PER_SEGMENT
        if at_max_words or end_of_sentence:
            start = chunk[0].get("start")
            end = chunk[-1].get("end")
            joined = " ".join(c.get("text", "") for c in chunk).strip()
            if joined and start is not None and end is not None:
                segments.append({"start": int(start), "end": int(end), "text": joined})
            chunk = []
    if chunk:
        start = chunk[0].get("start")
        end = chunk[-1].get("end")
        joined = " ".join(c.get("text", "") for c in chunk).strip()
        if joined and start is not None and end is not None:
            segments.append({"start": int(start), "end": int(end), "text": joined})
    return segments


def export_lecture_srt(lecture_id: int, session: Session, base_dir: str):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    try:
        words = json.loads(lecture.words_json or "[]")
    except Exception:
        words = []
    segments = _build_segments(words)
    if not segments:
        raise HTTPException(status_code=400, detail="No subtitle timing data available for this lecture")

    lines = []
    for idx, seg in enumerate(segments, 1):
        lines.append(str(idx))
        lines.append(f"{_ms_to_srt_timestamp(seg['start'])} --> {_ms_to_srt_timestamp(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    srt_content = "\n".join(lines).strip() + "\n"

    safe_title = "".join(c for c in lecture.title if c not in r"\/:*?\"<>|").strip() or f"lecture-{lecture_id}"
    safe_title = safe_title.replace("\n", " ").replace("\r", " ")[:200]
    download_filename = f"subtitles-{safe_title}.srt"
    export_path = os.path.join(base_dir, f"export_{lecture_id}.srt")
    with open(export_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    return FileResponse(export_path, filename=download_filename, media_type="application/x-subrip")


def export_lecture_vtt(lecture_id: int, session: Session, base_dir: str):
    lecture = session.get(Lecture, lecture_id)
    if not lecture or lecture.status != "completed":
        raise HTTPException(status_code=404, detail="Lecture not ready")

    try:
        words = json.loads(lecture.words_json or "[]")
    except Exception:
        words = []
    segments = _build_segments(words)
    if not segments:
        raise HTTPException(status_code=400, detail="No subtitle timing data available for this lecture")

    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_ms_to_vtt_timestamp(seg['start'])} --> {_ms_to_vtt_timestamp(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    vtt_content = "\n".join(lines).strip() + "\n"

    safe_title = "".join(c for c in lecture.title if c not in r"\/:*?\"<>|").strip() or f"lecture-{lecture_id}"
    safe_title = safe_title.replace("\n", " ").replace("\r", " ")[:200]
    download_filename = f"subtitles-{safe_title}.vtt"
    export_path = os.path.join(base_dir, f"export_{lecture_id}.vtt")
    with open(export_path, "w", encoding="utf-8") as f:
        f.write(vtt_content)

    return FileResponse(export_path, filename=download_filename, media_type="text/vtt")
